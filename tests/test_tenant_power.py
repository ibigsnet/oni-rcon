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
        calls = [c for c in log.read_text().splitlines() if not c.startswith(("checklist", "restore --json"))]
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
        assert [c for c in log.read_text().splitlines() if not c.startswith(("checklist", "restore --json"))] == ["status --json"]

    asyncio.run(go())


def test_empty_lobby_rechecks_players_without_force(tmp_path, monkeypatch):
    """A player joining after the dialog snapshot must not be forcibly disconnected."""
    tool, log = fake_tool(tmp_path)
    tool.write_text(tool.read_text().replace("*) echo '   Stopping h3-01... done.'", "*) echo 'players may be online'; exit 4"))
    monkeypatch.setenv("IBIGS_ONI_POWER", str(tool))

    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = TenantApp([Server(port=p, password="demo") for p in ports], by="pytest", intro=False)
        notices = []
        async with app.run_test(size=(80, 32)) as pilot:
            original_notify = app.notify
            def notify(message, *args, **kwargs):
                notices.append(str(message))
                original_notify(message, *args, **kwargs)
            monkeypatch.setattr(app, 'notify', notify)
            await pilot.press("f7")
            assert await wait_for(pilot, lambda: isinstance(app.screen, PowerPanel))
            app.screen.query_one("#power-target").value = "h3-02"
            await pilot.pause()
            await pilot.click("#restart")
            assert await wait_for(pilot, lambda: isinstance(app.screen, Confirm))
            await pilot.click("#yes")
            assert await wait_for(pilot, lambda: any('occupancy changed' in n for n in notices))
        assert [c for c in log.read_text().splitlines() if not c.startswith(("checklist", "restore --json"))] == ["status --json", "restart h3-02 --yes"]
        assert any('Nothing was stopped' in n and 'Open F7 again' in n for n in notices)

    asyncio.run(go())


def test_f7_buttons_fit_80_columns_and_large_lobby_list(tmp_path, monkeypatch):
    tool, log = fake_tool(tmp_path)
    many = [dict(LOBS[1], service=f'h3-{i:02}', name='A long lobby name ' * 6) for i in range(1, 25)]
    tool.write_text(tool.read_text().replace(json.dumps(LOBS), json.dumps(many)))
    monkeypatch.setenv("IBIGS_ONI_POWER", str(tool))

    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = TenantApp([Server(port=p, password="demo") for p in ports], by="pytest", intro=False)
        async with app.run_test(size=(80, 32)) as pilot:
            await pilot.press("f7")
            assert await wait_for(pilot, lambda: isinstance(app.screen, PowerPanel))
            await pilot.pause()
            panel = app.screen.query_one('.power')
            assert 0 <= panel.region.x < panel.region.right <= 80
            for button_id in ('no', 'start', 'restart', 'stop'):
                button = app.screen.query_one('#' + button_id)
                assert button.region.width >= len(str(button.label))
                assert 0 <= button.region.x < button.region.right <= 80
                assert 0 <= button.region.y < button.region.bottom <= 32
            assert await pilot.click('#no')
        assert [c for c in log.read_text().splitlines() if not c.startswith(("checklist", "restore --json"))] == ['status --json']

    asyncio.run(go())


def test_f7_number_and_letter_keys_pick_and_act(tmp_path, monkeypatch):
    """0-9 pick the lobby (0 = all), S / R / X press START / RESTART / STOP: no counting Tab presses."""
    tool, log = fake_tool(tmp_path)
    monkeypatch.setenv("IBIGS_ONI_POWER", str(tool))

    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = TenantApp([Server(port=p, password="demo") for p in ports], by="pytest", intro=False)
        async with app.run_test(size=(120, 32)) as pilot:
            await pilot.press("f7")
            assert await wait_for(pilot, lambda: isinstance(app.screen, PowerPanel))
            sel = app.screen.query_one("#power-target")
            await pilot.press("3")
            assert sel.value == "h3-03"
            await pilot.press("9")              # no such lobby: unchanged
            assert sel.value == "h3-03"
            await pilot.press("0")
            assert sel.value == "*"
            await pilot.press("2")
            await pilot.press("x")              # STOP h3-02: nobody on it, one confirmation
            assert await wait_for(pilot, lambda: isinstance(app.screen, Confirm))
            assert "h3-02" in str(app.screen.b) and "h3-01" not in str(app.screen.b)
            await pilot.click("#yes")
            assert await wait_for(pilot, lambda: log.exists() and "stop" in log.read_text())
        assert [c for c in log.read_text().splitlines() if not c.startswith(("checklist", "restore --json"))][-1].startswith("stop h3-02")

    asyncio.run(go())


def test_confirm_arrow_keys_move_between_buttons():
    """The confirm box says "← → choose": the arrows really move between ABORT and the action."""
    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = TenantApp([Server(port=p, password="demo") for p in ports], by="pytest", intro=False)
        async with app.run_test(size=(120, 32)) as pilot:
            app.push_screen(Confirm("CONFIRM STOP", "STOP 1 server(s)", "STOP"))
            assert await wait_for(pilot, lambda: isinstance(app.screen, Confirm))
            assert app.screen.focused.id == "no"
            await pilot.press("right")
            assert app.screen.focused.id == "yes"
            await pilot.press("left")
            assert app.screen.focused.id == "no"
    asyncio.run(go())
