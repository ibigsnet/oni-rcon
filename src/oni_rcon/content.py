"""What a server can load, sorted into the groups people look for: stock maps, mods, ReclaimerForge downloads, the
content folder, Halo 3 and Matchmaking variants, saved and Workshop variants. And the playlist files a lobby plays.

The server's `maps` and `modes` replies only say map, mode or variant; the reference says where each one came from:
  {"kind": "map", "name": "Valhalla", "reference": "riverworld"}                          a base map (stock or a mod)
  {"kind": "mode", "name": "Slayer", "reference": "Slayer"}                               a base game type
  {"kind": "variant", "reference": "Game Modes/Forge/rf-d70540eb57f22df48390.json"}       a file in the content folder
  {"kind": "variant", "reference": "default_guardian_012.mvar"}                          Halo 3 / Matchmaking, built in
  {"kind": "variant", "name": "MLG Guardian", "reference": "MLG Guardian"}                saved in MCC, or Workshop
Nothing here talks to a server or to Forge: it's given what they said and returns rows to show.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from . import fuzzy
from .forge import (author_of, kind_of, latest_of, listing_id, metrics_of, num, pick, recent_of, title_of,
                    version_label, votes_of)
from .state import norm, refs_of

# Halo 3's multiplayer maps, by the engine name the server references them with
STOCK_MAPS = {
    "armory": "Rat's Nest", "bunkerworld": "Standoff", "chill": "Narrows", "chillout": "Cold Storage",
    "construct": "Construct", "cyberdyne": "The Pit", "deadlock": "High Ground", "descent": "Assembly",
    "docks": "Longshore", "fortress": "Citadel", "ghosttown": "Ghost Town", "guardian": "Guardian",
    "isolation": "Isolation", "lockout": "Blackout", "midship": "Heretic", "riverworld": "Valhalla",
    "salvation": "Epitaph", "sandbox": "Sandbox", "shrine": "Sandtrap", "sidewinder": "Avalanche",
    "snowbound": "Snowbound", "spacecamp": "Orbital", "warehouse": "Foundry", "zanzibar": "Last Resort",
    "s3d_edge": "Edge", "s3d_turf": "Icebox", "s3d_waterfall": "Waterfall",
}

STOCK_NAMES = {norm(v) for v in STOCK_MAPS.values()}

MAP_GROUPS = [("stock", "Stock maps"), ("mods", "Mod maps"), ("forge", "ReclaimerForge downloads"),
              ("folder", "Your content folder"), ("builtin", "Halo 3 & Matchmaking variants"),
              ("saved", "Saved & Workshop variants"), ("catalog", "Get more from ReclaimerForge")]
MODE_GROUPS = [("stock", "Base game types"), ("forge", "ReclaimerForge downloads"),
               ("folder", "Your content folder"), ("builtin", "Halo 3 & Matchmaking variants"),
               ("saved", "Saved & Workshop variants"), ("catalog", "Get more from ReclaimerForge")]
GROUP_HELP = {
    "stock": "Halo 3's own multiplayer maps and game types.",
    "mods": "Custom .map packages in the content folder's Mods, or installed from the Workshop.",
    "forge": "Installed from ReclaimerForge (F6, or uploaded into a Forge folder).",
    "folder": "Files you put in the content folder yourself.",
    "builtin": "Variants that ship with Halo 3 and MCC Matchmaking.",
    "saved": "Variants saved in MCC, and Workshop variants.",
    "catalog": "Not on this server yet: choosing one downloads and installs it first.",
}

# the browser's column buttons: click one to order by it, again to turn the order round
SORTS = [("name", "NAME"), ("recent", "INSTALLED"), ("downloads", "DOWNLOADS"), ("rated", "RATING")]
HIGH_FIRST = {"recent", "downloads", "rated"}  # first click: newest, most, best
CATALOG_SORT = {"name": "latest", "recent": "latest", "downloads": "downloads", "rated": "rated"}


@dataclass
class Item:
    """One thing the server can load, or (group "catalog") a Forge listing it could have."""
    name: str
    reference: str
    group: str
    folder: str = ""  # a subfolder inside its group, for files: "UFM Encompass"
    kind: str = ""  # map, mode or variant, as the server said; or the catalog's map / gametype
    detail: str = ""  # what's shown dimmed after the name
    forge: dict | None = None  # the forge-state.json entry, or the catalog listing
    installed_at: str = ""
    downloads: float = 0
    rating: float = 0
    stem: str = field(default="", repr=False)

    @property
    def listing(self) -> str:
        return listing_id(self.forge) if self.forge and self.group == "catalog" else ""

    def words(self) -> str:
        """Everything the filter matches against."""
        f = self.forge or {}
        return " ".join([self.name, self.reference, self.folder, self.detail, str(f.get("title") or ""),
                         str(f.get("author") or "")]).lower()


def is_path(ref: str) -> bool:
    return ref.replace("\\", "/").split("/", 1)[0].lower() in ("maps", "game modes", "mods", "playlists")


def stem_of(ref: str) -> str:
    return norm(PurePosixPath(ref.replace("\\", "/")).stem) if ref else ""


def forge_index(installed: dict[str, dict]) -> dict[str, dict[str, dict]]:
    """Every name an installed Forge listing goes by, to its forge-state entry: "files" by the names of the files it
    put in the content folder (rf-<sha>), "names" by its title and the manifest's reference as well."""
    out: dict[str, dict[str, dict]] = {"files": {}, "names": {}}
    for lid, e in installed.items():
        e = {**e, "listing_id": e.get("listing_id") or lid}
        for f in e.get("files") or []:
            if n := stem_of(str(f.get("path") or "")):
                out["files"].setdefault(n, e)
        for n in refs_of(e):
            out["names"].setdefault(n, e)
    return out


def forge_entry(e: dict, index: dict[str, dict[str, dict]]) -> dict | None:
    """The installed Forge listing a server entry is, if it is one. A file in the content folder is one only by its
    own file name: another file with the same title is someone else's copy."""
    ref, name = str(pick(e, "reference", default="")), str(pick(e, "name", default=""))
    if is_path(ref):
        return index["files"].get(stem_of(ref))
    for n in (stem_of(ref), norm(ref), norm(name)):
        if n and (hit := index["files"].get(n) or index["names"].get(n)):
            return hit
    return None


def classify(e: dict, index: dict[str, dict]) -> Item:
    """A `maps` or `modes` entry as an Item in its group."""
    kind = str(e.get("kind") or "")
    ref = str(pick(e, "reference", "name", default=""))
    name = str(pick(e, "name", "reference", default="?")).strip() or ref
    hit = forge_entry(e, index)
    folder, detail = "", ""
    if kind == "map":
        group = "stock" if ref.lower() in STOCK_MAPS or norm(name) in STOCK_NAMES else "mods"
        detail = ref
    elif kind == "mode":
        group = "stock"
    elif is_path(ref):
        parts = ref.replace("\\", "/").split("/")
        inside = parts[1:-1]
        if inside and inside[0].lower() in ("forge", "reclaimerforge", "forge subscriptions") or hit:
            group, inside = "forge", inside[1:] if inside and inside[0].lower().startswith(("forge", "reclaimer")) \
                else inside
        else:
            group = "folder"
        folder = "/".join(p for p in inside if not re.fullmatch(r"[0-9a-f-]{8,}", p))  # not a version's own folder
        detail = parts[-1]
    elif ref.lower().endswith((".mvar", ".bin", ".json")):
        group, detail = "builtin", ref
    else:
        group = "forge" if hit else "saved"
    if hit and group != "forge":
        group = "forge"
    item = Item(name, ref, group, folder, kind, detail, hit, stem=stem_of(ref))
    if hit:
        item.installed_at = str(hit.get("installed_at") or "")
        item.detail = " · ".join(x for x in (f"v{hit['version']}" if hit.get("version") else "",
                                             str(hit.get("author") or "")) if x)
    return item


def downloads_of(x: dict) -> float:
    keys = ("download_count", "downloads", "downloads_total", "total_downloads")
    return num(pick(x, *keys)) or num(pick(metrics_of(x), *keys)) or recent_of(x) or 0


def catalog_items(listings: list[dict], want: str, have: set[str]) -> list[Item]:
    """Forge listings of one kind (map or gametype) that aren't installed here yet."""
    out = []
    for x in listings:
        lid = listing_id(x)
        if kind_of(x) != want or not lid or lid in have or str(x.get("status") or "").lower() == "withdrawn":
            continue
        up, down, _ = votes_of(x)
        v = latest_of(x)
        detail = " · ".join(s for s in (f"v{version_label(v)}" if v else "", author_of(x),
                                        f"▲{up}" if up else "") if s)
        out.append(Item(title_of(x), "", "catalog", "", want, detail, x,
                        downloads=downloads_of(x),
                        rating=(up or 0) - (down or 0)))
    return out


def sort_value(how: str, i: Item):
    """What an item is ordered by, or None when it doesn't say (those go last, by name, either way round)."""
    if how == "recent":
        return i.installed_at or None
    if how == "downloads":
        return i.downloads if i.forge else None
    if how == "rated":
        return i.rating if i.forge else None
    return i.name.casefold()


def ordered(rows: list[Item], how: str = "name", flip: bool = False) -> list[Item]:
    known = [i for i in rows if sort_value(how, i) is not None]
    rest = sorted((i for i in rows if sort_value(how, i) is None), key=lambda i: i.name.casefold())
    known.sort(key=lambda i: (sort_value(how, i), i.name.casefold()), reverse=(how in HIGH_FIRST) != flip)
    return known + rest


def arrange(items: list[Item], what: str, how: str = "name", text: str = "",
            flip: bool = False) -> list[tuple[str, str, list[Item]]]:
    """(group key, group title, items) in the order shown, filtered by text (every word must match) and ordered.
    Empty groups are left out."""
    words = text.lower().split()
    groups = MAP_GROUPS if what == "maps" else MODE_GROUPS
    out = []
    for key, title in groups:
        rows = ordered([i for i in items if i.group == key and (not words or fuzzy.matches(text, i.words()))], how, flip)
        if rows:
            out.append((key, title, rows))
    return out


def items_for(entries: list[dict], installed: dict[str, dict]) -> list[Item]:
    index = forge_index(installed)
    return [classify(e, index) for e in entries if isinstance(e, dict)]


# --- playlists: the files a lobby's `playlist = "playlists/<file>.json"` names ---------------------------------
MIN_GAMES = 3  # the server's ballots offer three different games each time
MAX_GAMES = 128  # the most the dedicated server plays in one rotation
PLAYLIST_KEYS = {"version", "name", "vote_seconds", "games"}
GAME_KEYS = {"map", "game", "name", "weight", "options", "world", "rules"}


class PlaylistError(ValueError):
    """Why a playlist can't be saved, in plain words."""


def new_playlist(name: str = "New playlist") -> dict:
    return {"version": 2, "name": name, "vote_seconds": 20, "games": []}


def check_playlist(p) -> dict:
    """A playlist in the shape the dedicated server reads (version 2), or PlaylistError. Unknown keys are refused, the
    way the server's own import refuses them; whether each map and game is installed is the server's to say."""
    if not isinstance(p, dict):
        raise PlaylistError("A playlist is a JSON object with a name and a list of games.")
    extra = set(p) - PLAYLIST_KEYS
    if extra:
        raise PlaylistError(f"Unknown playlist setting: {', '.join(sorted(extra))}.")
    if p.get("version", 2) != 2:
        raise PlaylistError("Only version 2 playlists are supported.")
    name = str(p.get("name") or "").strip()
    if not name:
        raise PlaylistError("Give the playlist a name.")
    vote = p.get("vote_seconds", 20)
    if not isinstance(vote, int) or isinstance(vote, bool) or not 0 <= vote <= 300:
        raise PlaylistError("Vote seconds must be a whole number from 0 to 300.")
    games = p.get("games")
    if not isinstance(games, list) or len(games) < MIN_GAMES:
        n = len(games) if isinstance(games, list) else 0
        raise PlaylistError(f"A playlist needs at least {MIN_GAMES} games (it has {n}): players vote on three "
                            "different games each time.")
    if len(games) > MAX_GAMES:
        raise PlaylistError(f"A playlist holds at most {MAX_GAMES} games; this one has {len(games)}.")
    out = []
    for n, g in enumerate(games, 1):
        if not isinstance(g, dict):
            raise PlaylistError(f"Game {n} isn't a map and game type.")
        extra = set(g) - GAME_KEYS
        if extra:
            raise PlaylistError(f"Game {n} has unknown settings: {', '.join(sorted(extra))}.")
        if not str(g.get("map") or "").strip() or not str(g.get("game") or "").strip():
            raise PlaylistError(f"Game {n} needs both a map and a game type.")
        w = g.get("weight", 1)
        if not isinstance(w, int) or isinstance(w, bool) or not 1 <= w <= 100:
            raise PlaylistError(f"Game {n}'s weight must be a whole number from 1 to 100.")
        out.append({k: v for k, v in g.items() if not (k == "weight" and v == 1) and v not in ("", None)})
    return {"version": 2, "name": name, "vote_seconds": vote, "games": out}


def playlist_json(p: dict) -> str:
    return json.dumps(check_playlist(p), indent=2, ensure_ascii=False) + "\n"


def game_label(g: dict) -> str:
    """"Team Slayer on Guardian ×3", the way the server's own playlist listing words it."""
    w = g.get("weight", 1)
    label = g.get("name") or f"{g.get('game', '?')} on {g.get('map', '?')}"
    return f"{label}  ×{w}" if w and w != 1 else str(label)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "playlist"
