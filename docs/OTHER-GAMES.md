# Other games: what a server needs for ONI RCON to manage it

ONI RCON was built for [Project Reclaimer](https://projectreclaimer.dev) (Halo 3), and it speaks Reclaimer's remote
console. Other community projects built the same way, such as Project Gravemind (Halo 2) and Reach Renaissance
(Halo: Reach), can be managed by it too, if their dedicated server answers the same remote console. This page is for
their developers: it is everything the desktop app sends and reads, so a server can be checked against it.

The rest of ONI comes for free once a server speaks it: the station cards and roster, the dossier, chat and the feed,
kicks, bans, mutes, VPN allowances and admins, map and mode changes, votes, the findings (F8), automation (F9) and
plug-ins (F10).

## Naming the game

A server says which project it is in its sign-in reply, with two extra keys:

```json
{"type": "auth", "ok": true, "protocol": 1, "server": "My Slayer", "version": "0.4.2",
 "project": "gravemind", "game": "Halo 2"}
```

- `project` is the project's id: `reclaimer`, `gravemind` or `renaissance`, or its name for one ONI doesn't know yet.
  A reply without it is taken for Project Reclaimer, which is how every Reclaimer server signs in today.
- `game` is the game, as players call it. ONI shows it on the station card (H2, REACH) and in the sitrep.
- `version` is the server's own version, compared only with servers of the same project.

For a server that isn't Project Reclaimer, ONI turns off what works through Reclaimer's own files and formats:
PLAYER CAPACITY, CAMERA and CHARACTER MODS (its `dedicated.toml` keys), the playlist actions and NEW GAME TYPE (its
playlist and game-type files), ReclaimerForge, and the warning that a newer Reclaimer release is out.

## The connection

Reach Renaissance's dedicated server is the exception: it speaks Valve's Source RCON, which the desktop app speaks too
(`protocol = "source"` on the server, or `source://HOST` on the setup screen). Its text replies to `status`,
`players`, `bans`, `maps` and `modes` are read into the station card, roster, blacklist and pickers; it pushes no events. Everything below is the protocol for the rest.

A WebSocket (`ws://`, or `wss://` behind a proxy) that ONI reaches directly or through an ssh tunnel to the game box.
Reclaimer listens on TCP, on the server's game port unless the config gives it an `rcon_port`, and on `127.0.0.1`
unless told otherwise. A password must be set for it to listen at all. Every message is one JSON object in a text frame.

1. ONI sends `{"type": "auth", "password": "…"}` first.
2. The server answers `{"type": "auth", "ok": true, "server", "version", "project", "game"}`, or
   `{"type": "auth", "ok": false, "error": "Wrong password."}` and closes. ONI never retries a refused password by itself.
3. Then ONI sends commands, and the server answers each one and pushes events.

**Command**: `{"type": "command", "command": "kick", "args": ["Bravo", "spawn camping"], "id": 12, "by": "viik"}`.
`by` is the operator's name, for the server's admin log. **Reply**: `{"type": "reply", "id": 12, "ok": true,
"text": "Kicked Bravo.", "data": {...}}`. `text` is shown as it is. `data` is there for the commands that list
something (below). A command ONI doesn't get an answer to in 10 seconds is shown as possibly done, and isn't resent.

**Event**: `{"type": "event", "event": "join", "time": 1730000000, ...}`, pushed to every signed-in console.

## Commands ONI sends

Arguments are strings. A player is named by their name, their slot number (`#3`) or their player ID.

| Command | Arguments | `data` ONI reads |
|---|---|---|
| `status` | | `{name, address, map, mode, phase, players, max_players, next, password_required, max_ping, hidden, anti_cheat, anti_cheat_active, block_vpn, text_chat, voice, votes}` |
| `players` | | `{count, max_players, players: [player]}` |
| `say` | text | |
| `tell` | player, text | |
| `kick` | player, reason | |
| `ban` | player, duration (`30m`, `2h`, `1d`, `2w`, empty = permanent), reason | |
| `banip` | address or range, duration, reason | |
| `unban` | player ID or address | |
| `bans` | | `{players: [{id, name, reason, expires, banned_by}], ips: [{ip, reason, expires}], devices: []}` (`expires` a Unix time or null) |
| `mute`, `unmute` | player, duration | |
| `team` | player, team | |
| `teamcount` | 2 to 8 | |
| `shuffle`, `endround`, `endgame` | | |
| `maps`, `modes` | | `{entries: [{kind: "map" or "mode" or "variant", name, reference}]}` |
| `map`, `mode`, `load`, `nextmap` | map and/or mode, by `reference` | |
| `nextmap` | none | `{next, rotation: [{map, mode}]}`: what plays next, and the rotation F3 shows |
| `vote` | | `{vote, playlist, subjects: ["kick", "endround", …]}` (`vote` null when none is under way) |
| `startvote`, `passvote`, `cancelvote` | subject, and a player for a kick vote | |
| `vpn` | none, or an address to check | `{allowed: [{target, note}], block_vpn, ranges}` |
| `vpnallow`, `vpnrevoke` | player ID or address, note | |
| `admins` | | `{admins: [{player_id, name, source}]}` |
| `admin` | `add` or `remove`, player ID | |
| `pardon` | player ID or address | |
| `servername`, `password` | text (until a restart) | |
| `maxping` | milliseconds, or `off` | |
| `reload` | | its `text` says what changed |
| `help` | | its `text` lists the commands |

A **player** is `{number, name, player_id, address, team, score, kills, deaths, alive, admin, muted, service_tag,
health, shields, seconds_since_last_death}`. `health` and `shields` run from 0 to 1, and `address` is only ever shown
blanked unless the operator unblanks it. A server without one of these keys is fine: ONI shows a dash.

A command the server doesn't have should answer `ok: false` with a `text` that says so. ONI shows it and carries on.

## Events ONI reads

| `event` | Keys |
|---|---|
| `join` | `name`, `player_id`, `address` |
| `leave` | `name`, `reason` |
| `chat` | `name`, `team`, `channel` (`all`, `team`, `server`), `text` |
| `kill` | `killer`, `victim` (engine IDs or names), `weapon` |
| `kick`, `ban`, `mute`, `unmute` | `name` (or `player`), `player_id`, `ip`, `by`, `reason`; a kick's `seconds`, how long they're kept out |
| `unban`, `pardon` | `target`, `name` (one or a list), `by` |
| `refused` | `name`, `player_id`, `ip`, `reason` (a join turned away) |
| `vote` | `name`, `text` |
| `control` | `text` (a map or mode change, a player moved) |
| `admin_added`, `admin_removed` | `id`, `name`, `by` |
| `cheat` | `name`, `text` |
| `admin` | `text` |

Any other `event` is shown in the feed as its `text`. Automation, plug-ins and the findings can still act on it by
its name.

## Trying it

The desktop demo (`oni-rcon --demo`) runs three pretend servers, and the third signs in as a Project Gravemind one
with Halo 2's maps. A real server is added like any other: its address and port on the setup screen, or a `[[server]]`
block in the config with `port`, `password` and `ssh` or `url`.

If your project would rather ONI spoke something else, or reads something it doesn't yet, open an issue on
[viik2k/oni-rcon](https://github.com/viik2k/oni-rcon) and say what your server answers.
