# ONI RCON desktop 1.14.0

A game queue, frame rate over RCON, private `!commands`, and a restart that keeps the game, all for Reclaimer 0.10 servers.

## New

- **Game queue.** F3 › MATCH has a GAME QUEUE button on Reclaimer 0.10+ servers: add a game (map, and optionally a mode), remove one, or clear the lot. Queued games show first in ROTATION as `Q1`, `Q2`… because that's the order they play in. The terminal console has it too. ([#58](https://github.com/viik2k/oni-rcon/pull/58))
- **Frame rate without a log.** F7 HEALTH shows a server's frame rate, frame times, traffic and players from RCON on Reclaimer 0.10+, so you can see a server falling behind even when the app doesn't read its log. The terminal console shows a frame rate for the first time. ([#57](https://github.com/viik2k/oni-rcon/pull/57))
- **Private `!commands`.** On Reclaimer 0.10.3+, your `!command` automation rules and Studio's `command()` register their words with the server, so a player's `!kills` or `!admin` reaches the console without showing in everyone's chat, and `/help` in game lists them. Older servers and players keep working through chat as before. ([#59](https://github.com/viik2k/oni-rcon/pull/59))
- **Restart keeps the game.** On Reclaimer 0.10.3+, RESTART SERVER saves the game first and restarts only once it's on disk; with `restore_state` on, the server plays on from it. If the save fails you're told why and asked whether to restart anyway. ([#60](https://github.com/viik2k/oni-rcon/pull/60))

## Fixed

- The event log shows Reclaimer 0.10.3's save and restore events as readable lines instead of raw dumps. ([#60](https://github.com/viik2k/oni-rcon/pull/60))

## Upgrading

Desktop installs update in place from the app. Config and plug-ins work unchanged.
