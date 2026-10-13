"""Copy anything the console shows: Ctrl+Y on any page or dialog lists what can be copied from where you are (the
selected row's cells, the row, the whole table, and what the page knows about the selection, such as a player's
ID, tag and clan), and copies the one chosen. The terminal gets it with OSC 52, which most terminals and tmux pass
on to the system clipboard; the toast shows the text too, for the few that don't.

Addresses stay blanked while they're redacted (x), and a Forge key is never copied."""
from __future__ import annotations

from rich.text import Text
from textual.widgets import DataTable, Tree

from .forge import scrub

ARROWS = (" ▲", " ▼")
PREVIEW = 60


def plain(v) -> str:
    s = v.plain if isinstance(v, Text) else str(v if v is not None else "")
    return s.strip()


def header(col) -> str:
    h = plain(col.label)
    for a in ARROWS:
        h = h.removesuffix(a)
    return h.strip()


def table_choices(t: DataTable) -> list[tuple[str, str]]:
    """(what, text) for a table: each cell of the selected row, the row's own ID, the row, and the whole table."""
    if not t.row_count:
        return []
    heads = [header(c) for c in t.ordered_columns]
    out = []
    row = t.cursor_row if t.show_cursor and t.cursor_type == "row" or t.cursor_type == "cell" else -1
    if 0 <= row < t.row_count:
        cells = [plain(v) for v in t.get_row_at(row)]
        ids = getattr(t, "ids", None)
        rid = ids[row] if isinstance(ids, list) and row < len(ids) else None
        if rid and not str(rid).isdigit() and rid not in cells:
            out.append(("ID", str(rid)))
        for h, v in zip(heads, cells):
            if v and v not in ("—", "-"):
                out.append((h or "MARK", v))
        out.append(("This row", "\t".join(f"{h}: {v}" if h else v for h, v in zip(heads, cells) if v)))
    lines = ["\t".join(heads)] + ["\t".join(plain(v) for v in t.get_row_at(r)) for r in range(t.row_count)]
    out.append((f"The whole table ({t.row_count} rows)", "\n".join(lines)))
    return out


def tree_choices(tree: Tree) -> list[tuple[str, str]]:
    node = tree.cursor_node
    if node is None:
        return []
    data = node.data
    out = []
    name, ref = getattr(data, "name", None), getattr(data, "reference", None)
    if name:
        out.append(("Name", str(name)))
    if ref and ref != name:
        out.append(("Reference (what the server loads)", str(ref)))
    forge = getattr(data, "forge", None) or {}
    if isinstance(forge, dict) and (lid := forge.get("listing_id") or forge.get("id")):
        out.append(("ReclaimerForge listing ID", str(lid)))
    if not out:
        out.append(("This line", plain(node.label)))
    return out


def widget_choices(w) -> list[tuple[str, str]]:
    """What the focused widget itself offers, walking up from it to the nearest table or tree."""
    while w is not None:
        if isinstance(w, DataTable):
            return table_choices(w)
        if isinstance(w, Tree):
            return tree_choices(w)
        w = w.parent
    return []


def finish(choices: list[tuple[str, str]], redact) -> list[tuple[str, str]]:
    """Cleaned, deduplicated, and safe to put on a clipboard."""
    out, seen = [], set()
    for what, text in choices:
        text = redact(scrub(str(text))).strip()
        if text and (what, text) not in seen:
            seen.add((what, text))
            out.append((what, text))
    return out


def label(what: str, text: str) -> Text:
    one = text.replace("\n", " ⏎ ").replace("\t", "  ")
    return Text.assemble((what, "bold"), ("   " + (one[:PREVIEW] + "…" if len(one) > PREVIEW else one), "grey62"))
