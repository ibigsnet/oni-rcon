# IBIGS Reclaimer terminal

## Customer VPS controls (0.8.1+ibigs.1)

Customers run `oni` in their VPS. F6 opens ReclaimerForge with their own API key; F7 starts and stops their lobbies;
F8 edits persistent jobs; F9 checks updates and enables or pauses automatic updates. The console opens even when
every lobby is stopped. Fleet mode keeps its existing controls and never enables the tenant scheduler.

Jobs support five-field numeric cron with an explicit IANA timezone, UTC-aligned intervals in minutes, and observed
match boundaries. Before-match broadcasts run when voting opens or loading begins; after-match broadcasts require
the native `finished` phase. Round breaks and reconnects do not invent completed matches. Team shuffles can run at
scheduled times or at match start, after a fresh check that the server is playing. No arbitrary shell jobs are accepted.

The `ibigs-oni-jobs` service runs independently of the terminal as a local system service. Its fixed maintenance
actions run as root so an expired interactive login cannot prevent scheduled updates. Configuration and the last 100 outcomes
live under `/srv/ibigs/automation`, readable by `ibigs-admin`. No game password, Forge key or player roster is stored
there. Jobs are claimed durably before execution; an uncertain reply is never retried. Missed occurrences are skipped,
and repeated local times during a DST fold are not replayed. Stop, restart and game-update jobs use the existing
occupancy checks without force. Automatic updates are opt-in. Console updates use the signed private IBIGS channel;
game updates pin the official image digest and retain rollback support. `oni jobs list`, `history`, `add`, `pause`,
`resume`, and `delete` provide the same schedules from the shell.

Install `hosting/template/addons/oni-jobs` from the IBIGS provisioning kit once on each enrolled VPS. A bundle update
alone does not install that system service. After a console release changes, the idle worker reopens itself on the new
release. Existing jobs, Forge preferences and stopped lobbies remain intact.

On fleet-console, run `oni-rcon` (alias: `reclaimer-console`). `oni-rcon --list` prints the inventory. The launcher is in `/srv/appdata/reclaimer/terminal/bin/oni-rcon`; Unraid's boot script restores the command links after reboot.

The IBIGS palette keeps Oni's amber/cyan theme with brighter labels, team colours and status text. Selected rows use a dark blue background so names and values remain readable. Disabled controls retain legible grey labels; they remain disabled. The active tab and keyboard focus also use weight or underlining, so colour is not the only cue. Reopen the terminal after an update to load the new palette.

The optimized 0.7.0+ibigs.2 build opens panels and dialogs in place and reserves scrollbar space. Numeric player columns keep their width as scores change. Decorative motion defaults off; an explicit `TEXTUAL_ANIMATIONS` environment setting is still respected for the remaining optional effects. The Superintendent still reacts to events, and its unchanged picture is reused.

The terminal retains 1,000 recent event entries, at most 1,000 rendered lines per log, and 200 command-history entries. Logs waiting for their first layout are bounded too. This limits local UI history; authoritative server and controller logs are unchanged. Unselected fleet lobbies release native detail caches on the next registry refresh. Only a new shared snapshot starts overview work, while selected player/status details keep their three-second poll. Hidden panels paint their latest cached state when opened, and a disconnect disables selected controls immediately.

An offline 200-lobby/32-player benchmark compared the initial 0.7 integration with this optimization: idle process CPU over 20 seconds fell from 1.230 to 0.389 seconds; fetch workers from 4,000 to 206; native player reads stayed at seven. After 5,000 synthetic hidden chat events and opening the log, RSS fell from 108.1 to 87.4 MiB. These are synthetic measurements, not a live capacity guarantee. Reduced retained history and disabled decorative motion contribute to the savings. The former pre-layout log queue grew beyond the visible line limit; both raw and deferred retention are now bounded.

The private fleet mode discovers NODE-A, NODE-C, NODE-B, NODE-E and NODE-D from the existing controller. It refreshes the registry every 15 seconds, includes dynamic lobbies, and marks retired entries. Search names, maps, modes, ports or `@tags` with Ctrl+F. Sort by player count (default: most first), fullness, capacity, name, node or attention needed. Filter by node and state; F7 focuses sorting and F8 focuses state. Filtering never silently changes the selected command target. Ctrl+P opens the lobby palette. F1 shows players, F3 game operations, F4 bans, F5 RCON, F6 container operations, and Ctrl+B selects a broadcast scope. In F2 chat, type `@swat Hello everyone` or `@swat @snipers Hello everyone`. In F5 use `@swat say Hello everyone`. Multiple tags target the union once per lobby; tags match the complete rotation, not only the current mode. `@all` sends to every fresh running lobby. Unknown tags and stopped/stale targets receive no messages. `oni-rcon --tags` lists available tags.

An empty running lobby remains online, including when native RCON reports `no_game`. `Standby spare — intentionally stopped` means optional dynamic capacity that the controller currently wants stopped. An operator hold, an unexpectedly stopped server, a retired registry entry, and stale or unavailable telemetry have separate messages. The 2026-10-09 inventory contains 82 running lobbies across five nodes; use the live inventory for current counts. Restart the terminal UI to pick up a new terminal image; game servers are unaffected.

**ReclaimerForge:** F9 opens the authenticated, cached fleet catalog, including thumbs-up/down votes, coauthors, recent download history, release notes, and the timed collections under FAVOURITES. Missing collection support is reported explicitly; an empty current collection is not inferred from an unavailable feed. The fleet view shows the provider's reviewed catalog without exposing its API key or permitting downloads or live playlist changes from the terminal. The managed controller owns those operations. In customer VPS mode, run `oni` and use F6 for Forge: the customer supplies their own key in their private persistent Forge configuration. Releases and assets are pinned; downloads must pass size and SHA-256 checks. New content is staged without overwriting earlier versions. Shell commands for credentials and unrestricted download hosts are disabled. Customer mode supports local round recording; fleet recording remains disabled until native event delivery is available.

**Superintendent:** the sidebar now includes the upstream status face. Customer/direct RCON mode reacts to the real event feed, with redacted addresses and reduced-motion support. fleet-console displays “Native event feed not connected” because the managed adapter does not stream game events. Its presence does not add event coverage or game-server polling.

**Join-time ping:** the player table and dossier can show the latency recorded when a player joined, using an optional direct/SSH log reader. It is not live ping or packet-loss monitoring. fleet-console has no game-log access and shows `—` for unknown ping. The automatic customer lobby configuration does not enable it; an operator can supply a private manual configuration with `ping_log = ["tail", "-F", "/srv/reclaimer/logs/{port}.log"]`, existing log-read permission and their server entries. Run `oni --config /absolute/path/to/oni-rcon.toml`; do not edit the generated lobby list. Shell strings and shell expansion are disabled. SSH readers require a trusted host key and POSIX host with `setsid`. See the [configuration examples](README.md#player-ping).

**Find players:** press **Ctrl+G**, or run `oni-rcon --find-player "Player name"`. Names and player IDs support partial, case-insensitive matching. Search spans every current server on every node, regardless of the main list's filters. Duplicate names remain separate results with short IDs, server names, nodes and ports. The directory uses native status files, refreshes every 10 seconds while open, and shows unavailable/stale coverage. Player addresses and device fingerprints are not included. Selecting a result checks the exact player ID through native RCON before opening that server's Assets tab and player dossier; it does not join, move, message, or moderate anyone.

**All servers:** RESET clears server/node/state filters and shows all current registered lobbies, including empty games, dynamic copies, Forge workspaces and stopped spares. Retired entries are available through the Retired filter instead of cluttering the default list. Registry changes refresh every 15 seconds; Ctrl+R refreshes immediately. The count distinguishes visible current servers from the complete current inventory.

Container operations use the existing generation/configuration checks, capacity admission, journal, and occupancy protection. Apply waits for a continuously empty lobby. Force is disabled in this terminal. Game-changing RCON commands require a confirmation; ordinary moderation can intentionally affect players. Fleet broadcasts are one audited batch. Raw commands target one selected lobby.

The terminal reads shared fleet counts and polls native detail only for the selected lobby. Player search reads an authenticated directory cached for five seconds in the management backend, which reads status files once per node rather than polling every game's RCON. Opening a search result adds one native roster check. The terminal never mounts Docker's socket, SSH keys, game directories, or game RCON passwords. Its only credential is a private management token. The backend accepts that token only on loopback. Native reads are bounded, serialized per node, and stop retrying rejected authentication until the process/settings change.

Fleet mode does **not yet stream native chat or kill events**. The Intercepts panel currently shows management connection events, and medals derived from native kill events are therefore unavailable. Upstream direct-RCON mode retains that implementation for future integration. The join-password button is disabled; persistent settings belong in the managed server configuration. This is not a public portal.

The container is built locally as `ibigs/reclaimer-terminal`, uses an immutable image tag, runs unprivileged with a read-only filesystem and resource limits, exposes no ports, and removes itself when the terminal closes. Existing sessions keep their original image when the launcher is updated.

The 0.8.1 integration includes the upstream Halo archive and larger Superintendent. Reduced motion remains the IBIGS default. Run `TEXTUAL_ANIMATIONS=full oni --intro full` to enable the archive; `--intro quick` and `--no-intro` select shorter startup. The customer wrapper keeps automatic lobby discovery when `--intro` is supplied. Boot preferences persist separately from the customer Forge key.

## Source and maintenance

Based on [viik2k/oni-rcon](https://github.com/viik2k/oni-rcon) through release [0.8.1](https://github.com/viik2k/oni-rcon/releases/tag/v0.8.1), upstream commit `ee5cbe60e0a55cd45b303f2d365144f373525fd3`. This source version is `0.8.1+ibigs.1`. It builds on the reviewed 0.6 integration and retained fixes at `47410c1286fe1a95a158073230d762c00d3127ff`. Copyright (c) 2026 ibigs, LLC. GPL-3.0-or-later. The upstream MIT notice is in NOTICE. Customer bundles include full corresponding source and licenses. Private repository: https://github.com/ibigsnet/oni-rcon.

Local source: `/home/user/Projects/ibigs-reclaimer-console`.
fleet-console source: `/srv/appdata/reclaimer/terminal/source`.
Management adapter: `/home/user/Games/ProjectReclaimer/dashboard/reclaimer_control/console_read.py` and `operator_console.py`.

Upstream self-updating binaries are disabled in code. Updates require source review, the test suite, a pinned dependency audit, a new image tag, and a launcher update. Build using `Dockerfile.ibigs`; `requirements.lock` pins all runtime dependencies and hashes. Keep one prior image for rollback. Do not copy an upstream release executable over the maintained build.

Historical 0.4 deployment validation on 2026-10-09: 74 console tests passed, including Forge schema, credential isolation, download checks, terminal control-character handling, and fleet/customer UI behavior. Live checks covered all five nodes, the F9 catalog, and rejection of unauthenticated catalog requests. An isolated customer clone passed the offline installer and three-lobby localhost RCON test; the test VM and ISO were removed. That deployment used `ibigs/reclaimer-terminal:2026.10.09.3`; source review alone does not change an installed launcher. No production broadcasts, bans, kicks, stops or restarts were used as UI tests.

The 0.6 integration preserves actual Forge schema aliases and sequence ordering while keeping withdrawn releases visible and unselectable. A stale catalog cannot override a known withdrawal, and a manifest explicitly marked withdrawn or draft is refused. The state file accepts earlier singular withdrawal records and tracks several withdrawn versions of the same listing across separate customer lobbies. Each affected lobby must move off its withdrawn release before that release's warning clears. No content is removed, reloaded, or restarted automatically by these warnings.

The 0.6 source validation covered 127 upstream and maintained test cases, including timed cached favourites, missing-cache behavior, vote sorting, multi-version withdrawals, small-terminal layout, F9 catalog alongside F6 lifecycle controls, player search and target preservation, download validation, and terminal credential isolation. Client publication and installation use the separately maintained IBIGS update channel; the application never replaces itself with an upstream release binary.

The 0.7 integration limits sidebar redraws to visible cards, fixes Operations at 80 columns, shows the correct ban moderator and adds optional join-time ping. IBIGS adapts card visibility to sorting and filtering, preserves the selected command target, and correctly registers new dynamic lobbies. Focused regressions cover these behaviors and log-reader process cleanup; combined-suite and container validation belong to the release record. An installed launcher changes only when its separately reviewed image is promoted.

## Customer lobby controls

The customer console includes F7 and a sidebar button to start, stop or restart this VM’s lobbies through the local `oni` wrapper. It shows players before confirmation and defaults to abort. Occupied or unreadable lobbies require a second explicit disconnect confirmation. If an empty lobby gains players before the helper’s final check, the operation stops and asks you to reopen F7 to review occupancy. The panel fits 80-column terminals. This local customer helper does not add Docker access or power controls to the fleet-console fleet adapter.
