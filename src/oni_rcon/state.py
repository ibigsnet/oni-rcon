"""What oni-rcon keeps between runs about Forge content: what's installed on which server, and what the update watcher
has seen. One small JSON file beside the config (forge-state.json), written whole through a temp file; held in memory
only when there's nowhere to keep it.
"""
from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path, PurePosixPath

from .config import write_atomic
from .forge import utc_iso


def norm(s) -> str:
    """A name as servers and catalogs both might spell it: "Guardian Rebuilt", "guardian_rebuilt", "guardian-rebuilt"."""
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").lower()).strip("_")


def refs_of(entry: dict) -> set[str]:
    """Every name an installed listing might go by on a server: the manifest's reference, its files' names, its title."""
    names = {entry.get("reference"), entry.get("title")}
    names |= {PurePosixPath(f.get("path", "")).stem for f in entry.get("files") or []}
    return {norm(n) for n in names if n} - {""}


SEEN = 500  # change keys remembered, for the overlap between one look at the feed and the next


class ForgeState:
    """{"version": 1, "servers": {server: {listing id: entry}}, ...}. A server is its Server.where; an entry records
    the version installed and every file with its SHA-256, so what's on disk can always be told from what Forge has.

    The watcher's part: "since", the changes feed's last as_of; "seen", the changes already handled; "latest", the
    newest version known per listing; "told", the version each update toast was for, so a restart doesn't repeat
    it; "withdrawn", {listing id: {"at", "acknowledged", "version_id"?, "version_ids"?}}, where version ids mean only
    those releases were withdrawn and the listing stays up. The singular field remains readable for prior files;
    "reconciled" says when the installed listings were last fetched whole."""

    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict = {"version": 1, "servers": {}}
        if path and path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if not (isinstance(loaded, dict) and loaded.get("version") == 1
                        and isinstance(loaded.get("servers"), dict)):
                    raise ValueError("not a version 1 state file")
                self.data.update(loaded)
            except (OSError, ValueError):  # kept aside, never written over: it may be worth reading by hand
                with contextlib.suppress(OSError):
                    path.replace(path.with_name(path.name + ".unreadable"))

    def save(self) -> None:
        if self.path:
            with contextlib.suppress(OSError):  # a read-only profile loses the record, not the install
                write_atomic(self.path, json.dumps(self.data, indent=1, sort_keys=True))

    def installed(self, server: str) -> dict[str, dict]:
        return self.data["servers"].get(server, {})

    def record(self, servers: list[str], entry: dict) -> None:
        """An install, on every server that shares the content folder it went into."""
        for s in servers:
            self.data["servers"].setdefault(s, {})[entry["listing_id"]] = entry
        w = self.data.get("withdrawn", {}).get(entry["listing_id"])
        if w and (withdrawn := self.withdrawn_versions(entry['listing_id'])):
            remaining = sorted(withdrawn & self.versions(entry['listing_id']))
            if remaining:
                w.update(version_id=remaining[0], version_ids=remaining)
            else:
                del self.data['withdrawn'][entry['listing_id']]  # every affected server has moved on
        self.save()

    def versions(self, lid: str) -> set[str]:
        """The versions of a listing installed anywhere."""
        return {es[lid].get("version_id") for es in self.data["servers"].values() if lid in es} - {None, ""}

    def where(self, lid: str, vid: str = "") -> list[str]:
        """The servers a listing is installed on, or with `vid` the ones running that version of it."""
        return [s for s, entries in self.data["servers"].items()
                if lid in entries and (not vid or entries[lid].get("version_id") == vid)]

    def ids(self) -> set[str]:
        return {lid for entries in self.data["servers"].values() for lid in entries}

    def playing(self, server: str, map_name, mode) -> list[dict]:
        """The installed listings a server is playing now: its map, its gametype, or both. Only what's installed is
        matched, never the catalog at large, so a stock map never gets someone else's credit."""
        m, g = norm(map_name), norm(mode)
        return [e for e in self.installed(server).values()
                if m and m in refs_of(e) and e.get("kind") != "gametype"
                or g and g in refs_of(e) and e.get("kind") != "map"]

    def entry(self, lid: str) -> dict | None:
        """Any server's record of a listing: its title, author and kind are the same everywhere."""
        return next((e[lid] for e in self.data["servers"].values() if lid in e), None)

    # --- the watcher's part -----------------------------------------------------------------------------------
    def since(self) -> str:
        """Where the changes feed was read up to: at first, the earliest install."""
        if self.data.get("since"):
            return self.data["since"]
        times = [e.get("installed_at") for es in self.data["servers"].values() for e in es.values()]
        return min((t for t in times if t), default="")

    def seen(self, key: str) -> bool:
        return key in self.data.get("seen", [])

    def saw(self, keys: list[str], as_of: str) -> None:
        self.data["seen"] = (self.data.get("seen", []) + keys)[-SEEN:]
        self.data["since"] = as_of
        self.save()

    def latest(self, lid: str) -> dict | None:
        return self.data.get("latest", {}).get(lid)

    def newer(self, lid: str, vid: str, label: str = "") -> list[str]:
        """A version Forge has put out. Returns the servers running another one, the first time it's heard of;
        an empty list once they've been told."""
        self.data.setdefault("latest", {})[lid] = {"id": vid, "version": label}
        behind = [s for s in self.where(lid) if self.installed(s)[lid].get("version_id") != vid]
        if not behind or self.data.setdefault("told", {}).get(lid) == vid:
            self.save()
            return []
        self.data["told"][lid] = vid
        self.save()
        return behind

    def withdraw(self, lid: str, vid: str = "") -> bool:
        """True the first time a listing, or with `vid` just that version of it, is heard to be withdrawn. A whole
        listing withdrawn after one of its versions replaces that, and is news again."""
        w = self.data.setdefault("withdrawn", {})
        previous = self.withdrawn_versions(lid)
        if lid in w and (not previous or vid and vid in previous):
            return False  # a whole-listing withdrawal includes every version
        versions = sorted(previous | {vid}) if vid else []
        w[lid] = {"at": utc_iso(), "acknowledged": False,
                  **({"version_id": versions[0], "version_ids": versions} if versions else {})}
        self.save()
        return True

    def withdrawn_versions(self, lid: str) -> set[str]:
        """Every affected release, including the singular field in older state files."""
        w = self.data.get('withdrawn', {}).get(lid) or {}
        versions = w.get('version_ids')
        return ({v for v in versions if isinstance(v, str) and v} if isinstance(versions, list) else set()) | (
            {w['version_id']} if isinstance(w.get('version_id'), str) and w['version_id'] else set())

    def restore(self, lid: str) -> None:
        """The whole listing is back. A withdrawn version stays withdrawn: only installing another clears it."""
        if lid in self.data.get("withdrawn", {}) and not self.withdrawn_versions(lid):
            del self.data["withdrawn"][lid]
            self.save()

    def is_withdrawn(self, lid: str) -> bool:
        return lid in self.data.get("withdrawn", {})

    def blocks(self, lid: str) -> bool:
        """Whether the whole listing is withdrawn, so nothing of it can be installed. When only a version is, a
        newer one still can."""
        w = self.data.get("withdrawn", {}).get(lid)
        return bool(w) and not self.withdrawn_versions(lid)

    def pulled(self, lid: str, entry: dict | None) -> bool:
        """Whether what's installed, `entry`, is what was withdrawn: all of it, or the one version that was."""
        w = self.data.get("withdrawn", {}).get(lid)
        versions = self.withdrawn_versions(lid)
        return bool(w) and (not versions or bool(entry) and entry.get("version_id") in versions)

    def acknowledge(self, lid: str) -> None:
        if lid in self.data.get("withdrawn", {}):
            self.data["withdrawn"][lid]["acknowledged"] = True
            self.save()

    def alarms(self) -> list[str]:
        """Withdrawn listings, or versions, still installed somewhere that nobody has acknowledged: CONDITION AMBER."""
        return [lid for lid, w in self.data.get("withdrawn", {}).items()
                if not w.get("acknowledged") and any(self.pulled(lid, es[lid]) for es in self.data["servers"].values()
                                                     if lid in es)]
