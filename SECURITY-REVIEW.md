# Source review — 2026-10-09, upstream 0.6.0

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
