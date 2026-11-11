"""The custom maps and game types a Project Reclaimer 0.10 server has but no longer lists.

Up to 0.9 a server's `maps` and `modes` replies named every variant it could load. From 0.10 they name only the base
maps and the built-in game types, so map pickers and the playlist editor lost every Forge save, map variant and custom
game type. The files are still there and the server still loads them: this reads the same folders the server's own
content library reads and adds what the reply leaves out, with references the server resolves:

  a file in the content folder's Maps or Game Modes   its path inside the content folder ("Maps/Forge/Pit Stop.json")
  a file in the game folder's variant folders         its file name ("default_guardian_012.mvar")

(0.10's library matches a content folder file by path first, anything by its stored name, and a game folder file by
file name; these are also the references a 0.9 server gave.) Nothing is added to a reply that already names variants,
and nothing is read unless the console runs where the server's files are (a content_dir, no ssh).

Only names are read: a JSON file's "name", else the file's own name. A file the server would refuse still shows here;
the server says why when it is asked to load it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .forge import version_tuple

EXTENSIONS = {".json", ".mvar", ".bin", ".rmeg", ".rvt"}  # what the server's library takes
MAP_EXTENSIONS = {".json", ".mvar"}
SKIP_FOLDERS = {"deleted"}  # where the server keeps what was deleted in game
SCAN_LIMIT = 4000  # files looked at per list, across its folders
NAME_READ_LIMIT = 4 * 1024 * 1024  # a Forge save is around 100 KB; nothing larger is opened for its name

# list -> (folder inside the content folder, the game folder's variant folders)
SOURCES = {
    "maps": ("Maps", ("halo3/map_variants", "halo3/hopper_map_variants")),
    "modes": ("Game Modes", ("halo3/game_variants", "halo3/hopper_game_variants",
                             "haloreach/game_variants", "haloreach/hopper_game_variants")),
}

_names: dict[tuple[str, float, int], str] = {}  # (path, mtime, size) -> the name read from it ("" when it has none)


def lists_variants(data: dict) -> bool:
    """Whether a `maps` or `modes` reply names anything but base maps and built-in game types (a server before 0.10)."""
    return any(isinstance(e, dict) and e.get("kind") not in ("map", "mode") for e in data.get("entries") or [])


def game_dir_of(server) -> str:
    """The server's game folder: the config's game_dir, else the folder named game beside the content folder."""
    explicit = str(getattr(server, "game_dir", "") or "")
    if explicit:
        return explicit
    content = str(getattr(server, "content_dir", "") or "")
    if not content:
        return ""
    beside = Path(content).parent / "game"
    return str(beside) if beside.is_dir() else ""


def readable_here(server) -> bool:
    """The server's files are on this machine: a content folder, and no ssh or remote address in the way."""
    return bool(getattr(server, "content_dir", "")) and not getattr(server, "ssh", "") and not getattr(server, "url", "")


def _name_in(path: Path) -> str:
    """A JSON file's own "name" (a Forge save's or a game type's title), or ""."""
    try:
        st = path.stat()
        key = (str(path), st.st_mtime, st.st_size)
        if key in _names:
            return _names[key]
        name = ""
        if st.st_size <= NAME_READ_LIMIT:
            doc = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(doc, dict) and isinstance(doc.get("name"), str):
                name = doc["name"].strip()
    except (OSError, ValueError, UnicodeDecodeError):
        return ""
    if len(_names) > 4 * SCAN_LIMIT:
        _names.clear()
    _names[key] = name
    return name


def _files(root: Path, extensions: set[str], budget: list[int]):
    """Files under root the library would take, in a steady order; hidden ones and the Deleted folder are left out."""
    if not root.is_dir():
        return
    for folder, subs, files in os.walk(root):
        subs[:] = sorted(d for d in subs if not d.startswith(".") and d.lower() not in SKIP_FOLDERS)
        for f in sorted(files):
            if f.startswith(".") or Path(f).suffix.lower() not in extensions:
                continue
            if budget[0] <= 0:
                return
            budget[0] -= 1
            yield Path(folder) / f


def local_entries(what: str, content_dir: str, game_dir: str = "") -> list[dict]:
    """The variants in the server's folders for one list ("maps" or "modes"), as entries of that list's reply."""
    if what not in SOURCES:
        return []
    inside, game_folders = SOURCES[what]
    extensions = MAP_EXTENSIONS if what == "maps" else EXTENSIONS
    budget = [SCAN_LIMIT]
    out: list[dict] = []
    if content_dir:
        content = Path(content_dir)
        for path in _files(content / inside, extensions, budget):
            ref = path.relative_to(content).as_posix()
            name = _name_in(path) if path.suffix.lower() == ".json" else ""
            out.append({"kind": "variant", "name": name or path.stem, "reference": ref})
    if game_dir:
        for folder in game_folders:
            top = Path(game_dir) / folder
            for path in _files(top, extensions, budget):
                if path.parent != top:  # the game keeps these folders flat; a file name is the whole reference
                    continue
                entry = {"kind": "variant", "name": path.stem, "reference": path.name}
                if path.suffix.lower() == ".json":  # the game ships .mvar and .bin: a JSON file here was put in by hand
                    entry["name"] = _name_in(path) or path.stem
                    entry["group"] = "folder"
                out.append(entry)
    return out


def before_010(version) -> bool:
    """A server that says it is older than 0.10: those name their variants themselves. An unknown version is not."""
    have = version_tuple(version) if version else ()
    return len(have) >= 2 and have[:2] < (0, 10)


def fill(server, what: str, data: dict, version="") -> dict:
    """A `maps` or `modes` reply with the server's own files added when it names none of them. The reply is returned
    as it came from a server older than 0.10, when it already lists variants, when the files aren't on this machine,
    or when there are none."""
    if what not in SOURCES or not isinstance(data, dict) or not readable_here(server) or before_010(version) \
            or lists_variants(data):
        return data
    entries = [e for e in data.get("entries") or [] if isinstance(e, dict)]
    have = {str(e.get("reference") or "").replace("\\", "/").casefold() for e in entries}
    added = [e for e in local_entries(what, server.content_dir, game_dir_of(server))
             if e["reference"].casefold() not in have]
    if not added:
        return data
    return {**data, "entries": entries + added, "filled_from_files": len(added)}
