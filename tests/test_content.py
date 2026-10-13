"""The map and game type browser, the F6 column orders and the F2 scope (IBIGS 0.9.0+ibigs.11)."""
import asyncio

import pytest

from oni_rcon.content import (PlaylistError, arrange, catalog_items, check_playlist, items_for, ordered,
                              playlist_json)

# shaped like `dedicated rcon --json maps` / `modes` on a customer VPS (VM 3100, 2026-10-13)
MAPS = [{"kind": "map", "name": "Valhalla", "reference": "riverworld"},
        {"kind": "map", "name": "Guardian", "reference": "guardian"},
        {"kind": "map", "name": "Hang 'Em High", "reference": "hang_em_high"},
        {"kind": "variant", "name": "Guardian", "reference": "default_guardian_012.mvar"},
        {"kind": "variant", "name": "MLG Guardian", "reference": "MLG Guardian"},
        {"kind": "variant", "name": "Pit Stop", "reference": "Maps/Forge/rf-3c1d0a9e44b2f7d81e02.json"},
        {"kind": "variant", "name": "Club Dead", "reference": "Maps/Club Dead.mvar"}]
MODES = [{"kind": "mode", "name": "Slayer", "reference": "Slayer"},
         {"kind": "variant", "name": "Team Slayer", "reference": "2v2_team_slayer_010.bin"},
         {"kind": "variant", "name": "UFM | ASSAULT", "reference": "Game Modes/Forge/rf-d70540eb57f22df48390.json"},
         {"kind": "variant", "name": "UFM | ASSAULT", "reference": "Game Modes/Forge/UFM Encompass/UFM _ASSAULT.json"},
         {"kind": "variant", "name": "FIESTA SMALL", "reference": "FIESTA SMALL"}]
INSTALLED = {
    "lst_pit": {"listing_id": "lst_pit", "title": "Pit Stop", "kind": "map", "author": "Ana", "version": "2",
                "installed_at": "2026-10-12T10:00:00Z", "files": [{"path": "Maps/Forge/rf-3c1d0a9e44b2f7d81e02.json"}]},
    "lst_ufm": {"listing_id": "lst_ufm", "title": "UFM | ASSAULT", "kind": "gametype", "author": "Zed", "version": "1.2",
                "installed_at": "2026-10-13T10:00:00Z",
                "files": [{"path": "Game Modes/Forge/rf-d70540eb57f22df48390.json"}]},
}


def groups(entries, what, **kw):
    return {key: [(i.name, i.folder) for i in rows] for key, _, rows in arrange(items_for(entries, INSTALLED), what, **kw)}


def test_every_map_lands_in_the_group_people_look_in():
    g = groups(MAPS, "maps")
    assert g["stock"] == [("Guardian", ""), ("Valhalla", "")]
    assert g["mods"] == [("Hang 'Em High", "")]
    assert g["forge"] == [("Pit Stop", "")]
    assert g["folder"] == [("Club Dead", "")]
    assert g["builtin"] == [("Guardian", "")]
    assert g["saved"] == [("MLG Guardian", "")]


def test_forge_files_are_matched_by_their_own_file_and_keep_their_folder():
    items = {(i.name, i.folder): i for i in items_for(MODES, INSTALLED)}
    ours = items[("UFM | ASSAULT", "")]
    assert ours.group == "forge" and ours.detail == "v1.2 · Zed" and ours.installed_at
    theirs = items[("UFM | ASSAULT", "UFM Encompass")]  # the same title in another folder: an upload, not our install
    assert theirs.group == "forge" and not theirs.forge and theirs.detail == "UFM _ASSAULT.json"
    assert items[("Team Slayer", "")].group == "builtin" and items[("FIESTA SMALL", "")].group == "saved"


def test_filter_needs_every_word_and_matches_authors_and_folders():
    assert groups(MODES, "modes", text="ufm encompass") == {"forge": [("UFM | ASSAULT", "UFM Encompass")]}
    assert groups(MODES, "modes", text="zed") == {"forge": [("UFM | ASSAULT", "")]}
    assert groups(MAPS, "maps", text="nothing like it") == {}


def test_orders_turn_round_and_unknowns_stay_last():
    items = items_for(MODES, INSTALLED)
    assert [i.name for i in ordered(items, "name")][:2] == ["FIESTA SMALL", "Slayer"]
    assert [i.name for i in ordered(items, "name", flip=True)][0] == "UFM | ASSAULT"
    newest = ordered(items, "recent")
    assert newest[0].installed_at == "2026-10-13T10:00:00Z"
    oldest = ordered(items + items_for(MAPS, INSTALLED), "recent", flip=True)
    assert oldest[0].installed_at == "2026-10-12T10:00:00Z" and oldest[2].installed_at == ""  # unknowns still last


def test_the_catalog_branch_offers_only_what_isnt_here():
    listings = [{"id": "lst_pit", "title": "Pit Stop", "kind": "map", "upvote_count": 9},
                {"id": "a", "title": "Alpha", "kind": "map", "upvote_count": 1, "downvote_count": 4},
                {"id": "b", "title": "Bravo", "kind": "map", "upvote_count": 30, "download_count": 7},
                {"id": "c", "title": "Charlie", "kind": "gametype"},
                {"id": "d", "title": "Delta", "kind": "map", "status": "withdrawn"}]
    got = catalog_items(listings, "map", set(INSTALLED))
    assert [i.name for i in got] == ["Alpha", "Bravo"]
    assert [i.name for i in ordered(got, "rated")] == ["Bravo", "Alpha"]  # best first
    assert [i.name for i in ordered(got, "rated", flip=True)] == ["Alpha", "Bravo"]  # lowest first
    assert [i.name for i in ordered(got, "downloads")] == ["Bravo", "Alpha"]


def test_playlists_are_checked_in_the_servers_own_shape():
    good = {"name": "Team Mix", "games": [{"map": "Guardian", "game": "Team Slayer", "weight": 3},
                                          {"map": "Valhalla", "game": "Slayer", "weight": 1}]}
    out = check_playlist(good)
    assert out == {"version": 2, "name": "Team Mix", "vote_seconds": 20,
                   "games": [{"map": "Guardian", "game": "Team Slayer", "weight": 3},
                             {"map": "Valhalla", "game": "Slayer"}]}
    assert playlist_json(good).endswith("}\n")
    for bad, words in [({"name": "", "games": good["games"]}, "name"),
                       ({"name": "x", "games": []}, "at least one"),
                       ({"name": "x", "games": [{"map": "Guardian"}]}, "both a map and a game type"),
                       ({"name": "x", "games": [{"map": "a", "game": "b", "weight": 0}]}, "weight"),
                       ({"name": "x", "games": [{"map": "a", "game": "b"}] * 129}, "at most 128"),
                       ({"name": "x", "games": good["games"], "shuffle": True}, "Unknown playlist setting"),
                       ({"name": "x", "games": [{"map": "a", "game": "b", "script": 1}]}, "unknown settings")]:
        with pytest.raises(PlaylistError, match=words):
            check_playlist(bad)


# --- in the console -----------------------------------------------------------------------------------------------
async def until(pilot, cond, tries: int = 120) -> bool:
    for _ in range(tries):
        await pilot.pause(0.05)
        if cond():
            return True
    return False


def test_load_map_and_mode_from_the_browser():
    from oni_rcon.app import Confirm, OniApp
    from oni_rcon.config import Server
    from oni_rcon.content_ui import Browser
    from oni_rcon.demo import serve_fakes

    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = OniApp([Server(port=p, password="demo") for p in ports], by="pytest", intro=False)
        async with app.run_test(size=(160, 48)) as pilot:
            assert await until(pilot, lambda: all(st.online and "modes" in st.data for st in app.stations))
            await pilot.press("f3")
            app.query_one("#op-load").press()
            assert await until(pilot, lambda: isinstance(app.screen, Browser) and app.screen.query("#browse-tree")
                               and app.screen.query_one("#browse-tree").root.children)
            tree = app.screen.query_one("#browse-tree")
            names = [str(n.label) for n in tree.root.children]
            assert any("STOCK MAPS" in n for n in names) and any("MOD MAPS" in n for n in names)
            assert any("HALO 3 & MATCHMAKING" in n for n in names) and any("SAVED & WORKSHOP" in n for n in names)
            assert not any("RECLAIMERFORGE" in n and "GET MORE" in n for n in names)  # no Forge key: no catalog

            app.screen.query_one("#browse-by-name").press()  # the same column again: Z-A
            assert await until(pilot, lambda: app.screen.flip)
            assert app.screen.flip and "▼" in str(app.screen.query_one("#browse-by-name").label)  # Z-A: descending
            stock = next(n for n in tree.root.children if "STOCK MAPS" in str(n.label))
            assert str(stock.children[0].label).startswith("Valhalla")

            await pilot.press(*"mlg guardian", "enter")  # a variant: filtered, Enter takes it
            assert await until(pilot, lambda: isinstance(app.screen, Browser) and app.screen.what == "modes")
            assert app.browse_order == ("name", True)  # remembered for the next look
            await pilot.press(*"team slayer", "enter")
            assert await until(pilot, lambda: isinstance(app.screen, Confirm))
            assert "MLG Guardian / " in app.screen.t
            app.screen.query_one("#yes").press()
            assert await until(pilot, lambda: keep[0].status["map"] == "MLG Guardian")

            await pilot.press("f3")
            app.query_one("#op-nextmap").press()  # QUEUE NEXT: the rules may stay as they are
            assert await until(pilot, lambda: isinstance(app.screen, Browser))
            await pilot.press(*"valhalla", "enter")
            assert await until(pilot, lambda: isinstance(app.screen, Browser) and app.screen.what == "modes")
            assert "keep the current rules" in str(app.screen.query_one("#browse-tree").root.children[0].label)
            await pilot.press("escape")
    asyncio.run(go())


def test_intercepts_follow_the_selected_server_and_f2_again_shows_all():
    from oni_rcon.app import OniApp
    from oni_rcon.config import Server
    from oni_rcon.demo import serve_fakes

    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = OniApp([Server(port=p, password="demo") for p in ports], by="pytest", intro=False)
        async with app.run_test(size=(160, 48)) as pilot:
            assert await until(pilot, lambda: all(st.online for st in app.stations))
            other = app.stations[1]
            ev = {"event": "chat", "name": "Elsewhere", "text": "hello from two"}
            assert app.local_only and not app.shown(other, ev)
            await pilot.press("f2")
            await pilot.pause(1.1)  # the title is painted on the 1 s tick
            assert f"{app.cur.label.upper()} ONLY" in str(app.query_one("#feed").border_title)
            await pilot.press("f2")  # again: every server
            assert not app.local_only and app.shown(other, ev) and app.query_one("#f-all").value
            await pilot.pause(1.1)
            assert "ALL SERVERS" in str(app.query_one("#feed").border_title)
            await pilot.press("f2")
            assert app.local_only
    asyncio.run(go())
