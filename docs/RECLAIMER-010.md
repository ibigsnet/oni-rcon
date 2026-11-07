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
