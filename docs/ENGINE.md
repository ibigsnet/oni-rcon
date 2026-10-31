# Plug-in engine: where it stands and what to build on it

Companion to [PLUGINS.md](PLUGINS.md) (the protocol) and [PLUGINS-BUILD.md](PLUGINS-BUILD.md) (the build brief). This file
says what the engine in desktop 1.8.0 can and can't do, the small changes that would unlock more, and plug-ins worth writing.

## What the engine does today

- **Service mode**: Oni runs `exec` (locally, or over ssh with `host`) once per request: one JSON line in, one JSON
  object out. Timeouts are 10 s for `view` and 30 s for `action`.
- **Session mode** ([session.rs](../desktop/src-tauri/src/session.rs)): one process per plug-in for the console's life,
  restarted with backoff. It gets `hello`, the events it asked for and the replies to its RCON, and may push views,
  toasts, log lines and RCON at any time. F10 shows whether it's running.
- **Blocks:** `kv`, `table`, `text`, `log`, and `html` (desktop only). A view with an html block shows only its html
  blocks in the desktop app; the other blocks stay as the terminal console's fallback.
- **Pages** run in a sandboxed frame (opaque origin, no network, no storage, no forms). They get `data`, the server
  name, the redaction flag, the theme colours, the events their manifest asks for and their actions' RCON replies, and
  can post back `action` (one of the view's own), `refresh`, `height`, `ready`, `rcon` (answered with the reply) and
  `call` (a request to their own `exec`, answered with its reply).
- **RCON** goes through an action's `rcon` reply, a session's `rcon` line or a page's `rcon` message, all filtered by
  `permissions.rcon` and the config's server names. What the server answered goes back: to a session at once, to a page
  at once, and to a service plug-in in its next view request (`replies`).
- **Badges** of `warn` and `alert` raise CONDITION to AMBER and RED. Views refresh on open, on Ctrl+R, after an action
  and every 30 s while F10 is open; a plug-in with a badge is polled every 60 s while the console is open.

## Limits that shape what a plug-in can be

1. **No history from Oni.** Requests carry the selected server's snapshot, and a session (or a page) gets events as they
   happen, but nothing from before it started: for that a plug-in reads its own (logs over ssh, an API, a file).
2. **The page can't fetch or remember** by itself: it asks its `exec` process with `call`, which has both.
3. **A page unloads when F10 closes** unless its html block has `"persist": true`, which keeps it running out of sight
   ([Plugins.tsx](../desktop/src/components/Plugins.tsx)). Its `data` only changes when a view is asked for, which is
   while F10 is open (or every 60 s for a plug-in with a badge).
4. **No rate limits.** A session or page can send as much RCON, and push as many views, as it likes; each command is
   logged. Only the commands in `permissions.rcon` ever go out.
5. **Session processes on Windows**: Oni stops one by closing stdin and killing the shell; a process the plug-in starts
   itself outlives that unless it exits on stdin closing.

## Engine additions, smallest first

| Change | Unlocks | Size |
|---|---|---|
| ~~**Snapshot in the view request**~~ Done (desktop): `snapshot` in view and action requests, addresses blanked unless granted | Team balancer, AFK list, live stats, per-player tools without log parsing | — |
| ~~**`"persist": true` on an html block**~~ Done (desktop): the frame stays mounted, hidden, while F10 is closed | Countdowns, scheduled broadcasts, timers in the page | — |
| ~~**RCON replies**~~ Done (desktop): to the page at once, in the next view request (`replies`), to a session as `rcon-reply` | Plug-ins that query (`status`, ban checks) and react | — |
| ~~**Session mode**~~ Done (desktop): supervision with backoff, the event filter, pushed views, toasts and log lines | Rules engine, join MOTD, AFK kicker, recorders | — |
| ~~**Page RCON, calls and events**~~ Done (desktop): `rcon`, `call` and `event` messages | Live pages: a broadcast board that sends directly, a kill ticker, a page that reads its own files | — |
| **Automation rules call plug-in actions** (F9) | Scheduled or triggered plug-in work without a session | Medium |
| **Session plug-ins in the terminal console** | The same plug-ins in both front-ends | Large |

## Plug-ins to build

### Now: one script, no data source

| Plug-in | Page | Permissions |
|---|---|---|
| **Broadcast board** | A grid of buttons from `messages.toml` ("Rules", "Join the Discord", "Restart in 5"); one click sends it | `rcon: say` |
| **Quick map launcher** | Map tiles with embedded thumbnails (about 24 small JPEGs fit the 512 KiB cap) and a mode pick; a click loads it | the map-load command |
| **Admin handover log** | A text box and timestamped notes, kept in a file next to the plug-in | none |
| **Callout maps** | A static reference page: Halo 3 maps as SVG with callout names | none |
| **Discord announce** | Type once: posted to a Discord webhook from `exec` and sent in-game | `rcon: say` |
| **Event countdown** | Reads `events.toml`, counts down to the next scrim or event, with ANNOUNCE | `rcon: say` |

### Soon: reads the game box over ssh

| Plug-in | Source | Notes |
|---|---|---|
| **Log search** | grep over the dedicated server logs | Text param, table of matches; row action kick or ban |
| **Report queue** | `!report` / `!admin` lines in the chat log | Badge `warn` while any are open |
| **Population chart** | join and leave lines | html chart of players by hour and weekday |
| **Stats ladder** | kill lines | K/D and medals per player, season reset |
| **Box panel** | `df`, `uptime`, `systemctl status`, `docker ps` | Disk, CPU and services; restart-service action (danger). Health covers memory and OOM |
| **Config snapshots** | `dedicated.toml`, playlists, ban list | Diff since the last snapshot; restore behind a confirm |
| **Uptime Kuma** | Kuma API from `exec` | Uptime bar per server; badge `alert` when one is down |
| **Forge watch** | ReclaimerForge API | New versions of installed maps, new uploads from followed authors; badge `warn` |

### Now possible with the snapshot, session mode and replies

- **Team balancer** and **AFK list** (snapshot).
- **Rules engine**, **AFK kicker** (session mode). **Join MOTD**: [greeter](../examples/plugins/greeter) is a start.
- **Ban check** (replies): look a player up with RCON and show what the server said.

Gryphon (moderation) and Loadout (Megalo deploys) already have plug-ins.
