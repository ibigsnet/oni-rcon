"""The Superintendent: New Mombasa's municipal AI, a small green face that lives in the sidebar on every tab, watches
what comes in from every station, and reacts to what an admin would care about. A port of the Halo ASCII Archive
design's AdminWatch scene, drawn the way art.py draws everything: pixel grids in half blocks.

Everything here is plain functions and one small state machine, so it's testable without the TUI. The app feeds it
events (`react`) and asks it, a few times a second, what face to wear (`look`).
"""
from __future__ import annotations

import math
import re
import textwrap
import time
from dataclasses import dataclass, field

from rich.text import Text

from .art import AMBER, CYAN, DIM, GREEN, GREY, RED, WHITE, halfblocks, noise

MINI, SMALL, BIG = 8, 12, 16  # pixels across the face, and so cells wide: 4, 6 or 8 rows, by the room there is
EASE = 0.28  # seconds an expression takes to settle after an event
DEBOUNCE = 1.2  # seconds a reaction holds against another of the same weight: a busy fleet doesn't make it flicker
BLINK_FOR = 0.22  # seconds a blink lasts
BLINK_EVERY = 5.5  # seconds: one blink in each stretch this long, when in the stretch it varies
CALL = re.compile(r"\b(admins?|mods?|hack\w*|cheat\w*|aimbot|wallhack)\b", re.I)  # a call for an admin, as app.ALERT

# The design's tones: rim bright, rim, rim shade, and two alternating scanline greens for the face.
GREENS = {"a": "#7FD18F", "b": "#A8E8B2", "c": "#4E8F5E", "d": "#24482D", "e": "#2D5A38"}
REDS = {"a": "#E5484D", "b": "#F5999C", "c": "#9A3035", "d": "#4A1A1C", "e": "#5C2326"}  # the face in alarm
EYE = "#EEFFF2"


@dataclass(frozen=True)
class Expr:
    """What the face is doing, in the design's units (a 48 pixel face: they're scaled to SIZE when drawn)."""
    size: float = 1.0  # eye size: 1.26 is wide-eyed alarm
    ex: float = 2.5  # eyes glance right, toward the feed
    ey: float = 0.0
    lid: float = 0.0  # the top lid coming down (0 open, 0.5 half)
    tilt: float = 0.0  # lids slanted in: a frown
    happy: bool = False  # the eyes become smiling arcs
    openL: float = 1.0  # 1 open, 0 closed: a blink; the right eye also closes for a wink
    openR: float = 1.0
    alarm: float = 0.0  # above half, the face flushes red; an alert pulses it through that


@dataclass(frozen=True)
class Reaction:
    name: str  # what it was, for tests and the log
    mood: str  # the word under the face
    color: str
    expr: dict  # the expression's difference from the base
    hold: float  # seconds before it relaxes
    prio: int  # a lower one doesn't cut a higher one short while it's held
    alert: bool = False  # the rim pulses red
    wink: bool = False


REACTIONS = {
    "welcome": Reaction("welcome", "WELCOMING", GREEN, {"happy": True}, 4.0, 1),
    "call": Reaction("call", "ALARMED", RED, {"size": 1.28, "ey": -1.5, "ex": 0.0}, 6.0, 3, alert=True),
    "cheat": Reaction("cheat", "HOSTILE", RED, {"tilt": 0.6, "lid": 0.1}, 6.0, 3, alert=True),
    "satisfied": Reaction("satisfied", "SATISFIED", GREEN, {"happy": True}, 4.5, 2),
    "medal": Reaction("medal", "IMPRESSED", AMBER, {"size": 1.14}, 4.0, 2),
    "unimpressed": Reaction("unimpressed", "UNIMPRESSED", GREY, {"lid": 0.48, "ex": 1.0}, 4.0, 1),
    "cheer": Reaction("cheer", "CHEERFUL", GREEN, {}, 3.5, 1, wink=True),
}
IDLE = Reaction("idle", "WATCHING", CYAN, {}, 0.0, 0)


def classify(ev: dict) -> str | None:
    """Which reaction an event earns, or None for the many that earn none. `ev` is an event as the app sees it:
    "event" names the kind, a kill carries "_medals", chat carries "channel" and "text"."""
    kind = ev.get("event")
    if kind == "chat":
        if ev.get("channel") == "server":
            return "cheer"
        return "call" if CALL.search(str(ev.get("text", ""))) else None
    if kind == "kill":
        return "medal" if ev.get("_medals") else None
    return {"join": "welcome", "cheat": "cheat", "kick": "satisfied", "ban": "satisfied",
            "mute": "unimpressed"}.get(kind)


def ease_out(p: float) -> float:
    return 1 - (1 - max(0.0, min(1.0, p))) ** 3


def ease_pop(p: float) -> float:
    """Overshoots a little before it settles: size changes pop rather than slide."""
    p = max(0.0, min(1.0, p))
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (p - 1) ** 3 + c1 * (p - 1) ** 2


def face(e: Expr, n: int = SMALL) -> Text:
    """The face as half-block text, n cells wide and n/2 rows tall: a green disc, scanlined, with two white eyes."""
    s, cx, cy = n / 48, n / 2, n / 2
    big = 0.469 * n  # the disc's radius: 22.5 of 48
    tones = (REDS if e.alarm > 0.5 else GREENS) | {"w": EYE}  # a clean flip: halfway blends only go muddy
    rows = []
    for y in range(n):
        row = ""
        for x in range(n):
            px, py, d = x + 0.5, y + 0.5, math.hypot(x + 0.5 - cx, y + 0.5 - cy)
            if d > big:
                row += "0"
                continue
            tone = None
            for side in (-1, 1):
                r = 6.4 * s * e.size
                dx, dy = (px - (cx + side * 8.6 * s + e.ex * s)) / r, (py - (cy + e.ey * s)) / r
                open_ = e.openL if side < 0 else e.openR
                if e.happy:  # a smiling arc, thick enough to stay an arc when the eye is a few pixels across
                    dd = math.hypot(dx, dy * 1.1)
                    hit = 0.4 <= dd <= 1.08 and dy <= 0.2
                else:
                    lid = -1 + 2 * e.lid + e.tilt * -side * dx
                    hit = math.hypot(dx, dy / max(open_, 0.1)) <= 1 and dy >= lid
                if hit:
                    tone = "w"
            if tone is None:
                rim = 1.3 * s * 2.2  # the bright edge, thin enough to stay one pixel at this size
                tone = ("b" if d > big - rim else "c" if d > big - 2.2 * rim else "d" if y % 2 else "e")
            row += tone
        rows.append(row)
    return halfblocks(rows, tones)


@dataclass
class Superintendent:
    """The face's moods over time. `react` takes an event; `look` says what it looks like right now."""
    motion: bool = True  # easing and blinks; off (TEXTUAL_ANIMATIONS=none) snaps and never blinks
    now: object = field(default=time.monotonic, repr=False)  # the clock, replaceable in tests
    current: Reaction = IDLE
    before: Reaction = IDLE
    since: float = float("-inf")
    last: str = ""  # a few words on what it reacted to, for under the face
    seen: int = 0  # reactions so far: the blink schedule varies with it

    def wants(self, ev: dict) -> bool:
        """Whether the event earns a reaction: asked first, so the words for the ones that don't are never made."""
        return classify(ev) is not None

    def react(self, ev: dict, subject: str = "") -> Reaction | None:
        """Take an event. Returns the reaction it earned if the face took it up: a lesser one doesn't cut short a
        reaction still being held, and one of the same weight waits a moment so a busy fleet doesn't make it flicker."""
        key = classify(ev)
        if key is None:
            return None
        new, t = REACTIONS[key], self.now()
        if self.held(t) and (new.prio < self.current.prio
                             or new.prio == self.current.prio and t - self.since < DEBOUNCE):
            return None
        self.before, self.current, self.since = self.look_at(t)[0], new, t
        self.last, self.seen = subject or self.last, self.seen + 1
        return new

    def held(self, t: float) -> bool:
        return t - self.since < self.current.hold

    def blinking(self, t: float) -> float:
        """0 to 1: how closed the eyes are, a quick triangular dip once in each stretch of BLINK_EVERY seconds, at a
        moment that varies from stretch to stretch. It depends on the clock alone, so a moment always looks the same."""
        if not self.motion:
            return 0.0
        k = math.floor(t / BLINK_EVERY)
        at = k * BLINK_EVERY + 0.4 + (BLINK_EVERY - BLINK_FOR - 0.8) * noise(k % 100000, 41)
        return max(0.0, 1 - abs(t - at - BLINK_FOR / 2) / (BLINK_FOR / 2))

    def look_at(self, t: float) -> tuple[Reaction, Expr]:
        """(the reaction now in force, the face for it). An expression eases in from the one before it; once its hold
        is over the face eases back to idle."""
        base = Expr()
        cur = self.current if self.held(t) else IDLE
        start = self.since if cur is self.current else self.since + self.current.hold
        p = (t - start) / EASE if self.motion else 1.0
        into = {**vars(base), **cur.expr}
        src = self.before.expr if cur is self.current else self.current.expr
        out = {**vars(base), **src}
        mix = ease_out(p)
        e = {k: (into[k] if isinstance(into[k], bool) else out[k] + (into[k] - out[k]) * (ease_pop(p) if k == "size" else mix))
             for k in into}
        e["happy"] = bool(into["happy"] if mix > 0.5 else out["happy"])
        blink = self.blinking(t) if not e["happy"] else 0.0
        wink = (1 - abs(2 * ((t - start) / 0.5) - 1)) if cur.wink and 0 <= t - start < 0.5 else 0.0
        e["openL"] = 1 - blink
        e["openR"] = max(0.08, 1 - blink - 0.92 * max(0.0, wink))
        if cur.alert and self.motion:
            e["alarm"] = 0.55 + 0.45 * math.cos((t - start) * 9)
        elif cur.alert:
            e["alarm"] = 1.0
        return cur, Expr(**e)

    def look(self, n: int = SMALL) -> tuple[Text, str, str, str, tuple]:
        """(the face, the mood word, its colour, what it last reacted to, a key that changes when the picture does)."""
        t = self.now()
        cur, e = self.look_at(t)
        key = tuple(round(v, 1) if isinstance(v, float) else v for v in vars(e).values()) + (cur.mood, self.last)
        return face(e, n), cur.mood, cur.color, self.last, key


def card(sup: Superintendent, width: int, n: int = SMALL) -> tuple[Text, tuple]:
    """What the sidebar shows: the face on the left and, beside it, the name, the mood and what it last reacted to.
    `width` is the room in cells. The key changes when the picture does, so an unchanged one needn't be repainted."""
    art, mood, color, last, key = sup.look(n)
    room, rows = max(8, width - n - 1), n // 2
    side = [Text("SUPERINTENDENT", DIM), Text(mood, color)] + ([Text("")] if rows >= 6 else [])
    for line in side:
        line.truncate(room)  # never wrap: a wrapped line would make the strip taller than its room
    free = rows - len(side)
    said = textwrap.wrap(last or "all quiet", room, break_long_words=True)  # what it reacted to, in the lines left
    if len(said) > free:
        said = said[:free]
        said[-1] = said[-1][:room - 1].rstrip() + "…"
    side += [Text(line, WHITE if last else DIM) for line in said]
    lines = art.split("\n")
    out = Text("\n").join(Text.assemble(row, " ", side[i] if i < len(side) else Text("")) for i, row in enumerate(lines))
    return out, key + (n, room)


__all__ = ["BIG", "MINI", "SMALL", "Expr", "Reaction", "REACTIONS", "Superintendent", "card", "classify", "face"]
