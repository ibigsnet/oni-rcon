"""Project Reclaimer 0.10 as it really answers. The shapes here were read off a 0.10.0 dedicated server: `role list`,
status, mutes, panels, polls and their events. Run against one by hand, see docs/RECLAIMER-010.md."""
import asyncio
import json

import pytest
from textual.widgets import OptionList

from oni_rcon import features_ui as F
from oni_rcon import rules as R
from oni_rcon import rules_run
from oni_rcon.app import Confirm, OniApp, Pick, admin_rows, describe, note_kick, vote_text
from oni_rcon.automation_ui import Panel
from oni_rcon.config import Server
from oni_rcon.demo import serve_fakes
from oni_rcon.jobs_ui import JobForm
from oni_rcon.tenant import Notice, TenantApp

A, B = "a" * 64, "b" * 64
ROLE_LIST = {"devs_as_vip": False, "groups": {"everyone": ["vote.call.kick"], "members": []}, "roles": [
    {"name": "host", "title": "HOST", "rank": 40, "can": ["kick"], "ban_minutes": 10080, "holders": []},
    {"name": "admin", "title": "ADMIN", "rank": 30, "can": ["kick"], "ban_minutes": 10080, "holders": [f"{A} (dedicated.toml)", f"{B} (admins)"]},
    {"name": "mod", "title": "MOD", "rank": 20, "can": ["kick", "ban.temp"], "ban_minutes": 60,
     "holders": [f"{'c' * 64} Alpha Two (added by oni-lab (RCON))", f"{'e' * 64} (moderators)"]},
    {"name": "vip", "title": "VIP", "rank": 10, "can": ["vote.exempt"], "ban_minutes": 0, "holders": [f"{'d' * 64} (added by oni-lab (RCON))"]}]}


def engine(*rules, **kw):
    e = R.Engine([R.validate(r) for r in rules], now=lambda: kw.get("now", 1000.0))
    e.connection("h3-01", True)
    return e


def rule(when, then=None, **kw):
    return {"name": "t", "when": when, "then": then or [{"type": "say", "to": "this", "message": "{player} {by} {reason}"}],
            "cooldown": 0, **kw}


# --- what broke on 0.10 --------------------------------------------------------------------------------------
def test_admins_reply_is_configured_and_added():
    rows = admin_rows({"added": [{"id": A, "name": "Alice", "added_by": "x"}], "configured": [B]}, [{"id": A}])
    assert {r["id"]: r["fixed"] for r in rows} == {A: False, B: True}
    assert next(r for r in rows if r["id"] == A)["on"] is True


def test_a_join_names_nobody_until_the_roster_does():
    e = engine(rule({"type": "join"}, [{"type": "tell_player", "message": "Welcome {player}"}]))
    assert e.event("h3-01", {"event": "join", "number": 1, "name": "", "id": A, "ip": "x", "admin": False}) == []
    fired = e.players("h3-01", [{"number": 1, "name": "Alice", "id": A, "engine_id": None}])
    assert [R.fill(f.rule["then"][0]["message"], f.ctx) for f in fired] == ["Welcome Alice"]
    assert e.players("h3-01", [{"number": 1, "name": "Alice", "id": A, "engine_id": None}]) == []  # once


def test_a_player_not_in_a_game_yet_is_still_on_the_roster():
    e = engine()
    e.players("h3-01", [{"number": 4, "name": "Alice", "id": A, "engine_id": None}])
    assert e.find(e.lobby("h3-01"), A)[1]["name"] == "Alice"


def test_a_vote_event_says_state_and_subject_now():
    e = engine(rule({"type": "vote", "kind": "kick"}, [{"type": "cancel_vote"}]))
    e.players("h3-01", [{"number": 1, "name": "Bravo", "id": B}])
    fired = e.event("h3-01", {"event": "vote", "state": "called", "subject": "kick Bravo", "caller": "Alpha"})
    assert len(fired) == 1 and fired[0].ctx["target"] == "Bravo" and fired[0].ctx["player"] == "Alpha"
    assert e.event("h3-01", {"event": "vote", "state": "passed", "subject": "kick Bravo", "caller": "Alpha"}) == []
    assert e.event("h3-01", {"event": "vote", "state": "called", "subject": "end the game", "caller": "Alpha"}) == []


def test_status_says_a_vote_in_words_and_it_fires_once():
    e = engine(rule({"type": "vote", "kind": "endgame"}, [{"type": "cancel_vote"}]))
    line = "vote 3 to end the game called by Alpha: 2 yes, 1 no, 3 needed of 8 voters, 28 s left (open)"
    assert R.vote_words(line)["kind"] == "endgame" and R.vote_words(line)["id"] == 3
    assert R.vote_words("something else") is None
    status = {"players": 8, "max_players": 16, "phase": "playing", "vote": line}
    assert len(e.status("h3-01", status)) == 1 and e.status("h3-01", status) == []
    assert vote_text(line).plain == line
    decided = engine(rule({"type": "vote", "kind": "kick"}, [{"type": "cancel_vote"}]))
    over = "vote 1 to kick Bravo called by the server: 0 yes, 0 no, 1 needed of 0 voters, 29 s left (failed)"
    assert decided.status("h3-01", {"players": 1, "phase": "no_game", "vote": over}) == []


def test_a_kick_by_vote_reads_as_one():
    ev = {"event": "kick", "name": "Bravo", "id": B, "by": "vote (Alpha)", "by_id": "", "reason": "", "seconds": 600}
    assert "by a vote called by Alpha" in describe(ev, {}).plain
    kicks = {}
    assert note_kick(kicks, ev, 0.0) and kicks[B]["by"] == "vote (Alpha)"


def test_the_feed_says_the_new_events_in_words():
    assert "now MOD" in describe({"event": "role_added", "name": "Alice", "id": A, "role": "mod", "by": "x"}, {}).plain
    assert "SHADOW MUTE" in describe({"event": "mute", "name": "Bravo", "id": B, "kind": "text", "shadow": True}, {}).plain
    won = describe({"event": "poll", "state": "decided", "question": "Map?", "winner": 1,
                    "answers": [{"answer": "A", "picks": 0}, {"answer": "B", "picks": 2}]}, {}).plain
    assert "Map?" in won and "won: B" in won
    assert "/kick" in describe({"event": "staff", "name": "M", "command": "kick", "args": ["x"]}, {}).plain


# --- the sandbox ----------------------------------------------------------------------------------------------
def test_moderation_rule_reports_kicks_with_who_and_why():
    e = engine(rule({"type": "moderation", "action": "kick"}))
    fired = e.event("h3-01", {"event": "kick", "name": "Bravo", "id": B, "by": "vote (Alpha)", "reason": "afk"})
    assert R.fill("{player} {by} {reason} {action}", fired[0].ctx) == "Bravo vote (Alpha) afk kick"
    assert e.event("h3-01", {"event": "ban", "name": "Bravo", "id": B, "by": "x"}) == []


@pytest.mark.parametrize("when,event,words", [
    ({"type": "poll_decided"}, {"event": "poll", "state": "decided", "question": "Q?", "winner": 0,
                                "answers": [{"answer": "Yes", "picks": 1}, {"answer": "No", "picks": 0}]}, "Q? Yes"),
    ({"type": "panel_press", "panel": "dj", "button": "Skip"}, {"event": "panel_press", "panel": "dj", "button": "skip", "name": "A", "id": A}, "skip"),
    ({"type": "server_phase", "phase": "post_game"}, {"event": "server_phase", "phase": "post_game", "since": 1}, "post game"),
    ({"type": "refused"}, {"event": "refused", "name": "Z", "id": A, "ip": "x", "reason": "banned"}, "banned"),
    ({"type": "staff_command"}, {"event": "staff", "name": "M", "id": A, "role": "mod", "command": "kick", "args": ["Z"], "result": "ok"}, "kick"),
])
def test_new_triggers_fire(when, event, words):
    e = engine(rule(when, [{"type": "say", "to": "this", "message": "{question} {winner} {button} {phase} {reason} {command}"}]))
    fired = e.event("h3-01", event)
    assert len(fired) == 1 and words in R.fill(fired[0].rule["then"][0]["message"], fired[0].ctx)


def test_server_phase_fires_only_on_a_change_and_feeds_the_condition():
    e = engine(rule({"type": "server_phase", "phase": "lobby"}))
    assert len(e.event("h3-01", {"event": "server_phase", "phase": "lobby"})) == 1
    assert e.event("h3-01", {"event": "server_phase", "phase": "lobby"}) == []
    assert e.condition({"type": "phase_is", "phase": "lobby"}, "h3-01", {}, {}) is True
    e.status("h3-01", {"phase": "playing", "server_phase": "in_game"})
    assert e.condition({"type": "phase_is", "phase": "in_game"}, "h3-01", {}, {}) is True


def test_the_servers_own_roles_count_for_conditions_and_messages():
    e = engine(rule({"type": "join"}, [{"type": "tell_staff", "message": "x"}]))
    c = "c" * 64
    e.players("h3-01", [{"number": 1, "name": "Mod", "id": c, "admin": False}, {"number": 2, "name": "Vee", "id": "d" * 64, "admin": False}])
    e.server_roles("h3-01", ROLE_LIST)
    assert e.role_of(c, "h3-01") == ["mod"] and e.role_of("d" * 64) == ["vip"]
    assert [p["name"] for _, p in e.staff()] == ["Mod"]  # VIP doesn't moderate; `admin` is false for a Mod
    assert e.condition({"type": "player_role", "role": "mod"}, "h3-01", {}, {"player_id": c})
    assert [p["name"] for _, p in e.with_role("vip")] == ["Vee"]


def test_new_actions_plan_real_commands():
    r = R.validate(rule({"type": "join"}, [
        {"type": "start_poll", "question": "Next?", "answers": "A, B", "seconds": 20},
        {"type": "show_panel", "panel": "rules", "title": "Rules", "message": "Be good", "slot": "side", "seconds": 45},
        {"type": "hide_panel", "panel": "rules"}, {"type": "set_joins", "joins": "closed"}]))
    e = engine()
    steps = R.plan(e, R.Firing(r, "h3-01", {"player": "x"}))
    assert [s.args for s in steps] == [("poll", "Next?", "A", "B", "20"),
                                       ("panel", "show", "rules", "Rules", "line:Be good", "slot:side", "timeout:45s"),
                                       ("panel", "hide", "rules"), ("joins", "closed")]
    assert all(s.words() for s in steps)


def test_rules_still_cannot_kick_ban_mute_or_give_roles():
    assert not {"kick", "ban", "mute", "role"} & set(R.CATALOG["then"])
    for block in R.CATALOG["then"].values():
        assert block[0] not in ("Kick", "Ban", "Mute")


def test_bad_new_blocks_are_refused():
    with pytest.raises(ValueError, match="2 to 6"):
        R.validate(rule({"type": "join"}, [{"type": "start_poll", "question": "Q", "answers": "only", "seconds": 20}]))
    with pytest.raises(ValueError, match="short word"):
        R.validate(rule({"type": "join"}, [{"type": "hide_panel", "panel": "two words"}]))


def test_every_template_validates():
    for s in R.STARTERS:
        hooked = {**s, "then": [{**a, "webhook": "x"} if a.get("type") == "discord" else a for a in s["then"]]}
        assert R.validate(hooked)["name"] == s["name"]


def test_explain_says_why_a_rule_would_not_fire():
    r = R.validate(rule({"type": "moderation", "action": "kick"},
                        **{"if": [{"type": "players_between", "min": 4, "max": 8}, {"type": "player_role", "role": "vip"}]}))
    e = engine()
    e.status("h3-01", {"players": 2, "max_players": 8})
    said = R.explain(e, r, "h3-01", "0.9.23")
    assert said[0][1] is False and "0.10" in said[0][0]  # the version
    assert said[1][1] is False and said[2][1] is None  # too few players; only a real player can answer the role
    assert R.explain(e, r, "h3-01", "0.10.0")[0][1] is True


def test_a_010_step_on_an_old_server_is_skipped_not_sent():
    class Client:
        ws, info, sent = True, {"version": "0.9.23"}, []

        async def call(self, *args, **kw):
            self.sent.append(args)
            return {"ok": True}

    run = rules_run.Runner.__new__(rules_run.Runner)
    run.clients, run.said, run.clock = {"h3-01": Client()}, {}, lambda: 0.0
    step = R.Step("rcon", "h3-01", ("joins", "closed"))
    assert "before 0.10" in asyncio.run(run.step(step)) and Client.sent == []
    Client.info = {"version": "0.10.0"}
    assert "done" in asyncio.run(run.step(step)) and Client.sent == [("joins", "closed")]


# --- reading the server's words ----------------------------------------------------------------------------------
def test_role_rows_read_every_holder_form():
    rows = {r["id"][:1]: r for r in F.role_rows(ROLE_LIST)}
    assert rows["a"]["fixed"] and rows["a"]["from"] == "dedicated.toml" and rows["b"]["fixed"] and rows["e"]["fixed"]
    assert rows["c"]["name"] == "Alpha Two" and not rows["c"]["fixed"] and rows["c"]["role"] == "mod"
    assert rows["d"]["name"] == "" and rows["d"]["from"].startswith("added by")
    assert R.staff_of(ROLE_LIST)["a" * 64] == {"role": "admin", "rank": 30}


def test_mute_and_panel_lines():
    rows = F.mute_rows(f"{A} Alice, 300 s left (spam), by Bravo [{B}]\n{B} Bravo Two\nNo mutes.")
    assert rows[0]["left"] == 300 and rows[0]["reason"] == "spam" and rows[0]["by"].startswith("Bravo")
    assert rows[1]["name"] == "Bravo Two" and rows[1]["left"] is None and len(rows) == 2
    assert F.panel_rows("labtest: Lab panel (1 lines; buttons: Skip=next)\nNo panels show.")[0]["id"] == "labtest"
    assert len(F.panel_rows("No panels show.")) == 0


def test_poll_and_panel_arguments_are_checked_here_first():
    assert F.poll_args("Q?", ["A", "", "B"], "30") == ["Q?", "A", "B", "30"]
    for bad in (("", ["A", "B"], 30), ("Q", ["A"], 30), ("Q", ["A", "B"], 5)):
        with pytest.raises(ValueError):
            F.poll_args(*bad)
    assert F.panel_args("rules", "Rules", ["one", ""], "Skip=next", "side", "60s") == [
        "rules", "Rules", "line:one", "button:Skip=next", "slot:side", "timeout:60s"]
    with pytest.raises(ValueError):
        F.panel_args("two words", "x", [], "", "side", "")


def test_setting_form_round_trip_keeps_what_it_did_not_touch():
    values = {"joins": "queue", "join_gates": ["in_game"], "queue_max": 5}
    fields = {k: d for k, _, d in F.form_fields("joins", values)}
    assert fields["joins"][0][1] == json.dumps("queue") and fields["queue_max"] == "5"
    got = {k: (d[0][1] if isinstance(d, list) else d) for k, d in fields.items()}
    assert F.changes_from("joins", got, values) == {}
    got["queue_max"] = "9"
    got["joins"] = C_DEFAULT = F.C.DEFAULT
    assert F.changes_from("joins", got, values) == {"queue_max": 9, "joins": None} and C_DEFAULT


def test_a_value_the_menu_has_no_word_for_is_kept():
    opts = F.choice_options(F.C.BY_KEY["customization"], ["Some Mod"])
    assert opts[0][1] == json.dumps(["Some Mod"]) and "Keep as it is" in opts[0][0]


def test_games_text_says_what_each_game_gives():
    text = F.games_text({"halo3": True, "haloreach": False})
    assert "Halo 3: YES" in text and "Halo: Reach: no" in text and "dedicated init" in text
    assert "unknown" in F.games_text(None)


# --- the screens, on the demo's pretend 0.10 servers --------------------------------------------------------------
async def until(pilot, ok, what=""):
    for _ in range(150):
        await pilot.pause(.03)
        if ok():
            return
    assert ok(), what


def panel_titled(app, title):
    return isinstance(app.screen, Panel) and app.screen.t.startswith(title)


async def open_hub(pilot, app, key):
    await pilot.press("ctrl+t")
    await until(pilot, lambda: panel_titled(app, "SERVER FEATURES"), "the hub")
    assert app.screen.query_one(OptionList).highlighted == 0  # a fresh menu opens at its top
    app.screen.query_one(OptionList).highlighted = [k for k, _, _ in F.HUB].index(key)
    await pilot.click("#p-open")


def test_roles_mutes_polls_panels_joins_on_a_pretend_server():
    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = OniApp([Server(port=p, password="demo") for p in ports[:1]], by="pytest", intro=False)
        async with app.run_test(size=(170, 50)) as pilot:
            await until(pilot, lambda: app.cur.online and app.cur.players, "connected")
            await open_hub(pilot, app, "roles")
            await until(pilot, lambda: panel_titled(app, "ROLES"), "roles")
            await pilot.click("#p-add")
            await until(pilot, lambda: isinstance(app.screen, Pick), "who")
            app.screen.dismiss(app.screen.shown[1][1])  # the first player online
            await until(pilot, lambda: isinstance(app.screen, Pick) and "WHICH ROLE" in app.screen.t, "which role")
            app.screen.dismiss("mod")
            await until(pilot, lambda: panel_titled(app, "ROLES") and app.screen.opts, "they're listed")
            pid = app.screen.opts[0][1].split("|")[0]
            await pilot.click("#p-remove")
            await until(pilot, lambda: isinstance(app.screen, Confirm), "confirm")
            await pilot.click("#yes")
            await until(pilot, lambda: panel_titled(app, "ROLES") and not app.screen.opts, "gone")
            assert pid
            app.screen.dismiss(None)
            await until(pilot, lambda: panel_titled(app, "SERVER FEATURES"), "back at the hub")
            assert app.screen.query_one(OptionList).highlighted == 0

            app.screen.query_one(OptionList).highlighted = [k for k, _, _ in F.HUB].index("polls")
            await pilot.click("#p-open")
            await until(pilot, lambda: panel_titled(app, "POLLS"))
            await pilot.click("#p-new")
            await until(pilot, lambda: isinstance(app.screen, JobForm))
            app.screen.dismiss({"question": "Next?", "a1": "A", "a2": "B", "a3": "", "a4": "", "a5": "", "a6": "", "seconds": "20"})
            await until(pilot, lambda: panel_titled(app, "SERVER FEATURES"))

            app.screen.query_one(OptionList).highlighted = [k for k, _, _ in F.HUB].index("panels")
            await pilot.click("#p-open")
            await until(pilot, lambda: panel_titled(app, "PANELS"))
            await pilot.click("#p-show")
            await until(pilot, lambda: isinstance(app.screen, JobForm))
            app.screen.dismiss({"id": "rules", "title": "Rules", "line1": "Be good", "line2": "", "button": "", "slot": "side", "timeout": "60s"})
            await until(pilot, lambda: panel_titled(app, "PANELS") and app.screen.opts, "showing")
            await pilot.click("#p-hide")
            await until(pilot, lambda: panel_titled(app, "PANELS") and not app.screen.opts, "hidden")
            app.screen.dismiss(None)
            await until(pilot, lambda: panel_titled(app, "SERVER FEATURES"))

            app.screen.query_one(OptionList).highlighted = [k for k, _, _ in F.HUB].index("joins")
            await pilot.click("#p-open")
            await until(pilot, lambda: panel_titled(app, "JOINS"))
            await pilot.click("#p-closed")
            await until(pilot, lambda: panel_titled(app, "JOINS") and "CLOSED" in str(app.screen.body), "paused")
        for k in keep:
            k.close() if hasattr(k, "close") else None
    asyncio.run(go())


def test_settings_with_no_helper_hand_back_lines_to_paste(monkeypatch):
    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = OniApp([Server(port=ports[0], password="demo")], by="pytest", intro=False)
        async with app.run_test(size=(170, 50)) as pilot:
            await until(pilot, lambda: app.cur.online, "connected")
            await open_hub(pilot, app, "models")
            await until(pilot, lambda: isinstance(app.screen, JobForm))
            names = [k for k, *_ in app.screen.fields]
            got = {n: F.C.DEFAULT for n in names}
            got["customization"] = json.dumps(["Halo: Reach"])
            app.screen.dismiss(got)
            await until(pilot, lambda: isinstance(app.screen, Notice), "the lines")
            assert "NEEDS REACH" in app.screen.body and 'customization = ["Halo: Reach"]' in app.screen.body
    asyncio.run(go())


def test_a_server_before_0_10_is_told_and_offered_nothing():
    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = OniApp([Server(port=ports[0], password="demo")], by="pytest", intro=False)
        async with app.run_test(size=(170, 50)) as pilot:
            await until(pilot, lambda: app.cur.online, "connected")
            app.cur.rcon.info["version"] = "0.9.23"
            await pilot.press("ctrl+t")
            await pilot.pause(.2)
            assert not isinstance(app.screen, Panel)
            assert any("0.9.23" in str(n.message) for n in app._notifications)
    asyncio.run(go())


def test_customer_console_saves_through_the_helper_and_shows_a_refusal(tmp_path, monkeypatch):
    log = tmp_path / "calls"
    tool = tmp_path / "oni"
    tool.write_text("#!/bin/sh\necho \"$@\" >> " + str(log) + "\ncase \"$*\" in\n"
                    "  *set*) cat >> " + str(log) + "; if grep -q bogus " + str(log) + "; then "
                    "printf '%s\\n' '{\"state\":\"refused\",\"text\":\"The server checked it and said no\"}'; exit 1; fi; "
                    "printf '%s\\n' '{\"state\":\"applied\",\"text\":\"applied now\"}';;\n"
                    "  config*) printf '%s\\n' '{\"server\":{\"coop_network\":\"host\"},\"notes\":[\"This lobby may use 3 GB\"]}';;\n"
                    "  *) printf '[]\\n';;\nesac\n")
    tool.chmod(0o755)
    monkeypatch.setenv("IBIGS_ONI_POWER", str(tool))

    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = TenantApp([Server(name="h3-01", port=ports[0], password="demo")], by="pytest", intro=False)
        async with app.run_test(size=(170, 50)) as pilot:
            await until(pilot, lambda: app.cur.online, "connected")
            for w in list(app.screen_stack[1:]):
                app.pop_screen()
            await open_hub(pilot, app, "coop")
            await until(pilot, lambda: isinstance(app.screen, JobForm))
            app.screen.dismiss({"coop_network": json.dumps("lockstep")})
            await until(pilot, lambda: isinstance(app.screen, Confirm))
            await pilot.click("#yes")
            await until(pilot, lambda: panel_titled(app, "SERVER FEATURES"))
            assert '"coop_network": "lockstep"' in log.read_text() and "h3-01 set --json" in log.read_text()
            assert "3 GB" in str(app.screen.foot)  # the helper's word on memory shows on the hub
            F_ = app.screen
            F_.query_one(OptionList).highlighted = [k for k, _, _ in F.HUB].index("advanced")
            await pilot.click("#p-open")
            await until(pilot, lambda: isinstance(app.screen, Pick))
            app.screen.dismiss("set")
            await until(pilot, lambda: isinstance(app.screen, JobForm))
            app.screen.dismiss({"key": "queue_mode", "value": "bogus"})
            await until(pilot, lambda: isinstance(app.screen, Confirm))
            await pilot.click("#yes")
            await until(pilot, lambda: isinstance(app.screen, Notice), "the refusal")
            assert app.screen.warn and "said no" in app.screen.body
    asyncio.run(go())


def test_f1_role_and_mute_ask_what_0_10_can_do():
    async def go():
        ports, keep = await serve_fakes(tick=False)
        app = OniApp([Server(port=ports[0], password="demo")], by="pytest", intro=False)
        async with app.run_test(size=(170, 50)) as pilot:
            await until(pilot, lambda: app.cur.online and app.query_one("#player-actions").display, "a player selected")
            await pilot.click("#pl-mute")
            await until(pilot, lambda: isinstance(app.screen, JobForm), "the mute form")
            assert [k for k, *_ in app.screen.fields] == ["kind", "time", "reason", "shadow"]
            await pilot.press("escape")
            await until(pilot, lambda: not isinstance(app.screen, JobForm))
            await pilot.click("#pl-role")
            await until(pilot, lambda: isinstance(app.screen, Pick) and "WHICH ROLE" in app.screen.t, "the role list")
            app.screen.dismiss("vip")
            await until(pilot, lambda: R.staff_of(app.cur.data.get("role")), "F1 learns who holds it")
            app.action_refresh()
            await until(pilot, lambda: "VIP" in str(app.query_one("#players").get_row_at(app.query_one("#players").cursor_row)[-1])
                        or any("VIP" in str(c) for r in range(app.query_one("#players").row_count) for c in app.query_one("#players").get_row_at(r)), "the flag")
    asyncio.run(go())
