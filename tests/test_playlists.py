"""IBIGS F3 PLAYLISTS: the customer console makes, edits and deploys playlists through the `oni` wrapper
(`oni playlist ...`), which checks every game with the server and only restarts when asked."""
import asyncio
import json
import stat
import sys

from oni_rcon.app import Confirm, OniApp, Pick
from oni_rcon.config import Server
from oni_rcon.content_ui import Browser
from oni_rcon.demo import serve_fakes
from oni_rcon.playlist_ui import Editor, Playlists
from oni_rcon.tenant import TenantApp

LIST = {"folder": "/srv/ibigs/stacks/reclaimer/shared/content/Playlists",
        "library": [{"path": "Forge/rf-0123456789abcdef0123.json", "name": "Zeta Mix", "games": 12,
                     "source": "forge", "modified": 1760000000},
                    {"path": "Oni/alpha.json", "name": "Alpha", "games": 2, "source": "oni", "modified": 1760300000},
                    {"path": "Mine/broken.json", "problem": "game 1 needs both a map and a game"}],
        "lobbies": [{"service": "h3-01", "name": "IBIGS VPS 1", "map": "Valhalla", "game": "Slayer",
                     "running": True, "players": []},
                    {"service": "h3-02", "name": "IBIGS VPS 2", "playlist": "playlists/alpha.json",
                     "running": True, "players": ["Alice"]}]}
ALPHA = {"version": 2, "name": "Alpha", "vote_seconds": 20,
         "games": [{"map": "Guardian", "game": "Slayer"}, {"map": "Valhalla", "game": "Team Slayer", "weight": 3}]}


def fake_oni(tmp_path, use_rc=0):
    """`oni playlist ...` as the wrapper answers it; every call and what came on stdin is logged."""
    log = tmp_path / "calls.jsonl"
    tool = tmp_path / "oni"
    tool.write_text(f"""#!{sys.executable}
import json, sys
args = sys.argv[1:]
data = sys.stdin.read() if args[:2] == ["playlist", "save"] else ""
open({str(log)!r}, "a").write(json.dumps({{"args": args, "stdin": data}}) + "\\n")
if args[:2] == ["playlist", "list"]:
    print(json.dumps({LIST!r}))
elif args[:2] == ["playlist", "show"]:
    print(json.dumps({ALPHA!r}))
elif args[:2] == ["playlist", "save"]:
    print(json.dumps({{"path": "Oni/" + json.loads(data)["name"].lower().replace(" ", "-") + ".json"}}))
elif args[:2] == ["playlist", "use"]:
    print("   h3-01: now plays playlist")
    sys.exit({use_rc})
""")
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    return tool, lambda: [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def ready(app):
    """The playlist manager is up and filled (it can be on screen a moment before its tables are)."""
    return isinstance(app.screen, Playlists) and bool(app.screen.query("#pl-lib")) and \
        bool(app.screen.query_one("#pl-lib").ids)


async def until(pilot, cond, n=120):
    for _ in range(n):
        await pilot.pause(0.05)
        if cond():
            return True
    return False


def test_without_the_helper_the_button_is_greyed_out():
    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = OniApp([Server(port=p, password="demo") for p in ports], by="pytest", intro=False)
        async with app.run_test(size=(160, 48)) as pilot:
            assert await until(pilot, lambda: all(st.online for st in app.stations))
            await pilot.press("f3")
            await pilot.pause(0.2)
            assert app.query_one("#op-playlists").disabled
    asyncio.run(go())


def test_the_library_orders_by_any_column_and_filters(tmp_path, monkeypatch):
    tool, calls = fake_oni(tmp_path)
    monkeypatch.setenv("IBIGS_ONI_POWER", str(tool))

    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = TenantApp([Server(port=p, password="demo") for p in ports], by="pytest", intro=False)
        async with app.run_test(size=(160, 48)) as pilot:
            assert await until(pilot, lambda: all(st.online for st in app.stations))
            await pilot.press("f3")
            await pilot.pause(0.2)
            assert not app.query_one("#op-playlists").disabled
            app.query_one("#op-playlists").press()
            assert await until(pilot, lambda: ready(app))
            lib = app.screen.query_one("#pl-lib")
            assert lib.ids == ["Forge/rf-0123456789abcdef0123.json", "Oni/alpha.json", "Mine/broken.json"]
            lib.on_data_table_header_selected(type("E", (), {"column_key": type("K", (), {"value": "name"})(),
                                                             "stop": lambda self=None: None})())
            await pilot.pause(0.1)
            assert lib.ids[:2] == ["Oni/alpha.json", "Mine/broken.json"]  # A-Z (a problem file goes by its path)
            assert "▲" in str(lib.columns["name"].label)
            lib.on_data_table_header_selected(type("E", (), {"column_key": type("K", (), {"value": "name"})(),
                                                             "stop": lambda self=None: None})())
            await pilot.pause(0.1)
            assert lib.ids[0] == "Forge/rf-0123456789abcdef0123.json" and "▼" in str(lib.columns["name"].label)
            lib.on_data_table_header_selected(type("E", (), {"column_key": type("K", (), {"value": "games"})(),
                                                             "stop": lambda self=None: None})())
            await pilot.pause(0.1)
            assert lib.ids == ["Forge/rf-0123456789abcdef0123.json", "Oni/alpha.json", "Mine/broken.json"]
            app.screen.query_one("#pl-filter").value = "forge"
            await pilot.pause(0.1)
            assert lib.ids == ["Forge/rf-0123456789abcdef0123.json"]
            await pilot.press("escape")
    asyncio.run(go())


def test_edit_a_copy_add_a_game_from_the_browser_and_save(tmp_path, monkeypatch):
    tool, calls = fake_oni(tmp_path)
    monkeypatch.setenv("IBIGS_ONI_POWER", str(tool))

    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = TenantApp([Server(port=p, password="demo") for p in ports], by="pytest", intro=False)
        async with app.run_test(size=(160, 48)) as pilot:
            assert await until(pilot, lambda: all(st.online and "modes" in st.data for st in app.stations))
            app.run_worker(app.playlists(start=("edit", "Forge/rf-0123456789abcdef0123.json")), group="dialog")
            assert await until(pilot, lambda: isinstance(app.screen, Editor) and app.screen.query("#pe-games")
                               and app.screen.query_one("#pe-games").ids)
            ed = app.screen
            assert ed.query_one("#pe-name").value == "Alpha (copy)"  # Forge's file is never rewritten: a copy
            assert ed.query_one("#pe-games").ids == ["0", "1"]
            ed.query_one("#add").press()
            assert await until(pilot, lambda: isinstance(app.screen, Browser))
            await pilot.press(*"sandtrap", "enter")
            assert await until(pilot, lambda: isinstance(app.screen, Browser) and app.screen.what == "modes")
            await pilot.press(*"oddball", "enter")
            assert await until(pilot, lambda: isinstance(app.screen, Editor) and len(ed.p["games"]) == 3)
            ed.query_one("#pe-games").move_cursor(row=2)
            ed.query_one("#more").press()
            ed.query_one("#up").press()
            await pilot.pause(0.1)
            assert ed.p["games"][1] == {"map": "sandtrap", "game": "oddball", "weight": 2}
            ed.query_one("#save").press()
            assert await until(pilot, lambda: ready(app))
            saved = [c for c in calls() if c["args"][:2] == ["playlist", "save"]]
            assert saved and saved[0]["args"] == ["playlist", "save", "--json"]  # a new file in Playlists/Oni
            body = json.loads(saved[0]["stdin"])
            assert body["name"] == "Alpha (copy)" and len(body["games"]) == 3
            assert "Saved Alpha (copy)" in str(app.screen.query_one("#pl-note").render())
            await pilot.press("escape")
    asyncio.run(go())


def test_use_on_all_lobbies_and_restart_only_empty_ones(tmp_path, monkeypatch):
    tool, calls = fake_oni(tmp_path)
    monkeypatch.setenv("IBIGS_ONI_POWER", str(tool))

    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = TenantApp([Server(port=p, password="demo") for p in ports], by="pytest", intro=False)
        async with app.run_test(size=(160, 48)) as pilot:
            assert await until(pilot, lambda: all(st.online for st in app.stations))
            app.run_worker(app.playlists(start=("use", "Oni/alpha.json")), group="dialog")
            assert await until(pilot, lambda: isinstance(app.screen, Pick))
            app.screen.dismiss("all")
            assert await until(pilot, lambda: isinstance(app.screen, Pick) and "WHEN" in app.screen.t)
            values = [v for _, v in app.screen.opts]
            assert values == ["later", "restart", "force"]  # Alice is on h3-02: disconnecting is its own choice
            app.screen.dismiss("restart")
            assert await until(pilot, lambda: isinstance(app.screen, Confirm))
            assert "Alice" in app.screen.b and "backed up" in app.screen.b and app.screen.focused.id == "no"
            app.screen.query_one("#yes").press()
            assert await until(pilot, lambda: ready(app))
            use = [c["args"] for c in calls() if c["args"][:2] == ["playlist", "use"]]
            assert use == [["playlist", "use", "Oni/alpha.json", "h3-01", "h3-02", "--restart", "--yes"]]
            await pilot.press("escape")
    asyncio.run(go())


def test_a_game_the_server_cant_play_is_shown_and_nothing_changes(tmp_path, monkeypatch):
    tool, calls = fake_oni(tmp_path, use_rc=6)
    monkeypatch.setenv("IBIGS_ONI_POWER", str(tool))

    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = TenantApp([Server(port=p, password="demo") for p in ports], by="pytest", intro=False)
        async with app.run_test(size=(160, 48)) as pilot:
            assert await until(pilot, lambda: all(st.online for st in app.stations))
            app.run_worker(app.playlists(start=("use", "Oni/alpha.json")), group="dialog")
            assert await until(pilot, lambda: isinstance(app.screen, Pick))
            app.screen.dismiss("h3-01")
            assert await until(pilot, lambda: isinstance(app.screen, Pick) and "WHEN" in app.screen.t)
            assert [v for _, v in app.screen.opts] == ["later", "restart"]  # nobody on h3-01: no disconnect choice
            app.screen.dismiss("later")
            assert await until(pilot, lambda: isinstance(app.screen, Confirm))
            app.screen.query_one("#yes").press()
            assert await until(pilot, lambda: isinstance(app.screen, Confirm) and app.screen.t == "NOT CHANGED")
            await pilot.press("escape")
            assert await until(pilot, lambda: ready(app))
            await pilot.press("escape")
    asyncio.run(go())
