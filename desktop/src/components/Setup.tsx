// The setup screen: host a server of your own, or add one that's running, each tested before it's saved.
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { useEffect, useRef, useState } from "react";
import { AMBER, CYAN, DIM, GREEN, RED, WHITE } from "../art/palette";
import { emblem } from "../art/archive";
import { CAMERAS, explain, type Dict } from "../lib/util";
import { Check, Pixel, useSize } from "./ui";

interface Spec { address: string; port: string; password: string; name: string; ssh: string }
// what the operator picks for a server made here: the backend's host::Settings
interface Plan { name: string; host: string; password: string; max: string; listed: boolean; internet: boolean; auto: boolean; playlist: string; camera: string }
type Mode = "start" | "host" | "connect";
const SPIN = "◐◓◑◒";

export function Setup({ info, onDone }: { info: Dict; onDone: (demo: boolean) => void }) {
  const s = info.setup;
  const [mode, setMode] = useState<Mode>("start");
  const [f, setF] = useState<Spec>({ address: "127.0.0.1", port: "49176", password: "", name: "", ssh: "" });
  const [plan, setPlan] = useState<Plan>({ name: "", host: s.by ?? "", password: "", max: "", listed: true, internet: false, auto: false, playlist: "", camera: "" });
  const [hs, setHs] = useState<Dict | null>(null); // where a server made here goes, and what's missing for it
  const [by, setBy] = useState(s.by);
  const [remember, setRemember] = useState(true);
  const [status, setStatus] = useState<[boolean | null, string]>([info.error ? false : null, info.error ?? ""]);
  const [added, setAdded] = useState<Spec[]>([]);
  const [testing, setTesting] = useState(false);
  const [failed, setFailed] = useState(false);
  const [frame, setFrame] = useState(0);
  const [showSsh, setShowSsh] = useState(false);
  const [found, setFound] = useState<Dict[]>([]); // set up here or found on a game box: in the config already
  const [busy, setBusy] = useState(false);
  const [hosted, setHosted] = useState(false); // the server made here is up: pressing the button again would only repeat it
  const pw = useRef<HTMLInputElement>(null);
  const nm = useRef<HTMLInputElement>(null);
  const [ref, { h }] = useSize<HTMLDivElement>();
  useEffect(() => {
    if (mode === "connect") pw.current?.focus();
    if (mode === "host") {
      nm.current?.focus();
      invoke<Dict>("host_status").then(setHs, () => setHs(null));
    }
  }, [mode]);
  useEffect(() => {
    if (!busy) return;
    const off = listen<string>("setup-line", (e) => setStatus([null, e.payload]));
    return () => void off.then((f) => f());
  }, [busy]);
  useEffect(() => {
    if (!testing && !busy) return;
    const t = setInterval(() => setFrame((n) => n + 1), 100);
    return () => clearInterval(t);
  }, [testing, busy]);
  const set = (k: keyof Spec) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const plant = (k: keyof Plan) => (e: React.ChangeEvent<HTMLInputElement>) => setPlan({ ...plan, [k]: e.target.value });
  const home = s.path.replace(/^\/home\/[^/]+|^\/root|^C:\\Users\\[^\\]+/, "~");
  const go = (m: Mode) => {
    setStatus([null, ""]);
    setMode(m);
  };

  const check = async (): Promise<string | null> => {
    if (!f.password) {
      pw.current?.focus();
      setStatus([false, "Enter the RCON password."]);
      return null;
    }
    const r: Dict = await invoke("setup_check", { spec: f });
    if (!r.ok) {
      setStatus([false, r.text]);
      return null;
    }
    if (added.some((a) => a.address === f.address && a.port === f.port && a.ssh === f.ssh)) {
      setStatus([false, `${r.where} is already in your list.`]);
      return null;
    }
    return r.where;
  };
  const add = (text: string) => {
    setAdded([...added, f]);
    setStatus([true, `${text} Add another (next port filled in), or press START.`]);
    setFailed(false);
    setF({ ...f, port: /^\d+$/.test(f.port) ? String(Number(f.port) + 1) : f.port, name: "" });
  };
  const test = async () => {
    const where = await check();
    if (!where) return;
    setFailed(false);
    setTesting(true);
    setStatus([null, `Connecting to ${where}…`]);
    const r: Dict = await invoke("setup_probe", { spec: f });
    setTesting(false);
    if (r.ok) add(r.text);
    else {
      setStatus([false, r.text ?? explain(r.detail ?? "", r.state ?? "offline")]);
      setFailed(true);
    }
  };
  // START THE SERVER, or FIND SERVERS ON IT: the backend saves what it finds, and says how it's going as it goes
  const hosting = async (what: "setup_host" | "setup_find") => {
    let settings = null;
    if (what === "setup_find" && !f.ssh.trim()) {
      setStatus([false, "Enter the game box, as you'd give it to ssh: user@game-box."]);
      return;
    }
    if (what === "setup_host" && !hs?.exists) {
      const max = plan.max.trim() ? Number(plan.max) : 0;
      if (!Number.isInteger(max) || max < (plan.max.trim() ? 1 : 0) || max > 63) {
        setStatus([false, "Max players is a number from 1 to 63, or empty for the default."]);
        return;
      }
      settings = { name: plan.name, host: plan.host, password: plan.password, max_players: max, hidden: !plan.listed, internet: plan.internet, auto_update: plan.auto, playlist: plan.playlist, camera: plan.camera };
    }
    setBusy(true);
    setStatus([null, what === "setup_host" ? "Setting up a server on this computer…" : `Looking on ${f.ssh.trim()}…`]);
    try {
      const r: Dict = await invoke(what, { dest: f.ssh, by, remember, settings });
      if (what === "setup_host") setHosted(true);
      setFound([...found, ...r.found.filter((x: Dict) => !found.some((y) => y.where === x.where))]);
      const done = what === "setup_host" ? `Your server${r.found.length > 1 ? "s are" : " is"} running and added.` : `Added ${r.found.length} server${r.found.length === 1 ? "" : "s"} that answered.`;
      setStatus([r.notes.length ? null : true, `${done} ${r.notes.join(" ")} Press START.`.replace("  ", " ")]);
    } catch (e) {
      setStatus([false, String(e)]);
    }
    setBusy(false);
  };
  const start = async () => {
    try {
      await invoke("setup_save", { specs: added, by, remember });
      onDone(false);
    } catch (e) {
      setStatus([false, String(e)]);
    }
  };
  const size = Math.max(0, Math.min(320, Math.floor((h - (mode === "start" ? 460 : 560)) / 4) * 4));
  const statusColor = status[0] === true ? GREEN : status[0] === false ? RED : AMBER;
  const have = s.existing.length || added.length || found.length;
  const statusLine = (
    <div style={{ marginTop: 10, color: statusColor, minHeight: 20, whiteSpace: "pre-wrap" }}>
      {testing || busy ? `${SPIN[frame % 4]} ` : status[0] === true ? "✓ " : status[0] === false && status[1] ? "✗ " : ""}{status[1]}
    </div>
  );
  const servers = have ? (
    <div><span style={{ color: CYAN }}>YOUR SERVERS   </span>
      {s.existing.map((x: Dict) => <span key={x.where}><span style={{ color: DIM }}>◉ </span><span style={{ color: WHITE }}>{x.name || x.where}   </span></span>)}
      {found.map((x) => <span key={x.where}><span style={{ color: GREEN }}>◉ </span><span style={{ color: WHITE }}>{x.name || x.where}   </span></span>)}
      {added.map((x, i) => <span key={i}><span style={{ color: GREEN }}>◉ </span><span style={{ color: WHITE }}>{x.name || `${x.address}:${x.port}`}   </span></span>)}
    </div>
  ) : null;
  const startBtn = <button className="btn success" disabled={busy || testing || !have} onClick={start}>START  ▸</button>;
  const blurb = {
    start: "Run a Halo 3 server of your own, or manage one that's already running.",
    host: "Name your server and it's running in a minute or two: the download, the server folder and its settings are done for you.",
    connect: "Let's connect to your Halo 3 server: its address and RCON password (dedicated.toml, under [rcon]).",
  }[mode];
  const card = (title: string, text: string, label: string, onClick: () => void, cls = "") => (
    <div className="panel" style={{ flex: 1, padding: "16px 18px", cursor: "pointer" }} onClick={onClick}>
      <div className="ptitle">{title}</div>
      <div style={{ color: DIM, flex: 1, marginBottom: 14 }}>{text}</div>
      <button className={`btn ${cls}`} onClick={(e) => { e.stopPropagation(); onClick(); }}>{label}</button>
    </div>
  );
  return (
    <div className="setup" ref={ref}>
      {size >= 96 ? <Pixel pic={emblem(size / 2)} width={size} /> : null}
      <div style={{ textAlign: "center" }}>
        <div className="pre" style={{ color: AMBER }}>O N I   R C O N</div>
        <div style={{ color: DIM }}>{blurb}</div>
      </div>
      {mode === "start" ? (
        <>
          <div style={{ display: "flex", gap: 16, width: "min(860px, 96vw)" }}>
            {card("HOST A SERVER", "Start a Halo 3 server on this computer from your copy of the game. Pick a name and a password; the rest is set up for you.", "HOST ONE HERE", () => go("host"), "primary")}
            {card("CONNECT TO ONE", "Already running a server, here or on another machine? Add it with its address and RCON password.", "CONNECT", () => go("connect"))}
            {card("TRY THE DEMO", "Look around the console with made-up servers first. Nothing is saved.", "DEMO", () => onDone(true))}
          </div>
          {status[1] ? statusLine : null}
          {have ? <div className="form" style={{ display: "flex", gap: 16, alignItems: "center" }}>{servers}<span style={{ flex: 1 }} />{startBtn}</div> : null}
        </>
      ) : mode === "host" ? (
        <div className="panel form" style={{ padding: "16px 22px" }}>
          <div className="ptitle">HOST A SERVER</div>
          {hs?.problem ? <div style={{ color: RED, whiteSpace: "pre-wrap" }}>✗ {hs.problem}</div> : null}
          {hs?.exists ? (
            <div style={{ color: AMBER }}>
              A server is already set up in {hs.folder}{hs.servers.length ? ` (${hs.servers.join(", ")})` : ""}. It's started as it is; change its settings in the dedicated.toml there.
            </div>
          ) : (
            <div className="dialog" style={{ all: "unset", display: "block" }}>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
                <div><label className="lbl">Server name</label><input ref={nm} className="input" maxLength={48} value={plan.name} onChange={plant("name")} placeholder="what players see in the browser" disabled={busy} /></div>
                <div><label className="lbl">Hosted by</label><input className="input" value={plan.host} onChange={plant("host")} placeholder="shown under the name" disabled={busy} /></div>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 140px 170px", gap: 16 }}>
                <div><label className="lbl">Join password</label><input className="input" value={plan.password} onChange={plant("password")} placeholder="empty lets anyone in" disabled={busy} /></div>
                <div><label className="lbl">Max players</label><input className="input" value={plan.max} onChange={plant("max")} placeholder="default" disabled={busy} /></div>
                <div><label className="lbl">Camera</label>
                  <select className="input" value={plan.camera} onChange={(e) => setPlan({ ...plan, camera: e.target.value })} disabled={busy} title="How players see their own character on foot. Characters made for third person play in first person unless players may choose.">
                    {[["First person", ""], ...CAMERAS.slice(1)].map(([l, v]) => <option key={v} value={v}>{l}</option>)}
                  </select></div>
              </div>
              <div style={{ marginTop: 14 }}>
                <label className="lbl">Playlist</label>
                <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                  <button className="btn" disabled={busy} onClick={async () => { const p = await invoke<string | null>("pick_playlist"); if (p) setPlan({ ...plan, playlist: p }); }}>CHOOSE A FILE…</button>
                  <span className="hint">{plan.playlist ? plan.playlist.split(/[\\/]/).pop() : "none: the server's default rotation (Guardian Slayer)"}</span>
                  {plan.playlist ? <span className="hint" style={{ cursor: "pointer" }} onClick={() => setPlan({ ...plan, playlist: "" })}>✕ clear</span> : null}
                </div>
              </div>
              <div style={{ display: "flex", gap: 22, flexWrap: "wrap", marginTop: 14 }}>
                <Check label="Show in the server browser" on={plan.listed} onChange={(listed) => setPlan({ ...plan, listed })} title="Off: nobody sees it listed, and players join by its address." />
                <Check label="Open to the internet (UPnP)" on={plan.internet} onChange={(internet) => setPlan({ ...plan, internet })} title="Asks your router to forward the ports, so friends outside your home can join." />
                <Check label="Keep it up to date" on={plan.auto} onChange={(auto) => setPlan({ ...plan, auto })} title="The server updates itself when a new version is out." />
              </div>
              <div className="hint" style={{ marginTop: 6 }}>Made in {hs?.folder ?? "…"}. Needs Halo: The Master Chief Collection from Steam; the server download is checked against its published SHA-256.</div>
            </div>
          )}
          <div style={{ display: "flex", gap: 16, marginTop: 12 }}>
            <Check label="Remember the RCON password" on={remember} onChange={setRemember} />
            <span className="hint">{remember ? `saved in ${home}` : "you'll be asked for it each start"}</span>
          </div>
          {statusLine}
          {servers ? <div style={{ marginTop: 6 }}>{servers}</div> : null}
          <div style={{ display: "flex", gap: 10, marginTop: 14 }}>
            <button className="btn" disabled={busy} onClick={() => go("start")}>◂ BACK</button>
            <span style={{ flex: 1 }} />
            <button className="btn primary" disabled={busy || hosted || !hs || !!hs.problem} onClick={() => hosting("setup_host")}>{hs?.exists ? "START IT" : "CREATE & START"}</button>
            {startBtn}
          </div>
        </div>
      ) : (
        <div className="panel form" style={{ padding: "16px 22px" }}>
          <div className="ptitle">ADD A SERVER</div>
          <div className="dialog" style={{ all: "unset", display: "block" }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 140px", gap: 16 }}>
              <div><label className="lbl">Address</label><input className="input" value={f.address} onChange={set("address")} placeholder="IP, host name or wss:// link" onKeyDown={(e) => e.key === "Enter" && test()} /></div>
              <div><label className="lbl">Port</label><input className="input" value={f.port} onChange={set("port")} placeholder="game port" onKeyDown={(e) => e.key === "Enter" && test()} /></div>
            </div>
            <div className="hint">Where the server runs: 127.0.0.1 is this computer. RCON shares the game port.</div>
            <div style={{ display: "grid", gridTemplateColumns: s.ask_by ? "1fr 1fr 1fr" : "1fr 1fr", gap: 16 }}>
              <div><label className="lbl">RCON password</label><input ref={pw} type="password" className="input" value={f.password} onChange={set("password")} placeholder="from dedicated.toml" onKeyDown={(e) => e.key === "Enter" && test()} /></div>
              <div><label className="lbl">Name (optional)</label><input className="input" value={f.name} onChange={set("name")} placeholder="else the server's own" onKeyDown={(e) => e.key === "Enter" && test()} /></div>
              {s.ask_by ? <div><label className="lbl">Your name</label><input className="input" value={by} onChange={(e) => setBy(e.target.value)} title="Kicks and bans you make are logged under it." /></div> : null}
            </div>
            <div style={{ marginTop: 12 }}>
              <span style={{ color: CYAN, cursor: "pointer" }} onClick={() => setShowSsh(!showSsh)}>{showSsh ? "▼" : "▶"} Advanced: reach it through SSH</span>
              {showSsh ? (
                <div style={{ paddingLeft: 18, marginTop: 6 }}>
                  <input className="input" value={f.ssh} onChange={set("ssh")} placeholder="user@game-box   (leave empty to connect directly)" onKeyDown={(e) => e.key === "Enter" && test()} />
                  <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 6 }}>
                    <button className="btn" disabled={busy || testing} onClick={() => hosting("setup_find")}>FIND SERVERS ON IT</button>
                    <span className="hint">finds its Reclaimer containers and server folders, and tests each</span>
                  </div>
                  <div className="hint">Best when the server is on another machine: RCON isn't encrypted, so keep it on 127.0.0.1 there and tunnel in. Needs an SSH key, not a password.</div>
                </div>
              ) : null}
            </div>
            <div style={{ display: "flex", gap: 16, marginTop: 12 }}>
              <Check label="Remember the password" on={remember} onChange={setRemember} />
              <span className="hint">{remember ? `saved in ${home}` : "you'll be asked for it each start"}</span>
            </div>
            {statusLine}
            {servers}
            <div style={{ display: "flex", gap: 10, marginTop: 14 }}>
              <button className="btn" disabled={busy || testing} onClick={() => go("start")}>◂ BACK</button>
              <span style={{ flex: 1 }} />
              {failed ? <button className="btn" onClick={async () => { if (await check()) add("Added without a test. It'll keep trying to connect once you start."); }}>ADD ANYWAY</button> : null}
              <button className="btn primary" disabled={testing || busy} onClick={test}>TEST & ADD</button>
              {startBtn}
            </div>
          </div>
        </div>
      )}
      <div className="hint">Ctrl+Q quits.   {info.version ? `oni-rcon ${info.version}` : ""}</div>
    </div>
  );
}
