"""Project Reclaimer 0.10 servers name only base maps and built-in game types: the console adds the server's own
files (0.9.0+ibigs.30). Reply shapes are 0.10.2's, as a customer server gave them on 2026-11-10."""
import json
from types import SimpleNamespace

import pytest

from oni_rcon import local_content
from oni_rcon.content import arrange, items_for
from oni_rcon.local_content import before_010, fill, game_dir_of, lists_variants, local_entries

MAPS_010 = {"entries": [{"kind": "map", "name": "Valhalla", "reference": "riverworld"},
                        {"kind": "map", "name": "Guardian", "reference": "guardian"}]}
MODES_010 = {"entries": [{"kind": "mode", "name": "Slayer", "reference": "Slayer"},
                         {"kind": "mode", "name": "Forge", "reference": "Forge"}]}
MAPS_09 = {"entries": MAPS_010["entries"] + [{"kind": "variant", "name": "Club Dead", "reference": "Maps/Club Dead.mvar"}]}


def put(path, doc=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"_blf" if doc is None else json.dumps(doc).encode())
    return path


@pytest.fixture
def box(tmp_path):
    """A server's folders the way a customer stack lays them out: content and game side by side."""
    content, game = tmp_path / "content", tmp_path / "game"
    put(content / "Maps/Forge/Grasslands [V1.4].json", {"version": 2, "name": "Grasslands [V1.4]", "map": {}})
    put(content / "Maps/Club Dead.mvar")
    put(content / "Maps/Deleted/old.mvar")
    put(content / "Maps/.rf-partial.json", {"name": "half copied"})
    put(content / "Maps/notes.txt")
    put(content / "Maps/stray.bin")  # a game variant in the maps folder is not a map
    put(content / "Game Modes/Forge/koth.json", {"version": 2, "name": "KOTH Custom [V2.4]", "mode": "koth"})
    put(content / "Game Modes/FK FAT KID.bin")
    put(content / "Game Modes/Project Reclaimer/gun-game.rmeg")
    put(content / "Game Modes/broken.json").write_text("{not json")
    put(content / "Backups/Before 0.10/Maps/Forge/old.json", {"name": "a backup"})
    put(game / "halo3/map_variants/default_guardian_012.mvar")
    put(game / "halo3/hopper_map_variants/Grasslands upload.json", {"version": 1, "name": "Grasslands [V1.1]"})
    put(game / "halo3/hopper_map_variants/nested/ignored.mvar")
    put(game / "halo3/game_variants/CTF upload.json", {"version": 2, "name": "CTF Custom [V2.4]"})
    put(game / "halo3/hopper_game_variants/2v2_team_slayer_010.bin")
    put(game / "haloreach/game_variants/headhunter_054.bin")
    server = SimpleNamespace(content_dir=str(content), game_dir="", ssh="", url="")
    return server, content, game


def refs(data):
    return [e["reference"] for e in data["entries"]]


def test_a_010_reply_gets_the_servers_own_maps(box):
    server, _content, _game = box
    got = fill(server, "maps", MAPS_010, "0.10.2")
    assert refs(got)[:2] == ["riverworld", "guardian"]  # what the server said comes first, untouched
    assert set(refs(got)[2:]) == {"Maps/Club Dead.mvar", "Maps/Forge/Grasslands [V1.4].json",
                                  "default_guardian_012.mvar", "Grasslands upload.json"}
    by_ref = {e["reference"]: e for e in got["entries"]}
    assert by_ref["Maps/Forge/Grasslands [V1.4].json"]["name"] == "Grasslands [V1.4]"  # the save's own title
    assert by_ref["Maps/Club Dead.mvar"]["name"] == "Club Dead"  # a binary variant: its file name
    assert by_ref["Grasslands upload.json"]["name"] == "Grasslands [V1.1]"
    assert all(e["kind"] == "variant" for e in got["entries"][2:])
    assert got["filled_from_files"] == 4
    assert MAPS_010["entries"] == [{"kind": "map", "name": "Valhalla", "reference": "riverworld"},
                                   {"kind": "map", "name": "Guardian", "reference": "guardian"}]  # the reply is not edited


def test_a_010_reply_gets_the_servers_own_game_types(box):
    server, _content, _game = box
    got = fill(server, "modes", MODES_010, "0.10.2")
    assert set(refs(got)[2:]) == {"Game Modes/FK FAT KID.bin", "Game Modes/Forge/koth.json",
                                  "Game Modes/Project Reclaimer/gun-game.rmeg", "Game Modes/broken.json",
                                  "CTF upload.json", "2v2_team_slayer_010.bin", "headhunter_054.bin"}
    by_ref = {e["reference"]: e for e in got["entries"]}
    assert by_ref["Game Modes/Forge/koth.json"]["name"] == "KOTH Custom [V2.4]"
    assert by_ref["Game Modes/broken.json"]["name"] == "broken"  # unreadable: still offered, the server says why


def test_what_is_left_out(box):
    server, _content, _game = box
    everything = refs(fill(server, "maps", MAPS_010, "0.10.2")) + refs(fill(server, "modes", MODES_010, "0.10.2"))
    for gone in ("old.mvar", ".rf-partial", "notes.txt", "stray.bin", "ignored.mvar", "Backups"):
        assert not any(gone in r for r in everything), gone


def test_older_servers_and_remote_ones_are_left_alone(box):
    server, content, _game = box
    assert fill(server, "maps", MAPS_09, "0.10.2") is MAPS_09  # it names its variants itself
    assert fill(server, "maps", MAPS_010, "0.9.23") is MAPS_010  # an empty 0.9 list is an empty list
    assert fill(server, "maps", MAPS_010, "0.9.7-demo") is MAPS_010
    assert fill(server, "status", MAPS_010, "0.10.2") is MAPS_010
    for far in (SimpleNamespace(content_dir=str(content), game_dir="", ssh="admin@box", url=""),
                SimpleNamespace(content_dir=str(content), game_dir="", ssh="", url="wss://example.invalid/rcon"),
                SimpleNamespace(content_dir="", game_dir="", ssh="", url="")):
        assert fill(far, "maps", MAPS_010, "0.10.2") is MAPS_010
    empty = SimpleNamespace(content_dir=str(content / "nowhere"), game_dir="", ssh="", url="")
    assert fill(empty, "maps", MAPS_010, "0.10.2") is MAPS_010  # nothing found: the reply as it came


def test_versions():
    assert before_010("0.9.23") and before_010("v0.9.7-demo") and before_010("0.8.1+ibigs.2")
    assert not before_010("0.10.0") and not before_010("0.10.2") and not before_010("1.0.0")
    assert not before_010("") and not before_010(None) and not before_010("?")  # unknown: decided by the reply itself
    assert lists_variants(MAPS_09) and not lists_variants(MAPS_010) and not lists_variants({})


def test_the_game_folder_is_found_beside_the_content_folder_or_named(box, tmp_path):
    server, _content, game = box
    assert game_dir_of(server) == str(game)
    other = tmp_path / "elsewhere"
    put(other / "halo3/map_variants/only_here.mvar")
    named = SimpleNamespace(content_dir=server.content_dir, game_dir=str(other), ssh="", url="")
    assert game_dir_of(named) == str(other)
    assert "only_here.mvar" in refs(fill(named, "maps", MAPS_010, "0.10.2"))
    assert "default_guardian_012.mvar" not in refs(fill(named, "maps", MAPS_010, "0.10.2"))
    alone = SimpleNamespace(content_dir=str(tmp_path / "solo" / "content"), game_dir="", ssh="", url="")
    assert game_dir_of(alone) == ""


def test_a_file_the_server_already_names_is_not_added_twice(box):
    server, _content, _game = box
    said = {"entries": MAPS_010["entries"] + [{"kind": "map", "name": "x", "reference": "maps/club dead.MVAR"}]}
    assert refs(fill(server, "maps", said, "0.10.2")).count("Maps/Club Dead.mvar") == 0


def test_the_browser_groups_them_where_people_look(box):
    server, _content, _game = box
    maps = arrange(items_for(fill(server, "maps", MAPS_010, "0.10.2")["entries"], {}), "maps")
    groups = {key: {i.name for i in rows} for key, _title, rows in maps}
    assert groups["stock"] == {"Valhalla", "Guardian"}
    assert "Grasslands [V1.4]" in groups["forge"]  # the content folder's Forge folder
    assert groups["folder"] == {"Club Dead", "Grasslands [V1.1]"}  # put in by hand, wherever it was put
    assert groups["builtin"] == {"default_guardian_012"}
    modes = arrange(items_for(fill(server, "modes", MODES_010, "0.10.2")["entries"], {}), "modes")
    groups = {key: {i.name for i in rows} for key, _title, rows in modes}
    assert {"FK FAT KID", "gun-game", "CTF Custom [V2.4]"} <= groups["folder"]
    assert {"2v2_team_slayer_010", "headhunter_054"} <= groups["builtin"]


def test_a_name_is_read_once_per_file_version(box, monkeypatch):
    server, content, _game = box
    local_content._names.clear()
    fill(server, "maps", MAPS_010, "0.10.2")
    opened = []
    real = local_content.Path.read_text
    monkeypatch.setattr(local_content.Path, "read_text", lambda self, *a, **k: opened.append(self) or real(self, *a, **k))
    fill(server, "maps", MAPS_010, "0.10.2")
    assert opened == []  # nothing changed on disk: nothing is opened again
    path = content / "Maps/Forge/Grasslands [V1.4].json"
    path.write_text(json.dumps({"version": 2, "name": "Grasslands renamed in game", "map": {}}))
    got = {e["reference"]: e["name"] for e in fill(server, "maps", MAPS_010, "0.10.2")["entries"]}
    assert got["Maps/Forge/Grasslands [V1.4].json"] == "Grasslands renamed in game" and len(opened) == 1


def test_a_huge_folder_is_cut_off(box, monkeypatch):
    server, content, _game = box
    monkeypatch.setattr(local_content, "SCAN_LIMIT", 5)
    for n in range(20):
        put(content / f"Maps/bulk/{n:02}.mvar")
    assert len(local_entries("maps", server.content_dir, game_dir_of(server))) == 5


def test_the_console_shows_a_010_servers_files_in_its_pickers(box, monkeypatch):
    """End to end against a stand-in 0.10 server: its lists name no variants, the pickers show the files anyway, and
    choosing one sends the reference the server resolves."""
    import asyncio

    from oni_rcon import demo
    from oni_rcon.app import OniApp
    from oni_rcon.config import Server
    from oni_rcon.content_ui import Browser

    server, content, _game = box
    monkeypatch.setattr(demo.Fake, "VERSION", "0.10.2")
    monkeypatch.setattr(demo.Fake, "LISTS_VARIANTS", False)

    async def until(pilot, cond, tries=200):
        for _ in range(tries):
            await pilot.pause(0.05)
            if cond():
                return True
        return False

    async def go():
        ports, _keep = await demo.serve_fakes(tick=False, specs=demo.SERVERS[:1])
        app = OniApp([Server(port=ports[0], password="demo", content_dir=str(content))], by="pytest", intro=False)
        async with app.run_test(size=(160, 48)) as pilot:
            assert await until(pilot, lambda: all(st.online and "modes" in st.data and "maps" in st.data
                                                  for st in app.stations))
            st = app.stations[0]
            maps = {e["reference"]: e for e in st.data["maps"]["entries"]}
            modes = {e["reference"]: e for e in st.data["modes"]["entries"]}
            assert maps["Maps/Forge/Grasslands [V1.4].json"]["name"] == "Grasslands [V1.4]"
            assert "default_guardian_012.mvar" in maps and "Grasslands upload.json" in maps
            assert modes["Game Modes/Forge/koth.json"]["name"] == "KOTH Custom [V2.4]"
            assert "CTF upload.json" in modes
            await pilot.press("f3")
            app.query_one("#op-load").press()
            assert await until(pilot, lambda: isinstance(app.screen, Browser) and app.screen.query("#browse-tree")
                               and app.screen.query_one("#browse-tree").root.children)
            names = [str(n.label) for n in app.screen.query_one("#browse-tree").root.children]
            assert any("YOUR CONTENT FOLDER" in n for n in names) and any("RECLAIMERFORGE DOWNLOADS" in n for n in names)
            assert any("HALO 3 & MATCHMAKING" in n for n in names)

    asyncio.run(go())
