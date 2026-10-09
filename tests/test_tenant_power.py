"""IBIGS F7: start / stop / restart lobbies from the customer console (through the `oni` wrapper's commands)."""
import asyncio
import json
import os
import stat

from oni_rcon.app import Confirm
from oni_rcon.config import Server
from oni_rcon.demo import serve_fakes
from oni_rcon.tenant import PowerPanel, TenantApp

LOBS = [{"service": "h3-01", "name": "IBIGS Halo 3 Server 1", "running": True, "players": ["Alice"], "max": 16},
        {"service": "h3-02", "name": "IBIGS Halo 3 Server 2", "running": True, "players": [], "max": 16},
        {"service": "h3-03", "name": "IBIGS Halo 3 Server 3", "running": False, "players": [], "max": 16}]


def fake_tool(tmp_path):
    log = tmp_path / "calls.log"
    tool = tmp_path / "oni"
    tool.write_text("#!/bin/sh\n"
                    f"echo \"$@\" >> {log}\n"
                    f"case \"$1\" in status) echo '{json.dumps(LOBS)}' ;; *) echo '   Stopping h3-01... done.' ;; esac\n")
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    return tool, log


async def wait_for(pilot, cond, n=100):
    for _ in range(n):
        await pilot.pause(0.05)
        if cond():
            return True
    return False


def test_f7_stop_one_with_players_needs_two_confirmations(tmp_path, monkeypatch):
    tool, log = fake_tool(tmp_path)
    monkeypatch.setenv("IBIGS_ONI_POWER", str(tool))

    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = TenantApp([Server(port=p, password="demo") for p in ports], by="pytest", intro=False)
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.press("f7")
            assert await wait_for(pilot, lambda: isinstance(app.screen, PowerPanel))
            assert app.query_one("#power-btn")  # the sidebar button is there too
            app.screen.query_one("#power-target").value = "h3-01"
            await pilot.pause()
            await pilot.click("#stop")
            assert await wait_for(pilot, lambda: isinstance(app.screen, Confirm))
            body = str(app.screen.b)
            assert "Alice" in body and "DISCONNECTED" in body and "h3-02" not in body
            assert app.screen.focused.id == "no"  # the safe choice is the default
            await pilot.click("#yes")
            assert await wait_for(pilot, lambda: isinstance(app.screen, Confirm) and "PLAYERS ARE ONLINE" in app.screen.t)
            await pilot.click("#yes")
            assert await wait_for(pilot, lambda: log.exists() and "stop" in log.read_text())
        calls = log.read_text().splitlines()
        assert calls == ["status --json", "stop h3-01 --yes --force"]

    asyncio.run(go())


def test_f7_abort_changes_nothing_and_stop_all_skips_stopped(tmp_path, monkeypatch):
    tool, log = fake_tool(tmp_path)
    monkeypatch.setenv("IBIGS_ONI_POWER", str(tool))

    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = TenantApp([Server(port=p, password="demo") for p in ports], by="pytest", intro=False)
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.click("#power-btn")
            assert await wait_for(pilot, lambda: isinstance(app.screen, PowerPanel))
            await pilot.click("#stop")  # ALL is the default target
            assert await wait_for(pilot, lambda: isinstance(app.screen, Confirm))
            assert "h3-03" not in str(app.screen.b)  # already stopped: not listed
            await pilot.click("#no")
            await pilot.pause(0.3)
        assert log.read_text().splitlines() == ["status --json"]

    asyncio.run(go())
