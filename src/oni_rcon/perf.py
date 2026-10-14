"""Server frame rate, as the server reports it.

The server aims at 60 frames a second and, for any minute in which it fell short, says so in its log:

    04:13:33 Server falling behind: under 54 frames a second for 58 of the last 58 seconds (29 on average, longest
    frame 125 ms) with 85 players. Each player gets one update a frame, so they see lag: ...

A minute without that line held 54 or more every second. So the frame rate of a minute is the slow seconds at their
average plus the rest at 60: a slight overestimate (a fine second can be 55), never an invented slowdown. Nothing
else in a log says how fast a server runs; RCON and status.json don't either.

The fleet's node agents read the same lines and send them in the snapshot as `perf.lags`, each
[when, slow seconds, of seconds, average fps, longest frame ms, players]; this module reads both.
"""
from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

TARGET = 60  # the server's frame rate when it keeps up
SLOW = 54  # what the server calls falling behind
BAD = 40  # under this, play is visibly choppy: the feed says so
WINDOW = 300.0  # seconds the averages cover
FRESH = 150.0  # a minute's report is "now" for this long (they come every 60 s)
KEEP = 30  # minutes kept per server

LAG = re.compile(r"Server falling behind: under (\d+) frames a second for (\d+) of the last (\d+) seconds "
                 r"\((\d+) on average, longest frame (\d+) ms\) with (\d+) players?", re.I)
CLOCK = re.compile(r"^(\d\d):(\d\d):(\d\d)\s")


@dataclass(frozen=True)
class Minute:
    """One lag report: the minute up to `ts`."""
    ts: float
    slow: int  # seconds under SLOW
    span: int  # seconds the report covers
    avg: int  # average fps over the slow seconds
    frame_ms: int  # longest frame
    players: int

    @property
    def fps(self) -> float:
        """The whole minute's average: slow seconds at their average, the rest at the target."""
        return (self.slow * self.avg + (self.span - self.slow) * TARGET) / self.span if self.span else TARGET


def minute(text: str, ts: float | None) -> Minute | None:
    """The lag report in a line of log, if it is one."""
    m = LAG.search(text)
    if not m or ts is None:
        return None
    slow, span = int(m[2]), int(m[3])
    if not 0 < span <= 3600 or not 0 <= slow <= span:
        return None
    return Minute(ts, slow, span, min(int(m[4]), TARGET), int(m[5]), int(m[6]))


def from_row(row) -> Minute | None:
    """A snapshot's [when, slow, span, avg, frame_ms, players], or None if it isn't one."""
    if not isinstance(row, (list, tuple)) or len(row) != 6:
        return None
    nums = [v for v in row if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0]
    if len(nums) != 6:
        return None
    ts, slow, span, avg, frame, players = nums
    if not 0 < span <= 3600 or slow > span:
        return None
    return Minute(float(ts), int(slow), int(span), min(int(avg), TARGET), int(frame), int(players))


@dataclass
class Reading:
    """What one server's frame rate looks like now."""
    fps: float  # the latest minute (TARGET when no recent report)
    avg: float  # over WINDOW
    worst: int | None  # the slowest minute's slow-second average in WINDOW, None when none were slow
    frame_ms: int | None  # the longest frame in WINDOW
    players: int | None  # as of the latest report
    slow_minutes: int  # reports in WINDOW
    since: float | None  # when the latest report came

    @property
    def behind(self) -> bool:
        return self.fps < SLOW


@dataclass
class Perf:
    """One server's lag reports, newest last. Every report this console reads covers at least WINDOW of log."""
    minutes: deque = field(default_factory=lambda: deque(maxlen=KEEP))
    read_at: float | None = None  # the last time a report (with or without lag lines) came in
    first_read: float | None = None  # the first: the per-minute history is known from `covers` before it
    covers: float = WINDOW
    choppy: bool = False  # the feed has said it's under BAD and hasn't said it recovered

    def add(self, m: Minute) -> bool:
        if any(abs(o.ts - m.ts) < 2 and o.span == m.span and o.avg == m.avg for o in self.minutes):
            return False  # the same line read again
        self.minutes.append(m)
        if len(self.minutes) > 1 and self.minutes[-2].ts > m.ts:
            self.minutes = deque(sorted(self.minutes, key=lambda x: x.ts), maxlen=KEEP)
        return True

    def reading(self, now: float) -> Reading | None:
        """None until a report has been read."""
        if self.read_at is None:
            return None
        recent = [m for m in self.minutes if now - WINDOW < m.ts <= now + 60]
        last = self.minutes[-1] if self.minutes else None
        live = last if last and 0 <= now - last.ts <= FRESH else None
        slow_s = sum(m.slow for m in recent)  # six reports can just cover a little over WINDOW
        avg = (sum(m.slow * m.avg for m in recent) + max(0.0, WINDOW - slow_s) * TARGET) / max(WINDOW, slow_s)
        return Reading(live.fps if live else TARGET, avg, min((m.avg for m in recent), default=None),
                       max((m.frame_ms for m in recent), default=None), last.players if last else None,
                       len(recent), last.ts if last else None)


def minutes_in(lines: list[str], at: Callable[[int, int, int], float],
               stamp_of: Callable[[str], tuple[float | None, str]]) -> list[Minute]:
    """The lag reports in a server's log lines. A line carries a docker/journal timestamp (stamp_of reads it) or a
    bare HH:MM:SS of its own (at turns that into a time)."""
    out = []
    for line in lines:
        if "falling behind" not in line:
            continue
        ts, body = stamp_of(line)
        if ts is None and (c := CLOCK.match(body)):
            ts = at(int(c[1]), int(c[2]), int(c[3]))
        if m := minute(body, ts):
            out.append(m)
    return out


def level(fps: float) -> str:
    return "ok" if fps >= SLOW else "warn" if fps >= BAD else "crit"


# --- where the frame time goes ---------------------------------------------------------------------------------
# The game simulates on one thread (Wine calls it MAIN_THREAD) and makes most of its system calls as round trips to
# wineserver64, itself single-threaded. So a lobby runs out of one core's speed long before the box runs out of cores.
# Reports carry the kernel's cumulative counters for those two threads ([tid, ns running, ns waiting for a CPU,
# voluntary switches, involuntary switches]); two readings make percentages.
@dataclass(frozen=True)
class Load:
    at: float
    main: float  # % of one core the game thread ran
    main_wait: float  # % of the time it was ready to run but had no CPU: the host is busy
    wine: float | None  # % of one core wineserver ran
    wine_switches: float | None  # its round trips a second, roughly


def counters(v) -> tuple | None:
    if not isinstance(v, (list, tuple)) or len(v) != 5:
        return None
    if not all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in v):
        return None
    return tuple(v)


@dataclass
class Threads:
    """One server's game-thread readings: the last raw one, and the percentages between readings."""
    last: tuple | None = None  # (at, main counters, wine counters or None)
    loads: deque = field(default_factory=lambda: deque(maxlen=240))
    throttled: tuple | None = None  # (at, cumulative seconds the container's CPU cap held it back)
    capped: float | None = None  # the share of the last interval it was held back

    def add(self, at: float, main, wine) -> Load | None:
        main, wine = counters(main), counters(wine)
        if main is None:
            return None
        prev, self.last = self.last, (at, main, wine)
        if not prev or at <= prev[0] or prev[1][0] != main[0] or main[1] < prev[1][1]:
            return None  # the first reading, or another thread (the server restarted): nothing to compare yet
        dt = (at - prev[0]) * 1e9
        w = prev[2]
        same_wine = bool(wine and w and w[0] == wine[0] and wine[1] >= w[1])
        load = Load(at, min(100.0, (main[1] - prev[1][1]) / dt * 100), min(100.0, (main[2] - prev[1][2]) / dt * 100),
                    min(100.0, (wine[1] - w[1]) / dt * 100) if same_wine else None,
                    (wine[3] - w[3]) / (dt / 1e9) if same_wine else None)
        self.loads.append(load)
        return load

    def throttle(self, at: float, seconds: float | None) -> None:
        if seconds is None:
            return
        prev, self.throttled = self.throttled, (at, seconds)
        if prev and at > prev[0] and seconds >= prev[1]:
            self.capped = (seconds - prev[1]) / (at - prev[0])
        elif prev and at > prev[0]:
            self.capped = None  # the counter started again: a restart

    def now(self, now: float) -> Load | None:
        return self.loads[-1] if self.loads and now - self.loads[-1].at <= 180 else None


MAXED = 90  # % of a core: the game thread has nothing left
BUSY_HOST = 10  # % of the time ready to run but waiting for a CPU
SERIAL = 130  # game thread + wineserver, % of a core: the pair ping-pongs on one core's speed


def bottleneck(load: Load | None, capped: float | None, behind: bool) -> tuple[str, str]:
    """(label, level): what's limiting the server. A slow server gets the verdict at crit; a fast one that's close to a
    limit at warn, and otherwise its headroom on the game thread."""
    if load is None:
        return "", ""
    if capped is not None and capped > 0.05:
        return "CPU CAP", "crit" if behind else "warn"
    if load.main_wait >= BUSY_HOST:
        return "HOST BUSY", "crit" if behind else "warn"
    if load.main >= MAXED:
        return "ONE CORE MAXED", "crit" if behind else "warn"
    if load.wine is not None and load.main + load.wine >= SERIAL:
        return "GAME+WINE ONE CORE", "crit" if behind else "warn"
    if behind:
        return "NOT CPU", "warn"
    return f"{100 - load.main:.0f}% HEADROOM", "ok"


def per_minute(p: Perf | None, t: Threads | None, now: float, n: int = 30) -> list[dict]:
    """The last n minutes, oldest first: fps, average frame time and longest frame (ms), game thread and wineserver
    % of a core, each None where unknown. Frame rate is known from as far back as the first report read."""
    out = []
    since = p.first_read - p.covers if p and p.first_read is not None else None
    for i in range(n - 1, -1, -1):
        hi = now - i * 60
        lo = hi - 60
        b = {"fps": None, "frame": None, "longest": None, "main": None, "wine": None}
        if p:
            m = next((m for m in reversed(p.minutes) if lo < m.ts <= hi), None)
            if m:
                b.update(fps=m.fps, frame=1000 / m.fps, longest=m.frame_ms)
            elif since is not None and lo >= since and p.read_at is not None and lo <= p.read_at:
                b.update(fps=float(TARGET), frame=1000 / TARGET)
        if t:
            got = [x for x in t.loads if lo < x.at <= hi]
            if got:
                b["main"] = sum(x.main for x in got) / len(got)
                wines = [x.wine for x in got if x.wine is not None]
                b["wine"] = sum(wines) / len(wines) if wines else None
        out.append(b)
    return out
