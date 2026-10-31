import { describe, expect, it } from "vitest";
import { Runner } from "./runner";
import { MAX_QUEUED, TELEGRAM_GAP, validate } from "./rules";

const setup = (phase = "playing") => {
  const calls: string[] = [], posts: string[] = [], tele: string[] = [];
  const r = new Runner({
    call: async (_s, c, a) => { calls.push([c, ...a].join(" ")); return { ok: true, data: { phase } }; },
    discord: async (n, t) => { posts.push(`${n}:${t}`); return 204; },
    telegram: async (n, t) => { tele.push(`${n}:${t}`); return n === "revoked" ? 401 : 200; },
    changed() {},
  }, () => 0, async () => {});
  r.webhooks = { hook: "https://discord.com/api/webhooks/1/••••••" };
  r.chats = { alerts: "123456789:•••• → -1001", revoked: "123456789:•••• → -1002" };
  r.engine.connection("s", true, "One");
  return { r, calls, posts, tele };
};
const rule = (then: object[], when: object = { type: "join" }) => validate({ name: "t", when, then, cooldown: 0, per_hour: 600 });

describe("runner", () => {
  it("says at most 20 a minute per server, and shuffles only mid-match", async () => {
    const a = setup();
    a.r.load([rule([{ type: "say", to: "this", message: "hi" }])], {});
    for (let i = 0; i < 25; i++) a.r.event("s", { event: "join", name: "A", player_id: String(i) });
    await new Promise((r) => setTimeout(r, 10));
    expect(a.calls.filter((c) => c.startsWith("say")).length).toBe(20);

    for (const [phase, runs] of [["playing", 1], ["lobby", 0]] as const) {
      const b = setup(phase);
      b.r.load([rule([{ type: "shuffle" }])], {});
      b.r.event("s", { event: "join", name: "A", player_id: "1" });
      await new Promise((r) => setTimeout(r, 10));
      expect(b.calls.filter((c) => c === "shuffle").length).toBe(runs);
    }
  });

  it("posts to Discord by name, and notes an unknown webhook", async () => {
    const a = setup();
    a.r.load([rule([{ type: "discord", webhook: "hook", message: "hello" }]), rule([{ type: "discord", webhook: "nope", message: "x" }])], {});
    a.r.event("s", { event: "join", name: "A", player_id: "1" });
    await new Promise((r) => setTimeout(r, 10));
    expect(a.posts).toEqual(["hook:hello"]);
    expect(a.r.history.some((n) => /no webhook called nope/.test(n.result))).toBe(true);
  });

  it("a step with several webhooks posts to each of them by name, and notes an unknown one on its own", async () => {
    const a = setup();
    a.r.webhooks.staff = "https://discord.com/api/webhooks/2/••••••";
    a.r.load([rule([{ type: "discord", webhook: "hook,staff,nope", message: "hello" }])], {});
    a.r.event("s", { event: "join", name: "A", player_id: "1" });
    await new Promise((r) => setTimeout(r, 10));
    expect(a.posts).toEqual(["hook:hello", "staff:hello"]);
    expect(a.r.history.at(-1)!.result).toMatch(/no webhook called nope/);
  });

  it("posts to Telegram by name, and says why a post failed", async () => {
    const a = setup();
    a.r.load([rule([{ type: "telegram", chat: "alerts", message: "hello" }]), rule([{ type: "telegram", chat: "nope", message: "x" }]), rule([{ type: "telegram", chat: "revoked", message: "y" }])], {});
    a.r.event("s", { event: "join", name: "A", player_id: "1" });
    await new Promise((r) => setTimeout(r, 10));
    expect(a.tele).toEqual(["alerts:hello", "revoked:y"]);
    const said = a.r.history.map((n) => n.result).join("\n");
    expect(said).toMatch(/Post to Telegram \(alerts\): hello: done/);
    expect(said).toMatch(/no chat called nope/);
    expect(said).toMatch(/Telegram refused it \(401\): the bot token is wrong/);
    expect(a.posts).toEqual([]);
  });

  it("spaces posts to one service, and a failed post doesn't block the next", async () => {
    let now = 0;
    const slept: number[] = [], sent: number[] = [];
    const r = new Runner({
      call: async () => ({ ok: true }),
      discord: async () => 204,
      telegram: async () => { sent.push(now); if (sent.length === 1) throw new Error("offline"); return 200; },
      changed() {},
    }, () => now, async (s) => { slept.push(s); now += s; });
    r.chats = { alerts: "x" };
    const f = { rule: { id: "r", name: "t" } } as any;
    const post = { kind: "telegram" as const, chat: "alerts", text: "hi" };
    await Promise.all([r.steps(f, [post]), r.steps(f, [post]), r.steps(f, [post])]);
    expect(sent).toEqual([0, TELEGRAM_GAP, TELEGRAM_GAP * 2]);
    expect(slept).toEqual([TELEGRAM_GAP, TELEGRAM_GAP]);
    expect(r.history.map((n) => n.result)[0]).toMatch(/Telegram could not be reached/);
  });

  const burst = (io: Partial<ConstructorParameters<typeof Runner>[0]> = {}) => {
    const sent: string[] = [];
    const r = new Runner({ call: async () => ({ ok: true }), discord: async () => 204, telegram: async (_n, t) => { sent.push(t); return 200; }, changed() {}, ...io }, () => 0, async () => {});
    r.chats = { alerts: "x" };
    const f = { rule: { id: "r", name: "t" } } as any;
    const post = (text: string) => r.steps(f, [{ kind: "telegram", chat: "alerts", text }]);
    return { r, sent, post };
  };

  it("holds at most MAX_QUEUED posts for a service, drops the rest, and catches up afterwards", async () => {
    const { r, sent, post } = burst();
    await Promise.all(Array.from({ length: MAX_QUEUED + 5 }, (_, i) => post(`m${i}`)));
    expect(sent).toHaveLength(MAX_QUEUED);
    expect(sent[0]).toBe("m0");
    const said = r.history.map((n) => n.result);
    expect(said.filter((x) => /skipped, \d+ posts are already waiting/.test(x))).toHaveLength(5);
    await post("later");
    expect(sent.at(-1)).toBe("later"); // the line drained, so the next one goes
  });

  it("a post that throws, even before it returns a promise, doesn't wedge the line", async () => {
    let n = 0;
    const { r, post } = burst({ telegram: (() => { if (n++ < 2) throw new Error("boom"); return Promise.resolve(200); }) as any });
    await post("one");
    await post("two");
    await post("three");
    expect(n).toBe(3);
    expect(r.history.map((x) => x.result).filter((x) => /could not be reached/.test(x))).toHaveLength(2);
    expect(r.history.at(-1)!.result).toMatch(/: done$/);
  });

  it("a name that is only on the object prototype is not a saved chat or webhook, and an empty text is not sent", async () => {
    const { r, sent, post } = burst();
    const f = { rule: { id: "r", name: "t" } } as any;
    await r.steps(f, [{ kind: "telegram", chat: "constructor", text: "x" }, { kind: "telegram", chat: "__proto__", text: "x" }, { kind: "discord", webhook: "toString", text: "x" }]);
    await post("   ");
    expect(sent).toEqual([]);
    const said = r.history.map((x) => x.result).join("\n");
    expect(said).toMatch(/no chat called constructor.*no chat called __proto__.*no webhook called toString/);
    expect(said).toMatch(/skipped, nothing to say/);
  });
});
