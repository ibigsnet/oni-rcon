"""SERVER FEATURES (Ctrl+T, or F12 on a customer console): what Project Reclaimer 0.10 adds, in plain menus.

Two kinds of thing live here. Live ones go straight over RCON and work on any connection: roles, mutes, polls, panels
and whether joins are open. Settings live in dedicated.toml: this builds them from serverconf's catalog, and where the
IBIGS `oni` helper is beside the server it saves them (`oni config`, which has the server check the file first and puts
it back if the server refuses). Anywhere else it shows the lines to paste, so nothing here needs that helper to be useful.

Nothing is offered to a server before 0.10: 0.9.23 refuses a file with any of these keys, and has none of these commands.
"""
from __future__ import annotations

import json
import re

from rich.text import Text

from . import serverconf as C
from .app import Confirm, Pick
from .art import AMBER, CYAN, DIM, WHITE
from .automation_ui import Panel, WidePick, lobby_id
from .jobs_ui import JobForm
from .rules import staff_of
from .safety import clean
from .tenant import Notice, clean_block

GAME_ROWS = (("halo3", "Halo 3", "Every multiplayer map and game type, and the campaign."),
             ("halo3odst", "Halo 3: ODST", "ODST's missions and Firefight, ODST's maps, ODST characters and Forge objects."),
             ("haloreach", "Halo: Reach", "Reach's maps, missions and Firefight, Reach characters and Forge objects."),
             ("halo1", "Halo: CE", "Halo: CE's multiplayer maps."),
             ("halo2", "Halo 2", "Halo 2's multiplayer maps."))
HOW_TO_ADD = ("A server plays only what's in its game folder. To add a game, install it on a PC with MCC and copy it "
              "across with  dedicated init --from <MCC folder> --maps odst,reach,reach-campaign,halo1,halo2  or copy its "
              "folder into the server's game folder, then restart. Players need the game too: anyone without it is told "
              "so at the door.")


# --- the small parts: pure, so they can be tested without a screen ---------------------------------------------
HOLDER = re.compile(r"\((dedicated\.toml|admins|moderators|added by .*)\)$")


def role_rows(data: dict) -> list[dict]:
    """Everyone a `role list` reply names, highest role first: id, name, role, title, fixed (dedicated.toml gives it, so
    RCON can't take it) and from (where it comes from, in words)."""
    rows = []
    for role in (data or {}).get("roles") or []:
        for holder in role.get("holders") or []:
            pid, _, rest = str(holder).partition(" ")
            m = HOLDER.search(rest)
            source = m[1] if m else ""
            rows.append({"id": pid.lower(), "name": clean(rest[:m.start()] if m else rest).strip(), "role": str(role.get("name") or ""),
                         "title": clean(str(role.get("title") or "")), "from": clean(source),
                         "fixed": source in ("dedicated.toml", "admins", "moderators")})
    return rows


def role_words(role: dict) -> str:
    can = role.get("can") or []
    limit = f", bans up to {role['ban_minutes']} min" if "ban.temp" in can and "ban.perm" not in can else ""
    return (f"{clean(str(role.get('title') or role.get('name')))}  rank {role.get('rank')}: "
            f"{', '.join(can) if len(can) < 30 else 'everything'}{limit}")


MUTE_LINE = re.compile(r"(\S+) (.*?)(?:, (\d+) s left)?(?: \((.*?)\))?(?:, by (.*))?$")


def mute_rows(text: str) -> list[dict]:
    """`mutes` answers in lines of words: "<id> <name>, 300 s left (reason), by Bravo"."""
    rows = []
    for line in str(text or "").splitlines():
        m = MUTE_LINE.fullmatch(line.strip())
        if m and len(m[1]) >= 16:
            rows.append({"id": m[1], "name": clean(m[2]), "left": int(m[3]) if m[3] else None, "reason": clean(m[4] or ""),
                         "by": clean(m[5] or "")})
    return rows


def panel_rows(text: str) -> list[dict]:
    """`panel list`: "rules: Server rules (1 lines; buttons: Skip=next)"."""
    rows = []
    for line in str(text or "").splitlines():
        ident, sep, rest = line.partition(":")
        if sep and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", ident.strip()):
            rows.append({"id": ident.strip(), "text": clean(rest.strip())})
    return rows


def poll_args(question: str, answers: list[str], seconds) -> list[str]:
    question, answers = question.strip(), [a.strip() for a in answers if a.strip()]
    if not question:
        raise ValueError("Ask a question.")
    if not 2 <= len(answers) <= 6:
        raise ValueError("A poll has 2 to 6 answers.")
    if not str(seconds).isdigit() or not 10 <= int(seconds) <= 300:
        raise ValueError("A poll stays open 10 to 300 seconds.")
    return [question, *answers, str(int(seconds))]


def panel_args(ident: str, title: str, lines: list[str], button: str, slot: str, timeout: str) -> list[str]:
    ident, title = ident.strip(), title.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", ident):
        raise ValueError("A panel's ID is one short word, like rules.")
    if not title:
        raise ValueError("Give the panel a title.")
    args = [ident, title, *[f"line:{ln.strip()}" for ln in lines if ln.strip()]]
    if button.strip():
        args.append(f"button:{button.strip()}")
    args.append(f"slot:{slot}")
    if timeout.strip():
        args.append(f"timeout:{timeout.strip()}")
    return args


def joins_words(data: dict) -> str:
    gates = data.get("join_gates") or []
    queue = data.get("queue") or []
    return (f"New players: {str(data.get('joins') or '?').upper()}"
            + (f"   Held during: {', '.join(gates)}" if gates else "   Held during: nothing")
            + f"\nRight now they {'get in' if data.get('open_now') else 'are kept out'} ({data.get('phase') or '?'})."
            + f"   Waiting in line: {len(queue)}")


def choice_options(setting: C.Setting, current) -> list[tuple[str, str]]:
    """A choice's menu, what's in effect first. A value the menu has no word for stays reachable as itself."""
    rows = [(f"Server default ({setting.default})" if setting.default else "Server default", C.DEFAULT)]
    rows += [(label, json.dumps(value)) for label, value in setting.options]
    if current is not None and not any(v == json.dumps(current) for _, v in rows):
        rows.append((f"Keep as it is: {clean(json.dumps(current))}", json.dumps(current)))
    want = C.DEFAULT if current is None else json.dumps(current)
    return sorted(rows, key=lambda r: r[1] != want)


def form_fields(section: str, values: dict) -> list:
    """A section's settings as form fields, from the server's block of settings (values: what `oni config` returned)."""
    fields = []
    for s in C.SECTIONS[section][2]:
        current = C.lookup(values, s.key)
        name = s.key.replace(".", "__")
        if s.kind == "choice":
            fields.append((name, s.label, choice_options(s, current)))
        else:
            shown = "" if current is None else ", ".join(current) if isinstance(current, list) else str(current)
            fields.append((name, f"{s.label}  (empty: {s.default})", shown))
    return fields


def changes_from(section: str, got: dict, values: dict) -> dict:
    """What the form changed, as the file holds it: {key: value, or None to take it out}."""
    out = {}
    for s in C.SECTIONS[section][2]:
        value = C.parse(s, got.get(s.key.replace(".", "__")))
        if value != C.lookup(values, s.key):
            out[s.key] = value
    return out


# --- how the screens talk to the server -------------------------------------------------------------------------
async def ask(app, st, command: str, *args) -> dict:
    try:
        return await st.rcon.call(command, *args)
    except Exception as exc:  # the server went away while the menu was open
        return {"ok": False, "text": f"The server didn't answer ({type(exc).__name__})."}


def said(app, reply: dict, title: str, then=None) -> bool:
    """The server's own words about what it did."""
    ok = bool(reply.get("ok"))
    app.notify(clean(str(reply.get("text") or ("Done." if ok else "The server refused."))), title=title,
               severity="information" if ok else "warning", timeout=8 if ok else 12)
    return ok


async def read_settings(app, st) -> tuple[dict, bool, dict]:
    """(this server's settings, whether this console can save them, everything else `oni config` said)."""
    if not app.playlist_tool():
        return {}, False, {}
    rc, out = await app.tool_run("config", lobby_id(st), "--json", timeout=60)
    try:
        info = json.loads(out.strip().splitlines()[-1])
        return (info.get("server") or {}) if rc == 0 else {}, rc == 0, info if rc == 0 else {}
    except (ValueError, IndexError, AttributeError):
        return {}, False, {}


async def save(app, st, changes: dict, title: str, can_save: bool) -> bool:
    """Say what happens, ask, then save through the helper; or, with no helper, show what to paste."""
    if not changes:
        app.notify("Nothing changed.", title=title, timeout=5)
        return False
    warn = C.warnings(changes)
    body = C.snippet(changes)
    if warn:
        body = "\n\n".join(warn) + "\n\n" + body
    if not can_save:
        app.copy_to_clipboard(C.snippet(changes))
        await app.push_screen_wait(Notice(title, "This console can't reach the server's dedicated.toml, so here's what "
                                          "to put in its [[server]] block (copied). A saved file applies by itself; "
                                          f"`dedicated check` tells you if something's wrong.\n\n{body}"))
        return False
    if not await app.push_screen_wait(Confirm(title, body + "\n\nThe server checks the new file before it's used; if it "
                                              "says no, nothing changes.", verb="SAVE", danger=bool(warn))):
        return False
    rc, out = await app.tool_run("config", lobby_id(st), "set", "--json", stdin=json.dumps({"changes": changes}), timeout=300)
    try:
        r = json.loads(out.strip().splitlines()[-1])
        text, ok = clean(str(r.get("text", ""))), r.get("state") in ("saved", "applied")
    except (ValueError, IndexError, AttributeError):
        text, ok = clean_block(out) or "The helper here is older than this console: update it (F9).", False
    if ok:
        app.notify(text, title=f"{title}: saved", timeout=12)
    else:
        await app.push_screen_wait(Notice(f"{title}: NOT CHANGED", text, warn=True))
    return ok


# --- the hub ----------------------------------------------------------------------------------------------------
HUB = [("roles", "ROLES", "Admin, Mod, VIP and your own: who holds what"),
       ("mutes", "MUTES", "Voice, text or both, and shadow mutes"),
       ("polls", "POLLS", "Ask every player a question"),
       ("panels", "PANELS", "A box on every player's screen, with buttons"),
       ("joins", "JOINS", "Open, paused or queued now; the line and VIP line"),
       ("votes", "VOTE RULES", "Who may call each vote, limits, cooldowns"),
       ("announce", "ANNOUNCEMENTS", "Who reads kicks, bans, mutes, votes, joins"),
       ("models", "PLAYER MODELS", "ODST's and Reach's characters"),
       ("forge", "FORGE OBJECTS", "ODST's and Reach's objects, primitives"),
       ("coop", "CO-OP NETWORK", "Lockstep or host"),
       ("games", "OTHER GAMES", "What this server can host"),
       ("more", "MORE", "Kick ladder, metrics, colour, next-game queue"),
       ("advanced", "ADVANCED", "Any other setting, and the file as it stands")]


async def features_hub(app) -> None:
    st = app.cur
    version = st.rcon.info.get("version") if st and st.rcon else None
    if not st or not st.online:
        app.notify("Choose a server that's running: these features talk to it.", title="Server features", timeout=8)
        return
    if not C.supports(version):
        app.notify(f"{st.label} runs {version or 'an unknown version'}. These features need Project Reclaimer 0.10 or "
                   "newer, and older servers refuse their settings.", title="Server features", severity="warning", timeout=12)
        return
    _, _, info = await read_settings(app, st)
    notes = "\n".join(clean(str(n)) for n in info.get("notes") or [])  # the helper's word on memory and the content folder
    while True:
        opts = [(Text.assemble((label.ljust(15), CYAN), (words, WHITE)), key) for key, label, words in HUB]
        choice = await app.push_screen_wait(Panel(f"SERVER FEATURES · {clean(st.label)}", f"Project Reclaimer {clean(str(version))}. "
                                                  "Roles to joins change the server at once; the rest are its settings.",
                                                  opts, [("CLOSE", "close", False), ("OPEN", "open", True)],
                                                  foot=Text(notes, AMBER) if notes else ""))
        if not choice:
            return
        try:
            await OPEN[choice[1]](app, st)
        except ValueError as exc:
            app.notify(clean(str(exc)), title="Server features", severity="warning", timeout=12)


async def setting_screen(app, st, section: str) -> None:
    values, can_save, _ = await read_settings(app, st)
    title, intro, _rows = C.SECTIONS[section]
    note = intro if can_save else intro + "\nThis console can't read the server's file, so these show the defaults."
    got = await app.push_screen_wait(JobForm(f"{title} · {clean(st.label)}", form_fields(section, values), verb="REVIEW", note=note))
    if got is None:
        return
    await save(app, st, changes_from(section, got, values), title, can_save)


def section_screen(section: str):
    async def run(app, st):
        await setting_screen(app, st, section)
    return run


# --- roles ------------------------------------------------------------------------------------------------------
async def roles_screen(app, st) -> None:
    while True:
        reply = await ask(app, st, "role", "list")
        if not reply.get("ok"):
            said(app, reply, "Roles")
            return
        data = reply.get("data") or {}
        st.data["role"] = data
        rows = role_rows(data)
        opts = [(Text.assemble((r["title"].ljust(8), AMBER), (r["name"] or "?", WHITE), ("  " + r["id"][:12] + "…", DIM),
                               ("  " + r["from"] if r["from"] else "", DIM)), r["id"] + "|" + r["role"]) for r in rows]
        groups = data.get("groups") or {}
        foot = ("Everyone may: " + (", ".join(groups.get("everyone") or []) or "nothing") +
                f"\nDevelopers as VIP: {'on' if data.get('devs_as_vip') else 'off'}.")
        choice = await app.push_screen_wait(Panel(
            "ROLES", "A role says what someone may do on this server, in chat too (/kick, /mute…). ADMIN does everything; "
            "MOD kicks, mutes and bans for a limited time; VIP can't be vote-kicked and goes first in the line.",
            opts, [("CLOSE", "close", False), ("ADD", "add", False), ("REMOVE", "remove", True), ("PERMISSIONS", "what", False),
                   ("NEW ROLE", "new", False), ("SETTINGS", "settings", False)],
            foot=foot, empty="Nobody has a role yet: choose ADD."))
        if not choice:
            return
        action, row = choice
        if action == "add":
            await give_role(app, st, data)
        elif action == "remove" and row:
            pid, role = row.split("|", 1)
            entry = next((r for r in rows if r["id"] == pid and r["role"] == role), None)
            if entry and entry["fixed"]:
                await app.push_screen_wait(Notice("WRITTEN IN THE FILE", f"dedicated.toml gives {entry['name'] or pid[:12]} "
                                                  f"this role ({entry['from']}), so it's changed there, not here."))
            elif await app.push_screen_wait(Confirm("REMOVE ROLE", f"{(entry or {}).get('name') or pid[:12]} loses "
                                                    f"{role.upper()} on every server sharing this ban list.", verb="REMOVE")):
                said(app, await ask(app, st, "role", "remove", pid), "Roles")
        elif action == "what":
            await app.push_screen_wait(Notice("WHAT EACH ROLE CAN DO", "\n\n".join(role_words(r) for r in data.get("roles") or [])))
        elif action == "new":
            await new_role(app, st)
        elif action == "settings":
            await setting_screen(app, st, "staff")


async def give_role(app, st, data: dict, preset: tuple | None = None) -> None:
    pid, name = preset or ("", "")
    if not pid:
        online = [(Text.assemble((clean(str(p.get("name") or "?")), WHITE), ("  " + clean(str(p.get("id") or p.get("player_id") or ""))[:12] + "…", DIM)),
                   (str(p.get("id") or p.get("player_id") or ""), clean(str(p.get("name") or "")))) for p in st.players
                  if p.get("id") or p.get("player_id")]
        pick = await app.push_screen_wait(WidePick("WHO? (online now, or paste an ID)", [
            (Text("Paste a player ID instead…", AMBER), ("", "")), *online]))
        if pick is None:
            return
        pid, name = pick
        if not pid:
            got = await app.push_screen_wait(JobForm("PLAYER ID", [("pid", "Player ID", "")], verb="NEXT", required=("pid",),
                                                     note="F1 → choose the player → the dossier shows their ID."))
            if not got:
                return
            pid, name = got["pid"].strip(), ""
    roles = [r for r in data.get("roles") or [] if r.get("name") != "host"]
    role = await app.push_screen_wait(WidePick(f"WHICH ROLE? · {name or pid[:12]}", [
        (Text.assemble((clean(str(r.get("title") or r.get("name"))).ljust(10), AMBER), (str(len(r.get("can") or [])) + " permissions", DIM)), r.get("name"))
        for r in sorted(roles, key=lambda r: -int(r.get("rank") or 0))]))
    if role:
        said(app, await ask(app, st, "role", "add", pid, role), "Roles")
        app.fetch(st, "players", "role")


async def new_role(app, st) -> None:
    values, can_save, _ = await read_settings(app, st)
    got = await app.push_screen_wait(JobForm("A ROLE OF YOUR OWN", [
        ("name", "Short name (e.g. dj)", ""), ("title", "Title beside the name, up to 16 letters", ""),
        ("rank", "Rank: VIP is 10, Mod 20, Admin 30", "12"),
        ("can", "What it may do, by commas (e.g. say, voice.music, or preset:mod, -mute.shadow)", "say")],
        verb="REVIEW", required=("name",),
        note="Permissions: " + ", ".join(C.PERMISSIONS) + "."))
    if got:
        await save(app, st, C.custom_role(got["name"], got["title"], got["rank"], got["can"]), "NEW ROLE", can_save)


async def role_for_player(app, st, pid: str, name: str) -> None:
    """F1's ROLE: one player, straight to the choice."""
    reply = await ask(app, st, "role", "list")
    if not reply.get("ok"):
        said(app, reply, "Roles")
        return
    data = reply.get("data") or {}
    held = staff_of(data).get(pid)
    if held and await app.push_screen_wait(Confirm(f"ROLE · {name}", f"{name} holds {held['role'].upper()}. Take it away?",
                                                   verb="REMOVE")):
        said(app, await ask(app, st, "role", "remove", pid), "Roles")
        app.fetch(st, "players", "role")
        return
    if not held:
        await give_role(app, st, data, (pid, name))


# --- mutes ------------------------------------------------------------------------------------------------------
async def mute_form(app, name: str) -> list[str] | None:
    """The arguments after the player for `mute`, or None. F1's MUTE uses this too."""
    from .app import MUTE_TIMES
    got = await app.push_screen_wait(JobForm(f"MUTE · {name}", [
        ("kind", "What", [("Text and voice", "all"), ("Voice only", "voice"), ("Text only", "text")]),
        ("time", "For how long", MUTE_TIMES),
        ("reason", "Reason", ""),
        ("shadow", "Shadow mute", [("No: they're told", ""), ("Yes: they aren't told, and nobody hears them", "shadow")])],
        verb="MUTE", note="A shadow mute leaves them thinking they're talking: their chat looks sent and their microphone "
                          "works, but the server relays nothing. Staff who may see it read their lines tagged [muted]."))
    if got is None:
        return None
    return [got["kind"], *([got["time"]] if got["time"] else []), *([got["reason"]] if got["reason"] else []),
            *([got["shadow"]] if got["shadow"] else [])]


async def mutes_screen(app, st) -> None:
    while True:
        reply = await ask(app, st, "mutes")
        if not reply.get("ok"):
            said(app, reply, "Mutes")
            return
        rows = mute_rows(reply.get("text"))
        opts = [(Text.assemble((r["name"] or "?", WHITE), ("  " + r["id"][:12] + "…", DIM),
                               (f"  {r['left'] // 60} min left" if r["left"] else "  until a restart", DIM),
                               (f"  {r['reason']}" if r["reason"] else "", DIM)), r["id"]) for r in rows]
        choice = await app.push_screen_wait(Panel("MUTES", "Mutes last until they run out or the server restarts.", opts,
                                                  [("CLOSE", "close", False), ("ADD", "add", False), ("UNMUTE", "unmute", True)],
                                                  empty="Nobody is muted."))
        if not choice:
            return
        action, pid = choice
        if action == "unmute" and pid:
            said(app, await ask(app, st, "unmute", pid), "Mutes")
        elif action == "add":
            online = [(clean(str(p.get("name") or "?")), str(p.get("id") or p.get("player_id") or "")) for p in st.players]
            if not online:
                app.notify("Nobody's on the server: a mute names a player who's here.", title="Mutes", timeout=8)
                continue
            who = await app.push_screen_wait(Pick("MUTE WHO?", online))
            if who:
                args = await mute_form(app, next(n for n, i in online if i == who))
                if args is not None:
                    said(app, await ask(app, st, "mute", who, *args), "Mutes")
                    app.fetch(st, "players")


# --- polls and panels -------------------------------------------------------------------------------------------
async def polls_screen(app, st) -> None:
    reply = await ask(app, st, "poll")
    data = reply.get("data") if isinstance(reply.get("data"), dict) else None
    now = "No poll yet."
    if data:
        answers = [str(a.get("answer") if isinstance(a, dict) else a) for a in data.get("answers") or []]
        counts = data.get("counts") or []
        now = f"{clean(str(data.get('question')))}\n" + "\n".join(
            f"  {clean(a)}: {counts[i] if i < len(counts) else (data.get('answers')[i].get('picks', '?') if isinstance(data.get('answers')[i], dict) else '?')}"
            for i, a in enumerate(answers))
    choice = await app.push_screen_wait(Panel("POLLS", f"Every player picks with F5 to F10 or /pick; the most picks win.\n\n{now}", [],
                                              [("CLOSE", "close", False), ("NEW POLL", "new", False), ("CANCEL POLL", "cancel", False)],
                                              empty="Choose NEW POLL."))
    if not choice:
        return
    if choice[0] == "cancel":
        said(app, await ask(app, st, "poll", "cancel"), "Polls")
    elif choice[0] == "new":
        got = await app.push_screen_wait(JobForm("NEW POLL", [
            ("question", "Question", ""), *[(f"a{i}", f"Answer {i}" + ("" if i < 3 else "  (optional)"), "") for i in range(1, 7)],
            ("seconds", "Open for (10 to 300 seconds)", "30")], verb="ASK", required=("question", "a1", "a2"),
            note='An answer can run a command if it wins: Valhalla | nextmap Valhalla. The server checks it first.'))
        if got:
            args = poll_args(got["question"], [got[f"a{i}"] for i in range(1, 7)], got["seconds"])
            said(app, await ask(app, st, "poll", *args), "Polls")


async def panels_screen(app, st) -> None:
    while True:
        reply = await ask(app, st, "panel", "list")
        rows = panel_rows(reply.get("text")) if reply.get("ok") else []
        opts = [(Text.assemble((r["id"].ljust(12), CYAN), (r["text"], WHITE)), r["id"]) for r in rows]
        choice = await app.push_screen_wait(Panel(
            "PANELS", "A box on every player's screen, under a SERVER heading: for rules, announcements or DJ controls. Up "
            "to 4 at once. Players can close one with ×.", opts,
            [("CLOSE", "close", False), ("SHOW", "show", False), ("HIDE", "hide", True)], empty="No panels showing."))
        if not choice:
            return
        action, ident = choice
        if action == "hide" and ident:
            said(app, await ask(app, st, "panel", "hide", ident), "Panels")
        elif action == "show":
            got = await app.push_screen_wait(JobForm("SHOW A PANEL", [
                ("id", "ID (a short word)", "notice"), ("title", "Title", ""), ("line1", "Line 1", ""), ("line2", "Line 2", ""),
                ("button", "Button (optional): Label, or Label=command", ""),
                ("slot", "Where", [("At the side", "side"), ("At the top", "top"), ("In the centre", "centre")]),
                ("timeout", "Hide after (e.g. 60s, 5m; empty: until you hide it)", "60s")],
                verb="SHOW", required=("id", "title"),
                note="A button's command runs as the player who pressed it, within their role."))
            if got:
                args = panel_args(got["id"], got["title"], [got["line1"], got["line2"]], got["button"], got["slot"], got["timeout"])
                said(app, await ask(app, st, "panel", "show", *args), "Panels")


# --- joins ------------------------------------------------------------------------------------------------------
async def joins_screen(app, st) -> None:
    while True:
        reply = await ask(app, st, "joins")
        if not reply.get("ok"):
            said(app, reply, "Joins")
            return
        data = reply.get("data") or {}
        queue = data.get("queue") or []
        opts = [(Text.assemble((str(q.get("name") or "?") if isinstance(q, dict) else str(q), WHITE)),
                 str(q.get("id") or i + 1) if isinstance(q, dict) else str(i + 1)) for i, q in enumerate(queue)]
        choice = await app.push_screen_wait(Panel(
            "JOINS", joins_words(data) + "\n\nOPEN, PAUSE and QUEUE change it now, until a reload changes the file's. SETTINGS "
            "keeps a choice: the line's size, and holding joins during a game (Infection).", opts,
            [("CLOSE", "close", False), ("OPEN", "open", False), ("PAUSE", "closed", False), ("QUEUE", "queue", False),
             ("REMOVE", "remove", True), ("SETTINGS", "settings", False)], empty="Nobody's waiting."))
        if not choice:
            return
        action, who = choice
        if action in ("open", "closed", "queue"):
            said(app, await ask(app, st, "joins", action), "Joins")
        elif action == "remove" and who:
            said(app, await ask(app, st, "joins", "remove", who), "Joins")
        elif action == "settings":
            await setting_screen(app, st, "joins")


# --- other games and advanced -----------------------------------------------------------------------------------
def games_text(found: dict | None) -> str:
    lines = []
    for folder, title, what in GAME_ROWS:
        have = (found or {}).get(folder)
        mark = "unknown" if found is None else "YES" if have else "no"
        lines += [f"{title}: {mark}", f"  {what}"]
    if found is None:
        lines += ["", "This console can't see the server's game folder. On the server: dedicated content lists what it has."]
    return "\n".join(lines + ["", HOW_TO_ADD])


async def games_screen(app, st) -> None:
    found = None
    if app.playlist_tool():
        rc, out = await app.tool_run("config", lobby_id(st), "games", "--json", timeout=60)
        try:
            found = json.loads(out.strip().splitlines()[-1]) if rc == 0 else None
        except (ValueError, IndexError):
            found = None
    await app.push_screen_wait(Notice("OTHER GAMES: WHAT THIS SERVER CAN HOST", games_text(found if isinstance(found, dict) else None)))


async def advanced_screen(app, st) -> None:
    values, can_save, info = await read_settings(app, st)
    what = await app.push_screen_wait(Pick("ADVANCED", [("Set any setting by its name", "set"), ("See this server's settings", "view")]))
    if what == "view":
        shown = json.dumps(C.safe_view(info or {"server": values}), indent=1) if can_save else \
            "This console can't read the server's file."
        await app.push_screen_wait(Notice("THE SERVER'S SETTINGS (passwords left out)", shown))
    elif what == "set":
        got = await app.push_screen_wait(JobForm("ANY SETTING", [("key", "Setting (e.g. votes.limits.escalate)", ""),
                                                                 ("value", "Value (true, 30, [\"a\", \"b\"], or words; empty takes it out)", "")],
                                                 verb="REVIEW", required=("key",),
                                                 note="The names are in the server guide. It goes in this server's own block, and the "
                                                      "server checks it before it's used. Passwords, ports and what it plays aren't "
                                                      "set here."))
        if got:
            key, value = C.advanced(got["key"], got["value"])
            await save(app, st, {key: value}, "ANY SETTING", can_save)


OPEN = {"roles": roles_screen, "mutes": mutes_screen, "polls": polls_screen, "panels": panels_screen, "joins": joins_screen,
        "games": games_screen, "advanced": advanced_screen, **{k: section_screen(k) for k in ("votes", "announce", "models", "coop", "forge", "more")}}
