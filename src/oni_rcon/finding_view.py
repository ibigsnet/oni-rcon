"""Read-only evidence behind the Superintendent's latest observations."""
import time

from rich.text import Text
from textual import on
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, RichLog, Static

from .app import Dialog, redact_text
from .art import AMBER, CYAN, DIM, WHITE


class FindingsView(Dialog):
    DEFAULT_CSS = '''
    FindingsView .dialog { width: 94%; max-width: 140; height: 85%; }
    FindingsView #findings-coverage { height: auto; max-height: 5; color: $text-muted; }
    FindingsView #findings-history { height: 1fr; min-height: 3; }
    '''

    def compose(self):
        with Vertical(classes='dialog'):
            yield Static(Text('SUPERINTENDENT · RECENT FINDINGS'), classes='dialog-title')
            yield Static(id='findings-coverage')
            yield RichLog(id='findings-history', wrap=True, markup=False)
            yield Static(Text('Review flags are observations, not cheat verdicts. No automatic kicks or bans. Last hour; up to 200 reports.'))
            with Horizontal(classes='dialog-buttons'):
                yield Button('CLEAR ALL ALERTS', id='findings-ack')
                yield Button('CLOSE', id='findings-close', variant='primary')
            yield self.keys('Esc close', 'Clear acknowledges alerts; report history stays')

    def on_mount(self):
        super().on_mount()
        self.key = None
        self.refresh_findings()
        self.set_interval(1, self.refresh_findings)

    def refresh_findings(self):
        rows = self.app.findings.recent()
        coverage = self.app.finding_coverage()
        key = (tuple(r.id for r in rows), self.app.findings.acknowledged_at, coverage, self.app.redact)
        if key == self.key:
            return
        self.key = key
        self.query_one('#findings-coverage', Static).update(Text(coverage))
        log = self.query_one('#findings-history', RichLog)
        log.clear()
        if not rows:
            log.write(Text('No findings observed yet. Missing telemetry does not mean all is quiet.', DIM))
        for row in rows:
            station = next((s for s in self.app.stations if str(s.index) == row.server), None)
            label = station.label if station else row.server
            level = 'REVIEW NEEDED' if row.level == 'review' else 'HIGHLIGHT'
            acknowledged = ' · acknowledged' if row.at <= self.app.findings.acknowledged_at else ''
            text = Text(time.strftime('%H:%M:%S', time.localtime(row.at))+'  ', DIM)
            text += Text(level, AMBER if row.level == 'review' else CYAN)
            text += Text(f' · {label}{acknowledged}\n{row.title}\n', WHITE)
            text += Text(row.evidence+'\nSource: '+row.source+'\n', DIM)
            log.write(Text(redact_text(text.plain, self.app.redact)))

    @on(Button.Pressed, '#findings-ack')
    def acknowledge(self, event):
        event.stop()
        self.app.action_clear_alerts()
        self.refresh_findings()

    @on(Button.Pressed, '#findings-close')
    def close(self, event):
        event.stop()
        self.dismiss(None)
