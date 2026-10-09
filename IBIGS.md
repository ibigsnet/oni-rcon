# IBIGS Reclaimer terminal

On fleet-console, run `oni-rcon` (alias: `reclaimer-console`). `oni-rcon --list` prints the inventory. The launcher is in `/srv/appdata/reclaimer/terminal/bin/oni-rcon`; Unraid's boot script restores the command links after reboot.

The private fleet mode discovers NODE-A, NODE-C, NODE-B, NODE-E and NODE-D from the existing controller. It refreshes the registry every 15 seconds, includes dynamic lobbies, and marks retired entries. Search names, maps, modes, ports or `@tags` with Ctrl+F. Sort by player count (default: most first), fullness, capacity, name, node or attention needed. Filter by node and state; F7 focuses sorting and F8 focuses state. Filtering never silently changes the selected command target. Ctrl+P opens the lobby palette. F1 shows players, F3 game operations, F4 bans, F5 RCON, F6 container operations, and Ctrl+B selects a broadcast scope. In F2 chat, type `@swat Hello everyone` or `@swat @snipers Hello everyone`. In F5 use `@swat say Hello everyone`. Multiple tags target the union once per lobby; tags match the complete rotation, not only the current mode. `@all` sends to every fresh running lobby. Unknown tags and stopped/stale targets receive no messages. `oni-rcon --tags` lists available tags.

An empty running lobby remains online, including when native RCON reports `no_game`. `Standby spare — intentionally stopped` means optional dynamic capacity that the controller currently wants stopped. An operator hold, an unexpectedly stopped server, a retired registry entry, and stale or unavailable telemetry have separate messages. At the 2026-10-08 check, all 73 baseline lobbies were healthy and six dynamic spares were intentionally stopped. Restart the terminal UI to pick up a new terminal image; game servers are unaffected.

**Find players:** press **Ctrl+G**, or run `oni-rcon --find-player "Player name"`. Names and player IDs support partial, case-insensitive matching. Search spans every current server on every node, regardless of the main list's filters. Duplicate names remain separate results with short IDs, server names, nodes and ports. The directory uses native status files, refreshes every 10 seconds while open, and shows unavailable/stale coverage. Player addresses and device fingerprints are not included. Selecting a result checks the exact player ID through native RCON before opening that server's Assets tab and player dossier; it does not join, move, message, or moderate anyone.

**All servers:** RESET clears server/node/state filters and shows all current registered lobbies, including empty games, dynamic copies, Forge workspaces and stopped spares. Retired entries are available through the Retired filter instead of cluttering the default list. Registry changes refresh every 15 seconds; Ctrl+R refreshes immediately. The count distinguishes visible current servers from the complete current inventory.

Container operations use the existing generation/configuration checks, capacity admission, journal, and occupancy protection. Apply waits for a continuously empty lobby. Force is disabled in this terminal. Game-changing RCON commands require a confirmation; ordinary moderation can intentionally affect players. Fleet broadcasts are one audited batch. Raw commands target one selected lobby.

The terminal reads shared fleet counts and polls native detail only for the selected lobby. Player search reads an authenticated directory cached for five seconds in the management backend, which reads status files once per node rather than polling every game's RCON. Opening a search result adds one native roster check. The terminal never mounts Docker's socket, SSH keys, game directories, or game RCON passwords. Its only credential is a private management token. The backend accepts that token only on loopback. Native reads are bounded, serialized per node, and stop retrying rejected authentication until the process/settings change.

Fleet mode does **not yet stream native chat or kill events**. The Intercepts panel currently shows management connection events, and medals derived from native kill events are therefore unavailable. Upstream direct-RCON mode retains that implementation for future integration. The join-password button is disabled; persistent settings belong in the managed server configuration. This is not a public portal.

The container is built locally as `ibigs/reclaimer-terminal`, uses an immutable image tag, runs unprivileged with a read-only filesystem and resource limits, exposes no ports, and removes itself when the terminal closes. Existing sessions keep their original image when the launcher is updated.

## Source and maintenance

Based on [viik2k/oni-rcon](https://github.com/viik2k/oni-rcon) through release 0.3.0. The 2026-10-08 source review covers commit `8f888563e917567c7f0d36789bbbbd0a147222d8`. Copyright (c) 2026 ibigs, LLC. GPL-3.0-or-later. The upstream MIT notice is in NOTICE. Private repository: https://github.com/ibigsnet/oni-rcon.

Local source: `/home/user/Projects/ibigs-reclaimer-console`.
fleet-console source: `/srv/appdata/reclaimer/terminal/source`.
Management adapter: `/home/user/Games/ProjectReclaimer/dashboard/reclaimer_control/console_read.py` and `operator_console.py`.

Upstream self-updating binaries are disabled in code. Updates require source review, the test suite, a pinned dependency audit, a new image tag, and a launcher update. Build using `Dockerfile.ibigs`; `requirements.lock` pins all runtime dependencies and hashes. Keep one prior image for rollback. Do not copy an upstream release executable over the maintained build.

Validation on 2026-10-08: private API/agent tests, a live rendered terminal, read-only native status probes, and a dependency audit with no known vulnerabilities reported. The console test suite passed 39 tests after the 0.3.0 merge. No production broadcasts, bans, kicks, stops or restarts were used as UI tests.
