"""The rules sandbox: WHEN / IF / THEN, checked, fired and turned into steps (0.9.0+ibigs.15)."""
import pytest

from oni_rcon import rules as R

A = "a" * 64
B = "b" * 64
C = "c" * 64


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def rule(when, then, extra=None, **kw):
    return R.validate({"name": kw.pop("name", "test"), "when": when, "then": then, **(extra or {}), **kw})


def world(rules, roles=None):
    clock = Clock()
    e = R.Engine([rule(*r) if isinstance(r, tuple) else r for r in rules], roles or {}, now=clock)
    for name, players in (("h3-01", [(1, "Alpha", A, 0, False), (2, "Bravo", B, 1, False)]),
                          ("h3-02", [(1, "Charlie", C, 0, True)])):
        e.connection(name, True)
        e.status(name, {"players": len(players), "max_players": 16, "map": "Guardian", "mode": "Slayer",
                        "phase": "playing"})
        e.players(name, [{"engine_id": i, "name": n, "id": pid, "team": t, "admin": adm}
                         for i, n, pid, t, adm in players])
    return e, clock


def steps(e, firings):
    return [s.words() for f in firings for s in R.plan(e, f)]


def test_every_starter_is_a_valid_rule_and_reads_in_words():
    for s in R.STARTERS:
        s = dict(s)
        for a in s["then"]:
            if a["type"] == "discord":
                a["webhook"] = "alerts"
        r = R.validate(s)
        assert r["name"] and "WHEN" in R.describe(r) and "THEN" in R.describe(r)


@pytest.mark.parametrize("bad, words", [
    ({"when": {"type": "kick_everyone"}}, "Unknown WHEN"),
    ({"then": [{"type": "kick"}]}, "Unknown THEN"),  # no moderation actions exist
    ({"then": [{"type": "ban"}]}, "Unknown THEN"),
    ({"then": [{"type": "say", "to": "this", "message": "hi {password}"}]}, "unknown placeholder"),
    ({"then": [{"type": "say", "to": "this", "message": "x" * 241}]}, "at most 240"),
    ({"then": [{"type": "say", "to": "this", "message": "two\nlines"}]}, "printable"),
    ({"when": {"type": "command", "command": "admin"}}, "starts with !"),
    ({"when": {"type": "spree", "counts": "0,500"}}, "between 2 and 100"),
    ({"then": []}, "1 to 8"),
    ({"servers": ["../etc"]}, "Servers"),
    ({"cooldown": -1}, "Cooldown"),
    ({"then": [{"type": "discord", "webhook": "", "message": "x"}]}, "webhook"),
])
def test_nonsense_is_refused_in_words(bad, words):
    base = {"name": "x", "when": {"type": "join"}, "then": [{"type": "say", "to": "this", "message": "hi"}]}
    with pytest.raises(ValueError, match=words):
        R.validate({**base, **bad})


def test_sprees_multikills_and_killjoy():
    e, clock = world([({"type": "spree", "counts": "3,5"}, [{"type": "say", "to": "this", "message": "{player}: {medal} ({kills})"}]),
                      ({"type": "multikill", "min": 2}, [{"type": "say", "to": "all", "message": "{medal} {player}"}]),
                      ({"type": "medal", "medal": "Killjoy"}, [{"type": "say", "to": "this", "message": "{player} ended it"}])])
    said = []
    for i in range(5):
        clock.t += 10  # too far apart to chain
        said += steps(e, e.event("h3-01", {"event": "kill", "killer": 1, "victim": 2}))
    assert said == ["Say on h3-01: Alpha: 3-kill spree (3)", "Say on h3-01: Alpha: Killing Spree (5)"]
    clock.t += 10
    e.event("h3-01", {"event": "kill", "killer": 2, "victim": 1})
    assert e.lobbies["h3-01"].sprees["1"] == 0
    clock.t += 1
    out = steps(e, e.event("h3-01", {"event": "kill", "killer": 2, "victim": 1}))
    assert "Say on h3-01: Double Kill Bravo" in out and "Say on h3-02: Double Kill Bravo" in out


def test_killjoy_after_a_five_spree():
    e, clock = world([({"type": "medal", "medal": "Killjoy"}, [{"type": "say", "to": "this", "message": "{player} ended a {kills}"}])])
    for _ in range(6):
        clock.t += 10
        e.event("h3-01", {"event": "kill", "killer": 1, "victim": 2})
    clock.t += 10
    assert steps(e, e.event("h3-01", {"event": "kill", "killer": 2, "victim": 1})) == ["Say on h3-01: Bravo ended a 6"]


def test_betrayals_and_suicides_earn_nothing():
    e, clock = world([({"type": "spree", "counts": "2"}, [{"type": "say", "to": "this", "message": "x"}])])
    e.players("h3-01", [{"engine_id": 1, "name": "Alpha", "id": A, "team": 0},
                        {"engine_id": 2, "name": "Bravo", "id": B, "team": 0}])
    for _ in range(3):
        clock.t += 10
        assert not e.event("h3-01", {"event": "kill", "killer": 1, "victim": 2})
    assert not e.event("h3-01", {"event": "kill", "killer": 1, "victim": 1})


def test_native_medals_like_perfection():
    e, _ = world([({"type": "medal", "medal": "Perfection"}, [{"type": "say", "to": "all", "message": "PERFECTION {player}"}])])
    out = steps(e, e.event("h3-01", {"event": "medal", "medal": "perfection", "player_id": A}))
    assert out == ["Say on h3-01: PERFECTION Alpha", "Say on h3-02: PERFECTION Alpha"]


def test_admin_command_tells_the_admin_on_another_server_and_ordinary_chat_does_nothing():
    e, _ = world([({"type": "command", "command": "!admin"},
                   [{"type": "tell_admins", "where": "any", "message": "{player} on {server}: {args}"},
                    {"type": "tell_player", "message": "Told {admins}."}], {"cooldown": 0})])
    assert not e.event("h3-01", {"event": "chat", "name": "Alpha", "text": "where is the admin lol"})
    out = steps(e, e.event("h3-01", {"event": "chat", "name": "Alpha", "text": "!ADMIN he's flying"}))
    assert out == ["Tell Charlie on h3-02: Alpha on h3-01: he's flying", "Tell Alpha on h3-01: Told Charlie."]
    f = e.event("h3-01", {"event": "chat", "name": "Alpha", "text": "!admin"})
    assert R.plan(e, f[0])[0].args == ("tell", C, "Alpha on h3-01: ")


def test_roles_count_as_admin_and_vip_protection_cancels_a_kick_vote():
    vip = next(s for s in R.STARTERS if s["name"] == "VIP kick protection")
    e, _ = world([R.validate(vip)], roles={B: {"roles": ["vip"]}, A: {"roles": ["admin"]}})
    assert {p["name"] for _, p in e.admins()} == {"Alpha", "Charlie"}
    assert not e.event("h3-01", {"event": "vote", "text": "Charlie called a vote to kick Alpha"})  # not a VIP
    out = steps(e, e.event("h3-01", {"event": "vote", "text": "Alpha called a vote to kick Bravo"}))
    assert out == ["Cancel the vote on h3-01", "Say on h3-01: Bravo is a VIP and can't be vote-kicked."]
    # the status poll then sees the same ballot: it doesn't fire twice
    assert not e.status("h3-01", {"players": 2, "phase": "playing", "vote": {"id": 7, "kind": "kick", "target": B}})


def test_server_down_waits_then_tells_admins_elsewhere_and_up_follows():
    e, clock = world([({"type": "server_down", "seconds": 60},
                       [{"type": "tell_admins", "where": "other", "message": "{down} is down"}]),
                      ({"type": "server_up"}, [{"type": "say", "to": "this", "message": "{server} is back"}])])
    e.connection("h3-01", False)
    clock.t += 30
    assert not e.tick()
    clock.t += 31
    assert steps(e, e.tick()) == ["Tell Charlie on h3-02: h3-01 is down"]
    clock.t += 30
    assert not e.tick()  # once per outage
    assert steps(e, e.connection("h3-01", True)) == ["Say on h3-01: h3-01 is back"]
    e.connection("h3-02", False)
    clock.t += 10
    assert not e.connection("h3-02", True)  # a quick restart is not an outage


def test_full_empty_and_crossings():
    e, _ = world([({"type": "full"}, [{"type": "say", "to": "this", "message": "full {count}/{max}"}]),
                  ({"type": "empty"}, [{"type": "say", "to": "this", "message": "empty"}]),
                  ({"type": "players_above", "count": 8}, [{"type": "say", "to": "this", "message": "8+"}]),
                  ({"type": "players_below", "count": 2}, [{"type": "say", "to": "this", "message": "under 2"}])])
    s = lambda n: steps(e, e.status("h3-01", {"players": n, "max_players": 16, "phase": "playing"}))
    assert s(9) == ["Say on h3-01: 8+"]
    assert s(16) == ["Say on h3-01: full 16/16"]
    assert s(16) == []
    assert s(1) == ["Say on h3-01: under 2"]
    assert s(0) == ["Say on h3-01: empty"]


def test_conditions():
    e, _ = world([({"type": "join"}, [{"type": "say", "to": "this", "message": "hi"}])])
    c = lambda **k: e.condition(R._block("if", k), "h3-01", {}, {"player_id": A})
    assert c(type="players_between", min=1, max=2) and not c(type="players_between", min=3, max=9)
    assert c(type="admin_online", where="other") and not c(type="admin_online", where="this")
    assert not c(type="no_admin_online")
    assert c(type="map_is", text="guard") and not c(type="mode_is", text="ctf")
    assert c(type="server_state", server="h3-02", state="up") and not c(type="server_state", server="h3-02", state="full")
    assert c(type="player_role", role="none")


def test_cooldown_and_hourly_cap():
    e, clock = world([({"type": "join"}, [{"type": "say", "to": "this", "message": "hi"}], {"cooldown": 60, "per_hour": 2})
                      if False else rule({"type": "join"}, [{"type": "say", "to": "this", "message": "hi"}],
                                         cooldown=60, per_hour=2)])
    j = lambda pid: e.event("h3-01", {"event": "join", "name": "X", "player_id": pid})
    assert j(A) and not j(A)  # same player inside the cooldown
    assert j(B)  # someone else
    clock.t += 61
    assert not j(A)  # the hourly cap (2) is used up
    clock.t += 3600
    assert j(A)


def test_every_n_minutes_and_at_a_time():
    e, clock = world([({"type": "every", "minutes": 10}, [{"type": "say", "to": "this", "message": "tick"}],)])
    clock.t = 1_800_000_000 - 1_800_000_000 % 600 + 595
    e.tick()
    clock.t += 10
    assert steps(e, e.tick()) == ["Say on h3-01: tick", "Say on h3-02: tick"]
    clock.t += 2
    assert not e.tick()


def test_test_button_plans_without_conditions_and_messages_fill_safely():
    r = rule({"type": "spree", "counts": "10"}, [{"type": "say", "to": "this", "message": "{player} {medal} {kills}"},
                                                 {"type": "discord", "webhook": "alerts", "message": "{server}: {player}"}],
             **{"if": [{"type": "players_between", "min": 50, "max": 60}]})
    e, _ = world([r])
    out = [s.words() for s in R.plan(e, R.sample(e, r, "h3-01"))]
    assert out == ["Say on h3-01: ExamplePlayer Killing Frenzy 10", "Post to Discord (alerts): h3-01: ExamplePlayer"]
    assert "\x1b" not in R.fill("{player} said {args}", {"player": "A\x1b[2Jb", "args": "x" * 400})
    assert len(R.fill("{args}", {"args": "x" * 400})) == 240


def test_discord_urls_and_storage(tmp_path):
    good = "https://discord.com/api/webhooks/123456789012345678/" + "A" * 68
    assert R.webhook_ok(good) and R.webhook_ok(good.replace("discord.com", "canary.discord.com"))
    for bad in ("http://discord.com/api/webhooks/1234567/" + "A" * 30, "https://evil.example/api/webhooks/1/x",
                "https://discord.com.evil.io/api/webhooks/123456/" + "A" * 30, ""):
        assert not R.webhook_ok(bad)
    assert R.masked(good).endswith("/••••••") and "AAAA" not in R.masked(good)
    s = R.RuleStore(tmp_path)
    s.save_webhook("alerts", good)
    with pytest.raises(ValueError, match="Discord webhook"):
        s.save_webhook("bad", "https://example.com/hook")
    assert s.webhooks() == {"alerts": good}
    s.save_rule(rule({"type": "join"}, [{"type": "say", "to": "this", "message": "hi"}], id="r1"))
    assert [r["id"] for r in s.rules()] == ["r1"]
    s.save_rule(delete="r1")
    assert s.rules() == []
    s.set_roles(A, "Alpha", ["vip", "admin"])
    s.set_votes("h3-01", False)
    players, votes = s.roles()
    assert players[A]["roles"] == ["admin", "vip"] and votes == {"h3-01": False}
    with pytest.raises(ValueError):
        s.set_roles("not-an-id", "x", ["admin"])
    s.set_roles(A, "Alpha", [])
    assert s.roles()[0] == {}


def test_the_background_service_runs_rules_over_real_connections(tmp_path):
    """Two fake lobbies over real WebSockets: a welcome, an !admin call that reaches the admin on the OTHER server, a
    Discord post, and ordinary chat that does nothing."""
    import asyncio
    import json
    import time
    from websockets.asyncio.server import serve
    from oni_rcon.jobs import Store, Worker

    hook = "https://discord.com/api/webhooks/123456789012345678/" + "A" * 68
    store = R.RuleStore(tmp_path)
    store.save_webhook("alerts", hook)
    for r in (rule({"type": "join"}, [{"type": "tell_player", "message": "Welcome {player}!"}], id="w"),
              rule({"type": "command", "command": "!admin"},
                   [{"type": "tell_admins", "where": "any", "message": "{player} on {server}: {args}"},
                    {"type": "discord", "webhook": "alerts", "message": "{player} needs help on {server}"}], id="a")):
        store.save_rule(r)
    posted = []

    async def go():
        seen, peers = {}, {}

        def handler_for(name, roster):
            async def handler(ws):
                json.loads(await ws.recv())
                await ws.send(json.dumps({"type": "auth", "ok": True, "version": "0.9.15"}))
                peers[name] = ws
                async for raw in ws:
                    c = json.loads(raw)
                    data = {"players": len(roster), "max_players": 16, "phase": "playing"} if c["command"] == "status" \
                        else {"count": len(roster), "players": roster} if c["command"] == "players" else None
                    if c["command"] not in ("status", "players"):
                        seen.setdefault(name, []).append((c["command"], c["args"]))
                    await ws.send(json.dumps({"type": "reply", "id": c["id"], "ok": True, **({"data": data} if data else {})}))
            return handler

        one = [{"engine_id": 1, "name": "Alpha", "id": A, "admin": False}]
        two = [{"engine_id": 1, "name": "Charlie", "id": C, "admin": True}]
        async with serve(handler_for("h3-01", one), "127.0.0.1", 0) as s1, \
                serve(handler_for("h3-02", two), "127.0.0.1", 0) as s2:
            lobbies = [dict(name=n, host="127.0.0.1", port=s.sockets[0].getsockname()[1], password="x")
                       for n, s in (("h3-01", s1), ("h3-02", s2))]
            worker = Worker(Store(tmp_path), discover=lambda: lobbies, states=lambda: {"h3-01": "running", "h3-02": "running"},
                            poster=lambda url, text: posted.append((url, text)) or 204)
            worker.jobs = []
            worker.rules.reload()
            assert worker.rules.active
            await worker.refresh_clients()
            for _ in range(200):
                if all(c.ws for c in worker.clients.values()) and len(peers) == 2:
                    break
                await asyncio.sleep(.01)
            for n, c in worker.clients.items():
                await worker.poll(n, c)
            send = lambda lobby, **ev: peers[lobby].send(json.dumps({"type": "event", "time": time.time(), **ev}))
            await send("h3-01", event="join", name="Alpha", player_id=A)
            await send("h3-01", event="chat", name="Alpha", text="admins are slow lol")
            await send("h3-01", event="chat", name="Alpha", text="!admin someone is flying", player_id=A)
            for _ in range(300):
                if posted and len(seen.get("h3-02", [])) >= 1 and seen.get("h3-01"):
                    break
                await asyncio.sleep(.01)
            await asyncio.sleep(.1)
            assert seen["h3-01"] == [("tell", [A, "Welcome Alpha!"])]
            assert seen["h3-02"] == [("tell", [C, "Alpha on h3-01: someone is flying"])]
            assert posted == [(hook, "Alpha needs help on h3-01")]
            history = json.loads((tmp_path / "rules-state.json").read_text())["history"]
            assert {h["rule"] for h in history} == {"w", "a"} and all("done" in h["result"] for h in history)
            assert hook not in json.dumps(history)
            for task in worker.tasks.values():
                task.cancel()
            await asyncio.gather(*worker.tasks.values(), return_exceptions=True)
    asyncio.run(go())
