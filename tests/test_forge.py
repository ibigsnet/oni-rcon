import asyncio
import hashlib
import json
import sys

import pytest

from oni_rcon import forge
from oni_rcon.config import forge_settings, load_config, remember_forge_key
from oni_rcon.forge import (ForgeClient, ForgeError, Secret, Unverified, compatible, load_key, next_page, scrub,
                            scrub_data)
from oni_rcon.forgefake import FakeForge, serve

KEY = "rfk_test1234_abcdefghijklmnopqrstuvwxyz0123"


@pytest.fixture
def fake():
    url, fk, httpd = serve(FakeForge(key=KEY))
    yield fk
    httpd.shutdown()


def client(fk, tmp_path, key=KEY, **kw) -> tuple[ForgeClient, list]:
    naps = []

    async def nap(s):
        naps.append(s)
    return ForgeClient(Secret(key), fk.base, cache_dir=tmp_path / "cache", sleep=nap, **kw), naps


def test_a_key_never_prints():
    s = Secret(f"  {KEY}\n")
    assert s.value == KEY and KEY not in f"{s} {s!r} {[s]} {s:>40}" and str(s) == forge.HIDDEN
    assert scrub(f"Authorization: Bearer {KEY}") == f"Authorization: Bearer {forge.HIDDEN}"
    assert scrub("rfk_someoneelses_KEY99") == forge.HIDDEN  # shaped like a key: blanked even if it isn't ours
    data = {"a": [1, {"b": f"x {KEY} y"}], "c": "clean"}
    assert KEY not in json.dumps(scrub_data(data)) and data["a"][1]["b"].startswith("x rfk_test")  # a copy
    clean = {"a": [1, {"b": "nothing here"}]}
    assert scrub_data(clean) is clean  # untouched when there's nothing to blank


def test_where_the_key_comes_from(tmp_path, monkeypatch):
    monkeypatch.delenv("ONI_RCON_FORGE_KEY", raising=False)
    env = {"MY_KEY": "rfk_fromenv_123456", "ONI_RCON_FORGE_KEY": "rfk_fallback_123456"}
    cmd = [sys.executable, "-c", "print('rfk_fromcmd_123456')"]
    assert load_key({"forge_api_key": "rfk_saved_123456", "forge_api_key_env": "MY_KEY"}, env).source == "the config file"
    found = load_key({"forge_api_key_env": "MY_KEY", "forge_api_key_command": cmd}, env)
    assert found.key.value == "rfk_fromenv_123456" and found.source == "$MY_KEY"
    assert load_key({"forge_api_key_env": "UNSET", "forge_api_key_command": cmd}, env).key.value == "rfk_fromcmd_123456"
    assert load_key({}, env).source == "$ONI_RCON_FORGE_KEY"
    assert load_key({}, {}).key is None and not load_key({}, {}).error  # no key: Forge is off, nothing's wrong
    assert "empty or not set" in load_key({"forge_api_key_env": "UNSET"}, {}).error
    bad = load_key({"forge_api_key_command": [sys.executable, "-c", "import sys; print('rfk_leaky_99999999', "
                                                                    "file=sys.stderr); sys.exit(3)"]}, {})
    assert bad.key is None and "exited 3" in bad.error and "rfk_leaky" not in bad.error  # never a SystemExit

    p = tmp_path / "c.toml"
    p.write_text('# mine\nby = "op"\nforge_api_key_env = "MY_KEY"\n[defaults]\nssh = "box"\n[[server]]\nport = 1\n'
                 'content_dir = "~/reclaimer/content"\n')
    assert forge_settings(p) == {"forge_api_key_env": "MY_KEY"}
    assert load_config(p)[1][0].content_dir == "~/reclaimer/content"


def test_the_key_is_saved_only_when_asked(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('# mine\n\nby = "op"\n[[server]]\nport = 1\n')
    remember_forge_key(p, KEY)
    text = p.read_text()
    assert text.startswith("# mine\n\nforge_api_key = ") and text.count(KEY) == 1
    assert forge_settings(p)["forge_api_key"] == KEY and load_config(p)[1][0].port == 1
    remember_forge_key(p, "rfk_another_1234567")  # a new key replaces the old one
    assert KEY not in p.read_text() and forge_settings(p)["forge_api_key"] == "rfk_another_1234567"
    if sys.platform != "win32":
        assert p.stat().st_mode & 0o777 == 0o600


def test_the_catalog(fake, tmp_path):
    async def go():
        fc, _ = client(fake, tmp_path)
        items, nxt, _ = await fc.listings("trending", "7d", page_size=10)
        assert len(items) == 10 and nxt == {"cursor": "10"} and fc.quota.remaining == 119 and fc.quota.limit == 120
        more, _, _ = await fc.listings("trending", "7d", page_size=10, more=nxt)
        assert not {x["id"] for x in items} & {x["id"] for x in more}
        path, q, _ = fake.seen[0]
        assert path == "/api/listings" and q == {"page_size": "10", "sort": "trending", "window": "7d"}
        await fc.listings("latest", "24h")
        assert "window" not in fake.seen[-1][1]  # a window only goes with the sorts it means something for
        found, _, _ = await fc.listings("latest", query="grif")
        assert [x["title"] for x in found] == ["Grifball"]
        detail = await fc.listing(found[0]["id"])
        assert detail["versions"] and forge.latest_of(detail)["id"] == detail["versions"][0]["id"]
        sent = fc.sent
        await fc.listing(found[0]["id"])
        assert fc.sent == sent  # fresh in the cache: not asked again
    asyncio.run(go())


@pytest.mark.parametrize("status,words", [(401, "refused your API key"), (403, "catalog:read and assets:download"),
                                          (404, "isn't available"), (409, "changed state"), (413, "that large"),
                                          (422, "couldn't accept"), (503, "down for a moment")])
def test_every_error_in_words(fake, tmp_path, status, words):
    async def go():
        fc, naps = client(fake, tmp_path)
        fake.fail["/api/listings"] = [status] * 4
        with pytest.raises(ForgeError) as e:
            await fc.listings()
        assert e.value.status == status and words in e.value.text and KEY not in str(e.value)
        assert len(naps) == (3 if status == 503 else 0)  # only a 429 or a 503 is tried again
    asyncio.run(go())


def test_a_refused_key_and_a_missing_scope(fake, tmp_path):
    async def go():
        fc, _ = client(fake, tmp_path, key="rfk_wrong_000000000")
        with pytest.raises(ForgeError) as e:
            await fc.listings()
        assert e.value.short == "KEY REFUSED" and "rfk_wrong" not in e.value.text
        fake.scopes = {"catalog:read"}
        fc, _ = client(fake, tmp_path)
        x = (await fc.listings())[0][0]
        with pytest.raises(ForgeError) as e:
            await fc.manifest(x["id"], forge.latest_of(x)["id"])
        assert e.value.status == 403
    asyncio.run(go())


def test_rate_limits_are_honoured(fake, tmp_path):
    async def go():
        fc, naps = client(fake, tmp_path)
        fake.fail["/api/listings"] = [429, 429]
        items, _, _ = await fc.listings()
        assert items and len(naps) == 2 and all(1 <= n <= 1.5 for n in naps)  # Retry-After: 1, with jitter

        fake.limit, fake.window = 3, 30  # spent: the next request waits for the reset rather than drawing a 429
        fake.hits.clear()
        fc, naps = client(fake, tmp_path)
        for _ in range(3):
            await fc.get("/api/listings", {"page_size": 1})
        assert fc.quota.remaining == 0 and fc.quota.wait() > 0
        fake.hits.clear()
        await fc.get("/api/listings", {"page_size": 2})
        assert len(naps) == 1 and 25 < naps[0] <= 31
        fake.window = 600  # a reset further off than anyone should wait: reported, not waited out
        fc.quota.remaining, fc.quota.reset_at = 0, forge.time.monotonic() + 600
        with pytest.raises(ForgeError) as e:
            await fc.get("/api/listings", {"page_size": 3})
        assert e.value.status == 429 and "Try again in" in e.value.text

        q = forge.Quota(limit=120, remaining=forge.RESERVE, reset_at=forge.time.monotonic() + 20)
        assert q.wait() == 0 and q.wait(background=True) > 0  # the watcher leaves the operator room
    asyncio.run(go())


def test_an_old_copy_when_forge_is_down(fake, tmp_path):
    async def go():
        fc, _ = client(fake, tmp_path)
        x = (await fc.listings())[0][0]
        await fc.listing(x["id"])
        fc.cache.get = lambda url, max_age, real=fc.cache.get: real(url, None if max_age is None else 0)  # all stale
        fake.fail["/api/listings/"] = [503] * 4
        old = await fc.listing(x["id"])
        assert old["id"] == x["id"] and old["_stale"] >= 0
        assert not list((tmp_path / "cache").rglob("*.json"))[0].read_text().count(KEY)  # never the key on disk
    asyncio.run(go())


def test_the_key_stays_on_forge(fake, tmp_path):
    cdn_url, cdn, cdn_httpd = serve(FakeForge(key="rfk_cdn_00000000"))
    try:
        cdn.blobs = fake.blobs
        fake.asset_base = cdn_url.replace("127.0.0.1", "localhost")  # another origin, as a CDN would be

        async def go():
            fc, _ = client(fake, tmp_path)
            x = next(x for x in (await fc.listings())[0])
            man = await fc.manifest(x["id"], forge.latest_of(x)["id"])
            a = man["assets"][0]
            path = await fc.download(a["url"], a["size"], a["sha256"])
            assert hashlib.sha256(path.read_bytes()).hexdigest() == a["sha256"]
            assert cdn.seen[-1][2] is False and fake.seen[-1][2] is True  # no key to the CDN; Forge got it
        asyncio.run(go())
    finally:
        cdn_httpd.shutdown()
    with pytest.raises(ValueError, match="https"):
        ForgeClient(Secret(KEY), "http://forge.example.org")


def test_downloads_are_checked(fake, tmp_path):
    async def go():
        fc, _ = client(fake, tmp_path)
        x = (await fc.listings())[0][0]
        a = (await fc.manifest(x["id"], forge.latest_of(x)["id"]))["assets"][0]
        fake.tamper.add(a["sha256"])
        with pytest.raises(Unverified, match="SHA-256"):
            await fc.download(a["url"], a["size"], a["sha256"])
        fake.tamper.clear()
        fake.pad.add(a["sha256"])
        with pytest.raises(Unverified, match="bigger than the manifest"):
            await fc.download(a["url"], a["size"], a["sha256"])
        fake.pad.clear()
        assert not list((tmp_path / "cache" / "assets").iterdir())  # nothing that failed is kept
        with pytest.raises(Unverified, match="no SHA-256"):
            await fc.download(a["url"], a["size"], "md5:abc")
        with pytest.raises(Unverified, match="plain http"):
            await fc.download("http://cdn.example.org/x", a["size"], a["sha256"])
        path = await fc.download(a["url"], a["size"], "sha256:" + a["sha256"].upper())
        sent = fc.sent
        assert await fc.download(a["url"], a["size"], a["sha256"]) == path and fc.sent == sent  # kept, rechecked
    asyncio.run(go())


def test_the_changes_feed_pages_from_as_of(fake, tmp_path):
    async def go():
        fc, _ = client(fake, tmp_path)
        lids = list(fake.listings)[:5]
        for lid in lids:
            fake.publish(lid)
        fake.withdraw(lids[0])
        since = forge.before(forge.utc_iso(), 60)
        changes, as_of = await fc.changes(since, page_size=2)
        assert len(changes) == 6 and changes[-1]["change"] == "withdrawn"
        pages = [q for path, q, _ in fake.seen if path == "/api/listings/changes"]
        assert len(pages) == 3 and "updated_before" not in pages[0]
        assert all(q["updated_before"] == as_of and q["updated_since"] == since for q in pages[1:])
        fake.fail["/api/listings/changes"] = [200]  # a page without as_of differs from the docs: say so
        with pytest.raises(ForgeError, match="no as_of"):
            await fc.changes(since)
    asyncio.run(go())


def test_reading_listings_tolerantly():
    assert next_page({"next_cursor": "abc"}) == {"cursor": "abc"} and next_page({"next_cursor": None}) is None
    assert next_page({"page": 2, "total_pages": 3}) == {"page": 3} and next_page({"page": 3, "pages": 3}) is None
    x = {"name": "Pit", "type": "Game_Type", "owner": {"id": "u1", "display_name": "Juno"},
         "ratings": {"positive": 9, "negative": 1}, "recent_downloads": {"7d": 40}, "state": "Withdrawn"}
    assert (forge.title_of(x), forge.kind_of(x), forge.owner_of(x), forge.author_of(x)) == ("Pit", "gametype", "u1", "Juno")
    assert forge.ratings_of(x) == (9, 1, None, 10) and forge.recent_of(x, "7d") == 40 and forge.is_withdrawn(x)


def test_compatibility():
    assert compatible(">=0.9.5", "0.9.7-demo") and not compatible("0.9.6", "0.9.7")
    assert compatible("0.9.x", "0.9.7") and compatible(">=0.9.5, <0.10", "0.9.7") and compatible("<=0.9", "0.9.7")
    assert not compatible(">0.9.7", "0.9.7") and compatible(["0.9.6", "0.9.7"], "0.9.7")
    assert compatible({"min": "0.9.5"}, "0.9.7") and not compatible({"max": "0.9.6"}, "0.9.7")
    assert compatible({"reclaimer": ">=0.9.8"}, "0.9.7") is False
    assert compatible(None, "0.9.7") is None and compatible(">=0.9", "") is None and compatible("soon", "0.9.7") is None
