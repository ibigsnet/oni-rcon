"""Ctrl+Y copies anything the console shows, on every page and in dialogs (IBIGS 0.9.0+ibigs.11)."""
import asyncio

from oni_rcon.app import OniApp, Pick
from oni_rcon.config import Server
from oni_rcon.content_ui import Browser
from oni_rcon.demo import serve_fakes


async def until(pilot, cond, n=120):
    for _ in range(n):
        await pilot.pause(0.05)
        if cond():
            return True
    return False


def options(app) -> dict[str, str]:
    """The copy menu's choices, as what -> the text it copies."""
    return {str(label).split("   ", 1)[0]: value for label, value in app.screen.opts}


async def copy(app, pilot, what: str) -> str:
    await pilot.press("ctrl+y")
    assert await until(pilot, lambda: isinstance(app.screen, Pick) and app.screen.t == "COPY")
    menu = options(app)
    assert what in menu, list(menu)
    app.screen.dismiss(menu[what])
    assert await until(pilot, lambda: not isinstance(app.screen, Pick))
    return app.clipboard


def test_copy_a_player_id_a_ban_and_a_map_reference():
    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = OniApp([Server(port=p, password="demo") for p in ports], by="pytest", intro=False)
        async with app.run_test(size=(160, 48)) as pilot:
            assert await until(pilot, lambda: all(st.online and st.players and "bans" in st.data
                                                  for st in app.stations))
            await pilot.press("f1")
            t = app.query_one("#players")
            t.focus()
            t.show_cursor = True
            t.move_cursor(row=0)
            await pilot.pause(0.1)
            p = app.cur_player()
            assert await copy(app, pilot, "Player ID") == str(p["player_id"])
            assert await copy(app, pilot, "Name") == p["name"]
            assert "CALLSIGN" in await copy(app, pilot, "The whole table (" + str(t.row_count) + " rows)")

            app.redact = False
            assert "203.0.113.7" in await copy(app, pilot, "Public address")
            app.redact = True  # x: redacted, an address never reaches the clipboard
            assert "203.0.113" not in await copy(app, pilot, "Public address")

            await pilot.press("f4")
            bans = app.query_one("#bans")
            if bans.row_count:
                bans.focus()
                bans.move_cursor(row=0)
                await pilot.pause(0.1)
                await pilot.press("ctrl+y")
                assert await until(pilot, lambda: isinstance(app.screen, Pick) and app.screen.t == "COPY")
                assert "TARGET" in options(app) and "This row" in options(app)
                await pilot.press("escape")

            await pilot.press("f3")
            app.query_one("#op-map").press()  # copy works in dialogs too: the browser's highlighted entry
            assert await until(pilot, lambda: isinstance(app.screen, Browser) and app.screen.query("#browse-tree")
                               and app.screen.query_one("#browse-tree").root.children)
            await pilot.press(*"mlg guardian")
            await pilot.pause(0.1)
            app.screen.action_focus_tree()
            tree = app.screen.query_one("#browse-tree")
            leaf = next(n for n in app.screen.walk() if getattr(n.data, "reference", "") == "MLG Guardian")
            tree.move_cursor(leaf)
            await pilot.pause(0.1)
            assert await copy(app, pilot, "Name") == "MLG Guardian"
            assert isinstance(app.screen, Browser)  # the browser stays open
            await pilot.press("escape")
    asyncio.run(go())


def test_a_team_move_reports_what_the_game_did_not_what_the_reply_said():
    """'Moving X to Red team.' only means the server was asked. The game may refuse; the console says so."""
    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = OniApp([Server(port=p, password="demo") for p in ports], by="pytest", intro=False)
        async with app.run_test(size=(160, 48)) as pilot:
            assert await until(pilot, lambda: all(st.online and st.players for st in app.stations))
            st, fake = app.stations[0], keep[0]
            p = st.players[0]
            here = p["team"]
            other = next(t for t in ("red", "blue") if t != here)
            fake.even_teams = True
            for x in fake.players:  # make the other team the bigger one: a move there must be refused
                x["team"] = other if x is not fake.players[0] else here
            await app._fetch(st, ("players",))
            app.notify = lambda msg, **kw: said.append((kw.get("title", ""), str(msg)))
            said = []
            await app.move_team(st, st.players[0] if st.players[0]["name"] == p["name"] else
                                next(x for x in st.players if x["name"] == p["name"]), other)
            assert any("NOT MOVED" in t and "Teams must stay even" in m for t, m in said), said
            fake.even_teams = False
            said.clear()
            await app.move_team(st, next(x for x in st.players if x["name"] == p["name"]), other)
            assert any(f"is on {other.upper()} now" in m for _, m in said), said
    asyncio.run(go())
