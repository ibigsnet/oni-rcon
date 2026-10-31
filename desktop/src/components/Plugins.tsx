// F10: plug-ins, separate programs that describe their own panel in JSON (docs/PLUGINS.md). The backend finds, runs
// and shapes them; this draws what they sent. Every string here is the plug-in's data, drawn as plain text (never
// markup) and redacted like the rest of the console. The one exception is an html block: the plug-in's own page, in a
// sandboxed frame with no network and no way into the app but the messages below.
import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import { useEffect, useRef, useState } from "react";
import { AMBER, CYAN, DIM, GREEN, GREY, INK, RED, WHITE } from "../art/palette";
import { badgeOf, oni, useV, type Plugin } from "../engine";
import { redactData, redactText, type Dict, type Line } from "../lib/util";
import { Btn, Panel, Table, type Row } from "./ui";

const BADGE: Record<string, string> = { ok: GREEN, warn: AMBER, alert: RED, off: DIM };

/** Its entry in the list: a light, its name, and its badge or what's wrong. */
function entry(p: Plugin): Row {
  const why = p.m.state, b = badgeOf(p), down = p.m.mode === "session" && p.session?.state !== "running";
  const light: [string, string] = p.busy ? ["◌", AMBER] : why && why !== "needs approval" ? ["⊘", RED] : why ? ["?", AMBER]
    : down ? ["○", p.session ? RED : DIM] : p.error ? ["○", RED] : !p.view ? ["○", DIM] : ["●", b ? BADGE[b.level] : GREEN];
  const tail: Line = why ? [[why === "needs approval" ? "NEEDS APPROVAL" : "DISABLED", why === "needs approval" ? AMBER : RED]]
    : down ? [[p.session ? "RESTARTING" : "STARTING", AMBER]] : p.error ? [["NO ANSWER", RED]] : b?.text ? [[b.text, BADGE[b.level]]] : [];
  return { id: p.m.id, cells: [[light], [[p.m.name || p.m.id, why ? DIM : WHITE]], p.update ? [["▲ ", CYAN], ...tail] : tail] };
}

const STEPS: [string, string][] = [
  ["GET IT", "install it from the index below, download it from git, or put its folder in plugins/"],
  ["TURN IT ON", "Oni adds it to plugins = [...] in your config"],
  ["APPROVE IT", "you read what it runs and may do, then allow it; it never runs before this"],
  ["WATCH IT", "its panel appears on this tab, and a badge it sets feeds CONDITION"],
];

/** The empty tab, and ADD A PLUG-IN: the four steps, with the first two done from here. `onDone` runs once one is on. */
function Onboarding({ first, onDone }: { first: boolean; onDone: () => void }) {
  const found = oni.pluginsFound, go = (r: Promise<boolean>) => void r.then((ok) => ok && onDone());
  const [index, setIndex] = useState<Dict[]>([]); // the oni-rcon plug-in index; its offline copy, or nothing, without a network
  useEffect(() => void invoke<Dict[]>("plugin_index").then(setIndex, () => {}), []);
  const has = (id: string) => oni.plugins.some((p) => p.m.id === id) || found.some((m) => m.id === id);
  return (
    <div style={{ whiteSpace: "pre-wrap", display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ color: AMBER }}>{first ? "NO PLUG-INS YET" : "ADD A PLUG-IN"}</div>
      <div>A plug-in is a separate program that draws its own panel here, such as Gryphon's moderation log. Adding one takes four steps, and Oni does the first two for you:</div>
      <div className="facts">{STEPS.flatMap(([k, v], i) => [<span key={i} style={{ color: CYAN }}>{i + 1} · {k}</span>, <span key={`${i}v`} style={{ color: DIM }}>{v}</span>])}</div>
      {found.length ? <div style={{ color: AMBER }}>WAITING IN PLUGINS/</div> : null}
      {found.map((m) => (
        <div key={m.id} style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ color: m.error ? RED : WHITE }}>{m.name || m.id}</span>
          <span style={{ color: DIM, flex: 1 }}>{redactText(m.error || m.description || "", oni.redact)}</span>
          {m.error ? null : <Btn label="TURN ON…" variant="primary" onClick={() => go(oni.pluginTurnOn(m.id))} id={`plugin-on-${m.id}`} />}
        </div>
      ))}
      {index.length ? <div style={{ color: AMBER }}>FROM THE ONI-RCON INDEX</div> : null}
      {index.map((e) => (
        <div key={e.id} style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ color: WHITE }}>{e.name}</span>
          <span style={{ color: DIM, flex: 1 }}>{e.description}{e.author ? ` · ${e.author}` : ""}</span>
          {has(e.id) ? <span style={{ color: GREEN }}>INSTALLED</span>
            : <Btn label="INSTALL…" variant="primary" onClick={() => go(oni.pluginIndexInstall(e))} id={`plugin-get-${e.id}`} />}
        </div>
      ))}
      <div><Btn label="DOWNLOAD FROM GIT…" variant="primary" onClick={() => go(oni.pluginInstall())} id="plugin-install" /></div>
      <div style={{ color: DIM }}>{oni.pluginsDir ? `Plug-ins live in ${oni.pluginsDir}. ` : ""}A folder you put there with an oni-plugin.toml in it shows up above. docs/PLUGINS.md says how to write one.</div>
    </div>
  );
}

/** A plug-in that won't run: why it can't, or what approving it would allow. */
function Gate({ p }: { p: Plugin }) {
  if (p.m.state !== "needs approval") {
    return (
      <div style={{ whiteSpace: "pre-wrap" }}>
        <div style={{ color: RED }}>DISABLED</div><br />
        <div>{redactText(p.m.state, oni.redact)}</div>
        {p.m.description ? <><br /><div style={{ color: DIM }}>{p.m.description}</div></> : null}
      </div>
    );
  }
  return (
    <div style={{ whiteSpace: "pre-wrap" }}>
      <div style={{ color: AMBER }}>NEEDS YOUR APPROVAL</div><br />
      <div>{oni.pluginTerms(p.m)}</div><br />
      <Btn label="APPROVE…" variant="warning" onClick={() => oni.pluginApprove(p)} id="plugin-approve" />
    </div>
  );
}

const THEME = ["ink", "surface", "panel", "panel-2", "line", "amber", "cyan", "red", "green", "warn", "white", "grey", "dim"];

const LEVEL: Record<string, "information" | "warning" | "error"> = { warn: "warning", warning: "warning", error: "error" };

/** An html block: the page from oniplugin:, sent its `data` (redacted), the servers and the theme; it may ask back for
 * one of the view's own actions ({oni:"action", id, row?, params?}), a fresh view ({oni:"refresh"}), a height
 * ({oni:"height", px}), RCON within its permissions ({oni:"rcon", id, server?, command, args}), its own program
 * ({oni:"call", ref, data}), a toast ({oni:"notify", text, level?}) or an F-key passed on to the window ({oni:"key"}).
 * It also hears the events its manifest asks for ({oni:"event"}) and what servers answered its actions' RCON. */
function Frame({ p, b }: { p: Plugin; b: Dict }) {
  const ref = useRef<HTMLIFrameElement>(null);
  const [h, setH] = useState<number>(b.height || 420);
  const told = useRef<number[]>([]);
  useV((s) => s.cards);
  const servers = JSON.stringify(oni.stations.map((s) => ({ name: oni.serverName(s), online: s.online })));
  const post = () => {
    const css = getComputedStyle(document.documentElement);
    ref.current?.contentWindow?.postMessage({ oni: "data", data: redactData(b.data, oni.redact), redact: oni.redact, server: oni.serverName(oni.cur),
      servers: JSON.parse(servers), theme: Object.fromEntries(THEME.map((k) => [k, css.getPropertyValue(`--${k}`).trim()])) }, "*"); // its origin is opaque: "*" is the only target
  };
  const latest = useRef(post);
  latest.current = post;
  useEffect(post, [b.data, oni.redact, servers]);
  useEffect(() => {
    const f = (m: Dict) => ref.current?.contentWindow?.postMessage(m, "*");
    p.pages.add(f);
    return () => void p.pages.delete(f);
  }, [p]);
  useEffect(() => {
    const send = (m: Dict) => ref.current?.contentWindow?.postMessage(m, "*");
    const on = (e: MessageEvent) => {
      const m = e.data;
      if (e.source !== ref.current?.contentWindow || !m || typeof m !== "object") return;
      if (m.oni === "ready") latest.current();
      else if (m.oni === "refresh") oni.pluginView(p);
      else if (m.oni === "height" && Number.isFinite(m.px)) setH(Math.max(40, Math.min(4000, m.px)));
      // ponytail: no rate limit on a page's RCON or calls; each is logged, and only its permitted commands go out
      else if (m.oni === "rcon" && typeof m.command === "string") oni.pluginPageRcon(p, m).then((r) => send({ oni: "rcon-reply", id: m.id ?? null, ...r }));
      else if (m.oni === "call") {
        const r = { oni: "reply", ref: m.ref ?? null };
        invoke("plugin_call", { id: p.m.id, data: m.data ?? null, server: oni.serverName(oni.cur) })
          .then((data) => send({ ...r, ok: true, data }), (err) => send({ ...r, ok: false, error: String(err) }));
      }
      else if (m.oni === "notify" && typeof m.text === "string") { // ponytail: 6 a minute per page, the rest dropped
        const now = Date.now(), t = (told.current = told.current.filter((x) => now - x < 60_000));
        if (t.length < 6 && t.push(now)) oni.notify(redactText(m.text, oni.redact).slice(0, 300), (p.m.name || p.m.id).toUpperCase(), LEVEL[m.level] ?? "information");
      }
      else if (m.oni === "key" && /^F([1-9]|1[0-2])$/.test(String(m.key))) window.dispatchEvent(new KeyboardEvent("keydown", { key: m.key, cancelable: true }));
      else if (m.oni === "action" && !oni.dialogs.length) { // only the actions its view declared, with their confirms
        const a = (p.view?.actions ?? []).find((x: Dict) => x.id === String(m.id));
        if (!a) return oni.notify(`Its page asked for '${String(m.id).slice(0, 40)}', which its view doesn't offer.`, (p.m.name || p.m.id).toUpperCase(), "warning");
        oni.pluginAction(p, a, m.row == null ? null : String(m.row), m.params && typeof m.params === "object" ? m.params : undefined);
      }
    };
    window.addEventListener("message", on);
    return () => window.removeEventListener("message", on);
  }, [p]);
  // the Studio's page has no addresses to blank, and a reload would stop its scripts and drop unsaved edits: it reads redact from data
  const src = `${convertFileSrc(b.src, "oniplugin")}?redact=${oni.redact && !p.m.builtin ? 1 : 0}&v=${b.v}`;
  return <iframe ref={ref} src={src} sandbox="allow-scripts" onLoad={post} title={`${p.m.name || p.m.id} page`}
    style={{ width: "100%", ...(b.fill ? { flex: "1 1 0", minHeight: 0 } : { height: h, flex: "none" }), colorScheme: "normal", border: "1px solid var(--panel-2)", borderRadius: 4, background: "transparent" }} />;
}

function Block({ p, b, acts }: { p: Plugin; b: Dict; acts: Map<string, Dict> }) {
  const [sel, setSel] = useState<string | null>(null);
  const rt = (s: string) => redactText(s, oni.redact);
  const more = b.more ? <div style={{ color: DIM }}>+{b.more} more</div> : null;
  if (b.type === "kv") {
    return <><div className="facts">{b.rows.flatMap(([k, v]: string[], i: number) => [<span key={i} style={{ color: AMBER }}>{rt(k)}</span>, <span key={`${i}v`}>{rt(v)}</span>])}</div>{more}</>;
  }
  if (b.type === "text") return <div style={{ whiteSpace: "pre-wrap" }}>{rt(b.text)}</div>;
  if (b.type === "html") return <Frame p={p} b={b} />;
  if (b.type === "log") return <><div style={{ whiteSpace: "pre-wrap", color: DIM }}>{b.lines.length ? b.lines.map(rt).join("\n") : "—"}</div>{more}</>;
  if (b.type !== "table") return <div style={{ color: GREY }}>this version of Oni can't draw a '{b.name}' block</div>;
  const width = b.columns.length || Math.max(1, ...b.rows.map((r: Dict) => r.cells.length));
  // selected by the plug-in's row id, so a refresh that moves the rows can't point an action at a different one
  const key = (r: Dict, j: number) => r.id || `#${j}`;
  const rows: Row[] = b.rows.map((r: Dict, j: number) => ({ id: key(r, j), cells: Array.from({ length: width }, (_, k) => rt(r.cells[k] ?? "")) }));
  const row = sel === null ? b.rows[0]?.id ?? null : b.rows.find((r: Dict, j: number) => key(r, j) === sel)?.id ?? null;
  return (
    <>
      <div style={{ maxHeight: 360, display: "flex", flexDirection: "column" }}>
        <Table cols={b.columns.length ? b.columns : Array(width).fill("")} rows={rows} selected={sel} onSelect={setSel} />
      </div>
      {more}
      {b.row_actions.length && b.rows.length ? (
        <div className="btns" style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {b.row_actions.map((a: string) => {
            const act = acts.get(a) ?? { id: a, label: a.toUpperCase(), danger: false, confirm: "", params: [] };
            return <Btn key={a} label={act.label} variant={act.danger ? "error" : ""} onClick={() => oni.pluginAction(p, act, row)} />;
          })}
        </div>
      ) : null}
    </>
  );
}

function View({ p }: { p: Plugin }) {
  const v: Dict = p.view ?? {}, b = v.badge;
  const acts = new Map<string, Dict>((v.actions ?? []).map((a: Dict) => [a.id, a]));
  // a view with its own page draws only that: the other blocks are its fallback for the terminal console
  const all: Dict[] = v.blocks ?? [], pages = all.filter((x) => x.type === "html"), blocks = pages.length ? pages : all;
  const fill = fills(p) ? { flex: 1, minHeight: 0 } : {};
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, ...fill }}>
      {p.m.builtin ? null : <div>
        <span style={{ color: AMBER }}>{v.title || p.m.name}</span><span style={{ color: DIM }}>  {p.m.version}</span>
        {b?.text ? <span style={{ marginLeft: 12, padding: "0 6px", color: INK, background: BADGE[b.level] }}>{b.text}</span> : null}
      </div>}
      {p.m.mode === "session" && p.session?.state !== "running" ? (
        <div style={{ color: AMBER }}>{p.session ? redactText(`stopped: ${p.session.detail} · restarting in ${p.session.retry_in}s`, oni.redact) : "starting…"}</div>
      ) : null}
      {p.error ? <div style={{ color: RED }}>⚠ {redactText(p.error, oni.redact)}{p.view ? "   ·   showing its last answer" : ""}</div> : null}
      {p.note ? <div style={{ color: DIM }}>{redactText(p.note, oni.redact)}</div> : null}
      {!p.view && !p.error ? <div style={{ color: DIM }}>asking…</div> : null}
      <div style={{ display: "flex", flexDirection: "column", gap: 10, opacity: p.error ? 0.5 : 1, ...fill }}>
        {blocks.map((blk: Dict, i: number) => <Block key={`${p.m.id}:${i}:${blk.type}`} p={p} b={blk} acts={acts} />)}
        {v.actions?.length ? (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {v.actions.map((a: Dict) => <Btn key={a.id} label={a.label} variant={a.danger ? "error" : "primary"} onClick={() => oni.pluginAction(p, a)} />)}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** Whether its page asked to keep running while it isn't on screen, and to take the whole panel. */
const persists = (p: Plugin) => !p.m.state && (p.view?.blocks ?? []).some((b: Dict) => b.type === "html" && b.persist);
const fills = (p: Plugin) => !p.m.state && (p.view?.blocks ?? []).some((b: Dict) => b.type === "html" && b.fill);

/** Always mounted (App hides it off F10). The selected plug-in's view is drawn; any other whose page has `persist`
 *  stays mounted out of sight, so its timers keep going. Each view keeps one place in one list: moving it would
 *  reload its frame. */
export function Plugins({ shown }: { shown: boolean }) {
  useV((s) => s.plugins);
  const [adding, setAdding] = useState<boolean | null>(null); // null: not chosen yet, so onboard while only the Studio is here
  const p = oni.plugins[Math.min(oni.psel, oni.plugins.length - 1)];
  const real = oni.plugins.some((x) => !x.m.builtin);
  const onboard = !p || (adding ?? !real);
  const select = (id: string) => {
    setAdding(false);
    oni.psel = Math.max(0, oni.plugins.findIndex((x) => x.m.id === id));
    useV.setState((s) => ({ plugins: s.plugins + 1 }));
  };
  useEffect(() => { if (shown) void oni.pluginScan(); }, [shown, oni.plugins.length]); // what's waiting in plugins/
  const live = oni.plugins.filter((x) => (shown && x === p && !x.m.state) || persists(x));
  useEffect(() => { // its update is offered once per new upstream commit, when its panel is on screen
    if (shown && p?.update && !p.update.asked && !oni.dialogs.length) oni.pluginUpdate(p);
  });
  return (
    <div className="hsplit grow">
      <Panel title="PLUG-INS" style={{ flex: 1, minWidth: 220 }}>
        <Table id="plugin-list" cols={["", "NAME", ""]} rows={oni.plugins.map(entry)} selected={onboard ? null : p?.m.id ?? null} onSelect={select}
          cursor={!onboard} /* with a cursor, Table picks the first row when none is selected, which would end onboarding at once */ />
        <div style={{ marginTop: 8 }}><Btn label="+ ADD A PLUG-IN" variant={onboard ? "" : "primary"} onClick={() => setAdding(true)} disabled={onboard || oni.demo} title={oni.demo ? "Not in the demo" : undefined} id="plugin-add" /></div>
      </Panel>
      <Panel title={onboard ? (real ? "ADD A PLUG-IN" : "PLUG-INS") : String(p.m.name || p.m.id).toUpperCase()} style={{ flex: 3 }} col={!onboard && fills(p)}>
        {!onboard && !p.m.builtin ? (
          <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 10 }}>
            {p.update ? <>
              <span style={{ color: CYAN }}>▲ {p.update.behind} new commit{p.update.behind === 1 ? "" : "s"} upstream</span>
              <Btn label="UPDATE…" variant="primary" onClick={() => oni.pluginUpdate(p)} id="plugin-update" />
            </> : null}
            {oni.demo ? null : <span style={{ marginLeft: "auto" }}><Btn label="TURN OFF…" onClick={() => oni.pluginTurnOff(p)} id="plugin-off" /></span>}
          </div>
        ) : null}
        {onboard ? <Onboarding first={!real} onDone={() => setAdding(false)} /> : p.m.state ? <Gate p={p} /> : null}
        {live.map((x) => <div key={x.m.id} style={{ display: x === p && !onboard ? "contents" : "none" }}><View p={x} /></div>)}
      </Panel>
    </div>
  );
}
