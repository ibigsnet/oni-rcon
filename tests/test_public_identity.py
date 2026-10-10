import asyncio
import json

from websockets.asyncio.server import serve

from oni_rcon.__main__ import login
from oni_rcon.rcon import Rcon


def test_default_identity_is_neutral_on_the_wire(monkeypatch):
    monkeypatch.setenv('USER', 'root')
    monkeypatch.setenv('LOGNAME', 'root')
    assert login() == 'Administrator'
    async def exercise():
        received = []

        async def server(ws):
            await ws.recv()
            await ws.send(json.dumps({'type': 'auth', 'ok': True, 'version': 'test'}))
            async for raw in ws:
                command = json.loads(raw)
                received.append(command)
                await ws.send(json.dumps({'type': 'reply', 'id': command['id'], 'ok': True}))

        async with serve(server, '127.0.0.1', 0) as listener:
            port = listener.sockets[0].getsockname()[1]
            ready = asyncio.Event()
            rcon = Rcon(f'ws://127.0.0.1:{port}', 'test-only', login(), lambda *_: None,
                        lambda _, state, __: ready.set() if state == 'online' else None)
            task = asyncio.create_task(rcon.run())
            try:
                await asyncio.wait_for(ready.wait(), 3)
                await rcon.call('kick', 'test-player', 'test reason')
                assert received[0]['by'] == 'Administrator'
                assert received[0]['args'] == ['test-player', 'test reason']
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
