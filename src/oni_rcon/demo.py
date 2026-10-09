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
        self.status = {"name": name, "address": "203.0.113.7:49176", "map": random.choice(MAPS), "mode": "Team Slayer",
                       "phase": "in_game", "max_players": max_players, "players": 0, "password_required": False,
                       "max_ping": 0, "hidden": False, "anti_cheat": "enforce", "anti_cheat_active": True,
                       "block_vpn": True, "text_chat": True, "voice": True, "vote": None, "votes": True,
                       "next": None, "playlist_vote": True}
        self.players = [self._player(n) for n in random.sample(CALLSIGNS, crowd)]
        self.bans = {"players": [{"id": "9f3a" + "0" * 28, "name": "Griefer", "reason": "team killing", "expires": None,
                                  "by": "Admin"}],
                     "ips": [{"ip": "198.51.100.0/24", "reason": "ban evasion", "expires": time.time() + 86400 * 6}],
                     "devices": []}
        self.allowed = [{"target": "192.0.2.44", "note": "mobile hotspot"}]
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
        """What's in the content folder, as the maps or modes list names it: the fake Forge's .map and .gt files."""
        if not (self.content and self.content.is_dir()):
            return []
        return [{"kind": "map" if suffix == ".map" else "mode", "name": f.stem.replace("_", " ").title(),
                 "reference": f.stem} for f in sorted(self.content.rglob(f"*{suffix}"))]

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

    async def command(self, cmd: str, args: list[str], by: str) -> dict:
        ok = lambda text, data=None: {"ok": True, "text": text, **({"data": data} if data is not None else {})}
        no = lambda text: {"ok": False, "text": text}
        self._renumber()
        if cmd in ("players", "status", "maps", "modes", "bans", "vpn", "vote", "nextmap"):
            return ok(f"{cmd}: see data", {
                "players": lambda: {"count": len(self.players), "max_players": self.status["max_players"],
                                    "players": self.players},
                "status": lambda: self.status,
                "maps": lambda: {"entries": [{"kind": "map", "name": m.replace("_", " ").title(), "reference": m}
                                             for m in MAPS] + self.installed(".map")},
                "modes": lambda: {"entries": [{"kind": "mode", "name": m, "reference": m.lower().replace(" ", "_")}
                                              for m in MODES] + self.installed(".gt")},
                "bans": lambda: self.bans,
                "vpn": lambda: {"allowed": self.allowed, "block_vpn": True, "ranges": 41812},
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
                                                 "by": by, "expires": None})
                await self.emit(cmd, player=p["name"], by=by, reason=" ".join(args[1:]))
                return ok(f"{p['name']} was {cmd}{'n' if cmd == 'ban' else ''}ed by {by}.")
            if cmd in ("mute", "unmute"):
                p["muted"] = cmd == "mute"
                await self.emit(cmd, player=p["name"], by=by)
                return ok(f"{p['name']} was {cmd}d by {by}.")
            if cmd == "team":
                p["team"] = args[1] if len(args) > 1 else p["team"]
                await self.emit("control", text=f"{p['name']} moved to {p['team']}.")
                return ok(f"Moving {p['name']} to {p['team']}.")
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


async def serve_fakes(password: str = "demo", tick: bool = True, specs=SERVERS,
                      content_dirs: list | None = None) -> tuple[list[int], list]:
    """Start the fake servers in the running loop; returns (ports, handles to keep alive)."""
    ports, keep = [], []
    for i, (name, max_players, crowd) in enumerate(specs):
        fake = Fake(name, password, max_players, crowd, str(content_dirs[i]) if content_dirs else "")
        server = await serve(fake.handler, "127.0.0.1", 0)
        ports.append(server.sockets[0].getsockname()[1])
        keep += [fake, server] + ([asyncio.create_task(fake.tick())] if tick else [])
    return ports, keep


def start_in_thread(password: str = "demo", content_dirs: list | None = None) -> list[int]:
    ports, ready = [], threading.Event()

    async def main():
        p, _keep = await serve_fakes(password, content_dirs=content_dirs)
        ports.extend(p)
        ready.set()
        await asyncio.Future()

    threading.Thread(target=asyncio.run, args=(main(),), daemon=True).start()
    ready.wait(10)
    return ports
