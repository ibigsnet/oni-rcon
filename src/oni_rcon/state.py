"""What oni-rcon keeps between runs about Forge content: what's installed on which server. One small JSON file beside the
config (forge-state.json), written whole through a temp file; held in memory only when there's nowhere to keep it.
"""
from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path, PurePosixPath

from .config import write_atomic


def norm(s) -> str:
    """A name as servers and catalogs both might spell it: "Guardian Rebuilt", "guardian_rebuilt", "guardian-rebuilt"."""
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").lower()).strip("_")


def refs_of(entry: dict) -> set[str]:
    """Every name an installed listing might go by on a server: the manifest's reference, its files' names, its title."""
    names = {entry.get("reference"), entry.get("title")}
    names |= {PurePosixPath(f.get("path", "")).stem for f in entry.get("files") or []}
    return {norm(n) for n in names if n} - {""}


class ForgeState:
    """{"version": 1, "servers": {server: {listing id: entry}}}. A server is its Server.where; an entry records the
    version installed and every file with its SHA-256, so what's on disk can always be told from what Forge has."""

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
        self.save()

    def where(self, lid: str) -> list[str]:
        return [s for s, entries in self.data["servers"].items() if lid in entries]

    def ids(self) -> set[str]:
        return {lid for entries in self.data["servers"].values() for lid in entries}

    def entry(self, lid: str) -> dict | None:
        """Any server's record of a listing: its title, author and kind are the same everywhere."""
        return next((e[lid] for e in self.data["servers"].values() if lid in e), None)
