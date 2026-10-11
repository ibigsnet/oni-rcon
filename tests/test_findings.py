import asyncio
import time

from textual.widgets import Input, Static

from oni_rcon.findings import Findings
from oni_rcon.fleet import FleetApp
from oni_rcon.finding_view import FindingsView
from test_terminal_performance import FixtureFleet


def test_repeated_votes_and_reciprocal_fights_ignore_results_and_chat():
    clock = [1000.]
    f = Findings(lambda: clock[0])
    def vote(a, b, server='s'):
        clock[0] += 10
        return f.ingest(server, {'event': 'vote', 'name': a, 'text': 'called a vote to kick '+b})
    assert not vote('A', 'B')
    assert not vote('B', 'A')
    assert not vote('A', 'B')
    assert vote('B', 'A')[0].kind == 'vote_fight'
    assert vote('C', 'B')[0].kind == 'repeated_votes'
    assert not vote('C', 'B')  # cooldown
    assert not vote('A', 'B', 'other')
    assert not f.ingest('s', {'event': 'chat', 'name': 'A', 'text': 'called a vote to kick B'})
    assert not f.ingest('s', {'event': 'vote', 'name': 'A', 'text': 'vote to kick passed (4 to 0)'})
    clock[0] += 301
    assert not vote('A', 'B')


def test_timing_dedup_gaps_and_highlights_do_not_prove_cheating():
    clock = [1000.]
    f = Findings(lambda: clock[0])
    last = None
    for i in range(5):
        clock[0] += 1
        event = {'event': 'kill', 'time': clock[0], 'killer': 'A', 'victim': str(i)}
        last = f.ingest('s', event)
        assert not f.ingest('s', event)
    assert last[0].kind == 'kill_burst' and last[0].level == 'highlight'
    assert 'not evidence' in last[0].evidence
    f.reset('s')
    assert not f.ingest('s', {'event': 'kill', 'killer': 'A', 'victim': 'B'})
    assert not f.ingest('s', {'event': 'kill', 'time': clock[0]-31, 'killer': 'A', 'victim': 'C'})
    for i in range(20):
        assert not f.ingest('s', {'event': 'kill', 'killer': 'A', 'victim': 'A'})
        assert not f.ingest('s', {'event': 'kill', 'killer': 'A', 'victim': 'B', 'betrayal': True})


def test_extreme_native_rate_is_review_only_and_explicit_medal_required():
    clock = [1000.]
    f = Findings(lambda: clock[0])
    for i in range(12):
        clock[0] += .1
        f.ingest('s', {'event': 'kill', 'time': clock[0], 'killer': 'A', 'victim': str(i)})
    assert f.recent()[0].kind == 'unusual_rate'
    assert f.recent()[0].level == 'review'
    assert not any(r.kind == 'perfection' for r in f.recent())
    report = f.ingest('s', {'event': 'medal', 'name': 'A', 'medal': 'Perfection'})[0]
    assert report.kind == 'perfection' and 'Server reported' in report.title
    before = f.recent()
    f.acknowledge()
    assert f.recent() == before
    assert all(r.at <= f.acknowledged_at for r in before)
    clock[0] += 3601
    assert not f.recent()


def test_clear_every_station_even_when_filtered_and_keep_evidence():
    async def run():
        app = FleetApp(FixtureFleet())
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(.2)
            app.query_one('#fleet-search', Input).value = 'no-such-server'
            await pilot.pause(.1)
            assert not app.visible_ids
            for st in app.stations:
                st.alerts, st.flash = 3, time.monotonic()+20
                app.cards[st.index].parent.styles.background = 'red'
                app.lit.add(st.index)
            app.alert_at = time.monotonic()
            app.banner_until = time.monotonic()+20
            app.query_one('#banner').add_class('-on')
            app.notify('Old alert')
            f = app.findings.ingest('0', {'event': 'cheat', 'name': 'Player', 'text': 'native reason'})[0]
            app.log_event(app.stations[0], {'event': 'chat', 'text': 'retained'})
            history = list(app.feed)
            await pilot.click('#clear-alerts')
            assert app.unseen == 0 and app.alert_at == float('-inf')
            assert not app.lit and all(st.flash == 0 for st in app.stations)
            assert not app.banner_until and not app.query_one('#banner').has_class('-on')
            assert not app._notifications
            assert list(app.feed) == history and f in app.findings.recent()
            assert app.findings.acknowledged_at >= f.at
            await pilot.press('f11')
            assert isinstance(app.screen, FindingsView)
            assert app.screen.query_one('#findings-history').content_size.height >= 3
            await pilot.click('#findings-ack')
            await pilot.press('escape')
            assert not isinstance(app.screen, FindingsView)
            app.alert(None)
            assert app.condition()[0] == 'RED'
            await pilot.press('ctrl+l')
            assert app.condition()[0] != 'RED'
            assert app.query_one('#sidebar-footer').region.bottom == app.query_one('#sidebar').region.bottom
    asyncio.run(run())
