"""What Project Reclaimer 0.10 lets a host set in dedicated.toml: the catalog the F-key screens are built from, plain
checks, and a careful editor for the file.

The keys are the server's own (its docs/servers.md); every one here was put through a real 0.10 `dedicated check`. A
0.9.23 server refuses a file that has any of them, so nothing here is written unless the server runs 0.10 or newer.
Each setting has one simple control; ADVANCED sets any other key the same way, and `dedicated check` still decides.

Only a machine beside the server can change the file: the IBIGS `oni` helper does that (`oni config`), having the server
check the new file first. Anywhere else the same screens hand back the lines to paste. `oni-rcon --edit-settings` edits
text on stdin, so the helper needs no copy of the logic: {"toml", "index", "changes", "version"} in, {"toml"} or {"error"} out.
"""
from __future__ import annotations

import copy
import json
import re
import sys
import tomllib
from dataclasses import dataclass

from .update import semver

MIN_VERSION = (0, 10, 0)
DEFAULT = "__default__"  # a choice that takes the key out of the file: the server's own default applies
NEEDS_REACH = ("NEEDS REACH: players without Halo: Reach are refused at the door. Most players don't have it, so a "
               "server that adds it can look empty. Choose it only if your players own Reach.")
NEEDS_ODST = "Needs ODST: players without Halo 3: ODST are told they can't join this server's games."


def supports(version) -> bool:
    """Whether a server of this version takes the 0.10 keys and commands. Unknown is no: 0.9.23 refuses them."""
    v = semver(version)
    return bool(v) and v >= MIN_VERSION


@dataclass(frozen=True)
class Setting:
    key: str  # dotted, under the server's own block: votes.limits.calls_per_hour is [server.votes.limits]
    label: str
    kind: str  # choice | int | text | words (a comma list)
    options: tuple = ()  # choice: (label, value), the value being what the file gets
    low: int = 0
    high: int = 0
    default: str = ""  # the server's default, in words
    warn: dict | None = None  # value -> what to say before saving it (a value is matched as JSON)


WHO = (("Everyone", "everyone"), ("Members", "members"), ("VIP and up", "vip"), ("Mods and up", "mod"),
       ("Admins only", "admin"), ("Nobody (turned off)", "off"))
TELL = (("Everyone sees it", "all"), ("Staff only", "staff"), ("Nobody", "off"))
ONOFF = (("On", True), ("Off", False))


def S(key, label, kind, *options, low=0, high=0, default="", warn=None):
    return Setting(key, label, kind, tuple(options), low, high, default, warn)


# The menus: one screen a section, the common settings only. Everything else is under ADVANCED and the file.
SECTIONS = {
    "votes": ("VOTE RULES", "Who may call each vote, and how often. Applies at once; players stay on.", [
        S("votes.kick", "Who may call a kick vote", "choice", *WHO, default="everyone"),
        S("votes.endround", "Who may vote to end the round", "choice", *WHO, default="everyone"),
        S("votes.endgame", "Who may vote to end the game", "choice", *WHO, default="everyone"),
        S("votes.shuffle", "Who may vote to shuffle teams", "choice", *WHO, default="everyone"),
        S("votes.limits.caller_min_joined_seconds", "Seconds on the server before calling a vote", "int", high=86400,
          default="no limit"),
        S("votes.limits.caller_cooldown_seconds", "Seconds a caller waits after a failed vote", "int", high=86400,
          default="60 (kick calls 180)"),
        S("votes.limits.calls_per_hour", "Most calls by one player an hour", "int", high=1000, default="no limit"),
        S("votes.limits.quorum_percent", "Share of players who must answer (%)", "int", high=100, default="the game's"),
        S("votes.limits.pass_percent", "Share of answers that must say yes (%)", "int", high=100, default="the game's"),
    ]),
    "joins": ("JOIN QUEUE AND GATES", "What happens to new players when the server is full or busy. Applies at once.", [
        S("joins", "New players", "choice", ("Come in", "open"), ("Wait in the queue", "queue"),
          ("Are kept out (paused)", "closed"), default="come in",
          warn={'"closed"': "Nobody new can join until you open it again. Players on the server stay."}),
        S("join_gates", "Hold new players during", "choice", ("Nothing (join any time)", []),
          ("A game in progress: they join at the next game (good for Infection)", ["in_game"]),
          ("Loading, between games", ["countdown", "loading"]),
          ("Voting, loading and the end of a game", ["vote", "countdown", "loading", "post_game"]),
          default="nothing"),
        S("queue_max", "Places in the line", "int", high=128, default="7"),
        S("vip_queue_max", "Places in the VIP line", "int", high=128, default="3"),
        S("vip_priority", "VIP share of free places (%)", "int", high=100, default="75"),
        S("queue_max_wait", "Longest wait in line (seconds)", "int", high=86400, default="3600"),
    ]),
    "announce": ("ANNOUNCEMENTS", "Who reads it when something happens. The player affected is always told. Applies "
                 "at once.", [
        S("announce.kick", "Kicks", "choice", *TELL, default="the server's"),
        S("announce.ban", "Bans", "choice", *TELL, default="the server's"),
        S("announce.mute", "Mutes", "choice", *TELL, default="the server's"),
        S("announce.votes", "Votes and polls", "choice", *TELL, default="the server's"),
        S("announce.joins", "Joins and leaves", "choice", *TELL, default="nobody"),
        S("command_echo", "Staff see the commands players type", "choice", ("Yes", "staff"), ("No", "none"),
          default="yes"),
        S("voice_mute", "A voice-muted player's microphone", "choice", ("Turned off, and they're told", "deny"),
          ("Stays on, but nobody hears them", "drop"), default="turned off"),
    ]),
    "staff": ("ROLE SETTINGS", "How far the standard roles go. Applies at once.", [
        S("moderator_ban_minutes", "A Mod's longest ban (minutes)", "int", low=1, high=525600, default="60"),
        S("devs_as_vip", "Project Reclaimer developers count as VIP", "choice", *ONOFF, default="off"),
    ]),
    "models": ("PLAYER MODELS", "Which other games' characters players may pick. The server restarts for this once it "
               "has no players.", [
        S("customization", "Characters added", "choice", ("Halo 3's only", []),
          ("Add Halo 3: ODST's", ["Halo 3: ODST"]), ("Add Halo: Reach's (NEEDS REACH)", ["Halo: Reach"]),
          ("Add ODST's and Reach's (NEEDS REACH)", ["Halo 3: ODST", "Halo: Reach"]), default="Halo 3's only",
          warn={'["Halo: Reach"]': NEEDS_REACH, '["Halo 3: ODST", "Halo: Reach"]': NEEDS_REACH}),
        S("require_customization", "Players must use them", "choice", *ONOFF, default="off"),
        S("no_vanilla_characters", "Nobody plays Halo 3's own characters", "choice", *ONOFF, default="off"),
    ]),
    "coop": ("CO-OP NETWORK", "How this server's campaign missions are played. Only co-op games read it. The server "
             "restarts for it once it has no players.", [
        S("coop_network", "Co-op network", "choice", ("Lockstep: Halo 3's own", "lockstep"),
          ("Host: the server simulates, players predict their own (experimental)", "host"), default="lockstep",
          warn={'"host"': "Experimental: try it on a test lobby before your players do."}),
    ]),
    "forge": ("FORGE OBJECTS", "Extra objects in Forge's palette. A map's own game's objects are always there. The "
              "server restarts for these once it has no players.", [
        S("odst_objects", "Halo 3: ODST's objects, on Halo 3's maps", "choice", *ONOFF, default="off",
          warn={"true": NEEDS_ODST}),
        S("reach_objects", "Halo: Reach's objects, on Halo 3's and ODST's maps", "choice", *ONOFF, default="off",
          warn={"true": NEEDS_REACH}),
        S("forge_primitives", "Project Reclaimer's primitives", "choice", *ONOFF, default="as the map offers"),
        S("halo3_objects", "Halo 3's objects, on ODST's maps", "choice", *ONOFF, default="as the map offers"),
    ]),
    "more": ("MORE SETTINGS", "Less often needed.", [
        S("kick_blocks", "How long kicks keep a player out, rising (e.g. 10m, 30m, 60m, 120m; none: not at all)",
          "words", default="10m, 30m, 60m, 120m"),
        S("metrics_http", "Health and Prometheus address, on this machine only (e.g. 127.0.0.1:9310)", "text",
          default="off", warn={}),
        S("server_message_color", "Colour of the console's chat lines (#RRGGBB)", "text", default="the game's"),
        S("queue_mode", "Next-game queue", "choice", ("Plays in turn, no ballot", "play"),
          ("Goes on the ballot", "ballot"), default="plays in turn"),
    ]),
}
BY_KEY = {s.key: s for _, _, rows in SECTIONS.values() for s in rows}

# Never set from a menu: they're how the server is reached, or what it is. dedicated.toml is still there for them.
PROTECTED = re.compile(r"^rcon|(^|\.)(rcon\w*|password|port|game|data|bans|public_address|bind_address|name|map|playlist|"
                       r"variant|campaign|odst|reach|enabled|admins)$")
SECRET = re.compile(r"password|secret|token|api_key|social_key", re.I)
KEY = re.compile(r"[a-z][a-z0-9_]*(\.[a-z0-9_-]+){0,3}")
DURATION = re.compile(r"\d+[smhd]?")
ROLE_NAME = re.compile(r"[a-z0-9_-]{1,32}")
PERMISSIONS = ("kick", "ban.temp", "ban.perm", "unban", "mute.text", "mute.voice", "mute.shadow", "vote.call.kick",
               "vote.call.endround", "vote.call.endgame", "vote.call.shuffle", "vote.cancel", "vote.pass", "vote.exempt",
               "poll.start", "poll.commands", "queue.edit", "map.change", "game.end", "teams.move", "teams.shuffle",
               "joins.gate", "settings.change", "say", "see.commands", "see.reports", "see.shadowmuted", "see.addresses",
               "bypass.queue", "bypass.vote_limits", "roles.grant", "voice.music", "bots.run")


def safe_view(data):
    """A copy of parsed settings with every password and secret left out, for a screen or a log."""
    if isinstance(data, dict):
        return {k: safe_view(v) for k, v in data.items() if not SECRET.search(k) and k != "rcon"}
    if isinstance(data, list):
        return [safe_view(v) for v in data]
    return data


def lookup(values: dict, key: str):
    """A dotted key in nested settings, or None."""
    for part in key.split("."):
        if not isinstance(values, dict) or part not in values:
            return None
        values = values[part]
    return values


def effective(top: dict, index: int, key: str):
    """(the value, where it's from): this server's block, else [defaults], else the file's top, else (None, "")."""
    servers = top.get("server") or []
    block = servers[index] if 0 <= index < len(servers) else {}
    for where, values in (("server", block), ("defaults", top.get("defaults") or {}), ("top", top)):
        v = lookup(values, key)
        if v is not None:
            return v, where
    return None, ""


def parse(setting: Setting, raw) -> object:
    """A control's value as the file holds it: DEFAULT or "" takes the key out (None). ValueError in plain words."""
    if raw in (DEFAULT, "", None):
        return None
    if setting.kind == "choice":
        value = json.loads(raw) if isinstance(raw, str) else raw
        if value not in [v for _, v in setting.options]:
            raise ValueError(f"{setting.label}: that isn't one of the choices.")
        return value
    if setting.kind == "int":
        if not re.fullmatch(r"\d{1,9}", str(raw).strip()):
            raise ValueError(f"{setting.label}: a whole number, please.")
        v = int(raw)
        if not setting.low <= v <= setting.high:
            raise ValueError(f"{setting.label}: from {setting.low} to {setting.high}.")
        return v
    text = str(raw).strip()
    if any(ord(c) < 32 for c in text):
        raise ValueError(f"{setting.label}: one line, please.")
    if setting.key == "kick_blocks":
        return kick_ladder(text)
    if setting.key == "metrics_http" and not re.fullmatch(r"(127\.0\.0\.1|localhost|\[::1\]):\d{1,5}", text):
        raise ValueError("The health address has no password, so it stays on this machine: 127.0.0.1:9310.")
    if setting.key == "server_message_color" and not re.fullmatch(r"#[0-9A-Fa-f]{6}", text):
        raise ValueError("A colour looks like #40A0FF.")
    return text


def kick_ladder(text: str) -> list[str]:
    """10m, 30m -> ["10m", "30m"]; none -> []: up to 8 rising steps."""
    if text.strip().lower() in ("none", "off", "0"):
        return []
    steps = [w for w in re.split(r"[,\s]+", text.strip()) if w]
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "": 1}
    if len(steps) > 8 or not all(DURATION.fullmatch(w) for w in steps):
        raise ValueError("Kick steps: up to 8 times, like 10m, 30m, 60m (or none).")
    secs = [int(re.match(r"\d+", w)[0]) * units[re.sub(r"\d", "", w)] for w in steps]
    if secs != sorted(set(secs)) or not all(secs):
        raise ValueError("Kick steps must rise, like 10m, 30m, 60m.")
    return steps


def advanced(key: str, raw: str) -> tuple[str, object]:
    """(key, value) from ADVANCED's two boxes. The value reads as TOML (true, 30, ["a", "b"]), else as plain text;
    empty takes the key out. The server's own check decides whether it means anything."""
    key = key.strip()
    if not KEY.fullmatch(key):
        raise ValueError("A key looks like queue_max or votes.limits.calls_per_hour.")
    if PROTECTED.search(key) or SECRET.search(key):
        raise ValueError(f"{key} is how the server is reached or what it plays: it has its own screen, or the file.")
    raw = raw.strip()
    if not raw:
        return key, None
    try:
        return key, tomllib.loads(f"v = {raw}")["v"]
    except tomllib.TOMLDecodeError:
        return key, raw


def custom_role(name: str, title: str, rank, can: str) -> dict:
    """The changes that add a role of the host's own. can: permission names, presets and removals, by commas."""
    name = name.strip().lower()
    if not ROLE_NAME.fullmatch(name) or name in ("host", "members", "everyone"):
        raise ValueError("A role's name is short, with letters and numbers, like dj.")
    title = title.strip() or name.upper()
    if len(title) > 16 or any(ord(c) < 32 for c in title):
        raise ValueError("A role's title is one line of up to 16 characters.")
    try:
        rank = int(rank)
    except (TypeError, ValueError):
        raise ValueError("A role's rank is a whole number from 1 to 39.") from None
    if not 1 <= rank <= 39:
        raise ValueError("A role's rank is from 1 to 39: VIP is 10, Mod 20, Admin 30.")
    words = [w for w in re.split(r"[,\s]+", can.strip()) if w]
    for word in words:
        bare = word.lstrip("-").split(":")[0]
        if word.lstrip("-").startswith("preset:") or bare in PERMISSIONS or bare.endswith(".*") or bare == "*":
            continue
        raise ValueError(f"'{word}' isn't a permission. Some are: kick, mute.text, poll.start, voice.music, say.")
    return {f"roles.{name}.title": title, f"roles.{name}.rank": rank, f"roles.{name}.can": words}


def warnings(changes: dict) -> list[str]:
    """What to say before saving these, largest first."""
    out = []
    for key, value in changes.items():
        setting = BY_KEY.get(key)
        words = (setting.warn or {}).get(json.dumps(value)) if setting and value is not None else None
        if words and words not in out:
            out.append(words)
    return sorted(out, key=lambda w: not w.startswith("NEEDS REACH"))


# --- the file ---------------------------------------------------------------------------------------------------
def toml_str(s: str) -> str:
    out = str(s).replace("\\", "\\\\").replace('"', '\\"')
    for a, b in (("\n", "\\n"), ("\r", "\\r"), ("\t", "\\t")):
        out = out.replace(a, b)
    return '"' + re.sub(r"[\x00-\x1f\x7f]", lambda m: f"\\u{ord(m[0]):04x}", out) + '"'


def toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {toml_value(x)}" for k, x in v.items()) + " }"
    return toml_str(v)


def snippet(changes: dict) -> str:
    """The lines to put in the server's [[server]] block, for a console that can't reach the file."""
    plain = {k: v for k, v in changes.items() if "." not in k}
    out = [f"{k} = {toml_value(v)}" if v is not None else f"# remove {k}" for k, v in plain.items()]
    tables: dict[str, list[str]] = {}
    for key, value in changes.items():
        if "." in key:
            head, _, leaf = key.rpartition(".")
            tables.setdefault(head, []).append(f"{leaf} = {toml_value(value)}" if value is not None else f"# remove {leaf}")
    for head, rows in tables.items():
        out += ["", f"[server.{head}]", *rows]
    return "\n".join(out)


HEAD = re.compile(r"\s*\[")
BLOCK = re.compile(r"\s*\[\[\s*server\s*\]\]")
SUB = re.compile(r"\s*\[\s*server\.([A-Za-z0-9_.-]+)\s*\]\s*(#.*)?$")
INSIDE = re.compile(r"\s*\[\[?\s*server\.")


def _block(lines: list[str], index: int) -> tuple[int, int]:
    """Where the index-th [[server]] and everything belonging to it lie: (its head's line, the line after)."""
    starts = [i for i, ln in enumerate(lines) if BLOCK.match(ln)]
    if index >= len(starts):
        raise ValueError(f"The file has no server {index + 1}.")
    start = starts[index]
    end = next((i for i in range(start + 1, len(lines)) if HEAD.match(lines[i]) and not INSIDE.match(lines[i])), len(lines))
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return start, end


def _value_end(lines: list[str], at: int) -> int:
    """The line after a key's value, which may spread over lines (an array)."""
    depth, i = 0, at
    while i < len(lines):
        text = re.sub(r'"(?:[^"\\]|\\.)*"', '""', lines[i]).split("#", 1)[0]
        depth += text.count("[") - text.count("]")
        i += 1
        if depth <= 0:
            break
    return i


def _drop_key(lines: list[str], lo: int, hi: int, leaf: str) -> tuple[int, int]:
    """Takes `leaf = ...` out of lines[lo:hi]: (where it was or -1, how many lines went)."""
    pattern = re.compile(r"\s*" + re.escape(leaf) + r"\s*=")
    for i in range(lo, hi):
        if pattern.match(lines[i]):
            stop = _value_end(lines, i)
            del lines[i:stop]
            return i, stop - i
    return -1, 0


def _section(lines: list[str], index: int, head: str) -> tuple[int, int] | None:
    """(first line of [server.<head>]'s keys, the line after them) within a server's block."""
    start, end = _block(lines, index)
    for i in range(start + 1, end):
        m = SUB.match(lines[i])
        if m and m[1] == head:
            stop = next((j for j in range(i + 1, end) if HEAD.match(lines[j])), end)
            return i + 1, stop
    return None


def _set_path(block: dict, key: str, value) -> None:
    parts = key.split(".")
    here = block
    for part in parts[:-1]:
        if not isinstance(here.get(part), dict):
            here[part] = {}
        here = here[part]
    if value is None:
        here.pop(parts[-1], None)
    else:
        here[parts[-1]] = value
    _prune(block)  # a table left with nothing in it isn't kept


def _prune(d: dict) -> None:
    for k in list(d):
        if isinstance(d[k], dict):
            _prune(d[k])
            if not d[k]:
                del d[k]


def edit(text: str, index: int, changes: dict) -> str:
    """dedicated.toml's text with these keys changed in its index-th [[server]], nothing else touched: comments and the
    file's layout stay. A key under a table goes under that table's own header; a plain one goes above the block's
    first table, where TOML needs it (coop_network above [server.campaign]). The result is parsed and compared before
    it's returned: a layout this can't edit safely raises ValueError, and the file stays as it was."""
    before = tomllib.loads(text)
    if index >= len(before.get("server") or []):
        raise ValueError(f"The file has no server {index + 1}.")
    want = copy.deepcopy(before)
    for key, value in changes.items():
        _set_path(want["server"][index], key, value)
    lines = text.splitlines(keepends=True)
    nl = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += nl
    for key, value in changes.items():
        if isinstance(value, dict):
            raise ValueError(f"{key}: set its keys one by one.")
        parts = key.split(".")
        if len(parts) == 1:
            _set_plain(lines, index, key, value, nl)
        else:
            _set_nested(lines, index, parts, value, nl)
    out = "".join(lines)
    refused = "The settings file is laid out in a way oni won't edit by itself. Nothing was changed."
    try:
        after = tomllib.loads(out)
    except tomllib.TOMLDecodeError:
        raise ValueError(refused) from None
    if after != want:
        raise ValueError(refused)
    return out


def _set_plain(lines, index, key, value, nl) -> None:
    # a table of this name goes when a plain value replaces it (votes = false over [server.votes])
    _drop_tables(lines, index, key)
    start, end = _block(lines, index)
    first = next((i for i in range(start + 1, end) if HEAD.match(lines[i])), end)
    at, _ = _drop_key(lines, start + 1, first, key)
    if value is None:
        return
    if at < 0:
        at = first
        while at > start + 1 and not lines[at - 1].strip():
            at -= 1
    lines.insert(at, f"{key} = {toml_value(value)}{nl}")


def _drop_tables(lines, index, key) -> None:
    start, end = _block(lines, index)
    i = start + 1
    while i < end:
        m = SUB.match(lines[i])
        if m and (m[1] == key or m[1].startswith(key + ".")):
            stop = next((j for j in range(i + 1, end) if HEAD.match(lines[j])), end)
            del lines[i:stop]
            end -= stop - i
        else:
            i += 1


def _set_nested(lines, index, parts, value, nl) -> None:
    head, leaf = ".".join(parts[:-1]), parts[-1]
    start, end = _block(lines, index)
    first = next((i for i in range(start + 1, end) if HEAD.match(lines[i])), end)
    _drop_key(lines, start + 1, first, parts[0])  # votes = true gives way to [server.votes]
    where = _section(lines, index, head)
    if value is None:
        if where:
            _drop_key(lines, where[0], where[1], leaf)
            where = _section(lines, index, head)
            if not any(ln.strip() and not ln.lstrip().startswith("#") for ln in lines[where[0]:where[1]]):
                del lines[where[0] - 1:where[1]]  # its header too: nothing is left in it
        return
    if where:
        at, _ = _drop_key(lines, where[0], where[1], leaf)
        if at < 0:
            at = where[1]
            while at > where[0] and not lines[at - 1].strip():
                at -= 1
        lines.insert(at, f"{leaf} = {toml_value(value)}{nl}")
        return
    _, end = _block(lines, index)
    lines[end:end] = [nl, f"[server.{head}]{nl}", f"{leaf} = {toml_value(value)}{nl}"]


def main() -> int:
    """The helper's way in: JSON on stdin, JSON on stdout, the file's text never in an argument or a log."""
    req = json.load(sys.stdin)
    try:
        if not supports(req.get("version")):
            raise ValueError("This server runs a version before 0.10, which refuses these settings. Update it first.")
        print(json.dumps({"toml": edit(req["toml"], int(req.get("index", 0)), req["changes"])}))
    except (ValueError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
