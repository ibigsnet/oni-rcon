"""Read-only fleet health from the controller's existing shared snapshot.

One bounded HTTP read covers the whole fleet. No Docker access, log subprocesses,
SSH credentials or extra game polling belong in the terminal.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import math
import time
import urllib.request

from rich.table import Table
from rich.text import Text
from textual.widgets import Static, TabbedContent

from .app import Roster
from .art import AMBER, CYAN, DIM, GREEN, RED, WHITE
from .health import HealthError

SNAPSHOT = 'http://192.0.2.10:9188/snapshot'
MAX_BYTES = 8 * 1024 * 1024


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def project(snapshot, now=None):
    """Retain only health fields; stale/unknown readings never become zeros."""
    now = time.time() if now is None else now
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('nodes'), list):
        raise HealthError('invalid fleet health snapshot')
    nodes, lobbies = {}, {}
    for node in snapshot['nodes'][:64]:
        if not isinstance(node, dict) or not isinstance(node.get('id'), str):
            continue
        ident = node['id']
        observed = number(node.get('observed_at'))
        fresh = observed is not None and 0 <= now - observed <= 60 and not node.get('transport_error')
        resources = node.get('resources') or {}
        if not isinstance(resources, dict):
            resources = {}
        nodes[ident] = {'at': observed, 'fresh': bool(fresh), **{k: number(resources.get(k)) for k in
            ('memory_available_bytes', 'cpu_fraction', 'tx_mbps', 'rx_mbps')}}
        for row in (node.get('lobbies') or [])[:2048]:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str):
                continue
            lobbies[row['id']] = {'node': ident, 'at': observed, 'fresh': bool(fresh),
                'started_at': number(row.get('started_at')), 'restart_count': number(row.get('restart_count')),
                'oom_killed': row.get('oom_killed') if isinstance(row.get('oom_killed'), bool) else None}
    return {'nodes': nodes, 'lobbies': lobbies}


def read_snapshot(opener):
    # Fixed private controller destination and no Authorization header: never forward the
    # private console credential to a second endpoint or an HTTP redirect.
    request = urllib.request.Request(SNAPSHOT, headers={'Accept': 'application/json'})
    with opener.open(request, timeout=8) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HealthError('fleet health snapshot exceeds 8 MiB')
    return project(json.loads(data))


class FleetHealthMixin:
    def watched(self, station):
        return getattr(self.fleet, 'health_enabled', False) is True and station.server.url.removeprefix('fleet://') in self.fleet.rows

    def health_tick(self):
        if (not self.is_running or getattr(self.fleet, 'health_enabled', False) is not True
                or self.fleet_health_busy or time.monotonic() < self.fleet_health_due):
            return
        self.fleet_health_busy = True
        self.run_worker(self.refresh_health(), group='fleet-health', exit_on_error=False)

    async def refresh_health(self):
        try:
            data = await asyncio.to_thread(self.fleet.health_snapshot)
            self.fleet_health = data
            self.fleet_health_error = ''
            for station in self.stations:
                if not self.watched(station):
                    continue
                row = data['lobbies'].get(station.server.url.removeprefix('fleet://'))
                if not row or not row['fresh']:
                    station.health_err = 'Node telemetry stale or unavailable; last reading retained'
                    continue
                station.health_err = ''
                station.health_ok = row['at']
                host = data['nodes'][row['node']]
                report = {'kind': 'host', 'now': row['at']}
                if host['memory_available_bytes'] is not None:
                    report['mem_available_mb'] = host['memory_available_bytes'] / 1048576
                lines = [json.dumps(report)]
                key = str(station.index)
                previous = self.mon.boxes.get(key)
                # Unknown OOM status is not "false". Do not fabricate a box or
                # infer an OOM from exit 137, which also occurs after SIGKILL.
                if row['started_at'] and row['oom_killed'] is not None:
                    box = {'kind': 'container', 'started': datetime.fromtimestamp(row['started_at'], timezone.utc).isoformat(),
                           'oom_killed': row['oom_killed']}
                    if row['restart_count'] is not None:
                        box['restarts'] = row['restart_count']
                    # Docker's OOMKilled flag persists. Announce a transition,
                    # not the same old flag on every shared snapshot.
                    box['oom_killed'] = row['oom_killed'] and previous is not None and not previous.oom_killed
                    lines.append(json.dumps(box))
                for notice in self.mon.ingest(key, row['node'], '\n'.join(lines)):
                    # Snapshot telemetry cannot prove a native RCON disconnect
                    # or how many people a restart displaced.
                    if notice.key and 'player' in notice.text:
                        notice.text = notice.text.split('  ·  ')[0]
                    self.health_notice(notice)
                if key in self.mon.boxes and row['oom_killed'] is not None:
                    self.mon.boxes[key].oom_killed = row['oom_killed']
        except Exception as exc:
            # Raw response bodies, file paths and credentials do not belong in errors.
            self.fleet_health_error = f'Fleet health unavailable ({type(exc).__name__}); last reading retained'
        finally:
            self.fleet_health_busy = False
            self.fleet_health_due = time.monotonic() + 15
        if self.is_running and self.query_one(TabbedContent).active == 'health':
            self.paint_health()

    def paint_health(self):
        if getattr(self.fleet, 'health_enabled', False) is not True:
            super().paint_health()
            return
        # Fleet links use cached status rather than native WebSocket events.
        # Leave drop/count columns unknown instead of attributing a disconnect.
        for event in self.mon.events:
            event.rcon, event.players = False, None
        super().paint_health()
        self.query_one('#health-setup', Static).display = True
        self.query_one('#health-setup', Static).update(Text(
            self.fleet_health_error or 'Cached fleet telemetry · one read per 15s · native crash logs and workaround services are not collected here.',
            AMBER if self.fleet_health_error else DIM))
        table = self.query_one('#crashes', Roster)
        table.border_title = 'CONTAINER EVENTS · OBSERVED DURING THIS SESSION'
        table.border_subtitle = 'Native worker crashes are not covered by this feed'
        host_table = Table.grid(padding=(0, 2), expand=True)
        narrow = self.size.width < 120
        if narrow:
            host_table.padding = (0, 1)
        for _ in range(5 if narrow else 6):
            host_table.add_column(no_wrap=True)
        headings = ('NODE', 'CPU', 'FREE GiB', 'RX/TX Mb/s', 'AGE') if narrow else ('NODE', 'CPU', 'FREE RAM', 'RX / TX Mb/s', 'AGE', 'STATE')
        host_table.add_row(*[Text(t, CYAN) for t in headings])
        now = time.time()
        for ident, row in sorted(self.fleet_health.get('nodes', {}).items()):
            age = now - row['at'] if row['at'] is not None else None
            fresh = row['fresh'] and age is not None and 0 <= age <= 60 and not self.fleet_health_error
            colour = WHITE if fresh else AMBER
            cpu = f"{row['cpu_fraction'] * 100:.0f}%" if row['cpu_fraction'] is not None else '—'
            memory = f"{row['memory_available_bytes'] / 1073741824:.1f}" + ('' if narrow else ' GiB') if row['memory_available_bytes'] is not None else '—'
            rate = lambda key: f'{row[key]:.1f}' if row[key] is not None else '—'
            cells = [Text(ident.upper(), colour), Text(cpu, colour), Text(memory, colour),
                     Text(rate('rx_mbps') + ('/' if narrow else ' / ') + rate('tx_mbps'), colour),
                     Text('STALE' if narrow and not fresh else f'{age:.0f}s' if age is not None else '—', colour)]
            if not narrow:
                cells.append(Text('CURRENT' if fresh else 'STALE', GREEN if fresh else RED))
            host_table.add_row(*cells)
        self.query_one('#host', Static).update(host_table)
        self.query_one('#host-box').border_title = 'NODES · HOST CPU / MEMORY / NETWORK'
