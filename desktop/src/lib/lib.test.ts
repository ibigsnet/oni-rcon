import { describe, expect, it } from "vitest";
import { behind, describe as describeEvent, explain, hotReloads, mixedVersions, idForms, kickLeft, notesText, noteKick, pardonTarget, parseCommand, plain, pluginReply, pluginSnapshot, reactionWords, redactData, redactText, sides, splitTag, typedParams, vpnCheck, vpnLists, type Kick } from "./util";
import { Medals } from "./medals";
import { compatible, latestOf, refsOf, votesOf } from "./forge";
import { Superintendent, classify } from "../art/superintendent";
import { BASE, face, faceClass } from "../art/archive";
import { smooth, tones } from "../art/pixels";
import { decrypt, noise } from "../art/palette";
import { advice, level, scaleTop } from "./perf";
import { filterTerms, nameDistance, rankedPlayers, type Found } from "./search";
import { Findings, chatRequest, publicRoster } from "./findings";
import { arrange, catalogItems, checkPlaylist, gameLabel, itemsFor, ordered, slug } from "./content";

describe("console lines", () => {
  it("keeps a message whole for say, tell, kick and rename", () => {
    expect(parseCommand("say it's a trap")).toEqual(["say", "it's a trap"]);
    expect(parseCommand('tell #3 "don\'t camp"')).toEqual(["tell", "#3", "don't camp"]);
    expect(parseCommand("kick Viper team killing again")).toEqual(["kick", "Viper", "team killing again"]);
    expect(parseCommand('ban "Echo 4" 2h spam')).toEqual(["ban", "Echo 4", "2h", "spam"]);
    expect(() => parseCommand('ban "Echo 4')).toThrow();
  });
});

describe("IDs", () => {
  it("reads one number in either base and either half of a long hex ID", () => {
    const f = idForms("00000000000000ff0000000000000010");
    expect(f.has("255")).toBe(true);
    expect(f.has("16")).toBe(true);
    expect(idForms(255).has("ff")).toBe(true);
    expect(idForms(-1).has("18446744073709551615")).toBe(true);
  });
});

describe("medals", () => {
  it("chains multi-kills, counts sprees and killjoys", () => {
    const m = new Medals();
    expect(m.kill("A", "B", 0)).toEqual([]);
    expect(m.kill("A", "C", 1)).toEqual(["DOUBLE KILL"]);
    expect(m.kill("A", "D", 2)).toEqual(["TRIPLE KILL"]);
    m.kill("A", "E", 10);
    expect(m.kill("A", "F", 20)).toEqual(["KILLING SPREE"]);
    expect(m.kill("B", "A", 30)).toEqual(["KILLJOY"]);
    expect(m.kill(null, "B", 31)).toEqual([]);
  });
});

describe("words", () => {
  it("explains connection failures from Rust's own messages", () => {
    expect(explain("IO error: Connection refused (os error 111); retry in 4s", "offline", true)).toBe("NOTHING ON THAT PORT");
    expect(explain("failed to lookup address information: Name or service not known", "offline", true)).toBe("UNKNOWN HOST");
    expect(explain("HTTP error: 404 Not Found", "offline", true)).toBe("NOT AN RCON PORT");
    expect(explain("Wrong password.", "denied", true)).toBe("PASSWORD REFUSED");
  });
  it("splits community tags", () => {
    expect(splitTag("ALPHA · Big Team")).toEqual(["ALPHA", "Big Team"]);
    expect(splitTag("Slayer")).toEqual(["", "Slayer"]);
  });
  it("blanks addresses and keys while redacted", () => {
    expect(redactText("from 203.0.113.9 rfk_abcdefghij", true)).toBe("from ███.███.███.███ rfk_…████");
    expect(redactData({ address: "1.2.3.4", n: 1 }, true)).toEqual({ address: "███.███.███.███", n: 1 });
  });
  it("gives a plug-in the server, with addresses only when its manifest grants them", () => {
    const players = [{ name: "A", team: "red", score: 3, address: "203.0.113.9" }, { name: "B", team: "blue", score: 5, address: "203.0.113.10" }];
    const s = pluginSnapshot({ map: "guardian", mode: "slayer" }, players, false);
    expect(s).toMatchObject({ map: "guardian", mode: "slayer", score: [{ team: "red", players: 1, score: 3 }, { team: "blue", players: 1, score: 5 }] });
    expect(s.players[0].address).toBe("███.███.███.███");
    expect(pluginSnapshot({}, players, true).players[1].address).toBe("203.0.113.10");
  });
  it("gives a plug-in its RCON replies, with addresses only when granted", () => {
    const x = { server: "A", command: "status", args: [] }, r = { ok: true, text: "ip 203.0.113.9", data: { address: "203.0.113.9" } };
    expect(pluginReply(x, r, false)).toEqual({ ...x, ok: true, text: "ip ███.███.███.███", data: { address: "███.███.███.███" } });
    expect(pluginReply(x, r, true).data.address).toBe("203.0.113.9");
    expect(pluginReply(x, { ok: null }, false)).toMatchObject({ ok: null, text: "", data: null });
  });
  it("describes admin and reload events", () => {
    const d = (ev: Record<string, unknown>) => plain(describeEvent(ev));
    expect(d({ event: "admin_added", name: "Bravo", id: "76561198000000001", by: "RCON" })).toBe("ADMIN  Bravo  made admin  by RCON");
    expect(d({ event: "admin_removed", id: "0123456789abcdef0123456789abcdef" })).toBe("ADMIN  01234567…  no longer admin");
    expect(d({ event: "control", action: "reload", ok: true, text: "max_players 16 -> 12" })).toBe("RELOAD  max_players 16 -> 12");
    expect(d({ event: "control", action: "reload", ok: false, text: "dedicated.toml: line 4" })).toBe("RELOAD FAILED  dedicated.toml: line 4");
    expect(d({ event: "control", action: "reload_applied", ok: true, text: "playlist" })).toBe("RELOAD APPLIED  playlist");
    expect(d({ event: "control", action: "kill_feed", ok: true, text: "x" }).startsWith("CONTROL  x")).toBe(true);
  });

  it("describes a kill with its medals", () => {
    const l = describeEvent({ event: "kill", killer: "A", victim: "B", weapon: "shotgun", _medals: ["DOUBLE KILL"] });
    expect(plain(l)).toBe("A ✕ B  [shotgun]   DOUBLE KILL ");
    expect(reactionWords({ event: "join", name: "Kestrel" })).toBe("Kestrel joined");
  });
  it("leaves free-for-alls without teams", () => {
    expect(sides([{ team: "red", score: 1 }, { team: "blue", score: 2 }, { team: "green", score: 0 }])).toEqual([]);
    expect(sides([{ team: 0, score: 3 }, { team: 0, score: 1 }, { team: "blue", score: 2 }])).toEqual([["red", 2, 4], ["blue", 1, 2]]);
  });
});

describe("forge", () => {
  it("reads compatibility specs", () => {
    expect(compatible(">=0.9.5", "0.9.7-demo")).toBe(true);
    expect(compatible("0.9.6", "0.9.7")).toBe(false);
    expect(compatible("<=0.9", "0.9.7")).toBe(true);
    expect(compatible({ min: "0.9.0", max: "0.9" }, "0.9.7")).toBe(true);
    expect(compatible(["0.8.x", "0.9.x"], "0.9.7")).toBe(true);
    expect(compatible(undefined, "0.9.7")).toBe(null);
  });
  it("never picks a withdrawn version", () => {
    const x = { latest_version: { id: "v2" }, versions: [{ id: "v2", status: "withdrawn", created_at: "2026-02-01" }, { id: "v1", created_at: "2026-01-01" }] };
    expect(latestOf(x)?.id).toBe("v1");
    expect(votesOf({ upvote_count: 3, downvote_count: 1 })).toEqual([3, 1, 4]);
    expect([...refsOf({ title: "Guardian Rebuilt", files: [{ path: "maps/guardian_rebuilt.map" }] })]).toEqual(["guardian_rebuilt"]);
  });
});

describe("the Superintendent", () => {
  it("reacts as an admin would, and a lesser event doesn't cut a greater one short", () => {
    let t = 100;
    const s = new Superintendent(true, () => t);
    expect(classify({ event: "chat", channel: "all", text: "admin pls" })).toBe("call");
    expect(classify({ event: "chat", channel: "all", text: "gg" })).toBe(null);
    expect(s.react({ event: "chat", text: "is there a mod online" }, "x")?.mood).toBe("ALARMED");
    t += 1;
    expect(s.react({ event: "join" })).toBe(null);
    t += 10;
    expect(s.lookAt(t)[0].mood).toBe("WATCHING");
  });
  it("blinks on a schedule of the clock alone", () => {
    const s = new Superintendent(true, () => 0);
    const closed = Array.from({ length: 1100 }, (_, i) => s.blinking(i / 100)).filter((b) => b > 0.9).length;
    expect(closed).toBeGreaterThan(0);
    expect(new Superintendent(false).blinking(1)).toBe(0);
  });
  it("draws a disc with two eyes at any size, crisp at the edge", () => {
    expect(faceClass(BASE, 24 - 8.6 + 2.5, 24)).toBe("w");
    expect(faceClass(BASE, 0, 0)).toBe(null);
    for (const n of [24, 64, 128]) {
      const p = face(BASE, n);
      expect(p.w).toBe(n);
      expect(p.alpha(0, 0)).toBe(0);
      expect(p.alpha(n >> 1, n - 3)).toBeGreaterThan(200);
    }
  });
});

describe("art", () => {
  it("smooths a traced grid without inventing colours", () => {
    const pic = smooth(["0330", "3333", "3333", "0330"], tones({ "3": "#D9A441" }), 40);
    expect(pic.w).toBe(40);
    for (let i = 0; i < pic.data.length; i += 4) if (pic.data[i + 3] === 255) expect([pic.data[i], pic.data[i + 1], pic.data[i + 2]]).toEqual([0xd9, 0xa4, 0x41]);
  });
  it("matches the terminal's noise and decrypt", () => {
    expect(noise(3, 7)).toBe(0.06072395155206323); // the Python console's own values: the same pixels dissolve the same way
    expect(noise(-5, 2)).toBe(0.12622592388652265);
    expect(noise(123456, 99)).toBe(0.6629038583487272);
    expect(decrypt("SECTION THREE", 1)).toBe("SECTION THREE");
    expect(decrypt("SECTION THREE", 0).length).toBe(13);
  });
});

describe("frame rate", () => {
  it("grades a rate and scales the chart to the longest frame", () => {
    expect([60, 54, 53, 40, 39].map(level)).toEqual(["ok", "ok", "warn", "warn", "crit"]);
    expect(scaleTop([{ fps: 60, longest: null }])).toBe(50);
    expect(scaleTop([{ fps: 30, longest: 125 }])).toBe(125);
    expect(scaleTop([{ fps: 30, longest: 9000 }])).toBe(500);
  });
  it("says what's happening in words", () => {
    const hist = [{ fps: 30, players: 64 }, { fps: 30, players: 80 }, { fps: 60, players: 20 }, { fps: null, players: null }];
    expect(advice(undefined, [])[0]).toMatch(/Not measured/);
    expect(advice({ behind: false, slow_minutes: 0 }, hist)[1]).toBe("Nothing to do.");
    expect(advice({ behind: true, fps: 29.4, slow_minutes: 2 }, hist)[0]).toMatch(/29 of 60 .* as few as 64 players/);
    expect(advice({ behind: false, slow_minutes: 1, worst: 41 }, hist)[0]).toMatch(/dipped to 41/);
  });
});

describe("player search", () => {
  const P = (name: string, o: Partial<Found> = {}): Found => ({ index: 0, key: name, name, tag: "", clan: "", team: "red", id: "1", server: "Alpha", node: "10.0.0.1", port: "2302", ...o });
  const all = [P("Mantis", { tag: "MNT", clan: "ONI", id: "7" }), P("Mantas", { id: "8", server: "Bravo", team: "blue" }), P("Zed", { id: "9" })];
  const names = (q: string) => rankedPlayers(all, q).map((r) => r.player.name);
  it("keeps quoted phrases whole and survives an unclosed quote", () => {
    expect(filterTerms('"Big Dog" clan:oni')).toEqual(["big dog", "clan:oni"]);
    expect(filterTerms('"Big Dog')).toEqual(["big", "dog"]);
  });
  it("finds a near miss of 4+ letters, after the exact one", () => {
    expect(nameDistance("mantiz", "mantis")).toBe(1);
    expect(nameDistance("man", "mat")).toBeNull();
    expect(names("mantis")).toEqual(["Mantis", "Mantas"]);
    expect(rankedPlayers(all, "mantis").map((r) => r.score)).toEqual([0, 1]);
  });
  it("needs every term to match one player; tags and IDs are never fuzzy", () => {
    expect(names("mantis server:bravo")).toEqual(["Mantas"]);
    expect(names("@mnt")).toEqual(["Mantis"]);
    expect(names("@mn")).toEqual([]);
    expect(names("id:7 clan:oni")).toEqual(["Mantis"]);
    expect(names("nothing:here")).toEqual([]);
  });
});

describe("findings", () => {
  const nobody = () => undefined;
  const at = () => { const c = { t: 1000 }; return [c, new Findings(() => c.t)] as const; };

  it("keeps real requests and rejects chat about mods, cheat codes and the kick button", () => {
    const table: [string, string | null][] = [
      ["need an admin", "admin_help"], ["any mods online?", "admin_help"], ["looking for help", "admin_help"], ["can someone help me?", "admin_help"],
      ["help please", "admin_help"], ["please kick Red", "moderation_request"], ["Can someone ban Red for team killing", "moderation_request"],
      ["Red needs to be kicked", "moderation_request"], ["Red is cheating", "player_report"], ["Red is using an aimbot", "player_report"],
      ["admin? Red is hacking", "player_report"], ["did halo 3 had these tf2 fan mod modes on release", null],
      ["that should be a no brainer, that's the whole point of this mod", null], ["How do I kick someone?", null], ["called a vote to kick Red", null],
      ["don't kick Red", null], ["Red is not cheating", null], ["I installed a mod", null], ["this map has cheat codes", null],
      ["help me capture the flag", null], ["kick button is broken", null],
    ];
    for (const [text, kind] of table) expect([text, chatRequest(text)?.kind ?? null]).toEqual([text, kind]);
  });

  it("counts repeated kick votes and a back-and-forth, and ignores results and chat", () => {
    const [c, f] = at();
    const vote = (a: string, b: string, server = "s") => { c.t += 10; return f.ingest(server, { event: "vote", name: a, text: "called a vote to kick " + b }); };
    expect(vote("A", "B")).toEqual([]);
    expect(vote("B", "A")).toEqual([]);
    expect(vote("A", "B")).toEqual([]);
    expect(vote("B", "A")[0].kind).toBe("vote_fight");
    expect(vote("C", "B")[0].kind).toBe("repeated_votes");
    expect(vote("C", "B")).toEqual([]); // cooling down
    expect(vote("A", "B", "other")).toEqual([]);
    expect(f.ingest("s", { event: "chat", name: "A", text: "called a vote to kick B" })).toEqual([]);
    expect(f.ingest("s", { event: "vote", name: "A", text: "vote to kick passed (4 to 0)" })).toEqual([]);
    c.t += 301;
    expect(vote("A", "B")).toEqual([]);
  });

  it("flags a kill burst as a highlight, not proof; stale, repeated, suicide and betrayal kills count for nothing", () => {
    const [c, f] = at();
    let last: ReturnType<Findings["ingest"]> = [];
    for (let i = 0; i < 5; i++) {
      c.t += 1;
      const e = { event: "kill", time: c.t, killer: "A", victim: String(i) };
      last = f.ingest("s", e);
      expect(f.ingest("s", e)).toEqual([]);
    }
    expect(last[0].kind).toBe("kill_burst");
    expect(last[0].level).toBe("highlight");
    expect(last[0].evidence).toContain("not evidence");
    f.reset("s");
    expect(f.ingest("s", { event: "kill", killer: "A", victim: "B" })).toEqual([]);
    expect(f.ingest("s", { event: "kill", time: c.t - 31, killer: "A", victim: "C" })).toEqual([]);
    for (let i = 0; i < 20; i++) {
      expect(f.ingest("s", { event: "kill", killer: "A", victim: "A" })).toEqual([]);
      expect(f.ingest("s", { event: "kill", killer: "A", victim: "B", betrayal: true })).toEqual([]);
    }
  });

  it("reviews an extreme rate, takes Perfection only from an explicit medal, and forgets after an hour", () => {
    const [c, f] = at();
    for (let i = 0; i < 12; i++) {
      c.t += 0.1;
      f.ingest("s", { event: "kill", time: c.t, killer: "A", victim: String(i) });
    }
    expect(f.recent()[0].kind).toBe("unusual_rate");
    expect(f.recent()[0].level).toBe("review");
    expect(f.recent().some((r) => r.kind === "perfection")).toBe(false);
    const report = f.ingest("s", { event: "medal", name: "A", medal: "Perfection" })[0];
    expect([report.kind, report.title]).toEqual(["perfection", expect.stringContaining("Server reported")]);
    const before = f.recent();
    f.acknowledge();
    expect(f.recent()).toEqual(before);
    expect(before.every((r) => r.at <= f.acknowledgedAt)).toBe(true);
    c.t += 3601;
    expect(f.recent()).toEqual([]);
  });

  it("skips kill heuristics in fast modes but still reviews, flags and counts votes", () => {
    for (const context of [{ mode: "Infection" }, { mode: "Slayer", name: "Big Team SWAT" }, { mode: "Slayer", tags: ["swat"] }, { name: "Mega Fat Kid" }]) {
      const [c, f] = at();
      for (let i = 0; i < 15; i++) {
        c.t += 0.1;
        expect(f.ingest("s", { event: "kill", time: c.t, killer: "A", victim: String(i) }, nobody, context)).toEqual([]);
      }
      expect(f.ingest("s", { event: "chat", name: "B", text: "need an admin" }, nobody, context)[0].kind).toBe("admin_help");
      expect(f.ingest("s", { event: "cheat", name: "A", text: "native reason" }, nobody, context)[0].kind).toBe("native_flag");
      expect(f.ingest("s", { event: "medal", name: "A", medal: "Perfection" }, nobody, context)[0].kind).toBe("perfection");
      let result: ReturnType<Findings["ingest"]> = [];
      for (let i = 0; i < 3; i++) {
        c.t += 5;
        result = f.ingest("s", { event: "vote", name: "B", text: "called a vote to kick A" }, nobody, context);
      }
      expect(result[0].kind).toBe("repeated_votes");
    }
  });

  it("repeats nothing across a phase or reconnect, but a new target, server or time still alerts", () => {
    const [c, f] = at();
    const chat = { event: "chat", id: "reporter-id", name: "Reporter", text: "please kick Red" };
    const first = f.ingest("s", chat)[0];
    expect(first.title).toContain("Reporter");
    expect(first.title).not.toContain("reporter-id");
    expect(first.subjects[0].name).toBe("red");
    f.ingest("s", { event: "phase" });
    f.reset("s");
    c.t += 65;
    expect(f.ingest("s", { ...chat, id: "another", name: "Another" })).toEqual([]);
    expect(f.ingest("s", { ...chat, text: "please kick Blue" })).not.toEqual([]);
    expect(f.ingest("other-server", chat)).not.toEqual([]);
    c.t += 121;
    expect(f.ingest("s", chat)).not.toEqual([]);
    expect(f.ingest("s", { ...chat, channel: "server" })).toEqual([]);
  });

  it("names the reported player from the roster, never a guessed slot or a duplicate name, and carries nothing private", () => {
    const [, f] = at();
    const [a, b, d] = ["a", "b", "c"].map((x) => x.repeat(64));
    const roster = publicRoster([{ id: a, engine_id: 1, name: "Reporter", ip: "private" }, { id: b, engine_id: 2, name: "Red", device: "private" }]);
    const row = f.ingest("s", { event: "chat", id: a, name: "Reporter", text: "admin? Red is hacking" }, nobody, {}, roster)[0];
    expect(row.subjects).toEqual([{ id: b, name: "Red" }]);
    expect(row.reporter).toEqual({ id: a, name: "Reporter" });
    expect(JSON.stringify(row)).not.toContain("private");
    expect(f.ingest("s", { event: "cheat", id: 2 }, nobody, {}, roster)[0].subjects[0].id).toBe(b);
    roster.push({ id: d, engine_id: "3", name: "Red" });
    expect(f.ingest("other", { event: "chat", name: "Reporter", text: "please ban Red" }, nobody, {}, roster)[0].subjects[0].id).toBe("");
  });

  it("drops a half-counted kill window when the mode changes, and cools highlights down per server and per player", () => {
    const [c, f] = at();
    const burst = (actor: string, mode: string, count = 5) => {
      const out: unknown[] = [];
      for (let i = 0; i < count; i++) {
        c.t += 1;
        out.push(...f.ingest("s", { event: "kill", killer: actor, victim: String(i) }, nobody, { mode }));
      }
      return out as { kind: string }[];
    };
    expect(burst("A", "Slayer", 4)).toEqual([]);
    expect(burst("A", "CTF", 1)).toEqual([]);
    expect(burst("A", "CTF", 4)[0].kind).toBe("kill_burst");
    expect(burst("B", "CTF")).toEqual([]);
    c.t += 61;
    expect(burst("A", "CTF")).toEqual([]);
    expect(burst("B", "CTF")).not.toEqual([]);
  });
});

describe("content browser", () => {
  const MAPS = [{ kind: "map", name: "Valhalla", reference: "riverworld" }, { kind: "map", name: "Guardian", reference: "guardian" },
    { kind: "map", name: "Hang 'Em High", reference: "hang_em_high" }, { kind: "variant", name: "Guardian", reference: "default_guardian_012.mvar" },
    { kind: "variant", name: "MLG Guardian", reference: "MLG Guardian" }, { kind: "variant", name: "Pit Stop", reference: "Maps/Forge/rf-3c1d0a9e44b2f7d81e02.json" },
    { kind: "variant", name: "Club Dead", reference: "Maps/Club Dead.mvar" }];
  const MODES = [{ kind: "mode", name: "Slayer", reference: "Slayer" }, { kind: "variant", name: "Team Slayer", reference: "2v2_team_slayer_010.bin" },
    { kind: "variant", name: "UFM | ASSAULT", reference: "Game Modes/Forge/rf-d70540eb57f22df48390.json" },
    { kind: "variant", name: "UFM | ASSAULT", reference: "Game Modes/Forge/UFM Encompass/UFM _ASSAULT.json" },
    { kind: "variant", name: "FIESTA SMALL", reference: "FIESTA SMALL" }];
  const INSTALLED = {
    lst_pit: { listing_id: "lst_pit", title: "Pit Stop", kind: "map", author: "Ana", version: "2", installed_at: "2026-10-12T10:00:00Z", files: [{ path: "Maps/Forge/rf-3c1d0a9e44b2f7d81e02.json" }] },
    lst_ufm: { listing_id: "lst_ufm", title: "UFM | ASSAULT", kind: "gametype", author: "Zed", version: "1.2", installed_at: "2026-10-13T10:00:00Z", files: [{ path: "Game Modes/Forge/rf-d70540eb57f22df48390.json" }] },
  };
  const groups = (entries: any[], what: "maps" | "modes", ...rest: [string?, string?, boolean?]) =>
    Object.fromEntries(arrange(itemsFor(entries, INSTALLED), what, ...rest).map(([k, , rows]) => [k, rows.map((i) => [i.name, i.folder])]));

  it("puts every map in the group people look in", () => {
    expect(groups(MAPS, "maps")).toEqual({
      stock: [["Guardian", ""], ["Valhalla", ""]], mods: [["Hang 'Em High", ""]], forge: [["Pit Stop", ""]], folder: [["Club Dead", ""]],
      builtin: [["Guardian", ""]], saved: [["MLG Guardian", ""]],
    });
  });

  it("matches Forge files by their own file and keeps their folder", () => {
    const items = new Map(itemsFor(MODES, INSTALLED).map((i) => [`${i.name}|${i.folder}`, i]));
    const ours = items.get("UFM | ASSAULT|")!;
    expect([ours.group, ours.detail, !!ours.installedAt]).toEqual(["forge", "v1.2 · Zed", true]);
    const theirs = items.get("UFM | ASSAULT|UFM Encompass")!; // the same title in another folder: an upload, not our install
    expect([theirs.group, theirs.forge, theirs.detail]).toEqual(["forge", null, "UFM _ASSAULT.json"]);
    expect([items.get("Team Slayer|")!.group, items.get("FIESTA SMALL|")!.group]).toEqual(["builtin", "saved"]);
  });

  it("filters on every word, over authors and folders too", () => {
    expect(groups(MODES, "modes", "name", "ufm encompass")).toEqual({ forge: [["UFM | ASSAULT", "UFM Encompass"]] });
    expect(groups(MODES, "modes", "name", "zed")).toEqual({ forge: [["UFM | ASSAULT", ""]] });
    expect(groups(MAPS, "maps", "name", "nothing like it")).toEqual({});
  });

  it("turns orders round and keeps the unknowns last", () => {
    const items = itemsFor(MODES, INSTALLED);
    expect(ordered(items, "name").slice(0, 2).map((i) => i.name)).toEqual(["FIESTA SMALL", "Slayer"]);
    expect(ordered(items, "name", true)[0].name).toBe("UFM | ASSAULT");
    expect(ordered(items, "recent")[0].installedAt).toBe("2026-10-13T10:00:00Z");
    const oldest = ordered([...items, ...itemsFor(MAPS, INSTALLED)], "recent", true);
    expect([oldest[0].installedAt, oldest[2].installedAt]).toEqual(["2026-10-12T10:00:00Z", ""]);
  });

  it("offers from the catalog only what is not here", () => {
    const listings = [{ id: "lst_pit", title: "Pit Stop", kind: "map", upvote_count: 9 }, { id: "a", title: "Alpha", kind: "map", upvote_count: 1, downvote_count: 4 },
      { id: "b", title: "Bravo", kind: "map", upvote_count: 30, download_count: 7 }, { id: "c", title: "Charlie", kind: "gametype" },
      { id: "d", title: "Delta", kind: "map", status: "withdrawn" }];
    const got = catalogItems(listings, "map", new Set(Object.keys(INSTALLED)));
    const names = (rows: typeof got) => rows.map((i) => i.name);
    expect(names(got)).toEqual(["Alpha", "Bravo"]);
    expect(names(ordered(got, "rated"))).toEqual(["Bravo", "Alpha"]);
    expect(names(ordered(got, "rated", true))).toEqual(["Alpha", "Bravo"]);
    expect(names(ordered(got, "downloads"))).toEqual(["Bravo", "Alpha"]);
  });
});

describe("playlists", () => {
  const good = { name: "Team Mix", games: [{ map: "Guardian", game: "Team Slayer", weight: 3 }, { map: "Valhalla", game: "Slayer", weight: 1 }, { map: "The Pit", game: "Slayer" }] };

  it("are checked in the server's own shape", () => {
    expect(checkPlaylist(good)).toEqual({ version: 2, name: "Team Mix", vote_seconds: 20,
      games: [{ map: "Guardian", game: "Team Slayer", weight: 3 }, { map: "Valhalla", game: "Slayer" }, { map: "The Pit", game: "Slayer" }] });
    const many = (n: number) => Array.from({ length: n }, (_, i) => ({ map: `m${i}`, game: "b" }));
    for (const [bad, words] of [[{ name: "", games: good.games }, "name"], [{ name: "x", games: [] }, "at least 3"],
      [{ name: "x", games: good.games.slice(0, 2) }, "at least 3 games \\(it has 2\\)"], [{ name: "x", games: [{ map: "Guardian" }, { map: "a" }, { map: "b" }] }, "both a map and a game type"],
      [{ name: "x", games: many(3).map((g) => ({ ...g, weight: 0 })) }, "weight"], [{ name: "x", games: many(129) }, "at most 128"],
      [{ name: "x", games: good.games, shuffle: true }, "Unknown playlist setting"], [{ name: "x", games: many(3).map((g) => ({ ...g, script: 1 })) }, "unknown settings"],
      [{ name: "x", vote_seconds: 4, games: good.games }, "5 to 120"], [{ name: "x", games: [...good.games, good.games[0]] }, "twice"]] as [unknown, string][])
      expect(() => checkPlaylist(bad)).toThrow(new RegExp(words));
  });

  it("word a game the way the server lists it", () => {
    expect(gameLabel({ map: "Guardian", game: "Team Slayer", weight: 3 })).toBe("Team Slayer on Guardian  ×3");
    expect(gameLabel({ map: "a", game: "b", name: "Mine" })).toBe("Mine");
    expect(slug(" Friday Night! ")).toBe("friday-night");
  });
});

describe("hot reload", () => {
  it("is 0.9.18 and later; older, unknown and demo servers keep the restart", () => {
    for (const v of ["0.9.18", "v0.9.18", "0.9.19-rc1", "0.10.0", "1.0.0"]) expect(hotReloads(v), v).toBe(true);
    for (const v of ["0.9.17", "0.9.7-demo", "0.8.30", "", "?", undefined, null]) expect(hotReloads(v), String(v)).toBe(false);
  });
});

describe("server version", () => {
  it("is behind only when both versions are known and the server's is older", () => {
    expect(behind("0.9.17", "0.9.18")).toBe("0.9.18");
    expect(behind("v0.9.9", "v0.9.18")).toBe("0.9.18"); // numbers, not strings
    expect(behind("0.9.18", "0.10.0")).toBe("0.10.0");
    for (const [have, latest] of [["0.9.18", "0.9.18"], ["0.9.19-rc1", "0.9.18"], ["?", "0.9.18"], [undefined, "0.9.18"], ["0.9.17", ""]])
      expect(behind(have, latest), `${have} vs ${latest}`).toBe("");
  });

  it("names a mixed fleet once, oldest first, and stays quiet when it agrees", () => {
    const st = (label: string, version: unknown) => ({ label, version });
    expect(mixedVersions([st("F1", "0.9.18"), st("F2", "0.9.17"), st("F3", "0.9.18")])).toBe("0.9.17 on F2 · 0.9.18 on F1, F3");
    expect(mixedVersions([st("F1", "0.9.18"), st("F2", "v0.9.18"), st("F3", "?")])).toBe("");
    expect(mixedVersions([])).toBe("");
  });
});

describe("release notes", () => {
  it("reads markdown as plain lines", () => {
    expect(notesText("## What's new\r\n\n\n- **Fast** boot, see [docs](https://x)\n  * `--intro` off\n")).toBe("WHAT'S NEW\n\n• Fast boot, see docs\n  • --intro off");
  });
});

describe("kicked list", () => {
  it("adds a kick, takes a refused join's address, and drops them on a pardon", () => {
    const kicks = new Map<string, Kick>(), t = 1_000_000;
    expect(noteKick(kicks, { event: "kick", name: "Bravo", id: "76561198000000001", by: "RCON", reason: "spawn camping", seconds: 600 }, t)).toBe(true);
    expect(noteKick(kicks, { event: "kick", name: "Slow", id: "x", by: "Server", seconds: 0 }, t)).toBe(false); // load_timeout: not kept out
    noteKick(kicks, { event: "refused", name: "Bravo", id: "76561198000000001", ip: "203.0.113.4", reason: "You were kicked: 9 minutes left" }, t);
    const k = kicks.get("76561198000000001")!;
    expect([k.ip, kickLeft(k, t + 61_000), kickLeft(k, t + 600_000), pardonTarget(k)]).toEqual(["203.0.113.4", "9m", "over", "76561198000000001"]);
    // turned away for a kick from before we connected: listed, time unknown; a ban's refusal isn't
    noteKick(kicks, { event: "refused", name: "Charlie", id: "c1", ip: "198.51.100.7", reason: "Kicked by an admin" }, t);
    expect(noteKick(kicks, { event: "refused", name: "Delta", id: "d1", reason: "You are banned" }, t)).toBe(false);
    expect(kickLeft(kicks.get("c1")!)).toBe("?");
    expect(noteKick(kicks, { event: "pardon", target: "198.51.100.7", name: ["Charlie"], by: "RCON" })).toBe(true);
    expect(noteKick(kicks, { event: "pardon", target: "Bravo", name: "Bravo", by: "RCON" })).toBe(true);
    expect(kicks.size).toBe(0);
  });

  it("shows a pardon as one line", () => {
    expect(plain(describeEvent({ event: "pardon", target: "76561198000000001", name: ["Bravo", "Bravo2"], by: "Alice (RCON)" })))
      .toBe("PARDON  Bravo, Bravo2  may rejoin  by Alice (RCON)");
  });
});

describe("plug-ins", () => {
  it("types an action's params as the contract says, and a bad number sends nothing", () => {
    const ps = [{ name: "n", label: "N", type: "number" }, { name: "b", type: "bool" }, { name: "t", type: "duration" }, { name: "c", type: "choice" }];
    expect(typedParams(ps, { p0: " 2.5 ", p1: "true", p2: "10m", p3: "warm" })).toEqual({ n: 2.5, b: true, t: "10m", c: "warm" });
    expect(typedParams(ps, { p0: "x" })).toBe("N must be a number: nothing was sent.");
    expect(typedParams(ps, { p0: "" })).toBe("N must be a number: nothing was sent.");
  });
});

describe("vpn lists (0.9.22)", () => {
  const now = 1_800_000_000;
  const v = { lists: { refreshed_at: now - 60, sources: [
    { list: "fresh", ranges: 100, asns: 0, fetched_at: now - 600, problem: null },
    { list: "old", ranges: 5, asns: 2, fetched_at: now - 8 * 86400, problem: null },
    { list: "broken", ranges: 0, fetched_at: null, problem: "HTTP 404" }],
    table: { table: "ip2asn", rows: 10, fetched_at: new Date((now - 3600) * 1000).toISOString(), blocks: 4 } } };
  it("flags a list older than a week, never fetched, or with a problem", () => {
    const l = vpnLists(v, now);
    expect(l.map((x) => [x.name, x.stale, x.problem])).toEqual([["fresh", false, ""], ["old", true, ""], ["broken", true, "HTTP 404"], ["ASN table (ip2asn)", false, ""]]);
    expect(l[0]).toMatchObject({ size: "100 ranges · 0 ASNs", age: "10m 00s" });
    expect(l[1].age).toBe("8d 0h");
    expect(l[2].age).toBe("never");
  });
  it("is empty before 0.9.22 or before the first refresh", () => {
    expect(vpnLists({ block_vpn: true, ranges: 3 })).toEqual([]);
    expect(vpnLists({ lists: null })).toEqual([]);
  });
  it("CHECK IP names the matching list and a fail-closed refusal", () => {
    expect(vpnCheck("refused", { listed: true, list: "x4bnet", refused: true })).toBe("refused · matched x4bnet");
    expect(vpnCheck("refused", { listed: false, refused: true, fail_closed: true })).toContain("fail_closed");
    expect(vpnCheck("fine", null)).toBe("fine");
  });
});
