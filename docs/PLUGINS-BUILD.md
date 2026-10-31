# Building the plug-in framework: brief for the implementing agent

Read [PLUGINS.md](PLUGINS.md) first. It is the contract. This file says what to build, in what order, where it goes, and
how to know it is done. If the spec is wrong or silent on something you hit, **change the spec in the same commit** and say so;
don't let the code drift from it.

## Scope

Build milestones **M1–M2 only** unless told otherwise. Everything else is later.

| | What | Repo |
|---|---|---|
| **M1** | Plug-in core for the Python console: manifest, discovery, trust, run a service plug-in, validate and normalise replies, forward `rcon` | oni-rcon |
| **M2** | PLUGINS tab (F10) in the Textual console, palette entries, CONDITION badge, `--demo` plug-in, `examples/plugins/hello` | oni-rcon |
| M3 | `gryphon oni` and the daemon state file (spec: `gryphon/docs/ONI.md`) | gryphon |
| M4 | Same host in the desktop app (`plugins.rs` + React panel), passing the same golden cases | oni-rcon |
| M5 | Session mode (stdio, events in, RCON out) | oni-rcon |

M3 is independent of M1–M2 and can run in parallel; it only needs the two docs.

## Not in scope

No install/update/marketplace. No sandbox. No plug-in-supplied code in Oni's process. No HTML/Markdown/rich text from a
plug-in. No new RCON connection for a plug-in. No changes to automation (F9) or Health. No git remote work, no release, no
version bump, no commit unless asked.

## Ground rules

- **Reuse before writing.** Local-or-ssh running is `health.run` (`src/oni_rcon/health.py:452`); copy its shape (SSH options
  from `install.SSH_OPTS`, `scrub` for error text) but the plug-in runner needs stdin as a pipe, so it is its own function in
  `plugins.py`. Find and use the existing address-redaction helper; do not write a second.
- **Config keys are top-level only** (`config.py` `forge_settings`/`health_settings` are the pattern). An older Oni exits on an
  unknown key inside `[defaults]` or `[[server]]`.
- **`Prefs` clobbers unknown keys on save** (`prefs.py`). Fix that first; trust lives there.
- **stdlib only.** No new Python dependency. `tomllib`, `hashlib`, `asyncio`, `json`.
- **Never run a plug-in without recorded trust**, and never at import time or start-up before the user can see the prompt.
- **Nothing a plug-in sends is markup.** Wrap in `rich.text.Text(...)`, never an f-string into a markup widget.
- Match the surrounding code: dense docstrings explaining *why*, no framework, tests in plain pytest like `tests/test_health.py`.

## Where things go (M1–M2)

| File | Change |
|---|---|
| `src/oni_rcon/plugins.py` (new) | `Manifest` (parse + validate `oni-plugin.toml`), `discover(dir, enabled)`, `trust_hash(path)`, `async call(plugin, request, timeout)`, `normalise_view(raw)`, `normalise_action(raw, manifest, servers)` |
| `src/oni_rcon/config.py` | `plugin_settings(path)` returning the top-level `plugins` / `plugin_*` keys; a bad value stops start with a message, like `health_settings` |
| `src/oni_rcon/prefs.py` | keep unknown keys on save; add `plugins_trusted: dict[str, str]` |
| `src/oni_rcon/app.py` | F10 PLUGINS pane in `compose()`; strip of plug-in names with badges; blocks renderer; palette entries; confirm dialog for `danger`/`confirm` actions (reuse the existing one, default button ABORT); CONDITION hook for `warn`/`alert` badges; refresh timers |
| `src/oni_rcon/oni.tcss` | styles for the new pane |
| `src/oni_rcon/demo.py` | `--demo` ships a fake in-process plug-in so the tab works with nothing installed |
| `examples/plugins/hello/` (new) | stdlib-only service plug-in (`oni-plugin.toml` + one script) that exercises every block type and every param type |
| `tests/test_plugins.py` (new) | the golden cases below |
| `README.md` | one short Plug-ins section linking to `docs/PLUGINS.md` |

## Golden cases (M1 must pass all; M4 must pass the same)

Write each as a test with a tiny fake plug-in script (a Python one-liner that prints canned stdout) so nothing needs ssh.

| # | Situation | Expected |
|---|---|---|
| 1 | Valid manifest, trusted, valid `view` reply | Panel model with title, badge and blocks as sent |
| 2 | Plug-in folder not named in `plugins = [...]` | Not listed, never run |
| 3 | Listed but no recorded trust | Listed as "needs approval", **not run**; approving records the hash |
| 4 | Manifest changed after approval | Treated as untrusted again until re-approved |
| 5 | `api = 2` and Oni supports 1 | Listed, disabled, reason shown |
| 6 | Manifest missing a required field / bad TOML | Listed with the reason; other plug-ins unaffected |
| 7 | Unknown block type `chart` | Grey line "this version of Oni can't draw a 'chart' block"; rest of panel drawn |
| 8 | Unknown extra keys anywhere | Ignored |
| 9 | stdout not JSON, exit 0 | Error shown, previous view kept greyed |
| 10 | stdout valid JSON, exit 1 | The JSON wins |
| 11 | No stdout, exit 3, stderr last line contains an IP | Error "<last line>" with the address scrubbed |
| 12 | Never exits | Killed at the timeout (10 s view, 30 s action); "no answer in 10s" |
| 13 | 600 table rows / 20 000-char string; stdout over 1 MiB | Truncated to the caps, "+100 more" line, no error; over 1 MiB is an error ("its reply is over 1 MiB") |
| 14 | A string containing `[bold red]x[/]` or `<b>` | Drawn literally |
| 15 | Action reply asks for `rcon` command not in `permissions.rcon` | Dropped, toast says so; nothing sent |
| 16 | Action reply asks for `unmute` on a server name not in the config | Dropped |
| 17 | Permitted `rcon` request | Sent through Oni's own link; command log line `plug-in <id> ▸ unmute …` |
| 18 | `{"error": "..."}` view / `{"ok": false, "error": "..."}` action | Greyed panel / toast, no traceback |
| 19 | Action with `danger` or `confirm` | Confirm dialog first; Esc or ABORT sends nothing |
| 20 | Badge `warn` / `alert` / `ok` / `off` | CONDITION amber / red / unchanged / unchanged, and drops back when the badge does |
| 21 | `plugins` key inside a `[[server]]` | Ignored (older Oni compatibility is what the top-level rule protects) |
| 22 | `prefs.json` already holds other keys | Survive a trust write |

## Done means

1. `uv run pytest` and the repo's linter pass; the new tests are the table above.
2. `oni-rcon --demo` shows a working PLUGINS tab with no setup.
3. **Run the real thing, not just the fixtures:** with `examples/plugins/hello` installed and enabled, the console draws every
   block, runs an action, shows a toast and honours a `danger` confirm. (And, once M3 exists, point it at a real `gryphon oni`
   and check the table shows the rows actually in `gryphon-actions.jsonl`. A panel that renders is not proof it shows the
   right data.)
4. `docs/PLUGINS.md` matches what you built; the "Status" line says what exists.
5. Report what you didn't verify (ssh path on Windows, a real remote host) instead of implying it.

## Defaults already decided (don't re-ask)

Out-of-process only. Service mode before session mode. Console before desktop. Trust is per front-end. Plug-ins are
explicit opt-in per config and per manifest hash. F10 is the tab. MIT for Gryphon, GPL for Oni, joined only by JSON.
