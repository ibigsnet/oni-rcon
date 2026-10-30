"""IBIGS maintains pinned source builds. Upstream self-updates are disabled.

The Reclaimer version helpers (reclaimer_latest, behind, mixed_versions) come from upstream: they only read the
newest Project Reclaimer release so the console can say when a server is behind. ONI_RCON_NO_UPDATE=1 turns that
lookup off too.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.request import Request, urlopen

from . import __version__

RECLAIMER = "https://api.github.com/repos/ProjectReclaimer/project-reclaimer-releases/releases/latest"


def fetch(url: str, timeout: float = 10) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": f"oni-rcon/{__version__}"}), timeout=timeout) as r:
        return r.read()


def check(exe: Path | None = None) -> str:
    return ''


def reclaimer_latest() -> str:
    """The newest Project Reclaimer release, as "0.9.18". Servers admit only players on exactly their version, and
    players' games update themselves. Empty when offline or turned off."""
    if os.environ.get("ONI_RCON_NO_UPDATE"):
        return ""
    try:
        return json.loads(fetch(RECLAIMER))["tag_name"].lstrip("v")
    except Exception:  # offline, rate limited: the cards just don't say
        return ""


def semver(version) -> tuple[int, ...] | None:
    """A version's major.minor.patch ("v0.9.18-rc1" is (0, 9, 18)); None when it isn't one."""
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", str(version or "").strip())
    return tuple(map(int, m.groups())) if m else None


def behind(have, latest) -> str:
    """The newer release a server is behind, as "0.9.18"; empty when it is current, ahead, or either is unknown."""
    h, l = semver(have), semver(latest)
    return ".".join(map(str, l)) if h and l and h < l else ""


def mixed_versions(stations: list[tuple[str, object]]) -> str:
    """"0.9.17 on F1 · 0.9.18 on F2, F3", oldest first, when (label, version) pairs disagree; empty when they agree.
    Unknown versions don't count."""
    by: dict[tuple, list[str]] = {}
    for label, version in stations:
        if v := semver(version):
            by.setdefault(v, []).append(label)
    if len(by) < 2:
        return ""
    return " · ".join(f"{'.'.join(map(str, v))} on {', '.join(labels)}" for v, labels in sorted(by.items()))
