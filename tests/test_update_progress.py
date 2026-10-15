"""IBIGS 0.9.0+ibigs.22: F9's update shows its progress (stages, elapsed time, lobbies coming back), F7 tells a
lobby that is still starting or stuck on its start-up check apart from one that runs."""
import asyncio
import json

from textual.app import App
from textual.widgets import ProgressBar, Static

from oni_rcon.jobs_ui import UpdateProgress
from oni_rcon.tenant import players_of, table


def fake(tmp_path, updater_rc=0):
    """`oni updates game-update` prints the updater's stages; `oni status --json` answers STARTING twice, then
    ONLINE (a counter file)."""
    n = tmp_path / "n"
    n.write_text("0")
    tool = tmp_path / "oni"
    starting = [{"service": "h3-01", "running": True, "players": None, "phase": "starting", "why": ""},
                {"service": "h3-02", "running": False, "players": None}]
    online = [{"service": "h3-01", "running": True, "players": [], "phase": "", "why": ""},
              {"service": "h3-02", "running": False, "players": None}]
    tool.write_text("\n".join([
        "#!/usr/bin/python3", "import json, sys, time", "a = sys.argv[1:]",
        "if a[0] == 'updates':",
        "    for l in ['  Project Reclaimer server version:  this server 0.9.17   latest 0.9.18',",
        "              '  Downloading project-reclaimer-dedicated (sha256:abc...): about 1-3 GB the first time...',",
        "              \"  Reclaimer's built-in game modes: up to date\", '  Restarting h3-01 on 0.9.18...',",
        "              '  \\x1b[1;32mUpdated to Project Reclaimer 0.9.18.\\x1b[0m']:",
        "        print(l, flush=True); time.sleep(0.05)",
        f"    sys.exit({updater_rc})",
        f"n = int(open({str(n)!r}).read()); open({str(n)!r}, 'w').write(str(n + 1))",
        f"print(json.dumps({starting!r} if n < 2 else {online!r}))"]))
    tool.chmod(0o755)
    return str(tool)


class Host(App):
    def __init__(self, screen):
        super().__init__()
        self.s, self.result = screen, "unset"

    def on_mount(self):
        async def run():
            self.result = await self.push_screen_wait(self.s)
        self.run_worker(run())


def test_update_progress_follows_stages_and_lobbies(tmp_path, monkeypatch):
    async def go():
        monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
        dlg = UpdateProgress("UPDATING GAME", fake(tmp_path), ["updates", "game-update"])
        app = Host(dlg)
        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(200):
                await pilot.pause(0.05)
                if dlg.done:
                    break
            assert dlg.done and dlg.rc == 0
            assert dlg.step == 5
            assert any("Updated to Project Reclaimer 0.9.18." == l for l in dlg.lines)  # colour codes gone
            assert "h3-01  ONLINE" in dlg.lobby_lines
            bar = dlg.query_one("#up-bar", ProgressBar)
            assert bar.progress == bar.total == 5
            assert "DONE" in str(dlg.query_one("#up-step", Static).render())
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert app.result == 0
    asyncio.run(go())


def test_failed_update_skips_waiting_and_can_close(tmp_path, monkeypatch):
    async def go():
        dlg = UpdateProgress("UPDATING GAME", fake(tmp_path, updater_rc=3), ["updates", "game-update"])
        app = Host(dlg)
        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(200):
                await pilot.pause(0.05)
                if dlg.done:
                    break
            assert dlg.done and dlg.rc == 3 and dlg.lobby_lines == ""
    asyncio.run(go())


_real_sleep = asyncio.sleep


async def _fast_sleep(t, *a, **k):
    return await _real_sleep(min(t, 0.01), *a, **k)


def test_f7_names_starting_and_waiting_lobbies():
    lobs = [{"service": "h3-01", "name": "A", "running": True, "players": None, "phase": "waiting",
             "why": "Cannot create /server/content/Maps: Access denied."},
            {"service": "h3-02", "name": "B", "running": True, "players": None, "phase": "starting"},
            {"service": "h3-03", "name": "C", "running": True, "players": ["x"], "max": 16, "phase": ""}]
    text = table(lobs).plain
    assert "WAITING" in text and "waiting on: Cannot create /server/content/Maps" in text
    assert "STARTING" in text and "usually 1-3 minutes" in text
    assert "RUNNING   1/16" in text
    assert players_of(lobs[0]) == "-" and players_of({"running": True, "players": None}) == "?"


def test_start_only_waits_for_lobbies(tmp_path, monkeypatch):
    async def go():
        monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
        dlg = UpdateProgress("START: h3-01", fake(tmp_path), None)
        app = Host(dlg)
        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(200):
                await pilot.pause(0.05)
                if dlg.done:
                    break
            assert dlg.done and dlg.STEPS == 1 and dlg.lines == []
            assert "h3-01  ONLINE" in dlg.lobby_lines
    asyncio.run(go())
