"""oni-rcon [targets...] [-c CONFIG] [--ssh DEST] [--by NAME] [--demo] [--setup]"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from . import __version__, update
from .config import (Server, default_config, forge_settings, health_settings, load_config, parse_target,
                     plugin_settings, resolve_passwords, user_config)
from .forge import ForgeSetup, Secret, cache_root, load_key
from .health import HealthSetup
from .plugins import PluginSetup, discover, enabled
from .prefs import Prefs

DEMO_HINT = ("Three pretend servers to look around, and a pretend ReclaimerForge on F6. Click a player, try the "
             "buttons, and press ? for a guide. When you're ready, + ADD SERVER (left) connects your own.")
NEW_HINT = "You're in. Click a player for what you can do, or press ? for a guide."


def main() -> None:
    if sys.argv[1:2] == ['--ibigs-jobs']:
        from .jobs import main as jobs_main
        raise SystemExit(jobs_main(sys.argv[2:]))
    ap = argparse.ArgumentParser(prog="oni-rcon", description="ONI-themed RCON console for Project Reclaimer servers.",
                                 epilog="With no targets, servers come from the config file (see oni-rcon.example.toml).")
    ap.add_argument("targets", nargs="*", help="PORT, HOST:PORT or ws(s)://URL of a server's RCON")
    ap.add_argument("-c", "--config", help="config file (default: $ONI_RCON_CONFIG, ./oni-rcon.toml, then the user config dir)")
    ap.add_argument("--ssh", metavar="DEST", default="", help="reach the targets through an SSH tunnel to DEST")
    ap.add_argument("--by", help="public administrator name shown to players (default: config `by`, then Administrator)")
    ap.add_argument("--demo", action="store_true", help="run against three simulated servers, no setup needed")
    ap.add_argument("--setup", action="store_true", help="add servers with the setup screen")
    ap.add_argument("--intro", choices=["full", "quick", "off"],
                    help="the boot animation this time: full (the long Halo archive), quick (the short splash), or off "
                         "(default: full the first time, quick after; Ctrl+P changes that)")
    ap.add_argument("--no-intro", action="store_true", help="skip the boot sequence (--intro off)")
    ap.add_argument("-V", "--version", action="version", version=f"oni-rcon {__version__}")
    a = ap.parse_args()

    setup, demo, targets, updater = a.setup, a.demo, a.targets, update.check
    typed: dict[str, str] = {}  # passwords given to the setup screen but not saved: good for this session
    while True:
        hint = ""
        cfg_by, path = "", Path(a.config) if a.config else default_config()
        if setup or not (demo or targets or path):
            if not sys.stdin.isatty():
                ap.error("no targets and no config file; try `oni-rcon --demo`, or `oni-rcon HOST:PORT` with the game port")
            from .wizard import setup as run_setup
            existing = []
            if path:
                cfg_by, existing = load_config(path)
            got, by = run_setup(path or user_config(), existing, a.by or cfg_by or login(),
                                ask_by=not (path or a.by))
            if got is None:
                return
            setup = False
            if got == "demo":
                demo, hint = True, DEMO_HINT
            else:  # the servers were added to the config; passwords not kept there are only in memory
                demo, targets, hint, path = False, [], NEW_HINT, path or user_config()
                typed.update({s.where: s.password for s in got})
                a.by = a.by or by
        if demo:
            from .demo import FakeHost, start_in_thread
            from .forgefake import start_demo
            scratch = Path(tempfile.mkdtemp(prefix="oni-rcon-demo-"))
            folders = [scratch / f"content-{i + 1}" for i in range(3)]  # each pretend server loads from its own
            box = FakeHost()  # a pretend game box too: crashes and a memory squeeze for F7
            servers = [Server(port=p, password="demo", content_dir=str(d))
                       for p, d in zip(start_in_thread(content_dirs=folders, host=box), folders)]
            hint = hint or DEMO_HINT
            url, key = start_demo()  # a pretend Forge too, with a pretend key: the real one is never touched
            forge = ForgeSetup(Secret(key), "the demo", url=url, poll=30, state_dir=scratch, cache_dir=scratch)
        elif targets:
            try:
                servers = [parse_target(t, ssh=a.ssh) for t in targets]
            except ValueError as e:
                ap.error(str(e))
        else:
            cfg_by, servers = load_config(path)
            for s in servers:
                s.password = s.password or typed.get(s.where, "")
        resolve_passwords(servers)
        if demo:
            health = HealthSetup(service="blueflame-demo", source=box)
            from .demo import DemoPlugin
            plugins = DemoPlugin.setup()
        else:
            forge, health, plugins = forge_setup(path), health_setup(path), plugin_setup(path)

        from .app import OniApp
        if os.environ.get('IBIGS_ONI_TENANT') == '1':
            from .tenant import TenantApp
            app_class = TenantApp
        else:
            app_class = OniApp
        prefs = Prefs.load(user_config().with_name("prefs.json"))
        if app_class is not OniApp:  # IBIGS customer console: no plug-ins (they run programs as the console's user)
            plugins = PluginSetup()
        result = app_class(servers, by=a.by or cfg_by or login(), updater=updater, hint=hint, forge=forge, prefs=prefs,
                           intro="off" if a.no_intro else a.intro or prefs.intro_mode(), health=health,
                           plugins=plugins, reclaimer=None if demo else update.reclaimer_latest).run()
        if result != "setup":
            return
        setup, updater = True, None  # + ADD SERVER: back to the setup screen, then round again; updates checked once


def forge_setup(path: Path | None) -> ForgeSetup:
    """Your own ReclaimerForge key, and where Forge things are kept. No key: the console runs as ever, without it."""
    # Generated tenant inventory is replaced each launch; keys/preferences belong in a persistent per-user file.
    if os.environ.get('IBIGS_ONI_TENANT') == '1' and os.environ.get('ONI_RCON_FORGE_CONFIG'):
        path = Path(os.environ['ONI_RCON_FORGE_CONFIG'])
    settings = forge_settings(path)
    found = load_key(settings)
    try:
        poll = max(60.0, float(settings.get("forge_poll", 600)))  # the key's quota is shared with browsing F6
    except (TypeError, ValueError):
        poll = 600.0
    return ForgeSetup(found.key, found.source, found.error, poll=poll, state_dir=user_config().parent,
                      cache_dir=cache_root(), config=path)


def health_setup(path: Path | None) -> HealthSetup:
    """The config's top-level health_* keys. A bad one stops the start with a message, rather than running without."""
    try:
        return HealthSetup.from_settings(health_settings(path))
    except ValueError as e:
        raise SystemExit(f"{path}: {e}") from None


def plugin_setup(path: Path | None) -> PluginSetup:
    """The plug-ins the config turns on, read from plugins/ next to it. Only read here: none runs until it's approved
    on F10."""
    try:
        ids = enabled(plugin_settings(path))
    except ValueError as e:
        raise SystemExit(f"{path}: {e}") from None
    return PluginSetup(discover(path.parent / "plugins" if path else None, ids))


def login() -> str:
    # RCON attributes moderation announcements to this name, not just private logs.
    # A login or hostname is not an appropriate public-facing default.
    return "Administrator"


if __name__ == "__main__":
    main()
