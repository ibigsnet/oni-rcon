"""`oni-rcon --chat` (`oni chat`): chat and server events from every server in a window of their own.

The full console stays as it is; this is for keeping chat in view beside it (another terminal or window). It signs in
like the console does, shows chat, joins and leaves, kicks, bans, mutes, admin calls and anti-cheat flags as they
come, and keeps the last few days on disk (IP addresses and keys redacted) so it opens with what happened while it
was closed. Type to talk as [Server]: Tab picks which server hears it, `@all ` sends to every one.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Input, RichLog, Static

from .app import KIND, render_event, redact_data, resolve
from .art import CYAN, DIM
from .config import Server
from .findings import chat_request
from .rcon import Rcon, Tunnel

HISTORY_DAYS = 3
HISTORY_LINES = 5000  # on disk; the screen keeps the same
SHOWN = {"chat", "traffic", "moderation"}  # kills are noise here: Ctrl+K shows them too
SIGN_INS = 4


def history_path() -> Path:
    """Next to the console's own state: ~/.local/state/oni-rcon/chat-history.jsonl (or $ONI_RCON_CHAT_HISTORY)."""
    if os.environ.get("ONI_RCON_CHAT_HISTORY"):
        return Path(os.environ["ONI_RCON_CHAT_HISTORY"])
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "oni-rcon" / "chat-history.jsonl"


class ChatHistory:
    """The last HISTORY_DAYS of events, at most HISTORY_LINES, one JSON object per line. Stored redacted."""

    def __init__(self, path: Path, now=time.time):
        self.path, self.now = path, now
        self.rows: deque = deque(maxlen=HISTORY_LINES)
        self.load()

    def fresh(self, row: dict) -> bool:
        return isinstance(row, dict) and self.now() - float(row.get("at", 0)) <= HISTORY_DAYS * 86400

    def load(self) -> None:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines[-HISTORY_LINES:]:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if self.fresh(row) and isinstance(row.get("ev"), dict):
                self.rows.append(row)

    def add(self, label: str, ev: dict) -> dict:
        row = {"at": self.now(), "label": label, "ev": redact_data(ev, True)}
        self.rows.append(row)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, separators=(",", ":")) + "\n")
            if self.path.stat().st_size > HISTORY_LINES * 600:  # rewrite only now and then, never per line
                self.compact()
        except OSError:
            pass  # a read-only home still gets a live window
        return row

    def compact(self) -> None:
        keep = [r for r in self.rows if self.fresh(r)]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in keep), encoding="utf-8")
        os.replace(tmp, self.path)


def category(ev: dict) -> str:
    return KIND.get(str(ev.get("event")), "ops")


def route(text: str, target: str, labels: list[str]) -> tuple[list[str], str]:
    """(servers to send to, message) for what was typed: `@all ` reaches every server, otherwise the target."""
    text = text.strip()
    if text.startswith("@all "):
        return list(labels), text[5:].strip()
    return ([target] if target in labels else []), text


class ChatApp(App):
    CSS = """
    Screen { background: #0B1118; }
    #head { height: 1; padding: 0 1; background: #151F2B; color: #9AD; }
    #log { height: 1fr; border: none; padding: 0 1; }
    #bar { height: 3; }
    #target { width: auto; min-width: 16; padding: 1 1 0 1; color: #5CF; }
    #say { width: 1fr; }
    """
    TITLE = "oni chat"
    BINDINGS = [Binding("tab", "next_target", "Next server", priority=True),
                Binding("ctrl+k", "toggle_kills", "Kills", priority=True),
                Binding("ctrl+q", "quit", "Quit")]

    def __init__(self, servers: list[Server], by: str, history: ChatHistory | None = None, rcon_factory=Rcon):
        super().__init__()
        self.servers, self.by, self.rcon_factory = servers, by, rcon_factory
        self.labels = [s.name or s.where for s in servers]
        self.rcons: dict[str, Rcon] = {}
        self.label_of: dict[Rcon, str] = {}
        self.state: dict[str, str] = {lab: "connecting" for lab in self.labels}
        self.target = self.labels[0] if self.labels else ""
        self.shown = set(SHOWN)
        self.history = history or ChatHistory(history_path())
        self.tunnels: dict[str, Tunnel] = {}
        self.width = min(24, max([10] + [len(x) for x in self.labels]))

    def compose(self) -> ComposeResult:
        yield Static(id="head")
        yield RichLog(id="log", wrap=True, max_lines=HISTORY_LINES, markup=False)
        with Horizontal(id="bar"):
            yield Static(id="target")
            yield Input(id="say", placeholder="» chat as [Server]   ·   Tab: which server   ·   @all : every server")

    def on_mount(self) -> None:
        log = self.query_one("#log", RichLog)
        for row in self.history.rows:
            if category(row["ev"]) in self.shown:
                log.write(self.line(row["label"], row["ev"], earlier=True))
        if self.history.rows:
            log.write(Text("── live ──", DIM))
        self.paint()
        remotes: dict[str, list] = {}
        for s in self.servers:
            if s.ssh and not s.url:
                remotes.setdefault(s.ssh, []).append((s.host, s.port))
        for dest, r in remotes.items():
            self.tunnels[dest] = Tunnel(dest, r, lambda *_: None)
            self.run_worker(self.tunnels[dest].run(), group="tunnels", exit_on_error=False)
        gate = asyncio.Semaphore(SIGN_INS)
        for s, label in zip(self.servers, self.labels):
            ready = None
            if s.url:
                url = s.url
            elif s.ssh:
                tun = self.tunnels[s.ssh]
                url, ready = f"ws://127.0.0.1:{tun.local[(s.host, s.port)]}", tun.ready
            else:
                url = f"ws://[{s.host}]:{s.port}" if ":" in s.host else f"ws://{s.host}:{s.port}"
            extra = {"host_key_dir": s.host_key_dir} if (self.rcon_factory is Rcon and s.host_key_dir
                                                         and not s.url and not s.ssh) else {}
            rcon = self.rcon_factory(url, s.password, self.by, self.on_rcon_event, self.on_rcon_state, ready, gate=gate, **extra)
            self.rcons[label], self.label_of[rcon] = rcon, label
            self.run_worker(rcon.run(), group="rcon", exit_on_error=False)
        self.query_one("#say", Input).focus()

    def line(self, label: str, ev: dict, earlier: bool = False) -> Text:
        text = render_event(label, ev, {}, True, self.width)
        return Text("· ", DIM) + text if earlier else text

    def paint(self) -> None:
        if not self.is_running:
            return
        up = sum(state == "online" for state in self.state.values())
        kills = "  ·  kills shown (Ctrl+K)" if "combat" in self.shown else ""
        self.query_one("#head", Static).update(
            Text(f"CHAT · {up}/{len(self.labels)} servers online{kills}  ·  history {HISTORY_DAYS} days", CYAN))
        self.query_one("#target", Static).update(Text(f"to {self.target[:22]}", CYAN))

    def on_rcon_state(self, rcon: Rcon, state: str, detail: str = "") -> None:
        if rcon in self.label_of:
            self.state[self.label_of[rcon]] = state
            self.paint()

    def on_rcon_event(self, rcon: Rcon, ev: dict) -> None:
        if not self.is_running or rcon not in self.label_of:
            return
        label = self.label_of[rcon]
        ev = resolve(ev, {})
        kind, text = ev.get("event"), str(ev.get("text", ""))
        if category(ev) not in SHOWN | {"combat"}:
            return
        self.history.add(label, ev)
        if category(ev) in self.shown:
            self.query_one("#log", RichLog).write(self.line(label, ev))
        if (kind == "chat" and ev.get("channel") != "server" and chat_request(text)) or kind == "cheat":
            self.bell()
            self.notify(f"{label}: {text[:120]}", title="CALL FOR ADMIN" if kind == "chat" else "ANTI-CHEAT",
                        severity="warning", timeout=10)

    def action_next_target(self) -> None:
        if self.labels:
            self.target = self.labels[(self.labels.index(self.target) + 1) % len(self.labels)]
            self.paint()

    def action_toggle_kills(self) -> None:
        (self.shown.discard if "combat" in self.shown else self.shown.add)("combat")
        self.paint()

    async def on_input_submitted(self, e: Input.Submitted) -> None:
        targets, message = route(e.value, self.target, self.labels)
        e.input.value = ""
        if not message:
            return
        sent = 0
        for label in targets:
            rcon = self.rcons.get(label)
            if not rcon or self.state.get(label) != "online":
                continue
            try:
                reply = await rcon.call("say", message)
                sent += bool(reply.get("ok"))
            except Exception:
                continue
        if not sent:
            self.notify("Not connected: nothing was sent.", severity="warning")
