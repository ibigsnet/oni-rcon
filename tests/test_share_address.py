"""IBIGS 2026-10-30: F3's SHARE / JOIN address. A customer pasted the sitrep's old UPLINK line (the console's own
RCON connection on this machine) as his server's address; the address players join was hidden under the redaction
blocks. The sitrep now leads with the join address (shown from the start on a customer console, c copies it) and
labels the RCON line as no use to players."""
import asyncio
import io

from rich.console import Console

import pytest

from oni_rcon import app as app_mod
from oni_rcon.app import GUIDE, OniApp, Pick, console_where, share_address, split_host_port
from oni_rcon.config import Server
from oni_rcon.demo import serve_fakes
from oni_rcon.tenant import TenantApp, checklist_text

JOIN = "203.0.113.7:49176"  # what the demo servers report: public_address:game_port


@pytest.fixture(autouse=True)
def no_own_name(monkeypatch):
    """The test machine's own DNS name must not stand in for a server's address."""
    monkeypatch.setattr(app_mod, "_OWN_NAME", [""])


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
        assert "this machine, port" in link and "127.0.0.1" not in link and "RCON · not for players" in link and "v0.9.7-demo" in link
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
        assert "public IP, port 20023" in share and "0.0.0.0" not in share
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


# --- IBIGS 2026-11-27: a customer joined 127.0.0.1:20061 -----------------------------------------------------------
def test_a_local_report_never_shows_the_local_address():
    for local in ("127.0.0.1:20061", "0.0.0.0:20061", "10.60.0.3:20061", "192.168.1.4:20061"):
        addr, why = share_address(local)
        host = local.rsplit(":", 1)[0]
        assert addr == "" and host not in why and "port 20061" in why, (local, why)


def test_console_link_names_this_machine_not_loopback():
    assert console_where(Server(port=20061, password="x")) == "this machine, port 20061"
    assert console_where(Server(host="localhost", port=20061, password="x")) == "this machine, port 20061"
    assert console_where(Server(host="203.0.113.7", port=20061, password="x")) == "203.0.113.7:20061"
    via = Server(port=20061, password="x", ssh="root@box")
    assert console_where(via) == via.where  # through ssh, 127.0.0.1 is the far machine's: say exactly what it is


def test_the_machines_public_name_fills_in_a_local_report(monkeypatch):
    monkeypatch.setattr(app_mod, "_OWN_NAME", ["halo-test.example.com"])
    monkeypatch.delenv("IBIGS_ONI_POWER", raising=False)
    monkeypatch.delenv("RECLAIMER_DEDICATED_PUBLIC_ADDRESS", raising=False)

    async def body(app, pilot):
        app.cur.data["status"]["address"] = "127.0.0.1:20061"
        app.paint_ops()
        share = line(plain(app.query_one("#sitrep")), "SHARE / JOIN")
        assert "halo-test.example.com:20061" in share and "127.0.0.1" not in share
        await pilot.press("c")
        await pilot.pause()
        assert app.clipboard == "halo-test.example.com:20061"
    run(TenantApp, body)


def test_own_public_name_rejects_local_and_unresolvable_names(monkeypatch):
    monkeypatch.setattr(app_mod, "NAT_ENV", "/nonexistent/nat.env")
    for fqdn, ip in (("localhost", "127.0.0.1"), ("box.local", "192.168.1.2"), ("halo.example.com", "10.0.0.5")):
        monkeypatch.setattr(app_mod, "_OWN_NAME", [])
        monkeypatch.setattr(app_mod.socket, "getfqdn", lambda f=fqdn: f)
        monkeypatch.setattr(app_mod.socket, "gethostbyname", lambda n, i=ip: i)
        assert app_mod.own_public_name() == "", fqdn
    monkeypatch.setattr(app_mod, "_OWN_NAME", [])
    monkeypatch.setattr(app_mod.socket, "getfqdn", lambda: "halo-x.example.com.")
    monkeypatch.setattr(app_mod.socket, "gethostbyname", lambda n: "203.0.113.20")
    assert app_mod.own_public_name() == "halo-x.example.com"


def test_an_ibigs_servers_saved_nat_address_comes_first(tmp_path, monkeypatch):
    env = tmp_path / "nat.env"
    env.write_text("PUBLIC=203.0.113.44\nGWMAC=aa:bb\nAT=1\n")
    assert app_mod.ibigs_nat_public(str(env)) == "203.0.113.44"
    monkeypatch.setattr(app_mod, "NAT_ENV", str(env))
    monkeypatch.setattr(app_mod, "_OWN_NAME", [])
    monkeypatch.setattr(app_mod.socket, "getfqdn", lambda: "halo-x.example.com")
    monkeypatch.setattr(app_mod.socket, "gethostbyname", lambda n: "203.0.113.20")
    assert app_mod.own_public_name() == "203.0.113.44"
    assert share_address("[::]:20061", app_mod.own_public_name()) == ("203.0.113.44:20061", "")
    env.write_text("PUBLIC=10.60.0.5\n")  # a local address saved there never counts
    assert app_mod.ibigs_nat_public(str(env)) == ""
    assert app_mod.ibigs_nat_public(str(tmp_path / "missing")) == ""
