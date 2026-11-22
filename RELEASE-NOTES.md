# ONI RCON desktop 1.15.0

Three Reclaimer server settings you can now change from the console: restore state, sandbox and networking.

## New

- **Restore state.** F3 › SERVER › RESTORE STATE turns `restore_state` on or off for a Reclaimer 0.10.3+ server, so a restart plays on from the game it saved instead of starting fresh. Applied at once. ([#64](https://github.com/viik2k/oni-rcon/pull/64))
- **Sandbox.** F3 › SERVER › SANDBOX plays a server's maps with Halo 3's or Halo: Reach's weapons and vehicles instead of the map's own (a Battle Rifle becomes a DMR, a Hornet a Falcon). Needs server 0.10.5+, and players need that game installed. ([#65](https://github.com/viik2k/oni-rcon/pull/65))
- **Networking.** F3 › SERVER › NETWORKING switches between Enhanced (the default) and Halo 3's classic networking. Needs server 0.10.4+. ([#65](https://github.com/viik2k/oni-rcon/pull/65))

## Under the hood

- In-range dependency updates (Tauri 2.12.7, npm, cargo, uv).

## Upgrading

Desktop installs update in place from the app. Config and plug-ins work unchanged.
