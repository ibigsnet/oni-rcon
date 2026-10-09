import asyncio
import time
from unittest.mock import AsyncMock, Mock
from textual.widgets import Input, ListView, Select
from oni_rcon.fleet import Fleet, FleetApp, FleetRcon, fleet_state
from oni_rcon.browser import matching_rows, live_count, tagged_message
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


def test_browser_orders_unknown_counts_last_and_searches_map_mode_port():
    a={**row(1),'players':10,'map':'The Pit','mode':'SWAT','name':'Team SWAT'}
    b={**row(2),'players':2,'runtime_cap':2,'map':'Guardian','mode':'Slayer'}
    c={**row(3),'players':90,'node_fresh':False}
    d={**row(4),'running':False,'fresh':False,'players':None,'spare':True,'desired_running':False}
    rows=[c,d,b,a]
    ids=lambda **kw:[r['id'] for r in matching_rows(rows,**kw)]
    assert ids()==[a['id'],b['id'],c['id'],d['id']]
    assert ids(sort='players-asc')==[b['id'],a['id'],c['id'],d['id']]
    assert ids(sort='fill')[:2]==[b['id'],a['id']]
    assert ids(search='node-b "THE PIT" swat')==[a['id']]
    assert ids(search='49002')==[b['id']]
    assert ids(search='"The Pit')==[a['id']]
    assert ids(state='occupied')==[a['id'],b['id']]
    assert ids(state='standby')==[d['id']]
    assert ids(state='attention')==[c['id']]
    assert not ids(state='occupied',snapshot_fresh=False)
    assert live_count(c) is None and live_count(d) is None


def test_browser_filter_sort_refresh_keep_command_target_and_handle_no_matches():
    async def check():
        a={**row(1),'players':4,'map':'The Pit','mode':'SWAT'}
        b={**row(2),'players':12,'map':'Guardian','mode':'Slayer'}
        c={**row(3),'running':False,'fresh':False,'rcon_up':False,'players':None,'spare':True,'desired_running':False}
        fleet=Mock(rows={r['id']:r for r in (a,b,c)},updated=time.monotonic())
        fleet.read=AsyncMock(return_value={'ok':True,'data':{'players':[]}})
        fleet.refresh=Mock()
        app=FleetApp(fleet)
        async with app.run_test(size=(150,44)) as pilot:
            await pilot.pause(.3)
            selected=app.cur
            adapters={s.server.url:s.rcon for s in app.stations}
            assert app.visible_ids==[b['id'],a['id'],c['id']]
            assert selected.rcon.ident==b['id']
            # Changing order preserves the selected identity and connections.
            app.query_one('#fleet-sort',Select).value='players-asc'
            await pilot.pause(.1)
            assert app.visible_ids==[a['id'],b['id'],c['id']] and app.cur is selected
            await pilot.press('ctrl+f')
            assert app.focused.id=='fleet-search'
            app.query_one('#fleet-search',Input).value='swat'
            await pilot.pause(.1)
            assert app.visible_ids==[a['id']] and app.cur is selected
            assert app.query_one('#stations',ListView).index is None
            assert 'outside filter' in app.query_one('#fleet-target').render().plain
            # Only explicit selection, not filtering, changes the RCON target.
            await pilot.press('enter')
            await pilot.pause(.1)
            assert app.cur.rcon.ident==a['id']
            app.query_one('#fleet-search',Input).value='nothing-matches'
            await pilot.pause(.1)
            assert not app.visible_ids and app.cur.rcon.ident==a['id']
            assert 'No matches' in app.query_one('#fleet-target').render().plain
            app.query_one('#stations').focus()
            await pilot.press('down','up','home','end')
            assert app.cur.rcon.ident==a['id']
            app.reset_browser()
            await pilot.pause(.1)
            app.query_one('#fleet-sort',Select).value='players'
            await pilot.pause(.1)
            app.query_one('#stations').focus()
            await pilot.press('1')
            await pilot.pause(.1)
            assert app.cur is selected
            # Registry additions, retirements and changing counts keep selection.
            fresh={**row(4),'node':'node-c','players':24}
            fleet.rows[fresh['id']]=fresh
            del fleet.rows[c['id']]
            fleet.rows[a['id']]={**a,'players':30}
            app.action_refresh_fleet()
            await pilot.pause(.3)
            assert app.visible_ids[:2]==[a['id'],fresh['id']]
            assert app.cur is selected
            assert all(s.rcon is adapters[s.server.url] for s in app.stations if s.server.url in adapters)
            app.query_one('#fleet-state',Select).value='retired'
            await pilot.pause(.1)
            assert app.visible_ids==[c['id']]
            app.action_station(app.stations.index(selected))
            await pilot.pause(.1)
            assert app.cur is selected and b['id'] in app.visible_ids
            fleet.submit.assert_not_called()
    asyncio.run(check())


def test_fleet_input_dispatches_once_and_tags_only_broadcast():
    async def check():
        fleet=Mock(rows={row(1)['id']:row(1)},updated=time.monotonic())
        fleet.read=AsyncMock(return_value={'ok':True,'data':{}})
        fleet.broadcast_tags=AsyncMock(return_value={'ok':True,'text':'Sent'})
        app=FleetApp(fleet)
        factory=Mock(wraps=app.rcon_factory)
        app.rcon_factory=factory
        app.send=Mock()
        app.send_all=Mock()
        async with app.run_test(size=(120,40)) as pilot:
            await pilot.pause(.2)
            assert factory.call_count==1
            await pilot.press('f5')
            command=app.query_one('#cmd',Input)
            command.value='status'
            await pilot.press('enter')
            await pilot.pause(.1)
            assert app.send.call_count==1
            app.send.reset_mock()
            command.value='@all status'
            await pilot.press('enter')
            await pilot.pause(.1)
            app.send.assert_not_called()
            app.send_all.assert_not_called()
            await pilot.press('f2')
            say=app.query_one('#say',Input)
            say.focus();say.value='hello'
            await pilot.press('enter')
            await pilot.pause(.1)
            app.send.assert_not_called()
            assert app.send_all.call_count==1
            sent=app.send_all.call_args.args
            assert sent[1:]==('say','hello') and len(sent[0])==1 and sent[0][0] is app.cur
            app.send_all.reset_mock()
            say.value='@swat @snipers hello'
            await pilot.press('enter')
            await pilot.pause(.1)
            app.send.assert_not_called()
            app.send_all.assert_not_called()
            fleet.broadcast_tags.assert_awaited_once_with(['swat','snipers'],'hello')
            fleet.broadcast_tags.reset_mock()
            await pilot.press('f5')
            command.focus();command.value='@swat say hello again'
            await pilot.press('enter')
            await pilot.pause(.1)
            fleet.broadcast_tags.assert_awaited_once_with(['swat'],'hello again')
            app.send.assert_not_called()
            app.send_all.assert_not_called()
    asyncio.run(check())


def test_tag_parsing_search_and_union_broadcast_skip_stale_stopped(tmp_path):
    assert tagged_message('@SWAT @snipers @swat Hello there') == (['swat','snipers'],'Hello there')
    for invalid in ('@swat','@bad_tag hello','@swat @snipers','@ hello'):
        with pytest.raises(ValueError): tagged_message(invalid)
    a={**row(1),'tags':['swat','snipers']}
    b={**row(2),'tags':['snipers']}
    c={**row(3),'tags':['swat'],'node_fresh':False}
    d={**row(4),'tags':['swat'],'running':False,'spare':True,'desired_running':False}
    assert [r['id'] for r in matching_rows([a,b],search='@swat')]==[a['id']]
    assert [r['id'] for r in matching_rows([a,b],search='@swat @snipers')]==[a['id']]
    async def check():
        key=tmp_path/'key';key.write_text('test-token')
        fleet=Fleet(key);fleet.rows={r['id']:r for r in (a,b,c,d)}
        fleet.updated=time.monotonic();fleet.refresh=Mock()
        fleet.submit_many=AsyncMock(return_value={'ok':True})
        await fleet.broadcast_tags(['swat','snipers'],'hello')
        fleet.submit_many.assert_awaited_once_with([a['id'],b['id']],'broadcast','hello',refresh=False)
        fleet.submit_many.reset_mock()
        with pytest.raises(ValueError):await fleet.broadcast_tags(['typo'],'hello')
        fleet.submit_many.assert_not_called()
        fleet.rows={r['id']:r for r in (c,d)}
        assert not (await fleet.broadcast_tags(['swat'],'hello'))['ok']
        fleet.submit_many.assert_not_called()
    asyncio.run(check())
