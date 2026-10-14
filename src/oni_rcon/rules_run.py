"""Running rules inside the background service (`oni jobs run`), on the scheduled jobs' RCON connections.

The worker in jobs.py owns the connections and calls on_event / on_connection / on_status / on_players / tick. This
holds the rules engine, carries out what fires and keeps a short history in rules-state.json. Rules files that don't
parse pause the rules (and say so in the history) without stopping the scheduled jobs.
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from collections import deque

from . import rules as R
from .jobs import atomic_json

SAY_PER_MINUTE = 20  # per server, across every rule: a misjudged rule can't flood the chat
DISCORD_GAP = 2.0  # seconds between Discord posts (Discord allows about 30 a minute per webhook)
HISTORY = 200

# Always on while anyone has the VIP role: VIPs can't be vote-kicked (until Reclaimer has VIPs of its own).
VIP_GUARD = {"id": "builtin-vip-guard", "name": "VIP kick protection (built in)", "enabled": True, "servers": "all",
             "when": {"type": "vote", "kind": "kick"}, "if": [{"type": "player_role", "role": "vip"}],
             "then": [{"type": "cancel_vote"},
                      {"type": "say", "to": "this", "message": "{target} is a VIP and can't be vote-kicked."}],
             "cooldown": 0, "per_hour": 120}


def post_discord(url: str, text: str, timeout: float = 10) -> int:
    """POST one message. No @everyone or role pings, whatever the text says."""
    body = json.dumps({"content": text, "username": "IBIGS Server", "allowed_mentions": {"parse": []}}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "oni-rcon rules"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - URL checked by rules.webhook_ok
        return r.status


class Runner:
    def __init__(self, store: R.RuleStore, clients: dict, start=None, poster=post_discord, clock=time.time):
        self.store, self.clients, self.start, self.poster = store, clients, start, poster
        self.engine = R.Engine(now=clock)
        self.clock = clock
        self.webhooks: dict = {}
        self.signature = None
        self.error = ""
        self.said: dict[str, deque] = {}
        self.discord_at = 0.0
        self.discord_lock = asyncio.Lock()
        self.tasks: set = set()
        self.history: list = []
        try:
            self.history = store.history()[-HISTORY:]
        except (OSError, ValueError):
            pass

    @property
    def active(self) -> bool:
        """Whether any rule could fire: then the service keeps a connection to every lobby."""
        return any(r["enabled"] for r in self.engine.rules)

    def reload(self) -> None:
        """Re-read rules, roles and webhooks when a file changed (the console edits them while this runs)."""
        stamp = []
        for name in ("rules.json", "roles.json", "discord.json"):
            try:
                st = (self.store.root / name).stat()
                stamp.append((name, st.st_mtime_ns, st.st_size))
            except FileNotFoundError:
                stamp.append((name, None, None))
        if stamp == self.signature:
            return
        self.signature = stamp
        try:
            rules = self.store.rules()
            roles, _ = self.store.roles()
            self.webhooks = self.store.webhooks()
        except (OSError, ValueError) as exc:
            if str(exc) != self.error:
                self.error = str(exc)
                self.note("", "", f"Rules paused: {exc}")
            self.engine.rules = []
            return
        self.error = ""
        if any("vip" in p["roles"] for p in roles.values()):
            rules = [R.validate(VIP_GUARD), *rules]
        self.engine.rules, self.engine.roles = rules, roles

    # inputs from the worker -------------------------------------------------------------------------------------
    def on_event(self, server, msg):
        self.carry_out(self.engine.event(server, msg))

    def on_connection(self, server, up):
        self.carry_out(self.engine.connection(server, up))

    def on_status(self, server, status):
        self.carry_out(self.engine.status(server, status))

    def on_players(self, server, data):
        players = data.get("players") if isinstance(data, dict) else data
        self.engine.players(server, players)

    def on_inventory(self, states: dict):
        """service -> container state ('running', 'exited', ...): stopped on purpose isn't an outage."""
        for name, state in states.items():
            self.engine.lobby(name).stopped = state not in ("running", "restarting")

    def tick(self):
        self.carry_out(self.engine.tick())

    def on_fps(self, server, fps):
        self.carry_out(self.engine.frame_rate(server, fps))

    @property
    def wants_fps(self) -> bool:
        return any(r["enabled"] and (r["when"]["type"] in ("fps_below", "fps_recovered") or
                                     any(c["type"] == "fps_under" for c in r["if"])) for r in self.engine.rules)

    # carrying out ------------------------------------------------------------------------------------------------
    def carry_out(self, firings):
        for f in firings:
            steps = R.plan(self.engine, f)
            task = asyncio.ensure_future(self.run_steps(f, steps))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def run_steps(self, firing, steps):
        results = []
        for step in steps:
            try:
                results.append(await self.step(step))
            except Exception as exc:  # one failed step never stops the service or the other steps
                results.append(f"{step.words()}: failed ({type(exc).__name__})")
        self.note(firing.rule["id"], firing.server, "; ".join(results) or "Nothing to do (nobody to tell).",
                  firing.rule["name"])

    async def step(self, step) -> str:
        if step.kind == "rcon":
            client = self.clients.get(step.server)
            if not client or not client.ws:
                return f"{step.words()}: skipped, server offline"
            if step.args[0] in ("say", "tell"):
                window = self.said.setdefault(step.server, deque())
                now = self.clock()
                while window and now - window[0] > 60:
                    window.popleft()
                if len(window) >= SAY_PER_MINUTE:
                    return f"{step.words()}: skipped, {SAY_PER_MINUTE} messages a minute is the limit"
                window.append(now)
            if step.args[0] == "shuffle":
                fresh = await client.call("status")
                if (fresh.get("data") or {}).get("phase") not in ("playing", "in_game"):
                    return f"{step.words()}: skipped, no match is being played"
            reply = await client.call(*step.args)
            return step.words() + (": done" if reply.get("ok") else ": the server refused")
        if step.kind == "discord":
            url = self.webhooks.get(step.webhook)
            if not url:
                return f"{step.words()}: no webhook called {step.webhook}"
            async with self.discord_lock:
                wait = DISCORD_GAP - (self.clock() - self.discord_at)
                if wait > 0:
                    await asyncio.sleep(wait)
                self.discord_at = self.clock()
                code = await asyncio.to_thread(self.poster, url, step.text)
            return step.words() + (": done" if 200 <= code < 300 else f": Discord answered {code}")
        if step.kind == "wait":
            await asyncio.sleep(step.args[0])
            return step.words()
        if step.kind == "start":
            lob = self.engine.lobbies.get(step.server)
            if lob and lob.up:
                return f"{step.words()}: already running"
            if not self.start:
                return f"{step.words()}: can't start servers from here"
            rc = await self.start(step.server)
            return step.words() + (": done" if rc == 0 else f": failed (exit {rc})")
        return step.words()

    def note(self, rule, server, result, name=""):
        self.history.append({"at": self.clock(), "rule": rule, "name": name, "server": server, "result": result[:600]})
        self.history = self.history[-HISTORY:]
        try:
            atomic_json(self.store.root / "rules-state.json", {"schema": 1, "history": self.history})
        except OSError:
            pass
