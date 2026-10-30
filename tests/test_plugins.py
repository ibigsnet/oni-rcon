"""The plug-in host: the golden cases in docs/PLUGINS-BUILD.md, numbered as there. Fake plug-ins are tiny Python
scripts that print canned stdout, so nothing needs ssh."""
import asyncio
import io
import json
import shutil
import sys
from pathlib import Path

import pytest
from rich.console import Console
from textual.widgets import DataTable, Static

from oni_rcon import plugins as pl
from oni_rcon.app import OniApp
from oni_rcon.config import Server, load_config, plugin_settings
from oni_rcon.demo import serve_fakes
from oni_rcon.plugins import Manifest, PluginError, PluginSetup
from oni_rcon.prefs import Prefs

VIEW = {"title": "Probe", "badge": {"level": "ok", "text": "FINE"},
        "blocks": [{"type": "kv", "rows": [["Mode", "dry-run"]]},
                   {"type": "table", "id": "t", "columns": ["Who"], "rows": [{"id": "r1", "cells": ["Bob"]}],
                    "row_actions": ["undo"]},
                   {"type": "text", "text": "hi"}, {"type": "log", "lines": ["a", "b"]}],
        "actions": [{"id": "check", "label": "Check", "params": [{"name": "text", "label": "Text", "type": "text"}]}]}


def plug(root: Path, pid: str, body: str = "", **top) -> Path:
    """plugins/<pid>/ with a manifest that runs run.py (whose source is `body`) with this Python. A JSON string is a
    TOML basic string, so json.dumps writes the values."""
    folder = root / "plugins" / pid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "run.py").write_text(body, encoding="utf-8")
    exe = f'"{sys.executable}" run.py'
    keys = "".join(f"{k} = {json.dumps(v)}\n" for k, v in {"api": 1, "name": pid.title(), **top}.items())
    (folder / pl.MANIFEST).write_text(f'{keys}[run]\nexec = {json.dumps(exe)}\n[permissions]\nrcon = ["unmute"]\n')
    return folder


def prints(obj, code: int = 0, err: str = "") -> str:
    """run.py that prints `obj` (JSON, or a str as-is), `err` on stderr, and exits `code`."""
    out = obj if isinstance(obj, str) else json.dumps(obj)
    return (f"import sys\nsys.stdin.read()\nsys.stdout.write({out!r})\nsys.stderr.write({err!r})\n"
            f"sys.exit({code})\n")


def ask(m: Manifest, request=None, timeout: float = 10.0):
    return asyncio.run(pl.call(m, request or {"type": "view", "server": None}, timeout))


# --- M1: manifest, trust, running, shaping --------------------------------------------------------------------------
def test_1_a_trusted_plugin_view_comes_back_as_sent(tmp_path):
    m = pl.load(plug(tmp_path, "probe", prints(VIEW)))
    assert not m.error and pl.state(m, {"probe": m.hash}) == ""
    v = pl.normalise_view(ask(m))
    assert v["title"] == "Probe" and v["badge"] == {"level": "ok", "text": "FINE"}
    assert [b["type"] for b in v["blocks"]] == ["kv", "table", "text", "log"]
    assert v["blocks"][1]["rows"] == [{"id": "r1", "cells": ["Bob"]}] and v["blocks"][1]["row_actions"] == ["undo"]
    assert v["actions"][0]["params"][0] == {"name": "text", "label": "Text", "type": "text", "options": []}


def test_2_a_folder_the_config_doesnt_name_is_not_listed(tmp_path):
    plug(tmp_path, "probe", prints(VIEW))
    plug(tmp_path, "other", prints(VIEW))
    assert [m.id for m in pl.discover(tmp_path / "plugins", ["probe"])] == ["probe"]
    assert pl.discover(tmp_path / "plugins", []) == []


def test_3_4_trust_is_the_manifest_hash_and_a_change_asks_again(tmp_path):
    folder = plug(tmp_path, "probe", prints(VIEW))
    m = pl.load(folder)
    assert pl.state(m, {}) == "needs approval"
    trusted = {"probe": m.hash}
    assert pl.state(m, trusted) == ""
    (folder / pl.MANIFEST).write_text((folder / pl.MANIFEST).read_text() + "# one byte more\n")
    assert pl.state(pl.load(folder), trusted) == "needs approval"


def test_5_a_newer_api_is_listed_disabled_with_the_reason(tmp_path):
    (m,) = pl.discover(plug(tmp_path, "probe", api=2).parent, ["probe"])
    assert "api 2" in m.error and "api 1" in m.error and pl.state(m, {"probe": m.hash}) == m.error


def test_6_a_broken_manifest_is_listed_with_why_and_the_others_still_load(tmp_path):
    plug(tmp_path, "good", prints(VIEW))
    plug(tmp_path, "noname", name="")
    bad = tmp_path / "plugins" / "badtoml"
    bad.mkdir()
    (bad / pl.MANIFEST).write_text("api = = 1\n")
    got = {m.id: m.error for m in pl.discover(tmp_path / "plugins", ["good", "noname", "badtoml", "missing", "../up"])}
    assert got["good"] == "" and "no name" in got["noname"] and "TOML" in got["badtoml"]
    assert pl.MANIFEST in got["missing"] and "lower-case" in got["../up"]
    assert pl.discover(None, ["good"])[0].error  # no config file: nowhere to find them


def test_7_8_unknown_blocks_and_keys(tmp_path):
    v = pl.normalise_view({**VIEW, "extra": 1, "blocks": [{"type": "chart", "points": [1]},
                                                          {"type": "text", "text": "x", "colour": "red"}]})
    assert v["blocks"] == [{"type": "unknown", "name": "chart"}, {"type": "text", "text": "x"}]
    assert "extra" not in v
    assert not pl.load(plug(tmp_path, "probe", prints(VIEW), homepage="https://example.org")).error


def test_9_10_11_12_what_a_reply_can_be(tmp_path):
    def run(body, timeout=10.0):
        return ask(pl.load(plug(tmp_path, "probe", body)), timeout=timeout)

    with pytest.raises(PluginError, match="isn't JSON"):
        run(prints("hello there"))  # 9
    assert run(prints(VIEW, code=1))["title"] == "Probe"  # 10: the JSON wins
    with pytest.raises(PluginError) as e:
        run(prints("", code=3, err="warming up\nconnect to 203.0.113.9 refused\n"))  # 11
    assert str(e.value) == "connect to [ip] refused"
    with pytest.raises(PluginError, match="exited 4"):
        run(prints("", code=4))
    with pytest.raises(PluginError, match=r"no answer in 0\.5s"):
        run("import time\ntime.sleep(30)\n", timeout=0.5)  # 12
    assert (pl.VIEW_TIMEOUT, pl.ACTION_TIMEOUT) == (10, 30)


def test_13_the_caps(tmp_path):
    rows = [{"id": str(i), "cells": [str(i)]} for i in range(600)]
    v = pl.normalise_view({"blocks": [{"type": "table", "columns": ["n"], "rows": rows},
                                      {"type": "text", "text": "x" * 20_000}],
                           "actions": [{"id": f"a{i}", "params": [{"name": f"p{j}"} for j in range(15)]}
                                       for i in range(30)]})
    table, text = v["blocks"]
    assert len(table["rows"]) == 500 and table["more"] == 100 and len(text["text"]) == 10_000
    assert len(v["actions"]) == 20 and len(v["actions"][0]["params"]) == 10
    # cut JSON can't be read, so a reply over 1 MiB is the one cap that's an error (PLUGINS.md says so)
    with pytest.raises(PluginError, match="over 1 MiB"):
        ask(pl.load(plug(tmp_path, "big", "import sys\nsys.stdout.write('[' + '0,' * 600_000 + '0]')\n")))


def test_15_16_rcon_outside_its_permissions_or_the_config_is_dropped():
    m = Manifest("probe", rcon=["unmute"])
    r = pl.normalise_action({"ok": True, "rcon": [{"server": "3", "command": "ban", "args": ["x"]},
                                                  {"server": "9", "command": "unmute", "args": ["x"]},
                                                  {"server": "3", "command": "unmute", "args": ["x", 5]}]}, m, ["3"])
    assert r["rcon"] == [{"server": "3", "command": "unmute", "args": ["x", "5"]}]
    assert len(r["dropped"]) == 2 and "permissions" in r["dropped"][0] and "no such server" in r["dropped"][1]


def test_18_a_reported_failure():
    assert pl.normalise_view({"error": "daemon not running"}) == {"error": "daemon not running"}
    r = pl.normalise_action({"ok": False, "error": "no such row"}, Manifest("p"), [])
    assert not r["ok"] and r["error"] == "no such row"
    assert pl.normalise_action("nonsense", Manifest("p"), [])["error"]


def test_21_plugins_in_a_server_block_is_ignored_and_top_level_is_read(tmp_path):
    p = tmp_path / "oni-rcon.toml"
    p.write_text('plugins = ["probe"]\n[[server]]\nport = 1\nplugins = ["nope"]\n')
    _, (s,) = load_config(p)
    assert s.port == 1 and pl.enabled(plugin_settings(p)) == ["probe"]
    with pytest.raises(ValueError):
        pl.enabled({"plugins": "probe"})


def test_22_a_trust_write_keeps_keys_it_doesnt_know(tmp_path):
    path = tmp_path / "prefs.json"
    path.write_text(json.dumps({"intro_seen": True, "desktop_thing": {"a": 1}}))
    prefs = Prefs.load(path)
    prefs.plugins_trusted["probe"] = "abc"
    prefs.save()
    data = json.loads(path.read_text())
    assert data["desktop_thing"] == {"a": 1} and data["plugins_trusted"] == {"probe": "abc"} and data["intro_seen"]
    assert Prefs.load(path).plugins_trusted == {"probe": "abc"}


# --- M2: the tab ----------------------------------------------------------------------------------------------------
class Source:
    """Stands in for running a plug-in: `reply(request)` gives the answer, or an exception to raise."""
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    async def call(self, m, request, timeout):
        self.calls.append(request)
        r = self.reply(request)
        if isinstance(r, Exception):
            raise r
        return r


async def until(pilot, cond, tries: int = 120) -> bool:
    for _ in range(tries):
        await pilot.pause(0.05)
        if cond():
            return True
    return False


def panel_text(app) -> str:
    console = Console(width=200, file=io.StringIO(), force_terminal=False)
    for w in app.query_one("#plugin-panel").query("*"):
        if type(w) is Static:
            console.print(w.content)
        elif isinstance(w, DataTable):
            console.print(" | ".join(str(c.label) for c in w.ordered_columns), markup=False)
            for r in range(w.row_count):
                console.print(" | ".join(str(c) for c in w.get_row_at(r)), markup=False)
    return console.file.getvalue()


async def opened(app, pilot, pg, src) -> None:
    """F8 (IBIGS keeps Health on F10), and one fresh view asked for and drawn."""
    await pilot.press("f8")
    asked = len(src.calls)
    pg.due = 0.0
    app.plugin_tick()
    assert await until(pilot, lambda: len(src.calls) > asked and not pg.busy)
    await pilot.pause(0.1)


def test_the_tab_draws_a_view_literally_greys_a_failure_and_feeds_condition():  # 7, 9, 14, 18, 20
    state = {"view": {**VIEW, "title": "[bold red]x[/] <b>", "blocks": [*VIEW["blocks"], {"type": "chart"}]}}
    src = Source(lambda req: state["view"])
    m = Manifest("probe", name="Probe", exec="(test)")

    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = OniApp([Server(port=p, password="demo") for p in ports], by="pytest", intro=False,
                     plugins=PluginSetup([m], source=src))
        async with app.run_test(size=(160, 48)) as pilot:
            assert await until(pilot, lambda: all(st.online for st in app.stations))
            pg = app.plugins[0]
            await opened(app, pilot, pg, src)
            text = panel_text(app)
            assert "[bold red]x[/] <b>" in text and "Mode" in text and "dry-run" in text and "Bob" in text  # 14
            assert "can't draw a 'chart' block" in text and "hi" in text  # 7: the rest is drawn
            assert app.condition()[0] == "GREEN"  # 20: ok changes nothing
            for level, want in [("warn", "AMBER"), ("alert", "RED"), ("off", "GREEN"), ("ok", "GREEN")]:
                state["view"] = {**VIEW, "badge": {"level": level, "text": level}}
                await opened(app, pilot, pg, src)
                assert app.condition()[0] == want, level
            state["view"] = {"error": "daemon not running at 203.0.113.9"}
            await opened(app, pilot, pg, src)
            assert pg.error and "[ip]" in pg.error  # 9 / 18: the last view stays, greyed, with why and pg.view["title"] == "Probe"
            assert app.query_one(".plugin-body").has_class("-stale") and "daemon not running" in panel_text(app)
            state["view"] = {**VIEW, "badge": {"level": "alert", "text": "!"}}
            await opened(app, pilot, pg, src)
            state["view"] = PluginError("no answer in 10s")
            await opened(app, pilot, pg, src)
            assert app.condition()[0] == "GREEN"  # 20: a badge that can't be confirmed doesn't hold CONDITION
            assert "no answer in 10s" in panel_text(app)
            names = [c.title for c in app.get_system_commands(app.screen)]
            assert "Probe › Check" in names

    asyncio.run(go())


def test_actions_confirm_first_and_send_only_permitted_rcon():  # 15, 17, 18, 19
    def reply(req):
        if req["type"] == "view":
            return {**VIEW, "actions": [{"id": "go-live", "label": "Go live", "danger": True, "confirm": "Really?"},
                                        {"id": "undo", "label": "Undo"}, {"id": "fail", "label": "Fail"}]}
        if req["id"] == "fail":
            return {"ok": False, "error": "no such row"}
        return {"ok": True, "toast": "Undid it.", "rcon": [
            {"server": req["server"], "command": "unmute", "args": [req["row"] or "nobody"]},
            {"server": req["server"], "command": "ban", "args": ["x"]}]}

    src = Source(reply)
    m = Manifest("probe", name="Probe", exec="(test)", rcon=["unmute"])

    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = OniApp([Server(port=p, password="demo") for p in ports], by="pytest", intro=False,
                     plugins=PluginSetup([m], source=src))
        async with app.run_test(size=(160, 48)) as pilot:
            assert await until(pilot, lambda: all(st.online for st in app.stations))
            pg = app.plugins[0]
            await opened(app, pilot, pg, src)
            acts = {a["id"]: a for a in pg.view["actions"]}
            def actions():
                return [c for c in src.calls if c["type"] == "action"]
            app.plugin_action(pg, acts["go-live"])  # 19: Esc sends nothing
            assert await until(pilot, lambda: bool(app.screen.query("#no")))
            assert await until(pilot, lambda: app.screen.focused is not None)  # on_mount focuses it a beat after the push
            assert app.screen.focused.id == "no"  # ABORT is the default
            await pilot.press("escape")
            app.plugin_action(pg, acts["go-live"])  # nor does ABORT
            assert await until(pilot, lambda: bool(app.screen.query("#no")))
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert actions() == []
            await pilot.click("#plugin-panel .plugin-actions Button")  # the row action under the table: undo on Bob
            assert await until(pilot, lambda: len(actions()) == 1)
            assert actions()[0]["row"] == "r1" and actions()[0]["server"] == app.cur.server.where
            assert await until(pilot, lambda: any(n.message == "Undid it." for n in app._notifications))
            await pilot.press("f5")  # the log draws once it's shown
            assert await until(pilot, lambda: any("plug-in probe ▸ unmute r1" in line.text
                                                  for line in app.query_one("#console-log").lines))  # 17
            assert await until(pilot, lambda: any("ban" in n.message and "permissions" in n.message
                                                  for n in app._notifications))  # 15
            assert not any(line.text.startswith("plug-in probe ▸ ban") for line in app.query_one("#console-log").lines)
            app.plugin_action(pg, acts["fail"])  # 18: a toast, no traceback
            assert await until(pilot, lambda: any(n.message == "no such row" for n in app._notifications))
            assert app.is_running

    asyncio.run(go())


def test_an_unapproved_plugin_never_runs_until_approved(tmp_path):  # 3, and the real runner end to end
    root = tmp_path / "cfg"
    folder = plug(root, "probe", "import pathlib\npathlib.Path('ran').touch()\n" + prints(VIEW))
    prefs = Prefs(tmp_path / "prefs.json")

    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = OniApp([Server(port=p, password="demo") for p in ports], by="pytest", intro=False, prefs=prefs,
                     plugins=PluginSetup(pl.discover(root / "plugins", ["probe"])))
        async with app.run_test(size=(160, 48)) as pilot:
            pg = app.plugins[0]
            await pilot.press("f8")
            pg.due = 0.0
            app.plugin_tick()
            await pilot.pause(0.5)
            assert not (folder / "ran").exists() and not pg.asked
            assert "NEEDS YOUR APPROVAL" in panel_text(app) and "run.py" in panel_text(app)
            assert "Probe › Check" not in [c.title for c in app.get_system_commands(app.screen)]
            app.plugin_approve(pg)
            assert await until(pilot, lambda: bool(app.screen.query("#yes")))
            app.screen.query_one("#yes").press()  # a click can land mid-transition
            assert await until(pilot, lambda: prefs.plugins_trusted == {"probe": pg.m.hash})
            app.plugin_tick()
            assert await until(pilot, lambda: pg.view is not None, tries=200)
            assert (folder / "ran").exists() and "Bob" in panel_text(app)
            assert json.loads((tmp_path / "prefs.json").read_text())["plugins_trusted"] == {"probe": pg.m.hash}

    asyncio.run(go())


def test_the_hello_example_runs_end_to_end(tmp_path):
    folder = tmp_path / "plugins" / "hello"
    shutil.copytree(Path(__file__).parent.parent / "examples" / "plugins" / "hello", folder)
    (m,) = pl.discover(tmp_path / "plugins", ["hello"])
    assert not m.error and m.rcon == ["say"]
    m.exec = f'"{sys.executable}" hello.py'  # the manifest says `python`, which a runner may not have on PATH
    v = pl.normalise_view(ask(m, {"type": "view", "server": "3"}))
    assert {b["type"] for b in v["blocks"]} == {"kv", "table", "log", "text", "unknown"}
    types = {p["type"] for a in v["actions"] for p in a["params"]}
    assert types == set(pl.PARAM_TYPES) and any(a["danger"] and a["confirm"] for a in v["actions"])
    r = pl.normalise_action(ask(m, {"type": "action", "id": "greet", "params": {}, "row": "Rook", "server": "3"}),
                            m, ["3"])
    assert r["ok"] and r["toast"] == "Said hello to Rook." and r["rcon"] == [
        {"server": "3", "command": "say", "args": ["Hello, Rook!"]}]
    assert r["view"]["badge"]["text"] == "1 SAID" and "greeted Rook on 3" in r["view"]["blocks"][2]["lines"][0]
