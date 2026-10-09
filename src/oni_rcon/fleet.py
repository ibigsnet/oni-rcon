"""IBIGS fleet adapter. Game passwords stay in the existing management layer."""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import shlex
import time
import urllib.error
import urllib.request
from textual import on, work
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, ListView, Select, Static, TabbedContent
from rich.text import Text
from .app import OniApp, Station, Confirm, Form, Roster, target_of
from .player_search import PlayerSearch, matching_players, coverage_text
from .browser import (SORTS, STATES, ServerItem, ServerList, ServerSearch, live_count, matching_rows,
                      server_tags, tagged_message)
from .config import Server

READS = frozenset('help status players maps modes nextmap vote bans vpn'.split())
DISRUPTIVE = frozenset('map mode load endgame endround shuffle teamcount servername'.split())
BASE = 'http://127.0.0.1:9190'


def fleet_state(row, snapshot_fresh):
    if row is None:
        return 'offline', 'Retired — no longer in the fleet registry'
    if not snapshot_fresh or not row.get('node_fresh'):
        return 'offline', 'Node telemetry stale — server state unknown'
    if row.get('running') is False:
        if row.get('operator_hold'):
            return 'offline', 'Stopped — operator hold'
        if row.get('spare') and row.get('desired_running') is False:
            return 'offline', 'Standby spare — intentionally stopped'
        if row.get('desired_running'):
            return 'offline', 'Stopped — expected to be running'
        return 'offline', 'Stopped'
    if row.get('running') is not True:
        return 'offline', 'Server process state unavailable'
    if not row.get('fresh') or not row.get('rcon_up'):
        return 'offline', 'Process running — waiting for fresh RCON telemetry'
    return 'online', 'Private fleet management'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ValueError('management redirects are disabled')


class Fleet:
    def __init__(self, token_file='/run/secrets/terminal.key'):
        self.token = Path(token_file).read_text().strip()
        self.opener=urllib.request.build_opener(NoRedirect(),urllib.request.ProxyHandler({}))
        self.view, self.rows, self.updated, self.locks = {}, {}, 0, {}

    def request(self, path, body=None, timeout=25):
        if path not in ('/api/fleet','/api/jobs','/api/action','/api/console-read','/api/players'):
            raise ValueError('unsupported management route')
        headers = {'Authorization':'Bearer '+self.token,'Accept':'application/json'}
        if body is not None:
            headers.update({'Content-Type':'application/json','Origin':self.view['origin'],
                            'X-CSRF-Token':self.view['csrf']})
        request=urllib.request.Request(BASE+path,headers=headers,
                                       data=json.dumps(body).encode() if body is not None else None)
        try:
            with self.opener.open(request,timeout=timeout) as response:
                data=response.read(4*1024*1024+1)
            if len(data)>4*1024*1024:raise ValueError('management response too large')
            return json.loads(data)
        except urllib.error.HTTPError as exc:
            try:reason=json.loads(exc.read(4096)).get('error','management request refused')
            except (ValueError,AttributeError):reason='management request refused'
            finally:exc.close()
            raise ValueError(str(reason)) from None

    def refresh(self):
        view=self.request('/api/fleet')
        if not isinstance(view.get('lobbies'),list):raise ValueError('invalid fleet snapshot')
        self.view=view;self.rows={r['id']:r for r in view['lobbies']};self.updated=time.monotonic()
        return view

    async def read(self, ident, command):
        row=self.rows[ident]
        lock=self.locks.setdefault(row['node'],asyncio.Lock())
        async with lock:
            row=self.rows[ident]
            body={k:row[k] for k in ('process_generation','config_hash')}
            body.update(instance=ident,command=command)
            result=await asyncio.to_thread(self.request,'/api/console-read',body)
        if not result.get('ok'):return {'ok':False,'text':result.get('error','read unavailable')}
        return result

    async def submit(self, ident, operation, command=None):
        return await self.submit_many([ident], operation, command)

    async def broadcast_tags(self,tags,message):
        await asyncio.to_thread(self.refresh)
        known={tag for row in self.rows.values() for tag in server_tags(row)}|{'all'}
        unknown=set(tags)-known
        if unknown:raise ValueError('Unknown tag(s): '+', '.join('@'+tag for tag in sorted(unknown)))
        selected=[row['id'] for row in self.rows.values()
                  if ('all' in tags or set(tags)&server_tags(row)) and
                  fleet_state(row,time.monotonic()-self.updated<=35)[0]=='online']
        if not selected:return {'ok':False,'text':'No fresh, running servers match those tags; nothing sent.'}
        return await self.submit_many(selected,'broadcast',message,refresh=False)

    async def submit_many(self, identities, operation, command=None, *, refresh=True):
        if refresh:await asyncio.to_thread(self.refresh)
        rows=[self.rows[ident] for ident in identities]
        body=dict(operation=operation,force=False,targets=[{k:row[k] for k in ('id','process_generation','config_hash')} for row in rows])
        if command is not None:body['command']=command
        accepted=await asyncio.to_thread(self.request,'/api/action',body)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            jobs=await asyncio.to_thread(self.request,'/api/jobs')
            job=next((r for r in jobs['jobs'] if r['id']==accepted['job']),None)
            if job and job['status'] not in ('queued','running'):
                result=job['results'][0] if job['results'] else {}
                if len(rows)>1:
                    successful=sum(bool(r.get('ok')) for r in job['results'])
                    return {'ok':successful==len(rows),'text':f'{successful}/{len(rows)} lobbies completed. Job '+accepted['job']}
                return {'ok':bool(result.get('ok')),'text':result.get('output') or result.get('detail') or
                        result.get('error') or job['status']}
            await asyncio.sleep(.5)
        return {'ok':True,'text':'Queued job '+accepted['job']+'. Track it in the private web console; it will not be resent.'}


class FleetRcon:
    def __init__(self, fleet, url, password, by, on_event, on_state, ready=None, gate=None, on_late=None):
        self.fleet,self.ident=fleet,url.removeprefix('fleet://')
        self.on_event,self.on_state=on_event,on_state
        self.state,self.detail,self.info,self.password='connecting','',{},''
        self.ws=None

    async def run(self):
        while True:
            row=self.fleet.rows.get(self.ident)
            state,detail=fleet_state(row,time.monotonic()-self.fleet.updated<=35)
            row=row or {}
            self.info={'server':row.get('name',self.ident),'version':row.get('image','').rsplit(':',1)[-1]}
            self.ws=True if state=='online' else None
            if (state,detail)!=(self.state,self.detail):
                self.state,self.detail=state,detail;self.on_state(self,state,detail)
            await asyncio.sleep(2)

    async def call(self, command, *args, timeout=10):
        row=self.fleet.rows.get(self.ident)
        if not row:raise ConnectionError('lobby retired')
        if command=='status' and not args:
            if getattr(self,'native_status',False):return await self.fleet.read(self.ident,'status')
            return {'ok':True,'data':{'name':row['name'],'map':row.get('map'),'mode':row.get('mode'),
                    'players':row.get('players'),'max_players':row.get('runtime_cap') or row.get('configured_cap'),
                    'phase':'unknown','health':row.get('health'),'settings_pending':row.get('settings_pending'),
                    'pending_reason':row.get('pending_reason')},'text':'Fleet telemetry'}
        if command in READS and not args:return await self.fleet.read(self.ident,command)
        return await self.fleet.submit(self.ident,'rcon',shlex.join([command,*map(str,args)]))


class FleetApp(OniApp):
    TITLE='IBIGS Reclaimer Fleet'
    BINDINGS=[*[b for b in OniApp.BINDINGS if b.key not in ('ctrl+r', *map(str,range(1,10)))],
              Binding('f6','lifecycle','Container'),Binding('ctrl+r','refresh_fleet','Refresh fleet'),
              Binding('ctrl+f','search_servers','Search',priority=True),
              Binding('ctrl+g','search_players','Find player',priority=True),
              Binding('f7','sort_servers','Sort'),Binding('f8','filter_servers','Filter'),
              *[Binding(str(i),f'visible_station({i-1})',show=False) for i in range(1,10)]]
    CSS='''
    #fleet-browser { height: 4; background: $panel; }
    #fleet-controls { height: 3; }
    #fleet-controls Select { height: 1; margin: 1 0; }
    #fleet-search { width: 1fr; min-width: 16; }
    #fleet-sort { width: 26; }
    #fleet-node { width: 16; }
    #fleet-state { width: 23; }
    #fleet-status { height: 1; padding: 0 1; }
    #fleet-summary { width: 24; color: $secondary; }
    #fleet-target { width: 1fr; color: $text-muted; }
    #fleet-target.-excluded { color: $warning; }
    #fleet-reset { width: 7; min-width: 7; height: 1; border: none; padding: 0; }
    #stations { height: 1fr; }
    #add-server, #sidebar > Center { display: none; }
    .-narrow #fleet-sort { width: 24; }
    .-narrow #fleet-node { width: 13; }
    .-narrow #fleet-state { width: 21; }
    PlayerSearch .dialog { width: 94%; max-width: 150; height: 85%; }
    '''

    def __init__(self, fleet, *, search='', node='all', state='all', sort='players'):
        self.fleet=fleet
        self.inventory_lock=asyncio.Lock()
        self.catalog=dict(fleet.rows)
        self.browse_search,self.browse_node,self.browse_state,self.browse_sort=search,node,state,sort
        self.browser_ready=False
        self.visible_ids=[]
        self.server_items={}
        self.browser_fresh=True
        self.browser_nodes=sorted({r['node'] for r in self.catalog.values()})
        servers=[Server(url='fleet://'+r['id'],name=self.label(r)) for r in matching_rows(fleet.rows.values(),sort=sort)]
        super().__init__(servers,by='fleet-console terminal',intro=False,updater=None,
                         rcon_factory=lambda *args, **kwargs: FleetRcon(fleet, *args, **kwargs),
                         hint='Ctrl+F server search · Ctrl+G find player across all nodes · F7 sort · F8 state filter · RESET shows all current servers. Native chat/kill streaming is not connected in fleet mode.')

    def compose_toolbar(self):
        with Vertical(id='fleet-browser'):
            with Horizontal(id='fleet-controls'):
                yield ServerSearch(self.browse_search,id='fleet-search',placeholder='Search names, maps, ports or @tags · Ctrl+F')
                yield Select(SORTS,value=self.browse_sort,allow_blank=False,id='fleet-sort',compact=True)
                yield Select([('All nodes','all'),*((n.upper(),n) for n in self.browser_nodes)],value=self.browse_node,allow_blank=False,id='fleet-node',compact=True)
                yield Select(STATES,value=self.browse_state,allow_blank=False,id='fleet-state',compact=True)
            with Horizontal(id='fleet-status'):
                yield Static(id='fleet-summary')
                yield Static(id='fleet-target')
                yield Button('RESET',id='fleet-reset',compact=True)

    def compose_stations(self):
        for station,card in zip(self.stations,self.cards):
            self.server_items[station.server.url.removeprefix('fleet://')]=ServerItem(station,card)
        yield ServerList(*self.server_items.values(),id='stations')

    def station_index(self,item):
        return self.stations.index(item.station)

    def station_number(self,station):
        ident=station.server.url.removeprefix('fleet://')
        return self.visible_ids.index(ident)+1 if ident in self.visible_ids else super().station_number(station)

    def browser_summary(self):
        if not self.browser_ready:return
        current_shown=sum(ident in self.fleet.rows for ident in self.visible_ids)
        self.query_one('#fleet-summary',Static).update(Text(f'{current_shown}/{len(self.fleet.rows)} current servers'))
        ident=self.cur.server.url.removeprefix('fleet://')
        excluded=ident not in self.visible_ids
        message=('No matches · ' if not self.visible_ids else '')+'Selected: '+self.cur.label
        if excluded:message+=' · outside filter'
        tags=sorted(server_tags(self.catalog.get(ident,{})))
        if tags:message+=' · '+' '.join('@'+tag for tag in tags)
        target=self.query_one('#fleet-target',Static)
        target.update(Text(message,no_wrap=True,overflow='ellipsis'))
        target.set_class(excluded,'-excluded')
        target.tooltip=Text(message)

    def apply_browser(self):
        if not self.browser_ready:return
        self.browser_fresh=time.monotonic()-self.fleet.updated<=35
        rows=matching_rows(self.catalog.values(),search=self.browse_search,node=self.browse_node,
                           state=self.browse_state,sort=self.browse_sort,snapshot_fresh=self.browser_fresh)
        visible=[r['id'] for r in rows]
        listing=self.query_one('#stations',ServerList)
        selected=self.server_items[self.cur.server.url.removeprefix('fleet://')]
        order=visible+sorted(set(self.server_items)-set(visible))
        actual=[item.station.server.url.removeprefix('fleet://') for item in listing.children]
        if visible!=self.visible_ids or order!=actual:
            ranks={ident:i for i,ident in enumerate(order)}
            # Clear the old highlight before moving DOM children. Programmatic
            # sorting must never retarget a command or reset the player's panel.
            with listing.prevent(ListView.Highlighted,ListView.Selected):
                listing.index=None
                listing.sort_children(key=lambda item:ranks[item.station.server.url.removeprefix('fleet://')])
                for ident,item in self.server_items.items():
                    item.display=ident in visible
                    item.disabled=ident not in visible
                listing.index=list(listing.children).index(selected) if selected.display else None
            self.visible_ids=visible
            for station in self.stations:self.paint_card(station)
        self.browser_summary()

    @on(Input.Changed,'#fleet-search')
    @on(Select.Changed,'#fleet-sort')
    @on(Select.Changed,'#fleet-node')
    @on(Select.Changed,'#fleet-state')
    def browser_changed(self,event):
        if not self.browser_ready:return
        self.browse_search=self.query_one('#fleet-search',Input).value
        self.browse_sort=self.query_one('#fleet-sort',Select).value
        self.browse_node=self.query_one('#fleet-node',Select).value
        self.browse_state=self.query_one('#fleet-state',Select).value
        self.apply_browser()

    @on(Input.Submitted,'#fleet-search')
    def search_result(self,event):
        event.stop()
        listing=self.query_one('#stations',ServerList)
        listing.focus()
        if listing.index is None:listing.action_first_server()

    @on(Button.Pressed,'#fleet-reset')
    def reset_browser(self,event=None):
        self.query_one('#fleet-search',Input).value=''
        self.query_one('#fleet-node',Select).value='all'
        self.query_one('#fleet-state',Select).value='all'
        self.browse_search,self.browse_node,self.browse_state='','all','all'
        self.apply_browser()

    def action_search_servers(self):
        self.query_one('#fleet-search',Input).focus()

    @work(exclusive=True,group='dialog')
    async def action_search_players(self):
        result=await self.push_screen_wait(PlayerSearch(self.fleet))
        if not result:return
        ident=result['server']['id']
        try:
            await self.refresh_inventory()
            row=self.fleet.rows.get(ident)
            if not row or row.get('process_generation')!=result['server'].get('process_generation'):
                raise ValueError('Server changed; search again for the player’s current location.')
            reply=await self.fleet.read(ident,'players')
            if not reply.get('ok'):
                raise ValueError('Could not verify the player’s current server; refresh the search.')
            if not any(target_of(p).lower()==result['id'] for p in reply.get('data',{}).get('players',[])):
                raise ValueError('Player has moved or left; refresh the search to find them again.')
            station=self.server_items[ident].station
            station.data['players']=reply['data']
            self.select_fleet_station(ident)
            self.action_tab('assets')
            self.call_after_refresh(self.focus_found_player,ident,result['id'])
        except Exception as exc:self.notify(str(exc),title='Player location changed',severity='warning')

    def focus_found_player(self,ident,player_id):
        if self.cur.rcon.ident!=ident:return
        self.paint_players()
        roster=self.query_one('#players',Roster)
        if player_id in roster.ids:roster.move_cursor(row=roster.ids.index(player_id))
        roster.focus()

    def action_sort_servers(self):
        self.query_one('#fleet-sort',Select).focus()

    def action_filter_servers(self):
        self.query_one('#fleet-state',Select).focus()

    def action_visible_station(self,index):
        if index<len(self.visible_ids):
            self.select_fleet_station(self.visible_ids[index])

    def action_station(self,index):
        # The command palette's captured index refers to the stable inventory.
        if index<len(self.stations):
            self.select_fleet_station(self.stations[index].server.url.removeprefix('fleet://'))

    def select_fleet_station(self,ident):
        if ident not in self.visible_ids:self.reset_browser()
        listing=self.query_one('#stations',ServerList)
        listing.index=list(listing.children).index(self.server_items[ident])

    def paint_all(self,feed=True):
        super().paint_all(feed=feed)
        self.browser_summary()

    def tick(self):
        super().tick()
        if self.is_running and self.browser_fresh!=(time.monotonic()-self.fleet.updated<=35):
            self.apply_browser()

    @staticmethod
    def label(row):
        return f"{row['node'].upper()} · {row['name']}"+(' [dynamic]' if row.get('spare') else '')

    def on_mount(self,event):
        event.prevent_default()
        super().on_mount()
        self.query_one('#op-password').disabled=True
        self.browser_ready=True
        self.apply_browser()
        self.set_interval(15,self.action_refresh_fleet)

    def fetch(self, station, *what):
        # Only selected-lobby detail reads reach the game. Overview counts are
        # already collected by the controller, so 75 cards do not mean 75 polls.
        if station is not self.cur:what=tuple(w for w in what if w=='status')
        else:
            pane=self.query_one(TabbedContent).active
            allowed={'status','players'}
            if pane=='operations':allowed.update(('maps','modes','nextmap','vote'))
            if pane=='blacklist':allowed.update(('bans','vpn'))
            what=tuple(w for w in what if w in allowed)
        if station.rcon:station.rcon.native_status=station is self.cur
        super().fetch(station,*what)

    @on(TabbedContent.TabActivated)
    def fleet_tab(self,event):
        if event.pane.id=='operations':self.fetch(self.cur,'status','maps','modes','nextmap','vote')
        if event.pane.id=='blacklist':self.fetch(self.cur,'bans','vpn')

    def poll_fast(self):
        if self.is_running:self.fetch(self.cur,'players','status')

    def poll_slow(self):
        for station in self.stations:self.fetch(station,'status')

    @work(exclusive=True,group='fleet-refresh')
    async def action_refresh_fleet(self):
        await self.refresh_inventory()

    async def refresh_inventory(self):
        async with self.inventory_lock:
            await self._refresh_inventory()

    async def _refresh_inventory(self):
        try:
            await asyncio.to_thread(self.fleet.refresh)
            self.catalog={ident:{**row,'_retired':True,'fresh':False} for ident,row in self.catalog.items()}
            self.catalog.update(self.fleet.rows)
            known={s.rcon.ident for s in self.stations if s.rcon}
            for ident,row in sorted(self.fleet.rows.items()):
                if ident in known:continue
                server=Server(url='fleet://'+ident,name=self.label(row));station=Station(server,server.name)
                card=Static(classes='card');self.stations.append(station);self.cards.append(card)
                item=ServerItem(station,card);self.server_items[ident]=item
                await self.query_one('#stations',ListView).append(item)
                station.rcon=self.rcon_factory(server.url,'',self.by,self.on_rcon_event,self.on_rcon_state)
                station.worker=self.run_worker(station.rcon.run(),group='rcon',exit_on_error=False)
            for station in self.stations:
                row=self.fleet.rows.get(station.rcon.ident)
                station.label=self.label(row) if row else station.label.split(' [retired]')[0]+' [retired]'
            nodes=sorted({r['node'] for r in self.catalog.values()})
            if nodes!=self.browser_nodes:
                self.browser_nodes=nodes
                selector=self.query_one('#fleet-node',Select)
                value=selector.value
                with selector.prevent(Select.Changed):
                    selector.set_options([('All nodes','all'),*((n.upper(),n) for n in nodes)])
                    selector.value=value
            self.apply_browser();self.poll_slow();self.paint_all()
        except Exception as exc:self.notify(str(exc),title='Fleet refresh unavailable',severity='warning')

    async def cmd(self, station, command, *args, **kwargs):
        if command in DISRUPTIVE:
            if not await self.push_screen_wait(Confirm('LIVE GAME CHANGE',
                    f'{station.label}: {shlex.join([command,*map(str,args)])}. This can interrupt the current match.')):
                return {'ok':False,'text':'Cancelled'}
        return await super().cmd(station,command,*args,**kwargs)

    @work(exclusive=True,group='dialog')
    async def action_lifecycle(self):
        row=self.fleet.rows.get(self.cur.rcon.ident)
        if not row:return
        result=await self.push_screen_wait(Form('CONTAINER · '+self.cur.label,
            [('operation','start / stop / restart / apply','apply')],verb='QUEUE',
            note='Restart and stop require an empty lobby. Apply waits for 60 seconds empty. Force is disabled.'))
        if not result:return
        operation=result['operation'].strip().lower()
        if operation not in ('start','stop','restart','apply'):
            self.notify('Choose start, stop, restart, or apply.',severity='warning');return
        if await self.push_screen_wait(Confirm('CONFIRM '+operation.upper(),self.cur.label)):
            try:
                reply=await self.fleet.submit(row['id'],operation)
                self.notify(reply['text'],severity='information' if reply['ok'] else 'warning',timeout=15)
            except Exception as exc:self.notify(str(exc),severity='error')

    def action_add_server(self):
        self.notify('Servers come from the fleet registry. Ctrl+R refreshes new spin-ups.')

    @on(Input.Submitted,'#cmd')
    def _command(self,event):
        event.prevent_default()
        if event.value.strip().startswith('@'):
            event.input.value=''
            try:
                tags,command=tagged_message(event.value)
                parts=shlex.split(command)
                if not parts or parts[0].casefold()!='say' or len(parts)<2:
                    raise ValueError('Tag targets support broadcasts only: @swat say Your message. Other commands select one server.')
                self.send_tag_broadcast(tags,' '.join(parts[1:]))
            except ValueError as exc:self.notify(str(exc),severity='warning')
            return
        super()._command(event)

    @on(Input.Submitted,'#say')
    def _say(self,event):
        event.prevent_default()
        if event.value.strip().startswith('@'):
            event.input.value=''
            try:
                tags,message=tagged_message(event.value)
                self.send_tag_broadcast(tags,message)
            except ValueError as exc:self.notify(str(exc),severity='warning')
            return
        super()._say(event)

    @work(group='tag-broadcast')
    async def send_tag_broadcast(self,tags,message):
        try:
            reply=await self.fleet.broadcast_tags(tags,message)
            self.notify(reply['text'],severity='information' if reply['ok'] else 'warning',timeout=15)
        except Exception as exc:self.notify(str(exc),title='Broadcast not sent',severity='error')

    @work(exclusive=True,group='dialog')
    async def action_broadcast(self):
        ident=self.cur.rcon.ident
        tags=sorted({tag for row in self.fleet.rows.values() for tag in server_tags(row)})
        tag_options=[('@'+tag+' ('+str(sum(tag in server_tags(r) and fleet_state(r,True)[0]=='online'
                                             for r in self.fleet.rows.values()))+' running)','tag:'+tag) for tag in tags]
        result=await self.push_screen_wait(Form('IBIGS BROADCAST',
            [('text','Message',''),('scope','Send to',[(self.cur.label,'one'),('Every running lobby','all'),*tag_options])],
            verb='TRANSMIT',required=('text',)))
        if not result:return
        try:
            if result['scope']=='one':reply=await self.fleet.submit(ident,'broadcast',result['text'])
            else:reply=await self.fleet.broadcast_tags([result['scope'].removeprefix('tag:')],result['text'])
            self.notify(reply['text'],severity='information' if reply['ok'] else 'warning',timeout=20)
        except Exception as exc:self.notify(str(exc),title='Broadcast not sent',severity='error')


def main():
    parser=argparse.ArgumentParser(description='IBIGS private Reclaimer fleet terminal on fleet-console')
    parser.add_argument('--list',action='store_true',help='list registered lobbies without opening the TUI')
    parser.add_argument('--probe',metavar='LOBBY_ID',help='read native status for one registered lobby')
    parser.add_argument('--tags',action='store_true',help='list broadcast/search tags and running-server counts')
    parser.add_argument('--find-player',metavar='NAME',help='find a player name or ID across every current server')
    parser.add_argument('--search',default='',help='filter by name, node, map, mode or port; quotes group phrases')
    parser.add_argument('--node',default='all',type=str.casefold,help='filter to one node, e.g. node-c or node-d')
    parser.add_argument('--state',choices=[v for _,v in STATES],default='all',help='filter servers by state')
    parser.add_argument('--sort',choices=[v for _,v in SORTS],default='players',help='sort servers (default: most players first)')
    args=parser.parse_args()
    fleet=Fleet();fleet.refresh()
    if args.node!='all' and args.node not in {r['node'] for r in fleet.rows.values()}:
        parser.error('unknown node: '+args.node)
    if args.find_player is not None:
        directory=fleet.request('/api/players')
        matches=matching_players(directory,args.find_player)
        for player in matches:
            server=player['server'];address=f"{server.get('public_ip') or server['node']}:{server['port']}"
            print(f"{player['name']} · {server['name']} · {server['node'].upper()} · {address} · ID {player['id'][:8]}…")
        print(coverage_text(directory,len(matches)))
        if not matches:print('No matching players in available fresh rosters.')
    elif args.tags:
        tags=sorted({t for r in fleet.rows.values() for t in server_tags(r)})
        for tag in tags:
            rows=[r for r in fleet.rows.values() if tag in server_tags(r)]
            running=sum(fleet_state(r,True)[0]=='online' for r in rows)
            print(f'@{tag:30} {running:3} running / {len(rows)} registered')
    elif args.list:
        rows=matching_rows(fleet.rows.values(),search=args.search,node=args.node,state=args.state,sort=args.sort)
        for row in rows:
            count=live_count(row)
            print(f"{row['id']:16} {str(count) if count is not None else '?':>3}/{row.get('runtime_cap') or row.get('configured_cap') or '?':<3} {row.get('health','?'):12} {row['name']}")
        print(f"{len(rows)}/{len(fleet.rows)} lobbies shown · {len({r['node'] for r in rows})} nodes")
    elif args.probe:
        if args.probe not in fleet.rows:parser.error('unknown lobby')
        reply=asyncio.run(fleet.read(args.probe,'status'));print(json.dumps(reply));raise SystemExit(0 if reply.get('ok') else 1)
    else:
        if not fleet.rows:parser.exit(message='No registered servers in the fleet snapshot.\n')
        FleetApp(fleet,search=args.search,node=args.node,state=args.state,sort=args.sort).run()


if __name__=='__main__':main()
