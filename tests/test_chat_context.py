"""Conversation evidence around alerts: timing, bounded history, gaps and navigation."""
import asyncio
import json
import time
from dataclasses import replace
from unittest.mock import Mock

from textual.widgets import RichLog, Button

from oni_rcon.findings import Findings, Finding, public_roster, CHAT_LIMIT
from oni_rcon.finding_view import FindingsView
from oni_rcon.fleet import FleetApp
from test_terminal_performance import FixtureFleet
from test_tenant_power import wait_for


def harness():
    clock = [1000.]
    f = Findings(lambda: clock[0])
    def chat(text, server='s', name='Reporter', **kw):
        clock[0] += 1
        return f.ingest(server, dict(event='chat', time=clock[0], name=name, text=text, **kw))
    return clock, f, chat


def test_before_trigger_and_followups_include_suppressed_requests_without_realerting():
    clock, f, chat = harness()
    chat('Red has been blocking the door')
    chat('Which player?', name='Blue')
    row = chat('Kick him!')[0]
    assert not row.subjects  # A pronoun never guesses a player to moderate.
    assert [m['position'] for m in row.chat_context['messages']] == ['before', 'before', 'trigger']
    assert not chat('Kick him!', name='Blue')  # Suppressed as an alert, retained as evidence.
    assert not chat('I meant Red, sorry', name='Reporter')
    current = f.recent()[0]
    assert current.id == row.id and len(f.recent()) == 1
    assert [m['text'] for m in current.chat_context['messages']] == [
        'Red has been blocking the door', 'Which player?', 'Kick him!', 'Kick him!', 'I meant Red, sorry']
    assert [m['position'] for m in current.chat_context['messages']][-2:] == ['after', 'after']
    assert current.chat_context['revision'] > row.chat_context['revision']


def test_context_is_server_scoped_timed_bounded_and_ignores_server_broadcasts():
    clock, f, chat = harness()
    chat('too old')
    clock[0] += 121
    chat('other lobby', server='other')
    chat('automated announcement', channel='server')
    for i in range(CHAT_LIMIT+5):
        chat('before '+str(i))
    row = chat('Kick him!')[0]
    assert row.chat_context['before_limited']
    assert len(row.chat_context['messages']) == CHAT_LIMIT+1
    assert row.chat_context['messages'][0]['text'] == 'before 5'
    for i in range(CHAT_LIMIT+5):
        assert not chat('after '+str(i))
    ctx = f.recent()[0].chat_context
    assert len(ctx['messages']) == 2*CHAT_LIMIT+1 and ctx['status'] == 'limited'
    assert ctx['messages'][-1]['text'] == 'after 19'
    clock, f, chat = harness()
    row = chat('Kick him!')[0]
    clock[0] += 121
    chat('outside follow-up window')
    assert f.recent()[0].chat_context['status'] == 'complete'
    assert len(f.recent()[0].chat_context['messages']) == 1
    clock[0] += 3601
    assert not f.recent()


def test_round_changes_preserve_conversation_but_gaps_close_capture():
    clock, f, chat = harness()
    chat('Red has been blocking the door')
    f.ingest('s', {'event': 'phase'})
    row = chat('Kick him!')[0]
    assert row.chat_context['messages'][0]['text'] == 'Red has been blocking the door'
    f.ingest('s', {'event': 'phase'})
    chat('now the round ended')
    f.ingest('s', {'event': 'lagged'})
    chat('after missing events')
    current = f.recent()[0]
    assert current.chat_context['status'] == 'interrupted'
    assert len(current.chat_context['messages']) == 3
    clock[0] += 61
    newer = chat('please ban Blue')[0]
    assert newer.chat_context['messages'][0]['text'] == 'after missing events'
    f.reset('s')
    assert f.recent()[0].chat_context['status'] == 'interrupted'


def test_timestamps_deduplicate_and_do_not_accept_out_of_order_or_stale_chat():
    clock, f, chat = harness()
    chat('recent')
    event = dict(event='chat', time=clock[0]+1, name='Reporter', text='Kick him!')
    clock[0] += 1
    row = f.ingest('s', event)[0]
    assert not f.ingest('s', event)
    assert not f.ingest('s', {**event, 'time': clock[0]-1, 'text': 'late'})
    assert not f.ingest('s', {**event, 'time': clock[0]-31, 'text': 'stale'})
    assert len(f.recent()[0].chat_context['messages']) == 2
    assert f.recent()[0].id == row.id


def test_public_speaker_identity_and_literal_sanitized_text_only():
    clock, f, _ = harness()
    roster = public_roster([{'id': 'a'*64, 'engine_id': 1, 'name': '[b]Reporter[/b]',
                            'ip': 'secret-address', 'device': 'secret-device'}])
    row = f.ingest('s', dict(event='chat', name='[b]Reporter[/b]', id=1,
                  text='Kick him! rfk_example_secret\x1b\n', channel='team'), roster=roster)[0]
    message = row.chat_context['messages'][0]
    assert message['speaker'] == {'id': 'a'*64, 'name': '[b]Reporter[/b]'}
    assert message['channel'] == 'team' and '[API KEY]' in message['text']
    encoded = json.dumps(row.row())
    assert all(secret not in encoded for secret in ('secret-address', 'secret-device', 'rfk_example_secret', '\\u001b'))


def test_non_chat_review_findings_also_get_surrounding_conversation():
    clock, f, chat = harness()
    chat('Red is floating above the map')
    row = f.ingest('s', dict(event='cheat', name='Red', text='native report'))[0]
    chat('he is back on the ground')
    current = f.recent()[0]
    assert row.kind == 'native_flag'
    assert [m['position'] for m in current.chat_context['messages']] == ['before', 'after']


def test_polled_evidence_replaces_same_finding_without_realerting_or_regressing():
    clock, observer, chat = harness()
    row = chat('Kick him!')[0]
    client = Findings(lambda: clock[0])
    assert client.add(Finding(**row.row()))
    client.acknowledge()
    chat('I meant Red')
    updated = observer.recent()[0]
    assert not client.add(Finding(**updated.row()))
    assert len(client.recent()[0].chat_context['messages']) == 2
    assert not client.add(row)  # Older poll cannot erase a captured follow-up.
    assert not client.add(replace(updated, server='other', chat_context={'revision': 100}))
    assert client.recent()[0].chat_context == updated.chat_context
    assert client.acknowledged_at == row.at
    client.reset('s')  # A fleet transport reconnect is not a gap in its independent observer.
    assert client.recent()[0].chat_context == updated.chat_context


def test_expanded_evidence_updates_without_scroll_jump_and_speaker_link_revalidates():
    async def run():
        app = FleetApp(FixtureFleet())
        async with app.run_test(size=(80, 24)) as pilot:
            assert await wait_for(pilot, lambda: all(s.online for s in app.stations))
            st = app.stations[-1]
            await app._fetch(st, ('players',))
            player = st.players[0]
            app.on_rcon_event(st.rcon, dict(event='chat', name=player['name'], player_id=player['player_id'],
                                          text='The player blocking the door is Red'))
            app.on_rcon_event(st.rcon, dict(event='chat', name='Reporter', text='Kick him!'))
            await pilot.press('f11')
            view = app.screen
            assert isinstance(view, FindingsView)
            row = app.findings.recent()[0]
            await pilot.click('#findings-expand')
            await pilot.pause(.1)
            detail = view.query_one('#findings-detail', RichLog)
            assert not view.query_one('#findings-history').display
            assert detail.region.height >= 10
            assert view.query_one('.dialog').content_region.contains_region(view.query_one('#findings-close').region)
            assert 'TRIGGER' in '\n'.join(line.text for line in detail.lines)
            for i in range(3):
                app.on_rcon_event(st.rcon, dict(event='chat', name='Reporter', text='follow-up '+str(i)))
            view.refresh_findings()
            await pilot.pause(.1)
            detail.scroll_to(y=2, animate=False)
            await pilot.pause(.1)
            before = detail.scroll_y
            app.on_rcon_event(st.rcon, dict(event='chat', name='Reporter', text='last follow-up'))
            view.refresh_findings()
            await pilot.pause(.1)
            assert detail.scroll_y == before
            assert 'last follow-up' in '\n'.join(line.text for line in detail.lines)
            detail.scroll_to(y=0, animate=False)
            await pilot.pause(.1)
            action = f"app.open_finding('{row.id}', 'chat', 0)"
            point = next((x-detail.region.x, y-detail.region.y)
                         for y in range(detail.region.y, detail.region.bottom)
                         for x in range(detail.region.x, detail.region.right)
                         if app.screen.get_style_at(x, y).meta.get('@click') == action)
            await pilot.click(detail, offset=point)
            assert await wait_for(pilot, lambda: app.cur is st and app.cur_player() is not None
                                  and app.cur_player().get('player_id') == player['player_id'])
    asyncio.run(run())


def test_fleet_poll_imports_followups_without_a_second_notification():
    async def run():
        fleet = FixtureFleet()
        app = FleetApp(fleet)
        async with app.run_test(size=(100, 34)) as pilot:
            assert await wait_for(pilot, lambda: all(s.online for s in app.stations))
            st = app.stations[-1]
            observer = Findings()
            first = observer.ingest(st.rcon.ident, dict(event='chat', name='Reporter', text='Kick him!'))[0]
            def response(*args):
                row = observer.recent()[0].row()
                ctx = row.pop('chat_context')
                links = {k: row.pop(k) for k in ('subjects', 'reporter')}
                return {'at': time.time(), 'findings': [row], 'links': {first.id: links},
                        'chat_contexts': {first.id: ctx}, 'nodes': []}
            fleet.request = response
            app.findings_loaded = True
            app.report_finding = Mock()
            await app.refresh_findings().wait()
            observer.ingest(st.rcon.ident, dict(event='chat', name='Reporter', text='I meant Red'))
            await app.refresh_findings().wait()
            assert app.report_finding.call_count == 1
            assert len(app.findings.recent()[0].chat_context['messages']) == 2
    asyncio.run(run())
