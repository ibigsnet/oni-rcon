"""Persistent tenant jobs. Fixed actions, local inventory, no shell command execution."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile
import time
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .rcon import Rcon
from .safety import clean

ACTIONS = {'broadcast', 'shuffle', 'start', 'stop', 'restart', 'update-game', 'update-console'}
EVENTS = {'before_match', 'after_match', 'match_start'}
from .schedule import PHASES, ROOT, MatchEvents, atomic_json, cron_due, cron_fields  # noqa: F401 (re-exported)
WRAPPER = '/usr/local/bin/oni'


def validate(job):
    if not isinstance(job, dict):
        raise ValueError('A job must be an object.')
    value = {k: job.get(k) for k in ('id', 'name', 'action', 'target', 'trigger', 'schedule', 'timezone', 'message')}
    value.update(enabled=job.get('enabled', True), delay=job.get('delay', 0))
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', str(value['id'])):
        raise ValueError('Invalid job ID.')
    if not isinstance(value['name'], str) or not 1 <= len(value['name']) <= 80 or clean(value['name']) != value['name']:
        raise ValueError('Name must be 1–80 printable characters.')
    if value['action'] not in ACTIONS:
        raise ValueError('Choose a supported action.')
    if not re.fullmatch(r'all|h3-\d{2,3}|server-\d{1,3}', str(value['target'])):
        raise ValueError('Choose all servers or an existing lobby.')
    if type(value['enabled']) is not bool or type(value['delay']) is not int or not 0 <= value['delay'] <= 300:
        raise ValueError('Delay must be 0–300 seconds; enabled must be true or false.')
    try:
        ZoneInfo(str(value['timezone']))
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError('Use a timezone such as America/New_York or UTC.') from None
    trigger = value['trigger']
    if trigger == 'cron':
        cron_fields(value['schedule'])
    elif trigger == 'interval':
        if not re.fullmatch(r'\d+', str(value['schedule'])) or not 1 <= int(value['schedule']) <= 10080:
            raise ValueError('Interval must be 1–10080 minutes.')
        value['schedule'] = str(int(value['schedule']))
    elif trigger in EVENTS:
        value['schedule'] = ''
        if value['action'] not in {'broadcast', 'shuffle'}:
            raise ValueError('Match events support broadcasts and match-start shuffles.')
        if value['action'] == 'shuffle' and trigger != 'match_start':
            raise ValueError('For an automatic shuffle, choose Match starts or a timed schedule.')
        if trigger == 'before_match' and value['delay']:
            raise ValueError('Before-match messages run when voting or loading begins; use zero delay.')
    else:
        raise ValueError('Choose a supported trigger.')
    message = value['message'] or ''
    if not isinstance(message, str) or len(message) > 240 or clean(message) != message or any(ord(c) < 32 for c in message):
        raise ValueError('Broadcasts must be one printable line of at most 240 characters.')
    if value['action'] == 'broadcast' and not message.strip():
        raise ValueError('Enter a broadcast message.')
    value['message'] = message
    if value['action'].startswith('update-') and value['target'] != 'all':
        raise ValueError('Updates apply to this VM; choose all servers.')
    return value


class Store:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.path = self.root / 'jobs.json'
        self.state_path = self.root / 'state.json'

    def load(self):
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return []
        if data.get('schema') != 1 or not isinstance(data.get('jobs'), list) or len(data['jobs']) > 64:
            raise ValueError('Invalid jobs file. Restore it before scheduled actions can run.')
        jobs = [validate(j) for j in data['jobs']]
        if len({j['id'] for j in jobs}) != len(jobs):
            raise ValueError('Job IDs must be unique.')
        return jobs

    def change(self, job=None, delete=None):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o2770)
        fd = os.open(self.root / 'config.lock', os.O_CREAT | os.O_RDWR, 0o660)
        with os.fdopen(fd, 'r+') as lock:
            if os.fstat(lock.fileno()).st_uid == os.geteuid():
                os.fchmod(lock.fileno(), 0o660)
            fcntl.flock(lock, fcntl.LOCK_EX)
            jobs = self.load()
            if job is not None:
                job = validate(job)
            identity = job['id'] if job else delete
            jobs = [j for j in jobs if j['id'] != identity]
            if job:
                jobs.append(job)
            if len(jobs) > 64:
                raise ValueError('This VM supports up to 64 jobs.')
            atomic_json(self.path, {'schema': 1, 'jobs': jobs})

    def state(self):
        try:
            data = json.loads(self.state_path.read_text())
        except FileNotFoundError:
            return {'schema': 1, 'claims': {}, 'history': []}
        if data.get('schema') != 1 or not isinstance(data.get('claims'), dict) or not isinstance(data.get('history'), list):
            raise ValueError('Invalid job state. Scheduled actions are paused.')
        return data


def occurrence(job, now, previous):
    if job['trigger'] == 'cron':
        local = datetime.fromtimestamp(now, ZoneInfo(job['timezone']))
        # Same wall-clock minute during a DST fold runs once, never twice.
        return local.strftime('%Y-%m-%dT%H:%M') if cron_due(job['schedule'], local) else None
    if job['trigger'] == 'interval':
        period = int(job['schedule']) * 60
        # UTC-aligned intervals; skip an occurrence missed while the worker was down.
        return str(int(now // period)) if now % period < 6 else None
    return None


def inventory():
    wrapper = runpy.run_path(WRAPPER)
    return wrapper['lobbies'](wrapper['read_env']())


def container_states():
    """service -> docker compose state (running, exited, ...), so rules can tell a stop from an outage."""
    wrapper = runpy.run_path(WRAPPER)
    return {k: v.get('state', '') for k, v in wrapper['compose_services']().items()}


def scheduled_targets(job, names):
    if job['action'].startswith('update-') or (job['target'] == 'all' and job['action'] in {'start', 'stop', 'restart'}):
        return ['all']
    return names if job['target'] == 'all' else [job['target']]


def run_fixed(argv, timeout=1800):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=timeout)
    return result.returncode


class Worker:
    def __init__(self, store, discover=inventory, states=container_states, poster=None):
        from .rules import RuleStore
        from .rules_run import Runner, post_discord
        self.store, self.discover, self.states = store, discover, states
        self.state = store.state()
        self.clients = {}
        self.tasks = {}
        self.trackers = {}
        self.generations = {}
        self.jobs = []
        self.pending = set()
        self.action_lock = asyncio.Lock()
        self.stopping = False
        # Rules (rules.py) share these connections; they need one to every lobby while any rule is on.
        self.rules = Runner(RuleStore(store.root), self.clients, start=self.start_lobby, poster=poster or post_discord)
        self.players_at = {}
        self.roles_said = {}  # lobby -> what its last 'waiting' said, so the history isn't one line a minute
        self.found = []
        self.frames = {}  # lobby -> perf.Perf, read from `oni health-report` while a rule wants frame rates

    async def apply_roles(self):
        """Roles and the votes switch (F8 → ROLES) need a restart to reach the server: the wrapper writes them and
        restarts a lobby only while it's empty. Busy lobbies wait for the next try, a minute later."""
        if not any((self.store.root / f).exists() for f in ('roles.json', 'applied.json')) or self.action_lock.locked():
            return
        async with self.action_lock:
            result = await asyncio.to_thread(subprocess.run, [WRAPPER, 'roles', 'apply', '--when-empty', '--json'],
                                             stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=900)
        try:
            rows = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            rows = [{'lobby': '', 'state': 'failed', 'text': clean(result.stderr.strip()[-300:]) or 'no answer'}]
        for row in rows:
            if row.get('state') in ('applied', 'failed') or (row.get('state') == 'pending' and
                                                            self.roles_said.get(row.get('lobby')) != row.get('text')):
                self.roles_said[row.get('lobby')] = row.get('text')
                self.rules.note('roles', str(row.get('lobby', '')), clean(str(row.get('text', ''))), 'Roles and votes')

    async def read_frames(self):
        """Each lobby's frame rate, for rules that use it: the server logs every minute it fell under 54 fps, and
        `oni health-report` passes those lines on (perf.py reads them)."""
        from . import health as hc
        from . import perf as pf
        for lobby in list(self.found):
            port = lobby.get('port')
            if not isinstance(port, int):
                continue
            try:
                result = await asyncio.to_thread(subprocess.run, [WRAPPER, 'health-report', str(port), '900s'],
                                                 stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                continue
            rep = hc.parse_report(result.stdout)
            if not rep.host.get('perf'):
                continue
            p = self.frames.setdefault(lobby['name'], pf.Perf())
            for m in pf.minutes_in(rep.lines, hc.clock_for(rep.host, time.time()), hc.split_stamp):
                p.add(m)
            p.read_at = time.time()
            reading = p.reading(time.time())
            if reading:
                self.rules.on_fps(lobby['name'], reading.fps)

    async def start_lobby(self, name):
        """A rule's 'start a stopped server': the wrapper's own start, never forced, one lifecycle action at a time."""
        async with self.action_lock:
            return await asyncio.to_thread(run_fixed, [WRAPPER, 'start', name, '--yes'], 600)

    def save(self):
        self.state['heartbeat'] = time.time()
        atomic_json(self.store.state_path, self.state)

    def record(self, job, target, result):
        self.state['history'].append({'at': time.time(), 'job': job['id'], 'target': target, 'result': result})
        self.state['history'] = self.state['history'][-100:]
        self.save()

    def connection(self, target, state):
        self.rules.on_connection(target, state == 'online')
        if state != 'online':
            self.generations[target] = self.generations.get(target, 0) + 1
            self.trackers[target] = MatchEvents()

    def event(self, target, msg):
        self.rules.on_event(target, msg)
        if msg.get('event') == 'lagged' or msg.get('type') == 'lagged':
            self.generations[target] = self.generations.get(target, 0) + 1
            self.trackers[target] = MatchEvents()
        elif msg.get('event') == 'phase':
            self.generations[target] = self.generations.get(target, 0) + 1
            at = msg.get('time')
            if not isinstance(at, (float, int)) or not -1 <= time.time() - at <= 3:
                self.trackers[target] = MatchEvents()
                return
            self.observe(target, msg)

    def observe(self, target, status):
        tracker = self.trackers.setdefault(target, MatchEvents())
        for event in tracker.observe(status):
            for job in self.jobs:
                if job['enabled'] and job['trigger'] == event and job['target'] in {'all', target}:
                    self.queue(job, target, 'event:' + uuid.uuid4().hex)

    def queue(self, job, target, key):
        identity = job['id'] + ':' + target
        if identity in self.pending or len(self.pending) >= 128:
            return
        claim = self.state['claims'].get(identity, {})
        recurrence = [job['trigger'], job['schedule'], job['timezone']]
        same_schedule = claim.get('recurrence') == recurrence
        if claim.get('key') == key and same_schedule:
            return
        if same_schedule and job['trigger'] == 'cron' and str(claim.get('key', '')) >= key:
            return  # do not run an earlier wall-clock minute again after a clock change or DST fold
        if same_schedule and job['trigger'] == 'interval' and int(claim.get('key', '-1')) >= int(key):
            return
        self.pending.add(identity)
        task = asyncio.create_task(self.execute(job, target, key))
        task.add_done_callback(lambda t: self.pending.discard(identity))
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    async def execute(self, job, target, key):
        if job['delay']:
            await asyncio.sleep(job['delay'])
        # A job removed, paused or edited during its delay must not execute the old action.
        if job not in self.store.load() or not job['enabled']:
            return
        identity = job['id'] + ':' + target
        # Durable claim BEFORE any action: a crash may skip a job, never replay an uncertain broadcast or shuffle.
        self.state['claims'][identity] = {'key': key, 'at': time.time(),
                                         'recurrence': [job['trigger'], job['schedule'], job['timezone']]}
        self.save()
        try:
            action = job['action']
            if action in {'broadcast', 'shuffle'}:
                client = self.clients.get(target)
                if not client or not client.ws:
                    self.record(job, target, 'Skipped: lobby is offline or not authenticated.')
                    return
                tracker = self.trackers[target]
                if job['trigger'] == 'before_match' and tracker.phase not in {'no_game', 'loading', 'starting'}:
                    self.record(job, target, 'Skipped: lobby already left the pre-match window.')
                    return
                if job['trigger'] == 'after_match' and tracker.phase not in {'finished', 'no_game'}:
                    self.record(job, target, 'Skipped: the next match has already started.')
                    return
                if action == 'shuffle':
                    fresh = await client.call('status')
                    if not fresh.get('ok') or (fresh.get('data') or {}).get('phase') != 'playing':
                        self.record(job, target, 'Skipped: teams can be shuffled during a running match.')
                        return
                reply = await client.call('say', job['message']) if action == 'broadcast' else await client.call('shuffle')
                self.record(job, target, 'Sent.' if reply.get('ok') else 'Server refused the action.')
            else:
                if self.action_lock.locked():
                    self.record(job, target, 'Skipped: another lifecycle or update job is running.')
                    return
                async with self.action_lock:
                    if action.startswith('update-'):
                        tool = '/usr/local/sbin/ibigs-' + ('oni-update' if action == 'update-console' else 'reclaimer-update')
                        argv = [tool] + (['--yes'] if action == 'update-game' else [])
                        if os.geteuid() != 0:
                            argv = ['sudo', '-n', *argv]
                    else:
                        argv = [WRAPPER, action, target, '--yes']
                    rc = await asyncio.to_thread(run_fixed, argv)
                    result = 'Completed.' if rc == 0 else ('Deferred: players online or occupancy unknown.' if rc == 4 else f'Failed (exit {rc}); inspect the updater/service log.')
                    self.record(job, target, result)
        except (OSError, ValueError, TimeoutError, ConnectionError, subprocess.SubprocessError):
            self.record(job, target, 'Failed or reply uncertain; this occurrence will not be retried.')

    async def refresh_clients(self):
        found = await asyncio.to_thread(self.discover)
        self.found = found
        if self.rules.active:
            with contextlib.suppress(Exception):
                self.rules.on_inventory(await asyncio.to_thread(self.states))
        needed = {s['name']: s for s in found if self.rules.active or any(
                  j['enabled'] and j['action'] in {'broadcast', 'shuffle'} and j['target'] in {'all', s['name']}
                  for j in self.jobs)}
        for name in list(self.clients):
            client = self.clients[name]
            info = needed.get(name)
            host = ('[::1]' if info['host'] == '::1' else info['host']) if info else ''
            if info is None or client.password != info['password'] or client.url != f"ws://{host}:{info['port']}":
                self.tasks.pop(name).cancel()
                self.clients.pop(name)
                self.trackers.pop(name, None)
        for name, info in needed.items():
            if name in self.clients:
                continue
            if info['host'] not in {'127.0.0.1', 'localhost', '::1'}:
                continue
            host = '[::1]' if info['host'] == '::1' else info['host']
            client = Rcon(f"ws://{host}:{info['port']}", info['password'], 'Scheduled job',
                          lambda c, e, n=name: self.event(n, e),
                          lambda c, s, d, n=name: self.connection(n, s,),
                          host_key_dir=str(info.get('host_key_dir') or ''))
            self.clients[name] = client
            self.tasks[name] = asyncio.create_task(client.run())
        return [s['name'] for s in found]

    async def poll(self, name, client):
        if not client.ws:
            return
        generation = self.generations.get(name, 0)
        try:
            result = await client.call('status', timeout=3)
            if generation != self.generations.get(name, 0):
                return  # a native event or disconnect superseded this in-flight snapshot
            if result.get('ok') and isinstance(result.get('data'), dict):
                self.observe(name, result['data'])
                self.rules.on_status(name, result['data'])
                if self.rules.active and time.time() - self.players_at.get(name, 0) >= 5:
                    self.players_at[name] = time.time()  # who's where, their admin flags, kill feed names
                    roster = await client.call('players', timeout=3)
                    if roster.get('ok'):
                        self.rules.on_players(name, roster.get('data'))
            else:
                self.trackers[name] = MatchEvents()
        except (OSError, ValueError, TimeoutError, ConnectionError):
            self.trackers[name] = MatchEvents()

    async def run(self):
        next_roles = time.time() + 20
        next_frames = time.time() + 30
        next_inventory = 0
        next_heartbeat = 0
        names = []
        active = Path('/opt/ibigs/oni-rcon/current')
        release = active.resolve() if active.exists() else None
        while True:
            self.jobs = self.store.load()  # malformed policy fails closed, systemd reports the failure
            self.rules.reload()  # a bad rules file pauses the rules only, and says so in their history
            now = time.time()
            if now >= next_inventory:
                if release and active.exists() and active.resolve() != release and not self.pending:
                    os.execv(WRAPPER, [WRAPPER, 'jobs', 'run'])
                names = await self.refresh_clients()
                next_inventory = now + 10
                valid = {j['id'] for j in self.jobs}
                self.state['claims'] = {k: v for k, v in self.state['claims'].items() if k.split(':')[0] in valid}
            for job in self.jobs:
                if not job['enabled'] or job['trigger'] in EVENTS:
                    continue
                key = occurrence(job, now, self.state['claims'])
                if key is None:
                    continue
                targets = scheduled_targets(job, names)
                for target in targets:
                    if target != 'all' and target not in names:
                        continue
                    self.queue(job, target, key)
            await asyncio.gather(*(self.poll(n, c) for n, c in self.clients.items()))
            self.rules.tick()
            if now >= next_frames:
                next_frames = now + 60
                if self.rules.wants_fps:
                    task = asyncio.create_task(self.read_frames())
                    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            if now >= next_roles:
                next_roles = now + 60
                task = asyncio.create_task(self.apply_roles())
                task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            if now >= next_heartbeat:
                self.save()
                next_heartbeat = now + 10
            await asyncio.sleep(2)


def main(args=None):
    ap = argparse.ArgumentParser(prog='oni jobs', description='Persistent schedules for this VM. Use F8 in oni for the editor.')
    sub = ap.add_subparsers(dest='command')
    sub.add_parser('run', help=argparse.SUPPRESS)
    sub.add_parser('list')
    sub.add_parser('history')
    sub.add_parser('status')
    for verb in ('pause', 'resume', 'delete'):
        sub.add_parser(verb).add_argument('id')
    add = sub.add_parser('add')
    add.add_argument('--name', required=True)
    add.add_argument('--action', required=True, choices=sorted(ACTIONS))
    add.add_argument('--target', default='all')
    add.add_argument('--trigger', default='cron', choices=['cron', 'interval', *sorted(EVENTS)])
    add.add_argument('--schedule', default='0 4 * * *')
    add.add_argument('--timezone', default='America/New_York')
    add.add_argument('--message', default='')
    add.add_argument('--delay', type=int, default=0)
    a = ap.parse_args(args)
    store = Store()
    try:
        if a.command == 'run':
            store.root.mkdir(parents=True, exist_ok=True, mode=0o2770)
            with open(store.root / 'worker.lock', 'a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                asyncio.run(Worker(store).run())
        elif a.command == 'add':
            job = vars(a).copy()
            job.update(id=uuid.uuid4().hex[:12], enabled=True)
            store.change(job)
            print('Saved ' + job['id'] + '. The scheduler runs it while oni is closed.')
        elif a.command in {'pause', 'resume', 'delete'}:
            job = next((j for j in store.load() if j['id'] == a.id), None)
            if not job:
                raise ValueError('No job has that ID.')
            if a.command == 'delete':
                store.change(delete=a.id)
            else:
                job['enabled'] = a.command == 'resume'
                store.change(job)
            print('Saved.')
        else:
            data = store.state() if a.command in {'history', 'status'} else store.load()
            print(json.dumps(data['history'] if a.command == 'history' else data, indent=2))
    except (OSError, ValueError, BlockingIOError) as exc:
        print(clean(str(exc)), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
