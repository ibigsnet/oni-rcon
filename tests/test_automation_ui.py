"""F8 AUTOMATION as a customer clicks through it: rules from a template, TEST, roles, votes, Discord (ibigs.15)."""
import asyncio

import pytest
from textual.widgets import Button, Input, OptionList, Select

from oni_rcon import rules as R
from oni_rcon.app import Confirm, Pick
from oni_rcon.automation_ui import Panel
from oni_rcon.config import Server
from oni_rcon.jobs_ui import JobForm
from oni_rcon.tenant import TenantApp

HOOK = "https://discord.com/api/webhooks/123456789012345678/" + "A" * 68
PID = "d" * 64


async def until(pilot, predicate, what=""):
    for _ in range(120):
        await pilot.pause(.03)
        if predicate():
            return
    assert predicate(), what


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = R.RuleStore(tmp_path / "auto")
    monkeypatch.setattr(R, "RuleStore", lambda root=None: R.RuleStore.__wrapped__(tmp_path / "auto")
                        if hasattr(R.RuleStore, "__wrapped__") else store)
    tool = tmp_path / "oni"
    tool.write_text("#!/bin/sh\ncase \"$1\" in\n"
                    "  status) printf '%s\\n' '[{\"service\":\"h3-01\"},{\"service\":\"h3-02\"}]';;\n"
                    "  roles) printf '%s\\n' '[{\"lobby\":\"h3-01\",\"state\":\"pending\",\"text\":\"waiting to apply admins: 1 (3 playing)\"}]';;\n"
                    "esac\n")
    tool.chmod(0o755)
    monkeypatch.setenv("IBIGS_ONI_POWER", str(tool))
    return store


def app_():
    return TenantApp([Server(name="h3-01", port=1, password="demo"), Server(name="h3-02", port=2, password="demo")],
                     by="test", intro=False)


async def open_section(pilot, app, index):
    await pilot.press("f8")
    await until(pilot, lambda: isinstance(app.screen, Panel), "the hub")
    app.screen.query_one(OptionList).highlighted = index
    await pilot.click("#p-open")


async def choose(pilot, app, value_pred):
    """In a Pick, choose the option whose value matches."""
    await until(pilot, lambda: isinstance(app.screen, Pick), "a pick list")
    i = next(i for i, (_, v) in enumerate(app.screen.shown) if value_pred(v))
    app.screen.dismiss(app.screen.shown[i][1])


async def ok(pilot, app, kind=JobForm):
    await until(pilot, lambda: isinstance(app.screen, kind), kind.__name__)
    await pilot.click("#yes")


def test_a_rule_from_a_template_saved_tested_and_switched_off(env):
    async def go():
        app = app_()
        async with app.run_test(size=(160, 48)) as pilot:
            await open_section(pilot, app, 0)
            await until(pilot, lambda: isinstance(app.screen, Panel) and app.screen.t == "RULES")
            assert "WHEN something happens" in str(app.screen.body)
            await pilot.click("#p-template")
            await choose(pilot, app, lambda v: R.STARTERS[v]["name"] == "Spree announcer")
            await ok(pilot, app)  # name + servers
            await choose(pilot, app, lambda v: v == "_keep")  # keep the template's WHEN
            await choose(pilot, app, lambda v: v == "_done")  # no conditions
            await choose(pilot, app, lambda v: v == "_done")  # keep its THEN
            await ok(pilot, app)  # limits
            await until(pilot, lambda: isinstance(app.screen, Confirm))
            assert "WHEN  A player reaches a killing spree" in app.screen.b and "{player} is on a {medal}" in app.screen.b
            await pilot.click("#yes")
            await until(pilot, lambda: isinstance(app.screen, Panel) and app.screen.t == "RULES")
            saved = env.rules()
            assert [r["name"] for r in saved] == ["Spree announcer"] and saved[0]["enabled"]
            await pilot.click("#p-test")
            await until(pilot, lambda: isinstance(app.screen, Confirm))
            assert "Say on h3-01: ExamplePlayer is on a Killing Spree! (5 kills)" in app.screen.b
            assert "Nothing was sent." in app.screen.b
            await pilot.click("#yes")
            await until(pilot, lambda: isinstance(app.screen, Panel) and app.screen.t == "RULES")
            await pilot.click("#p-toggle")
            await until(pilot, lambda: not env.rules()[0]["enabled"])
    asyncio.run(go())


def test_a_rule_built_from_scratch_with_a_condition(env):
    async def go():
        app = app_()
        async with app.run_test(size=(120, 40)) as pilot:
            await open_section(pilot, app, 0)
            await until(pilot, lambda: isinstance(app.screen, Panel) and app.screen.t == "RULES")
            await pilot.click("#p-new")
            await until(pilot, lambda: isinstance(app.screen, JobForm))
            app.screen.query_one("#field-name", Input).value = "Admin pager"
            await pilot.click("#yes")
            await choose(pilot, app, lambda v: v == "command")
            await ok(pilot, app)  # !admin is the default
            await choose(pilot, app, lambda v: v == "admin_online")
            await ok(pilot, app)
            await choose(pilot, app, lambda v: v == "_done")
            await choose(pilot, app, lambda v: v == "tell_admins")
            await until(pilot, lambda: isinstance(app.screen, JobForm))
            app.screen.query_one("#field-message", Input).value = "{player} on {server} needs you: {args}"
            await pilot.click("#yes")
            await choose(pilot, app, lambda v: v == "_done")
            await ok(pilot, app)
            await until(pilot, lambda: isinstance(app.screen, Confirm))
            assert "IF    An admin is online" in app.screen.b
            await pilot.click("#yes")
            await until(pilot, lambda: bool(env.rules()))
            r = env.rules()[0]
            assert r["when"] == {"type": "command", "command": "!admin"} and r["cooldown"] == 30
            assert r["then"][0]["message"] == "{player} on {server} needs you: {args}"
    asyncio.run(go())


def test_roles_by_pasted_id_and_votes(env):
    async def go():
        app = app_()
        async with app.run_test(size=(120, 40)) as pilot:
            await open_section(pilot, app, 2)
            await until(pilot, lambda: isinstance(app.screen, Panel) and app.screen.t == "ROLES")
            assert "waiting to apply admins" in str(app.screen.foot)  # what's still pending, in words
            await pilot.click("#p-add")
            await choose(pilot, app, lambda v: v == ("", ""))
            await until(pilot, lambda: isinstance(app.screen, JobForm))
            app.screen.query_one("#field-pid", Input).value = PID.upper()
            app.screen.query_one("#field-name", Input).value = "Delta"
            await pilot.click("#yes")
            await until(pilot, lambda: isinstance(app.screen, JobForm) and app.screen.query("#field-roles"))
            app.screen.query_one("#field-roles", Select).value = "admin,vip"
            await pilot.click("#yes")
            await until(pilot, lambda: isinstance(app.screen, Confirm))
            assert "can't be vote-kicked" in app.screen.b and "next time it's empty" in app.screen.b
            await pilot.click("#yes")
            await until(pilot, lambda: bool(env.roles()[0]))
            assert env.roles()[0][PID]["roles"] == ["admin", "vip"]
            await pilot.press("escape")
            await until(pilot, lambda: isinstance(app.screen, Panel) and app.screen.t == "AUTOMATION")
            app.screen.query_one(OptionList).highlighted = 3
            await pilot.click("#p-open")
            await until(pilot, lambda: isinstance(app.screen, Panel) and app.screen.t == "PLAYER VOTES")
            await pilot.click("#p-off")
            await until(pilot, lambda: env.roles()[1] == {"h3-01": False})
    asyncio.run(go())


def test_discord_walks_through_the_steps_and_tests_the_hook(env, monkeypatch):
    sent = []
    monkeypatch.setattr("oni_rcon.rules_run.post_discord", lambda url, text, timeout=10: sent.append((url, text)) or 204)

    async def go():
        app = app_()
        async with app.run_test(size=(120, 44)) as pilot:
            await open_section(pilot, app, 4)
            await until(pilot, lambda: isinstance(app.screen, Panel) and app.screen.t == "DISCORD")
            await pilot.click("#p-add")
            await until(pilot, lambda: isinstance(app.screen, JobForm))
            assert "Integrations → Webhooks → New Webhook" in app.screen.note and "Copy Webhook URL" in app.screen.note
            app.screen.query_one("#field-url", Input).value = "https://example.com/not-discord"
            await pilot.click("#yes")
            await until(pilot, lambda: isinstance(app.screen, Panel) and app.screen.t == "AUTOMATION")
            assert env.webhooks() == {}  # refused, with a message
            app.screen.query_one(OptionList).highlighted = 4
            await pilot.click("#p-open")
            await until(pilot, lambda: isinstance(app.screen, Panel) and app.screen.t == "DISCORD")
            await pilot.click("#p-add")
            await until(pilot, lambda: isinstance(app.screen, JobForm))
            app.screen.query_one("#field-url", Input).value = HOOK
            await pilot.click("#yes")
            await until(pilot, lambda: isinstance(app.screen, Confirm))
            await pilot.click("#yes")
            await until(pilot, lambda: bool(sent))
            assert env.webhooks() == {"alerts": HOOK} and sent[0][0] == HOOK
            await until(pilot, lambda: isinstance(app.screen, Panel) and app.screen.t == "DISCORD")
            listed = str(app.screen.query_one(OptionList).get_option_at_index(0).prompt)
            assert "alerts" in listed and "AAAA" not in listed  # the secret part never shows
    asyncio.run(go())
