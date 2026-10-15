"""IBIGS 0.9.0+ibigs.24: one boot animation (long or short, the user's pick), the F7 lobby table scrolls on
small windows, and the Forge catalog keeps its pick when the operator switches servers."""
import asyncio

from textual.app import App
from textual.containers import VerticalScroll

from oni_rcon.prefs import Prefs
from oni_rcon.tenant import PowerPanel


def test_intro_mode_is_one_animation_the_users_pick(tmp_path):
    p = Prefs.load(None)
    assert p.intro_mode() == "full"            # the first run shows the long one
    p.intro_seen = True
    assert p.intro_mode() == "quick"           # afterwards the short one, unless picked otherwise
    p.intro_choice = "full"
    assert p.intro_mode() == "full"
    p.intro_choice = "quick"
    assert p.intro_mode() == "quick"
    legacy = Prefs(None, intro_seen=True, full_intro=True)   # pre-ibigs.24 "full every time"
    assert legacy.intro_mode() == "full"


def test_intro_choice_survives_a_save(tmp_path):
    path = tmp_path / "prefs.json"
    p = Prefs.load(path)
    p.intro_seen, p.intro_choice = True, "full"
    p.save()
    again = Prefs.load(path)
    assert again.intro_choice == "full" and again.intro_mode() == "full"
    # junk on disk never breaks the pick
    path.write_text('{"intro_seen": true, "intro_choice": "sideways"}')
    assert Prefs.load(path).intro_choice == ""


class Host(App):
    def __init__(self, screen):
        super().__init__()
        self.s, self.result = screen, "unset"

    def on_mount(self):
        async def run():
            self.result = await self.push_screen_wait(self.s)
        self.run_worker(run())


def test_f7_lobby_table_scrolls():
    lobs = [{"service": f"h3-{i:02}", "name": f"Lobby {i}", "running": i == 1,
             "players": None, "phase": "starting" if i == 1 else ""} for i in range(1, 10)]

    async def go():
        panel = PowerPanel(lobs)
        app = Host(panel)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause(0.1)
            box = panel.query_one(".power-lobbies", VerticalScroll)
            # nine lobbies plus the starting hint outgrow the capped area: the box scrolls instead of clipping
            assert box.virtual_size.height > box.size.height > 0
            assert box.allow_vertical_scroll
            # every button still on screen in a 24-row window
            for bid in ("start", "restart", "stop", "remove"):
                assert panel.query_one(f"#{bid}").region.height > 0
    asyncio.run(go())
