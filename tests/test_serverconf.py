"""The 0.10 settings catalog and the careful dedicated.toml editor."""
import json
import subprocess
import sys
import tomllib

import pytest

from oni_rcon import serverconf as C

FILE = """# my server
game = "game"
[master]
enabled = true

[rcon]
address = "127.0.0.1"

[[server]]  # first
name = "One"
port = 20001
# a note to keep
map = "Valhalla"

[server.campaign]
mission = 3010

[[server]]
name = "Two"
votes = true
port = 20003

[workshop]
provisioner = "x"
"""


def test_version_gate_is_0_10_and_unknown_is_no():
    assert C.supports("0.10.0") and C.supports("v0.10.1") and C.supports("0.11.0") and C.supports("1.0.0")
    assert not C.supports("0.9.23") and not C.supports(None) and not C.supports("dev")


def test_plain_key_lands_above_the_first_table_and_comments_stay():
    out = C.edit(FILE, 0, {"coop_network": "host"})
    assert tomllib.loads(out)["server"][0]["coop_network"] == "host"
    assert out.index("coop_network") < out.index("[server.campaign]")
    assert "# a note to keep" in out and "# my server" in out and "[[server]]  # first" in out


def test_table_keys_get_their_own_headers_in_the_right_block():
    out = C.edit(FILE, 0, {"votes.kick": "members", "votes.limits.calls_per_hour": 4, "announce.joins": "all"})
    data = tomllib.loads(out)
    assert data["server"][0]["votes"] == {"kick": "members", "limits": {"calls_per_hour": 4}}
    assert data["server"][0]["announce"] == {"joins": "all"}
    assert data["server"][1]["votes"] is True and data["workshop"] == {"provisioner": "x"}


def test_a_table_replaces_the_old_switch_and_the_switch_replaces_the_table():
    out = C.edit(FILE, 1, {"votes.kick": "off"})
    assert tomllib.loads(out)["server"][1]["votes"] == {"kick": "off"}
    assert tomllib.loads(out)["workshop"] == {"provisioner": "x"}  # still its own table, after the block's
    back = C.edit(out, 1, {"votes": False})
    assert tomllib.loads(back)["server"][1]["votes"] is False and "[server.votes]" not in back


def test_taking_the_last_key_out_takes_its_header_too():
    out = C.edit(FILE, 0, {"announce.kick": "all"})
    back = C.edit(out, 0, {"announce.kick": None})
    assert "[server.announce]" not in back
    assert tomllib.loads(back) == tomllib.loads(FILE)


def test_removing_a_key_that_is_not_there_changes_nothing():
    assert tomllib.loads(C.edit(FILE, 0, {"joins": None})) == tomllib.loads(FILE)


def test_replacing_a_value_spread_over_lines():
    text = FILE.replace('map = "Valhalla"', 'customization = [\n  "Halo 3: ODST",\n]\nmap = "Valhalla"')
    out = C.edit(text, 0, {"customization": ["Halo: Reach"]})
    assert tomllib.loads(out)["server"][0]["customization"] == ["Halo: Reach"]
    assert tomllib.loads(out)["server"][0]["map"] == "Valhalla"


def test_a_layout_it_cannot_edit_is_refused_not_mangled():
    text = FILE.replace('name = "One"', 'name = "One"\nvotes = { kick = "off" }')
    with pytest.raises(ValueError, match="laid out"):
        C.edit(text, 0, {"votes.endgame": "off"})
    with pytest.raises(ValueError, match="no server"):
        C.edit(FILE, 5, {"joins": "open"})


def test_role_table_round_trip():
    out = C.edit(FILE, 0, C.custom_role("dj", "DJ", 12, "say, voice.music"))
    assert tomllib.loads(out)["server"][0]["roles"]["dj"] == {"title": "DJ", "rank": 12, "can": ["say", "voice.music"]}


@pytest.mark.parametrize("args", [("host", "", 5, "say"), ("dj", "x" * 17, 5, "say"), ("dj", "DJ", 40, "say"),
                                  ("dj", "DJ", 5, "fly")])
def test_custom_role_refusals(args):
    with pytest.raises(ValueError):
        C.custom_role(*args)


def test_custom_role_takes_presets_wildcards_removals():
    got = C.custom_role("x", "", 15, "preset:mod, -mute.shadow, mute.*, ban.temp:120")
    assert got["roles.x.can"] == ["preset:mod", "-mute.shadow", "mute.*", "ban.temp:120"] and got["roles.x.title"] == "X"


def test_parse_checks_each_kind():
    assert C.parse(C.BY_KEY["queue_max"], "5") == 5 and C.parse(C.BY_KEY["queue_max"], "") is None
    with pytest.raises(ValueError):
        C.parse(C.BY_KEY["queue_max"], "999")
    with pytest.raises(ValueError):
        C.parse(C.BY_KEY["queue_max"], "lots")
    assert C.parse(C.BY_KEY["coop_network"], json.dumps("host")) == "host"
    assert C.parse(C.BY_KEY["coop_network"], C.DEFAULT) is None
    with pytest.raises(ValueError):
        C.parse(C.BY_KEY["coop_network"], json.dumps("bogus"))
    assert C.parse(C.BY_KEY["metrics_http"], "127.0.0.1:9310") == "127.0.0.1:9310"
    with pytest.raises(ValueError, match="this machine"):
        C.parse(C.BY_KEY["metrics_http"], "0.0.0.0:9310")
    assert C.parse(C.BY_KEY["kick_blocks"], "10m, 30m") == ["10m", "30m"]
    assert C.parse(C.BY_KEY["kick_blocks"], "none") == []
    with pytest.raises(ValueError, match="rise"):
        C.parse(C.BY_KEY["kick_blocks"], "30m, 10m")


def test_reach_warns_loudly_and_only_when_it_applies():
    said = C.warnings({"customization": ["Halo 3: ODST", "Halo: Reach"], "coop_network": "host"})
    assert said[0].startswith("NEEDS REACH") and any("Experimental" in w for w in said)
    assert C.warnings({"customization": ["Halo 3: ODST"]}) == []
    assert C.warnings({"reach_objects": True})[0].startswith("NEEDS REACH")
    assert C.warnings({"reach_objects": False}) == []
    assert "ODST" in C.warnings({"odst_objects": True})[0]


def test_advanced_reads_toml_and_keeps_the_keys_that_reach_the_server_away():
    assert C.advanced("votes.limits.escalate", "true") == ("votes.limits.escalate", True)
    assert C.advanced("queue_starve_after", "300") == ("queue_starve_after", 300)
    assert C.advanced("forge_mods", '["a", "b"]') == ("forge_mods", ["a", "b"])
    assert C.advanced("voice_quality", "high") == ("voice_quality", "high")
    assert C.advanced("joins", "") == ("joins", None)
    for key in ("rcon_password", "password", "port", "rcon.address", "game", "BAD KEY", "social_key"):
        with pytest.raises(ValueError):
            C.advanced(key, "x")


def test_safe_view_never_carries_a_password():
    view = C.safe_view({"rcon": {"password": "p"}, "server": [{"name": "a", "rcon_password": "p", "password": "q",
                                                               "votes": {"kick": "off"}}], "social_key": "k"})
    assert view == {"server": [{"name": "a", "votes": {"kick": "off"}}]}


def test_effective_prefers_the_server_then_defaults():
    top = {"joins": "closed", "defaults": {"queue_max": 3, "votes": {"kick": "mod"}}, "server": [{"queue_max": 5}]}
    assert C.effective(top, 0, "queue_max") == (5, "server")
    assert C.effective(top, 0, "votes.kick") == ("mod", "defaults")
    assert C.effective(top, 0, "joins") == ("closed", "top")
    assert C.effective(top, 0, "announce.kick") == (None, "")


def test_snippet_shows_what_to_paste():
    text = C.snippet({"coop_network": "host", "votes.kick": "off", "votes.limits.calls_per_hour": 4})
    assert text.index("coop_network") < text.index("[server.votes]") < text.index("[server.votes.limits]")
    assert tomllib.loads(text.replace("[server.", "[")) and 'kick = "off"' in text


def test_helper_entry_refuses_old_servers_and_edits_new_ones():
    run = lambda req: subprocess.run([sys.executable, "-m", "oni_rcon", "--edit-settings"], input=json.dumps(req),
                                     capture_output=True, text=True)
    old = run({"toml": FILE, "index": 0, "changes": {"joins": "queue"}, "version": "0.9.23"})
    assert old.returncode == 1 and "before 0.10" in json.loads(old.stdout)["error"]
    new = run({"toml": FILE, "index": 0, "changes": {"joins": "queue"}, "version": "0.10.0"})
    assert tomllib.loads(json.loads(new.stdout)["toml"])["server"][0]["joins"] == "queue"


def test_every_menu_key_is_one_a_real_0_10_check_took():
    """The keys were run through `dedicated check` on 0.10.0; this keeps the catalog to that list."""
    checked = {"votes.kick", "votes.endround", "votes.endgame", "votes.shuffle", "votes.limits.caller_min_joined_seconds",
               "votes.limits.caller_cooldown_seconds", "votes.limits.calls_per_hour", "votes.limits.quorum_percent",
               "votes.limits.pass_percent", "joins", "join_gates", "queue_max", "vip_queue_max", "vip_priority",
               "queue_max_wait", "announce.kick", "announce.ban", "announce.mute", "announce.votes", "announce.joins",
               "command_echo", "voice_mute", "moderator_ban_minutes", "devs_as_vip", "customization",
               "require_customization", "no_vanilla_characters", "coop_network", "odst_objects", "reach_objects",
               "forge_primitives", "halo3_objects", "kick_blocks", "metrics_http", "server_message_color", "queue_mode"}
    assert set(C.BY_KEY) == checked
