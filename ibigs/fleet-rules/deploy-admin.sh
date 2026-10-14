#!/bin/bash
# Fleet rules, step 2 of 3: the fleet-console admin console gets /api/rules (save + push to every node, history).
# Builds ibigs/reclaimer-admin:2026.10.14.1-rules = the running image + fleet_rules_admin.py, operator_console.py
# (three small hooks) and oni_rules/. Refuses if the running admin changed since this was prepared.
# Undo: bash rollback-admin.sh
set -euo pipefail
D=$(dirname "$(readlink -f "$0")")
OLD=ibigs/reclaimer-admin:2026.10.13.1-readcache NEW=ibigs/reclaimer-admin:2026.10.14.1-rules
CTL=$(ssh fleet-console 'docker inspect -f "{{.Config.Image}}" reclaimer-control')
[ "$(ssh fleet-console 'docker inspect -f "{{.Config.Image}}" reclaimer-admin')" = "$OLD" ] || { echo "admin image changed; re-prepare"; exit 2; }
[ "$(ssh fleet-console 'docker exec reclaimer-admin sha256sum /app/reclaimer_control/operator_console.py' | cut -d' ' -f1)" = \
  "$(sha256sum "$D/admin/operator_console.py.orig" | cut -d' ' -f1)" ] || { echo "running operator_console.py changed; re-prepare"; exit 2; }
CTX=$(mktemp -d); trap 'rm -rf "$CTX"' EXIT
mkdir -p "$CTX/rc" && cp -r "$D/admin/reclaimer_control/." "$CTX/rc/" && rm -rf "$CTX/rc/__pycache__" "$CTX/rc/oni_rules/__pycache__"
printf 'FROM %s\nCOPY rc/ /app/reclaimer_control/\n' "$OLD" > "$CTX/Dockerfile"
tar -C "$CTX" -cf - . | ssh fleet-console "docker build -q -t $NEW -"
ssh fleet-console "docker run --rm --entrypoint python $NEW -c 'import reclaimer_control.operator_console, reclaimer_control.fleet_rules_admin, reclaimer_control.oni_rules.rules as r; print(len(r.STARTERS), \"templates\")'"
ssh fleet-console "docker run --rm -i --user 0 --network host --entrypoint python -v /var/run/docker.sock:/var/run/docker.sock -v /srv/appdata/reclaimer:/provider $CTL -" < "$D/promote-admin.py" | tee "$D/admin-promotion.json"
echo "Done: /api/rules is live, rules switch OFF. Next: bash deploy-console.sh"
