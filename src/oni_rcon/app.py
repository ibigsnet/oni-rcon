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
from rich.padding import Padding
from rich.table import Table
from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Center, Grid, Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.markup import escape
from textual.screen import ModalScreen, Screen
from textual.suggester import SuggestFromList
from textual.theme import Theme
from textual.widgets import (Button, Checkbox, DataTable, Footer, Input, Label, ListItem, ListView, OptionList,
                             RichLog, Select, Static, TabbedContent, TabPane)
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
       ("cancelvote", "CANCEL VOTE", "warning"), ("broadcast", "BROADCAST", "primary"),
       ("servername", "RENAME", "default"), ("password", "JOIN PASSWORD", "default"),
       ("maxping", "PING LIMIT", "default"), ("reconnect", "RECONNECT", "default")]
BAN_OPS = [("newban", "NEW BAN", "error"), ("unban", "UNBAN  u", "warning"), ("vpnallow", "VPN ALLOW  a", "default"),
           ("vpnrevoke", "VPN REVOKE  r", "default"), ("vpncheck", "CHECK IP", "default")]


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
        return Text.assemble((f" {a.upper()} {sa} ", f"bold {parts[0][1]}"), split_bar(parts, width),
                             (f" {sb} {b.upper()} ", f"bold {parts[1][1]}"))
    names = Text("  ").join(Text(f"{t.upper()} {s}", f"bold {c}") for (t, _, s), (_, c) in zip(teams, parts))
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


def short(v) -> str:
    """A long ID (32 hex digits) cut to its first 8, for the feed; the dossier and console have it whole."""
    s = str(v)
    return s[:8] + "…" if len(s) > 16 and re.fullmatch(r"[0-9a-fA-F-]+", s) else s


def q(a: str) -> str:
    return f'"{a}"' if not a or " " in a else a


def keycaps(*pairs: tuple[str, str]) -> Text:
    return Text("   ").join(Text.assemble((k, f"bold {AMBER}"), (f" {v}", DIM)) for k, v in pairs)


def describe(ev: dict, names: dict, redact: bool = True) -> Text:
    """What an event says, after its time and station. Known kinds get a layout; anything else prints its fields."""
    kind = str(ev.get("event", "?"))
    cat = KIND.get(kind, "ops")
    line = Text()
    if kind == "chat":
        ch = str(ev.get("channel", "all"))
        if ch == "server":
            line.append("[SERVER] ", f"bold {AMBER}")
            line.append(redact_text(str(ev.get("text", "")), redact), AMBER)
        else:
            team = str(pick(ev, "team", default=ch.removeprefix("team").strip())).lower()
            line.append(f"[{ch.upper()}] ", DIM)
            line.append(who(pick(ev, "name", "player", "from", "sender"), names), f"bold {TEAM_COLOR.get(team, WHITE)}")
            line.append(": " + redact_text(str(ev.get("text", "")), redact))
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
        for medal in ev.get("_medals") or ():  # padded with blank braille, not spaces: wrap between pills only
            line.append("  ")
            line.append(f"\u2800{medal}\u2800".replace(" ", "\u2800"), f"bold {INK} on {GOLD}")
    else:
        line.append(kind.upper(), f"bold {CATS[cat]}")
        rest = {k: v for k, v in ev.items() if k not in ("type", "event", "time")}
        name = rest.pop("name", None) or rest.pop("player", None)
        if name is not None:
            line.append(f"  {who(name, names)}", "bold")
        text = rest.pop("text", None)
        if text:
            line.append(f"  {redact_text(str(text), redact)}")
        for k, v in rest.items():
            v = (redact_addr(v, redact) if k in ADDRESS_KEYS else json.dumps(redact_data(v, redact))
                 if isinstance(v, (dict, list)) else redact_text(short(v), redact))
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
    t = Text(str(pick(v, "subject", "type", "kind", default="vote")).upper(), f"bold {AMBER}")
    if target := pick(v, "target", "player", "name"):
        t.append(f" {target}")
    yes, no = num(pick(v, "yes", "for")), num(pick(v, "no", "against"))
    if yes is not None or no is not None:
        yes, no = yes or 0, no or 0
        t += Text.assemble(("\nYES ", DIM), (f"{yes} ", f"bold {GREEN}"), split_bar([(yes, GREEN), (no, RED)], 10),
                           (f" {no}", f"bold {RED}"), (" NO", DIM))
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
class Confirm(ModalScreen[bool]):
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

    def on_mount(self) -> None:
        self.query_one("#no").focus()  # the safe choice is the default

    def on_button_pressed(self, e: Button.Pressed) -> None:
        e.stop()
        self.dismiss(e.button.id == "yes")


class Form(ModalScreen[dict | None]):
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
                yield Label(label)
                if isinstance(default, list):
                    yield Select(default, allow_blank=False, id=f"field-{key}")
                else:
                    yield Input(str(default), id=f"field-{key}", password=key in self.secret)
            with Horizontal(classes="dialog-buttons"):
                yield Button("ABORT", id="no")
                yield Button(self.verb, id="yes", variant="error" if self.danger else "primary")

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
            self.notify(f"{missing[0]} is required", severity="warning")
        else:
            self.dismiss(vals)


class Pick(ModalScreen[str | None]):
    """A filterable list; returns the chosen value, or None when aborted."""
    BINDINGS = [Binding("escape", "dismiss(None)", show=False), Binding("down", "focus_list", show=False)]

    def __init__(self, title: str, options: list):
        super().__init__()
        self.t, self.opts, self.shown = title, options, options

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog pick"):
            yield Static(Text(self.t), classes="dialog-title")
            if len(self.opts) > 8:
                yield Input(placeholder="filter…", id="pick-filter")
            yield OptionList(id="pick-list")

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


class Boot(Screen):
    """The splash: decrypts the title and materialises the emblem while the stations sign in. Any key skips it,
    and F1 to F5 go straight to their tab."""
    SPIN = "◐◓◑◒"
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
        self.set_interval(0.07, self.tick)
        self.tick()

    def on_key(self, e: events.Key) -> None:
        if e.key not in ("f1", "f2", "f3", "f4", "f5"):
            e.stop()  # skipping shouldn't also redact, broadcast or switch station unseen
        self.action_skip()

    @staticmethod
    def dotted(k: str, v: str, color: str) -> Text:
        return Text(f"> {k} ", WHITE) + Text("." * max(3, 46 - len(k)), DIM) + Text(f" {v}\n", f"bold {color}")

    def tick(self) -> None:
        app, el = self.app, time.monotonic() - self.t0
        # leave room for the title, the log (a line per tunnel and station) and the progress bar
        rows = app.size.height - 14 - len(app.stations) - len(app.tunnels)
        if el < 1.9 or self.drawn != rows:  # materialise, sweep once, then leave it be
            self.query_one("#boot-emblem", Static).update(
                emblem(rows, reveal=el / 0.9, scan=(el - 0.8) / 0.9 if 0.8 < el < 1.7 else None))
            self.drawn = rows
        self.query_one("#boot-title", Static).update(Text.assemble(
            ("\n" + decrypt(self.HEADING, el / 0.8, int(el / 0.07)) + "\n", f"bold {AMBER}"),
            ("SECTION THREE  ·  REMOTE CONSOLE TERMINAL  ·  ", DIM), ("TOP SECRET", f"bold {RED}")))

        spin = self.SPIN[int(el * 8) % 4]
        log = [("AUTHENTICATING OPERATOR", app.by.upper(), AMBER, True),
               ("OPENING SECURE CHANNELS", f"{len(app.stations)} STATION{'S' * (len(app.stations) != 1)}", AMBER, True)]
        for tun in app.tunnels.values():
            v, c = {"up": ("ESTABLISHED", GREEN), "down": ("DOWN", RED)}.get(tun.state, (f"{spin} OPENING", AMBER))
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
        progress = (sum(ok for *_, ok in log[:steps]) + finished) / (len(log) + 1)
        out += Text("\n") + gauge(progress, 56, AMBER) + Text(f" {round(progress * 100):>3}%", DIM)
        self.query_one("#boot-log", Static).update(out)

    def action_skip(self) -> None:
        if self.app.screen is self:
            self.app.pop_screen()


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


class Log(RichLog):
    """A RichLog that follows new lines only while it's scrolled to the bottom, so reading back isn't yanked away,
    and that wraps lines to the width it will have while its tab is hidden: hidden, it has no width, and a plain
    RichLog then wraps everything written to it at 78 columns."""
    inset = 8  # what the tabs' width loses to the border, padding and scrollbar; measured whenever it's shown

    def write(self, content, width: int | None = None, expand: bool = False, shrink: bool = True,
              scroll_end: bool | None = None, animate: bool = False):
        tabs = self.app.query_one(TabbedContent).size.width
        if self.size.width:  # as if the scrollbar is there: it will be once the log fills
            self.inset = tabs - self.scrollable_content_region.width + (
                0 if self.show_vertical_scrollbar else self.styles.scrollbar_size_vertical)
        elif width is None and tabs > self.inset:  # as wide as the line, up to what's there once shown
            console = self.app.console
            width = min(measure_renderables(console, console.options, [content]).maximum, tabs - self.inset)
        if scroll_end is None:
            scroll_end = self.is_vertical_scroll_end
        return super().write(content, width, expand, shrink, scroll_end, animate)


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
        Binding("slash", "focus_input", "Type", show=False),
        *[Binding(str(i), f"station({i - 1})", show=False) for i in range(1, 10)],
    ]

    def __init__(self, servers: list[Server], by: str, intro: bool = True, updater: Callable[[], str] | None = None):
        super().__init__()
        self.by, self.intro, self.updater = by, intro, updater
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
        self.alerts, self.alert_at = 0, float("-inf")  # alerts not yet seen on the feed, and when the last came in

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
                with Center():
                    yield Static(id="crest")
                yield Static(id="uplink")
            with TabbedContent(id="tabs", initial="assets"):
                with TabPane("ASSETS", id="assets"):
                    with Assets():
                        yield Roster(id="players", cursor_type="row", zebra_stripes=True)
                        with VerticalScroll(id="dossier-box"):
                            yield Static(id="dossier")
                with TabPane("INTERCEPTS", id="intercepts"):
                    with Horizontal(id="filters"):
                        for cat in CATS:
                            yield Checkbox(cat.upper(), True, id=f"f-{cat}")
                        yield Checkbox("THIS STATION ONLY", False, id="f-local")
                    yield Log(id="feed", wrap=True, max_lines=3000)
                    yield Input(id="say", placeholder="» say to this station   ·   @all <text> transmits to every station")
                with TabPane("OPERATIONS", id="operations"):
                    with Horizontal(id="ops-top"):
                        yield Static(id="sitrep")
                        with Vertical(id="ops-side"):
                            with Grid(id="ops-grid"):
                                for op, label, variant in OPS:
                                    yield Button(label, id=f"op-{op}", variant=variant)
                            yield Static(id="theatre")
                    yield Roster(id="rotation", cursor_type="none", zebra_stripes=True)
                with TabPane("BLACKLIST", id="blacklist"):
                    with Blacklist():
                        yield Roster(id="bans", cursor_type="row", zebra_stripes=True)
                        yield Roster(id="vpn", cursor_type="row", zebra_stripes=True)
                        with Horizontal(classes="bar"):
                            for op, label, variant in BAN_OPS:
                                yield Button(label, id=f"bl-{op}", variant=variant)
                with TabPane("CONSOLE", id="console"):
                    yield Log(id="console-log", wrap=True, max_lines=5000)
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
        self.paint_all()
        if self.intro:
            self.push_screen(Boot())
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

    def on_resize(self) -> None:
        # the sidebar crest takes what the station cards and uplink leave, and goes when that's too little
        free = self.size.height - 9 - len(self.tunnels) - 4 * len(self.stations)
        crest = self.query_one("#crest", Static)
        crest.update(art := emblem(min(free, 16), 32))
        crest.display = bool(art.plain)

    # --- connections -----------------------------------------------------------------------------------------
    def tunnel_of(self, st: Station) -> Tunnel | None:
        return self.tunnels.get(st.server.ssh) if st.server.ssh and not st.server.url else None

    def link(self, st: Station, spin: str = "◌") -> tuple[str, str, bool]:
        """Where a station's sign-in stands, for the boot log: (word, colour, settled)."""
        state, tun = st.rcon.state if st.rcon else "connecting", self.tunnel_of(st)
        if state == "connecting":
            if tun and tun.state == "down":
                return "TUNNEL DOWN", RED, True
            return f"{spin} {'AWAITING TUNNEL' if tun and tun.state != 'up' else 'HANDSHAKE'}", AMBER, False
        return {"online": ("SECURE", GREEN, True), "denied": ("DENIED", RED, True)}.get(state, ("NO CARRIER", RED, True))

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
            self.notify(f"{detail}\nNot retried: wrong passwords lock your address out. RECONNECT (F3) takes the "
                        "password again.", title=f"{st.label} · SIGN-IN REFUSED", severity="error", timeout=20)
        elif state == "offline" and st.prev == "online":
            self.log_event(st, {"event": "uplink", "text": f"LOST  {detail}"})
        if state != "connecting":
            st.prev = state
        self.paint_card(st)
        self.paint_masthead()

    def on_tunnel(self, tun: Tunnel, state: str, detail: str) -> None:
        if not self.is_running:  # a late reply or event while quitting: the widgets are already gone
            return
        if state == "down":
            self.log_event(None, {"event": "uplink", "text": f"SSH {tun.dest} DOWN  {detail}"})
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
                     + Text(redact_text(f"» {' '.join([command, *map(q, args)])}", self.redact), "bold"))
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
            self.alert()
        elif kind == "cheat":
            self.notify(describe(ev, names, self.redact).plain, title=f"ANTI-CHEAT · {st.label}", severity="error",
                        timeout=12)
            self.alert()
        if kind in ("join", "leave", "refused", "kick", "ban", "mute", "unmute", "control"):
            self.fetch(st, "players", "status")
        if kind in ("ban", "unban"):
            self.fetch(st, "bans")
        if kind == "vote":
            self.fetch(st, "vote")
        if kind == "control":
            self.fetch(st, "nextmap")

    def alert(self) -> None:
        """Raise the condition. An alert counts as seen at once when the operator is watching the feed."""
        self.alert_at = time.monotonic()
        if self.query_one(TabbedContent).active != "intercepts":
            self.alerts += 1
        self.bell()
        self.paint_masthead()

    def condition(self) -> tuple[str, str]:
        """RED while an alert is unseen and for a moment after any; AMBER while a station is down; else GREEN."""
        if self.alerts or time.monotonic() - self.alert_at < 15:
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
            ("SIGINT FEED  ", f"bold {AMBER}"),
            ("●" if live else "○", RED if live and time.time() % 2 < 1 else blend(RED, INK, .4) if live else DIM),
            (f" LIVE · {rate}/min" if live else " NO SIGNAL", DIM))

    def paint_masthead(self) -> None:
        cond, color = self.condition()
        if cond == "RED" and time.time() % 2 < 1:  # blink
            color = blend(RED, INK, .55)
        online = sum(st.online for st in self.stations)
        assets = sum(num(st.data.get("status", {}).get("players")) or 0 for st in self.stations if st.online)
        g = Table.grid(expand=True)
        for j in ("left", "center", "right"):
            g.add_column(justify=j, no_wrap=True)
        g.add_row(Text("▲ ", AMBER) + Text("OFFICE OF NAVAL INTELLIGENCE", f"bold {AMBER}")
                  + Text("  ·  SECTION III" if self.size.width >= 160 else "", DIM),
                  Text.assemble((f" CONDITION {cond}" + (f" · {self.alerts} UNSEEN" if self.alerts else "") + " ",
                                 f"bold {INK} on {color}"),
                                (f"  {online}/{len(self.stations)} STATIONS SECURE  ·  {assets} ASSETS",
                                 CYAN if online else RED)),
                  Text("TOP SECRET // ", f"bold {RED}") + Text(f"OPERATOR {self.by.upper()}  ")
                  + Text(time.strftime("%H:%M:%S"), DIM))
        self.query_one("#masthead", Static).update(g)

    def paint_card(self, st: Station) -> None:
        i = self.stations.index(st)
        s = st.data.get("status", {})
        state = st.rcon.state if st.rcon else "connecting"
        glyph, color = STATE[state]
        g = Table.grid(expand=True, padding=(0, 1), pad_edge=False)
        g.add_column(no_wrap=True, overflow="ellipsis", ratio=1)
        g.add_column(justify="right", no_wrap=True)
        n, mx = num(s.get("players")), num(s.get("max_players"))
        g.add_row(Text(f"{glyph} ", color) + Text(f"{i + 1}  {st.label.upper()}", "bold"),
                  Text(f"{n}/{mx}" if st.online and n is not None else "", AMBER if n else DIM))
        if not st.online:  # the reason, on two lines if it needs them
            self.cards[i].update(Group(g, Padding(Text(st.rcon.detail if st.rcon else "", color), (0, 0, 0, 3))))
            return
        g.add_row(Text("   " + " · ".join(str(x).replace("_", " ") for x in (s.get("map"), s.get("mode")) if x), DIM),
                  Text(str(s.get("phase") or "").replace("_", " ").upper(), GREEN if s.get("phase") == "in_game" else DIM))
        full = n / mx if n is not None and mx else 0
        # how full it is, then what's been happening there: the last 100 s in 5 s steps
        self.cards[i].update(Group(g, Text("   ") + gauge(full, 8, RED if full >= 1 else AMBER if full >= .75 else GREEN)
                                   + Text("  ") + spark(st.rate(20, 5))))

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
        st = self.cur
        players = sorted(st.players, key=lambda p: (TEAM_ORDER.get(team_of(p), len(TEAMS)), team_of(p),
                                                    -(num(pick(p, "score")) or 0)))
        self.row_players, rows = {}, []
        for i, p in enumerate(players):
            team, name = team_of(p), str(pick(p, "name", default="?"))
            k, d = pick(p, "kills"), pick(p, "deaths")
            kd = f"{k / max(d, 1):.2f}" if isinstance(k, int) and isinstance(d, int) else "—"
            flags = Text()
            if (spree := st.medals.spree.get(name, 0)) >= SPREE:
                flags.append(f"★{spree} ", f"bold {GOLD}")
            if p.get("admin"):
                flags.append("ADM ", AMBER)
            if p.get("muted"):
                flags.append("MUT ", RED)
            if p.get("alive") is False:
                flags.append("KIA", DIM)
            key = target_of(p)
            key = key if key not in self.row_players else f"{key}~{i}"
            self.row_players[key] = p
            rows.append((key, [str(pick(p, "number", default="")), Text(name, f"bold {TEAM_COLOR.get(team, WHITE)}"),
                               Text(team.upper() or "—", TEAM_COLOR.get(team, DIM)), str(pick(p, "score", default="—")),
                               str(k if k is not None else "—"), str(d if d is not None else "—"), kd,
                               bar(p.get("health")), bar(p.get("shields")), flags]))
        t = self.query_one("#players", Roster)
        t.fill(rows)
        mx = st.data.get("players", {}).get("max_players") or st.data.get("status", {}).get("max_players")
        t.border_title = Text(f"ASSETS IN THEATRE · {len(players)}/{mx or '?'}")
        t.border_subtitle = team_strip(sides(players))
        self.paint_dossier()

    def cur_player(self) -> dict | None:
        return self.row_players.get(self.query_one("#players", Roster).selected)

    def paint_dossier(self) -> None:
        box, p = self.query_one("#dossier", Static), self.cur_player()
        if not p:
            msg = "NO ASSETS IN THEATRE" if self.cur.online else "STATION OFFLINE"
            box.update(Text(f"\n\n{msg}\n\nPlayers appear here as they join.", DIM, justify="center"))
            return
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
        head = Table.grid(padding=(0, 2))
        head.add_row(biosig(str(pick(p, "player_id", "id", default=name)), color), facts([
            ("CALLSIGN", Text(name, f"bold {color}")),
            ("SERVICE TAG", pick(p, "service_tag", "tag", default="—")),
            ("TEAM", Text(team.upper() or "—", TEAM_COLOR.get(team, DIM))),
            ("STATUS", " · ".join(["ALIVE" if p.get("alive") else "KIA" if p.get("alive") is False else "—"]
                                  + ["ADMIN"] * bool(p.get("admin")) + ["MUTED"] * bool(p.get("muted")))),
            ("SCORE", f"{pick(p, 'score', default='—')}    K {k if k is not None else '—'} / D {d if d is not None else '—'}"),
            ("STREAK", Text(f"★ {spree} without dying", f"bold {GOLD}") if spree >= SPREE
             else Text(f"{spree} without dying") if spree else Text("—", DIM))]))
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
        raw = {k: redact_addr(v, True) if self.redact and k in ADDRESS_KEYS else v for k, v in p.items()}
        box.update(Group(Text("PERSONNEL FILE", f"bold {AMBER}") + Text("  //  CLASSIFIED", f"bold {RED}"), Text(),
                         head, Text(), facts(rows), Text(),
                         keycaps(("t", "tell"), ("k", "kick"), ("b", "ban"), ("m", "mute")),
                         keycaps(("j", "team"), ("v", "vpn allow"), ("y", "copy ID")),
                         Text(), Text("RAW", DIM), JSON.from_data(raw)))

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
                     ("VOTE", vote_text(vote.get("vote")))]:
            g.add_row(a, b if isinstance(b, Text) else Text(str(b)))
        self.query_one("#sitrep", Static).update(g)
        self.paint_theatre()
        here = str(s.get("map") or "").lower().replace(" ", "_")
        rows, marked = [], False
        for i, e in enumerate(st.data.get("nextmap", {}).get("rotation") or [], 1):
            mp, md = str(pick(e, "map", "base_map", default="?")), str(pick(e, "mode", "game", default="?"))
            now = not marked and mp.lower().replace(" ", "_") == here
            marked |= now
            style = f"bold {AMBER}" if now else ""
            rows.append((str(i), [Text("▶" if now else str(i), style), Text(mp, style), Text(md, style)]))
        self.query_one("#rotation", Roster).fill(rows)

    def paint_theatre(self) -> None:
        """Who's winning: each team's numbers and score as bars, or a leaderboard when there are no teams."""
        st = self.cur
        players, teams = st.players, sides(st.players)
        g = Table.grid(padding=(0, 2))
        if not players:
            self.query_one("#theatre", Static).update(Text("NO ASSETS IN THEATRE" if st.online else "STATION OFFLINE",
                                                           DIM))
            return
        if teams:
            top = max(max(s, 0) for *_, s in teams) or 1
            for team, n, score in teams:
                c = TEAM_COLOR.get(team, WHITE)
                g.add_row(Text(team.upper(), f"bold {c}"), Text(f"{n} ON FIELD", DIM), Text(str(score), f"bold {c}"),
                          hbar(max(score, 0) / top, 34, c))
        else:  # free for all: the five best
            best = sorted(players, key=lambda p: -(num(pick(p, "score")) or 0))[:5]
            top = max(num(pick(best[0], "score")) or 0, 1)
            for p in best:
                score = num(pick(p, "score")) or 0
                g.add_row(Text(str(pick(p, "name", default="?")), "bold"), Text(""), Text(str(score), f"bold {AMBER}"),
                          hbar(max(score, 0) / top, 34, AMBER))
        leaders = sorted(players, key=lambda p: -(num(pick(p, "score")) or 0))[:3]
        lines = [Text("TOP GUNS  ", DIM) + Text(" · ").join(
            Text(f"{pick(p, 'name', default='?')} {pick(p, 'score', default=0)}",
                 TEAM_COLOR.get(team_of(p), WHITE)) for p in leaders)]
        sprees = sorted(((n, name) for name, n in st.medals.spree.items() if n >= SPREE), reverse=True)
        if sprees:
            lines.append(Text("ON A SPREE  ", DIM) + Text(" · ").join(Text(f"{name} ★{n}", f"bold {GOLD}")
                                                                    for n, name in sprees[:4]))
        self.query_one("#theatre", Static).update(Group(g, Text(), *lines))

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
                                   Text(left, f"bold {RED}" if left == "PERMANENT" else DIM if left == "expired" else AMBER),
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
        if e.pane.id == "intercepts" and self.alerts:  # seen now
            self.alerts = 0
            self.paint_masthead()

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

    def action_tab(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab
        self.query_one(PANE_FOCUS[tab]).focus()  # else focus left in the old pane pulls the tabs back to it

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
