# Plug-ins (design draft)

Status: **service and session mode built in the desktop app** (`plugins.rs`, `session.rs`, `Plugins.tsx`: the F10 tab,
palette entries, the CONDITION badge; `--demo` has one). Examples: [hello](../examples/plugins/hello) (service) and
[greeter](../examples/plugins/greeter) (session). The terminal console has service mode only. First plug-in:
[Gryphon](../../gryphon) (automated moderation).

## Why this shape

Oni has two front-ends in two languages (the Textual console in Python, the desktop app in Rust and React), so a plug-in
written *into* either would have to be written twice. And the things worth plugging in (Gryphon, a stats recorder, a
Discord relay) mostly want to run 24/7 on a host, long after the console is closed.

So a plug-in is **never code in Oni's process**. It is a separate program that speaks a small JSON protocol, and it
**describes its own panel** in a fixed vocabulary of blocks that both front-ends draw. Write it once, in any language;
it appears in the desktop app and the terminal console alike. Oni (AGPL-3.0-or-later) and a plug-in's licence stay
separate, because they only exchange JSON: a plug-in is its own program, under whatever licence its author picks.

## A plug-in on disk

```
<config dir>/plugins/gryphon/oni-plugin.toml     # the manifest; the folder name is the plug-in id
```

```toml
api         = 1                       # protocol version; Oni refuses a higher one than it knows
name        = "Gryphon"
version     = "0.1.0"
author      = "Arche Labs"
description = "Automated hate-speech moderation."

[run]
mode = "service"                      # service: runs elsewhere, Oni asks it things.  session: Oni starts it with the console.
exec = "gryphon oni -c /etc/gryphon/gryphon.toml"   # a shell string, run the way health_cmd is (service: once per request; session: once)
host = ""                             # an ssh destination ("" = this machine), same as ping_log/health_cmd
requires = [{ program = "gryphon", url = "https://github.com/viik2k/gryphon" }]  # optional: programs it needs (below)

[permissions]
events = []                           # event kinds pushed to its session process and its pages: "chat", "join", "kill", ...
rcon   = ["pardon", "unban", "unmute"]  # RCON commands it may ask Oni to send for it (an action's `rcon` reply, or session mode)
addresses = false                     # true = sees player IPs unredacted (redaction is on by default everywhere)
```

**What it needs** (desktop app). `run.requires` lists the programs a plug-in needs on the computer that runs it: each a
name (`"node"`) or a table `{program, name, url}` with the name to show and an `https://` page to get it from. A plug-in
whose `exec` starts with `python`, `python3`, `py` or `node` needs that runtime without saying so, and Oni knows Python's
and Node's download pages. When a plug-in is turned on, Oni looks for each one on PATH (on Windows also in the plug-in's folder, where `cmd` looks first) and
offers its page before asking for approval; one that's still missing keeps the plug-in from running, and F10 shows
**NEEDS PYTHON 3** with GET PYTHON 3 and CHECK AGAIN. Python counts when `python3`, `python` or Windows' `py` launcher
says it's Python 3 (Windows' Store placeholder for `python` doesn't), and a leading `python` in `exec` runs as whichever
it found, so `exec = "python greeter.py"` works on a Mac and on Windows without `python` on PATH. Nothing is checked for a
plug-in with a `host`: what's on that machine is its own business. The terminal console ignores `requires`.

Oni lists a plug-in only when its folder is named in the config's **top-level** `plugins = ["gryphon"]` (top level only: an
older Oni exits on a key it doesn't know inside `[defaults]` or `[[server]]`, and ignores one out there).

## Trust

A plug-in is a program that runs as you. Oni does not sandbox it and says so. Before the first run Oni shows the plug-in's
name, author, the exact `exec` line, its host and its permissions, and asks. The answer is stored with a hash of the
manifest in `prefs.json`; **any change to the manifest asks again**. Oni never installs, updates, turns on or starts a
plug-in by itself (each is a click of yours in F10), and the self-updater never touches `plugins/`. `permissions` limits what Oni will *do for* a plug-in (events it
forwards, RCON commands it sends, whether it sees addresses); it is not a promise about what the process itself can do.

## Adding one (desktop app)

F10 walks you through it, from the empty tab or **+ ADD A PLUG-IN**: **get it** (a folder already in `plugins/` is offered
with TURN ON…; DOWNLOAD FROM GIT… asks for an `https://`, `ssh://` or `git@host:path` address, shows it, and `git clone`s it
into `plugins/<name>/`; a repository without an `oni-plugin.toml` is removed again), **turn it on** (Oni adds the folder to
the config's top-level `plugins = [...]`, keeping the rest of the file, and makes the config if there is none), **approve
it** (the dialog under Trust: the plug-in doesn't run before it), and **watch it** (its panel, and its badge in CONDITION).
TURN OFF… on a plug-in's panel takes it out of the list and stops it; its folder and approval stay. Editing the config
and cloning by hand still work.

The same page lists **the oni-rcon index**, [`plugins/index.json`](../plugins/index.json) in this repository: plug-ins
reviewed here, each one INSTALL… away, through the same turn-on and approval steps. Oni fetches it each time the page
opens (never with `ONI_RCON_NO_UPDATE=1`) and keeps a copy, `plugin-index.json` beside `prefs.json`, for offline. Each
entry is `{id, name, description, url, path, commit, author, tags}`: `url` is an `https://` git address, `path` the
plug-in's folder in it (`""` for the root) and `commit` the reviewed one (`""` for the tip). An entry at its repository's
root is a clone and gets updates as below; one in a subfolder is a copy of that folder alone, so it doesn't. To list
yours, open a pull request adding it.

## Updates (desktop app)

A plug-in folder that is a `git clone` follows its upstream branch. Install one with

```
git clone https://github.com/you/your-plugin <config dir>/plugins/your-plugin
```

Each time the app starts or its window is reloaded, and on Ctrl+R on F10, Oni runs `git fetch` in each such folder. When the
upstream branch has commits the folder hasn't, F10 marks the plug-in ▲ and, the first time its panel is on screen, asks
**UPDATE <NAME>?** with the commits' subjects and where they came from. UPDATE fast-forwards the folder
(`git merge --ff-only`), restarts a session plug-in and asks it for a fresh view: no restart of Oni. A folder with
commits or edits of its own in the way isn't touched, and says why. A changed manifest needs approving again, like any
manifest change; a code-only change doesn't, so read what changed if you don't know its author.

**For a plug-in's author**: commit to the branch people cloned (normally `main`). That's the release. Oni needs `git`
on PATH, never prompts for a login (a private repository needs credentials git already has), and never shows the login
part of an https remote.

## Two ways to run

**service**: Gryphon's case. The daemon lives on a host and keeps moderating whether or not a console is open. Each time
Oni needs something it runs `exec` there (locally, or over ssh with the same code the Health tab uses), writes one JSON
request to stdin and reads one JSON reply from stdout. Nothing stays connected, so a flaky link costs one refresh, not a
session. Timeouts: 10 s for `view`, 30 s for `action`.

**session**: for a plug-in that wants the live feed (a rules engine, a recorder). Oni starts `exec` with the console and
stops it on quit, restarts it with backoff if it dies (1 s, doubling to 60 s; back to 1 s after a minute's run), and
talks JSON lines over stdin/stdout. Only the event kinds in `permissions.events` are pushed; RCON goes through Oni's own
links, so a plug-in costs no extra RCON slot. Oni stops it by closing its stdin, then killing it 2 s later: **exit when
stdin closes** (on Windows a process the plug-in starts itself outlives the kill).

## Protocol (api 1)

Every message is one JSON object. Unknown keys are ignored.

**view**: Oni → plug-in `{"type":"view","server":"3","snapshot":{...},"replies":[...]}` (the selected server; `null` for
none). Reply below.

`replies` (desktop 1.8.0 and later) are what the servers answered the RCON its actions asked for since its last view
(the last 20): `{"server", "command", "args", "ok", "text", "data"}`, with `ok` `null` for no reply and addresses
blanked unless `permissions.addresses` is true. A view is asked for straight after an action that sent RCON, so a
plug-in can query (`status`, a ban check) and draw the answer.

`snapshot` (desktop 1.8.0 and later; `null` while the server is offline, absent from the terminal console) is the selected
server as the console sees it: `{"players": [...], "map": "guardian", "mode": "slayer", "score": [{"team": "red",
"players": 4, "score": 31}, ...]}`. `players` are the server's own player objects (`name`, `player_id`, `team`, `score`,
`kills`, `deaths`, ...), with addresses blanked unless `permissions.addresses` is true. `score` is empty in a
free-for-all. Action requests carry the same `snapshot`.

Reply:

```json
{"title": "Gryphon",
 "badge": {"level": "ok", "text": "DRY-RUN"},
 "blocks": [
   {"type": "kv",    "rows": [["Mode", "dry-run"], ["Servers online", "5 / 6"], ["Last action", "12 min ago"]]},
   {"type": "table", "id": "actions", "columns": ["Time", "Server", "Action", "Player", "Why"],
                     "rows": [{"id": "<player id>", "cells": ["12:41", "3", "mute 10m", "Bob", "chat [hate] 'nazi'"]}],
                     "row_actions": ["undo"]},
   {"type": "text",  "text": "Words are re-read when the file changes."}
 ],
 "actions": [
   {"id": "check",  "label": "Check a message", "params": [{"name": "text", "label": "Message", "type": "text"}]},
   {"id": "go-live", "label": "Go live", "danger": true, "confirm": "Gryphon will really kick, mute and ban."}
 ]}
```

Blocks: `kv`, `table`, `text`, `log` (lines, newest last). `badge.level` is `ok | warn | alert | off`; `warn` raises the
console's CONDITION to AMBER and `alert` to RED while it holds, `ok` and `off` change nothing. Param types:
`text | number | bool | choice` (with `options`) `| duration` (`10m`, `1d`). An action with `danger` or `confirm` goes through
Oni's own confirm dialog, whose button defaults to ABORT. A block type this Oni doesn't know is drawn as a grey line
("this version of Oni can't draw a 'chart' block"), never an error.

**html block** (desktop app only; the terminal console draws it as the grey line): a plug-in's own page, for any UI the
other blocks can't draw. A view with an html block shows only its html blocks in the desktop app, so its other blocks
can be the terminal console's fallback without drawing twice.

```json
{"type": "html", "html": "<!doctype html>…", "height": 420, "data": {"any": "JSON"}}
```

- `html` is a whole page (up to 512 KiB). It's shown in a sandboxed frame (`sandbox="allow-scripts"`, opaque origin) with
  its own CSP: inline `<script>` and `<style>`, `data:`/`blob:` images, fonts and media, and **no network at all** (no
  fetch, no external scripts, styles or images) and no forms (use buttons). It can't reach Oni, Tauri or the page around it.
- `height` is the frame's starting height in pixels (default 420); the page can change it.
- `"persist": true` (desktop 1.8.0 and later) keeps the page running, out of sight, while F10 is closed or another plug-in
  is selected, so its timers keep going (a countdown, a scheduled broadcast). Without it the page is unloaded then. Its
  `action` messages still work while hidden; a `danger`/`confirm` action shows its dialog over whatever tab is open.
  Views are still asked for only as above, so `data` doesn't change while F10 is closed (unless it has a badge).
- `"fill": true` (after desktop 1.8.0) lets the frame take the whole panel instead of a fixed `height`.
- The frame reloads only when `html` changes. Send changing values in `data` instead: Oni posts it to the page on load
  and whenever a view arrives, redacted like everything else. With redaction on, IPv4 addresses in `html` are blanked too.
- Oni → page (`message` event): `{"oni": "data", "data": …, "redact": true, "server": "3", "servers": [{"name": "3",
  "online": true}, …], "theme": {"amber": "#d9a441", …}}`.
- Page → Oni (`parent.postMessage(msg, "*")`):
  `{"oni": "action", "id": "undo", "row": "<row id>", "params": {"name": value}}` runs one of the view's own `actions`
  (any other id is refused), with its `confirm`/`danger` dialog, skipping the form when `params` is given;
  `{"oni": "refresh"}` asks for a new view; `{"oni": "height", "px": 300}` resizes the frame (40–4000);
  `{"oni": "ready"}` asks for the data again.
- Also (desktop 1.8.0 and later): `{"oni": "rcon", "id": 1, "server": "3", "command": "say", "args": ["hi"]}` sends one
  RCON command, filtered like an action's (`server` defaults to the selected one), and is answered
  `{"oni": "rcon-reply", "id": 1, "ok": true, "text": "…", "data": …}`. `{"oni": "call", "ref": 1, "data": {...}}` runs its
  `exec` with `{"type": "call", "data": {...}, "server": "3"}` (a session plug-in answers with the request's `ref`) and is
  answered `{"oni": "reply", "ref": 1, "ok": true, "data": <its reply>}` or `{… "ok": false, "error": "…"}`: the page's
  way to fetch, store or compute without a view round trip. The page hears `{"oni": "event", "server", "ev"}` for each
  event kind in `permissions.events`, and `{"oni": "rcon-reply", "replies": [...]}` after an action's RCON. A `kill`
  event also carries `killer_name`, `victim_name` and `medals`, as the console names them.
- Also (after desktop 1.8.0): `{"oni": "notify", "text": "…", "level": "info" | "warn" | "error"}` pops up a
  notification (6 a minute, the rest dropped), and `{"oni": "key", "key": "F1"}` (F1–F12) passes an F-key the page
  caught on to Oni, so its tabs still switch while the frame has focus.
- **Built in: [Automation Studio](STUDIO.md)** is an html-block plug-in shipped inside the desktop app (`studio.rs`,
  `studio.html`). It needs no folder, config entry or approval, hears every event kind, and its `call`s go to the app
  itself, which keeps its scripts in `studio/` beside the config.

**action**: Oni → plug-in `{"type":"action","id":"undo","params":{},"row":"<row id>","server":"3","snapshot":{...}}`. Reply:

```json
{"ok": true, "toast": "Undid Bob's mute.", "view": {...optional, replaces the panel...},
 "rcon": [{"server": "3", "command": "unmute", "args": ["<player id>"]}]}
```

`rcon` asks Oni to send those commands on the plug-in's behalf; Oni sends only commands listed in `permissions.rcon`,
logs each to the command log as `plug-in gryphon ▸ unmute …`, and drops the rest with a toast. `server` is a server name from
the config; one that isn't there is dropped too. So a *service* plug-in can
still move a player without holding an RCON slot or a password.

**session mode adds** (each a JSON line with `type`):

- Oni → plug-in: `hello {api, servers:[{index,name}], granted:{events, rcon, addresses}}` first;
  `event {server, ev}` (`ev` the server's own event object, addresses blanked unless granted);
  `rcon-reply {id, ok, text, data}` for each `rcon` it sent (`id` is its own);
  and `view`, `action` and `call` requests as above, each with a `ref` number.
- plug-in → Oni: the answer to a request, as for a service plug-in but carrying the request's `ref` (no answer in 10 s for
  a view or 30 s for an action is an error; requests made while it's down fail with "it isn't running");
  `rcon {id, server, command, args}` (filtered like an action's; one refused is answered `rcon-reply` with `ok: false`);
  `view {…}` (pushed: replaces the panel, at most 5 times a second; a burst is drawn as its latest); `notify {level: info | warning | error, text}` (a toast);
  `log {text}` (the command log). A line over 1 MiB, or that isn't a JSON object, is ignored.
- RCON a plug-in sends by itself (a session's `rcon`, a page's `{"oni": "rcon"}`) is held to 60 commands a minute per
  plug-in, and 20 `say`/`tell` a minute to one server, as automation is. One over the limit isn't sent: it's answered
  `ok: false` with `not sent: … is the limit`, and the command log says so now and then. An action's `rcon` (a click) isn't
  limited. Oni keeps up to 256 lines waiting for a session that isn't reading its stdin; while it's that far behind, newer
  ones (events first of all) are dropped and the command log says so.

## Wire rules (both hosts must follow these exactly)

- **Running a service plug-in.** `exec` is a shell string. Without `host`, run it on this machine through the platform shell;
  with `host`, run `ssh -o BatchMode=yes -o ConnectTimeout=15 <host> "<exec>"`. Same as `health.run`, except stdin is a pipe:
  write the request as one JSON line, then close stdin. A local plug-in runs with its own folder as the working directory.
- **Reading the reply.** The whole of stdout is one JSON object. If it parses, it wins even when the exit code is non-zero.
  If it doesn't parse, or nothing was printed, show an error: the last stderr line (scrubbed of addresses), or "exited N",
  or "its reply isn't JSON" when it exited 0 with no stderr.
  stderr is otherwise never shown. A timeout kills the process and shows "no answer in Ns".
- **Failures.** A reply may be `{"error": "words"}` (view) or `{"ok": false, "error": "words"}` (action). Shown as a toast and,
  for a view, a greyed panel. Never a traceback.
- **Caps.** 500 table rows (the rest dropped, with a "+N more" line); 10 000 characters per string; 20 actions;
  10 params per action. Over these caps is truncated, never an error. stdout over 1 MiB is the one exception: an error
  ("its reply is over 1 MiB"), because cut JSON can't be read.
- **Values.** Params arrive as: `number` a JSON number, `bool` `true`/`false`, the rest strings. A `row_actions` id not in
  `actions` gets its id, upper-cased, as the button label.
- **Plain text only.** Every string a plug-in sends is data, never markup: not Rich/Textual markup, not HTML, not Markdown.
  Escape or use text nodes. The `html` block is the one exception, and only inside its sandboxed frame. This is the one security rule that is easy to get wrong in the console.
- **Redaction.** Anything shown from a plug-in goes through the same address redaction as the rest of the UI (on by default).
  `permissions.addresses` only decides whether Oni *sends* unredacted addresses to the plug-in.
- **Ids.** A plug-in id is its folder name, `[a-z0-9-]+`. A row `id` is opaque to Oni: the plug-in chooses it and gets it
  back as `row`. An unknown `api` greater than Oni's is listed, disabled, with the reason.
- **Selected server.** `server` is the console's selected server's config `name` (its address when it has none), or
  `null`. A plug-in may ignore it.

## Config and storage

- **Where.** Plug-ins live in `plugins/` next to the active config file, one folder each. Both front-ends already share that file.
- **Which are on.** A top-level `plugins = ["gryphon"]` in the config. Nothing in `[defaults]` or `[[server]]`. Add a
  `plugin_settings(path)` helper next to `forge_settings` and `health_settings`, top-level keys only (`plugins`, `plugin_*`).
- **Trust.** `prefs.json` gains `plugins_trusted: {"gryphon": "<sha256 of oni-plugin.toml bytes>"}`. `Prefs` today rewrites
  only its own keys on save, so it must be changed to load, modify and write, keeping keys it doesn't know. Each front-end
  keeps its own trust record; approving in one does not approve in the other.
- **Refresh.** On open, on Ctrl+R, after an action, and every 30 s while the tab is open. Never in the background when closed,
  except a plug-in with a `badge` that feeds CONDITION: that one is polled every 60 s while the console is open. Each
  approved plug-in is asked once soon after start, to learn whether it has a badge. One whose last answer was an error
  adds no badge. The `--demo` plug-in counts as approved.

## What the user sees

- One **PLUGINS** tab (F10) with a strip of plug-in names, each showing its badge. The panel is the plug-in's `view`,
  refreshed on open, on Ctrl+R, after any action and every 30 s while open. The same tab in the terminal console and the
  desktop app.
- **Palette** (Ctrl+P) gets `Gryphon › Check a message`, `Gryphon › Go live`, one entry per action.
- A plug-in that can't be reached shows its last view greyed with the reason ("ssh refused your key"), not a blank tab.
- Addresses in anything a plug-in sends are redacted like everywhere else unless the user toggled `x`.

## Building it

1. **Spec and brief**: this file and [PLUGINS-BUILD.md](PLUGINS-BUILD.md), which lists the golden cases both hosts must pass
   and what to build in what order. Gryphon's side: `gryphon/docs/ONI.md`.
2. **Host in the terminal console** (first: Python iterates fastest, and it proves the protocol): `plugins.py` and a Textual
   panel, service mode only, plus Gryphon's `gryphon oni`.
3. **Host in the desktop app**: `plugins.rs` (discovery, trust, run, ssh reuse) and a React `PluginPanel`, against the same fixtures.
4. **Session mode** and the palette/CONDITION hooks. Done in the desktop app.
5. Later, maybe: automation rules (F9) that call a plug-in action, and moving Health's `health_cmd` onto the same path.

## Gryphon's side (independent of Oni; can be built now)

`gryphon oni` implements `view` and `action` and nothing else. It reads `gryphon-actions.jsonl` and a small
`gryphon-state.json` the daemon refreshes every 10 s (mode, per-server online, started-at), so `view` can say "daemon not
running" instead of guessing. Actions: `check` (what does the list make of this text), `undo` (row → an `rcon` of `unban`, `unmute`
or `pardon` depending on what was done), `go-live` / `go-dry` (rewrites `live` in `gryphon.toml`, behind a confirm), `add-word`
(appends to the `extra_words` file). Oni never reads Gryphon's files; it only asks `gryphon oni`. Full contract:
`gryphon/docs/ONI.md`.
