// The window: the setup screen or the console, with the boot in between, and every key the terminal console had.
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { useCallback, useEffect, useRef, useState } from "react";
import { bump, fkey, oni, TABS, useV, type Tab } from "./engine";
import { OPS, BAN_OPS, TAB_TIPS, askPassword, automation, bl, broadcast, goto, installUpdate, op, player, watchUpdates, whatsNew } from "./actions";
import { Sidebar } from "./components/Sidebar";
import { Banner, Footer, Masthead, Toasts } from "./components/Chrome";
import { Assets, assetsUi } from "./components/Assets";
import { ConsoleTab, Intercepts } from "./components/Feeds";
import { Blacklist, Operations, blSel } from "./components/Ops";
import { Forge, fg, forgeAction, forgeLoad } from "./components/Forge";
import { Health } from "./components/Health";
import { Plugins } from "./components/Plugins";
import { Dialogs } from "./components/Dialogs";
import { Archive, Boot } from "./components/Boot";
import { Setup } from "./components/Setup";
import { useSize } from "./components/ui";
import { targetOf, pick, type Dict } from "./lib/util";
import type { Opened } from "./components/Findings";

const NAMES: Record<Tab, string> = { assets: "ASSETS", intercepts: "INTERCEPTS", operations: "OPERATIONS", blacklist: "BLACKLIST", console: "CONSOLE", forge: "FORGE", health: "HEALTH", plugins: "PLUG-INS" };
const FOCUS: Record<Tab, string> = { assets: "players", intercepts: "feed", operations: "op-load", blacklist: "bans", console: "cmd", forge: "listings", health: "crashes", plugins: "plugin-list" };

export function setTab(t: Tab) {
  oni.setTab(t);
  requestAnimationFrame(() => {
    const el = document.getElementById(FOCUS[t]) as HTMLElement | null;
    if (el && !(el as HTMLButtonElement).disabled) el.focus();
  });
}

/** Search every server's players; open the one picked, on their server, selected. */
async function findPlayer() {
  const r = await oni.ask("find", {});
  if (!r) return;
  assetsUi.want = r.key;
  oni.select(r.index);
  setTab("assets");
  bump("roster");
}

/** The Superintendent's findings; open the server of the one picked, with its player selected if they're still there. */
async function openFindings() {
  const r: Opened | null = await oni.ask("findings", {});
  if (!r) return;
  oni.select(r.index);
  const { subject } = r;
  if (subject) {
    const here: Dict[] = oni.stations[r.index]?.players ?? [];
    const id = (p: Dict) => String(pick(p, "player_id", "id") ?? "");
    const named = here.filter((p) => String(pick(p, "name") ?? "") === subject.name);
    const p = (subject.id && here.find((x) => id(x) === subject.id)) || (named.length === 1 ? named[0] : undefined);
    if (p) assetsUi.want = targetOf(p);
    else oni.notify(`${subject.name || "That player"} isn't on this server any more.`, "FINDINGS");
  }
  setTab("assets");
  bump("roster");
}

function palette(onSetup: () => void) {
  const items: [string, string, () => void][] = [
    ["Go to station", "every station, the busiest first  (g)", goto],
    ["Superintendent findings", "what it noticed in the last hour, with the evidence  (F8)", openFindings],
    ["Automation rules", "when something happens, do this: chat, Discord, Telegram, votes, alerts  (F9)", automation],
    ["Find a player", "on any station, by name, tag, clan, team, ID or address  (Ctrl+F)", findPlayer],
    ...oni.stations.map((s, i) => [`Station ${i + 1}: ${s.label}`, s.where, () => oni.select(i)] as [string, string, () => void]),
    ...OPS.map(([o, label]) => [o === "broadcast" ? "Broadcast to stations" : `Ops: ${label.replace(/\b\w+/g, (w) => w[0] + w.slice(1).toLowerCase())}`,
      o === "broadcast" ? "say on this or every station" : `${o} on the selected station`, () => op(o)] as [string, string, () => void]),
    ...BAN_OPS.map(([o, label]) => [`Blacklist: ${label.split("  ")[0].toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase())}`, o,
      () => bl(o, ...blSel())] as [string, string, () => void]),
    ...oni.plugins.filter((p) => !p.m.state).flatMap((p) => (p.view?.actions ?? []).map((a: Dict) =>
      [`${p.m.name || p.m.id} › ${a.label}`, "plug-in action  (F10)", () => { setTab("plugins"); oni.pluginAction(p, a); }] as [string, string, () => void])),
    ...(oni.appUpdate ? [[`Update to ${oni.appUpdate.version}`, "what's new, then install and restart  (Shift+U)", installUpdate] as [string, string, () => void]] : []),
    ["Toggle address redaction", "hide or show player IPs", () => oni.toggleRedact()],
    [`Boot sequence: ${oni.fullIntro ? "quick after the first run" : "full every time"}`, oni.fullIntro ? "now: the full sequence plays every time" : "now: full the first time, quick after",
      async () => {
        const r: Dict = await invoke("prefs_intro", { full: !oni.fullIntro, seen: null });
        oni.fullIntro = r.full_intro;
        oni.notify(oni.fullIntro ? "The full boot sequence plays every time you start." : "The full boot sequence plays on the first run only; after that the boot is quick.", "BOOT SEQUENCE");
      }],
    ["Health: read the reports now", "crashes, memory and the workaround service  (F7, Ctrl+R)", () => invoke("health_now")],
    ["Help: field manual", "what everything does, in plain words  (?)", () => oni.ask("help", {})],
    ["Add a server", "open the setup screen", () => import("./actions").then((a) => a.addServer(onSetup))],
    ["Forge: Load API key", "your own ReclaimerForge key, for F6", () => forgeAction("key")],
    ["Forge: Refresh catalog", "fetch the catalog again", () => forgeLoad(false, true)],
    ["Forge: Publish files", "your own maps, gametypes and playlists, onto ReclaimerForge  (F6, p)", () => forgeAction("publish")],
    ["Quit", "close the console  (Ctrl+Q)", () => getCurrentWindow().close()],
  ];
  oni.ask("palette", { items });
}

/** Every key the console answers to. Typing in a box keeps its letters; the function keys and Ctrl work anywhere. */
function useKeys(onSetup: () => void) {
  useEffect(() => {
    oni.onFindings = openFindings; // the Superintendent's face opens them too
    return () => { oni.onFindings = null; };
  }, []);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (oni.dialogs.length) {
        if (e.key === "Escape") {
          const d = oni.dialogs[oni.dialogs.length - 1];
          d.resolve(d.kind === "confirm" ? false : null);
        }
        return;
      }
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target as HTMLElement)?.tagName ?? "");
      const ctrl = e.ctrlKey || e.metaKey;
      if (e.key === "F8") {
        e.preventDefault();
        return void openFindings();
      }
      if (e.key === "F9") {
        e.preventDefault();
        return void automation();
      }
      if (e.key === "F10") {
        e.preventDefault();
        return setTab("plugins");
      }
      const fn = /^F([1-7])$/.exec(e.key);
      if (fn) {
        e.preventDefault();
        return setTab(TABS[Number(fn[1]) - 1]);
      }
      if (ctrl) {
        const k = e.key.toLowerCase();
        const m: Record<string, () => void> = { b: broadcast, r: () => { oni.refresh(); if (oni.tab === "forge") { fg.details.clear(); fg.want = null; forgeLoad(false, true); } },
          p: () => palette(onSetup), f: findPlayer };
        if (m[k]) {
          e.preventDefault();
          m[k]();
        }
        return;
      }
      if (e.key === "Escape" && typing) return (e.target as HTMLElement).blur();
      if (typing || e.altKey) return;
      const k = e.key;
      const tabKeys: Partial<Record<Tab, Record<string, () => void>>> = {
        assets: Object.fromEntries(["t:tell", "k:kick", "b:ban", "m:mute", "j:team", "v:vpnallow", "p:admin", "y:copy"].map((x) => {
          const [c, w] = x.split(":");
          return [c, () => player(w, assetsUi.selected ? assetsUi.rows.get(assetsUi.selected) : undefined)];
        })),
        blacklist: Object.fromEntries(["n:newban", "u:unban", "a:vpnallow", "r:vpnrevoke", "p:adminadd", "d:adminremove", "o:pardon"].map((x) => {
          const [c, w] = x.split(":");
          return [c, () => bl(w, ...blSel())];
        })),
        forge: Object.fromEntries(["i:install", "l:load", "a:ack", "s:sort", "w:window", "n:more", "k:key", "y:copy", "p:publish", "u:update", "e:edit", "x:withdraw"].map((x) => {
          const [c, w] = x.split(":");
          return [c, () => forgeAction(w)];
        })),
      };
      const own = tabKeys[oni.tab]?.[k];
      if (own) {
        e.preventDefault();
        return own();
      }
      if (/^[1-9]$/.test(k)) return oni.select(Number(k) - 1);
      const global: Record<string, () => void> = {
        g: goto, x: () => oni.toggleRedact(), "?": () => oni.ask("help", {}), U: installUpdate,
        "/": () => {
          if (oni.tab === "forge") return document.getElementById("forge-q")?.focus();
          if (oni.tab !== "intercepts") setTab("console");
          requestAnimationFrame(() => document.getElementById(oni.tab === "intercepts" ? "say" : "cmd")?.focus());
        },
      };
      if (global[k]) {
        e.preventDefault();
        global[k]();
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onSetup]);
}

// Per-viewer layout: the sidebar's width and whether it's folded away. Storage can be missing; the layout still works without it.
const saved = (k: string) => { try { return localStorage.getItem(k); } catch { return null; } };
const save = (k: string, v: string | null) => { try { v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* not kept */ } };

/** Drag the gap between two panels in any .hsplit/.vsplit to resize them; double-click the gap to put them back. */
function useSplits() {
  useEffect(() => {
    const isSplit = (el: HTMLElement) => (el.classList?.contains("hsplit") || el.classList?.contains("vsplit")) && getComputedStyle(el).flexWrap !== "wrap";
    const growing = (box: HTMLElement) => [...box.children].filter((k) => {
      const cs = getComputedStyle(k);
      return cs.display !== "none" && cs.position !== "absolute" && Number(cs.flexGrow) > 0;
    }) as HTMLElement[];
    const down = (e: PointerEvent) => {
      const box = e.target as HTMLElement;
      if (e.button !== 0 || !isSplit(box)) return;
      const row = getComputedStyle(box).flexDirection.startsWith("row");
      const kids = growing(box);
      const at = row ? e.clientX : e.clientY;
      const start = (k: HTMLElement) => { const r = k.getBoundingClientRect(); return row ? r.left : r.top; };
      const size = (k: HTMLElement) => { const r = k.getBoundingClientRect(); return row ? r.width : r.height; };
      const i = kids.findIndex((k) => start(k) > at);
      if (i < 1) return;
      e.preventDefault();
      const [a, b] = [kids[i - 1], kids[i]];
      const sizes = kids.map(size); // freeze every growing panel at its size, so moving one gap moves only its two neighbours
      kids.forEach((k, j) => { k.style.flex = `${sizes[j]} 1 0px`; });
      const [sa, sb] = [sizes[i - 1], sizes[i]];
      const move = (m: PointerEvent) => {
        const d = Math.max(40 - sa, Math.min(sb - 40, (row ? m.clientX : m.clientY) - at));
        a.style.flexGrow = String(sa + d);
        b.style.flexGrow = String(sb - d);
      };
      const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); document.body.style.cursor = ""; };
      document.body.style.cursor = row ? "col-resize" : "row-resize";
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    };
    const reset = (e: MouseEvent) => {
      const box = e.target as HTMLElement;
      if (isSplit(box)) [...box.children].forEach((k) => { (k as HTMLElement).style.flex = ""; });
    };
    window.addEventListener("pointerdown", down);
    window.addEventListener("dblclick", reset);
    return () => { window.removeEventListener("pointerdown", down); window.removeEventListener("dblclick", reset); };
  }, []);
}

function Console({ onSetup }: { onSetup: () => void }) {
  useV((s) => s.ui);
  useKeys(onSetup);
  useSplits();
  useEffect(() => {
    requestAnimationFrame(() => document.getElementById(FOCUS[oni.tab])?.focus());
  }, []);
  const [ref, { w }] = useSize<HTMLDivElement>();
  const [width, setWidth] = useState(() => Number(saved("oni.sidebar")) || 0);
  const [folded, setFolded] = useState(() => saved("oni.sidebar.folded") === "1");
  const fit = (n: number) => Math.round(Math.max(200, Math.min(n, Math.max(200, w - 480))));
  const sidebar = width ? fit(width) : w >= 1700 ? 400 : w < 1250 ? 280 : 330;
  const fold = () => { setFolded(!folded); save("oni.sidebar.folded", folded ? null : "1"); };
  const grip = (e: React.PointerEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    const left = el.parentElement!.getBoundingClientRect().left;
    el.setPointerCapture(e.pointerId);
    el.classList.add("drag");
    let n = sidebar;
    el.onpointermove = (m) => { n = fit(m.clientX - left); setWidth(n); };
    el.onpointerup = () => { el.onpointermove = el.onpointerup = null; el.classList.remove("drag"); save("oni.sidebar", String(n)); };
  };
  const unsize = () => { setWidth(0); save("oni.sidebar", null); };
  const bar = useRef<HTMLDivElement>(null);
  const t = oni.tab;
  useEffect(() => { bar.current?.querySelector(".on")?.scrollIntoView({ block: "nearest", inline: "nearest" }); }, [t]);
  const wheel = (e: React.WheelEvent<HTMLDivElement>) => { if (!e.deltaX) e.currentTarget.scrollLeft += e.deltaY; };
  return (
    <div className="console" ref={ref} style={{ ["--sidebar" as any]: `${folded ? 0 : sidebar}px` }}>
      <Masthead width={w} />
      <Banner />
      <div className={folded ? "body folded" : "body"}>
        <Sidebar onSetup={onSetup} />
        <div className="sidegrip" onPointerDown={grip} onDoubleClick={unsize} title="Drag to resize the sidebar; double-click to reset" />
        <div className="tabs">
          <div className="tabbar" ref={bar} onWheel={wheel}>
            <button className="fold" onClick={fold} title={folded ? "Show the sidebar" : "Hide the sidebar"}>{folded ? "»" : "«"}</button>
            {TABS.map((x) => (
              <button key={x} className={x === t ? "on" : ""} onClick={() => setTab(x)} title={TAB_TIPS[x]}><span className="f">{fkey(x)}</span>{NAMES[x]}</button>
            ))}
          </div>
          {t !== "plugins" ? (
            <div className="pane" key={t}>
              {t === "assets" ? <Assets /> : t === "intercepts" ? <Intercepts /> : t === "operations" ? <Operations /> : t === "blacklist" ? <Blacklist />
                : t === "console" ? <ConsoleTab /> : t === "forge" ? <Forge /> : <Health />}
            </div>
          ) : null}
          {/* always mounted, hidden off F10, so a plug-in page with `persist` keeps running */}
          <div className="pane" style={t === "plugins" ? undefined : { display: "none" }}><Plugins shown={t === "plugins"} /></div>
        </div>
      </div>
      <Footer />
    </div>
  );
}

type Screen = { kind: "loading" } | { kind: "setup"; info: Dict } | { kind: "archive" } | { kind: "boot" } | { kind: "console" } | { kind: "error"; text: string };

export function App() {
  const [screen, setScreen] = useState<Screen>({ kind: "loading" });
  const launch = useCallback(async () => {
    try {
      const info: Dict = await invoke("launch_info");
      if (info.mode === "setup") return setScreen({ kind: "setup", info });
      await startConsole(false);
    } catch (e) {
      setScreen({ kind: "error", text: `Couldn't start: ${String(e)}` });
    }
  }, []);
  const startConsole = async (demo: boolean) => {
    try {
      const info: Dict = await invoke("start_console", { demo });
      await oni.start(info);
      const motion = !matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (info.intro === "full" && motion) {
        invoke("prefs_intro", { full: null, seen: true });
        setScreen({ kind: "archive" });
      } else if (info.intro === "off") welcome();
      else setScreen({ kind: "boot" });
    } catch (e) {
      setScreen({ kind: "error", text: String(e) });
    }
  };
  useEffect(() => { launch(); }, [launch]);
  useEffect(() => { // Ctrl+Q quits from any screen
    const q = (e: KeyboardEvent) => (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "q" && getCurrentWindow().close();
    window.addEventListener("keydown", q);
    return () => window.removeEventListener("keydown", q);
  }, []);
  const welcome = useCallback(() => {
    setScreen({ kind: "console" });
    if (oni.hint) {
      oni.notify(oni.hint, "WELCOME, OPERATOR", "information", 15);
      oni.hint = "";
    }
    askPassword().then(whatsNew);
    watchUpdates();
  }, []);
  const toBoot = useCallback(() => setScreen({ kind: "boot" }), []);
  const onSetup = useCallback(async () => {
    const info: Dict = await invoke("launch_info");
    setScreen({ kind: "setup", info });
  }, []);
  return (
    <>
      {screen.kind === "setup" ? <Setup info={screen.info} onDone={startConsole} />
        : screen.kind === "archive" ? <Archive onDone={toBoot} onSkip={welcome} />
          : screen.kind === "boot" ? <Boot onDone={welcome} />
            : screen.kind === "console" ? <Console onSetup={onSetup} />
              : screen.kind === "error" ? <div className="boot"><div style={{ color: "var(--red)", whiteSpace: "pre-wrap", maxWidth: 800 }}>{screen.text}</div><button className="btn primary" onClick={onSetup}>OPEN SETUP</button></div>
                : null}
      <Dialogs />
      <Toasts />
    </>
  );
}
