import asyncio
import time
from unittest.mock import AsyncMock, Mock
from textual.widgets import ListView
from oni_rcon.fleet import Fleet, FleetApp, FleetRcon, fleet_state
from oni_rcon.app import Form
from oni_rcon.config import _run
import pytest


def row(i):
    return dict(id=f'node-b-{49000+i}',node='node-b',port=49000+i,name=f'Test {i}',players=0,
                runtime_cap=32,configured_cap=32,health='healthy',fresh=True,node_fresh=True,
                running=True,rcon_up=True,image='official:0.9.8',process_generation='v1',config_hash='cfg')


def test_shell_password_commands_are_refused():
    with pytest.raises(SystemExit):_run('echo no; curl bad.example')


def test_standby_is_distinct_from_empty_lobby_retired_and_stale():
    active=row(1)
    assert active['players']==0 and fleet_state(active,True)[0]=='online'
    spare={**active,'running':False,'fresh':False,'rcon_up':None,'spare':True,'desired_running':False}
    assert fleet_state(spare,True)==('offline','Standby spare — intentionally stopped')
    assert 'state unknown' in fleet_state(spare,False)[1]
    assert 'state unknown' in fleet_state({**spare,'node_fresh':False},True)[1]
    assert 'expected to be running' in fleet_state({**spare,'desired_running':True},True)[1]
    assert 'operator hold' in fleet_state({**spare,'operator_hold':True},True)[1]
    assert 'Retired' in fleet_state(None,True)[1]
    assert 'unavailable' in fleet_state({**active,'running':None},True)[1]


def test_overview_does_not_poll_every_server_native_rcon():
    async def check():
        fleet=Mock(rows={row(i)['id']:row(i) for i in range(1,76)},updated=time.monotonic())
        fleet.read=AsyncMock(return_value={'ok':True,'data':{'players':[]}})
        for ident in fleet.rows:
            adapter=FleetRcon(fleet,'fleet://'+ident,'','tester',lambda *a:None,lambda *a:None)
            reply=await adapter.call('status')
            assert reply['data']['max_players']==32
        fleet.read.assert_not_called()
    asyncio.run(check())


def test_tui_initial_fleet_dynamic_discovery_and_safe_lifecycle_dialog():
    async def check():
        fleet=Mock(rows={row(1)['id']:row(1),row(2)['id']:row(2)},updated=time.monotonic())
        fleet.read=AsyncMock(side_effect=lambda ident,cmd:{'ok':True,'data':{'players':[]} if cmd=='players' else {}})
        fleet.refresh=Mock()
        app=FleetApp(fleet)
        async with app.run_test(size=(155,48)) as pilot:
            await pilot.pause(.5)
            assert len(app.stations)==2
            assert all(call.args[0]==row(1)['id'] for call in fleet.read.call_args_list)
            fleet.rows[row(3)['id']]=row(3)
            app.action_refresh_fleet()
            await pilot.pause(.5)
            assert len(app.stations)==3
            assert len(app.query_one('#stations',ListView).children)==3
            await pilot.press('f6')
            await pilot.pause(.2)
            assert isinstance(app.screen,Form)
            await pilot.press('escape')
            fleet.submit.assert_not_called()
    asyncio.run(check())


def test_mutation_uses_current_generation_and_never_forces(tmp_path):
    async def check():
        key=tmp_path/'key';key.write_text('test-token')
        fleet=Fleet(key);fleet.rows={row(1)['id']:row(1)}
        fleet.refresh=Mock()
        requests=[]
        def request(path,body=None):
            requests.append((path,body))
            if path=='/api/action':return {'job':'once'}
            return {'jobs':[{'id':'once','status':'complete','results':[{'ok':True,'detail':'Applied'}]}]}
        fleet.request=request
        result=await fleet.submit(row(1)['id'],'restart')
        assert result['ok'] and requests[0][1]['force'] is False
        assert requests[0][1]['targets'][0]['process_generation']=='v1'
        assert len([r for r in requests if r[0]=='/api/action'])==1
    asyncio.run(check())
