"""IBIGS 2026-10-30: F3's SHARE / JOIN address. A customer pasted the sitrep's old UPLINK line (the console's own
RCON connection on this machine) as his server's address; the address players join was hidden under the redaction
blocks. The sitrep now leads with the join address (shown from the start on a customer console, c copies it) and
labels the RCON line as no use to players."""
import asyncio
import io

from rich.console import Console

from oni_rcon.app import GUIDE, OniApp, Pick, share_address, split_host_port
from oni_rcon.config import Server
from oni_rcon.demo import serve_fakes
from oni_rcon.tenant import TenantApp, checklist_text

JOIN = "203.0.113.7:49176"  # what the demo servers report: public_address:game_port


def plain(widget) -> str:
    console = Console(width=120, file=io.StringIO(), record=True, color_system=None)
    console.print(widget.content)
    return console.export_text()


def line(text: str, label: str) -> str:
    return next((ln for ln in text.splitlines() if ln.strip().startswith(label)), "")


async def until(pilot, cond, tries: int = 300) -> bool:
    for _ in range(tries):
        await pilot.pause(0.05)
        if cond():
            return True
    return False


# --- deriving the address -----------------------------------------------------------------------------------------
def test_share_address_is_the_reported_public_address_and_game_port():
    assert share_address(JOIN) == (JOIN, "")
    assert share_address("play.example.com:20023") == ("play.example.com:20023", "")
    assert share_address("[2001:db8::7]:20023") == ("[2001:db8::7]:20023", "")


def test_a_local_address_gives_only_the_port():
    for local in ("0.0.0.0:20023", "127.0.0.1:20023", "10.0.0.5:20023", "192.168.1.4:20023", "172.20.0.2:20023",
                  "100.64.0.1:20023", ":20023", "localhost:20023", "[::]:20023", "[fe80::1]:20023"):
        addr, why = share_address(local)
        assert addr == "" and "port 20023" in why, local
    assert share_address(None) == ("", "the server hasn't said its address yet")
    assert share_address("203.0.113.7")[0] == ""  # no port: nothing a player could join


def test_public_address_setting_fills_in_a_local_report_and_keeps_the_game_port():
    assert share_address("0.0.0.0:20023", "203.0.113.9") == ("203.0.113.9:20023", "")
    assert share_address(":20023", "203.0.113.9:1") == ("203.0.113.9:20023", "")  # the game port, not the setting's
    assert share_address("10.0.0.5:20023", "10.0.0.6")[0] == ""                    # a local setting doesn't help
    assert share_address(JOIN, "203.0.113.9") == (JOIN, "")                        # the server's own word wins


def test_split_host_port():
    assert split_host_port("203.0.113.7:5") == ("203.0.113.7", 5)
    assert split_host_port("[2001:db8::1]:5") == ("2001:db8::1", 5)
    assert split_host_port("203.0.113.7:99999") == ("203.0.113.7", None)
    assert split_host_port("") == ("", None)


# --- the sitrep ---------------------------------------------------------------------------------------------------
def run(app_class, body, size=(170, 50)):
    async def go():
        ports, keep = await serve_fakes(tick=False, specs=[("Probe", 16, 2)])
        app = app_class([Server(port=p, password="demo") for p in ports], by="pytest", intro=False)
        async with app.run_test(size=size) as pilot:
            assert await until(pilot, lambda: app.cur.online and "status" in app.cur.data)
            await pilot.press("f3")
            await pilot.pause()
            await body(app, pilot)
    asyncio.run(go())


def test_generic_console_hides_the_share_address_until_x(monkeypatch):
    monkeypatch.delenv("IBIGS_ONI_POWER", raising=False)

    async def body(app, pilot):
        text = plain(app.query_one("#sitrep"))
        share, link = line(text, "SHARE / JOIN"), line(text, "CONSOLE LINK")
        assert "███" in share and JOIN not in text and "hidden" in share
        assert app.cur.server.where in link and "RCON · not for players" in link and "v0.9.7-demo" in link
        assert "UPLINK" not in text and "PUBLIC ADDRESS" not in text
        await pilot.press("c")  # hidden: nothing copied, and the toast says why
        await pilot.pause()
        assert app.clipboard == ""
        assert any("hidden" in n.message for n in app._notifications)
        await pilot.press("x")
        await pilot.pause()
        share = line(plain(app.query_one("#sitrep")), "SHARE / JOIN")
        assert JOIN in share and "c copies" in share
        await pilot.press("c")
        await pilot.pause()
        assert app.clipboard == JOIN
    run(OniApp, body)


def test_customer_console_shows_the_share_address_from_the_start(monkeypatch):
    monkeypatch.delenv("IBIGS_ONI_POWER", raising=False)

    async def body(app, pilot):
        assert app.redact  # player IPs stay hidden: only the address to share shows
        text = plain(app.query_one("#sitrep"))
        assert JOIN in line(text, "SHARE / JOIN") and "RCON · not for players" in line(text, "CONSOLE LINK")
        await pilot.press("c")
        await pilot.pause()
        assert app.clipboard == JOIN
        # x twice: visible, then hidden for a stream, which hides the share line too
        await pilot.press("x")
        await pilot.press("x")
        await pilot.pause()
        assert app.redact and app.share_hidden
        text = plain(app.query_one("#sitrep"))
        assert JOIN not in text and "███" in line(text, "SHARE / JOIN")
        app.copy_to_clipboard("")
        await pilot.press("c")
        await pilot.pause()
        assert app.clipboard == ""
        assert not dict(app.copy_extras())["Share / join address"]
    run(TenantApp, body)


def test_customer_copy_menu_offers_the_share_address_unblanked(monkeypatch):
    monkeypatch.delenv("IBIGS_ONI_POWER", raising=False)

    async def body(app, pilot):
        assert ("Share / join address", JOIN) in app.copy_extras()
        await pilot.press("ctrl+y")
        assert await until(pilot, lambda: isinstance(app.screen, Pick) and app.screen.t == "COPY")
        shown = [o[0].plain for o in app.screen.opts]
        assert any(o.startswith("Share / join address") and JOIN in o for o in shown), shown
        await pilot.press("escape")
    run(TenantApp, body)


def test_a_local_report_shows_the_port_to_use(monkeypatch):
    monkeypatch.delenv("IBIGS_ONI_POWER", raising=False)

    async def body(app, pilot):
        app.cur.data["status"]["address"] = "0.0.0.0:20023"
        app.paint_ops()
        share = line(plain(app.query_one("#sitrep")), "SHARE / JOIN")
        assert "your public IP, port 20023" in share
        await pilot.press("c")
        await pilot.pause()
        assert app.clipboard == ""
        monkeypatch.setenv("RECLAIMER_DEDICATED_PUBLIC_ADDRESS", "203.0.113.9")
        app.paint_ops()
        assert "203.0.113.9:20023" in line(plain(app.query_one("#sitrep")), "SHARE / JOIN")
    run(TenantApp, body)


# --- words --------------------------------------------------------------------------------------------------------
def test_help_and_start_guide_mention_the_share_address():
    words = " ".join(v for _, rows in GUIDE for _, v in rows)
    assert "SHARE / JOIN" in words and "CONSOLE LINK" in words and "c copies it" in words
    text, _ = checklist_text([{"ok": True, "text": "Halo 3 is installed", "next": ""}], [("h3-01", JOIN)])
    assert "SHARE / JOIN ADDRESS" in text and f"h3-01:  {JOIN}" in text
    text, _ = checklist_text([{"ok": True, "text": "Halo 3 is installed", "next": ""}])
    assert "on F3 once a lobby is running" in text
