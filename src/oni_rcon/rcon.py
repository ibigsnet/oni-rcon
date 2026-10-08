"""Project Reclaimer RCON over its WebSocket protocol, and the SSH tunnels that reach it.

Protocol (server 0.9.x): send {"type":"auth","password"}, get {"type":"auth","ok",...,"server","version"}; then
{"type":"command","command","args":[...],"id","by"} -> {"type":"reply","id","ok","text","data"?}. The server also
pushes {"type":"event","event":"chat"|"kill"|"join"|...} to every signed-in tool.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import shutil
import socket
from typing import Callable

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException


class Rcon:
    """One signed-in connection per server, shared by commands and the event stream: servers admit only 4 tools."""

    def __init__(self, url: str, password: str, by: str, on_event: Callable, on_state: Callable,
                 ready: asyncio.Event | None = None):
        self.url, self.password, self.by = url, password, by
        self.on_event, self.on_state, self.ready = on_event, on_state, ready
        self.state, self.detail, self.info = "connecting", "", {}
        self.ws = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}

    def _set(self, state: str, detail: str = "") -> None:
        self.state, self.detail = state, detail
        self.on_state(self, state, detail)

    async def run(self) -> None:
        """Connect, sign in and pump messages; reconnect with backoff. Returns only when the sign-in is refused."""
        delay = 2
        while True:
            if self.ready and not self.ready.is_set():
                self._set("connecting", "awaiting SSH tunnel")
                await self.ready.wait()
            self._set("connecting")
            try:
                async with connect(self.url, proxy=None, open_timeout=10, max_size=None) as ws:
                    await ws.send(json.dumps({"type": "auth", "password": self.password}))
                    reply = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    if not isinstance(reply, dict):
                        raise ValueError("that port doesn't speak RCON")
                    if not reply.get("ok"):
                        # Never retry a refused sign-in: 5 wrong passwords in 10 min lock the address out.
                        self._set("denied", reply.get("error") or reply.get("text") or "sign-in refused")
                        return
                    self.info, self.ws, delay = reply, ws, 2
                    self._set("online", f"v{reply.get('version', '?')}")
                    async for raw in ws:
                        if isinstance(msg := json.loads(raw), dict):
                            self._dispatch(msg)
                    detail = "connection closed"
            except (OSError, TimeoutError, WebSocketException, ValueError) as e:
                detail = str(e) or type(e).__name__
            except Exception as e:  # a fault handling a message: drop the link and come back, rather than die online
                detail = f"{type(e).__name__}: {e}"
            finally:
                self.ws = None
                for f in self._pending.values():
                    if not f.done():
                        f.set_exception(ConnectionError("connection lost"))
                self._pending.clear()
            self._set("offline", f"{detail}; retry in {delay}s")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    def _dispatch(self, msg: dict) -> None:
        if msg.get("type") == "reply":
            f = self._pending.pop(msg.get("id"), None)
            if f and not f.done():
                f.set_result(msg)
        elif msg.get("type") == "event":
            self.on_event(self, msg)
        else:  # {"type":"error"} for a malformed message, or anything newer than this client
            self.on_event(self, {**msg, "type": "event", "event": msg.get("type", "unknown")})

    async def call(self, command: str, *args, timeout: float = 10.0) -> dict:
        """Run one command; returns the reply dict ({"ok","text","data"?}). Never retried: commands aren't idempotent."""
        if not self.ws:
            raise ConnectionError(self.detail or self.state)
        id_ = next(self._ids)
        fut = self._pending[id_] = asyncio.get_running_loop().create_future()
        try:
            await self.ws.send(json.dumps({"type": "command", "command": command, "args": [str(a) for a in args],
                                           "id": id_, "by": self.by}))
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(id_, None)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Tunnel:
    """One `ssh -N -L ...` per destination, forwarding every server behind it; restarted if ssh exits."""

    def __init__(self, dest: str, remotes: list[tuple[str, int]], on_state: Callable):
        self.dest, self.on_state = dest, on_state
        self.local = {r: free_port() for r in dict.fromkeys(remotes)}  # (host, port) -> local port
        self.ready = asyncio.Event()
        self.state, self.detail = "opening", ""

    def _set(self, state: str, detail: str = "") -> None:
        self.state, self.detail = state, detail
        self.on_state(self, state, detail)

    async def run(self) -> None:
        ssh = shutil.which("ssh")
        if not ssh:
            self._set("down", "no ssh client on PATH")
            return
        args = [ssh, "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]
        for (host, port), local in self.local.items():
            args += ["-L", f"127.0.0.1:{local}:{host}:{port}"]
        args.append(self.dest)
        delay = 2
        while True:
            self._set("opening")
            proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL,
                                                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            try:
                probe = asyncio.create_task(self._probe(proc))
                err = (await proc.stderr.read()).decode(errors="replace").strip()
                await proc.wait()
                probe.cancel()
            finally:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
            self.ready.clear()
            if self.state == "up":
                delay = 2
            self._set("down", f"{err.splitlines()[-1] if err else f'ssh exited {proc.returncode}'}; retry in {delay}s")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    async def _probe(self, proc) -> None:
        """ssh listens locally only once it has signed in, so a local port accepting means the tunnel is up."""
        port = next(iter(self.local.values()))
        while proc.returncode is None:
            try:
                _, w = await asyncio.open_connection("127.0.0.1", port)
                w.close()
                self._set("up")
                self.ready.set()
                return
            except OSError:
                await asyncio.sleep(0.3)
