// The console's mind: what each station said last, the feed and the command log, polling, commands and fan-out, what
// an event means (names, medals, alerts, the Superintendent), and the condition. Components read from here and
// subscribe to the counters in `useV`; nothing is copied into React state, so a busy fleet repaints only what changed.
import { create } from "zustand";
import { invoke } from "@tauri-apps/api/core";
import chime from "./art/superintendent.mp3";
import { listen } from "@tauri-apps/api/event";
import { writeText } from "@tauri-apps/plugin-clipboard-manager";
import { AMBER, CYAN, DIM, GREEN, RED, WHITE } from "./art/palette";
import { Superintendent, classify } from "./art/superintendent";
import { Medals } from "./lib/medals";
import { Engine, type Roles } from "./lib/rules";
import { Runner, type Note } from "./lib/runner";
import { Findings, publicRoster, quietKillMode, type Finding, type Who } from "./lib/findings";
import {
  CATS, ID_LIKE, KIND, cmdLine, commonPrefix, explain, idForms, mixedVersions, num, pick, playerIds, plain, reactionWords,
  pluginReply, pluginSnapshot, redactData, redactText, resolve, splitTag, stripEnds, who, noteKick, typedParams,
  type Dict, type Kick, type Line,
} from "./lib/util";

export const FLEET = 12;
const FANOUT = 4;
const SLOW_POLL = 15_000;
export const SPREE = 5;
export type Tab = "assets" | "intercepts" | "operations" | "blacklist" | "console" | "forge" | "health" | "plugins";
export const TABS: Tab[] = ["assets", "intercepts", "operations", "blacklist", "console", "forge", "health", "plugins"];
/** The key that opens a tab: F1 to F7 in order, then plug-ins on F10 (F8 and F9 were taken). */
export const fkey = (t: Tab) => (t === "plugins" ? "F10" : `F${TABS.indexOf(t) + 1}`);
const PLUGIN_EVERY = 30_000, PLUGIN_BADGE_EVERY = 60_000; // ms between asks: with F10 open, and for a badge while it's shut

// --- what the window repaints, by counter ----------------------------------------------------------------------
type Counters = { cards: number; roster: number; ops: number; bans: number; feed: number; log: number; forge: number;
  auto: number; health: number; mast: number; ui: number; toasts: number; dialog: number; plugins: number; clock: number }; // clock: once a second, for what counts down or blinks
export const useV = create<Counters>(() => ({ cards: 0, roster: 0, ops: 0, bans: 0, feed: 0, log: 0, forge: 0, auto: 0, health: 0, mast: 0, ui: 0, toasts: 0, dialog: 0, plugins: 0, clock: 0 }));
const pending = new Set<keyof Counters>();
let flushQueued = false;
// the feed and the log repaint at most this often: a fleet's events arrive as a stream, and a repaint per event
// scrolled the whole view dozens of times a second for lines nobody can read that fast
const SLOW: Partial<Record<keyof Counters, number>> = { feed: 200, log: 120 };
const slowAt: Partial<Record<keyof Counters, number>> = {};
const slowTimer: Partial<Record<keyof Counters, number>> = {};
export function bump(...keys: (keyof Counters)[]) {
  const now = performance.now();
  keys = keys.filter((k) => {
    const gap = SLOW[k];
    if (!gap) return true;
    if (slowTimer[k]) return false; // a repaint is already coming
    const wait = (slowAt[k] ?? 0) + gap - now;
    if (wait <= 0) {
      slowAt[k] = now;
      return true;
    }
    slowTimer[k] = window.setTimeout(() => {
      slowTimer[k] = 0;
      slowAt[k] = performance.now();
      bump(k);
    }, wait);
    return false;
  });
  if (!keys.length) return;
  for (const k of keys) pending.add(k);
  if (!flushQueued) {
    flushQueued = true;
    requestAnimationFrame(() => {
      flushQueued = false;
      const s = useV.getState(), next: Partial<Counters> = {};
      for (const k of pending) next[k] = s[k] + 1;
      pending.clear();
      useV.setState(next);
    });
  }
}

export class Station {
  label: string;
  tag = "";
  state = "connecting";
  detail = "";
  retryAt = 0;
  prev = "";
  info: Dict = {};
  data: Dict = {};
  inflight = new Set<string>();
  medals = new Medals();
  activity: number[] = [];
  alerts = 0;
  flash = 0;
  firstSeen = new Map<string, number>();
  painted = false;
  polled = 0;
  pings = new Map<string, number>();
  here = new Map<string, string>();
  past = new Map<string, string>();
  aliases = new Map<string, string>();
  unmatched = false;
  kicks = new Map<string, Kick>(); // who it saw kicked, for F4's PARDON
  constructor(public index: number, public where: string, public name: string, public ssh: string, public url: string,
    public contentDir: string, public host: string, public port: number, public canRestart = false) {
    this.label = name || where;
  }
  get online() { return this.state === "online"; }
  get players(): Dict[] { return this.data.players?.players ?? []; }
  get names(): Map<unknown, string> {
    const m = new Map<unknown, string>();
    for (const p of this.players) if (p.engine_id !== undefined) m.set(p.engine_id, String(pick(p, "name") ?? "?"));
    return m;
  }
  /** Read the roster for the IDs a kill might name its players by. Long ones are kept, so a kill by someone who just
   *  left still reads. */
  learn(players: Dict[]) {
    this.here = new Map();
    for (const p of players) for (const f of playerIds(p)) this.here.set(f, String(pick(p, "name") ?? "?"));
    for (const [f, n] of this.here) if (f.length >= 8) this.past.set(f, n);
    while (this.past.size > 4000) this.past.delete(this.past.keys().next().value!);
  }
  /** The callsign a killer or victim ID belongs to; an ID that belongs to nobody here becomes PLAYER n. */
  ident = (v: unknown): string | null => {
    const forms = idForms(v);
    for (const f of forms) {
      const n = this.here.get(f) ?? this.past.get(f);
      if (n !== undefined) return n;
    }
    if ((typeof v === "string" && ID_LIKE.test(v.trim())) || (typeof v === "number" && String(Math.abs(v)).length >= 6)) {
      const key = [...forms].sort()[0];
      if (!this.aliases.has(key)) this.aliases.set(key, `PLAYER ${this.aliases.size + 1}`);
      return this.aliases.get(key)!;
    }
    return null;
  };
  /** Events in each of the last `buckets` stretches of `span` ms, oldest first. */
  rate(buckets: number, span: number, whole = false): number[] {
    let now = performance.now();
    if (whole) now -= now % span;
    while (this.activity.length && now - this.activity[0] > 300_000) this.activity.shift();
    const out = new Array(buckets).fill(0);
    for (const t of this.activity) {
      const i = buckets - 1 - Math.floor((now - t) / span);
      if (i >= 0 && i < buckets) out[i]++;
    }
    return out;
  }
}

export interface FeedEntry { id: number; st: number | null; ev: Dict; cat: string }
export interface LogEntry { id: number; line?: Line; json?: unknown }
export interface Toast { id: number; title: string; text: string; severity: string; until: number }
export interface Dialog { id: number; kind: string; props: Dict; resolve: (v: any) => void }

export interface Reply { ok: boolean | null; text?: string; data?: any }

/** A plug-in the config turns on (its manifest, and `state`: why it won't run, "" when it will), and what the console
 *  last heard from it. Its view is kept, greyed, when an ask fails. `replies` are the RCON answers it hasn't been told
 *  of yet (sent in its next view request), `session` how its process is (session mode), `pages` its frames' inboxes. */
export interface Plugin {
  m: Dict; view: Dict | null; error: string; busy: boolean; asked: boolean; due: number; note: string;
  replies: Dict[]; session: { state: string; detail: string; retry_in: number } | null; pages: Set<(msg: Dict) => void>;
  update: { behind: number; log: string[]; source: string; to: string; asked: boolean } | null; // its git upstream is ahead
}
export const badgeOf = (p: Plugin): Dict | null => (p.view && !p.error ? p.view.badge : null);

class Oni {
  by = "";
  stations: Station[] = [];
  tunnels = new Map<string, { state: string; detail: string }>();
  fleet = false;
  demo = false;
  /** The newest Project Reclaimer release ("0.9.18"), asked GitHub at most hourly; "" until known. */
  reclaimer = "";
  private reclaimerAt = -Infinity;
  private mixedSaid = "";
  intro = "quick";
  hint = "";
  version = "";
  /** A newer desktop release, asked GitHub every 6 hours; null until one is out. */
  appUpdate: { version: string; notes: string } | null = null;
  sel = 0;
  tab: Tab = "assets";
  redact = true;
  rawEvents = false;
  filters = new Set(Object.keys(CATS));
  localOnly = false;
  feed: FeedEntry[] = [];
  log: LogEntry[] = [];
  history: string[] = [];
  toasts: Toast[] = [];
  dialogs: Dialog[] = [];
  alertAt = -Infinity;
  sup = new Superintendent();
  findings = new Findings();
  /** Automation rules: they run while this console is open, and act through the same calls as the buttons do. */
  rules: Dict[] = [];
  roles: Roles = {};
  runner = new Runner({
    call: (where, command, args) => {
      const st = this.stations.find((s) => s.where === where);
      return st?.online ? this.call(st, command, args) : Promise.resolve(null);
    },
    discord: (name, text) => invoke<number>("discord_post", { name, text }),
    telegram: (name, text) => invoke<number>("telegram_post", { name, text }),
    changed: () => bump("auto"),
    noted: (n) => this.noteAutomation(n),
  });
  private rosters = new WeakMap<object, Who[]>();
  labelWidth = 14;
  banner: { text: string; warn: boolean; until: number } | null = null;
  health: Dict = { on: false, crashes: [], hosts: {}, boxes: {}, services: {}, errors: [], service_err: {}, watched: [] };
  forgeStatus: Dict = { key: false, source: "", error: "", quota: "" };
  forgeState: Dict = { servers: {}, withdrawn: {}, latest: {}, alarms: [] };
  fullIntro = false;
  waiting: number[] = [];
  plugins: Plugin[] = [];
  psel = 0;
  private ids = 0;
  private timers: number[] = [];
  private unlisten: (() => void) | null = null;
  onInstalled: ((e: Dict) => void) | null = null;
  onForgeRefresh: ((lid: string) => void) | null = null;
  onFindings: (() => void) | null = null;

  get cur() { return this.stations[this.sel]; }

  async start(info: Dict) {
    this.stop();
    this.by = info.by;
    this.stations = info.stations.map((s: Dict) => new Station(s.index, s.where, s.name, s.ssh, s.url, s.content_dir, s.host, s.port, s.restart));
    this.tunnels = new Map(info.tunnels.map((d: string) => [d, { state: "opening", detail: "" }]));
    this.fleet = this.stations.length > FLEET;
    this.demo = info.demo;
    this.intro = info.intro;
    this.hint = info.hint;
    this.version = info.version;
    this.forgeStatus = info.forge;
    this.forgeState = info.forge_state;
    this.health = info.health;
    this.fullIntro = info.full_intro;
    this.waiting = info.waiting;
    this.sel = 0;
    this.feed = [];
    this.log = [];
    this.banner = null;
    this.alertAt = -Infinity;
    this.toasts = [];
    this.findings.clear();
    this.runner.history = [];
    this.runner.engine = new Engine();
    this.loadAutomation();
    this.sup.clear();
    this.measureLabels();
    this.logLine([["ONI remote console. Commands go to the selected station; `@all` prefixes run on every station; `help` asks the server; `clear` clears this log.", DIM]]);
    this.unlisten = await listen<Dict[]>("oni", (e) => this.batch(e.payload));
    const every = (ms: number, f: () => void) => this.timers.push(window.setInterval(f, ms));
    every(1000, () => this.tick());
    every(3000, () => this.pollFast());
    every(1000, () => this.pollSlow());
    every(2000, () => this.pluginTick());
    every(60_000, () => this.checkReclaimer());
    this.mixedSaid = "";
    this.checkReclaimer();
    this.plugins = [];
    this.loadPlugins(performance.now() + 3000); // each asked once soon after start, for its badge
    for (const st of this.stations) if (st.online) this.fetch(st, "status");
    bump("cards", "roster", "ops", "bans", "feed", "log", "forge", "health", "mast", "ui");
  }

  async loadAutomation() {
    try {
      const f = await invoke<Dict>("automation_load");
      [this.rules, this.roles, this.runner.webhooks, this.runner.chats] = [f.rules, f.roles, f.webhooks, f.telegram ?? {}];
      this.runner.load(this.rules, this.roles);
    } catch (e) {
      this.notify(String(e), "AUTOMATION", "error", 10);
    }
  }

  /** Save the rules and roles (the Rust side keeps the file), then run on them. */
  async saveAutomation(rules = this.rules, roles = this.roles) {
    await invoke("automation_save", { rules, roles });
    [this.rules, this.roles] = [rules, roles];
    this.runner.load(rules, roles);
  }

  /** What a rule did goes in the command log, the audit trail beside everything typed by hand. */
  private noteAutomation(n: Note) {
    this.logLine([[new Date().toTimeString().slice(0, 9), DIM], ["AUTO ", CYAN], [`${n.name || n.rule}${n.server ? ` · ${n.server}` : ""}  `, WHITE], [n.result, DIM]]);
  }

  stop() {
    this.timers.forEach((t) => clearInterval(t));
    this.timers = [];
    this.unlisten?.();
    this.unlisten = null;
  }

  // --- what the backend says -------------------------------------------------------------------------------------
  private batch(items: Dict[]) {
    for (const m of items) {
      try {
        this.one(m);
      } catch (err) {
        console.error(err, m);
      }
    }
  }

  private one(m: Dict) {
    switch (m.kind) {
      case "link": return this.onLink(this.stations[m.index], m);
      case "event": return m.index === null ? this.logEvent(null, m.ev) : this.onEvent(this.stations[m.index], m.ev);
      case "late": return this.logReply(this.stations[m.index], m.line, m.reply, false, true);
      case "tunnel": {
        this.tunnels.set(m.dest, { state: m.state, detail: m.detail });
        if (m.state === "down") this.logEvent(null, { event: "uplink", text: `SSH ${m.dest} DOWN  ${m.detail}` });
        return bump("cards", "ui");
      }
      case "ping": {
        const st = this.stations[m.index];
        st.pings.set(m.id, m.ms);
        if (st.pings.size > 5000) st.pings.delete(st.pings.keys().next().value!);
        if (st === this.cur) bump("roster");
        return;
      }
      case "health": {
        this.health = m.view;
        if (this.runner.wantsFps) for (const st of this.stations) this.runner.fps(st.where, m.view.frames?.[String(st.index)]?.fps);
        return bump("health", "mast", "cards", "ops");
      }
      case "health-notice": return this.healthNotice(m);
      case "toast": return this.notify(m.explain ? explain(m.text) : m.text, m.title, m.severity, m.timeout);
      case "log": {
        const head: Line = m.station ? [[new Date().toTimeString().slice(0, 9), DIM], [`${m.station} `, CYAN]] : [];
        const col = ({ red: RED, green: GREEN, white: WHITE } as Dict)[m.color] ?? WHITE;
        return this.logLine([...head, [m.explain ? explain(m.text) : m.text, col]]);
      }
      case "forge-state": this.forgeState = m.state; return bump("forge", "ops", "mast");
      case "forge-refresh": return this.onForgeRefresh?.(m.lid);
      case "installed": return this.onInstalled?.(m);
      case "plugin-session": case "plugin-view": case "plugin-notify": case "plugin-log": case "plugin-rcon": return this.onPlugin(m);
    }
  }

  private onLink(st: Station, m: Dict) {
    const { state, detail } = m;
    this.findings.reset(String(st.index)); // whatever happened while the link was down is unknown
    st.state = state;
    st.detail = detail;
    st.info = m.info ?? st.info;
    if (state !== "connecting") this.runner.connection(st.where, state === "online", st.label);
    if (state === "online") {
      st.medals.newGame(); // what happened while we were away is unknown
      this.relabel();
      this.fetch(st, "status", "players", "maps", "modes", "nextmap", "vote", "bans", "vpn", "admins");
      st.polled = performance.now();
      this.logEvent(st, { event: "uplink", text: `SECURE  ${detail}` });
      this.noteMixed();
      if (st === this.cur) bump("forge");
    } else if (state === "denied") {
      this.logEvent(st, { event: "uplink", text: `SIGN-IN REFUSED: ${detail}` });
      this.notify(`${explain(detail, state)}\nNot retried by itself. RECONNECT (F3) asks for the password again.`, `${st.label} · SIGN-IN REFUSED`, "error", 20);
    } else if (state === "offline" && st.prev === "online") {
      this.logEvent(st, { event: "uplink", text: `LOST  ${detail}` });
    }
    st.retryAt = state === "offline" && m.retry_in ? performance.now() + m.retry_in * 1000 : 0;
    if (state !== "connecting") st.prev = state;
    bump("cards", "mast", "ui");
    if (st === this.cur) bump("roster", "ops", "bans");
  }

  private onEvent(st: Station, raw: Dict) {
    if (this.rawEvents) this.logLine([[`${st.label} ◂ `, CYAN], [JSON.stringify(redactData(raw, this.redact)), DIM]]);
    const names = st.names;
    const context = this.findingContext(st);
    const found = this.findings.ingest(String(st.index), raw, (id) => this.nameOf(st, names, id), context, this.roster(st));
    for (const f of found) this.reportFinding(f, st);
    this.runner.event(st.where, raw);
    if (noteKick(st.kicks, raw) && st === this.cur) bump("bans");
    if (raw.event === "join") {
      for (const f of idForms(pick(raw, "id", "player_id"))) if (f.length >= 8) st.past.set(f, String(pick(raw, "name", "player") ?? "?"));
    }
    const rawIds = [raw.killer, raw.victim];
    const ev = resolve(raw, names, st.ident);
    const kind = ev.event;
    if (kind === "chat") ev._review_alert = found.some((f) => f.level === "review");
    if (kind === "kill") {
      ev._quiet_kills = quietKillMode(context);
      const aliases = new Set(st.aliases.values());
      if (aliases.has(ev.killer) || aliases.has(ev.victim)) {
        this.fetch(st, "players"); // someone the roster hasn't caught up with: ask again
        this.unmatchedIds(st, rawIds);
      }
      const killer = pick(ev, "killer");
      ev._medals = st.medals.kill(killer === undefined ? null : who(killer, names), who(pick(ev, "victim"), names), performance.now() / 1000);
    }
    this.pluginEvent(st, kind === "kill" ? { ...raw, killer_name: who(ev.killer, names), victim_name: who(ev.victim, names), medals: ev._medals ?? [] } : raw);
    st.activity.push(performance.now());
    this.logEvent(st, ev);
    this.sup.react(ev, classify(ev) ? reactionWords(ev, this.redact) : "");
    if (["join", "leave", "refused", "kick", "ban", "mute", "unmute", "control"].includes(kind)) this.fetch(st, "players", "status");
    if (kind === "ban" || kind === "unban") this.fetch(st, "bans");
    if (kind === "admin_added" || kind === "admin_removed") this.fetch(st, "admins", "players");
    if (kind === "vote") this.fetch(st, "vote");
    if (kind === "control") this.fetch(st, "nextmap");
  }

  // --- findings ----------------------------------------------------------------------------------------------------
  private findingContext(st: Station): Dict {
    const status = st.data.status ?? {};
    return { ...status, name: [status.name, st.name, st.label].filter(Boolean).join(" ") };
  }

  /** The roster as a finding may carry it, rebuilt only when the roster is. */
  private roster(st: Station): Who[] {
    const players = st.data.players?.players;
    if (!Array.isArray(players)) return [];
    let r = this.rosters.get(players);
    if (!r) this.rosters.set(players, (r = publicRoster(players)));
    return r;
  }

  /** The callsign an ID or slot belongs to, as far as this station has learned. */
  private nameOf(st: Station, names: Map<unknown, string>, id: string): string | undefined {
    if (/^\d+$/.test(id) && names.has(Number(id))) return names.get(Number(id));
    for (const f of idForms(id)) {
      const n = st.here.get(f) ?? st.past.get(f);
      if (n !== undefined) return n;
    }
    return undefined;
  }

  private reportFinding(f: Finding, st: Station) {
    this.sup.report(redactText(`${st.label}: ${f.title}`, this.redact), f.level);
    if (f.level !== "review") return;
    this.notify(redactText(`${f.title}\nF8 for the evidence`, this.redact), `REVIEW NEEDED · ${st.label}`, "warning", 12);
    this.alert(st);
  }

  /** Acknowledge everything: the alerts, the banner, the toasts, the face. */
  acknowledgeFindings() {
    this.findings.acknowledge();
    this.sup.clear();
    this.alertAt = -Infinity;
    this.banner = null;
    this.toasts = [];
    for (const s of this.stations) [s.alerts, s.flash] = [0, 0];
    bump("mast", "cards", "ui", "toasts");
  }

  private unmatchedIds(st: Station, ids: unknown[]) {
    if (st.unmatched || !ids.some((i) => (typeof i === "number" || typeof i === "string") && ID_LIKE.test(String(i)))) return;
    st.unmatched = true;
    const keys = [...new Set(st.players.flatMap((p) => Object.entries(p).filter(([, v]) => typeof v === "number" || typeof v === "string").map(([k]) => k)))].sort();
    this.logLine([[`${st.label}: kills name players by an ID no roster field matches, so they read PLAYER n. The roster has: ${keys.join(", ") || "nothing yet"}`, AMBER]]);
  }

  private healthNotice(m: Dict) {
    const st = m.index === null ? null : this.stations[m.index];
    this.logEvent(st, { event: "health", text: m.text, level: m.level });
    if (m.quiet) return; // the feed only: frame rate is news, not an alarm
    this.banner = { text: ` ⚠ ${st ? st.label : "HOST"}  ${m.text}      click to dismiss`, warn: m.level !== "crit", until: performance.now() + 30_000 };
    this.alert(st);
    bump("ui");
  }

  // --- data --------------------------------------------------------------------------------------------------------
  async call(st: Station, command: string, args: string[] = [], timeout?: number): Promise<Reply> {
    try {
      return await invoke<Reply>("call", { index: st.index, command, args, timeout });
    } catch (e) {
      return { ok: false, text: String(e) };
    }
  }

  fetch(st: Station, ...what: string[]) {
    const todo = what.filter((w) => !st.inflight.has(w));
    if (!st.online || !todo.length) return;
    todo.forEach((w) => st.inflight.add(w));
    this.fetchNow(st, todo).finally(() => todo.forEach((w) => st.inflight.delete(w)));
  }

  async fetchNow(st: Station, what: string[]) {
    const replies = await Promise.all(what.map((w) => this.call(st, w)));
    what.forEach((w, i) => {
      const r = replies[i];
      if (r.ok && w === "admins" && Array.isArray(r.data)) st.data[w] = { admins: r.data };
      else if (r.ok && r.data && typeof r.data === "object" && !Array.isArray(r.data)) {
        if (w === "status" && r.data.phase !== st.data.status?.phase) st.medals.newGame();
        if (w === "players") {
          st.learn(r.data.players ?? []);
          this.runner.players(st.where, r.data.players ?? []);
        }
        if (w === "status") this.runner.status(st.where, r.data);
        st.data[w] = r.data;
      }
    });
    bump("cards");
    if (st === this.cur) {
      if (what.includes("players")) bump("roster");
      if (what.some((w) => ["status", "nextmap", "vote", "players"].includes(w))) bump("ops");
      if (what.some((w) => w === "bans" || w === "vpn" || w === "admins")) bump("bans");
      if (what.some((w) => w === "maps" || w === "modes")) bump("forge");
    }
    if (what.includes("status")) bump("mast");
  }

  private pollFast() {
    this.fetch(this.cur, "players", "status");
    if (this.tab === "operations") this.fetch(this.cur, "vote");
  }

  /** Each second, the stations due: every one comes round once in SLOW_POLL, spread out rather than all at once. An
   *  empty server's roster isn't asked for: its status says nobody's there, and a join brings the roster in. */
  private pollSlow() {
    const now = performance.now();
    const due = this.stations.filter((s) => s.online && now - s.polled >= SLOW_POLL).sort((a, b) => a.polled - b.polled);
    for (const st of due.slice(0, Math.ceil(this.stations.length / 15))) {
      st.polled = now;
      const empty = num(st.data.status?.players) === 0 && !st.players.length;
      this.fetch(st, "status", ...(empty ? [] : ["players"]));
    }
  }

  /** Asks GitHub for the newest Reclaimer release, at most hourly. Offline it stays quiet and asks again next hour.
   *  Not in the demo: its servers are a made-up build. */
  private checkReclaimer() {
    if (this.demo || performance.now() - this.reclaimerAt < 3_600_000) return;
    this.reclaimerAt = performance.now();
    invoke<string>("reclaimer_latest").then((v) => {
      if (!v || v === this.reclaimer) return;
      this.reclaimer = v;
      bump("cards", "ops");
    }).catch(() => { /* offline: the cards just don't say */ });
  }

  /** One feed line when the connected stations run different versions: a player can join only those on theirs.
   *  Said again only when the mix changes. */
  private noteMixed() {
    const mix = mixedVersions(this.stations.filter((s) => s.online).map((s) => ({ label: s.label, version: s.info.version })));
    if (mix && mix !== this.mixedSaid) this.logEvent(null, { event: "uplink", text: `MIXED VERSIONS  ${mix}` });
    this.mixedSaid = mix;
  }

  replyText(r: Reply) {
    return redactText(String(r.text || (r.ok ? "done" : r.ok === null ? "no reply" : "failed")), this.redact);
  }

  logReply(st: Station, line: string | null, r: Reply, showData = false, late = false) {
    const col = r.ok ? GREEN : r.ok === null ? AMBER : RED;
    if (line === null) this.logLine([[`  ${r.ok ? "✓" : r.ok === null ? "…" : "✕"} `, col], [`${st.label}  `, CYAN], [this.replyText(r), col]]);
    else {
      this.logLine([[new Date().toTimeString().slice(0, 9), DIM], [`${st.label} `, CYAN],
        [redactText(`${late ? "« late reply to" : "»"} ${line}`, this.redact), late ? DIM : WHITE]]);
      this.logLine([["  "], [this.replyText(r), col]]);
    }
    if (showData && r.data !== undefined && r.data !== null) this.logJson(redactData(r.data, this.redact));
  }

  /** Run one command, write it to the command log (the audit trail), toast the result. */
  async cmd(st: Station, command: string, args: string[] = [], o: { toast?: boolean; showData?: boolean; quiet?: boolean; brief?: boolean } = {}) {
    const { toast = true, showData = false, quiet = false, brief = false } = o;
    const r = await this.call(st, command, args);
    this.logReply(st, brief ? null : cmdLine(command, args), r, showData);
    if (toast || !(r.ok || quiet))
      this.notify(this.replyText(r), `${st.label} · ${command.toUpperCase()}`, r.ok ? "information" : r.ok === null ? "warning" : "error");
    return r;
  }

  /** One command on many stations, FANOUT at a time, with one tally at the end for a fleet. */
  async fanout(stations: Station[], command: string, args: string[] = [], showData = false) {
    const many = stations.length > 1;
    if (many) this.logLine([[new Date().toTimeString().slice(0, 9), DIM], ["@all ", CYAN],
      [redactText(`» ${cmdLine(command, args)}  →  ${stations.length} stations`, this.redact), WHITE]]);
    const results: Reply[] = [];
    let next = 0;
    await Promise.all(Array.from({ length: Math.min(FANOUT, stations.length) }, async () => {
      while (next < stations.length) {
        const st = stations[next++];
        results.push(await this.cmd(st, command, args, { toast: false, showData, quiet: many, brief: many }));
      }
    }));
    if (results.length < 2) return;
    const ok = results.filter((r) => r.ok === true).length, late = results.filter((r) => r.ok === null).length;
    const bad = results.length - ok - late;
    const tally: Line = [[new Date().toTimeString().slice(0, 9), DIM], ["@all ", CYAN], [command, WHITE],
      [`  ·  ${results.length} stations  ·  `, DIM], [`${ok} ok`, GREEN],
      ...(late ? [["  ·  ", DIM], [`${late} no reply yet`, AMBER]] as Line : []),
      ...(bad ? [["  ·  ", DIM], [`${bad} failed`, RED]] as Line : [])];
    this.logLine(tally);
    this.notify(plain(tally).slice(9), `@ALL ${command.toUpperCase()}`, ok === results.length ? "information" : ok ? "warning" : "error");
  }

  async send(st: Station, command: string, args: string[] = [], then: string[] = [], o: Parameters<Oni["cmd"]>[3] = {}) {
    const r = await this.cmd(st, command, args, o);
    if (then.length && r.ok) await this.fetchNow(st, then);
    return r;
  }

  // --- the feed and the log --------------------------------------------------------------------------------------
  logEvent(st: Station | null, ev: Dict) {
    if (typeof ev.time !== "number") ev = { ...ev, time: Date.now() / 1000 }; // stamped as it arrives, not as it's drawn
    this.feed.push({ id: ++this.ids, st: st ? st.index : null, ev, cat: KIND[ev.event] ?? "ops" });
    if (this.feed.length > 3000) this.feed.splice(0, this.feed.length - 3000);
    bump("feed");
  }

  shown(e: FeedEntry) {
    return this.filters.has(e.cat) && !(this.localOnly && e.st !== null && e.st !== this.sel);
  }

  logLine(line: Line) {
    this.log.push({ id: ++this.ids, line: line.map(([t, c, b]) => [redactText(t, false), c, b]) });
    if (this.log.length > 5000) this.log.splice(0, this.log.length - 5000);
    bump("log");
  }

  logJson(v: unknown) {
    this.log.push({ id: ++this.ids, json: v });
    if (this.log.length > 5000) this.log.splice(0, this.log.length - 5000);
    bump("log");
  }

  clearLog() {
    this.log = [];
    bump("log");
  }

  // --- alerts and the condition ------------------------------------------------------------------------------------
  notify(text: string, title = "", severity = "information", timeout = 6) {
    const t: Toast = { id: ++this.ids, title: redactText(title, false), text: redactText(text, false), severity, until: performance.now() + timeout * 1000 };
    this.toasts = [...this.toasts.slice(-5), t];
    bump("toasts");
    window.setTimeout(() => {
      this.toasts = this.toasts.filter((x) => x !== t);
      bump("toasts");
    }, timeout * 1000);
  }

  private chime = new Audio(chime);
  bell() {
    this.chime.currentTime = 0;
    this.chime.play().catch(() => { /* no sound to be had */ });
  }

  /** Beep, pulse the station's card, and raise the condition. Unseen unless the operator is looking at that station
   *  or at the feed. No station: the host's. */
  alert(st: Station | null) {
    this.bell();
    this.alertAt = performance.now();
    if (st) {
      st.flash = this.alertAt + 3000;
      if (st !== this.cur && this.tab !== "intercepts") st.alerts++;
    }
    bump("mast", "cards");
  }

  get unseen() { return this.stations.reduce((a, s) => a + s.alerts, 0); }

  /** RED while an alert is unseen and for a moment after any; AMBER while a station is down, the host is short of
   *  memory, or something installed from Forge was withdrawn and nobody has acknowledged it; else GREEN. */
  condition(): ["RED" | "AMBER" | "GREEN", string] {
    const badges = this.plugins.map((p) => badgeOf(p)?.level);
    if (this.unseen || performance.now() - this.alertAt < 15_000 || badges.includes("alert")) return ["RED", RED];
    if (!this.stations.every((s) => s.online) || this.forgeState.alarms?.length || this.health.short || badges.includes("warn")) return ["AMBER", AMBER];
    return ["GREEN", GREEN];
  }

  private tick() {
    this.runner.tick();
    if (this.banner && performance.now() > this.banner.until) this.banner = null;
    const latest = this.findings.recent().find((r) => r.at > this.findings.acknowledgedAt);
    if (latest && Date.now() / 1000 - latest.at <= 300) this.sup.last = redactText(`${this.stations[Number(latest.server)]?.label ?? latest.server}: ${latest.title} · F8`, this.redact);
    else if (this.sup.last.endsWith(" · F8")) this.sup.last = "";
    bump("mast", "cards", "clock");
  }

  // --- labels ------------------------------------------------------------------------------------------------------
  /** Server names usually share a community prefix ("Some Clan | OCE ..."): show only what differs. */
  relabel() {
    const reported = this.stations.map((s) => String(s.info?.server ?? ""));
    const names = reported.filter(Boolean);
    const pre = new Set(names).size > 1 ? commonPrefix(names) : "";
    const cut = Math.max(pre.lastIndexOf(" "), pre.lastIndexOf("|")) + 1;
    const tags = new Map<string, number>();
    for (const n of names) {
      const t = splitTag(stripEnds(n.slice(cut), " |·-"))[0];
      tags.set(t, (tags.get(t) ?? 0) + 1);
    }
    this.stations.forEach((st, i) => {
      const n = reported[i];
      if (n && !st.name) {
        st.label = stripEnds(n.slice(cut), " |·-") || n;
        const [tag, own] = splitTag(st.label);
        if (tag && (tags.get(tag) ?? 0) > 1) [st.label, st.tag] = [own, tag];
        else st.tag = "";
      }
    });
    const seen = new Map<string, number>();
    for (const st of this.stations) seen.set(st.label, (seen.get(st.label) ?? 0) + 1);
    for (const st of this.stations) if (st.tag && (seen.get(st.label) ?? 0) > 1) [st.label, st.tag] = [`${st.tag} · ${st.label}`, ""];
    this.measureLabels();
    for (const st of this.stations) this.runner.engine.lobby(st.where, st.label);
    invoke("set_labels", { labels: this.stations.map((s) => s.label) }).catch(() => {});
    bump("cards", "feed", "ui");
  }

  measureLabels() {
    this.labelWidth = Math.max(4, Math.min(18, Math.max(4, ...this.stations.map((s) => s.label.length))));
  }

  tunnelOf(st: Station) {
    return st.ssh && !st.url ? this.tunnels.get(st.ssh) : undefined;
  }

  /** Why a station isn't online, looking through to its SSH tunnel when that's what's holding it up. */
  why(st: Station, short = true): string {
    const tun = this.tunnelOf(st);
    if (tun && tun.state !== "up" && (st.state === "connecting" || st.state === "offline")) {
      if (tun.state === "down") return explain(tun.detail, "offline", short);
      return short ? "OPENING SSH TUNNEL" : "Opening the SSH tunnel…";
    }
    if (st.state === "connecting") {
      if (this.waiting.includes(st.index)) return short ? "AWAITING PASSWORD" : "Waiting for the RCON password.";
      return short ? "CONNECTING…" : "Connecting…";
    }
    return explain(st.detail, st.state, short);
  }

  /** Where a station's sign-in stands, for the boot log: [word, colour, settled]. */
  link(st: Station, spin = "◌"): [string, string, boolean] {
    const tun = this.tunnelOf(st);
    if (st.online) return ["SECURE", GREEN, true];
    if (st.state === "connecting" && !(tun && tun.state === "down") && !this.waiting.includes(st.index))
      return [`${spin} ${tun && tun.state !== "up" ? "AWAITING TUNNEL" : "HANDSHAKE"}`, AMBER, false];
    return [this.why(st), RED, true];
  }

  select(i: number) {
    if (i === this.sel || i < 0 || i >= this.stations.length) return;
    this.sel = i;
    this.cur.alerts = 0;
    this.plugins.forEach((p) => (p.due = 0)); // their views are per server: ask again for this one
    this.fetch(this.cur, "status", "players", "nextmap", "vote", "bans", "vpn", "admins");
    bump("cards", "roster", "ops", "bans", "forge", "mast", "ui", ...(this.localOnly ? ["feed" as const] : []));
  }

  setTab(t: Tab) {
    this.tab = t;
    if (t === "health") this.banner = null;
    if (t === "intercepts" && this.unseen) this.stations.forEach((s) => (s.alerts = 0));
    bump("ui", "mast", "cards");
  }

  toggleRedact() {
    this.redact = !this.redact;
    if (this.redact) this.sup.last = "";
    bump("roster", "ops", "bans", "feed", "health", "ui", "forge");
    this.notify(this.redact ? "Addresses redacted." : "Addresses visible. Mind your stream.", "REDACTION", this.redact ? "information" : "warning");
  }

  refresh() {
    if (this.tab === "health") invoke("health_now").catch(() => {});
    if (this.tab === "plugins") {
      this.plugins.forEach((p) => (p.due = 0));
      this.pluginUpdates();
    }
    this.fetch(this.cur, "status", "players", "maps", "modes", "nextmap", "vote", "bans", "vpn", "admins");
    for (const st of this.stations) this.fetch(st, "status");
  }

  async reconnect(st: Station, password?: string) {
    await invoke("reconnect", { index: st.index, password: password ?? null });
  }

  async copy(text: string, title: string) {
    try {
      await writeText(text);
      this.notify(text, title);
    } catch (e) {
      this.notify(String(e), "COPY FAILED", "warning");
    }
  }

  // --- plug-ins (F10) ----------------------------------------------------------------------------------------------
  /** How a plug-in names a server: its name in the config, or its address. Not the label, which changes once the
   *  server reports its own name. */
  serverName(st?: Station) { return st ? st.name || st.where : null; }
  /** The selected server as a plug-in sees it, or null while it's offline. */
  snapshot(p: Plugin) { return this.cur?.online ? pluginSnapshot(this.cur.data.status ?? {}, this.cur.players, !!p.m.addresses) : null; }

  /** The list from the backend, keeping what each already said; `due` for any it hasn't seen before. */
  async loadPlugins(due = 0) {
    try {
      const list = await invoke<Dict[]>("plugins_list");
      const had = new Map(this.plugins.map((p) => [p.m.id, p]));
      this.plugins = list.map((m) => Object.assign(had.get(m.id) ?? { view: null, error: "", busy: false, asked: false, due, note: "", replies: [], session: null, pages: new Set<(msg: Dict) => void>(), update: null }, { m }));
    } catch { /* no console running */ }
    const servers = this.stations.map((s) => ({ index: s.index, name: this.serverName(s) }));
    for (const p of this.plugins) {
      if (p.m.mode === "session" && !p.m.state && !this.demo) invoke("plugin_session_start", { id: p.m.id, servers }).catch((e) => (p.error = String(e)));
    }
    this.pluginUpdates();
    bump("plugins", "mast");
  }

  /** Fetch each plug-in's git upstream (at start, a window reload, and Ctrl+R on F10); offline or not a clone is just
   *  no update. The same upstream commit is offered once. */
  pluginUpdates() {
    for (const p of this.demo ? [] : this.plugins.filter((x) => !x.m.error)) {
      invoke<Dict | null>("plugin_update_check", { id: p.m.id }).then((u) => {
        p.update = u ? { ...(u as NonNullable<Plugin["update"]>), asked: p.update?.to === u.to && !!p.update?.asked } : null;
        bump("plugins");
      }, () => {});
    }
  }

  /** Offer its git upstream's new commits, and on yes fast-forward to them and start it afresh. */
  async pluginUpdate(p: Plugin) {
    const u = p.update, name = String(p.m.name || p.m.id);
    if (!u) return;
    u.asked = true;
    const body = `${u.behind} new commit${u.behind === 1 ? "" : "s"} from ${u.source || "its repository"}:\n\n${u.log.join("\n")}${u.behind > u.log.length ? "\n…" : ""}`
      + "\n\nThe new code runs as you, like the old. A change to its manifest asks for your approval again.";
    if (!(await this.confirm(`UPDATE ${name.toUpperCase()}?`, redactText(body, this.redact), "UPDATE", false))) return bump("plugins");
    try {
      await invoke("plugin_update", { id: p.m.id });
    } catch (e) {
      return this.notify(redactText(String(e), this.redact), `${name.toUpperCase()} · NOT UPDATED`, "error", 12);
    }
    [p.update, p.session, p.view, p.due] = [null, null, null, 0];
    this.notify(`Updated to ${u.to}.`, name.toUpperCase());
    await this.loadPlugins();
  }

  private plugin(id: string) { return this.plugins.find((p) => p.m.id === id); }

  /** What a session plug-in said by itself: how its process is, a view, a toast, a log line, or RCON to send. */
  private onPlugin(m: Dict) {
    const p = this.plugin(m.id);
    if (!p) return;
    const name = String(p.m.name || p.m.id);
    const head: Line = [[new Date().toTimeString().slice(0, 9), DIM], [`plug-in ${p.m.id} `, CYAN]];
    if (m.kind === "plugin-session") {
      const was = p.session?.state;
      p.session = { state: m.state, detail: m.detail, retry_in: m.retry_in };
      if (m.state === "running") p.due = 0;
      else if (was === "running") this.logLine([...head, [redactText(`stopped: ${m.detail}; restarting in ${m.retry_in}s`, this.redact), AMBER]]);
    } else if (m.kind === "plugin-view") {
      if (m.view.error) p.error = redactText(m.view.error, true);
      else [p.view, p.error] = [m.view, ""];
    } else if (m.kind === "plugin-notify") {
      this.notify(redactText(m.text, this.redact), name.toUpperCase(), m.level);
    } else if (m.kind === "plugin-log") {
      this.logLine([...head, [redactText(m.text, this.redact), m.warn ? AMBER : DIM]]);
    } else if (m.kind === "plugin-rcon") {
      this.pluginRcon(p, m).then((r) => invoke("plugin_send", { id: p.m.id, msg: { type: "rcon-reply", id: m.ref, ok: r.ok, text: r.text, data: r.data } }).catch(() => {}));
    }
    bump("plugins", "mast");
  }

  /** Send one RCON command a plug-in asked for (already through its permissions), log it beside what's typed by hand,
   *  and give back what it may hear of the answer. */
  async pluginRcon(p: Plugin, x: Dict): Promise<Dict> {
    const st = this.stations.find((s) => this.serverName(s) === x.server);
    if (!st) return pluginReply(x, { ok: false, text: "no such server" }, true);
    const res = await this.call(st, x.command, x.args);
    this.logLine([[new Date().toTimeString().slice(0, 9), DIM], [`${st.label} `, CYAN], [redactText(`plug-in ${p.m.id} ▸ ${cmdLine(x.command, x.args)}`, this.redact), WHITE]]);
    this.logLine([["  "], [this.replyText(res), res.ok ? GREEN : res.ok === null ? AMBER : RED]]);
    return pluginReply(x, res, !!p.m.addresses);
  }

  /** RCON its page asked for ({oni:"rcon", server?, command, args}): the same filter as an action's, on the selected
   *  server unless it names one. */
  async pluginPageRcon(p: Plugin, m: Dict): Promise<Dict> {
    const want = { server: m.server == null ? this.serverName(this.cur) : String(m.server), command: m.command, args: Array.isArray(m.args) ? m.args : [] };
    const n = await invoke<Dict>("plugin_filter", { id: p.m.id, rcon: want, servers: this.stations.map((s) => this.serverName(s)) })
      .catch((e) => ({ rcon: [], dropped: [String(e)] }));
    if (n.rcon.length) return this.pluginRcon(p, n.rcon[0]);
    const why = String(n.dropped[0] ?? "refused");
    this.logLine([[new Date().toTimeString().slice(0, 9), DIM], [redactText(`plug-in ${p.m.id}'s page asked for ${why}: not sent`, this.redact), AMBER]]);
    return pluginReply(want, { ok: false, text: `not sent: ${why}` }, true);
  }

  /** A server event, to each plug-in whose manifest asks for its kind: its session process and its pages. */
  private pluginEvent(st: Station, raw: Dict) {
    for (const p of this.plugins) {
      if (p.m.state || !(p.m.builtin || p.m.events?.includes(raw.event))) continue; // the built-in Studio hears every kind
      const msg = { server: this.serverName(st), ev: redactData(raw, !p.m.addresses) };
      if (p.session?.state === "running") invoke("plugin_send", { id: p.m.id, msg: { type: "event", ...msg } }).catch(() => {});
      p.pages.forEach((f) => f({ oni: "event", ...msg }));
    }
  }

  /** Ask the plug-ins that are due: with F10 open every one that runs, every 30 s; shut, only one whose badge feeds
   *  CONDITION, every 60 s (and each once after start, to learn whether it has a badge). */
  private pluginTick() {
    const now = performance.now(), shown = this.tab === "plugins";
    for (const p of this.plugins) if (!p.m.state && !p.busy && now >= p.due && (shown || !p.asked || badgeOf(p))) this.pluginView(p);
  }

  async pluginView(p: Plugin) {
    if (p.busy) return; // a page's refresh during the tick's ask: two at once would hand it the same replies twice
    p.busy = true;
    bump("plugins");
    const server = this.serverName(this.cur), told = p.replies;
    try {
      const v = await invoke<Dict>("plugin_view", { id: p.m.id, server, snapshot: this.snapshot(p), replies: told });
      p.replies = p.replies.filter((x) => !told.includes(x)); // it heard them, whatever it made of them
      if (v.error) p.error = v.error;
      else [p.view, p.error] = [v, ""];
    } catch (e) {
      p.error = String(e);
    }
    [p.busy, p.asked] = [false, true];
    // the server changed while it was asked: ask again for the one selected now
    p.due = server !== this.serverName(this.cur) ? 0 : performance.now() + (this.tab === "plugins" ? PLUGIN_EVERY : PLUGIN_BADGE_EVERY);
    bump("plugins", "mast");
  }

  /** What a plug-in is and may do, as approval shows it. */
  pluginTerms(m: Dict) {
    return redactText(`${m.name} ${m.version}`.trim() + (m.author ? ` by ${m.author}` : "") + (m.description ? `\n${m.description}` : "")
      + `\n\nRuns   ${m.exec}\nOn     ${m.host || "this computer"}\nMay have Oni send   ${m.rcon.join(", ") || "nothing"}`
      + `\nSees player addresses   ${m.addresses ? "yes" : "no"}\n\nOni doesn't sandbox it: it runs as you and can do whatever you can. `
      + "This approval holds until its manifest changes.", this.redact);
  }

  async pluginApprove(p: Plugin) {
    if (!(await this.confirm(`RUN THE PLUG-IN ${String(p.m.name || p.m.id).toUpperCase()}?`, this.pluginTerms(p.m), "APPROVE"))) return;
    try {
      await invoke("plugin_approve", { id: p.m.id });
    } catch (e) {
      return this.notify(String(e), "APPROVAL NOT SAVED", "error");
    }
    p.due = 0;
    await this.loadPlugins();
  }

  // --- onboarding: get a plug-in, turn it on, approve it ------------------------------------------------------------
  /** Where plug-ins go, and the folders already there that the config doesn't turn on (F10's "add a plug-in"). */
  pluginsDir = "";
  pluginsFound: Dict[] = [];

  async pluginScan() {
    try {
      const r = await invoke<Dict>("plugins_found");
      [this.pluginsDir, this.pluginsFound] = [r.dir, r.found];
    } catch {
      [this.pluginsDir, this.pluginsFound] = ["", []]; // the demo, or no console running
    }
    bump("plugins");
  }

  /** Put a folder of plugins/ in the config, select it and go straight to its approval. False if it didn't get on. */
  async pluginTurnOn(id: string) {
    try {
      await invoke("plugin_enable", { id, on: true });
    } catch (e) {
      this.notify(redactText(String(e), this.redact), "PLUG-IN NOT TURNED ON", "error", 12);
      return false;
    }
    await this.loadPlugins();
    await this.pluginScan();
    const p = this.plugin(id);
    if (!p) return true;
    this.psel = this.plugins.indexOf(p);
    bump("plugins");
    if (p.m.state === "needs approval") await this.pluginApprove(p);
    return true;
  }

  /** Ask for a git address, clone it into plugins/ and turn it on. Downloading runs nothing: approval comes next. */
  async pluginInstall() {
    const got = await this.form("ADD A PLUG-IN", [["url", "Git address  (https://…, ssh://… or git@host:path)", ""]], { verb: "NEXT", danger: false });
    const url = String(got?.url ?? "").trim();
    if (!url) return false;
    const shown = url.replace(/\/\/[^/@]*@/, "//"); // never echo a login
    return this.pluginDownload(`git clone ${shown}`, () => invoke<string>("plugin_install", { url }));
  }

  /** Install one the plug-in index lists (by id: the backend holds its address) and turn it on, approval next. */
  pluginIndexInstall(e: Dict) {
    const from = `${e.url}${e.path ? `, folder ${e.path}` : ""}${e.commit ? `, commit ${String(e.commit).slice(0, 12)}` : ""}`;
    return this.pluginDownload(`${e.name} from the oni-rcon index:\n${from}`, () => invoke("plugin_index_install", { id: e.id }).then(() => String(e.id)));
  }

  private async pluginDownload(what: string, get: () => Promise<string>) {
    if (!(await this.confirm("DOWNLOAD THIS PLUG-IN?", `${what}\ninto ${this.pluginsDir}\n\nDownloading doesn't run it. You are asked to approve it next, with what it runs and what it may do.`, "DOWNLOAD", false))) return false;
    let id: string;
    try {
      id = await get();
    } catch (e) {
      this.notify(redactText(String(e), this.redact), "PLUG-IN NOT DOWNLOADED", "error", 12);
      return false;
    }
    return this.pluginTurnOn(id);
  }

  /** Take it out of the config's plugins list and stop it. Its folder and approval stay, so turning it on again is one click. */
  async pluginTurnOff(p: Plugin) {
    const name = String(p.m.name || p.m.id);
    if (!(await this.confirm(`TURN OFF ${name.toUpperCase()}?`, `It stops, and ${p.m.id} comes out of plugins = [...] in your config. Its folder and your approval stay: turn it on again from ADD A PLUG-IN.`, "TURN OFF", false))) return;
    try {
      await invoke("plugin_enable", { id: p.m.id, on: false });
    } catch (e) {
      return this.notify(redactText(String(e), this.redact), "NOT TURNED OFF", "error", 12);
    }
    this.psel = 0;
    await this.loadPlugins();
    await this.pluginScan();
  }

  /** Its params asked for, then its confirm if it has one (ABORT is the default), then run. Esc or ABORT at either
   *  step sends nothing. Then: toast what it said, send the RCON its permissions allow, and show the view it sent back
   *  or ask for a fresh one. */
  /** Run one of its actions: its form (unless `given` answers it, from the plug-in's own page), confirm, then RCON. */
  async pluginAction(p: Plugin, a: Dict, row: string | null = null, given?: Dict) {
    if (p.m.state) return;
    const title = `${p.m.name || p.m.id} › ${a.label}`.toUpperCase();
    let params: Dict = {};
    if (a.params.length) {
      const got = given ? Object.fromEntries(a.params.map((x: Dict, i: number) => [`p${i}`, given[x.name] ?? ""])) : await this.form(title, a.params.map((x: Dict, i: number) => [`p${i}`, x.label + (x.type === "duration" ? "  (10m, 1d)" : ""),
        x.type === "bool" ? [["No", "false"], ["Yes", "true"]] : x.type === "choice" ? x.options.map((o: string) => [o, o]) : ""]), { verb: "RUN", danger: a.danger });
      if (!got) return;
      const typed = typedParams(a.params, got);
      if (typeof typed === "string") return this.notify(typed, title, "warning");
      params = typed;
    }
    if ((a.danger || a.confirm) && !(await this.confirm(title, a.confirm || `Run ${a.label}?`, a.label.toUpperCase().slice(0, 24), a.danger))) return;
    const r: Dict = await invoke<Dict>("plugin_action", { id: p.m.id, action: a.id, params, row, server: this.serverName(this.cur), snapshot: this.snapshot(p),
      servers: this.stations.map((s) => this.serverName(s)) }).catch((e) => ({ ok: false, toast: "", error: String(e), view: null, rcon: [], dropped: [] }));
    const words = redactText(r.toast || r.error || (r.ok ? "done" : "failed"), this.redact);
    p.note = `${new Date().toTimeString().slice(0, 8)}  ${a.label}: ${words}`;
    this.notify(words, title, r.ok ? "information" : "error");
    for (const d of r.dropped) {
      this.notify(`Not sent: ${d}.`, title, "warning");
      this.logLine([[new Date().toTimeString().slice(0, 9), DIM], [redactText(`plug-in ${p.m.id} asked for ${d}: not sent`, this.redact), AMBER]]);
    }
    const replies: Dict[] = [];
    for (const x of r.rcon) {
      const res = await this.pluginRcon(p, x);
      replies.push(res);
      if (!res.ok) this.notify(redactText(res.text || "no reply", this.redact), `${x.server} · ${x.command.toUpperCase()}`, res.ok === null ? "warning" : "error");
    }
    if (replies.length) {
      p.replies = [...p.replies, ...replies].slice(-20); // ponytail: the last 20 only; one that sends more reads its own logs
      p.pages.forEach((f) => f({ oni: "rcon-reply", replies }));
    }
    if (r.view && !r.view.error) [p.view, p.error] = [r.view, ""];
    if (!r.view || r.view.error || replies.length) p.due = 0; // a fresh view tells it what the servers answered
    bump("plugins", "mast");
  }

  // --- dialogs -----------------------------------------------------------------------------------------------------
  ask<T = any>(kind: string, props: Dict): Promise<T> {
    return new Promise((resolve) => {
      const d: Dialog = { id: ++this.ids, kind, props, resolve: (v) => {
        this.dialogs = this.dialogs.filter((x) => x !== d);
        bump("dialog");
        resolve(v);
      } };
      this.dialogs = [...this.dialogs, d];
      bump("dialog");
    });
  }
  confirm(title: string, body: string, verb = "EXECUTE", danger = true) {
    return this.ask<boolean>("confirm", { title, body, verb, danger });
  }
  form(title: string, fields: [string, string, string | [string, string][]][], o: Dict = {}) {
    return this.ask<Dict | null>("form", { title, fields, ...o });
  }
  pick(title: string, options: [Line | string, string][]) {
    return this.ask<string | null>("pick", { title, options });
  }
}

export const oni = new Oni();
(window as any).oni = oni;
