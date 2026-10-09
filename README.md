# oni-rcon

![oni-rcon boot screen: the ONI emblem over the clearance check](docs/boot.png)

**An ONI-themed terminal admin console for Project Reclaimer Halo 3 dedicated servers.**
Every server you run on one screen: players, live intercepts, kicks and bans, map and mode control, votes, VPN
allowances and a raw command console, wrapped in Office of Naval Intelligence, Section Three dossier styling.

![Assets tab: players table and personnel dossier](docs/assets.png)

**[Download for Windows](https://github.com/viik2k/oni-rcon/releases/latest/download/oni-rcon-windows-x64.exe)** · [Linux](https://github.com/viik2k/oni-rcon/releases/latest/download/oni-rcon-linux-x64) ·
or `uv tool install git+https://github.com/viik2k/oni-rcon`. The first time it opens a setup screen: type your server's
address and RCON password, it tests them, and you're in. Or choose **Try the demo** to look around three simulated
servers first.

## What it does

| Tab | |
|---|---|
| **F1 Assets** | Live player table per server: team, score, K/D, health and shield bars, admin, dead and spree flags, and the score race between the teams along the bottom. The dossier shows the selected player's file, a biometric glyph drawn from their player ID, the medals they've earned and the raw JSON. `t` tell · `k` kick · `b` ban · `m` mute · `j` team · `v` VPN allow · `y` copy ID |
| **F2 Intercepts** | One feed from every server: chat (team and server), kills with weapon, joins, leaves, kicks, bans, votes and game phases. Kills carry their Halo 3 medals: double kill through killionaire, killing spree through invincible, and killjoy. Filter by chat, combat, traffic, moderation or ops; scroll up to read back and it holds still. Chat that mentions admins or cheating raises a toast and the terminal bell |
| **F3 Operations** | Sitrep for the selected server with the vote under way and its tally; the theatre, with each team's numbers and score as bars, the top guns and who's on a spree; the playlist rotation with the current map marked; and buttons for load map + mode, change map or mode, queue next, end round or game, shuffle, team count, call, pass or cancel a vote, broadcast, rename, join password and ping limit |
| **F4 Blacklist** | Bans by player, IP and device; new ban (timed or permanent), unban, VPN allow and revoke, check an IP |
| **F5 Console** | Type any RCON command (`help` lists them) with ↑/↓ history. The command log records everything sent from this session and its replies. The message in `say`, `tell`, `kick` and `servername` is the rest of the line as typed, sent as one argument, so it needs no quotes |
| **F6 Forge** | The [ReclaimerForge](https://www.reclaimerforge.net) catalog of community maps, gametypes and playlists, read with your own API key. Order it by trending, rising, latest, updated, downloads, rated, unrated or overlooked, over the last 24 hours, 7 days or 30 days, and search it. Each listing shows its type, author, rating, recent downloads, and whether it runs on the selected server's version; its file lists every version. `s` sort · `w` window · `/` search · `n` more · `k` key · `y` copy ID |

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
- **Built for fleets.** Past 12 servers the cards slim to two lines, the boot screen tallies the stations instead of
  listing each one, and the console stays light enough for a web terminal: a card is redrawn only when what it shows
  changes, status polls are spread over 15 seconds instead of fired together, and servers sign in 8 at a time with
  jittered reconnects.
- **An alert condition.** The masthead reads CONDITION GREEN; AMBER while a station is down; RED when a call for an
  admin or an anti-cheat hit comes in. A server you aren't looking at pulses red and keeps a ⚑ count until you open it
  or the Intercepts tab.
- **Broadcast to one server or all.** `Ctrl+B`. `@all` messages and commands go out 4 servers at a time, each reply
  goes in the command log on one line, and a tally closes it ("79 stations · 77 ok · 2 no reply yet"). A command that
  gets no reply in 10 seconds is never resent, and if its reply comes later the log shows it.
- **Command palette.** `Ctrl+P`.
- **Player addresses are redacted by default,** in the tables, the feed, the toasts and the console's replies and raw
  events. Press `x` to reveal them. This helps when you stream or share screenshots.
- **SSH tunnels are built in.** One `ssh -L` per host carries every server on it, and it reconnects with backoff.
- **Destructive actions ask first.** The confirm button defaults to ABORT.
- **A refused password is never retried.** The server locks an address out after 5 wrong passwords in 10 minutes, so
  oni-rcon won't trip it. RECONNECT on F3 asks for the password again before it tries.
- **Medals count what the console has seen.** They're worked out from the kill feed, so a spree that started before
  oni-rcon connected isn't known, and a reconnect starts everyone's spree over.

<p>
  <img src="docs/intercepts.png" alt="Intercepts tab: one feed of chat, kills, medals and joins from every server" width="49%">
  <img src="docs/operations.png" alt="Operations tab: sitrep, theatre, rotation and server controls" width="49%">
</p>

## Install

You need a Project Reclaimer dedicated server with RCON turned on (tested against 0.9.7).

**Windows:** download [`oni-rcon-windows-x64.exe`](https://github.com/viik2k/oni-rcon/releases/latest/download/oni-rcon-windows-x64.exe) and double-click it. The build
isn't code-signed, so SmartScreen may stop it the first time: choose **More info**, then **Run anyway**. The setup screen
takes it from there.

**Linux:**

```
curl -Lo oni-rcon https://github.com/viik2k/oni-rcon/releases/latest/download/oni-rcon-linux-x64
chmod +x oni-rcon
./oni-rcon --demo
```

**With Python 3.11+** (any OS, macOS included):

```
uv tool install git+https://github.com/viik2k/oni-rcon
```

or `pipx install git+https://github.com/viik2k/oni-rcon`. From a clone, `uv tool install .` also works.

**Updates.** The downloads update themselves: at start they check GitHub for a newer release, swap it in, and tell you
to restart. A Python install tells you to run `uv tool upgrade oni-rcon` instead. Set `ONI_RCON_NO_UPDATE=1` to turn
the check off.

Use a terminal with true colour and a font that has box-drawing glyphs, such as Windows Terminal, iTerm2, kitty or
WezTerm. At least 140×40 looks best.

## Turn on RCON on the server

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
oni-rcon 11774                                # a server on this machine
oni-rcon 11774 11775 11776                    # several at once
oni-rcon --ssh admin@game-box 11774 11775     # through an SSH tunnel to the game box
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
port = 11774
[[server]]
port = 11775
```

## ReclaimerForge

[ReclaimerForge](https://www.reclaimerforge.net) is the community catalog of forged maps, gametypes and playlists for
Reclaimer. oni-rcon reads it with **your own API key**: none ships with oni-rcon, and every admin uses their own.
Without one, everything else works as before. `oni-rcon --demo` brings a pretend Forge to try F6 with.

**Get a key** on reclaimerforge.net with the `catalog:read` and `assets:download` scopes, and nothing more. oni-rcon
never writes, uploads or publishes anything there.

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

oni-rcon keeps to the key's quota (120 requests a minute): it reads the rate-limit headers on every reply, waits out a
`429` for as long as Forge asks, and keeps recent replies so it doesn't ask twice. Those replies and every verified
download sit in `%LOCALAPPDATA%\oni-rcon` on Windows or `~/.cache/oni-rcon` elsewhere.

## Security

- **RCON is plain text.** Keep the server's `[rcon] address` on `127.0.0.1` and reach it with `--ssh` (or a VPN). Never
  open the RCON port to the internet.
- **Keep passwords out of files you commit.** Use `password_env` or `password_command`, and never `password`, in a
  config that lives in a repo. `oni-rcon.toml` is in `.gitignore`.
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
| `F1`–`F6` | Assets · Intercepts · Operations · Blacklist · Console · Forge |
| `1`–`9` | select server |
| `g` | go to any server: the list puts the busiest first and filters as you type |
| `Ctrl+B` | broadcast |
| `Ctrl+R` | refresh now |
| `Ctrl+P` | command palette |
| `?` | the field manual: what everything does, in plain words |
| `x` | show or hide player addresses |
| `/` | jump to the input line |
| `End` | in the feed or command log: back to the newest line (scrolling up holds the view still) |
| `t k b m j v y` | on a player: tell, kick, ban, mute, team, VPN allow, copy ID |
| `n u a r` | on the blacklist: new ban, unban, VPN allow, VPN revoke |
| `s w n k y` | on the Forge catalog: sort, time window, next page, API key, copy listing ID |
| any key | skip the boot sequence; `F1`–`F6` also open that tab (`--no-intro` skips it for good) |
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
`oni_rcon/forgefake.py` is a pretend ReclaimerForge, serving the API as `oni_rcon/forge.py` reads it, so neither the
tests nor `--demo` ever reach the real site. Where the developer docs leave a field name or a page shape open, the
assumption is written down at the top of `forge.py`.
Animations follow Textual's `TEXTUAL_ANIMATIONS` (`none`, `basic` or `full`), so `TEXTUAL_ANIMATIONS=none oni-rcon`
turns them off.

To release, bump `__version__` in `src/oni_rcon/__init__.py`, commit, and push a matching tag (`git tag v0.2.0 &&
git push --tags`). The release workflow builds the Windows and Linux binaries and publishes them, and running copies
pick the new version up on their next start.

## Thanks

To the builder of [ReclaimerForge](https://www.reclaimerforge.net), who keeps the community's catalog of forged maps,
gametypes and playlists running on their own time, and gave this integration the nod. F6 is only there because that
work is. oni-rcon doesn't speak for ReclaimerForge; it's a separate community project with its own terms.

## Disclaimer

oni-rcon is an unofficial, fan-made tool. It is not affiliated with or endorsed by Microsoft, Halo Studios or the
Project Reclaimer team. Halo and related names are trademarks of Microsoft Corporation, and the ONI styling is a fan
tribute: the emblem in the interface is a fan-made pixel rendition, and the Windows icon is the ONI emblem. oni-rcon
contains no game files.

## Licence

[MIT](LICENSE) © 2026 Arche Labs
