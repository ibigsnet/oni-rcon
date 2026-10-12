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
from .findings import CHAT_BEFORE, CHAT_LIMIT


def chat_status(context):
    status = context.get('status')
    if status == 'collecting':
        if time.time() < context.get('until', 0):
            return 'Collecting follow-up chat until '+time.strftime('%H:%M:%S', time.localtime(context['until']))
        return 'Follow-up window ended'
    return {'complete': 'Follow-up window ended', 'limited': 'Follow-up message limit reached',
            'interrupted': 'Capture interrupted: some conversation may be missing',
            'unavailable': 'Context omitted from this response; older context is bounded'}.get(
                status, 'No surrounding chat was captured for this older finding')


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
                yield Button('EXPAND', id='findings-expand')
                yield Button('CLEAR ALL', id='findings-ack')
                yield Button('CLOSE', id='findings-close', variant='primary')
            yield self.keys('↑↓ review', 'Scroll evidence', 'Expand chat', 'Esc close')

    def on_mount(self):
        super().on_mount()
        self.key = None
        self.rows = {}
        self.detail_id = None
        self.expanded = False
        self.refresh_findings()
        self.set_interval(1, self.refresh_findings)

    def refresh_findings(self):
        rows = self.app.findings.recent()
        coverage = self.app.finding_coverage()
        key = (tuple((r.id, r.chat_context.get('revision', 0), chat_status(r.chat_context)) for r in rows),
               self.app.findings.acknowledged_at, coverage, self.app.redact)
        if key == self.key:
            return
        self.key = key
        self.query_one('#findings-coverage', Static).update(Text(coverage))
        listing = self.query_one('#findings-history', OptionList)
        selected = listing.get_option_at_index(listing.highlighted).id if listing.highlighted is not None else None
        only_context = self.rows.keys() == {r.id for r in rows} and self.rows
        self.rows = {r.id: r for r in rows}
        if only_context and self.list_key == (self.app.findings.acknowledged_at, self.app.redact):
            self.show_detail()
            return
        self.list_key = (self.app.findings.acknowledged_at, self.app.redact)
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
        scroll = detail.scroll_y if row and self.detail_id == row.id else 0
        self.detail_id = row.id if row else None
        detail.clear()
        self.query_one('#findings-server', Button).disabled = row is None
        self.query_one('#findings-player', Button).disabled = not row or not row.subjects
        self.query_one('#findings-expand', Button).disabled = row is None
        if not row:
            return
        text = Text(redact_text(row.evidence+'\nSource: '+row.source+' · Review only; no automatic moderation.\n', self.app.redact), DIM)
        valid_id = re.fullmatch(r'[0-9a-f]{24}', row.id)
        def link(label, role, index=0):
            if valid_id:
                text.append(redact_text(label, self.app.redact), Style(color=CYAN, underline=True,
                            meta={'@click': f"app.open_finding('{row.id}', '{role}', {index})"}))
            else:
                text.append(redact_text(label, self.app.redact), CYAN)
        if valid_id:
            link('Open server', 'server')
            for i, player in enumerate(row.subjects[:4]):
                link('  Player: '+str(player.get('name') or 'Unknown'), 'subject', i)
            if row.reporter:
                link('  Reporter: '+str(row.reporter.get('name') or 'Unknown'), 'reporter')
        if row.level == 'review':
            ctx = row.chat_context
            text.append(f'\nPLAYER CHAT · up to {CHAT_LIMIT} before / {CHAT_LIMIT} after · {CHAT_BEFORE//60} min either side\n', WHITE)
            text.append(chat_status(ctx)+'\n', AMBER if ctx.get('status') == 'interrupted' else DIM)
            if ctx.get('since', 0) > row.at-CHAT_BEFORE:
                text.append('Earlier context is partial: this feed began at '+time.strftime('%H:%M:%S', time.localtime(ctx['since']))+'\n', DIM)
            if ctx.get('before_limited'):
                text.append('Busy chat: showing the last 20 messages before the finding.\n', DIM)
            if ctx and not ctx.get('messages'):
                text.append('No player messages captured in this window.\n', DIM)
            for i, message in enumerate(ctx.get('messages', [])):
                position = message.get('position', '')
                text.append(time.strftime('%H:%M:%S', time.localtime(message['at']))+' ', DIM)
                text.append({'before': 'BEFORE', 'trigger': 'TRIGGER', 'after': 'AFTER'}.get(position, ''),
                            AMBER if position == 'trigger' else DIM)
                text.append(' ['+redact_text(str(message.get('channel', 'all')), self.app.redact)+'] ', DIM)
                link(str(message.get('speaker', {}).get('name') or 'Unknown player'), 'chat', i)
                text.append(': '+redact_text(str(message.get('text', '')), self.app.redact)+'\n',
                            AMBER if position == 'trigger' else WHITE)
        detail.write(text)
        self.call_after_refresh(detail.scroll_to, y=scroll, animate=False)

    @on(Button.Pressed, '#findings-expand')
    def expand(self, event):
        event.stop()
        self.expanded = not self.expanded
        self.query_one('#findings-history').display = not self.expanded
        self.query_one('#findings-coverage').display = not self.expanded
        event.button.label = 'SPLIT VIEW' if self.expanded else 'EXPAND'
        self.query_one('#findings-detail').focus()

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
