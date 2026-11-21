//! Project Reclaimer RCON over its WebSocket protocol, and the SSH tunnels that reach it.
//!
//! Protocol (server 0.9.x): send {"type":"auth","password"}, get {"type":"auth","ok",...,"server","version"}; then
//! {"type":"command","command","args":[...],"id","by"} -> {"type":"reply","id","ok","text","data"?}. The server also
//! pushes {"type":"event","event":"chat"|"kill"|"join"|...} to every signed-in tool.
//!
//! A source:// URL is Valve's Source RCON over TCP instead, as Reach Renaissance's dedicated server speaks it: packets
//! of <len i32><id i32><type i32><body>\0\0, auth as type 3 (answered type 2, id -1 when refused), commands as type 2
//! answered type 0, then an empty type 0 with the same id. Replies are text; status and players get the data the
//! window reads parsed out of them. No events: the window polls.
use futures_util::{SinkExt, StreamExt};
use parking_lot::Mutex;
use serde_json::{json, Value};
use std::collections::{HashMap, VecDeque};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{mpsc, oneshot, watch, Semaphore};
use tokio_tungstenite::tungstenite::protocol::WebSocketConfig;
use tokio_tungstenite::tungstenite::Message;

/// What a connection tells whoever owns it.
#[derive(Debug, Clone)]
pub enum LinkNote {
    State { state: &'static str, detail: String, retry_in: u64, info: Value },
    Event(Value),
    Late { line: String, reply: Value },
}

#[derive(Debug)]
pub enum CallError {
    NotConnected(String),
    Timeout(f64),
    Lost,
}

impl std::fmt::Display for CallError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CallError::NotConnected(d) => f.write_str(d),
            CallError::Timeout(t) => write!(f, "no reply in {t}s; it may still run, and isn't resent"),
            CallError::Lost => f.write_str("connection lost"),
        }
    }
}

struct Inner {
    state: &'static str,
    detail: String,
    info: Value,
    out: Option<mpsc::UnboundedSender<String>>,
    pending: HashMap<u64, oneshot::Sender<Value>>,
    late: VecDeque<(u64, String)>, // commands that timed out, by id: their reply may still come
}

/// One signed-in connection per server, shared by commands and the event stream: servers admit only 4 tools.
#[derive(Clone)]
pub struct Rcon {
    inner: Arc<Mutex<Inner>>,
    ids: Arc<AtomicU64>,
    pub password: Arc<Mutex<String>>,
    pub by: String,
}

impl Rcon {
    pub fn new(password: &str, by: &str) -> Self {
        Rcon {
            inner: Arc::new(Mutex::new(Inner {
                state: "connecting",
                detail: String::new(),
                info: json!({}),
                out: None,
                pending: HashMap::new(),
                late: VecDeque::new(),
            })),
            ids: Arc::new(AtomicU64::new(1)),
            password: Arc::new(Mutex::new(password.to_string())),
            by: by.to_string(),
        }
    }

    fn set(&self, tx: &mpsc::UnboundedSender<LinkNote>, state: &'static str, detail: String, retry_in: u64) {
        let info = {
            let mut i = self.inner.lock();
            i.state = state;
            i.detail = detail.clone();
            i.info.clone()
        };
        let _ = tx.send(LinkNote::State { state, detail, retry_in, info });
    }

    /// Connect, sign in and pump messages; reconnect with backoff. Returns only when the sign-in is refused.
    pub async fn run(
        self,
        url: String,
        mut ready: Option<watch::Receiver<bool>>,
        gate: Arc<Semaphore>,
        tx: mpsc::UnboundedSender<LinkNote>,
    ) {
        let mut delay: u64 = 2;
        loop {
            if let Some(r) = ready.as_mut() {
                if !*r.borrow() {
                    self.set(&tx, "connecting", "awaiting SSH tunnel".into(), 0);
                    if r.wait_for(|up| *up).await.is_err() {
                        return;
                    }
                }
            }
            self.set(&tx, "connecting", String::new(), 0);
            let detail = match self.session(&url, &gate, &tx).await {
                Ok(Some(denied)) => {
                    // Never retry a refused sign-in: 5 wrong passwords in 10 min lock the address out.
                    self.set(&tx, "denied", denied, 0);
                    return;
                }
                Ok(None) => {
                    delay = 2;
                    "connection closed".to_string()
                }
                Err(e) => e,
            };
            {
                let mut i = self.inner.lock();
                i.out = None;
                i.pending.clear(); // dropping the senders fails every waiting call with "connection lost"
                i.late.clear(); // a reply only comes back on the connection that asked
            }
            // jittered, so a fleet that lost its link together doesn't come back in lockstep
            let wait = delay + crate::util::rnd(0.0, (delay / 2 + 1) as f64) as u64;
            self.set(&tx, "offline", format!("{detail}; retry in {wait}s"), wait);
            tokio::time::sleep(Duration::from_secs(wait)).await;
            delay = (delay * 2).min(60);
        }
    }

    /// One connection: Ok(Some(why)) when the password was refused, Ok(None) when it closed after signing in.
    async fn session(
        &self,
        url: &str,
        gate: &Semaphore,
        tx: &mpsc::UnboundedSender<LinkNote>,
    ) -> Result<Option<String>, String> {
        if let Some(addr) = url.strip_prefix("source://") {
            return self.source_session(addr, gate, tx).await;
        }
        let (ws, reply) = {
            let _permit = gate.acquire().await.map_err(|e| e.to_string())?; // only the sign-in: a fleet connects a few at a time
            let mut cfg = WebSocketConfig::default();
            cfg.max_message_size = None;
            cfg.max_frame_size = None;
            let (mut ws, _) = tokio::time::timeout(
                Duration::from_secs(10),
                tokio_tungstenite::connect_async_with_config(url, Some(cfg), false),
            )
            .await
            .map_err(|_| "timed out during opening handshake".to_string())?
            .map_err(|e| e.to_string())?;
            let pw = self.password.lock().clone();
            ws.send(Message::text(json!({"type": "auth", "password": pw}).to_string()))
                .await
                .map_err(|e| e.to_string())?;
            let first = tokio::time::timeout(Duration::from_secs(10), ws.next())
                .await
                .map_err(|_| "timed out waiting for the sign-in reply".to_string())?;
            let text = match first {
                Some(Ok(Message::Text(t))) => t.to_string(),
                Some(Ok(_)) => return Err("that port doesn't speak RCON".into()),
                Some(Err(e)) => return Err(e.to_string()),
                None => return Err("connection closed".into()),
            };
            let reply: Value = serde_json::from_str(&text).map_err(|_| "that port doesn't speak RCON".to_string())?;
            if !reply.is_object() {
                return Err("that port doesn't speak RCON".into());
            }
            (ws, reply)
        };
        if reply.get("ok").and_then(Value::as_bool) != Some(true) {
            let why = crate::util::pick_str(&reply, &["error", "text"]);
            return Ok(Some(if why.is_empty() { "sign-in refused".into() } else { why }));
        }
        let (mut sink, mut stream) = ws.split();
        let (out_tx, mut out_rx) = mpsc::unbounded_channel::<String>();
        {
            let mut i = self.inner.lock();
            i.info = reply.clone();
            i.out = Some(out_tx);
        }
        let version = crate::util::pick_str(&reply, &["version"]);
        self.set(tx, "online", format!("v{}", if version.is_empty() { "?" } else { &version }), 0);
        loop {
            tokio::select! {
                msg = stream.next() => match msg {
                    Some(Ok(Message::Text(t))) => {
                        if let Ok(v) = serde_json::from_str::<Value>(&t) {
                            if v.is_object() {
                                self.dispatch(v, tx);
                            }
                        }
                    }
                    Some(Ok(Message::Close(_))) | None => return Ok(None),
                    Some(Ok(_)) => {}
                    Some(Err(e)) => return Err(e.to_string()),
                },
                out = out_rx.recv() => match out {
                    Some(text) => sink.send(Message::text(text)).await.map_err(|e| e.to_string())?,
                    None => return Ok(None),
                },
            }
        }
    }

    /// One Source RCON connection, as `session`.
    async fn source_session(&self, addr: &str, gate: &Semaphore, tx: &mpsc::UnboundedSender<LinkNote>) -> Result<Option<String>, String> {
        use tokio::io::AsyncWriteExt;
        let sock = {
            let _permit = gate.acquire().await.map_err(|e| e.to_string())?;
            let mut sock = tokio::time::timeout(Duration::from_secs(10), tokio::net::TcpStream::connect(addr))
                .await
                .map_err(|_| "timed out connecting".to_string())?
                .map_err(|e| e.to_string())?;
            let pw = self.password.lock().clone();
            sock.write_all(&source_packet(0, 3, &pw)).await.map_err(|e| e.to_string())?;
            loop {
                let (id, kind, body) = tokio::time::timeout(Duration::from_secs(10), source_read(&mut sock))
                    .await
                    .map_err(|_| "timed out waiting for the sign-in reply".to_string())?
                    .map_err(|_| "that port doesn't speak Source RCON".to_string())?;
                if kind == 2 {
                    if id == -1 {
                        return Ok(Some(if body.is_empty() { "sign-in refused".into() } else { body }));
                    }
                    break;
                }
                // Valve's own servers send an empty type 0 before the auth reply
            }
            sock
        };
        let (mut rd, mut wr) = sock.into_split();
        let (ptx, mut prx) = mpsc::unbounded_channel();
        // read in a task of its own: a read cut off by select! would lose half a packet
        let reader = tokio::spawn(async move {
            while let Ok(p) = source_read(&mut rd).await {
                if ptx.send(p).is_err() {
                    break;
                }
            }
        });
        let (out_tx, mut out_rx) = mpsc::unbounded_channel::<String>();
        {
            let mut i = self.inner.lock();
            i.info = json!({"protocol": "source", "project": "renaissance", "game": "Halo: Reach"}); // the only Source RCON server ONI knows
            i.out = Some(out_tx);
        }
        self.set(tx, "online", "Source RCON".into(), 0);
        let mut asked: HashMap<i32, (String, String)> = HashMap::new(); // id -> (command, reply so far)
        let end = async {
            loop {
                tokio::select! {
                    p = prx.recv() => {
                        let Some((id, _, body)) = p else { return Ok(None) };
                        if body.is_empty() {
                            if let Some((command, text)) = asked.remove(&id) {
                                self.dispatch(source_reply(id, &command, &text), tx);
                            }
                        } else if let Some((_, text)) = asked.get_mut(&id) {
                            text.push_str(&body);
                        }
                    }
                    out = out_rx.recv() => {
                        let Some(msg) = out else { return Ok(None) };
                        let v: Value = serde_json::from_str(&msg).unwrap_or_default();
                        let id = v["id"].as_u64().unwrap_or(0) as i32;
                        let command = v["command"].as_str().unwrap_or_default().to_string();
                        let args: Vec<String> = v["args"].as_array().into_iter().flatten().filter_map(|a| a.as_str().map(String::from)).collect();
                        asked.insert(id, (command.clone(), String::new()));
                        wr.write_all(&source_packet(id, 2, &source_line(&command, &args))).await.map_err(|e| e.to_string())?;
                    }
                }
            }
        }
        .await;
        reader.abort();
        end
    }

    fn dispatch(&self, msg: Value, tx: &mpsc::UnboundedSender<LinkNote>) {
        match msg.get("type").and_then(Value::as_str) {
            Some("reply") => {
                let id = msg.get("id").and_then(Value::as_u64).unwrap_or(0);
                let mut i = self.inner.lock();
                if let Some(f) = i.pending.remove(&id) {
                    let _ = f.send(msg);
                } else if let Some(pos) = i.late.iter().position(|(x, _)| *x == id) {
                    let (_, line) = i.late.remove(pos).unwrap();
                    let _ = tx.send(LinkNote::Late { line, reply: msg });
                }
            }
            Some("event") => {
                let _ = tx.send(LinkNote::Event(msg));
            }
            other => {
                // {"type":"error"} for a malformed message, or anything newer than this client
                let mut m = msg.clone();
                let kind = other.unwrap_or("unknown").to_string();
                if let Some(o) = m.as_object_mut() {
                    o.insert("type".into(), "event".into());
                    o.insert("event".into(), kind.into());
                }
                let _ = tx.send(LinkNote::Event(m));
            }
        }
    }

    /// Run one command; the reply ({"ok","text","data"?}). Never retried: commands aren't idempotent.
    pub async fn call(&self, command: &str, args: &[String], timeout: f64) -> Result<Value, CallError> {
        let id = self.ids.fetch_add(1, Ordering::Relaxed);
        let (ftx, frx) = oneshot::channel();
        {
            let mut i = self.inner.lock();
            let Some(out) = i.out.clone() else {
                let why = if i.detail.is_empty() { i.state.to_string() } else { i.detail.clone() };
                return Err(CallError::NotConnected(why));
            };
            i.pending.insert(id, ftx);
            let msg = json!({"type": "command", "command": command, "args": args, "id": id, "by": self.by});
            if out.send(msg.to_string()).is_err() {
                i.pending.remove(&id);
                return Err(CallError::Lost);
            }
        }
        match tokio::time::timeout(Duration::from_secs_f64(timeout), frx).await {
            Ok(Ok(v)) => Ok(v),
            Ok(Err(_)) => Err(CallError::Lost),
            Err(_) => {
                let mut i = self.inner.lock();
                i.pending.remove(&id);
                i.late.push_back((id, std::iter::once(command.to_string()).chain(args.iter().cloned()).collect::<Vec<_>>().join(" ")));
                while i.late.len() > 200 {
                    i.late.pop_front(); // replies that never come shouldn't pile up for ever
                }
                Err(CallError::Timeout(timeout))
            }
        }
    }
}

fn source_packet(id: i32, kind: i32, body: &str) -> Vec<u8> {
    let b = body.as_bytes();
    let mut p = Vec::with_capacity(14 + b.len());
    p.extend_from_slice(&(10 + b.len() as i32).to_le_bytes());
    p.extend_from_slice(&id.to_le_bytes());
    p.extend_from_slice(&kind.to_le_bytes());
    p.extend_from_slice(b);
    p.extend_from_slice(&[0, 0]);
    p
}

/// One packet: (id, type, body).
async fn source_read(r: &mut (impl tokio::io::AsyncRead + Unpin)) -> std::io::Result<(i32, i32, String)> {
    use tokio::io::AsyncReadExt;
    let size = r.read_i32_le().await?;
    if !(10..=1 << 20).contains(&size) {
        return Err(std::io::ErrorKind::InvalidData.into());
    }
    let mut p = vec![0; size as usize];
    r.read_exact(&mut p).await?;
    let id = i32::from_le_bytes(p[0..4].try_into().unwrap());
    let kind = i32::from_le_bytes(p[4..8].try_into().unwrap());
    Ok((id, kind, String::from_utf8_lossy(&p[8..p.len() - 2]).into_owned()))
}

/// The command line: an argument with spaces is quoted, but not the last, which servers read as the rest of the line
/// (say's message, kick's reason).
fn source_line(command: &str, args: &[String]) -> String {
    let mut line = command.to_string();
    for (i, a) in args.iter().enumerate() {
        let quote = i + 1 < args.len() && (a.is_empty() || a.contains(|c: char| c.is_whitespace() || c == '"'));
        line.push(' ');
        line += &if quote { format!("\"{}\"", a.replace('\\', "\\\\").replace('"', "\\\"")) } else { a.clone() };
    }
    line
}

/// A Source RCON reply shaped as a Reclaimer one. Only its text says whether it worked.
fn source_reply(id: i32, command: &str, text: &str) -> Value {
    // ponytail: failure read from Reach Renaissance's wording (dedicated/src/Rcon.cs, Chat.cs); a server that words it otherwise reads as ok
    let failed = [
        "unknown command",
        "usage:",
        "that command failed",
        "the engine wouldn't",
        "couldn't write the announcement",
        "nothing to announce",
        "can't read the ban list",
        "the engine's ban list didn't make sense",
        "nobody here is banned as",
        "nobody in this game",
        "two players here are called",
        "this server isn't running yet",
    ]
        .iter()
        .any(|f| text.starts_with(f))
        // say with match chat down: the file is written and dropped, so no player saw it
        || text.contains(" — nobody saw it: ");
    let mut r = json!({"type": "reply", "id": id, "ok": !failed, "text": text});
    let data = match command {
        _ if failed => None,
        "status" => rr_status(text),
        "players" => Some(rr_players(text)),
        "bans" => Some(rr_bans(text)),
        "maps" | "modes" => Some(rr_entries(command, text)),
        _ => None,
    };
    if let Some(d) = data {
        r["data"] = d;
    }
    r
}

/// Reach Renaissance's status: "NAME: STATE", "Current game: MODE on MAP (started …)", "Players: N/MAX", "Next: …".
fn rr_status(text: &str) -> Option<Value> {
    let mut lines = text.lines();
    let (name, phase) = lines.next()?.rsplit_once(": ")?;
    let mut d = json!({"name": name, "phase": phase});
    for l in lines {
        if let Some(g) = l.strip_prefix("Current game: ") {
            if let Some((mode, map)) = g.split(" (started").next().unwrap_or(g).rsplit_once(" on ") {
                d["mode"] = json!(mode);
                d["map"] = json!(map);
            }
        } else if let Some((n, m)) = l.strip_prefix("Players: ").and_then(|p| p.split_once('/')) {
            d["players"] = json!(n.trim().parse::<i64>().ok());
            d["max_players"] = json!(m.trim().parse::<i64>().ok());
        } else if let Some(n) = l.strip_prefix("Next: ") {
            d["next"] = json!(n);
        }
    }
    Some(d)
}

/// Reach Renaissance's players: "1. name: NAME, id: …, peer: …, address: …[, relayed]…", then "N/MAX players".
fn rr_players(text: &str) -> Value {
    use once_cell::sync::Lazy;
    use regex::Regex;
    static ROW: Lazy<Regex> = Lazy::new(|| Regex::new(r"^\d+\. name: (.*)$").unwrap());
    static END: Lazy<Regex> = Lazy::new(|| Regex::new(r", (?:id: |peer: |address: |relayed|banned|joined)").unwrap());
    static FACT: Lazy<Regex> = Lazy::new(|| Regex::new(r", (id|peer|address): ([^,]+)").unwrap());
    static MAX: Lazy<Regex> = Lazy::new(|| Regex::new(r"^\d+/(\d+) players$").unwrap());
    let (mut players, mut max) = (vec![], Value::Null);
    for l in text.lines() {
        if let Some(c) = ROW.captures(l) {
            let rest = c.get(1).unwrap().as_str();
            let name = END.find(rest).map_or(rest, |m| &rest[..m.start()]);
            let mut p = json!({"name": name});
            for f in FACT.captures_iter(&rest[name.len()..]) {
                p[&f[1]] = json!(&f[2]);
            }
            players.push(p);
        } else if let Some(c) = MAX.captures(l) {
            max = json!(c[1].parse::<i64>().ok());
        }
    }
    json!({"players": players, "max_players": max})
}

/// Reach Renaissance's bans: "1. NAME: id HEX, address IP, until YYYY-MM-DD HH:MM | permanent, "REASON"", then a count.
/// A ban with an id is a player ban (unban takes the id), one with only an address an address ban.
fn rr_bans(text: &str) -> Value {
    use chrono::TimeZone;
    use once_cell::sync::Lazy;
    use regex::Regex;
    static ROW: Lazy<Regex> = Lazy::new(|| {
        Regex::new(r#"^\d+\. (.*?): (?:id ([0-9A-Fa-f]+), )?(?:address ([^,]+), )?(?:until (\d{4}-\d\d-\d\d \d\d:\d\d)|permanent)(?:, "(.*)")?$"#).unwrap()
    });
    let (mut players, mut ips) = (vec![], vec![]);
    for c in text.lines().filter_map(|l| ROW.captures(l)) {
        // ponytail: RR prints the end in the server's local time; read as this PC's, so EXPIRES is off by the zone gap when they differ
        let expires = c.get(4).and_then(|t| chrono::NaiveDateTime::parse_from_str(t.as_str(), "%Y-%m-%d %H:%M").ok())
            .and_then(|t| chrono::Local.from_local_datetime(&t).earliest())
            .map(|t| t.timestamp() as f64);
        let mut b = json!({"name": &c[1], "reason": c.get(5).map(|r| r.as_str()), "expires": expires});
        match (c.get(2), c.get(3)) {
            (Some(id), _) => {
                b["id"] = json!(id.as_str());
                players.push(b)
            }
            (None, Some(ip)) => {
                b["ip"] = json!(ip.as_str());
                ips.push(b)
            }
            _ => {}
        }
    }
    json!({"players": players, "ips": ips, "devices": []})
}

/// Reach Renaissance's maps and modes: one name per line (scenario names; game type file names), or a "no …" line.
fn rr_entries(command: &str, text: &str) -> Value {
    let kind = if command == "maps" { "map" } else { "mode" };
    let e: Vec<Value> = text.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with("no maps in") && !l.starts_with("no game types found"))
        .map(|l| json!({"kind": kind, "name": l, "reference": l}))
        .collect();
    json!({"entries": e})
}

pub fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(0)
}

pub const SSH_OPTS: &[&str] = &["-o", "BatchMode=yes", "-o", "ConnectTimeout=15"];

/// One `ssh -N -L ...` per destination, forwarding every server behind it; restarted if ssh exits.
pub struct Tunnel {
    pub dest: String,
    pub local: Vec<((String, u16), u16)>, // (host, port) -> local port
    pub ready: watch::Sender<bool>,
    pub state: Arc<Mutex<(String, String)>>,
}

impl Tunnel {
    pub fn new(dest: &str, remotes: &[(String, u16)]) -> Self {
        let mut local: Vec<((String, u16), u16)> = vec![];
        for r in remotes {
            if !local.iter().any(|(x, _)| x == r) {
                local.push((r.clone(), free_port()));
            }
        }
        Tunnel {
            dest: dest.into(),
            local,
            ready: watch::channel(false).0,
            state: Arc::new(Mutex::new(("opening".into(), String::new()))),
        }
    }

    pub fn local_port(&self, host: &str, port: u16) -> u16 {
        self.local.iter().find(|((h, p), _)| h == host && *p == port).map(|(_, l)| *l).unwrap_or(0)
    }

    pub async fn run(self: Arc<Self>, tx: mpsc::UnboundedSender<(String, String, String)>) {
        let set = |s: &str, d: String| {
            *self.state.lock() = (s.to_string(), d.clone());
            let _ = tx.send((self.dest.clone(), s.to_string(), d));
        };
        let Some(ssh) = crate::util::which("ssh") else {
            set("down", "no ssh client on PATH".into());
            return;
        };
        let mut delay: u64 = 2;
        loop {
            set("opening", String::new());
            let mut c = crate::util::command(&ssh.to_string_lossy());
            c.args(["-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]);
            for ((host, port), local) in &self.local {
                c.arg("-L").arg(format!("127.0.0.1:{local}:{host}:{port}"));
            }
            c.arg(&self.dest)
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::piped());
            let (err, code, was_up) = match c.spawn() {
                Err(e) => (e.to_string(), None, false),
                Ok(mut child) => {
                    let mut stderr = child.stderr.take();
                    let port = self.local.first().map(|(_, l)| *l).unwrap_or(0);
                    let me = self.clone();
                    let set2 = {
                        let tx = tx.clone();
                        let state = self.state.clone();
                        let dest = self.dest.clone();
                        move || {
                            *state.lock() = ("up".into(), String::new());
                            let _ = tx.send((dest.clone(), "up".into(), String::new()));
                        }
                    };
                    // ssh listens locally only once it has signed in, so a local port accepting means it's up
                    let probe = tokio::spawn(async move {
                        loop {
                            if tokio::net::TcpStream::connect(("127.0.0.1", port)).await.is_ok() {
                                set2();
                                let _ = me.ready.send(true);
                                return;
                            }
                            tokio::time::sleep(Duration::from_millis(300)).await;
                        }
                    });
                    let mut buf = Vec::new();
                    if let Some(s) = stderr.as_mut() {
                        use tokio::io::AsyncReadExt;
                        let _ = s.read_to_end(&mut buf).await;
                    }
                    let status = child.wait().await.ok();
                    probe.abort();
                    let up = self.state.lock().0 == "up";
                    (String::from_utf8_lossy(&buf).trim().to_string(), status.and_then(|s| s.code()), up)
                }
            };
            let _ = self.ready.send(false);
            if was_up {
                delay = 2;
            }
            let last = err.lines().last().map(str::to_string).unwrap_or_else(|| format!("ssh exited {}", code.unwrap_or(-1)));
            set("down", format!("{last}; retry in {delay}s"));
            tokio::time::sleep(Duration::from_secs(delay)).await;
            delay = (delay * 2).min(60);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reach_renaissance_text_reads_as_data() {
        let s = source_reply(1, "status", "Oni OCE | Reach: in_game\nCurrent game: Firefight on ff_overlook (started 3m ago)\nPlayers: 2/16\nEngine: build 1");
        assert_eq!(s["data"], json!({"name": "Oni OCE | Reach", "phase": "in_game", "mode": "Firefight", "map": "ff_overlook", "players": 2, "max_players": 16}));
        let p = source_reply(2, "players", "1. name: viik, the one, id: 00ab, peer: 5, address: 1.2.3.4, relayed\n2. name: x, joined after this game started\n2/16 players");
        assert_eq!(p["data"]["players"], json!([{"name": "viik, the one", "id": "00ab", "peer": "5", "address": "1.2.3.4"}, {"name": "x"}]));
        assert_eq!(p["data"]["max_players"], 16);
        let bad = source_reply(3, "endmatch", "unknown command \"endmatch\". Try: help");
        assert_eq!((&bad["ok"], bad.get("data")), (&json!(false), None));
        // say: RR's Chat.cs wording when match chat is up, down, or the file can't be written
        assert_eq!(source_reply(4, "say", "announced: hi all")["ok"], true);
        let unseen = "announced: hi all — nobody saw it: the engine's match chat isn't up (no online session)";
        assert_eq!(source_reply(5, "say", unseen)["ok"], false);
        assert_eq!(source_reply(6, "say", "couldn't write the announcement: Access denied")["ok"], false);
        assert_eq!(source_reply(7, "say", "nothing to announce")["ok"], false);
        let id = format!("{}00ab", "0".repeat(60));
        let b = source_reply(8, "bans", &format!("1. Griefer: id {id}, address 1.2.3.4, until 2030-01-02 03:04, \"team: killing\"\n2. no name: address 5.6.7.0, permanent\n2 ban(s) in bans.json, shared by every server"));
        assert_eq!(b["data"]["players"][0]["id"], json!(id));
        assert_eq!((&b["data"]["players"][0]["name"], &b["data"]["players"][0]["reason"]), (&json!("Griefer"), &json!("team: killing")));
        assert!(b["data"]["players"][0]["expires"].as_f64().unwrap() > 1.8e9);
        assert_eq!(b["data"]["ips"], json!([{"name": "no name", "ip": "5.6.7.0", "reason": null, "expires": null}]));
        assert_eq!(source_reply(9, "bans", "nobody is banned")["data"]["players"], json!([]));
        assert_eq!(source_reply(10, "bans", "can't read the ban list: refused")["ok"], false);
        let m = source_reply(11, "maps", "Boneyard\nSwordBase");
        assert_eq!(m["data"]["entries"][1], json!({"kind": "map", "name": "SwordBase", "reference": "SwordBase"}));
        assert_eq!(source_reply(12, "modes", "no game types found; put .bin files in Game Modes or set mcc")["data"]["entries"], json!([]));
        assert_eq!(source_reply(13, "unban", "nobody here is banned as \"x\" (try: bans)")["ok"], false);
        assert_eq!(source_line("kick", &["a b".into(), "being rude".into()]), "kick \"a b\" being rude");
        assert_eq!(source_line("say", &["hello all".into()]), "say hello all");
    }

    #[tokio::test]
    async fn a_source_server_signs_in_and_answers() {
        use tokio::io::AsyncWriteExt;
        let l = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = l.local_addr().unwrap().port();
        tokio::spawn(async move {
            let (mut s, _) = l.accept().await.unwrap();
            let (id, _, pw) = source_read(&mut s).await.unwrap();
            s.write_all(&source_packet(if pw == "pw" { id } else { -1 }, 2, "")).await.unwrap();
            while let Ok((id, _, line)) = source_read(&mut s).await {
                s.write_all(&source_packet(id, 0, &format!("you said {line}"))).await.unwrap();
                s.write_all(&source_packet(id, 0, "")).await.unwrap();
            }
        });
        let rc = Rcon::new("pw", "test");
        let (tx, mut rx) = mpsc::unbounded_channel();
        tokio::spawn(rc.clone().run(format!("source://127.0.0.1:{port}"), None, Arc::new(Semaphore::new(1)), tx));
        while !matches!(rx.recv().await, Some(LinkNote::State { state: "online", .. })) {}
        let r = rc.call("say", &["hi there".into()], 5.0).await.unwrap();
        assert_eq!((&r["ok"], &r["text"]), (&json!(true), &json!("you said say hi there")));
    }
}
