# Hosting and connecting servers

The [desktop app](../desktop/README.md) can put a Project Reclaimer dedicated server on your own computer, or find the
ones already running on a game box, and add them with their remote console tested. Both are on its setup screen (the
first start, or **+ ADD SERVER** in the console):

| | On the setup screen |
|---|---|
| A server on this PC | **HOST ONE HERE**, then name it and press **CREATE & START** |
| The servers on a game box | **FIND SERVERS ON IT**, under *Advanced: reach it through SSH* |

Each step shows under the form as it happens. What they find goes into your config file (keeping what's already in it);
press **START** for the console. Do either again and it adds only what's new. The terminal console reads the same config
file, so it connects to them too.

## A server on this PC

### Windows

You need Halo: The Master Chief Collection installed from Steam. Then **HOST ONE HERE**. The form asks what players
will see: the server's **name**, who it's **hosted by**, a **join password** (empty lets anyone in), **max players**, and
whether it's **shown in the server browser** (off: players join by its address), **open to the internet** (UPnP asks
your router to forward the ports) and **kept up to date**. If MCC isn't found, it says so before downloading anything.
Then **CREATE & START**:

1. It finds MCC in your Steam libraries.
2. It downloads the newest Project Reclaimer dedicated server from its
   [GitHub releases](https://github.com/ProjectReclaimer/project-reclaimer-releases/releases), checks it against the
   published SHA-256, and saves it as `project-reclaimer.exe`. A download that doesn't match is thrown away.
3. It makes the server folder with `project-reclaimer dedicated init`: `reclaimer` at the root of the drive MCC is on
   (`D:\reclaimer`), so the maps are hard-linked instead of copied.
   It adds `050_floodvoi.map`, which init leaves out and Flood Infection needs (a server folder that's there already
   gets it too).
4. It saves your choices in `dedicated.toml` (comments kept). A custom server is one server: the first of the two
   `dedicated init` makes is yours, the second is turned off (`enabled = false`, not deleted).
   It turns the remote console on: a new random password goes into `dedicated.toml` under `[rcon]`, with the address
   left on `127.0.0.1`. A password already there is kept.
5. It runs `dedicated check`, then starts the servers in a window of their own. **Ctrl+C in that window stops them.**
   If Windows asks, allow Project Reclaimer through the firewall.
6. Once each remote console answers, it adds the servers to your config with their password (or without it, if you
   untick **Remember the password**).

When it's up, it tells you how players get in ("find *Friday Night* in the server browser", or the address to join a
hidden one) and the firewall command for your ports. Paste it into any PowerShell: it opens a second window that asks
for administrator, which the rules need (run plain, `New-NetFirewallRule` fails with "Access is denied"):

```
Start-Process powershell -Verb RunAs -ArgumentList '-NoExit','-Command','New-NetFirewallRule -DisplayName ''Reclaimer games'' -Direction Inbound -Protocol UDP -LocalPort 49176 -Action Allow; New-NetFirewallRule -DisplayName ''Reclaimer browser'' -Direction Inbound -Protocol TCP -LocalPort 49175 -Action Allow'
```

The same ports must be forwarded on your router for players outside your home, unless you chose UPnP. The remote
console needs nothing opened: it stays on this PC.

A server folder that's already there is started as it is, and your choices aren't applied to it (the form says so):
edit its `dedicated.toml` to change it. With the servers still running, HOST ONE HERE just adds them; stopped, it
starts them again. To change the maps or the playlists, edit `dedicated.toml` in the server folder (Project
Reclaimer's [hosting guide](https://projectreclaimer.dev/host) has every setting) and restart the servers. The
folder is `reclaimer` on MCC's drive, or wherever `ONI_RCON_HOST_DIR` points.

### Linux (Docker)

You need Docker with Compose (`sudo apt install docker.io docker-compose-v2` on Ubuntu) and to be in the `docker`
group (`sudo usermod -aG docker $USER`, then log in again). Then **HOST ONE HERE**, fill in the same form, and
**CREATE & START**; without Docker it says so first.

It saves the official [`compose.yaml`](https://projectreclaimer.dev/docker/compose.yaml) in `~/reclaimer`, writes a new
`RECLAIMER_DEDICATED_RCON_PASSWORD` into `.env` there (owner-only; a password already in it is kept), runs
`docker compose up -d`, waits for the image's first start to write `server/dedicated.toml`, and adds the servers it
lists once their remote consoles answer. For a custom server it starts only `server-1` (the first server in
`dedicated.toml`), writes your choices into `dedicated.toml` once it exists, and restarts that container with them.

**The game files.** The image needs Halo 3's files from MCC. If MCC is installed on this machine (Steam on Linux), it
finds it and sets `GAME_DIR` in `.env`. Otherwise copy them in from the MCC folder of a PC that has the game, keeping
their folders, into `~/reclaimer/server/game`:

- `halo3/halo3.dll`
- `halo3/maps/shared.map`, and `halo3/maps/<map>.map` with `halo3/maps/info/<map>.mapinfo` for each map you host
- `campaign.map` and `040_voi.map` from `halo3/maps`, for Forge's campaign vehicles and AI
- `050_floodvoi.map` from `halo3/maps`, for Flood Infection (without it a vote for it passes and the game fails to load)

For example, from a Windows PC: `scp -r "<MCC folder>\halo3" admin@game-box:~/reclaimer/server/game/` (all of it, a
few GB). Then HOST ONE HERE again.

Open the game ports: it prints the `ufw` commands for yours (`sudo ufw allow 49176/udp && sudo ufw allow 49175/tcp`).

Stop the servers with `docker compose down` in `~/reclaimer`; HOST ONE HERE starts them again.

## The servers on a game box

For servers that are already running on a box you reach over SSH: open *Advanced: reach it through SSH*, type the box
as you'd give it to ssh (`admin@game-box`), and press **FIND SERVERS ON IT**.

It needs SSH key sign-in to the box (`ssh-copy-id admin@game-box` once). It sends a short read-only script
([`discover.sh`](../desktop/src-tauri/src/discover.sh)) over SSH that looks for:

- **Containers** running the `project-reclaimer-dedicated` image (with `docker`, or `sudo -n docker` if your user can
  use that without a password). Each container's `/server` mount gives its `dedicated.toml`; `RECLAIMER_DEDICATED_ONLY`
  narrows it to the one server that container runs.
- **Server folders**: any `dedicated.toml` up to four folders deep under your home, `/opt` and `/srv`.

Passwords are blanked on the box before anything comes back. For each server it finds, the app then reads the
password over SSH, tests the remote console through a tunnel, and adds only the ones that answer, each with:

```toml
[[server]]
port = 49176
name = "Slayer"
ssh = "admin@game-box"
content_dir = "/home/admin/reclaimer/server/content"
password_command = ["ssh", "-o", "BatchMode=yes", "admin@game-box", "docker inspect ... || sed -n ... dedicated.toml"]
```

so **the password is never written on your machine**: it's read on the box each time the console starts, from the
container's environment or the folder's `dedicated.toml`. Change it on the box and the console follows. A server
with its own `rcon_password` in its `[[server]]` block gets that one, found by its RCON port.

When every server it adds is in a container, it also sets `ping_log` and `health_cmd` (if your config has none), so the
PING column and the F7 Health tab work at once. Each picks the right container by the server's port.

What it can't do yet, and what to do instead:

| | |
|---|---|
| A server with its remote console off | Listed but not added. Set `[rcon] password` in its `dedicated.toml` and restart it. |
| Servers not in containers | Added, but with no `ping_log` or `health_cmd`: write those for your setup ([README](../README.md#player-ping)). |
| A Windows game box | Not searched. Add its servers with the form and the SSH box filled in. |
| Folders elsewhere | Not found. Add them with the form or the config file. |

## Ports

A server's remote console listens on its **game port** over TCP (49176 for the first server, 49177 for the second)
unless its `[[server]]` in `dedicated.toml` sets `rcon_port`. The lobby is TCP 49175. Keep `[rcon] address` on
`127.0.0.1`: RCON is not encrypted, and oni-rcon reaches it through SSH.
