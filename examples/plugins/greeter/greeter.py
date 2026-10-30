"""An Oni session plug-in in one file, standard library only. Oni starts it with the console and talks JSON lines:
one object per line on stdin (hello, event, rcon-reply, and view/action requests carrying a `ref`), one per line on
stdout (rcon, view, notify, log, and the answer to a request, carrying the same `ref`). It exits when stdin closes,
which is how Oni stops it. Anything on stderr is shown only when it stops: its last line."""
import json
import sys
import time

RULES = "Rules: no team killing, no slurs, have fun."
servers, joins, last_status = [], [], {}
asked = status_id = 0  # its last RCON id, and its last status RCON id: that reply fills "Last status"


def out(msg: dict) -> None:
    print(json.dumps(msg), flush=True)  # one line each; flush, or Oni hears nothing until the buffer fills


def view() -> dict:
    return {
        "title": "Greeter",
        "badge": {"level": "ok", "text": f"{len(joins)} JOINED"},
        "blocks": [
            {"type": "kv", "rows": [["Servers", ", ".join(s["name"] for s in servers) or "none"],
                                    ["Last status", json.dumps(last_status)[:200] or "not asked"]]},
            {"type": "table", "columns": ["Time", "Server", "Player"], "rows": [list(j) for j in joins[-20:][::-1]]},
        ],
        "actions": [{"id": "rules", "label": "Say the rules"}, {"id": "status", "label": "Ask for status"}],
    }


def rcon(server: str, command: str, *args: str) -> int:
    global asked
    asked += 1
    out({"type": "rcon", "id": asked, "server": server, "command": command, "args": list(args)})
    return asked


def handle(m: dict) -> None:
    global servers, last_status, status_id
    t = m.get("type")
    if t == "hello":
        servers = m["servers"]
        out({"type": "log", "text": f"started for {len(servers)} servers; allowed {m['granted']['rcon']}"})
        out({"type": "view", **view()})
    elif t == "event":
        ev, server = m["ev"], m["server"]
        who = str(ev.get("name") or ev.get("player") or "someone")
        if ev.get("event") == "join":
            joins.append((time.strftime("%H:%M"), server, who))
            rcon(server, "say", f"Welcome, {who}! Type !rules for the rules.")
            out({"type": "view", **view()})  # pushed: the panel updates without being asked
        elif ev.get("event") == "chat" and str(ev.get("text", "")).strip().lower() == "!rules":
            rcon(server, "say", RULES)
    elif t == "rcon-reply":
        if not m["ok"]:
            out({"type": "notify", "level": "warning", "text": f"RCON failed: {m['text']}"})
        elif m["id"] == status_id and isinstance(m.get("data"), dict):
            last_status = m["data"]
            out({"type": "view", **view()})
    elif t == "view":
        out({"ref": m["ref"], **view()})
    elif t == "action":
        server = m.get("server")
        if not server:
            return out({"ref": m["ref"], "ok": False, "error": "select a server first"})
        if m["id"] == "rules":
            rcon(server, "say", RULES)
        else:
            status_id = rcon(server, "status")
        out({"ref": m["ref"], "ok": True, "toast": "Sent."})
    elif "ref" in m:
        out({"ref": m["ref"], "error": f"no '{t}' here"})


for line in sys.stdin:  # ends when Oni closes stdin: then it exits
    try:
        handle(json.loads(line))
    except Exception as e:  # one bad message shouldn't end the session
        print(f"greeter: {e!r}", file=sys.stderr, flush=True)
