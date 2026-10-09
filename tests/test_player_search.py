import asyncio
import time
from unittest.mock import AsyncMock, Mock

from textual.widgets import DataTable, Input, Select
from oni_rcon.fleet import FleetApp
from oni_rcon.player_search import PlayerSearch, matching_players, coverage_text
from oni_rcon.browser import matching_rows


def server(i, node='node-b', **changes):
    return dict(id=f'{node}-{i}',node=node,port=49000+i,name=f'Server {i}',players=2,
                runtime_cap=4,configured_cap=4,health='healthy',fresh=True,node_fresh=True,
                running=True,rcon_up=True,image='official:0.9.9',process_generation='v1',config_hash='cfg',**changes)


def directory(rows):
    now=time.time()
    servers=[{**r,'state':'ready','observed_at':now} for r in rows]
    return {'at':now,'servers':servers,'players':[
        {'name':'[red]Player','id':str(i+1)*64,'server':r} for i,r in enumerate(servers)],
        'coverage':{'total':len(rows),'checked':len(rows),'unavailable':0,'nodes':len({r['node'] for r in rows})}}


def test_duplicate_player_names_keep_separate_ids_and_nodes_and_stale_locations_disappear():
    d=directory([server(1),server(2,'node-c')])
    assert len(matching_players(d,'PLAYER'))==2
    assert len(matching_players(d,'[red]'))==2
    assert len(matching_players(d,'2'*10))==1
    d['players'][0]['server']['observed_at']-=40
    assert len(matching_players(d,'player'))==1
    assert 'unavailable/stale' in coverage_text(d,1)
    assert not matching_players(d,'nothing')


def test_all_current_servers_includes_forge_spares_dynamic_and_empty_but_not_retired():
    rows=[server(1),server(2,spare=True),{**server(3),'players':0},
          {**server(4),'running':False,'spare':True},server(5,_retired=True)]
    assert len(matching_rows(rows))==4
    assert [r['id'] for r in matching_rows(rows,state='retired')]==[rows[-1]['id']]


def test_player_search_ignores_server_filter_and_selection_verifies_the_exact_id():
    async def check():
        rows=[server(1),server(2,'node-c')];d=directory(rows)
        fleet=Mock(rows={r['id']:r for r in rows},updated=time.monotonic())
        fleet.refresh=Mock();fleet.request=Mock(return_value=d)
        fleet.read=AsyncMock(side_effect=lambda ident,cmd:{'ok':True,'data':{'players':[
            {'id':p['id'],'name':p['name']} for p in d['players'] if p['server']['id']==ident]} if cmd=='players' else {}})
        app=FleetApp(fleet)
        async with app.run_test(size=(150,46)) as pilot:
            await pilot.pause(.3)
            app.query_one('#fleet-node',Select).value='node-b'
            await pilot.press('ctrl+g');await pilot.pause(.3)
            assert isinstance(app.screen,PlayerSearch)
            assert app.screen.query_one('#player-results',DataTable).row_count==2
            app.screen.query_one('#player-search',Input).value='2'*10
            await pilot.pause(.1)
            assert app.screen.query_one('#player-results',DataTable).row_count==1
            await pilot.press('enter','enter');await pilot.pause(.5)
            assert not isinstance(app.screen,PlayerSearch)
            assert app.cur.rcon.ident==rows[1]['id']
            assert app.query_one('#fleet-node',Select).value=='all'
            fleet.submit.assert_not_called()
    asyncio.run(check())


def test_search_failure_removes_previously_visible_locations():
    async def check():
        r=server(1);fleet=Mock(rows={r['id']:r},updated=time.monotonic())
        fleet.read=AsyncMock(return_value={'ok':True,'data':{'players':[]}});fleet.refresh=Mock()
        fleet.request=Mock(return_value=directory([r]));app=FleetApp(fleet)
        async with app.run_test(size=(90,34)) as pilot:
            await pilot.press('ctrl+g');await pilot.pause(.3)
            assert app.screen.query_one('#player-results',DataTable).row_count==1
            fleet.request.side_effect=ValueError('offline')
            await pilot.press('ctrl+r');await pilot.pause(.3)
            assert app.screen.query_one('#player-results',DataTable).row_count==0
            assert 'unavailable' in app.screen.query_one('#player-coverage').render().plain.lower()
            await pilot.press('escape')
            fleet.submit.assert_not_called()
    asyncio.run(check())
