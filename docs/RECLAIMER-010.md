# Project Reclaimer 0.10 in oni-rcon

oni-rcon works with 0.9 and 0.10 servers side by side. Everything below shows only when the server you've selected says
it runs 0.10 or newer, because a 0.9.23 server refuses a settings file with any of these keys.

## Using it

**Ctrl+T** opens SERVER FEATURES for the selected server (F12 too, on a customer console).

| Screen | What it does | Takes effect |
| --- | --- | --- |
| ROLES | Who is Admin, Mod, VIP or a role of your own; add and remove by player; a Mod's longest ban; developers as VIP | at once |
| MUTES | Voice, text or both; shadow mutes | at once |
| POLLS | Ask every player a question; an answer can run a command | at once |
| PANELS | A box on every player's screen, with lines and a button | at once |
| JOINS | Open, paused or queued now; who's waiting; line sizes and gates (`in_game` for Infection) in SETTINGS | at once |
| VOTE RULES | Who may call each vote; limits and cooldowns | at once |
| ANNOUNCEMENTS | Who reads kicks, bans, mutes, votes and joins | at once |
| PLAYER MODELS | `customization`: ODST's and Reach's characters. **Adding Reach refuses every player without Reach** | restart once empty |
| FORGE OBJECTS | `odst_objects`, `reach_objects`, `forge_primitives`, `halo3_objects`, with what each needs | restart once empty |
| CO-OP NETWORK | `coop_network = "lockstep"` or `"host"` (experimental) | restart once empty |
| OTHER GAMES | Which games' files this server has, and what each lets it host | |
| MORE | `kick_blocks`, `metrics_http`, the console's colour, `queue_mode` | |
| ADVANCED | Any other key by name, and the settings as they stand with passwords left out | |

Roles down to joins are live commands, so they work on any connection. The rest are `dedicated.toml` settings:

- Beside the server, with the IBIGS `oni` helper, they save through `oni config`. The running server checks the new file
  with `dedicated check` first; the old one is backed up, and goes back if the server still refuses it on reload.
- Anywhere else, the screen gives you the lines to put in the server's `[[server]]` block, and copies them.

F1 has ROLE (`e`) for the selected player. MUTE asks voice, text or both, and shadow. F8 ROLES and VOTES open these screens
on a 0.10 server.

## Rules (the sandbox)

F8 → RULES on 0.10 adds, when a 0.10 server is connected:

- **WHEN** a player is kicked, banned or muted (`{by}`, `{reason}`, `{action}`); staff use a command; a poll ends; a panel's
  button is pressed; the server reaches a phase; a waiting player is called in; a player is turned away.
- **IF** the server is in a phase. "The player has a role" reads the server's own roles.
- **THEN** ask a poll; show or hide a panel; pause or open joins; message the staff.

TEST says, without sending anything, whether each condition holds right now and which version the rule needs. A rule
can report a kick, a ban or a mute. It can't do one: those stay with a person. `oni --demo` has three pretend 0.10 servers
to try all of this on.

## Old game types 0.10 skips

0.10 checks every setting of a JSON game type against its own table as the server starts, and leaves out the whole file
for one value it hasn't, with a line in the log:

    Skipped content file .../halo3/game_variants/KOTH ....json: Teams enabled (rounds.teams) has no value 3. Valid choices: "Off" (0), "On" (1).

The game type is then simply missing. oni finds these and upgrades them to 0.10's standard:

- **F9 → Check & fix** adds a line: `old game types the server skips: N, fixable: M`. It changes nothing.
- **F9 → Upgrade old game types** lists each file and what would change, and upgrades only after UPGRADE. It reads the
  folders of the servers on this machine: `content_dir` (Maps, Game Modes, Mods) and `game_dir`, the server's game folder
  (its `halo3/game_variants` and the other variant folders), a `[[server]]` key of its own.
- From a shell: `oni-rcon --gametypes doctor --game GAME --content CONTENT` reports (`--json` gives `oni doctor`'s
  `{"fixed", "results": [{"check", "state", "text"}]}` plus each file), and `oni-rcon --gametypes upgrade ...` asks
  before it changes anything (`--dry-run` never does, `--yes` doesn't ask). `--log FILE` (or `-` for stdin) reads the
  server's log too, so files it skipped for any reason are named, under the server's own paths; a container's paths
  are matched to the folders here by their last folders and name.

Each file changed keeps its original beside it, byte for byte, as `NAME.json.oni-backup` (`-2`, `-3`... for a newer
original of the same name). The game never lists that file. The new file keeps its name, its date (lists sort by it),
its order of keys and everything but the values that change; it's checked again once written and put back if it
doesn't pass. A second run finds nothing to do.

What changes, from 0.10.1's table (`dedicated gametype-catalog`, kept in `gametype_catalog_010.json`), and only where
the value has one meaning:

| Written | Becomes | Why |
| --- | --- | --- |
| An Off/On setting above 1 (`rounds.teams: 3`, `2`) | On (1) | every Off/On setting has its own key, so nothing else was packed into it, and every value but 0 was written to turn it on |
| `rounds.teams` out of range in Capture the Flag, Assault, Territories, VIP / Infection, Juggernaut | On / Off | the engine always plays those modes in teams / without (`fixed_teams`) |
| `true` / `false` in an Off/On setting | 1 / 0 | 0.10 reads only numbers and labels |
| a whole number written `3.0` | 3 | the same |
| `null` | left out | how a 0.10 file says "the game type's own value" |
| a label 0.10 calls ambiguous across modes | this mode's number | as 0.10's own message asks |
| a byte-order mark at the start | removed | the game can't read past it |

Labels 0.10 reads itself (`"On"`, `"Battle rifle"`, an earlier editor's `"Allies"`) are left as they are. Anything else
(a number out of its range, such as `rounds.limit: 0`, an unknown option, a choice that doesn't exist) has no safe
value: the file stays exactly as it is and the report says why. `--game-type-value` leaves those settings out instead,
so the game type's own value plays: what 0.10.2 does by itself as it loads such a file.

The IBIGS `oni` helper's `oni doctor` and `oni gametypes` are lines away: `oni-rcon --gametypes doctor --json` with the
lobby's host folders, its `results` added to the doctor's; `oni gametypes upgrade` runs `oni-rcon --gametypes upgrade`.

## What's different on 0.10, for anyone reading the code

- `admins` answers `{"configured": [...], "added": [...]}`. `players` says `admin` only for Admin and up, so staff come from
  `role list`, whose holders read `"<id> <name> (added by …)"`, `"<id> (dedicated.toml)"`, `"(admins)"`, `"(moderators)"`.
- A `join` event has an empty name; the roster has it a moment later. `engine_id` is null for a player not in a game.
- `vote` events are `{"state", "subject", "caller"}`; status's `vote` is a line of words. A kick by vote says `by "vote (<caller>)"`.
- `mutes`, `panel list` answer in words only. `poll` with no arguments answers JSON in its text as well as its data.
- The status's `phase` is the round's (`no_game`, `playing`, `finished`); `server_phase` is `lobby`, `voting`, `countdown`,
  `loading`, `in_game`, `post_game`.
- The reload's answer in the log reads `[reload] Configuration reloaded.`, with no clock in front.
- There's no Wine: F10's WINE column stays empty, and the game thread is the busiest `guest thread`.
- A playlist that names an old built-in mode's file under `Game Modes/Project Reclaimer` fails; modes go by title now.

## Testing against a real server

`tests/test_live_010.py` runs against a throwaway 0.10 server and changes it, so it's skipped unless you point it at one:

    ONI_RCON_LIVE=127.0.0.1:46901 ONI_RCON_LIVE_PASSWORD=... pytest tests/test_live_010.py
