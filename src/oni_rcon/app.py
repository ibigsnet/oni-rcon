"""The ONI terminal: a Textual UI over one RCON connection per server."""
from __future__ import annotations

import asyncio
import json
import re
import shlex
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from os.path import commonprefix
from pathlib import Path
from typing import Callable

from rich.console import Group
from rich.json import JSON
from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.color import Color
from textual.containers import Center, Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.suggester import SuggestFromList
from textual.theme import Theme
from textual.widgets import (Button, Checkbox, Collapsible, DataTable, Footer, Input, Label, ListItem, ListView,
                             OptionList, RichLog, Select, Static, TabbedContent, TabPane)
from textual.widgets.option_list import Option
from textual.worker import WorkerState

from .config import Server
from .rcon import Rcon, Tunnel

AMBER, CYAN, RED, GREEN, DIM, WHITE, GREY = "#D9A441", "#4FC3D9", "#E5484D", "#5FB98A", "#5C6773", "#D6DCE4", "#8B95A1"
ONI = Theme(name="oni", primary=AMBER, secondary=CYAN, accent=CYAN, warning="#E8A33D", error=RED, success=GREEN,
            foreground=WHITE, background="#06080B", surface="#0B0F14", panel="#111821", dark=True)

TEAMS = ["red", "blue", "green", "orange", "purple", "gold", "brown", "pink"]  # the engine's team order
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

OPS = [("load", "LOAD MAP+MODE", "primary"), ("map", "CHANGE MAP", "default"), ("mode", "CHANGE MODE", "default"),
       ("nextmap", "QUEUE NEXT", "default"), ("endround", "END ROUND", "warning"), ("endgame", "END GAME", "error"),
       ("shuffle", "SHUFFLE TEAMS", "default"), ("teamcount", "TEAM COUNT", "default"),
       ("startvote", "CALL VOTE", "default"), ("passvote", "PASS VOTE", "success"),
       ("cancelvote", "CANCEL VOTE", "default"), ("broadcast", "BROADCAST", "primary"),
       ("servername", "RENAME", "default"), ("password", "JOIN PASSWORD", "default"),
       ("maxping", "PING LIMIT", "default"), ("reconnect", "RECONNECT", "default")]
OP_GROUPS = [("MATCH", ["load", "map", "mode", "nextmap", "endround", "endgame"]),
             ("TEAMS & VOTES", ["shuffle", "teamcount", "startvote", "passvote", "cancelvote"]),
             ("SERVER", ["broadcast", "servername", "password", "maxping", "reconnect"])]
BAN_OPS = [("newban", "NEW BAN", "error"), ("unban", "UNBAN  u", "warning"), ("vpnallow", "VPN ALLOW  a", "default"),
           ("vpnrevoke", "VPN REVOKE  r", "default"), ("vpncheck", "CHECK IP", "default")]
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
    (("invalid http", "did not receive a valid http", "rejected websocket", "invalid status"), "NOT AN RCON PORT",
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

# The ONI emblem, traced into pixel grids (largest first); tones 1 dark face, 2 rays, 3 bright face.
EMBLEMS = [b.split() for b in re.sub(r"(?m)^#.*\n", "", Path(__file__).with_name("emblem.txt")
                                     .read_text(encoding="utf-8")).strip().split("\n\n")]
TONES = {"1": "#2C2415", "2": "#6F5626", "3": AMBER}


def emblem(rows: int, cols: int = 999) -> Text:
    """The largest emblem that fits in rows x cols cells, two pixels per cell in half blocks. Empty if none fits."""
    g = next((g for g in EMBLEMS if len(g) // 2 <= rows and len(g[0]) <= cols), [])
    lines = []
    for top, bot in zip(g[::2], g[1::2]):
        line = Text()
        for a, b in zip(top, bot):
            if a == b == "0":
                line.append(" ")
            elif a == b:  # a solid cell is a coloured space: no glyph seams between rows
                line.append(" ", f"on {TONES[a]}")
            elif b == "0":
                line.append("▀", TONES[a])
            elif a == "0":
                line.append("▄", TONES[b])
            else:
                line.append("▀", f"{TONES[a]} on {TONES[b]}")
        lines.append(line)
    return Text("\n").join(lines)


# --- reading tolerant: field names come from the docs, so every lookup accepts the plausible spellings ---------
def pick(d, *keys, default=None):
    if isinstance(d, dict):
        for k in keys:
            if d.get(k) not in (None, ""):
                return d[k]
    return default


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


def bar(v, width: int = 5) -> Text:
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return Text("—", DIM)
    n = round(max(0.0, min(1.0, v)) * width)
    return Text("▰" * n, GREEN if v > .6 else AMBER if v > .3 else RED) + Text("▱" * (width - n), DIM)


def until(ts) -> str:
    if not isinstance(ts, (int, float)):
        return "PERMANENT"
    left = int(ts - time.time())
    if left <= 0:
        return "expired"
    d, h, m = left // 86400, left % 86400 // 3600, left % 3600 // 60
    return f"{d}d {h}h" if d else f"{h}h {m}m" if h else f"{m}m"


def redact_addr(v, on: bool) -> str:
    return "███.███.███.███" if on and v else str(v or "—")


def q(a: str) -> str:
    return f'"{a}"' if not a or " " in a else a


def render_event(label: str, ev: dict, names: dict, redact: bool = True) -> Text:
    """One feed line for a pushed event. Known kinds get a layout; anything else prints its fields."""
    kind = str(ev.get("event", "?"))
    cat = KIND.get(kind, "ops")
    ts = ev.get("time")
    line = Text(datetime.fromtimestamp(ts).strftime("%H:%M:%S") if isinstance(ts, (int, float))
                else time.strftime("%H:%M:%S"), DIM)
    line.append(f" {label[:14]:<14} ", CYAN)
    line.append(f"{GLYPH.get(kind, '·')} ", CATS[cat])
    if kind == "chat":
        ch = str(ev.get("channel", "all"))
        if ch == "server":
            line.append("[SERVER] ", f"bold {AMBER}")
            line.append(str(ev.get("text", "")), AMBER)
        else:
            team = str(pick(ev, "team", default=ch.removeprefix("team").strip())).lower()
            line.append(f"[{ch.upper()}] ", DIM)
            line.append(who(pick(ev, "name", "player", "from", "sender"), names), f"bold {TEAM_COLOR.get(team, WHITE)}")
            line.append(": " + str(ev.get("text", "")))
    elif kind == "kill":
        killer, victim = pick(ev, "killer"), pick(ev, "victim")
        k, v = who(killer, names), who(victim, names)
        if killer is None or k == v:
            line.append(v, "bold")
            line.append(" died", DIM)
        else:
            line.append(k, "bold")
            line.append(" ✕ ", RED)
            line.append(v)
        how = pick(ev, "weapon", "damage", "cause", "how")
        if how:
            line.append(f"  [{how}]", DIM)
    else:
        line.append(kind.upper(), f"bold {CATS[cat]}")
        rest = {k: v for k, v in ev.items() if k not in ("type", "event", "time")}
        name = rest.pop("name", None) or rest.pop("player", None)
        if name is not None:
            line.append(f"  {who(name, names)}", "bold")
        text = rest.pop("text", None)
        if text:
            line.append(f"  {text}")
        for k, v in rest.items():
            v = redact_addr(v, redact) if k in ("address", "ip") else json.dumps(v) if isinstance(v, (dict, list)) else v
            line.append(f"  {k}=", DIM)
            line.append(str(v))
    return line


@dataclass
class Station:
    server: Server
    label: str
    rcon: Rcon | None = None
    worker: object = None
    prev: str = ""
    data: dict = field(default_factory=dict)  # last good `data` per read command (status, players, bans, ...)
    inflight: set = field(default_factory=set)
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


# --- dialogs ------------------------------------------------------------------------------------------------------
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
            yield Static(self.t, classes="dialog-title")
            yield Static(self.b, classes="dialog-body")
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
    """fields: (key, label, default) where a list default is a Select of (label, value) and anything else an Input."""
    BINDINGS = [Binding("escape", "dismiss(None)", show=False)]

    def __init__(self, title: str, fields: list, verb: str = "OK", danger: bool = False, note: str = "",
                 required: tuple = ()):
        super().__init__()
        self.t, self.fields, self.verb, self.danger, self.note, self.required = title, fields, verb, danger, note, required

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog danger" if self.danger else "dialog"):
            yield Static(self.t, classes="dialog-title")
            if self.note:
                yield Static(self.note, classes="dialog-body")
            for key, label, default in self.fields:
                yield Label(label + ("  (required)" if key in self.required else ""))
                if isinstance(default, list):
                    yield Select(default, allow_blank=False, id=f"field-{key}")
                else:
                    yield Input(str(default), id=f"field-{key}")
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
            vals[key] = v.strip() if isinstance(v, str) else ""
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
            yield Static(self.t, classes="dialog-title")
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
        ol.add_options([Option(l, id=str(i)) for i, (l, _) in enumerate(self.shown)])
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
        ("Flags", "ADM an admin  ·  MUT muted  ·  KIA dead right now  ·  NEW just joined"),
    ]),
    ("F2  INTERCEPTS  ·  what's happening", [
        ("The feed", "Chat, kills, joins, kicks and bans from every server, newest at the bottom. Scroll up to read "
                     "back; it holds still until you press End."),
        ("Talk back", "Type in the box at the bottom to chat as [Server]. Start with @all to reach every server."),
        ("Alerts", "Chat asking for an admin, or naming a cheat, pops up and beeps. Servers you aren't looking at "
                   "show a ⚑ count until you open them."),
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
        ("Passwords", "A refused password is never retried by itself: five wrong tries lock you out."),
    ]),
]


class Help(Dialog):
    BINDINGS = [Binding("escape,question_mark,q", "dismiss", show=False)]

    def compose(self) -> ComposeResult:
        parts = []
        for section, rows in GUIDE:
            g = Table.grid(padding=(0, 2))
            g.add_column(style=CYAN, no_wrap=True, width=14)
            g.add_column()
            for k, v in rows:
                g.add_row(k, Text(v, WHITE))
            parts += [Text(section, f"bold {AMBER}"), g, Text()]
        with Vertical(classes="dialog help"):
            yield Static("FIELD MANUAL", classes="dialog-title")
            with VerticalScroll(id="help-body"):
                yield Static(Group(*parts))
            yield self.keys("Esc or ? close", "↑ ↓ scroll", "Ctrl+P every action")

    def on_mount(self) -> None:
        self.query_one("#help-body").focus()


class Boot(Screen):
    """The splash: types out the clearance check while the stations sign in. Any key skips it."""
    BINDINGS = [Binding("escape,enter,space", "skip", "Skip")]
    SPIN = "◐◓◑◒"

    def compose(self) -> ComposeResult:
        with Vertical(id="boot"):
            with Center():
                yield Static(id="boot-emblem")
            yield Static(Text.assemble(("\nO F F I C E   O F   N A V A L   I N T E L L I G E N C E\n", f"bold {AMBER}"),
                                       ("SECTION THREE  ·  REMOTE CONSOLE TERMINAL  ·  ", DIM),
                                       ("TOP SECRET", f"bold {RED}")), id="boot-title")
            with Center():  # align centres children as one block, and the emblem is full width
                yield Static(id="boot-log")

    REVEAL = 0.8  # seconds for the scan line to draw the emblem

    def __init__(self):
        super().__init__()
        self.t0, self.done, self.art, self.drawn = time.monotonic(), None, Text(), -1

    def on_mount(self) -> None:
        self.query_one("#boot-log").styles.height = len(self.app.stations) + 5  # fixed, so lines don't shift it
        self.set_interval(0.07, self.tick)
        self.on_resize()

    def on_resize(self) -> None:  # leave room for the title, a log line per station and the progress bar
        self.art, self.drawn = emblem(self.app.size.height - 14 - len(self.app.stations)), -1
        self.paint_emblem(time.monotonic() - self.t0)

    def paint_emblem(self, el: float) -> None:
        """Draw the emblem top down behind a scan line; the whole of it at once with animations off."""
        lines = self.art.split("\n") if self.art.plain else []
        n = len(lines) if self.app.animation_level != "full" else min(len(lines), int(len(lines) * el / self.REVEAL))
        if n == self.drawn:
            return
        self.drawn, w = n, len(lines[0]) if lines else 0
        rest = [Text("━" * w, CYAN)] + [Text(" " * w)] * (len(lines) - n - 1) if n < len(lines) else []
        self.query_one("#boot-emblem", Static).update(Text("\n").join(lines[:n] + rest))

    @staticmethod
    def dotted(k: str, v: str, color: str) -> Text:
        dots = max(3, min(46 - len(k), 59 - len(k) - len(v)))  # a long value takes from the dots, not the next line
        return Text(f"> {k} ", WHITE) + Text("." * dots, DIM) + Text(f" {v}\n", f"bold {color}")

    def tick(self) -> None:
        app, el = self.app, time.monotonic() - self.t0
        self.paint_emblem(el)
        steps = int(el / 0.22)
        head = [("AUTHENTICATING OPERATOR", app.by.upper(), AMBER),
                ("OPENING SECURE CHANNELS", f"{len(app.stations)} STATION{'S' * (len(app.stations) != 1)}", AMBER)]
        out, progress = Text(), len(head[:steps])
        for k, v, c in head[:steps]:
            out += self.dotted(k, v, c)
        for i, st in enumerate(app.stations[:max(0, steps - len(head))]):
            state = st.rcon.state if st.rcon else "connecting"
            v, c = {"online": ("SECURE", GREEN), "denied": ("DENIED", RED)}.get(
                state, (app.why(st), RED) if state == "offline" else (f"{self.SPIN[int(el * 8) % 4]} HANDSHAKE", AMBER))
            out += self.dotted(f"[{i + 1}] {st.label.upper()[:34]}", v, c)
            progress += state != "connecting"
        settled = all(st.rcon and st.rcon.state != "connecting" for st in app.stations)
        if steps > len(head) + len(app.stations) and settled or el > 15:
            ok = any(st.online for st in app.stations)
            out += self.dotted("CLEARANCE", "GRANTED" if ok else "NO STATION REACHABLE", GREEN if ok else RED)
            progress += 1
            self.done = self.done or time.monotonic()
            if time.monotonic() - self.done > 1.1:
                self.action_skip()
        else:  # the prompt blinks while it works
            out += Text("> ", WHITE) + Text("█" if int(el * 3) % 2 == 0 else " ", AMBER) + Text("\n")
        filled = round(60 * progress / (len(head) + len(app.stations) + 1))
        out += Text("\n") + Text("▰" * filled, AMBER) + Text("▱" * (60 - filled), DIM)
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


class Feed(RichLog):
    """A log that follows new lines only while it's scrolled to the bottom: reading back isn't yanked away."""
    unread = 0

    def watch_scroll_y(self, old: float, new: float) -> None:
        super().watch_scroll_y(old, new)
        if new < old or self.is_vertical_scroll_end:  # only a person scrolls up; the bottom resumes following
            self.auto_scroll = self.is_vertical_scroll_end
            if self.auto_scroll and self.unread:
                self.unread, self.border_subtitle = 0, ""

    def write(self, content, *args, **kw):
        if not self.auto_scroll:
            self.unread += 1
            self.border_subtitle = f"▼ {self.unread} NEW  ·  End to follow"
        return super().write(content, *args, **kw)

    def clear(self):
        self.unread, self.border_subtitle, self.auto_scroll = 0, "", True
        return super().clear()


class CommandInput(Input):
    BINDINGS = [Binding("up", "history(-1)", show=False), Binding("down", "history(1)", show=False)]

    def action_history(self, step: int) -> None:
        h = self.app.history
        if h:
            self.app.hpos = max(0, min(len(h), self.app.hpos + step))
            self.value = h[self.app.hpos] if self.app.hpos < len(h) else ""
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
        self.hpos = 0
        self.row_players: dict[str, dict] = {}
        self.ban_rows: dict[str, str] = {}
        self.vpn_rows: dict[str, str] = {}

    @property
    def cur(self) -> Station:
        return self.stations[self.sel]

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
                        yield DataTable(id="players", cursor_type="row", zebra_stripes=True)
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
                    yield DataTable(id="rotation", cursor_type="none", zebra_stripes=True)
                with TabPane("[dim]F4[/] BLACKLIST", id="blacklist"):
                    with Blacklist():
                        yield DataTable(id="bans", cursor_type="row", zebra_stripes=True)
                        yield DataTable(id="vpn", cursor_type="row", zebra_stripes=True)
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
                           "#sitrep": "SITREP", "#rotation": "ROTATION", "#bans": "BLACKLIST",
                           "#vpn": "VPN ALLOWANCES", "#console-log": "COMMAND LOG"}.items():
            self.query_one(sel).border_title = title
        for wid, tip in TIPS.items():
            self.query_one(f"#{wid}").tooltip = tip
        tabs = self.query_one(TabbedContent)
        for pane, tip in TAB_TIPS.items():
            tabs.get_tab(pane).tooltip = tip
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

        self.set_interval(1, self.paint_masthead)
        self.set_interval(3, self.poll_fast)
        self.set_interval(15, self.poll_slow)
        self.set_interval(0.15, self.animate_cards)
        self.paint_all()
        if self.intro:
            self.push_screen(Boot())
        else:
            self.welcome()
        self.on_resize()
        if self.updater:
            self.run_worker(self.check_update, thread=True, exit_on_error=False)

    def check_update(self) -> None:
        if msg := self.updater():
            self.call_from_thread(self.notify, msg, title="UPDATE", timeout=20)

    def welcome(self) -> None:
        """Once the boot screen is gone: a pointer for someone new, if __main__ asked for one."""
        if self.hint:
            self.notify(self.hint, title="WELCOME, OPERATOR", timeout=15)
            self.hint = ""

    def on_resize(self) -> None:
        self.set_class(self.size.width < 140, "-narrow")  # on the app: the boot screen may be the one on top
        self.paint_masthead()
        # the sidebar crest takes what the station cards and uplink leave, and goes when that's too little
        free = self.size.height - 10 - len(self.tunnels) - 3 * len(self.stations)
        crest = self.query_one("#crest", Static)
        crest.update(art := emblem(min(free, 16), 32))
        crest.display = bool(art.plain)

    # --- connections -----------------------------------------------------------------------------------------
    def on_rcon_state(self, rcon: Rcon, state: str, detail: str) -> None:
        if not self.is_running:  # a late reply or event while quitting: the widgets are already gone
            return
        st = next(s for s in self.stations if s.rcon is rcon)
        if state == "online":
            self.relabel()
            self.fetch(st, "status", "players", "maps", "modes", "nextmap", "vote", "bans", "vpn")
            self.log_event(st, {"event": "uplink", "text": f"SECURE  {detail}"})
        elif state == "denied":
            self.log_event(st, {"event": "uplink", "text": f"SIGN-IN REFUSED: {detail}"})
            self.notify(f"{explain(detail, state)}\nNot retried by itself. Fix the password, then RECONNECT (F3).",
                        title=f"{st.label} · SIGN-IN REFUSED", severity="error", timeout=20, markup=False)
        elif state == "offline" and st.prev == "online":
            self.log_event(st, {"event": "uplink", "text": f"LOST  {detail}"})
        retry = re.search(r"retry in (\d+)s", detail) if state == "offline" else None
        st.retry_at = time.monotonic() + int(retry[1]) if retry else 0.0
        if state != "connecting":
            st.prev = state
        self.paint_card(st)

    def on_tunnel(self, tun: Tunnel, state: str, detail: str) -> None:
        if not self.is_running:  # a late reply or event while quitting: the widgets are already gone
            return
        if state == "down":
            self.log_event(None, {"event": "uplink", "text": f"SSH {tun.dest} DOWN  {detail}"})
            for st in self.stations:  # their own state stays "offline: waiting for the tunnel"; say why
                if st.server.ssh == tun.dest and not st.server.url and not st.online:
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
        for st, n in zip(self.stations, reported):
            if n and not st.server.name:
                st.label = n[cut:].strip(" |·-") or n
        for st in self.stations:
            self.paint_card(st)

    def reconnect(self, st: Station) -> None:
        st.worker.cancel()
        st.worker = self.run_worker(st.rcon.run(), group="rcon", exit_on_error=False)

    # --- data ------------------------------------------------------------------------------------------------
    def fetch(self, st: Station, *what: str) -> None:
        what = tuple(w for w in what if w not in st.inflight)
        if st.online and what:
            st.inflight.update(what)
            self.run_worker(self._fetch(st, what), group="fetch", exit_on_error=False)

    async def _fetch(self, st: Station, what: tuple) -> None:
        try:
            replies = await asyncio.gather(*(st.rcon.call(w) for w in what), return_exceptions=True)
        finally:
            st.inflight.difference_update(what)
        for w, r in zip(what, replies):
            if isinstance(r, dict) and r.get("ok") and isinstance(r.get("data"), dict):
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
        ok, text = bool(r.get("ok")), str(r.get("text") or ("done" if r.get("ok") else "failed"))
        self.log_cmd(Text(time.strftime("%H:%M:%S "), DIM) + Text(f"{st.label} ", CYAN)
                     + Text(f"» {' '.join([command, *map(q, args)])}", "bold"))
        self.log_cmd(Text(f"  {text}", GREEN if ok else RED))
        if show_data and r.get("data") is not None:
            self.log_cmd(JSON.from_data(r["data"]))
        if toast or not ok:
            self.notify(text, title=f"{st.label} · {command.upper()}", severity="information" if ok else "error",
                        markup=False)
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
        self.log_event(st, ev)
        if self.raw_events:
            self.log_cmd(Text(f"{st.label} ◂ ", CYAN) + Text(json.dumps(ev), DIM))
        kind, text = ev.get("event"), str(ev.get("text", ""))
        if kind == "chat" and ev.get("channel") != "server" and ALERT.search(text):
            self.notify(f"{who(pick(ev, 'name', 'player'), st.names)}: {text}", title=f"CALL FOR ADMIN · {st.label}",
                        severity="warning", timeout=12, markup=False)  # player text: brackets aren't markup
            self.alert(st)
        elif kind == "cheat":
            self.notify(render_event("", ev, st.names, self.redact).plain[24:], title=f"ANTI-CHEAT · {st.label}",
                        severity="error", timeout=12, markup=False)
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
        """Beep, pulse the station's card, and count it there until someone opens that station."""
        self.bell()
        st.flash = time.monotonic() + 3
        if st is not self.cur:
            st.alerts += 1

    def log_event(self, st: Station | None, ev: dict) -> None:
        self.feed.append((st, ev))
        if self.shown(st, ev):
            self.query_one("#feed", RichLog).write(self.feed_line(st, ev))

    def shown(self, st: Station | None, ev: dict) -> bool:
        return KIND.get(ev.get("event"), "ops") in self.filters and not (self.local_only and st not in (None, self.cur))

    def feed_line(self, st: Station | None, ev: dict) -> Text:
        return render_event(st.label if st else "LINK", ev, st.names if st else {}, self.redact)

    # --- painting --------------------------------------------------------------------------------------------
    def paint(self, st: Station, what: tuple) -> None:
        if not self.is_running:  # a late reply or event while quitting: the widgets are already gone
            return
        self.paint_card(st)
        if st is not self.cur:
            return
        if "players" in what:
            self.paint_players()
        if {"status", "nextmap", "vote"} & set(what):
            self.paint_ops()
        if {"bans", "vpn"} & set(what):
            self.paint_bans()

    def paint_all(self) -> None:
        for st in self.stations:
            self.paint_card(st)
        self.paint_players()
        self.paint_ops()
        self.paint_bans()
        self.paint_uplink()
        self.paint_masthead()
        self.repaint_feed()

    def paint_masthead(self) -> None:
        if not self.is_running:  # the 1 s timer can fire once more while quitting
            return
        online = sum(st.online for st in self.stations)
        assets = sum(st.data.get("status", {}).get("players") or 0 for st in self.stations if st.online)
        alerts = sum(st.alerts for st in self.stations)
        g = Table.grid(expand=True)
        for j in ("left", "center", "right"):
            g.add_column(justify=j, no_wrap=True)
        wide = self.size.width >= 140  # else just the essentials, so nothing gets cut mid-word
        g.add_row(Text("▲ ", AMBER) + Text("OFFICE OF NAVAL INTELLIGENCE" if wide else "ONI", f"bold {AMBER}")
                  + Text("  ·  SECTION III" if wide else "", DIM),
                  Text(f"{online}/{len(self.stations)} STATIONS SECURE  ·  {assets} ASSETS"
                       + " IN THEATRE" * wide, CYAN if online else RED)
                  + (Text(f"  ·  ⚑ {alerts} ALERT{'S' * (alerts != 1)}", f"bold {RED}") if alerts else Text()),
                  Text("TOP SECRET // " if wide else "", f"bold {RED}") + Text(f"OPERATOR {self.by.upper()}  ")
                  + Text(time.strftime("%H:%M:%S"), DIM))
        self.query_one("#masthead", Static).update(g)

    def why(self, st: Station, short: bool = True) -> str:
        """Why a station isn't online, looking through to its SSH tunnel when that's what's down."""
        rc = st.rcon
        if rc is None or rc.state == "connecting":
            return "CONNECTING…" if short else "Connecting…"
        tun = self.tunnels.get(st.server.ssh) if st.server.ssh and not st.server.url else None
        if tun and tun.state != "up" and rc.state == "offline":
            return explain(tun.detail, short=short) if tun.detail else "OPENING SSH TUNNEL" if short else \
                "Opening the SSH tunnel…"
        return explain(rc.detail, rc.state, short=short)

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
        n, mx = s.get("players"), s.get("max_players")
        count = Text(f"{n}/{mx}" if st.online and n is not None else "", AMBER if n else DIM)
        g.add_row(Text(f"{glyph} ", color) + Text(f"{i + 1}  {st.label.upper()}", "bold"),
                  Text(f"⚑ {st.alerts}  ", f"bold {RED}") + count if st.alerts else count)
        if st.online:
            sub = Text("   " + " · ".join(str(x).replace("_", " ") for x in (s.get("map"), s.get("mode")) if x), DIM)
            phase = Text(str(s.get("phase") or "").replace("_", " ").upper(), GREEN if s.get("phase") == "in_game" else DIM)
        else:
            left = st.retry_at - time.monotonic()
            sub = Text("   " + self.why(st), color if state != "connecting" else AMBER)
            phase = Text(f"↻ {int(left) + 1}s", DIM) if left > 0 else Text("F3 ↻", DIM) if state == "denied" else Text()
        g.add_row(sub, phase)
        self.cards[i].update(g)

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
        t = Text.assemble(("OPERATOR  ", DIM), (self.by, f"bold {AMBER}"), "\n")
        for dest, tun in self.tunnels.items():
            glyph, color = {"up": ("◉", GREEN), "opening": ("◌", AMBER)}.get(tun.state, ("○", RED))
            t.append(f"SSH {dest}  ", DIM)
            t.append(f"{glyph} {tun.state.upper()}\n", color)
        t.append("ADDRESSES ", DIM)
        t.append("REDACTED" if self.redact else "VISIBLE", RED if self.redact else GREEN)
        self.query_one("#uplink", Static).update(t)

    def paint_players(self) -> None:
        t = self.query_one("#players", DataTable)
        keep = self.selected_key(t)
        t.clear()
        self.row_players = {}
        st, now = self.cur, time.monotonic()
        players = sorted(st.players, key=lambda p: (team_of(p), -(pick(p, "score", default=0) or 0)))
        for i, p in enumerate(players):
            team, color = team_of(p), TEAM_COLOR.get(team_of(p), WHITE)
            k, d = pick(p, "kills"), pick(p, "deaths")
            kd = f"{k / max(d, 1):.2f}" if isinstance(k, int) and isinstance(d, int) else "—"
            flags = Text()
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
                flags.append("NEW", f"bold {GREEN}")
            t.add_row(str(pick(p, "number", default="")), Text(str(pick(p, "name", default="?")), f"bold {color}"),
                      Text(team.upper() or "—", TEAM_COLOR.get(team, DIM)), str(pick(p, "score", default="—")),
                      str(k if k is not None else "—"), str(d if d is not None else "—"), kd,
                      bar(p.get("health")), bar(p.get("shields")), flags, key=key)
        if keep in self.row_players:
            t.move_cursor(row=t.get_row_index(keep), animate=False)
        st.painted = st.painted or "players" in st.data  # everyone here at the first look isn't news
        mx = st.data.get("players", {}).get("max_players") or st.data.get("status", {}).get("max_players")
        t.border_title = f"ASSETS IN THEATRE · {len(players)}/{mx or '?'}"
        self.paint_dossier()

    @staticmethod
    def selected_key(t: DataTable) -> str | None:
        if not t.row_count:
            return None
        return t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value

    def cur_player(self) -> dict | None:
        return self.row_players.get(self.selected_key(self.query_one("#players", DataTable)))

    def paint_dossier(self) -> None:
        box, p = self.query_one("#dossier", Static), self.cur_player()
        self.query_one("#player-actions").display = self.query_one("#raw-box").display = bool(p)
        if not p:
            if self.cur.online:
                msg = Text.assemble(("NO ASSETS IN THEATRE\n\n", f"bold {DIM}"),
                                    ("Nobody is playing on this server right now.\nPlayers appear here as they join.", DIM))
            else:
                msg = Text.assemble(("STATION OFFLINE\n\n", f"bold {RED}"), (self.why(self.cur, short=False), WHITE),
                                    ("\n\nIt retries by itself. To retry now: F3, then RECONNECT.", DIM))
            box.update(Text("\n\n") + msg)
            box.styles.text_align = "center"
            return
        box.styles.text_align = "left"
        self.query_one("#pl-mute", Button).label = "UNMUTE" if p.get("muted") else "MUTE"
        team = team_of(p)
        g = Table.grid(padding=(0, 2))
        g.add_column(style=DIM, no_wrap=True)
        g.add_column()
        k, d = pick(p, "kills"), pick(p, "deaths")
        rows = [("CALLSIGN", Text(str(pick(p, "name", default="?")), f"bold {TEAM_COLOR.get(team, WHITE)}")),
                ("SERVICE TAG", pick(p, "service_tag", "tag", default="—")),
                ("PLAYER ID", pick(p, "player_id", "id", default="—")),
                ("ADDRESS", Text(redact_addr(pick(p, "address", "ip"), self.redact), RED if self.redact else WHITE)),
                ("TEAM", Text(team.upper() or "—", TEAM_COLOR.get(team, DIM))),
                ("STATUS", " · ".join(["ALIVE" if p.get("alive") else "KIA" if p.get("alive") is False else "—"]
                                      + ["ADMIN"] * bool(p.get("admin")) + ["MUTED"] * bool(p.get("muted")))),
                ("HEALTH", bar(p.get("health"), 14)), ("SHIELDS", bar(p.get("shields"), 14)),
                ("SCORE", f"{pick(p, 'score', default='—')}    K {k if k is not None else '—'} / D {d if d is not None else '—'}"),
                ("LAST DEATH", f"{pick(p, 'seconds_since_last_death')}s ago"
                 if isinstance(pick(p, "seconds_since_last_death"), (int, float)) else "—")]
        guests = pick(p, "guest_players", "guests")
        if guests:
            rows.append(("GUESTS", str(len(guests) if isinstance(guests, list) else guests)))
        for a, b in rows:
            g.add_row(a, b if isinstance(b, Text) else Text(str(b)))
        raw = {k: redact_addr(v, True) if self.redact and k in ("address", "ip") else v for k, v in p.items()}
        box.update(Group(Text("PERSONNEL FILE", f"bold {AMBER}") + Text("  //  CLASSIFIED", f"bold {RED}"), Text(), g))
        self.query_one("#dossier-raw", Static).update(JSON.from_data(raw))

    def paint_ops(self) -> None:
        st = self.cur
        s, vote = st.data.get("status", {}), st.data.get("vote", {})
        info = st.rcon.info if st.rcon else {}
        yes_no = lambda v, on="ON", off="OFF": Text(on, GREEN) if v else Text(off, DIM) if v is not None else Text("—", DIM)
        g = Table.grid(padding=(0, 2))
        g.add_column(style=DIM, no_wrap=True)
        g.add_column()
        for a, b in [("STATION", Text(str(s.get("name") or info.get("server") or st.label), f"bold {AMBER}")),
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
                     ("VOTE", json.dumps(vote.get("vote")) if vote.get("vote") else "none under way")]:
            g.add_row(a, b if isinstance(b, Text) else Text(str(b)))
        self.query_one("#sitrep", Static).update(g)
        voting = bool(vote.get("vote")) if "vote" in st.data else None  # unknown until the first fetch: leave on
        for op, *_ in OPS:  # greyed out when it can't work right now, so nobody wonders why nothing happened
            off = (op != "reconnect" and not st.online or op in ("passvote", "cancelvote") and voting is False
                   or op == "startvote" and voting is True)
            self.query_one(f"#op-{op}", Button).disabled = off
        t = self.query_one("#rotation", DataTable)
        t.clear()
        for i, e in enumerate(st.data.get("nextmap", {}).get("rotation") or [], 1):
            t.add_row(str(i), str(pick(e, "map", "base_map", default="?")), str(pick(e, "mode", "game", default="?")))

    def paint_bans(self) -> None:
        b, v = self.cur.data.get("bans", {}), self.cur.data.get("vpn", {})
        t = self.query_one("#bans", DataTable)
        t.clear()
        self.ban_rows = {}
        for kind, lst in (("PLAYER", b.get("players")), ("IP", b.get("ips")), ("DEVICE", b.get("devices"))):
            for e in lst or []:
                target = str(pick(e, "id", "player_id", "ip", "range", "address", "device", "fingerprint", default="?"))
                key = f"{kind}:{target}:{len(self.ban_rows)}"
                self.ban_rows[key] = target
                shown = redact_addr(target, self.redact) if kind == "IP" else target
                t.add_row(kind, shown, str(pick(e, "name", default="")), str(pick(e, "reason", default="")),
                          until(pick(e, "expires", "until")), str(pick(e, "by", "group", default="")), key=key)
        t.border_title = f"BLACKLIST · {len(self.ban_rows)}"
        t = self.query_one("#vpn", DataTable)
        t.clear()
        self.vpn_rows = {}
        for e in v.get("allowed") or []:
            target = str(pick(e, "target", "id", "player_id", "ip", "range", "address", default=e))
            key = f"{target}:{len(self.vpn_rows)}"
            self.vpn_rows[key] = target
            t.add_row(target, str(pick(e, "note", default="")), key=key)
        t.border_title = (f"VPN ALLOWANCES · blocking {'ON' if v.get('block_vpn') else 'OFF'}"
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
            self.paint_all()
            self.fetch(self.cur, "status", "players", "nextmap", "vote", "bans", "vpn")

    @on(DataTable.RowHighlighted, "#players")
    def _row(self, _) -> None:
        self.paint_dossier()

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
        if text and not any(st.online for st in targets):
            self.notify("Not connected, so nothing was sent.", severity="warning")
        for st in targets:
            if text and st.online:
                self.send(st, "say", text, toast=False)

    @on(Input.Submitted, "#cmd")
    def _command(self, e: Input.Submitted) -> None:
        line, e.input.value = e.value.strip(), ""
        if not line:
            return
        self.history.append(line)
        self.hpos = len(self.history)
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
        for i, st in enumerate(self.stations):
            yield SystemCommand(f"Station {i + 1}: {st.label}", st.server.where, lambda i=i: self.action_station(i))
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
            self.notify(f"Transmitted to {len(targets)} station(s).", title="BROADCAST")

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
            team = await self.push_screen_wait(Pick(f"MOVE {name} TO", [(Text(c.upper(), f"bold {TEAM_COLOR[c]}"), c)
                                                                         for c in TEAMS]))
            if team:
                self.send(st, "team", t, team, then=("players",))
        elif what == "vpnallow":
            f = await self.push_screen_wait(Form(f"VPN ALLOW · {name}", [("note", "Note (why)", "")], verb="ALLOW",
                                                 note="Lets them join through a VPN on every server sharing the ban list."))
            if f is not None:
                self.send(st, "vpnallow", t, *opt(f["note"]), then=("vpn",))

    def entries(self, st: Station, what: str) -> list:
        return [(Text.assemble((str(pick(e, "name", default="?")), "bold"), (f"  {e.get('kind', '')}", DIM),
                               (f"  {e.get('reference', '')}", DIM)), str(pick(e, "reference", "name")))
                for e in st.data.get(what, {}).get("entries") or []]

    @work(exclusive=True, group="dialog")
    async def op(self, name: str) -> None:
        st = self.cur
        s = st.data.get("status", {})
        if name == "reconnect":
            if st.rcon.state != "denied" or await self.push_screen_wait(Confirm(
                    "RETRY SIGN-IN?", "This station refused the password. Five wrong passwords in ten minutes lock "
                                      "your address out for up to ten minutes.", verb="RETRY")):
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
            target = rows.get(self.selected_key(self.query_one(sel, DataTable)))
            if not target:
                self.notify("Select an entry first.", severity="warning")
                return
            if await self.push_screen_wait(Confirm(f"{name.upper()}  {target}", "Lifts it together with its linked "
                                                   "entries, on every server sharing the ban list.", verb=name.upper(),
                                                   danger=False)):
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
