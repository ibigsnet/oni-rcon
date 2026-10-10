"""Fleet health reads a shared cache, never each worker's log or Docker socket."""
import asyncio
import io
import json
import time
from unittest.mock import Mock

import pytest
from textual.widgets import TabbedContent

from oni_rcon import fleet as cli
from oni_rcon.fleet_health import MAX_BYTES, SNAPSHOT, project, read_snapshot
from oni_rcon.health import HealthError
from oni_rcon.prefs import Prefs
from test_terminal_performance import FixtureFleet


def snapshot(now):
    return {'nodes': [{'id': n, 'observed_at': now, 'resources': {'memory_available_bytes': 8 * 1073741824,
        'cpu_fraction': .2, 'rx_mbps': 3, 'tx_mbps': 4}, 'lobbies': [{'id': n + '-49175',
        'started_at': now - 1000, 'restart_count': 0, 'oom_killed': False, 'password': 'must-not-survive'}]}
        for n in ('node-b', 'node-d')]}


def test_projection_keeps_duplicate_ports_separate_and_unknown_or_stale_readings():
    data = snapshot(1000)
    data['nodes'][0]['resources']['cpu_fraction'] = float('nan')
    data['nodes'][1]['observed_at'] = 800
    result = project(data, now=1001)
    assert result['nodes']['node-b']['cpu_fraction'] is None
    assert result['nodes']['node-b']['fresh']
    assert not result['lobbies']['node-d-49175']['fresh']
    assert len(result['lobbies']) == 2
    assert 'must-not-survive' not in json.dumps(result)
    assert not project(data, now=999)['nodes']['node-b']['fresh']


def test_fixed_health_destination_has_no_console_token_and_bounds_the_read():
    opener = Mock()
    opener.open.return_value = io.BytesIO(json.dumps(snapshot(time.time())).encode())
    assert read_snapshot(opener)['nodes']
    request = opener.open.call_args.args[0]
    assert request.full_url == SNAPSHOT
    assert request.get_header('Authorization') is None
    opener.open.return_value = io.BytesIO(b' ' * (MAX_BYTES + 1))
    with pytest.raises(HealthError, match='8 MiB'):
        read_snapshot(opener)


@pytest.mark.parametrize('size', [(80, 32), (160, 48)])
def test_fleet_health_uses_one_cached_read_and_keeps_panels_still(size):
    async def run():
        fleet = FixtureFleet()
        fleet.health_enabled = True
        data = snapshot(time.time())
        # Fixture also has 49176, independent of the cache example above.
        data['nodes'][0]['lobbies'].append({**data['nodes'][0]['lobbies'][0], 'id': 'node-b-49176'})
        fleet.health_snapshot = Mock(side_effect=lambda: project(data))
        app = cli.FleetApp(fleet)
        async with app.run_test(size=size) as pilot:
            await pilot.pause(.2)
            body = app.query_one('#body').region
            side = app.query_one('#sidebar').region
            app.fleet_health_due = time.monotonic() + 100
            await app.refresh_health()
            assert fleet.health_snapshot.call_count == 1
            assert set(app.mon.boxes) == {'0', '1'}
            assert not app.mon.events
            assert not app.fleet_health_error
            await pilot.press('f10')
            await pilot.pause(.1)
            assert app.query_one(TabbedContent).active == 'health'
            assert 'not collected' in str(app.query_one('#health-setup').content)
            assert 'not covered' in str(app.query_one('#crashes').border_subtitle)
            assert app.query_one('#body').region == body and app.query_one('#sidebar').region == side
            # A persistent OOM flag produces only one transition event.
            data['nodes'][0]['lobbies'][0]['oom_killed'] = True
            await app.refresh_health()
            await app.refresh_health()
            assert len(app.mon.events) == 1
            data['nodes'][0]['observed_at'] -= 120
            await app.refresh_health()
            assert all('stale' in s.health_err.lower() for s in app.stations)
            assert app.mon.boxes['0'].oom_killed  # last reading retained, never cleared by missing data
            assert len(app.mon.events) == 1
            await pilot.press('f3')
            assert app.query_one('#body').region == body
            titles = [c.title for c in app.get_system_commands(app.screen)]
            assert 'Health: refresh fleet telemetry' in titles
            assert 'Add a server' not in titles
            assert 'Forge: Load API key' not in titles
    asyncio.run(run())


@pytest.mark.parametrize('flags, expected', [([], 'full'), (['--intro', 'quick'], 'quick'),
                                         (['--intro=full', '--no-intro'], 'off')])
def test_fleet_cli_retains_discovery_and_persistent_boot_preferences(monkeypatch, tmp_path, flags, expected):
    fleet = FixtureFleet()
    monkeypatch.setattr(cli, 'Fleet', lambda: fleet)
    monkeypatch.setattr(cli.FleetApp, 'run', lambda self: seen.append(self))
    monkeypatch.setattr(cli.sys if hasattr(cli, 'sys') else __import__('sys'), 'argv', ['oni-rcon', *flags])
    monkeypatch.setenv('IBIGS_ONI_PREFS', str(tmp_path / 'prefs.json'))
    seen = []
    cli.main()
    app, = seen
    assert app.fleet is fleet and app.intro == expected and app.updater is None
    app.prefs.full_intro = True
    app.prefs.save()
    assert Prefs.load(tmp_path / 'prefs.json').full_intro
