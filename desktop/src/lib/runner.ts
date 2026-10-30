// Carries out what the rules engine decides: the rcon commands, the Discord and Telegram posts and the waits, with the limits that
// keep a misjudged rule from flooding a server. The console feeds it events; it hands every command to the IO it was
// given, so it runs the same under test. From ibigs/oni-rcon's rules_run.py, by IBIGS.
import { DISCORD_GAP, Engine, HISTORY, MAX_QUEUED, SAY_PER_MINUTE, TELEGRAM_GAP, VIP_GUARD, plan, stepWords, telegramWhy, validate, type Firing, type Roles, type Step } from "./rules";
import type { Dict } from "./util";

export interface IO {
  /** Run `command` on the server called `server` (a station's `where`). null: not connected. */
  call(server: string, command: string, args: string[]): Promise<{ ok: boolean | null; data?: any } | null>;
  /** Post `text` to the webhook called `name`: the HTTP status. */
  discord(name: string, text: string): Promise<number>;
  /** Post `text` to the Telegram chat called `name`: the HTTP status. */
  telegram(name: string, text: string): Promise<number>;
  changed(): void;
  /** Each finished firing, for the command log. */
  noted?(n: Note): void;
}

export interface Note { at: number; rule: string; name: string; server: string; result: string }

export class Runner {
  engine: Engine;
  history: Note[] = [];
  /** Webhook name -> its URL with the secret hidden: the window never holds the real ones. */
  webhooks: Record<string, string> = {};
  /** Telegram chat name -> the bot and chat with the token hidden. */
  chats: Record<string, string> = {};
  private said = new Map<string, number[]>();
  private lanes = new Map<string, { at: number; line: Promise<number>; waiting: number }>();

  constructor(private io: IO, private clock: () => number = () => Date.now() / 1000, private sleep = (s: number) => new Promise<void>((r) => setTimeout(r, s * 1000))) {
    this.engine = new Engine([], {}, clock);
  }

  get active() { return this.engine.rules.some((r) => r.enabled); }
  /** The frame-rate triggers only matter when a rule uses them. */
  get wantsFps() { return this.engine.rules.some((r) => r.enabled && (["fps_below", "fps_recovered"].includes(r.when.type) || r.if.some((c: Dict) => c.type === "fps_under"))); }

  /** The saved rules and roles. While anyone has the VIP role, VIPs can't be vote-kicked. */
  load(rules: Dict[], roles: Roles) {
    const checked: Dict[] = [];
    for (const r of rules) {
      try {
        checked.push(validate(r));
      } catch (e) {
        this.note("", r?.id ?? "", "", `Rule "${r?.name ?? "?"}" skipped: ${e instanceof Error ? e.message : e}`);
      }
    }
    this.engine.rules = Object.values(roles).some((p) => p.roles.includes("vip")) ? [VIP_GUARD(), ...checked] : checked;
    this.engine.roles = roles;
    this.io.changed();
  }

  // inputs ---------------------------------------------------------------------------------------------------------
  event(server: string, ev: Dict) { this.run(this.engine.event(server, ev)); }
  connection(server: string, up: boolean, label?: string) { this.run(this.engine.connection(server, up, label)); }
  status(server: string, s: Dict) { this.run(this.engine.status(server, s)); }
  players(server: string, p: unknown) { this.engine.players(server, p); }
  fps(server: string, fps: number | null | undefined) { this.run(this.engine.frameRate(server, fps)); }
  tick() { this.run(this.engine.tick()); }

  private run(firings: Firing[]) {
    for (const f of firings) void this.steps(f, plan(this.engine, f));
  }

  /** A rule's steps in order. One failing never stops the others, or the console. */
  async steps(f: Firing, steps: Step[]) {
    const results: string[] = [];
    for (const s of steps) {
      try {
        results.push(await this.step(s));
      } catch (e) {
        results.push(`${stepWords(this.engine, s)}: failed (${e instanceof Error ? e.name : "error"})`);
      }
    }
    this.note(f.rule.id, f.server, results.join("; ") || "Nothing to do (nobody to tell).", f.rule.name);
  }

  /** Posts to one service go out one at a time, `gap` seconds apart, so a burst of firings can't trip its rate limit. A post
   *  that fails, or throws, is 0 and the line carries on. With MAX_QUEUED already waiting the post is dropped (null). */
  private paced(service: string, gap: number, send: () => Promise<number>): Promise<number | null> {
    const lane = this.lanes.get(service) ?? { at: -Infinity, line: Promise.resolve(0), waiting: 0 };
    this.lanes.set(service, lane);
    if (lane.waiting >= MAX_QUEUED) return Promise.resolve(null);
    lane.waiting++;
    return (lane.line = lane.line.then(async () => {
      try {
        const wait = gap - (this.clock() - lane.at);
        if (wait > 0) await this.sleep(wait);
        lane.at = this.clock();
        return await send();
      } catch {
        return 0;
      } finally {
        lane.waiting--;
      }
    }));
  }

  private async step(s: Step): Promise<string> {
    const words = stepWords(this.engine, s);
    if (s.kind === "wait") {
      await this.sleep(s.seconds!);
      return words;
    }
    if (s.kind === "discord") {
      if (!Object.hasOwn(this.webhooks, s.webhook!)) return `${words}: no webhook called ${s.webhook}`;
      const code = await this.paced(`discord:${s.webhook}`, DISCORD_GAP, () => this.io.discord(s.webhook!, s.text!)); // Discord limits each webhook, so each gets its own line
      if (code === null) return `${words}: skipped, ${MAX_QUEUED} posts are already waiting`;
      return words + (code >= 200 && code < 300 ? ": done" : code ? `: Discord answered ${code}` : ": Discord could not be reached");
    }
    if (s.kind === "telegram") {
      if (!Object.hasOwn(this.chats, s.chat!)) return `${words}: no chat called ${s.chat}`;
      if (!s.text!.trim()) return `${words}: skipped, nothing to say`;
      const code = await this.paced("telegram", TELEGRAM_GAP, () => this.io.telegram(s.chat!, s.text!));
      return `${words}: ${code === null ? `skipped, ${MAX_QUEUED} posts are already waiting` : telegramWhy(code)}`;
    }
    const [cmd, ...rest] = s.args!;
    if (cmd === "say" || cmd === "tell") {
      const now = this.clock(), window = (this.said.get(s.server!) ?? []).filter((t) => now - t <= 60);
      this.said.set(s.server!, window);
      if (window.length >= SAY_PER_MINUTE) return `${words}: skipped, ${SAY_PER_MINUTE} messages a minute is the limit`;
      window.push(now);
    }
    if (cmd === "shuffle") {
      const fresh = await this.io.call(s.server!, "status", []);
      if (!["playing", "in_game"].includes(fresh?.data?.phase)) return `${words}: skipped, no match is being played`;
    }
    const reply = await this.io.call(s.server!, cmd, rest);
    if (!reply) return `${words}: skipped, server offline`;
    return words + (reply.ok ? ": done" : ": the server refused");
  }

  note(rule: string, server: string, result: string, name = "") {
    const n = { at: this.clock(), rule, name, server: this.engine.name(server), result: result.slice(0, 600) };
    this.history.push(n);
    if (this.history.length > HISTORY) this.history.splice(0, this.history.length - HISTORY);
    this.io.noted?.(n);
    this.io.changed();
  }
}
