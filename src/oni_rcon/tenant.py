"""Customer console: direct RCON for the lobbies discovered inside this customer's VM."""
import asyncio
import json
import os
import subprocess

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Select, Static

from .app import OniApp, Confirm, Dialog, Form, Pick
from .safety import clean

# IBIGS 2026-10-09: F7 = start / stop / restart this VM's lobbies. The work is done by the `oni` wrapper
# (IBIGS_ONI_POWER, `oni status --json` / `oni stop h3-01 --yes --force`): `docker compose stop` with the stack's own
# SIGINT + grace period, `docker compose up -d` to start. The console only shows, asks and confirms.
POWER_TIMEOUT = 900


def clean_block(text: str) -> str:
    """A helper's output for a dialog: colours and control characters out, line breaks kept."""
    import re
    text = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', text or '')
    return '\n'.join(clean(ln) for ln in text.strip().splitlines())[-6000:]


def players_of(lob):
    if not lob.get('running'):
        return '-'
    if lob.get('players') is None:
        return '-' if lob.get('phase') in ('waiting', 'starting') else '?'
    return f"{len(lob['players'])}/{lob.get('max') or '?'}"


def busy(lob):
    return bool(lob.get('running')) and (lob.get('players') is None or bool(lob.get('players')))


def table(lobs):
    t = Text()
    t.append(f"{'LOBBY':<8} {'STATE':<9} {'PLAYERS':<8} NAME IN THE SERVER LIST\n", style='bold')
    for lob in lobs:
        t.append(f"{clean(lob['service']):<8} ")
        phase = lob.get('phase') or ''
        label = {'waiting': 'WAITING', 'starting': 'STARTING'}.get(phase, 'RUNNING') if lob.get('running') else 'STOPPED'
        t.append(f"{label:<9} ", style={'WAITING': 'bold red', 'STARTING': 'bold cyan', 'RUNNING': 'bold green'}
                 .get(label, 'yellow'))
        t.append(f"{players_of(lob):<8} ", style='bold red' if busy(lob) else '')
        t.append(clean(str(lob.get('name', ''))) + '\n')
        # 2026-10-15: a game server that never reported in used to read RUNNING ? for hours
        if phase == 'waiting':
            t.append(f"         └ waiting on: {clean(str(lob.get('why') or 'its start-up check'))}. "
                     "It retries every 15 s; RESTART shows the full message.\n", style='red')
        elif phase == 'starting':
            t.append("         └ starting up: usually 1-3 minutes, longer right after an update.\n", style='dim')
    return t


class PowerPanel(Dialog):
    """Pick ALL or one lobby, then STOP / START / RESTART. Returns (action, [services]) or None."""
    # Keys so nobody has to count Tab presses: 0 = all lobbies, 1-9 = that lobby; S start, R restart, X stop.
    BINDINGS = [Binding('escape', 'dismiss(None)', show=False),
                Binding('left', 'app.focus_previous', show=False), Binding('right', 'app.focus_next', show=False),
                *[Binding(str(n), f'pick({n})', show=False, priority=True) for n in range(10)],
                Binding('s', "act('start')", show=False, priority=True),
                Binding('r', "act('restart')", show=False, priority=True),
                Binding('x', "act('stop')", show=False, priority=True),
                Binding('d', "act('remove')", show=False, priority=True)]

    def __init__(self, lobs, removed=None):
        super().__init__()
        self.lobs, self.removed = lobs, removed or []

    def action_pick(self, n: int) -> None:
        sel = self.query_one('#power-target', Select)
        if n == 0:
            sel.value = '*'
        elif n <= len(self.lobs):
            sel.value = self.lobs[n - 1]['service']
        else:
            return
        if sel.expanded:
            sel.expanded = False
        sel.focus()

    def action_act(self, what: str) -> None:
        for b in self.query(f'#{what}').results(Button):
            b.press()

    def compose(self) -> ComposeResult:
        with Vertical(classes='dialog power'):
            yield Static(Text('START / STOP SERVERS'), classes='dialog-title')
            # 2026-10-15: six lobbies plus their STARTING/WAITING lines outgrow a small window: this part scrolls
            with VerticalScroll(classes='dialog-body power-lobbies'):
                yield Static(table(self.lobs))
            options = [(f'0  ALL SERVERS ({len(self.lobs)})', '*')] + \
                      [(f"{i if i < 10 else ' '}  {clean(l['service'])}  {clean(str(l.get('name', '')))}", l['service'])
                       for i, l in enumerate(self.lobs, 1)]
            # The first lobby, not ALL: ALL + START woke every spare lobby on the first customer's server.
            yield Select(options, allow_blank=False, value=self.lobs[0]['service'] if self.lobs else '*',
                         id='power-target')
            with Horizontal(classes='dialog-buttons'):
                yield Button('CLOSE', id='no')
                yield Button('START  S', id='start', variant='success')
                yield Button('RESTART  R', id='restart', variant='warning')
                yield Button('STOP  X', id='stop', variant='error')
            # 2026-10-15: lobbies nobody uses can go (kept 7 days), and come back
            with Horizontal(classes='dialog-buttons'):
                yield Button('REMOVE LOBBY  D', id='remove', variant='error')
                if self.removed:
                    yield Button(f'RESTORE A REMOVED LOBBY ({len(self.removed)})', id='restore', variant='primary')
            yield self.keys('0-9 pick a lobby', 'S start · R restart · X stop · D remove', '← → or Tab move',
                            'Esc close', 'players are always shown before anything happens')

    def on_mount(self) -> None:
        super().on_mount()
        self.query_one('#power-target').focus()

    def on_button_pressed(self, e: Button.Pressed) -> None:
        e.stop()
        if e.button.id == 'no':
            self.dismiss(None)
            return
        if e.button.id == 'restore':
            self.dismiss(('restore', []))
            return
        target = self.query_one('#power-target', Select).value
        chosen = [l['service'] for l in self.lobs] if target == '*' else [target]
        self.dismiss((e.button.id, chosen))


class Notice(Dialog):
    """Plain text to read (what a start/restart or a Workshop install said), with a CLOSE button."""
    BINDINGS = [Binding('escape', 'dismiss(None)', show=False), Binding('enter', 'dismiss(None)', show=False)]

    def __init__(self, title: str, body: str, warn: bool = False):
        super().__init__()
        self.t, self.body, self.warn = title, body, warn

    def compose(self) -> ComposeResult:
        with Vertical(classes='dialog danger' if self.warn else 'dialog'):
            yield Static(Text(self.t), classes='dialog-title')
            with VerticalScroll(classes='notice-body'):
                yield Static(Text(self.body), classes='dialog-body')
            with Horizontal(classes='dialog-buttons'):
                yield Button('CLOSE', id='no', variant='primary')
            yield self.keys('Enter or Esc close')

    def on_mount(self) -> None:
        super().on_mount()
        self.query_one('#no').focus()

    def on_button_pressed(self, e: Button.Pressed) -> None:
        e.stop()
        self.dismiss(None)


def checklist_text(items: list) -> tuple[str, bool]:
    """(text, everything done) for the getting-started checklist from `oni checklist --json`."""
    lines, nxt = [], None
    for i in items:
        lines.append(('  [x] ' if i.get('ok') else '  [ ] ') + clean(str(i.get('text', ''))))
        if not i.get('ok') and nxt is None:
            nxt = clean(str(i.get('next', '')))
    if nxt:
        lines += ['', 'NEXT STEP:', '  ' + nxt]
    lines += ['', 'Step-by-step guide: https://ibigs.us/halo-3/guide   ·   F12 opens this again']
    return '\n'.join(lines), nxt is None


class TenantApp(OniApp):
    TITLE = 'IBIGS Server Console'
    PLUGINS_KEY, PLUGINS_TAB = '', False  # customer consoles don't run plug-ins; F8 is AUTOMATION here
    BINDINGS = [*[b for b in OniApp.BINDINGS if b.key != 'f8'], Binding('ctrl+f', 'goto', 'Find server', priority=True),
                Binding('f7', 'power', 'Start/Stop', priority=True),
                Binding('f8', 'jobs', 'Automation', priority=True), Binding('f9', 'updates', 'Updates', priority=True),
                Binding('f12', 'settings_menu', 'Settings & mods', priority=True),
                Binding('e', 'give_role', 'Give role', show=False)]

    def welcome(self):
        super().welcome()
        self.notify('Managing this VM’s lobbies. F7 starts or stops servers; F3 PLAYLISTS deploys playlists. F8 '
                    'AUTOMATION: rules (sprees, !admin, server alerts), schedules, roles, votes and Discord. On F1, e '
                    'gives the selected player a role. F6 opens ReclaimerForge. F12: settings and Workshop mods.',
                    title='IBIGS', timeout=10)
        self.run_worker(self.first_run_checklist(), exclusive=False, group='checklist')

    def offline_hint(self, st) -> str:
        return (f"{st.label} isn't running or isn't answering yet, so these buttons are off. "
                "Start it with F7 (or wait a minute if you just did); the buttons wake up once it's running.")

    def compose_stations(self) -> ComposeResult:
        yield from super().compose_stations()
        yield Button('⏻ START / STOP SERVERS  F7', id='power-btn', compact=True, variant='warning')
        yield Button('AUTOMATION  F8', id='jobs-btn', compact=True)
        yield Button('UPDATES  F9', id='updates-btn', compact=True)
        yield Button('SETTINGS & MODS  F12', id='settings-btn', compact=True)

    def sidebar_room(self):
        room, need, card = super().sidebar_room()
        return room - 5, need, card  # Four compact controls and their top margin.

    @on(Button.Pressed, '#settings-btn')
    def _settings_button(self, e: Button.Pressed) -> None:
        e.stop()
        self.action_settings_menu()

    async def first_run_checklist(self, always: bool = False) -> None:
        """Shown at start until the basics are done (installed, running, answering, named); F12 shows it any time."""
        if not self.playlist_tool():
            return
        rc, out = await self.tool_run('checklist', '--json', timeout=60)
        try:
            items = json.loads(out.strip().splitlines()[-1]) if rc == 0 else None
        except (ValueError, IndexError):
            items = None
        if not items:
            return
        text, done = checklist_text(items)
        if always or not done:
            await self.push_screen_wait(Notice('GETTING STARTED', text))

    @work(exclusive=True, group='dialog')
    async def action_settings_menu(self) -> None:
        what = await self.push_screen_wait(Pick('SETTINGS & MODS', [
            ('Lobby settings: name, player limit, map, game type', 'settings'),
            ('Install a Workshop mod (Steam link)', 'workshop'),
            ('Installed Workshop mods', 'mods'),
            ('Getting-started checklist', 'checklist')]))
        if what == 'settings':
            await self.lobby_settings()
        elif what == 'workshop':
            await self.workshop_install()
        elif what == 'mods':
            rc, out = await self.tool_run('workshop', 'list', timeout=60)
            await self.push_screen_wait(Notice('WORKSHOP MODS', clean_block(out) or 'None installed yet.'))
        elif what == 'checklist':
            await self.first_run_checklist(always=True)

    async def lobby_settings(self) -> None:
        names = await self.lobby_names()
        if not names:
            self.notify('Could not read your lobbies.', title='Settings', severity='warning', timeout=8)
            return
        lobby = names[0] if len(names) == 1 else await self.push_screen_wait(
            Pick('WHICH LOBBY?', [(n, n) for n in names]))
        if not lobby:
            return
        rc, out = await self.tool_run('settings', lobby, '--json', timeout=60)
        try:
            cur = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            await self.push_screen_wait(Notice('SETTINGS', clean_block(out), warn=True))
            return
        show = lambda v: '' if v is None else str(v)
        fields = [('name', 'Name in the server list', show(cur.get('name'))),
                  ('max_players', 'Most players at once (1-127)', show(cur.get('max_players'))),
                  ('min_players', 'Players needed to start a game', show(cur.get('min_players'))),
                  ('map', 'Map it starts on', show(cur.get('map'))),
                  ('game', 'Game type it starts on', show(cur.get('game')))]
        if cur.get('running') and not cur.get('live'):
            fields.append(('restart', 'Apply', [('Restart it now if nobody is playing', 'yes'),
                                               ('Save only: applies at the next restart', 'no')]))
        note = ('Map and game type must be names the server lists (F3 > CHANGE MAP shows them). Workshop maps: '
                'install the mod first (F12 > Install a Workshop mod).')
        if cur.get('live'):  # Reclaimer 0.9.18+: the server applies a saved change by itself, players stay on
            note += (' Saving applies it straight away: players stay on. A new name or starting map makes the '
                     'lobby restart by itself once nobody is playing.')
        got = await self.push_screen_wait(Form(f'SETTINGS · {lobby}', fields, verb='SAVE', note=note))
        if not got:
            return
        changes = [f'{k}={got[k]}' for k, *_ in fields
                   if k != 'restart' and got.get(k, '') != show(cur.get(k)) and got.get(k, '') != '']
        if not changes:
            self.notify('Nothing changed.', title=f'Settings · {lobby}', timeout=5)
            return
        flags = ['--restart'] if got.get('restart') == 'yes' else []
        rc, out = await self.tool_run('settings', lobby, *changes, *flags, '--json', timeout=POWER_TIMEOUT)
        try:
            r = json.loads(out.strip().splitlines()[-1])
            text, ok = clean(str(r.get('text', ''))), r.get('state') in ('saved', 'applied')
            if r.get('state') == 'refused':
                text = 'The server checked it and said no, so nothing changed: ' + text
        except (ValueError, IndexError):
            text, ok = clean_block(out), rc == 0
        if ok:
            self.notify(text, title=f'Settings · {lobby}: saved', timeout=12)
            self.action_refresh()
        else:
            await self.push_screen_wait(Notice(f'SETTINGS · {lobby}: NOT CHANGED', text, warn=True))

    async def workshop_install(self) -> None:
        got = await self.push_screen_wait(Form('INSTALL A WORKSHOP MOD', [
            ('link', 'Steam Workshop link (or the item number)', '')], verb='INSTALL', required=('link',),
            note='Halo 3 mods from Halo: The Master Chief Collection\'s Steam Workshop. It downloads with YOUR Steam '
                 'account (it must own MCC): if this server is signed out of Steam, the next screen asks you to sign '
                 'in. Steam asks for the password itself; oni never sees it.'))
        if not got:
            return
        tool = self.playlist_tool()
        try:
            with self.suspend():  # back to the plain terminal: Steam's own password / Steam Guard prompts need it
                print('\n=== oni: installing a Workshop mod ===\n', flush=True)
                subprocess.run([tool, 'workshop', 'add', got['link']])
                try:
                    input('\nPress Enter to go back to oni... ')
                except EOFError:
                    pass
        except Exception:  # no real terminal (tests, some web consoles): run it here, sign-in not possible
            rc, out = await self.tool_run('workshop', 'add', got['link'], timeout=3600)
            await self.push_screen_wait(Notice('WORKSHOP', clean_block(out), warn=rc != 0))
        self.action_refresh()

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
        from .automation_ui import automation_dialog
        await automation_dialog(self, await self.lobby_names())

    async def lobby_names(self) -> list:
        rc, out = await self.power_run('status', '--json')
        try:
            return [l['service'] for l in json.loads(out)] if rc == 0 else []
        except (ValueError, KeyError):
            return []

    @work(exclusive=True, group='dialog')
    async def action_give_role(self) -> None:
        """e on F1: give the selected player ADMIN, VIP or MOD."""
        from .automation_ui import add_role
        from .rules import RuleStore
        p = self.cur_player()
        pid = str((p or {}).get('player_id') or (p or {}).get('id') or '')
        if not p or len(pid) != 64:
            self.notify('Choose a player on F1 first (their ID must be known).', title='Roles', timeout=6)
            return
        try:
            await add_role(self, RuleStore(), await self.lobby_names(), (pid, clean(str(p.get('name') or ''))))
        except (OSError, ValueError) as exc:
            self.notify(clean(str(exc)), title='Roles', severity='error', timeout=10)

    @work(exclusive=True, group='dialog')
    async def action_updates(self) -> None:
        from .jobs_ui import updates_dialog
        await updates_dialog(self)

    @on(Button.Pressed, '#power-btn')
    def _power_button(self, e: Button.Pressed) -> None:
        e.stop()
        self.action_power()

    def playlist_tool(self) -> str:
        return os.environ.get('IBIGS_ONI_POWER', '')  # the oni wrapper: `oni playlist ...` as well as start/stop

    async def power_run(self, *args):
        if not self.playlist_tool():
            return 127, 'Start/stop needs the IBIGS oni command: leave and type  oni servers'
        return await self.tool_run(*args, timeout=POWER_TIMEOUT)

    async def remove_or_restore(self, action, chosen, lobs, removed) -> None:
        """REMOVE one lobby (stopped, out of the list, kept 7 days) or RESTORE one removed before."""
        if action == 'restore':
            if not removed:
                return
            pick = await self.push_screen_wait(Pick('RESTORE WHICH LOBBY?', [
                (f"{clean(str(g['lobby']))}  {clean(str(g.get('name', '')))}  ({g.get('days_left', '?')} day(s) left)",
                 g['folder']) for g in removed]))
            if not pick:
                return
            rc, out = await self.power_run('restore', pick, '--json')
        else:
            if len(chosen) != 1:
                self.notify('Pick ONE lobby to remove (not ALL SERVERS).', title='Remove', severity='warning')
                return
            lob = chosen[0]
            if len(lobs) <= 1:
                self.notify("That's your only lobby, so it stays. Use STOP instead.", title='Remove',
                            severity='warning', timeout=8)
                return
            hot = busy(lob)
            body = (f"{lob['service']}  {clean(str(lob.get('name', '')))}\n\nIt stops and disappears from your list. "
                    "Its settings, stats and bans are kept for 7 days: F7 → RESTORE brings it back as it was. "
                    "Your game files, maps and other lobbies are not touched.")
            if hot:
                who = ', '.join(clean(p) for p in (lob.get('players') or [])) or 'people MAY be playing'
                body += f"\n\n!! Players on it: {who}. They will be DISCONNECTED."
            if not await self.push_screen_wait(Confirm(f"REMOVE {lob['service'].upper()}", body,
                                                       verb='DISCONNECT + REMOVE' if hot else 'REMOVE')):
                return
            rc, out = await self.power_run('remove', lob['service'], '--yes', *(['--force'] if hot else []), '--json')
        try:
            r = json.loads(out.strip().splitlines()[-1])
            text, ok = clean(str(r.get('text', ''))), r.get('state') in ('removed', 'restored')
        except (ValueError, IndexError):
            text, ok = clean_block(out), rc == 0
        if ok:
            self.notify(text, title=action.capitalize(), timeout=15)
        else:
            await self.push_screen_wait(Notice(f'{action.upper()}: NOT DONE', text, warn=True))
        self.action_refresh()

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
        rc2, out2 = await self.power_run('restore', '--json')
        try:
            removed = json.loads(out2.strip().splitlines()[-1]) if rc2 == 0 else []
        except (ValueError, IndexError):
            removed = []
        choice = await self.push_screen_wait(PowerPanel(lobs, removed if isinstance(removed, list) else []))
        if not choice:
            return
        action, services = choice
        chosen = [l for l in lobs if l['service'] in services]
        if action in ('remove', 'restore'):
            await self.remove_or_restore(action, chosen, lobs, removed)
            return
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
        if rc != 0 or 'NOT ' in out or 'did not stay up' in out:
            # the helper says exactly what's wrong (a typo with its line, a missing mod, the lobby's last log lines)
            await self.push_screen_wait(Notice(f"{action.upper()}: PROBLEM", clean_block(out), warn=True))
        elif action in ('start', 'restart') and self.playlist_tool():
            # 2026-10-15: follow the lobbies until they're online (or say what they wait on), instead of leaving
            # "RUNNING ?" for the customer to puzzle over
            from .jobs_ui import UpdateProgress
            await self.push_screen_wait(UpdateProgress(f"{action.upper()}: {', '.join(l['service'] for l in todo)}",
                                                       self.playlist_tool(), None, wait_minutes=20))
        else:
            done = [ln.strip() for ln in out.splitlines() if 'done.' in ln or 'already' in ln or 'left stopped' in ln]
            self.notify('\n'.join(done) or out.strip()[-400:], title=f"{action.capitalize()}: done", timeout=15)
        self.action_refresh()
