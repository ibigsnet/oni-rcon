"""IBIGS 0.9.0+ibigs.20: the first customer's evening, made easier. Settings and Workshop (F12), a failed start that
says why, the getting-started checklist, F3 explaining its greyed-out buttons, and game types carrying host settings."""
import asyncio
import json
import os

from oni_rcon.app import Form, Pick
from oni_rcon.config import Server
from oni_rcon.demo import serve_fakes
from oni_rcon.tenant import Notice, PowerPanel, TenantApp, checklist_text
from oni_rcon.variants import clash_note, find_game_type

LOBS = [{"service": "h3-01", "name": "Desert Bus", "running": True, "players": [], "max": 96},
        {"service": "h3-02", "name": "IBIGS Halo 3 Server 2", "running": False, "players": [], "max": 16}]
SETTINGS = {"lobby": "h3-01", "name": "Desert Bus", "max_players": 96, "min_players": 2, "map": "Valhalla",
            "game": "Slayer", "playlist": "", "running": True, "players": []}


def fake_tool(tmp_path, checklist=None, restart_out=None):
    """The oni wrapper as the console sees it: answers by command, logs every call."""
    log = tmp_path / "calls.log"
    tool = tmp_path / "oni"
    answers = {"status": json.dumps(LOBS), "settings": json.dumps(SETTINGS),
               "checklist": json.dumps(checklist if checklist is not None else
                                       [{"ok": True, "text": "Halo 3 is installed on the server", "next": ""}])}
    script = ["#!/usr/bin/python3", "import json, sys",
              f"open({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')",
              f"A = {answers!r}", "a = sys.argv[1:]",
              "if a[0] == 'settings' and len(a) > 3:",
              "    print(json.dumps({'lobby': a[1], 'state': 'applied', 'text': 'max_players 24: restarted'})); sys.exit(0)",
              "if a[0] == 'restart':",
              f"    print({restart_out or '   Restarting h3-01... done.'!r}); sys.exit({5 if restart_out else 0})",
              "print(A.get(a[0], 'ok'))"]
    tool.write_text("\n".join(script) + "\n")
    tool.chmod(0o755)
    return tool, log


async def wait_for(pilot, cond, n=120):
    for _ in range(n):
        await pilot.pause(0.05)
        if cond():
            return True
    return False


def run_app(tmp_path, monkeypatch, body, **tool_kw):
    tool, log = fake_tool(tmp_path, **tool_kw)
    monkeypatch.setenv("IBIGS_ONI_POWER", str(tool))

    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = TenantApp([Server(port=p, password="demo") for p in ports], by="pytest", intro=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await body(app, pilot)
    asyncio.run(go())
    return [c for c in log.read_text().splitlines()]


def test_settings_from_f12_change_only_what_was_edited(tmp_path, monkeypatch):
    async def body(app, pilot):
        await pilot.pause(0.3)
        await pilot.press("f12")
        assert await wait_for(pilot, lambda: isinstance(app.screen, Pick))
        app.screen.dismiss("settings")
        assert await wait_for(pilot, lambda: isinstance(app.screen, Pick) and "WHICH LOBBY" in app.screen.t)
        app.screen.dismiss("h3-01")
        assert await wait_for(pilot, lambda: isinstance(app.screen, Form))
        app.screen.query_one("#field-max_players").value = "24"
        app.screen.submit()
        await pilot.pause(1.0)
    calls = run_app(tmp_path, monkeypatch, body)
    assert "settings h3-01 --json" in calls
    assert "settings h3-01 max_players=24 --restart --json" in calls   # name/map/game untouched, restart chosen


def test_a_failed_start_shows_the_whole_reason(tmp_path, monkeypatch):
    out = ("   h3-01: NOT RESTARTED. /srv/ibigs/stacks/reclaimer/instances/h3-01/dedicated.toml has a mistake: "
           "Expected '=' after a key (at line 5, column 7). Fix it with:  nano /srv/...")

    async def body(app, pilot):
        await pilot.pause(0.3)
        await pilot.press("f7")
        assert await wait_for(pilot, lambda: isinstance(app.screen, PowerPanel))
        assert app.screen.query_one("#power-target").value == "h3-01"   # not ALL SERVERS
        await pilot.press("r")
        assert await wait_for(pilot, lambda: getattr(app.screen, "t", "").startswith("CONFIRM"))
        await pilot.click("#yes")
        assert await wait_for(pilot, lambda: isinstance(app.screen, Notice))
        assert "line 5" in app.screen.body and "nano" in app.screen.body and "PROBLEM" in app.screen.t
    run_app(tmp_path, monkeypatch, body, restart_out=out)


def test_checklist_pops_up_until_the_basics_are_done(tmp_path, monkeypatch):
    todo = [{"ok": True, "text": "Halo 3 is installed on the server", "next": ""},
            {"ok": False, "text": "A lobby is running", "next": "Press F7, pick h3-01, then START (or S)."}]

    async def body(app, pilot):
        assert await wait_for(pilot, lambda: isinstance(app.screen, Notice), n=200)
        assert "GETTING STARTED" in app.screen.t and "Press F7" in app.screen.body and "[ ]" in app.screen.body
    run_app(tmp_path, monkeypatch, body, checklist=todo)
    text, done = checklist_text(todo)
    assert not done and text.index("NEXT STEP") > text.index("A lobby is running")


def test_f3_says_why_its_buttons_are_off(tmp_path, monkeypatch):
    tool, _ = fake_tool(tmp_path)
    monkeypatch.setenv("IBIGS_ONI_POWER", str(tool))

    async def go():
        app = TenantApp([Server(port=1, password="demo")], by="pytest", intro=False)   # nothing listens on port 1
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.5)
            await pilot.press("f3")
            await pilot.pause(0.5)
            app.paint_ops()
            why = app.query_one("#ops-offline")
            assert why.display and "F7" in str(why.render())
            assert app.query_one("#op-load").disabled
    asyncio.run(go())


def test_game_type_host_settings_clash_is_found_by_name(tmp_path):
    gm = tmp_path / "Game Modes" / "Forge"
    gm.mkdir(parents=True)
    (gm / "e19375c9.json").write_text(json.dumps({"version": 1, "name": "Desert Bus th3biscuitBOY (R1.5)",
                                                  "hosting": {"max_players": 16, "game_port": 11774},
                                                  "mode": "CaptureTheFlag"}))
    (gm / "plain.json").write_text(json.dumps({"name": "Plain CTF", "mode": "CaptureTheFlag"}))
    note = clash_note(tmp_path, "Desert Bus th3biscuitBOY (R1.5)", 96, 2)
    assert "max players 16 in the game type, 96 on this server" in note and "hosting" in note
    assert clash_note(tmp_path, "desert bus th3biscuitboy (r1.5)", 16, 2) == ""      # matching: nothing to say
    assert clash_note(tmp_path, "Plain CTF", 96, 2) == ""                             # no hosting block
    assert clash_note(tmp_path, "Game Modes/Forge/e19375c9.json", 24, 2)              # by path as well
    assert find_game_type(tmp_path, "../../etc/passwd.json") is None                  # never outside the folder
    assert clash_note(None, "anything", 96) == ""
