"""Fleet rules: oni-rcon's rules sandbox (WHEN / IF / THEN) for this node's lobbies (2026-10-14).

The engine is oni-rcon's own, copied unchanged into oni_rules/ (rules.py, rules_run.py, schedule.py, safety.py, from
ibigsnet/oni-rcon). The fleet-console admin console pushes the fleet's rules here with the `configure_rules` RPC, through the
forced-SSH transport and the journal like every other change. They're kept in /state/rules/ with an on/off switch,
and the switch is OFF until someone turns it on.

This worker (container reclaimer-rules, `python -m reclaimer_control.fleet_rules`) runs them:
- Only while rules are on and at least one rule is enabled, it keeps one WebSocket RCON connection per eligible lobby.
  Eligible means operator_enabled, running, fresh, RCON up, no operator hold. That's one more native console slot per
  lobby, the same as vote guard.
- A refused sign-in (including "full") is never retried for that process generation.
- What fires is carried out on those connections: say, tell, cancel a vote, shuffle.
- Starting servers is refused here: lifecycle belongs to the controller.
- Discord posts go straight to the webhook.
- Limits: every rule's cooldown and hourly cap, 20 rule messages a minute per server, 2 s between Discord posts.

Up/down/full/empty and frame rate come from this node's snapshot (perf.lags). Admins are the fleet admin list
(node.json fleet_defaults.admins) plus the ADMIN role pushed with the rules. Rules see this node's lobbies only.
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import itertools
import json
import os
import re
import time
from pathlib import Path

from .common import Docker, atomic_json, digest, read_json, unix_time

STATE = Path(os.environ.get('RECLAIMER_STATE', '/state'))
DIR = STATE / 'rules'
MAX_BODY = 60000  # the forced-command transport reads at most 64 KiB of request
ID64 = re.compile(r'[0-9a-f]{64}')


# --- the RPC: store what the admin console pushed -------------------------------------------------------------------
def configure(state, request):
    """{'doc': {'enabled', 'rules', 'roles', 'webhooks'}} -> written to /state/rules/ after the same checks oni makes."""
    from .oni_rules import rules as R
    doc = request.get('doc')
    if not isinstance(doc, dict) or len(json.dumps(doc)) > MAX_BODY:
        raise ValueError('rules document missing or too large')
    enabled = doc.get('enabled')
    if type(enabled) is not bool:
        raise ValueError('enabled must be true or false')
    rules = doc.get('rules') or []
    if not isinstance(rules, list) or len(rules) > R.MAX_RULES:
        raise ValueError('rules must be a list of at most %d' % R.MAX_RULES)
    rules = [R.validate(r) for r in rules]
    if len({r['id'] for r in rules}) != len(rules):
        raise ValueError('rule IDs must be unique')
    roles = doc.get('roles') or {}
    if not isinstance(roles, dict) or len(roles) > 2000:
        raise ValueError('roles must be an object')
    players = {}
    for pid, p in roles.items():
        if not ID64.fullmatch(str(pid)) or not isinstance(p, dict):
            raise ValueError('roles: keys are 64-character player IDs')
        players[pid] = {'name': R.clean(str(p.get('name', '')))[:40],
                        'roles': [r for r in p.get('roles', []) if r in R.ROLES], 'servers': 'all'}
    hooks = doc.get('webhooks') or {}
    if not isinstance(hooks, dict) or not all(isinstance(k, str) and re.fullmatch(r'[A-Za-z0-9 _-]{1,32}', k)
                                              and R.webhook_ok(v) for k, v in hooks.items()):
        raise ValueError('webhooks must be name -> Discord webhook URL')
    target = Path(state) / 'rules'
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    signature = digest({'enabled': enabled, 'rules': rules, 'roles': players, 'webhooks': hooks})
    atomic_json(target / 'rules.json', {'schema': 1, 'rules': rules})
    atomic_json(target / 'roles.json', {'schema': 1, 'players': players, 'votes': {}})
    atomic_json(target / 'discord.json', {'schema': 1, 'webhooks': hooks})
    atomic_json(target / 'policy.json', {'schema': 1, 'enabled': enabled, 'signature': signature, 'at': time.time()})
    return {'ok': True, 'signature': signature, 'rules': len(rules), 'enabled': enabled}


def status(state):
    """Read only: the switch, the worker's health and the last 40 firings."""
    d = Path(state) / 'rules'
    policy = read_json(d / 'policy.json', {})
    history = read_json(d / 'rules-state.json', {}).get('history', [])
    return {'ok': True, 'enabled': bool(policy.get('enabled')), 'signature': policy.get('signature'),
            'health': read_json(d / 'health.json', {}), 'history': history[-40:]}


# --- the worker ------------------------------------------------------------------------------------------------------
def fps_of(row):
    """The latest minute's frame rate from the snapshot's perf.lags ([when, slow s, of s, avg, longest, players])."""
    perf = row.get('perf') if isinstance(row.get('perf'), dict) else None
    if not perf or not isinstance(perf.get('lags'), list):
        return None
    now, best = time.time(), None
    for lag in perf['lags']:
        if isinstance(lag, list) and len(lag) == 6 and all(isinstance(x, (int, float)) for x in lag):
            if 0 <= now - lag[0] <= 150 and (best is None or lag[0] > best[0]):
                best = lag
    if best is None:
        return 60.0
    _, slow, span, avg, _, _ = best
    return (slow * avg + (span - slow) * 60) / span if span else 60.0


class Client:
    """One lobby's native console, as rules_run.Runner expects: .ws and call(*args) -> reply."""

    def __init__(self, ident):
        self.ident, self.ws = ident, None
        self.seq = itertools.count(1)
        self.waiting: dict[int, asyncio.Future] = {}

    async def call(self, command, *args, timeout=3):
        if not self.ws:
            raise ConnectionError('not connected')
        n = next(self.seq)
        fut = asyncio.get_running_loop().create_future()
        self.waiting[n] = fut
        try:
            await self.ws.send(json.dumps({'type': 'command', 'command': command, 'args': [str(a) for a in args],
                                           'id': n, 'by': 'IBIGS Rules'}))
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.waiting.pop(n, None)


class Worker:
    def __init__(self, state=STATE):
        from .oni_rules.rules import RuleStore
        from .oni_rules.rules_run import Runner
        self.state, self.dir = Path(state), Path(state) / 'rules'
        self.docker = Docker()
        self.clients: dict[str, Client] = {}
        self.tasks: dict[str, tuple] = {}  # lobby -> (generation, task)
        self.denied: dict[str, str] = read_json(self.dir / 'auth-denied.json', {})
        self.gate = asyncio.Semaphore(3)
        self.runner = Runner(RuleStore(self.dir), self.clients, start=None)
        self.fps: dict[str, float] = {}

    def enabled(self):
        return bool(read_json(self.dir / 'policy.json', {}).get('enabled'))

    def admins_as_roles(self, node):
        """The fleet admin list counts as the ADMIN role, beside the roles pushed with the rules."""
        admins = (node.get('fleet_defaults') or {}).get('admins') or []
        for pid in admins:
            if ID64.fullmatch(str(pid)):
                entry = self.runner.engine.roles.setdefault(pid, {'name': '', 'roles': [], 'servers': 'all'})
                if 'admin' not in entry['roles']:
                    entry['roles'] = ['admin', *entry['roles']]

    def lobbies(self, node, snapshot):
        """id -> (item, row) for lobbies rules may talk to."""
        rows = {r.get('id'): r for r in snapshot.get('lobbies', []) if isinstance(r, dict)}
        fresh = 0 <= time.time() - (snapshot.get('observed_at') or 0) <= 45
        out = {}
        if not node.get('operator_enabled') or not fresh:
            return rows, out
        for item in node.get('instances', []):
            row = rows.get(item.get('id'))
            if (row and item.get('enabled', True) and not item.get('operator_hold') and row.get('running')
                    and row.get('fresh') and row.get('rcon_up') and row.get('process_generation')):
                out[item['id']] = (item, row)
        return rows, out

    async def session(self, item, generation):
        from websockets.asyncio.client import connect
        from .team_shuffle_runtime import connection
        ident = item['id']
        client = self.clients.setdefault(ident, Client(ident))
        try:
            async with self.gate:
                info, uri, password = await asyncio.to_thread(connection, self.docker, item)
                if info['Id'] + ':' + str(unix_time(info['State']['StartedAt'])) != generation:
                    return
                key = digest([generation, password])
                if self.denied.get(ident) == key:
                    return
                ws = await connect(uri, proxy=None, open_timeout=4, close_timeout=1, max_size=1024 * 1024,
                                   ping_interval=20, ping_timeout=10)
                await ws.send(json.dumps({'type': 'auth', 'password': password}))
                reply = json.loads(await asyncio.wait_for(ws.recv(), 4))
                if not isinstance(reply, dict) or reply.get('type') != 'auth' or not reply.get('ok'):
                    # Never retry a refusal (wrong password lockouts, or every console slot taken).
                    self.denied[ident] = key
                    atomic_json(self.dir / 'auth-denied.json', self.denied)
                    await ws.close()
                    return
            client.ws = ws
            pump = asyncio.create_task(self.poll(ident, client))
            try:
                async for raw in ws:
                    msg = json.loads(raw)
                    if not isinstance(msg, dict):
                        continue
                    if msg.get('type') == 'reply':
                        fut = client.waiting.get(msg.get('id'))
                        if fut and not fut.done():
                            fut.set_result(msg)
                    elif msg.get('type') == 'event':
                        self.runner.on_event(ident, msg)
                    elif msg.get('type') == 'lagged':
                        self.runner.on_event(ident, {'event': 'lagged'})
            finally:
                pump.cancel()
        except (OSError, ValueError, asyncio.TimeoutError, StopIteration, KeyError):
            pass
        except Exception:
            pass  # websockets' ConnectionClosed and anything else: reconnect on the next pass
        finally:
            client.ws = None

    async def poll(self, ident, client):
        """status every 3 s (phase, votes, counts), players every 6 s (names, teams, admin flags)."""
        n = 0
        while client.ws:
            with contextlib.suppress(Exception):
                s = await client.call('status')
                if s.get('ok') and isinstance(s.get('data'), dict):
                    self.runner.on_status(ident, s['data'])
                if n % 2 == 0:
                    p = await client.call('players')
                    if p.get('ok'):
                        self.runner.on_players(ident, p.get('data'))
            n += 1
            await asyncio.sleep(3)

    async def step(self):
        on = self.enabled()
        if on:
            self.runner.reload()
        else:  # off: nothing runs, and switching on re-reads everything
            self.runner.engine.rules, self.runner.signature = [], None
        node = await asyncio.to_thread(self.configured)
        snapshot = read_json(self.state / 'snapshot.json', {})
        rows, eligible = self.lobbies(node, snapshot)
        self.admins_as_roles(node)
        # facts every rule can use, from the snapshot
        self.runner.on_inventory({i: 'running' if r.get('desired_running', True) else 'exited' for i, r in rows.items()})
        for ident, row in rows.items():
            self.runner.on_connection(ident, bool(row.get('running') and row.get('rcon_up')))
            fps = fps_of(row)
            if fps is not None and self.fps.get(ident) != fps:
                self.fps[ident] = fps
                self.runner.on_fps(ident, fps)
        wanted = {i: v for i, v in eligible.items()} if on and self.runner.active else {}
        for ident, (gen, task) in list(self.tasks.items()):
            if ident not in wanted or wanted[ident][1]['process_generation'] != gen:
                task.cancel()
                self.tasks.pop(ident)
                c = self.clients.get(ident)
                if c and c.ws:
                    with contextlib.suppress(Exception):
                        await c.ws.close()
        for ident, (item, row) in wanted.items():
            gen = row['process_generation']
            if ident not in self.tasks or self.tasks[ident][1].done():
                self.tasks[ident] = (gen, asyncio.create_task(self.session(item, gen)))
        self.runner.tick()
        atomic_json(self.dir / 'health.json', {'at': time.time(), 'enabled': on, 'rules': len(self.runner.engine.rules),
                                               'connected': sorted(i for i, c in self.clients.items() if c.ws),
                                               'eligible': len(eligible), 'error': self.runner.error})

    def configured(self):
        from .agent import configured
        return configured()

    async def run(self):
        while True:
            try:
                await self.step()
            except Exception as exc:  # a bad reading never stops the worker; health says what happened
                with contextlib.suppress(Exception):
                    atomic_json(self.dir / 'health.json', {'at': time.time(), 'error': type(exc).__name__})
            await asyncio.sleep(2)


def main():
    DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(DIR / 'worker.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(Worker().run())


if __name__ == '__main__':
    main()
