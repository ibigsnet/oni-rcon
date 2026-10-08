"""Fleet browsing uses shared telemetry, never additional game-server polls."""
from __future__ import annotations

import shlex
import re

from textual.binding import Binding
from textual.widgets import Input, ListItem, ListView

SORTS = [
    ('Players: most first', 'players'),
    ('Players: fewest first', 'players-asc'),
    ('Fullest first (%)', 'fill'),
    ('Capacity: largest first', 'capacity'),
    ('Server name A–Z', 'name'),
    ('Node / port', 'node'),
    ('Needs attention first', 'health'),
]
STATES = [
    ('All states', 'all'),
    ('Players online', 'occupied'),
    ('Empty running', 'empty'),
    ('Running', 'running'),
    ('Healthy', 'healthy'),
    ('Standby spares', 'standby'),
    ('Stopped', 'stopped'),
    ('Needs attention', 'attention'),
    ('Settings pending', 'pending'),
    ('Retired', 'retired'),
]


def server_tags(row):
    values=row.get('tags') or []
    return {tag.casefold() for tag in values if isinstance(tag,str) and
            re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9-]{0,47}',tag)}


def tagged_message(text):
    """Leading @tags select broadcast recipients; the remainder is literal chat."""
    tags=[]
    remaining=text.strip()
    while remaining.startswith('@'):
        token,_,remaining=remaining.partition(' ')
        tag=token[1:].casefold()
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,47}',tag):
            raise ValueError('Use @tag followed by a space and a message.')
        if tag not in tags:tags.append(tag)
        if len(tags)>8:raise ValueError('Select at most eight tags per broadcast.')
        remaining=remaining.lstrip()
    if not tags or not remaining:raise ValueError('A tag broadcast needs a message, e.g. @swat Hello everyone.')
    return tags,remaining


def live_count(row, snapshot_fresh=True):
    count = row.get('players')
    if (snapshot_fresh and row.get('node_fresh') and row.get('running') and row.get('fresh')
            and row.get('rcon_up') and type(count) is int and count >= 0):
        return count
    return None


def capacity(row):
    for key in ('runtime_cap', 'configured_cap'):
        value = row.get(key)
        if type(value) is int and value > 0:
            return value
    return None


def state_matches(row, state, snapshot_fresh=True):
    retired = bool(row.get('_retired'))
    observed = snapshot_fresh and bool(row.get('node_fresh')) and not retired
    running = observed and row.get('running') is True
    stopped = observed and row.get('running') is False
    healthy = running and row.get('fresh') and row.get('rcon_up') and row.get('health') == 'healthy'
    standby = stopped and row.get('spare') and row.get('desired_running') is False and not row.get('operator_hold')
    count = live_count(row, snapshot_fresh) if not retired else None
    return bool({
        'all': True,
        'occupied': count is not None and count > 0,
        'empty': count == 0,
        'running': running,
        'healthy': healthy,
        'standby': standby,
        'stopped': stopped,
        'attention': not retired and (not observed or row.get('running') is None or
                         (running and not healthy) or (stopped and row.get('desired_running'))),
        'pending': not retired and row.get('settings_pending'),
        'retired': retired,
    }.get(state, False))


def matching_rows(rows, *, search='', node='all', state='all', sort='players', snapshot_fresh=True):
    """All search terms must match; quotes group phrases. Unknown counts sort last."""
    try:
        terms = shlex.split(search.casefold())
    except ValueError:
        # A half-typed quote should not break live search.
        terms = search.casefold().replace('"', '').replace("'", '').split()
    found = []
    for row in rows:
        if node != 'all' and row.get('node') != node:
            continue
        if not state_matches(row, state, snapshot_fresh):
            continue
        tags=server_tags(row)
        haystack = (' '.join(str(row.get(key) or '') for key in
                            ('id', 'name', 'node', 'port', 'map', 'mode', 'container'))+' '+' '.join(tags)).casefold()
        if all((term[1:] in tags if term.startswith('@') else term in haystack) for term in terms):
            found.append(row)

    def key(row):
        tie = (str(row.get('node') or ''), row.get('port') or 0, row['id'])
        count = None if row.get('_retired') else live_count(row, snapshot_fresh)
        cap = capacity(row)
        if sort in ('players', 'players-asc'):
            return (count is None, (-count if sort == 'players' else count) if count is not None else 0, tie)
        if sort == 'fill':
            known = count is not None and cap is not None
            return (not known, -count / cap if known else 0, -(count or 0), tie)
        if sort == 'capacity':
            return (cap is None, -(cap or 0), tie)
        if sort == 'name':
            return (str(row.get('name') or '').casefold(), tie)
        if sort == 'health':
            rank = (0 if state_matches(row, 'attention', snapshot_fresh) else
                    1 if state_matches(row, 'pending', snapshot_fresh) else
                    2 if state_matches(row, 'running', snapshot_fresh) else 3)
            return (rank, tie)
        return tie

    return sorted(found, key=key)


class ServerItem(ListItem):
    def __init__(self, station, card):
        super().__init__(card)
        self.station = station


class ServerList(ListView):
    BINDINGS = [Binding('home', 'first_server', show=False), Binding('end', 'last_server', show=False)]

    def move_visible(self, direction):
        indices = [i for i, item in enumerate(self.children) if item.display and not item.disabled]
        if not indices:
            return
        if self.index is None:
            self.index = indices[0] if direction > 0 else indices[-1]
        else:
            candidates = [i for i in indices if (i - self.index) * direction > 0]
            if candidates:
                self.index = candidates[0] if direction > 0 else candidates[-1]

    def action_cursor_down(self):
        self.move_visible(1)

    def action_cursor_up(self):
        self.move_visible(-1)

    def action_first_server(self):
        self.index = None
        self.move_visible(1)

    def action_last_server(self):
        self.index = None
        self.move_visible(-1)


class ServerSearch(Input):
    BINDINGS = [Binding('escape', 'clear_search', show=False)]

    def action_clear_search(self):
        self.value = ''
        self.app.query_one('#stations').focus()
