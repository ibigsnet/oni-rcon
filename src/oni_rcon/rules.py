"""Rules: WHEN something happens on this VPS's servers, IF the conditions hold, THEN do things (0.9.0+ibigs.15).

A rule is plain data, built from menus in the console (F8 → RULES) and run by the same background service as the
scheduled jobs, so rules keep working while nobody has oni open:

    {"id": "a1b2c3", "name": "Spree announcer", "enabled": true, "servers": "all",
     "when": {"type": "spree", "counts": "5,10,15"},
     "if": [{"type": "players_between", "min": 4, "max": 128}],
     "then": [{"type": "say", "to": "this", "message": "{player} is on a {medal}!"}],
     "cooldown": 0, "per_hour": 120}

Everything a rule can do is in CATALOG below: one place, with the words the menus show. A rule can talk (say, tell,
Discord), cancel a vote, shuffle teams or start a stopped server. It can't kick, ban, mute, restart a busy server or run
commands, and nothing reacts to what people say except "!commands" typed on purpose: IBIGS moderates cheating, not
speech, and kicks and bans stay with a person.

The engine is pure: the worker feeds it events, status and roster readings and gets back Firings to carry out.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .jobs import ROOT, MatchEvents, atomic_json, cron_fields, cron_due
from .safety import clean

ROLES = ("admin", "vip", "mod")
SPREES = {5: "Killing Spree", 10: "Killing Frenzy", 15: "Running Riot", 20: "Rampage", 25: "Untouchable",
          30: "Invincible"}
MULTIS = {2: "Double Kill", 3: "Triple Kill", 4: "Overkill", 5: "Killtacular", 6: "Killtrocity", 7: "Killimanjaro",
          8: "Killtastrophe", 9: "Killpocalypse", 10: "Killionaire"}
CHAIN = 4.0  # seconds between kills that still chain into a multi-kill (Halo 3's window)
MEDALS = [*SPREES.values(), *MULTIS.values(), "Killjoy", "Perfection"]
MAX_RULES = 128
INSTANT = {"medal", "spree", "multikill", "join", "leave", "match_start", "match_end", "before_match", "every", "at",
           "vote"}
MAX_LINE = 240

# --- the menus ------------------------------------------------------------------------------------------------
# kind: what a field holds. int (min, max), text (max length), choice (options), server (this VPS's lobbies),
# servers (all or a list), role, message (a template), webhook (a saved Discord webhook's name).
F = lambda key, label, kind, default=None, **kw: {"key": key, "label": label, "kind": kind, "default": default, **kw}

CATALOG = {
    "when": {
        "medal": ("A player earns a medal", "Sprees, multi-kills, Killjoy, and Perfection or any other medal the "
                  "server reports.", [F("medal", "Which medal", "choice", "any", options=["any", *MEDALS])]),
        "spree": ("A player reaches a killing spree", "Kills without dying: 5 is a Killing Spree, 10 a Frenzy, and so "
                  "on.", [F("counts", "At these counts (comma list)", "text", "5,10,15,20,25,30", max=60)]),
        "multikill": ("A player gets a multi-kill", "Kills less than 4 seconds apart: 2 is a Double Kill, 3 a "
                      "Triple, 4 an Overkill.", [F("min", "At least this many kills", "int", 3, min=2, max=10)]),
        "join": ("A player joins", "Someone finishes connecting.", []),
        "leave": ("A player leaves", "Someone disconnects or is removed.", []),
        "command": ("A player types a !command", "Only messages that START with the command count, e.g. !admin. "
                    "Ordinary chat never triggers anything.",
                    [F("command", "Command", "text", "!admin", max=24)]),
        "vote": ("A vote starts", "A player calls a vote.",
                 [F("kind", "Kind of vote", "choice", "any", options=["any", "kick", "endround", "endgame", "shuffle",
                                                                     "playlist"])]),
        "match_start": ("A match starts", "The game goes live.", []),
        "match_end": ("A match ends", "The game finishes (not a round break).", []),
        "before_match": ("Voting or loading begins", "Between matches, before the next one starts.", []),
        "server_down": ("A server goes down", "It stops answering for this long; a quick restart doesn't count.",
                        [F("seconds", "Down for at least (seconds)", "int", 60, min=15, max=3600)]),
        "server_up": ("A server comes back", "It answers again after being reported down.", []),
        "full": ("A server fills up", "Every slot is taken.", []),
        "empty": ("A server empties", "The last player leaves.", []),
        "players_above": ("Players rise to N", "The count reaches N from below.",
                          [F("count", "N players", "int", 8, min=1, max=128)]),
        "players_below": ("Players drop under N", "The count falls below N.",
                          [F("count", "N players", "int", 4, min=1, max=128)]),
        "every": ("Every N minutes", "On a timer, lined up with the clock.",
                  [F("minutes", "Minutes", "int", 30, min=1, max=10080)]),
        "at": ("At a time of day", "Cron-style: minute hour day month weekday.",
               [F("cron", "When (minute hour day month weekday)", "text", "0 20 * * 5", max=60),
                F("timezone", "Timezone", "text", "America/New_York", max=48)]),
    },
    "if": {
        "players_between": ("Player count is between", "On the server where it happened.",
                            [F("min", "At least", "int", 0, min=0, max=128), F("max", "At most", "int", 128, min=0,
                                                                                   max=128)]),
        "admin_online": ("An admin is online", "Someone with the ADMIN role, or the server's own admin flag.",
                         [F("where", "Where", "choice", "any", options=["any", "this", "other"])]),
        "no_admin_online": ("No admin is online", "Nobody with ADMIN is playing anywhere on this VPS.", []),
        "player_role": ("The player has a role", "The player who triggered the rule.",
                        [F("role", "Role", "choice", "vip", options=[*ROLES, "none"])]),
        "map_is": ("The map is", "Part of the map's name is enough.", [F("text", "Map", "text", "", max=40)]),
        "mode_is": ("The game mode is", "Part of the mode's name is enough.", [F("text", "Mode", "text", "", max=40)]),
        "time_between": ("The time is between", "Local time, e.g. 18:00 to 23:59. Wraps past midnight.",
                         [F("start", "From (HH:MM)", "text", "18:00", max=5), F("end", "To (HH:MM)", "text", "23:59",
                                                                                 max=5),
                          F("timezone", "Timezone", "text", "America/New_York", max=48)]),
        "server_state": ("Another server is", "Up, down, full or empty right now.",
                         [F("server", "Server", "server", ""),
                          F("state", "State", "choice", "up", options=["up", "down", "full", "empty"])]),
    },
    "then": {
        "say": ("Say in chat", "A message everyone on the server sees.",
                [F("to", "On", "choice", "this", options=["this", "all", "other servers"]),
                 F("message", "Message", "message", "")]),
        "say_on": ("Say on a specific server", "", [F("server", "Server", "server", ""),
                                                   F("message", "Message", "message", "")]),
        "tell_player": ("Message the player privately", "Only the player who triggered the rule sees it.",
                        [F("message", "Message", "message", "")]),
        "tell_admins": ("Message the admins privately", "Every online ADMIN, on whichever server they're playing.",
                        [F("where", "Admins on", "choice", "any", options=["any", "this", "other"]),
                         F("message", "Message", "message", "")]),
        "tell_role": ("Message everyone with a role", "Privately, wherever they're playing.",
                      [F("role", "Role", "choice", "vip", options=list(ROLES)), F("message", "Message", "message", "")]),
        "cancel_vote": ("Cancel the vote", "Stops the vote under way on that server.", []),
        "shuffle": ("Shuffle the teams", "Only while a match is being played.", []),
        "start_server": ("Start a stopped server", "Does nothing if it's already running.",
                         [F("server", "Server", "server", "")]),
        "discord": ("Post to Discord", "Sends the message to a Discord channel through a webhook you've added.",
                    [F("webhook", "Webhook", "webhook", ""), F("message", "Message", "message", "")]),
    },
}

TEMPLATE_HELP = ("{player} the player · {server} this server · {count} players on it · {max} its slots · {medal} the "
                 "medal · {kills} spree or multi-kill count · {map} · {mode} · {admins} admins online · {target} a "
                 "vote's target · {args} what followed a !command · {down} the server that went down · {time}")
PLACEHOLDERS = {"player", "server", "count", "max", "medal", "kills", "map", "mode", "admins", "target", "args",
                "down", "time"}

# Starting points: copy one, change the words. Kept small and obviously useful.
STARTERS = [
    {"name": "Spree announcer", "when": {"type": "spree", "counts": "5,10,15,20,25,30"},
     "then": [{"type": "say", "to": "this", "message": "{player} is on a {medal}! ({kills} kills)"}],
     "cooldown": 0, "per_hour": 240},
    {"name": "Multi-kill shout-out", "when": {"type": "multikill", "min": 3},
     "then": [{"type": "say", "to": "this", "message": "{medal} by {player}!"}], "cooldown": 0, "per_hour": 240},
    {"name": "Perfection", "when": {"type": "medal", "medal": "Perfection"},
     "then": [{"type": "say", "to": "all", "message": "PERFECTION! {player} won on {server} without dying."}],
     "cooldown": 0, "per_hour": 60},
    {"name": "Welcome", "when": {"type": "join"},
     "then": [{"type": "tell_player", "message": "Welcome to {server}, {player}! Type !admin if you need help."}],
     "cooldown": 0, "per_hour": 600},
    {"name": "!admin call", "when": {"type": "command", "command": "!admin"},
     "if": [{"type": "admin_online", "where": "any"}],
     "then": [{"type": "tell_admins", "where": "any", "message": "{player} on {server} needs an admin: {args}"},
              {"type": "tell_player", "message": "An admin has been told. Hang tight."}], "cooldown": 60},
    {"name": "!admin with nobody on", "when": {"type": "command", "command": "!admin"},
     "if": [{"type": "no_admin_online"}],
     "then": [{"type": "discord", "webhook": "", "message": "{player} on {server} asked for an admin: {args}"},
              {"type": "tell_player", "message": "No admin is online; we've pinged Discord."}], "cooldown": 120},
    {"name": "Server down: tell the admins", "when": {"type": "server_down", "seconds": 60},
     "then": [{"type": "tell_admins", "where": "other", "message": "Heads up: {down} is down."},
              {"type": "discord", "webhook": "", "message": "{down} has been down for a minute."}], "cooldown": 600},
    {"name": "Full server alert", "when": {"type": "full"},
     "then": [{"type": "tell_admins", "where": "any", "message": "{server} is full ({count}/{max})."}],
     "cooldown": 900},
    {"name": "Empty server: point people elsewhere", "when": {"type": "players_below", "count": 2},
     "then": [{"type": "say", "to": "this", "message": "Quiet here! Try our other servers."}], "cooldown": 1800},
    {"name": "VIP kick protection", "when": {"type": "vote", "kind": "kick"},
     "if": [{"type": "player_role", "role": "vip"}],
     "then": [{"type": "cancel_vote"}, {"type": "say", "to": "this", "message": "{target} is a VIP and can't be vote-kicked."}],
     "cooldown": 0},
]


# --- checking a rule --------------------------------------------------------------------------------------------
def _line(v, what, required=True):
    if not isinstance(v, str) or len(v) > MAX_LINE or clean(v) != v or any(ord(c) < 32 for c in v):
        raise ValueError(f"{what} must be one printable line of at most {MAX_LINE} characters.")
    if required and not v.strip():
        raise ValueError(f"{what} is empty.")
    bad = {m for m in re.findall(r"\{(\w*)\}", v)} - PLACEHOLDERS
    if bad:
        raise ValueError(f"{what}: unknown placeholder {{{sorted(bad)[0]}}}. You can use: " + TEMPLATE_HELP)
    return v


def _block(part, block, servers=()):
    """One WHEN/IF/THEN block, checked against the catalog. Returns it normalized."""
    if not isinstance(block, dict) or block.get("type") not in CATALOG[part]:
        raise ValueError(f"Unknown {part.upper()} choice: {block.get('type') if isinstance(block, dict) else block!r}.")
    label, _, fields = CATALOG[part][block["type"]]
    out = {"type": block["type"]}
    for f in fields:
        v = block.get(f["key"], f["default"])
        kind = f["kind"]
        if kind == "int":
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ValueError(f"{label}: {f['label']} must be a whole number.") from None
            if not f["min"] <= v <= f["max"]:
                raise ValueError(f"{label}: {f['label']} must be {f['min']}–{f['max']}.")
        elif kind == "choice":
            if v not in f["options"]:
                raise ValueError(f"{label}: choose one of {', '.join(f['options'])}.")
        elif kind == "message":
            v = _line(v, f"{label}: the message")
        elif kind in ("text", "server", "webhook"):
            if not isinstance(v, str) or len(v) > f.get("max", 64) or clean(v) != v:
                raise ValueError(f"{label}: {f['label']} is too long or has odd characters.")
            v = v.strip()
            if kind == "server" and not re.fullmatch(r"h3-\d{2,3}|server-\d{1,3}", v):
                raise ValueError(f"{label}: choose a server.")
            if kind == "webhook" and not v:
                raise ValueError(f"{label}: choose a Discord webhook (add one under DISCORD first).")
        out[f["key"]] = v
    t = out["type"]
    if t == "spree":
        counts = sorted({int(x) for x in re.findall(r"\d+", out["counts"])})
        if not counts or any(not 2 <= c <= 100 for c in counts):
            raise ValueError("Killing spree: give counts between 2 and 100, like 5,10,15.")
        out["counts"] = ",".join(map(str, counts))
    elif t == "command":
        if not re.fullmatch(r"![A-Za-z0-9_-]{1,23}", out["command"]):
            raise ValueError("A command starts with ! and has letters or numbers, like !admin.")
        out["command"] = out["command"].lower()
    elif t in ("at", "time_between"):
        try:
            ZoneInfo(out["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Use a timezone such as America/New_York or UTC.") from None
        if t == "at":
            cron_fields(out["cron"])
        else:
            for k in ("start", "end"):
                if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", out[k]):
                    raise ValueError("Times look like 18:00 or 07:30.")
    elif t == "players_between" and out["min"] > out["max"]:
        raise ValueError("Player count: 'at least' is more than 'at most'.")
    return out


def validate(rule):
    if not isinstance(rule, dict):
        raise ValueError("A rule must be an object.")
    out = {"id": str(rule.get("id") or uuid.uuid4().hex[:12]), "name": rule.get("name"),
           "enabled": rule.get("enabled", True)}
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", out["id"]):
        raise ValueError("Invalid rule ID.")
    if not isinstance(out["name"], str) or not 1 <= len(out["name"]) <= 80 or clean(out["name"]) != out["name"]:
        raise ValueError("Give the rule a name (1–80 characters).")
    if type(out["enabled"]) is not bool:
        raise ValueError("enabled must be true or false.")
    servers = rule.get("servers", "all")
    if servers != "all":
        if not isinstance(servers, list) or not servers or len(servers) > 64 or not all(
                isinstance(s, str) and re.fullmatch(r"h3-\d{2,3}|server-\d{1,3}", s) for s in servers):
            raise ValueError("Servers: choose all, or a list of this VPS's servers.")
        servers = sorted(set(servers))
    out["servers"] = servers
    out["when"] = _block("when", rule.get("when"))
    conds = rule.get("if") or []
    acts = rule.get("then") or []
    if not isinstance(conds, list) or len(conds) > 8:
        raise ValueError("Up to 8 conditions.")
    if not isinstance(acts, list) or not 1 <= len(acts) <= 8:
        raise ValueError("A rule needs 1 to 8 things to do.")
    out["if"] = [_block("if", c) for c in conds]
    out["then"] = [_block("then", a) for a in acts]
    # moments (a medal, a join) shouldn't be held back by default; states and alerts get a breather
    instant = out["when"]["type"] in INSTANT
    for k, default, hi in (("cooldown", 0 if instant else 30, 86400), ("per_hour", 120 if instant else 60, 600)):
        v = rule.get(k, default)
        if type(v) is not int or not (0 if k == "cooldown" else 1) <= v <= hi:
            raise ValueError(f"{'Cooldown' if k == 'cooldown' else 'Per-hour limit'} must be "
                             f"{0 if k == 'cooldown' else 1}–{hi}.")
        out[k] = v
    return out


def describe_block(part, block) -> str:
    """One block in words, for lists and the confirmation."""
    label, _, fields = CATALOG[part][block["type"]]
    bits = []
    for f in fields:
        v = block.get(f["key"])
        if f["kind"] == "message":
            bits.append(f'"{v}"')
        elif v not in (None, "", "any") or f["kind"] == "int":
            bits.append(str(v))
    return label + (": " + ", ".join(bits) if bits else "")


def describe(rule) -> str:
    where = "every server" if rule["servers"] == "all" else ", ".join(rule["servers"])
    lines = [f"WHEN  {describe_block('when', rule['when'])}  ({where})"]
    lines += [f"IF    {describe_block('if', c)}" for c in rule["if"]]
    lines += [f"THEN  {describe_block('then', a)}" for a in rule["then"]]
    limits = []
    if rule["cooldown"]:
        limits.append(f"at most once every {rule['cooldown']} s")
    limits.append(f"at most {rule['per_hour']} times an hour")
    return "\n".join(lines + ["      " + ", ".join(limits)])


# --- storage -----------------------------------------------------------------------------------------------------
class RuleStore:
    """rules.json, roles.json and discord.json beside the scheduled jobs (/srv/ibigs/automation, group ibigs-admin).
    The console writes what people want; the background service reads it every few seconds."""

    def __init__(self, root=ROOT):
        from pathlib import Path
        self.root = Path(root)

    def _read(self, name, default):
        try:
            data = json.loads((self.root / name).read_text())
        except FileNotFoundError:
            return default
        if not isinstance(data, dict) or data.get("schema") != 1:
            raise ValueError(f"{name} is damaged. Restore it before rules can run.")
        return data

    def _write(self, name, data):
        import fcntl
        import os
        self.root.mkdir(parents=True, exist_ok=True, mode=0o2770)
        fd = os.open(self.root / "config.lock", os.O_CREAT | os.O_RDWR, 0o660)
        with os.fdopen(fd, "r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            atomic_json(self.root / name, {"schema": 1, **data})

    # rules
    def rules(self):
        data = self._read("rules.json", {"schema": 1, "rules": []})
        rules = data.get("rules")
        if not isinstance(rules, list) or len(rules) > MAX_RULES:
            raise ValueError("rules.json is damaged. Restore it before rules can run.")
        out = [validate(r) for r in rules]
        if len({r["id"] for r in out}) != len(out):
            raise ValueError("Rule IDs must be unique.")
        return out

    def save_rule(self, rule=None, delete=None):
        rules = self.rules()
        if rule is not None:
            rule = validate(rule)
        ident = rule["id"] if rule else delete
        rules = [r for r in rules if r["id"] != ident] + ([rule] if rule else [])
        if len(rules) > MAX_RULES:
            raise ValueError(f"Up to {MAX_RULES} rules.")
        self._write("rules.json", {"rules": rules})

    # roles: {player_id: {"name", "roles": [...], "servers": "all"|[...]}}; votes: {lobby: true|false}
    def roles(self):
        data = self._read("roles.json", {"schema": 1, "players": {}, "votes": {}})
        players, votes = data.get("players"), data.get("votes", {})
        if not isinstance(players, dict) or not isinstance(votes, dict):
            raise ValueError("roles.json is damaged.")
        out = {}
        for pid, p in players.items():
            if not re.fullmatch(r"[0-9a-f]{64}", str(pid)) or not isinstance(p, dict):
                raise ValueError("roles.json has an entry that isn't a player ID.")
            roles = [r for r in p.get("roles", []) if r in ROLES]
            servers = p.get("servers", "all")
            if servers != "all" and not (isinstance(servers, list) and all(
                    isinstance(s, str) and re.fullmatch(r"h3-\d{2,3}|server-\d{1,3}", s) for s in servers)):
                servers = "all"
            out[pid] = {"name": clean(str(p.get("name", "")))[:40], "roles": roles, "servers": servers}
        votes = {k: v for k, v in votes.items() if re.fullmatch(r"h3-\d{2,3}|server-\d{1,3}", str(k))
                 and isinstance(v, bool)}
        return out, votes

    def set_roles(self, pid, name, roles, servers="all"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(pid)):
            raise ValueError("That isn't a player ID.")
        if any(r not in ROLES for r in roles):
            raise ValueError("Roles are admin, vip and mod.")
        players, votes = self.roles()
        if roles:
            players[pid] = {"name": clean(str(name))[:40], "roles": sorted(set(roles), key=ROLES.index),
                            "servers": servers}
        else:
            players.pop(pid, None)
        self._write("roles.json", {"players": players, "votes": votes})

    def set_votes(self, lobby, on):
        players, votes = self.roles()
        if on is None:
            votes.pop(lobby, None)
        else:
            votes[lobby] = bool(on)
        self._write("roles.json", {"players": players, "votes": votes})

    # discord: {name: url}. Readable by the ibigs-admin group, like everything here; never shown in full.
    def webhooks(self):
        data = self._read("discord.json", {"schema": 1, "webhooks": {}})
        hooks = data.get("webhooks")
        if not isinstance(hooks, dict):
            raise ValueError("discord.json is damaged.")
        return {k: v for k, v in hooks.items() if isinstance(k, str) and isinstance(v, str) and webhook_ok(v)}

    def save_webhook(self, name, url=None):
        hooks = self.webhooks()
        if url is None:
            hooks.pop(name, None)
        else:
            if not re.fullmatch(r"[A-Za-z0-9 _-]{1,32}", name or ""):
                raise ValueError("Name the webhook with letters, numbers and spaces (up to 32).")
            if not webhook_ok(url):
                raise ValueError("That doesn't look like a Discord webhook URL. It starts with "
                                 "https://discord.com/api/webhooks/")
            hooks[name] = url.strip()
        self._write("discord.json", {"webhooks": hooks})

    def history(self):
        data = self._read("rules-state.json", {"schema": 1, "history": []})
        return data.get("history", []) if isinstance(data.get("history"), list) else []


WEBHOOK = re.compile(r"https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/\d{5,30}/[A-Za-z0-9_-]{20,100}")


def webhook_ok(url) -> bool:
    return isinstance(url, str) and bool(WEBHOOK.fullmatch(url.strip()))


def masked(url: str) -> str:
    """A webhook URL with its secret part hidden, for the screen."""
    head, _, _ = url.rpartition("/")
    return head + "/••••••"


DISCORD_STEPS = (
    "To let rules post in a Discord channel:\n"
    "  1. In Discord, open your server and go to the channel you want.\n"
    "  2. Click the gear (Edit Channel) → Integrations → Webhooks → New Webhook.\n"
    "  3. Give it a name (for example IBIGS Alerts), then click Copy Webhook URL.\n"
    "  4. Paste the URL below and give it a short name here, like alerts.\n"
    "You need the Manage Webhooks permission in that Discord server. Anyone with the URL can post in that channel, "
    "so keep it private. Delete the webhook in Discord to revoke it.")


# --- the engine ----------------------------------------------------------------------------------------------------
@dataclass
class Lobby:
    """What the service knows about one server right now."""
    name: str
    up: bool = False
    down_since: float | None = None
    down_told: bool = False  # a server_down rule has fired for this outage
    stopped: bool = False  # its container was stopped on purpose (F7, oni stop): not an outage
    count: int | None = None
    max: int | None = None
    map: str = ""
    mode: str = ""
    phase: str = ""
    roster: dict = field(default_factory=dict)  # engine id -> {"name", "id", "team", "admin"}
    roster_at: float = 0.0
    sprees: dict = field(default_factory=dict)  # engine id -> kills this life
    chains: dict = field(default_factory=dict)  # engine id -> (kills chained, time of the last)
    matches: MatchEvents = field(default_factory=MatchEvents)
    vote_id: object = None  # the ballot last seen, so a vote fires once


@dataclass
class Firing:
    """A rule that fired, and the facts its actions and messages use."""
    rule: dict
    server: str
    ctx: dict


def fill(template: str, ctx: dict) -> str:
    """The message with its {placeholders} filled; unknown or missing ones become empty. One printable line."""
    out = re.sub(r"\{(\w+)\}", lambda m: clean(str(ctx.get(m[1], ""))).replace("\n", " "), template)
    return out[:MAX_LINE]


class Engine:
    def __init__(self, rules=(), roles=None, now=None):
        self.rules = list(rules)
        self.roles = roles or {}
        self.lobbies: dict[str, Lobby] = {}
        self.fired: dict[tuple, float] = {}  # (rule, server, player) -> last time
        self.hourly: dict[str, deque] = {}  # rule -> times in the last hour
        self.clock = (lambda: time.time()) if now is None else now
        self.last_tick = None

    # facts --------------------------------------------------------------------------------------------------------
    def lobby(self, name) -> Lobby:
        return self.lobbies.setdefault(name, Lobby(name))

    def role_of(self, pid) -> list[str]:
        return self.roles.get(str(pid), {}).get("roles", [])

    def admins(self, where=None) -> list[tuple[str, dict]]:
        """(server, player) for every online admin: the ADMIN role or the server's own admin flag."""
        out = []
        for name, lob in self.lobbies.items():
            if where is not None and name not in where or not lob.up:
                continue
            for p in lob.roster.values():
                if p.get("admin") is True or "admin" in self.role_of(p.get("id")):
                    out.append((name, p))
        return out

    def with_role(self, role) -> list[tuple[str, dict]]:
        return [(n, p) for n, lob in self.lobbies.items() if lob.up for p in lob.roster.values()
                if role in self.role_of(p.get("id"))]

    def context(self, server, **kw) -> dict:
        lob = self.lobby(server)
        ctx = {"server": server, "count": lob.count if lob.count is not None else "", "max": lob.max or "",
               "map": lob.map, "mode": lob.mode, "admins": ", ".join(p["name"] for _, p in self.admins()) or "none",
               "time": datetime.now().strftime("%H:%M")}
        ctx.update(kw)
        return ctx

    # inputs -------------------------------------------------------------------------------------------------------
    def connection(self, server, up: bool) -> list[Firing]:
        lob, now, out = self.lobby(server), self.clock(), []
        if up and not lob.up:
            if lob.down_told:
                out += self.fire("server_up", server, self.context(server, down=server))
            lob.down_since, lob.down_told = None, False
        elif not up and lob.up:
            lob.down_since = now
            lob.roster, lob.sprees, lob.chains, lob.matches = {}, {}, {}, MatchEvents()
        lob.up = up
        return out

    def status(self, server, s: dict) -> list[Firing]:
        lob, out = self.lobby(server), []
        if not isinstance(s, dict):
            return out
        before = lob.count
        n, mx = s.get("players"), s.get("max_players")
        lob.count = n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else lob.count
        lob.max = mx if isinstance(mx, int) and not isinstance(mx, bool) and mx > 0 else lob.max
        lob.map, lob.mode = clean(str(s.get("map") or ""))[:40], clean(str(s.get("mode") or ""))[:40]
        phase = s.get("phase")
        if isinstance(phase, str) and phase != lob.phase:
            if phase == "playing" or lob.phase == "playing":
                lob.sprees, lob.chains = {}, {}  # sprees end with the game
            lob.phase = phase
        for ev in lob.matches.observe(s):
            out += self.fire({"after_match": "match_end"}.get(ev, ev), server, self.context(server))
        if before is not None and lob.count is not None and lob.count != before:
            ctx = self.context(server)
            if lob.max and lob.count >= lob.max > before:
                out += self.fire("full", server, ctx)
            if lob.count == 0 < before:
                out += self.fire("empty", server, ctx)
            out += self.fire("players_above", server, ctx, crossed=(before, lob.count))
            out += self.fire("players_below", server, ctx, crossed=(before, lob.count))
        vote = s.get("vote")
        if isinstance(vote, dict) and vote.get("id") is not None and vote.get("id") != lob.vote_id:
            seen = isinstance(lob.vote_id, tuple) and self.clock() - lob.vote_id[1] < 60
            lob.vote_id = vote.get("id")
            if seen:  # the event already announced this ballot
                return out
            out += self.vote_started(server, str(vote.get("kind") or vote.get("subject") or ""),
                                     vote.get("target"), vote.get("target_id"), "")
        elif not vote:
            lob.vote_id = None
        return out

    def players(self, server, players) -> None:
        lob = self.lobby(server)
        if not isinstance(players, list):
            return
        roster = {}
        for p in players[:128]:
            if not isinstance(p, dict):
                continue
            ident = p.get("engine_id", p.get("number"))
            name = clean(str(p.get("name") or ""))[:40]
            if ident is None or not name:
                continue
            roster[str(ident)] = {"name": name, "id": str(p.get("id") or p.get("player_id") or ""),
                                  "team": p.get("team"), "admin": p.get("admin")}
        for gone in set(lob.roster) - set(roster):
            lob.sprees.pop(gone, None)
            lob.chains.pop(gone, None)
        lob.roster, lob.roster_at = roster, self.clock()
        lob.count = len(roster) if lob.count is None else lob.count

    def find(self, lob: Lobby, value) -> tuple[str, dict] | None:
        """A roster entry by engine id, player ID or name."""
        if value is None:
            return None
        v = str(value)
        if v in lob.roster:
            return v, lob.roster[v]
        for k, p in lob.roster.items():
            if v in (p["id"], p["name"]):
                return k, p
        return None

    def event(self, server, ev: dict) -> list[Firing]:
        lob, kind, out = self.lobby(server), ev.get("event"), []
        if kind in ("lagged",) or ev.get("type") == "lagged":
            lob.sprees, lob.chains = {}, {}
            return out
        if kind == "kill":
            return self.kill(server, ev)
        if kind == "join":
            pid = str(ev.get("player_id") or ev.get("id") or "")
            name = clean(str(ev.get("name") or ""))[:40]
            return self.fire("join", server, self.context(server, player=name), player_id=pid)
        if kind == "leave":
            pid = str(ev.get("player_id") or ev.get("id") or "")
            name = clean(str(ev.get("name") or ""))[:40]
            hit = self.find(lob, pid or name)
            if hit:
                lob.sprees.pop(hit[0], None)
                lob.chains.pop(hit[0], None)
            return self.fire("leave", server, self.context(server, player=name), player_id=pid)
        if kind == "chat":
            text = str(ev.get("text") or "").strip()
            if not text.startswith("!"):
                return out  # ordinary chat never triggers anything
            word, _, rest = text.partition(" ")
            name = clean(str(ev.get("name") or ""))[:40]
            pid = str(ev.get("player_id") or ev.get("id") or "")
            if not pid and (hit := self.find(lob, name)):
                pid = hit[1]["id"]
            return self.fire("command", server, self.context(server, player=name, args=clean(rest)[:120]),
                             player_id=pid, command=word.lower())
        if kind in ("medal", "award"):
            medal = clean(str(ev.get("medal") or ev.get("award") or ""))[:40]
            hit = self.find(lob, ev.get("player_id") or ev.get("id") or ev.get("name"))
            if not medal:
                return out
            name = hit[1]["name"] if hit else clean(str(ev.get("name") or ""))[:40]
            return self.fire("medal", server, self.context(server, player=name, medal=medal.title(), kills=""),
                             player_id=hit[1]["id"] if hit else "", medal=medal)
        if kind == "vote":
            action = str(ev.get("action") or "")
            text = str(ev.get("text") or "")
            m = re.search(r"called a vote to (\w+)(?: (.+))?", text)
            if action == "start" or m:
                vkind = str(ev.get("vote_type") or (m[1] if m else ""))
                return self.vote_started(server, vkind, ev.get("target") or (m[2] if m else None), ev.get("target_id"),
                                         clean(str(ev.get("name") or ev.get("caller") or ""))[:40], via_event=True)
        return out

    def vote_started(self, server, kind, target, target_id, caller, via_event=False) -> list[Firing]:
        lob = self.lobby(server)
        if via_event:
            lob.vote_id = ("event", self.clock())  # the status poll's copy of this ballot must not fire it again
        kind = kind.lower().replace("_", "")
        kind = {"endmatch": "endgame", "endmap": "endgame"}.get(kind, kind)
        hit = self.find(lob, target_id) or self.find(lob, target)
        tname = hit[1]["name"] if hit else clean(str(target or ""))[:40]
        return self.fire("vote", server, self.context(server, player=caller, target=tname), vote=kind,
                         player_id=hit[1]["id"] if hit else "", role_subject="target")

    def kill(self, server, ev) -> list[Firing]:
        lob, now, out = self.lobby(server), self.clock(), []
        if lob.phase not in ("", "playing", "in_game"):
            return out
        killer, victim = self.find(lob, ev.get("killer")), self.find(lob, ev.get("victim"))
        ended = 0
        if victim:
            ended = lob.sprees.get(victim[0], 0)
            lob.sprees[victim[0]] = 0
            lob.chains.pop(victim[0], None)
        if not killer or (victim and killer[0] == victim[0]):
            return out
        if any(ev.get(k) is True for k in ("betrayal", "teamkill", "suicide")) or (
                victim and killer[1].get("team") is not None and killer[1]["team"] == victim[1].get("team")):
            return out  # betrayals earn nothing
        k, p = killer
        n = lob.sprees[k] = lob.sprees.get(k, 0) + 1
        chained, last = lob.chains.get(k, (0, float("-inf")))
        chained = chained + 1 if now - last <= CHAIN else 1
        lob.chains[k] = (chained, now)
        base = dict(player=p["name"])
        if chained >= 2:
            medal = MULTIS[min(chained, 10)]
            ctx = self.context(server, medal=medal, kills=chained, **base)
            out += self.fire("multikill", server, ctx, player_id=p["id"], count=chained)
            out += self.fire("medal", server, ctx, player_id=p["id"], medal=medal)
        if n >= 2:  # each rule picks its own counts (when_matches)
            medal = SPREES.get(n, f"{n}-kill spree")
            ctx = self.context(server, medal=medal, kills=n, **base)
            out += self.fire("spree", server, ctx, player_id=p["id"], count=n)
            if n in SPREES:
                out += self.fire("medal", server, ctx, player_id=p["id"], medal=medal)
        if ended >= 5:
            out += self.fire("medal", server, self.context(server, medal="Killjoy", kills=ended, **base),
                             player_id=p["id"], medal="Killjoy")
        return out

    def tick(self) -> list[Firing]:
        """Timers and outages: call every couple of seconds."""
        now, out = self.clock(), []
        for name, lob in self.lobbies.items():
            if not lob.up and lob.down_since is not None and not lob.down_told and not lob.stopped:
                for rule in self.rules:
                    if rule["enabled"] and rule["when"]["type"] == "server_down" and \
                            now - lob.down_since >= rule["when"]["seconds"]:
                        lob.down_told = True
                        out += self.fire("server_down", name, self.context(name, down=name), only=rule)
        prev, self.last_tick = self.last_tick, now
        if prev is not None:
            for rule in self.rules:
                w = rule["when"]
                if not rule["enabled"] or w["type"] not in ("every", "at"):
                    continue
                if w["type"] == "every":
                    period = w["minutes"] * 60
                    due = int(now // period) != int(prev // period)
                else:
                    a = datetime.fromtimestamp(now, ZoneInfo(w["timezone"]))
                    b = datetime.fromtimestamp(prev, ZoneInfo(w["timezone"]))
                    due = a.strftime("%Y%m%d%H%M") != b.strftime("%Y%m%d%H%M") and cron_due(w["cron"], a)
                if due:
                    targets = [n for n, lob in self.lobbies.items() if lob.up] if rule["servers"] == "all" else \
                        [n for n in rule["servers"] if self.lobby(n).up]
                    for server in targets:
                        out += self.fire(w["type"], server, self.context(server), only=rule)
        return out

    # matching -------------------------------------------------------------------------------------------------------
    def when_matches(self, w, **facts) -> bool:
        t = w["type"]
        if t == "medal":
            return w["medal"] == "any" or str(facts.get("medal", "")).casefold() == w["medal"].casefold()
        if t == "spree":
            return facts.get("count") in {int(x) for x in w["counts"].split(",")}
        if t == "multikill":
            return facts.get("count", 0) >= w["min"]
        if t == "command":
            return facts.get("command") == w["command"]
        if t == "vote":
            return w["kind"] == "any" or facts.get("vote") == w["kind"]
        if t in ("players_above", "players_below"):
            before, now = facts.get("crossed", (None, None))
            if before is None or now is None:
                return False
            return before < w["count"] <= now if t == "players_above" else before >= w["count"] > now
        return True

    def condition(self, c, server, ctx, facts) -> bool:
        t, lob = c["type"], self.lobby(server)
        if t == "players_between":
            return lob.count is not None and c["min"] <= lob.count <= c["max"]
        if t == "admin_online":
            where = None if c["where"] == "any" else ({server} if c["where"] == "this" else set(self.lobbies) - {server})
            return bool(self.admins(where))
        if t == "no_admin_online":
            return not self.admins()
        if t == "player_role":
            roles = self.role_of(facts.get("player_id"))
            return (not roles) if c["role"] == "none" else c["role"] in roles
        if t == "map_is":
            return c["text"].casefold() in lob.map.casefold()
        if t == "mode_is":
            return c["text"].casefold() in lob.mode.casefold()
        if t == "time_between":
            now = datetime.now(ZoneInfo(c["timezone"])).strftime("%H:%M")
            a, b = c["start"], c["end"]
            return a <= now <= b if a <= b else now >= a or now <= b
        if t == "server_state":
            other = self.lobby(c["server"])
            return {"up": other.up, "down": not other.up,
                    "full": bool(other.up and other.max and other.count is not None and other.count >= other.max),
                    "empty": bool(other.up and other.count == 0)}[c["state"]]
        return False

    def fire(self, trigger, server, ctx, only=None, **facts) -> list[Firing]:
        now, out = self.clock(), []
        for rule in [only] if only else self.rules:
            if not rule["enabled"] or rule["when"]["type"] != trigger:
                continue
            if rule["servers"] != "all" and server not in rule["servers"]:
                continue
            if not self.when_matches(rule["when"], **facts):
                continue
            if not all(self.condition(c, server, ctx, facts) for c in rule["if"]):
                continue
            key = (rule["id"], server, facts.get("player_id", ""))
            if rule["cooldown"] and now - self.fired.get(key, float("-inf")) < rule["cooldown"]:
                continue
            hour = self.hourly.setdefault(rule["id"], deque())
            while hour and now - hour[0] > 3600:
                hour.popleft()
            if len(hour) >= rule["per_hour"]:
                continue
            self.fired[key] = now
            hour.append(now)
            out.append(Firing(rule, server, {**ctx, "_player_id": facts.get("player_id", "")}))
        if len(self.fired) > 5000:
            self.fired = {k: v for k, v in self.fired.items() if now - v < 86400}
        return out


# --- what a firing does -------------------------------------------------------------------------------------------
@dataclass
class Step:
    """One concrete thing to do: an RCON command on a server, or a Discord post."""
    kind: str  # rcon | discord | start
    server: str = ""
    args: tuple = ()
    webhook: str = ""
    text: str = ""

    def words(self) -> str:
        if self.kind == "discord":
            return f"Post to Discord ({self.webhook}): {self.text}"
        if self.kind == "start":
            return f"Start {self.server} if it's stopped"
        cmd = self.args[0]
        if cmd == "say":
            return f"Say on {self.server}: {self.args[1]}"
        if cmd == "tell":
            return f"Tell {self.text} on {self.server}: {self.args[2]}"
        return {"cancelvote": f"Cancel the vote on {self.server}", "shuffle": f"Shuffle teams on {self.server}"}.get(
            cmd, f"{cmd} on {self.server}")


def plan(engine: Engine, firing: Firing) -> list[Step]:
    """The steps a firing turns into, given who's where right now. Nothing is sent here: TEST shows this list."""
    steps, ctx, here = [], firing.ctx, firing.server
    for a in firing.rule["then"]:
        t = a["type"]
        msg = fill(a.get("message", ""), ctx) if "message" in a else ""
        if t == "say":
            if a["to"] == "this":
                targets = [here]
            else:
                targets = [n for n, lob in engine.lobbies.items() if lob.up and (a["to"] == "all" or n != here)]
            steps += [Step("rcon", n, ("say", msg)) for n in targets]
        elif t == "say_on":
            steps.append(Step("rcon", a["server"], ("say", msg)))
        elif t == "tell_player":
            if ctx.get("_player_id"):
                steps.append(Step("rcon", here, ("tell", ctx["_player_id"], msg), text=str(ctx.get("player", ""))))
        elif t == "tell_admins":
            where = None if a["where"] == "any" else ({here} if a["where"] == "this" else set(engine.lobbies) - {here})
            for server, p in engine.admins(where):
                if p.get("id"):
                    steps.append(Step("rcon", server, ("tell", p["id"], msg), text=p["name"]))
        elif t == "tell_role":
            for server, p in engine.with_role(a["role"]):
                if p.get("id"):
                    steps.append(Step("rcon", server, ("tell", p["id"], msg), text=p["name"]))
        elif t == "cancel_vote":
            steps.append(Step("rcon", here, ("cancelvote",)))
        elif t == "shuffle":
            steps.append(Step("rcon", here, ("shuffle",)))
        elif t == "start_server":
            steps.append(Step("start", a["server"]))
        elif t == "discord":
            steps.append(Step("discord", webhook=a["webhook"], text=msg))
    return steps


def sample(engine: Engine, rule: dict, server: str) -> Firing:
    """A made-up firing for TEST: the rule's trigger with example facts on a real server, ignoring its conditions,
    cooldown and limits."""
    w = rule["when"]
    ctx = engine.context(server, player="ExamplePlayer", args="help please", target="ExampleTarget", down=server)
    if w["type"] == "spree":
        n = int(w["counts"].split(",")[0])
        ctx.update(medal=SPREES.get(n, f"{n}-kill spree"), kills=n)
    elif w["type"] == "multikill":
        ctx.update(medal=MULTIS[w["min"]], kills=w["min"])
    elif w["type"] == "medal":
        ctx.update(medal="Perfection" if w["medal"] == "any" else w["medal"], kills="")
    return Firing(rule, server, {**ctx, "_player_id": "0" * 64})
