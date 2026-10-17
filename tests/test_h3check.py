import base64, hashlib, json
import pytest
from oni_rcon.h3check import Rejected, check, H3_MAP_VARIANT

MAP = {"version": 1, "name": "Arena", "base_map": {"id": 30, "stem": "guardian"}, "hosting": {}, "mode": "forge",
       "rules": {}, "map": {"data": base64.b64encode(b"\0" * H3_MAP_VARIANT).decode()}}
GAME = {"version": 1, "name": "CTF", "author": "x", "base_map": {"id": 30, "stem": "guardian"}, "hosting": {},
        "map": {}, "mode": "ctf", "rules": {}, "options": {}, "packed_game": {"data": base64.b64encode(b"_blf" + b"\0" * 60).decode()}}
PLAYLIST = {"version": 2, "name": "p", "vote_seconds": 30, "games": [{"map": "Maps/Forge/a.json", "game": "ctf"}]}
J = lambda d: json.dumps(d).encode()


def test_genuine_forge_files_pass():
    assert check("a.json", J(MAP), "forge") == "forge map"
    assert check("g.json", J(GAME), "forge") == "forge game type"
    assert check("p.json", J(PLAYLIST), "forge") == "forge playlist"
    assert check("v.mvar", b"_blf" + b"\0" * 100, "forge") == "forge variant"


@pytest.mark.parametrize("name,data", [
    ("map.json", b"MZ\x90\0" + b"\0" * 100),                  # a Windows program named .json
    ("v.mvar", b"MZ" + b"\0" * 100),                          # ...named .mvar
    ("v.bin", b"PK\x03\x04" + b"\0" * 100),                   # a zip named .bin
    ("evil.exe", b"_blf" + b"\0" * 10),                       # right bytes, wrong (program) extension
    ("x.dll", b"\0" * 10), ("run.bat", b"@echo off"), ("s.ps1", b"x"), ("l.lnk", b"x"), ("a.zip", b"x"),
    ("x.txt", b"hello"),                                     # Forge never ships text
    ("x.json", J({**MAP, "rcon_password": "x"})),             # unknown fields
    ("x.json", J({**MAP, "map": {"data": base64.b64encode(b"\0" * 1000).decode()}})),   # wrong map size
    ("x.json", J({**MAP, "map": {"data": base64.b64encode(b"MZ" + b"\0" * 2000).decode()}})),
    ("x.json", J({**GAME, "packed_game": {"data": base64.b64encode(b"\x7fELF" + b"\0" * 60).decode()}})),
    ("p.json", J({**PLAYLIST, "games": [{"map": "../../etc/passwd", "game": "ctf"}]})),
    ("p.json", J({**PLAYLIST, "games": [{"map": "C:\\\\Windows\\\\x", "game": "ctf"}]})),
    ("big.json", b"{" + b" " * (3 * 1024 * 1024) + b"}"),
    ("deep.json", b'{"a":' * 100 + b"1" + b"}" * 100),
])
def test_forge_refuses(name, data):
    with pytest.raises(Rejected):
        check(name, data, "forge")


def test_genuine_mod_files_pass():
    assert check("maps/x.map", b"daeh" + b"\0" * 100) == "halo map"
    assert check("sound/x.fsb", b"FSB5" + b"\0" * 100) == "sound bank"
    assert check("img.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 10) == "image"
    assert check("ModInfo.json", b'{"Title": "x"}') == "mod json"
    assert check("tag.scenario", b"\0" * 60 + b"MALB" + b"\0" * 10) == "halo tag"
    assert check("readme.txt", b"Thanks for playing") == "text"


@pytest.mark.parametrize("name,data", [
    ("maps/x.map", b"MZ" + b"\0" * 100),        # program disguised as a map
    ("maps/x.map", b"\0" * 100),                # no map header
    ("x.fsb", b"\x7fELF" + b"\0" * 10),
    ("img.png", b"<script>alert(1)</script>"),
    ("x.bin", b"#!/bin/sh\nrm -rf /"),
    ("x.info", b"MZ" + b"\0" * 100),
    ("payload.dll", b"\0"), ("tool.exe", b"\0"), ("x.so", b"\0"), ("x.desktop", b"\0"), ("x.html", b"<html>"),
    ("x.unknownext", b"\0" * 10), ("noext", b"\0" * 10), (".hidden.map", b"daeh"),
])
def test_mod_refuses(name, data):
    with pytest.raises(Rejected):
        check(name, data, "mod")


def test_game_programs_must_be_the_official_ones():
    dll = b"MZ" + b"official" * 10
    official = {"halo3/halo3.dll": hashlib.sha256(dll).hexdigest()}
    assert check("halo3/halo3.dll", dll, "game", official) == "official game program"
    with pytest.raises(Rejected):
        check("halo3/halo3.dll", dll + b"patched", "game", official)
    with pytest.raises(Rejected):
        check("halo3/extra.dll", dll, "game", official)
    with pytest.raises(Rejected):
        check("halo3/halo3.dll", dll, "mod", official)        # never a program in a mod
    assert check("halo3/maps/shared.map", b"daeh" + b"\0" * 10, "game") == "halo map"


def test_vet_refuses_held_releases_and_reports_bad_files(tmp_path, monkeypatch):
    from oni_rcon import install
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_bytes(J(MAP))
    bad.write_bytes(b"MZ" + b"\0" * 100)
    files = [install.File("Maps/Forge/rf-good.json", "u", 1, "a" * 64)]
    sent = []
    monkeypatch.setattr(install, "report", lambda *a: sent.append(a))
    monkeypatch.setattr(install, "held", lambda lid, vid: None)
    install.vet(files, [good], "lid-1", "vid-1")                    # fine: nothing raised, nothing reported
    assert sent == []
    with pytest.raises(install.InstallError, match="IBIGS has been told"):
        install.vet([install.File("Maps/Forge/rf-bad.json", "u", 1, "b" * 64)], [bad], "lid-1", "vid-1")
    assert sent and sent[0][:3] == ("lid-1", "vid-1", "Maps/Forge/rf-bad.json")
    monkeypatch.setattr(install, "held", lambda lid, vid: "evil.json: really a Windows program")
    with pytest.raises(install.InstallError, match="holding this release"):
        install.vet(files, [good], "lid-1", "vid-1")


def test_held_reads_the_hold_list(monkeypatch):
    import io, json as _json
    from oni_rcon import install
    doc = {"holds": [{"listing_id": "L", "version_id": "V", "reason": "bad"}]}
    monkeypatch.setattr(install, "HOLDS_URL", "https://example.invalid/h.json")
    monkeypatch.setattr(install, "_holds", (0.0, set()))
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=0: io.BytesIO(_json.dumps(doc).encode()))
    assert install.held("L", "V") == "bad" and install.held("L", "other") is None and install.held("X", "V") is None
