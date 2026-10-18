"""F8 AUTOMATION: rules, scheduled jobs, roles, the votes switch and Discord, for customer VPSes (0.9.0+ibigs.15).

Everything here edits files under /srv/ibigs/automation; the background service (`oni jobs run`) does the work, so it
keeps happening when oni is closed. Every screen says in plain words what it does and when it takes effect.
"""
from __future__ import annotations

import asyncio
import re
import time

from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, OptionList, Static
from textual.widgets.option_list import Option

from . import rules as R
from .app import Confirm, Dialog, Pick
from .art import AMBER, CYAN, DIM, GREEN, RED, WHITE
from .jobs_ui import JobForm, jobs_dialog
from .safety import clean

ROLE_CHOICES = [("ADMIN", ["admin"]), ("VIP", ["vip"]), ("MOD", ["mod"]), ("ADMIN + VIP", ["admin", "vip"]),
                ("ADMIN + MOD", ["admin", "mod"]), ("VIP + MOD", ["vip", "mod"]), ("ADMIN + VIP + MOD", list(R.ROLES)),
                ("No role (remove)", [])]
ROLE_WORDS = {"admin": "ADMIN: can use admin commands in game. Applied to the server the next time it's empty.",
              "vip": "VIP: can't be vote-kicked (active now). More VIP perks arrive with future Reclaimer builds.",
              "mod": "MOD: saved now; takes effect when Project Reclaimer adds moderators."}


class WidePick(Pick):
    """A pick list wide enough for each choice's explanation."""


class Panel(Dialog):
    """A list with buttons: returns (button id, highlighted value) or None. `body` explains, `foot` shows recent."""
    BINDINGS = [Binding("escape", "dismiss(None)", show=False)]

    def __init__(self, title, body, options, buttons, foot="", empty="Nothing here yet."):
        super().__init__()
        self.t, self.body, self.opts, self.buttons, self.foot, self.empty = title, body, options, buttons, foot, empty

    def compose(self):
        with Vertical(classes="dialog jobs automation"):
            yield Static(Text(self.t), classes="dialog-title")
            if self.body:
                yield Static(self.body if isinstance(self.body, Text) else Text(self.body), classes="dialog-body")
            ol = OptionList(id="panel-list")
            yield ol
            if self.foot:
                yield Static(self.foot if isinstance(self.foot, Text) else Text(self.foot, DIM), classes="jobs-history")
            with Horizontal(classes="dialog-buttons"):
                for label, ident, needs_item in self.buttons:
                    yield Button(label, id="p-" + ident, disabled=needs_item and not self.opts,
                                 variant="primary" if ident in ("new", "template", "add") else "default")
            yield self.keys("Esc close", "↑ ↓ choose", "Tab buttons")

    def on_mount(self):
        ol = self.query_one(OptionList)
        if self.opts:
            ol.add_options([Option(lbl if isinstance(lbl, Text) else Text(str(lbl)), id=str(i))
                            for i, (lbl, _) in enumerate(self.opts)])
            ol.highlighted = 0
        else:
            ol.add_option(Option(Text(self.empty, DIM), id="none", disabled=True))
        ol.focus()

    def chosen(self):
        i = self.query_one(OptionList).highlighted
        return self.opts[i][1] if self.opts and i is not None and i < len(self.opts) else None

    def on_option_list_option_selected(self, e):
        e.stop()
        first = next((ident for _, ident, needs in self.buttons if needs), None)
        if first and self.opts:
            self.dismiss((first, self.chosen()))

    def on_button_pressed(self, e: Button.Pressed):
        e.stop()
        ident = e.button.id.removeprefix("p-")
        self.dismiss(None if ident == "close" else (ident, self.chosen()))


def lobby_id(st) -> str:
    """How rules name a server: h3-01 on a customer VPS, the lobby ID (node-a-49178) on the IBIGS fleet."""
    return st.server.url.removeprefix("fleet://") if st.server.url.startswith("fleet://") else st.server.name


class FleetRuleStore:
    """The same interface as rules.RuleStore, kept by the fleet admin console (/api/rules) and run by every node.
    Changes apply here at once and are pushed in the background, so the screen never waits on the nodes."""
    fleet_mode = True

    def __init__(self, fleet):
        import threading
        self.fleet, self.lock = fleet, threading.Lock()
        self.doc = {"enabled": False, "rules": [], "roles": {}, "webhooks": {}, "nodes": {}, "history": []}
        self.pending = 0
        self.last_error = ""

    def load(self):
        """Blocking GET; call it off the UI thread."""
        if not self.fleet.view:
            self.fleet.refresh()  # the CSRF token and origin POSTs need
        got = self.fleet.request("/api/rules", None, 40)
        if not isinstance(got, dict) or not isinstance(got.get("rules"), list):
            raise ValueError("invalid rules reply")
        with self.lock:
            self.doc = got
        return got

    def _post(self, change):
        import threading

        def go():
            try:
                got = self.fleet.request("/api/rules", change, 120)
                with self.lock:
                    self.doc, self.last_error = got, ""
            except Exception as exc:
                self.last_error = clean(str(exc))[:200]
            finally:
                self.pending -= 1
        self.pending += 1
        threading.Thread(target=go, daemon=True).start()

    def rules(self):
        return [R.validate(r) for r in self.doc.get("rules", [])]

    def save_rule(self, rule=None, delete=None):
        rules = self.rules()
        if rule is not None:
            rule = R.validate(rule)
        ident = rule["id"] if rule else delete
        rules = [r for r in rules if r["id"] != ident] + ([rule] if rule else [])
        if len(rules) > R.MAX_RULES:
            raise ValueError(f"Up to {R.MAX_RULES} rules.")
        self.doc["rules"] = rules
        self._post({"rules": rules})

    def roles(self):
        return {pid: {"name": p.get("name", ""), "roles": p.get("roles", []), "servers": "all"}
                for pid, p in (self.doc.get("roles") or {}).items()}, {}

    def set_roles(self, pid, name, roles, servers="all"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(pid)):
            raise ValueError("That isn't a player ID.")
        players, _ = self.roles()
        if roles:
            players[pid] = {"name": clean(str(name))[:40], "roles": roles, "servers": "all"}
        else:
            players.pop(pid, None)
        self.doc["roles"] = players
        self._post({"roles": players})

    def set_votes(self, lobby, on):
        raise ValueError("Fleet votes are managed by vote guard on each node.")

    def webhooks(self):
        return dict(self.doc.get("webhooks") or {})  # masked: the full URLs stay in the fleet console and the nodes

    def save_webhook(self, name, url=None):
        if url is not None and not R.webhook_ok(url):
            raise ValueError("That doesn't look like a Discord webhook URL. It starts with "
                             "https://discord.com/api/webhooks/")
        if url is None:
            self.doc.get("webhooks", {}).pop(name, None)
        else:
            self.doc.setdefault("webhooks", {})[name] = R.masked(url)
        self._post({"webhooks": {name: url}})

    def enabled(self):
        return bool(self.doc.get("enabled"))

    def set_enabled(self, on):
        self.doc["enabled"] = bool(on)
        self._post({"enabled": bool(on)})

    def history(self):
        return [{**h, "server": h.get("server") or h.get("node", "")} for h in self.doc.get("history", [])]

    def nodes_text(self):
        bits = []
        for node, r in sorted((self.doc.get("nodes") or {}).items()):
            bits.append(f"{node} {'✓' if r.get('ok') else '✗ ' + str(r.get('error') or 'not reached')}")
        state = "saving…" if self.pending else self.last_error and "last save failed: " + self.last_error
        return " · ".join(bits) + (f"   ({state})" if state else "")


def lobbies_of(app) -> list[str]:
    return sorted({lobby_id(st) for st in app.stations if R.SERVER.fullmatch(lobby_id(st))})


def live_engine(app, rules_=(), roles=None) -> R.Engine:
    """The rules engine as this console sees the servers right now: for TEST and for who's online where."""
    e = R.Engine(rules_, roles or {})
    for st in app.stations:
        name = lobby_id(st)
        e.connection(name, bool(st.online))
        e.status(name, st.data.get("status", {}))
        e.players(name, [{"engine_id": p.get("engine_id", i), "name": p.get("name"), "admin": p.get("admin"),
                          "id": p.get("player_id") or p.get("id"), "team": p.get("team")}
                         for i, p in enumerate(st.players)])
    return e


# --- the hub -------------------------------------------------------------------------------------------------------
HUB_TEXT = ("Automation runs on this server even when oni is closed.\n\n"
            "RULES      When something happens (a killing spree, a !command, a server going down), do something.\n"
            "SCHEDULES  Do something at set times: messages, restarts, updates.\n"
            "ROLES      Make players ADMIN, VIP or MOD.\n"
            "VOTES      Turn player voting on or off for each server.\n"
            "DISCORD    Connect a Discord channel so rules can post there.")


FLEET_HUB_TEXT = ("Fleet rules run on every node, on that node's lobbies, even when no console is open.\n\n"
                  "RULES    When something happens (a spree, a !command, a server going down), do something.\n"
                  "ROLES    VIP (can't be vote-kicked) and MOD; ADMIN here counts for rules. The game's own admin\n"
                  "         list on fleet servers is the fleet admin list, which rules also treat as ADMIN.\n"
                  "DISCORD  Connect a Discord channel so rules can post there.\n"
                  "SWITCH   Rules only run while the fleet switch is ON. It starts OFF.")


async def automation_dialog(app, targets, store=None):
    fleet = store is not None
    store = store or R.RuleStore()
    while True:
        if fleet:
            try:
                await asyncio.to_thread(store.load)
            except Exception as exc:
                app.notify(f"Can't reach the fleet's rules: {clean(str(exc))[:160]}", title="Fleet rules",
                           severity="error", timeout=12)
                return
            choice = await app.push_screen_wait(Panel(
                "FLEET AUTOMATION", FLEET_HUB_TEXT, [
                    (Text.assemble(("SWITCH     ", CYAN), ("ON: rules are running" if store.enabled() else
                                                          "OFF: no rule runs anywhere", GREEN if store.enabled() else AMBER)), "switch"),
                    (Text.assemble(("RULES      ", CYAN), (f"{sum(r['enabled'] for r in store.rules())} on, "
                                                           f"{len(store.rules())} in all", WHITE)), "rules"),
                    (Text.assemble(("ROLES      ", CYAN), (f"{len(store.roles()[0])} player(s)", WHITE)), "roles"),
                    (Text.assemble(("DISCORD    ", CYAN), (f"{len(store.webhooks())} webhook(s)", WHITE)), "discord")],
                [("CLOSE", "close", False), ("OPEN", "open", True)], foot=store.nodes_text() or "Not pushed to any node yet."))
            if not choice:
                return
            section = choice[1]
            try:
                if section == "switch":
                    on = not store.enabled()
                    if await app.push_screen_wait(Confirm("FLEET RULES " + ("ON" if on else "OFF"),
                            ("Every node starts running the enabled rules, with one console connection per lobby."
                             if on else "Every node stops running rules and closes their connections."),
                            verb="TURN " + ("ON" if on else "OFF"), danger=on)):
                        store.set_enabled(on)
                        app.notify("Sending to every node…", title="Fleet rules", timeout=5)
                elif section == "rules":
                    await rules_dialog(app, store, targets)
                elif section == "roles":
                    await roles_dialog(app, store, targets)
                elif section == "discord":
                    await discord_dialog(app, store)
            except (OSError, ValueError) as exc:
                app.notify(clean(str(exc)), title="Fleet rules", severity="error", timeout=12)
            await asyncio.sleep(0)
            continue
        try:
            rules_ = store.rules()
            players, votes = store.roles()
            hooks = store.webhooks()
        except (OSError, ValueError) as exc:
            app.notify(clean(str(exc)), title="Automation", severity="error", timeout=12)
            return
        opts = [(Text.assemble(("RULES      ", CYAN), (f"{sum(r['enabled'] for r in rules_)} on, "
                                                         f"{len(rules_)} in all", WHITE)), "rules"),
                (Text.assemble(("SCHEDULES  ", CYAN), ("timed messages, restarts and updates", WHITE)), "jobs"),
                (Text.assemble(("ROLES      ", CYAN), (f"{len(players)} player(s) with a role", WHITE)), "roles"),
                (Text.assemble(("VOTES      ", CYAN), (", ".join(f"{k} {'on' if v else 'off'}" for k, v in
                                                                 sorted(votes.items())) or "as each server is set",
                                                       WHITE)), "votes"),
                (Text.assemble(("DISCORD    ", CYAN), (f"{len(hooks)} webhook(s)" if hooks else "not connected",
                                                       WHITE)), "discord")]
        choice = await app.push_screen_wait(Panel("AUTOMATION", HUB_TEXT, opts,
                                                  [("CLOSE", "close", False), ("OPEN", "open", True)]))
        if not choice:
            return
        section = choice[1]
        try:
            if section == "rules":
                await rules_dialog(app, store, targets)
            elif section == "jobs":
                await jobs_dialog(app, targets)
            elif section == "roles":
                await roles_dialog(app, store, targets)
            elif section == "votes":
                await votes_dialog(app, store, targets)
            elif section == "discord":
                await discord_dialog(app, store)
        except (OSError, ValueError) as exc:
            app.notify(clean(str(exc)), title="Automation", severity="error", timeout=12)


# --- rules ---------------------------------------------------------------------------------------------------------
RULES_TEXT = ("A rule is WHEN something happens → IF (optional) these are true → THEN do these things.\n"
              "Start from a TEMPLATE and change the words, or build one with NEW. TEST shows exactly what a rule "
              "would do, without sending anything.")


def rule_line(r) -> Text:
    return Text.assemble(("ON   " if r["enabled"] else "OFF  ", GREEN if r["enabled"] else DIM), (r["name"], WHITE),
                         ("   " + R.describe_block("when", r["when"]), DIM))


async def rules_dialog(app, store, targets):
    while True:
        rules_ = store.rules()
        history = store.history()[-8:]
        foot = Text()
        for h in reversed(history):
            foot.append(time.strftime("%H:%M ", time.localtime(h["at"])), DIM)
            foot.append((h.get("name") or h.get("rule") or "") + " ", WHITE)
            foot.append((f"[{h['server']}] " if h.get("server") else "") + h["result"] + "\n", DIM)
        choice = await app.push_screen_wait(Panel(
            "RULES", RULES_TEXT, [(rule_line(r), r["id"]) for r in rules_],
            [("CLOSE", "close", False), ("TEMPLATE", "template", False), ("NEW", "new", False),
             ("EDIT", "edit", True), ("TEST", "test", True), ("ON / OFF", "toggle", True), ("DELETE", "delete", True)],
            foot=foot or Text("Nothing has fired yet.", DIM), empty="No rules yet: choose TEMPLATE or NEW."))
        if not choice:
            return
        action, ident = choice
        rule = next((r for r in rules_ if r["id"] == ident), None)
        if action == "template":
            pick = await app.push_screen_wait(WidePick("START FROM A TEMPLATE", [
                (Text.assemble((s["name"], WHITE), ("  " + R.describe_block("when", R._block("when", s["when"])), DIM)), i)
                for i, s in enumerate(R.STARTERS)]))
            if pick is not None:
                await edit_rule(app, store, targets, dict(R.STARTERS[pick], id=None))
        elif action == "new":
            await edit_rule(app, store, targets, None)
        elif action == "edit" and rule:
            await edit_rule(app, store, targets, rule)
        elif action == "test" and rule:
            await test_rule(app, store, rule)
        elif action == "toggle" and rule:
            store.save_rule({**rule, "enabled": not rule["enabled"]})
            app.notify(f"{rule['name']}: {'OFF' if rule['enabled'] else 'ON'}. The service picks it up within seconds.",
                       title="Rules", timeout=5)
        elif action == "delete" and rule:
            if await app.push_screen_wait(Confirm("DELETE RULE", R.describe(rule), verb="DELETE")):
                store.save_rule(delete=rule["id"])


def field_input(f, current, targets, webhooks):
    """A catalog field as a JobForm field."""
    v = current if current is not None else f["default"]
    if f["kind"] == "choice":
        opts = [(o.replace("_", " ").title() if o not in ("this", "all", "other", "any") else
                 {"this": "This server", "all": "Every server", "other": "Other servers", "any": "Any"}[o], o)
                for o in f["options"]]
        return (f["key"], f["label"], sorted(opts, key=lambda x: x[1] != v))
    if f["kind"] == "server":
        return (f["key"], f["label"], sorted([(t, t) for t in targets], key=lambda x: x[1] != v) or [("(no servers)", "")])
    if f["kind"] == "webhook":
        return (f["key"], f["label"], sorted([(n, n) for n in webhooks], key=lambda x: x[1] != v)
                or [("(add one under DISCORD first)", "")])
    return (f["key"], f["label"] + (" — " + R.TEMPLATE_HELP if f["kind"] == "message" else ""), "" if v is None else v)


async def fill_block(app, part, kind, targets, webhooks, current=None):
    """Ask for one block's settings. None if the person backed out."""
    label, hint, fields = R.CATALOG[part][kind]
    block = {"type": kind}
    if fields:
        got = await app.push_screen_wait(JobForm(f"{part.upper()}: {label}".upper(),
                                                 [field_input(f, (current or {}).get(f["key"]), targets, webhooks)
                                                  for f in fields], verb="OK", note=hint))
        if got is None:
            return None
        block.update(got)
    return block


async def pick_kind(app, part, title, extra=()):
    opts = [*extra, *[(Text.assemble((lbl, WHITE), ("  " + hint, DIM)), k) for k, (lbl, hint, _) in R.CATALOG[part].items()]]
    return await app.push_screen_wait(WidePick(title, opts))


async def edit_rule(app, store, targets, rule):
    """The builder: name and servers, WHEN, IF (any number), THEN (one or more), limits, review."""
    webhooks = store.webhooks()
    rule = dict(rule or {"name": "My rule", "when": None, "if": [], "then": []})
    servers = rule.get("servers", "all")
    first = await app.push_screen_wait(JobForm("RULE", [
        ("name", "Name", rule.get("name") or "My rule"),
        ("servers", "Which servers", sorted([("Every server", "all"), *[(t, t) for t in targets]],
                                            key=lambda x: x[1] != (servers if isinstance(servers, str) else servers[0])))],
        verb="NEXT", required=("name",),
        note="Rules watch the servers you choose. You'll pick WHEN, IF and THEN next."))
    if first is None:
        return
    rule["name"], rule["servers"] = first["name"], "all" if first["servers"] == "all" else [first["servers"]]
    # WHEN
    keep = rule.get("when")
    extra = [(Text.assemble(("Keep: ", DIM), (R.describe_block("when", R._block("when", keep)), WHITE)), "_keep")] \
        if keep else []
    kind = await pick_kind(app, "when", "WHEN… (what starts the rule)", extra)
    if kind is None:
        return
    if kind != "_keep":
        when = await fill_block(app, "when", kind, targets, webhooks, keep if keep and keep["type"] == kind else None)
        if when is None:
            return
        rule["when"] = when
    # IF
    conds = list(rule.get("if") or [])
    while len(conds) < 8:
        listed = "\n".join("  • " + R.describe_block("if", R._block("if", c)) for c in conds) or "  (none: it always runs)"
        kind = await pick_kind(app, "if", f"IF… (only when these are true)\n{listed}",
                               [(Text("Done with conditions →", GREEN), "_done"),
                                *([(Text("Remove the last condition", AMBER), "_pop")] if conds else [])])
        if kind in (None, "_done"):
            break
        if kind == "_pop":
            conds.pop()
            continue
        block = await fill_block(app, "if", kind, targets, webhooks)
        if block:
            conds.append(block)
    rule["if"] = conds
    # THEN
    acts = list(rule.get("then") or [])
    while len(acts) < 8:
        listed = "\n".join("  • " + R.describe_block("then", R._block("then", a)) if a.get("type") != "discord" or
                           a.get("webhook") else "  • Post to Discord (choose a webhook)" for a in acts) or "  (nothing yet)"
        kind = await pick_kind(app, "then", f"THEN… (what to do, in order)\n{listed}",
                               [*([(Text("Done →", GREEN), "_done")] if acts else []),
                                *([(Text("Remove the last action", AMBER), "_pop")] if acts else [])])
        if kind is None:
            return
        if kind == "_done":
            break
        if kind == "_pop":
            acts.pop()
            continue
        if kind == "discord" and not webhooks:
            app.notify("Connect Discord first: F8 → DISCORD. It takes a minute.", title="Discord", timeout=8)
            await discord_dialog(app, store)
            webhooks = store.webhooks()
            if not webhooks:
                continue
        block = await fill_block(app, "then", kind, targets, webhooks)
        if block:
            acts.append(block)
    for a in acts:  # a template's Discord step with no webhook chosen yet: the first saved one
        if a.get("type") == "discord" and not a.get("webhook") and webhooks:
            a["webhook"] = sorted(webhooks)[0]
    rule["then"] = acts
    # limits
    defaults = R.validate({**rule, "id": rule.get("id") or "draft",
                           **({"cooldown": rule["cooldown"]} if "cooldown" in rule else {}),
                           **({"per_hour": rule["per_hour"]} if "per_hour" in rule else {})}) if acts else None
    if defaults is None:
        return
    limits = await app.push_screen_wait(JobForm("LIMITS", [
        ("cooldown", "Wait at least this many seconds before it runs again (per server and player; 0 = no wait)",
         str(defaults["cooldown"])),
        ("per_hour", "Never more than this many times an hour", str(defaults["per_hour"]))],
        verb="REVIEW", note="Limits keep a rule from spamming chat. On top of these, a server never gets more than "
                            "20 messages a minute from rules."))
    if limits is None:
        return
    try:
        rule.update(cooldown=int(limits["cooldown"]), per_hour=int(limits["per_hour"]))
    except ValueError:
        raise ValueError("Limits are whole numbers.") from None
    rule.setdefault("enabled", True)
    if not rule.get("id"):
        rule.pop("id", None)
    final = R.validate(rule)
    if await app.push_screen_wait(Confirm("SAVE RULE · " + final["name"], R.describe(final) +
                                          "\n\nIt starts working within seconds, also while oni is closed.",
                                          verb="SAVE", danger=False)):
        store.save_rule(final)
        app.notify(f"Saved {final['name']}. Use TEST to see what it does.", title="Rules", timeout=6)


async def test_rule(app, store, rule):
    players, _ = store.roles()
    engine = live_engine(app, [rule], players)
    servers = [n for n in (engine.lobbies if rule["servers"] == "all" else rule["servers"])] or ["h3-01"]
    steps = R.plan(engine, R.sample(engine, rule, servers[0]))
    lines = [R.describe(rule), "", f"If it fired now on {servers[0]} (with an example player):"]
    lines += ["  • " + s.words() for s in steps] or ["  • nothing: nobody to tell right now"]
    lines += ["", "Nothing was sent. Conditions, cooldowns and limits are skipped in a test."]
    await app.push_screen_wait(Confirm("TEST · " + rule["name"], "\n".join(lines), verb="OK", danger=False))


# --- roles ---------------------------------------------------------------------------------------------------------
ROLES_TEXT = ("ADMIN  admin commands in game. Written to the server the next time it's empty (nobody gets kicked).\n"
              "VIP    can't be vote-kicked (works now). More perks come with future Reclaimer builds.\n"
              "MOD    saved now; works when Project Reclaimer adds moderators.\n"
              "Pick someone who's online, or paste a player ID (F1 → the player's dossier shows it).")


async def roles_dialog(app, store, targets):
    while True:
        players, _ = store.roles()
        rc, out = await app.power_run("roles", "apply", "--check", "--json") if not getattr(store, "fleet_mode", False) \
            else (2, "")
        pending = "On the fleet, roles count for rules (VIP kick protection, tell ADMIN/VIP/MOD)." \
            if getattr(store, "fleet_mode", False) else ""
        if rc in (0, 1):
            import json
            try:
                rows = json.loads(out.strip().splitlines()[-1])
                waiting = [f"{r['lobby']}: {r['text']}" for r in rows if r.get("state") == "pending"]
                pending = "\n".join(waiting) or "Every server is up to date."
            except (ValueError, IndexError, KeyError, TypeError):
                pending = ""
        opts = [(Text.assemble((" ".join(r.upper() for r in p["roles"]).ljust(16), AMBER), (p["name"] or "?", WHITE),
                               ("  " + pid[:12] + "…", DIM),
                               ("  all servers" if p["servers"] == "all" else "  " + ", ".join(p["servers"]), DIM)), pid)
                for pid, p in sorted(players.items(), key=lambda kv: kv[1]["name"].lower())]
        choice = await app.push_screen_wait(Panel("ROLES", ROLES_TEXT, opts,
                                                  [("CLOSE", "close", False), ("ADD", "add", False),
                                                   ("CHANGE", "change", True), ("REMOVE", "remove", True)],
                                                  foot=pending, empty="Nobody has a role yet: choose ADD."))
        if not choice:
            return
        action, pid = choice
        if action == "add":
            await add_role(app, store, targets)
        elif action == "change" and pid:
            await set_role(app, store, targets, pid, players[pid]["name"], players[pid])
        elif action == "remove" and pid:
            if await app.push_screen_wait(Confirm("REMOVE ROLES", f"{players[pid]['name']} loses "
                                                  f"{', '.join(players[pid]['roles']).upper()}.", verb="REMOVE")):
                store.set_roles(pid, players[pid]["name"], [])


async def add_role(app, store, targets, preset=None):
    online = []
    for st in app.stations:
        for p in st.players:
            pid = str(p.get("player_id") or p.get("id") or "")
            if re.fullmatch(r"[0-9a-f]{64}", pid):
                online.append((Text.assemble((clean(str(p.get("name") or "?")), WHITE), (f"  on {lobby_id(st)}", DIM)),
                               (pid, clean(str(p.get("name") or "")))))
    if preset:
        pid, name = preset
    else:
        pick = await app.push_screen_wait(WidePick("WHO? (online now, or paste an ID)",
                                               [(Text("Paste a player ID instead…", AMBER), ("", "")), *online]))
        if pick is None:
            return
        pid, name = pick
        if not pid:
            got = await app.push_screen_wait(JobForm("PLAYER ID", [("pid", "Player ID (64 letters and numbers)", ""),
                                                                   ("name", "Name to show", "")],
                                                     verb="NEXT", required=("pid",),
                                                     note="F1 → choose the player → the dossier shows their ID. "
                                                          "Ctrl+Y copies it."))
            if not got:
                return
            pid, name = got["pid"].lower(), got["name"] or "Player"
    players, _ = store.roles()
    await set_role(app, store, targets, pid, name, players.get(pid))


async def set_role(app, store, targets, pid, name, current=None):
    have = (current or {}).get("roles", [])
    servers = (current or {}).get("servers", "all")
    got = await app.push_screen_wait(JobForm(f"ROLES · {name}", [
        ("roles", "Role", sorted(ROLE_CHOICES and [(l, ",".join(v)) for l, v in ROLE_CHOICES],
                                 key=lambda x: x[1] != ",".join(have or ["admin"]))),
        ("servers", "On", sorted([("Every server", "all"), *[(t, t) for t in targets]],
                                 key=lambda x: x[1] != (servers if isinstance(servers, str) else servers[0])))],
        verb="REVIEW", note="\n".join(ROLE_WORDS.values())))
    if not got:
        return
    roles = [r for r in got["roles"].split(",") if r]
    where = "all" if got["servers"] == "all" else [got["servers"]]
    words = "\n".join(ROLE_WORDS[r] for r in roles) or "No roles: they become an ordinary player again."
    if await app.push_screen_wait(Confirm("SAVE ROLES", f"{name} ({pid[:12]}…): {', '.join(roles).upper() or 'none'} "
                                                        f"on {'every server' if where == 'all' else where[0]}.\n\n{words}",
                                          verb="SAVE", danger=False)):
        store.set_roles(pid, name, roles, where)
        app.notify("Saved. ADMIN reaches each server the next time it's empty; VIP protection is on now.",
                   title="Roles", timeout=8)


# --- votes ---------------------------------------------------------------------------------------------------------
async def votes_dialog(app, store, targets):
    _, votes = store.roles()
    opts = [(Text.assemble((t.ljust(10), WHITE), ({True: ("players can vote", GREEN), False: ("voting is off", AMBER)}
                                                 .get(votes.get(t), ("as the server is set", DIM)))), t) for t in targets]
    choice = await app.push_screen_wait(Panel(
        "PLAYER VOTES", "Turn player voting (kick, end round, shuffle, map votes) on or off for each server.\n"
        "The game only has one switch for all vote types. It's applied the next time the server is empty.\n"
        "To block just one kind (say, kick votes), use a rule: WHEN a vote starts (kick) → THEN cancel the vote.",
        opts, [("CLOSE", "close", False), ("ON", "on", True), ("OFF", "off", True), ("LEAVE AS IS", "leave", True)],
        empty="No servers found."))
    if not choice or not choice[1]:
        return
    action, lobby = choice
    store.set_votes(lobby, None if action == "leave" else action == "on")
    app.notify(f"{lobby}: votes {'unchanged' if action == 'leave' else action.upper()}. Applied the next time it's "
               "empty.", title="Votes", timeout=8)


# --- discord -------------------------------------------------------------------------------------------------------
async def discord_dialog(app, store):
    while True:
        hooks = store.webhooks()
        choice = await app.push_screen_wait(Panel(
            "DISCORD", "Rules can post to Discord channels. Each channel needs a webhook: a private link Discord "
            "gives you. ADD walks you through it.",
            [(Text.assemble((n.ljust(16), WHITE), (R.masked(u), DIM)), n) for n, u in sorted(hooks.items())],
            [("CLOSE", "close", False), ("ADD", "add", False), ("SEND TEST", "test", True), ("REMOVE", "remove", True)],
            empty="Not connected yet: choose ADD."))
        if not choice:
            return
        action, name = choice
        if action == "add":
            got = await app.push_screen_wait(JobForm("CONNECT DISCORD", [
                ("name", "A short name for this channel (e.g. alerts)", "alerts" if "alerts" not in hooks else ""),
                ("url", "Webhook URL (paste it here)", "")], verb="SAVE", required=("name", "url"),
                note=R.DISCORD_STEPS))
            if got:
                store.save_webhook(got["name"], got["url"])
                if await app.push_screen_wait(Confirm("SEND A TEST MESSAGE?", f"Post \"oni-rcon is connected.\" to "
                                                      f"{got['name']} now, to check it works?", verb="SEND",
                                                      danger=False)):
                    await send_test(app, store, got["name"], url=got["url"])
        elif action == "test" and name:
            if getattr(store, "fleet_mode", False):
                app.notify("Saved fleet webhooks are kept on the fleet console host only. Test one with a rule's TEST, or remove and "
                           "add it again.", title="Discord", timeout=8)
            else:
                await send_test(app, store, name)
        elif action == "remove" and name:
            if await app.push_screen_wait(Confirm("REMOVE WEBHOOK", f"Rules stop posting to {name}. (To revoke it "
                                                  "completely, also delete the webhook in Discord.)", verb="REMOVE")):
                store.save_webhook(name, None)


async def send_test(app, store, name, url=None):
    from .rules_run import post_discord
    url = url or store.webhooks().get(name)
    try:
        code = await asyncio.to_thread(post_discord, url, "oni-rcon is connected. Rules can post here now.")
        ok = 200 <= code < 300
    except Exception as exc:  # network, a deleted webhook (404), Discord down
        ok, code = False, getattr(exc, "code", type(exc).__name__)
    app.notify("Sent: check the channel." if ok else f"Discord didn't take it ({code}). Check the URL, or make a new "
               "webhook.", title="Discord", severity="information" if ok else "error", timeout=10)
