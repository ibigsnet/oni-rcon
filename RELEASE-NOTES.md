# ONI RCON desktop 1.10.0

One-click plug-ins from the oni-rcon index, update notices while the app runs, and a lighter console.

## New

- **Plug-in index.** ADD A PLUG-IN lists the plug-ins from the oni-rcon index. INSTALL… on one downloads it, then asks the usual download and approval questions. The list is cached, so it still shows offline. Pasting a git URL still works for any other plug-in.
- **Update notices.** The app checks for a new release when it starts and every 6 hours. When there is one, you get a toast and Shift+U in the footer. Shift+U shows the release notes and installs the update.
- **What's new.** The first start after an update shows that version's notes once.
- **Camera (Project Reclaimer 0.9.20).** Host setup offers Camera. CAMERA on F3 switches a running 0.9.20+ server between first person, third person and letting players choose. The change applies from the next game.
- **ODST playlists (0.9.19).** Host setup and SWITCH PLAYLIST also clear a server's `odst` table.

## Fixes

- The console no longer re-renders everything once a second, so it uses less CPU, most of all with many servers.
- A plug-in download that times out no longer leaves a half-written folder that blocks every retry.
- The Plug-ins tab keeps showing its getting-started steps while Automation Studio is the only plug-in.

## Upgrading

Desktop installs update in place from the app. Config and plug-ins work unchanged. `ONI_RCON_NO_UPDATE=1` turns off update checks and the plug-in index download.
