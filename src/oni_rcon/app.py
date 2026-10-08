"""The ONI terminal: a Textual UI over one RCON connection per server."""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import shlex
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from os.path import commonprefix
from typing import Callable

from rich.console import Group
from rich.json import JSON
from rich.measure import measure_renderables
from rich.table import Table
from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.color import Color
from textual.containers import Center, Grid, Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.markup import escape
from textual.screen import ModalScreen, Screen
from textual.suggester import SuggestFromList
from textual.theme import Theme
from textual.widgets import (Button, Checkbox, Collapsible, DataTable, Footer, Input, Label, ListItem, ListView,
                             OptionList, RichLog, Select, Static, TabbedContent, TabPane)
from textual.widgets.option_list import Option
from textual.worker import WorkerState

from .art import (AMBER, CYAN, DIM, GOLD, GREEN, GREY, INK, RED, WHITE, biosig, blend, decrypt, emblem, gauge, hbar,
                  spark, split_bar)
from .config import Server
from .medals import Medals
from .rcon import Rcon, Tunnel

ONI = Theme(name="oni", primary=AMBER, secondary=CYAN, accent=CYAN, warning="#E8A33D", error=RED, success=GREEN,
            foreground=WHITE, background=INK, surface="#0B0F14", panel="#111821", dark=True)

TEAMS = ["red", "blue", "green", "orange", "purple", "gold", "brown", "pink"]  # the engine's team order
TEAM_ORDER = {t: i for i, t in enumerate(TEAMS)}
TEAM_COLOR = dict(zip(TEAMS, ["#E5484D", "#4C8DFF", "#4CC27A", "#F08A24", "#A066E0", "#E8C547", "#A0714A", "#F07FB8"]))
BAN_TIMES = [("Permanent", ""), ("30 minutes", "30m"), ("2 hours", "2h"), ("12 hours", "12h"), ("1 day", "1d"),
             ("7 days", "7d"), ("2 weeks", "2w"), ("30 days", "30d")]
MUTE_TIMES = [("Until restart", ""), ("10 minutes", "10m"), ("1 hour", "1h"), ("12 hours", "12h"), ("1 day", "1d")]
COMMANDS = ["status", "players", "say", "tell", "kick", "ban", "banip", "unban", "bans", "vpn", "vpnallow", "vpnrevoke",
            "mute", "unmute", "endround", "endgame", "maps", "modes", "map", "mode", "load", "nextmap", "servername",
            "password", "maxping", "teamcount", "shuffle", "team", "vote", "startvote", "passvote", "cancelvote", "help"]

CATS = {"chat": WHITE, "combat": RED, "traffic": CYAN, "moderation": AMBER, "ops": GREY}
KIND = {"chat": "chat", "kill": "combat", "join": "traffic", "leave": "traffic", "refused": "traffic",
        "kick": "moderation", "ban": "moderation", "unban": "moderation", "mute": "moderation",
        "unmute": "moderation", "cheat": "moderation"}
GLYPH = {"chat": "»", "kill": "✕", "join": "▲", "leave": "▼", "refused": "⊘", "kick": "◆", "ban": "■", "unban": "□",
         "mute": "◈", "unmute": "◇", "cheat": "!", "vote": "◉", "control": "•", "uplink": "≡"}
STATE = {"online": ("◉", GREEN), "connecting": ("◌", AMBER), "offline": ("○", RED), "denied": ("⊘", RED)}
SPIN = "◐◓◑◒"
PANE_FOCUS = {"assets": "#players", "intercepts": "#feed", "operations": "#op-load", "blacklist": "#bans",
              "console": "#cmd"}
ALERT = re.compile(r"\b(admins?|mods?|hack\w*|cheat\w*|aimbot|wallhack)\b", re.I)
ADDRESS_KEYS = {"address", "ip", "ip_address", "addr"}
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
REFS = ("killer", "victim", "name", "player", "from", "sender")  # event fields that can name a player by engine ID
SPREE = 5  # kills without dying that make a spree, and earn the marker in the roster

OPS = [("load", "LOAD MAP+MODE", "primary"), ("map", "CHANGE MAP", "default"), ("mode", "CHANGE MODE", "default"),
       ("nextmap", "QUEUE NEXT", "default"), ("endround", "END ROUND", "warning"), ("endgame", "END GAME", "error"),
       ("shuffle", "SHUFFLE TEAMS", "default"), ("teamcount", "TEAM COUNT", "default"),
       ("startvote", "CALL VOTE", "default"), ("passvote", "PASS VOTE", "success"),
       ("cancelvote", "CANCEL VOTE", "default"), ("broadcast", "BROADCAST", "primary"),
       ("servername", "RENAME", "default"), ("password", "JOIN PASSWORD", "default"),
       ("maxping", "PING LIMIT", "default"), ("reconnect", "RECONNECT", "default")]
BAN_OPS = [("newban", "NEW BAN", "error"), ("unban", "UNBAN  u", "warning"), ("vpnallow", "VPN ALLOW  a", "default"),
           ("vpnrevoke", "VPN REVOKE  r", "default"), ("vpncheck", "CHECK IP", "default")]
OP_GROUPS = [("MATCH", ["load", "map", "mode", "nextmap", "endround", "endgame"]),
             ("TEAMS & VOTES", ["shuffle", "teamcount", "startvote", "passvote", "cancelvote"]),
             ("SERVER", ["broadcast", "servername", "password", "maxping", "reconnect"])]
PLAYER_OPS = [("tell", "TELL", "t"), ("kick", "KICK", "k"), ("ban", "BAN", "b"), ("mute", "MUTE", "m"),
              ("team", "TEAM", "j"), ("vpnallow", "VPN OK", "v"), ("copy", "COPY ID", "y")]

# What each control does, in plain words: shown when the mouse rests on it.
TIPS = {
    "op-load": "Pick a map and a mode and switch to them now. Ends the current game.",
    "op-map": "Switch to another map, keeping the current mode. Ends the current game.",
    "op-mode": "Switch to another mode, keeping the current map. Ends the current game.",
    "op-nextmap": "Choose what plays after this game, without interrupting it.",
    "op-endround": "End this round as if its time ran out.",
    "op-endgame": "End the whole game now, with the scores as they stand.",
    "op-shuffle": "Mix the players up across the teams.",
    "op-teamcount": "Spread the players over 2 to 8 teams.",
    "op-startvote": "Ask the players to vote: end the round, shuffle, kick someone, and more.",
    "op-passvote": "Pass the vote that's under way, whatever the count.",
    "op-cancelvote": "Call off the vote that's under way.",
    "op-broadcast": "Send a [Server] message to everyone in this or every server.  Ctrl+B",
    "op-servername": "Change the name in the server browser, until the server restarts.",
    "op-password": "Make players type a password to join (or remove it).",
    "op-maxping": "Stop players with a high ping from joining.",
    "op-reconnect": "Drop this connection and sign in again.",
    "bl-newban": "Ban a player ID, an IP address or a whole range.",
    "bl-unban": "Lift the selected ban.  Key: u",
    "bl-vpnallow": "Let a player or address join through a VPN.  Key: a",
    "bl-vpnrevoke": "Take back the selected VPN allowance.  Key: r",
    "bl-vpncheck": "Check whether an address looks like a VPN.",
    "pl-tell": "Send a private message only they can see.  Key: t",
    "pl-kick": "Remove them from the game. They can rejoin after a short wait.  Key: k",
    "pl-ban": "Keep them out, for a while or for good.  Key: b",
    "pl-mute": "Silence their text and voice chat (or lift it).  Key: m",
    "pl-team": "Move them to another team.  Key: j",
    "pl-vpnallow": "Let them join through a VPN.  Key: v",
    "pl-copy": "Copy their player ID, for a ban list or a report.  Key: y",
    "add-server": "Add another server with the setup screen.",
    "f-local": "Show only what happens on the selected server.",
    "raw": "Also print every event the servers push, as raw data.",
}
TAB_TIPS = {"assets": "Players on the selected server: click one for their file and actions.",
            "intercepts": "Live chat, kills, joins and moderation from every server, and a box to talk back.",
            "operations": "Change the map or mode, end rounds, run votes, and server settings.",
            "blacklist": "Bans, and players allowed to join through a VPN.",
            "console": "Type server commands directly. For when a button doesn't cover it."}

# A connection failure in words to act on: (what the error says, card text, the full explanation)
EXPLAIN = [
    (("ssh: connect to host", "could not resolve hostname"), "SSH CAN'T REACH THE BOX",
     "SSH couldn't reach that machine. Check the user@host spelling, and that SSH runs there."),
    (("host key verification",), "SSH DOESN'T KNOW THE BOX",
     "SSH hasn't seen that machine before. Connect once with ssh in a terminal to trust it, then retry."),
    (("permission denied", "publickey"), "SSH REFUSED YOUR KEY",
     "SSH refused your key. Load it into your SSH agent, or name it in ~/.ssh/config."),
    (("no ssh client",), "SSH NOT INSTALLED", "There's no SSH on this computer, so the tunnel can't open."),
    (("connect call failed", "connection refused", "refused the network connection", "errno 111", "10061", "1225"),
     "NOTHING ON THAT PORT",
     "Nothing answered on that port. Is the server running, with an RCON password set in dedicated.toml?"),
    (("name or service not known", "getaddrinfo", "nodename nor servname", "no address associated", "11001"),
     "UNKNOWN HOST", "Can't find that host name. Check the spelling."),
    (("network is unreachable", "no route to host"), "NO ROUTE", "Can't reach that network. Check your connection or VPN."),
    (("timed out", "timeout", "10060", "semaphore"), "NO ANSWER",
     "No answer. Check the address and port, and any firewall in between."),
    (("invalid http", "did not receive a valid http", "rejected websocket", "invalid status", "speak rcon"),
     "NOT AN RCON PORT",
     "Something answered, but it isn't RCON. RCON listens on the game port."),
    (("isn't a valid uri", "invalid uri", "scheme isn't"), "BAD LINK", "That isn't a valid ws:// or wss:// link."),
    (("connection closed", "connection lost", "no close frame"), "CONNECTION DROPPED", "The connection dropped."),
]


def explain(detail: str, state: str = "offline", short: bool = False) -> str:
    """Why a station isn't online, for someone who has never seen a socket error."""
    d = re.sub(r";?\s*retry in \d+s$", "", detail or "").strip()
    low = d.lower()
    if state == "denied":
        if any(k in low for k in ("too many", "locked", "lockout")):
            return "LOCKED OUT" if short else "Locked out after too many wrong passwords. Wait ten minutes, then retry."
        return "PASSWORD REFUSED" if short else ("The server refused the password. Check it against dedicated.toml "
                                                 "[rcon]. Five wrong tries in ten minutes lock you out for a while.")
    for keys, card, text in EXPLAIN:
        if any(k in low for k in keys):
            return card if short else text
    return (d.upper() or "NO CARRIER") if short else d or "Not connected."



# --- reading tolerant: field names come from the docs, so every lookup accepts the plausible spellings ---------
def pick(d, *keys, default=None):
    if isinstance(d, dict):
        for k in keys:
            if d.get(k) not in (None, ""):
                return d[k]
    return default


def num(v):
    """v as a number, or None. A count may come as the list of what it counts."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    return len(v) if isinstance(v, (list, tuple)) else None


def team_of(p: dict) -> str:
    t = pick(p, "team", "team_name", "color")
    if isinstance(t, int) and not isinstance(t, bool) and 0 <= t < len(TEAMS):
        return TEAMS[t]
    return str(t).lower() if t is not None else ""


def target_of(p: dict) -> str:
    """Player ID first: numbers shift when someone leaves between a refresh and a keypress."""
    pid = pick(p, "player_id", "id", "xuid")
    if pid:
        return str(pid)
    n = pick(p, "number", "index")
    return f"#{n}" if n is not None else str(pick(p, "name", default="?"))


def who(v, names: dict) -> str:
    if isinstance(v, dict):
        return str(pick(v, "name", default=names.get(pick(v, "engine_id"), "?")))
    if isinstance(v, int) and not isinstance(v, bool):
        return names.get(v, f"#{v}")
    return str(v) if v not in (None, "") else "?"


def resolve(ev: dict, names: dict) -> dict:
    """A copy of the event that names its players outright. An engine ID is a slot the engine hands to the next
    player to join, so it has to be read against the roster when the event arrives, not when the feed redraws."""
    return {k: who(v, names) if k in REFS and isinstance(v, (int, dict)) and not isinstance(v, bool) else v
            for k, v in ev.items()}


def tally(players: list) -> list[tuple[str, int, int]]:
    """(team, players, total score) per team, in the engine's team order; empty when nobody is on a team."""
    teams: dict[str, list] = {}
    for p in players:
        if team := team_of(p):
            entry = teams.setdefault(team, [0, 0])
            entry[0] += 1
            entry[1] += num(pick(p, "score")) or 0
    return [(t, n, s) for t, (n, s) in sorted(teams.items(), key=lambda kv: TEAM_ORDER.get(kv[0], len(TEAMS)))]


def sides(players: list) -> list[tuple[str, int, int]]:
    """The teams, or none in a free-for-all, which can put every player on a team of their own."""
    teams = tally(players)
    return [] if len(teams) > 2 and all(n == 1 for _, n, _ in teams) else teams


def bar(v, width: int = 5) -> Text:
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return Text("—", DIM)
    return gauge(v, width, GREEN if v > .6 else AMBER if v > .3 else RED)


def team_strip(teams: list, width: int = 24) -> Text | None:
    """The score race between the teams as one bar, for under the roster. None without at least two teams."""
    if len(teams) < 2:
        return None
    parts = [(max(score, 0), TEAM_COLOR.get(team, WHITE)) for team, _, score in teams]
    if len(teams) == 2:
        (a, _, sa), (b, _, sb) = teams
        return Text.assemble((f" {a.upper()} {sa} ", parts[0][1]), split_bar(parts, width),
                             (f" {sb} {b.upper()} ", parts[1][1]))
    names = Text("  ").join(Text(f"{t.upper()} {s}", c) for (t, _, s), (_, c) in zip(teams, parts))
    return Text(" ") + names + Text(" ") + split_bar(parts, width) + Text(" ")


def until(ts) -> str:
    if not isinstance(ts, (int, float)):
        return "PERMANENT"
    left = int(ts - time.time())
    if left <= 0:
        return "expired"
    d, h, m = left // 86400, left % 86400 // 3600, left % 3600 // 60
    return f"{d}d {h}h" if d else f"{h}h {m}m" if h else f"{m}m"


def clock(ts) -> str:
    """HH:MM:SS for an event's time, in seconds or milliseconds; now when it has none that reads."""
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        with contextlib.suppress(OverflowError, ValueError, OSError):
            return datetime.fromtimestamp(ts / 1000 if ts > 1e11 else ts).strftime("%H:%M:%S")
    return time.strftime("%H:%M:%S")


def redact_addr(v, on: bool) -> str:
    return "███.███.███.███" if on and v else str(v or "—")


def redact_text(s: str, on: bool) -> str:
    """s with any IPv4 address in it blanked, while addresses are redacted."""
    return IPV4.sub("███.███.███.███", s) if on else s


def redact_data(v, on: bool):
    """A reply or event with every address in it blanked, while addresses are redacted: for showing, not sending."""
    if not on:
        return v
    if isinstance(v, dict):
        return {k: redact_addr(x, on) if k in ADDRESS_KEYS and isinstance(x, str) else redact_data(x, on)
                for k, x in v.items()}
    if isinstance(v, list):
        return [redact_data(x, on) for x in v]
    return redact_text(v, on) if isinstance(v, str) else v


def clip_id(v) -> str:
    """A long ID (32 hex digits) cut to its first 8, for the feed; the dossier and console have it whole."""
    s = str(v)
    return s[:8] + "…" if len(s) > 16 and re.fullmatch(r"[0-9a-fA-F-]+", s) else s


def q(a: str) -> str:
    return f'"{a}"' if not a or " " in a else a


def describe(ev: dict, names: dict, redact: bool = True) -> Text:
    """What an event says, after its time and station. Known kinds get a layout; anything else prints its fields."""
    kind = str(ev.get("event", "?"))
    cat = KIND.get(kind, "ops")
    line = Text()
    if kind == "chat":
        ch = str(ev.get("channel", "all"))
        if ch == "server":
            line.append("[SERVER] ", AMBER)
            line.append(redact_text(str(ev.get("text", "")), redact), AMBER)
        else:
            team = str(pick(ev, "team", default=ch.removeprefix("team").strip())).lower()
            line.append(f"[{ch.upper()}] ", DIM)
            line.append(who(pick(ev, "name", "player", "from", "sender"), names), TEAM_COLOR.get(team, WHITE))
            line.append(": " + redact_text(str(ev.get("text", "")), redact))
    elif kind == "kill":
        killer, victim = pick(ev, "killer"), pick(ev, "victim")
        k, v = who(killer, names), who(victim, names)
        if killer is None or k == v:
            line.append(v, WHITE)
            line.append(" died", DIM)
        else:
            line.append(k, WHITE)
            line.append(" ✕ ", RED)
            line.append(v)
        how = pick(ev, "weapon", "damage", "cause", "how")
        if how:
            line.append(f"  [{how}]", DIM)
        for medal in ev.get("_medals") or ():  # padded with blank braille, not spaces: wrap between pills only
            line.append("  ")
            line.append(f"\u2800{medal}\u2800".replace(" ", "\u2800"), f"{INK} on {GOLD}")
    else:
        line.append(kind.upper(), CATS[cat])
        rest = {k: v for k, v in ev.items() if k not in ("type", "event", "time")}
        name = rest.pop("name", None) or rest.pop("player", None)
        if name is not None:
            line.append(f"  {who(name, names)}", WHITE)
        text = rest.pop("text", None)
        if text:
            line.append(f"  {redact_text(str(text), redact)}")
        for k, v in rest.items():
            v = (redact_addr(v, redact) if k in ADDRESS_KEYS else json.dumps(redact_data(v, redact))
                 if isinstance(v, (dict, list)) else redact_text(clip_id(v), redact))
            line.append(f"  {k}=", DIM)
            line.append(str(v))
    return line


def render_event(label: str, ev: dict, names: dict, redact: bool = True, width: int = 14) -> Text:
    """One feed line for a pushed event: time, station, glyph, then what it says."""
    kind = str(ev.get("event", "?"))
    line = Text(clock(ev.get("time")), DIM)
    line.append(f" {label[:width]:<{width}} ", CYAN)
    line.append(f"{GLYPH.get(kind, '·')} ", CATS[KIND.get(kind, "ops")])
    return line + describe(ev, names, redact)


def vote_text(v) -> Text:
    """The vote under way: what it's on, and the tally as a tug of war when the server gives one."""
    if not v:
        return Text("none under way", DIM)
    if not isinstance(v, dict):
        return Text(json.dumps(v))
    t = Text(str(pick(v, "subject", "type", "kind", default="vote")).upper(), AMBER)
    if target := pick(v, "target", "player", "name"):
        t.append(f" {target}")
    yes, no = num(pick(v, "yes", "for")), num(pick(v, "no", "against"))
    if yes is not None or no is not None:
        yes, no = yes or 0, no or 0
        t += Text.assemble(("\nYES ", DIM), (f"{yes} ", GREEN), split_bar([(yes, GREEN), (no, RED)], 6),
                           (f" {no}", RED), (" NO", DIM))
    return t


@dataclass(eq=False)  # told apart by identity: comparing by value would compare everything they hold
class Station:
    server: Server
    label: str
    rcon: Rcon | None = None
    worker: object = None
    prev: str = ""
    data: dict = field(default_factory=dict)  # last good `data` per read command (status, players, bans, ...)
    inflight: set = field(default_factory=set)
    medals: Medals = field(default_factory=Medals)
    activity: deque = field(default_factory=lambda: deque(maxlen=1000))  # when recent events came in (monotonic)
    alerts: int = 0  # calls for an admin and anti-cheat hits not yet looked at
    flash: float = 0.0  # the card pulses until this monotonic time
    retry_at: float = 0.0  # when the next reconnect attempt goes out
    first_seen: dict = field(default_factory=dict)  # player key -> when their row first appeared
    painted: bool = False  # players drawn once: anyone new after this gets a NEW flag

    @property
    def online(self) -> bool:
        return bool(self.rcon and self.rcon.state == "online")

    @property
    def players(self) -> list:
        return self.data.get("players", {}).get("players") or []

    @property
    def names(self) -> dict:
        return {p.get("engine_id"): str(pick(p, "name", default="?")) for p in self.players if p.get("engine_id") is not None}

    def rate(self, buckets: int, span: float) -> list[int]:
        """Events in each of the last `buckets` stretches of `span` seconds, oldest first."""
        now, out = time.monotonic(), [0] * buckets
        for t in self.activity:
            if 0 <= (i := buckets - 1 - int((now - t) // span)) < buckets:
                out[i] += 1
        return out


# --- dialogs: titles and bodies are Text, because they carry player names and markup in a name must stay a name ---
class Dialog(ModalScreen):
    """A modal that dims the screen behind it as its box rises into place (the -enter transitions in oni.tcss)."""
    DEFAULT_CLASSES = "-enter"

    @staticmethod
    def keys(*hints: str) -> Static:
        return Static(Text("   ·   ".join(hints), DIM), classes="dialog-keys")

    def on_mount(self) -> None:
        self.call_after_refresh(self.remove_class, "-enter")


class Confirm(Dialog):
    BINDINGS = [Binding("escape", "dismiss(False)", show=False)]

    def __init__(self, title: str, body: str, verb: str = "EXECUTE", danger: bool = True):
        super().__init__()
        self.t, self.b, self.verb, self.danger = title, body, verb, danger

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog danger" if self.danger else "dialog"):
            yield Static(Text(self.t), classes="dialog-title")
            yield Static(Text(self.b), classes="dialog-body")
            with Horizontal(classes="dialog-buttons"):
                yield Button("ABORT", id="no")
                yield Button(self.verb, id="yes", variant="error" if self.danger else "primary")
            yield self.keys("Esc cancel", "← → choose", "Enter confirm")

    def on_mount(self) -> None:
        self.query_one("#no").focus()  # the safe choice is the default

    def on_button_pressed(self, e: Button.Pressed) -> None:
        e.stop()
        self.dismiss(e.button.id == "yes")


class Form(Dialog):
    """fields: (key, label, default) where a list default is a Select of (label, value) and anything else an Input.
    Keys in `secret` are typed masked and kept exactly as typed."""
    BINDINGS = [Binding("escape", "dismiss(None)", show=False)]

    def __init__(self, title: str, fields: list, verb: str = "OK", danger: bool = False, note: str = "",
                 required: tuple = (), secret: tuple = ()):
        super().__init__()
        self.t, self.fields, self.verb, self.danger, self.note, self.required = title, fields, verb, danger, note, required
        self.secret = secret

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog danger" if self.danger else "dialog"):
            yield Static(Text(self.t), classes="dialog-title")
            if self.note:
                yield Static(Text(self.note), classes="dialog-body")
            for key, label, default in self.fields:
                yield Label(label + ("  (required)" if key in self.required else ""))
                if isinstance(default, list):
                    yield Select(default, allow_blank=False, id=f"field-{key}")
                else:
                    yield Input(str(default), id=f"field-{key}", password=key in self.secret)
            with Horizontal(classes="dialog-buttons"):
                yield Button("ABORT", id="no")
                yield Button(self.verb, id="yes", variant="error" if self.danger else "primary")
            yield self.keys("Esc cancel", "Tab next field", "Enter " + self.verb.lower())

    def on_mount(self) -> None:
        self.query("Input, Select").first().focus()

    @on(Input.Submitted)
    def _enter(self, e: Input.Submitted) -> None:
        e.stop()
        self.submit()

    def on_button_pressed(self, e: Button.Pressed) -> None:
        e.stop()
        self.submit() if e.button.id == "yes" else self.dismiss(None)

    def submit(self) -> None:
        vals = {}
        for key, *_ in self.fields:
            v = self.query_one(f"#field-{key}").value
            vals[key] = "" if not isinstance(v, str) else v if key in self.secret else v.strip()
        missing = [k for k in self.required if not vals[k]]
        if missing:
            box = self.query_one(f"#field-{missing[0]}")
            box.focus()
            box.add_class("-missing")  # a red flash on the empty box says which one
            self.set_timer(0.6, lambda: box.remove_class("-missing"))
        else:
            self.dismiss(vals)


class Pick(Dialog):
    """A filterable list; returns the chosen value, or None when aborted."""
    BINDINGS = [Binding("escape", "dismiss(None)", show=False), Binding("down", "focus_list", show=False)]

    def __init__(self, title: str, options: list):
        super().__init__()
        self.t, self.opts, self.shown = title, options, options

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog pick"):
            yield Static(Text(self.t), classes="dialog-title")
            if len(self.opts) > 8:
                yield Input(placeholder="type to filter…", id="pick-filter")
            yield OptionList(id="pick-list")
            yield self.keys("Esc cancel", "↑ ↓ move", "Enter or click to choose")

    def on_mount(self) -> None:
        self.fill("")
        self.query("#pick-filter, #pick-list").first().focus()

    def fill(self, text: str) -> None:
        text = text.lower()
        self.shown = [(l, v) for l, v in self.opts
                      if text in (l.plain if isinstance(l, Text) else str(l)).lower() or text in str(v).lower()]
        ol = self.query_one(OptionList)
        ol.clear_options()
        ol.add_options([Option(l if isinstance(l, Text) else Text(str(l)), id=str(i)) for i, (l, _) in enumerate(self.shown)])
        if self.shown:
            ol.highlighted = 0

    def action_focus_list(self) -> None:
        self.query_one(OptionList).focus()

    @on(Input.Changed, "#pick-filter")
    def _filter(self, e: Input.Changed) -> None:
        e.stop()
        self.fill(e.value)

    @on(Input.Submitted, "#pick-filter")
    def _first(self, e: Input.Submitted) -> None:
        e.stop()
        if self.shown:
            self.dismiss(self.shown[self.query_one(OptionList).highlighted or 0][1])

    @on(OptionList.OptionSelected)
    def _chosen(self, e: OptionList.OptionSelected) -> None:
        e.stop()
        self.dismiss(self.shown[int(e.option.id)][1])


GUIDE = [
    ("GETTING AROUND", [
        ("Servers", "Your servers are on the left. Click one, or press 1 to 9. A green ◉ is connected; a red ○ "
                    "says why it isn't, and retries by itself."),
        ("Tabs", "F1 to F5, or click the names along the top."),
        ("Anything", "Ctrl+P opens a searchable list of every action. Rest the mouse on a button to see what it does."),
    ]),
    ("F1  ASSETS  ·  the players", [
        ("Pick a player", "Click a row, or move with ↑ ↓. Their file opens on the right, with buttons to tell, kick, "
                          "ban, mute, move or allow them through a VPN."),
        ("Keys", "t tell  ·  k kick  ·  b ban  ·  m mute  ·  j team  ·  v VPN allow  ·  y copy their ID"),
        ("Flags", "ADM an admin  ·  MUT muted  ·  KIA dead right now  ·  NEW just joined  ·  ★5 on a killing spree"),
        ("Their file", "The glyph is drawn from their player ID: the same player always gets the same one. Medals and "
                       "their streak count what this console has seen since it connected."),
    ]),
    ("F2  INTERCEPTS  ·  what's happening", [
        ("The feed", "Chat, kills, joins, kicks and bans from every server, newest at the bottom. Scroll up to read "
                     "back; it holds still until you press End."),
        ("Talk back", "Type in the box at the bottom to chat as [Server]. Start with @all to reach every server."),
        ("Medals", "Kills carry their Halo 3 medals as they happen: double kill and up, sprees, killjoys."),
        ("Alerts", "Chat asking for an admin, or naming a cheat, pops up, beeps and turns the CONDITION at the top red. "
                   "Servers you aren't looking at show a ⚑ count until you open them or this feed."),
    ]),
    ("F3  OPERATIONS  ·  running the match", [
        ("Buttons", "Change map or mode, queue what plays next, end the round, shuffle teams, run votes, broadcast, "
                    "and server settings. Anything that ends a game asks first."),
    ]),
    ("F4  BLACKLIST  ·  bans", [
        ("Lists", "Every ban by player, address and device, and who may join through a VPN. Pick a row, then UNBAN "
                  "or REVOKE."),
    ]),
    ("F5  CONSOLE  ·  typing commands", [
        ("Commands", "For anything without a button. Type help for the server's list; ↑ ↓ bring back earlier ones."),
    ]),
    ("SAFETY", [
        ("Addresses", "Player IPs are hidden. Press x to show them, and again to hide them before you stream."),
        ("Confirming", "Risky actions ask first, and the safe choice (ABORT) is already selected."),
        ("Passwords", "A refused password is never retried by itself: five wrong tries lock you out. RECONNECT on F3 "
                      "asks for it again."),
    ]),
]


class Help(Dialog):
    BINDINGS = [Binding("escape,question_mark,q", "dismiss", show=False)]

    def compose(self) -> ComposeResult:
        crest = Table.grid(padding=(0, 3))
        crest.add_column()
        crest.add_column(vertical="middle")
        crest.add_row(emblem(8, 16), Text.assemble(("OFFICE OF NAVAL INTELLIGENCE\n", AMBER),
                                                   ("SECTION THREE  ·  REMOTE CONSOLE TERMINAL\n\n", DIM),
                                                   ("Every key, button and readout, in plain words.", WHITE)))
        parts = [crest, Text()]
        for section, rows in GUIDE:
            g = Table.grid(padding=(0, 2))
            g.add_column(style=CYAN, no_wrap=True, width=14)
            g.add_column()
            for k, v in rows:
                g.add_row(k, Text(v, WHITE))
            parts += [Text(section, AMBER), g, Text()]
        with Vertical(classes="dialog help"):
            yield Static("FIELD MANUAL", classes="dialog-title")
            with VerticalScroll(id="help-body"):
                yield Static(Group(*parts))
            yield self.keys("Esc or ? close", "↑ ↓ scroll", "Ctrl+P every action")

    def on_mount(self) -> None:
        self.query_one("#help-body").focus()


class Boot(Screen):
    """The splash: decrypts the title and materialises the emblem while the stations sign in. Any key skips it,
    and F1 to F5 go straight to their tab. With animations off (TEXTUAL_ANIMATIONS) it all appears at once."""
    HEADING = "O F F I C E   O F   N A V A L   I N T E L L I G E N C E"

    def compose(self) -> ComposeResult:
        with Vertical(id="boot"):
            with Center():
                yield Static(id="boot-emblem")
            yield Static(id="boot-title")
            with Center():  # align centres children as one block, and the emblem is full width
                yield Static(id="boot-log")

    def on_mount(self) -> None:
        self.t0, self.done, self.drawn = time.monotonic(), None, None
        # fixed, so the emblem doesn't shift as lines come in: one per tunnel and station, the rest, the progress bar
        self.query_one("#boot-log").styles.height = len(self.app.stations) + len(self.app.tunnels) + 5
        self.set_interval(0.07, self.tick)
        self.tick()

    def on_key(self, e: events.Key) -> None:
        if e.key not in ("f1", "f2", "f3", "f4", "f5"):
            e.stop()  # skipping shouldn't also redact, broadcast or switch station unseen
        self.action_skip()

    @staticmethod
    def dotted(k: str, v: str, color: str) -> Text:
        dots = max(3, min(46 - len(k), 59 - len(k) - len(v)))  # a long value takes from the dots, not the next line
        return Text(f"> {k} ", WHITE) + Text("." * dots, DIM) + Text(f" {v}\n", color)

    def tick(self) -> None:
        app, el = self.app, time.monotonic() - self.t0
        t = el if app.animation_level == "full" else 9.0  # past every effect: drawn whole at once
        # leave room for the title, the log (a line per tunnel and station) and the progress bar
        rows = app.size.height - 14 - len(app.stations) - len(app.tunnels)
        if t < 1.9 or self.drawn != rows:  # materialise, sweep once, then leave it be
            self.query_one("#boot-emblem", Static).update(
                emblem(rows, reveal=t / 0.9, scan=(t - 0.8) / 0.9 if 0.8 < t < 1.7 else None))
            self.drawn = rows
        self.query_one("#boot-title", Static).update(Text.assemble(
            ("\n" + decrypt(self.HEADING, t / 0.8, int(t / 0.07)) + "\n", AMBER),
            ("SECTION THREE  ·  REMOTE CONSOLE TERMINAL  ·  ", DIM), ("TOP SECRET", RED)))

        spin = SPIN[int(el * 8) % 4]
        log = [("AUTHENTICATING OPERATOR", app.by.upper(), AMBER, True),
               ("OPENING SECURE CHANNELS", f"{len(app.stations)} STATION{'S' * (len(app.stations) != 1)}", AMBER, True)]
        for tun in app.tunnels.values():
            v, c = ((f"{spin} OPENING", AMBER) if tun.state == "opening" else ("ESTABLISHED", GREEN)
                    if tun.state == "up" else (explain(tun.detail, short=True), RED))
            log.append((f"SSH TUNNEL {tun.dest.upper()[:34]}", v, c, tun.state != "opening"))
        log += [(f"[{i + 1}] {st.label.upper()[:34]}", *app.link(st, spin)) for i, st in enumerate(app.stations)]
        steps = int(el / 0.22)
        out = Text()
        for k, v, c, _ in log[:steps]:
            out += self.dotted(k, v, c)
        settled = all(ok for *_, ok in log)
        finished = steps > len(log) and settled or el > 15
        if finished:
            ok = any(st.online for st in app.stations)
            out += self.dotted("CLEARANCE", *(("GRANTED", GREEN) if ok else ("PENDING", AMBER) if not settled
                                              else ("NO STATION REACHABLE", RED)))
            self.done = self.done or time.monotonic()
            if time.monotonic() - self.done > 1.1:
                self.action_skip()
        else:  # the prompt blinks while it works
            out += Text("> ", WHITE) + Text("█" if int(el * 3) % 2 == 0 else " ", AMBER) + Text("\n")
        progress = (sum(ok for *_, ok in log[:steps]) + finished) / (len(log) + 1)
        out += Text("\n") + gauge(progress, 56, AMBER) + Text(f" {round(progress * 100):>3}%", DIM)
        self.query_one("#boot-log", Static).update(out)

    def action_skip(self) -> None:
        if self.app.screen is self:
            self.app.pop_screen()
            self.app.welcome()


class Assets(Horizontal):
    BINDINGS = [Binding("t", "app.player('tell')", "Tell"), Binding("k", "app.player('kick')", "Kick"),
                Binding("b", "app.player('ban')", "Ban"), Binding("m", "app.player('mute')", "Mute"),
                Binding("j", "app.player('team')", "Team"), Binding("v", "app.player('vpnallow')", "VPN allow"),
                Binding("y", "app.player('copy')", "Copy ID")]


class Blacklist(Vertical):
    BINDINGS = [Binding("n", "app.bl('newban')", "New ban"), Binding("u", "app.bl('unban')", "Unban"),
                Binding("a", "app.bl('vpnallow')", "VPN allow"), Binding("r", "app.bl('vpnrevoke')", "VPN revoke")]


class Roster(DataTable):
    """A table refreshed in place. Rows stay put, so a refresh never scrolls the view, and the cursor stays with
    the selected row's id when rows come, go or reorder: an unban must hit the entry the operator chose."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.ids: list[str] = []

    @property
    def selected(self) -> str | None:
        return self.ids[self.cursor_row] if 0 <= self.cursor_row < len(self.ids) else None

    def fill(self, rows: list[tuple[str, list]]) -> None:
        """rows: (id, cells) in display order."""
        keep, have = self.selected, self.row_count
        for r, (_, cells) in enumerate(rows):
            if r < have:
                for c, v in enumerate(cells):
                    self.update_cell_at(Coordinate(r, c), v, update_width=True)
            else:
                self.add_row(*cells, key=str(r))
        for r in range(have - 1, len(rows) - 1, -1):
            self.remove_row(str(r))
        self.ids = [i for i, _ in rows]
        if keep in self.ids and self.ids.index(keep) != self.cursor_row:
            self.move_cursor(row=self.ids.index(keep))
        self.refresh()


class Feed(RichLog):
    """A log that follows new lines only while it's scrolled to the bottom, so reading back isn't yanked away, and
    counts what came in meanwhile. Lines written while its tab is hidden wrap to the width it will have: hidden, it
    has no width, and a plain RichLog then wraps everything written to it at 78 columns."""
    unread = 0
    inset = 8  # what the tabs' width loses to the border, padding and scrollbar; measured whenever it's shown

    def watch_scroll_y(self, old: float, new: float) -> None:
        super().watch_scroll_y(old, new)
        if new < old or self.is_vertical_scroll_end:  # only a person scrolls up; the bottom resumes following
            self.auto_scroll = self.is_vertical_scroll_end
            if self.auto_scroll and self.unread:
                self.unread, self.border_subtitle = 0, ""

    def write(self, content, width: int | None = None, expand: bool = False, shrink: bool = True,
              scroll_end: bool | None = None, animate: bool = False):
        tabs = self.app.query_one(TabbedContent).size.width
        if self.size.width:  # as if the scrollbar is there: it will be once the log fills
            self.inset = tabs - self.scrollable_content_region.width + (
                0 if self.show_vertical_scrollbar else self.styles.scrollbar_size_vertical)
        elif width is None and tabs > self.inset:  # as wide as the line, up to what's there once shown
            console = self.app.console
            width = min(measure_renderables(console, console.options, [content]).maximum, tabs - self.inset)
        if not self.auto_scroll:
            self.unread += 1
            self.border_subtitle = f"▼ {self.unread} NEW  ·  End to follow"
        return super().write(content, width, expand, shrink, scroll_end, animate)

    def clear(self):
        self.unread, self.border_subtitle, self.auto_scroll = 0, "", True
        return super().clear()


class CommandInput(Input):
    BINDINGS = [Binding("up", "history(-1)", show=False), Binding("down", "history(1)", show=False)]

    def action_history(self, step: int) -> None:
        app, h = self.app, self.app.history
        if h:
            if app.hpos == len(h):  # leaving what's being typed: keep it for the way back down
                app.draft = self.value
            app.hpos = max(0, min(len(h), app.hpos + step))
            self.value = h[app.hpos] if app.hpos < len(h) else app.draft
            self.cursor_position = len(self.value)


# --- the app ------------------------------------------------------------------------------------------------------
class OniApp(App):
    CSS_PATH = "oni.tcss"
    TITLE = "ONI RCON"
    BINDINGS = [
        Binding("f1", "tab('assets')", "Assets"), Binding("f2", "tab('intercepts')", "Intercepts"),
        Binding("f3", "tab('operations')", "Ops"), Binding("f4", "tab('blacklist')", "Blacklist"),
        Binding("f5", "tab('console')", "Console"), Binding("ctrl+b", "broadcast", "Broadcast"),
        Binding("ctrl+r", "refresh", "Refresh"), Binding("x", "redact", "Redact"),
        Binding("question_mark", "help", "Help"), Binding("slash", "focus_input", "Type", show=False),
        *[Binding(str(i), f"station({i - 1})", show=False) for i in range(1, 10)],
    ]

    def __init__(self, servers: list[Server], by: str, intro: bool = True, updater: Callable[[], str] | None = None,
                 hint: str = ""):
        super().__init__()
        self.by, self.intro, self.updater, self.hint = by, intro, updater, hint
        self.frame, self.lit = 0, set()  # lit: cards mid-pulse
        self.stations = [Station(s, s.name or s.where) for s in servers]
        self.cards = [Static(classes="card") for _ in servers]
        self.sel, self.redact, self.raw_events = 0, True, False
        self.feed: deque = deque(maxlen=3000)
        self.filters, self.local_only = set(CATS), False
        self.tunnels: dict[str, Tunnel] = {}
        self.history: list[str] = []
        self.hpos, self.draft = 0, ""
        self.row_players: dict[str, dict] = {}
        self.ban_rows: dict[str, str] = {}
        self.vpn_rows: dict[str, str] = {}
        self.alert_at = float("-inf")  # when the last alert came in; each station counts its own unseen ones

    @property
    def cur(self) -> Station:
        return self.stations[self.sel]

    def notify(self, message: str, *, markup: bool = False, **kw) -> None:
        super().notify(message, markup=markup, **kw)  # toasts carry chat and server text: never read it as markup

    def compose(self) -> ComposeResult:
        yield Static(id="masthead")
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield ListView(*[ListItem(c) for c in self.cards], id="stations")
                yield Button("+ ADD SERVER", id="add-server", compact=True)
                with Center():
                    yield Static(id="crest")
                yield Static(id="uplink")
            with TabbedContent(id="tabs", initial="assets"):
                with TabPane("[dim]F1[/] ASSETS", id="assets"):
                    with Assets():
                        yield Roster(id="players", cursor_type="row", zebra_stripes=True)
                        with VerticalScroll(id="dossier-box"):
                            yield Static(id="dossier")
                            with Grid(id="player-actions"):
                                for op, label, _ in PLAYER_OPS:
                                    yield Button(label, id=f"pl-{op}", compact=True,
                                                 variant="error" if op in ("kick", "ban") else "default")
                            with Collapsible(title="RAW DATA", id="raw-box"):
                                yield Static(id="dossier-raw")
                with TabPane("[dim]F2[/] INTERCEPTS", id="intercepts"):
                    with Horizontal(id="filters"):
                        for cat in CATS:
                            yield Checkbox(cat.upper(), True, id=f"f-{cat}")
                        yield Checkbox("THIS STATION ONLY", False, id="f-local")
                    yield Feed(id="feed", wrap=True, max_lines=3000)
                    yield Input(id="say", placeholder="» type to chat as [Server] on this server   ·   start with @all "
                                                      "to reach every server")
                with TabPane("[dim]F3[/] OPERATIONS", id="operations"):
                    with Horizontal(id="ops-top"):
                        yield Static(id="sitrep")
                        with Vertical(id="ops-panel"):
                            ops = {op: (label, variant) for op, label, variant in OPS}
                            for group, names in OP_GROUPS:
                                yield Static(group, classes="ops-head")
                                with Grid(classes="ops-grid"):
                                    for op in names:
                                        yield Button(ops[op][0], id=f"op-{op}", variant=ops[op][1], compact=True)
                    with Horizontal(id="ops-bottom"):
                        yield Roster(id="rotation", cursor_type="none", zebra_stripes=True)
                        yield Static(id="theatre")
                with TabPane("[dim]F4[/] BLACKLIST", id="blacklist"):
                    with Blacklist():
                        yield Roster(id="bans", cursor_type="row", zebra_stripes=True)
                        yield Roster(id="vpn", cursor_type="row", zebra_stripes=True)
                        with Horizontal(classes="bar"):
                            for op, label, variant in BAN_OPS:
                                yield Button(label, id=f"bl-{op}", variant=variant)
                with TabPane("[dim]F5[/] CONSOLE", id="console"):
                    yield Feed(id="console-log", wrap=True, max_lines=5000)
                    with Horizontal(id="console-bar"):
                        yield CommandInput(id="cmd", placeholder="command   ·   @all <command> runs on every station   ·   ↑↓ history",
                                           suggester=SuggestFromList(COMMANDS, case_sensitive=False))
                        yield Checkbox("RAW EVENTS", False, id="raw")
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(ONI)
        self.theme = "oni"
        cols = {"#players": ["#", "CALLSIGN", "TEAM", "SCORE", "K", "D", "K/D", "HEALTH", "SHIELD", "FLAGS"],
                "#rotation": ["#", "MAP", "MODE"], "#bans": ["TYPE", "TARGET", "NAME", "REASON", "EXPIRES", "BY"],
                "#vpn": ["ALLOWED THROUGH VPN", "NOTE"]}
        for sel, c in cols.items():
            self.query_one(sel, DataTable).add_columns(*c)
        for sel, title in {"#players": "ASSETS IN THEATRE", "#dossier-box": "DOSSIER", "#feed": "SIGINT FEED",
                           "#sitrep": "SITREP", "#theatre": "THEATRE", "#rotation": "ROTATION", "#bans": "BLACKLIST",
                           "#vpn": "VPN ALLOWANCES", "#console-log": "COMMAND LOG"}.items():
            self.query_one(sel).border_title = title
        for wid, tip in TIPS.items():  # escaped: "[Server]" in a tip is text, not a style
            self.query_one(f"#{wid}").tooltip = escape(tip)
        tabs = self.query_one(TabbedContent)
        for pane, tip in TAB_TIPS.items():
            tabs.get_tab(pane).tooltip = escape(tip)
        self.log_cmd(Text("ONI remote console. Commands go to the selected station; `@all` prefixes run on every "
                          "station; `help` asks the server; `clear` clears this log.", DIM))

        remotes: dict[str, list] = {}
        for st in self.stations:
            if st.server.ssh and not st.server.url:
                remotes.setdefault(st.server.ssh, []).append((st.server.host, st.server.port))
        for dest, r in remotes.items():
            self.tunnels[dest] = Tunnel(dest, r, self.on_tunnel)
            self.run_worker(self.tunnels[dest].run(), group="tunnels", exit_on_error=False)
        for st in self.stations:
            s, ready = st.server, None
            if s.url:
                url = s.url
            elif s.ssh:
                tun = self.tunnels[s.ssh]
                url, ready = f"ws://127.0.0.1:{tun.local[(s.host, s.port)]}", tun.ready
            else:
                url = f"ws://[{s.host}]:{s.port}" if ":" in s.host else f"ws://{s.host}:{s.port}"
            st.rcon = Rcon(url, s.password, self.by, self.on_rcon_event, self.on_rcon_state, ready)
            st.worker = self.run_worker(st.rcon.run(), group="rcon", exit_on_error=False)

        self.set_interval(1, self.tick)
        self.set_interval(3, self.poll_fast)
        self.set_interval(15, self.poll_slow)
        self.set_interval(0.15, self.animate_cards)
        self.paint_all()
        if self.intro:
            self.push_screen(Boot())
        else:
            self.welcome()
        self.on_resize()
        if self.updater:  # a thread of its own rather than a worker: a slow download must not hold up quitting
            threading.Thread(target=self.check_update, daemon=True).start()

    def check_update(self) -> None:
        try:
            msg = self.updater()
        except Exception:  # best effort, and a traceback from a thread would land on top of the screen
            return
        if msg:
            with contextlib.suppress(RuntimeError):  # quit in the meantime
                self.call_from_thread(self.notify, msg, title="UPDATE", timeout=20)

    def welcome(self) -> None:
        """Once the boot screen is gone: a pointer for someone new, if __main__ asked for one."""
        if self.hint:
            self.notify(self.hint, title="WELCOME, OPERATOR", timeout=15)
            self.hint = ""

    def on_resize(self) -> None:
        self.set_class(self.size.width < 140, "-narrow")  # on the app: the boot screen may be the one on top
        self.paint_masthead()
        self.call_after_refresh(self.refit)
        # the sidebar crest takes what the station cards, the add button and the uplink leave, and goes when that's
        # too little
        free = self.size.height - 11 - len(self.tunnels) - 4 * len(self.stations)
        crest = self.query_one("#crest", Static)
        crest.update(art := emblem(min(free, 16), 32))
        crest.display = bool(art.plain)

    def refit(self) -> None:
        """After a resize, once laid out: the panels that shape themselves to the room they have."""
        if self.is_running:
            self.paint_dossier()
            self.paint_ops()

    # --- connections -----------------------------------------------------------------------------------------
    def tunnel_of(self, st: Station) -> Tunnel | None:
        return self.tunnels.get(st.server.ssh) if st.server.ssh and not st.server.url else None

    def link(self, st: Station, spin: str = "◌") -> tuple[str, str, bool]:
        """Where a station's sign-in stands, for the boot log: (word, colour, settled)."""
        state, tun = st.rcon.state if st.rcon else "connecting", self.tunnel_of(st)
        if state == "online":
            return "SECURE", GREEN, True
        if state == "connecting" and not (tun and tun.state == "down"):
            return f"{spin} {'AWAITING TUNNEL' if tun and tun.state != 'up' else 'HANDSHAKE'}", AMBER, False
        return self.why(st), RED, True

    def why(self, st: Station, short: bool = True) -> str:
        """Why a station isn't online, looking through to its SSH tunnel when that's what's holding it up."""
        rc, tun = st.rcon, self.tunnel_of(st)
        if tun and tun.state != "up" and (rc is None or rc.state in ("connecting", "offline")):
            if tun.state == "down":
                return explain(tun.detail, short=short)
            return "OPENING SSH TUNNEL" if short else "Opening the SSH tunnel…"
        if rc is None or rc.state == "connecting":
            return "CONNECTING…" if short else "Connecting…"
        return explain(rc.detail, rc.state, short=short)

    def on_rcon_state(self, rcon: Rcon, state: str, detail: str) -> None:
        if not self.is_running:  # a late reply or event while quitting: the widgets are already gone
            return
        st = next(s for s in self.stations if s.rcon is rcon)
        if state == "online":
            st.medals.new_game()  # what happened while we were away is unknown
            self.relabel()
            self.fetch(st, "status", "players", "maps", "modes", "nextmap", "vote", "bans", "vpn")
            self.log_event(st, {"event": "uplink", "text": f"SECURE  {detail}"})
        elif state == "denied":
            self.log_event(st, {"event": "uplink", "text": f"SIGN-IN REFUSED: {detail}"})
            self.notify(f"{explain(detail, state)}\nNot retried by itself. RECONNECT (F3) asks for the password again.",
                        title=f"{st.label} · SIGN-IN REFUSED", severity="error", timeout=20)
        elif state == "offline" and st.prev == "online":
            self.log_event(st, {"event": "uplink", "text": f"LOST  {detail}"})
        retry = re.search(r"retry in (\d+)s", detail) if state == "offline" else None
        st.retry_at = time.monotonic() + int(retry[1]) if retry else 0.0
        if state != "connecting":
            st.prev = state
        self.paint_card(st)
        self.paint_masthead()

    def on_tunnel(self, tun: Tunnel, state: str, detail: str) -> None:
        if not self.is_running:  # a late reply or event while quitting: the widgets are already gone
            return
        if state == "down":
            self.log_event(None, {"event": "uplink", "text": f"SSH {tun.dest} DOWN  {detail}"})
        for st in self.stations:  # the ones waiting on it say why in its words
            if self.tunnel_of(st) is tun and not st.online:
                self.paint_card(st)
        self.paint_uplink()

    def on_worker_state_changed(self, e) -> None:
        if e.state == WorkerState.ERROR:
            self.notify(repr(e.worker.error), title="INTERNAL FAULT", severity="error", timeout=15)

    def relabel(self) -> None:
        """Server names usually share a community prefix ("Some Clan | OCE ..."): show only what differs."""
        reported = [st.rcon.info.get("server", "") if st.rcon else "" for st in self.stations]
        names = [n for n in reported if n]
        pre = commonprefix(names) if len(set(names)) > 1 else ""
        cut = max(pre.rfind(" "), pre.rfind("|")) + 1
        before = [st.label for st in self.stations]
        for st, n in zip(self.stations, reported):
            if n and not st.server.name:
                st.label = n[cut:].strip(" |·-") or n
        for st in self.stations:
            self.paint_card(st)
        if [st.label for st in self.stations] != before:
            self.repaint_feed()  # the lines so far name stations as they were labelled then

    def reconnect(self, st: Station) -> None:
        st.worker.cancel()
        st.worker = self.run_worker(st.rcon.run(), group="rcon", exit_on_error=False)

    # --- data ------------------------------------------------------------------------------------------------
    def fetch(self, st: Station, *what: str) -> None:
        what = tuple(w for w in what if w not in st.inflight)
        if st.online and what:
            st.inflight.update(what)

            async def go():
                try:
                    await self._fetch(st, what)
                finally:
                    st.inflight.difference_update(what)
            self.run_worker(go(), group="fetch", exit_on_error=False)

    async def _fetch(self, st: Station, what: tuple) -> None:
        replies = await asyncio.gather(*(st.rcon.call(w) for w in what), return_exceptions=True)
        for w, r in zip(what, replies):
            if isinstance(r, dict) and r.get("ok") and isinstance(r.get("data"), dict):
                if w == "status" and r["data"].get("phase") != st.data.get("status", {}).get("phase"):
                    st.medals.new_game()
                st.data[w] = r["data"]
        self.paint(st, what)

    def poll_fast(self) -> None:
        if not self.is_running:  # the 3 s timer can fire once more while quitting
            return
        self.fetch(self.cur, "players", "status")
        if self.query_one(TabbedContent).active == "operations":
            self.fetch(self.cur, "vote")

    def poll_slow(self) -> None:
        for st in self.stations:
            self.fetch(st, "status", "players")

    async def cmd(self, st: Station, command: str, *args: str, toast: bool = True, show_data: bool = False) -> dict:
        """Run one command, write it to the command log (the audit trail), toast the result."""
        try:
            r = await st.rcon.call(command, *args)
        except Exception as e:
            r = {"ok": False, "text": str(e) or type(e).__name__}
        ok = bool(r.get("ok"))
        text = redact_text(str(r.get("text") or ("done" if ok else "failed")), self.redact)
        self.log_cmd(Text(time.strftime("%H:%M:%S "), DIM) + Text(f"{st.label} ", CYAN)
                     + Text(redact_text(f"» {' '.join([command, *map(q, args)])}", self.redact), WHITE))
        self.log_cmd(Text(f"  {text}", GREEN if ok else RED))
        if show_data and r.get("data") is not None:
            self.log_cmd(JSON.from_data(redact_data(r["data"], self.redact)))
        if toast or not ok:
            self.notify(text, title=f"{st.label} · {command.upper()}", severity="information" if ok else "error")
        return r

    def send(self, st: Station, command: str, *args: str, then: tuple = (), **kw) -> None:
        async def go():
            r = await self.cmd(st, command, *args, **kw)
            if then and r.get("ok"):
                await self._fetch(st, then)
        self.run_worker(go(), group="cmd", exit_on_error=False)

    def on_rcon_event(self, rcon: Rcon, ev: dict) -> None:
        if not self.is_running:  # a late reply or event while quitting: the widgets are already gone
            return
        st = next(s for s in self.stations if s.rcon is rcon)
        if self.raw_events:
            self.log_cmd(Text(f"{st.label} ◂ ", CYAN) + Text(json.dumps(redact_data(ev, self.redact)), DIM))
        names = st.names
        ev = resolve(ev, names)
        kind, text = ev.get("event"), str(ev.get("text", ""))
        if kind == "kill":
            killer = pick(ev, "killer")
            ev["_medals"] = st.medals.kill(None if killer is None else who(killer, names),
                                           who(pick(ev, "victim"), names), time.monotonic())
        st.activity.append(time.monotonic())
        self.log_event(st, ev)
        if kind == "chat" and ev.get("channel") != "server" and ALERT.search(text):
            self.notify(redact_text(f"{who(pick(ev, 'name', 'player'), names)}: {text}", self.redact),
                        title=f"CALL FOR ADMIN · {st.label}", severity="warning", timeout=12)
            self.alert(st)
        elif kind == "cheat":
            self.notify(describe(ev, names, self.redact).plain, title=f"ANTI-CHEAT · {st.label}", severity="error",
                        timeout=12)
            self.alert(st)
        if kind in ("join", "leave", "refused", "kick", "ban", "mute", "unmute", "control"):
            self.fetch(st, "players", "status")
        if kind in ("ban", "unban"):
            self.fetch(st, "bans")
        if kind == "vote":
            self.fetch(st, "vote")
        if kind == "control":
            self.fetch(st, "nextmap")

    def alert(self, st: Station) -> None:
        """Beep, pulse the station's card, and raise the condition. It counts as unseen, on the card and in the
        masthead, unless the operator is looking at that station or at the feed."""
        self.bell()
        st.flash = self.alert_at = time.monotonic()
        st.flash += 3
        if st is not self.cur and self.query_one(TabbedContent).active != "intercepts":
            st.alerts += 1
        self.paint_masthead()

    @property
    def unseen(self) -> int:
        return sum(st.alerts for st in self.stations)

    def condition(self) -> tuple[str, str]:
        """RED while an alert is unseen and for a moment after any; AMBER while a station is down; else GREEN."""
        if self.unseen or time.monotonic() - self.alert_at < 15:
            return "RED", RED
        if not all(st.online for st in self.stations):
            return "AMBER", AMBER
        return "GREEN", GREEN

    def log_event(self, st: Station | None, ev: dict) -> None:
        self.feed.append((st, ev))
        if self.shown(st, ev):
            self.query_one("#feed", RichLog).write(self.feed_line(st, ev))

    def shown(self, st: Station | None, ev: dict) -> bool:
        return KIND.get(ev.get("event"), "ops") in self.filters and not (self.local_only and st not in (None, self.cur))

    def feed_line(self, st: Station | None, ev: dict) -> Text:
        width = max(4, min(18, max(len(s.label) for s in self.stations)))
        return render_event(st.label if st else "LINK", ev, {}, self.redact, width)  # resolved when it came in

    # --- painting --------------------------------------------------------------------------------------------
    def paint(self, st: Station, what: tuple) -> None:
        if not self.is_running:  # a late reply or event while quitting: the widgets are already gone
            return
        self.paint_card(st)
        if st is not self.cur:
            return
        if "players" in what:
            self.paint_players()
        if {"status", "nextmap", "vote", "players"} & set(what):
            self.paint_ops()
        if {"bans", "vpn"} & set(what):
            self.paint_bans()

    def paint_all(self, feed: bool = True) -> None:
        for st in self.stations:
            self.paint_card(st)
        self.paint_players()
        self.paint_ops()
        self.paint_bans()
        self.paint_uplink()
        self.paint_masthead()
        if feed:
            self.repaint_feed()

    def tick(self) -> None:
        """Once a second: the clock, the condition, and the activity traces."""
        if not self.is_running:  # the 1 s timer can fire once more while quitting
            return
        self.paint_masthead()
        for st in self.stations:
            self.paint_card(st)
        rate = sum(sum(st.rate(1, 60)) for st in self.stations)
        live = any(st.online for st in self.stations)
        self.query_one("#feed").border_title = Text.assemble(
            ("SIGINT FEED  ", AMBER),
            ("●" if live else "○", RED if live and time.time() % 2 < 1 else blend(RED, INK, .4) if live else DIM),
            (f" LIVE · {rate}/min" if live else " NO SIGNAL", DIM))

    def paint_masthead(self) -> None:
        cond, color = self.condition()
        if cond == "RED" and time.time() % 2 < 1:  # blink
            color = blend(RED, INK, .55)
        online, unseen = sum(st.online for st in self.stations), self.unseen
        assets = sum(num(st.data.get("status", {}).get("players")) or 0 for st in self.stations if st.online)
        wide = self.size.width >= 140  # else just the essentials, so nothing gets cut mid-word
        g = Table.grid(expand=True)
        for j in ("left", "center", "right"):
            g.add_column(justify=j, no_wrap=True)
        g.add_row(Text("▲ ", AMBER) + Text("OFFICE OF NAVAL INTELLIGENCE" if wide else "ONI", AMBER)
                  + Text("  ·  SECTION III" if self.size.width >= 160 else "", DIM),
                  Text.assemble((f" CONDITION {cond}" + (f" · ⚑ {unseen}" if unseen else "") + " ",
                                 f"{INK} on {color}"),
                                (f"  {online}/{len(self.stations)} {'STATIONS SECURE' if wide else 'SECURE'}  ·  "
                                 f"{assets} ASSETS", CYAN if online else RED)),
                  Text("TOP SECRET // " if wide else "", RED) + Text(f"OPERATOR {self.by.upper()}  ")
                  + Text(time.strftime("%H:%M:%S"), DIM))
        self.query_one("#masthead", Static).update(g)

    def paint_card(self, st: Station) -> None:
        i = self.stations.index(st)
        s = st.data.get("status", {})
        state = st.rcon.state if st.rcon else "connecting"
        glyph, color = STATE[state]
        if self.animation_level != "none":
            if state == "connecting":
                glyph = SPIN[self.frame % 4]
            elif state == "online" and self.frame % 20 < 2:
                color = "#B4F5D0"  # a heartbeat: brighter for a moment every few seconds
        g = Table.grid(expand=True, padding=(0, 1), pad_edge=False)
        g.add_column(no_wrap=True, overflow="ellipsis", ratio=1)
        g.add_column(justify="right", no_wrap=True)
        n, mx = num(s.get("players")), num(s.get("max_players"))
        count = Text(f"{n}/{mx}" if st.online and n is not None else "", AMBER if n else DIM)
        g.add_row(Text(f"{glyph} ", color) + Text(f"{i + 1}  {st.label.upper()}", WHITE),
                  Text.assemble((f"⚑ {st.alerts}  ", RED), count) if st.alerts else count)
        if not st.online:  # why, in words to act on, and when it tries again
            left = st.retry_at - time.monotonic()
            g.add_row(Text("   " + self.why(st), color if state != "connecting" else AMBER),
                      Text(f"↻ {int(left) + 1}s", DIM) if left > 0 else Text("F3 ↻", DIM) if state == "denied" else Text())
            self.cards[i].update(g)
            return
        g.add_row(Text("   " + " · ".join(str(x).replace("_", " ") for x in (s.get("map"), s.get("mode")) if x), DIM),
                  Text(str(s.get("phase") or "").replace("_", " ").upper(), GREEN if s.get("phase") == "in_game" else DIM))
        full = n / mx if n is not None and mx else 0
        # how full it is, then what's been happening there, in 5 s steps across what the card has room for
        steps = max(4, (self.cards[i].size.width or 33) - 13)
        self.cards[i].update(Group(g, Text("   ") + gauge(full, 8, RED if full >= 1 else AMBER if full >= .75 else GREEN)
                                   + Text("  ") + spark(st.rate(steps, 5))))

    def animate_cards(self) -> None:
        """The 0.15 s frame: spinners, the online heartbeat, retry countdowns, and alert pulses."""
        if not self.is_running:
            return
        self.frame += 1
        now, moving = time.monotonic(), self.animation_level != "none"
        for i, (st, card) in enumerate(zip(self.stations, self.cards)):
            pulsing = moving and st.flash > now
            if pulsing:  # red swelling and fading, about once a second
                card.parent.styles.background = Color.parse(RED).with_alpha(0.1 + 0.3 * abs(self.frame % 6 - 3) / 3)
                self.lit.add(i)
            elif i in self.lit:
                card.parent.styles.clear_rule("background")
                self.lit.discard(i)
                pulsing = True  # one last repaint, back to the stylesheet's colour
            if pulsing:
                card.notify_style_update()  # the card caches its parent's colour; a CSS transition can't do this
            state = st.rcon.state if st.rcon else "connecting"
            if (pulsing or moving and (state == "connecting" or state == "online" and self.frame % 20 in (0, 2))
                    or state != "online" and self.frame % 7 == 0):
                self.paint_card(st)

    def paint_uplink(self) -> None:
        t = Text.assemble(("OPERATOR  ", DIM), (self.by, AMBER), "\n")
        for dest, tun in self.tunnels.items():
            glyph, color = {"up": ("◉", GREEN), "opening": ("◌", AMBER)}.get(tun.state, ("○", RED))
            t.append(f"SSH {dest}  ", DIM)
            t.append(f"{glyph} {tun.state.upper()}\n", color)
        t.append("ADDRESSES ", DIM)
        t.append("REDACTED" if self.redact else "VISIBLE", RED if self.redact else GREEN)
        self.query_one("#uplink", Static).update(t)

    def paint_players(self) -> None:
        st, now = self.cur, time.monotonic()
        players = sorted(st.players, key=lambda p: (TEAM_ORDER.get(team_of(p), len(TEAMS)), team_of(p),
                                                    -(num(pick(p, "score")) or 0)))
        self.row_players, rows = {}, []
        for i, p in enumerate(players):
            team, name = team_of(p), str(pick(p, "name", default="?"))
            k, d = pick(p, "kills"), pick(p, "deaths")
            kd = f"{k / max(d, 1):.2f}" if isinstance(k, int) and isinstance(d, int) else "—"
            flags = Text()
            if (spree := st.medals.spree.get(name, 0)) >= SPREE:
                flags.append(f"★{spree} ", GOLD)
            if p.get("admin"):
                flags.append("ADM ", AMBER)
            if p.get("muted"):
                flags.append("MUT ", RED)
            if p.get("alive") is False:
                flags.append("KIA ", DIM)
            key = target_of(p)
            key = key if key not in self.row_players else f"{key}~{i}"
            self.row_players[key] = p
            if now - st.first_seen.setdefault(key, now if st.painted else 0.0) < 12:
                flags.append("NEW", GREEN)
            rows.append((key, [str(pick(p, "number", default="")), Text(name, TEAM_COLOR.get(team, WHITE)),
                               Text(team.upper() or "—", TEAM_COLOR.get(team, DIM)), str(pick(p, "score", default="—")),
                               str(k if k is not None else "—"), str(d if d is not None else "—"), kd,
                               bar(p.get("health")), bar(p.get("shields")), flags]))
        t = self.query_one("#players", Roster)
        t.fill(rows)
        st.painted = st.painted or "players" in st.data  # everyone here at the first look isn't news
        mx = st.data.get("players", {}).get("max_players") or st.data.get("status", {}).get("max_players")
        t.border_title = Text(f"ASSETS IN THEATRE · {len(players)}/{mx or '?'}")
        t.border_subtitle = team_strip(sides(players))
        self.paint_dossier()

    def cur_player(self) -> dict | None:
        return self.row_players.get(self.query_one("#players", Roster).selected)

    def paint_dossier(self) -> None:
        box, p = self.query_one("#dossier", Static), self.cur_player()
        self.query_one("#player-actions").display = self.query_one("#raw-box").display = bool(p)
        if not p:
            if self.cur.online:
                msg = Text.assemble(("NO ASSETS IN THEATRE\n\n", DIM),
                                    ("Nobody is playing on this server right now.\nPlayers appear here as they join.", DIM))
            else:
                msg = Text.assemble(("STATION OFFLINE\n\n", RED), (self.why(self.cur, short=False), WHITE),
                                    ("\n\nIt retries by itself. To retry now: F3, then RECONNECT.", DIM))
            box.update(Text("\n\n") + msg)
            box.styles.text_align = "center"
            return
        box.styles.text_align = "left"
        self.query_one("#pl-mute", Button).label = "UNMUTE" if p.get("muted") else "MUTE"
        team, name = team_of(p), str(pick(p, "name", default="?"))
        color = TEAM_COLOR.get(team, WHITE)
        k, d = pick(p, "kills"), pick(p, "deaths")
        spree, earned = self.cur.medals.spree.get(name, 0), self.cur.medals.earned.get(name)

        def facts(rows: list) -> Table:
            g = Table.grid(padding=(0, 2))
            g.add_column(style=DIM, no_wrap=True)
            g.add_column()
            for a, b in rows:
                g.add_row(a, b if isinstance(b, Text) else Text(str(b)))
            return g
        glyph, head = biosig(str(pick(p, "player_id", "id", default=name)), color), facts([
            ("CALLSIGN", Text(name, color)),
            ("SERVICE TAG", pick(p, "service_tag", "tag", default="—")),
            ("TEAM", Text(team.upper() or "—", TEAM_COLOR.get(team, DIM))),
            ("STATUS", " · ".join(["ALIVE" if p.get("alive") else "KIA" if p.get("alive") is False else "—"]
                                  + ["ADMIN"] * bool(p.get("admin")) + ["MUTED"] * bool(p.get("muted")))),
            ("SCORE", f"{pick(p, 'score', default='—')}    K {k if k is not None else '—'} / D {d if d is not None else '—'}"),
            ("STREAK", Text(f"★ {spree} without dying", GOLD) if spree >= SPREE
             else Text(f"{spree} without dying") if spree else Text("—", DIM))])
        if self.query_one("#dossier-box").content_size.width in range(1, 46):  # narrow: the glyph above the file
            head = Group(glyph, Text(), head)
        else:
            side = Table.grid(padding=(0, 2))
            side.add_row(glyph, head)
            head = side
        rows = [("PLAYER ID", pick(p, "player_id", "id", default="—")),
                ("ADDRESS", Text(redact_addr(pick(p, "address", "ip"), self.redact), RED if self.redact else WHITE)),
                ("HEALTH", bar(p.get("health"), 14)), ("SHIELDS", bar(p.get("shields"), 14)),
                ("LAST DEATH", f"{pick(p, 'seconds_since_last_death')}s ago"
                 if isinstance(pick(p, "seconds_since_last_death"), (int, float)) else "—")]
        guests = pick(p, "guest_players", "guests")
        if guests:
            rows.append(("GUESTS", str(len(guests) if isinstance(guests, list) else guests)))
        if earned:
            rows.append(("MEDALS", Text(" · ".join(f"{m} ×{n}" if n > 1 else m for m, n in earned.most_common()), GOLD)))
        box.update(Group(Text("PERSONNEL FILE", AMBER) + Text("  //  CLASSIFIED", RED), Text(),
                         head, Text(), facts(rows)))
        self.query_one("#dossier-raw", Static).update(JSON.from_data(redact_data(p, self.redact)))

    def paint_ops(self) -> None:
        st = self.cur
        s, vote = st.data.get("status", {}), st.data.get("vote", {})
        info = st.rcon.info if st.rcon else {}
        yes_no = lambda v, on="ON", off="OFF": Text(on, GREEN) if v else Text(off, DIM) if v is not None else Text("—", DIM)
        g = Table.grid(padding=(0, 2))
        g.add_column(style=DIM, no_wrap=True)
        g.add_column()
        for a, b in [("STATION", Text(str(s.get("name") or info.get("server") or st.label), AMBER)),
                     ("UPLINK", f"{st.server.where}   v{info.get('version', '?')}"),
                     ("PUBLIC ADDRESS", Text(redact_addr(s.get("address"), self.redact), RED if self.redact else WHITE)),
                     ("PHASE", Text(str(s.get("phase") or "—").replace("_", " ").upper(), CYAN)),
                     ("MAP", str(s.get("map") or "—")), ("MODE", str(s.get("mode") or "—")),
                     ("NEXT", str(s.get("next") or "playlist")),
                     ("PLAYERS", f"{s.get('players', '—')} / {s.get('max_players', '—')}"),
                     ("JOIN PASSWORD", yes_no(s.get("password_required"), "SET", "OPEN")),
                     ("PING LIMIT", f"{s['max_ping']} ms" if s.get("max_ping") else "off"),
                     ("ANTI-CHEAT", f"{s.get('anti_cheat', '—')}" + ("  (active)" if s.get("anti_cheat_active") else "")),
                     ("VPN BLOCK", yes_no(s.get("block_vpn"))), ("TEXT CHAT", yes_no(s.get("text_chat"))),
                     ("VOICE", yes_no(s.get("voice"))), ("HIDDEN", yes_no(s.get("hidden"), "YES", "NO")),
                     ("VOTE", vote_text(vote.get("vote")))]:
            g.add_row(a, b if isinstance(b, Text) else Text(str(b)))
        self.query_one("#sitrep", Static).update(g)
        voting = bool(vote.get("vote")) if "vote" in st.data else None  # unknown until the first fetch: leave on
        for op, *_ in OPS:  # greyed out when it can't work right now, so nobody wonders why nothing happened
            off = (op != "reconnect" and not st.online or op in ("passvote", "cancelvote") and voting is False
                   or op == "startvote" and voting is True)
            self.query_one(f"#op-{op}", Button).disabled = off
        self.paint_theatre()
        here = str(s.get("map") or "").lower().replace(" ", "_")
        rows, marked = [], False
        for i, e in enumerate(st.data.get("nextmap", {}).get("rotation") or [], 1):
            mp, md = str(pick(e, "map", "base_map", default="?")), str(pick(e, "mode", "game", default="?"))
            now = not marked and mp.lower().replace(" ", "_") == here
            marked |= now
            style = AMBER if now else ""
            rows.append((str(i), [Text("▶" if now else str(i), style), Text(mp, style), Text(md, style)]))
        self.query_one("#rotation", Roster).fill(rows)

    def paint_theatre(self) -> None:
        """Who's winning: each team's numbers and score as bars, or a leaderboard when there are no teams."""
        st = self.cur
        players, teams = st.players, sides(st.players)
        width = max(8, self.query_one("#theatre").content_size.width - 26)  # what the names and numbers leave the bar
        g = Table.grid(padding=(0, 2))
        if not players:
            self.query_one("#theatre", Static).update(Text("NO ASSETS IN THEATRE" if st.online else "STATION OFFLINE",
                                                           DIM))
            return
        if teams:
            top = max(max(s, 0) for *_, s in teams) or 1
            for team, n, score in teams:
                c = TEAM_COLOR.get(team, WHITE)
                g.add_row(Text(team.upper(), c), Text(f"{n} ON FIELD", DIM), Text(str(score), c),
                          hbar(max(score, 0) / top, width, c))
        else:  # free for all: the five best
            best = sorted(players, key=lambda p: -(num(pick(p, "score")) or 0))[:5]
            top = max(num(pick(best[0], "score")) or 0, 1)
            for p in best:
                score = num(pick(p, "score")) or 0
                g.add_row(Text(str(pick(p, "name", default="?")), WHITE), Text(""), Text(str(score), AMBER),
                          hbar(max(score, 0) / top, width, AMBER))
        leaders = sorted(players, key=lambda p: -(num(pick(p, "score")) or 0))[:3]
        lines = [Text("TOP GUNS  ", DIM) + Text(" · ").join(
            Text(f"{pick(p, 'name', default='?')} {pick(p, 'score', default=0)}",
                 TEAM_COLOR.get(team_of(p), WHITE)) for p in leaders)]
        sprees = sorted(((n, name) for name, n in st.medals.spree.items() if n >= SPREE), reverse=True)
        if sprees:
            lines.append(Text("ON A SPREE  ", DIM) + Text(" · ").join(Text(f"{name} ★{n}", GOLD)
                                                                    for n, name in sprees[:4]))
        self.query_one("#theatre", Static).update(Group(g, *lines))

    def paint_bans(self) -> None:
        b, v = self.cur.data.get("bans", {}), self.cur.data.get("vpn", {})
        rows, self.ban_rows = [], {}
        for kind, color, lst in (("PLAYER", AMBER, b.get("players")), ("IP", RED, b.get("ips")),
                                 ("DEVICE", CYAN, b.get("devices"))):
            for e in lst or []:
                target = str(pick(e, "id", "player_id", "ip", "range", "address", "device", "fingerprint", default="?"))
                key = f"{kind}:{target}"
                key = key if key not in self.ban_rows else f"{key}~{len(self.ban_rows)}"
                self.ban_rows[key] = target
                shown = redact_addr(target, self.redact) if kind == "IP" else target
                left = until(pick(e, "expires", "until"))
                rows.append((key, [Text(kind, color), shown, str(pick(e, "name", default="")),
                                   str(pick(e, "reason", default="")),
                                   Text(left, RED if left == "PERMANENT" else DIM if left == "expired" else AMBER),
                                   str(pick(e, "by", "group", default=""))]))
        t = self.query_one("#bans", Roster)
        t.fill(rows)
        t.border_title = f"BLACKLIST · {len(rows)}"
        rows, self.vpn_rows = [], {}
        for e in v.get("allowed") or []:
            target = str(pick(e, "target", "id", "player_id", "ip", "range", "address", default=e))
            key = target if target not in self.vpn_rows else f"{target}~{len(self.vpn_rows)}"
            self.vpn_rows[key] = target
            rows.append((key, [redact_text(target, self.redact), str(pick(e, "note", default=""))]))
        t = self.query_one("#vpn", Roster)
        t.fill(rows)
        t.border_title = Text(f"VPN ALLOWANCES · blocking {'ON' if v.get('block_vpn') else 'OFF'}"
                              + (f" · {v['ranges']} ranges" if isinstance(v.get("ranges"), int) else ""))
        for op, *_ in BAN_OPS:
            self.query_one(f"#bl-{op}", Button).disabled = not self.cur.online or (
                op == "unban" and not self.ban_rows or op == "vpnrevoke" and not self.vpn_rows)

    def repaint_feed(self) -> None:
        log = self.query_one("#feed", RichLog)
        log.clear()
        for st, ev in list(self.feed)[-1000:]:
            if self.shown(st, ev):
                log.write(self.feed_line(st, ev))

    def log_cmd(self, renderable) -> None:
        self.query_one("#console-log", RichLog).write(renderable)

    # --- input -----------------------------------------------------------------------------------------------
    @on(ListView.Highlighted, "#stations")
    def _station(self, e: ListView.Highlighted) -> None:
        if e.list_view.index is not None and e.list_view.index != self.sel:
            self.sel = e.list_view.index
            self.cur.alerts = 0  # looked at now
            for t in self.query(Roster):
                if t.row_count:
                    t.move_cursor(row=0)  # another station's lists: start at the top
            self.paint_all(feed=self.local_only)
            self.fetch(self.cur, "status", "players", "nextmap", "vote", "bans", "vpn")

    @on(DataTable.RowHighlighted, "#players")
    def _row(self, _) -> None:
        self.paint_dossier()

    @on(TabbedContent.TabActivated)
    def _tab(self, e: TabbedContent.TabActivated) -> None:
        if e.pane.id == "intercepts" and self.unseen:  # every station's alerts are in the feed: seen now
            for st in self.stations:
                st.alerts = 0
            self.paint_all(feed=False)

    @on(TabbedContent.TabActivated)
    def _slide_in(self, e: TabbedContent.TabActivated) -> None:
        """The new tab's content glides in from the right. A slide, not a fade: opacity changes get cached into the
        logs' rendered lines and leave them dim."""
        pane = e.pane
        pane.add_class("-from")  # jumps there: only -slide carries the transition

        def glide() -> None:
            pane.add_class("-slide").remove_class("-from")
            self.set_timer(0.3, lambda: pane.remove_class("-slide"))
        self.call_after_refresh(glide)

    @on(Checkbox.Changed)
    def _check(self, e: Checkbox.Changed) -> None:
        cid = e.checkbox.id or ""
        if cid == "raw":
            self.raw_events = e.value
        elif cid == "f-local":
            self.local_only = e.value
            self.repaint_feed()
        elif cid.startswith("f-"):
            (self.filters.add if e.value else self.filters.discard)(cid[2:])
            self.repaint_feed()

    @on(Input.Submitted, "#say")
    def _say(self, e: Input.Submitted) -> None:
        text, e.input.value = e.value.strip(), ""
        targets = self.stations if text.startswith("@all ") else [self.cur]
        text = text.removeprefix("@all ").strip()
        live = [st for st in targets if st.online]
        for st in live:
            if text:
                self.send(st, "say", text, toast=False)
        if text and not live:
            self.notify("Not connected: nothing was sent.", severity="warning")

    @on(Input.Submitted, "#cmd")
    def _command(self, e: Input.Submitted) -> None:
        line, e.input.value = e.value.strip(), ""
        if not line:
            return
        if line not in self.history[-1:]:
            self.history.append(line)
        self.hpos, self.draft = len(self.history), ""
        self.query_one("#console-log", RichLog).scroll_end(animate=False)  # you'll want to see the reply
        if line == "clear":
            self.query_one("#console-log", RichLog).clear()
            return
        targets = self.stations if line.startswith("@all ") else [self.cur]
        try:
            parts = shlex.split(line.removeprefix("@all "))
        except ValueError as err:
            self.log_cmd(Text(f"  {err}", RED))
            return
        for st in targets:
            self.send(st, parts[0], *parts[1:], toast=False, show_data=True)

    @on(Button.Pressed)
    def _button(self, e: Button.Pressed) -> None:
        bid = e.button.id or ""
        if bid == "op-broadcast":
            self.action_broadcast()
        elif bid.startswith("op-"):
            self.op(bid[3:])
        elif bid.startswith("bl-"):
            self.bl(bid[3:])
        elif bid.startswith("pl-"):
            self.action_player(bid[3:])
        elif bid == "add-server":
            self.action_add_server()

    def action_tab(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab
        w = self.query_one(PANE_FOCUS[tab])
        if w.disabled:  # a greyed-out button can't take focus: the pane's first one that can, else the server list
            w = next(iter(self.query_one(f"#{tab}").query("Button:enabled")), self.query_one("#stations"))
        w.focus()  # else focus left in the old pane pulls the tabs back to it

    def action_help(self) -> None:
        if not isinstance(self.screen, Help):
            self.push_screen(Help())

    @work(exclusive=True, group="dialog")
    async def action_add_server(self) -> None:
        if await self.push_screen_wait(Confirm(
                "ADD A SERVER", "Opens the setup screen. Connections close while you're there and reopen when you "
                                "come back.", verb="OPEN SETUP", danger=False)):
            self.exit("setup")


    def action_station(self, i: int) -> None:
        if i < len(self.stations):
            self.query_one("#stations", ListView).index = i

    def action_focus_input(self) -> None:
        tabs = self.query_one(TabbedContent)
        if tabs.active != "intercepts":
            tabs.active = "console"
        self.query_one("#say" if tabs.active == "intercepts" else "#cmd").focus()

    def action_refresh(self) -> None:
        self.fetch(self.cur, "status", "players", "maps", "modes", "nextmap", "vote", "bans", "vpn")
        for st in self.stations:
            self.fetch(st, "status")

    def action_redact(self) -> None:
        self.redact = not self.redact
        self.paint_all()
        self.notify("Addresses redacted." if self.redact else "Addresses visible. Mind your stream.",
                    title="REDACTION", severity="information" if self.redact else "warning")

    def get_system_commands(self, screen):
        yield from super().get_system_commands(screen)
        for i, st in enumerate(self.stations):  # the palette reads titles as markup, and labels come from servers
            yield SystemCommand(escape(f"Station {i + 1}: {st.label}"), escape(st.server.where),
                                lambda i=i: self.action_station(i))
        for op, label, _ in OPS:
            if op == "broadcast":
                yield SystemCommand("Broadcast to stations", "say on this or every station", self.action_broadcast)
            else:
                yield SystemCommand(f"Ops: {label.title()}", f"{op} on the selected station", lambda op=op: self.op(op))
        for op, label, _ in BAN_OPS:
            yield SystemCommand(f"Blacklist: {label.split('  ')[0].title()}", op, lambda op=op: self.bl(op))
        yield SystemCommand("Toggle address redaction", "hide or show player IPs", self.action_redact)
        yield SystemCommand("Help: field manual", "what everything does, in plain words  (?)", self.action_help)
        yield SystemCommand("Add a server", "open the setup screen", self.action_add_server)

    # --- actions with dialogs (workers, so they can await the dialog) -----------------------------------------
    @work(exclusive=True, group="dialog")
    async def action_broadcast(self) -> None:
        f = await self.push_screen_wait(Form("BROADCAST", [("text", "Message ([Server] line in chat)", ""),
                                                           ("scope", "Transmit to", [("Every station", "all"),
                                                                                     (f"{self.cur.label} only", "one")])],
                                             verb="TRANSMIT", required=("text",)))
        if f:
            targets = [st for st in (self.stations if f["scope"] == "all" else [self.cur]) if st.online]
            for st in targets:
                self.send(st, "say", f["text"], toast=False)
            self.notify(f"Transmitted to {len(targets)} station(s)." if targets else "Not connected: nothing was sent.",
                        title="BROADCAST", severity="information" if targets else "warning")

    @work(exclusive=True, group="dialog")
    async def action_player(self, what: str) -> None:
        st, p = self.cur, self.cur_player()
        if not p:
            self.notify("No asset selected.", severity="warning")
            return
        t, name = target_of(p), str(pick(p, "name", default="?"))
        opt = lambda v: [v] if v else []
        if what == "copy":
            self.copy_to_clipboard(t)
            self.notify(t, title="PLAYER ID COPIED")
        elif what == "tell":
            f = await self.push_screen_wait(Form(f"TELL · {name}", [("text", "Message (only they see it)", "")],
                                                 verb="SEND", required=("text",)))
            if f:
                self.send(st, "tell", t, f["text"])
        elif what == "kick":
            f = await self.push_screen_wait(Form(f"KICK · {name}", [("reason", "Reason (they read it)", "")],
                                                 verb="KICK", danger=True,
                                                 note="Repeat kicks block rejoining for 10, 30, 60, then 120 minutes."))
            if f is not None:
                self.send(st, "kick", t, *opt(f["reason"]), then=("players", "status"))
        elif what == "ban":
            f = await self.push_screen_wait(Form(f"BAN · {name}", [("time", "Duration", BAN_TIMES),
                                                                   ("reason", "Reason", "")], verb="BAN", danger=True,
                                                 note="Bans their player ID, address and device together, on every "
                                                      "server sharing this ban list."))
            if f is not None:
                self.send(st, "ban", t, *opt(f["time"]), *opt(f["reason"]), then=("players", "status", "bans"))
        elif what == "mute":
            if p.get("muted"):
                self.send(st, "unmute", t, then=("players",))
                return
            f = await self.push_screen_wait(Form(f"MUTE · {name}", [("time", "Duration", MUTE_TIMES),
                                                                    ("reason", "Reason", "")], verb="MUTE",
                                                 note="Silences their text and voice chat. Rejoining keeps it."))
            if f is not None:
                self.send(st, "mute", t, *opt(f["time"]), *opt(f["reason"]), then=("players",))
        elif what == "team":
            team = await self.push_screen_wait(Pick(f"MOVE {name} TO", [(Text(c.upper(), TEAM_COLOR[c]), c)
                                                                         for c in TEAMS]))
            if team:
                self.send(st, "team", t, team, then=("players",))
        elif what == "vpnallow":
            f = await self.push_screen_wait(Form(f"VPN ALLOW · {name}", [("note", "Note (why)", "")], verb="ALLOW",
                                                 note="Lets them join through a VPN on every server sharing the ban list."))
            if f is not None:
                self.send(st, "vpnallow", t, *opt(f["note"]), then=("vpn",))

    def entries(self, st: Station, what: str) -> list:
        return [(Text.assemble((str(pick(e, "name", default="?")), WHITE), (f"  {e.get('kind', '')}", DIM),
                               (f"  {e.get('reference', '')}", DIM)), str(pick(e, "reference", "name")))
                for e in st.data.get(what, {}).get("entries") or []]

    @work(exclusive=True, group="dialog")
    async def op(self, name: str) -> None:
        st = self.cur
        s = st.data.get("status", {})
        if name == "reconnect":
            if st.rcon.state == "denied":
                f = await self.push_screen_wait(Form(
                    "RETRY SIGN-IN", [("pw", "RCON password", st.rcon.password)], verb="RETRY", required=("pw",),
                    secret=("pw",), note="This station refused the password. Five wrong passwords in ten minutes "
                                         "lock your address out for up to ten minutes."))
                if not f:
                    return
                st.rcon.password = st.server.password = f["pw"]
            self.reconnect(st)
            return
        if not st.online:
            self.notify(f"{st.label} is not connected.", severity="warning")
            return
        if name in ("load", "map", "mode", "nextmap"):
            mp = md = ""
            if name != "mode":
                maps = self.entries(st, "maps")
                if not maps:
                    self.notify("Map list not loaded yet.", severity="warning")
                    return
                mp = await self.push_screen_wait(Pick("SELECT MAP", maps))
                if not mp:
                    return
            if name != "map":
                modes = self.entries(st, "modes")
                if name == "nextmap":
                    modes = [(Text("(keep the current rules)", DIM), "")] + modes
                md = await self.push_screen_wait(Pick("SELECT MODE", modes))
                if md is None or (name != "nextmap" and not md):
                    return
            args = [a for a in (mp, md) if a]
            if name != "nextmap" and not await self.push_screen_wait(Confirm(
                    f"{name.upper()}  {' / '.join(args)}", "Ends the current game for everyone on this station; "
                                                           "the new one starts in the next lobby.")):
                return
            self.send(st, name, *args, then=("status", "nextmap"))
        elif name in ("endround", "endgame", "shuffle"):
            body = {"endround": "Ends the round as if its time ran out.",
                    "endgame": "Ends the game with the scores as they stand.",
                    "shuffle": "Shuffles players across the teams that have players."}[name]
            if await self.push_screen_wait(Confirm(name.upper(), body, danger=name != "shuffle")):
                self.send(st, name, then=("status", "players"))
        elif name == "teamcount":
            n = await self.push_screen_wait(Pick("SPREAD PLAYERS OVER", [(f"{i} teams", str(i)) for i in range(2, 9)]))
            if n:
                self.send(st, "teamcount", n, then=("players",))
        elif name == "startvote":
            subjects = st.data.get("vote", {}).get("subjects") or ["endround", "endgame", "shuffle", "kick", "playlist"]
            subj = await self.push_screen_wait(Pick("CALL A VOTE", [(x.upper(), x) for x in subjects]))
            if not subj:
                return
            args = [subj]
            if subj == "kick":
                who_ = await self.push_screen_wait(Pick("VOTE TO KICK", [(str(pick(p, "name")), target_of(p))
                                                                         for p in st.players]))
                if not who_:
                    return
                args.append(who_)
            self.send(st, "startvote", *args, then=("vote",))
        elif name in ("passvote", "cancelvote"):
            self.send(st, name, then=("vote",))
        elif name == "servername":
            f = await self.push_screen_wait(Form("RENAME STATION", [("name", "Server name (until restart)",
                                                                     s.get("name", ""))], verb="RENAME", required=("name",)))
            if f:
                self.send(st, "servername", f["name"], then=("status",))
        elif name == "password":
            f = await self.push_screen_wait(Form("JOIN PASSWORD", [("pw", "Password players need (empty = open server)", "")],
                                                 verb="SET", note="Lasts until restart. Doesn't touch the RCON password."))
            if f is not None:
                self.send(st, "password", f["pw"], then=("status",))
        elif name == "maxping":
            f = await self.push_screen_wait(Form("PING LIMIT", [("ms", "Highest join ping in ms, up to 1000 (off = none)",
                                                                 s.get("max_ping") or "off")], verb="SET", required=("ms",)))
            if f:
                self.send(st, "maxping", f["ms"], then=("status",))

    @work(exclusive=True, group="dialog")
    async def bl(self, name: str) -> None:
        st = self.cur
        if not st.online:
            self.notify(f"{st.label} is not connected.", severity="warning")
            return
        opt = lambda v: [v] if v else []
        if name == "newban":
            f = await self.push_screen_wait(Form("BAN BY ID OR ADDRESS", [
                ("target", "Player ID, IP address or range (CIDR)", ""), ("time", "Duration", BAN_TIMES),
                ("reason", "Reason", "")], verb="BAN", danger=True, required=("target",)))
            if f:
                self.send(st, "ban", f["target"], *opt(f["time"]), *opt(f["reason"]), then=("bans",))
        elif name in ("unban", "vpnrevoke"):
            sel, rows = ("#bans", self.ban_rows) if name == "unban" else ("#vpn", self.vpn_rows)
            target = rows.get(self.query_one(sel, Roster).selected)
            if not target:
                self.notify("Select an entry first.", severity="warning")
                return
            if await self.push_screen_wait(Confirm(f"{name.upper()}  {redact_text(target, self.redact)}",
                                                   "Lifts it together with its linked entries, on every server "
                                                   "sharing the ban list.", verb=name.upper(), danger=False)):
                self.send(st, name, target, then=("bans", "vpn"))
        elif name == "vpnallow":
            f = await self.push_screen_wait(Form("VPN ALLOW", [("target", "Player ID, IP address or range", ""),
                                                               ("note", "Note", "")], verb="ALLOW", required=("target",)))
            if f:
                self.send(st, "vpnallow", f["target"], *opt(f["note"]), then=("vpn",))
        elif name == "vpncheck":
            f = await self.push_screen_wait(Form("CHECK AN ADDRESS", [("ip", "IP address", "")], verb="CHECK",
                                                 required=("ip",)))
            if f:
                self.send(st, "vpn", f["ip"])
