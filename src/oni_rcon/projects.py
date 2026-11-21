"""The community projects a server can run, as the desktop app's lib/projects.ts has them. Oni speaks Project Reclaimer's
RCON, and another project can speak it too (docs/OTHER-GAMES.md): its sign-in reply names it (`project`), and what is
Reclaimer's alone, its releases and versions, doesn't apply to that server."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .update import mixed_versions


@dataclass(frozen=True)
class Project:
    id: str
    game: str
    name: str
    tag: str
    reclaimer: bool = False


PROJECTS = (
    Project("reclaimer", "Halo 3", "Project Reclaimer", "H3", True),
    Project("gravemind", "Halo 2", "Project Gravemind", "H2"),
    Project("renaissance", "Halo: Reach", "Reach Renaissance", "REACH"),
)


def _line(v: object, n: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", "" if v is None else str(v)).strip()[:n]


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def project_of(info: dict | None) -> Project:
    """The project a server's sign-in reply names: `project` (an id above, or its name), with `game` for one Oni
    doesn't know. Project Reclaimer when it names none, as Reclaimer's own reply doesn't."""
    named = _line((info or {}).get("project"), 40)
    if not named:
        return PROJECTS[0]
    k = _key(named)
    for p in PROJECTS:
        if k in (p.id, _key(p.name), f"project{p.id}"):
            return p
    game = _line((info or {}).get("game"), 30)
    words = [w for w in re.split(r"[^A-Za-z0-9]+", game or named) if w]  # "Halo: Combat Evolved" is HCE, "Halo 2" H2
    tag = "".join(w if w.isdigit() else w[0] for w in words) if len(words) > 1 else (words[0] if words else "?")
    return Project(k or "other", game or "another game", named, tag.upper()[:6])


def mixed_by_project(stations: list[tuple[str, dict]]) -> str:
    """mixed_versions for each project on its own: versions of different games don't compare. Named by project when
    the stations run more than one."""
    by: dict[str, tuple[Project, list[tuple[str, object]]]] = {}
    for label, info in stations:
        p = project_of(info)
        by.setdefault(p.id, (p, []))[1].append((label, (info or {}).get("version")))
    parts = []
    for p, pairs in by.values():
        if mix := mixed_versions(pairs):
            parts.append(f"{p.name}: {mix}" if len(by) > 1 else mix)
    return "  ·  ".join(parts)
