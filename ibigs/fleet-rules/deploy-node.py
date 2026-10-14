"""Executed inside a node's reclaimer-agent (`python -`): adds fleet rules, switched OFF.

1. Code (under <data_root>/code/reclaimer_control/, refused if any of it already exists):
   - fleet_rules.py and oni_rules/ (oni-rcon's engine, unchanged)
   - agent.py gets two RPC actions, configure_rules (journaled) and rules_status (read only), inserted at one anchor
     after a backup and a parse check
2. /state/rules/policy.json: enabled = false
3. Container reclaimer-rules, from the agent's own image, `python -m reclaimer_control.fleet_rules`:
   - every agent mount read-only except /state/rules
   - the agent's network mode
   - 128 MiB memory, 0.25 CPU, read-only root, no capabilities
4. It must publish a fresh /state/rules/health.json within 30 s; otherwise everything above is undone.

With the switch off the worker opens no game connection. The fleet-console admin console turns it on with
configure_rules. The FILES dict is filled in by deploy-nodes.sh.
"""
import ast
import base64
import json
import time
from pathlib import Path

from reclaimer_control.agent import STATE, action_lock, configured
from reclaimer_control.common import Docker, atomic_json, atomic_write, read_json

FILES = {}  # relative path -> base64 source (filled in by deploy-nodes.sh)
ANCHOR = '    if request.get("action") == "snapshot":\n'
RPC = '''    if request.get('action') == 'rules_status':
        from .fleet_rules import status
        return status(STATE)
    if request.get('action') == 'configure_rules':
        # Fleet rules (2026-10-14): the fleet-console admin console's rules document, validated and stored; journaled.
        from .fleet_rules import configure
        with (STATE/'rules.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return journal_request(request, lambda: configure(STATE, request))
'''
NAME = 'reclaimer-rules'

docker = Docker()
node = configured()
root = Path(node['data_root'])
code = root / 'code' / 'reclaimer_control'
agent = docker.inspect('reclaimer-agent')
if any(NAME == n.lstrip('/') for c in docker.containers() for n in c.get('Names', [])):
    raise SystemExit('reclaimer-rules already exists; inspect before replacing')
for rel, b64 in FILES.items():
    # identical leftovers (an earlier rollback keeps the code) are fine; anything different is not
    if (code / rel).exists() and (code / rel).read_bytes() != base64.b64decode(b64):
        raise SystemExit(f'{rel} already exists and differs; inspect before replacing')
agent_py = code / 'agent.py'
before = agent_py.read_text()
if 'configure_rules' in before:
    raise SystemExit('agent.py already has configure_rules; inspect before replacing')
if before.count(ANCHOR) != 1:
    raise SystemExit('agent.py anchor not found exactly once; not touching it')
patched = before.replace(ANCHOR, RPC + ANCHOR)
ast.parse(patched)
state_src = next(m['Source'] for m in agent['Mounts'] if m['Destination'] == '/state')
backup = root / 'deploy' / 'fleet-rules-20261014'
backup.mkdir(parents=True, exist_ok=True)
atomic_write(backup / 'agent.py.before', before, 0o644)
atomic_json(backup / 'agent-container.json', agent)

written = []
with action_lock():
    try:
        (code / 'oni_rules').mkdir(exist_ok=True)
        for rel, b64 in FILES.items():
            src = base64.b64decode(b64).decode()
            ast.parse(src)
            atomic_write(code / rel, src, 0o644)
            written.append(code / rel)
        rules_dir = STATE / 'rules'
        rules_dir.mkdir(exist_ok=True, mode=0o700)
        if not (rules_dir / 'policy.json').exists():
            atomic_json(rules_dir / 'policy.json', {'schema': 1, 'enabled': False, 'signature': None, 'at': time.time()})
        atomic_write(agent_py, patched, 0o644)
    except Exception:
        atomic_write(agent_py, before, 0o644)
        for p in written:
            p.unlink(missing_ok=True)
        raise

mounts = []
for m in agent['Mounts']:
    if m['Destination'] in ('/host/proc', '/host/sys'):
        continue
    mounts.append(m['Source'] + ':' + m['Destination'] + ':ro')
mounts.append(state_src.rstrip('/') + '/rules:/state/rules')
spec = {'Image': agent['Config']['Image'], 'Hostname': 'reclaimer-rules-' + node['id'],
        'Entrypoint': ['python', '-m', 'reclaimer_control.fleet_rules'], 'Cmd': [],
        'Env': ['PYTHONDONTWRITEBYTECODE=1', 'PYTHONUNBUFFERED=1', 'RECLAIMER_STATE=/state', 'PYTHONPATH=/app'],
        'Labels': {'us.ibigs.project': 'reclaimer', 'us.ibigs.role': 'fleet-rules', 'us.ibigs.node': node['id']},
        'HostConfig': {'Binds': mounts, 'NetworkMode': agent['HostConfig']['NetworkMode'],
                       'RestartPolicy': {'Name': 'unless-stopped'}, 'Memory': 128 * 1024 * 1024, 'NanoCpus': 250000000,
                       'PidsLimit': 64, 'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges:true'],
                       'ReadonlyRootfs': True, 'Tmpfs': {'/tmp': 'rw,noexec,nosuid,size=16m'},
                       'LogConfig': {'Type': 'json-file', 'Config': {'max-size': '3m', 'max-file': '2'}}}}
atomic_json(backup / 'rules-container.json', spec)
started, cid = time.time(), None


def undo():
    if cid:
        try:
            docker.request('DELETE', '/containers/' + cid + '?force=true&v=true')
        except Exception:
            pass
    with action_lock():
        atomic_write(agent_py, before, 0o644)
        for p in written:
            p.unlink(missing_ok=True)


try:
    cid = docker.request('POST', '/containers/create?name=' + NAME, spec)['Id']
    docker.request('POST', '/containers/' + cid + '/start')
except Exception:
    undo()
    raise
for _ in range(30):
    info = docker.inspect(NAME)
    health = read_json(STATE / 'rules' / 'health.json', {})
    if info['State']['Running'] and health.get('at', 0) > started and health.get('enabled') is False:
        print(json.dumps({'node': node['id'], 'service': NAME, 'image': spec['Image'], 'health': health,
                          'container_id': cid, 'rules_switch': 'OFF'}), flush=True)
        break
    time.sleep(1)
else:
    undo()
    raise SystemExit('reclaimer-rules did not publish fresh health with the switch off; rolled back')
