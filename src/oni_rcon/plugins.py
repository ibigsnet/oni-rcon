"""Plug-ins: separate programs that describe their own panel in JSON. The contract is docs/PLUGINS.md.

A plug-in is never code in this process. Its folder holds an `oni-plugin.toml`; the config's top-level
`plugins = [...]` names the folders that are on, and the user approves each one against a hash of its manifest before
it ever runs. A service plug-in is run once per request, here or over ssh the way the Health tab runs health_cmd,
with one JSON request on stdin and one JSON reply on stdout.

Everything a plug-in sends is untrusted data: it is cut to the caps below and shaped into a fixed vocabulary here,
so the console only ever draws plain strings from a known set of block types.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .health import scrub
from .install import SSH_OPTS

API = 1  # the protocol version this Oni speaks
VIEW_TIMEOUT, ACTION_TIMEOUT = 10.0, 30.0
MAX_OUT = 1 << 20  # bytes of stdout read; more is an error, because a cut JSON object doesn't parse
MAX_ROWS = 500  # rows of a table (and of a kv or log block); the rest become one "+N more" line
MAX_STR = 10_000  # characters per string
MAX_ACTIONS, MAX_PARAMS = 20, 10
ID = re.compile(r"[a-z0-9-]+")
BLOCKS = ("kv", "table", "text", "log")
LEVELS = ("ok", "warn", "alert", "off")
PARAM_TYPES = ("text", "number", "bool", "choice", "duration")
MANIFEST = "oni-plugin.toml"


class PluginError(Exception):
    """Why a plug-in gave no usable answer, in words with no address in them."""


@dataclass
class Manifest:
    id: str  # the folder name
    hash: str = ""  # sha256 of the manifest's bytes: what an approval is recorded against
    name: str = ""
    version: str = ""
    author: str = ""
    description: str = ""
    mode: str = "service"
    exec: str = ""
    host: str = ""
    events: list = field(default_factory=list)
    rcon: list = field(default_factory=list)
    addresses: bool = False
    error: str = ""  # why it can't run at all (a bad manifest, a newer api); it's still listed, with this
    folder: Path | None = None  # where it was found: a local one runs there, so exec can name a file beside it

    @property
    def title(self) -> str:
        return self.name or self.id


def trust_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(folder: Path) -> Manifest:
    """One plug-in's manifest. Never raises: what's wrong with it goes in `error`, so it's listed with the reason
    and the others are unaffected."""
    m = Manifest(folder.name, folder=folder)
    path = folder / MANIFEST
    try:
        m.hash = trust_hash(path)
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError:
        m.error = f"no {MANIFEST} in {folder}"
        return m
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        m.error = f"{MANIFEST} isn't valid TOML: {e}"
        return m
    run, perms = data.get("run"), data.get("permissions", {})
    api = data.get("api")
    if not isinstance(api, int) or isinstance(api, bool):
        m.error = "the manifest has no api number"
    elif not isinstance(run, dict) or not isinstance(perms, dict):
        m.error = "the manifest needs a [run] table, and [permissions] must be a table"
    else:
        for k in ("name", "version", "author", "description"):
            setattr(m, k, str(data.get(k, ""))[:200])
        m.mode, m.exec, m.host = run.get("mode", "service"), run.get("exec"), run.get("host", "")
        m.events, m.rcon, m.addresses = perms.get("events", []), perms.get("rcon", []), perms.get("addresses", False)
        if api > API:
            m.error = f"needs plug-in api {api}; this version of Oni speaks api {API}"
        elif not m.name:
            m.error = "the manifest has no name"
        elif m.mode not in ("service", "session"):
            m.error = 'run.mode must be "service" or "session"'
        elif m.mode == "session":
            m.error = "session plug-ins aren't supported by this version of Oni yet"
        elif not isinstance(m.exec, str) or not m.exec.strip():
            m.error = "run.exec must be the command line to run"
        elif not isinstance(m.host, str):
            m.error = 'run.host must be an ssh destination, or "" for this machine'
        elif not (all(isinstance(x, str) for x in m.events) and isinstance(m.events, list)
                  and isinstance(m.rcon, list) and all(isinstance(x, str) for x in m.rcon)):
            m.error = "permissions.events and permissions.rcon must be lists of names"
        elif not isinstance(m.addresses, bool):
            m.error = "permissions.addresses must be true or false"
    return m


def discover(folder: Path | None, enabled: list[str]) -> list[Manifest]:
    """The plug-ins the config turns on, in its order. A folder it doesn't name is never read, and a name that isn't
    a plain folder name (so can't climb out of plugins/) never becomes a path."""
    return [Manifest(pid, error="a plug-in id must be lower-case letters, digits and dashes") if not ID.fullmatch(pid)
            else load(folder / pid) if folder else Manifest(pid, error="no config file, so no plugins folder")
            for pid in dict.fromkeys(enabled)]


def enabled(settings: dict) -> list[str]:
    """The config's `plugins = [...]`, or ValueError when it isn't a list of folder names."""
    ids = settings.get("plugins", [])
    if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
        raise ValueError('plugins must be a list of plug-in folder names, such as plugins = ["gryphon"]')
    return ids


def state(m: Manifest, trusted: dict) -> str:
    """Why a plug-in won't run, or "" when it will: the manifest's own fault first, then a missing approval (also
    when the manifest changed since it was given)."""
    if m.error:
        return m.error
    return "" if trusted.get(m.id) == m.hash else "needs approval"


# --- running one ---------------------------------------------------------------------------------------------------
async def _read(stream, cap: int, tail: bool = False) -> bytes:
    """Up to cap+1 bytes of a pipe (so over the cap shows), or with `tail` its last `cap` bytes, read to the end so a
    chatty stderr never blocks the process."""
    buf = b""
    while chunk := await stream.read(65536):
        buf = (buf + chunk)[-cap:] if tail else buf + chunk
        if not tail and len(buf) > cap:
            break
    return buf


async def call(m: Manifest, request: dict, timeout: float) -> dict:
    """Run a service plug-in once: one JSON line in, one JSON object out. Valid JSON wins whatever the exit code;
    otherwise PluginError with the last stderr line, scrubbed of addresses."""
    if m.host:
        if not (exe := shutil.which("ssh")):
            raise PluginError("no ssh client on PATH")
        proc = await asyncio.create_subprocess_exec(exe, *SSH_OPTS, m.host, m.exec, stdin=asyncio.subprocess.PIPE,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    else:
        proc = await asyncio.create_subprocess_shell(m.exec, cwd=m.folder, stdin=asyncio.subprocess.PIPE,
                                                     stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    async def exchange() -> tuple[bytes, bytes]:
        errs = asyncio.ensure_future(_read(proc.stderr, 4096, tail=True))
        with contextlib.suppress(ConnectionError):  # one that doesn't read its request can still answer
            proc.stdin.write(json.dumps(request).encode() + b"\n")
            await proc.stdin.drain()
        proc.stdin.close()
        out = await _read(proc.stdout, MAX_OUT)
        if len(out) > MAX_OUT:
            errs.cancel()
            raise PluginError(f"its reply is over {MAX_OUT >> 20} MiB")
        err = await errs
        await proc.wait()
        return out, err

    try:
        out, err = await asyncio.wait_for(exchange(), timeout)
    except TimeoutError:
        raise PluginError(f"no answer in {timeout:g}s") from None
    finally:
        if proc.returncode is None:  # ponytail: on Windows this kills the shell, not a child it started
            proc.kill()
            await proc.wait()
    text = out.decode("utf-8", errors="replace").strip()
    with contextlib.suppress(ValueError):
        if isinstance(reply := json.loads(text), dict):
            return reply
    last = err.decode("utf-8", errors="replace").strip().splitlines()
    raise PluginError(scrub(last[-1][:300]) if last else "its reply isn't JSON" if text and not proc.returncode
                      else f"exited {proc.returncode}")


# --- shaping what it sent ------------------------------------------------------------------------------------------
def s(v) -> str:
    """Any value as a plain string within the cap: never markup, which the console makes sure of when drawing."""
    return ("" if v is None else v if isinstance(v, str) else json.dumps(v))[:MAX_STR]


def _rows(v) -> tuple[list, int]:
    rows = v if isinstance(v, list) else []
    return rows[:MAX_ROWS], max(0, len(rows) - MAX_ROWS)


def _block(b) -> dict | None:
    if not isinstance(b, dict):
        return None
    t = b.get("type")
    if t not in BLOCKS:
        return {"type": "unknown", "name": s(t)[:40]}
    if t == "text":
        return {"type": "text", "text": s(b.get("text"))}
    rows, more = _rows(b.get("lines") if t == "log" else b.get("rows"))
    if t == "log":
        return {"type": "log", "lines": [s(x) for x in rows], "more": more}
    if t == "kv":
        return {"type": "kv", "rows": [[s(r[0]), s(r[1] if len(r) > 1 else "")] for r in rows
                                       if isinstance(r, list) and r], "more": more}
    cols = [s(c) for c in b.get("columns", [])[:50]] if isinstance(b.get("columns"), list) else []
    out = []
    for r in rows:
        cells = r.get("cells") if isinstance(r, dict) else r
        if isinstance(cells, list):
            out.append({"id": s(r.get("id")) if isinstance(r, dict) else "", "cells": [s(c) for c in cells[:50]]})
    acts = b.get("row_actions") if isinstance(b.get("row_actions"), list) else []
    return {"type": "table", "id": s(b.get("id")), "columns": cols, "rows": out, "more": more,
            "row_actions": [s(a) for a in acts[:MAX_ACTIONS]]}


def _param(p) -> dict | None:
    if not isinstance(p, dict) or not p.get("name"):
        return None
    t = p.get("type") if p.get("type") in PARAM_TYPES else "text"
    opts = [s(o) for o in p.get("options", [])[:100]] if isinstance(p.get("options"), list) else []
    if t == "choice" and not opts:
        t = "text"
    return {"name": s(p["name"])[:100], "label": s(p.get("label") or p["name"])[:200], "type": t, "options": opts}


def _action(a) -> dict | None:
    if not isinstance(a, dict) or not a.get("id"):
        return None
    params = a.get("params") if isinstance(a.get("params"), list) else []
    return {"id": s(a["id"])[:100], "label": s(a.get("label") or a["id"])[:200], "danger": a.get("danger") is True,
            "confirm": s(a.get("confirm")), "params": [x for p in params[:MAX_PARAMS] if (x := _param(p))]}


def normalise_view(raw) -> dict:
    """A view reply in the one shape the console draws: unknown keys gone, strings cut, rows capped. A reply that
    is `{"error": ...}`, or isn't an object, comes back with `error` set and nothing else."""
    if not isinstance(raw, dict):
        return {"error": "its reply isn't a JSON object"}
    if raw.get("error"):
        return {"error": s(raw["error"])[:500]}
    badge = raw.get("badge") if isinstance(raw.get("badge"), dict) else None
    blocks = raw.get("blocks") if isinstance(raw.get("blocks"), list) else []
    actions = raw.get("actions") if isinstance(raw.get("actions"), list) else []
    return {"title": s(raw.get("title"))[:200],
            "badge": {"level": badge["level"] if badge.get("level") in LEVELS else "off",
                      "text": s(badge.get("text"))[:40]} if badge else None,
            "blocks": [x for b in blocks if (x := _block(b))],
            "actions": [x for a in actions[:MAX_ACTIONS] if (x := _action(a))]}


def normalise_action(raw, m: Manifest, servers: list[str]) -> dict:
    """An action reply: whether it worked, what to toast, a view to replace the panel with, and the RCON commands it
    asked for split into those Oni will send (named in the manifest's permissions, on a server in the config) and
    `dropped`, the reasons for the rest."""
    if not isinstance(raw, dict):
        return {"ok": False, "toast": "", "error": "its reply isn't a JSON object", "view": None, "rcon": [], "dropped": []}
    ok = raw.get("ok") is not False and not raw.get("error")
    out = {"ok": ok, "toast": s(raw.get("toast"))[:500], "error": s(raw.get("error"))[:500], "rcon": [], "dropped": [],
           "view": normalise_view(raw["view"]) if isinstance(raw.get("view"), dict) else None}
    asks = raw.get("rcon") if isinstance(raw.get("rcon"), list) else []
    for r in asks[:50]:
        if not isinstance(r, dict):
            continue
        cmd, server = s(r.get("command"))[:40], s(r.get("server"))[:200]
        args = [s(a)[:500] for a in r.get("args", [])[:20]] if isinstance(r.get("args"), list) else []
        if cmd not in m.rcon:
            out["dropped"].append(f"{cmd or 'a command'}: not in its permissions")
        elif server not in servers:
            out["dropped"].append(f"{cmd} on {server or 'no server'}: no such server in the config")
        else:
            out["rcon"].append({"server": server, "command": cmd, "args": args})
    return out


def server_name(server) -> str:
    """How a plug-in names a server: its `name` in the config, or its address when it has none. Not the label the
    console shows, which changes once the server reports its own name."""
    return server.name or server.where


@dataclass
class PluginSetup:
    """The plug-ins the config turns on. `source` stands in for running them, for --demo and the tests:
    async call(manifest, request, timeout), and every one it serves counts as approved."""
    plugins: list[Manifest] = field(default_factory=list)
    source: object = None
