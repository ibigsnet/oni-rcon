"""IBIGS integration coverage for optional join-log readers and sorted managed fleets."""
import asyncio
import contextlib
import json
import os
from pathlib import Path
import signal
import sys
import time
from unittest.mock import AsyncMock, Mock

import pytest
from textual.widgets import Input, Select

from oni_rcon.app import Card, ping_cell
from oni_rcon.config import Server, load_config, ping_command
from oni_rcon.fleet import FleetApp
from oni_rcon.pings import follow_join_pings, remote_command, stop_follower


@pytest.mark.parametrize('value', ['tail -f /log; echo nope', ['tail', 1], [''], [], ['x\x00y'], ['x'] * 65])
def test_ping_log_requires_explicit_bounded_arguments(value):
    with pytest.raises(ValueError, match='explicit argument list'):
        ping_command(value, 49175)


def test_ping_log_config_rejects_shell_strings_and_preserves_literal_arguments(tmp_path):
    config = tmp_path / 'config.toml'
    config.write_text('ping_log = "tail -f /log"\n[[server]]\nurl = "fleet://example"\n')
    with pytest.raises(SystemExit, match='shell commands are disabled'):
        load_config(config)
    assert ping_command(['helper', '$(nope);*', '/log/{port}'], 49175) == ['helper', '$(nope);*', '/log/49175']
    assert ping_cell(None).plain == '—' and ping_cell(0).plain == '0'


def test_managed_and_url_connections_never_start_a_log_process(monkeypatch):
    launch = AsyncMock()
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', launch)

    async def check():
        for url in ('fleet://node-b-49175', 'wss://example.test/lobby'):
            server = Server(url=url)
            server.ping_log = ['not-executed']
            await follow_join_pings(server, {})
        launch.assert_not_awaited()

    asyncio.run(check())


def process_running(pid):
    try:
        # A killed child may briefly await init's reaper; a zombie is no longer a running log follower.
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
    except FileNotFoundError:
        return False


def log_helper(tmp_path):
    script = tmp_path / 'follow.py'
    script.write_text('''import json, os, signal, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'])
Path(sys.argv[1]).write_text(json.dumps({'parent': os.getpid(), 'child': child.pid, 'args': sys.argv[2:]}))
print('[S] Tester connected (player ID abc123, ping 77 ms).', flush=True)
time.sleep(60)
''')
    metadata = tmp_path / 'pids.json'
    marker = tmp_path / 'must-not-be-created'
    literal = f'$(touch {marker}); echo unsafe'
    return [sys.executable, str(script), str(metadata), literal], metadata, marker, literal


async def wait_until(predicate, timeout=4):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.02)


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Linux process-group cleanup')
def test_local_log_follower_cancellation_cleans_its_children_and_never_uses_shell(tmp_path):
    async def check():
        argv, metadata, marker, literal = log_helper(tmp_path)
        server = Server(port=49175)
        server.ping_log = argv
        readings = {}
        task = asyncio.create_task(follow_join_pings(server, readings))
        try:
            await wait_until(lambda: bool(readings))
            record = json.loads(metadata.read_text())
            assert readings == {'abc123': 77} and record['args'] == [literal]
            assert not marker.exists()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 4)
            await wait_until(lambda: not any(process_running(record[key]) for key in ('parent', 'child')))
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(check())


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='POSIX SSH supervisor with setsid')
def test_remote_supervisor_quotes_arguments_and_cleans_children_when_ssh_stdin_closes(tmp_path):
    async def check():
        argv, metadata, marker, literal = log_helper(tmp_path)
        process = await asyncio.create_subprocess_exec(
            '/bin/sh', '-c', remote_command(argv), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
        record = None
        try:
            assert b'ping 77 ms' in await asyncio.wait_for(process.stdout.readline(), 4)
            record = json.loads(metadata.read_text())
            assert record['args'] == [literal] and not marker.exists()
            process.stdin.close()
            await asyncio.wait_for(process.wait(), 4)
            await wait_until(lambda: not any(process_running(record[key]) for key in ('parent', 'child')))
        finally:
            await stop_follower(process)
            if record:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(record['parent'], signal.SIGKILL)

    asyncio.run(check())


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='POSIX SSH supervisor with setsid')
def test_remote_supervisor_finishes_when_log_source_exits_with_stdin_open():
    async def check():
        process = await asyncio.create_subprocess_exec(
            '/bin/sh', '-c', remote_command([sys.executable, '-c', 'print("finished")']),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, start_new_session=True)
        try:
            assert await asyncio.wait_for(process.stdout.readline(), 4) == b'finished\n'
            await asyncio.wait_for(process.wait(), 4)
        finally:
            await stop_follower(process)

    asyncio.run(check())


def fleet_row(i, players):
    return dict(id=f'node-b-{49000+i}', node='node-b', port=49000+i, name=f'Lobby {i:02}', players=players,
                runtime_cap=32, configured_cap=32, health='healthy', fresh=True, node_fresh=True,
                running=True, rcon_up=True, image='official:test', process_generation='v1', config_hash='cfg')


def test_visible_fleet_cards_follow_stable_ids_after_sort_filter_and_dynamic_discovery():
    async def check():
        rows = [fleet_row(i, i) for i in range(24)]
        fleet = Mock(rows={r['id']: r for r in rows}, updated=time.monotonic())
        fleet.read = AsyncMock(return_value={'ok': True, 'data': {'players': []}})
        fleet.refresh = Mock()
        app = FleetApp(fleet)
        async with app.run_test(size=(130, 42)) as pilot:
            await pilot.pause(.4)
            listing = app.query_one('#stations')
            listing.scroll_home(animate=False)
            await pilot.pause(.1)
            selected = app.cur
            by_id = {st.rcon.ident: st for st in app.stations}
            least, busiest, filtered = (by_id[rows[i]['id']] for i in (0, 23, 12))
            assert app.visible_ids[0] == rows[-1]['id']
            assert busiest.index in app.seen and least.index not in app.seen
            assert busiest.drawn
            app.query_one('#fleet-sort', Select).value = 'players-asc'
            await pilot.pause(.2)
            listing.scroll_home(animate=False)
            await pilot.pause(.1)
            assert least.index in app.seen and busiest.index not in app.seen
            assert app.cur is selected
            app.query_one('#fleet-search', Input).value = '"Lobby 12"'
            await pilot.pause(.2)
            assert app.seen == {filtered.index} and filtered.drawn
            assert app.cur is selected
            fresh = fleet_row(30, 31)
            fleet.rows[fresh['id']] = fresh
            await app.refresh_inventory()
            await pilot.pause(.2)
            new = app.stations[-1]
            assert new.index == 24 and new.rcon.ident == fresh['id']
            assert app.by_rcon[new.rcon] is new and isinstance(app.cards[new.index], Card)
            assert new.online and app.cur is selected
            app.query_one('#fleet-search', Input).value = '"Lobby 30"'
            await pilot.pause(.2)
            assert app.seen == {new.index} and new.drawn
            assert app.cur is selected
            fleet.submit.assert_not_called()

    asyncio.run(check())
