#!/bin/bash
# Fleet rules, step 1 of 3: every node gets the reclaimer-rules worker and the configure_rules / rules_status RPC,
# with the rules switch OFF (no game connection until the admin console turns it on). Stops at the first failure;
# each node rolls itself back if its worker doesn't come up healthy. Undo: bash rollback-nodes.sh
# Usage: bash deploy-nodes.sh [node-a node-b node-e node-d]
set -euo pipefail
D=$(dirname "$(readlink -f "$0")")
SRC=/home/user/Projects/oni-rcon-sessions/src/oni_rcon
for f in rules rules_run schedule safety; do   # the engine must be oni-rcon's, byte for byte
  cmp -s "$SRC/$f.py" "$D/agent/reclaimer_control/oni_rules/$f.py" || { echo "oni_rules/$f.py differs from oni-rcon"; exit 2; }
done
PAYLOAD=$(mktemp); trap 'rm -f "$PAYLOAD"' EXIT
python3 - "$D" > "$PAYLOAD" <<'PY'
import base64, sys, pathlib
d = pathlib.Path(sys.argv[1]); a = d / 'agent' / 'reclaimer_control'
files = {'fleet_rules.py': a / 'fleet_rules.py', **{f'oni_rules/{p.name}': p for p in sorted((a / 'oni_rules').glob('*.py'))}}
src = (d / 'deploy-node.py').read_text()
blob = '{' + ', '.join(f'{k!r}: {base64.b64encode(v.read_bytes()).decode()!r}' for k, v in files.items()) + '}'
assert src.count('FILES = {}') == 1
print(src.replace('FILES = {}', 'FILES = ' + blob, 1))
PY
nodes=("$@"); [ $# -eq 0 ] && nodes=(node-a node-b node-e node-d)
for n in "${nodes[@]}"; do
  echo "== $n"
  case $n in
    node-a) ssh node-a 'docker exec -i reclaimer-agent python -' < "$PAYLOAD" | tee "$D/deploy-$n.json" ;;
    node-c)  ssh node-c 'docker exec -i reclaimer-agent python -' < "$PAYLOAD" | tee "$D/deploy-$n.json" ;;
    node-b)    ssh node-b 'pct exec 920 -- docker exec -i reclaimer-agent python -' < "$PAYLOAD" | tee "$D/deploy-$n.json" ;;
    node-e)   ssh node-e 'pct exec 910 -- docker exec -i reclaimer-agent python -' < "$PAYLOAD" | tee "$D/deploy-$n.json" ;;
    node-d)   ssh node-d 'pct exec 911 -- docker exec -i reclaimer-agent python -' < "$PAYLOAD" | tee "$D/deploy-$n.json" ;;
    *) echo "unknown node $n"; exit 2 ;;
  esac
done
echo "Done: the worker runs on every node, rules switch OFF. Next: bash deploy-admin.sh"
