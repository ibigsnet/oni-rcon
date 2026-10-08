"""oni-rcon [targets...] [-c CONFIG] [--ssh DEST] [--by NAME] [--demo]"""
from __future__ import annotations

import argparse
import getpass
import sys

from . import __version__, update
from .config import Server, default_config, load_config, parse_target, resolve_passwords, user_config


def main() -> None:
    ap = argparse.ArgumentParser(prog="oni-rcon", description="ONI-themed RCON console for Project Reclaimer servers.",
                                 epilog="With no targets, servers come from the config file (see oni-rcon.example.toml).")
    ap.add_argument("targets", nargs="*", help="PORT, HOST:PORT or ws(s)://URL of a server's RCON")
    ap.add_argument("-c", "--config", help="config file (default: $ONI_RCON_CONFIG, ./oni-rcon.toml, then the user config dir)")
    ap.add_argument("--ssh", metavar="DEST", default="", help="reach the targets through an SSH tunnel to DEST")
    ap.add_argument("--by", help="your name in the server's admin log (default: config `by`, then your login)")
    ap.add_argument("--demo", action="store_true", help="run against three simulated servers, no setup needed")
    ap.add_argument("--no-intro", action="store_true", help="skip the boot sequence")
    ap.add_argument("-V", "--version", action="version", version=f"oni-rcon {__version__}")
    a = ap.parse_args()

    cfg_by, path = "", None
    if not (a.demo or a.targets):
        path = a.config or default_config()
        if not path:
            if not sys.stdin.isatty():
                ap.error("no targets and no config file; try `oni-rcon --demo`, or `oni-rcon HOST:PORT` with the game port")
            # a double-clicked exe gets no arguments: offer the demo instead of an error in a window that closes
            try:
                input(f"No servers set up yet. Put them in {user_config()} (see the README), or run oni-rcon HOST:PORT.\n"
                      "Press Enter to look around the demo, or close this window. ")
            except (EOFError, KeyboardInterrupt):
                return
            a.demo = True
    if a.demo:
        from .demo import start_in_thread
        servers = [Server(port=p, password="demo") for p in start_in_thread()]
    elif a.targets:
        try:
            servers = [parse_target(t, ssh=a.ssh) for t in a.targets]
        except ValueError as e:
            ap.error(str(e))
    else:
        cfg_by, servers = load_config(path)
    resolve_passwords(servers)

    from .app import OniApp
    OniApp(servers, by=a.by or cfg_by or login(), intro=not a.no_intro, updater=update.check).run()


def login() -> str:
    try:
        return getpass.getuser()
    except Exception:  # no login name to be had, as in some containers
        return "operator"


if __name__ == "__main__":
    main()
