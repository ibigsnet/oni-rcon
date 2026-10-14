"""What the scheduled jobs and the rules share, with no dependencies beyond the standard library: cron, native match
boundaries and atomic JSON files. The IBIGS fleet's node agents run the rules engine (rules.py) with this module and
safety.py copied in unchanged (reclaimer_control/oni_rules/)."""
from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from pathlib import Path

PHASES = {'no_game', 'loading', 'starting', 'playing', 'round_over', 'between_rounds', 'finished'}
ROOT = Path(os.environ.get('IBIGS_JOBS_DIR', '/srv/ibigs/automation'))


def cron_fields(expression):
    fields = str(expression).split()
    if len(fields) != 5:
        raise ValueError('Use five cron fields: minute hour day month weekday (0 or 7 = Sunday).')
    result = []
    for field, low, high in zip(fields, (0, 0, 1, 1, 0), (59, 23, 31, 12, 7)):
        values = set()
        for part in field.split(','):
            if not re.fullmatch(r'(\*|\d+(-\d+)?)(/\d+)?', part):
                raise ValueError('Cron accepts numbers, *, ranges, comma lists, and /steps.')
            base, _, step = part.partition('/')
            step = int(step or 1)
            if not 1 <= step <= high + 1:
                raise ValueError('Cron step is outside its field range.')
            if base == '*':
                start, end = low, high
            elif '-' in base:
                start, end = map(int, base.split('-'))
            else:
                start = int(base)
                end = high if '/' in part else start
            if not low <= start <= end <= high:
                raise ValueError('Cron value is outside its field range.')
            values.update(range(start, end + 1, step))
        result.append(values)
    result[4] = {v % 7 for v in result[4]}
    return fields, result


def cron_due(expression, when):
    fields, values = cron_fields(expression)
    minute, hour, day, month, weekday = values
    dom, dow = when.day in day, (when.weekday() + 1) % 7 in weekday
    # Standard cron: when both day fields are restricted, either may match.
    day_ok = dom and dow if fields[2].startswith('*') or fields[4].startswith('*') else dom or dow
    return when.minute in minute and when.hour in hour and when.month in month and day_ok


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o2770)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            os.fchmod(f.fileno(), 0o660)
            json.dump(data, f, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


class MatchEvents:
    """Only witnessed native boundaries count. Reconnects and round breaks never finish a match."""
    def __init__(self):
        self.phase = None
        self.vote = None
        self.played = False
        self.before_sent = False

    def observe(self, status):
        phase = status.get('phase')
        if phase not in PHASES:
            self.__init__()
            return []
        before = self.phase
        events = []
        ballot = status.get('playlist_vote')
        vote = ballot.get('active') if isinstance(ballot, dict) else None
        if phase == 'no_game' and before not in {None, 'no_game'}:
            self.before_sent = False
        if before is not None:
            voting = phase == 'no_game' and vote is True and self.vote is False
            loading = phase in {'loading', 'starting'} and before in {'no_game', 'loading', 'starting'}
            if (voting or loading) and not self.before_sent:
                events.append('before_match')
                self.before_sent = True
            if phase == 'playing' and before not in {'playing', 'round_over', 'between_rounds'}:
                events.append('match_start')
            if phase == 'finished' and before != 'finished' and self.played:
                events.append('after_match')
        if phase == 'playing':
            self.played = True
        elif phase in {'finished', 'no_game', 'loading', 'starting'}:
            self.played = False
        self.phase = phase
        if vote is not None:
            self.vote = bool(vote)
        elif phase != 'no_game':
            self.vote = False
        return events
