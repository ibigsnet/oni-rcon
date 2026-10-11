"""Private, fleet-wide player lookup, independent of server-list filters."""
from __future__ import annotations
import asyncio
import time
import shlex
import unicodedata
import re
from functools import lru_cache

from rich.text import Text
from textual import on, work
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Static

from .app import Dialog


def normalized(value):
    return unicodedata.normalize('NFKC', str(value or '')).casefold()


def player_tag(player):
    return player.get('service_tag') or player.get('tag') or ''


def filter_terms(query):
    try:
        return shlex.split(normalized(query))
    except ValueError:
        # Keep filtering while a quoted phrase is still being typed.
        return normalized(query).replace('"', '').replace("'", '').split()


@lru_cache(maxsize=4096)
def name_distance(term, name):
    """Small, bounded edit distance, including one adjacent transposition."""
    if not 4 <= len(term) <= 64:
        return None
    limit = 1 if len(term) < 8 else 2
    best = limit + 1
    for candidate in {name, *re.findall(r'\w+', name)}:
        if len(candidate) > 64 or abs(len(term)-len(candidate)) > limit:
            continue
        prevprev = None
        prev = list(range(len(candidate)+1))
        for i, left in enumerate(term, 1):
            row = [i]
            for j, right in enumerate(candidate, 1):
                cost = min(row[-1]+1, prev[j]+1, prev[j-1]+(left != right))
                if i > 1 and j > 1 and left == candidate[j-2] and term[i-2] == right:
                    cost = min(cost, prevprev[j-2]+1)
                row.append(cost)
            prevprev, prev = prev, row
        best = min(best, prev[-1])
    return best if best <= limit else None


def ranked_players(directory, query='', now=None):
    """AND filters on one fresh player; typo tolerance applies only to names."""
    now = time.time() if now is None else now
    terms = filter_terms(query)
    matches = []
    for player in directory.get('players', []):
        server = player.get('server', {})
        if server.get('state') != 'ready' or not 0 <= now - (server.get('observed_at') or 0) <= 30:
            continue
        fields = {key: normalized(value) for key, value in {
            'name': player.get('name'), 'tag': player_tag(player),
            'clan': player.get('clan_tag'), 'team': player.get('team'),
            'id': player.get('id'), 'server': server.get('name'),
            'node': server.get('node'), 'port': server.get('port'),
        }.items()}
        def matches_term(term):
            field, sep, value = term.partition(':')
            if sep and field in fields:
                if value and value in fields[field]:
                    return 0
                return name_distance(value, fields['name']) if field == 'name' else None
            if term.startswith('@'):
                return 0 if term[1:] and term[1:] == fields['tag'] else None
            if any(term in value for value in fields.values()):
                return 0
            return None if sep else name_distance(term, fields['name'])
        scores = [matches_term(term) for term in terms]
        if all(score is not None for score in scores):
            matches.append((player, sum(scores)))
    return sorted(matches, key=lambda pair: (pair[1], normalized(pair[0]['name']) != normalized(query),
                  normalized(pair[0]['name']), normalized(player_tag(pair[0])),
                  pair[0]['server']['node'], pair[0]['server']['port'], pair[0]['id']))


def matching_players(directory, query='', now=None):
    return [player for player, score in ranked_players(directory, query, now)]


def coverage_text(directory, matches, now=None):
    now = time.time() if now is None else now
    coverage = directory.get('coverage', {})
    age = max(0, int(now - directory.get('at', now)))
    missing = coverage.get('unavailable', 0)
    stale = sum(s.get('state') == 'ready' and not 0 <= now-(s.get('observed_at') or 0) <= 30
                for s in directory.get('servers', []))
    suffix = f' · {missing+stale} unavailable/stale' if missing or stale else ''
    if coverage.get('discovery_errors'):
        suffix += ' · discovery incomplete'
    if 'details_total' in coverage:
        suffix += f' · tags {coverage.get("details_checked", 0)}/{coverage["details_total"]} occupied rosters current'
    return (f'{matches} matching players · {coverage.get("checked",0)}/{coverage.get("total",0)} servers checked'
            f' · {coverage.get("nodes",0)} nodes · refreshed {age}s ago{suffix}')


class PlayerSearch(Dialog):
    BINDINGS = [Binding('escape', 'dismiss(None)', show=False),
                Binding('ctrl+r', 'refresh_players', 'Refresh players', priority=True),
                Binding('down', 'focus_results', show=False)]
    DEFAULT_CSS = '''
    PlayerSearch .dialog { width: 94%; max-width: 150; height: 85%; }
    PlayerSearch #player-search { margin: 1 0; }
    PlayerSearch #player-results { height: 1fr; min-height: 4; }
    PlayerSearch #player-coverage { height: auto; min-height: 2; color: $text-muted; }
    PlayerSearch #player-search-note { height: auto; color: $text-muted; }
    '''

    def __init__(self, fleet):
        super().__init__()
        self.fleet, self.directory, self.results = fleet, {}, {}
        self.rendered = {}

    def compose(self):
        with Vertical(classes='dialog'):
            yield Static(Text('FILTER PLAYERS · ALL NODES'), classes='dialog-title')
            yield Input(placeholder='Name + tag, e.g. Player Z74 · tag:Z74 · node:node-b · quotes for phrases', id='player-search', max_length=128)
            yield Static(Text('Reading player locations…'), id='player-coverage')
            yield DataTable(id='player-results', cursor_type='row', zebra_stripes=True)
            yield Static(Text('All terms match one player. Names tolerate small typos (4+ characters); approximate matches follow exact matches. Filters: name:, tag:, clan:, team:, id:, server:, node:, port:. Tags/IDs are never fuzzy. @Z74 is an exact tag. Enter opens the server.'), id='player-search-note')
            with Horizontal(classes='dialog-buttons'):
                yield Button('CLOSE', id='player-close')
                yield Button('REFRESH', id='player-refresh')
                yield Button('OPEN SERVER', id='player-open', variant='primary')
            yield self.keys('Esc close', '↓ results', 'Enter open server', 'Ctrl+R refresh')

    def on_mount(self):
        super().on_mount()
        self.query_one('#player-results', DataTable).add_columns('Player', 'TAG', 'Clan', 'Team', 'Server', 'Node', 'Port', 'Player ID', 'Seen', 'Match')
        self.query_one('#player-search', Input).focus()
        self.action_refresh_players()
        self.set_interval(10, self.action_refresh_players)
        self.set_interval(1, self.render_results)

    @work(exclusive=True, group='player-directory')
    async def action_refresh_players(self):
        try:
            data = await asyncio.to_thread(self.fleet.request, '/api/players', None, 25)
            if not isinstance(data.get('players'), list) or not isinstance(data.get('servers'), list):
                raise ValueError('invalid player directory')
            if not self.is_mounted:
                return
            self.directory = data
            self.render_results()
        except Exception as exc:
            if self.is_mounted:
                self.directory, self.results = {}, {}
                self.rendered = {}
                self.query_one('#player-results', DataTable).clear()
                self.query_one('#player-open', Button).disabled = True
                self.query_one('#player-coverage', Static).update(Text('Player search unavailable: '+str(exc)))

    @on(Input.Changed, '#player-search')
    def search_changed(self, event):
        event.stop()
        self.render_results()

    def render_results(self):
        if not self.is_mounted or not self.directory:
            return
        table = self.query_one('#player-results', DataTable)
        selected = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value if table.row_count else None
        ranked = ranked_players(self.directory, self.query_one('#player-search', Input).value)
        matches = [p for p, score in ranked]
        scores = {p['server']['id']+':'+p['id']: score for p, score in ranked}
        self.results = {p['server']['id']+':'+p['id']: p for p in matches}
        now = time.time()
        cells = {}
        for key, player in self.results.items():
            server = player['server']
            cells[key] = [Text(player['name']), Text(player_tag(player) or '—'),
                          Text(player.get('clan_tag') or '—'), Text(player.get('team') or '—'),
                          Text(server['name']), Text(server['node'].upper()),
                          str(server['port']), player['id'][:8]+'…', f'{int(now-server["observed_at"])}s ago',
                          'Approximate name' if scores[key] else 'Exact / substring']
        if list(cells) != list(self.rendered):
            table.clear()
            for key, values in cells.items():table.add_row(*values, key=key)
        else:
            columns = list(table.columns)
            for key, values in cells.items():
                for index, value in enumerate(values):
                    if value != self.rendered[key][index]:table.update_cell(key, columns[index], value, update_width=True)
        self.rendered = cells
        if selected in self.results:
            table.move_cursor(row=list(self.results).index(selected))
        self.query_one('#player-open', Button).disabled = not bool(matches)
        message = coverage_text(self.directory, len(matches), now)
        if approximate := sum(score > 0 for score in scores.values()):
            message += f' · {approximate} approximate name matches'
        if not matches:
            message += ' · No matching players in available fresh rosters.'
        self.query_one('#player-coverage', Static).update(Text(message))

    @on(Input.Submitted, '#player-search')
    def submitted(self, event):
        event.stop()
        self.action_focus_results()

    def action_focus_results(self):
        self.query_one('#player-results', DataTable).focus()

    def choose(self):
        table = self.query_one('#player-results', DataTable)
        if not table.row_count:
            return
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        player = self.results.get(key)
        if player and matching_players({'players': [player]}):
            self.dismiss(player)

    @on(DataTable.RowSelected, '#player-results')
    def selected(self, event):
        event.stop()
        self.choose()

    @on(Button.Pressed)
    def pressed(self, event):
        event.stop()
        if event.button.id == 'player-close':
            self.dismiss(None)
        elif event.button.id == 'player-refresh':
            self.action_refresh_players()
        elif event.button.id == 'player-open':
            self.choose()
