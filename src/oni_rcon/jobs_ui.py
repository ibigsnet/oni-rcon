"""Tenant schedule and update dialogs."""
import asyncio
import re
import time
import uuid
import json

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, ProgressBar, Select, Static

from .app import Confirm, Dialog, Form
from .jobs import Store, validate
from .safety import clean

ACTION_OPTIONS = [('Broadcast a message', 'broadcast'), ('Shuffle teams', 'shuffle'), ('Start lobby', 'start'),
                  ('Stop when empty', 'stop'), ('Restart when empty', 'restart'),
                  ('Update game when empty', 'update-game'), ('Update Oni console', 'update-console')]
TRIGGER_OPTIONS = [('Every N minutes', 'interval'), ('Cron / time of day', 'cron'),
                   ('Before match (voting or loading)', 'before_match'), ('After match (finished)', 'after_match'),
                   ('Match starts', 'match_start')]


def preferred(options, value):
    return sorted(options, key=lambda x: x[1] != value)


def job_summary(job):
    when = dict((v, k) for k, v in TRIGGER_OPTIONS).get(job['trigger'], job['trigger'])
    if job['trigger'] == 'interval':
        when = f"Every {job['schedule']} minutes"
    elif job['trigger'] == 'cron':
        when = f"{job['schedule']} ({job['timezone']})"
    return f"{job['name']} | {job['target']} | {job['action']} | {when}"


class JobForm(Form):
    def compose(self):
        with Vertical(classes='dialog job-form'):
            yield Static(Text(self.t), classes='dialog-title')
            with VerticalScroll(classes='job-fields'):
                if self.note:
                    yield Static(Text(self.note), classes='dialog-body')
                for key, label, default in self.fields:
                    yield Label(label)
                    if isinstance(default, list):
                        yield Select(default, allow_blank=False, id=f'field-{key}')
                    else:
                        yield Input(str(default), id=f'field-{key}')
            with Horizontal(classes='dialog-buttons'):
                yield Button('ABORT', id='no')
                yield Button(self.verb, id='yes', variant='primary')
            yield self.keys('Esc cancel', 'Tab next', 'Enter continue')


class JobsPanel(Dialog):
    BINDINGS = [Binding('escape', 'dismiss(None)', show=False)]

    def __init__(self, jobs, state):
        super().__init__()
        self.jobs, self.state = jobs, state

    def compose(self):
        with Vertical(classes='dialog jobs'):
            yield Static('SCHEDULED JOBS', classes='dialog-title')
            age = time.time() - self.state.get('heartbeat', 0)
            health = 'Scheduler is running.' if age < 30 else 'Scheduler is not reporting. Jobs will wait until it starts.'
            yield Static(Text(health + '\nJobs continue when you close oni. Missed runs are not replayed.'), classes='dialog-body')
            options = [(('ON  ' if j['enabled'] else 'OFF ') + job_summary(j), j['id']) for j in self.jobs]
            yield Select(options or [('No jobs yet — choose NEW', '')], allow_blank=False, id='jobs-target')
            history = self.state.get('history', [])[-4:]
            lines = [f"{time.strftime('%H:%M', time.localtime(h['at']))}  {h['job']}  {h['target']}: {h['result']}" for h in history]
            yield Static(Text('\n'.join(lines) or 'No runs recorded yet.'), classes='jobs-history')
            with Horizontal(classes='dialog-buttons'):
                for label, identity in [('CLOSE', 'close'), ('NEW', 'new'), ('EDIT', 'edit'), ('PAUSE / RESUME', 'toggle'), ('DELETE', 'delete')]:
                    yield Button(label, id='job-' + identity, disabled=not self.jobs and identity in {'edit', 'toggle', 'delete'})
            yield self.keys('Esc close', 'Tab next', 'Enter choose')

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        action = event.button.id.removeprefix('job-')
        self.dismiss(None if action == 'close' else (action, self.query_one('#jobs-target', Select).value))


async def edit_job(app, targets, job=None):
    original = job or {'id': uuid.uuid4().hex[:12], 'name': 'Server reminder', 'action': 'broadcast', 'target': 'all',
                       'trigger': 'interval', 'schedule': '30', 'timezone': 'America/New_York',
                       'message': '', 'delay': 0, 'enabled': True}
    first = await app.push_screen_wait(JobForm('SCHEDULE A JOB', [
        ('name', 'Name', original['name']),
        ('action', 'Action', preferred(ACTION_OPTIONS, original['action'])),
        ('target', 'Lobby', preferred([('All this VM’s lobbies', 'all'), *[(n, n) for n in targets]], original['target'])),
        ('trigger', 'When', preferred(TRIGGER_OPTIONS, original['trigger']))], verb='NEXT', required=('name',)))
    if first is None:
        return None
    fields = []
    trigger = first['trigger']
    if trigger in {'cron', 'interval'}:
        default = original['schedule'] if trigger == original['trigger'] else ('0 4 * * *' if trigger == 'cron' else '30')
        fields.append(('schedule', 'Minute hour day month weekday' if trigger == 'cron' else 'Minutes between runs', default))
    if trigger == 'cron':
        fields.append(('timezone', 'Timezone', original['timezone']))
    if first['action'] == 'broadcast':
        fields.append(('message', 'Broadcast message (up to 240 characters)', original['message']))
    if trigger in {'after_match', 'match_start'}:
        fields.append(('delay', 'Seconds after the event', str(original['delay'])))
    values = {}
    if fields:
        values = await app.push_screen_wait(JobForm('JOB DETAILS', fields, verb='REVIEW',
            note='Cron example: 0 20 * * 5 = Friday at 20:00 in your timezone.\n'
                 'Stops, restarts and game updates skip occupied or unreadable lobbies.'))
        if values is None:
            return None
    result = {**original, **first, **values}
    result['delay'] = int(values.get('delay', '0'))
    result = validate(result)
    if not await app.push_screen_wait(Confirm('SAVE JOB', job_summary(result) +
            ('\nMessage: ' + result['message'] if result['action'] == 'broadcast' else '') +
            '\nThis schedule remains active after you close oni.', verb='SAVE', danger=False)):
        return None
    return result


async def jobs_dialog(app, targets):
    store = Store()
    while True:
        try:
            jobs = store.load()
            choice = await app.push_screen_wait(JobsPanel(jobs, store.state()))
            if choice is None:
                return
            action, identity = choice
            job = next((j for j in jobs if j['id'] == identity), None)
            if action in {'new', 'edit'}:
                result = await edit_job(app, targets, job if action == 'edit' else None)
                if result:
                    store.change(result)
            elif action == 'toggle' and job:
                job['enabled'] = not job['enabled']
                store.change(job)
            elif action == 'delete' and job:
                if await app.push_screen_wait(Confirm('DELETE JOB', job_summary(job), verb='DELETE')):
                    store.change(delete=job['id'])
        except (OSError, ValueError) as exc:
            app.notify(clean(str(exc)), title='Scheduled jobs', severity='error', timeout=12)
            return


class UpdatesPanel(Dialog):
    BINDINGS = [Binding('escape', 'dismiss(None)', show=False)]

    def __init__(self, jobs, console_timer=False):
        super().__init__()
        self.jobs = jobs
        self.console_timer = console_timer

    def compose(self):
        game = any(j['action'] == 'update-game' and j['enabled'] for j in self.jobs)
        console = self.console_timer or any(j['action'] == 'update-console' and j['enabled'] for j in self.jobs)
        with Vertical(classes='dialog jobs'):
            yield Static('UPDATES', classes='dialog-title')
            yield Static(Text(f"Automatic game updates: {'ON' if game else 'OFF'}\n"
                              f"Automatic Oni updates: {'ON' if console else 'OFF'}\n\n"
                              'Game updates wait while players are online. Oni updates apply to new console sessions.\n'
                              'You can edit update times under F8. Halo game files are updated with setup --update.'), classes='dialog-body')
            yield Select([('Check game version', 'game-check'), ('Update game now (when empty)', 'game-update'),
                          ('Check Oni update channel', 'console-check'), ('Update Oni now', 'console-update'),
                          ('Turn automatic game updates ' + ('OFF' if game else 'ON'), 'game-auto'),
                          ('Turn automatic Oni updates ' + ('OFF' if console else 'ON'), 'console-auto')],
                         allow_blank=False, id='update-choice')
            with Horizontal(classes='dialog-buttons'):
                yield Button('CLOSE', id='update-close')
                yield Button('CONTINUE', id='update-go', variant='primary')

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        self.dismiss(None if event.button.id == 'update-close' else self.query_one('#update-choice', Select).value)


async def updates_dialog(app):
    store = Store()
    try:
        jobs = store.load()
        rc, raw = await app.power_run('updates', 'status')
        try:
            console_timer = bool(json.loads(raw).get('console_timer')) if rc == 0 else False
        except (ValueError, AttributeError):
            console_timer = False
        choice = await app.push_screen_wait(UpdatesPanel(jobs, console_timer))
        if not choice:
            return
        kind, verb = choice.split('-')
        if verb == 'auto':
            identity = 'auto-' + kind
            old = next((j for j in jobs if j['id'] == identity), None)
            job = old or {'id': identity, 'name': 'Automatic ' + ('game' if kind == 'game' else 'Oni') + ' updates',
                          'action': 'update-' + kind, 'target': 'all', 'trigger': 'cron',
                          'schedule': '0 * * * *' if kind == 'game' else '20 4 * * *', 'timezone': 'America/New_York',
                          'message': '', 'delay': 0, 'enabled': False}
            enabled = any(j['action'] == 'update-' + kind and j['enabled'] for j in jobs) or (kind == 'console' and console_timer)
            job['enabled'] = not enabled
            if await app.push_screen_wait(Confirm('AUTOMATIC UPDATES',
                    ('Enable: ' if job['enabled'] else 'Pause: ') + job_summary(job), verb='SAVE', danger=False)):
                if kind == 'console' and console_timer:
                    rc, _ = await app.power_run('updates', 'console-timer-off')
                    if rc:
                        raise ValueError('Could not pause the existing console update timer. Automatic updates are unchanged.')
                if enabled:
                    for existing in jobs:
                        if existing['action'] == 'update-' + kind:
                            store.change({**existing, 'enabled': False})
                else:
                    store.change(job)
                app.notify('Saved. Change the schedule or pause it under F8.', title='Updates')
            return
        if verb == 'update' and not await app.push_screen_wait(Confirm('UPDATE ' + kind.upper(),
                'Game updates restart only empty lobbies. Busy or unknown lobbies defer the update.\n'
                'Oni uses the signed IBIGS channel and keeps a rollback copy.', verb='UPDATE', danger=False)):
            return
        tool = app.playlist_tool() if hasattr(app, 'playlist_tool') else ''
        if verb == 'update' and tool:
            # 2026-10-15: show the update as it happens (stages, elapsed time, lobbies coming back) instead of a
            # silent wait of several minutes.
            await app.push_screen_wait(UpdateProgress(f'UPDATING {kind.upper()}', tool, ['updates', choice],
                                                      wait_online=kind == 'game'))
            return
        app.notify('Checking updates…' if verb == 'check' else 'Updating… This may take several minutes.', title='Updates', timeout=10)
        rc, output = await app.power_run('updates', choice)
        app.notify(clean(output.strip()[-1400:]) or ('Completed.' if rc == 0 else f'Failed (exit {rc}).'),
                   title='Updates', severity='information' if rc == 0 else 'warning', timeout=20)
    except (OSError, ValueError) as exc:
        app.notify(clean(str(exc)), title='Updates', severity='error')


ANSI = re.compile(r"\x1b\[[0-9;]*m")
# What the game updater (ibigs-reclaimer-update) prints at each stage -> (step, words for the step line).
GAME_STEPS = [(re.compile(r"server version|Couldn't check online"), 1, "Checking for the newest Project Reclaimer"),
              (re.compile(r"^Downloading "), 2, "Downloading the new version (1-3 GB the first time, less after)"),
              (re.compile(r"built-in game modes"), 3, "Installing the built-in game modes"),
              (re.compile(r"^Restarting "), 4, "Restarting the lobbies that were running"),
              (re.compile(r"^(Updated to|Up to date)"), 5, "Waiting for the lobbies to come back online")]


class UpdateProgress(Dialog):
    """2026-10-15: a progress view for updates and starts. Runs the oni command, follows its stage lines on a bar
    (step N of 5, with the elapsed time and what it is doing now), then waits until every running lobby reports in,
    showing each one as STARTING, WAITING (with the server's reason) or ONLINE. Nothing is guessed: steps move only
    when the updater or the lobbies say so; within a step the bar pulses."""
    BINDINGS = [Binding('escape', 'close', show=False), Binding('enter', 'close', show=False)]
    STEPS = 5

    def __init__(self, title: str, tool: str, args: list[str] | None, wait_online: bool = True,
                 wait_minutes: float = 15):
        super().__init__()
        self.t, self.tool, self.args, self.wait_online = title, tool, args, wait_online
        self.wait_s = wait_minutes * 60
        self.start = time.monotonic()
        self.step, self.words, self.done, self.rc = 0, 'Starting…', False, 0
        self.lines: list[str] = []
        self.lobby_lines = ''
        if not args:  # only waiting for lobbies to come online (F7 START / RESTART)
            self.STEPS = 1

    def compose(self) -> ComposeResult:
        with Vertical(classes='dialog'):
            yield Static(Text(self.t), classes='dialog-title')
            yield Static('', id='up-step', classes='dialog-body')
            yield ProgressBar(total=None, show_eta=False, id='up-bar')
            yield Static('', id='up-lobbies', classes='dialog-body')
            with VerticalScroll(classes='notice-body'):
                yield Static('', id='up-log', classes='dialog-body')
            with Horizontal(classes='dialog-buttons'):
                yield Button('WORKING…', id='no', disabled=True)
            yield self.keys('The window closes when you choose CLOSE; the work keeps going if you leave oni')

    def on_mount(self) -> None:
        self.set_interval(1, self.paint)
        self.run_worker(self.go(), exclusive=False)

    def set_step(self, step: int, words: str) -> None:
        if step >= self.step:
            self.step, self.words = step, words

    def paint(self) -> None:
        secs = int(time.monotonic() - self.start)
        bar = self.query_one('#up-bar', ProgressBar)
        if self.done:
            bar.update(total=self.STEPS, progress=self.STEPS)
        elif self.STEPS == 1:
            running = self.lobby_lines.count('\n') + 1 if self.lobby_lines else 0
            ready = self.lobby_lines.count('ONLINE')
            bar.update(total=max(running, 1), progress=ready) if running else bar.update(total=None)
        elif self.step:
            bar.update(total=self.STEPS, progress=self.step - 1 + 0.5)
        head = (f'DONE in {secs // 60}:{secs % 60:02}' if self.done else
                f'Step {max(self.step, 1)} of {self.STEPS} · {secs // 60}:{secs % 60:02} · {self.words}')
        self.query_one('#up-step', Static).update(Text(head, 'bold'))
        self.query_one('#up-lobbies', Static).update(Text(self.lobby_lines))
        self.query_one('#up-log', Static).update(Text('\n'.join(self.lines[-12:]), 'dim'))

    async def go(self) -> None:
        if self.args:
            try:
                proc = await asyncio.create_subprocess_exec(self.tool, *self.args, stdout=asyncio.subprocess.PIPE,
                                                            stderr=asyncio.subprocess.STDOUT,
                                                            stdin=asyncio.subprocess.DEVNULL)
                assert proc.stdout
                async for raw in proc.stdout:
                    line = clean(ANSI.sub('', raw.decode(errors='replace')).strip())
                    if not line:
                        continue
                    self.lines.append(line)
                    for pat, step, words in GAME_STEPS:
                        if pat.search(line):
                            self.set_step(step, words)
                self.rc = await proc.wait()
            except OSError as e:
                self.lines.append(f'Could not run it: {e}')
                self.rc = 127
        if self.wait_online and self.rc == 0:
            self.set_step(self.STEPS, 'Waiting for the lobbies to come online')
            await self.follow_lobbies()
        self.done = True
        self.paint()
        b = self.query_one('#no', Button)
        b.label, b.disabled, b.variant = 'CLOSE', False, 'primary'
        b.focus()

    async def follow_lobbies(self) -> None:
        """Until every running lobby reports players (or the time is up): STARTING / WAITING (why) / ONLINE."""
        end = time.monotonic() + self.wait_s
        while time.monotonic() < end:
            try:
                proc = await asyncio.create_subprocess_exec(self.tool, 'status', '--json',
                                                            stdout=asyncio.subprocess.PIPE,
                                                            stderr=asyncio.subprocess.DEVNULL)
                out, _ = await asyncio.wait_for(proc.communicate(), 60)
                lobs = json.loads(out.decode().strip().splitlines()[-1])
            except (OSError, ValueError, IndexError, asyncio.TimeoutError):
                await asyncio.sleep(5)
                continue
            running = [l for l in lobs if l.get('running')]
            rows, ready = [], 0
            for l in running:
                phase = l.get('phase') or ''
                if not phase and l.get('players') is not None:
                    ready += 1
                    rows.append(f"  ✓ {clean(l['service'])}  ONLINE")
                elif phase == 'waiting':
                    rows.append(f"  ! {clean(l['service'])}  WAITING: {clean(str(l.get('why') or ''))}")
                else:
                    rows.append(f"  … {clean(l['service'])}  STARTING")
            self.lobby_lines = '\n'.join(rows) or '  No lobby is running.'
            if running:
                self.words = f'Waiting for the lobbies to come back online ({ready} of {len(running)} ready)'
            if ready == len(running):
                return
            await asyncio.sleep(5)
        self.lines.append('Still not online after the wait. F7 shows each lobby and why it is waiting.')

    def action_close(self) -> None:
        if self.done:
            self.dismiss(self.rc)

    @on(Button.Pressed, '#no')
    def _close(self) -> None:
        self.action_close()
