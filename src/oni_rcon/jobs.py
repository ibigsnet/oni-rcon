"""Persistent tenant jobs. Fixed actions, local inventory, no shell command execution."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile
import time
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .rcon import Rcon
from .safety import clean

ACTIONS = {'broadcast', 'shuffle', 'start', 'stop', 'restart', 'update-game', 'update-console'}
EVENTS = {'before_match', 'after_match', 'match_start'}
PHASES = {'no_game', 'loading', 'starting', 'playing', 'round_over', 'between_rounds', 'finished'}
ROOT = Path(os.environ.get('IBIGS_JOBS_DIR', '/srv/ibigs/automation'))
WRAPPER = '/usr/local/bin/oni'


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


def validate(job):
    if not isinstance(job, dict):
        raise ValueError('A job must be an object.')
    value = {k: job.get(k) for k in ('id', 'name', 'action', 'target', 'trigger', 'schedule', 'timezone', 'message')}
    value.update(enabled=job.get('enabled', True), delay=job.get('delay', 0))
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', str(value['id'])):
        raise ValueError('Invalid job ID.')
    if not isinstance(value['name'], str) or not 1 <= len(value['name']) <= 80 or clean(value['name']) != value['name']:
        raise ValueError('Name must be 1–80 printable characters.')
    if value['action'] not in ACTIONS:
        raise ValueError('Choose a supported action.')
    if not re.fullmatch(r'all|h3-\d{2,3}|server-\d{1,3}', str(value['target'])):
        raise ValueError('Choose all servers or an existing lobby.')
    if type(value['enabled']) is not bool or type(value['delay']) is not int or not 0 <= value['delay'] <= 300:
        raise ValueError('Delay must be 0–300 seconds; enabled must be true or false.')
    try:
        ZoneInfo(str(value['timezone']))
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError('Use a timezone such as America/New_York or UTC.') from None
    trigger = value['trigger']
    if trigger == 'cron':
        cron_fields(value['schedule'])
    elif trigger == 'interval':
        if not re.fullmatch(r'\d+', str(value['schedule'])) or not 1 <= int(value['schedule']) <= 10080:
            raise ValueError('Interval must be 1–10080 minutes.')
        value['schedule'] = str(int(value['schedule']))
    elif trigger in EVENTS:
        value['schedule'] = ''
        if value['action'] not in {'broadcast', 'shuffle'}:
            raise ValueError('Match events support broadcasts and match-start shuffles.')
        if value['action'] == 'shuffle' and trigger != 'match_start':
            raise ValueError('For an automatic shuffle, choose Match starts or a timed schedule.')
        if trigger == 'before_match' and value['delay']:
            raise ValueError('Before-match messages run when voting or loading begins; use zero delay.')
    else:
        raise ValueError('Choose a supported trigger.')
    message = value['message'] or ''
    if not isinstance(message, str) or len(message) > 240 or clean(message) != message or any(ord(c) < 32 for c in message):
        raise ValueError('Broadcasts must be one printable line of at most 240 characters.')
    if value['action'] == 'broadcast' and not message.strip():
        raise ValueError('Enter a broadcast message.')
    value['message'] = message
    if value['action'].startswith('update-') and value['target'] != 'all':
        raise ValueError('Updates apply to this VM; choose all servers.')
    return value


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


class Store:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.path = self.root / 'jobs.json'
        self.state_path = self.root / 'state.json'

    def load(self):
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return []
        if data.get('schema') != 1 or not isinstance(data.get('jobs'), list) or len(data['jobs']) > 64:
            raise ValueError('Invalid jobs file. Restore it before scheduled actions can run.')
        jobs = [validate(j) for j in data['jobs']]
        if len({j['id'] for j in jobs}) != len(jobs):
            raise ValueError('Job IDs must be unique.')
        return jobs

    def change(self, job=None, delete=None):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o2770)
        fd = os.open(self.root / 'config.lock', os.O_CREAT | os.O_RDWR, 0o660)
        with os.fdopen(fd, 'r+') as lock:
            if os.fstat(lock.fileno()).st_uid == os.geteuid():
                os.fchmod(lock.fileno(), 0o660)
            fcntl.flock(lock, fcntl.LOCK_EX)
            jobs = self.load()
            if job is not None:
                job = validate(job)
            identity = job['id'] if job else delete
            jobs = [j for j in jobs if j['id'] != identity]
            if job:
                jobs.append(job)
            if len(jobs) > 64:
                raise ValueError('This VM supports up to 64 jobs.')
            atomic_json(self.path, {'schema': 1, 'jobs': jobs})

    def state(self):
        try:
            data = json.loads(self.state_path.read_text())
        except FileNotFoundError:
            return {'schema': 1, 'claims': {}, 'history': []}
        if data.get('schema') != 1 or not isinstance(data.get('claims'), dict) or not isinstance(data.get('history'), list):
            raise ValueError('Invalid job state. Scheduled actions are paused.')
        return data


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


def occurrence(job, now, previous):
    if job['trigger'] == 'cron':
        local = datetime.fromtimestamp(now, ZoneInfo(job['timezone']))
        # Same wall-clock minute during a DST fold runs once, never twice.
        return local.strftime('%Y-%m-%dT%H:%M') if cron_due(job['schedule'], local) else None
    if job['trigger'] == 'interval':
        period = int(job['schedule']) * 60
        # UTC-aligned intervals; skip an occurrence missed while the worker was down.
        return str(int(now // period)) if now % period < 6 else None
    return None


def inventory():
    wrapper = runpy.run_path(WRAPPER)
    return wrapper['lobbies'](wrapper['read_env']())


def scheduled_targets(job, names):
    if job['action'].startswith('update-') or (job['target'] == 'all' and job['action'] in {'start', 'stop', 'restart'}):
        return ['all']
    return names if job['target'] == 'all' else [job['target']]


def run_fixed(argv, timeout=1800):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=timeout)
    return result.returncode


class Worker:
    def __init__(self, store, discover=inventory):
        self.store, self.discover = store, discover
        self.state = store.state()
        self.clients = {}
        self.tasks = {}
        self.trackers = {}
        self.jobs = []
        self.pending = set()
        self.action_lock = asyncio.Lock()
        self.stopping = False

    def save(self):
        self.state['heartbeat'] = time.time()
        atomic_json(self.store.state_path, self.state)

    def record(self, job, target, result):
        self.state['history'].append({'at': time.time(), 'job': job['id'], 'target': target, 'result': result})
        self.state['history'] = self.state['history'][-100:]
        self.save()

    def connection(self, target, state):
        if state != 'online':
            self.trackers[target] = MatchEvents()

    def event(self, target, msg):
        if msg.get('event') == 'lagged' or msg.get('type') == 'lagged':
            self.trackers[target] = MatchEvents()
        elif msg.get('event') == 'phase':
            at = msg.get('time')
            if not isinstance(at, (float, int)) or not -1 <= time.time() - at <= 3:
                self.trackers[target] = MatchEvents()
                return
            self.observe(target, msg)

    def observe(self, target, status):
        tracker = self.trackers.setdefault(target, MatchEvents())
        for event in tracker.observe(status):
            for job in self.jobs:
                if job['enabled'] and job['trigger'] == event and job['target'] in {'all', target}:
                    self.queue(job, target, 'event:' + uuid.uuid4().hex)

    def queue(self, job, target, key):
        identity = job['id'] + ':' + target
        if identity in self.pending or len(self.pending) >= 128:
            return
        claim = self.state['claims'].get(identity, {})
        recurrence = [job['trigger'], job['schedule'], job['timezone']]
        same_schedule = claim.get('recurrence') == recurrence
        if claim.get('key') == key and same_schedule:
            return
        if same_schedule and job['trigger'] == 'cron' and str(claim.get('key', '')) >= key:
            return  # do not run an earlier wall-clock minute again after a clock change or DST fold
        if same_schedule and job['trigger'] == 'interval' and int(claim.get('key', '-1')) >= int(key):
            return
        self.pending.add(identity)
        task = asyncio.create_task(self.execute(job, target, key))
        task.add_done_callback(lambda t: self.pending.discard(identity))
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    async def execute(self, job, target, key):
        if job['delay']:
            await asyncio.sleep(job['delay'])
        # A job removed, paused or edited during its delay must not execute the old action.
        if job not in self.store.load() or not job['enabled']:
            return
        identity = job['id'] + ':' + target
        # Durable claim BEFORE any action: a crash may skip a job, never replay an uncertain broadcast or shuffle.
        self.state['claims'][identity] = {'key': key, 'at': time.time(),
                                         'recurrence': [job['trigger'], job['schedule'], job['timezone']]}
        self.save()
        try:
            action = job['action']
            if action in {'broadcast', 'shuffle'}:
                client = self.clients.get(target)
                if not client or not client.ws:
                    self.record(job, target, 'Skipped: lobby is offline or not authenticated.')
                    return
                tracker = self.trackers[target]
                if job['trigger'] == 'before_match' and tracker.phase not in {'no_game', 'loading', 'starting'}:
                    self.record(job, target, 'Skipped: lobby already left the pre-match window.')
                    return
                if job['trigger'] == 'after_match' and tracker.phase not in {'finished', 'no_game'}:
                    self.record(job, target, 'Skipped: the next match has already started.')
                    return
                if action == 'shuffle':
                    fresh = await client.call('status')
                    if not fresh.get('ok') or (fresh.get('data') or {}).get('phase') != 'playing':
                        self.record(job, target, 'Skipped: teams can be shuffled during a running match.')
                        return
                reply = await client.call('say', job['message']) if action == 'broadcast' else await client.call('shuffle')
                self.record(job, target, 'Sent.' if reply.get('ok') else 'Server refused the action.')
            else:
                if self.action_lock.locked():
                    self.record(job, target, 'Skipped: another lifecycle or update job is running.')
                    return
                async with self.action_lock:
                    if action.startswith('update-'):
                        tool = '/usr/local/sbin/ibigs-' + ('oni-update' if action == 'update-console' else 'reclaimer-update')
                        argv = [tool] + (['--yes'] if action == 'update-game' else [])
                        if os.geteuid() != 0:
                            argv = ['sudo', '-n', *argv]
                    else:
                        argv = [WRAPPER, action, target, '--yes']
                    rc = await asyncio.to_thread(run_fixed, argv)
                    result = 'Completed.' if rc == 0 else ('Deferred: players online or occupancy unknown.' if rc == 4 else f'Failed (exit {rc}); inspect the updater/service log.')
                    self.record(job, target, result)
        except (OSError, ValueError, TimeoutError, ConnectionError, subprocess.SubprocessError):
            self.record(job, target, 'Failed or reply uncertain; this occurrence will not be retried.')

    async def refresh_clients(self):
        found = await asyncio.to_thread(self.discover)
        needed = {s['name']: s for s in found if any(j['enabled'] and j['action'] in {'broadcast', 'shuffle'}
                  and j['target'] in {'all', s['name']} for j in self.jobs)}
        for name in list(self.clients):
            client = self.clients[name]
            info = needed.get(name)
            host = ('[::1]' if info['host'] == '::1' else info['host']) if info else ''
            if info is None or client.password != info['password'] or client.url != f"ws://{host}:{info['port']}":
                self.tasks.pop(name).cancel()
                self.clients.pop(name)
                self.trackers.pop(name, None)
        for name, info in needed.items():
            if name in self.clients:
                continue
            if info['host'] not in {'127.0.0.1', 'localhost', '::1'}:
                continue
            host = '[::1]' if info['host'] == '::1' else info['host']
            client = Rcon(f"ws://{host}:{info['port']}", info['password'], 'Scheduled job',
                          lambda c, e, n=name: self.event(n, e),
                          lambda c, s, d, n=name: self.connection(n, s,))
            self.clients[name] = client
            self.tasks[name] = asyncio.create_task(client.run())
        return [s['name'] for s in found]

    async def poll(self, name, client):
        if not client.ws:
            return
        try:
            result = await client.call('status', timeout=3)
            if result.get('ok') and isinstance(result.get('data'), dict):
                self.observe(name, result['data'])
            else:
                self.trackers[name] = MatchEvents()
        except (OSError, ValueError, TimeoutError, ConnectionError):
            self.trackers[name] = MatchEvents()

    async def run(self):
        next_inventory = 0
        next_heartbeat = 0
        names = []
        active = Path('/opt/ibigs/oni-rcon/current')
        release = active.resolve() if active.exists() else None
        while True:
            self.jobs = self.store.load()  # malformed policy fails closed, systemd reports the failure
            now = time.time()
            if now >= next_inventory:
                if release and active.exists() and active.resolve() != release and not self.pending:
                    os.execv(WRAPPER, [WRAPPER, 'jobs', 'run'])
                names = await self.refresh_clients()
                next_inventory = now + 10
                valid = {j['id'] for j in self.jobs}
                self.state['claims'] = {k: v for k, v in self.state['claims'].items() if k.split(':')[0] in valid}
            for job in self.jobs:
                if not job['enabled'] or job['trigger'] in EVENTS:
                    continue
                key = occurrence(job, now, self.state['claims'])
                if key is None:
                    continue
                targets = scheduled_targets(job, names)
                for target in targets:
                    if target != 'all' and target not in names:
                        continue
                    self.queue(job, target, key)
            await asyncio.gather(*(self.poll(n, c) for n, c in self.clients.items()))
            if now >= next_heartbeat:
                self.save()
                next_heartbeat = now + 10
            await asyncio.sleep(2)


def main(args=None):
    ap = argparse.ArgumentParser(prog='oni jobs', description='Persistent schedules for this VM. Use F8 in oni for the editor.')
    sub = ap.add_subparsers(dest='command')
    sub.add_parser('run', help=argparse.SUPPRESS)
    sub.add_parser('list')
    sub.add_parser('history')
    sub.add_parser('status')
    for verb in ('pause', 'resume', 'delete'):
        sub.add_parser(verb).add_argument('id')
    add = sub.add_parser('add')
    add.add_argument('--name', required=True)
    add.add_argument('--action', required=True, choices=sorted(ACTIONS))
    add.add_argument('--target', default='all')
    add.add_argument('--trigger', default='cron', choices=['cron', 'interval', *sorted(EVENTS)])
    add.add_argument('--schedule', default='0 4 * * *')
    add.add_argument('--timezone', default='America/New_York')
    add.add_argument('--message', default='')
    add.add_argument('--delay', type=int, default=0)
    a = ap.parse_args(args)
    store = Store()
    try:
        if a.command == 'run':
            store.root.mkdir(parents=True, exist_ok=True, mode=0o2770)
            with open(store.root / 'worker.lock', 'a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                asyncio.run(Worker(store).run())
        elif a.command == 'add':
            job = vars(a).copy()
            job.update(id=uuid.uuid4().hex[:12], enabled=True)
            store.change(job)
            print('Saved ' + job['id'] + '. The scheduler runs it while oni is closed.')
        elif a.command in {'pause', 'resume', 'delete'}:
            job = next((j for j in store.load() if j['id'] == a.id), None)
            if not job:
                raise ValueError('No job has that ID.')
            if a.command == 'delete':
                store.change(delete=a.id)
            else:
                job['enabled'] = a.command == 'resume'
                store.change(job)
            print('Saved.')
        else:
            data = store.state() if a.command in {'history', 'status'} else store.load()
            print(json.dumps(data['history'] if a.command == 'history' else data, indent=2))
    except (OSError, ValueError, BlockingIOError) as exc:
        print(clean(str(exc)), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
