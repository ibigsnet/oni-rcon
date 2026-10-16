"""Playlists, from the console: the library (the content folder's Playlists, ReclaimerForge downloads included), what
each lobby plays, an editor whose ADD GAME is the map and game type browser, and deploying to one lobby or all of them.

The files and dedicated.toml are changed by the IBIGS `oni` helper (`oni playlist ...`), which has the server check
every game first, backs the configuration up and only restarts a lobby when asked, with the same player checks as F7.
This module only shows, asks and confirms. Every table here orders by a column when its name is clicked, and turns
the order round when it's clicked again."""
from __future__ import annotations

import time

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Static

from . import fuzzy
from .app import Dialog, Form
from .art import AMBER, CYAN, DIM, GOLD, GREEN, RED, WHITE
from .content import PlaylistError, check_playlist, game_label, new_playlist
from .safety import clean

SOURCE_WORDS = {"forge": ("RECLAIMERFORGE", GOLD), "oni": ("YOURS", GREEN), "other": ("CONTENT FOLDER", AMBER)}


class SortedTable(DataTable):
    """A table whose rows order by the column whose name is clicked: once in its natural direction (A–Z, most,
    newest), again the other way round. Rows are (id, cells, values), values being what each column orders by."""

    def __init__(self, columns: list[tuple[str, str, bool]], own_order: bool = True, **kw):
        super().__init__(cursor_type="row", zebra_stripes=True, **kw)
        self.own_order = own_order  # False: a header click is left to the screen (the editor reorders the games)
        self.spec = columns  # (key, label, high first)
        self.rows_in: list[tuple[str, list, dict]] = []
        self.order: tuple[str, bool] | None = None
        self.text = ""
        self.ids: list[str] = []

    def on_mount(self) -> None:
        for key, label, _ in self.spec:
            self.add_column(label, key=key)

    @property
    def selected(self) -> str | None:
        return self.ids[self.cursor_row] if 0 <= self.cursor_row < len(self.ids) else None

    def show(self, rows: list[tuple[str, list, dict]] | None = None, text: str | None = None) -> None:
        if rows is not None:
            self.rows_in = rows
        if text is not None:
            self.text = text.lower()
        keep = self.selected
        words = self.text.split()
        rows = [r for r in self.rows_in if not words or fuzzy.matches(self.text, *map(str, r[2].values()))]
        if self.order:
            key, flip = self.order
            high = next(h for k, _, h in self.spec if k == key)
            known = [r for r in rows if r[2].get(key) not in (None, "")]
            rest = [r for r in rows if r[2].get(key) in (None, "")]
            known.sort(key=lambda r: (lambda v: v.casefold() if isinstance(v, str) else v)(r[2][key]),
                       reverse=high != flip)
            rows = known + rest
        self.clear()
        self.ids = []
        for rid, cells, _ in rows:
            self.add_row(*cells, key=rid)
            self.ids.append(rid)
        for key, label, high in self.spec:
            arrow = ""
            if self.order and self.order[0] == key:
                arrow = " ▼" if high != self.order[1] else " ▲"
            self.columns[key].label = Text(label + arrow)
        if keep in self.ids:
            self.move_cursor(row=self.ids.index(keep))

    def on_data_table_header_selected(self, e: DataTable.HeaderSelected) -> None:
        if not self.own_order:
            return
        e.stop()
        key = str(e.column_key.value)
        cur = self.order
        self.order = (key, not cur[1]) if cur and cur[0] == key else (key, False)
        self.show()


def when(ts) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if isinstance(ts, (int, float)) else ""


def plays(lob: dict) -> Text:
    if lob.get("playlist"):
        return Text.assemble(("playlist ", DIM), (clean(lob["playlist"]).removeprefix("playlists/"), GOLD))
    return Text.assemble((clean(str(lob.get("map") or "?")), WHITE), (" / ", DIM),
                         (clean(str(lob.get("game") or "?")), WHITE))


class Playlists(Dialog):
    """The library and the lobbies. Dismisses with (action, library path or None) or None."""
    BINDINGS = [Binding("escape", "dismiss(None)", show=False)]

    def __init__(self, data: dict, note: str = ""):
        super().__init__()
        self.data, self.note = data, note

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog playlists"):
            yield Static(Text("PLAYLISTS"), classes="dialog-title")
            yield Input(placeholder="type to filter playlists: name, file, where from…", id="pl-filter")
            yield SortedTable([("name", "PLAYLIST", False), ("games", "GAMES", True), ("source", "FROM", False),
                               ("path", "FILE", False), ("modified", "CHANGED", True)], id="pl-lib")
            yield Static(Text("WHAT EACH LOBBY PLAYS", AMBER), classes="pl-head")
            yield SortedTable([("service", "LOBBY", False), ("name", "NAME", False), ("plays", "PLAYS", False),
                               ("state", "STATE", False), ("players", "PLAYERS", True)], id="pl-lobbies")
            yield Static(Text(self.note, DIM), id="pl-note")
            with Horizontal(classes="dialog-buttons pl-buttons"):
                yield Button("NEW", id="new", variant="success", compact=True)
                yield Button("EDIT", id="edit", compact=True)
                yield Button("USE ON LOBBIES…", id="use", variant="primary", compact=True)
                yield Button("CHECK", id="check", compact=True)
                yield Button("ONE MAP + MODE…", id="single", compact=True)
                yield Button("DELETE", id="delete", variant="error", compact=True)
                yield Button("CLOSE", id="no", compact=True)
            yield self.keys("Esc close", "click a column to order, again to reverse", "Enter use on lobbies",
                            "n new · e edit · c check · Del delete")

    def on_mount(self) -> None:
        lib = []
        for p in self.data.get("library") or []:
            path = str(p.get("path") or "")
            word, color = SOURCE_WORDS.get(p.get("source"), ("", DIM))
            if "problem" in p:
                cells = [Text(clean(path.rsplit("/", 1)[-1]), RED), "—", Text(word, color), Text(clean(path), DIM),
                         Text("PROBLEM: " + clean(p["problem"]), RED)]
            else:
                cells = [Text(clean(p.get("name") or "?"), WHITE), str(p.get("games", "")), Text(word, color),
                         Text(clean(path), DIM), when(p.get("modified"))]
            lib.append((path, cells, {"name": p.get("name") or path, "games": p.get("games"), "source": word,
                                      "path": path, "modified": p.get("modified")}))
        self.query_one("#pl-lib", SortedTable).show(lib)
        lobs = []
        for lob in self.data.get("lobbies") or []:
            players = lob.get("players")
            n = None if players is None else len(players)
            state = Text("RUNNING", GREEN) if lob.get("running") else Text("STOPPED", AMBER)
            lobs.append((lob["service"], [Text(clean(lob["service"]), CYAN), clean(str(lob.get("name") or "")),
                                          plays(lob), state,
                                          ("?" if n is None else str(n)) if lob.get("running") else "—"],
                         {"service": lob["service"], "name": lob.get("name"), "plays": plays(lob).plain,
                          "state": state.plain, "players": n}))
        self.query_one("#pl-lobbies", SortedTable).show(lobs)
        self.query_one("#pl-lib").focus()

    @on(Input.Changed, "#pl-filter")
    def _filter(self, e: Input.Changed) -> None:
        e.stop()
        self.query_one("#pl-lib", SortedTable).show(text=e.value)

    @on(Input.Submitted, "#pl-filter")
    def _to_list(self, e: Input.Submitted) -> None:
        e.stop()
        self.query_one("#pl-lib").focus()

    @on(DataTable.RowSelected, "#pl-lib")
    def _use(self, e: DataTable.RowSelected) -> None:
        e.stop()
        self.act("use")

    def on_key(self, event) -> None:
        if isinstance(self.focused, Input):
            return
        keys = {"n": "new", "e": "edit", "c": "check", "delete": "delete", "u": "use"}
        if event.key in keys:
            event.stop()
            self.act(keys[event.key])

    def on_button_pressed(self, e: Button.Pressed) -> None:
        e.stop()
        self.act(e.button.id)

    def act(self, what: str) -> None:
        if what == "no":
            self.dismiss(None)
            return
        path = self.query_one("#pl-lib", SortedTable).selected
        if what in ("edit", "use", "check", "delete") and not path:
            self.notify("Pick a playlist first, or NEW to make one.", severity="warning")
            return
        if what == "delete" and not path.lower().startswith("oni/"):
            self.notify("Only playlists made here can be deleted here. ReclaimerForge's stay with its downloads.",
                        severity="warning", timeout=8)
            return
        self.dismiss((what, path))


class Editor(Dialog):
    """A playlist: its name, the vote time and its games in order, each a map + game type and how often ballots
    offer it. Dismisses with the checked playlist, or None."""
    BINDINGS = [Binding("escape", "dismiss(None)", show=False), Binding("ctrl+s", "save", "Save", priority=True)]

    def __init__(self, playlist: dict, title: str = "NEW PLAYLIST", where: str = ""):
        super().__init__()
        self.p = {**new_playlist(), **playlist, "games": [dict(g) for g in playlist.get("games") or []]}
        self.t, self.where = title, where

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog playlists"):
            yield Static(Text(self.t), classes="dialog-title")
            with Horizontal(classes="pe-fields"):
                yield Static("NAME", classes="pe-label")
                yield Input(self.p.get("name") or "", id="pe-name")
                yield Static("VOTE SECONDS", classes="pe-label")
                yield Input(str(self.p.get("vote_seconds", 20)), id="pe-vote", type="integer")
            yield SortedTable([("n", "#", False), ("map", "MAP", False), ("game", "GAME TYPE", False),
                               ("weight", "WEIGHT", True), ("share", "OFFERED", True)], own_order=False,
                              id="pe-games")
            yield Static("", id="pe-note")
            with Horizontal(classes="dialog-buttons pl-buttons"):
                yield Button("ADD GAME", id="add", variant="success", compact=True)
                yield Button("COPY", id="dup", compact=True)
                yield Button("WEIGHT +", id="more", compact=True)
                yield Button("WEIGHT −", id="less", compact=True)
                yield Button("UP", id="up", compact=True)
                yield Button("DOWN", id="down", compact=True)
                yield Button("REMOVE", id="remove", variant="error", compact=True)
                yield Button("SAVE", id="save", variant="primary", compact=True)
                yield Button("CANCEL", id="no", compact=True)
            yield self.keys("Esc cancel", "a add · + − weight · [ ] move · Del remove", "click a column to sort the "
                            "games by it", "Ctrl+S save")

    def on_mount(self) -> None:
        self.paint()
        self.query_one("#pe-games").focus()

    def paint(self, cursor: int | None = None) -> None:
        t = self.query_one("#pe-games", SortedTable)
        games = self.p["games"]
        total = sum(int(g.get("weight", 1)) for g in games) or 1
        rows = []
        for i, g in enumerate(games):
            w = int(g.get("weight", 1))
            share = round(100 * w / total)
            rows.append((str(i), [str(i + 1), Text(clean(str(g.get("map", "?"))), WHITE),
                                  Text(clean(str(g.get("game", "?"))), WHITE), str(w), f"{share}%"],
                         {"n": i, "map": str(g.get("map", "")), "game": str(g.get("game", "")), "weight": w,
                          "share": share}))
        t.order = None  # the playlist's own order; a header click sorts the games themselves (see below)
        t.show(rows)
        if cursor is not None and 0 <= cursor < len(games):
            t.move_cursor(row=cursor)
        self.query_one("#pe-note", Static).update(Text(
            f"{len(games)} game{'s' * (len(games) != 1)}" + (f"  ·  saved as {self.where}" if self.where else
                                                              "  ·  saved in Playlists/Oni"), DIM))

    @on(DataTable.HeaderSelected, "#pe-games")
    def _sort_games(self, e: DataTable.HeaderSelected) -> None:
        """In the editor, ordering by a column reorders the playlist itself: the rotation follows it."""
        e.stop()
        key = str(e.column_key.value)
        if key in ("n", "share"):
            return
        t = self.query_one("#pe-games", SortedTable)
        flip = bool(t.order and t.order[0] == key and not t.order[1])
        value = (lambda g: int(g.get("weight", 1))) if key == "weight" else (lambda g: str(g.get(key, "")).casefold())
        self.p["games"].sort(key=value, reverse=(key == "weight") != flip)
        self.paint()
        t.order = (key, flip)  # shows the arrow; the rows are already in that order
        t.show()

    def row(self) -> int:
        t = self.query_one("#pe-games", SortedTable)
        return int(t.selected) if t.selected is not None else -1

    def on_key(self, event) -> None:
        if isinstance(self.focused, Input):
            return
        keys = {"a": "add", "plus": "more", "equals_sign": "more", "minus": "less", "left_square_bracket": "up",
                "right_square_bracket": "down", "delete": "remove"}
        if event.key in keys:
            event.stop()
            self.act(keys[event.key])

    def on_button_pressed(self, e: Button.Pressed) -> None:
        e.stop()
        self.act(e.button.id)

    def action_save(self) -> None:
        self.act("save")

    def act(self, what: str) -> None:
        games, i = self.p["games"], self.row()
        if what == "no":
            self.dismiss(None)
        elif what == "add":
            self.add_game()
        elif what == "save":
            self.p["name"] = self.query_one("#pe-name", Input).value.strip()
            vote = self.query_one("#pe-vote", Input).value.strip()
            self.p["vote_seconds"] = int(vote) if vote.lstrip("-").isdigit() else -1
            try:
                self.dismiss(check_playlist(self.p))
            except PlaylistError as e:
                self.notify(str(e), title="NOT SAVED", severity="warning", timeout=8)
        elif i < 0:
            self.notify("Pick a game first.", severity="warning")
        elif what == "dup":
            games.insert(i + 1, dict(games[i]))
            self.paint(i + 1)
        elif what in ("more", "less"):
            w = int(games[i].get("weight", 1)) + (1 if what == "more" else -1)
            games[i]["weight"] = max(1, min(100, w))
            self.paint(i)
        elif what in ("up", "down"):
            j = i - 1 if what == "up" else i + 1
            if 0 <= j < len(games):
                games[i], games[j] = games[j], games[i]
                self.paint(j)
        elif what == "remove":
            games.pop(i)
            self.paint(min(i, len(games) - 1))

    @work(exclusive=True, group="pe-add")
    async def add_game(self) -> None:
        """A map, then a game type to play on it, both from the browser of the selected server."""
        app, st = self.app, self.app.cur
        if not (st.data.get("maps", {}).get("entries") and st.data.get("modes", {}).get("entries")):
            f = await app.push_screen_wait(Form(
                "ADD GAME", [("map", "Map (as the server names it)", ""), ("game", "Game type", "")], verb="ADD",
                required=("map", "game"), note=f"{st.label} isn't connected, so its maps can't be listed: type the "
                                                f"names. The server checks them before the playlist is used."))
            if not f:
                return
            mp, md = f["map"], f["game"]
        else:
            mp = await app.browse(st, "maps", "ADD GAME · MAP")
            if not mp:
                return
            md = await app.browse(st, "modes", f"ADD GAME · {mp} · GAME TYPE")
            if not md:
                return
        self.p["games"].append({"map": mp, "game": md})
        self.paint(len(self.p["games"]) - 1)


def describe_lobbies(lobs: list[dict]) -> str:
    out = []
    for lob in lobs:
        who = lob.get("players")
        state = ("stopped" if not lob.get("running") else "players unknown" if who is None else
                 f"{len(who)} playing: {', '.join(clean(p) for p in who)}" if who else "empty")
        out.append(f"  {lob['service']}  {clean(str(lob.get('name') or ''))}  ({state})  now: {plays(lob).plain}")
    return "\n".join(out)
