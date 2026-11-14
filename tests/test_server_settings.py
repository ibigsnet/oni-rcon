"""SERVER SETTINGS: a 0.10 server's dedicated.toml read, checked, edited in place (comments and order kept), backed
up, with secrets never shown; and the screen that does it (Ctrl+E)."""
import asyncio
import os
import tomllib
from pathlib import Path

import pytest

from oni_rcon import demo
from oni_rcon import server_settings as ss
from oni_rcon.app import Confirm, OniApp, Pick
from oni_rcon.config import Server
from oni_rcon.demo import serve_fakes
from oni_rcon.settings_ui import SettingsView

BOT = "ab" * 32
OTHER_BOT = "cd" * 32

FILE = """\
# Dedicated servers (a test file).
game = "game"
host_name = "Test host"

[rcon]
password = "hunter2-rcon-secret"
address = "127.0.0.1"

# Used by every server unless its own block says otherwise.
[defaults]
max_players = 16
min_players = 1
# The highest ping (ms) a player may join with; 0 takes everyone.
max_ping = 0  # keep me
password = "join-secret"
admins = [
  "x",
  "y",
]
# Game ticks a second.
#tick_rate = 60
allow_bots = false

# One [[server]] block per server.

[[server]]
name = "First"
port = 49176
playlist = "playlists/slayer.playlist.json"
rcon_password = "per-server-secret"
weights_note = [
  [1, 2],
  [3, 4],
]
social_key = "c29tZSBrZXk="

[[server]]
name = "Second"
port = 49177
map = "Guardian"
game = "Slayer"
"""


def loaded_from(text: str, port: int = 49176) -> ss.Loaded:
    data = ss.parse(text)
    return ss.Loaded(Path("dedicated.toml"), text, data, ss.server_index(data, port))


def apply(text: str, changes, port: int = 49176) -> str:
    return ss.edit(text, loaded_from(text, port).index, changes)


def changed_lines(before: str, after: str) -> tuple[list[str], list[str]]:
    import difflib
    diff = list(difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0))
    return ([d[1:] for d in diff if d.startswith("-") and not d.startswith("---")],
            [d[1:] for d in diff if d.startswith("+") and not d.startswith("+++")])


# --- finding the file ------------------------------------------------------------------------------------------------
def test_the_file_is_found_beside_the_servers_folders(tmp_path):
    inst = tmp_path / "inst"
    (inst / "data" / "First").mkdir(parents=True)
    (inst / "dedicated.toml").write_text(FILE)
    for server in (Server(port=49176, host_key_dir=str(inst / "data")),
                   Server(port=49176, host_key_dir=str(inst / "data" / "First")),
                   Server(port=49176, content_dir=str(inst / "content")),
                   Server(port=49176, dedicated_toml=str(inst / "dedicated.toml"))):
        got = ss.locate(server)
        assert got.path == inst / "dedicated.toml" and got.block["name"] == "First"
    assert ss.locate(Server(port=49177, host_key_dir=str(inst / "data"))).block["name"] == "Second"


def test_a_shared_content_folder_does_not_win_over_the_servers_own_data_folder(tmp_path):
    shared, inst = tmp_path / "shared", tmp_path / "inst"
    (shared / "content").mkdir(parents=True)
    (inst / "data").mkdir(parents=True)
    (shared / "dedicated.toml").write_text('[[server]]\nname = "Elsewhere"\nport = 1\n')
    (inst / "dedicated.toml").write_text(FILE)
    got = ss.locate(Server(port=49176, host_key_dir=str(inst / "data"), content_dir=str(shared / "content")))
    assert got.path == inst / "dedicated.toml"


def test_no_file_for_a_remote_server_or_the_wrong_port(tmp_path):
    (tmp_path / "dedicated.toml").write_text(FILE)
    with pytest.raises(ss.SettingsError, match="ssh"):
        ss.locate(Server(port=49176, ssh="admin@box", dedicated_toml=str(tmp_path / "dedicated.toml")))
    with pytest.raises(ss.SettingsError, match="port 50000"):
        ss.locate(Server(port=50000, dedicated_toml=str(tmp_path / "dedicated.toml")))
    with pytest.raises(ss.SettingsError, match="doesn't know where"):
        ss.locate(Server(port=49176))
    with pytest.raises(ss.SettingsError, match="Looked at"):
        ss.locate(Server(port=49176, host_key_dir=str(tmp_path / "a" / "b" / "c" / "data")))


def test_rcon_port_names_the_block():
    text = FILE.replace('name = "Second"\nport = 49177', 'name = "Second"\nport = 49177\nrcon_port = 49300')
    assert loaded_from(text, 49300).block["name"] == "Second"
    with pytest.raises(ss.SettingsError):
        loaded_from(text, 49177)


def test_version_gate():
    assert ss.supports("0.10.3") is True and ss.supports("0.10.0-rc1") is True and ss.supports("1.0.0") is True
    assert ss.supports("0.9.23") is False and ss.supports("0.9.7-demo") is False
    assert ss.supports(None) is None and ss.supports("") is None


# --- checking values ---------------------------------------------------------------------------------------------
def test_values_are_checked_against_the_documented_types_and_ranges():
    tick, model, ping = ss.BY_KEY["tick_rate"], ss.BY_KEY["network_model"], ss.BY_KEY["max_ping"]
    assert ss.check(tick, "30") == 30 and ss.check(tick, 60) == 60
    with pytest.raises(ss.SettingsError, match="60, 30"):
        ss.check(tick, "45")
    assert ss.check(model, "enhanced") == "enhanced"
    with pytest.raises(ss.SettingsError):
        ss.check(model, "turbo")
    assert ss.check(ping, "1000") == 1000
    for bad in ("1001", "-1", "fast", "1.5", True):
        with pytest.raises(ss.SettingsError):
            ss.check(ping, bad)
    assert ss.check(ss.BY_KEY["allow_bots"], "on") is True and ss.check(ss.BY_KEY["allow_bots"], False) is False
    with pytest.raises(ss.SettingsError):
        ss.check(ss.BY_KEY["allow_bots"], "maybe")
    with pytest.raises(ss.SettingsError, match="48"):
        ss.check(ss.BY_KEY["name"], "x" * 49)
    with pytest.raises(ss.SettingsError, match="one line"):
        ss.check(ss.BY_KEY["welcome"], "hi\x1b[2Jthere")
    assert ss.check(ss.BY_KEY["friendly_fire"], False) is False  # a choice whose values are true and false


def test_players_to_start_never_above_the_most_players():
    loaded = loaded_from(FILE)
    with pytest.raises(ss.SettingsError, match="can't be more"):
        ss.changes_for(loaded, "min_players", 20, "server")
    with pytest.raises(ss.SettingsError, match=r"\(4\).*\(2\)"):
        ss.changes_for(loaded_from(FILE.replace("min_players = 1", "min_players = 4")), "max_players", 2, "server")
    assert ss.changes_for(loaded, "min_players", 16, "server")


def test_raw_values_for_settings_without_a_control():
    assert ss.raw_value("true") is True and ss.raw_value("30") == 30
    assert ss.raw_value('["a", "b"]') == ["a", "b"] and ss.raw_value("plain words") == "plain words"
    for bad in ("", "{ a = 1 }", "[[1]]"):
        with pytest.raises(ss.SettingsError):
            ss.raw_value(bad)
    assert ss.editable("text_chat") == ""
    for locked in ("port", "playlist", "admins", "public_address", "rcon_password", "social_key", "a.b"):
        assert ss.editable(locked), locked


# --- the editor --------------------------------------------------------------------------------------------------
def test_a_change_moves_one_line_and_keeps_everything_else():
    out = apply(FILE, [ss.Change(("server",), "network_model", "enhanced")])
    gone, added = changed_lines(FILE, out)
    assert gone == [] and added == ['network_model = "enhanced"']
    data = tomllib.loads(out)
    assert data["server"][0]["network_model"] == "enhanced" and "network_model" not in data["server"][1]
    # it went in the first server's own keys, before the second block
    assert out.index('network_model = "enhanced"') < out.index('name = "Second"')


def test_a_new_key_goes_beside_its_commented_example():
    out = apply(FILE, [ss.Change(("defaults",), "tick_rate", 30)])
    assert "#tick_rate = 60\ntick_rate = 30\nallow_bots = false" in out
    assert tomllib.loads(out)["defaults"]["tick_rate"] == 30


def test_a_replaced_value_keeps_its_comment_and_a_removed_one_goes():
    out = apply(FILE, [ss.Change(("defaults",), "max_ping", 150)])
    assert "max_ping = 150  # keep me\n" in out and "max_ping = 0" not in out
    back = apply(out, [ss.Change(("defaults",), "max_ping", ss.REMOVE)])
    gone, added = changed_lines(out, back)
    assert gone == ["max_ping = 150  # keep me"] and added == []
    assert "max_ping" not in tomllib.loads(back)["defaults"]


def test_arrays_over_several_lines_are_not_taken_for_headers():
    out = apply(FILE, [ss.Change(("server",), "tick_rate", 30), ss.Change(("defaults",), "voice_quality", "high")])
    data = tomllib.loads(out)
    assert data["server"][0]["weights_note"] == [[1, 2], [3, 4]] and data["server"][0]["tick_rate"] == 30
    assert data["defaults"]["admins"] == ["x", "y"] and data["defaults"]["voice_quality"] == "high"
    gone, added = changed_lines(FILE, out)
    assert gone == [] and sorted(added) == ["tick_rate = 30", 'voice_quality = "high"']


def test_crlf_and_a_byte_order_mark_are_kept():
    text = "﻿" + FILE.replace("\n", "\r\n")
    out = ss.edit(text, 0, [ss.Change(("server",), "tick_rate", 30)])
    assert out.startswith("﻿") and "tick_rate = 30\r\n" in out and "\n" not in out.replace("\r\n", "")


def test_a_layout_it_cant_edit_safely_is_refused():
    inline = 'roles = { dj = { can = ["say"] } }\n\n[[server]]\nname = "A"\nport = 1\n'
    loaded = loaded_from(inline, 1)
    with pytest.raises(ss.SettingsError, match="won't edit"):
        ss.edit(inline, 0, ss.add_bot(loaded, BOT))
    dotted = '[[server]]\nname = "A"\nport = 1\nvotes.kick = "admin"\n'
    with pytest.raises(ss.SettingsError, match="won't edit"):
        ss.edit(dotted, 0, [ss.Change(("server",), "votes", "off")])


def test_strings_with_brackets_and_hashes_dont_confuse_it():
    text = FILE.replace('name = "First"', 'name = "First [EU] # one"  # the [[server]] list name')
    out = apply(text, [ss.Change(("server",), "name", "First [US]")])
    assert 'name = "First [US]"  # the [[server]] list name\n' in out
    assert tomllib.loads(out)["server"][0]["name"] == "First [US]"


# --- saving, backups, restoring ------------------------------------------------------------------------------
def test_save_backs_up_first_and_writes_in_place(tmp_path):
    path = tmp_path / "dedicated.toml"
    path.write_text(FILE)
    inode, mode = path.stat().st_ino, path.stat().st_mode
    loaded = ss.load(path, 49176)
    after, saved = ss.save(loaded, ss.changes_for(loaded, "tick_rate", 30, "server"))
    assert saved.read_text() == FILE and saved.name.startswith("dedicated.toml.oni-backup-")
    assert path.stat().st_ino == inode and path.stat().st_mode == mode
    assert tomllib.loads(path.read_text())["server"][0]["tick_rate"] == 30 and after.block["tick_rate"] == 30
    _, nothing = ss.save(after, ss.changes_for(after, "tick_rate", 30, "server"))
    assert nothing is None and len(ss.backups(path)) == 1  # no change: no write, no backup
    ss.restore(path, saved)
    assert path.read_text() == FILE


def test_only_the_newest_backups_are_kept(tmp_path):
    path = tmp_path / "dedicated.toml"
    path.write_text("a = 1\n")
    made = []
    for i in range(ss.BACKUPS_KEPT + 3):
        path.write_text(f"a = {i}\n")
        made.append(ss.backup(path))
        os.utime(made[-1], ns=(i * 10**9, i * 10**9))  # in order, though they share a second
    kept = ss.backups(path)
    assert len(kept) == ss.BACKUPS_KEPT and kept == made[-ss.BACKUPS_KEPT:]
    assert kept[-1].read_text() == f"a = {ss.BACKUPS_KEPT + 2}\n"


def test_a_file_changed_meanwhile_is_not_overwritten(tmp_path):
    path = tmp_path / "dedicated.toml"
    path.write_text(FILE)
    loaded = ss.load(path, 49176)
    path.write_text(FILE.replace("max_players = 16", "max_players = 24"))
    with pytest.raises(ss.SettingsError, match="changed since"):
        ss.save(loaded, ss.changes_for(loaded, "tick_rate", 30, "server"))
    assert ss.backups(path) == [] and "max_players = 24" in path.read_text()


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs a file this user can't write")
def test_a_file_it_cant_write_says_so(tmp_path):
    path = tmp_path / "dedicated.toml"
    path.write_text(FILE)
    path.chmod(0o444)
    loaded = ss.load(path, 49176)
    with pytest.raises(ss.SettingsError, match="can't be written"):
        ss.save(loaded, ss.changes_for(loaded, "tick_rate", 30, "server"))
    assert path.read_text() == FILE


# --- secrets ---------------------------------------------------------------------------------------------------
def test_passwords_and_keys_are_never_shown():
    for key in ("password", "rcon_password", "social_key", "api_token", "client_secret", "rcon.password"):
        assert ss.is_secret(key), key
    for key in ("film_keep", "forge_autosave_keep", "max_players", "keyboard_layout"):
        assert not ss.is_secret(key), key
    loaded = loaded_from(FILE)
    every = " ".join(f"{r.server} {r.defaults} {r.in_use}" for r in ss.all_rows(loaded))
    every += " ".join(f"{r.server} {r.defaults} {r.in_use}" for g, *_ in ss.GROUPS for r in ss.group_rows(loaded, g))
    for secret in ("hunter2-rcon-secret", "join-secret", "per-server-secret", "c29tZSBrZXk="):
        assert secret not in every
    rows = {r.key: r for r in ss.all_rows(loaded)}
    assert rows["password"].in_use == ss.masked("x") and rows["rcon_password"].server == ss.masked("x")
    assert rows["rcon_password"].why_locked and rows["social_key"].why_locked
    assert "rcon" not in rows  # a table: it stays in the file
    assert ss.shown("password", "") == "(empty)"


def test_which_value_wins():
    text = FILE.replace('name = "First"', 'name = "First"\nmax_players = 24')
    loaded = loaded_from(text)
    rows = {r.key: r for r in ss.group_rows(loaded, "players")}
    assert (rows["max_players"].server, rows["max_players"].defaults, rows["max_players"].in_use,
            rows["max_players"].source) == ("24", "16", "24", "this server")
    assert rows["min_players"].source == "[defaults]"
    perf = {r.key: r for r in ss.group_rows(loaded, "performance")}
    assert perf["tick_rate"].source == "server default" and perf["tick_rate"].in_use == "60"
    assert ss.value_words(ss.BY_KEY["network_model"], "network_model", "classic").startswith("Classic")
    assert rows["host_name"].defaults == "Test host"  # host_name's second place is the file's top


# --- bots --------------------------------------------------------------------------------------------------------
def test_adding_a_bot_makes_its_role_once_then_adds_to_it():
    loaded = loaded_from(FILE)
    out = apply(FILE, ss.add_bot(loaded, BOT))
    role = tomllib.loads(out)["roles"]["bots"]
    assert role == {"title": "Bot", "rank": 1, "can": ["bots.run"], "players": [BOT]}
    assert out.index("[roles.bots]") < out.index("# One [[server]] block per server.")  # above the servers' notes
    again = loaded_from(out)
    assert ss.bot_ids(again) == [BOT]
    with pytest.raises(ss.SettingsError, match="already"):
        ss.add_bot(again, BOT)
    two = apply(out, ss.add_bot(again, OTHER_BOT))
    assert tomllib.loads(two)["roles"]["bots"]["players"] == [BOT, OTHER_BOT]
    gone = apply(two, ss.remove_bot(loaded_from(two), BOT))
    assert tomllib.loads(gone)["roles"]["bots"]["players"] == [OTHER_BOT]


def test_a_role_that_already_runs_bots_is_used():
    text = FILE.replace("# One [[server]]", '[roles.dj]\ntitle = "DJ"\ncan = ["say", "bots.run"]\nplayers = []\n\n'
                                            "# One [[server]]")
    out = apply(text, ss.add_bot(loaded_from(text), BOT))
    assert tomllib.loads(out)["roles"]["dj"]["players"] == [BOT] and "bots" not in tomllib.loads(out)["roles"]
    assert [(n, p) for _, n, p in ss.bot_roles(loaded_from(out))] == [("dj", [BOT])]


def test_bot_ids_are_checked_and_roles_are_limited():
    loaded = loaded_from(FILE)
    for bad in ("", "abc", "g" * 64, BOT + "a"):
        with pytest.raises(ss.SettingsError, match="64"):
            ss.add_bot(loaded, bad)
    many = FILE.replace("# One [[server]]", "".join(f'[roles.r{i}]\ncan = ["say"]\n' for i in range(12))
                        + "\n# One [[server]]")
    with pytest.raises(ss.SettingsError, match="12 roles"):
        ss.add_bot(loaded_from(many), BOT)


def test_reload_answers():
    assert ss.reload_words("Applied now: welcome. Restarts once it has no players, for: network_model.") == (True,
                                                                                                           False)
    assert ss.reload_words("Applied from the next game: tick_rate.") == (False, False)
    assert ss.reload_words("Restart the supervisor to apply: public_address.") == (False, True)


# --- the screen ------------------------------------------------------------------------------------------------
async def until(pilot, cond, n=160):
    for _ in range(n):
        await pilot.pause(0.05)
        if cond():
            return True
    return False


def test_ctrl_e_changes_the_tick_rate_with_a_backup_and_a_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(demo.Fake, "VERSION", "0.10.3")

    async def go():
        ports, keep = await serve_fakes(tick=False, specs=demo.SERVERS[:1])
        path = tmp_path / "dedicated.toml"
        path.write_text(FILE.replace("port = 49176", f"port = {ports[0]}"))
        before = path.read_text()
        app = OniApp([Server(port=ports[0], password="demo", dedicated_toml=str(path))], by="pytest", intro=False)
        async with app.run_test(size=(160, 48)) as pilot:
            assert await until(pilot, lambda: all(st.online for st in app.stations))
            await pilot.press("f3")
            await pilot.pause(0.2)
            assert not app.query_one("#op-settings").disabled
            await pilot.press("ctrl+e")
            assert await until(pilot, lambda: isinstance(app.screen, SettingsView))
            view = app.screen
            assert view.editable and "No bots are in." in view.bots
            perf = view.query_one("#set-performance")
            assert perf.row_count == len(ss.group_rows(view.loaded, "performance"))
            await pilot.pause(0.2)
            assert "30 holds big lobbies" in str(view.query_one("#set-hint").render())
            view.act("edit")  # the first row: tick_rate
            assert await until(pilot, lambda: isinstance(app.screen, Pick))
            assert "WHERE" in app.screen.t  # two servers in the file: this one, or [defaults]?
            assert [v for _, v in app.screen.opts] == ["server", "defaults"]
            app.screen.dismiss("server")
            assert await until(pilot, lambda: isinstance(app.screen, Pick) and "TICK RATE" in app.screen.t)
            assert [v for _, v in app.screen.opts] == [60, 30]
            app.screen.dismiss(30)
            assert await until(pilot, lambda: isinstance(app.screen, Confirm) and app.screen.verb == "SAVE")
            assert "from the next game" in app.screen.b
            app.screen.dismiss(True)
            assert await until(pilot, lambda: isinstance(app.screen, Confirm) and app.screen.t == "SAVED")
            assert "Nothing changed." in app.screen.b
            app.screen.dismiss(True)
            assert await until(pilot, lambda: isinstance(app.screen, SettingsView))
            assert app.screen.loaded.block["tick_rate"] == 30
            app.screen.dismiss(None)
        data = tomllib.loads(path.read_text())
        assert data["server"][0]["tick_rate"] == 30
        backups = ss.backups(path)
        assert len(backups) == 1 and backups[0].read_text() == before
        del keep
    asyncio.run(go())


def test_an_older_server_shows_its_settings_without_changing_them(tmp_path):
    async def go():
        ports, keep = await serve_fakes(tick=False, specs=demo.SERVERS[:1])  # the demo says 0.9.7
        path = tmp_path / "dedicated.toml"
        path.write_text(FILE.replace("port = 49176", f"port = {ports[0]}"))
        app = OniApp([Server(port=ports[0], password="demo", dedicated_toml=str(path))], by="pytest", intro=False)
        async with app.run_test(size=(160, 48)) as pilot:
            assert await until(pilot, lambda: all(st.online for st in app.stations))
            await pilot.press("ctrl+e")
            assert await until(pilot, lambda: isinstance(app.screen, SettingsView))
            assert not app.screen.editable and "0.10 or newer" in app.screen.note
            assert app.screen.query_one("#edit").disabled
            app.screen.dismiss(None)
        assert ss.backups(path) == []
        del keep
    asyncio.run(go())


def test_a_server_over_ssh_is_told_where_to_run_it():
    async def go():
        ports, keep = await serve_fakes(tick=False, specs=demo.SERVERS[:1])
        app = OniApp([Server(port=ports[0], password="demo")], by="pytest", intro=False)
        async with app.run_test(size=(160, 48)) as pilot:
            assert await until(pilot, lambda: all(st.online for st in app.stations))
            app.stations[0].server.ssh = "admin@box"  # as if reached over ssh: its files are elsewhere
            await pilot.press("ctrl+e")
            assert await until(pilot, lambda: isinstance(app.screen, Confirm))
            assert "another machine" in app.screen.b
            app.screen.dismiss(False)
        del keep
    asyncio.run(go())


def one_server(port: int) -> str:
    return FILE[:FILE.index("\n[[server]]\nname = \"Second\"")].replace("port = 49176", f"port = {port}") + "\n"


def test_bots_and_a_setting_that_needs_a_restart(tmp_path, monkeypatch):
    from oni_rcon import settings_ui
    monkeypatch.setattr(demo.Fake, "VERSION", "0.10.3")
    restarted = []

    async def fake_restart(app, st, loaded):
        restarted.append(loaded.path)
    monkeypatch.setattr(settings_ui, "restart", fake_restart)

    async def go():
        ports, keep = await serve_fakes(tick=False, specs=demo.SERVERS[:1])
        path = tmp_path / "dedicated.toml"
        path.write_text(one_server(ports[0]))
        app = OniApp([Server(port=ports[0], password="demo", dedicated_toml=str(path))], by="pytest", intro=False)
        async with app.run_test(size=(160, 48)) as pilot:
            assert await until(pilot, lambda: all(st.online for st in app.stations))
            await pilot.press("ctrl+e")
            assert await until(pilot, lambda: isinstance(app.screen, SettingsView))
            app.screen.dismiss(("addbot", "", "bots"))
            assert await until(pilot, lambda: getattr(app.screen, "t", "") == "ADD A BOT")
            assert "8 bots at most" in app.screen.note
            app.screen.dismiss({"id": BOT})
            assert await until(pilot, lambda: isinstance(app.screen, Confirm) and app.screen.verb == "SAVE")
            assert "allow_bots turned on" in app.screen.b
            app.screen.dismiss(True)
            assert await until(pilot, lambda: isinstance(app.screen, Confirm) and app.screen.t == "SAVED")
            app.screen.dismiss(True)
            assert await until(pilot, lambda: isinstance(app.screen, SettingsView))
            assert app.screen.tab == "bots" and BOT in str(app.screen.query_one("#set-botlist").render())
            # one server in the file: the netcode is changed where it's in force, without asking where
            app.screen.dismiss(("edit", "network_model", "network"))
            assert await until(pilot, lambda: isinstance(app.screen, Pick) and "NETCODE" in app.screen.t)
            app.screen.dismiss("enhanced")
            assert await until(pilot, lambda: isinstance(app.screen, Confirm) and app.screen.verb == "SAVE")
            assert ss.RESTART in app.screen.b
            app.screen.dismiss(True)
            assert await until(pilot, lambda: isinstance(app.screen, Pick) and "RESTART" in app.screen.t)
            assert [v for _, v in app.screen.opts] == ["wait", "now"]
            app.screen.dismiss("now")
            assert await until(pilot, lambda: isinstance(app.screen, Confirm) and app.screen.t == "RESTART NOW")
            assert "disconnected" in app.screen.b  # the demo server has players on it: asked once more
            app.screen.dismiss(True)
            assert await until(pilot, lambda: isinstance(app.screen, SettingsView))
            app.screen.dismiss(None)
        data = tomllib.loads(path.read_text())
        assert data["defaults"]["allow_bots"] is True and data["roles"]["bots"]["players"] == [BOT]
        assert data["server"][0]["network_model"] == "enhanced" and restarted == [path]
        assert len(ss.backups(path)) == 2
        del keep
    asyncio.run(go())


def test_the_customer_consoles_restart_names_the_lobby_by_its_folder(tmp_path):
    from types import SimpleNamespace
    from oni_rcon.settings_ui import lobby_of

    async def names():
        return ["h3-07"]
    app = SimpleNamespace(playlist_tool=lambda: "oni", lobby_names=names)
    here = ss.Loaded(tmp_path / "h3-07" / "dedicated.toml", "", {}, 0)
    elsewhere = ss.Loaded(tmp_path / "other" / "dedicated.toml", "", {}, 0)
    assert asyncio.run(lobby_of(app, here)) == "h3-07"
    assert asyncio.run(lobby_of(app, elsewhere)) == ""
    assert asyncio.run(lobby_of(SimpleNamespace(playlist_tool=lambda: ""), here)) == ""
