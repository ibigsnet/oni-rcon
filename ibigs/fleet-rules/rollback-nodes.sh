#!/bin/bash
# Removes reclaimer-rules and puts agent.py back as it was before (fleet_rules.py and oni_rules/ stay, unused).
set -euo pipefail
PY='
from pathlib import Path
from reclaimer_control.agent import configured
from reclaimer_control.common import Docker, atomic_write
d = Docker(); root = Path(configured()["data_root"])
try: d.request("DELETE", "/containers/reclaimer-rules?force=true&v=true")
except Exception as e: print("no container:", e)
b = root / "deploy/fleet-rules-20261014/agent.py.before"
atomic_write(root / "code/reclaimer_control/agent.py", b.read_text(), 0o644); print("restored agent.py")
'
nodes=("$@"); [ $# -eq 0 ] && nodes=(node-a node-b node-e node-d)
for n in "${nodes[@]}"; do
  case $n in
    node-a|node-c) echo "$PY" | ssh $n 'docker exec -i reclaimer-agent python -' ;;
    node-b)  echo "$PY" | ssh node-b 'pct exec 920 -- docker exec -i reclaimer-agent python -' ;;
    node-e) echo "$PY" | ssh node-e 'pct exec 910 -- docker exec -i reclaimer-agent python -' ;;
    node-d) echo "$PY" | ssh node-d 'pct exec 911 -- docker exec -i reclaimer-agent python -' ;;
  esac
done
