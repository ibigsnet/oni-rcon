"""The 0.10 game type upgrader: old game types 0.10 skips, fixed only where a value has one safe meaning."""
import asyncio
import json
import os
from types import SimpleNamespace

import pytest

from oni_rcon import gametypes as G

GAME_VARIANTS = ("halo3", "game_variants")
# What 0.10.1 logs for such a file (the path as the server's container sees it).
LOGGED = ('Skipped content file /server/game/halo3/game_variants/{name}: Teams enabled (rounds.teams) has no value 3. '
          'Valid choices: "Off" (0), "On" (1).')
OLD_TIME = 1_700_000_000


def game_type(mode="KingOfTheHill", **options):
    """A game type as 0.9 and 0.10 save it, with made-up names."""
    return {"version": 1, "name": f"Test {mode}", "base_map": {"id": 320, "stem": "guardian", "title": "Guardian"},
            "mode": mode, "rules": {"score_to_win": 25, "kill_points": 1, "death_points": 0, "suicide_points": -1,
                                    "betrayal_points": -1},
            "options": {"mode.hill_movement": 8, "mode.team_scoring": 3, "rounds.minutes": 0,
                        "rounds.team_changing": 2, **options},
            "world": {"player_scale_percent": None, "sprint": "unlimited"}, "author": "tester"}


def put(folder, name, data, raw=None):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(raw if raw is not None else (json.dumps(data, indent=2) + "\n").encode())
    os.utime(path, (OLD_TIME, OLD_TIME))
    return path


@pytest.fixture
def game(tmp_path):
    return tmp_path / "game"


def variants(game):
    return game.joinpath(*GAME_VARIANTS)


def test_the_table_is_0_10s():
    cat = G.catalog()
    assert set(cat.modes) == {"Slayer", "CaptureTheFlag", "Oddball", "KingOfTheHill", "Assault", "Infection",
                              "Territories", "Juggernaut", "Vip", "EscalationSlayer", "Forge"}
    for fields in cat.modes.values():
        teams = fields["rounds.teams"]
        assert (teams.kind, teams.min, teams.max, teams.labels) == ("flag", 0, 1, ("Off", "On"))
        assert fields["rounds.limit"].min == 1
    assert "firefight.team_lives" in cat.firefight
    assert cat.modes["KingOfTheHill"]["mode.team_scoring"].labels[3] == "Team control"


@pytest.mark.parametrize("old", [2, 3])
def test_teams_2_and_3_become_on_with_the_original_kept_beside_it(game, old):
    path = put(variants(game), "koth.json", game_type(**{"rounds.teams": old}))
    before = path.read_bytes()
    findings, _ = G.scan(G.folders_of(game))
    assert [(f.path, f.state) for f in findings] == [(path, "fixable")]
    assert G.summary(findings) == (1, 1)
    change, = findings[0].changes
    assert (change.key, change.old, change.new) == ("rounds.teams", old, 1)

    done, failed = G.upgrade_all(findings)
    assert failed == [] and len(done) == 1
    backup = path.with_name("koth.json" + G.BACKUP_SUFFIX)
    assert done[0][1] == backup and backup.read_bytes() == before, "the original, byte for byte"
    after = json.loads(path.read_text())
    want = game_type(**{"rounds.teams": 1})
    assert after == want and list(after["options"]) == list(want["options"]), "only the one value, order kept"
    assert path.stat().st_mtime == OLD_TIME, "lists sort by date: it keeps its place"
    assert path.read_text().endswith("}\n")
    assert G.inspect(path)[0].state == "ok"


def test_a_valid_file_is_left_exactly_as_it_is(game):
    path = put(variants(game), "fine.json", game_type(**{"rounds.teams": 1, "player.primary_weapon": "Battle rifle"}))
    before = path.read_bytes()
    findings, elsewhere = G.scan(G.folders_of(game))
    assert findings == [] and elsewhere == []
    assert G.upgrade_file(path) == (G.inspect(path)[0], None)
    assert path.read_bytes() == before and path.stat().st_mtime == OLD_TIME
    assert not path.with_name("fine.json" + G.BACKUP_SUFFIX).exists()
    assert G.doctor_notes(findings, elsewhere)[0]["state"] == "ok"


def test_a_value_with_no_safe_meaning_is_left_alone_and_reported(game):
    path = put(variants(game), "zeroed.json", game_type(**{"rounds.teams": 3, "rounds.limit": 0, "no.such": 1}))
    before = path.read_bytes()
    findings, _ = G.scan(G.folders_of(game))
    f, = findings
    assert f.state == "stuck"
    assert any("Round limit (rounds.limit) has no value 0. Valid values: 1 to 10." in p for p in f.problems)
    assert any("Unknown game option for King of the Hill: no.such" in p for p in f.problems)
    assert G.summary(findings) == (1, 0)
    assert G.upgrade_all(findings) == ([], [])
    assert G.upgrade_file(path)[1] is None, "not even its safe values: the file stays as it is"
    assert path.read_bytes() == before and not path.with_name("zeroed.json" + G.BACKUP_SUFFIX).exists()
    notes = G.doctor_notes(findings, [])
    assert notes[0]["text"] == "old game types the server skips: 1, fixable: 0"
    assert "can't be fixed safely" in notes[1]["text"] and "rounds.limit" in notes[1]["text"]
    assert "can't fix safely, left as it is: Round limit" in G.report(findings, [])


def test_the_game_types_own_value_only_when_asked(game):
    path = put(variants(game), "zeroed.json", game_type(**{"rounds.teams": 3, "rounds.limit": 0}))
    findings, _ = G.scan(G.folders_of(game), game_type_value=True)
    assert G.summary(findings) == (1, 1)
    G.upgrade_all(findings, game_type_value=True)
    options = json.loads(path.read_text())["options"]
    assert options["rounds.teams"] == 1 and "rounds.limit" not in options


def test_a_second_run_changes_nothing(game):
    path = put(variants(game), "koth.json", game_type(**{"rounds.teams": 3}))
    G.upgrade_all(G.scan(G.folders_of(game))[0])
    once, files = path.read_bytes(), sorted(p.name for p in path.parent.iterdir())
    findings, _ = G.scan(G.folders_of(game))
    assert findings == [] and G.upgrade_all(findings) == ([], [])
    assert path.read_bytes() == once and sorted(p.name for p in path.parent.iterdir()) == files


def test_a_new_file_of_the_same_name_gets_its_own_backup(game):
    path = put(variants(game), "koth.json", game_type(**{"rounds.teams": 3}))
    G.upgrade_all(G.scan(G.folders_of(game))[0])
    put(variants(game), "koth.json", game_type(**{"rounds.teams": 2}))
    second = path.read_bytes()
    G.upgrade_all(G.scan(G.folders_of(game))[0])
    assert path.with_name("koth.json.oni-backup-2").read_bytes() == second
    assert json.loads(path.with_name("koth.json.oni-backup").read_text())["options"]["rounds.teams"] == 3


@pytest.mark.parametrize("mode, old, new", [("CaptureTheFlag", 3, 1), ("Infection", 2, 0), ("Juggernaut", 5, 0)])
def test_modes_whose_engine_fixes_teams_get_what_it_plays(game, mode, old, new):
    data = game_type(mode, **{"rounds.teams": old})
    data["options"] = {"rounds.teams": old}
    path = put(variants(game), "fixed.json", data)
    findings, _ = G.scan(G.folders_of(game))
    assert findings[0].changes[0].new == new
    G.upgrade_all(findings)
    assert json.loads(path.read_text())["options"] == {"rounds.teams": new}


@pytest.mark.parametrize("key, old, new", [
    ("rounds.friendly_fire", True, 1), ("rounds.reset_map", False, 0), ("rounds.minutes", 15.0, 15),
    ("rounds.teams", None, G.REMOVE), ("map.indestructible_vehicles", 2, 1),
])
def test_other_values_with_one_meaning(key, old, new, tmp_path):
    data = game_type()
    data["options"] = {key: old}
    path = put(tmp_path / "Game Modes", "x.json", data)
    finding, fixed = G.inspect(path)
    assert finding.state == "fixable", finding.problems
    assert finding.changes[0].new is new if new is G.REMOVE else finding.changes[0].new == new
    assert fixed["options"] == ({} if new is G.REMOVE else {key: new})


@pytest.mark.parametrize("key, old", [
    ("rounds.teams", -1), ("rounds.teams", "maybe"), ("rounds.minutes", 61), ("rounds.minutes", 1.5),
    ("mode.team_scoring", 4), ("map.weapons", -1), ("rounds.minutes", [1]),
])
def test_values_with_no_safe_meaning(key, old, tmp_path):
    data = game_type()
    data["options"] = {key: old}
    path = put(tmp_path / "Game Modes", "x.json", data)
    finding, fixed = G.inspect(path)
    assert (finding.state, fixed, finding.changes) == ("stuck", None, [])
    assert finding.problems


def test_labels_read_as_0_10_reads_them(tmp_path):
    data = game_type()
    data["options"] = {"rounds.teams": "on", "player.radar": "Allies", "player.primary_weapon": "battle rifle"}
    finding, fixed = G.inspect(put(tmp_path, "labels.json", data))
    assert (finding.state, fixed) == ("ok", None), "0.10 reads labels and earlier editors' names itself"


def test_a_byte_order_mark_is_removed(tmp_path):
    raw = b"\xef\xbb\xbf" + json.dumps(game_type(**{"rounds.teams": 1})).encode()
    path = put(tmp_path / "Game Modes", "bom.json", None, raw=raw)
    finding, _ = G.inspect(path)
    assert finding.state == "fixable"
    G.upgrade_file(path)
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert path.with_name("bom.json.oni-backup").read_bytes() == raw


def test_other_json_files_are_not_game_types(game, tmp_path):
    put(variants(game), "playlist.json", {"name": "p", "entries": [{"rounds.teams": 3}]})
    put(variants(game), "broken.json", None, raw=b"{")
    assert G.scan(G.folders_of(game)) == ([], [])


def test_the_servers_log_names_the_files_it_skipped(game):
    path = put(variants(game), "koth one.json", game_type(**{"rounds.teams": 3}))
    other = put(variants(game), "named.json", dict(game_type(), name=""))
    log = "\n".join([
        "2026-11-09T01:00:00Z " + LOGGED.format(name="koth one.json"),
        "Skipped content file /server/game/halo3/game_variants/named.json: Choose a name of 1–48 characters",
        "Skipped content file /server/content/Game Modes/gone.json: Unknown game option for Slayer: x",
        "[content] /server/content/Maps/a.json stays as it was: the backup failed",
        "an unrelated line",
    ])
    assert [kind for _, _, kind in G.read_log(log)] == ["skipped"] * 4
    findings, elsewhere = G.scan(G.folders_of(game), log)
    by = {f.path: f for f in findings}
    assert by[path].state == "fixable" and by[path].log[0].startswith("skipped: Teams enabled")
    assert by[other].state == "stuck" and "the server says: Choose a name" in by[other].problems[0]
    assert len(elsewhere) == 2 and "gone.json" in elsewhere[0]
    assert G.summary(findings) == (2, 1)


def test_the_command_asks_before_it_changes_anything(game, capsys, monkeypatch):
    path = put(variants(game), "koth.json", game_type(**{"rounds.teams": 3}))
    before = path.read_bytes()
    assert G.main(["doctor", "--game", str(game)]) == 1
    assert "Old game types the server skips: 1, fixable: 1" in capsys.readouterr().out
    assert G.main(["upgrade", "--dry-run", "--game", str(game)]) == 1
    assert G.main(["upgrade", "--game", str(game)]) == 1, "not a terminal and no --yes: nothing changes"
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert G.main(["upgrade", "--game", str(game)]) == 1
    assert path.read_bytes() == before and not path.with_name("koth.json.oni-backup").exists()
    capsys.readouterr()
    assert G.main(["doctor", "--json", "--game", str(game)]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["fixed"] is False and out["results"][0]["text"] == ("old game types the server skips: 1, fixable: 1 "
                                                                   "(see: oni gametypes upgrade)")
    assert out["files"][0]["changes"][0] == {"option": "rounds.teams", "label": "Teams enabled", "from": 3, "to": 1,
                                             "removed": False, "why": out["files"][0]["changes"][0]["why"]}
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert G.main(["upgrade", "--game", str(game)]) == 0
    assert "Upgraded koth.json" in capsys.readouterr().out
    assert G.main(["doctor", "--game", str(game)]) == 0
    with pytest.raises(SystemExit):
        G.main(["doctor"])


def test_the_console_checks_its_own_servers_folders(tmp_path):
    from oni_rcon.jobs_ui import gametypes_note
    game = tmp_path / "game"
    put(variants(game), "koth.json", game_type(**{"rounds.teams": 2}))
    here = SimpleNamespace(server=SimpleNamespace(ssh="", url="", content_dir=str(tmp_path / "content"),
                                                  game_dir=str(game)))
    away = SimpleNamespace(server=SimpleNamespace(ssh="admin@elsewhere", url="", content_dir="/x", game_dir="/y"))
    assert G.local_folders([here.server, away.server, here.server]) == G.folders_of(game, tmp_path / "content")
    note = asyncio.run(gametypes_note(SimpleNamespace(stations=[here, away])))
    assert note == "[ !! ] old game types the server skips: 1, fixable: 1 (F9: Upgrade old game types)"
    assert asyncio.run(gametypes_note(SimpleNamespace(stations=[away]))) == ""
