"""IBIGS 2026-10-15: game types that carry the host settings of the PC they were saved on.

A game type saved from Reclaimer's Host Game screen keeps a "hosting" block (player limit, ports, visibility...).
The first customer loaded one with "max_players": 16 on a server set to 96, and the server stopped with
"min_players must be from 1 through max_players". Before loading, oni finds the game type's file in the server's
content folder (by path, or by the "name" inside it, which is how the server lists saved game types) and says
plainly what would clash, so the person can fix the file or the server first. Nothing here changes any file.
"""
from __future__ import annotations

import json
from pathlib import Path

SCAN_LIMIT = 2000          # files looked at per content folder, at most
FILE_LIMIT = 2_000_000     # bytes: game type files are small; anything bigger isn't one


def _load(path: Path) -> dict | None:
    try:
        if path.stat().st_size > FILE_LIMIT:
            return None
        d = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return d if isinstance(d, dict) else None


def find_game_type(content_dir: str | Path | None, ref: str) -> tuple[Path, dict] | None:
    """The game type file the server means by `ref`: a path inside the content folder, or a saved game type's name."""
    if not content_dir or not ref:
        return None
    root = Path(content_dir)
    if ref.lower().endswith(".json"):
        p = (root / ref).resolve()
        if root.resolve() in p.parents and (d := _load(p)) is not None:
            return p, d
        return None
    folder = root / "Game Modes"
    if not folder.is_dir():
        return None
    want = ref.strip().casefold()
    for n, p in enumerate(sorted(folder.rglob("*.json"))):
        if n >= SCAN_LIMIT:
            break
        d = _load(p)
        if d is not None and str(d.get("name", "")).strip().casefold() == want:
            return p, d
    return None


def clash_note(content_dir: str | Path | None, ref: str, server_max, server_min=None) -> str:
    """Plain words about host settings inside the game type that disagree with this server, or ""."""
    found = find_game_type(content_dir, ref)
    if not found:
        return ""
    path, d = found
    hosting = d.get("hosting")
    if not isinstance(hosting, dict):
        return ""
    lines = []
    gmax = hosting.get("max_players")
    if isinstance(gmax, int) and not isinstance(gmax, bool) and isinstance(server_max, int) and gmax != server_max:
        lines.append(f"max players {gmax} in the game type, {server_max} on this server")
    gmin = hosting.get("min_players")
    if isinstance(gmin, int) and not isinstance(gmin, bool) and isinstance(server_max, int) and gmin > server_max:
        lines.append(f"min players {gmin} in the game type is more than this server's {server_max}")
    if isinstance(gmax, int) and isinstance(server_min, int) and server_min > gmax:
        lines.append(f"this server needs {server_min} players to start, the game type allows only {gmax}")
    if not lines:
        return ""
    rel = path.name if len(str(path)) > 70 else str(path)
    return ("⚠ This game type was saved with the host settings of the PC it was made on:\n  "
            + "\n  ".join(lines)
            + f"\nThe server may stop instead of loading it. Make them match first: change max_players in the "
              f"game type file ({rel}, under \"hosting\"), or the server's player limit (SETTINGS, F12).")
