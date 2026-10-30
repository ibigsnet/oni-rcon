# Automation Studio

A plug-in built into the desktop console: an editor for small JavaScript automations, and the place they run. Open it
with **F10** → *Automation Studio*. It needs no install or approval, and it's always in the list.

Where [automation rules](AUTOMATION.md) (F9) are WHEN / ONLY IF / THEN DO, the Studio handles anything you can write
in code: counters that outlive restarts, `!commands` with arguments, strike systems, rotating announcements, and Discord logs.

```js
// The top level runs once, when the script starts. What it hooks keeps it running.
command("!kills", (ev) => {
  ev.reply(`${ev.player} has ${store.get("kills:" + ev.player, 0)} kills`);
});

on("kill", (ev) => {
  store.inc("kills:" + ev.player);
});

every("30m", () => say("*", "Type !kills to see how you're doing"));
```

## The editor

- **Scripts** (left): every script, with its light (green running, amber starting, red failed, grey finished) and
  its switch. A script that is switched **on** starts whenever Oni does. Hover a script to rename, duplicate or delete it.
  **Outline** lists what the open script hooks (`on`, `command`, `every`, `after`) and its top-level names.
- **Tabs**, with a dot for unsaved changes. A script changed on disk reloads by itself. If you also have changes, a
  banner asks which copy to keep.
- **Editing**: syntax colouring, bracket matching, auto-closing brackets and quotes, multi-line indent, comment and move,
  find and replace (with case and regex), and go to line or symbol.
- **Suggestions** as you type (Ctrl+Space): the API, event kinds (`on("…`), each kind's fields (`ev.…`), store
  methods, your server names, Discord and Telegram names from F9, durations and ban or mute times. Each comes with its
  docs. Signature help appears inside a call (Ctrl+Shift+Space), and hovering a name shows its docs.
- **Problems**, marked under the code and listed in PROBLEMS: syntax errors, plus checks for an unknown event kind or
  field, a server, webhook or chat Oni doesn't have, a bad duration or ban time, an RCON command Oni doesn't know,
  `fetch`, `eval` and the like (not available), and runtime errors from the last run, with their line.
- **Commands** (Ctrl+Shift+P) and **open a script** (Ctrl+P), both with fuzzy search.

The panel below the editor (Ctrl+J) has these tabs:

| Tab | What it's for |
|---|---|
| CONSOLE | Every script's `log`/`warn`/`error` lines, the RCON each one sent, starts, stops and failures. Filter by script, level or text. Click a `file:line` link to jump to it. |
| PROBLEMS | Everything above, for every script. |
| EVENTS | Live server events as scripts see them. Pause, filter, expand to the JSON, and send any one to TEST or into SIMULATE. |
| SIMULATE | Edit an event as JSON (each kind has an example) and send it to the TEST run, or, after a confirm, to the live scripts. |
| RCON | A command line to any server, through the same permissions as a script's. |
| STORE | A script's saved data as JSON. You can view, edit, save or clear it. |
| DOCS | The API, events, fields, limits and keys, searchable. |

## Running

- **RUN** (Ctrl+Enter) saves the script, turns it on and starts it again from what is saved. It runs for real until
  **STOP**, and starts again whenever Oni does.
- **TEST** (Ctrl+Shift+Enter) runs the editor's text, saved or not, as a **dry run**:
  - Read-only commands are sent: `status`, `players`, `bans`, `maps`, `modes`, `nextmap`, `admins` and `help`.
    `nextmap` and `admins` count only without arguments: `nextmap Guardian` queues a game, so it is logged.
  - Every other command, including `say` and `kick`, is logged instead and resolves to `{ ok: null }`.
  - `notify`, `discord` and `telegram` are logged too.
  - The store starts from the saved one but is never saved.

  Real events reach the dry run, and SIMULATE sends it made-up ones.
- **RESTART** starts the live run again from disk (or the TEST from the editor). *Stop every script* (Ctrl+Shift+P)
  stops all of them without turning any off.

The top level runs once. `on`, `command`, `every` and `after` keep a script running, and once it has nothing left to wait
for, it's *finished*. An error in a handler is logged and the script carries on, but an error at the top level stops it.
F10's list shows how many scripts are running, or how many failed. A live script that fails also pops a notification.

## How it's kept safe

Each script runs in its own Web Worker inside the plug-in's sandboxed frame:

- It has no network: no `fetch`, WebSocket or imports.
- It has no files and no page.
- It can't reach the app except through the frame's messages.

Its RCON goes through Oni's plug-in permission filter, logged like any plug-in's. Discord and Telegram posts go
through the webhooks and chats you set up in F9, named, never by URL or token. Addresses in events are blanked, as for any
plug-in without the `addresses` permission. These limits apply:

| What | Limit |
|---|---|
| RCON | 120 commands a minute per script |
| `say` and `tell` | 20 a minute per server, all scripts together |
| `notify` | 6 a minute per script |
| Discord, Telegram | one post every 2 s (Discord) or 3 s (Telegram), with at most 20 waiting |
| errors | a script with over 50 errors in a minute is stopped |
| hangs | a script that stops answering for 6 s (a loop that never ends) is stopped |
| timers | `every()` and `setInterval` run at most once a second |
| log | 500 lines per 10 s per script |
| store | 256 KiB per script |
| scripts | 200, each at most 256 KiB |

## The API

| | |
|---|---|
| `on(kind, filter?, handler)` | Runs `handler(ev)` for each event of a kind (`"chat"`, `"kill"`, …), a list of kinds, or `"*"` for every kind. `filter` is an object of fields to match (`text` ignores case, a list matches any of its items, a RegExp tests the field) or a function of `ev`. Returns a function that stops it. |
| `command(name, filter?, handler)` | Runs `handler(ev)` when a chat line starts with `name` (or one of a list), like `"!rules"`. Case is ignored, and the server's own lines never count. `ev.command` is the word, `ev.rest` the text after it, `ev.args` its words. |
| `every(duration, fn)` | Runs `fn` every `duration`: `"30s"`, `"10m"`, `"2h"`, `"1d"` or milliseconds, at least 1 s. Returns an id for `cancel`. |
| `after(duration, fn)` | Runs `fn` once, after `duration`. Returns an id for `cancel`. |
| `cancel(id)` | Stops an `every` or `after`. |
| `sleep(duration)` | A promise that resolves after `duration`: `await sleep("5s")`. |
| `log(...)`, `warn(...)`, `error(...)` | Write to the console. Objects are shown in full. |
| `rcon(server, command, ...args)` | Sends any command Oni knows. Resolves to `{ ok, text, data }`, where `ok` is false when the command failed or was not sent. |
| `say(server, text)` | Says `text` on a server, or on every online server with `"*"`. |
| `tell(server, player, text)` | Whispers to one player. `player` is a name, an id, or a player or event object. |
| `kick(server, player, reason?)` | Kicks a player. |
| `ban(server, player, time?, reason?)` | Bans a player. `time` is `""` (for good), `"30m"`, `"2h"`, `"12h"`, `"1d"`, `"7d"`, `"2w"` or `"30d"`. |
| `mute(server, player, time?, reason?)`, `unmute(server, player)` | Mutes a player (`""` means until the server restarts, or `"10m"`, `"1h"`, `"12h"` or `"1d"`), or unmutes them. |
| `players(server)` | Resolves to the players on a server, as its `players` command lists them. |
| `servers()` | The servers Oni has, as `[{ name, online }]`. |
| `notify(text, level?)` | Pops a notification up in Oni. `level` is `"info"`, `"warn"` or `"error"`. |
| `discord(hook, text)`, `telegram(chat, text)` | Posts to the webhook or chat with that name in F9. Resolves once it's posted. |
| `store` | Saved data that outlives restarts, as JSON values only: `get(key, fallback?)`, `set(key, value)`, `inc(key, by?)`, `del(key)`, `has(key)`, `keys(prefix?)`, `all()`, `clear()`. |
| `script` | `script.name`, and `script.dry` (true during a TEST). |

### Events

Every event has these fields:

- `ev.server`: the server it came from.
- `ev.kind`: the event kind.
- `ev.at`: when Oni got it, in ms since 1970.
- `ev.player`: who it's about. That is `killer_name` for a kill, `target` for a pardon, and `name` for every other kind.

Every event also has these helpers, which act on its server and player: `ev.reply(text)`, `ev.tell(text)`,
`ev.kick(reason?)`, `ev.ban(time?, reason?)`, `ev.mute(time?, reason?)` and `ev.unmute()`.

| Kind | Fields |
|---|---|
| `chat` | `channel` (`"server"` is the server itself), `name`, `team`, `text`, `player_id` |
| `kill` | `killer`, `victim`, `weapon`, `killer_name`, `victim_name`, `medals` |
| `join` | `name`, `player_id`, `address` |
| `leave` | `name`, `reason` |
| `refused` | `name`, `id`, `ip`, `reason` |
| `kick` | `name`, `id`, `ip`, `by`, `reason`, `seconds` |
| `ban` | `name`, `id`, `ip`, `by`, `reason` |
| `unban`, `unmute`, `admin_added`, `admin_removed` | `name`, `id`, `by` |
| `mute` | `name`, `id`, `by`, `reason` |
| `cheat` | `name`, `text` |
| `pardon` | `target`, `name`, `by` |
| `vote` | `text`, `name`, `action`, `vote_type`, `target` |
| `medal` | `medal`, `name`, `player_id` |
| `award` | `award`, `name`, `player_id` |
| `lagged` | `name`, `player_id` |
| `control` | `text`, `action`, `ok` |
| `admin` | `text` |

## Templates

**+ NEW** (Ctrl+N) starts from one of these templates:

| Template | What it does |
|---|---|
| Empty | Nothing yet. |
| Welcome | Greets each player who joins, counts their visits, and answers `!rules`. |
| Chat filter | Mutes a player who says a listed word and bans one who keeps it up (three strikes). |
| Kill streaks | Calls out every fifth kill in a row and answers `!top`. |
| Announcer | Rotates through a list of messages on every server every 10 minutes. |
| Reports | `!report <player> <what they did>` notifies you in Oni and posts to Discord. |
| Discord log | Posts joins, kicks and bans to a Discord webhook. |
| Next-game vote | An admin types `!nextvote`, players answer with a number (their last answer counts), standings go out with 30 and 10 s left, and the winner is queued with `nextmap` so the current game finishes. `!nextvote end`, `cancel` and `list`. Votes on `CHOICES` at the top of the script, or on up to 5 games from the server's rotation. Only admins (matched by player ID) can start or stop one. |

## On disk

Scripts live in `studio/` beside the config file:

- each script is `<name>.js`;
- its store is `<name>.data.json`;
- `studio.json` lists the scripts that are on.

Names are lower-case letters, digits, `-` and `_`, up to 40 characters, starting with a letter or digit. You can edit the
files in any editor. The Studio picks up changes within 5 s.

## Keys

| Keys | Action |
|---|---|
| Ctrl+S, Ctrl+Shift+S | save, save all |
| Ctrl+Enter, Ctrl+Shift+Enter | run, test |
| Ctrl+P, Ctrl+Shift+P | open a script, commands |
| Ctrl+Shift+O, Ctrl+G | go to a symbol, a line |
| Ctrl+F, Ctrl+H | find, replace |
| Ctrl+Space, Ctrl+Shift+Space | suggest, signature |
| Ctrl+/, Ctrl+D, Ctrl+L | comment, select the next match, select the line |
| Alt+Up/Down, Shift+Alt+Up/Down, Ctrl+Shift+K | move, copy, delete lines |
| Ctrl+] / Ctrl+[, Ctrl+Shift+\ | indent / outdent, jump to the matching bracket |
| Ctrl+J, Ctrl+\`, Ctrl+Shift+M | panel, console, problems |
| Ctrl+N, Ctrl+W, Ctrl+Tab | new, close, next tab |
| F1–F12 | Oni's own tabs, as everywhere |

Scripts keep running when F10 is closed or another tab is open. They stop when Oni closes.

Unsaved editor changes live only in the open Studio, so save before closing Oni.
