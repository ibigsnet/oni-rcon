"""The Halo ASCII Archive: the boot sequence's pixel art and timeline, ported 1:1 from the Claude Design project
(halo-archive.jsx). Every picture is a function of the clock, drawn as a grid of hex colours that art.pixels turns
into half blocks, so all of it is testable without the TUI.

The sequence is the design's scenes in order: ONI emblem, Section Three, Installation 04, 343 Guilty Spark, the
Superintendent. Its last two scenes (AdminWatch, Seal) are the live sidebar and the boot screen's own clearance check.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .art import AMBER, CYAN, DIM, GOLD, GREEN, GREY, RED, WHITE, blend, decrypt, noise

Grid = list[list[str | None]]  # rows of hex colours; None is empty

# (name, seconds): the design's OM_SCENES, minus AdminWatch and Seal
SCENES = [("ONI", 4.5), ("SectionThree", 5.0), ("Installation04", 6.5), ("GuiltySpark", 5.5), ("Superintendent", 7.5)]
TOTAL = sum(d for _, d in SCENES)
FILES = [("FILE 01  ONI EMBLEM", "ONI"), ("FILE 02  SECTION THREE", "SectionThree"),
         ("FILE 03  INSTALLATION 04", "Installation04"), ("FILE 04  343 GUILTY SPARK", "GuiltySpark"),
         ("FILE 05  SUPERINTENDENT", "Superintendent")]
FILE_COUNT = len(FILES) + 1  # the design's gauge counts the seal too: the boot screen after this is that sixth step
DECRYPT_FOR = 1.4  # seconds into a scene that its file reads DECRYPTED

GUNGNIR = [
    "0000000000000000003333000000000000000000", "0000330000000000033333300000000000330000",
    "0003333000000000333333330000000003333000", "0033333300000003333333333000000033333300",
    "0033333330000033333333333300000333333300", "0003333333000333333333333330003333333000",
    "0000333333303333333003333333033333330000", "0000033333333333330000333333333333300000",
    "0000003333333333300000033333333333000000", "0000000333333333000000003333333330000000",
    "0000000033333330000000000333333300000000", "0000000333333333000000003333333330000000",
    "0000003333333333300000033333333333000000", "0000033333333333330000333333333333300000",
    "0000333333303333333003333333033333330000", "0003333333000333333333333330003333333000",
    "0033333330000033333333333300000333333300", "0333333300000003333333333000000033333330",
    "0333333000000000333333330000000003333330", "0333333000000000333333330000000003333330",
    "0333333300000003333333333000000033333330", "0033333330000033333333333300000333333300",
    "0003333333000333333333333330003333333000", "0000333333303333333003333333033333330000",
    "0000033333333333330000333333333333300000", "0000003333333333300000033333333333000000",
    "0000000333333333000000003333333330000000", "0000000033333330000000000333333300000000",
    "0000000333333333000000003333333330000000", "0000003333333333300000033333333333000000",
    "0000033333333333330000333333333333300000", "0000333333303333333003333333033333330000",
    "0003333333000333333333333330003333333000", "0033333330000033333333333300000333333300",
    "0033333300000003333333333000000033333300", "0003333000000000333333330000000003333000",
    "0000330000000000033333300000000000330000", "0000000000000000003333000000000000000000"]
METAL = ["#161C23", "#252D37", "#3E4752", "#5C6773", "#8B95A1", "#C5CCD5"]
FACE_EYE = "#EEFFF2"
BRIGHT = "#BFF3FF"  # the scan on the ring and the spark


def cl(v: float, a: float = 0.0, b: float = 1.0) -> float:
    return max(a, min(b, v))


# the design's three motion helpers
def ease_out_cubic(p: float) -> float:
    return 1 - (1 - cl(p)) ** 3


def ease_in_out_sine(p: float) -> float:
    return -(math.cos(math.pi * cl(p)) - 1) / 2


def ease_out_back(p: float) -> float:
    p, c1 = cl(p), 1.70158
    return 1 + (c1 + 1) * (p - 1) ** 3 + c1 * (p - 1) ** 2


def materialise(grid: Grid, reveal: float, scan: float | None = None, scan_color: str | None = None,
                mode: str = "radial") -> Grid | None:
    """Pixels dissolve in out of noise, from the centre or along a diagonal; a bright scan sweeps down once.
    None while nothing has appeared yet."""
    if reveal <= 0:
        return None
    h, w = len(grid), len(grid[0])
    far, line = math.hypot(w / 2, h / 2), None if scan is None else scan * (h + 4) - 2
    out: Grid = []
    for y, row in enumerate(grid):
        r: list[str | None] = []
        for x, c in enumerate(row):
            if c is not None and reveal < 1:
                m = (0.45 * noise(x, y) + 0.55 * (x + y) / (w + h) if mode == "diag"
                     else 0.55 * noise(x, y) + 0.45 * math.hypot(x - w / 2, y - h / 2) / far)
                if m >= reveal:
                    c = None
            r.append(scan_color if c is not None and line is not None and abs(y - line) < 1.5 else c)
        out.append(r)
    return out


def gungnir(glint: float | None = None) -> Grid:
    """Section Three's lattice, amber with a dark edge, and an optional glint along a diagonal."""
    h, w = len(GUNGNIR), len(GUNGNIR[0])

    def on(x: int, y: int) -> bool:
        return 0 <= y < h and 0 <= x < w and GUNGNIR[y][x] == "3"
    out: Grid = []
    for y, row in enumerate(GUNGNIR):
        r: list[str | None] = []
        for x, d in enumerate(row):
            if d != "3":
                r.append(None)
            elif glint is not None and abs(x - y - glint) < 2.2:
                r.append(GOLD)
            else:
                edge = not (on(x - 1, y) and on(x + 1, y) and on(x, y - 1) and on(x, y + 1))
                r.append("#6F5626" if edge else AMBER)
        out.append(r)
    return out


def vnoise(u: float, v: float) -> float:
    x0, y0 = math.floor(u), math.floor(v)
    fx, fy = u - x0, v - y0

    def s(t: float) -> float:
        return t * t * (3 - 2 * t)

    def n(x: int, y: int) -> float:
        return noise(((x % 48) + 48) % 48, y + 7)
    a, b, c, d = n(x0, y0), n(x0 + 1, y0), n(x0, y0 + 1), n(x0 + 1, y0 + 1)
    return a + (b - a) * s(fx) + (c - a) * s(fy) + (a - b - c + d) * s(fx) * s(fy)


def ring(spin: float, tilt: float) -> Grid:
    """Installation 04: a thin band tilted toward us, spinning about its axis, land, sea and cloud on its inner face."""
    W, H, cx, cy, R, b = 80, 48, 40, 24, 35, 4.5
    grid: Grid = [[None] * W for _ in range(H)]
    zb = [[-1e9] * W for _ in range(H)]
    ct, st = math.cos(tilt), math.sin(tilt)
    for i in range(1100):
        th = i / 1100 * math.pi * 2
        c, s = math.cos(th), math.sin(th)
        inner = -s * ct > 0
        lon = ((th + spin) / (math.pi * 2) * 48) % 48
        lit = abs(s) > 0.35
        v = -b
        while v <= b:
            x, z = R * c, R * s
            yp, zp = v * ct + z * st, -v * st + z * ct
            X, Y = math.floor(cx + x), math.floor(cy + yp)
            if not (0 <= X < W and 0 <= Y < H) or zp < zb[Y][X]:
                v += 0.45
                continue
            zb[Y][X] = zp
            if abs(v) > b - 0.9:
                col = GREY if lit else DIM
            elif inner:
                n = vnoise(lon / 1.6, (v + b) / 3)
                col = ("#2E7F95" if lit else "#1F5664") if n < 0.42 else (GREEN if lit else "#3C7A5A") if n < 0.8 else WHITE
            else:
                col = DIM if math.floor(lon * 2) % 7 == 0 else "#3A434E" if lit else "#262E37"
            grid[Y][X] = col
            v += 0.45
    return grid


def spark(yaw: float, bob: float, glow: float) -> Grid:
    """343 Guilty Spark: a lit metal sphere with seams, one blue eye, turning and bobbing."""
    N, cx, cy, R = 48, 24, 24 + bob, 15.5
    light = (-0.5, -0.62, 0.6)
    ln = math.hypot(*light)
    lx, ly, lz = (v / ln for v in light)
    ex_, ez = math.sin(yaw), math.cos(yaw)
    eye_mid, core = blend(CYAN, "#2E7F95", glow), blend("#E6FBFF", CYAN, glow)
    out: Grid = []
    for y in range(N):
        row: list[str | None] = []
        for x in range(N):
            nx, ny = (x + 0.5 - cx) / R, (y + 0.5 - cy) / R
            r2 = nx * nx + ny * ny
            if r2 > 1:
                d = math.sqrt(r2)
                eye_x = cx + ex_ * R * 0.8
                row.append("#12303A" if ez > 0.2 and d < 1.12 and math.hypot(x + 0.5 - eye_x, y + 0.5 - cy) < 7
                           and glow > 0.55 else None)
                continue
            nz = math.sqrt(1 - r2)
            a = math.acos(cl(nx * ex_ + nz * ez, -1, 1))
            if a < 0.14:
                row.append(core)
            elif a < 0.33:
                row.append(eye_mid)
            elif a < 0.42:
                row.append("#0E1318")
            else:
                lon = math.atan2(nx * math.cos(yaw) - nz * math.sin(yaw), nx * math.sin(yaw) + nz * math.cos(yaw))
                lam = cl(nx * lx + ny * ly + nz * lz)
                k = min(5, math.floor(lam * 5.2) + (0 if r2 > 0.86 else 1))
                if abs(ny) < 0.06 or abs(math.sin(2 * lon)) < 0.09:
                    k = max(0, k - 2)
                row.append(METAL[k])
        out.append(row)
    return out


def superintendent(e) -> Grid:
    """The Superintendent: a green disc, scanlined face, two white eyes that blink, glance, smile and widen. `e` is
    a superintendent.Expr (size, ex, ey, lid, tilt, happy, openL, openR, alarm)."""
    N, cx, cy, R, al = 48, 24, 24, 22.5, e.alarm

    def rim(c: str) -> str:
        return blend(c, RED if al > 0.5 else c, 1 - al * 0.6) if al > 0 else c
    tone = {"a": rim("#7FD18F"), "b": rim("#A8E8B2"), "c": rim("#4E8F5E"), "d": rim("#24482D"), "e": rim("#2D5A38")}
    out: Grid = []
    for y in range(N):
        row: list[str | None] = []
        for x in range(N):
            px, py = x + 0.5, y + 0.5
            d = math.hypot(px - cx, py - cy)
            if d > R:
                row.append(None)
                continue
            hit = False
            for side in (-1, 1):
                r = 6.4 * e.size
                dx, dy = (px - (cx + side * 8.6 + e.ex)) / r, (py - (cy + e.ey)) / r
                if e.happy:
                    dd = math.hypot(dx, dy * 1.1)
                    hit = hit or (dd <= 1 and dd >= 0.52 and dy <= 0.08)
                else:
                    open_ = e.openL if side < 0 else e.openR
                    if math.hypot(dx, dy / max(open_, 0.1)) <= 1:
                        hit = hit or dy >= -1 + 2 * e.lid + e.tilt * -side * dx
            if hit:
                row.append(FACE_EYE)
            else:
                row.append(tone["a"] if d > R - 1.3 else tone["b"] if d > R - 2.8 else tone["c"] if d > R - 4.4
                           else tone["d"] if y % 2 else tone["e"])
        out.append(row)
    return out


def super_mood(lt: float) -> tuple[str, str]:
    """The Superintendent scene's mood word and its colour."""
    return (("ONLINE", GREEN) if lt < 2.25 else ("WATCHFUL", CYAN) if lt < 3.75 else ("PLEASED", GREEN)
            if lt < 5.2 else ("ALARMED", RED) if lt < 6.1 else ("ONLINE", GREEN))


def super_expr(lt: float):
    """The Superintendent scene's face at lt seconds in: a blink, a glance right and back, a smile, wide eyes."""
    from .superintendent import Expr

    def k(a: float, b: float) -> float:
        return ease_out_cubic((lt - a) / (b - a))

    def blink(at: float) -> float:
        return cl(1 - abs(lt - at - 0.11) / 0.11)
    op = 1 - blink(1.8) - blink(6.35)
    return Expr(size=1 + 0.26 * k(5.25, 5.4) - 0.26 * k(6.0, 6.2),
                ex=-3 * k(2.25, 2.5) + 6 * k(2.85, 3.1) - 3 * k(3.4, 3.6),
                ey=-1 * k(5.25, 5.4) + 1 * k(6.0, 6.2), happy=3.85 < lt < 5.05, openL=op, openR=op)


@dataclass
class Frame:
    """One moment of the sequence: what to draw and say."""
    scene: str
    art: Grid | None  # None for the ONI emblem, which the boot screen draws from its own sizes (see `oni`)
    oni: tuple[float, float | None] | None  # the emblem's (reveal, scan), for art.emblem
    title: str
    sub: str
    tag: str
    color: str  # the title's colour
    tag_color: str
    files: list[tuple[str, bool]]  # (label, decrypted) for each file started
    progress: float  # 0 to 1 of the design's six steps


TITLES = {
    "ONI": ("OFFICE OF NAVAL INTELLIGENCE", AMBER, "SECTION THREE  ·  REMOTE CONSOLE TERMINAL  ·  ", "TOP SECRET", RED),
    "SectionThree": ("SECTION THREE", AMBER, "OFFICE OF NAVAL INTELLIGENCE  ·  GUNGNIR  ·  ", "BLACK OPERATIONS", RED),
    "Installation04": ("INSTALLATION 04", CYAN, "FORERUNNER ARRAY  ·  10,000 KM  ·  ", "CLASSIFIED", RED),
    "GuiltySpark": ("343 GUILTY SPARK", CYAN, "MONITOR  ·  INSTALLATION 04  ·  ", "RECLAIMER DETECTED", AMBER),
    "Superintendent": ("SUPERINTENDENT", GREEN, "NEW MOMBASA MUNICIPAL AI  ·  MOOD  ", "ONLINE", GREEN),
}


def spaced(s: str) -> str:
    return "   ".join(" ".join(w) for w in s.split(" "))


def locate(t: float) -> tuple[str, float, float, float]:
    """(scene, seconds into it, its start, its end) at t seconds; the last scene holds past the end."""
    start = 0.0
    for name, dur in SCENES:
        if t < start + dur:
            return name, t - start, start, start + dur
        start += dur
    name, dur = SCENES[-1]
    return name, dur, start - dur, start


def frame(t: float) -> Frame:
    """The whole screen's state at t seconds: a pure function of the clock, like the design's Piece."""
    scene, lt, start, end = locate(t)

    def rev(in_dur: float = 1.2) -> float:
        return ease_in_out_sine(lt / in_dur) * (1 - ease_in_out_sine((t - (end - 0.6)) / 0.6))

    def scan_at(a: float = 1.0) -> float | None:
        p = lt - a
        return p if 0 < p < 1 else None
    art, oni = None, None
    if scene == "ONI":
        oni = (rev(1.3), scan_at(0.9))
    elif scene == "SectionThree":
        glint = -44 + (lt - 2.3) / 1.2 * 88 if 2.3 < lt < 3.5 else None
        art = materialise(gungnir(glint), rev(1.6), mode="diag")
    elif scene == "Installation04":
        tilt = (6 + 30 * ease_in_out_sine(lt / 4.5)) * math.pi / 180
        art = materialise(ring(t * 0.55, tilt), rev(), scan_at(1.2), BRIGHT)
    elif scene == "GuiltySpark":
        art = materialise(spark(0.55 * math.sin(lt * 1.25), 1.4 * math.sin(lt * 2.1), 0.5 + 0.5 * math.sin(lt * 5)),
                          rev(), scan_at(), BRIGHT)
    else:
        art = materialise(superintendent(super_expr(lt)), rev(1.2), scan_at(), "#D9FFE0")
    title, color, sub, tag, tag_color = TITLES[scene]
    if scene == "Superintendent":
        tag, tag_color = super_mood(lt)
    starts = {n: sum(d for _, d in SCENES[:i]) for i, (n, _) in enumerate(SCENES)}
    files = [(label, t >= starts[name] + DECRYPT_FOR) for label, name in FILES if t >= starts[name]]
    return Frame(scene, art, oni, decrypt(spaced(title), lt / 0.8, int(t / 0.07)), sub, tag, color, tag_color, files,
                 cl(sum(done for _, done in files) / FILE_COUNT))


__all__ = ["FILES", "SCENES", "TOTAL", "Frame", "frame", "gungnir", "materialise", "ring", "spark", "superintendent"]
