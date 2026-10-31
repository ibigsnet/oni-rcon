"""Simulated Reclaimer servers for --demo and the tests: the real WebSocket protocol, fake players and events.

Player and event field names are this client's best reading of the server docs; a real server may differ, which
the UI tolerates (unknown fields still show in the dossier's raw view).
"""
from __future__ import annotations

import asyncio
import json
import random
import threading
import time
from pathlib import Path

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

CALLSIGNS = ["Bravo", "Echo 4", "Viper", "Nomad", "Ghost", "Rook", "Sable", "Kestrel", "Juno", "Tango", "Onyx",
             "Hollow", "Vesper", "Mako", "Quill", "Atlas", "Cinder", "Lark", "Drift", "Static"]
MAPS = ["guardian", "the_pit", "narrows", "construct", "valhalla", "sandtrap", "high_ground", "last_resort",
        "isolation", "epitaph", "foundry", "standoff", "snowbound", "heretic"]
MODES = ["Slayer", "Team Slayer", "Capture the Flag", "Oddball", "King of the Hill", "Territories", "Assault", "SWAT"]
# shaped like a real server's lists: base maps by engine name, then variants from Halo 3, MCC and the content folder
DEMO_MAP_VARIANTS = [("Guardian", "default_guardian_012.mvar"), ("MLG Guardian", "MLG Guardian"),
                     ("SWAT Guardian", "SWAT Guardian"), ("Pit Stop", "Maps/Forge/rf-3c1d0a9e44b2f7d81e02.json"),
                     ("Club Dead", "Maps/Club Dead.mvar")]
DEMO_MODE_VARIANTS = [("Team Slayer", "2v2_team_slayer_010.bin"), ("MLG Team Slayer", "mlg_cstruct_ts8_010.bin"),
                      ("Fiesta Small", "FIESTA SMALL"), ("UFM | Team KOTH", "Game Modes/Forge/UFM Encompass/UFM _Team_KOTH.json")]
DEMO_MODS = [("Hang 'Em High", "hang_em_high")]
TEAMS = ["red", "blue"]
WEAPONS = ["battle rifle", "sniper rifle", "shotgun", "energy sword", "frag grenade", "rocket launcher", "melee"]
CHAT = ["gg", "nice shot", "who has sniper?", "lag on blue base", "rematch?", "ez", "push top mid",
        "anyone up for MLG after?", "wp all"]
CALLS = ["admin someone is spawn camping", "is there a mod on? red is hacking"]  # raise the console's alert
SERVERS = [("Demo Ops | Slayer", 16, 9), ("Demo Ops | Big Team Battle", 32, 17), ("Demo Ops | MLG 4v4", 8, 6)]


class Fake:
    def __init__(self, name: str, password: str, max_players: int, crowd: int, content_dir: str = ""):
        self.password, self.clients, self.engine, self.skill = password, set(), 0, {}
        self.content = Path(content_dir) if content_dir else None  # Forge installs land here, and get listed
        self.even_teams = False  # the game type's team balancing: a move may not make a team bigger than the other
        self.status = {"name": name, "address": "203.0.113.7:49176", "map": random.choice(MAPS), "mode": "Team Slayer",
                       "phase": "in_game", "max_players": max_players, "players": 0, "password_required": False,
                       "max_ping": 0, "hidden": False, "anti_cheat": "enforce", "anti_cheat_active": True,
                       "block_vpn": True, "text_chat": True, "voice": True, "vote": None, "votes": True,
                       "next": None, "playlist_vote": True}
        self.players = [self._player(n) for n in random.sample(CALLSIGNS, crowd)]
        self.bans = {"players": [{"id": "9f3a" + "0" * 28, "name": "Griefer", "reason": "team killing", "expires": None,
                                  "banned_by": "Admin"}],
                     "ips": [{"ip": "198.51.100.0/24", "reason": "ban evasion", "expires": time.time() + 86400 * 6}],
                     "devices": []}
        self.allowed = [{"target": "192.0.2.44", "note": "mobile hotspot"}]
        self.kicked = {}  # player ID -> name, until a pardon
        self.vote, self.vote_at = None, 0.0
        self.hot = (None, 0.0)  # the last killer and when: some go on a roll, for multi-kills

    def _player(self, name: str) -> dict:
        self.engine += 1
        self.skill[self.engine] = random.lognormvariate(0, 0.7)  # a few players carry the lobby, for sprees
        k, d = random.randint(0, 25), random.randint(0, 18)
        return {"number": 0, "name": name, "player_id": "%032x" % random.getrandbits(128),
                "address": f"203.0.113.{random.randint(2, 250)}", "admin": name == "Bravo", "muted": False,
                "team": random.choice(TEAMS), "score": k, "kills": k, "deaths": d,
                "service_tag": name[:4].upper(), "alive": random.random() > 0.2, "health": round(random.random(), 2),
                "shields": round(random.random(), 2), "seconds_since_last_death": random.randint(3, 300),
                "engine_id": self.engine, "guest_players": []}

    def installed(self, suffix: str) -> list[dict]:
        """What's in the content folder, as the maps or modes list names it: the fake Forge's map and game type JSON."""
        if not (self.content and self.content.is_dir()):
            return []
        out = []
        for f in sorted(self.content.rglob("*.json")):
            try:
                doc = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            is_mode = isinstance(doc, dict) and "packed_game" in doc
            is_map = isinstance(doc, dict) and isinstance(doc.get("map"), dict) and "data" in doc["map"]
            if (suffix == ".map" and is_map) or (suffix == ".gt" and is_mode):
                out.append({"kind": "map" if is_map else "mode", "name": f.stem.replace("_", " ").title(),
                            "reference": f.stem})
        return out

    def _renumber(self):
        for i, p in enumerate(self.players, 1):
            p["number"] = i
        self.status["players"] = len(self.players)

    def _find(self, who: str) -> dict | None:
        self._renumber()
        return next((p for p in self.players
                     if who in (f"#{p['number']}", p["player_id"]) or p["name"].lower() == who.lower()), None)

    async def emit(self, event: str, **fields):
        msg = json.dumps({"type": "event", "event": event, "time": int(time.time()), **fields})
        for ws in list(self.clients):
            try:
                await ws.send(msg)
            except Exception:
                self.clients.discard(ws)

    async def handler(self, ws):
        try:
            msg = json.loads(await ws.recv())
            if msg.get("type") != "auth" or msg.get("password") != self.password:
                await ws.send(json.dumps({"type": "auth", "ok": False, "error": "Wrong password."}))
                return
            await ws.send(json.dumps({"type": "auth", "ok": True, "protocol": 1, "server": self.status["name"],
                                      "version": "0.9.7-demo"}))
            self.clients.add(ws)
            async for raw in ws:
                msg = json.loads(raw)
                parts = str(msg.get("command", "")).split() or [""]
                args = [str(a) for a in msg.get("args") or []] or parts[1:]
                reply = await self.command(parts[0], args, msg.get("by") or "an admin")
                await ws.send(json.dumps({"type": "reply", "id": msg.get("id"), **reply}))
        except ConnectionClosed:  # the console quit mid-conversation: normal, not worth a traceback
            pass
        finally:
            self.clients.discard(ws)

    @staticmethod
    def vpn_lists() -> dict:
        """The 0.9.22 `vpn` lists: one fresh, one a week and a half old that failed its last fetch."""
        t = int(time.time())
        return {"refreshed_at": t - 600, "sources": [
            {"list": "x4bnet", "ranges": 37958, "asns": 0, "fetched_at": t - 600, "problem": None},
            {"list": "hosting-asns", "ranges": 3854, "asns": 212, "fetched_at": t - 10 * 86400,
             "problem": "HTTP 404 from the list's address; kept the last copy"}],
            "table": {"table": "ip2asn", "rows": 512340, "fetched_at": t - 2 * 86400, "asns": 212, "announced": 3854,
                      "blocks": 3854}}

    async def command(self, cmd: str, args: list[str], by: str) -> dict:
        ok = lambda text, data=None: {"ok": True, "text": text, **({"data": data} if data is not None else {})}
        no = lambda text: {"ok": False, "text": text}
        self._renumber()
        if cmd == "vpn" and args:  # CHECK IP, the 0.9.22 shape: the list an address matched
            listed = args[0].startswith("45.")
            return ok(f"{args[0]} is refused: it is on a VPN list." if listed else f"{args[0]} doesn't look like a VPN.",
                      {"address": args[0], "local": False, "listed": listed, "list": "x4bnet" if listed else None,
                       "allowed": False, "refused": listed, "fail_closed": False})
        if cmd in ("admin", "admins") and args[:1] in ([], ["list"]):  # a bare list of IDs and objects: the shape is
            return ok("admins: see data", [  # undocumented, so the demo answers in both forms the clients take
                {"name": "Overseer", "player_id": "0d57c0ffee0d57c0ffee0d57c0ffee00", "source": "dedicated.toml",
                 "online": False},
                *({"name": p["name"], "player_id": p["player_id"], "source": "rcon", "online": True}
                  for p in self.players if p.get("admin"))])
        if cmd in ("players", "status", "maps", "modes", "bans", "vpn", "vote", "nextmap"):
            return ok(f"{cmd}: see data", {
                "players": lambda: {"count": len(self.players), "max_players": self.status["max_players"],
                                    "players": self.players},
                "status": lambda: self.status,
                "maps": lambda: {"entries": [{"kind": "map", "name": m.replace("_", " ").title(), "reference": m}
                                             for m in MAPS]
                                 + [{"kind": "map", "name": n, "reference": r} for n, r in DEMO_MODS]
                                 + [{"kind": "variant", "name": n, "reference": r} for n, r in DEMO_MAP_VARIANTS]
                                 + self.installed(".map")},
                "modes": lambda: {"entries": [{"kind": "mode", "name": m, "reference": m.lower().replace(" ", "_")}
                                              for m in MODES]
                                  + [{"kind": "variant", "name": n, "reference": r} for n, r in DEMO_MODE_VARIANTS]
                                  + self.installed(".gt")},
                "bans": lambda: self.bans,
                "vpn": lambda: {"allowed": self.allowed, "block_vpn": True, "ranges": 41812, "fail_closed": False,
                                "lists": self.vpn_lists()},
                "vote": lambda: {"vote": self.vote, "playlist": None,
                                 "subjects": ["kick", "endround", "endgame", "shuffle", "playlist"]},
                "nextmap": lambda: {"next": self.status["next"],
                                    "rotation": [{"map": m, "mode": random.choice(MODES)} for m in MAPS[:6]]},
            }[cmd]())
        if cmd == "say":
            await self.emit("chat", channel="server", text=" ".join(args))
            return ok("Said.")
        if cmd in ("tell", "kick", "ban", "mute", "unmute", "team", "vpnallow"):
            p = self._find(args[0]) if args else None
            if not p:
                if cmd in ("ban", "vpnallow") and args:  # offline target: an ID, address or range
                    (self.bans["ips" if "." in args[0] else "players"] if cmd == "ban" else self.allowed).append(
                        {"ip" if "." in args[0] else "id": args[0], "reason": " ".join(args[1:])})
                    return ok(f"{cmd} {args[0]} done.")
                return no(f"No player is named '{args[0] if args else ''}'; players lists them.")
            if cmd == "tell":
                return ok(f"Told {p['name']}.")
            if cmd in ("kick", "ban"):
                self.players.remove(p)
                if cmd == "ban":
                    self.bans["players"].append({"id": p["player_id"], "name": p["name"], "reason": " ".join(args[1:]),
                                                 "banned_by": by, "expires": None})
                if cmd == "kick":
                    self.kicked[p["player_id"]] = p["name"]
                    await self.emit("kick", name=p["name"], id=p["player_id"], by=by, reason=" ".join(args[1:]), seconds=600)
                else:
                    await self.emit(cmd, player=p["name"], by=by, reason=" ".join(args[1:]))
                return ok(f"{p['name']} was {cmd}{'n' if cmd == 'ban' else ''}ed by {by}.")
            if cmd in ("mute", "unmute"):
                p["muted"] = cmd == "mute"
                await self.emit(cmd, player=p["name"], by=by)
                return ok(f"{p['name']} was {cmd}d by {by}.")
            if cmd == "team":  # as the real server: the reply says it's asked; the game's verdict follows as an event
                want = args[1].lower() if len(args) > 1 else p["team"]
                same = sum(x["team"] == want for x in self.players) >= sum(x["team"] == p["team"] for x in self.players)
                if want == p["team"]:
                    why = f"Team change refused: Already on {want.title()} team."
                elif self.even_teams and same:
                    why = f"Team change refused: Teams must stay even: {want.title()} team has as many players as yours."
                else:
                    why = ""
                if not why:
                    p["team"] = want
                asyncio.get_running_loop().call_later(0.3, lambda: asyncio.ensure_future(self.emit(
                    "control", action="team", ok=not why, text=why or f"{p['name']} moved to {want.title()} team.")))
                return ok(f"Moving {p['name']} to {want.title()} team.")
            self.allowed.append({"target": p["player_id"], "note": " ".join(args[1:])})
            return ok(f"{p['name']} may join through a VPN.")
        if cmd in ("unban", "vpnrevoke"):
            lists = [self.allowed] if cmd == "vpnrevoke" else self.bans.values()
            for lst in lists:
                for e in list(lst):
                    if args and args[0] in (e.get("id"), e.get("ip"), e.get("name"), e.get("target")):
                        lst.remove(e)
                        return ok(f"{cmd} {args[0]} done.")
            return no(f"Nothing matches '{args[0] if args else ''}'.")
        if cmd == "pardon":
            pid = next((i for i, n in self.kicked.items() if args and args[0] in (i, n)), None)
            if pid is None:
                return no(f"Nobody kicked here matches '{args[0] if args else ''}'.")
            name = self.kicked.pop(pid)
            await self.emit("pardon", target=args[0], name=[name], by=by)
            return ok(f"{name} may rejoin now.")
        if cmd == "admin" and len(args) > 1 and args[0] in ("add", "remove"):
            p = next((p for p in self.players if args[1] in (p["name"], p["player_id"])), None)
            if p:
                p["admin"] = args[0] == "add"
            name = p["name"] if p else args[1]
            await self.emit(f"admin_{args[0]}{'ed' if args[0] == 'add' else 'd'}", name=name,
                            id=p["player_id"] if p else args[1], by=by)
            return ok(f"{name} {'is now an admin' if args[0] == 'add' else 'is no longer an admin'}.")
        if cmd == "reload":
            await self.emit("control", action="reload", ok=True, text="Nothing changed.")
            return ok("Nothing changed.")
        if cmd in ("map", "mode", "load", "nextmap"):
            self.status["next"] = " / ".join(args)
            if cmd != "nextmap":
                await self.emit("control", text=f"Loading {' / '.join(args)}.")
                if cmd in ("map", "load") and args:
                    self.status["map"] = args[0]
                if cmd == "mode" and args or cmd == "load" and len(args) > 1:
                    self.status["mode"] = args[-1]
            return ok(f"Accepted: {cmd} {' '.join(args)}.")
        if cmd in ("endround", "endgame", "shuffle", "teamcount", "passvote", "cancelvote", "startvote"):
            if cmd == "startvote":
                self.vote = {"subject": args[0] if args else "?", "yes": 0, "no": 0,
                             **({"target": args[1]} if len(args) > 1 else {})}
                self.vote_at = time.monotonic()
            elif cmd in ("passvote", "cancelvote"):
                self.vote = None
            await self.emit("vote" if "vote" in cmd else "control", text=f"{cmd} {' '.join(args)} by {by}".strip())
            return ok(f"{cmd} accepted.")
        if cmd == "servername":
            self.status["name"] = args[0] if args else self.status["name"]
            return ok(self.status["name"], {"name": self.status["name"]})
        if cmd == "password":
            self.status["password_required"] = bool(args and args[0])
            return ok("Password set." if args and args[0] else "No password.",
                      {"password_required": self.status["password_required"]})
        if cmd == "maxping":
            self.status["max_ping"] = 0 if not args or args[0] == "off" else int(args[0])
            return ok(f"Max ping {self.status['max_ping'] or 'off'}.", {"max_ping": self.status["max_ping"]})
        if cmd == "help":
            return ok("Demo server: status players say tell kick ban unban mute unmute team maps modes map mode load ...")
        return no(f"Unknown command '{cmd}'; help lists them.")

    async def tick(self):
        """Keeps the feed alive: firefights, chat, votes, and the odd join or leave."""
        while True:
            await asyncio.sleep(random.uniform(0.8, 3.0))
            alive = [p for p in self.players if p["alive"]]
            roll = random.random()
            if roll < 0.62 and len(alive) > 1:
                k = self._killer(alive)
                v = random.choice([p for p in alive if p["team"] != k["team"]] or [p for p in alive if p is not k])
                k["kills"] += 1; k["score"] += 1; v["deaths"] += 1
                k["shields"] = round(random.uniform(0, .6), 2)  # it was a fight
                v.update(alive=False, health=0.0, shields=0.0, seconds_since_last_death=0)
                self.hot = (k, time.monotonic())
                await self.emit("kill", killer=k["engine_id"], victim=v["engine_id"], weapon=random.choice(WEAPONS))
            elif roll < 0.88 and alive:
                p = random.choice(alive)
                await self.emit("chat", channel=random.choice(["all", f"team {p['team']}"]), name=p["name"],
                                team=p["team"], text=random.choice(CALLS if random.random() < .015 else CHAT))
            elif roll < 0.94 and len(self.players) < self.status["max_players"]:
                spare = [n for n in CALLSIGNS if n not in {p["name"] for p in self.players}]
                if spare:
                    p = self._player(random.choice(spare))
                    self.players.append(p)
                    await self.emit("join", name=p["name"], player_id=p["player_id"], address=p["address"])
            elif roll < 0.997 and len(self.players) > 2:
                p = random.choice(self.players)
                self.players.remove(p)
                await self.emit("leave", name=p["name"], reason="quit")
            else:
                await self.emit("cheat", name=random.choice(alive)["name"] if alive else "?",
                                text="speed out of range (sample)")
            await self._vote()
            alive = [p for p in alive if p["alive"]]
            for p in random.sample(alive, min(len(alive), random.randint(0, 3))):  # crossfire
                hit = random.uniform(.2, .9)
                p["health"] = round(max(.05, p["health"] - max(0.0, hit - p["shields"])), 2)
                p["shields"] = round(max(0.0, p["shields"] - hit), 2)
            for p in self.players:
                if not p["alive"]:
                    if random.random() < 0.5:
                        p.update(alive=True, health=1.0, shields=1.0)
                else:  # shields recharge quickly, health slowly
                    p.update(shields=round(min(1.0, p["shields"] + .25), 2), health=round(min(1.0, p["health"] + .08), 2))
                p["seconds_since_last_death"] += 2
            self._renumber()

    def _killer(self, alive: list) -> dict:
        k, at = self.hot
        if any(p is k for p in alive) and time.monotonic() - at < 3.5 and random.random() < 0.35:
            return k
        return random.choices(alive, [self.skill[p["engine_id"]] for p in alive])[0]

    async def _vote(self):
        """Votes fill up and close; now and then a player calls one."""
        if self.vote:
            self.vote["yes"] += random.choice([0, 0, 1, 1, 2])
            self.vote["no"] += random.choice([0, 0, 0, 1])
            need = len(self.players) // 2 + 1
            if self.vote["yes"] >= need or time.monotonic() - self.vote_at > 30:
                result = "passed" if self.vote["yes"] >= need else "failed"
                await self.emit("vote", text=f"vote to {self.vote['subject']} {result} "
                                             f"({self.vote['yes']} to {self.vote['no']})")
                self.vote = None
        elif random.random() < 0.012 and len(self.players) > 2:
            caller, target = random.sample(self.players, 2)
            subject = random.choice(["shuffle", "endround", "kick"])
            self.vote = {"subject": subject, "yes": 1, "no": 0, **({"target": target["name"]} if subject == "kick" else {})}
            self.vote_at = time.monotonic()
            await self.emit("vote", name=caller["name"], text=f"called a vote to {subject}"
                                                              + (f" {target['name']}" if subject == "kick" else ""))


def iso(t: float) -> str:
    """A time as docker --timestamps writes it."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t % 1 * 1e9):09d}Z"


class FakeHost:
    """A pretend game box for --demo and the tests: the report a health_cmd would print for each fake server (the
    host's memory, the container, the server's log), and a timeline that makes the HEALTH tab worth looking at: a
    SIGNATURE crash on the first server, a BARE one on the second, then the host running short of memory. A crash
    really drops the server's RCON connections, and its players come back after the 5 s the real one takes."""

    def __init__(self, signature_at: float = 7.0, bare_at: float = 13.0, squeeze_at: float = 19.0, rejoin: float = 5.0,
                 autorun: bool = True):
        self.at, self.rejoin, self.autorun = (signature_at, bare_at, squeeze_at), rejoin, autorun
        self.fakes: dict[int, Fake] = {}
        self.logs: dict[int, list[str]] = {}
        self.started: dict[int, float] = {}
        self.avail, self.swap_used, self.swap_total, self.oom = 3400, 120, 2048, 0
        self.load_pct = 82
        self.crashed: list[tuple[str, int]] = []  # (class, port), as they happened

    def attach(self, ports: list[int], fakes: list[Fake]) -> None:
        now = time.time()
        for port, fake, age in zip(ports, fakes, (5 * 3600, 3 * 3600 + 1200, 11 * 3600)):
            self.fakes[port], self.logs[port], self.started[port] = fake, [], now - age

    async def run(self) -> None:
        """The timeline. The first two ports are the ones that crash; the third just carries on."""
        ports = list(self.fakes)
        t0 = time.monotonic()
        for when, what in zip(self.at, ("signature", "bare", "squeeze")):
            await asyncio.sleep(max(0.0, when - (time.monotonic() - t0)))
            if what == "squeeze":
                self.avail, self.swap_used = 640, 1300
            else:
                await self.crash(ports[0 if what == "signature" else 1], what == "signature")

    async def crash(self, port: int, signature: bool) -> None:
        """What 0.9.11 under Wine does when a player with the blue developer helmet joins: the game process dies and
        restarts, the container doesn't, and every player is dropped."""
        fake, t = self.fakes[port], int(time.time())
        name, clock = fake.status["name"], time.strftime("%H:%M:%S", time.gmtime(t))
        if signature:  # docker's own timestamps on every line
            lines = [f"{iso(t)} Experimental startup exception 0xC0000005 in halo3.dll at RVA 0x14C73E",
                     f"{iso(t)} Error: Engine probe worker failed: exit code: 1",
                     f"{iso(t)} {clock} [{name}] stopped (exit code: 1); restarting in 5 s."]
        else:  # the other fault, with no exception line, and a log with no timestamps but the one on the stop line
            lines = ["Error: Engine probe worker failed: exit code: 1",
                     f"{clock} [{name}] stopped (exit code: 1); restarting in 5 s."]
        self.logs[port] += lines
        self.crashed.append(("SIGNATURE" if signature else "BARE", port))
        crowd = len(fake.players)
        for ws in list(fake.clients):
            await ws.close()
        fake.players = []
        fake._renumber()
        await asyncio.sleep(self.rejoin)
        fake.players = [fake._player(n) for n in random.sample(CALLSIGNS, crowd)]
        fake._renumber()

    async def report(self, port: int, since: str = "") -> str:
        n = int(time.time())
        fake = self.fakes[port]
        lines = [f"host now={n} clock={time.strftime('%H:%M:%S', time.gmtime(n))} mem_available_mb={self.avail} "
                 f"swap_used_mb={self.swap_used} swap_total_mb={self.swap_total} oom_kill={self.oom} perf=1 "
                 f"load_pct={self.load_pct} cpus=4",
                 f"container started={iso(self.started[port])} finished=0001-01-01T00:00:00Z oom_killed=false "
                 f"exit_code=0 restarts=0",
                 # a join, as the server logs it: the console must read around the ID and the address
                 f"{iso(n - 3600)} [{fake.status['name']}] Bob connected from 203.0.113.9 "
                 f"(player ID {'ab12' * 16}, ping 77 ms)."]
        # the game thread's and wineserver's scheduler counters, as `oni health-report` reads them from /proc: the first
        # server's game thread runs flat out (one core maxed), the others have room
        busy = 0.95 if port == next(iter(self.fakes)) else 0.3
        t = time.time()
        lines.append(json.dumps({"kind": "threads", "at": t,
                                 "main": [port, int(t * busy * 1e9), int(t * 0.001 * 1e9), int(t * 2000), int(t * 5)],
                                 "wine": [port + 1, int(t * busy * 0.6 * 1e9), 0, int(t * 30000), int(t * 3)]}))
        if port == next(iter(self.fakes)):  # the busiest box can't keep up: a lag report for each of the last 5 min
            for ago, (slow, avg, frame) in zip(range(270, 0, -60), ((31, 47, 61), (52, 41, 88), (59, 36, 104),
                                                                    (59, 33, 131), (58, 38, 97))):
                lines.append(f"{iso(n - ago)} [{fake.status['name']}] Server falling behind: under 54 frames a second "
                             f"for {slow} of the last 59 seconds ({avg} on average, longest frame {frame} ms) with "
                             f"{len(fake.players)} players. Each player gets one update a frame, so they see lag: give "
                             f"this server more processor time or fewer players.")
        return "\n".join(lines + self.logs[port])

    async def service(self, unit: str) -> str:
        """What systemctl and journalctl say about the workaround service."""
        t = time.time()
        stamp = lambda ago: time.strftime("%Y-%m-%dT%H:%M:%S+0000", time.gmtime(t - ago))
        return (f"active\n{stamp(7200)} demo-box {unit}[412]: moved tag halo-latest to 0.9.11\n"
                f"{stamp(1800)} demo-box {unit}[412]: running 0.9.12-rc1, NOT the pinned build 0.9.11")


async def serve_fakes(password: str = "demo", tick: bool = True, specs=SERVERS, content_dirs: list | None = None,
                      host: FakeHost | None = None) -> tuple[list[int], list]:
    """Start the fake servers in the running loop; returns (ports, handles to keep alive). A `host` gets them
    attached, and its timeline started (unless it says it'll be started by hand)."""
    ports, keep, fakes = [], [], []
    for i, (name, max_players, crowd) in enumerate(specs):
        fake = Fake(name, password, max_players, crowd, str(content_dirs[i]) if content_dirs else "")
        server = await serve(fake.handler, "127.0.0.1", 0)
        ports.append(server.sockets[0].getsockname()[1])
        fakes.append(fake)
        keep += [fake, server] + ([asyncio.create_task(fake.tick())] if tick else [])
    if host:
        host.attach(ports, fakes)
        if host.autorun:  # else whoever holds it starts the timeline, with run()
            keep.append(asyncio.create_task(host.run()))
    return ports, keep


def start_in_thread(password: str = "demo", content_dirs: list | None = None, host: FakeHost | None = None) -> list[int]:
    ports, ready = [], threading.Event()

    async def main():
        p, _keep = await serve_fakes(password, content_dirs=content_dirs, host=host)
        ports.extend(p)
        ready.set()
        await asyncio.Future()

    threading.Thread(target=asyncio.run, args=(main(),), daemon=True).start()
    ready.wait(10)
    return ports


class DemoPlugin:
    """A pretend Gryphon for --demo's F10, answered in this process: the same requests and replies a real plug-in
    sends over stdin and stdout, so the tab works with nothing installed."""
    WORDS = ("nazi", "slur")

    def __init__(self):
        self.live, self.log = False, ["daemon started", "word list loaded: 412 words"]
        now = time.time()
        self.rows = [{"id": "%032x" % random.getrandbits(128), "at": now - m * 60, "server": s, "what": w, "who": n, "why": y}
                     for m, s, w, n, y in [(12, 0, "mute 10m", "Rook", "chat [hate] 'n***'"),
                                           (47, 1, "kick", "Static", "name [slur]"),
                                           (95, 0, "ban 1d", "Cinder", "chat [hate], third strike")]]

    @classmethod
    def setup(cls):
        from .plugins import Manifest, PluginSetup
        m = Manifest("gryphon-demo", name="Gryphon (demo)", version="0.1.0", author="Arche Labs",
                     description="Automated moderation, pretend.", exec="(answered inside oni-rcon for --demo)",
                     rcon=["unmute", "unban", "pardon"])
        return PluginSetup([m], source=cls())

    def view(self) -> dict:
        return {"title": "Gryphon", "badge": {"level": "warn" if self.live else "ok", "text": "LIVE" if self.live else "DRY-RUN"},
                "blocks": [
                    {"type": "kv", "rows": [["Mode", "live" if self.live else "dry-run"], ["Servers online", "3 / 3"],
                                            ["Last action", f"{int((time.time() - self.rows[0]['at']) // 60)} min ago"
                                             if self.rows else "none"]]},
                    {"type": "table", "id": "actions", "columns": ["Time", "Server", "Action", "Player", "Why"],
                     "rows": [{"id": r["id"], "cells": [time.strftime("%H:%M", time.localtime(r["at"])),
                                                        str(r["server"] + 1), r["what"], r["who"], r["why"]]}
                              for r in self.rows], "row_actions": ["undo"]},
                    {"type": "log", "lines": self.log[-8:]},
                    {"type": "text", "text": "A pretend plug-in: nothing here runs as a program. See docs/PLUGINS.md "
                                             "for how a real one is written."}],
                "actions": [
                    {"id": "check", "label": "Check a message", "params": [{"name": "text", "label": "Message", "type": "text"}]},
                    {"id": "go-dry", "label": "Go dry-run"} if self.live else
                    {"id": "go-live", "label": "Go live", "danger": True, "confirm": "Gryphon will really kick, mute and ban."}]}

    async def call(self, m, request: dict, timeout: float) -> dict:
        await asyncio.sleep(0.2)  # a real one takes a moment
        if request.get("type") == "view":
            return self.view()
        act, params = request.get("id"), request.get("params") or {}
        if act == "check":
            text = str(params.get("text", ""))
            hit = next((w for w in self.WORDS if w in text.lower()), None)
            return {"ok": True, "toast": f"Would act: hate [{hit}]" if hit else "Nothing in the list matches that."}
        if act in ("go-live", "go-dry"):
            self.live = act == "go-live"
            self.log.append(f"mode set to {'live' if self.live else 'dry-run'}")
            return {"ok": True, "toast": "Gryphon is live." if self.live else "Gryphon is in dry-run.", "view": self.view()}
        if act == "undo" and (row := next((r for r in self.rows if r["id"] == request.get("row")), None)):
            self.rows.remove(row)
            self.log.append(f"undid {row['what']} on {row['who']}")
            cmd = "unmute" if row["what"].startswith("mute") else "unban" if row["what"].startswith("ban") else "pardon"
            ask = [{"server": request["server"], "command": cmd, "args": [row["id"]]}] if request.get("server") else []
            return {"ok": True, "toast": f"Undid {row['who']}'s {row['what'].split()[0]}.", "rcon": ask}
        return {"ok": False, "error": "pick a row first" if act == "undo" else f"no action {act!r}"}
