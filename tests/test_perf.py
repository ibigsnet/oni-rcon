"""Frame rate read from the server's own "Server falling behind" lines (perf.py), on F10, F3 and the cards."""
import asyncio
import time
from unittest.mock import Mock

from textual.widgets import TabbedContent

from oni_rcon import fleet as cli
from oni_rcon import health as hc
from oni_rcon import perf as pf
from oni_rcon.app import OniApp
from oni_rcon.config import Server
from oni_rcon.demo import FakeHost, serve_fakes
from oni_rcon.fleet_health import project
from oni_rcon.health import HealthSetup, Monitor
from test_fleet_health import snapshot
from test_health import grid_text, ready, table_text
from test_terminal_performance import FixtureFleet

T0 = hc.parse_ts("2026-10-14T04:15:00Z")


def lag(clock, slow=58, span=58, avg=29, frame=125, players=85):
    return (f"{clock} Server falling behind: under 54 frames a second for {slow} of the last {span} seconds ({avg} on "
            f"average, longest frame {frame} ms) with {players} players. Each player gets one update a frame, so they "
            f"see lag: give this server more processor time or fewer players.")


def host(now=T0, perf=True):
    return (f"host now={int(now)} clock={time.strftime('%H:%M:%S', time.gmtime(now))} mem_available_mb=4000 "
            f"swap_used_mb=0 swap_total_mb=0 oom_kill=0{' perf=1 load_pct=93 cpus=8' if perf else ''}\n")


def test_a_minute_counts_its_slow_seconds_at_their_average_and_the_rest_at_60():
    m = pf.minute(lag("04:13:33", slow=30, span=60, avg=40), 1.0)
    assert (m.slow, m.span, m.avg, m.frame_ms, m.players) == (30, 60, 40, 125, 85)
    assert m.fps == 50
    assert pf.minute("04:13:33 Network upload over the last 5 minutes: 13 MB/s to 67 players", 1.0) is None
    assert pf.minute(lag("04:13:33", slow=70, span=60), 1.0) is None  # impossible: not read
    assert pf.level(60) == "ok" and pf.level(45) == "warn" and pf.level(30) == "crit"


def test_both_log_shapes_and_the_window():
    at = hc.clock_for({"now": T0, "clock": "04:15:00"}, T0)
    lines = [lag("04:13:33", avg=29), "2026-10-14T04:14:33.83Z [Mega] " + lag("", avg=25).strip(),
             lag("03:00:00", avg=5)]  # an hour ago: kept, but outside the 5-minute window
    got = pf.minutes_in(lines, at, hc.split_stamp)
    assert [m.avg for m in got] == [29, 25, 5]
    assert got[0].ts == T0 - 87 and round(got[1].ts - T0, 2) == -26.17
    p = pf.Perf()
    for m in got:
        p.add(m)
    assert not p.add(got[0])  # read twice, kept once
    p.read_at = T0
    r = p.reading(T0)
    assert round(r.fps) == 25 and r.worst == 25 and r.frame_ms == 125 and r.slow_minutes == 2 and r.players == 85
    assert r.behind
    assert 45 < r.avg < 50  # two minutes 58 s at ~27, the rest of the 5 at 60
    later = p.reading(T0 + 600)  # nothing new for 10 minutes: it kept up
    assert later.fps == 60 and later.avg == 60 and later.worst is None and not later.behind


def test_a_report_without_lag_lines_only_counts_when_it_says_it_would_carry_them():
    mon = Monitor(started=T0 - 3600)
    mon.ingest("0", "", host(perf=False), now=T0)  # an older `oni health-report`: crash lines only
    assert mon.frame_rate("0", T0) is None
    mon.ingest("0", "", host(), now=T0)
    assert mon.frame_rate("0", T0).fps == 60
    assert mon.hosts[""].load_pct == 93 and mon.hosts[""].cpus == 8
    mon.ingest("1", "", "2026-10-14T04:14:00Z Network upload over the last 5 minutes: 1 MB/s to 9 players\n", now=T0)
    assert mon.frame_rate("1", T0).fps == 60  # a raw log says so by itself


def test_the_feed_hears_once_when_it_gets_choppy_and_once_when_it_recovers():
    mon = Monitor(started=T0 - 3600)
    notes = mon.ingest("0", "", host() + lag("04:14:33", avg=29) + "\n", now=T0)
    assert len(notes) == 1 and notes[0].quiet and "FALLING BEHIND" in notes[0].text and "85 players" in notes[0].text
    assert not mon.ingest("0", "", host(T0 + 60) + lag("04:15:33", avg=31) + "\n", now=T0 + 60)
    back = mon.ingest("0", "", host(T0 + 400), now=T0 + 400)
    assert len(back) == 1 and "KEEPING UP" in back[0].text
    assert not mon.ingest("0", "", host(T0 + 460), now=T0 + 460)


def test_snapshot_rows_are_read_strictly():
    assert pf.from_row([T0, 58, 58, 29, 125, 85]).fps < 30
    for bad in ([T0, 58, 58, 29, 125], [T0, 70, 58, 29, 125, 85], [T0, True, 58, 29, 125, 85],
                [T0, 58, 58, float("nan"), 125, 85], "58,58", None):
        assert pf.from_row(bad) is None


def test_demo_frame_rate_reaches_f10_f3_and_the_card():
    async def go():
        host_ = FakeHost(autorun=False)
        ports, keep = await serve_fakes(tick=False, host=host_)
        app = OniApp([Server(port=p, password="demo") for p in ports], by="pytest", intro=False,
                     health=HealthSetup(source=host_))
        async with app.run_test(size=(170, 50)) as pilot:
            await ready(app, pilot)
            await pilot.press("f10")
            await pilot.pause(0.3)
            t = app.query_one("#perf")
            rows = [[str(c) for c in t.get_row_at(r)] for r in range(t.row_count)]
            assert len(rows) == 3
            assert rows[0][1:5] == ["38", "41", "33", "131 ms"]  # the slowest first
            assert all(r[1:3] == ["60", "60"] and r[3] == "—" for r in rows[1:])
            assert "1 FALLING BEHIND" in str(app.query_one("#perf").border_subtitle)
            assert "CPU LOAD" in grid_text(app.query_one("#host")) and "of 4 cores" in grid_text(app.query_one("#host"))
            feed = " ".join(str(app.feed_line(st, ev)) for st, ev in app.feed if ev.get("event") == "health")
            assert "FALLING BEHIND" in feed
            assert not app.query_one("#banner").has_class("-on")  # a slow server is news, not an alarm
            slow = app.stations[0]
            assert slow.drawn[7] == 38 and all(st.drawn[7] is None for st in app.stations[1:])
            app.action_station(0)
            await pilot.pause(0.1)
            app.paint_ops()
            sitrep = grid_text(app.query_one("#sitrep"))
            assert "SERVER FPS" in sitrep and "38/60" in sitrep and "5m 41 · low 33" in sitrep

    asyncio.run(go())


def test_fleet_lag_rows_and_cpu_come_from_the_snapshot():
    async def run():
        fleet = FixtureFleet()
        fleet.health_enabled = True
        now = time.time()
        data = snapshot(now - 15)
        data['nodes'][0]['lobbies'].append({**data['nodes'][0]['lobbies'][0], 'id': 'node-b-49176'})
        slow = data['nodes'][0]['lobbies'][0]
        slow.update(cpu_seconds_total=1000.0, perf={'lags': [[now - 30, 59, 59, 36, 144, 65]]})
        data['nodes'][0]['lobbies'][1].update(perf={'lags': []})  # read, and keeping up
        fleet.health_snapshot = Mock(side_effect=lambda: project(data))
        app = cli.FleetApp(fleet)
        async with app.run_test(size=(170, 50)) as pilot:
            await pilot.pause(.2)
            app.fleet_health_due = time.monotonic() + 100
            await app.refresh_health()
            data['nodes'][0]['observed_at'] += 15
            slow['cpu_seconds_total'] += 57.0  # 3.8 cores
            await app.refresh_health()
            await pilot.press('f10')
            await pilot.pause(.1)
            assert app.query_one(TabbedContent).active == 'health'
            t = app.query_one('#perf')
            rows = [[str(c) for c in t.get_row_at(r)] for r in range(t.row_count)]
            assert rows[0][1:8] == ['36', '55', '36', '144 ms', '65', '380%', rows[0][7]] and rows[0][7].endswith('ago')
            assert rows[1][1] == '60' and rows[1][2] == '60'
            assert app.stations[0].drawn[7] == 36
            # a node whose agent doesn't read lag lines yet says nothing rather than "60"
            assert all(app.mon.frame_rate(str(s.index)) is None for s in app.stations[2:])
    asyncio.run(run())
