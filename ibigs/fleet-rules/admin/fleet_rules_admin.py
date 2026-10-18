"""/api/rules for the fleet admin console (2026-10-14): the fleet's rules, roles and Discord webhooks.

GET  /api/rules  -> {enabled, rules, roles, webhooks: {name: masked}, nodes: {node: last push}, history: [...]}
POST /api/rules  -> {enabled?, rules?, roles?, webhooks?: {name: url | null (remove) | "keep"}}

POST merges the change into /state/fleet-rules.json, checks it with oni-rcon's own validation (oni_rules, copied
unchanged), then pushes the whole document to every node with the journaled `configure_rules` RPC over the forced-SSH
transport. Each node's answer is kept. A node that's down gets it on the next save, or on POST {} which re-pushes
as-is. History is read from the nodes (`rules_status`, read only) and cached for 10 s.
"""
from __future__ import annotations

import concurrent.futures
import json
import re
import threading
import time

from .common import atomic_json, read_json

LOCK = threading.Lock()
MAX_DOC = 60000


def _doc(console):
    return read_json(console.state / 'fleet-rules.json', {'enabled': False, 'rules': [], 'roles': {}, 'webhooks': {},
                                                           'nodes': {}})


def _specs(console):
    return {n['id']: n for n in read_json(console.fleet / 'fleet.json', {}).get('nodes', []) if n.get('id')}


def view(console):
    from .oni_rules.rules import masked
    doc = _doc(console)
    cached = getattr(console, 'rules_history_cache', None)
    if not cached or time.monotonic() - cached[0] > 10:
        history = []

        def one(spec):
            try:
                st = console.transport(spec, 'rules_status', timeout=15)
                return [{**h, 'node': spec['id']} for h in st.get('history', [])] if isinstance(st, dict) else []
            except Exception:
                return []
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            for rows in pool.map(one, _specs(console).values()):
                history += rows
        history.sort(key=lambda h: h.get('at', 0))
        cached = console.rules_history_cache = (time.monotonic(), history[-60:])
    return {'enabled': doc['enabled'], 'rules': doc['rules'], 'roles': doc['roles'],
            'webhooks': {k: masked(v) for k, v in doc['webhooks'].items()}, 'nodes': doc.get('nodes', {}),
            'history': cached[1]}


def save(console, actor, body):
    from .oni_rules import rules as R
    if not isinstance(body, dict):
        raise ValueError('send a JSON object')
    with LOCK:
        doc = _doc(console)
        if 'enabled' in body:
            if type(body['enabled']) is not bool:
                raise ValueError('enabled must be true or false')
            doc['enabled'] = body['enabled']
        if 'rules' in body:
            rules = body['rules']
            if not isinstance(rules, list) or len(rules) > R.MAX_RULES:
                raise ValueError(f'up to {R.MAX_RULES} rules')
            doc['rules'] = [R.validate(r) for r in rules]
            if len({r['id'] for r in doc['rules']}) != len(doc['rules']):
                raise ValueError('rule IDs must be unique')
        if 'roles' in body:
            roles = body['roles']
            if not isinstance(roles, dict):
                raise ValueError('roles must be an object')
            clean = {}
            for pid, p in roles.items():
                if not isinstance(p, dict) or not re.fullmatch(r'[0-9a-f]{64}', str(pid)):
                    raise ValueError('roles: keys are 64-character player IDs')
                got = [r for r in p.get('roles', []) if r in R.ROLES]
                if got:
                    clean[pid] = {'name': R.clean(str(p.get('name', '')))[:40], 'roles': got, 'servers': 'all'}
            doc['roles'] = clean
        for name, url in (body.get('webhooks') or {}).items():
            if url == 'keep':
                continue
            if url is None:
                doc['webhooks'].pop(name, None)
                continue
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9 _-]{1,32}', name) or not R.webhook_ok(url):
                raise ValueError("That doesn't look like a Discord webhook URL (https://discord.com/api/webhooks/…)")
            doc['webhooks'][name] = url.strip()
        push = {k: doc[k] for k in ('enabled', 'rules', 'roles', 'webhooks')}
        if len(json.dumps(push)) > MAX_DOC:
            raise ValueError('rules are too large to send to the nodes; remove some')
        doc['updated'], doc['by'] = time.time(), actor
        atomic_json(console.state / 'fleet-rules.json', doc)
    results = {}

    def one(spec):
        try:
            got = console.transport(spec, 'configure_rules', doc=push, timeout=40)
            ok = isinstance(got, dict) and got.get('ok')
            return spec['id'], {'ok': bool(ok), 'at': time.time(), 'signature': got.get('signature') if ok else None,
                                'error': None if ok else str(got.get('error') if isinstance(got, dict) else got)[:200]}
        except Exception as exc:
            return spec['id'], {'ok': False, 'at': time.time(), 'signature': None, 'error': type(exc).__name__}
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        for node, result in pool.map(one, _specs(console).values()):
            results[node] = result
    with LOCK:
        doc = _doc(console)
        doc['nodes'] = {**doc.get('nodes', {}), **results}
        atomic_json(console.state / 'fleet-rules.json', doc)
    console.rules_history_cache = None
    return view(console)
