# Fleet rules: the node and admin-console side

oni-rcon's rules sandbox for the IBIGS fleet. The engine is `src/oni_rcon/rules.py`, with `rules_run.py`,
`schedule.py` and `safety.py`. The deploy copies those files unchanged into each node's and the admin console's
`reclaimer_control/oni_rules/` and refuses to run if any of them differ.

| File | Goes to | What it does |
|---|---|---|
| `agent/fleet_rules.py` | each node, `reclaimer_control/` | `configure` / `status` (RPC) and the `reclaimer-rules` worker |
| `deploy-node.py` | run inside each node's agent | adds the files, two RPC actions in `agent.py` and the worker container, with the switch OFF; rolls back if unhealthy |
| `admin/fleet_rules_admin.py`, `admin/operator_console.patch` | fleet admin console (`reclaimer-admin`) | `GET/POST /api/rules`: saves, then pushes to every node with the journaled `configure_rules` |

The tests run against copies of the live `reclaimer_control` package (not in this repo):
`test_fleet_rules.py` (worker, end to end), `test_agent_rpc.py` (the patched RPC) and `test_admin_rules.py`.

The scripts that roll these files out (and back) depend on IBIGS's own hosts, so they live in IBIGS's private tooling,
not here. `deploy-node.py` is the part that runs inside a node's agent.
