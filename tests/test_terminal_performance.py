"""Resource and navigation regressions; no real fleet, network, or game commands."""
import asyncio
from collections import Counter
from unittest.mock import Mock

from rich.text import Text
from textual.widgets import Button, RichLog, TabbedContent

from oni_rcon.app import Roster, UI_LOG_LIMIT
from oni_rcon.fleet import FleetApp
from oni_rcon.superintendent import Superintendent, card
import oni_rcon.superintendent as superintendent


class FixtureFleet:
    def __init__(self):
        import time
        self.updated = time.monotonic()
        self.rows = {f'node-b-{49175+i}': dict(id=f'node-b-{49175+i}', node='node-b', port=49175+i,
                     name=f'Lobby {i}', players=2, runtime_cap=32, configured_cap=32,
                     health='healthy', fresh=True, node_fresh=True, running=True, rcon_up=True,
                     image='official:test', process_generation='test', config_hash='test') for i in range(2)}
        self.reads = Counter()
        self.score = 9

    def refresh(self):
        return None

    def request(self, *args):
        return {'mode': 'managed-read-only', 'items': [], 'favourites': [], 'at': 0}

    async def read(self, ident, command):
        self.reads[ident, command] += 1
        data = {'status': {'name': ident, 'players': 2, 'max_players': 32, 'map': 'guardian',
                           'mode': 'Slayer', 'phase': 'in_game'},
                'players': {'players': [{'player_id': ident, 'name': 'Player ' + ident,
                                        'score': self.score, 'kills': self.score, 'deaths': 1,
                                        'team': 'red', 'address': '198.51.100.17'}]},
                'bans': {'players': [{'id': ident, 'name': 'Banned ' + ident, 'banned_by': 'Test'}]},
                'vpn': {'entries': []}, 'maps': {'maps': []}, 'modes': {'modes': []}}
        return {'ok': True, 'data': data.get(command, {})}

    async def submit(self, *args, **kwargs):
        raise AssertionError('Navigation must never mutate a game')


def test_cached_fleet_poll_only_schedules_work_for_a_new_snapshot():
    async def run():
        fleet = FixtureFleet()
        app = FleetApp(fleet)
        async with app.run_test(size=(150, 44)) as pilot:
            await pilot.pause(.3)
            app.overview_updated = fleet.updated
            original = app.fetch
            app.fetch = Mock()
            for _ in range(20):
                app.poll_slow()
            app.fetch.assert_not_called()
            fleet.updated += .001
            app.poll_slow()
            assert len(app.fetch.call_args_list) == len(app.stations) - 1
            assert all(call.args[0] is not app.cur for call in app.fetch.call_args_list)
            app.poll_slow()
            assert len(app.fetch.call_args_list) == len(app.stations) - 1
            app.fetch = original
    asyncio.run(run())


def test_panels_stay_in_place_and_return_to_fresh_selected_data(monkeypatch):
    monkeypatch.delenv('TEXTUAL_ANIMATIONS', raising=False)

    async def run():
        fleet = FixtureFleet()
        app = FleetApp(fleet)
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(.3)
            assert app.animation_level == 'none'
            body = app.query_one('#body').region
            sidebar = app.query_one('#sidebar').region
            for key in ('f3', 'f4', 'f5', 'f9', 'f1'):
                await pilot.press(key)
                await pilot.pause(.02)
                pane = app.query_one(TabbedContent).active_pane
                assert pane.styles.offset.resolve(pane.size, app.size).x == 0
                assert app.query_one('#body').region == body
                assert app.query_one('#sidebar').region == sidebar
            roster = app.query_one('#players', Roster)
            widths = [column.width for column in roster.columns.values()]
            await pilot.press('f5')
            fleet.score = 100
            await app._fetch(app.cur, ('players',))
            assert '100' not in [str(c) for c in roster.cells[0]]  # hidden pane did no work
            await pilot.press('f1')
            await pilot.pause(.1)
            assert roster.cells[0][3] == '100'
            assert [column.width for column in roster.columns.values()] == widths
            # Revealed addresses must not survive a redaction toggle while another pane is active.
            app.action_redact()
            await pilot.press('f5')
            app.action_redact()
            await pilot.press('f1')
            assert app.redact
            assert '198.51.100.17' not in str(app.query_one('#dossier-raw').content)
            await pilot.press('2')
            await pilot.pause(.2)
            selected = app.cur
            await pilot.press('f4')
            await pilot.pause(.2)
            assert app.cur is selected
            assert any(selected.rcon.ident in str(v) for v in app.ban_rows.values())
    asyncio.run(run())


def test_hidden_logs_are_bounded_before_their_first_layout():
    async def run():
        app = FleetApp(FixtureFleet())
        async with app.run_test(size=(150, 44)) as pilot:
            await pilot.pause(.3)
            for i in range(UI_LOG_LIMIT * 2 + 7):
                app.log_event(app.cur, {'event': 'chat', 'name': 'Demo', 'text': f'message {i}'})
                app.log_cmd(Text(f'command result {i}'))
            log = app.query_one('#feed', RichLog)
            console = app.query_one('#console-log', RichLog)
            assert len(app.feed) == len(app.feed_pending) == UI_LOG_LIMIT
            assert len(log._deferred_renders) <= UI_LOG_LIMIT
            assert len(console._deferred_renders) == UI_LOG_LIMIT
            await pilot.press('f2')
            await pilot.pause(.2)
            assert not app.feed_pending
            assert len(log.lines) <= UI_LOG_LIMIT and not log._deferred_renders
            await pilot.press('f5')
            await pilot.pause(.1)
            assert len(console.lines) <= UI_LOG_LIMIT and not console._deferred_renders
    asyncio.run(run())


def test_registry_refresh_keeps_scrollback_and_releases_unselected_detail():
    async def run():
        app = FleetApp(FixtureFleet())
        async with app.run_test(size=(150, 30)) as pilot:
            await pilot.pause(.3)
            await pilot.press('f2')
            for i in range(100):
                app.log_event(app.cur, {'event': 'chat', 'name': 'Demo', 'text': f'message {i}'})
            await pilot.pause(.1)
            log = app.query_one('#feed', RichLog)
            log.scroll_to(y=3, animate=False)
            await pilot.pause(.1)
            before = log.scroll_y, log.auto_scroll, len(log.lines)
            other = next(st for st in app.stations if st is not app.cur)
            other.data['bans'] = {'players': [{'id': 'old'}]}
            other.first_seen['old'] = 0
            await app.refresh_inventory()
            await pilot.pause(.1)
            assert (log.scroll_y, log.auto_scroll, len(log.lines)) == before
            assert set(other.data) <= {'status'} and not other.first_seen
    asyncio.run(run())


def test_disconnect_disables_selected_controls_without_waiting_for_a_poll():
    async def run():
        app = FleetApp(FixtureFleet())
        async with app.run_test(size=(150, 44)) as pilot:
            await pilot.pause(.3)
            await pilot.press('f3')
            await pilot.pause(.1)
            assert not app.query_one('#op-endgame', Button).disabled
            app.cur.rcon.state = 'offline'
            app.cur.rcon.ws = None
            app.on_rcon_state(app.cur.rcon, 'offline', 'connection lost')
            assert app.query_one('#op-load', Button).disabled
            assert app.query_one('#op-endgame', Button).disabled
    asyncio.run(run())


def test_unchanged_superintendent_reuses_rendered_face_and_card(monkeypatch):
    draw = Mock(wraps=superintendent.face)
    monkeypatch.setattr(superintendent, 'face', draw)
    sup = Superintendent(motion=False, now=lambda: 100.0)
    first = card(sup, 32, 12)
    for _ in range(100):
        current = card(sup, 32, 12)
        assert current[0] is first[0]
    assert draw.call_count == 1
    sup.react({'event': 'cheat'}, 'New alert')
    assert card(sup, 32, 12)[1] != first[1]
    assert draw.call_count == 2
    card(sup, 32, 16)
    assert draw.call_count == 3
