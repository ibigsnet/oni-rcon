"""Read-only evidence behind the Superintendent's latest observations."""
import time
import re

from rich.style import Style
from rich.text import Text
from textual import on
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from .app import Dialog, redact_text
from .art import AMBER, CYAN, DIM, WHITE


class FindingsView(Dialog):
    BINDINGS = [Binding('escape', 'dismiss(None)', show=False)]
    DEFAULT_CSS = '''
    FindingsView .dialog { width: 94%; max-width: 140; height: 92%; padding: 0 1; }
    FindingsView .dialog-title { margin-bottom: 0; }
    FindingsView #findings-coverage { height: auto; max-height: 3; color: $text-muted; }
    FindingsView #findings-history { height: 1fr; min-height: 5; overflow-x: hidden; }
    FindingsView #findings-detail { height: 1fr; min-height: 3; overflow-x: hidden; }
    FindingsView .dialog-buttons, FindingsView .dialog-keys { margin-top: 0; }
    FindingsView .dialog-keys { height: 1; }
    FindingsView .dialog-buttons Button { min-width: 0; width: 1fr; margin: 0; }
    '''

    def compose(self):
        with Vertical(classes='dialog'):
            yield Static(Text('SUPERINTENDENT · RECENT FINDINGS'), classes='dialog-title')
            yield Static(id='findings-coverage')
            yield OptionList(id='findings-history')
            yield RichLog(id='findings-detail', wrap=True, markup=False, min_width=1, auto_scroll=False)
            with Horizontal(classes='dialog-buttons'):
                yield Button('OPEN SERVER', id='findings-server')
                yield Button('VIEW PLAYER', id='findings-player')
                yield Button('CLEAR ALL', id='findings-ack')
                yield Button('CLOSE', id='findings-close', variant='primary')
            yield self.keys('↑↓ review', 'Enter / click open', 'Esc close', 'Clear keeps history')

    def on_mount(self):
        super().on_mount()
        self.key = None
        self.rows = {}
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
        listing = self.query_one('#findings-history', OptionList)
        selected = listing.get_option_at_index(listing.highlighted).id if listing.highlighted is not None else None
        self.rows = {r.id: r for r in rows}
        listing.clear_options()
        if not rows:
            listing.add_option(Option(Text('No findings observed yet. Missing telemetry does not mean all is quiet.', DIM), disabled=True))
        for row in rows:
            station = next((s for s in self.app.stations if str(s.index) == row.server), None)
            label = station.label if station else row.server
            level = 'REVIEW NEEDED' if row.level == 'review' else 'HIGHLIGHT'
            acknowledged = ' · acknowledged' if row.at <= self.app.findings.acknowledged_at else ''
            text = Text(time.strftime('%H:%M:%S', time.localtime(row.at))+'  ', DIM)
            text += Text(level, AMBER if row.level == 'review' else CYAN)
            text += Text(f' · {label}{acknowledged} · {row.title}', WHITE)
            listing.add_option(Option(Text(redact_text(text.plain, self.app.redact)), id=row.id))
        listing.highlighted = next((i for i, r in enumerate(rows) if r.id == selected), 0) if rows else None
        self.show_detail()

    def selected(self):
        listing = self.query_one('#findings-history', OptionList)
        return self.rows.get(listing.get_option_at_index(listing.highlighted).id) if listing.highlighted is not None else None

    @on(OptionList.OptionHighlighted, '#findings-history')
    def show_detail(self, event=None):
        row = self.selected()
        detail = self.query_one('#findings-detail', RichLog)
        detail.clear()
        self.query_one('#findings-server', Button).disabled = row is None
        self.query_one('#findings-player', Button).disabled = not row or not row.subjects
        if not row:
            return
        text = Text(redact_text(row.evidence+'\nSource: '+row.source+' · Review only; no automatic moderation.\n', self.app.redact), DIM)
        if re.fullmatch(r'[0-9a-f]{24}', row.id):
            def link(label, role, index=0):
                text.append(redact_text(label, self.app.redact), Style(color=CYAN, underline=True,
                            meta={'@click': f"app.open_finding('{row.id}', '{role}', {index})"}))
                text.append('  ')
            link('Open server', 'server')
            for i, player in enumerate(row.subjects[:4]):
                link('Player: '+str(player.get('name') or 'Unknown'), 'subject', i)
            if row.reporter:
                link('Reporter: '+str(row.reporter.get('name') or 'Unknown'), 'reporter')
        detail.write(text)

    @on(OptionList.OptionSelected, '#findings-history')
    @on(Button.Pressed, '#findings-player')
    def open_player(self, event):
        event.stop()
        if row := self.selected():
            self.app.action_open_finding(row.id)

    @on(Button.Pressed, '#findings-server')
    def open_server(self, event):
        event.stop()
        if row := self.selected():
            self.app.action_open_finding(row.id, 'server')

    @on(Button.Pressed, '#findings-ack')
    def acknowledge(self, event):
        event.stop()
        self.app.action_clear_alerts()
        self.refresh_findings()

    @on(Button.Pressed, '#findings-close')
    def close(self, event):
        event.stop()
        self.dismiss(None)
