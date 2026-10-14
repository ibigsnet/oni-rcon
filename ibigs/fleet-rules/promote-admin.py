"""Run on fleet-console in a throwaway container (docker.sock + /provider): swap reclaimer-admin to the image with /api/rules.
Refuses if the admin changed since this was prepared or an operator job is pending; rolls back on any failed check."""
import json
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path

from reclaimer_control.agent import container_spec
from reclaimer_control.common import Docker, atomic_json

OLD, NEW = 'ibigs/reclaimer-admin:2026.10.13.1-readcache', 'ibigs/reclaimer-admin:2026.10.14.1-rules'
D = Docker()
root = Path('/provider')
old = D.inspect('reclaimer-admin')
assert old['Config']['Image'] == OLD, 'admin changed since this was prepared: ' + old['Config']['Image']
D.request('GET', '/images/' + NEW.replace('/', '%2F') + '/json')
with sqlite3.connect(root / 'admin/state/audit.sqlite') as db:
    assert db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] == 0, \
        'operator jobs pending; try again in a minute'
backup = root / 'deploy/fleet-rules-20261014'
backup.mkdir(parents=True, exist_ok=True)
atomic_json(backup / 'admin-container-before.json', old)
spec = container_spec(old, NEW)
old_id, new_id = old['Id'], None
D.request('POST', f'/containers/{old_id}/stop?t=10', timeout=20)
D.request('POST', f'/containers/{old_id}/rename?name=reclaimer-admin-before-rules-20261014')
try:
    new_id = D.request('POST', '/containers/create?name=reclaimer-admin', spec)['Id']
    D.request('POST', f'/containers/{new_id}/start')
    for _ in range(20):
        try:
            with urllib.request.urlopen('http://127.0.0.1:9190/healthz', timeout=2) as r:
                assert json.load(r)['ok']
            break
        except (OSError, ValueError):
            time.sleep(1)
    else:
        raise RuntimeError('operator API failed readiness')
    try:
        urllib.request.urlopen('http://127.0.0.1:9190/api/rules', timeout=3)
        raise AssertionError('/api/rules allowed unauthenticated access')
    except urllib.error.HTTPError as exc:
        assert exc.code == 403
    token = (root / 'terminal/secrets/terminal.key').read_text().strip()
    req = urllib.request.Request('http://127.0.0.1:9190/api/rules', headers={'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(req, timeout=60) as r:
        rules = json.load(r)
    assert isinstance(rules['rules'], list) and rules['enabled'] is False
    req = urllib.request.Request('http://127.0.0.1:9190/api/findings', headers={'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(req, timeout=30) as r:
        assert isinstance(json.load(r)['findings'], list)  # the existing API still answers
    result = {'image': NEW, 'rules_api': True, 'anonymous_forbidden': True, 'switch': 'OFF',
              'history_from_nodes': len(rules.get('history', [])), 'game_mutations': 0}
    atomic_json(backup / 'admin-promotion-result.json', result)
    print(json.dumps(result))
except Exception:
    if new_id:
        D.request('DELETE', f'/containers/{new_id}?force=true&v=true')
    D.request('POST', f'/containers/{old_id}/rename?name=reclaimer-admin')
    D.request('POST', f'/containers/{old_id}/start')
    raise
