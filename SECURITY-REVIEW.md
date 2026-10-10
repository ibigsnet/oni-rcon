# Source review — 2026-10-10, upstream 0.9.0

Reviewed upstream v0.9.0 and main through `10fe552c81e48e2a4c41d377a07e566ef0edc564`. The existing maintained merge is `2b8e1dc92389ff9fd873d8753a664ea72ef2d18c`; the 0.9.0+ibigs.2 release carries a separately hashed local patch against that base. Upstream adds Health reports and resolves player names across native ID formats. No malicious behavior was identified in this scoped source review. This is not a formal security audit or review of the upstream executable.

The maintained health command accepts explicit arguments, bounds output to 2 MiB, has a timeout, cleans up helper process groups, and keeps strict SSH host verification. Customer helpers read local log/container/host data. F10 avoids the existing customer F7–F9 shortcuts. Upstream binary self-updating remains disabled; customer publication requires the existing signed IBIGS channel.

fleet-console reads one bounded shared snapshot from the existing private fleet-console controller endpoint `192.0.2.10:9188` every 15 seconds, with redirects and environment proxies disabled and no authorization header. Only node resource readings and container metadata survive projection. It adds no game polling, socket mount, SSH key, raw log access or runtime privileges. Unknown/stale metrics remain unknown/stale. Worker crash-log and workaround-service coverage is explicitly unavailable in fleet mode. OOM flag transitions and observed container generations are events, not proof of player disconnections.

Corrected an upstream diagnostic ambiguity: exit 137 alone does not prove an OOM; require Docker's OOMKilled flag. Reserved the health alert line so appearing/dismissing alerts do not shift terminal panels. Halo startup options now work in the managed fleet launcher; persistent boot preferences contain no credentials. Existing bright colors, reduced motion, bounded logs, selected-only native detail polling and safe command targets are retained.

No runtime dependency declarations, pins or base-image digest changed in this pass. Source tests and live read-only canary evidence are recorded in `/home/user/outputs/reclaimer-oni-090-20261010`. No live game action is used to test terminal controls.

# Source review — 2026-10-10, upstream 0.8.1

Reviewed upstream range `dfd7a6124f510139f00377c3aff5fadc05802918` (0.7.0) through `ee5cbe60e0a55cd45b303f2d365144f373525fd3` (0.8.1). The changes add generated Halo archive artwork, boot preferences and sidebar sizing/rendering. They add no dependencies, network destinations, subprocess paths or runtime privileges. Boot preferences use the existing owner-only atomic writer. IBIGS retains reduced motion by default, pre-layout sidebar sizing, tenant/fleet selection, the injected RCON adapter and disabled upstream binary updates. Customer schedules and fixed maintenance actions remain local to their VPS. This is source review, not a formal audit or verification of upstream executables.

# Source review — 2026-10-09, upstream 0.7.0

The 0.7.0+ibigs.2 performance pass keeps all network/authentication and write-confirmation boundaries. It bounds the pre-layout terminal log queue as well as rendered history, releases unselected fleet detail caches, and coalesces overview work by snapshot generation. Native player polling stays at three seconds. Selected controls repaint immediately on disconnect; hidden views refresh before use. The pre-F7 performance suite passed 149 tests, including unchanged command targets, redaction after hidden-panel transitions, bounded hidden logs, preserved scrollback, and process cleanup. No runtime dependencies changed.

Reviewed upstream range: `e5772fe56a558f164f6a55d270795d8bc8104718` (0.6.0) through
`dfd7a6124f510139f00377c3aff5fadc05802918` (0.7.0), including 0.6.1
`a8c17e551408fed530f35ed90cef0fe84799e632` and 0.6.2 `3e32178ca7010cc197d820a33ac94c3cf3016e7f`.
Primary release notes: [0.6.1](https://github.com/viik2k/oni-rcon/releases/tag/v0.6.1),
[0.6.2](https://github.com/viik2k/oni-rcon/releases/tag/v0.6.2),
[0.7.0](https://github.com/viik2k/oni-rcon/releases/tag/v0.7.0). Maintained source version: `0.7.0+ibigs.2`.
This is source review, not a formal audit or verification of upstream executables.

This range adds visible-card rendering and cached layout measurements, an 80-column Operations layout fix,
correct ban-moderator attribution, and optional join-time player ping from server connection logs. Runtime
dependency pins, dependency declarations and the Docker base are unchanged; no new dependency audit is claimed.

IBIGS integration decisions:

- Replaced upstream's new shell-string `ping_log` subprocess with an opt-in argument array. Configured shell
  operators are literal arguments; command strings are refused. Remote argument quoting uses a fixed POSIX
  supervisor, strict SSH host verification, no PTY and bounded reconnect backoff. Linux process groups are cleaned
  up when the source ends or the UI closes. Missing commands/read permissions do not expose arguments or raw logs.
- Join-ping parsing retains only bounded player IDs and integer milliseconds in memory, up to 5,000 records per
  station. It neither persists raw logs nor displays source addresses. It is a last observed join-time sample,
  not live latency, loss telemetry or a moderation signal. No matching record displays unknown (`—`).
- The new subprocess path is only for an operator-configured direct or SSH station. URL connections, including
  `fleet://`, never start it. fleet-console gets no Docker socket, logs, SSH credentials or extra privileges, and cached
  RCON response latency is never presented as player ping. Existing customer automatic discovery does not enable
  log readers; manual configuration and existing read permission are required.
- Adapted upstream's visible-card optimization to IBIGS sorted/filtered stable station identities. Added proper
  card type, index and RCON callback registration for dynamically discovered lobbies. Sorting/filtering still
  cannot silently retarget commands, and new station discovery performs no game mutation.
- Preserved the managed Forge projection, target checks, credential redaction, download validation, F6/F9 modes,
  withdrawn-release tracking, tagged broadcasts and player directory. Binary self-updates remain disabled; client
  updates continue through the separately maintained IBIGS signed publication channel.

Focused validation uses offline RCON/controller fixtures, a local log helper, a local POSIX supervisor and temporary
files. It covers literal command arguments, rejected shell strings, managed-mode process exclusion, cleanup of log
helper descendants, log-source exit, dynamic discovery, stable filtered viewport identities, and F3 then Help at
80×32. All 24 focused checks passed with Python 3.12 on 2026-10-09. No production broadcasts, moderation, game loads, stops or restarts are source tests. Final combined-suite
and image canary results are recorded separately during release promotion.

The customer-only F7 panel uses an argument array to the existing local `oni` wrapper. It requires explicit occupied-lobby confirmation before passing `--force`; an earlier empty snapshot cannot bypass the helper’s final occupancy refusal. Narrow-terminal layout and this join-during-confirmation path have separate offline regression coverage. It does not grant the fleet-console fleet adapter Docker or shell access.

## Historical source review — upstream 0.6.0

Reviewed upstream range: `8703bde` (0.4.0) through `e5772fe56a558f164f6a55d270795d8bc8104718` (0.6.0), including 0.5.0 `a8e7a21539e219baf6563ceb599142e96b6cabc3`. Primary release notes: [0.5.0](https://github.com/viik2k/oni-rcon/releases/tag/v0.5.0), [0.6.0](https://github.com/viik2k/oni-rcon/releases/tag/v0.6.0). Maintained source version: `0.6.0+ibigs.1`. This is a source review, not a formal audit or verification of upstream executables.

The reviewed changes add the Superintendent sidebar, shutdown-event race guards, Forge thumbs-up/down votes, author lists, curated favourites, download-history context, release notes, and release-specific withdrawal handling. No new runtime dependencies, executable downloads, subprocess paths, telemetry destinations, or runtime permissions were introduced by this range. Runtime dependency pins and Docker base remain unchanged. The historical dependency audit below has not been represented as a new scan.

IBIGS integration decisions and checks:

- Kept upstream executable self-updates disabled. Customer release publication and verified installation belong to the separate IBIGS channel, not to Forge or GitHub update checks inside the console.
- Retained owner-only atomic credential files, shell-string credential command rejection, strict SSH host verification, bounded RCON transport, key redaction, and terminal control-character cleaning. Forge presentation now additionally strips bidirectional overrides from author/title/description/release-note fields.
- Retained pinned manifest identity, size and SHA-256 checks, approved Forge/CDN origins, no credential forwarding on redirects, content-root containment, and content-addressed installation paths. Withdrawn or draft manifests are refused. Known listing/release withdrawals cannot be bypassed with a stale catalog or the install hotkey.
- Preserved live-schema aliases (`owner_username`, `last_7d`, asset fields) and sequence ordering. Upstream's single withdrawn-version record was extended compatibly so separate customer lobbies on several withdrawn releases remain correctly flagged. A warning never removes files, loads content, or restarts a game.
- fleet-console continues to read only `/api/forge` through the private controller, without a Forge key or direct downloads. Curated favourites are read-only, locally time-filtered, and explicitly unavailable when the cache lacks that feed. Withdrawn entries remain retrievable for details but do not appear as active catalog recommendations. Vote sorting does not use the deprecated star rating.
- Fleet search, tags, player lookup, target identity, F6 guarded lifecycle controls, F9 catalog, and customer F6 remain intact. Cached fleet counts do not imply native-event or round-recording coverage. The Superintendent explicitly says that the native event feed is not connected on fleet-console.
- Production mutations were not used for source tests. Tests use temporary files, offline Forge/RCON fixtures and a mock controller. Deployment still requires the normal unprivileged read-only image canary and launcher promotion.

## Historical baseline review — 2026-10-08

Reviewed source commit: `8f888563e917567c7f0d36789bbbbd0a147222d8` from https://github.com/viik2k/oni-rcon. Scope: application entry point, configuration/password resolution and persistence, RCON and SSH transport, UI command/event handling, updater, packaging workflow, and pinned runtime dependencies. This was a source and dependency review, not a formal security audit or verification of the upstream release binaries.

No obvious malware, obfuscation, telemetry collector, or credential-exfiltration destination was found in the reviewed application source. Outbound connections implement the configured RCON/SSH destinations and GitHub update checks. UI moderation and arbitrary RCON commands are intentional administrator capabilities.

Changes made for the maintained build:

- Removed automatic upstream executable replacement and network update checks. Upstream accepted a missing release digest and trusted release metadata from the same channel as the executable; neither provides an independent signature.
- Disabled shell strings for password commands. Explicit argument arrays remain available in standalone mode; the fleet adapter uses neither facility.
- Made standalone credential-file writes atomic and owner-only from creation, avoiding a write-before-chmod interval.
- Bounded WebSocket messages to 1 MiB and required the expected authentication message type.
- Rejected SSH option-like destinations and required known host verification in standalone tunnels.
- Kept all game credentials in the management layer. The fleet token is confined to the loopback API; redirects and proxy environment variables cannot forward it elsewhere.
- Added native read allowlists/limits and authentication-failure suppression. Existing write operations keep target-generation validation and an audit trail.
- Blocked force lifecycle operations and raw fleet-wide commands in the terminal. Broadcasts use a single explicit batch; game-changing commands ask the operator to confirm.
- Fixed feed wrapping while its tab is hidden. The complete maintained terminal test suite passes.

The runtime lock contains Textual 8.2.8, websockets 17.2, Rich 15.0.0 and their pinned transitive dependencies. `pip-audit` reported no known vulnerabilities for that lock on the review date. This does not prove the absence of undiscovered vulnerabilities.

Live validation used reads only. Native chat/kill streaming remains pending in fleet mode; the current adapter should not be represented as a complete replacement for the upstream event stream.

The 0.9.0+ibigs.4 sidebar change adds no network destinations, dependencies or privileges. It includes the concurrently reviewed client public-identity default from ibigs.3; fleet-console uses a neutral display label while its authenticated controller identity remains available in private audit records. Native event coverage is unchanged.
