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
from textual.widgets import Input, ListItem, ListView, Static, TabbedContent
from .app import OniApp, Station, Confirm, Form
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
        if path not in ('/api/fleet','/api/jobs','/api/action','/api/console-read'):
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

    async def submit_many(self, identities, operation, command=None):
        await asyncio.to_thread(self.refresh)
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
    def __init__(self, fleet, url, password, by, on_event, on_state, ready=None):
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
    BINDINGS=[*[b for b in OniApp.BINDINGS if b.key!='ctrl+r'],Binding('f6','lifecycle','Container'),Binding('ctrl+r','refresh_fleet','Refresh fleet')]

    def __init__(self, fleet):
        self.fleet=fleet
        servers=[Server(url='fleet://'+r['id'],name=self.label(r)) for r in sorted(fleet.rows.values(),key=lambda r:(r['node'],r['port']))]
        super().__init__(servers,by='fleet-console terminal',intro=False,updater=None,
                         rcon_factory=lambda *args:FleetRcon(fleet,*args),
                         hint='All nodes · F6 container controls · Ctrl+R refresh · Ctrl+B broadcast. Intercepts shows management events; native kill/chat streaming is not connected in fleet mode.')

    @staticmethod
    def label(row):
        return f"{row['node'].upper()} · {row['name']}"+(' [dynamic]' if row.get('spare') else '')

    def on_mount(self):
        super().on_mount()
        self.query_one('#op-password').disabled=True
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
        try:
            await asyncio.to_thread(self.fleet.refresh)
            known={s.rcon.ident for s in self.stations if s.rcon}
            for ident,row in sorted(self.fleet.rows.items()):
                if ident in known:continue
                server=Server(url='fleet://'+ident,name=self.label(row));station=Station(server,server.name)
                card=Static(classes='card');self.stations.append(station);self.cards.append(card)
                await self.query_one('#stations',ListView).append(ListItem(card))
                station.rcon=self.rcon_factory(server.url,'',self.by,self.on_rcon_event,self.on_rcon_state)
                station.worker=self.run_worker(station.rcon.run(),group='rcon',exit_on_error=False)
            for station in self.stations:
                row=self.fleet.rows.get(station.rcon.ident)
                station.label=self.label(row) if row else station.label.split(' [retired]')[0]+' [retired]'
            self.poll_slow();self.paint_all()
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
        if event.value.strip().startswith('@all '):
            self.notify('Use Ctrl+B for a fleet broadcast. Raw commands select one lobby at a time.',severity='warning')
            event.input.value='';return
        super()._command(event)

    @on(Input.Submitted,'#say')
    def _say(self,event):
        if event.value.strip().startswith('@all '):
            self.notify('Use Ctrl+B to submit one audited fleet broadcast.',severity='warning')
            event.input.value='';return
        super()._say(event)

    @work(exclusive=True,group='dialog')
    async def action_broadcast(self):
        result=await self.push_screen_wait(Form('IBIGS BROADCAST',
            [('text','Message',''),('scope','Send to',[('Every running lobby','all'),(self.cur.label,'one')])],
            verb='TRANSMIT',required=('text',)))
        if not result:return
        targets=[s.rcon.ident for s in (self.stations if result['scope']=='all' else [self.cur]) if s.online]
        if not targets:
            self.notify('No connected lobbies selected.',severity='warning');return
        reply=await self.fleet.submit_many(targets,'broadcast',result['text'])
        self.notify(reply['text'],severity='information' if reply['ok'] else 'warning',timeout=20)


def main():
    parser=argparse.ArgumentParser(description='IBIGS private Reclaimer fleet terminal on fleet-console')
    parser.add_argument('--list',action='store_true',help='list registered lobbies without opening the TUI')
    parser.add_argument('--probe',metavar='LOBBY_ID',help='read native status for one registered lobby')
    args=parser.parse_args()
    fleet=Fleet();fleet.refresh()
    if args.list:
        for row in sorted(fleet.rows.values(),key=lambda r:(r['node'],r['port'])):
            print(f"{row['id']:16} {str(row.get('players')):>3}/{row.get('runtime_cap') or row.get('configured_cap'):<3} {row.get('health','?'):12} {row['name']}")
        print(f"{len(fleet.rows)} lobbies · {len({r['node'] for r in fleet.rows.values()})} nodes")
    elif args.probe:
        if args.probe not in fleet.rows:parser.error('unknown lobby')
        reply=asyncio.run(fleet.read(args.probe,'status'));print(json.dumps(reply));raise SystemExit(0 if reply.get('ok') else 1)
    else:FleetApp(fleet).run()


if __name__=='__main__':main()
