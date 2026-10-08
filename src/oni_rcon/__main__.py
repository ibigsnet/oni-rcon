"""oni-rcon [targets...] [-c CONFIG] [--ssh DEST] [--by NAME] [--demo] [--setup]"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from . import __version__, update
from .config import Server, default_config, load_config, parse_target, resolve_passwords, user_config

DEMO_HINT = ("Three pretend servers to look around. Click a player, try the buttons, and press ? for a guide. "
             "When you're ready, + ADD SERVER (left) connects your own.")
NEW_HINT = "You're in. Click a player for what you can do, or press ? for a guide."


def main() -> None:
    ap = argparse.ArgumentParser(prog="oni-rcon", description="ONI-themed RCON console for Project Reclaimer servers.",
                                 epilog="With no targets, servers come from the config file (see oni-rcon.example.toml).")
    ap.add_argument("targets", nargs="*", help="PORT, HOST:PORT or ws(s)://URL of a server's RCON")
    ap.add_argument("-c", "--config", help="config file (default: $ONI_RCON_CONFIG, ./oni-rcon.toml, then the user config dir)")
    ap.add_argument("--ssh", metavar="DEST", default="", help="reach the targets through an SSH tunnel to DEST")
    ap.add_argument("--by", help="your name in the server's admin log (default: config `by`, then your login)")
    ap.add_argument("--demo", action="store_true", help="run against three simulated servers, no setup needed")
    ap.add_argument("--setup", action="store_true", help="add servers with the setup screen")
    ap.add_argument("--no-intro", action="store_true", help="skip the boot sequence")
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
            from .demo import start_in_thread
            servers = [Server(port=p, password="demo") for p in start_in_thread()]
            hint = hint or DEMO_HINT
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

        from .app import OniApp
        result = OniApp(servers, by=a.by or cfg_by or login(), intro=not a.no_intro, updater=updater,
                        hint=hint).run()
        if result != "setup":
            return
        setup, updater = True, None  # + ADD SERVER: back to the setup screen, then round again; updates checked once


def login() -> str:
    try:
        return getpass.getuser()
    except Exception:  # no login name to be had, as in some containers
        return "operator"


if __name__ == "__main__":
    main()
