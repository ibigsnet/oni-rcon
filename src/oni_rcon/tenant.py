"""Customer console: direct RCON for the lobbies discovered inside this customer's VM."""
import asyncio
import json
import os
import subprocess

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Select, Static

from .app import OniApp, Confirm, Dialog
from .safety import clean

# IBIGS 2026-10-09: F7 = start / stop / restart this VM's lobbies. The work is done by the `oni` wrapper
# (IBIGS_ONI_POWER, `oni status --json` / `oni stop h3-01 --yes --force`): `docker compose stop` with the stack's own
# SIGINT + grace period, `docker compose up -d` to start. The console only shows, asks and confirms.
POWER_TIMEOUT = 900


def players_of(lob):
    if not lob.get('running'):
        return '-'
    if lob.get('players') is None:
        return '?'
    return f"{len(lob['players'])}/{lob.get('max') or '?'}"


def busy(lob):
    return bool(lob.get('running')) and (lob.get('players') is None or bool(lob.get('players')))


def table(lobs):
    t = Text()
    t.append(f"{'LOBBY':<8} {'STATE':<9} {'PLAYERS':<8} NAME IN THE SERVER LIST\n", style='bold')
    for lob in lobs:
        t.append(f"{clean(lob['service']):<8} ")
        t.append(f"{'RUNNING' if lob.get('running') else 'STOPPED':<9} ",
                 style='bold green' if lob.get('running') else 'yellow')
        t.append(f"{players_of(lob):<8} ", style='bold red' if busy(lob) else '')
        t.append(clean(str(lob.get('name', ''))) + '\n')
    return t


class PowerPanel(Dialog):
    """Pick ALL or one lobby, then STOP / START / RESTART. Returns (action, [services]) or None."""
    BINDINGS = [Binding('escape', 'dismiss(None)', show=False)]

    def __init__(self, lobs):
        super().__init__()
        self.lobs = lobs

    def compose(self) -> ComposeResult:
        with Vertical(classes='dialog power'):
            yield Static(Text('START / STOP SERVERS'), classes='dialog-title')
            yield Static(table(self.lobs), classes='dialog-body')
            options = [(f'ALL SERVERS ({len(self.lobs)})', '*')] + \
                      [(f"{clean(l['service'])}  {clean(str(l.get('name', '')))}", l['service']) for l in self.lobs]
            yield Select(options, allow_blank=False, value='*', id='power-target')
            with Horizontal(classes='dialog-buttons'):
                yield Button('CLOSE', id='no')
                yield Button('START', id='start', variant='success')
                yield Button('RESTART', id='restart', variant='warning')
                yield Button('STOP', id='stop', variant='error')
            yield self.keys('Esc close', 'Tab next', 'Enter choose', 'players are always shown before anything happens')

    def on_mount(self) -> None:
        super().on_mount()
        self.query_one('#power-target').focus()

    def on_button_pressed(self, e: Button.Pressed) -> None:
        e.stop()
        if e.button.id == 'no':
            self.dismiss(None)
            return
        target = self.query_one('#power-target', Select).value
        chosen = [l['service'] for l in self.lobs] if target == '*' else [target]
        self.dismiss((e.button.id, chosen))


class TenantApp(OniApp):
    TITLE = 'IBIGS Server Console'
    BINDINGS = [*OniApp.BINDINGS, Binding('ctrl+f', 'goto', 'Find server', priority=True),
                Binding('f7', 'power', 'Start/Stop', priority=True),
                Binding('f8', 'jobs', 'Jobs', priority=True), Binding('f9', 'updates', 'Updates', priority=True)]

    def welcome(self):
        super().welcome()
        self.notify('Managing this VM’s lobbies. F7 starts or stops servers. Ctrl+F finds a server; F6 opens '
                    'ReclaimerForge. Use your own Forge key. Installing content never restarts a game.',
                    title='IBIGS', timeout=8)

    def compose_stations(self) -> ComposeResult:
        yield from super().compose_stations()
        yield Button('⏻ START / STOP SERVERS  F7', id='power-btn', compact=True, variant='warning')
        yield Button('SCHEDULED JOBS  F8', id='jobs-btn', compact=True)
        yield Button('UPDATES  F9', id='updates-btn', compact=True)

    def sidebar_room(self):
        room, need, card = super().sidebar_room()
        return room - 4, need, card  # Three compact controls and their top margin.

    @on(Button.Pressed, '#jobs-btn')
    def _jobs_button(self, e: Button.Pressed) -> None:
        e.stop()
        self.action_jobs()

    @on(Button.Pressed, '#updates-btn')
    def _updates_button(self, e: Button.Pressed) -> None:
        e.stop()
        self.action_updates()

    @work(exclusive=True, group='dialog')
    async def action_jobs(self) -> None:
        from .jobs_ui import jobs_dialog
        rc, out = await self.power_run('status', '--json')
        try:
            targets = [l['service'] for l in json.loads(out)] if rc == 0 else []
        except (ValueError, KeyError):
            targets = []
        await jobs_dialog(self, targets)

    @work(exclusive=True, group='dialog')
    async def action_updates(self) -> None:
        from .jobs_ui import updates_dialog
        await updates_dialog(self)

    @on(Button.Pressed, '#power-btn')
    def _power_button(self, e: Button.Pressed) -> None:
        e.stop()
        self.action_power()

    async def power_run(self, *args):
        tool = os.environ.get('IBIGS_ONI_POWER', '')
        if not tool:
            return 127, 'Start/stop needs the IBIGS oni command: leave and type  oni servers'

        def run():
            try:
                r = subprocess.run([tool, *args], capture_output=True, text=True, timeout=POWER_TIMEOUT,
                                   stdin=subprocess.DEVNULL)
                return r.returncode, (r.stdout + r.stderr)
            except (OSError, subprocess.TimeoutExpired) as exc:
                return 1, str(exc)
        return await asyncio.to_thread(run)

    @work(exclusive=True, group='dialog')
    async def action_power(self) -> None:
        rc, out = await self.power_run('status', '--json')
        try:
            lobs = json.loads(out.strip().splitlines()[0]) if rc == 0 else None
        except (ValueError, IndexError):
            lobs = None
        if not lobs:
            self.notify(out.strip() or 'Could not read the server list.', title='Start/Stop', severity='warning',
                        timeout=10)
            return
        choice = await self.push_screen_wait(PowerPanel(lobs))
        if not choice:
            return
        action, services = choice
        chosen = [l for l in lobs if l['service'] in services]
        if action == 'start':
            todo = [l for l in chosen if not l.get('running')]
        elif action == 'stop':
            todo = [l for l in chosen if l.get('running')]
        else:
            todo = chosen
        if not todo:
            self.notify(f"Nothing to {action}: already {'running' if action == 'start' else 'stopped'}.",
                        title='Start/Stop', timeout=6)
            return
        hot = [l for l in todo if busy(l)] if action != 'start' else []
        lines = [f"{action.upper()} {len(todo)} server(s):", '']
        lines += [f"  {l['service']}  {clean(str(l.get('name', '')))}  players {players_of(l)}" for l in todo]
        if hot:
            lines += ['']
            for l in hot:
                who = ', '.join(clean(p) for p in (l.get('players') or []))
                lines.append(f"  !! {l['service']}: " + (f"{len(l['players'])} online: {who}" if l.get('players')
                                                          else "player list unreadable: people MAY be playing"))
            lines.append(f"  They will be DISCONNECTED{' for about a minute' if action == 'restart' else ''}.")
        if action == 'stop':
            lines += ['', 'Stopped servers stay stopped (also after a reboot) until you start them again.']
        verb = (f'DISCONNECT PLAYERS + {action.upper()}' if hot else action.upper())
        if not await self.push_screen_wait(Confirm(f'CONFIRM {action.upper()}', '\n'.join(lines), verb=verb,
                                                   danger=bool(hot) or action != 'start')):
            return
        if hot and not await self.push_screen_wait(Confirm('PLAYERS ARE ONLINE',
                f'Last check: {len(hot)} server(s) may have players. Really {action}?', verb=verb)):
            return
        self.notify(f"{action.capitalize()}ing {', '.join(l['service'] for l in todo)}... (a clean stop can take up "
                    "to 45 s per server)", title='Start/Stop', timeout=8)
        # The helper re-reads occupancy immediately before acting. An earlier empty snapshot
        # is not permission to disconnect somebody who joined while the dialog was open.
        flags = ['--yes', '--force'] if hot else ['--yes']
        rc, out = await self.power_run(action, *[l['service'] for l in todo], *flags)
        if rc == 4:
            self.notify('Player occupancy changed or is uncertain. Nothing was stopped. Open F7 again to review '
                        'the current players before retrying.', title='Start/Stop: review players',
                        severity='warning', timeout=15)
            self.action_refresh()
            return
        done = [ln.strip() for ln in out.splitlines() if 'done.' in ln or 'FAILED' in ln or 'already' in ln]
        self.notify('\n'.join(done) or out.strip()[-400:], title=f"{action.capitalize()}: "
                    + ('done' if rc == 0 else 'problem'), severity='information' if rc == 0 else 'error', timeout=15)
        self.action_refresh()
