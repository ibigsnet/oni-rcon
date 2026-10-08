# Source review — 2026-10-08

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
