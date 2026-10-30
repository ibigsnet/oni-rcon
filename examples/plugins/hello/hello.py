"""An Oni plug-in in one file, standard library only. Oni runs it once per request: one JSON object on stdin, one
JSON object back on stdout. Anything on stderr is ignored unless the reply isn't JSON, when its last line is shown."""
import json
import sys
import time
from pathlib import Path

LOG = Path(__file__).with_name("hello.log")  # its own memory between runs: Oni keeps none for it


def remember(line: str) -> None:
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')}  {line}\n")


def view(server) -> dict:
    lines = LOG.read_text(encoding="utf-8").splitlines() if LOG.exists() else []
    return {
        "title": "Hello",
        "badge": {"level": "ok", "text": f"{len(lines)} SAID"},
        "blocks": [
            {"type": "kv", "rows": [["Selected server", server or "none"], ["Python", sys.version.split()[0]]]},
            {"type": "table", "id": "people", "columns": ["Who", "Greeting"],
             "rows": [{"id": who, "cells": [who, f"Hello, {who}!"]} for who in ("Rook", "Static", "[b]not bold[/b]")],
             "row_actions": ["greet"]},
            {"type": "log", "lines": lines[-10:]},
            {"type": "text", "text": "Pick a row and press GREET, or try the actions below."},
            {"type": "chart", "points": [1, 2, 3]},  # a block this Oni doesn't know: drawn as a grey line
        ],
        "actions": [
            {"id": "greet", "label": "Greet"},
            {"id": "form", "label": "Every param", "params": [
                {"name": "words", "label": "Words", "type": "text"},
                {"name": "times", "label": "How many times", "type": "number"},
                {"name": "loud", "label": "Loud", "type": "bool"},
                {"name": "tone", "label": "Tone", "type": "choice", "options": ["warm", "dry", "formal"]},
                {"name": "for", "label": "For how long", "type": "duration"}]},
            {"id": "forget", "label": "Forget everything", "danger": True,
             "confirm": "Clears hello.log. Nothing is sent to any server."},
        ],
    }


def action(req: dict) -> dict:
    act, params, server = req.get("id"), req.get("params") or {}, req.get("server")
    if act == "greet":
        who = req.get("row") or "everyone"
        remember(f"greeted {who} on {server}")
        say = [{"server": server, "command": "say", "args": [f"Hello, {who}!"]}] if server else []
        return {"ok": True, "toast": f"Said hello to {who}.", "rcon": say, "view": view(server)}
    if act == "form":
        remember(f"form: {json.dumps(params)}")
        return {"ok": True, "toast": f"Got {json.dumps(params)}", "view": view(server)}
    if act == "forget":
        LOG.unlink(missing_ok=True)
        return {"ok": True, "toast": "Forgotten.", "view": view(server)}
    return {"ok": False, "error": f"no action {act!r}"}


def main() -> None:
    req = json.loads(sys.stdin.readline() or "{}")
    reply = view(req.get("server")) if req.get("type") == "view" else action(req)
    json.dump(reply, sys.stdout)


if __name__ == "__main__":
    main()
