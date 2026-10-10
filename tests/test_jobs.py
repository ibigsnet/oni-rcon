import asyncio
from datetime import datetime
import json
import time
from zoneinfo import ZoneInfo

import pytest

from oni_rcon.jobs import MatchEvents, Store, Worker, cron_due, occurrence, scheduled_targets, validate


def job(**changes):
    return validate(dict(id='test', name='Evening reminder', action='broadcast', target='h3-01', trigger='cron',
                         schedule='0 20 * * 5', timezone='America/New_York', message='Welcome!', enabled=True,
                         delay=0, **changes)) if not changes else validate({**job(), **changes})


def test_cron_ranges_steps_weekdays_and_calendar_semantics():
    at = datetime(2026, 10, 9, 20, 0, tzinfo=ZoneInfo('America/New_York'))
    assert cron_due('0 20 * * 5', at)
    assert not cron_due('0 19 * * 5', at)
    assert cron_due('*/15 18-22 * 9,10 1-5', at)
    assert cron_due('0 20 1 * 5', at)  # weekday OR day-of-month when both are restricted
    assert not cron_due('0 20 1 * *', at)
    assert cron_due('0 20 * * 0,7', at.replace(day=11))


def test_dst_repeated_minute_has_one_claim_and_gap_is_not_replayed():
    j = job(schedule='30 1 * * *')
    first = datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo('America/New_York'), fold=0)
    second = first.replace(fold=1)
    assert first.timestamp() != second.timestamp()
    assert occurrence(j, first.timestamp(), {}) == occurrence(j, second.timestamp(), {})
    assert occurrence(job(schedule='30 2 * * *'), datetime(2026, 3, 8, 3, 0, tzinfo=ZoneInfo('America/New_York')).timestamp(), {}) is None


@pytest.mark.parametrize('changes', [
    {'schedule':'* * * * * /bin/sh'}, {'schedule':'60 * * * *'}, {'schedule':'*/0 * * * *'},
    {'schedule':'0 0 0 * *'}, {'timezone':'../../etc/passwd'}, {'action':'shell'},
    {'message':'hello\nquit'}, {'message':'\x1b[31mhello'}, {'target':'h3-01;id'},
    {'trigger':'after_match', 'action':'restart'}, {'trigger':'before_match','action':'shuffle'},
    {'trigger':'interval','schedule':'0'}, {'enabled':'true'}, {'delay':True},
])
def test_invalid_jobs_cannot_run(changes):
    with pytest.raises(ValueError):
        job(**changes)


def test_match_events_ignore_reconnect_round_break_and_unknown_phase():
    events = MatchEvents()
    assert events.observe({'phase':'finished'}) == []
    assert events.observe({'phase':'no_game','playlist_vote':{'active':False}}) == []
    assert events.observe({'phase':'no_game','playlist_vote':{'active':True}}) == ['before_match']
    assert events.observe({'phase':'no_game','playlist_vote':{'active':True}}) == []
    assert events.observe({'phase':'playing'}) == ['match_start']
    assert events.observe({'phase':'round_over'}) == []
    assert events.observe({'phase':'between_rounds'}) == []
    assert events.observe({'phase':'playing'}) == []
    assert events.observe({'phase':'finished'}) == ['after_match']
    assert events.observe({'phase':'finished'}) == []
    events.observe({'phase':'playing'})
    events.observe({})
    assert events.observe({'phase':'finished'}) == []


def test_fixed_map_before_match_is_sent_once_and_boolean_ballots_are_safe():
    events = MatchEvents()
    assert events.observe({'phase':'no_game','playlist_vote':False}) == []
    assert events.observe({'phase':'loading'}) == ['before_match']
    assert events.observe({'phase':'starting'}) == []
    assert events.observe({'phase':'playing'}) == ['match_start']


def test_interval_skips_a_missed_window():
    j = job(trigger='interval',schedule='5')
    assert occurrence(j, 900, {}) == '3'
    assert occurrence(j, 904, {}) == '3'
    assert occurrence(j, 960, {}) is None


def test_store_preserves_jobs_and_refuses_corrupt_policy(tmp_path):
    store = Store(tmp_path)
    store.change(job())
    store.change(job(id='second', action='shuffle', message=''))
    store.change({**store.load()[0], 'enabled':False})
    assert len(store.load()) == 2
    store.change(delete='second')
    assert store.load()[0]['enabled'] is False
    store.path.write_text('{broken')
    with pytest.raises(ValueError):
        store.load()


def test_all_lobbies_power_action_uses_one_guarded_batch():
    names = ['h3-01', 'h3-02']
    assert scheduled_targets(job(action='stop', target='all'), names) == ['all']
    assert scheduled_targets(job(action='broadcast', target='all'), names) == names
    assert scheduled_targets(job(action='restart'), names) == ['h3-01']


def test_group_member_can_edit_root_created_config_lock(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.change(job())
    monkeypatch.setattr('oni_rcon.jobs.os.geteuid', lambda: -1)
    store.change(job(name='Updated from tenant login'))
    assert store.load()[0]['name'] == 'Updated from tenant login'


class FakeClient:
    def __init__(self, store, phase='playing', fail=False):
        self.ws = True
        self.calls = []
        self.store, self.phase, self.fail = store, phase, fail

    async def call(self, command, *args, **kwargs):
        assert self.store.state()['claims']  # claim was durable before even the read/command
        self.calls.append((command, args))
        if self.fail:
            raise TimeoutError('uncertain')
        return {'ok':True, 'data':{'phase':self.phase}}


def test_durable_claim_prevents_duplicate_and_unknown_reply_retry(tmp_path):
    async def go():
        store=Store(tmp_path); j=job(); store.change(j)
        worker=Worker(store); fake=FakeClient(store, fail=True)
        worker.clients['h3-01']=fake; worker.trackers['h3-01']=MatchEvents()
        worker.queue(j,'h3-01','minute')
        await asyncio.sleep(.03)
        worker.queue(j,'h3-01','minute')
        await asyncio.sleep(.03)
        assert len(fake.calls)==1
        restarted=Worker(store); restarted.clients['h3-01']=fake
        restarted.queue(j,'h3-01','minute')
        await asyncio.sleep(.03)
        assert len(fake.calls)==1
        assert 'uncertain' in store.state()['history'][-1]['result']
    asyncio.run(go())


def test_pause_during_delay_and_offline_lobby_do_not_send(tmp_path):
    async def go():
        store=Store(tmp_path); j=job(delay=1); store.change(j)
        worker=Worker(store); fake=FakeClient(store)
        worker.clients['h3-01']=fake
        task=asyncio.create_task(worker.execute(j,'h3-01','minute'))
        await asyncio.sleep(.01)
        store.change({**j,'enabled':False})
        await task
        assert fake.calls==[]
        j=job(); store.change(j); worker.clients.clear()
        await worker.execute(j,'h3-01','next-minute')
        assert 'offline' in store.state()['history'][-1]['result']
    asyncio.run(go())


def test_shuffle_checks_real_phase_and_automatic_restart_never_forces(tmp_path, monkeypatch):
    calls=[]
    monkeypatch.setattr('oni_rcon.jobs.run_fixed', lambda argv: calls.append(argv) or 4)
    async def go():
        store=Store(tmp_path); j=job(action='shuffle'); store.change(j)
        worker=Worker(store); fake=FakeClient(store,phase='no_game')
        worker.clients['h3-01']=fake; worker.trackers['h3-01']=MatchEvents()
        await worker.execute(j,'h3-01','a')
        assert [c[0] for c in fake.calls]==['status']
        j=job(action='restart'); store.change(j)
        await worker.execute(j,'h3-01','b')
        assert calls==[['/usr/local/bin/oni','restart','h3-01','--yes']]
        assert 'Deferred' in store.state()['history'][-1]['result']
    asyncio.run(go())


def test_native_websocket_boundaries_drive_only_the_selected_lobby(tmp_path):
    from websockets.asyncio.server import serve

    async def go():
        store = Store(tmp_path)
        for identity, trigger, action, message in [('before', 'before_match', 'broadcast', 'Good luck!'),
                                                  ('after', 'after_match', 'broadcast', 'Good game!'),
                                                  ('shuffle', 'match_start', 'shuffle', '')]:
            store.change(job(id=identity, trigger=trigger, action=action, message=message))
        status = {'phase': 'no_game', 'playlist_vote': {'active': False}}
        seen, peers = [], []

        async def handler(ws):
            auth = json.loads(await ws.recv())
            assert auth == {'type': 'auth', 'password': 'isolated-test'}
            await ws.send(json.dumps({'type': 'auth', 'ok': True, 'version': '0.9.11'}))
            peers.append(ws)
            async for raw in ws:
                command = json.loads(raw)
                if command['command'] != 'status':
                    seen.append((command['command'], command['args']))
                await ws.send(json.dumps({'type': 'reply', 'id': command['id'], 'ok': True, 'data': dict(status)}))

        async with serve(handler, '127.0.0.1', 0) as server:
            port = server.sockets[0].getsockname()[1]
            worker = Worker(store, discover=lambda: [dict(name='h3-01',host='127.0.0.1',port=port,password='isolated-test')])
            worker.jobs = store.load()
            await worker.refresh_clients()
            for _ in range(100):
                if worker.clients['h3-01'].ws:
                    break
                await asyncio.sleep(.01)
            client = worker.clients['h3-01']
            assert client.ws
            await worker.poll('h3-01', client)
            status['playlist_vote']['active'] = True
            await worker.poll('h3-01', client)
            await asyncio.sleep(.05)
            status['phase'] = 'playing'
            await peers[0].send(json.dumps({'type':'event','event':'phase','phase':'playing','time':time.time()}))
            await asyncio.sleep(.05)
            status['phase'] = 'round_over'
            await peers[0].send(json.dumps({'type':'event','event':'phase','phase':'round_over','time':time.time()}))
            await asyncio.sleep(.02)
            assert ('say',['Good game!']) not in seen
            status['phase'] = 'finished'
            await peers[0].send(json.dumps({'type':'event','event':'phase','phase':'finished','time':time.time()}))
            await asyncio.sleep(.05)
            await worker.poll('h3-01', client)
            await asyncio.sleep(.05)
            assert seen == [('say',['Good luck!']),('shuffle',[]),('say',['Good game!'])]
            state_text = store.state_path.read_text()
            assert 'isolated-test' not in state_text and 'Good luck!' not in state_text
            for task in worker.tasks.values():
                task.cancel()
            await asyncio.gather(*worker.tasks.values(),return_exceptions=True)
    asyncio.run(go())


def test_late_status_reply_cannot_replay_match_boundaries(tmp_path):
    async def go():
        worker = Worker(Store(tmp_path))
        worker.observe('h3-01', {'phase': 'no_game'})
        worker.observe('h3-01', {'phase': 'playing'})
        seen = []
        worker.jobs = [job(trigger='after_match')]
        worker.queue = lambda *args: seen.append(args)

        class LateStatus:
            ws = True

            async def call(self, *args, **kwargs):
                worker.event('h3-01', {'event': 'phase', 'phase': 'finished', 'time': time.time()})
                return {'ok': True, 'data': {'phase': 'playing'}}

        await worker.poll('h3-01', LateStatus())
        worker.observe('h3-01', {'phase': 'finished'})
        assert len(seen) == 1
        assert worker.trackers['h3-01'].phase == 'finished'
    asyncio.run(go())
