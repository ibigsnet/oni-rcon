import asyncio
import json

from oni_rcon.chat import HISTORY_DAYS, ChatApp, ChatHistory, route
from oni_rcon.config import Server
from oni_rcon.demo import serve_fakes


def test_history_keeps_days_redacts_and_survives_restart(tmp_path):
    clock = [1_000_000.0]
    path = tmp_path / "h.jsonl"
    h = ChatHistory(path, now=lambda: clock[0])
    h.add("SWAT", {"event": "chat", "name": "Bravo", "text": "gg"})
    h.add("SWAT", {"event": "refused", "name": "Rook", "ip": "203.0.113.9", "text": "banned"})
    stored = path.read_text()
    assert "203.0.113.9" not in stored  # addresses never reach the disk
    clock[0] += HISTORY_DAYS * 86400 - 10
    again = ChatHistory(path, now=lambda: clock[0])
    assert [r["ev"]["event"] for r in again.rows] == ["chat", "refused"]
    clock[0] += 20  # past the window: the old lines are gone on the next start
    assert list(ChatHistory(path, now=lambda: clock[0]).rows) == []


def test_history_ignores_garbage_and_compacts(tmp_path):
    path = tmp_path / "h.jsonl"
    path.write_text('not json\n{"at": 1}\n' + json.dumps({"at": 5, "label": "A", "ev": {"event": "chat"}}) + "\n")
    h = ChatHistory(path, now=lambda: 10.0)
    assert len(h.rows) == 1
    h.compact()
    assert len(path.read_text().splitlines()) == 1


def test_route_target_or_everyone():
    labels = ["SWAT", "Chaos"]
    assert route("gg all", "SWAT", labels) == (["SWAT"], "gg all")
    assert route("@all server restarts soon", "SWAT", labels) == (labels, "server restarts soon")
    assert route("hi", "gone", labels) == ([], "hi")


def test_chat_window_against_fakes(tmp_path):
    async def go():
        ports, keep = await serve_fakes(tick=False)
        servers = [Server(port=p, password="demo", name=f"S{i}") for i, p in enumerate(ports)]
        app = ChatApp(servers, by="pytest", history=ChatHistory(tmp_path / "h.jsonl"))
        async with app.run_test(size=(120, 30)) as pilot:
            for _ in range(100):
                await pilot.pause(0.05)
                if all(state == "online" for state in app.state.values()):
                    break
            assert all(state == "online" for state in app.state.values())
            rcon = app.rcons["S1"]
            app.on_rcon_event(rcon, {"type": "event", "event": "chat", "name": "Rook", "channel": "all",
                                "text": "is there an admin on?"})
            app.on_rcon_event(rcon, {"type": "event", "event": "kill", "killer": "a", "victim": "b"})
            log = app.query_one("#log")
            text = "\n".join(str(line.text) for line in log.lines)
            assert "is there an admin on?" in text and "S1" in text
            assert len(app.history.rows) == 2  # kills are kept, just not shown unless asked
            await pilot.press("tab")
            assert app.target == "S1"
            await pilot.click("#say")
            for ch in "hello":
                await pilot.press(ch)
            await pilot.press("enter")
            for _ in range(40):
                await pilot.pause(0.05)
                if any("hello" in str(r["ev"].get("text", "")) for r in app.history.rows):
                    break
            assert any(r["label"] == "S1" and "hello" in str(r["ev"].get("text", "")) for r in app.history.rows)
            await pilot.press("ctrl+k")
            assert "combat" in app.shown
    asyncio.run(go())
