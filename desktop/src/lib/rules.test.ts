import { describe, expect, it } from "vitest";
import { Engine, MAX_PRIVATE, MatchEvents, STARTERS, VIP_GUARD, cronDue, privateCommands, registerCommands, cronFields, chatOk, describe as describeRule, fill, plan, relay, sample, telegramWhy, validate, zoned } from "./rules";

const rule = (o: Record<string, any>) => validate({ name: "t", ...o });
const lobby = (e: Engine, key = "s1") => {
  e.connection(key, true, "Server One");
  e.status(key, { players: 4, max_players: 16, map: "Guardian", mode: "Slayer", phase: "playing" });
  e.players(key, [{ engine_id: 1, name: "Ann", player_id: "a".repeat(64), team: "red", admin: true }, { engine_id: 2, name: "Bob", player_id: "b".repeat(64), team: "blue" }]);
};
const kill = (e: Engine, killer: number, victim: number, extra = {}) => e.event("s1", { event: "kill", killer, victim, weapon: "Sniper Rifle", ...extra });

describe("rules", () => {
  it("every starter validates and plans", () => {
    for (const s of STARTERS) {
      const r = validate({ ...s, then: s.then.map((a: any) => (a.type === "discord" ? { ...a, webhook: "hook" } : a)) });
      const e = new Engine([r]);
      lobby(e);
      expect(plan(e, sample(e, r, "s1")).length).toBeGreaterThan(0);
    }
  });

  it("starters are plentiful and uniquely named (the template list picks by name)", () => {
    expect(STARTERS.length).toBeGreaterThanOrEqual(50);
    expect(new Set(STARTERS.map((s) => s.name)).size).toBe(STARTERS.length);
  });

  it("a Discord step takes several webhooks: one post each, no repeats, at most 10", () => {
    const post = (webhook: string) => rule({ when: { type: "join" }, then: [{ type: "discord", webhook, message: "{player} joined" }] });
    const r = post(" main, staff,main ");
    expect(r.then[0].webhook).toBe("main,staff");
    const e = new Engine([r]);
    lobby(e);
    expect(plan(e, sample(e, r, "s1")).map((s) => [s.kind, s.webhook, s.text])).toEqual([["discord", "main", "ExamplePlayer joined"], ["discord", "staff", "ExamplePlayer joined"]]);
    expect(relay(STARTERS.find((s) => s.name === "Lag alert")!, "main,staff").then[1].webhook).toBe("main,staff");
    expect(() => post(Array.from({ length: 11 }, (_, i) => `h${i}`).join(","))).toThrow(/at most 10/);
    expect(() => post(" , ")).toThrow(/choose a Discord webhook/);
  });

  it("rejects bad rules", () => {
    expect(() => rule({ when: { type: "nope" }, then: [{ type: "cancel_vote" }] })).toThrow(/Unknown WHEN/);
    expect(() => rule({ when: { type: "join" }, then: [{ type: "say", to: "this", message: "hi {nobody}" }] })).toThrow(/unknown placeholder/);
    expect(() => rule({ when: { type: "join" }, then: [] })).toThrow(/1 to 8/);
    expect(() => rule({ when: { type: "at", cron: "61 * * * *" }, then: [{ type: "cancel_vote" }] })).toThrow();
  });

  it("a Telegram step needs a chat, plans a post, and reads back in words", () => {
    expect(() => rule({ when: { type: "join" }, then: [{ type: "telegram", message: "hi" }] })).toThrow(/choose a Telegram chat/);
    expect(() => rule({ when: { type: "join" }, then: [{ type: "telegram", chat: "alerts", message: "hi {nobody}" }] })).toThrow(/unknown placeholder/);
    const r = rule({ when: { type: "join" }, then: [{ type: "telegram", chat: "alerts", message: "{player} joined {server}" }] });
    const e = new Engine([r]);
    lobby(e);
    expect(plan(e, sample(e, r, "s1"))).toEqual([{ kind: "telegram", chat: "alerts", text: "ExamplePlayer joined Server One" }]);
    expect(describeRule(r)).toContain('THEN  Post to Telegram: alerts, "{player} joined {server}"');
  });

  it("a template relays to Telegram when no Discord webhook is set up, and says so to the player", () => {
    let posts = 0;
    for (const s of STARTERS) {
      const viaTelegram = relay(s, undefined, "alerts"), viaDiscord = relay(s, "hook", "alerts");
      expect(JSON.stringify(viaTelegram)).not.toMatch(/Discord|"webhook"/);
      expect(viaTelegram.then.filter((a: any) => a.type === "telegram").length).toBe(s.then.filter((a: any) => a.type === "discord").length);
      expect(JSON.stringify(viaDiscord)).not.toMatch(/"telegram"/); // a webhook wins when both exist
      if (!s.then.some((a: any) => a.type === "discord")) expect(viaTelegram).toBe(s);
      else posts++;
      expect(() => validate(viaTelegram)).not.toThrow();
    }
    expect(posts).toBeGreaterThanOrEqual(3);
    const admin = relay(STARTERS.find((s) => s.name === "!admin with nobody on")!, undefined, "alerts");
    expect(admin.then).toEqual([{ type: "telegram", chat: "alerts", message: "{player} on {server} asked for an admin: {args}" }, { type: "tell_player", message: "No admin is online; we've pinged Telegram." }]);
  });

  it("explains a Telegram status in words", () => {
    expect(telegramWhy(200)).toBe("done");
    expect(telegramWhy(0)).toBe("Telegram could not be reached");
    expect(telegramWhy(403)).toMatch(/\(403\): the bot was removed/);
    expect(telegramWhy(418)).toBe("Telegram refused it (418)");
    expect(telegramWhy(503)).toMatch(/^Telegram failed \(503\)/);
  });

  it("a player can't turn their name or !admin text into a Telegram mention, and the rule's own words are untouched", () => {
    const e = new Engine([]);
    lobby(e);
    const make = (then: object) => rule({ when: { type: "join" }, then: [then] });
    const f = (r: any) => { const x = sample(e, r, "s1"); x.ctx.player = "@admin_bob"; x.ctx.args = "hey @all and @ops"; return x; };
    const tg = make({ type: "telegram", chat: "alerts", message: "{player} says {args} (cc @ops)" });
    expect(plan(e, f(tg))[0].text).toBe("\uFF20admin_bob says hey \uFF20all and \uFF20ops (cc @ops)");
    expect(plan(e, f(tg))[0].text).not.toMatch(/(?<!cc )@/);
    // Discord blocks pings itself (allowed_mentions), so its text is as filled; in-game chat has no mentions
    expect(plan(e, f(make({ type: "discord", webhook: "h", message: "{player} says {args}" })))[0].text).toBe("@admin_bob says hey @all and @ops");
  });

  it("a chat is a number or a public @channel, as the Rust side holds it", () => {
    // a chat is trimmed before it is checked, as the Rust side does when it saves one
    for (const ok of ["-1001234567890", "123456789", "@oni_alerts", "123456789012345678", " -100 ", "@oni_alerts\n"]) expect(chatOk(ok), ok).toBe(true);
    for (const bad of ["", "-", "12a", "@a", "@1abcdef", "oni_alerts", "@oni alerts", "-100 1", "@oni_alerts/../x", "1234567890123456789", "-1234567890123456789", 5, null]) expect(chatOk(bad), String(bad)).toBe(false);
  });

  it("fills placeholders on one line", () => {
    expect(fill("{player} did {nope}ok", { player: "A\u0007\nB" })).toBe("AB did ok");
  });

  it("spree and multikill fire from the kill feed, once per count", () => {
    const e = new Engine([rule({ when: { type: "spree", counts: "2,3" }, then: [{ type: "say", to: "this", message: "{player} {medal}" }] }),
      rule({ when: { type: "multikill", min: 2 }, then: [{ type: "say", to: "this", message: "{medal}" }] })], {}, () => 100);
    lobby(e);
    expect(kill(e, 1, 2)).toHaveLength(0);
    const f = kill(e, 1, 2); // second kill inside the chain window
    expect(f.map((x) => x.rule.when.type).sort()).toEqual(["multikill", "spree"]);
    expect(plan(e, f.find((x) => x.rule.when.type === "spree")!)[0].args).toEqual(["say", "Ann Killing Spree"].map((x, i) => (i ? "Ann 2-kill spree" : x)));
  });

  it("a vote cancels for a VIP target, and the guard is off without a VIP", () => {
    const e = new Engine([VIP_GUARD()], { ["b".repeat(64)]: { name: "Bob", roles: ["vip"], servers: "all" } });
    lobby(e);
    const f = e.event("s1", { event: "vote", action: "start", vote_type: "kick", target: "Bob", target_id: "b".repeat(64) });
    expect(f).toHaveLength(1);
    expect(plan(e, f[0]).map((s) => s.args![0])).toEqual(["cancelvote", "say"]);
    expect(new Engine([VIP_GUARD()]).event("s1", { event: "vote", action: "start", vote_type: "kick", target: "Bob" })).toHaveLength(0);
  });

  it("cooldown and the hourly limit hold a rule back", () => {
    let t = 0;
    const e = new Engine([rule({ when: { type: "join" }, then: [{ type: "tell_player", message: "hi" }], cooldown: 10, per_hour: 2 })], {}, () => t);
    lobby(e);
    const join = (n: string, id: string) => e.event("s1", { event: "join", name: n, player_id: id }).length;
    expect([join("A", "1"), join("A", "1")]).toEqual([1, 0]); // same player inside the cooldown
    expect(join("B", "2")).toBe(1);
    expect(join("C", "3")).toBe(0); // third firing in an hour
    t = 4000;
    expect(join("C", "3")).toBe(1);
  });

  it("players_above fires on the crossing only", () => {
    const e = new Engine([rule({ when: { type: "players_above", count: 5 }, then: [{ type: "say", to: "this", message: "x" }] })]);
    lobby(e);
    expect(e.status("s1", { players: 5, phase: "playing" })).toHaveLength(1);
    expect(e.status("s1", { players: 6, phase: "playing" })).toHaveLength(0);
  });

  it("frame rate crossings", () => {
    const e = new Engine([rule({ when: { type: "fps_below", fps: 40 }, then: [{ type: "say", to: "this", message: "{fps}" }] }),
      rule({ when: { type: "fps_recovered", fps: 54 }, then: [{ type: "say", to: "this", message: "ok" }] })]);
    lobby(e);
    expect(e.frameRate("s1", 60)).toHaveLength(0);
    expect(e.frameRate("s1", 30)).toHaveLength(1);
    expect(e.frameRate("s1", 20)).toHaveLength(0);
    expect(e.frameRate("s1", 58).map((f) => f.rule.when.type)).toEqual(["fps_recovered"]);
  });

  it("server_down waits, then server_up reports once", () => {
    let t = 0;
    const e = new Engine([rule({ when: { type: "server_down", seconds: 30 }, then: [{ type: "discord", webhook: "h", message: "{down}" }] }),
      rule({ when: { type: "server_up" }, then: [{ type: "discord", webhook: "h", message: "up" }] })], {}, () => t);
    lobby(e);
    e.connection("s1", false);
    t = 10;
    expect(e.tick()).toHaveLength(0);
    t = 40;
    expect(e.tick()).toHaveLength(1);
    t = 50;
    expect(e.tick()).toHaveLength(0);
    expect(e.connection("s1", true)).toHaveLength(1);
  });

  it("ordinary chat never fires, !commands do", () => {
    const e = new Engine([rule({ when: { type: "command", command: "!admin" }, then: [{ type: "tell_player", message: "{args}" }] })]);
    lobby(e);
    expect(e.event("s1", { event: "chat", name: "Ann", text: "need an admin" })).toHaveLength(0);
    const f = e.event("s1", { event: "chat", name: "Ann", text: "!Admin cheater here" });
    expect(f).toHaveLength(1);
    expect(plan(e, f[0])[0].args).toEqual(["tell", "a".repeat(64), "cheater here"]);
  });

  // a 0.10.3 private command, as RCON.md describes the event: word, args, text, the player's ID and role, private
  const priv = (o = {}) => ({ type: "event", event: "command", time: 1, player_id: "b".repeat(64), word: "admin", args: ["cheater", "here"],
    text: "!admin cheater here", role: "moderator", private: true, ...o });

  it("a private command (0.10.3) fires the same !command rule, with its args, the player and the server's role", () => {
    const fresh = () => {
      const e = new Engine([rule({ when: { type: "command", command: "!admin" }, if: [{ type: "player_role", role: "mod" }], then: [{ type: "tell_player", message: "{player}: {args}" }] })]);
      lobby(e);
      return e;
    };
    let e = fresh();
    const f = e.event("s1", priv());
    expect(f).toHaveLength(1);
    expect(plan(e, f[0])[0].args).toEqual(["tell", "b".repeat(64), "Bob: cheater here"]);
    expect(fresh().event("s1", priv({ role: "player" }))).toHaveLength(0); // the role condition reads the event's role
    e = fresh();
    const g = e.event("s1", priv({ word: "/ADMIN", args: "x" })); // a prefix or case doesn't matter
    expect(g).toHaveLength(1);
    expect(plan(e, g[0])[0].args).toEqual(["tell", "b".repeat(64), "Bob: x"]);
    expect(fresh().event("s1", priv({ word: "rules" }))).toHaveLength(0);
    expect(fresh().event("s1", priv({ private: undefined }))).toHaveLength(0); // only the server's private commands
  });

  it("private commands: each enabled !command rule's word for that server, once, at most 16", () => {
    const cmd = (command: string, o = {}) => rule({ name: `${command} rule`, when: { type: "command", command }, then: [{ type: "tell_player", message: "hi" }], ...o });
    const rules = [cmd("!admin"), cmd("!admin", { name: "second" }), cmd("!rules", { enabled: false }), cmd("!kills", { servers: ["s2"] }), rule({ when: { type: "join" }, then: [{ type: "say", to: "this", message: "hi" }] })];
    expect(privateCommands(rules, "s1")).toEqual([["admin", "!admin rule"]]);
    expect(privateCommands(rules, "s2")).toEqual([["admin", "!admin rule"], ["kills", "!kills rule"]]);
    expect(privateCommands(Array.from({ length: 20 }, (_, i) => cmd(`!c${i}`)), "s1")).toHaveLength(MAX_PRIVATE);
    expect(privateCommands(rules, "s1", ["kills", "admin"])).toEqual([["admin", "!admin rule"], ["kills", "!kills"]]); // Studio's, after the rules'
  });

  it("registering private commands: fresh adds all (removing first), then only the difference", async () => {
    const sent: string[] = [];
    const call = async (args: string[]) => (sent.push(args.join(" ")), { ok: args[1] !== "bad", text: "no" });
    const had = new Map<string, string>();
    expect(await registerCommands(call, had, [["admin", "!admin call"], ["bad", "x"]], true)).toEqual(["!bad: no"]);
    expect(sent).toEqual(["remove admin", "add admin !admin call", "remove bad", "add bad x"]);
    expect([...had]).toEqual([["admin", "!admin call"]]);
    sent.length = 0;
    await registerCommands(call, had, [["admin", "!admin call"], ["kills", "!kills"]], false);
    expect(sent).toEqual(["add kills !kills"]);
    sent.length = 0;
    await registerCommands(call, had, [["kills", "your kills"]], false);
    expect(sent).toEqual(["remove admin", "remove kills", "add kills your kills"]);
    expect([...had]).toEqual([["kills", "your kills"]]);
  });

  it("match boundaries come from phase changes", () => {
    const m = new MatchEvents();
    expect(m.observe({ phase: "no_game" })).toEqual([]);
    expect(m.observe({ phase: "loading" })).toEqual(["before_match"]);
    expect(m.observe({ phase: "playing" })).toEqual(["match_start"]);
    expect(m.observe({ phase: "round_over" })).toEqual([]);
    expect(m.observe({ phase: "playing" })).toEqual([]);
    expect(m.observe({ phase: "finished" })).toEqual(["after_match"]);
  });

  it("cron: lists, ranges, steps and the day-field OR rule", () => {
    expect([...cronFields("*/15 9-10 * * 7")[1][0]]).toEqual([0, 15, 30, 45]);
    const z = (ms: number) => zoned(ms, "UTC");
    const fri20 = Date.UTC(2026, 9, 16, 20, 0); // Fri 2026-10-16
    expect(cronDue("0 20 * * 5", z(fri20))).toBe(true);
    expect(cronDue("0 20 * * 6", z(fri20))).toBe(false);
    expect(cronDue("0 20 1 * 5", z(fri20))).toBe(true); // either day field may match
    expect(z(Date.UTC(2026, 9, 18, 0, 0)).dow).toBe(0); // Sunday
  });
});
