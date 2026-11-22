"""SERVER SETTINGS (Ctrl+E, F3's SETTINGS, F12 on a customer console): a server's dedicated.toml in plain words, by
group, with a careful editor (server_settings.py). After a save the console asks the server to `reload`, shows its
answer, and offers a restart for a setting that needs one: the customer console's own restart (the `oni` helper, as
F7) where it knows the lobby, else RCON `restart now`."""
from __future__ import annotations

import asyncio

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Static, TabbedContent, TabPane

from . import server_settings as ss
from .app import Confirm, Dialog, Form, Pick
from .art import AMBER, CYAN, DIM, GREEN, RED, WHITE
from .safety import clean

TABS = [*ss.GROUPS, ("all", "ALL SETTINGS", "Every setting this server's block, [defaults] and the top of the file "
                                            "hold. Settings without a control of their own are edited as a raw "
                                            "value; the server checks each one as it reloads.")]
APPLIES_SHORT = {ss.NOW: ("now", GREEN), ss.NEXT_GAME: ("next game", CYAN), ss.RESTART: ("restart", AMBER),
                 ss.ASK: ("reload says", DIM)}
WINS = ("This server's own value wins over [defaults]; with neither set, the server's default applies. "
        "Saving backs dedicated.toml up beside itself first.")
RELOAD_WAIT = 60  # seconds for the server's reload answer: it checks the whole configuration first


CELL = 26  # the most of a value a table cell shows; the hint under the table has the whole of it


def cell(text: str) -> str:
    text = clean(text)
    return text if len(text) <= CELL else text[:CELL - 1] + "…"


def applies_text(applies: str) -> Text:
    word, color = APPLIES_SHORT.get(applies, (applies, DIM))
    return Text(word, color)


class SettingsView(Dialog):
    """The settings by group. Dismisses with (action, key, tab) or None. Actions: edit, reset, addbot, removebot,
    addkey."""
    BINDINGS = [Binding("escape", "dismiss(None)", show=False)]

    def __init__(self, loaded: ss.Loaded, label: str, editable: bool, note: str = "", bots: str = "",
                 tab: str = "performance"):
        super().__init__()
        self.loaded, self.label, self.editable, self.note, self.bots, self.tab = loaded, label, editable, note, bots, tab
        self.rows: dict[str, list[ss.Row]] = {}

    def compose(self) -> ComposeResult:
        server = self.loaded.block
        with Vertical(classes="dialog settings"):
            yield Static(Text(f"SERVER SETTINGS · {self.label}"), classes="dialog-title")
            yield Static(Text.assemble((str(self.loaded.path), CYAN), ("  ·  [[server]] ", DIM),
                                       (clean(str(server.get("name") or self.loaded.index + 1)), WHITE),
                                       ("\n" + WINS, DIM),
                                       *([("\n" + self.note, AMBER)] if self.note else [])), id="set-head")
            with TabbedContent(id="set-tabs", initial=self.tab):
                for group, name, intro in TABS:
                    with TabPane(name, id=group):
                        yield Static(Text(intro, DIM), classes="set-intro")
                        yield DataTable(id=f"set-{group}", cursor_type="row", zebra_stripes=True)
                        if group == "bots":
                            yield Static(id="set-botlist")
            yield Static(id="set-hint")
            with Horizontal(classes="dialog-buttons set-buttons"):
                yield Button("EDIT", id="edit", variant="primary", compact=True, disabled=not self.editable)
                yield Button("RESET", id="reset", compact=True, disabled=not self.editable)
                yield Button("ADD BOT ID", id="addbot", variant="success", compact=True, disabled=not self.editable)
                yield Button("REMOVE BOT ID", id="removebot", variant="warning", compact=True,
                             disabled=not self.editable)
                yield Button("ADD SETTING", id="addkey", compact=True, disabled=not self.editable)
                yield Button("CLOSE", id="no", compact=True)
            yield self.keys("Esc close", "click a group (or Tab to the groups, then ← →)", "Enter edit",
                            "Del reset to [defaults] or the server's default")

    def on_mount(self) -> None:
        for group, *_ in TABS:
            table = self.query_one(f"#set-{group}", DataTable)
            table.add_columns("SETTING", "THIS SERVER", "[DEFAULTS]", "IN USE", "FROM", "APPLIES")
            rows = ss.all_rows(self.loaded) if group == "all" else ss.group_rows(self.loaded, group)
            self.rows[group] = rows
            for r in rows:
                table.add_row(Text(r.label, DIM if r.why_locked else WHITE), Text(cell(r.server), WHITE),
                              Text(cell(r.defaults), DIM), Text(cell(r.in_use), GREEN if r.source else DIM),
                              Text(r.source, CYAN if r.source == "this server" else DIM),
                              Text("locked", DIM) if r.why_locked else applies_text(r.applies), key=r.key)
        self.query_one("#set-botlist", Static).update(self.bots_text())
        self.show_buttons()
        self.query_one(f"#set-{self.tab}", DataTable).focus()
        self.call_after_refresh(self.describe)  # once the tabs have settled on the one shown

    def bots_text(self) -> Text:
        roles = ss.bot_roles(self.loaded)
        t = Text()
        if not roles:
            t.append(f"No role has {ss.BOT_PERMISSION} yet: ADD BOT ID makes one ([roles.{ss.BOT_ROLE}], rank 1, "
                     "for every server in this file) with the bot's ID in it.\n", DIM)
        for where, name, players in roles:
            t.append(f"Role {name} ({where}): ", AMBER)
            t.append(", ".join(players) if players else "no bot IDs yet", WHITE if players else DIM)
            t.append("\n")
        n = len(ss.bot_ids(self.loaded))
        t.append(f"A server takes {ss.MAX_BOTS} bots at most at once", DIM)
        t.append(f" ({n} ID{'s' if n != 1 else ''} may join now)." if n else ".", AMBER if n > ss.MAX_BOTS else DIM)
        t.append(" A bot's ID: run  reclaimer-bot id  on the bot's machine.\n", DIM)
        t.append("CONNECTED NOW: ", AMBER)
        t.append(clean(self.bots) or "(not asked: the server isn't connected)", WHITE if self.bots else DIM)
        return t

    @property
    def group(self) -> str:
        return self.query_one("#set-tabs", TabbedContent).active or "performance"

    @property
    def selected(self) -> ss.Row | None:
        table = self.query_one(f"#set-{self.group}", DataTable)
        rows = self.rows.get(self.group) or []
        return rows[table.cursor_row] if 0 <= table.cursor_row < len(rows) else None

    def show_buttons(self) -> None:
        bots, every = self.group == "bots", self.group == "all"
        self.query_one("#addbot").display = self.query_one("#removebot").display = bots
        self.query_one("#addkey").display = every

    def describe(self) -> None:
        r = self.selected
        hint = self.query_one("#set-hint", Static)
        if not r:
            hint.update("")
            return
        setting = ss.BY_KEY.get(r.key)
        t = Text.assemble((r.label, AMBER), (f"  ({r.key})", DIM))
        if setting and setting.hint:
            t.append("\n" + setting.hint, WHITE)
        if len(r.in_use) > CELL:
            t.append(f"\nIn use: {clean(r.in_use)}", WHITE)
        if setting and setting.default:
            t.append(f"\nServer default: {setting.default}.", DIM)
        t.append("\n" + (f"Can't be changed here: {r.why_locked}" if r.why_locked else f"Applies {r.applies}."),
                 AMBER if r.why_locked else DIM)
        hint.update(t)

    @on(TabbedContent.TabActivated, "#set-tabs")
    def _tab(self, e: TabbedContent.TabActivated) -> None:
        e.stop()
        self.show_buttons()
        self.describe()

    @on(DataTable.RowHighlighted)
    def _row(self, e: DataTable.RowHighlighted) -> None:
        e.stop()
        self.describe()

    @on(DataTable.RowSelected)
    def _enter(self, e: DataTable.RowSelected) -> None:
        e.stop()
        self.act("edit")

    def on_key(self, event) -> None:
        if event.key == "delete":
            event.stop()
            self.act("reset")

    def on_button_pressed(self, e: Button.Pressed) -> None:
        e.stop()
        self.act(e.button.id)

    def act(self, what: str) -> None:
        if what == "no":
            self.dismiss(None)
            return
        if not self.editable:
            self.notify(self.note or "These settings can't be changed from here.", severity="warning", timeout=8)
            return
        r = self.selected
        if what in ("edit", "reset"):
            if not r:
                return
            if r.why_locked:
                self.notify(r.why_locked, title=r.key, severity="warning", timeout=8)
                return
        self.dismiss((what, r.key if r else "", self.group))


def _where(loaded: ss.Loaded, setting_where: tuple, key: str) -> list[str]:
    """The tables to offer for a setting, the one in force first."""
    _, source = loaded.effective(key, setting_where)
    order = [source] if source else ["server"]
    return order + [w for w in setting_where if w not in order]


async def _pick_where(app, loaded: ss.Loaded, key: str, where: tuple) -> str | None:
    """This server, or [defaults] (the top of the file for host_name). A file with one server edits where the value
    is in force without asking."""
    options = _where(loaded, where, key)
    servers = loaded.data.get("server") or []
    if len(options) == 1 or len(servers) == 1:
        return options[0]
    words = {"server": "This server only: wins over [defaults]",
             "defaults": f"[defaults]: every server in this file that doesn't set its own ({len(servers)} servers)",
             "top": "The top of the file: every server that doesn't set its own"}
    return await app.push_screen_wait(Pick(f"{key.upper()} · WHERE?", [(words[w], w) for w in options]))


async def ask_change(app, loaded: ss.Loaded, action: str, key: str) -> tuple[list, str, list[str]] | None:
    """(changes, what they do in words, the keys changed) from the operator, or None."""
    setting = ss.BY_KEY.get(key)
    if action == "reset":
        for where in ("server", "defaults", "top"):
            if loaded.get(key, where)[0] and (not setting or where in setting.where):
                if where != "server" and len(loaded.data.get("server") or []) > 1 and not await app.push_screen_wait(
                        Confirm(f"RESET {key.upper()}", f"{key} is set in [{where}], for every server in this file "
                                f"that doesn't set its own. Take it out there?", verb="TAKE IT OUT")):
                    return None
                after = "the server's default" if not setting else (setting.default or "the server's default")
                return ss.changes_for(loaded, key, ss.REMOVE, where), f"{key}: taken out of [{where}] ({after} " \
                                                                       f"or a value elsewhere applies)", [key]
        app.notify(f"{key} isn't set in this server's block or [defaults]: the server's default applies already.",
                   title="RESET", timeout=6)
        return None
    if action == "addbot":
        f = await app.push_screen_wait(Form("ADD A BOT", [("id", "The bot's player ID (64 characters)", "")],
                                            verb="NEXT", required=("id",), note=(
            f"Bots join while their ID holds a role with {ss.BOT_PERMISSION}. Run  reclaimer-bot id  on the bot's "
            f"machine to see its ID. A server takes {ss.MAX_BOTS} bots at most at once; more IDs may be listed, but "
            f"no more than {ss.MAX_BOTS} get in together.")))
        if not f:
            return None
        changes = ss.add_bot(loaded, f["id"])
        what = f"bot ID {f['id'][:12]}… may join (role {changes[0].table[-1]}, every server in this file)"
        value, source = loaded.effective("allow_bots")
        if value is not True:
            changes += ss.changes_for(loaded, "allow_bots", True, source or "server")
            what += "; allow_bots turned on"
        return changes, what, ["allow_bots", "roles"]
    if action == "removebot":
        ids = ss.bot_ids(loaded)
        if not ids:
            app.notify("No bot IDs are listed.", title="BOTS", timeout=5)
            return None
        pid = await app.push_screen_wait(Pick("REMOVE WHICH BOT ID?", [(p, p) for p in ids]))
        if not pid:
            return None
        return ss.remove_bot(loaded, pid), f"bot ID {pid[:12]}… can no longer join", ["roles"]
    if action == "addkey":
        f = await app.push_screen_wait(Form("ADD A SETTING", [("key", "Setting (as the server's docs name it)", ""),
                                                              ("value", "Value: true, 30, \"words\" or [\"a\", \"b\"]",
                                                               "")], verb="NEXT", required=("key", "value"),
                                            note="Set in this server's block. The server checks it as it reloads, "
                                                 "and the old file can be put back if it says no."))
        if not f:
            return None
        key = f["key"].strip()
        if key in ss.BY_KEY:
            app.notify(f"{key} has a control of its own: pick it in the list.", title="ADD SETTING", timeout=6)
            return None
        if why := ss.editable(key):
            raise ss.SettingsError(f"{key}: {why}")
        value = ss.raw_value(f["value"])
        return ss.changes_for(loaded, key, value, "server"), f"{key} = {ss.shown(key, value)} (this server)", [key]
    # edit
    if setting is None:
        if why := ss.editable(key):
            raise ss.SettingsError(f"{key}: {why}")
        where = await _pick_where(app, loaded, key, ("server", "defaults"))
        if not where:
            return None
        _, current = loaded.get(key, where)
        f = await app.push_screen_wait(Form(f"{key.upper()} · [{where}]", [
            ("value", "Value: true, 30, \"words\" or [\"a\", \"b\"]",
             ss.toml_value(current) if current is not None else "")], verb="NEXT", required=("value",),
            note="A raw value: the server checks it as it reloads."))
        if not f:
            return None
        value = ss.raw_value(f["value"])
        return ss.changes_for(loaded, key, value, where), f"{key} = {ss.shown(key, value)} ([{where}])", [key]
    where = await _pick_where(app, loaded, key, setting.where)
    if not where:
        return None
    here, current = loaded.get(key, where)
    if setting.kind in ("choice", "bool"):
        options = [(Text.assemble((words, WHITE), ("   ← now" if here and value == current
                                                    and type(value) is type(current) else "", GREEN)), value)
                   for value, words in setting.choices]
        if here:
            options.append((Text.assemble(("Leave it out", WHITE), (f"   the server's default: {setting.default}"
                                                                     if setting.default else "", DIM)), ss.REMOVE))
        value = await app.push_screen_wait(Pick(f"{setting.label.upper()} · [{where}]", options))
        if value is None:
            return None
    else:
        secret = setting.kind == "secret"
        bounds = f" ({setting.low} to {setting.high})" if setting.kind == "int" else ""
        f = await app.push_screen_wait(Form(f"{setting.label.upper()} · [{where}]", [
            ("value", setting.label + bounds, "" if secret or current is None else str(current))],
            verb="NEXT", secret=("value",) if secret else (), required=() if setting.kind != "int" else ("value",),
            note=setting.hint + (f" Server default: {setting.default}." if setting.default else "")))
        if f is None:
            return None
        if setting.kind == "text" and not f["value"].strip():
            if not here:
                return None
            value = ss.REMOVE  # an emptied box takes the server's own text out
        else:
            value = ss.check(setting, f["value"])
    if value is not ss.REMOVE:
        value = ss.check(setting, value)
    words = "taken out" if value is ss.REMOVE else (ss.masked(value) if setting.kind == "secret"
                                                    else ss.value_words(setting, key, value))
    return ss.changes_for(loaded, key, value, where), f"{setting.label}: {words} ([{where}])", [key]


def when_text(keys: list[str]) -> str:
    out = []
    for key in keys:
        setting = ss.BY_KEY.get(key)
        out.append(f"{key}: {setting.applies if setting else ss.ASK}")
    return "\n".join(out)


async def lobby_of(app, loaded: ss.Loaded) -> str:
    """The customer console's lobby (as F7 names it) this file belongs to: its folder's name, when F7 lists it."""
    names = getattr(app, "lobby_names", None)
    if not names or not app.playlist_tool():
        return ""
    folder = loaded.path.parent.name
    try:
        return folder if folder in await names() else ""
    except Exception:
        return ""


async def restart(app, st, loaded: ss.Loaded) -> None:
    """Restart now: the customer console's F7 path where it knows the lobby, else the server's own RCON restart."""
    lobby = await lobby_of(app, loaded)
    if lobby:
        app.notify(f"Restarting {lobby}... (a clean stop can take up to 45 s)", title="SERVER SETTINGS", timeout=8)
        rc, out = await app.power_run("restart", lobby, "--yes", "--force")
        app.notify(clean(out.strip())[-400:] or ("Restarted." if rc == 0 else "The restart didn't finish."),
                   title=f"RESTART {lobby}", severity="information" if rc == 0 else "error", timeout=12)
        return
    await app.cmd(st, "restart", "now")


async def after_save(app, st, loaded: ss.Loaded, saved, keys: list[str]) -> None:
    """Ask the server to reload, show what it says, and offer a restart when a setting needs one."""
    if not st.online:
        await app.push_screen_wait(Confirm("SAVED", "dedicated.toml is saved. The server isn't connected, so it "
                                           "reads the new file when it starts (a running server with auto_reload on "
                                           "takes it up within seconds by itself).\n\nWhen it applies:\n"
                                           + when_text(keys), verb="OK", danger=False))
        return
    try:
        r = await st.rcon.call("reload", timeout=RELOAD_WAIT)
        r = {**r, "ok": bool(r.get("ok"))}
    except TimeoutError:
        r = {"ok": None, "text": "No answer to reload yet: with auto_reload on the server applies the file by itself."}
    except Exception as e:
        r = {"ok": None, "text": f"reload couldn't be sent ({clean(str(e))}): with auto_reload on the server applies "
                                 "the file by itself."}
    app.log_reply(st, "reload", r)
    text = clean(str(r.get("text") or ""))
    if r["ok"] is False:
        body = f"The server checked the new file and said no, so it kept the old settings:\n\n{text[-1500:]}"
        if saved and await app.push_screen_wait(Confirm("THE SERVER REFUSED THE CHANGE",
                                                        body + "\n\nPut the file back as it was?", verb="PUT IT BACK")):
            await asyncio.to_thread(ss.restore, loaded.path, saved)
            await app.cmd(st, "reload", toast=False, quiet=True)
            app.notify("dedicated.toml is back as it was.", title="SERVER SETTINGS", timeout=8)
        return
    restarts, supervisor = ss.reload_words(text)
    restarts = restarts or any(ss.BY_KEY.get(k) and ss.BY_KEY[k].applies == ss.RESTART for k in keys)
    body = f"The server says: {text or 'nothing'}\n\nWhen it applies:\n{when_text(keys)}"
    if supervisor:
        body += "\n\nSome of it needs the whole lobby restarted (the supervisor): F7 on the customer console."
    if not restarts:
        await app.push_screen_wait(Confirm("SAVED", body, verb="OK", danger=False))
        return
    players = len(st.players)
    choice = await app.push_screen_wait(Pick("SAVED · THIS ONE NEEDS A RESTART", [
        (Text.assemble(("Let it restart by itself once nobody is playing", WHITE), ("   recommended", GREEN)), "wait"),
        (Text.assemble(("Restart it now", WHITE), (f"   {players} player(s) on it are disconnected for about a minute"
                                                   if players else "   nobody is on it", RED if players else DIM)),
         "now")]))
    if choice == "now" and (not players or await app.push_screen_wait(Confirm(
            "RESTART NOW", f"{players} player(s) are on {st.label}. They are disconnected for about a minute.",
            verb="DISCONNECT + RESTART"))):
        await restart(app, st, loaded)
    else:
        app.notify(body[:600], title="SERVER SETTINGS · SAVED", timeout=12)


async def server_settings(app, st, tab: str = "performance") -> None:
    """The settings screen for one station, round and round until it's closed: each save reopens it on the file as
    it now is."""
    while True:
        try:
            loaded = await asyncio.to_thread(ss.locate, st.server)
        except ss.SettingsError as e:
            await app.push_screen_wait(Confirm("SERVER SETTINGS", str(e), verb="OK", danger=False))
            return
        version = (st.rcon.info or {}).get("version") if st.rcon else None
        ok = ss.supports(version)
        note = ""
        if ok is False:
            note = (f"This server runs {clean(str(version))}: these settings need Project Reclaimer 0.10 or newer, "
                    "and an older server refuses a file that has them. Update it first; until then they're shown "
                    "only.")
        elif ok is None:
            note = "The server isn't connected, so its version isn't known: a change applies when it next starts."
        bots = ""
        if st.online and ok:
            try:
                r = await st.rcon.call("bots")
                bots = str(r.get("text") or "") if r.get("ok") else ""
            except Exception:
                bots = ""
        choice = await app.push_screen_wait(SettingsView(loaded, st.label, ok is not False, note, bots, tab))
        if not choice:
            return
        action, key, tab = choice
        try:
            asked = await ask_change(app, loaded, action, key)
            if not asked:
                continue
            changes, what, keys = asked
            text = await asyncio.to_thread(ss.edit, loaded.text, loaded.index, changes)
            if text == loaded.text:
                app.notify("Nothing changed.", title="SERVER SETTINGS", timeout=5)
                continue
            body = (f"{what}\n\nWhen it applies:\n{when_text(keys)}\n\n{loaded.path} is backed up beside itself "
                    "first, and the server checks the new file as it reloads.")
            if not await app.push_screen_wait(Confirm("SAVE SETTINGS", body, verb="SAVE", danger=False)):
                continue
            loaded, saved = await asyncio.to_thread(ss.save, loaded, changes)
        except ss.SettingsError as e:
            await app.push_screen_wait(Confirm("NOT CHANGED", str(e), verb="OK"))
            continue
        await after_save(app, st, loaded, saved, keys)
