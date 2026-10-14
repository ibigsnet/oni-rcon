"""F10's frame-time chart for one server: the last 30 minutes, a column a minute (perf.per_minute).

    FRAME TIME · Mega Fat Kid · ms, a column a minute               ▇ average frame   ╸ longest frame
    125 │              ╸ ╸
        │            ╸ ▄ █ ╸
     63 │        ╸ ▂ █ █ █ █ ▆
        │      ▃ █ █ █ █ █ █ █ ▅
    17 ┄│┄┄┄┄┄┄█┄█┄█┄█┄█┄█┄█┄█┄█┄▂┄┄   ← 60 fps
        └────────────────────────────
    GAME THREAD  ▃▄▅▆▇███▇▆        % of one core
    WINESERVER   ▂▃▄▅▅▆▆▅▄▃
                 -30 min                       now
"""
from __future__ import annotations

import math

from rich.text import Text

from . import perf as pf
from .art import AMBER, CYAN, DIM, GREEN, RED, WHITE

LEVEL = {"ok": GREEN, "warn": AMBER, "crit": RED}
EIGHTHS = " ▁▂▃▄▅▆▇█"
TARGET_MS = 1000 / pf.TARGET


def scale_top(hist: list[dict]) -> float:
    """The chart's top in ms: room for the longest frame, never under 50 ms (3x the target), a round number."""
    peak = max([b["longest"] or b["frame"] or 0 for b in hist] + [50])
    step = 25 if peak <= 150 else 50
    return min(500.0, math.ceil(peak / step) * step)


def cell(value: float | None, lo: float, hi: float) -> str:
    """One character of a bar standing to `value`, for the band lo..hi."""
    if value is None or value <= lo:
        return " "
    if value >= hi:
        return "█"
    return EIGHTHS[max(1, round((value - lo) / (hi - lo) * 8))]


def strip(values: list[float | None], width: int, maxed: float) -> Text:
    out = Text()
    for v in values:
        if v is None:
            out.append("·".ljust(width), DIM)
        else:
            out.append(EIGHTHS[max(1, min(8, round(v / 100 * 8)))] * (width - 1) + " ",
                       RED if v >= maxed else AMBER if v >= 70 else GREEN)
    return out


def explain(head: str, fix: str) -> Text:
    """The plain-words lines under the chart."""
    return Text.assemble(("WHAT'S HAPPENING  ", CYAN), (head + "\n", WHITE),
                         ("WHAT TO DO        ", CYAN), (fix + "\n", AMBER))


def chart(label: str, hist: list[dict], height: int = 7, col: int = 2) -> Text:
    """The chart, as Rich Text. `hist` is perf.per_minute's, oldest first."""
    out = Text.assemble(("\nFRAME TIME", CYAN), (f" · {label} · ms, a column a minute   ", DIM),
                        ("█", WHITE), (" average frame  ", DIM), ("╸", AMBER), (" longest frame\n", DIM))
    if not any(b["frame"] is not None for b in hist):
        return out + Text("  no frame-rate reports read for this server yet\n", DIM)
    top = scale_top(hist)
    band = top / height
    target_row = min(height - 1, int(TARGET_MS / band))  # the row the 60 fps line runs through, from the bottom
    for r in range(height - 1, -1, -1):
        lo, hi = r * band, (r + 1) * band
        axis = f"{top:4.0f} " if r == height - 1 else f"{TARGET_MS:4.0f} " if r == target_row else "     "
        out.append(axis, DIM)
        out.append("┤" if r in (height - 1, target_row) else "│", DIM)
        for b in hist:
            frame, longest = b["frame"], b["longest"]
            ch = cell(frame, lo, hi)
            style = LEVEL[pf.level(b["fps"])] if b["fps"] is not None else DIM
            if ch == " " and longest and lo < longest <= hi:
                out.append("╸".ljust(col), AMBER)
            elif ch == " " and r == target_row and frame is not None:
                out.append("┄" * col, DIM)
            else:
                out.append(ch * (col - 1) + (" " if ch != " " else " "), style)
        out.append("   ← 60 fps\n" if r == target_row else "\n", DIM)
    out.append("     └" + "─" * (len(hist) * col) + "\n", DIM)
    if any(b["main"] is not None for b in hist):
        out.append("GAME  ", DIM)
        out.append(strip([b["main"] for b in hist], col, pf.MAXED))
        out.append(" game thread, % of one core\n", DIM)
        out.append("WINE  ", DIM)
        out.append(strip([b["wine"] for b in hist], col, pf.MAXED))
        out.append(" wineserver\n", DIM)
    out.append(f"      -{len(hist)} min".ljust(6 + len(hist) * col - 3) + "now\n", DIM)
    return out
