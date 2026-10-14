import asyncio, json, time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'pkg'))
if not os.path.isdir(os.path.join(os.path.dirname(__file__), 'pkg', 'reclaimer_control')):
    import pytest
    pytest.skip('needs a copy of the live reclaimer_control package in pkg/ (see ../README.md)', allow_module_level=True)
import pytest
from reclaimer_control import fleet_rules as FR

A, C = 'a' * 64, 'c' * 64
HOOK = "https://discord.com/api/webhooks/123456789012345678/" + "A" * 68


def doc(enabled=True):
    return {'enabled': enabled, 'webhooks': {'alerts': HOOK}, 'roles': {B: {'name': 'Bravo', 'roles': ['vip']}},
            'rules': [{'id': 'w', 'name': 'Welcome', 'when': {'type': 'join'},
                       'then': [{'type': 'tell_player', 'message': 'Welcome {player}!'}]},
                      {'id': 'a', 'name': 'Admin call', 'when': {'type': 'command', 'command': '!admin'},
                       'then': [{'type': 'tell_admins', 'where': 'any', 'message': '{player} on {server}: {args}'},
                                {'type': 'discord', 'webhook': 'alerts', 'message': '{player} needs help'}],
                       'cooldown': 0},
                      {'id': 's', 'name': 'Start', 'when': {'type': 'command', 'command': '!start'},
                       'then': [{'type': 'start_server', 'server': 'node-a-49178'}], 'cooldown': 0}]}

B = 'b' * 64


def test_configure_checks_and_stores(tmp_path):
    out = FR.configure(tmp_path, {'doc': doc(False)})
    assert out['ok'] and out['rules'] == 3 and out['enabled'] is False
    assert json.loads((tmp_path / 'rules' / 'policy.json').read_text())['enabled'] is False
    for bad in ({'doc': {**doc(), 'enabled': 'yes'}}, {'doc': {**doc(), 'rules': [{'when': {'type': 'kick'}}]}},
                {'doc': {**doc(), 'webhooks': {'x': 'https://evil.example/hook'}}},
                {'doc': {**doc(), 'roles': {'nope': {'roles': ['admin']}}}}, {'doc': 'x' * 70000}):
        with pytest.raises(ValueError):
            FR.configure(tmp_path, bad)
    st = FR.status(tmp_path)
    assert st['ok'] and st['enabled'] is False and st['signature'] == out['signature']


def test_fps_from_snapshot_lags():
    now = time.time()
    assert FR.fps_of({}) is None
    assert FR.fps_of({'perf': {'lags': []}}) == 60.0
    assert round(FR.fps_of({'perf': {'lags': [[now - 30, 59, 59, 36, 144, 65]]}})) == 36
    assert FR.fps_of({'perf': {'lags': [[now - 600, 59, 59, 36, 144, 65]]}}) == 60.0


def test_worker_off_by_default_then_runs_rules_over_a_real_connection(tmp_path, monkeypatch):
    from websockets.asyncio.server import serve
    seen, peers = [], []
    posted = []

    async def handler(ws):
        json.loads(await ws.recv())
        await ws.send(json.dumps({'type': 'auth', 'ok': True}))
        peers.append(ws)
        async for raw in ws:
            c = json.loads(raw)
            data = {'players': 2, 'max_players': 16, 'phase': 'playing'} if c['command'] == 'status' else \
                {'players': [{'engine_id': 1, 'name': 'Alpha', 'id': A}, {'engine_id': 2, 'name': 'Bravo', 'id': B}]} \
                if c['command'] == 'players' else None
            if not data:
                seen.append((c['command'], c['args'], c['by']))
            await ws.send(json.dumps({'type': 'reply', 'id': c['id'], 'ok': True, **({'data': data} if data else {})}))

    async def go():
        async with serve(handler, '127.0.0.1', 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            from reclaimer_control.common import unix_time
            gen = 'cid:' + str(unix_time('1970-01-01T00:01:40Z'))
            node = {'id': 'node-a', 'operator_enabled': True, 'fleet_defaults': {'admins': [C]},
                    'instances': [{'id': 'node-a-49178', 'enabled': True}, {'id': 'node-a-49179', 'enabled': True}]}
            snap = {'observed_at': time.time(), 'lobbies': [
                {'id': 'node-a-49178', 'running': True, 'fresh': True, 'rcon_up': True, 'process_generation': gen,
                 'desired_running': True},
                {'id': 'node-a-49179', 'running': True, 'fresh': True, 'rcon_up': True, 'process_generation': 'x:1',
                 'desired_running': True, 'operator_hold': False}]}
            (tmp_path / 'snapshot.json').write_text(json.dumps(snap))
            monkeypatch.setattr(FR.Worker, 'configured', lambda self: node)
            monkeypatch.setattr(FR, 'Docker', lambda: None)
            import reclaimer_control.team_shuffle_runtime as tsr
            monkeypatch.setattr(tsr, 'connection', lambda docker, item:
                                ({'Id': 'cid', 'State': {'StartedAt': '1970-01-01T00:01:40Z'}},
                                 f'ws://127.0.0.1:{port}/', 'pw'))
            import reclaimer_control.oni_rules.rules_run as RR
            monkeypatch.setattr(RR, 'post_discord', lambda url, text, timeout=10: posted.append(text) or 204)
            node['instances'][1]['operator_hold'] = True  # held: never connected to
            FR.configure(tmp_path, {'doc': doc(False)})
            w = FR.Worker(tmp_path)
            w.runner.poster = RR.post_discord
            await w.step()
            assert not w.tasks and json.loads((tmp_path / 'rules' / 'health.json').read_text())['enabled'] is False
            FR.configure(tmp_path, {'doc': doc(True)})
            for _ in range(100):
                await w.step()
                if peers and w.clients.get('node-a-49178') and w.clients['node-a-49178'].ws:
                    break
                await asyncio.sleep(.02)
            assert list(w.tasks) == ['node-a-49178']
            await asyncio.sleep(.2)
            send = lambda **ev: peers[0].send(json.dumps({'type': 'event', 'time': time.time(), **ev}))
            await send(event='join', name='Alpha', player_id=A)
            await send(event='chat', name='Alpha', text='!admin flying guy', player_id=A)
            await send(event='chat', name='Alpha', text='!start', player_id=A)
            await send(event='vote', text='Alpha called a vote to kick Bravo')  # Bravo is VIP
            for _ in range(100):
                await asyncio.sleep(.02)
                if len(seen) >= 3 and posted:
                    break
            await asyncio.sleep(.2)
            cmds = [(c, a) for c, a, _ in seen]
            assert ('tell', [A, 'Welcome Alpha!']) in cmds
            assert ('cancelvote', []) in cmds and ('say', ["Bravo is a VIP and can't be vote-kicked."]) in cmds
            assert posted == ['Alpha needs help']
            assert all(by == 'IBIGS Rules' for _, _, by in seen)
            hist = json.loads((tmp_path / 'rules' / 'rules-state.json').read_text())['history']
            assert any("can't start servers from here" in h['result'] for h in hist)  # lifecycle stays with the controller
            # the fleet admin (C) isn't on this lobby: the admin call told nobody in game, but Discord got it
            # switching off closes the connection
            FR.configure(tmp_path, {'doc': doc(False)})
            await w.step()
            await asyncio.sleep(.1)
            assert not w.tasks
    asyncio.run(go())
