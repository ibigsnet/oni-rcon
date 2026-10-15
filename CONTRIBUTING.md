# Contributing to oni-rcon (IBIGS)

Thanks for helping out. Contributors are invited: you can read this repository, work in your own fork and send
pull requests, which the maintainer (@ibigsnet) reviews and merges.

## Keep unreleased work in here

oni-rcon is GPL-3.0 and builds on [viik2k/oni-rcon](https://github.com/viik2k/oni-rcon). The IBIGS changes in
this repository go to customers' servers through a signed update channel, so unreleased work stays here until it
ships:

- Don't push this repository, or your branches, to another account or host. Your fork is the only other copy on
  GitHub; GitHub keeps forks of a private repository private.
- Don't hand out builds you make from it. Customers get the signed releases.
- Report security problems to the maintainer privately, not in a public channel.
- Never commit secrets: RCON passwords, Discord webhook URLs, Steam logins, server addresses from a customer's
  console. Tests use the fake servers in `oni_rcon.demo`.

## Workflow

Only the maintainer pushes to `main`, bumps the version and publishes releases (`src/oni_rcon/__init__.py`,
`IBIGS.md`, the signed customer channel).

1. Press **Fork** on GitHub. The fork is private, like this repository.
2. Clone your fork and add this repository as `upstream`:

   ```bash
   git clone git@github.com:<you>/oni-rcon.git
   cd oni-rcon
   git remote add upstream git@github.com:ibigsnet/oni-rcon.git
   ```

3. Start a branch from the latest `main`, named after the work, such as `feature/<topic>` or `fix/<topic>`:

   ```bash
   git fetch upstream
   git switch -c fix/<topic> upstream/main
   ```

4. Make the change **with a test** (see below), commit, push the branch to your fork, and open a pull request
   against `main` here. Fill in the template: what changed, why, and how you tested it.
5. CI runs the tests on Python 3.11, 3.12 and 3.13. A red check means the pull request can't be merged yet.
6. Review comments are normal. Push more commits to the same branch; the pull request updates by itself.
7. To pick up newer work, `git fetch upstream` and rebase onto (or merge) `upstream/main`.

Keep one topic per pull request; small ones get merged faster. Talk to the maintainer before changing anything
under `.github/workflows/` or the update/release code (`src/oni_rcon/update.py`).

## Setup

- Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).
- Run the tests: `uv run pytest -q` (all of them should pass before you open a pull request).
- Try the console without a real server: `uv run oni-rcon --demo` (pretend servers on 127.0.0.1).
- The customer console (F7 start/stop, F8 automation, F9 updates): `uv run python -c "from oni_rcon.tenant import
  TenantApp"` is what the `oni` wrapper on customer servers starts; its tests are `tests/test_tenant_*.py`.

## Writing a good change

- Every behaviour change gets a test in `tests/`. UI tests drive the app with Textual's `run_test()` pilot against
  the fake servers; look at `tests/test_tenant_power.py` for a short example.
- Say what you see on screen in plain words: customers are server owners, not programmers.
- Moderation scope: oni flags cheating for a person to review. It never kicks, bans or mutes by itself, and never
  acts on ordinary chat.
