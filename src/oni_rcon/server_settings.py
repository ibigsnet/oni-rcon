"""A Project Reclaimer 0.10 server's settings in dedicated.toml: what each one means in plain words, checks for a new
value, and a careful editor for the file.

The console edits the file only where it runs beside the server (no ssh, no url) and only for a server that says it
runs 0.10 or newer. The file is found from the server's own folders: `dedicated_toml` when the config names it, else
beside the server's data folder (`host_key_dir`) or its content folder (`content_dir`), and the [[server]] block is
the one whose RCON port (`rcon_port`, else `port`) is the station's.

Editing keeps everything else in the file as it was, comments and order included: a key is changed on its own line, a
new one goes beside its commented-out example or after the last setting of its table. The result is parsed again and
compared with what was asked for; if anything else would differ, nothing is written. Before each write the file is
copied beside itself (dedicated.toml.oni-backup-YYYYMMDD-HHMMSS, the newest BACKUPS_KEPT kept).

Every setting here and when it applies comes from the server's own documentation (servers.md, "Changing settings
while servers run" and the dedicated.toml table). A setting whose meaning that leaves unclear is not given a control
of its own: ALL SETTINGS edits it as a raw value, and the server's own check (`reload`) decides.
"""
from __future__ import annotations

import copy
import re
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

FILE_NAME = "dedicated.toml"
BACKUP_MARK = ".oni-backup-"
BACKUPS_KEPT = 10
MIN_VERSION = (0, 10)
MAX_BOTS = 8  # a server takes 8 bots at most (servers.md, Bots)
MAX_CUSTOM_ROLES = 12  # "a configuration adds up to twelve more"
STANDARD_ROLES = {"host", "admin", "mod", "vip", "members", "everyone"}
BOT_ROLE = "bots"
BOT_PERMISSION = "bots.run"
REMOVE = object()  # a change that takes the key out of its table

# When a changed setting applies (the server's reload answers with the same three).
NOW = "at once, players stay on"
NEXT_GAME = "from the next game"
RESTART = "the server restarts for it once nobody is playing"
ASK = "the server's reload answer says when"
RESTARTS_WHEN_EMPTY = "Restarts once it has no players"  # the reload answer's words (server reload.rs)
RESTART_THE_SUPERVISOR = "Restart the supervisor to apply"

SECRET = re.compile(r"pass|secret|token|rcon|(^|_)keys?$", re.I)
BARE_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
PLAYER_ID = re.compile(r"[0-9A-Fa-f]{64}")

# Never set from these screens: how the server is reached, what it plays, who holds its roles, and the supervisor's
# own settings. They have screens of their own (F3 PLAYLISTS, F4), or stay in the file.
PROTECTED = {
    "game", "data", "bans", "public_address", "bind_address", "upnp", "auto_reload", "auto_update", "port",
    "rcon_port", "enabled", "playlist", "playlists", "variant", "map", "base_map", "campaign", "admins",
    "moderators", "vote_kick_immune", "stats_server", "stats_copies", "film_receivers", "film_upload_kbps",
    "failover", "metrics_http", "master", "rcon", "vpn", "roles", "weights", "workshop", "server", "defaults",
}


class SettingsError(Exception):
    """Something to tell the operator, in plain words; nothing was written."""


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    group: str
    kind: str  # choice | bool | int | text | secret
    choices: tuple = ()  # (value, words)
    low: int | None = None
    high: int | None = None
    default: str = ""  # what the server does when neither the server's block nor [defaults] sets it
    hint: str = ""
    applies: str = ASK
    where: tuple = ("server", "defaults")  # the tables it may be set in; the first that sets it wins
    max_len: int = 0


GROUPS = [
    ("performance", "PERFORMANCE", "How much work the server does each frame. Fewer ticks and more threads help a "
                                   "big lobby on a slow CPU."),
    ("network", "NETWORK", "How games reach players: the netcode, the ping limit and the checks on what players send."),
    ("render", "RENDER · HEADLESS", "A dedicated server always runs headless: no window, GPU or sound device. These "
                                    "choose what it still draws, and whether it records films of its games."),
    ("players", "PLAYERS", "Who gets in, how many, and how the lobby runs between games."),
    ("rules", "GAME RULES", "The server's rules for every game it plays."),
    ("bots", "BOTS", "Bots (reclaimer-bot: a DJ, a chat relay, an announcer) join chat and voice only: no slot, team, "
                     "score or vote."),
]
GROUP_NAMES = {g: name for g, name, _ in GROUPS}
ONOFF = ((True, "On"), (False, "Off"))


def _s(key, label, group, kind, *choices, **kw) -> Setting:
    return Setting(key, label, group, kind, tuple(choices), **kw)


SETTINGS = [
    # PERFORMANCE
    _s("tick_rate", "Tick rate", "performance", "choice", (60, "60 ticks a second"), (30, "30 ticks a second"),
       default="60", applies=NEXT_GAME,
       hint="30 holds big lobbies smooth on slower CPUs; 60 for competitive. Multiplayer games only: co-op, ODST "
            "and Firefight stay at 60. Players' own games stay at 60 and anyone can join."),
    _s("channel_threads", "Network threads", "performance", "int", low=0, high=64, applies=RESTART,
       default="1 (the game's thread alone); 0 above 32 places",
       hint="Threads that update the players' network channels each frame, the game's own included. 1: the game's "
            "thread alone. 0: one for every 8 players, up to a quarter of the machine's threads and 8."),
    _s("node_threads", "Animation threads", "performance", "int", low=0, high=64, applies=RESTART,
       default="1 (the game's thread alone)",
       hint="Threads that build the players' skeletons each tick, the game's own included. 0: from 48 bipeds up, "
            "one for every 16, up to a quarter of the machine's threads and 4."),
    _s("encode_once", "Encode each update once", "performance", "bool", *ONOFF, applies=RESTART,
       default="on above 32 places, off below",
       hint="Encode each object's update once a tick and send the same bits to every player who needs it. What "
            "each player receives is unchanged."),
    # NETWORK
    _s("network_model", "Netcode", "network", "choice", ("classic", "Classic: Halo 3's own"),
       ("enhanced", "Enhanced: opt-in, for testing"), default="classic", applies=RESTART,
       hint="Classic is Halo 3's own. Enhanced sends each player the updates by the error they would see and blends "
            "corrections. Every player's game uses the server's model; the server browser shows it."),
    _s("coop_network", "Co-op netcode", "network", "choice", ("lockstep", "Lockstep: Halo 3's, 60 ticks"),
       ("lockstep30", "Lockstep at 30 ticks (the Xbox 360's)"),
       ("host", "Host: the server simulates (experimental)"), default="lockstep",
       hint="How co-op missions, ODST and Firefight play. Multiplayer games use the netcode above."),
    _s("max_ping", "Ping limit (ms)", "network", "int", low=0, high=1000, default="0 (no limit)", applies=NOW,
       hint="A player whose lowest ping is over this is turned away within 5 s of joining. 0 takes everyone."),
    _s("anti_cheat", "Anti-cheat", "network", "choice", ("enforce", "Enforce: refuse and kick"),
       ("detect", "Detect: refuse and report"), ("off", "Off"), default="enforce", applies=NEXT_GAME,
       hint="What happens to events only a host may send when a player sends them."),
    _s("block_vpn", "Block VPNs", "network", "bool", *ONOFF, default="on", applies=NOW,
       hint="Players joining through a known VPN are refused (F4 allows one by one)."),
    _s("load_timeout", "Co-op load wait (s)", "network", "int", low=0, high=600, default="30", applies=NEXT_GAME,
       hint="Co-op games only: seconds a player's game may hold the others up loading before it is removed. 0 "
            "waits as long as it takes."),
    # RENDER · HEADLESS
    _s("draw_frames", "Draw frames", "render", "bool", *ONOFF, default="off", applies=NEXT_GAME,
       hint="Off: the server draws no frames. On: it draws them all the same, the way back if a server misbehaves."),
    _s("film_recording", "Record films", "render", "bool", *ONOFF, default="off", applies=NEXT_GAME,
       hint="Record each game's film to films/ in the server's folder: a normal film any player's Theater plays."),
    _s("film_keep", "Films kept", "render", "int", low=0, high=100000, default="25", applies=NEXT_GAME,
       hint="How many recorded films stay; the oldest go first."),
    _s("film_max_mb", "Films' space (MB)", "render", "int", low=0, high=10_000_000, default="2048",
       applies=NEXT_GAME, hint="The most the recorded films may take together."),
    # PLAYERS
    _s("name", "Name in the server list", "players", "text", where=("server",), max_len=48, applies=NOW,
       hint="Up to 48 characters."),
    _s("host_name", "Host name", "players", "text", where=("server", "top"), max_len=64, applies=NOW,
       hint="Shown beneath the server's name in the server browser."),
    _s("max_players", "Most players", "players", "int", low=1, high=127, default="16", applies=NOW,
       hint="Players the server admits at once."),
    _s("min_players", "Players to start a game", "players", "int", low=1, high=127, default="1", applies=NOW,
       hint="The lobby waits for this many players before a game starts. Not more than the most players."),
    _s("password", "Join password", "players", "secret", default="none", applies=NOW, max_len=128,
       hint="Players type it to join. Empty: no password. Never shown here."),
    _s("hidden", "Hidden from the server list", "players", "bool", *ONOFF, default="off", applies=NOW,
       hint="On: not listed on any master; players join by address."),
    _s("joins", "New players", "players", "choice", ("open", "Come in"), ("queue", "Wait in the join queue"),
       ("closed", "Are kept out"), default="open", applies=NOW,
       hint="Closed keeps every new player out until it opens again; players on the server stay."),
    _s("post_game_seconds", "Post-game screen (s)", "players", "int", low=0, high=3600, default="15", applies=NOW,
       hint="Seconds the post-game screen stays before the next game."),
    _s("start_countdown_seconds", "Start countdown (s)", "players", "int", low=0, high=3600, default="3",
       applies=NOW, hint="Seconds the lobby counts down once enough players are in."),
    _s("welcome", "Welcome message", "players", "text", max_len=500, default="none", applies=NOW,
       hint="What a player reads as they join. {server} and {name} are filled in."),
    _s("voice_quality", "Voice quality", "players", "choice", ("standard", "Standard (16 kHz)"),
       ("high", "High (24 kHz)"), default="standard", applies=NOW,
       hint="What players' voice sends. Bots and roles with voice.music send music quality whatever this says."),
    # GAME RULES
    _s("friendly_fire", "Friendly fire", "rules", "choice", (True, "As the game type says"),
       (False, "Off: teammates take no damage"), default="as the game type says", applies=NEXT_GAME),
    _s("team_changing", "Team changing", "rules", "choice", ("balanced", "Only to a smaller team"),
       ("game_type", "As the game type says"), ("off", "Nobody changes team"), default="balanced",
       applies=NEXT_GAME),
    _s("camera", "Camera", "rules", "choice", ("first_person", "First person"), ("player_choice", "Player's choice"),
       ("third_person", "Third person"), default="first_person", applies=NEXT_GAME),
    _s("force_model", "Everyone plays", "rules", "choice", ("spartan", "Spartan"), ("elite", "Elite"),
       default="each player's own pick", applies=NEXT_GAME,
       hint="Multiplayer games of Halo 3's only. A game type's character set still wins."),
    _s("round_outlasts_leavers", "Round outlasts leavers", "rules", "bool", *ONOFF, default="off",
       applies=NEXT_GAME,
       hint="On: when leavers leave too few contenders the others play on. Off, as Halo 3: the round ends."),
    _s("stock_map_defaults", "Bungie's extra respawn points", "rules", "bool", *ONOFF, default="on",
       applies=NEXT_GAME, hint="Halo 3's maps played without a saved map get the default variants' extra respawns."),
    _s("map_hud", "Map's own HUD for everyone", "rules", "bool", *ONOFF, default="off", applies=NEXT_GAME,
       hint="On: every player sees the map's own HUD whatever character they play."),
    _s("forge_edit", "Who edits in Forge", "rules", "choice", ("game_type", "As the game type allows"),
       ("roles", "Only roles with forge.edit"), default="game_type", applies=NOW),
    # BOTS
    _s("allow_bots", "Bots may join", "bots", "bool", *ONOFF, default="off", applies=NOW,
       hint=f"Bots get in while their player ID holds a role with {BOT_PERMISSION}. At most {MAX_BOTS} at once."),
]
BY_KEY = {s.key: s for s in SETTINGS}


# --- reading -------------------------------------------------------------------------------------------------------
def supports(version) -> bool | None:
    """Whether a server of this version takes these settings: None when the version isn't known (not connected)."""
    nums = [int(n) for n in re.findall(r"\d+", str(version or ""))[:2]]
    if len(nums) < 2:
        return None
    return tuple(nums) >= MIN_VERSION


def is_secret(key: str) -> bool:
    return any(SECRET.search(part) for part in str(key).split("."))


def masked(value) -> str:
    return "(empty)" if value in ("", None) else "•••••• (set)"


def is_local(server) -> bool:
    return not getattr(server, "ssh", "") and not getattr(server, "url", "")


def candidates(server) -> list[Path]:
    """Where this server's dedicated.toml may be, most specific first."""
    if not is_local(server):
        return []
    explicit = str(getattr(server, "dedicated_toml", "") or "")
    if explicit:
        return [Path(explicit).expanduser()]
    out = []
    data = str(getattr(server, "host_key_dir", "") or "")
    if data:
        here = Path(data).expanduser()
        out += [here / FILE_NAME, here.parent / FILE_NAME, here.parent.parent / FILE_NAME]
    content = str(getattr(server, "content_dir", "") or "")
    if content:
        out.append(Path(content).expanduser().parent / FILE_NAME)
    seen, unique = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def server_index(data: dict, port: int) -> int:
    """The [[server]] block on this RCON port: its rcon_port, else its game port."""
    servers = data.get("server")
    if not isinstance(servers, list) or not servers:
        raise SettingsError("The file has no [[server]] block.")
    hits = [i for i, s in enumerate(servers) if isinstance(s, dict) and s.get("rcon_port", s.get("port")) == port]
    if len(hits) != 1:
        raise SettingsError(f"No single [[server]] block in the file answers on port {port}."
                            if not hits else f"Several [[server]] blocks answer on port {port}.")
    return hits[0]


@dataclass
class Loaded:
    path: Path
    text: str
    data: dict
    index: int

    @property
    def block(self) -> dict:
        return self.data["server"][self.index]

    def table(self, where: str) -> dict:
        if where == "server":
            return self.block
        if where == "top":
            return self.data
        got = self.data.get(where)
        return got if isinstance(got, dict) else {}

    def get(self, key: str, where: str):
        """(set there, its value)."""
        table = self.table(where)
        return (key in table, table.get(key))

    def effective(self, key: str, where: tuple = ("server", "defaults")) -> tuple[object, str]:
        """(the value in force, the table it comes from), or (None, "") when the server's own default applies."""
        for w in where:
            here, value = self.get(key, w)
            if here:
                return value, w
        return None, ""


def read_text(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise SettingsError(f"{path} isn't there.") from None
    except OSError as e:
        raise SettingsError(f"{path} can't be read: {e.strerror or e}.") from None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SettingsError(f"{path} isn't UTF-8 text.") from None


def parse(text: str) -> dict:
    try:
        return tomllib.loads(text.removeprefix("﻿"))
    except tomllib.TOMLDecodeError as e:
        raise SettingsError(f"The file doesn't read as TOML ({e}). Fix it first; nothing here changes it.") from None


def load(path: Path, port: int) -> Loaded:
    text = read_text(path)
    data = parse(text)
    return Loaded(path, text, data, server_index(data, port))


def locate(server) -> Loaded:
    """This server's dedicated.toml, read, with its block found. SettingsError says why not."""
    if not is_local(server):
        raise SettingsError("This server is reached over ssh or a URL: its dedicated.toml is on another machine. "
                            "Run oni-rcon on the server's machine to change its settings.")
    places = candidates(server)
    if not places:
        raise SettingsError("oni-rcon doesn't know where this server's files are. Give it the server's data folder "
                            "(host_key_dir), its content folder (content_dir) or dedicated_toml in its config.")
    problems = []
    for p in places:
        if not p.is_file():
            continue
        try:
            return load(p, server.port)
        except SettingsError as e:
            problems.append(f"{p}: {e}")
    if problems:
        raise SettingsError("\n".join(problems))
    raise SettingsError("No dedicated.toml was found beside this server's folders. Looked at:\n"
                        + "\n".join(str(p) for p in places))


# --- checking a new value ------------------------------------------------------------------------------------------
def _one_line(setting_label: str, text: str, max_len: int) -> str:
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise SettingsError(f"{setting_label}: one line, no control characters.")
    if max_len and len(text) > max_len:
        raise SettingsError(f"{setting_label}: at most {max_len} characters.")
    return text


def check(setting: Setting, raw):
    """A control's value as the file holds it, or SettingsError in plain words."""
    if setting.kind == "bool" and isinstance(raw, str):
        raw = {"on": True, "yes": True, "off": False, "no": False}.get(raw.strip().lower(), raw)
    if setting.kind in ("choice", "bool"):
        for value, _ in setting.choices:
            if raw == value and type(raw) is type(value) or str(raw).strip().lower() == str(value).lower():
                return value
        words = ", ".join(str(v).lower() if isinstance(v, bool) else str(v) for v, _ in setting.choices)
        raise SettingsError(f"{setting.label}: one of {words}.")
    if setting.kind == "int":
        text = str(raw).strip()
        if isinstance(raw, bool) or not re.fullmatch(r"-?\d{1,9}", text):
            raise SettingsError(f"{setting.label}: a whole number.")
        value = int(text)
        if setting.low is not None and value < setting.low or setting.high is not None and value > setting.high:
            raise SettingsError(f"{setting.label}: from {setting.low} to {setting.high}.")
        return value
    text = str(raw) if setting.kind == "secret" else str(raw).strip()
    return _one_line(setting.label, text, setting.max_len)


def cross_check(loaded: Loaded, key: str, value, where: str) -> None:
    """Checks that need the rest of the file: the players needed to start never above the most players."""
    if key not in ("max_players", "min_players") or value is REMOVE:
        return
    other = "min_players" if key == "max_players" else "max_players"
    have, source = loaded.effective(other)
    if where == "defaults" and source == "server":
        return  # this server keeps its own; the other servers are checked by the server itself
    if not isinstance(have, int) or isinstance(have, bool):
        have = 1 if other == "min_players" else 16
    lo, hi = (have, value) if key == "max_players" else (value, have)
    if lo > hi:
        raise SettingsError(f"Players to start a game ({lo}) can't be more than the most players ({hi}).")


def raw_value(text: str):
    """ALL SETTINGS' value box: TOML (true, 30, "words", ["a", "b"]), else plain words. Tables are refused."""
    text = text.strip()
    if not text:
        raise SettingsError("Type a value, or use RESET to take the setting out.")
    try:
        value = tomllib.loads(f"v = {text}")["v"]
    except tomllib.TOMLDecodeError:
        value = _one_line("The value", text, 4096)
    if isinstance(value, dict) or isinstance(value, list) and any(isinstance(v, (dict, list)) for v in value):
        raise SettingsError("A table can't be set from here: edit the file for that.")
    return value


def editable(key: str) -> str:
    """Why a key can't be changed here, or "" when it can."""
    if is_secret(key) and key != "password":
        return "It's a password or key: it stays in the file and is never shown here."
    if key in PROTECTED:
        return ("It decides how the server is reached, what it plays or who holds its roles: F3 PLAYLISTS, F4 "
                "and the file change it.")
    if not BARE_KEY.fullmatch(key):
        return "Only plain keys are edited here."
    return ""


def shown(key: str, value) -> str:
    """A value as the screens show it: secrets masked, lists and words as the file has them."""
    if is_secret(key):
        return masked(value)
    if value is None:
        return ""
    return toml_value(value) if not isinstance(value, str) else value


# --- writing -------------------------------------------------------------------------------------------------------
def toml_str(s: str) -> str:
    out = str(s).replace("\\", "\\\\").replace('"', '\\"')
    for a, b in (("\n", "\\n"), ("\r", "\\r"), ("\t", "\\t")):
        out = out.replace(a, b)
    return '"' + re.sub(r"[\x00-\x1f\x7f]", lambda m: f"\\u{ord(m[0]):04x}", out) + '"'


def toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {toml_value(x)}" for k, x in v.items()) + " }"
    return toml_str(v)


@dataclass(frozen=True)
class Change:
    table: tuple  # ("server",) this server's block, ("defaults",), ("top",), or a table's path such as ("roles", "bots")
    key: str
    value: object  # REMOVE takes it out


def _scan(line: str, state: tuple[int, str | None]) -> tuple[tuple[int, str | None], int | None]:
    """Walks one line of TOML: (the bracket depth and open multi-line string after it, where its comment starts)."""
    depth, ml = state
    i, n, comment = 0, len(line), None
    while i < n:
        if ml:
            j = line.find(ml, i)
            if j < 0:
                break
            i, ml = j + 3, None
            continue
        c = line[i]
        if c == "#":
            comment = i
            break
        if line.startswith('"""', i) or line.startswith("'''", i):
            ml, i = line[i:i + 3], i + 3
            continue
        if c == '"':
            i += 1
            while i < n and line[i] != '"':
                i += 2 if line[i] == "\\" else 1
            i += 1
            continue
        if c == "'":
            j = line.find("'", i + 1)
            i = n if j < 0 else j + 1
            continue
        if c in "[{":
            depth += 1
        elif c in "]}":
            depth = max(0, depth - 1)
        i += 1
    return (depth, ml), comment


def _starts(lines: list[str]) -> list[bool]:
    """For each line: whether it starts outside any value (a key, a header, a comment or a blank line may start it)."""
    out, state = [], (0, None)
    for line in lines:
        out.append(state == (0, None))
        state, _ = _scan(line, state)
    return out


HEADER = re.compile(r"\s*\[")
SERVER_HEADER = re.compile(r"\s*\[\[\s*server\s*\]\]\s*(#.*)?$")


def _table_header(path: tuple) -> re.Pattern:
    return re.compile(r"\s*\[\s*" + r"\s*\.\s*".join(re.escape(p) for p in path) + r"\s*\]\s*(#.*)?$")


def _key_line(key: str) -> re.Pattern:
    return re.compile(r"(\s*)(?:" + re.escape(key) + r'|"' + re.escape(key) + r'")\s*=')


def _section(lines, starts, table: tuple, index: int) -> tuple[int, int] | None:
    """(first line of a table's keys, the line after them), or None when the file has no such table."""
    heads = [i for i, ln in enumerate(lines) if starts[i] and HEADER.match(ln)]
    if table == ("top",):
        return 0, heads[0] if heads else len(lines)
    if table == ("server",):
        found = [i for i in heads if SERVER_HEADER.match(lines[i])]
        if index >= len(found):
            raise SettingsError(f"The file has no server {index + 1}.")
        head = found[index]
    else:
        pattern = _table_header(table)
        found = [i for i in heads if pattern.match(lines[i])]
        if not found:
            return None
        head = found[0]
    after = next((i for i in heads if i > head), len(lines))
    return head + 1, after


def _value_end(starts, at: int) -> int:
    return next((j for j in range(at + 1, len(starts)) if starts[j]), len(starts))


def _set(lines: list[str], table: tuple, index: int, key: str, value, nl: str) -> None:
    starts = _starts(lines)
    where = _section(lines, starts, table, index)
    pattern = _key_line(key)
    if where is None:
        if value is REMOVE:
            return
        _new_table(lines, starts, table, f"{key} = {toml_value(value)}{nl}", nl)
        return
    lo, hi = where
    found = next((i for i in range(lo, hi) if starts[i] and pattern.match(lines[i])), None)
    if found is not None:
        end = _value_end(starts, found)
        if value is REMOVE:
            del lines[found:end]
            return
        indent = pattern.match(lines[found])[1]
        tail = ""
        if end == found + 1:  # a one-line value keeps the comment after it
            _, at = _scan(lines[found], (0, None))
            if at is not None:
                tail = "  " + lines[found][at:].rstrip("\r\n")
        lines[found:end] = [f"{indent}{key} = {toml_value(value)}{tail}{nl}"]
        return
    if value is REMOVE:
        return
    example = re.compile(r"\s*#\s*" + re.escape(key) + r"\s*=")
    at = next((i + 1 for i in range(lo, hi) if starts[i] and example.match(lines[i])), None)
    if at is None:
        keys = [i for i in range(lo, hi) if starts[i] and re.match(r"\s*[A-Za-z0-9_\"'.-]+\s*=", lines[i])]
        at = _value_end(starts, keys[-1]) if keys else lo
    lines.insert(at, f"{key} = {toml_value(value)}{nl}")


def _new_table(lines, starts, table: tuple, first: str, nl: str) -> None:
    """A new [table] with its first key: above the first [[server]] block and the paragraph of comments right over it,
    else at the end."""
    if table in (("server",), ("top",)):
        raise SettingsError("The file has no such table.")
    block = [f"[{'.'.join(table)}]{nl}", first]
    heads = [i for i, ln in enumerate(lines) if starts[i] and SERVER_HEADER.match(ln)]
    if heads:
        at = heads[0]
        while at > 0 and not lines[at - 1].strip():
            at -= 1
        while at > 0 and lines[at - 1].lstrip().startswith("#"):
            at -= 1
        if at and lines[at - 1].strip():
            block.insert(0, nl)
        lines[at:at] = [*block, nl]
        return
    if lines and lines[-1].strip():
        block.insert(0, nl)
    lines.extend(block)


def _apply_to_data(data: dict, index: int, change: Change) -> None:
    if change.table == ("server",):
        here = data["server"][index]
    elif change.table == ("top",):
        here = data
    else:
        here = data
        for part in change.table:
            here = here.setdefault(part, {})
            if not isinstance(here, dict):
                raise SettingsError(f"[{'.'.join(change.table)}] isn't a table in the file.")
    if change.value is REMOVE:
        here.pop(change.key, None)
    else:
        here[change.key] = change.value


REFUSED = ("The file is laid out in a way oni-rcon won't edit by itself (for example a table written inline). "
           "Nothing was changed: edit dedicated.toml by hand for this one.")


def edit(text: str, index: int, changes: list[Change]) -> str:
    """The file's text with these changes and nothing else: comments, order and spacing stay. The result is parsed
    and compared with what was asked for; SettingsError when they differ, and nothing should be written."""
    bom = "﻿" if text.startswith("﻿") else ""
    body = text.removeprefix("﻿")
    before = parse(body)
    want = copy.deepcopy(before)
    for c in changes:
        _apply_to_data(want, index, c)
    lines = body.splitlines(keepends=True)
    nl = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += nl
    for c in changes:
        _set(lines, c.table, index, c.key, c.value, nl)
    out = "".join(lines)
    try:
        after = tomllib.loads(out)
    except tomllib.TOMLDecodeError:
        raise SettingsError(REFUSED) from None
    if after != want:
        raise SettingsError(REFUSED)
    return bom + out


def backups(path: Path) -> list[Path]:
    """This file's backups, oldest first."""
    def age(p: Path):
        try:
            return p.stat().st_mtime_ns, p.name
        except OSError:
            return 0, p.name
    return sorted(path.parent.glob(path.name + BACKUP_MARK + "*"), key=age)


def backup(path: Path, keep: int = BACKUPS_KEPT) -> Path:
    """A copy of the file beside it, named by the time; the oldest beyond `keep` go."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.name}{BACKUP_MARK}{stamp}")
    n = 1
    while dest.exists():
        n += 1
        dest = path.with_name(f"{path.name}{BACKUP_MARK}{stamp}-{n}")
    try:
        dest.write_bytes(path.read_bytes())
    except OSError as e:
        raise SettingsError(f"No backup could be made beside {path} ({e.strerror or e}), so nothing was "
                            "changed.") from None
    for old in backups(path)[:-keep] if keep > 0 else []:
        try:
            old.unlink()
        except OSError:
            pass
    return dest


def _write(path: Path, text: str) -> None:
    """In place: the file keeps its owner, its mode and its inode (a bind-mounted file stays mounted)."""
    try:
        with open(path, "r+b") as f:
            f.write(text.encode("utf-8"))
            f.truncate()
    except OSError as e:
        raise SettingsError(f"{path} can't be written ({e.strerror or e}): this console's user may not own it. "
                            "Nothing was changed.") from None


def save(loaded: Loaded, changes: list[Change]) -> tuple[Loaded, Path | None]:
    """Writes the changes: the file must still be as it was read, a backup is made first. Returns the file as it is
    now and the backup (None when nothing changed, and nothing was written)."""
    now = read_text(loaded.path)
    if now != loaded.text:
        raise SettingsError("dedicated.toml changed since this screen read it. It has been read again: look at it "
                            "and make the change once more.")
    text = edit(now, loaded.index, changes)
    if text == now:
        return loaded, None
    saved = backup(loaded.path)
    _write(loaded.path, text)
    return Loaded(loaded.path, text, parse(text), loaded.index), saved


def restore(path: Path, saved: Path) -> None:
    """Puts a backup's text back in place."""
    try:
        text = saved.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise SettingsError(f"The backup {saved} can't be read ({e}).") from None
    _write(path, text)


def changes_for(loaded: Loaded, key: str, value, where: str) -> list[Change]:
    """The change that sets one setting in one table: "server" (this server's block), "defaults" or "top"."""
    setting = BY_KEY.get(key)
    if setting and where not in setting.where:
        raise SettingsError(f"{setting.label} isn't set in [{where}].")
    if where == "defaults" and key == "name":
        raise SettingsError("Each server has its own name.")
    if value is not REMOVE:
        cross_check(loaded, key, value, where)
    return [Change((where,), key, value)]


# --- bots ----------------------------------------------------------------------------------------------------------
def _can(role) -> list:
    can = role.get("can") if isinstance(role, dict) else None
    return can if isinstance(can, list) else []


def bot_roles(loaded: Loaded) -> list[tuple[str, str, list]]:
    """Roles whose `can` has bots.run: (where: "every server" or "this server", the role's name, its players)."""
    out = []
    for where, roles in (("every server", loaded.data.get("roles")), ("this server", loaded.block.get("roles"))):
        if not isinstance(roles, dict):
            continue
        for name, role in roles.items():
            if BOT_PERMISSION in _can(role):
                players = role.get("players") if isinstance(role.get("players"), list) else []
                out.append((where, str(name), [str(p) for p in players]))
    return out


def bot_ids(loaded: Loaded) -> list[str]:
    return list(dict.fromkeys(p for _, _, players in bot_roles(loaded) for p in players))


def _bot_role(loaded: Loaded) -> tuple[str, dict] | None:
    """The [roles.<name>] for every server that bots are added to: one already holding bots.run (bots first)."""
    roles = loaded.data.get("roles")
    if not isinstance(roles, dict):
        return None
    holding = [n for n, r in roles.items() if BOT_PERMISSION in _can(r)]
    for name in ([BOT_ROLE] if BOT_ROLE in holding else []) + holding:
        return name, roles[name]
    return None


def add_bot(loaded: Loaded, player_id: str) -> list[Change]:
    """The changes that let this player ID in as a bot: added to the bots role, which is made when there is none."""
    player_id = player_id.strip()
    if not PLAYER_ID.fullmatch(player_id):
        raise SettingsError("A bot's player ID is 64 letters and numbers (0-9, a-f): `reclaimer-bot id` prints it "
                            "on the bot's machine.")
    if player_id.lower() in (p.lower() for p in bot_ids(loaded)):
        raise SettingsError("That bot can already join.")
    found = _bot_role(loaded)
    if found:
        name, role = found
        players = role.get("players") if isinstance(role.get("players"), list) else []
        return [Change(("roles", name), "players", [*players, player_id])]
    roles = loaded.data.get("roles") if isinstance(loaded.data.get("roles"), dict) else {}
    if BOT_ROLE in roles:  # a role of that name without bots.run: it gets the permission too
        role = roles[BOT_ROLE]
        players = role.get("players") if isinstance(role.get("players"), list) else []
        return [Change(("roles", BOT_ROLE), "can", [*_can(role), BOT_PERMISSION]),
                Change(("roles", BOT_ROLE), "players", [*players, player_id])]
    if len([n for n in roles if n not in STANDARD_ROLES]) >= MAX_CUSTOM_ROLES:
        raise SettingsError(f"The file already has {MAX_CUSTOM_ROLES} roles of its own, the most a server takes. "
                            f"Give one of them {BOT_PERMISSION} in the file instead.")
    # Rank 1, the lowest: any staff member may still mute, kick or ban a bot.
    return [Change(("roles", BOT_ROLE), "title", "Bot"), Change(("roles", BOT_ROLE), "rank", 1),
            Change(("roles", BOT_ROLE), "can", [BOT_PERMISSION]), Change(("roles", BOT_ROLE), "players", [player_id])]


def remove_bot(loaded: Loaded, player_id: str) -> list[Change]:
    roles = loaded.data.get("roles") if isinstance(loaded.data.get("roles"), dict) else {}
    out = []
    for name, role in roles.items():
        players = role.get("players") if isinstance(role, dict) and isinstance(role.get("players"), list) else []
        if BOT_PERMISSION in _can(role) and player_id in players:
            out.append(Change(("roles", name), "players", [p for p in players if p != player_id]))
    if not out:
        raise SettingsError("That ID is in this server's own role, not one for every server: take it out in the file.")
    return out


# --- what the screens list -----------------------------------------------------------------------------------------
@dataclass
class Row:
    key: str
    label: str
    server: str  # this server's own value, as shown ("" unset)
    defaults: str  # [defaults]' (or the file's top for host_name)
    in_use: str
    source: str  # "this server", "[defaults]", "top of the file", "server default"
    applies: str
    why_locked: str  # "" when it can be changed here


SOURCE_WORDS = {"server": "this server", "defaults": "[defaults]", "top": "top of the file", "": "server default"}


def value_words(setting: Setting | None, key: str, value) -> str:
    if setting and setting.kind in ("choice", "bool"):
        for v, words in setting.choices:
            if value == v and type(value) is type(v):
                return words
    return shown(key, value)


def row(loaded: Loaded, setting: Setting) -> Row:
    second = setting.where[1] if len(setting.where) > 1 else ""
    here, mine = loaded.get(setting.key, "server")
    there, theirs = loaded.get(setting.key, second) if second else (False, None)
    value, source = loaded.effective(setting.key, setting.where)
    in_use = value_words(setting, setting.key, value) if source else (setting.default or "the server's default")
    if setting.kind == "secret":
        in_use = masked(value) if source else "(none)"
    return Row(setting.key, setting.label, value_words(setting, setting.key, mine) if here else "",
               value_words(setting, setting.key, theirs) if there else "", in_use, SOURCE_WORDS[source],
               setting.applies, "")


def group_rows(loaded: Loaded, group: str) -> list[Row]:
    return [row(loaded, s) for s in SETTINGS if s.group == group]


def all_rows(loaded: Loaded) -> list[Row]:
    """Every setting: the ones above, then every other key this server's block, [defaults] or the file's top has."""
    out = [row(loaded, s) for s in SETTINGS]
    known = set(BY_KEY)
    others: dict[str, None] = {}
    for table in (loaded.block, loaded.table("defaults"), loaded.data):
        for k, v in table.items():
            if k not in known and not isinstance(v, dict) and not (isinstance(v, list) and v and isinstance(v[0], dict)):
                others.setdefault(k)
    for k in sorted(others):
        here, mine = loaded.get(k, "server")
        there, theirs = loaded.get(k, "defaults")
        top_here, top = loaded.get(k, "top")
        value, source = loaded.effective(k, ("server", "defaults", "top"))
        out.append(Row(k, k, shown(k, mine) if here else "", shown(k, theirs) if there else
                       (shown(k, top) + "  (top)" if top_here else ""), shown(k, value), SOURCE_WORDS[source], ASK,
                       editable(k)))
    return out


def reload_words(text: str) -> tuple[bool, bool]:
    """(the server restarts for something once it's empty, the supervisor must restart) from a reload answer."""
    return RESTARTS_WHEN_EMPTY.lower() in text.lower(), RESTART_THE_SUPERVISOR.lower() in text.lower()
