"""The map and game type browser behind LOAD MAP+MODE, CHANGE MAP, CHANGE MODE, QUEUE NEXT and the playlist editor:
a tree of what the server has (stock, mods, ReclaimerForge downloads, the content folder, built-in and saved
variants), plus what ReclaimerForge has that it doesn't, which installs first. Type to filter; click NAME, INSTALLED,
DOWNLOADS or RATING to order by it, and again to turn the order round."""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Static, Tree

from .app import Dialog
from .art import AMBER, CYAN, DIM, GOLD, GREEN, WHITE
from .content import GROUP_HELP, HIGH_FIRST, SORTS, Item, arrange
from .safety import clean

GROUP_COLOR = {"stock": WHITE, "mods": CYAN, "forge": GOLD, "folder": AMBER, "builtin": WHITE, "saved": WHITE,
               "catalog": GREEN}
OPEN_ALL = 60  # with this few items or fewer, every group starts open


class Keep:
    """A choice that isn't a server reference: "keep the current rules" for QUEUE NEXT."""

    def __init__(self, label: str, value: str = ""):
        self.label, self.value = label, value


class Browser(Dialog):
    """Returns a reference (str), the catalog listing to install first (dict), a Keep's value, or None."""
    BINDINGS = [Binding("escape", "dismiss(None)", show=False), Binding("down", "focus_tree", show=False)]

    def __init__(self, title: str, what: str, items: list[Item], *, sort: str = "name", flip: bool = False,
                 keep: Keep | None = None, playing: str = "",
                 catalog: Callable[[str, bool], Awaitable[list[Item]]] | None = None):
        super().__init__()
        self.t, self.what, self.items, self.keep = title, what, items, keep
        self.sort, self.flip = sort, flip
        self.playing = (playing or "").lower()
        self.catalog, self.fetched, self.text = catalog, None, ""

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog browse"):
            yield Static(Text(self.t), classes="dialog-title")
            yield Input(placeholder="type to filter: name, author, folder, file…", id="browse-filter")
            with Horizontal(id="browse-cols"):
                yield Static(Text("ORDER BY", DIM), id="browse-by")
                for key, word in SORTS:
                    yield Button(word, id=f"browse-by-{key}", classes="browse-col", compact=True)
            tree: Tree = Tree("content", id="browse-tree")
            tree.show_root = False
            tree.guide_depth = 3
            yield tree
            yield Static("", id="browse-detail")
            yield self.keys("Esc cancel", "type to filter", "↓ to the list", "click a column or s to order",
                            "Enter or click to choose")

    def on_mount(self) -> None:
        self.paint_columns()
        self.fill()
        self.query_one("#browse-filter").focus()

    def paint_columns(self) -> None:
        for key, word in SORTS:
            b = self.query_one(f"#browse-by-{key}", Button)
            on_ = key == self.sort
            b.label = word + ((" ▼" if (key in HIGH_FIRST) != self.flip else " ▲") if on_ else "")
            b.variant = "primary" if on_ else "default"

    # --- the tree -----------------------------------------------------------------------------------------------
    def fill(self) -> None:
        tree = self.query_one(Tree)
        tree.clear()
        pool = self.items + (self.fetched or [])
        groups = arrange(pool, self.what, self.sort, self.text, self.flip)
        total = sum(len(rows) for key, _, rows in groups if key != "catalog")
        if self.keep and not self.text:
            tree.root.add_leaf(Text(self.keep.label, DIM), data=self.keep)
        for key, title, rows in groups:
            if key == "catalog" and not self.catalog:
                continue
            label = Text.assemble((title.upper(), f"bold {GROUP_COLOR.get(key, WHITE)}"), (f"  {len(rows)}", DIM))
            node = tree.root.add(label, data=key)
            folders: dict[str, object] = {}
            for item in rows:
                parent = node
                if item.folder:
                    if item.folder not in folders:
                        folders[item.folder] = node.add(Text(item.folder + "/", AMBER), data=key)
                    parent = folders[item.folder]
                parent.add_leaf(self.label_of(item), data=item)
            for f in folders.values():
                if self.text or total <= OPEN_ALL:
                    f.expand()
            if self.text or total <= OPEN_ALL or key in ("stock", "catalog") or any(map(self.is_playing, rows)):
                node.expand()
        if self.catalog and not any(k == "catalog" for k, _, _ in groups) and not self.text:
            node = tree.root.add(Text.assemble(("GET MORE FROM RECLAIMERFORGE", f"bold {GREEN}"),
                                               ("  open to look", DIM)), data="catalog")
            node.add_leaf(Text("… loading the catalog" if self.fetched is None else "nothing new to add", DIM))
        if not groups and not (self.keep and not self.text):
            tree.root.add_leaf(Text("Nothing matches. Clear the filter to see everything.", DIM))
        self.query_one("#browse-detail", Static).update(Text(f"{total} on this server", DIM))

    def is_playing(self, item: Item) -> bool:
        """The server's status names what's playing by its name; a reference matches as well."""
        return bool(self.playing) and self.playing in (item.reference.lower(), item.name.lower())

    def label_of(self, item: Item) -> Text:
        t = Text(clean(item.name) or "?", WHITE)
        if self.is_playing(item):
            t.append("  ● playing", GREEN)
        if item.detail and item.detail != item.name:
            t.append("  " + clean(item.detail), DIM)
        return t

    @on(Tree.NodeExpanded)
    def _expanded(self, e: Tree.NodeExpanded) -> None:
        if e.node.data == "catalog" and self.catalog and self.fetched is None:
            self.load_catalog()

    @work(exclusive=True, group="browse-catalog")
    async def load_catalog(self) -> None:
        try:
            self.fetched = await self.catalog(self.sort, self.flip)
        except Exception as exc:  # the catalog is an extra: its failure never closes the browser
            self.fetched = []
            self.query_one("#browse-detail", Static).update(Text(f"ReclaimerForge: {exc}", AMBER))
            return
        self.fill()

    # --- choosing -----------------------------------------------------------------------------------------------
    def chosen(self, data) -> None:
        if isinstance(data, Keep):
            self.dismiss(data.value)
        elif isinstance(data, Item):
            self.dismiss(data.forge if data.group == "catalog" else data.reference)

    @on(Tree.NodeSelected)
    def _selected(self, e: Tree.NodeSelected) -> None:
        e.stop()
        if isinstance(e.node.data, (Item, Keep)):
            self.chosen(e.node.data)

    @on(Tree.NodeHighlighted)
    def _highlighted(self, e: Tree.NodeHighlighted) -> None:
        d = e.node.data
        if isinstance(d, Item):
            where = d.reference if d.group != "catalog" else "on ReclaimerForge: choosing it installs it here first"
            text = Text.assemble((clean(d.name), WHITE), ("   " + clean(where), DIM))
        elif isinstance(d, str):
            text = Text(GROUP_HELP.get(d, ""), DIM)
        else:
            text = Text("")
        self.query_one("#browse-detail", Static).update(text)

    @on(Input.Changed, "#browse-filter")
    def _filter(self, e: Input.Changed) -> None:
        e.stop()
        self.text = e.value.strip()
        self.fill()

    @on(Input.Submitted, "#browse-filter")
    def _first(self, e: Input.Submitted) -> None:
        e.stop()
        leaves = [n for n in self.walk() if isinstance(n.data, (Item, Keep))]
        if leaves and self.text:
            self.chosen(leaves[0].data)
        else:
            self.action_focus_tree()

    def walk(self):
        stack = list(reversed(self.query_one(Tree).root.children))
        while stack:
            n = stack.pop()
            yield n
            stack.extend(reversed(n.children))

    def action_focus_tree(self) -> None:
        tree = self.query_one(Tree)
        tree.focus()
        if tree.cursor_line < 0:
            tree.cursor_line = 0

    def on_key(self, event) -> None:
        if isinstance(self.focused, Tree) and event.key in ("s", "S", "slash"):
            event.stop()
            if event.key == "slash":
                self.query_one("#browse-filter").focus()
            elif event.key == "S":
                self.order(self.sort)
            else:
                keys = [k for k, _ in SORTS]
                self.order(keys[(keys.index(self.sort) + 1) % len(keys)])

    @on(Button.Pressed, ".browse-col")
    def _column(self, e: Button.Pressed) -> None:
        e.stop()
        self.order(str(e.button.id).removeprefix("browse-by-"))

    def order(self, key: str) -> None:
        """A new column starts in its natural direction (A–Z, newest, most, best); the same one again turns it."""
        self.flip = not self.flip if key == self.sort else False
        self.sort = key
        if self.fetched is not None:  # the catalog branch is asked for again in the new order
            self.fetched = None
            if self.catalog:
                self.load_catalog()
        self.paint_columns()
        self.fill()
