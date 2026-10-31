## IBIGS fork: current release 0.9.0+ibigs.29 (30 October 2026)

This is IBIGS's public fork of [viik2k/oni-rcon](https://github.com/viik2k/oni-rcon) 0.9.0 (GPL-3.0), the console
behind the IBIGS fleet and every IBIGS customer server. `main` is the maintained source. Customer servers don't
pull from GitHub: they get signed releases through IBIGS's update channel (**F9** → update now, or the nightly run).

### Latest releases

- **0.9.0+ibigs.29** (30 Oct): brings in upstream oni-rcon (desktop 1.10.0 main + staging): **F4** kicked players with
  PARDON, the admins list (add/remove, MAKE ADMIN) and VPN lists; a note on a card when its server is behind the latest
  Project Reclaimer release. **F3** now leads with **SHARE / JOIN**, the address players join at (`c` copies it), and
  the console's own RCON line reads **CONSOLE LINK**. Plug-ins are not shown or run on customer consoles. The fleet
  view no longer stops with an error when a server row has no image.
- **0.9.0+ibigs.28** (17 Oct): when oni runs on the same machine as the server, it signs in with the server's host
  key and uses the spot the server keeps for its own machine, so it is never turned away when 4 tools are already
  connected. A full console now reads **CONSOLE FULL** and oni keeps retrying, instead of looking like a wrong password.
- **0.9.0+ibigs.27** (17 Oct): every ReclaimerForge download, Workshop item and upload is checked to be Halo 3
  content before it is installed (`h3check`); a release that fails goes on the IBIGS hold list and can't be installed
  anywhere until it is reviewed.
- **0.9.0+ibigs.26** (16 Oct): fuzzy search in every search box ("gluch" finds Gulch); **F9 → Check & fix**
  (`oni doctor`) finds and repairs common setup problems.
- **0.9.0+ibigs.25** (16 Oct): big Workshop mods install again (no 30-second cut-off).
- **0.9.0+ibigs.24** (15 Oct): one-click Forge deploy, live status words, one boot animation of your choice.

Full release notes, newest first: [IBIGS.md](IBIGS.md).

### What the IBIGS fork adds

**END ROUND / END MATCH** are available directly above the player roster, with more controls under **F3**.
Confirmation names the target server, map, mode and player count. End match keeps the server process running.

**Clickable findings and notifications** open the relevant server and recheck the reported player's full ID
against a fresh roster. The reporter and subject are separate links. Missing or ambiguous identities leave
no player selected; opening a report never performs moderation.

**Conversation context:** review findings retain up to 20 player messages from the preceding two minutes,
mark the trigger, then collect up to 20 follow-up messages over two minutes. F11 updates the same finding
without another alert; **EXPAND** gives the conversation the full evidence panel. Timestamps, channels and
clickable speakers help identify what a request refers to. Pronouns such as “him” never guess a player.
Feed gaps are marked; round changes preserve chat. Only observed messages are available, with the existing
one-hour / 200-finding history limit. Busy fleet responses prioritize newer conversations.

**Quieter Superintendent:** Infection, Fat Kid and SWAT suppress kill-count highlights and suspicion flags.
Other modes limit highlights to one per player per five minutes and one per server per minute. Explicit
help requests, requests to kick/ban/mute, and player allegations produce review alerts with the original chat.
Game-mod discussion, command tutorials and denials are excluded. Repeat chat topics are suppressed for two
minutes across speakers, with a one-minute per-speaker/category limit that survives round changes. This is
English-language triage, not a guarantee that every request is detected. Native anti-cheat reports and
repeated kick votes remain eligible in every mode.

**Ctrl+G** opens fleet player search. Exact and substring matches rank first; small name typos and adjacent
transpositions are supported and labelled. Structured filters remain non-fuzzy, stale rosters are excluded,
duplicate names retain their identities, and opening a result checks the full player ID.

**CLEAR ALL ALERTS / Ctrl+L** acknowledges every server in the open console, including filtered servers.
Current health conditions and report history remain. **F11** or the Superintendent opens recent findings:
repeated kick votes, reciprocal vote fights, kill bursts, explicit native medals and reports requiring review.
Suspicion never triggers automatic moderation. Perfection requires an explicit native medal event.
Customer findings observe the connected event feed while the console is open. The fleet console reads findings from
independent private node observers; it does not stream the full native feed into F2.

Customer VMs launch `oni`, which discovers only their own local lobbies. **F6** opens ReclaimerForge;
provisioning supplies a private customer catalog/download credential, and personal overrides are preserved.
**F7** controls lobbies, **F8** is AUTOMATION (rules such as spree announcers, `!admin` calls and server-down alerts; schedules; ADMIN/VIP/MOD roles; the votes switch; Discord; see [docs/AUTOMATION.md](docs/AUTOMATION.md)), and **F9** checks updates and manages automatic
updates. New-client images enable Oni and guarded game updates by default; existing choices are preserved.
Verified Forge downloads are staged with content hashes, without silently loading a map or restarting a game.
In the fleet console, **F9** browses the controller's shared Forge catalog and **F6** retains guarded container operations.
**F10** reports health. The fixed sidebar and bounded polling preserve the selected command target.

The IBIGS deployment target is Linux: customer scheduling uses POSIX file locks and the VPS system service,
and the fleet console runs the terminal in a Linux container. CI runs the complete suite on Linux with Python 3.11, 3.12
and 3.13. Native Windows hosting helpers are not supported by this maintained fork.

API mappings were checked against ReclaimerForge's real catalog, manifest and OpenAPI schema. Credentials stay on
the API origin, asset fetches are restricted to Forge/its CDN, paths cannot escape the content root via a symlink,
and server/catalog terminal control characters are removed. Shell-string password/key commands stay disabled.
Self-updating binaries remain disabled: reviewed, pinned IBIGS source is packaged with its license and source.

> **IBIGS maintained copy:** On the fleet console host, run `oni-rcon`. See [IBIGS setup and limitations](IBIGS.md) and [source review](SECURITY-REVIEW.md). Startup update checks are disabled.

# oni-rcon

![oni-rcon boot screen: the ONI emblem over the clearance check](docs/boot.png)

**An ONI-themed terminal admin console for Project Reclaimer Halo 3 dedicated servers.**
Every server you run on one screen: players, live intercepts, kicks and bans, map and mode control, votes, VPN
allowances and a raw command console, wrapped in Office of Naval Intelligence, Section Three dossier styling.

![Assets tab: players table and personnel dossier](docs/assets.png)

On the fleet console host, run `oni-rcon`. From a clone: `uv sync`, then `uv run oni-rcon`. A direct connection opens a setup screen:
type the server address and RCON password, and it tests them. **Try the demo** looks around three simulated servers.
The desktop app in `desktop/` (Tauri) comes from upstream [viik2k/oni-rcon](https://github.com/viik2k/oni-rcon) and is kept in step with it; IBIGS builds and supports the terminal console.

**No server yet?** In the [desktop app](desktop/README.md), **HOST ONE HERE** on the setup screen asks for a name and a
join password, downloads Project Reclaimer's dedicated server, sets it up from your Steam copy of MCC, turns its remote
console on and connects.
**Servers on a game box already?** **FIND SERVERS ON IT** finds them over SSH and adds them.
[Hosting and connecting](docs/hosting.md) · [watch the hosting demo](docs/hosting-demo.mp4).

**Desktop app.** [`desktop/`](desktop/README.md) is the same console rebuilt as a native window (Rust and React, on
Tauri): the same config, keys and look, with the emblem, the archive and the Superintendent drawn at four times the
resolution, and lighter on a big fleet. Installers are on the [releases page](https://github.com/viik2k/oni-rcon/releases/latest).

## What it does

| Tab | |
|---|---|
| **F1 Assets** | Live player table per server: team, score, K/D, health and shield bars, admin, dead and spree flags, and the score race between the teams along the bottom. The dossier shows the selected player's file, a biometric glyph drawn from their player ID, the medals they've earned and the raw JSON. `t` tell · `k` kick · `b` ban · `m` mute · `j` team · `v` VPN allow · `p` make admin · `y` copy ID |
| **F2 Intercepts** | The selected server's feed (tick ALL SERVERS, or press F2 again, for every server): chat (team and server), kills with weapon, joins, leaves, kicks, bans, votes and game phases. Kills carry their Halo 3 medals: double kill through killionaire, killing spree through invincible, and killjoy. Filter by chat, combat, traffic, moderation or ops; scroll up to read back and it holds still. Chat that mentions admins or cheating raises a toast and the terminal bell |
| **F3 Operations** | Sitrep for the selected server with the vote under way and its tally, and the title and author of the map or gametype playing when it came from ReclaimerForge; the theatre, with each team's numbers and score as bars, the top guns and who's on a spree; the playlist rotation with the current map marked; and buttons for load map + mode, change map or mode, queue next (each opens the content browser: stock maps, mods, ReclaimerForge downloads by folder, your content folder, Halo 3 and Matchmaking variants, saved and Workshop variants, and ReclaimerForge listings not installed yet, which install first; type to filter, click NAME, INSTALLED, DOWNLOADS or RATING to order and again to reverse), playlists (with the IBIGS `oni` helper), end round or game, shuffle, team count, call, pass or cancel a vote, broadcast, rename, join password and ping limit |
| **F4 Blacklist** | Bans by player, IP and device; new ban (timed or permanent), unban, VPN allow and revoke, check an IP (on the desktop with server 0.9.22 or later, which list it matched). On the desktop with server 0.9.22 or later, each VPN list's size, age and last fetch problem, flagged when older than a week or failing, and whether the server refuses everyone while no list is loaded (fail-closed). The admins, with who's on and where each was made: add or remove one at once on every server sharing the ban list (server 0.9.16 or later), or, on the desktop, of one server only through its `dedicated.toml` (from the next restart). Who was kicked and how long they're still out: pardon one so they can rejoin now (server 0.9.16 or later). `p` add admin · `d` remove admin · `o` pardon |
| **F5 Console** | Type any RCON command (`help` lists them) with ↑/↓ history. The command log records everything sent from this session and its replies. The message in `say`, `tell`, `kick` and `servername` is the rest of the line as typed, sent as one argument, so it needs no quotes |
| **F6 Forge** | The [ReclaimerForge](https://www.reclaimerforge.net) catalog of community maps, gametypes and playlists, read with your own API key. Order it by trending, rising, latest, updated, downloads, rated, unrated or overlooked (trending and rising over the last 24 hours, 7 days or 30 days), or see the collections ReclaimerForge is featuring now, and search it. Each listing shows its type, authors, thumbs up and down, recent downloads, and whether it runs on the selected server's version; its file lists every version, and any of them installs on the selected server, every file checked against Forge's manifest. Click a column's name to order by it, again to reverse, a third time for Forge's order. `i` install on this server · `l` load it now (a playlist: choose lobbies) · `s` sort · `w` window · `o` order by the next column · `r` reverse · `/` search · `n` more · `k` key · `y` copy ID |
| **F10 Health** | Whether the servers are crashing and the box is coping. CRASHES lists each server's latest crashes read from its log (time, server, class, how many players were on, whether the RCON connection dropped with it); FRAME RATE shows each server's frames a second from the server's own "falling behind" log lines (now, 5-minute average, worst, longest frame, players), what's limiting it (BOTTLENECK) and a 30-minute frame-time chart with plain-words advice; see [Is my server lagging?](docs/SERVER-PERFORMANCE.md); HOST shows free memory, swap, the kernel's `oom_kill` counter and each container's start time and OOMKilled flag; WORKAROUND shows whether an optional systemd service is running. A new crash, an OOM kill or memory falling under your limit flashes a banner over every tab, goes in the feed and beeps. Needs `health_cmd` in the config: [see below](#health-watch) |
| **F8 Plug-ins** | Panels drawn by programs you add, such as Gryphon's moderation log: each one's status, tables and buttons, and a badge that can turn CONDITION amber or red. Off until you name one in the config and approve it: [see below](#plug-ins) |

Also:

- **No config file needed.** The setup screen adds servers for you and tests each one first. **+ ADD SERVER** in the
  sidebar brings it back later.
- **Point and click.** Every action has a button, every button explains itself when the mouse rests on it, and `?`
  opens a plain-words guide to the whole console.
- **Errors you can act on.** A server that won't connect says why ("Nothing answered on that port", "SSH refused your
  key") and counts down to its next try.
- **Many servers at once.** The sidebar holds one card per server, and `1`–`9` jumps between them; `g` lists every
  server, the busiest first, to type a name into. Each card shows how full the server is and a trace of its activity
  over the last 100 seconds. Names that share a community tag ("ALPHA · Big Team") are shortened to what tells them
  apart, with the tag on the card's second line.
- **Version drift.** A server lets in only players on exactly its version, and players' games update themselves, so a
  server behind the newest Project Reclaimer release says so on its card and in F3 (`▲ 0.9.18 out` in the TUI), and
  the feed says `MIXED VERSIONS` when your servers disagree. Checked hourly; `ONI_RCON_NO_UPDATE=1` turns it off.
- **Built for fleets.** Past 12 servers the cards slim to two lines, the boot screen tallies the stations instead of
  listing each one, and the console stays light enough for a web terminal: a card is redrawn only when what it shows
  changes, status polls are spread over 15 seconds instead of fired together, and servers sign in 8 at a time with
  jittered reconnects.
- **An alert condition.** The masthead reads CONDITION GREEN; AMBER while a station is down, the host is short of memory
  or something you installed from Forge has been withdrawn; RED when a call for an admin, an anti-cheat hit, a crash or
  an OOM kill comes in. A server you aren't looking at pulses red and keeps a ⚑ count until you open it
  or the Intercepts tab.
- **The Superintendent.** A small green face at the bottom of the sidebar, on every tab, that watches the feed of every
  server and reacts as an admin would: it welcomes a join, startles at a call for an admin, scowls at a cheat flag,
  approves a kick or a ban, is impressed by a medal, is unimpressed by a mute and winks at the server's own messages.
  The word under it says how it feels, and the words beside it say what it reacted to, with addresses redacted like
  everywhere else. It blinks now and then. It takes a moment to settle into each mood and then relaxes, a busy fleet
  doesn't make it flicker, and an alert pulses it red until it's over. It is drawn the way the emblem is, from the
  Halo ASCII archive piece, and it never goes away: it grows up to 40 pixels across when the terminal is tall enough (the words go
  under the larger faces), shrinks to 16, 12 and then 8 as it gets short, and the station list scrolls to make room before the Superintendent or
  the operator line does. With `TEXTUAL_ANIMATIONS=none` it changes expression at once and never blinks.
- **The Halo ASCII Archive.** The first time you start it the boot plays the whole archive from the design project:
  the ONI emblem, Section Three's lattice, Installation 04 turning, 343 Guilty Spark and the Superintendent decrypt one
  file at a time (about 30 seconds, any key skips it), then the emblem returns for the clearance check. After that
  every start is the quick boot, the emblem and the clearance check, so you're in at once. **Ctrl+P, "Boot sequence"**
  switches to playing the full archive every time (handy if you leave the screen up); `--intro full|quick|off` decides
  it for one run. It's kept in `prefs.json` next to the config file, and with `TEXTUAL_ANIMATIONS=none` the archive
  never plays.
- **Broadcast to one server or all.** `Ctrl+B`. `@all` messages and commands go out 4 servers at a time, each reply
  goes in the command log on one line, and a tally closes it ("79 stations · 77 ok · 2 no reply yet"). A command that
  gets no reply in 10 seconds is never resent, and if its reply comes later the log shows it.
- **Command palette.** `Ctrl+P`.
- **Find a player.** `Ctrl+F` searches every server's players at once: a name (a typo of four letters or more still finds
  them), `@tag`, `clan:`, `team:`, `id:`, `server:`, `node:` or `port:`, all of which must match the same player.
  Enter opens their server with them selected. (From ibigs/oni-rcon, by IBIGS.)
- **Superintendent findings.** `F8`, or a click on the Superintendent, lists what it noticed in the last hour, with the
  evidence behind each: chat asking for an admin or reporting a player (with up to 20 messages either side, two minutes
  before and after), repeated or back-and-forth kick votes, a kill burst, an unusual kill rate, a cheat flag the server
  reported and an explicit Perfection medal. They are observations for you to review, never grounds for moderation by
  themselves: nothing is kicked, banned or muted for you, and kill heuristics stand down in Infection and SWAT-style
  modes. A finding that needs review turns the CONDITION red, pops up and chimes; CLEAR ALL acknowledges them. The
  chat is matched by explicit requests ("need an admin", "please kick Red", "Red is cheating"), not by words like "mod"
  or "cheat" alone. (From ibigs/oni-rcon, by IBIGS.)
- **Content browser.** CHANGE MAP, CHANGE MODE, LOAD and QUEUE NEXT list what the server can load in groups (stock maps and
  game types, mods, ReclaimerForge downloads, your own content folder, the Halo 3 and Matchmaking variants, saved and
  Workshop variants) instead of one long list. Type to filter (every word has to match a name, author, folder or file),
  order by name, install date, downloads or rating (click a button again to turn it round), and a ● marks what is
  playing now. With a ReclaimerForge key loaded, a last group offers maps and game types the server doesn't have yet:
  choosing one asks, then installs it. (From ibigs/oni-rcon, by IBIGS.)
- **Playlists.** PLAYLISTS (F3, desktop) lists the server folder's `playlists`, the ones made here and any other, with
  the playlists installed from F6. NEW and EDIT open an editor: a name, the seconds players get to vote (5 to 120) and
  its games, a map and a game type each, with a weight for how often it comes up. Three games at least, each map and game
  type once, no more than 128, in the shape the dedicated server reads. A playlist this console made is saved over; any
  other is opened as a copy. USE ON THIS SERVER sets `playlist` in `dedicated.toml`. (From ibigs/oni-rcon, by IBIGS.)
- **Automation rules.** AUTOMATION (F9, desktop) holds rules of WHEN something happens, ONLY IF it holds, THEN DO these: say in
  chat, message a player, the admins or a role, cancel a vote, shuffle the teams, post to Discord or Telegram. Build one step
  by step, start from a template, TEST it, and set who counts as an ADMIN, VIP or MOD. Chat from rules is held to 20 lines a
  minute per server, Discord webhook URLs and Telegram bot tokens stay in the app and show hidden, and every run is written to
  the command log. Rules run while the console is open. See [docs/AUTOMATION.md](docs/AUTOMATION.md). (From ibigs/oni-rcon, by
  IBIGS.)
- **Automation Studio.** A built-in F10 plug-in (desktop) for the automations rules can't express: write JavaScript in an
  editor with suggestions, docs on hover, live problems and a command palette. RUN a script for real or TEST it as a dry
  run, watch live events, simulate your own, and keep data that outlives restarts. Scripts run sandboxed, with no
  network, RCON only through the plug-in filter, and chat and Discord rate limits. See [docs/STUDIO.md](docs/STUDIO.md).
- **Player addresses are redacted by default,** in the tables, the feed, the toasts and the console's replies and raw
  events. Press `x` to reveal them. This helps when you stream or share screenshots.
- **SSH tunnels are built in.** One `ssh -L` per host carries every server on it, and it reconnects with backoff.
- **Destructive actions ask first.** The confirm button defaults to ABORT.
- **A refused password is never retried.** The server locks an address out after 5 wrong passwords in 10 minutes, so
  oni-rcon won't trip it. RECONNECT on F3 asks for the password again before it tries.
- **Medals count what the console has seen.** They're worked out from the kill feed, so a spree that started before
  oni-rcon connected isn't known, and a reconnect starts everyone's spree over.

![Health tab: crashes read from the logs, the host's memory and the workaround service](docs/health.png)

<p>
  <img src="docs/intercepts.png" alt="Intercepts tab: one feed of chat, kills, medals and joins from every server" width="49%">
  <img src="docs/operations.png" alt="Operations tab: sitrep, theatre, rotation and server controls" width="49%">
</p>

## Install

On the fleet console host, run `oni-rcon`. The launcher and the fleet limits are in [IBIGS.md](IBIGS.md).

From a clone, with Python 3.11+:

```
uv sync
uv run oni-rcon
```

`uv run oni-rcon --demo` opens three simulated servers. This copy does not check GitHub for a newer binary on startup.

A direct connection needs a Project Reclaimer dedicated server with RCON turned on (tested against 0.9.7). The setup
screen asks for the address and password and tests them before saving.

Use a terminal with true colour and a font that has box-drawing glyphs, such as Windows Terminal, iTerm2, kitty or
WezTerm. At least 140×40 looks best.

## Host a server

This is in the [desktop app](desktop/README.md), on its setup screen.

**HOST ONE HERE**, on Windows with MCC installed from Steam, asks for the server's name, join password, player limit and
whether it's listed or open to the internet, then downloads the newest Project Reclaimer dedicated server
(checked against its published SHA-256), makes a server folder from your game (`reclaimer` on the game's drive), turns
the remote console on with a new random password, starts the servers in a window of their own and adds them. On Linux
it does the same with the official Docker image in `~/reclaimer`.

**FIND SERVERS ON IT**, under *Advanced: reach it through SSH*, looks on the game box you give it for Reclaimer
containers and server folders, tests each remote console and adds the ones that answer. Their password stays on the
box: the config reads it there at each start. With containers it sets up [player ping](#player-ping) and the
[health watch](#health-watch) too.

[docs/hosting.md](docs/hosting.md) has every step, the firewall rules, copying the game files to a Linux box, and what
it can't find yet. The servers it adds are in the shared config file, so the terminal console connects to them too.

## Turn on RCON on the server

You can skip this if HOST ONE HERE or FIND SERVERS ON IT did it for you.

In `dedicated.toml`:

```toml
[rcon]
password = "a long random string"   # off while empty; 8 characters at least
address = "127.0.0.1"               # keep it: RCON is not encrypted
```

With the Docker image, set `RECLAIMER_DEDICATED_RCON_PASSWORD` in the container's environment instead, and keep the
real value in an `.env` that never goes into Git. Each server listens on its own **game port** over TCP. To change
that, set `rcon_port` (and optionally `rcon_password`) in that server's `[[server]]` block. Restart the server.

## Connect

The easy way is the setup screen: it opens by itself the first time, from **+ ADD SERVER** in the sidebar, or with
`oni-rcon --setup`. It adds each server to your config file (below), keeps any comments and settings already there,
and saves the password only if you leave **Remember the password** ticked.

![Setup screen: address, port and RCON password, tested before it's saved](docs/setup.png)

From the command line:

```
oni-rcon 49176                                # a server on this machine
oni-rcon 49176 49177 49178                    # several at once
oni-rcon --ssh admin@game-box 49176 49177     # through an SSH tunnel to the game box
oni-rcon wss://rcon.example.org/slayer        # behind a TLS reverse proxy
```

Each target is `PORT`, `HOST:PORT`, `[IPv6]:PORT` or a `ws://` / `wss://` URL. With `--ssh`, `HOST` is resolved on
the SSH destination, so the default `127.0.0.1` means "the game box itself". The tunnel needs key authentication
(`BatchMode=yes`): load your key into an agent or set it in `~/.ssh/config`.

oni-rcon finds the password in this order:

1. the config file's `password`, `password_env` or `password_command`
2. `$ONI_RCON_PASSWORD`
3. a single prompt shared by every server

The name the server's admin log records you under is `--by`, then the config's `by`, then your login name.

### Config file

With no targets on the command line, oni-rcon reads the first of these that exists:

1. `$ONI_RCON_CONFIG`
2. `./oni-rcon.toml`, which is the exe's own folder when you double-click it
3. `%APPDATA%\oni-rcon\config.toml` on Windows, or `~/.config/oni-rcon/config.toml` on Linux and macOS

Start from [`oni-rcon.example.toml`](oni-rcon.example.toml). A typical host setup tunnels to the game box and reads
the password from the server's own `.env` over SSH, so the password never sits on your machine:

```toml
by = "your-name"

[defaults]
ssh = "admin@game-box"
password_command = ["ssh", "-o", "BatchMode=yes", "admin@game-box",
                    "sed -n 's/^RECLAIMER_DEDICATED_RCON_PASSWORD=//p' ~/reclaimer/.env"]

[[server]]
port = 49176
[[server]]
port = 49177
```

### Restarting a server

RESTART SERVER on F3 (desktop app) runs the server's `restart_cmd` where the server is: on its `ssh` host, or on
this machine without one. The setup screen writes it for the servers it finds or hosts, and a server on this Windows
machine that its server folder lists gets one without any config; for others, add it to the
`[[server]]` block (or `[defaults]`; `{port}` is the server's RCON port):

```toml
restart_cmd = "docker restart my-server-{port}"
```

Everyone on it is dropped and the console reconnects once it's back. PLAYER CAPACITY (F3, desktop) saves
`max_players` in that server's `dedicated.toml`, found beside its `content_dir`, then offers this restart so it applies. SWITCH PLAYLIST does the same with `playlist`, picked from the
`playlists` folder beside `dedicated.toml` or from the playlists installed from F6, and takes out the server's `map`,
`game`, `variant` or `campaign` (a server plays from one). NEW PLAYLIST (and PLAYLISTS, which also edits them) makes one there from the server's own maps
and modes, three games at least, and offers to switch to it. NEW GAME TYPE saves a game type in the content folder's
`Game Modes`, built on one of Reclaimer's ten base modes with the score, teams, time limit, rounds, lives, respawn
delay and shields you give it (blank keeps the mode's own); once the server lists it, CHANGE MODE plays it. Servers sharing a container (or, on Windows, the
one window HOST ONE HERE started) restart together.

### Player ping

The PLAYERS tab has a PING column (green under 100 ms, amber under 200, red above). RCON has no ping, so it's the
number the server logs when someone joins, the same one `maxping` uses: a join-time reading, not a live one. To fill
it in, give oni-rcon a command that follows the server log, as a top-level key above the first `[table]`:

```toml
ping_log = ["tail", "-F", "/srv/reclaimer/logs/{port}.log"]  # {port} = that server's RCON port
```

Use an **argument array**, not a shell string. A literal `{port}` in each argument is replaced with that server's
RCON port; pipes, redirects, arithmetic, `$()` and `~` are not expanded. For Docker logs, an operator who already
has permission may use `["docker", "logs", "--follow", "--since", "12h", "my-server-{port}"]`. This setting does
not grant Docker or log access. If names require a more complex lookup, write and review a local helper and pass
its path and `{port}` as separate arguments.

It runs on the `ssh` host (or locally when there's none), restarts with bounded backoff when the source exits, and
stops with oni-rcon. SSH log readers require a POSIX host with `setsid` and a previously trusted SSH host key.
Arguments are quoted individually on SSH; closing the terminal cleans up the reader's process group. Only player
IDs and join-time milliseconds are kept in memory. Raw log text and addresses are not displayed or saved.

Without a matching log record the column shows `—`, including players who joined before the available log window.
That means **unknown**, not zero latency. The dossier labels the value JOIN PING; it is not ongoing packet loss or
latency monitoring. WebSocket-URL and fleet connections never start log readers, and RCON connection latency
is never substituted for player ping.

The customer `oni` wrapper's automatically generated lobby list does not enable a log reader. For an optional
operator-managed configuration, run `oni --config /absolute/path/to/oni-rcon.toml` with your own server entries,
credential environment variables and the top-level array above. Keep that configuration private; do not edit the
wrapper's generated list because the next launch replaces it. This source update does not change client permissions.

### Health watch

F10 reads each server's log and the box's memory, so a crash or a memory squeeze shows up where you're already looking.
It needs one thing from you: `health_cmd`, a command that prints a small report for **one** server. Like `ping_log` it
is a top-level key above the first `[table]`, `{port}` is the server's RCON port, and it runs on the server's `ssh`
host (or on this machine without one). It runs once a minute (and soon after a server's RCON connection drops), and it
only reads. `{since}` is how far back to read the log: a day
the first time, a few minutes after.

```toml
health_cmd = ["/usr/local/bin/oni", "health-report", "{port}", "{since}"]
health_min_free_mb = 1024            # alert when memory available falls under this (default 1024)
health_service = "halo-blueflame"    # optional: a systemd service to watch; leave it out and its panel is hidden
```

[`oni-rcon.example.toml`](oni-rcon.example.toml) uses the same installed IBIGS helper, including swap. Standalone operators can supply their own helper as an explicit argument list; implicit shell commands are rejected. Reports are limited to 2 MiB and 30 seconds. The report is plain lines:

| Line | Fields |
|---|---|
| `host ...` | `mem_available_mb`, `swap_used_mb`, `swap_total_mb`, `oom_kill` (the cgroup's `memory.events` counter), and `now` (epoch) with `clock` (`HH:MM:SS`, only needed to date a log line that has just a clock time) |
| `container ...` | `started`, `finished`, `oom_killed`, `exit_code`, `restarts`, as `docker inspect` gives them |
| anything else | a line of the server's log. With `docker logs --timestamps` every line carries its time |

`host` and `container` can also be JSON lines: `{"kind": "host", "mem_available_mb": 812}`. Only the keys above are read.

**What counts as a crash.** Three log lines make one, and the class says which fault it was:

- **SIGNATURE** has the exception line with its code and RVA (`Experimental startup exception 0xC0000005 in halo3.dll at
  RVA 0x14C73E`). That is the known crash of image 0.9.11 under Wine, caused by a player with the blue developer helmet.
- **BARE** has only `Engine probe worker failed: exit code: 1`, no exception line before it. A different fault, listed
  separately.
- **OOM-KILL** is a container the kernel killed for memory: `OOMKilled` true or exit code 137. A rise in the host's
  `oom_kill` counter shows as an OOM-KILL on HOST until a container's own flag says which server it was.
- **CONTAINER-RESTART** is a container whose start time changed with no OOM kill to explain it. The game process
  restarting after a crash is *not* a container restart: the container stays up.

A line the log repeats counts once, and so does a crash that two reports both contain. PLAYERS is the count this
console last saw before the crash. A dropped RCON connection means the server restarted, so a crash within 90 seconds
of one is marked DROPPED and counted from just before the drop. Crashes from before you opened oni-rcon fill the table
but don't alert.

**Memory.** Red under `health_min_free_mb` (that's the alert, and it turns the masthead's CONDITION amber), amber under
twice that. Swap is amber from a quarter used and red from half. `oom_kill` shows how far it has risen since you opened
oni-rcon.

**Frame rate.** The same report carries the server's log, and a server logs every minute it spent under 54 frames a
second. F10's FRAME RATE table and 30-minute FRAME TIME chart, the `38fps` on a lagging server's card and F3's SERVER FPS
are read from those lines, so `health_cmd` has to print the log. [docs/SERVER-PERFORMANCE.md](docs/SERVER-PERFORMANCE.md)
is a plain guide to reading them. (From [ibigsnet/oni-rcon](https://github.com/ibigsnet/oni-rcon), by IBIGS.)

**WORKAROUND** runs `systemctl is-active` and `journalctl` for the named unit (read-only) and shows whether it's active,
the time of its last `moved tag` line and any `NOT the pinned build` line. It needs the ssh user to be allowed to read that
journal; without `health_service` the panel is hidden.

**Player IDs and IPv4 addresses are stripped from every log line the moment it arrives**, and only a time, an exception
code, a module, an RVA and an exit code are kept. Nothing is written to disk. Without `health_cmd` the tab says how
to turn it on.

## ReclaimerForge

[ReclaimerForge](https://www.reclaimerforge.net) is the community catalog of forged maps, gametypes and playlists for
Reclaimer. oni-rcon reads it with **your own API key**: none ships with oni-rcon, and every admin uses their own.
Without one, everything else works as before. `oni-rcon --demo` brings a pretend Forge to try F6 with.

**Get a key** on reclaimerforge.net. Your account needs the Developer role (an Owner assigns it) and a verified
email. Then open Developer tools and create a named key with the `catalog:read` and `assets:download` scopes, and
nothing more. The key is shown once, when you make it, so save it then. Make one for oni-rcon alone, so you can rotate
it without touching anything else that uses a key: create the replacement, load it into oni-rcon, then revoke the old
one. A key stops working when it expires or is revoked, when its account loses the Developer role or its verified
email, or when the account is banned; F6 then says the key was refused. The terminal console never writes, uploads or
publishes anything there.

**Publishing** (desktop app only) takes a key with `listings:write`, `files:upload` and `listings:publish` as well, and
`listings:delete` to withdraw versions and delete listings. On F6, `p` opens a file dialog (`.json`, `.mvar`, `.bin`, up
to 100 MiB each), one form asks for the title, version, who sees it, description, tags and release notes, and the files
are uploaded in chunks and published. If any step fails, the draft it made is taken down again. MINE (`s`) lists your
own listings, drafts included: `u` publishes new files as the next version, `e` edits the title, description, tags and
who sees it, `x` withdraws the selected version (Forge keeps the last one; DELETE LISTING takes the lot), and NOTES
changes a version's label and release notes. Publishing needs a verified email on the account.

**Give it to oni-rcon.** The first of these that's set wins:

1. `forge_api_key_env = "NAME"` in the config file: the key is in that environment variable
2. `forge_api_key_command = [...]`: the first line a command prints, from your password manager say
3. `$ONI_RCON_FORGE_KEY`

Or press `k` on F6 and type it, for this session. Choose **Remember it** there to save it in the config file instead;
that's off unless you pick it, and the file is then readable only by you.

```toml
forge_api_key_env = "RECLAIMERFORGE_KEY"
# or: forge_api_key_command = ["pass", "show", "reclaimerforge/api-key"]
```

These go at the top of the config file, above `[defaults]` and the `[[server]]` blocks: TOML reads a key that comes
after a table as part of that table.

### Browsing the catalog

`s` steps through the orders: trending, rising, latest, updated, downloads, rated, unrated and overlooked, then
**FAVOURITES** and **INSTALLED HERE**. `w` sets the window (24 hours, 7 days or 30 days) for trending and rising, the
two orders it applies to; it greys out for the rest.

- **Rating** is thumbs up and down, as ReclaimerForge now counts them. *Rated* puts the most thumbs up first, then the
  fewest down, and *unrated* is listings nobody has voted on. Forge's old star rating is archived and isn't shown.
- **Authors.** A listing can have several. All are named, the original owner first, and the owner's ID is beside them.
- **Recent downloads** fill from the day Forge began recording them, and older totals aren't backfilled. Where a window
  reaches back past that day the file says so, because the count then covers only part of it. Downloads through an API
  key, oni-rcon's included, don't count toward a listing's popularity.
- **Favourites** are ReclaimerForge's curated collections, in their order and for as long as each one runs (a
  collection starts at its start time and ends at its end time). The file says which collection a listing is in and
  until when. Nothing is featured between collections, and F6 says so.
- **Descriptions and release notes** are Markdown on the website. oni-rcon shows them as plain text, with any
  terminal control characters removed, and never renders them.
- **Compatibility** is whatever the author reported for the version; where the listing doesn't say, it reads `?` and
  stays unknown. Check it on the server you mean to run it on.

### Installing on a server

Pick a listing on F6, pick a version (the newest one that can be installed is picked for you), and press `i`. A
version its author has withdrawn is marked `✕` and is never installed. oni-rcon fetches that version's
manifest, downloads every file and checks each one against the manifest's size and SHA-256 before any of it is used,
copies them into the server's content folder, checks them again there, and only then moves them into place. A file
that fails a check is thrown away and nothing is installed. Replacing anything already there asks first, with ABORT
picked. `◉` in the catalog marks what's installed on the selected server.

Tell oni-rcon where each server loads content from with `content_dir`, in `[defaults]` or a `[[server]]` block:

```toml
[defaults]
ssh = "admin@game-box"
content_dir = "~/reclaimer/content"   # on the game box, for a server reached over ssh
```

- **Behind `--ssh`**, files go to the same SSH destination as the tunnel, with the same key authentication, and need a
  POSIX shell and `sha256sum` on the game box (the Reclaimer docker host has both). With docker, name the host folder
  that's mounted into the container.
- **On this machine** (no `ssh`), it's a plain copy.
- **Reached by a `ws://` or `wss://` link**, a server can't be installed to, and F6 says so.
- Servers that share a `content_dir` share an install: it shows on all of them.

**Loading is a separate step.** Whether a dedicated server picks up new content while it runs is up to the server, so
installing never loads anything. Once the server lists the new map or gametype, `l` on F6 loads it, the same as LOAD
MAP+MODE on F3. If it isn't listed yet, the server needs a restart: RESTART SERVER on F3 in the desktop app. A playlist is
installed like anything else, and SWITCH PLAYLIST on F3 (desktop) puts the server on it. What's installed where is kept in `forge-state.json`, beside the config file.

### Updates and withdrawals

While anything is installed, oni-rcon reads Forge's changes feed every 10 minutes (`forge_poll = 600`, in seconds, at
the top of the config; 60 at least). The feed names the versions each change published and withdrew. It is read back
over a few minutes each time and entries are told apart by their IDs, so a late write isn't missed and nothing is
announced twice. Every 6 hours oni-rcon also fetches each installed listing whole for anything the feed missed. Those
background checks stop well short of the key's quota, so browsing F6 always has room.

- **A new version** gets one toast, says which servers run the old one, and marks the listing `▲` on F6.
- **A withdrawn listing** gets a toast and a line in the feed, is flagged `⚠ WITHDRAWN` in the F3 rotation wherever
  it's in it, and turns the masthead to **CONDITION AMBER** until you acknowledge it: on F6, `s` to INSTALLED HERE,
  pick it, then `a`. It stays installed and flagged; what to do about it is yours to decide.
- **A withdrawn version** is different from a withdrawn listing: its author took one release back and the listing is
  still up. It matters only on servers running that version, which get the same `⚠` and amber until you install
  another version (which clears it) or acknowledge it. The versions left aren't announced as updates when they're
  older than what you have.

### Rounds played

oni-rcon counts the rounds each server plays, per map and gametype, from the status it already polls, and keeps them
in `rounds.jsonl` beside the config file, one line per round: when it started and ended, the peak and final player
counts, the Forge listing it came from if any, and whether the console saw it from the start. Nothing is sent
anywhere. ReclaimerForge plans to take "verified host feedback" one day, and the lines are shaped for that; there's
no endpoint for it yet, so for now they're yours to read. No player names, IDs or addresses go in, and servers are
named as they name themselves, never by address or SSH login. Like medals, it counts only what the console saw.

oni-rcon keeps to the key's quota (120 requests a minute): it reads the rate-limit headers on every reply, waits out a
`429` for as long as Forge asks, and keeps recent replies so it doesn't ask twice. Those replies and every verified
download sit in `%LOCALAPPDATA%\oni-rcon` on Windows or `~/.cache/oni-rcon` elsewhere.

## Plug-ins

A plug-in is a separate program that draws its own panel on F8, in any language: Oni runs it once per request (on
this machine, or over ssh like `health_cmd`), sends one line of JSON and reads one back. Its actions show up as buttons
and in the palette, and it can ask Oni to send RCON commands for it, but only the ones its manifest lists.

Put its folder in `plugins/` next to your config file and name it at the top of the config, above the first `[table]`:

```toml
plugins = ["hello"]
```

The first time, F8 shows what it runs, where, and what it may send, and asks you to approve it; any change to its
`oni-plugin.toml` asks again. [`examples/plugins/hello`](examples/plugins/hello) is a working one in a single Python
file. [docs/PLUGINS.md](docs/PLUGINS.md) is the protocol.

## Security

- **RCON is plain text.** Keep the server's `[rcon] address` on `127.0.0.1` and reach it with `--ssh` (or a VPN). Never
  open the RCON port to the internet.
- **Keep passwords out of files you commit.** Use `password_env` or `password_command`, and never `password`, in a
  config that lives in a repo. `oni-rcon.toml` is in `.gitignore`.
- **F10 only reads.** `health_cmd` is your command, run as you on your host: keep it to `docker logs`, `docker inspect`
  and the like. oni-rcon adds only `systemctl is-active` and `journalctl` for `health_service`. Player IDs and addresses
  in logs are stripped before anything is shown or kept.
- **A plug-in runs as you.** Oni doesn't sandbox it: approve only one you'd run yourself. What it sends is shown as
  plain text, with addresses redacted, and the only RCON it can have sent is what its manifest lists.
- **Mind the tool limit.** A server admits 4 RCON tools at once, and oni-rcon uses one connection per server.
- **Kicks and bans name you.** They go into the server's admin log under your `--by` name.
- **Your Forge key goes to reclaimerforge.net and nowhere else.** It travels in the `Authorization` header over HTTPS:
  never in a URL, and never to a download link on another host. It never shows in the command log, a toast, a raw
  JSON view or an error: anything shaped like a key (`rfk_…`) is blanked on screen, so it can't leak into a stream or
  a screenshot. It's written to the config file only if you tick to remember it, and the file is then owner-only.
  A key that was ever pasted somewhere public (a chat, an issue, a screenshot) should be revoked and replaced.

## Keys

| Key | Action |
|---|---|
| `F1`–`F6`, `F10` | Assets · Intercepts · Operations · Blacklist · Console · Forge · Health |
| `F8` | Plug-ins (IBIGS customer and fleet consoles use F8 for AUTOMATION / filter; the tab is still there to click in the fleet console) |
| `1`–`9` | select server |
| `g` | go to any server: the list puts the busiest first and filters as you type |
| `Ctrl+F` | find a player on any server |
| `F8` | the Superintendent's findings (the desktop app) |
| `Ctrl+B` | broadcast |
| `Ctrl+R` | refresh now (on F10, read every health report now; on F8, ask every plug-in) |
| `Ctrl+P` | command palette |
| `?` | the field manual: what everything does, in plain words |
| `x` | show or hide player addresses |
| `/` | jump to the input line |
| `End` | in the feed or command log: back to the newest line (scrolling up holds the view still) |
| `t k b m j v y` | on a player: tell, kick, ban, mute, team, VPN allow, copy ID |
| `n u a r o` | on the blacklist: new ban, unban, VPN allow, VPN revoke, pardon a kick |
| `i l a s w n k y` | on the Forge catalog: install, load now, acknowledge a withdrawal, sort (including FAVOURITES and INSTALLED HERE), time window, next page, API key, copy listing ID |
| any key | skip the boot sequence; `F1`–`F6`, `F8`, `F10` also open that tab (`--no-intro` skips it for good, `--intro full` plays all of it once) |
| `Ctrl+Q` | quit |

## Field names

oni-rcon reads players and events from the server's JSON replies tolerantly, accepting more than one spelling for
each field. The dossier always shows the raw JSON. If something reads as `—` where your server sends data, open an
issue with that raw JSON.

## Develop

```
uv sync
uv run pytest
uv run oni-rcon --demo
```

The demo servers (`oni_rcon/demo.py`) speak the same protocol as a real server and are what the tests run against.
`FakeHost` beside them is a pretend game box for F10: it prints the report a `health_cmd` would, and on a timeline gives
the first server a SIGNATURE crash, the second a BARE one and then the host a memory squeeze, really dropping their RCON
connections. `oni-rcon --demo` starts it about 7, 13 and 19 seconds in.
`oni_rcon/forgefake.py` is a pretend ReclaimerForge, serving the API as `oni_rcon/forge.py` reads it, so neither the
tests nor `--demo` ever reach the real site. Where the developer docs leave a field name or a page shape open, the
assumption is written down at the top of `forge.py`.
Animations follow Textual's `TEXTUAL_ANIMATIONS` (`none`, `basic` or `full`), so `TEXTUAL_ANIMATIONS=none oni-rcon`
turns them off.

`__version__` lives in `src/oni_rcon/__init__.py`. This copy does not publish release binaries from a tag, and a running
console does not replace itself. Updates ship as a reviewed source build; see [IBIGS.md](IBIGS.md).

## Thanks

To the builder of [ReclaimerForge](https://www.reclaimerforge.net), who keeps the community's catalog of forged maps,
gametypes and playlists running on their own time, and gave this integration the nod. F6 is only there because that
work is. oni-rcon doesn't speak for ReclaimerForge; it's a separate community project with its own terms.

## Disclaimer

oni-rcon is an unofficial, fan-made tool. It is not affiliated with or endorsed by Microsoft, Halo Studios or the
Project Reclaimer team. Halo and related names are trademarks of Microsoft Corporation, and the ONI styling is a fan
tribute: the emblem in the interface is a fan-made pixel rendition, and the Windows icon is the ONI emblem. oni-rcon
contains no game files.

## License

GPL-3.0-or-later. Copyright (c) 2026 Arche Labs, with portions copyright (c) 2026 ibigs, LLC. See [LICENSE](LICENSE) and [NOTICE](NOTICE).


IBIGS customer controls remain F7 Start/Stop, F8 Automation (rules, schedules, roles, votes, Discord) and F9 Updates. The upstream Health tab is F10. Generated customer inventory enables the local, read-only health helper automatically.
