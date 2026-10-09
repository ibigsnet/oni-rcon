"""A pretend ReclaimerForge for --demo and the tests: the API exactly as forge.py reads it, seeded listings, the
rate-limit headers, and switches that force each error. It never talks to the real site, and its key is no real key.

Its files are its own invention (.map, .gt and .playlist holding filler bytes): the fake RCON servers in demo.py list
what lands in their content folder, so an install can be loaded in the demo.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from .forge import SCOPES, SORTS, WINDOWED, WINDOWS, parse_iso, utc_iso

DEMO_KEY = "rfk_demo_notarealkey00"
AUTHORS = [("usr_7a1c03", "Kestrel"), ("usr_2be944", "lowgrav"), ("usr_91d0aa", "Onyx Works"), ("usr_c44e17", "Juno"),
           ("usr_5f0262", "Halcyon"), ("usr_e810b9", "Mako")]
MAPS = [("Guardian Rebuilt", "guardian"), ("Pit Stop", "the_pit"), ("Narrows Siege", "narrows"),
        ("Valhalla Convoy", "valhalla"), ("Sandtrap Outpost", "sandtrap"), ("Foundry Arena", "foundry"),
        ("Epitaph Echo", "epitaph"), ("Construct Inferno", "construct"), ("Last Resort Breach", "last_resort"),
        ("Snowbound Night", "snowbound"), ("High Ground Assault", "high_ground"), ("Standoff Rally", "standoff"),
        ("Isolation Ward", "isolation"), ("Heretic Mini", "heretic")]
GAMETYPES = [("Grifball", "Assault"), ("Infection Classic", "Infection"), ("SWAT Magnums", "Slayer"),
             ("Juggernaut Plus", "Juggernaut"), ("Oddball Rush", "Oddball"), ("Fiesta Slayer", "Slayer"),
             ("Duel Arena", "Slayer"), ("Team Snipers", "Slayer")]
PLAYLISTS = ["Community Picks", "Big Team Remix", "Party Night"]
EXT = {"map": ".map", "gametype": ".gt", "playlist": ".playlist"}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


class FakeForge:
    def __init__(self, key: str = DEMO_KEY, scopes=SCOPES, limit: int = 120, seed: int = 7, window: float = 60):
        self.key, self.scopes, self.limit, self.window = key, set(scopes), limit, window
        self.rnd = random.Random(seed)
        self.lock = threading.RLock()
        self.listings: dict[str, dict] = {}
        self.manifests: dict[str, dict] = {}  # version id -> manifest without links
        self.blobs: dict[str, bytes] = {}  # sha256 -> the file
        self.changes: list[dict] = []
        self.hits: deque = deque()  # monotonic times of the requests still inside the window
        self.fail: dict[str, list[int]] = {}  # path prefix -> statuses the next requests to it are answered with
        self.tamper: set[str] = set()  # shas served with a byte flipped
        self.pad: set[str] = set()  # shas served with extra bytes on the end
        self.seen: list[tuple[str, dict, bool]] = []  # (path, query, carried the key) per request
        self.fetched: set[str] = set()  # listings whose manifest was asked for: what the demo installed
        self.base = self.asset_base = ""
        self.t0 = datetime.now(timezone.utc).replace(microsecond=0)
        n = 0
        for title, base in MAPS:
            self._add(n := n + 1, title, "map", base_map=base)
        for title, base in GAMETYPES:
            self._add(n := n + 1, title, "gametype", base_mode=base)
        for title in PLAYLISTS:
            self._add(n := n + 1, title, "playlist")

    # --- the catalog ------------------------------------------------------------------------------------------
    def _add(self, i: int, title: str, kind: str, **extra) -> None:
        r = self.rnd
        owner_id, author = r.choice(AUTHORS)
        up, total = r.randint(0, 240), r.randint(20, 9000)
        recent = {"24h": r.randint(0, 40), "7d": r.randint(10, 300), "30d": r.randint(40, 1200)}
        x = {"id": f"lst_{i:03d}{r.getrandbits(32):08x}", "title": title, "kind": kind, "owner_id": owner_id,
             "author": author, "summary": f"A {kind} by {author}, made in Forge for Reclaimer servers.",
             "ratings": {"up": up, "down": r.randint(0, max(1, up // 8))}, "downloads": total,
             "recent_downloads": recent, "compatibility": r.choice([">=0.9.5", ">=0.9.5", ">=0.9.6", "0.9.6"]),
             "status": "published", "created_at": utc_iso(self.t0 - timedelta(days=r.randint(30, 400))),
             "versions": [], **extra}
        self.listings[x["id"]] = x
        for k in range(r.randint(1, 3)):
            self._version(x, f"1.{k}", self.t0 - timedelta(days=20 - 6 * k))

    def _version(self, x: dict, label: str, when: datetime) -> dict:
        size = self.rnd.randint(3000, 60000)
        blob = (f"{x['title']} {label} | ONI demo filler\n".encode() * (size // 20 + 1))[:size]
        sha = hashlib.sha256(blob).hexdigest()
        self.blobs[sha] = blob
        v = {"id": f"ver_{self.rnd.getrandbits(40):010x}", "version": label, "created_at": utc_iso(when),
             "notes": self.rnd.choice(["Spawns rebalanced.", "Fixed a gap behind red base.", "First release.",
                                       "Weapon timers tuned.", "Lighting pass."]), "status": "published"}
        ref = slug(x["title"])
        self.manifests[v["id"]] = {"listing_id": x["id"], "version_id": v["id"], "version": label,
                                   "kind": x["kind"], "reference": ref,
                                   "assets": [{"path": ref + EXT[x["kind"]], "size": size, "sha256": sha}]}
        x["versions"].insert(0, v)
        x["latest_version"] = {"id": v["id"], "version": label}
        x["updated_at"] = utc_iso(when)
        return v

    def publish(self, lid: str) -> str:
        """A new version of a listing, as its author would put one out; returns its id."""
        with self.lock:
            x = self.listings[lid]
            major, minor = (x["latest_version"]["version"].split(".") + ["0"])[:2]
            now = datetime.now(timezone.utc)
            v = self._version(x, f"{major}.{int(minor) + 1}", now)
            self.changes.append({"listing_id": lid, "version_id": v["id"], "change": "version_published",
                                 "updated_at": utc_iso(now)})
            return v["id"]

    def withdraw(self, lid: str) -> None:
        with self.lock:
            now = utc_iso()
            self.listings[lid].update(status="withdrawn", updated_at=now)
            self.changes.append({"listing_id": lid, "change": "withdrawn", "status": "withdrawn", "updated_at": now})

    @staticmethod
    def summary(x: dict) -> dict:
        return {k: v for k, v in x.items() if k != "versions"}

    def order(self, items: list, sort: str, window: str) -> list:
        recent = lambda x: x["recent_downloads"].get(window if sort in WINDOWED else "7d", 0)
        score = lambda x: (x["ratings"]["up"] + 1) / (x["ratings"]["up"] + x["ratings"]["down"] + 2)
        key = {"latest": lambda x: x["created_at"], "updated": lambda x: x["updated_at"],
               "downloads": recent, "rated": lambda x: (score(x), x["ratings"]["up"]),
               "unrated": lambda x: -(x["ratings"]["up"] + x["ratings"]["down"]),
               "overlooked": lambda x: score(x) / math.log(x["downloads"] + 3),
               "trending": lambda x: recent(x) * score(x), "rising": lambda x: recent(x) / (x["downloads"] + 50)}[sort]
        return sorted(items, key=key, reverse=True)

    # --- the API ----------------------------------------------------------------------------------------------
    def reply(self, status: int, data, headers: dict | None = None) -> tuple[int, dict, bytes]:
        return status, {"Content-Type": "application/json", **(headers or {})}, json.dumps(data).encode()

    def handle(self, path: str, q: dict, auth: str) -> tuple[int, dict, bytes]:
        with self.lock:
            authorised = auth == f"Bearer {self.key}"
            self.seen.append((path, q, bool(auth)))
            if path.startswith("/assets/"):  # where manifests link: no key needed, as with a CDN's signed links
                return self.asset(path.removeprefix("/assets/"))
            if not authorised:
                return self.reply(401, {"error": "unauthorized", "message": "Missing, invalid or expired API key."})
            now = time.monotonic()
            while self.hits and now - self.hits[0] >= self.window:
                self.hits.popleft()
            reset = math.ceil(self.window - (now - self.hits[0])) if self.hits else math.ceil(self.window)
            if len(self.hits) >= self.limit:
                return self.reply(429, {"error": "rate_limited", "message": "Too many requests."},
                                  {"Retry-After": str(reset), "X-RateLimit-Limit": str(self.limit),
                                   "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)})
            self.hits.append(now)
            rate = {"X-RateLimit-Limit": str(self.limit), "X-RateLimit-Remaining": str(self.limit - len(self.hits)),
                    "X-RateLimit-Reset": str(reset)}
            for prefix, statuses in self.fail.items():
                if path.startswith(prefix) and statuses:
                    status = statuses.pop(0)
                    extra = {"Retry-After": "1"} if status in (429, 503) else {}
                    return self.reply(status, {"error": f"forced_{status}", "message": f"forced {status}"},
                                      {**rate, **extra})
            status, data = self.route(path, q)
            return self.reply(status, data, rate)

    def route(self, path: str, q: dict) -> tuple[int, dict]:
        bad = lambda msg: (422, {"error": "validation", "message": msg})
        try:
            size = int(q.get("page_size", 50))
            start = int(q.get("cursor", 0))
        except ValueError:
            return bad("page_size and cursor must be whole numbers")
        if not 1 <= size <= 100:
            return bad("page_size must be 1 to 100")
        if path == "/api/listings":
            sort, window = q.get("sort", "latest"), q.get("window", "7d")
            if sort not in SORTS or window not in WINDOWS:
                return bad("unknown sort or window")
            items = [x for x in self.listings.values() if x["status"] == "published"]
            if text := q.get("q", "").lower():
                items = [x for x in items if text in x["title"].lower() or text in x["author"].lower()]
            if since := parse_iso(q.get("updated_since")):
                items = [x for x in items if parse_iso(x["updated_at"]) > since]
            items = self.order(items, sort, window)
            page = items[start:start + size]
            more = start + size < len(items)
            return 200, {"items": [self.summary(x) for x in page], "next_cursor": str(start + size) if more else None}
        if path == "/api/listings/changes":
            since, upto = parse_iso(q.get("updated_since")), parse_iso(q.get("updated_before"))
            if not since:
                return bad("updated_since is required, as an ISO 8601 time")
            as_of = datetime.now(timezone.utc).replace(microsecond=0)
            items = [c for c in self.changes
                     if since < parse_iso(c["updated_at"]) <= (upto or as_of)]
            page = items[start:start + size]
            more = start + size < len(items)
            return 200, {"as_of": utc_iso(upto or as_of), "items": page,
                         "next_cursor": str(start + size) if more else None}
        if m := re.fullmatch(r"/api/listings/([^/]+)/versions/([^/]+)/manifest", path):
            if "assets:download" not in self.scopes:
                return 403, {"error": "forbidden", "message": "This key lacks the assets:download scope."}
            x, man = self.listings.get(m[1]), self.manifests.get(m[2])
            if not x or not man or man["listing_id"] != x["id"] or x["status"] != "published":
                return 404, {"error": "not_found", "message": "That version is unavailable."}
            self.fetched.add(x["id"])
            base = self.asset_base or self.base
            return 200, {**man, "assets": [{**a, "url": f"{base}/assets/{a['sha256']}"} for a in man["assets"]]}
        if m := re.fullmatch(r"/api/listings/([^/]+)", path):
            if not (x := self.listings.get(m[1])):
                return 404, {"error": "not_found", "message": "No such listing."}
            return 200, x
        return 404, {"error": "not_found", "message": "No such endpoint."}

    def asset(self, sha: str) -> tuple[int, dict, bytes]:
        blob = self.blobs.get(sha)
        if blob is None:
            return self.reply(404, {"error": "not_found"})
        if sha in self.tamper:
            blob = bytes([blob[0] ^ 1]) + blob[1:]
        if sha in self.pad:
            blob += b"\0" * 4096
        return 200, {"Content-Type": "application/octet-stream"}, blob


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlsplit(self.path)
        q = {k: v[-1] for k, v in parse_qs(u.query).items()}
        status, headers, body = self.server.fake.handle(unquote(u.path), q, self.headers.get("Authorization", ""))
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):  # quiet: the console is the screen
        pass


def serve(fake: FakeForge | None = None) -> tuple[str, FakeForge, ThreadingHTTPServer]:
    """Start a fake on a free local port, in a thread of its own; returns (its URL, it, the server to shut down)."""
    fake = fake or FakeForge()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.daemon_threads = True
    httpd.fake = fake
    threading.Thread(target=httpd.serve_forever, args=(0.05,), daemon=True, name="fake-forge").start()
    fake.base = f"http://127.0.0.1:{httpd.server_address[1]}"
    return fake.base, fake, httpd


def start_demo() -> tuple[str, str]:
    """For --demo: a fake for the life of the process that now and then puts out a new version of something the demo
    installed, and once in a while withdraws one, so the update watcher has news to show. Returns (URL, key)."""
    url, fake, _ = serve()

    def news():
        rnd = random.Random()
        while True:
            time.sleep(rnd.uniform(45, 90))
            with fake.lock:
                live = sorted(lid for lid in fake.fetched if fake.listings[lid]["status"] == "published")
            if live:
                lid = rnd.choice(live)
                fake.withdraw(lid) if rnd.random() < .2 else fake.publish(lid)
    threading.Thread(target=news, daemon=True, name="fake-forge-news").start()
    return url, fake.key
