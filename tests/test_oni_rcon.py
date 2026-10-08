import asyncio
import hashlib
import json
import sys

import pytest

from oni_rcon.app import Boot, Form, Help, OniApp, emblem, explain, render_event, target_of
from oni_rcon.config import Server, add_servers, load_config, parse_target, resolve_passwords
from oni_rcon.demo import serve_fakes
from oni_rcon.rcon import Rcon


def test_targets():
    assert parse_target("11774").where == "127.0.0.1:11774"
    assert (parse_target("10.0.0.5:49176", ssh="box").where) == "10.0.0.5:49176 via ssh box"
    assert parse_target("[::1]:5000").host == "::1"
    assert parse_target("wss://rcon.example.org/s1").url == "wss://rcon.example.org/s1"
    with pytest.raises(ValueError):
        Server()


def test_config(tmp_path, monkeypatch):
    p = tmp_path / "c.toml"
    p.write_text(f'by = "op"\n[defaults]\nssh = "box"\npassword_command = [{json.dumps(sys.executable)}, "-c", "print(42)"]\n'
                 '[[server]]\nport = 1\n[[server]]\nport = 2\nssh = ""\npassword_env = "X_PW"\n')
    monkeypatch.setenv("X_PW", "fromenv")
    by, servers = load_config(p)
    resolve_passwords(servers)
    assert by == "op" and [s.ssh for s in servers] == ["box", ""]
    assert [s.password for s in servers] == ["42", "fromenv"]


def test_add_servers(tmp_path):
    p = tmp_path / "new" / "config.toml"
    add_servers(p, [Server(port=11774, name='Say "hi" \\ 🎮', password="pw")], by="op")
    add_servers(p, [Server(url="wss://x.example/s"), Server(host="::1", port=5, ssh="box", password="secret")],
                remember=False)
    by, servers = load_config(p)
    assert by == "op" and [s.where for s in servers] == ["127.0.0.1:11774", "wss://x.example/s", "::1:5 via ssh box"]
    assert servers[0].name == 'Say "hi" \\ 🎮' and servers[0].password == "pw" and not servers[2].password

    # appended to a hand-written file: its comments stay, and a [defaults] tunnel doesn't leak into a direct server
    p.write_text('# mine\n[defaults]\nssh = "game-box"\n[[server]]\nport = 1\n')
    add_servers(p, [Server(port=2, password="x")])
    assert p.read_text().startswith("# mine")
    assert [s.ssh for s in load_config(p)[1]] == ["game-box", ""]


def test_explain():
    assert explain("[Errno 111] Connect call failed ('127.0.0.1', 1); retry in 4s") .startswith("Nothing answered")
    assert explain("[Errno -2] Name or service not known; retry in 2s", short=True) == "UNKNOWN HOST"
    assert "password" in explain("Wrong password.", "denied")
    assert explain("something new; retry in 8s") == "something new"
    assert explain("[WinError 1225] The remote computer refused the network connection", short=True) == \
        "NOTHING ON THAT PORT"  # Windows words it differently


def test_emblem_fits():
    for rows, cols in [(30, 999), (20, 999), (16, 32), (12, 24)]:
        lines = emblem(rows, cols).split("\n")
        assert 0 < len(lines) <= rows and all(len(line) <= cols for line in lines)
        assert len({len(line) for line in lines}) == 1  # one block: the rows stay aligned when centred
    assert emblem(11).plain == ""


def test_event_lines():
    names = {7: "Viper", 9: "Rook"}
    assert render_event("s1", {"event": "kill", "killer": 7, "victim": 9, "weapon": "sword"}, names).plain.endswith(
        "Viper ✕ Rook  [sword]")
    assert "died" in render_event("s1", {"event": "kill", "victim": 9}, names).plain
    assert "[SERVER] hi" in render_event("s1", {"event": "chat", "channel": "server", "text": "hi"}, {}).plain
    line = render_event("s1", {"event": "ban", "name": "Rook", "address": "1.2.3.4"}, {}).plain
    assert "1.2.3.4" not in line and "BAN  Rook" in line
    assert target_of({"number": 3, "player_id": "abc"}) == "abc" and target_of({"number": 3}) == "#3"


def test_rcon_against_fake():
    async def go():
        (port, *_), keep = await serve_fakes(tick=False)
        events, states = [], []
        rc = Rcon(f"ws://127.0.0.1:{port}", "demo", "pytest", lambda _, e: events.append(e),
                  lambda _, s, d: states.append(s))
        task = asyncio.create_task(rc.run())
        while rc.state != "online":
            await asyncio.sleep(0.01)
        st = await rc.call("status")
        assert st["ok"] and st["data"]["max_players"] == 16
        players = (await rc.call("players"))["data"]["players"]
        victim = players[0]
        r = await rc.call("kick", victim["player_id"], "spawn camping")
        assert r["ok"]
        await asyncio.sleep(0.05)
        assert any(e.get("event") == "kick" for e in events)
        assert (await rc.call("players"))["data"]["count"] == len(players) - 1
        task.cancel()

        bad = Rcon(f"ws://127.0.0.1:{port}", "wrong", "pytest", lambda *_: None, lambda *_: None)
        await asyncio.wait_for(bad.run(), 5)  # returns instead of retrying
        assert bad.state == "denied"

    asyncio.run(go())


def test_app_against_fakes():
    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = OniApp([Server(port=p, password="demo") for p in ports], by="pytest")
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.press("escape")  # skip the splash
            await pilot.resize_terminal(150, 44)
            await pilot.pause()
            assert not isinstance(app.screen, Boot)  # a resize must not replay it
            for _ in range(100):
                await pilot.pause(0.05)
                if all(st.online and "players" in st.data for st in app.stations):
                    break
            assert app.stations[0].label == "Slayer"  # common "Demo Ops | " prefix stripped
            assert app.query_one("#players").row_count == len(app.stations[0].players) > 0
            assert app.query_one("#op-passvote").disabled  # no vote under way

            await pilot.click("#pl-tell")  # the player file's buttons open the same dialogs as the keys
            await pilot.pause()
            assert isinstance(app.screen, Form)
            await pilot.press("escape")
            await pilot.press("question_mark")
            await pilot.pause()
            assert isinstance(app.screen, Help)
            await pilot.press("escape")
            await pilot.pause()

            app.on_rcon_event(app.stations[1].rcon, {"type": "event", "event": "chat", "text": "admin [b]help"})
            assert app.stations[1].alerts == 1  # counted on the station not being looked at
            await pilot.press("2")
            await pilot.pause()
            assert app.stations[1].alerts == 0
            await pilot.press("1")
            await pilot.press("f5")
            app.query_one("#cmd").focus()
            app.query_one("#cmd").value = "say hello there"
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert any("hello there" in str(e.get("text")) for _, e in app.feed)
    asyncio.run(go())


def test_setup_screen(tmp_path):
    from oni_rcon.wizard import SetupApp, target
    assert target("10.0.0.5", "11774") == "10.0.0.5:11774" and target("box:5", "11774") == "box:5"
    assert target("::1", "5") == "[::1]:5" and target("wss://h/s", "11774") == "wss://h/s"

    async def go():
        ports, keep = await serve_fakes(tick=False)
        cfg = tmp_path / "config.toml"
        app = SetupApp(cfg, [], "op", ask_by=True)
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause(0.2)  # the emblem settles into the rows the form leaves
            status = lambda: str(app.query_one("#setup-status").render())
            assert app.query_one("#start").disabled  # nothing to start with yet
            app.query_one("#port").value = str(ports[0])
            app.query_one("#password").value = "wrong"
            assert await pilot.click("#add")
            for _ in range(100):
                await pilot.pause(0.05)
                if status().startswith("✗"):
                    break
            assert "refused the password" in status() and not app.added
            app.query_one("#password").value = "demo"
            assert await pilot.click("#add")
            for _ in range(100):
                await pilot.pause(0.05)
                if app.added:
                    break
            assert app.query_one("#port").value == str(ports[0] + 1)  # the next server on the box, probably
            assert await pilot.click("#start")
        assert [s.port for s in app.return_value] == [ports[0]]
        by, servers = load_config(cfg)
        assert by == "op" and servers[0].password == "demo"
    asyncio.run(go())


def test_self_update(tmp_path, monkeypatch):
    from oni_rcon import update
    exe, old, build = tmp_path / "oni-rcon.exe", tmp_path / "oni-rcon.exe.old", b"new build"
    exe.write_bytes(b"running build")
    rel = {"tag_name": "v99.0.0", "assets": [{"name": "a", "size": len(build), "browser_download_url": "dl",
                                              "digest": "sha256:" + hashlib.sha256(build).hexdigest()}]}
    monkeypatch.setattr(update, "ASSET", "a")
    monkeypatch.setattr(update, "fetch", lambda url, timeout=0: build if url == "dl" else json.dumps(rel).encode())
    monkeypatch.delenv("ONI_RCON_NO_UPDATE", raising=False)
    assert update.check(exe) == "Updated to v99.0.0. Restart oni-rcon to use it."
    assert exe.read_bytes() == build and old.read_bytes() == b"running build"

    rel["tag_name"], rel["assets"][0]["size"] = "v99.0.1", 1  # a cut-off download leaves the exe alone
    assert "update failed" in update.check(exe)
    assert exe.read_bytes() == build and not old.exists()  # last time's copy is cleared on the next start
    assert "uv tool upgrade" in update.check(None)  # not a release build: say how
    rel["tag_name"] = "v0.0.1"
    assert update.check(exe) == ""
