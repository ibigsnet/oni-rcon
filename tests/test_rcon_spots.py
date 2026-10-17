import asyncio, json, os, time
import pytest
from websockets.asyncio.server import serve
from oni_rcon.rcon import Rcon, host_key


def test_host_key_reads_the_newest_key(tmp_path):
    old, new = tmp_path / "old-name" / "rcon-host.key", tmp_path / "new-name" / "rcon-host.key"
    for p, k in ((old, "a" * 64), (new, "b" * 64)):
        p.parent.mkdir()
        p.write_text(k)
    os.utime(old, (time.time() - 100, time.time() - 100))
    assert host_key(str(tmp_path)) == "b" * 64
    assert host_key("") is None and host_key(str(tmp_path / "missing")) is None
    (tmp_path / "x").mkdir(); (tmp_path / "x" / "rcon-host.key").write_text("not a key!")
    assert host_key(str(tmp_path)) is None             # junk is never sent


def test_full_console_waits_instead_of_giving_up_and_host_key_gets_in(tmp_path):
    (tmp_path / "srv").mkdir(); (tmp_path / "srv" / "rcon-host.key").write_text("k" * 64)
    seen = []

    async def handler(ws):
        msg = json.loads(await ws.recv()); seen.append(msg)
        if msg.get("host_key") == "k" * 64:
            await ws.send(json.dumps({"type": "auth", "ok": True, "version": "0.9.18"}))
            await ws.wait_closed()
        else:
            await ws.send(json.dumps({"type": "auth", "ok": False, "full": True, "error": "The remote console is full"}))

    async def scenario():
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            states = []
            plain = Rcon(f"ws://127.0.0.1:{port}", "pw", "t", lambda *a: None, lambda c, s, d: states.append((s, d)))
            task = asyncio.create_task(plain.run())
            await asyncio.sleep(0.5)
            assert not task.done()                        # still trying: a full console is not a refusal
            assert any(s == "offline" and "console full" in d for s, d in states)
            assert not any(s == "denied" for s, _ in states)
            task.cancel()
            local = Rcon(f"ws://127.0.0.1:{port}", "pw", "t", lambda *a: None, lambda *a: None,
                         host_key_dir=str(tmp_path))
            task = asyncio.create_task(local.run())
            for _ in range(50):
                if local.state == "online":
                    break
                await asyncio.sleep(0.05)
            assert local.state == "online"                 # the server's own spot
            task.cancel()
    asyncio.run(scenario())
    assert seen[-1]["host_key"] == "k" * 64 and "host_key" not in seen[0]
