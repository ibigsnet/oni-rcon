"""IBIGS 2026-11-09: old game types that Project Reclaimer 0.10 skips, brought up to its standard.

A game type saved as JSON keeps its settings by name ("options": {"rounds.teams": 1, ...}). Project Reclaimer 0.10
checks every one against its own table as the server starts, and a file holding a single value the table hasn't is
left out whole:

    Skipped content file .../game_variants/KOTH ....json: Teams enabled (rounds.teams) has no value 3.
    Valid choices: "Off" (0), "On" (1).

The game type is then simply missing from the server. This module finds those files (by checking them against 0.10's
table, and from the server's own log lines), says what is wrong in plain words, and rewrites only the values that have
one safe meaning in 0.10, after keeping the original byte for byte beside it. A value with no safe meaning is never
guessed: the file stays as it is and the report says why. Nothing is written without being asked to.

The table is 0.10.1's own (`dedicated gametype-catalog`, in gametype_catalog_010.json). What counts as safe:

- An Off/On setting (0.10 calls these flags) holding a number above 1 means On: every Off/On setting has its own key,
  so a file never packs other settings into one, and every value but 0 was written to turn it on. Negative: no meaning.
- The teams setting of a mode whose engine fixes it (Capture the Flag, Assault, Territories and VIP always play in
  teams; Infection and Juggernaut never do: `fixed_teams` in game_options.rs) becomes what the engine plays anyway.
- true and false in an Off/On setting become 1 and 0; a whole number written as 3.0 becomes 3; a blank (null) value
  is left out, which is how 0.10 files say "the game type's own value".
- A choice written by its label is read the way 0.10 reads it; a label 0.10 refuses because it names different values
  in different modes is written as this mode's number, as 0.10's own message asks.

Anything else (a number outside its range, an unknown option, a choice that doesn't exist) is reported and left alone.
`--game-type-value` leaves those settings out instead, so the game type's own value plays: what Project Reclaimer
0.10.2 does by itself as it loads such a file (`leave_out_unplayable`), for servers still on 0.10.1.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

CATALOG_FILE = Path(__file__).with_name("gametype_catalog_010.json")
MAX_FILE = 2 * 1024 * 1024   # bytes: the game reads no bigger game type (forge::MAX_FILE)
MAX_DEPTH = 8                # folders deep, as the game looks
SCAN_LIMIT = 20_000          # files looked at per folder, at most
BACKUP_SUFFIX = ".oni-backup"   # not .json, .bin or .mvar: the game never lists it
MAX_BACKUP_NAMES = 1000
I32 = (-2**31, 2**31 - 1)
# game_options.rs fixed_teams(): the native copies force teams on for the objective modes and off for the two that
# pick sides themselves, whatever the variant says.
FIXED_TEAMS = {"CaptureTheFlag": 1, "Assault": 1, "Territories": 1, "Vip": 1, "Infection": 0, "Juggernaut": 0}
TEAMS = "rounds.teams"
# Choice names earlier editors used, which 0.10 still reads (game_options.rs `former`, the motion tracker's).
FORMER = {"radar": {"moving players": 2, "all players": 3, "allies": 4}}
MODE_WORDS = {"CaptureTheFlag": "Capture the Flag", "KingOfTheHill": "King of the Hill", "Vip": "VIP",
              "EscalationSlayer": "Escalation Slayer"}
# What the server says about a game type it leaves out (0.10.0+), or plays without some of its settings (0.10.2+).
LOG_LINES = [
    (re.compile(r"Skipped content file (?P<path>.+?\.json): (?P<why>.+)$"), "skipped"),
    (re.compile(r"\[content\] (?P<path>.+?\.json) stays as it was: (?P<why>.+)$"), "skipped"),
    (re.compile(r"\[modes\] (?P<path>.+?\.json): the game type's value instead: (?P<why>.+)$"), "partly"),
]


@dataclass(frozen=True)
class Field:
    key: str
    kind: str      # flag, choice or number
    min: int
    max: int
    label: str
    labels: tuple[str, ...]

    def holds(self, value) -> bool:
        return type(value) is int and self.min <= value <= self.max

    def choice(self, value: int) -> str | None:
        i = value - self.min
        return self.labels[i] if 0 <= i < len(self.labels) else None

    def resolve(self, written: str) -> int | None:
        """The number a label (any case), an earlier editor's label or a number written as text stands for here."""
        w = written.strip()
        for i, label in enumerate(self.labels):
            if label.casefold() == w.casefold():
                return self.min + i
        former = FORMER.get(self.key.rpartition(".")[2], {}).get(w.casefold())
        if former is not None:
            return former
        if re.fullmatch(r"[+-]?\d+", w) and self.min <= int(w) <= self.max:
            return int(w)
        return None

    def valid(self) -> str:
        """0.10's own words for what this setting takes."""
        if self.labels:
            return "Valid choices: " + ", ".join(f'"{l}" ({self.min + i})' for i, l in enumerate(self.labels)) + "."
        return f"Valid values: {self.min} to {self.max}."

    def shown(self, value: int) -> str:
        label = self.choice(value)
        return f"{label} ({value})" if label is not None else str(value)


@dataclass
class Catalog:
    modes: dict[str, dict[str, Field]]
    firefight: dict[str, Field]
    source: str

    def by_key(self, key: str) -> list[Field]:
        return [f for fields in [*self.modes.values(), self.firefight] if (f := fields.get(key))]


@lru_cache(maxsize=1)
def catalog() -> Catalog:
    raw = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    specs = raw["specs"]

    def fields(table: dict) -> dict[str, Field]:
        out = {}
        for key, i in table.items():
            kind, lo, hi, label, labels = specs[i]
            out[key] = Field(key, kind, lo, hi, label, tuple(labels))
        return out

    return Catalog({m: fields(t) for m, t in raw["modes"].items()}, fields(raw["firefight"]), raw["source"])


@dataclass
class Change:
    key: str
    old: object
    new: object          # REMOVE: the setting is left out
    why: str
    label: str = ""


REMOVE = object()


@dataclass
class Finding:
    path: Path
    mode: str = ""
    name: str = ""
    state: str = "ok"    # ok, fixable, stuck (something has no safe meaning), other (not a game type), unreadable
    changes: list[Change] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)       # what the server said about it
    bom: bool = False

    @property
    def skipped(self) -> bool:
        """The server leaves it out (or would, as it starts)."""
        return self.state in ("fixable", "stuck") or any(w.startswith("skipped:") for w in self.log)

    def as_dict(self) -> dict:
        return {"path": str(self.path), "name": self.name, "mode": self.mode, "state": self.state,
                "changes": [{"option": c.key, "label": c.label, "from": c.old,
                             "to": None if c.new is REMOVE else c.new, "removed": c.new is REMOVE, "why": c.why}
                            for c in self.changes],
                "problems": self.problems, "server_said": self.log}


def _value(field: Field, key: str, value, mode: str, cat: Catalog) -> tuple[str, object, str]:
    """('ok', value, '') | ('change', new, why) | ('problem', None, why) for one setting of a game type."""
    shown = json.dumps(value, ensure_ascii=False)
    if value is None:
        return "change", REMOVE, "a blank value; left out, so the game type's own value plays (how 0.10 files say it)"
    if type(value) is bool:
        if field.kind == "flag":
            return "change", int(value), f"{shown} written for an Off/On setting; 0.10 reads only 0 and 1"
        return "problem", None, f"{field.label} ({key}) has {shown}, which 0.10 can't read. {field.valid()}"
    if type(value) is float:
        if not (math.isfinite(value) and value.is_integer()):
            return "problem", None, f"{field.label} ({key}) has {shown}, a fraction. {field.valid()}"
        number = int(value)
        kind, new, why = _value(field, key, number, mode, cat)
        if kind == "problem":
            return kind, new, why
        return "change", new, f"{shown} written with a decimal point, which 0.10 can't read" + (f"; {why}" if why else "")
    if type(value) is str:
        found = {v for f in cat.by_key(key) if (v := f.resolve(value)) is not None}
        if len(found) == 1 and field.holds(next(iter(found))):
            return "ok", value, ""     # 0.10 reads the label itself: the file stays as it is
        own = field.resolve(value)
        if own is not None:
            return "change", own, (f"the label {shown} names different values in different modes, so 0.10 refuses "
                                   f"it; written as {mode}'s number")
        return "problem", None, f"{field.label} ({key}) has no choice {shown}. {field.valid()}"
    if type(value) is not int:
        return "problem", None, f"{field.label} ({key}) has {shown}, which isn't a setting's value. {field.valid()}"
    if not I32[0] <= value <= I32[1]:
        return "problem", None, f"{field.label} ({key}) has no value {value}. {field.valid()}"
    if field.holds(value):
        return "ok", value, ""
    if key == TEAMS and mode in FIXED_TEAMS:
        forced = FIXED_TEAMS[mode]
        words = MODE_WORDS.get(mode, mode)
        return "change", forced, (f"{value} isn't Off or On; {words} always plays "
                                  f"{'in teams' if forced else 'without teams'}, whatever the file says")
    if field.kind == "flag" and value > 1:
        return "change", 1, (f"{value} isn't Off (0) or On (1); every Off/On setting has a key of its own, so any "
                             f"number above 1 was written to turn it on")
    return "problem", None, f"{field.label} ({key}) has no value {value}. {field.valid()}"


def _check_options(options, fields: dict[str, Field], mode: str, cat: Catalog, where: str,
                   finding: Finding, game_type_value: bool) -> dict | None:
    """The options as 0.10 wants them (or None if nothing changes), noting each change and problem."""
    if not isinstance(options, dict):
        finding.problems.append(f"{where} isn't a list of settings")
        return None
    out, changed = {}, False
    for key, value in options.items():
        f = fields.get(key)
        if f is None:
            kind, new, why = "problem", None, f"Unknown game option for {MODE_WORDS.get(mode, mode)}: {key}"
        else:
            kind, new, why = _value(f, key, value, mode, cat)
        if kind == "problem" and game_type_value:
            kind, new, why = "change", REMOVE, why + " Left out, so the game type's own value plays (as 0.10.2 does)."
        if kind == "change":
            finding.changes.append(Change(key, value, new, why, f.label if f else key))
            changed = True
            if new is REMOVE:
                continue
            out[key] = new
        else:
            if kind == "problem":
                finding.problems.append(why)
            out[key] = value
    return out if changed else None


def inspect(path: Path, game_type_value: bool = False) -> tuple[Finding, dict | None]:
    """What 0.10 makes of one file, and the file as it should be (None when nothing would change)."""
    finding = Finding(Path(path))
    try:
        if path.stat().st_size > MAX_FILE:
            finding.state = "other"
            return finding, None
        raw = path.read_bytes()
    except OSError as e:
        finding.state, finding.problems = "unreadable", [f"can't read it: {e.strerror or e}"]
        return finding, None
    finding.bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        finding.state = "other"       # not JSON: the game's own complaint, if any, is in its log
        return finding, None
    if not (isinstance(data, dict) and isinstance(data.get("mode"), str) and "name" in data):
        finding.state = "other"       # a playlist, a manifest, a note: not a game type
        return finding, None
    cat = catalog()
    finding.mode, finding.name = data["mode"], str(data.get("name", ""))
    fields = cat.modes.get(finding.mode)
    new = dict(data)
    changed = False
    if fields is not None and "options" in data:
        options = _check_options(data["options"], fields, finding.mode, cat, "options", finding, game_type_value)
        if options is not None:
            new["options"], changed = options, True
    odst = data.get("odst")
    if isinstance(odst, dict) and isinstance(odst.get("options"), dict) and odst["options"]:
        options = _check_options(odst["options"], cat.firefight, "Firefight", cat, "odst.options", finding,
                                 game_type_value)
        if options is not None:
            new["odst"], changed = {**odst, "options": options}, True
    if fields is None and not changed and not finding.problems:
        finding.state = "other"       # campaign, Firefight-only and other kinds this table doesn't cover
        return finding, None
    if finding.bom:
        finding.changes.insert(0, Change("(file)", "byte-order mark", "none", "a byte-order mark at the start, which "
                                         "the game can't read past", "the start of the file"))
        changed = True
    finding.state = "stuck" if finding.problems else ("fixable" if changed else "ok")
    return finding, (new if changed else None)


def folders_of(game: str | Path | None = None, content: str | Path | None = None) -> list[Path]:
    """Where a 0.10 server reads JSON game types from (host_content::content_files): its game folder's Halo 3
    variants and its content folder's Maps, Game Modes and Mods."""
    out = []
    if game:
        out += [Path(game) / "halo3" / f for f in ("game_variants", "map_variants", "hopper_game_variants",
                                                     "hopper_map_variants")]
    if content:
        out += [Path(content) / f for f in ("Maps", "Game Modes", "Mods")]
    return out


def json_files(folder: Path) -> list[Path]:
    found: list[Path] = []

    def walk(d: Path, depth: int) -> None:
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            return
        for e in entries:
            if len(found) >= SCAN_LIMIT:
                return
            try:
                if e.is_dir(follow_symlinks=False):
                    if depth < MAX_DEPTH:
                        walk(Path(e.path), depth + 1)
                elif e.is_file(follow_symlinks=False) and e.name.lower().endswith(".json"):
                    found.append(Path(e.path))
            except OSError:
                continue

    walk(Path(folder), 0)
    return found


def read_log(text: str) -> list[tuple[str, str, str]]:
    """(path as the server names it, why, skipped|partly) for each game type the server's log complains about."""
    out = []
    for line in text.splitlines():
        for rx, kind in LOG_LINES:
            if m := rx.search(line.rstrip()):
                out.append((m["path"].strip(), m["why"].strip(), kind))
                break
    return out


def _match(logged: str, files: list[Path]) -> Path | None:
    """The local file a server path names: the one sharing the longest tail of folders and name (a container sees
    the same folder under another root). None if no file or two equally good ones."""
    want = [p for p in logged.replace("\\", "/").split("/") if p]
    best, score, tie = None, 0, False
    for f in files:
        have, n = f.parts, 0
        while n < min(len(want), len(have)) and want[-1 - n] == have[-1 - n]:
            n += 1
        if n > score:
            best, score, tie = f, n, False
        elif n == score and n:
            tie = True
    return None if tie or not score else best


def scan(folders, log_text: str = "", game_type_value: bool = False) -> tuple[list[Finding], list[str]]:
    """Every game type 0.10 would leave out (or that the log says it left out) in `folders`; and the log's complaints
    about files none of the folders hold."""
    files: list[Path] = []
    for folder in folders:
        files += json_files(Path(folder))
    findings: dict[Path, Finding] = {}
    for f in files:
        finding, _ = inspect(f, game_type_value)
        if finding.state in ("fixable", "stuck", "unreadable"):
            findings[f] = finding
    elsewhere = []
    for logged, why, kind in read_log(log_text):
        local = _match(logged, files)
        if local is None:
            elsewhere.append(f"{logged}: {why}")
            continue
        finding = findings.get(local) or inspect(local, game_type_value)[0]
        if finding.state == "ok":
            finding.state = "stuck"   # the server refuses it for a reason outside the settings table
            finding.problems.append(f"the server says: {why}")
        word = f"{kind}: {why}"
        if word not in finding.log:
            finding.log.append(word)
        findings[local] = finding
    return sorted(findings.values(), key=lambda f: str(f.path)), elsewhere


def _write_atomic(path: Path, data: bytes, like: os.stat_result | None) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".oni-upgrade-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        if like is not None:
            os.chmod(tmp, like.st_mode & 0o7777)
            if hasattr(os, "chown"):
                try:
                    os.chown(tmp, like.st_uid, like.st_gid)
                except OSError:
                    pass      # not ours to give away: the file keeps this login's ownership
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def back_up(path: Path, original: bytes) -> Path:
    """The original, byte for byte, beside it (NAME.json.oni-backup; -2, -3... when another is already there). A
    second run reuses a copy that already matches."""
    for n in range(1, MAX_BACKUP_NAMES + 1):
        name = path.with_name(path.name + BACKUP_SUFFIX + ("" if n == 1 else f"-{n}"))
        try:
            if name.read_bytes() == original:
                return name
            continue
        except FileNotFoundError:
            pass
        st = path.stat()
        _write_atomic(name, original, st)
        if name.read_bytes() != original:
            raise OSError(f"the backup {name.name} didn't read back the same")
        os.utime(name, ns=(st.st_atime_ns, st.st_mtime_ns))
        return name
    raise OSError("too many backups of this file beside it")


def render(data: dict, original: bytes) -> bytes:
    """The game type as 0.10's own pretty JSON writes it, keeping the original's order and trailing newline."""
    text = json.dumps(data, indent=2, ensure_ascii=False)
    return (text + ("\n" if original.rstrip(b" \t\r").endswith(b"\n") else "")).encode("utf-8")


def upgrade_file(path: Path, game_type_value: bool = False) -> tuple[Finding, Path | None]:
    """Backs the file up and writes it in 0.10's terms; the backup, or None when nothing changed. A file that still
    has something 0.10 refuses afterwards goes back to how it was."""
    finding, new = inspect(path, game_type_value)
    if new is None or finding.state != "fixable":
        return finding, None          # nothing to do, or something with no safe meaning: the file stays as it is
    original = path.read_bytes()
    st = path.stat()
    backup = back_up(path, original)
    _write_atomic(path, render(new, original), st)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))   # lists sort by date: keep its place
    after, again = inspect(path, game_type_value)
    if again is not None or after.state != "ok":
        _write_atomic(path, original, st)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        raise OSError("the rewritten file didn't check out; put back as it was")
    return finding, backup


def upgrade_all(findings: list[Finding], game_type_value: bool = False) -> tuple[list, list]:
    """Upgrades every fixable file found: ([(finding, backup)], [(finding, why)])."""
    done, failed = [], []
    for f in findings:
        if f.state != "fixable":
            continue
        try:
            _, backup = upgrade_file(f.path, game_type_value)
            done.append((f, backup))
        except OSError as e:
            failed.append((f, str(e)))
    return done, failed


def local_folders(servers) -> list[Path]:
    """Where the servers on this machine (no ssh, no URL) read game types from, each folder once."""
    seen: list[Path] = []
    for s in servers:
        if getattr(s, "ssh", "") or getattr(s, "url", ""):
            continue
        game = os.path.expanduser(getattr(s, "game_dir", "") or "") or None
        content = os.path.expanduser(getattr(s, "content_dir", "") or "") or None
        for folder in folders_of(game, content):
            if folder not in seen:
                seen.append(folder)
    return seen


def short(findings: list[Finding], most: int = 12) -> str:
    """A few lines for a dialog: each file and what changes in it."""
    cat, lines = catalog(), []
    shown = [f for f in findings if f.skipped or f.changes]
    for f in shown[:most]:
        lines.append(f"{f.path.name}")
        for c in f.changes:
            fld = cat.modes.get(f.mode, {}).get(c.key) or cat.firefight.get(c.key)
            new = "left out" if c.new is REMOVE else (fld.shown(c.new) if fld and type(c.new) is int else str(c.new))
            lines.append(f"  {c.label}: {json.dumps(c.old, ensure_ascii=False)} -> {new}")
        for p in f.problems:
            lines.append(f"  can't fix safely: {p[:160]}")
    if len(shown) > most:
        lines.append(f"...and {len(shown) - most} more (oni gametypes doctor lists them all)")
    return "\n".join(lines)


# ---- words --------------------------------------------------------------------------------------------------------

def _shown(c: Change, cat: Catalog, mode: str) -> str:
    if c.key == "(file)":
        return f"  - {c.why}: removed"
    f = cat.modes.get(mode, {}).get(c.key) or cat.firefight.get(c.key)
    old = json.dumps(c.old, ensure_ascii=False)
    if c.new is REMOVE:
        return f"  - {c.label} ({c.key}): {old} -> left out. {c.why}"
    new = f.shown(c.new) if f else str(c.new)
    return f"  - {c.label} ({c.key}): {old} -> {new}. {c.why}"


def summary(findings: list[Finding]) -> tuple[int, int]:
    skipped = [f for f in findings if f.skipped]
    return len(skipped), sum(1 for f in skipped if f.state == "fixable")


def doctor_notes(findings: list[Finding], elsewhere: list[str]) -> list[dict]:
    """`oni doctor`'s notes ({check, state: ok|warn|fail, text}): one line to say how many, then one per file."""
    n, m = summary(findings)
    if not n and not elsewhere and not any(f.state == "unreadable" for f in findings):
        return [{"check": "gametypes", "state": "ok", "text": "every game type is in Project Reclaimer 0.10's format"}]
    notes = [{"check": "gametypes", "state": "warn",
              "text": f"old game types the server skips: {n}, fixable: {m}"
                      + (" (see: oni gametypes upgrade)" if m else "")}]
    for f in findings:
        if not f.skipped:
            continue
        what = "fixable" if f.state == "fixable" else "can't be fixed safely: " + "; ".join(f.problems)[:300]
        notes.append({"check": "gametypes", "state": "warn", "text": f"{f.path.name}: {what}"})
    for line in elsewhere:
        notes.append({"check": "gametypes", "state": "warn",
                      "text": f"the server skipped a file oni didn't find here: {line}"[:400]})
    unread = [f for f in findings if f.state == "unreadable"]
    if unread:
        notes.append({"check": "gametypes", "state": "warn", "text": f"couldn't check {len(unread)} game type file(s): "
                      f"{unread[0].problems[0]} (try: sudo oni gametypes doctor)"})
    return notes


def report(findings: list[Finding], elsewhere: list[str]) -> str:
    cat = catalog()
    n, m = summary(findings)
    lines = [f"Old game types the server skips: {n}, fixable: {m}"]
    for f in findings:
        if f.state == "unreadable":
            lines += ["", f"{f.path}", f"  ? couldn't check it: {'; '.join(f.problems)}"]
            continue
        if not f.skipped and not f.changes:
            continue
        words = MODE_WORDS.get(f.mode, f.mode)
        lines.append("")
        lines.append(f"{f.path}" + (f"  ({words})" if words else ""))
        for c in f.changes:
            lines.append(_shown(c, cat, f.mode))
        for p in f.problems:
            lines.append(f"  ! can't fix safely, left as it is: {p}")
        for w in f.log:
            lines.append(f"  server log: {w}")
    for line in elsewhere:
        lines += ["", f"The server skipped a file oni didn't find in these folders: {line}"]
    return "\n".join(lines)


# ---- command line: oni-rcon --gametypes (the IBIGS helper's `oni gametypes`) ---------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="oni gametypes", description="Find the game types Project Reclaimer 0.10 skips, and upgrade them safely.",
        epilog="Folders: --game is the server's game folder (halo3/game_variants and the others are read under it), "
               "--content its content folder (Maps, Game Modes, Mods); any other folder by name. The original of "
               "each file changed is kept beside it as NAME.json.oni-backup.")
    ap.add_argument("action", choices=["doctor", "upgrade"], help="doctor: report only. upgrade: fix what is safe")
    ap.add_argument("folders", nargs="*", help="more folders to check, with their subfolders")
    ap.add_argument("--game", help="the server's game folder")
    ap.add_argument("--content", help="the server's content folder")
    ap.add_argument("--log", help="the server's log, to find the files it skipped (- reads it from stdin)")
    ap.add_argument("--dry-run", action="store_true", help="upgrade: say what would change, change nothing")
    ap.add_argument("--yes", action="store_true", help="upgrade: don't ask first (for scripts)")
    ap.add_argument("--game-type-value", action="store_true",
                    help="settings with no safe value are left out, so the game type's own value plays (as 0.10.2 "
                         "does as it loads), instead of the file staying as it is")
    ap.add_argument("--json", action="store_true", help="machine-readable")
    a = ap.parse_args(argv)
    folders = folders_of(a.game, a.content) + [Path(f) for f in a.folders]
    if not folders:
        ap.error("say where the game types are: --game, --content or a folder")
    log_text = ""
    if a.log:
        try:
            log_text = sys.stdin.read() if a.log == "-" else Path(a.log).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            ap.error(f"can't read the log: {e}")
    findings, elsewhere = scan(folders, log_text, a.game_type_value)
    n, m = summary(findings)
    if a.action == "doctor" or a.dry_run or not m:
        if a.json:
            print(json.dumps({"fixed": False, "results": doctor_notes(findings, elsewhere), "skipped": n, "fixable": m,
                              "files": [f.as_dict() for f in findings], "not_found": elsewhere,
                              "changed": False}, indent=2, ensure_ascii=False))
        else:
            print(report(findings, elsewhere))
            if m and a.action == "upgrade":
                print("\nNothing was changed (--dry-run).")
            elif m:
                print("\nNothing was changed. To fix them:  oni gametypes upgrade")
        return 1 if n or elsewhere else 0
    if not a.json:
        print(report(findings, elsewhere))
    if not a.yes:
        if not sys.stdin.isatty() or a.log == "-":
            print("\nNothing was changed: add --yes to upgrade without being asked.", file=sys.stderr)
            return 1
        if input(f"\nUpgrade {m} game type{'s' if m != 1 else ''}? Each original is kept beside it. [y/N] "
                 ).strip().lower() not in ("y", "yes"):
            print("Nothing was changed.")
            return 1
    done, failed = upgrade_all(findings, a.game_type_value)
    if a.json:
        print(json.dumps({"upgraded": [{"path": str(f.path), "backup": str(b) if b else None} for f, b in done],
                          "failed": [{"path": str(f.path), "why": why} for f, why in failed],
                          "left": [f.as_dict() for f in findings if f.state != "fixable"], "changed": bool(done)},
                         indent=2, ensure_ascii=False))
    else:
        print()
        for f, backup in done:
            print(f"Upgraded {f.path.name}" + (f" (the original is {backup.name})" if backup else ""))
        for f, why in failed:
            print(f"Couldn't upgrade {f.path.name}: {why}")
        if done:
            print("The server lists them at its next content refresh or restart.")
    stuck = sum(1 for f in findings if f.skipped and f.state != "fixable")
    return 1 if failed or stuck or elsewhere else 0


if __name__ == "__main__":
    sys.exit(main())
