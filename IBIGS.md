# IBIGS Reclaimer terminal

## Fleet rules (0.9.0+ibigs.17)

F12 in the fleet-console fleet console opens FLEET AUTOMATION, with SWITCH, RULES, ROLES and DISCORD. It's the same rules
sandbox as the customers' F8, kept by the admin console (`/api/rules`) and run on every node by a `reclaimer-rules`
worker that uses oni-rcon's own engine, copied unchanged into `reclaimer_control/oni_rules/`.

- **SWITCH starts OFF.** While it's off, no node opens a connection for rules. While it's on, each node keeps one
  native console connection per eligible lobby (operator-enabled, running, fresh, RCON up, not on hold) as long as at
  least one rule is enabled. A refused sign-in is never retried for that process.
- **Rules see their own node's lobbies.** "Admins" means the fleet admin list plus the ADMIN role from ROLES.
- **Starting servers is refused on the fleet**, because lifecycle stays with the controller. Everything else works
  as on customer VPSes: say, tell, cancel a vote, shuffle, Discord, and the frame-rate triggers (from each node's
  `perf.lags`).
- **Saving** pushes the whole document to every node with the journaled `configure_rules` RPC. FLEET AUTOMATION
  shows each node's last answer, and history is read back from the nodes.
- **Webhook URLs** are sent once and only ever shown masked.
- **Engine refactor:** `schedule.py` holds the dependency-free pieces the engine shares with the jobs (cron, match
  boundaries, atomic JSON), so the engine runs on the nodes without the console's own dependencies. Server names in
  rules can be fleet lobby IDs such as `node-a-49178`.

## More rule building blocks (0.9.0+ibigs.16)

**WHEN:**
- a kill with a weapon ("sniper", "sword"…)
- first blood
- a player reaching N kills in a match
- a betrayal (rules can only talk about it)
- a server's frame rate dropping under N, or recovering to N

**IF:** the player's K/D this match (with a minimum number of kills), the day of the week, the server's frame rate
under N, another server's player count.

**THEN:** wait N seconds between steps.

New templates: First blood, Sniper shout-out, Lag alert (to admins and Discord).

Frame rates reach the rules through the service. Once a minute, and only while a rule uses frame rate, it reads each
lobby's `oni health-report`, the same source F10 uses.

The guides are also published on the website at ibigs.us/halo-3/lag and ibigs.us/halo-3/automation.

## Automation: rules, roles, votes and Discord (0.9.0+ibigs.15)

F8 on customer VPSes is now AUTOMATION. Its sections are RULES, SCHEDULES (the existing jobs), ROLES, VOTES and
DISCORD. The plain guide is [docs/AUTOMATION.md](docs/AUTOMATION.md).

**RULES** read as WHEN → IF → THEN and are built entirely from menus (`rules.CATALOG`).
- Triggers:
  - every spree count, every multi-kill (Double Kill to Killionaire), Killjoy, and Perfection or any other medal the
    server reports
  - joins and leaves, and `!commands`
  - votes, and match start, end and pre-match
  - a server going down or coming back
  - a server filling up or emptying, and player counts crossing a number
  - timers
- Conditions: player count, admin online (here or elsewhere), the player's role, map or mode, time of day, and another
  server's state.
- Actions: say on this, every, other or one server; private messages to the player, to every admin wherever they're
  playing, or to a role; cancel the vote; shuffle; start a stopped server; post to Discord.
- Messages fill in `{player} {server} {medal} {kills} {args} {down}` and more.
- Ten templates are included (spree announcer, `!admin` call, server-down pager, full-server alert and so on).
- TEST shows the exact steps without sending anything. History shows what fired.
- Limits: each rule has a cooldown and an hourly cap, and rules never send a server more than 20 messages a minute.

Rules run in `ibigs-oni-jobs` on the same RCON connections as the scheduled jobs. That is one connection per lobby
while any rule is on. The engine counts sprees and multi-kills from the native kill feed the way the fleet's
`medal_notices` does: by engine ID, betrayals and suicides earn nothing, and a game's sprees reset with the game. A
lobby stopped with F7 is "stopped", not "down".

Rules cannot kick, ban, mute or restart a busy server. Chat triggers nothing except a message that starts with a
`!command`: IBIGS moderates cheating, not speech.

**ROLES** (`e` on F1 opens it for the selected player):
- **ADMIN** is written to `dedicated.toml` `admins` by `oni roles apply` the next time the lobby is empty, with the
  usual backup and a re-read check. The lobby then restarts only if it is still empty. The service tries once a
  minute. Hand-added admins are kept; only IDs recorded in `applied.json` are ever removed.
- **VIP** can't be vote-kicked, starting now: a built-in rule cancels the ballot.
- **MOD** is stored until Project Reclaimer supports it.

**VOTES** sets each lobby's `votes` switch, applied the same way as ADMIN.

**DISCORD** walks through creating a webhook, takes the pasted URL (only `discord.com/api/webhooks/…` URLs are
accepted), offers a test post and shows the URL masked. Posts never ping anyone and are spaced 2 seconds apart.

## Frame-time chart and bottleneck (0.9.0+ibigs.13)

Under F10's FRAME RATE table, FRAME TIME charts the selected server's last 30 minutes, one column a minute. Each
column shows the average frame time, the longest frame (`╸`) and the 60 fps line. Strips under the chart show the
game thread's and wineserver's share of one core for the same minutes. The table adds GAME, WINE and BOTTLENECK, and
F3's SITREP adds GAME THREAD.

The game simulates on one thread (Wine names it MAIN_THREAD), and most of its system calls are round trips to
wineserver64, which is also single-threaded. A lobby therefore runs out of one core's speed long before the box runs
out of cores. On 2026-10-14, Mega Fat Kid on NODE-A fell behind with its game thread at 79% of a core, wineserver at
59% (about 36,000 round trips a second) and 0.1% waiting for a CPU. That is the two threads waiting on each other, not
host contention.

BOTTLENECK reads the kernel's scheduler counters for those two threads, comparing two reports.
- ONE CORE MAXED: the game thread runs 90% or more.
- GAME+WINE ONE CORE: the game thread and wineserver together take 130% or more of a core.
- HOST BUSY: the game thread spends 10% or more of its time waiting for a free CPU.
- CPU CAP: the container's CPU limit throttled it.
- NOT CPU: the server is slow, but none of the above applies.

A server that keeps up shows its game thread's HEADROOM. The first two verdicts are fixed by a faster core or fewer
players, not by more cores.

Customers get the counters from `oni health-report`, which reads its own lobby's threads from `/proc`. The fleet gets
them from the node agents' `threads` row.

## Server frame rate (0.9.0+ibigs.12)

F10 HEALTH has a FRAME RATE table: every server's frames a second, slowest first. It shows FPS NOW (the latest minute),
5 MIN (the average), WORST (the slowest seconds' average), LONGEST FRAME, PLAYERS, CPU (fleet: the lobby's share of one
core) and LAST SLOW. A server falling behind shows its frame rate on its card (`38fps 9/16`), and F3's SITREP has a
SERVER FPS line. When a server drops under 40 fps, the F2 feed says so once, and says so again when it has kept up for
3 minutes. These are feed messages only, with no banner or beep. The HOST box adds CPU LOAD.

The numbers come from the server's own log. It aims at 60 fps and logs each minute it spent under 54 ("Server falling
behind: under 54 frames a second for 58 of the last 58 seconds (29 on average, longest frame 125 ms) with 85
players"). A minute's rate is its slow seconds at their average and the rest at 60. That can slightly overstate a
fine second, but it never invents a slowdown. RCON and status.json carry no frame rate.

Customer VPSes need the matching `oni` wrapper, which ships in the same release. Its `health-report` now passes the
lag lines and says `perf=1`. An older wrapper's report shows `—`, not a false 60. The fleet console reads `perf.lags`
from each node agent's snapshot row. A node whose agent doesn't send them shows `—`.

## Content browser, playlists, copy and team moves (0.9.0+ibigs.11)

LOAD MAP+MODE, CHANGE MAP, CHANGE MODE, QUEUE NEXT, Forge's `l` and the playlist editor open one browser: a tree of
stock maps, mod maps, ReclaimerForge downloads (with title, version and author, grouped by folder), the content
folder, Halo 3 and Matchmaking variants, and saved and Workshop variants. The groups come from each reference's
shape, because the server's `maps` and `modes` replies only say map, mode or variant. Type to filter by name, author,
folder or file. Click NAME, INSTALLED, DOWNLOADS or RATING to order; click again to reverse. Where Forge installs are
possible, GET MORE FROM RECLAIMERFORGE lists listings not installed yet; choosing one installs it (confirmed, every
file checked) and then uses it once the server lists it. F6 orders the catalog by any column the same way (`o` next
column, `r` reverse). Forge's "load now" now matches installed files by name (`Game Modes/Forge/rf-<sha>.json`);
before, it always said the server didn't list them.

F3 PLAYLISTS (customer VPSes; a playlist needs 3 to 128 games, because the server's ballots offer three each time) shows the Playlists library (Forge downloads and your own) and what each lobby plays,
with filtering and column ordering. It makes and edits playlists; ADD GAME uses the browser, and weights, order,
copy and remove are on keys and buttons. It deploys to one lobby or all. The work is done by `oni playlist`
(`list|show|save|check|use|single|delete`), also usable from the shell. Before anything changes, the server checks
every game itself (`dedicated playlist show`, inside the running lobby). Each lobby gets its own copy in
`playlists/`, and `dedicated.toml` is backed up to `.oni-backup/` and re-read before replacement. Lobbies restart
only when asked, with F7's player checks; players are disconnected only by explicit choice. Forge's playlist copies
are never rewritten: edits are saved as copies in `Playlists/Oni`.

Ctrl+Y on any page or dialog copies what is selected: player ID, name, tag, clan, ban target, listing or version ID,
server address, any cell, the row, the whole table, or the feed. Addresses stay blanked while redacted.

F2 now follows the selected server; ALL SERVERS, or F2 again, shows every server. A team move reports the game's
verdict: the server's "Moving X to Red team." only means the request was accepted. The game can still refuse, for
example "Teams must stay even", "Players are joining or leaving" or "Already on Red team". Customer consoles show the
refusal from the event; the fleet console re-reads the roster for up to about 25 seconds.

## Customer VPS controls (0.9.0+ibigs.10)

Customers run `oni` in their VPS. F6 opens ReclaimerForge with provisioned access or their personal API-key override; F7 starts and stops their lobbies;
F8 edits persistent jobs; F9 checks updates and enables or pauses automatic updates. The console opens even when
every lobby is stopped. Fleet mode keeps its existing controls and never enables the tenant scheduler.

From 0.9.0+ibigs.10 customer consoles open with the same full boot sequence as the fleet-console fleet console every time
(`oni --intro quick` or `oni --no-intro` opt out). Enter, any other key or a click skips it.

Jobs support five-field numeric cron with an explicit IANA timezone, UTC-aligned intervals in minutes, and observed
match boundaries. Before-match broadcasts run when voting opens or loading begins; after-match broadcasts require
the native `finished` phase. Round breaks and reconnects do not invent completed matches. Team shuffles can run at
scheduled times or at match start, after a fresh check that the server is playing. No arbitrary shell jobs are accepted.

The `ibigs-oni-jobs` service runs independently of the terminal as a local system service. Its fixed maintenance
actions run as root so an expired interactive login cannot prevent scheduled updates. Configuration and the last 100 outcomes
live under `/srv/ibigs/automation`, readable by `ibigs-admin`. No game password, Forge key or player roster is stored
there. Jobs are claimed durably before execution; an uncertain reply is never retried. Missed occurrences are skipped,
and repeated local times during a DST fold are not replayed. Stop, restart and game-update jobs use the existing
occupancy checks without force. New-client images enable automatic console and guarded game updates; existing preferences are preserved. Console updates use the signed private IBIGS channel;
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

**ReclaimerForge:** F9 opens the authenticated, cached fleet catalog, including thumbs-up/down votes, coauthors, recent download history, release notes, and the timed collections under FAVOURITES. Missing collection support is reported explicitly; an empty current collection is not inferred from an unavailable feed. The fleet view shows the provider's reviewed catalog without exposing its API key or permitting downloads or live playlist changes from the terminal. The managed controller owns those operations. In customer VPS mode, run `oni` and use F6 for Forge. Provisioning supplies a private customer key command; a customer may save a personal key override in their persistent Forge configuration. Releases and assets are pinned; downloads must pass size and SHA-256 checks. New content is staged without overwriting earlier versions. Shell-string credential commands and unrestricted download hosts are disabled; an approved argument array can read the provisioned local credential. Customer mode supports local round recording; fleet recording remains disabled until native event delivery is available.

**Superintendent:** the sidebar includes the upstream status face. Customer/direct RCON mode reacts to the real event feed, with redacted addresses and reduced-motion support. fleet-console receives bounded findings from independent, read-only node observers through the private authenticated operator API. F11 shows findings and observer coverage; the managed adapter still does not stream the full native feed into F2.

**Join-time ping:** the player table and dossier can show the latency recorded when a player joined, using an optional direct/SSH log reader. It is not live ping or packet-loss monitoring. fleet-console has no game-log access and shows `—` for unknown ping. The automatic customer lobby configuration does not enable it; an operator can supply a private manual configuration with `ping_log = ["tail", "-F", "/srv/reclaimer/logs/{port}.log"]`, existing log-read permission and their server entries. Run `oni --config /absolute/path/to/oni-rcon.toml`; do not edit the generated lobby list. Shell strings and shell expansion are disabled. SSH readers require a trusted host key and POSIX host with `setsid`. See the [configuration examples](README.md#player-ping).

**Filter players:** press **Ctrl+G**, or run `oni-rcon --find-player "Player Z74"`. Every term must match the same player row, across name, service tag, clan tag, team, ID, server, node and port. Matching is partial, Unicode-normalized and case-insensitive; quotes keep phrases together. Use `name:Player tag:Z74`, `node:node-d`, or `@Z74` for an exact service-tag match. Results show Player, TAG, Clan, Team, Server, Node, Port, Player ID and Seen. Missing native details display `—`; coverage reports how many occupied rosters have current tags. The filter includes every current server regardless of the main list's filters. Duplicate names remain separate. Selecting a result verifies its full player ID before opening the server; filtering never moves, messages or moderates players.

**All servers:** RESET clears server/node/state filters and shows all current registered lobbies, including empty games, dynamic copies, Forge workspaces and stopped spares. Retired entries are available through the Retired filter instead of cluttering the default list. Registry changes refresh every 15 seconds; Ctrl+R refreshes immediately. The count distinguishes visible current servers from the complete current inventory.

Container operations use the existing generation/configuration checks, capacity admission, journal, and occupancy protection. Apply waits for a continuously empty lobby. Force is disabled in this terminal. Game-changing RCON commands require a confirmation; ordinary moderation can intentionally affect players. Fleet broadcasts are one audited batch. Raw commands target one selected lobby.

The terminal reads shared fleet counts. Player filtering uses an authenticated directory cached for five seconds. Locations come from native status files; tags, clans and teams come from an on-demand native cache lasting at most 20 seconds. Empty lobbies are skipped. A six-second per-node budget, a 16-read ceiling, shared locks and authentication backoff bound collection. Typing filters only local results. Failed detail reads preserve name/ID locations and explicitly reduce tag coverage. Opening a result adds one native identity check. The terminal never mounts Docker's socket, SSH keys, game directories, or game RCON passwords. Its only credential is a private management token. The backend accepts that token only on loopback. Native reads are bounded, serialized per node, and stop retrying rejected authentication until the process/settings change.

Fleet mode does **not yet stream native chat or kill events**. The Intercepts panel currently shows management connection events, and medals derived from native kill events are therefore unavailable. Upstream direct-RCON mode retains that implementation for future integration. The join-password button is disabled; persistent settings belong in the managed server configuration. This is not a public portal.

The container is built locally as `ibigs/reclaimer-terminal`, uses an immutable image tag, runs unprivileged with a read-only filesystem and resource limits, exposes no ports, and removes itself when the terminal closes. Existing sessions keep their original image when the launcher is updated.

The 0.8.1 integration includes the upstream Halo archive and larger Superintendent. Reduced motion remains the IBIGS default. Run `TEXTUAL_ANIMATIONS=full oni --intro full` to enable the archive; `--intro quick` and `--no-intro` select shorter startup. The customer wrapper keeps automatic lobby discovery when `--intro` is supplied. Boot preferences persist separately from the customer Forge key.

## Source and maintenance

Based on [viik2k/oni-rcon](https://github.com/viik2k/oni-rcon) through release [0.9.0](https://github.com/viik2k/oni-rcon/releases/tag/v0.9.0), upstream main commit `10fe552c81e48e2a4c41d377a07e566ef0edc564`. Runtime `0.9.0+ibigs.10` is packaged as a hash-pinned patch over maintained merge `2b8e1dc92389ff9fd873d8753a664ea72ef2d18c`. It builds on the reviewed 0.6 integration and retained fixes at `47410c1286fe1a95a158073230d762c00d3127ff`. Signed manifests identify the immutable deployment artifact. Copyright (c) 2026 ibigs, LLC. GPL-3.0-or-later. The upstream MIT notice is in NOTICE. Customer bundles include full corresponding source and licenses. Private repository: https://github.com/ibigsnet/oni-rcon.

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

## 0.9 Health and startup

fleet-console: `oni-rcon` opens the boot sequence and fleet. `oni-rcon --no-intro` skips it; `TEXTUAL_ANIMATIONS=full oni-rcon --intro full` shows the upstream Halo archive. Ctrl+P exposes the boot preference. Preferences persist under the terminal's dedicated `state` directory; existing sessions retain their installed image until closed.

F10 on fleet-console shows all five nodes' cached host CPU, free RAM, RX/TX throughput, telemetry age, container start/restart counters and Docker OOM flags. One shared read covers the fleet every 15 seconds. It does not read native worker crash logs or report a blue-flame workaround as installed. The event list covers container changes observed while the console is open, not a historical incident archive. CPU is the host fraction, and network rates are host measurements, not a game tick-rate measurement. Stale readings are marked explicitly. Health refresh and appearance cannot change the selected command target.

Customers use `oni`, with F10 for the local upstream crash-log reader; F7 power, F8 jobs and F9 updates remain available. An alert occupies a reserved line so showing and dismissing it cannot shift panels. Exit 137 without Docker OOMKilled is retained as an exit code, not labeled a proven memory kill. The separate source patch and its SHA-256 accompany the customer bundle and are applied by the provider build script before offline packaging.

## Fixed sidebar and public identity

The Superintendent and connection status are docked at the bottom of the sidebar. The server list has its own bounded scroll area; filtering, sorting, adding dynamic lobbies and moving between tabs cannot push the footer off screen. Customer F7/F8/F9 controls are included in the space budget. This changes layout only, without additional polling.

The fleet-console display label is `IBIGS Admin`. Direct/customer RCON defaults to `Administrator`, preserving explicit `--by` or private configuration overrides. fleet-console sends actions through the authenticated management API and node-local Source RCON; it does not send the terminal label with moderation commands. The controller retains its authenticated actor in private audit records.


### Superintendent findings and alert acknowledgement (0.9.0+ibigs.7)

F11 or clicking the Superintendent opens the last hour of observations (up to 200).
The shared alert row includes CLEAR ALL ALERTS; Ctrl+L works even in a text input.
Acknowledgement clears all unread counts, pulses, banners and toasts, including
filtered stations. It preserves report history and current health conditions.
New observations can alert again. Acknowledgement is local to the open console.

Reports include three kick-vote starts against one target within five minutes,
two vote starts in each direction for a vote fight, five kills against at least
three opponents within ten seconds, native anti-cheat flags, and player requests
for review. Twelve timestamped kills against six opponents in three seconds is
a review flag with alternative explanations, never a cheat verdict. No detector
issues moderation commands. Perfection requires an explicit native medal event;
partial-match kills/deaths are insufficient. Windows reset on disconnects, feed
gaps and phase changes. Replayed or stale timestamped events do not count.

Customer observations run while the console is open. fleet-console uses independent,
read-only node observers and the authenticated /api/findings operator route.
Coverage distinguishes connected occupied lobbies from unavailable observers.
The observers keep only bounded findings, not player addresses or fingerprints.

Fleet player search supports one name edit for 4–7 characters and two for longer
names, including adjacent transpositions. Exact/substring matches rank first;
approximate names are labeled in the results. Tag, ID, node and other structured
filters remain strict, all terms apply to one player, and stale rosters are
excluded. Server selection still revalidates the exact player ID.


### Match controls and actionable findings (0.9.0+ibigs.8)

F1 shows End round and End match above the roster. F3 retains map, mode, queue,
team and vote controls. Confirmations name the target server, map, mode and player
count. The command target remains the confirmed server if selection changes.

Notifications have an Open server / player link. F11 supports clicking a report,
Enter to open it, and separate server, subject and reporter links in the evidence.
Opening a report refreshes the roster and matches the full public player ID.
Unresolved names, duplicate names, disconnected servers and departed players leave
no player selected. Server filters are reset when needed to reveal the target.
Only navigation/read requests occur; moderation always remains a separate action.
Legacy clients still receive the original report schema; link metadata is additive
at the private response level. Stored metadata excludes addresses and fingerprints.

Kill-count heuristics are disabled for Infection, Fat Kid and SWAT, including SWAT
lobbies whose engine mode is Slayer. Mode/map changes clear partial kill windows.
Other modes retain highlights with a five-minute per-player and one-minute per-server
cooldown. Explicit native medals, native anti-cheat flags and vote-pattern reports
are unaffected by the mode filter. A native flag or player accusation is not proof.

Chat uses conservative English request/allegation rules instead of keyword matching.
Examples: “need an admin”, “looking for help”, “please kick Player”, “Player is cheating”.
Ordinary game-mod talk, command tutorials and negated accusations are excluded.
Repeated topics across speakers wait two minutes; each speaker/category waits one
minute. Phase changes and reconnects reset evidence windows but keep cooldowns.
Evidence retains the original request for review. No external language service is used.


### Conversation context (0.9.0+ibigs.9)

Review findings now include player chat from the same lobby: at most 20 messages in the
120 seconds before the finding, the triggering message when applicable, and at most 20
messages in the following 120 seconds. The alert is immediate. Follow-ups update the
existing finding without re-alerting or undoing acknowledgement, including chat requests
whose duplicate notifications were suppressed. Kick-vote patterns and native flags also
receive context. Server-channel automated announcements are excluded.

F11 labels BEFORE / TRIGGER / AFTER, timestamps and channels, and links speakers through
the same exact-ID roster check. EXPAND hides the report list to make room for reading;
SPLIT VIEW restores it. New messages preserve the evidence scroll position. Ambiguous
pronouns are not roster targets. Connection gaps/restarts close pending conversations
with an interruption notice; round changes keep chat continuity. Context begins only
when the observer/console has a live feed, so earlier messages cannot be reconstructed.

The existing 200-finding / one-hour retention remains. Ring buffers, text lengths and
follow-up windows are bounded. Fleet context is an additive authenticated response field;
older clients continue receiving the original report schema. A byte budget keeps responses
under the terminal ceiling, prioritizing recent conversations. Already-received context
is never discarded by an older or budget-limited poll. No external language service is used.
