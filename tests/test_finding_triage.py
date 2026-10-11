import asyncio
import time
from unittest.mock import AsyncMock, Mock

import pytest
from textual.widgets import Button, Input, OptionList, TabbedContent

from oni_rcon.app import Confirm, Roster
from oni_rcon.findings import Findings, chat_request, public_roster
from oni_rcon.finding_view import FindingsView
from oni_rcon.fleet import FleetApp
from test_terminal_performance import FixtureFleet
from test_tenant_power import wait_for


@pytest.mark.parametrize('text,kind', [
    ('need an admin', 'admin_help'), ('any mods online?', 'admin_help'),
    ('looking for help', 'admin_help'), ('can someone help me?', 'admin_help'),
    ('help please', 'admin_help'), ('please kick Red', 'moderation_request'),
    ('Can someone ban Red for team killing', 'moderation_request'),
    ('Red needs to be kicked', 'moderation_request'),
    ('Red is cheating', 'player_report'), ('Red is using an aimbot', 'player_report'),
    ('admin? Red is hacking', 'player_report'),
    ('did halo 3 had these tf2 fan mod modes on release', None),
    ("that should be a no brainer, that's the whole point of this mod", None),
    ('How do I kick someone?', None), ('called a vote to kick Red', None),
    ("don't kick Red", None), ('Red is not cheating', None),
    ('I installed a mod', None), ('this map has cheat codes', None),
    ('help me capture the flag', None), ('kick button is broken', None),
])
def test_chat_intent_keeps_real_requests_and_rejects_observed_noise(text, kind):
    request = chat_request(text)
    assert (request.kind if request else None) == kind


@pytest.mark.parametrize('context', [
    {'mode': 'Infection'}, {'mode': 'Slayer', 'name': 'Big Team SWAT'},
    {'mode': 'Slayer', 'tags': ['swat']}, {'name': 'Mega Fat Kid'},
])
def test_fast_modes_suppress_kill_heuristics_but_preserve_review_and_medals(context):
    clock = [1000.]
    f = Findings(lambda: clock[0])
    for i in range(15):
        clock[0] += .1
        assert not f.ingest('s', dict(event='kill', time=clock[0], killer='A', victim=str(i)), context=context)
    assert f.ingest('s', dict(event='chat', name='B', text='need an admin'), context=context)[0].kind == 'admin_help'
    assert f.ingest('s', dict(event='cheat', name='A', text='native reason'), context=context)[0].kind == 'native_flag'
    assert f.ingest('s', dict(event='medal', name='A', medal='Perfection'), context=context)[0].kind == 'perfection'
    for i in range(3):
        clock[0] += 5
        result = f.ingest('s', dict(event='vote', name='B', text='called a vote to kick A'), context=context)
    assert result[0].kind == 'repeated_votes'


def test_repeat_suppression_survives_phase_and_reconnect_and_new_targets_still_alert():
    clock = [1000.]
    f = Findings(lambda: clock[0])
    chat = dict(event='chat', id='reporter-id', name='Reporter', text='please kick Red')
    first = f.ingest('s', chat)[0]
    assert 'Reporter' in first.title and 'reporter-id' not in first.title
    assert first.subjects[0]['name'] == 'red'
    f.ingest('s', dict(event='phase'))
    f.reset('s')
    clock[0] += 65
    assert not f.ingest('s', {**chat, 'id': 'another', 'name': 'Another'})
    assert f.ingest('s', {**chat, 'text': 'please kick Blue'})
    assert f.ingest('other-server', chat)
    clock[0] += 121
    assert f.ingest('s', chat)
    assert not f.ingest('s', {**chat, 'channel': 'server'})


def test_subject_is_the_reported_person_and_never_a_guessed_slot_or_duplicate_name():
    roster = public_roster([
        {'id': 'a'*64, 'engine_id': 1, 'name': 'Reporter', 'ip': 'private'},
        {'id': 'b'*64, 'engine_id': 2, 'name': 'Red', 'device': 'private'},
    ])
    f = Findings()
    row = f.ingest('s', dict(event='chat', id='a'*64, name='Reporter', text='admin? Red is hacking'), roster=roster)[0]
    assert row.subjects == ({'id': 'b'*64, 'name': 'Red'},)
    assert row.reporter == {'id': 'a'*64, 'name': 'Reporter'}
    assert 'private' not in str(row.row())
    native = f.ingest('s', dict(event='cheat', id=2), roster=roster)[0]
    assert native.subjects[0]['id'] == 'b'*64
    roster.append({'id': 'c'*64, 'engine_id': '3', 'name': 'Red'})
    row = f.ingest('other', dict(event='chat', name='Reporter', text='please ban Red'), roster=roster)[0]
    assert row.subjects[0]['id'] == ''


def test_mode_change_drops_partial_kill_window_and_highlights_have_server_and_player_cooldowns():
    clock = [1000.]
    f = Findings(lambda: clock[0])
    def burst(actor, mode, count=5):
        result = []
        for i in range(count):
            clock[0] += 1
            result += f.ingest('s', dict(event='kill', killer=actor, victim=str(i)), context={'mode': mode})
        return result
    assert not burst('A', 'Slayer', 4)
    assert not burst('A', 'CTF', 1)
    assert burst('A', 'CTF', 4)[0].kind == 'kill_burst'
    assert not burst('B', 'CTF')
    clock[0] += 61
    assert not burst('A', 'CTF')
    assert burst('B', 'CTF')


def test_chat_notifications_use_one_filtered_path_and_click_opens_verified_subject(monkeypatch):
    async def run():
        fleet = FixtureFleet()
        app = FleetApp(fleet)
        async with app.run_test(size=(100, 34), notifications=True) as pilot:
            assert await wait_for(pilot, lambda: all(s.online for s in app.stations))
            st = app.stations[-1]
            await app._fetch(st, ('players',))
            p = st.players[0]
            app.clear_notifications()
            bell = Mock()
            monkeypatch.setattr(app, 'bell', bell)
            event = dict(event='chat', name='Reporter', id='a'*64, text='please kick '+p['name'])
            app.on_rcon_event(st.rcon, event)
            app.on_rcon_event(st.rcon, event)
            app.on_rcon_event(st.rcon, dict(event='chat', name='Reporter', text='the whole point of this mod'))
            assert bell.call_count == 1
            assert len(app.findings.recent()) == 1
            row = app.findings.recent()[0]
            assert row.subjects[0]['id'] == p['player_id']
            await pilot.pause(.2)
            # Click the real notification's action text, not the action method directly.
            toast = app.screen.query_one('Toast')
            assert 'VIEW PLAYER' in str(toast.render())
            point = next((x-toast.region.x, y-toast.region.y)
                         for y in range(toast.region.y, toast.region.bottom)
                         for x in range(toast.region.x, toast.region.right)
                         if app.screen.get_style_at(x, y).meta.get('@click') == f"app.open_finding('{row.id}')")
            await pilot.click(toast, offset=point)
            assert await wait_for(pilot, lambda: app.cur is st and app.cur_player() is not None
                                  and app.cur_player().get('player_id') == p['player_id'])
            assert app.query_one(TabbedContent).active == 'assets'
            assert app.query_one('#finding-context').display
            assert app.query_one('#players', Roster).show_cursor
    asyncio.run(run())


def test_findings_links_cross_filters_and_left_player_does_not_select_someone_else():
    async def run():
        fleet = FixtureFleet()
        app = FleetApp(fleet)
        async with app.run_test(size=(80, 24)) as pilot:
            assert await wait_for(pilot, lambda: all(s.online for s in app.stations))
            st = app.stations[-1]
            row = app.findings.ingest(str(st.index), dict(event='chat', name='Reporter', text='please kick Departed'),
                                     roster=public_roster([dict(id='d'*64, name='Departed')]))[0]
            app.query_one('#fleet-search', Input).value = 'no-such-server'
            await pilot.press('f11')
            assert isinstance(app.screen, FindingsView)
            assert app.screen.query_one('#findings-history').content_size.height >= 3
            assert app.screen.query_one('.dialog').content_region.contains_region(app.screen.query_one('#findings-close').region)
            app.screen.query_one('#findings-history', OptionList).focus()
            await pilot.press('enter')
            assert await wait_for(pilot, lambda: app.cur is st and app.query_one('#finding-context').display)
            assert st.rcon.ident in app.visible_ids
            assert not app.query_one('#players', Roster).show_cursor
            assert app.cur_player() is None
            assert app.query_one('#pl-kick', Button).disabled
            await pilot.press('down')
            assert app.cur_player() is not None  # An explicit roster choice restores ordinary controls.
    asyncio.run(run())


@pytest.mark.parametrize('command', ['endround', 'endgame'])
def test_match_buttons_cancel_without_commands_and_confirm_only_the_named_server(command):
    async def run():
        app = FleetApp(FixtureFleet())
        async with app.run_test(size=(80, 24)) as pilot:
            assert await wait_for(pilot, lambda: all(s.online for s in app.stations))
            app.cmd = AsyncMock(return_value={'ok': True})
            target = app.cur
            button = app.query_one('#match-'+command, Button)
            assert button.region.right <= 80 and button.region.bottom <= 24
            await pilot.click(button)
            assert await wait_for(pilot, lambda: isinstance(app.screen, Confirm))
            assert target.label in str(app.screen.b)
            assert app.screen.focused.id == 'no'
            await pilot.press('escape')
            app.cmd.assert_not_called()
            await pilot.click(button)
            assert await wait_for(pilot, lambda: isinstance(app.screen, Confirm))
            app.action_station(1-target.index)
            await pilot.click('#yes')
            assert await wait_for(pilot, lambda: app.cmd.call_count == 1)
            assert app.cmd.call_args.args == (target, command)
            app.cur.rcon.state, app.cur.rcon.ws = 'offline', None
            app.on_rcon_state(app.cur.rcon, 'offline', 'lost')
            assert app.query_one('#match-endround', Button).disabled
            assert app.query_one('#match-endgame', Button).disabled
    asyncio.run(run())
