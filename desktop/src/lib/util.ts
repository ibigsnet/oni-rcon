// Reading tolerant: field names come from the docs, so every lookup accepts the plausible spellings. The rest is
// what the terminal console's app.py did to turn replies and events into words, unchanged in meaning.
import { AMBER, CYAN, DIM, GOLD, GREEN, GREY, INK, RED, TEAMS, TEAM_COLOR, WHITE } from "../art/palette";

export type Dict = Record<string, any>;
export type Span = [string, string?, string?]; // text, colour, background
export type Line = Span[];

export function pick(d: unknown, ...keys: string[]): any {
  if (d && typeof d === "object" && !Array.isArray(d)) {
    for (const k of keys) {
      const v = (d as Dict)[k];
      if (v !== null && v !== undefined && v !== "") return v;
    }
  }
  return undefined;
}

/** v as a number, or undefined. A count may come as the list of what it counts. */
export function num(v: unknown): number | undefined {
  if (typeof v === "number") return v;
  if (Array.isArray(v)) return v.length;
  return undefined;
}

export const plain = (l: Line) => l.map((s) => s[0]).join("");

export function teamOf(p: Dict): string {
  const t = pick(p, "team", "team_name", "color");
  if (typeof t === "number" && t >= 0 && t < TEAMS.length) return TEAMS[t];
  return t !== undefined ? String(t).toLowerCase() : "";
}

/** Player ID first: numbers shift when someone leaves between a refresh and a keypress. */
export function targetOf(p: Dict): string {
  const pid = pick(p, "player_id", "id", "xuid");
  if (pid) return String(pid);
  const n = pick(p, "number", "index");
  return n !== undefined ? `#${n}` : String(pick(p, "name") ?? "?");
}

export function who(v: unknown, names: Map<unknown, string> = new Map()): string {
  if (v && typeof v === "object") return String(pick(v, "name") ?? names.get(pick(v, "engine_id")) ?? "?");
  if (typeof v === "number") return names.get(v) ?? `#${v}`;
  return v !== undefined && v !== null && v !== "" ? String(v) : "?";
}

const ID_KEY = /(^|_)(id|xuid|uid|guid|uuid)$/i;
export const ID_LIKE = /^(\d{6,}|[0-9a-f]{16,})$/i;
const SLOT = 1n << 64n;

/** Every spelling an ID can take: as written, the same number in decimal and in hex, and for a long hex ID either
 *  64-bit half of it. A kill names its players by one of a roster entry's IDs, in whichever base the server picks. */
export function idForms(v: unknown): Set<string> {
  const out = new Set<string>();
  if (typeof v !== "number" && typeof v !== "string") return out;
  if (v === "") return out;
  const s = String(v).trim().toLowerCase();
  out.add(s);
  if (/^-?\d+$/.test(s)) {
    let n = BigInt(s) % SLOT;
    if (n < 0n) n += SLOT;
    out.add(n.toString());
    out.add(n.toString(16));
  } else if (/^[0-9a-f]{5,}$/.test(s)) {
    out.add(BigInt("0x" + s).toString());
    if (s.length > 16) for (const half of [s.slice(0, 16), s.slice(-16)]) {
      out.add(half);
      out.add(BigInt("0x" + half).toString());
    }
  }
  return out;
}

export function playerIds(p: Dict): Set<string> {
  const out = new Set<string>();
  for (const [k, v] of Object.entries(p)) if (ID_KEY.test(k)) for (const f of idForms(v)) out.add(f);
  return out;
}

const REFS = ["killer", "victim", "name", "player", "from", "sender"];

/** A copy of the event that names its players outright, read against the roster when it arrives. */
export function resolve(ev: Dict, names: Map<unknown, string>, ident?: (v: unknown) => string | null): Dict {
  const out: Dict = {};
  for (const [k, v] of Object.entries(ev)) {
    const n = (k === "killer" || k === "victim") && ident ? ident(v) : null;
    if (n !== null) out[k] = n;
    else if (REFS.includes(k) && (typeof v === "number" || (v && typeof v === "object"))) out[k] = who(v, names);
    else out[k] = v;
  }
  return out;
}

/** [team, players, total score] per team, in the engine's team order. */
export function tally(players: Dict[]): [string, number, number][] {
  const teams = new Map<string, [number, number]>();
  for (const p of players) {
    const t = teamOf(p);
    if (!t) continue;
    const e = teams.get(t) ?? [0, 0];
    e[0]++;
    e[1] += num(pick(p, "score")) ?? 0;
    teams.set(t, e);
  }
  const order = (t: string) => (TEAMS.includes(t) ? TEAMS.indexOf(t) : TEAMS.length);
  return [...teams.entries()].sort((a, b) => order(a[0]) - order(b[0])).map(([t, [n, s]]) => [t, n, s]);
}

/** The teams, or none in a free-for-all, which can put every player on a team of their own. */
export function sides(players: Dict[]) {
  const t = tally(players);
  return t.length > 2 && t.every(([, n]) => n === 1) ? [] : t;
}

export function gauge(v: number, width: number, color: string): Line {
  const n = Math.round(Math.max(0, Math.min(1, v)) * width);
  return [["▰".repeat(n), color], ["▱".repeat(width - n), DIM]];
}

export function bar(v: unknown, width = 5): Line {
  if (typeof v !== "number") return [["—", DIM]];
  return gauge(v, width, v > 0.6 ? GREEN : v > 0.3 ? AMBER : RED);
}

const BARS = "▁▂▃▄▅▆▇█";
export function spark(values: number[], color = CYAN): Line {
  const top = Math.max(0, ...values) || 1;
  return values.map((v) => [BARS[Math.max(0, Math.min(7, Math.ceil((v / top) * 8) - 1))], v > 0 ? color : "#1C242E"] as Span);
}

export function splitBar(parts: [number, string][], width: number): Line {
  let total = parts.reduce((a, [v]) => a + v, 0);
  if (!total) {
    parts = parts.map(([, c]) => [1, c]);
    total = parts.length;
  }
  let used = 0;
  return parts.map(([v, c], i) => {
    const n = i === parts.length - 1 ? width - used : Math.min(width - used, Math.round((v / total) * width));
    used += n;
    return ["━".repeat(Math.max(0, n)), c] as Span;
  });
}

export function pingCell(ms: number | undefined): Line {
  return ms === undefined ? [["—", DIM]] : [[String(ms), ms < 100 ? GREEN : ms < 200 ? AMBER : RED]];
}

export function until(ts: unknown): string {
  if (typeof ts !== "number") return "PERMANENT";
  const left = Math.floor(ts - Date.now() / 1000);
  if (left <= 0) return "expired";
  const d = Math.floor(left / 86400), h = Math.floor((left % 86400) / 3600), m = Math.floor((left % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}

const pad = (n: number) => String(n).padStart(2, "0");
export const hms = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;

/** HH:MM:SS for an event's time, in seconds or milliseconds; now when it has none that reads. */
export function clock(ts: unknown): string {
  if (typeof ts === "number" && isFinite(ts)) return hms(new Date(ts > 1e11 ? ts : ts * 1000));
  return hms(new Date());
}

/** The time of day of an epoch time, with the date when it wasn't today. */
export function when(ts: number): string {
  const t = new Date(ts * 1000), now = new Date();
  return t.toDateString() === now.toDateString() ? hms(t) : `${pad(t.getMonth() + 1)}-${pad(t.getDate())} ${hms(t)}`;
}

export function age(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${pad(m)}m` : m ? `${m}m ${pad(s % 60)}s` : `${s}s`;
}

/** Epoch seconds from a server time: seconds, milliseconds or an ISO string; null when it doesn't read. */
export function epoch(ts: unknown): number | null {
  if (typeof ts === "number" && isFinite(ts)) return ts > 1e11 ? ts / 1000 : ts;
  const ms = typeof ts === "string" ? Date.parse(ts) : NaN;
  return isNaN(ms) ? null : ms / 1000;
}

export const VPN_STALE_DAYS = 7; // the server calls a list stale after a week (0.9.22)
export interface VpnList { name: string; size: string; age: string; stale: boolean; problem: string }

/** The `vpn` reply's lists (0.9.22+): each source and the ASN table, with its age and whether it needs a look. */
export function vpnLists(v: Dict, now = Date.now() / 1000): VpnList[] {
  const l = v.lists;
  if (!l || typeof l !== "object") return [];
  const row = (name: string, size: string, at: unknown, problem: unknown): VpnList => {
    const t = epoch(at);
    return { name, size, age: t === null ? "never" : age(now - t), stale: t === null || now - t > VPN_STALE_DAYS * 86400, problem: problem ? String(problem) : "" };
  };
  const n = (x: unknown, unit: string) => (typeof x === "number" ? `${x} ${unit}` : "");
  const out = ((l.sources ?? []) as Dict[]).map((s) =>
    row(String(s.list ?? "?"), [n(s.ranges, "ranges"), n(s.asns, "ASNs")].filter(Boolean).join(" · "), s.fetched_at, s.problem));
  const t = l.table as Dict | null | undefined;
  if (t && typeof t === "object") out.push(row(`ASN table${t.table ? ` (${t.table})` : ""}`, [n(t.rows, "rows"), n(t.blocks, "blocks")].filter(Boolean).join(" · "),
    t.fetched_at, t.problem));
  return out;
}

/** CHECK IP's toast: the server's answer, plus the list it matched and fail-closed when the reply has them (0.9.22+). */
export function vpnCheck(text: string, d: unknown): string {
  if (!d || typeof d !== "object") return text;
  const x = d as Dict, extra: string[] = [];
  if (x.list) extra.push(`matched ${x.list}`);
  if (x.fail_closed && x.refused && !x.listed && !x.local && !x.allowed) extra.push("refused because no list is loaded yet (fail_closed)");
  return [text, ...extra].join(" · ");
}

const KEY_LIKE = /rfk_[A-Za-z0-9_\-]{6,}/g;
const IPV4 = /\b(?:\d{1,3}\.){3}\d{1,3}\b/g;
export const BLANK = "███.███.███.███";
const ADDRESS_KEYS = new Set(["address", "ip", "ip_address", "addr"]);

export const scrub = (s: string) => s.replace(KEY_LIKE, "rfk_…████");
export const redactAddr = (v: unknown, on: boolean) => (on && v ? BLANK : String(v || "—"));
export const redactText = (s: string, on: boolean) => (on ? scrub(s).replace(IPV4, BLANK) : scrub(s));

export function redactData(v: unknown, on: boolean): unknown {
  if (typeof v === "string") return redactText(v, on);
  if (Array.isArray(v)) return v.map((x) => redactData(x, on));
  if (v && typeof v === "object") {
    return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, on && ADDRESS_KEYS.has(k) && typeof x === "string" ? redactAddr(x, on) : redactData(x, on)]));
  }
  return v;
}

/** What a plug-in is told of a server as it stands: its roster, map, mode and team scores. Addresses are blanked
 *  unless the plug-in's manifest grants `addresses`. */
export function pluginSnapshot(status: Dict, players: Dict[], addresses: boolean): Dict {
  return { players: redactData(players, !addresses), map: status.map ?? null, mode: status.mode ?? null,
    score: sides(players).map(([team, players, score]) => ({ team, players, score })) };
}

/** What a plug-in hears back from one RCON command it had Oni send: the server's answer, with addresses blanked
 *  unless its manifest grants them. */
export function pluginReply(x: Dict, r: { ok: boolean | null; text?: string; data?: unknown }, addresses: boolean): Dict {
  return { server: x.server, command: x.command, args: x.args, ok: r.ok, text: redactText(String(r.text ?? ""), !addresses), data: redactData(r.data ?? null, !addresses) };
}

/** A plug-in action's form answers (keyed p0, p1, ... in its params' order) as the contract types them: number as a
 *  JSON number, bool as true/false, the rest strings. A number that isn't one gives the words to say, and nothing is sent. */
export function typedParams(params: Dict[], got: Dict): Dict | string {
  const out: Dict = {};
  for (const [i, p] of params.entries()) {
    const v = String(got[`p${i}`] ?? "").trim();
    if (p.type === "number" && (v === "" || !Number.isFinite(Number(v)))) return `${p.label} must be a number: nothing was sent.`;
    out[p.name] = p.type === "number" ? Number(v) : p.type === "bool" ? v === "true" : v;
  }
  return out;
}

export function clipId(v: unknown): string {
  const s = String(v);
  return s.length > 16 && /^[0-9a-fA-F-]+$/.test(s) ? s.slice(0, 8) + "…" : s;
}

const q = (a: string) => (!a || a.includes(" ") ? `"${a}"` : a);
export const cmdLine = (command: string, args: string[]) => [command, ...args.map(q)].join(" ");

const FREE_TEXT: Record<string, number> = { say: 0, tell: 1, kick: 1, servername: 0 };

/** A console line as the command and its arguments. Quotes group words as in a shell, except in the message a say,
 *  tell, kick or rename carries: that's the rest of the line as typed. */
export function parseCommand(line: string): string[] {
  let i = 0;
  const word = (): string | null => {
    while (i < line.length && /\s/.test(line[i])) i++;
    if (i >= line.length) return null;
    let out = "", quote = "";
    while (i < line.length) {
      const c = line[i];
      if (quote) {
        if (c === quote) quote = "";
        else if (c === "\\" && quote === '"' && i + 1 < line.length && '"\\'.includes(line[i + 1])) out += line[++i];
        else out += c;
      } else if (c === '"' || c === "'") quote = c;
      else if (/\s/.test(c)) break;
      else if (c === "\\" && i + 1 < line.length) out += line[++i];
      else out += c;
      i++;
    }
    if (quote) throw new Error("No closing quotation");
    return out;
  };
  const first = word();
  if (first === null) return [];
  const words = [first];
  const lead = FREE_TEXT[first.toLowerCase()];
  if (lead === undefined) {
    for (let w = word(); w !== null; w = word()) words.push(w);
    return words;
  }
  while (words.length <= lead) {
    const w = word();
    if (w === null) break;
    words.push(w);
  }
  let rest = line.slice(i).trim();
  if (rest.length > 1 && rest[0] === rest[rest.length - 1] && "'\"".includes(rest[0]) && !rest.slice(1, -1).includes(rest[0])) rest = rest.slice(1, -1);
  return rest ? [...words, rest] : words;
}

const SEPARATORS = [" · ", " | ", " - ", " — ", ": "];

/** ["ALPHA", "Big Team Rockets"] for "ALPHA · Big Team Rockets"; ["", name] when it carries no tag. */
export function splitTag(name: string): [string, string] {
  const at = SEPARATORS.map((sep) => [name.indexOf(sep), sep] as [number, string]).filter(([i]) => i > 0 && i <= 24);
  if (!at.length) return ["", name];
  at.sort((a, b) => a[0] - b[0] || a[1].localeCompare(b[1]));
  const [i, sep] = at[0];
  const own = name.slice(i + sep.length).trim();
  return own ? [name.slice(0, i).trim(), own] : ["", name];
}

export function commonPrefix(xs: string[]): string {
  if (!xs.length) return "";
  let p = xs[0];
  for (const x of xs) while (!x.startsWith(p)) p = p.slice(0, -1);
  return p;
}

export function stripEnds(s: string, chars: string) {
  let a = 0, b = s.length;
  while (a < b && chars.includes(s[a])) a++;
  while (b > a && chars.includes(s[b - 1])) b--;
  return s.slice(a, b);
}

// A connection failure in words to act on: (what the error says, card text, the full explanation)
const EXPLAIN: [string[], string, string][] = [
  [["ssh: connect to host", "could not resolve hostname"], "SSH CAN'T REACH THE BOX", "SSH couldn't reach that machine. Check the user@host spelling, and that SSH runs there."],
  [["host key verification"], "SSH DOESN'T KNOW THE BOX", "SSH hasn't seen that machine before. Connect once with ssh in a terminal to trust it, then retry."],
  [["permission denied", "publickey"], "SSH REFUSED YOUR KEY", "SSH refused your key. Load it into your SSH agent, or name it in ~/.ssh/config."],
  [["no ssh client"], "SSH NOT INSTALLED", "There's no SSH on this computer, so the tunnel can't open."],
  [["connect call failed", "connection refused", "refused the network connection", "errno 111", "10061", "1225", "os error 111"], "NOTHING ON THAT PORT", "Nothing answered on that port. Is the server running, with an RCON password set in dedicated.toml?"],
  [["name or service not known", "getaddrinfo", "nodename nor servname", "no address associated", "11001", "failed to lookup address", "no such host"], "UNKNOWN HOST", "Can't find that host name. Check the spelling."],
  [["network is unreachable", "no route to host"], "NO ROUTE", "Can't reach that network. Check your connection or VPN."],
  [["timed out", "timeout", "10060", "semaphore"], "NO ANSWER", "No answer. Check the address and port, and any firewall in between."],
  [["invalid http", "did not receive a valid http", "rejected websocket", "invalid status", "speak rcon", "http error", "handshake not finished", "missing, duplicated or incorrect header", "protocol error"], "NOT AN RCON PORT", "Something answered, but it isn't RCON. RCON listens on the game port."],
  [["isn't a valid uri", "invalid uri", "scheme isn't", "url error", "unsupported url scheme", "relative url"], "BAD LINK", "That isn't a valid ws:// or wss:// link."],
  [["connection closed", "connection lost", "no close frame", "connection reset", "broken pipe", "already closed"], "CONNECTION DROPPED", "The connection dropped."],
];

/** Server 0.9.18 and later apply dedicated.toml edits without a restart, and `reload` says what changed. */
export function hotReloads(version: unknown): boolean {
  const v = semver(version);
  return !!v && cmpVer(v, [0, 9, 18]) >= 0;
}

/** A version's major.minor.patch ("v0.9.18-rc1" is [0, 9, 18]); null when it isn't one. */
export function semver(version: unknown): number[] | null {
  const m = /^v?(\d+)\.(\d+)\.(\d+)/.exec(String(version ?? "").trim());
  return m ? m.slice(1).map(Number) : null;
}
const cmpVer = (a: number[], b: number[]) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2];

/** The newer Project Reclaimer release a server is behind, as "0.9.18"; "" when it is current, ahead, or either
 *  version is unknown. Servers admit only players on exactly their version, and players' clients update themselves. */
export function behind(have: unknown, latest: unknown): string {
  const [h, l] = [semver(have), semver(latest)];
  return h && l && cmpVer(h, l) < 0 ? l.join(".") : "";
}

/** Release notes' markdown as plain lines: headings in capitals, bullets as •, links as their words, no ** __ or `. */
export const notesText = (md: string) => md.replace(/\r/g, "").split("\n").map((l) => l.replace(/^#{1,6}\s+(.*)/, (_, h: string) => h.toUpperCase())
  .replace(/^(\s*)[-*+]\s+/, "$1• ").replace(/\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/\*\*|__|`/g, "")).join("\n").replace(/\n{3,}/g, "\n\n").trim();

/** "0.9.17 on F1 · 0.9.18 on F2, F3", oldest first, when the stations run different versions; "" when they agree.
 *  Unknown versions don't count. */
export function mixedVersions(stations: { label: string; version: unknown }[]): string {
  const by = new Map<string, { v: number[]; labels: string[] }>();
  for (const s of stations) {
    const v = semver(s.version);
    if (!v) continue;
    const k = v.join(".");
    if (!by.has(k)) by.set(k, { v, labels: [] });
    by.get(k)!.labels.push(s.label);
  }
  if (by.size < 2) return "";
  return [...by].sort((a, b) => cmpVer(a[1].v, b[1].v)).map(([k, g]) => `${k} on ${g.labels.join(", ")}`).join(" · ");
}

/** Someone this station saw kicked: `pardon` (server 0.9.16 or later) lets them back in now. `until` is 0 when unknown. */
export type Kick = { name: string; id: string; ip: string; by: string; reason: string; until: number };

/** Keeps a station's kicked list from its events: a kick adds them, a refused join adds its address (or them, when a
 *  kick from before we connected turned them away), and a pardon takes them off. */
export function noteKick(kicks: Map<string, Kick>, ev: Dict, now = Date.now()): boolean {
  const kind = ev.event, id = String(pick(ev, "id", "player_id") ?? ""), name = String(pick(ev, "name") ?? "");
  const key = id || name, ip = String(pick(ev, "ip") ?? "");
  if (kind === "kick") {
    const s = num(ev.seconds) ?? 0;
    if (s <= 0 || !key) return false; // removed for holding up the next game: not kept out
    kicks.delete(key); // re-added last, so the list runs oldest to newest
    kicks.set(key, { name, id, ip, by: String(ev.by ?? ""), reason: String(ev.reason ?? ""), until: now + s * 1000 });
    while (kicks.size > 50) kicks.delete(kicks.keys().next().value!);
  } else if (kind === "refused") {
    const k = kicks.get(key);
    if (k) k.ip ||= ip;
    else if (key && /kick/i.test(String(ev.reason ?? ""))) kicks.set(key, { name, id, ip, by: "", reason: String(ev.reason), until: 0 });
    else return false;
  } else if (kind === "pardon") {
    const t = String(ev.target ?? ""), names = [ev.name ?? []].flat().map(String);
    const before = kicks.size;
    for (const [k, v] of kicks) if ([v.id, v.ip, v.name].includes(t) || names.includes(v.name)) kicks.delete(k);
    return kicks.size !== before;
  } else return false;
  return true;
}

/** What a pardon is sent with: the player ID, else the address, else the name they were kicked under. */
export const pardonTarget = (k: Kick) => k.id || k.ip || k.name;
/** How long a kick still keeps them out: minutes, "over" (a pardon still resets their next kick) or "?" when unknown. */
export const kickLeft = (k: Kick, now = Date.now()) => (!k.until ? "?" : k.until > now ? `${Math.ceil((k.until - now) / 60000)}m` : "over");

/** Why a station isn't online, for someone who has never seen a socket error. */
export function explain(detail: string, state = "offline", short = false): string {
  const d = (detail || "").replace(/;?\s*retry in \d+s$/, "").trim();
  const low = d.toLowerCase();
  if (state === "denied") {
    if (["too many", "locked", "lockout"].some((k) => low.includes(k)))
      return short ? "LOCKED OUT" : "Locked out after too many wrong passwords. Wait ten minutes, then retry.";
    return short ? "PASSWORD REFUSED" : "The server refused the password. Check it against dedicated.toml [rcon]. Five wrong tries in ten minutes lock you out for a while.";
  }
  for (const [keys, card, text] of EXPLAIN) if (keys.some((k) => low.includes(k))) return short ? card : text;
  return short ? d.toUpperCase() || "NO CARRIER" : d || "Not connected.";
}

export const CATS: Record<string, string> = { chat: WHITE, combat: RED, traffic: CYAN, moderation: AMBER, ops: GREY };
export const KIND: Record<string, string> = { chat: "chat", kill: "combat", join: "traffic", leave: "traffic", refused: "traffic",
  kick: "moderation", ban: "moderation", unban: "moderation", mute: "moderation", unmute: "moderation", cheat: "moderation", pardon: "moderation",
  admin_added: "moderation", admin_removed: "moderation" };
export const GLYPH: Record<string, string> = { health: "⚠", chat: "»", kill: "✕", join: "▲", leave: "▼", refused: "⊘", kick: "◆", ban: "■",
  unban: "□", pardon: "↺", mute: "◈", unmute: "◇", admin_added: "✦", admin_removed: "✧", cheat: "!", vote: "◉", control: "•", uplink: "≡", forge: "⬢" };

/** What an event says, after its time and station. Known kinds get a layout; anything else prints its fields. */
export function describe(ev: Dict, redact = true): Line {
  const kind = String(ev.event ?? "?");
  const cat = KIND[kind] ?? "ops";
  const line: Line = [];
  if (kind === "chat") {
    const ch = String(ev.channel ?? "all");
    if (ch === "server") {
      line.push(["[SERVER] ", AMBER], [redactText(String(ev.text ?? ""), redact), AMBER]);
    } else {
      const team = String(pick(ev, "team") ?? ch.replace(/^team/, "").trim()).toLowerCase();
      line.push([`[${ch.toUpperCase()}] `, DIM], [who(pick(ev, "name", "player", "from", "sender")), TEAM_COLOR[team] ?? WHITE],
        [": " + redactText(String(ev.text ?? ""), redact)]);
    }
  } else if (kind === "kill") {
    const killer = pick(ev, "killer"), victim = pick(ev, "victim");
    const k = who(killer), v = who(victim);
    if (killer === undefined || k === v) line.push([v, WHITE], [" died", DIM]);
    else line.push([k, WHITE], [" ✕ ", RED], [v]);
    const how = pick(ev, "weapon", "damage", "cause", "how");
    if (how) line.push([`  [${how}]`, DIM]);
    for (const m of ev._medals ?? []) line.push(["  "], [` ${m} `, INK, GOLD]);
  } else if (kind === "pardon") {
    const names = [ev.name ?? []].flat().map(String).filter(Boolean);
    line.push(["PARDON", AMBER], [`  ${names.length ? names.join(", ") : redactText(String(ev.target ?? "?"), redact)}`, WHITE], ["  may rejoin", DIM]);
    if (ev.by) line.push(["  by ", DIM], [String(ev.by)]);
  } else if (kind === "admin_added" || kind === "admin_removed") {
    line.push(["ADMIN", AMBER], [`  ${ev.name ? String(ev.name) : redactText(clipId(ev.id ?? "?"), redact)}`, WHITE],
      [kind === "admin_added" ? "  made admin" : "  no longer admin", DIM]);
    if (ev.by) line.push(["  by ", DIM], [String(ev.by)]);
  } else if (kind === "control" && (ev.action === "reload" || ev.action === "reload_applied")) {
    const failed = ev.ok === false; // a reload that changed nothing says why
    line.push([failed ? "RELOAD FAILED" : ev.action === "reload" ? "RELOAD" : "RELOAD APPLIED", failed ? RED : CYAN]);
    if (ev.text) line.push([`  ${redactText(String(ev.text), redact)}`]);
  } else if (kind === "health") {
    line.push(["HEALTH  " + String(ev.text ?? ""), ev.level === "crit" ? RED : AMBER]);
  } else {
    line.push([kind.toUpperCase(), CATS[cat]]);
    const rest: Dict = Object.fromEntries(Object.entries(ev).filter(([k]) => !["type", "event", "time"].includes(k)));
    const name = rest.name ?? rest.player;
    delete rest.name;
    delete rest.player;
    if (name !== undefined && name !== null) line.push([`  ${who(name)}`, WHITE]);
    const text = rest.text;
    delete rest.text;
    if (text) line.push([`  ${redactText(String(text), redact)}`]);
    for (const [k, v] of Object.entries(rest)) {
      const shown = ADDRESS_KEYS.has(k) ? redactAddr(v, redact) : v && typeof v === "object" ? JSON.stringify(redactData(v, redact)) : redactText(clipId(v), redact);
      line.push([`  ${k}=`, DIM], [String(shown)]);
    }
  }
  return line;
}

const UNSAFE = /[\x00-\x08\x0b-\x1f\x7f-\x9f‪-‮⁦-⁩]/g;
export const tidy = (v: unknown, lines = false) => {
  const s = String(v ?? "").replace(UNSAFE, "");
  return lines ? s.trim() : s.split(/\s+/).filter(Boolean).join(" ");
};

/** A few plain words for what the Superintendent reacted to, under its face. */
export function reactionWords(ev: Dict, redact = true): string {
  const kind = String(ev.event ?? "?"), name = who(pick(ev, "name", "player", "from", "sender"));
  if (kind === "kill" && ev._medals?.length) return tidy(`${ev._medals[ev._medals.length - 1]}: ${who(pick(ev, "killer"))}`);
  if (kind === "join") return tidy(`${name} joined`);
  if (["kick", "ban", "mute"].includes(kind)) {
    const reason = pick(ev, "reason", "text");
    return tidy(`${kind} ${name}` + (reason ? `: ${redactText(String(reason), redact)}` : ""));
  }
  if (kind === "chat" && ev.channel !== "server") return tidy(`${name}: ${redactText(String(ev.text ?? ""), redact)}`);
  return tidy(plain(describe(ev, redact)));
}

/** The vote under way: what it's on, and the tally as a tug of war when the server gives one. */
export function voteText(v: unknown): Line[] {
  if (!v) return [[["none under way", DIM]]];
  if (typeof v !== "object") return [[[JSON.stringify(v)]]];
  const head: Line = [[String(pick(v, "subject", "type", "kind") ?? "vote").toUpperCase(), AMBER]];
  const target = pick(v, "target", "player", "name");
  if (target) head.push([` ${target}`]);
  const yes = num(pick(v, "yes", "for")), no = num(pick(v, "no", "against"));
  if (yes === undefined && no === undefined) return [head];
  const y = yes ?? 0, n = no ?? 0;
  return [head, [["YES ", DIM], [`${y} `, GREEN], ...splitBar([[y, GREEN], [n, RED]], 6), [` ${n}`, RED], [" NO", DIM]]];
}

/** dedicated.toml's camera (0.9.20), as host::CAMERAS has it; first person is the server's default. */
export const CAMERAS: [string, string][] = [["First person", "first_person"], ["Players choose", "player_choice"], ["Third person", "third_person"]];
