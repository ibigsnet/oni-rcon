"""oni-rcon against a real Project Reclaimer 0.10 dedicated server. It changes that server (a role, a poll, a panel, the
joins switch, all put back), so it runs only when pointed at a throwaway one:

    ONI_RCON_LIVE=127.0.0.1:46901 ONI_RCON_LIVE_PASSWORD=... pytest tests/test_live_010.py

With a player on it (ONI_RCON_LIVE_PLAYER=1, any game client), it also mutes, shadow-mutes and unmutes them."""
import asyncio
import os

import pytest
from textual.widgets import OptionList

from oni_rcon import features_ui as F
from oni_rcon import rules as R
from oni_rcon.app import OniApp, Confirm, Pick, admin_rows
from oni_rcon.automation_ui import Panel
from oni_rcon.config import Server
from oni_rcon.jobs_ui import JobForm
from oni_rcon.rcon import Rcon

LIVE, PASSWORD = os.environ.get("ONI_RCON_LIVE"), os.environ.get("ONI_RCON_LIVE_PASSWORD")
pytestmark = pytest.mark.skipif(not (LIVE and PASSWORD), reason="needs a throwaway 0.10 server: see the docstring")
TEST_ID = "ab" * 32


async def connected():
    events = []
    rc = Rcon(f"ws://{LIVE}", PASSWORD, "oni-live-test", lambda _, e: events.append(e), lambda *_: None)
    task = asyncio.ensure_future(rc.run())
    for _ in range(100):
        if rc.state == "online":
            return rc, task, events
        await asyncio.sleep(.05)
    raise AssertionError(f"not connected: {rc.state} {rc.detail}")


def test_replies_and_events_are_the_shapes_oni_reads():
    async def go():
        rc, task, events = await connected()
        assert R.at_least_010(rc.info.get("version"))
        status = (await rc.call("status"))["data"]
        assert status["phase"] in ("no_game", "playing", "finished") and status["server_phase"]
        assert isinstance((await rc.call("players"))["data"]["players"], list)
        assert isinstance(admin_rows((await rc.call("admins"))["data"], []), list)
        health = (await rc.call("health"))["data"]
        assert "frame_rate" in health and R.at_least_010(health["version"])

        assert (await rc.call("role", "add", TEST_ID, "vip"))["ok"]
        data = (await rc.call("role", "list"))["data"]
        assert R.staff_of(data)[TEST_ID] == {"role": "vip", "rank": 10}
        assert next(r for r in F.role_rows(data) if r["id"] == TEST_ID)["fixed"] is False
        assert (await rc.call("role", "remove", TEST_ID))["ok"]
        assert TEST_ID not in R.staff_of((await rc.call("role", "list"))["data"])

        args = F.panel_args("onitest", "Oni test", ["a line"], "Skip=next", "side", "10s")
        assert (await rc.call("panel", "show", *args))["ok"]
        assert F.panel_rows((await rc.call("panel", "list"))["text"])[0]["id"] == "onitest"
        assert (await rc.call("panel", "hide", "onitest"))["ok"]

        assert (await rc.call("poll", *F.poll_args("Oni test?", ["A", "B"], 10)))["ok"]
        await asyncio.sleep(.5)
        await rc.call("poll", "cancel")

        start = (await rc.call("joins"))["data"]
        assert (await rc.call("joins", "closed"))["data"]["joins"] == "closed"
        assert (await rc.call("joins", "gate", "in_game"))["data"]["join_gates"] == ["in_game"]
        await rc.call("joins", "gate", "none")
        await rc.call("joins", start["joins"])

        await asyncio.sleep(.5)
        kinds = {e.get("event") for e in events}
        assert {"role_added", "role_removed", "poll"} <= kinds
        engine = R.Engine([R.validate({"name": "t", "when": {"type": "moderation", "action": "any"},
                                       "then": [{"type": "say", "to": "this", "message": "{action} {by}"}]})])
        fired = [f for e in events for f in engine.event("lab", e)]
        assert len(fired) == 2 and "oni-live-test" in fired[0].ctx["by"]
        task.cancel()
    asyncio.run(go())


@pytest.mark.skipif(not os.environ.get("ONI_RCON_LIVE_PLAYER"), reason="needs a player on the server")
def test_mutes_on_a_real_player():
    async def go():
        rc, task, events = await connected()
        player = (await rc.call("players"))["data"]["players"][0]
        assert player["id"] and player["admin"] is False
        assert (await rc.call("mute", player["id"], "text", "5m", "oni test", "shadow"))["ok"]
        rows = F.mute_rows((await rc.call("mutes"))["text"])
        assert rows[0]["id"] == player["id"] and rows[0]["left"] and rows[0]["reason"] == "oni test"
        assert (await rc.call("players"))["data"]["players"][0]["muted"] is True
        assert (await rc.call("unmute", player["id"]))["ok"]
        await asyncio.sleep(.5)
        mute = next(e for e in events if e.get("event") == "mute")
        assert mute["shadow"] is True and mute["kind"] == "text"
        task.cancel()
    asyncio.run(go())


def test_the_screens_drive_a_real_server():
    async def until(pilot, ok, what=""):
        for _ in range(200):
            await pilot.pause(.03)
            if ok():
                return
        assert ok(), what

    async def go():
        host, _, port = LIVE.rpartition(":")
        app = OniApp([Server(host=host, port=int(port), password=PASSWORD)], by="oni-live-test", intro=False)
        async with app.run_test(size=(170, 50)) as pilot:
            await until(pilot, lambda: app.cur.online, "connected")
            assert R.at_least_010(app.cur.rcon.info["version"])
            await pilot.press("ctrl+t")
            await until(pilot, lambda: isinstance(app.screen, Panel) and app.screen.t.startswith("SERVER FEATURES"))
            await pilot.click("#p-open")  # the top row: ROLES
            await until(pilot, lambda: isinstance(app.screen, Panel) and app.screen.t == "ROLES")
            await pilot.click("#p-add")
            await until(pilot, lambda: isinstance(app.screen, Pick))
            app.screen.dismiss(("", ""))  # paste an ID
            await until(pilot, lambda: isinstance(app.screen, JobForm))
            app.screen.dismiss({"pid": TEST_ID})
            await until(pilot, lambda: isinstance(app.screen, Pick) and "WHICH ROLE" in app.screen.t)
            app.screen.dismiss("mod")
            await until(pilot, lambda: isinstance(app.screen, Panel) and app.screen.t == "ROLES"
                        and any(TEST_ID in v for _, v in app.screen.opts), "listed by the real server")
            app.screen.query_one(OptionList).highlighted = next(
                i for i, (_, v) in enumerate(app.screen.opts) if TEST_ID in v)
            await pilot.click("#p-remove")
            await until(pilot, lambda: isinstance(app.screen, Confirm))
            await pilot.click("#yes")
            await until(pilot, lambda: isinstance(app.screen, Panel) and app.screen.t == "ROLES"
                        and not any(TEST_ID in v for _, v in app.screen.opts), "gone from the real server")
    asyncio.run(go())
