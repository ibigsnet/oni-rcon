//! Session plug-ins (docs/PLUGINS.md): one process per plug-in, started with the console and stopped with it, spoken to
//! in JSON lines over its stdin and stdout. Oni writes `hello`, the events it may have, replies to its RCON, and view and
//! action requests tagged with a `ref` (answered by the line carrying the same `ref`); the plug-in writes `rcon`, `view`,
//! `notify` and `log` whenever it likes. What it writes is shaped here the way a service plug-in's reply is, then handed
//! to the window, which sends its RCON through the console's own links.
use crate::plugins::{self, Manifest};
use once_cell::sync::Lazy;
use parking_lot::Mutex;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::sync::{mpsc, oneshot};

const MAX_LINE: u64 = 1 << 20; // bytes of one line it writes; the rest of a longer one is read as a line that won't parse
const BACKOFF_MAX: f64 = 60.0; // seconds between restarts at most; a run this long resets the wait to 1 s
const QUEUE: usize = 256; // lines waiting for it to read; past that, while it isn't reading, the rest are dropped
const VIEW_EVERY: Duration = Duration::from_millis(200); // pushed views are drawn at most this often, the latest always

/// Where what it says goes: the window's "oni" event in the app, a list in the tests.
pub type Emit = Arc<dyn Fn(Value) + Send + Sync>;
type Waiting = Arc<Mutex<HashMap<u64, oneshot::Sender<Value>>>>;

struct Session {
    tx: mpsc::UnboundedSender<String>, // lines for its stdin
    waiting: Waiting,                  // asks not yet answered, by ref
    up: Arc<AtomicBool>,
    task: tauri::async_runtime::JoinHandle<()>,
}

/// What every run of one plug-in shares.
struct Ctx {
    m: Manifest,
    hello: Value,
    names: Vec<String>, // the servers as plug-ins name them: an `rcon` for any other is refused
    tx: mpsc::UnboundedSender<String>,
    waiting: Waiting,
    up: Arc<AtomicBool>,
    emit: Emit,
}

static SESSIONS: Lazy<Mutex<HashMap<String, Session>>> = Lazy::new(Default::default);
static REF: AtomicU64 = AtomicU64::new(1);

/// Start its process, and keep it going, unless it already is. `servers` are `{index, name}` for each server.
pub fn start(m: Manifest, servers: Vec<Value>, emit: Emit) {
    let mut all = SESSIONS.lock();
    if all.get(&m.id).is_some_and(|s| !s.task.inner().is_finished()) {
        return;
    }
    let (tx, rx) = mpsc::unbounded_channel();
    let (waiting, up) = (Waiting::default(), Arc::new(AtomicBool::new(false)));
    let hello = json!({"type": "hello", "api": plugins::API, "servers": servers,
        "granted": {"events": m.events, "rcon": m.rcon, "addresses": m.addresses}});
    let names = servers.iter().filter_map(|s| s["name"].as_str().map(String::from)).collect();
    let id = m.id.clone();
    let c = Ctx { m, hello, names, tx: tx.clone(), waiting: waiting.clone(), up: up.clone(), emit };
    // the app's runtime, not tokio::spawn: a sync command calls this from the main thread, where there's no runtime
    all.insert(id, Session { tx, waiting, up, task: tauri::async_runtime::spawn(supervise(c, rx)) });
}

/// Stop one, so the next start runs it afresh (after an update).
pub fn stop(id: &str) {
    if let Some(s) = SESSIONS.lock().remove(id) {
        s.task.abort();
    }
}

/// Stop every one: its stdin closes (the sign to exit) and the process is killed.
pub fn stop_all() {
    for (_, s) in SESSIONS.lock().drain() {
        s.task.abort();
    }
}

fn running<T>(id: &str, f: impl FnOnce(&Session) -> T) -> Result<T, String> {
    SESSIONS.lock().get(id).filter(|s| s.up.load(Ordering::Relaxed)).map(f).ok_or_else(|| "it isn't running".into())
}

/// Write one message to it.
pub fn send(id: &str, msg: &Value) -> Result<(), String> {
    running(id, |s| s.tx.send(format!("{msg}\n")).is_ok())?.then_some(()).ok_or_else(|| "it isn't running".into())
}

/// A request tagged with a fresh `ref`, and the line that answers it.
pub async fn ask(id: &str, mut request: Value, timeout: f64) -> Result<Value, String> {
    let r = REF.fetch_add(1, Ordering::Relaxed);
    request["ref"] = json!(r);
    let (tx, rx) = oneshot::channel();
    let waiting = running(id, |s| {
        s.waiting.lock().insert(r, tx);
        let _ = s.tx.send(format!("{request}\n"));
        s.waiting.clone()
    })?;
    match tokio::time::timeout(Duration::from_secs_f64(timeout), rx).await {
        Ok(Ok(v)) => Ok(v),
        Ok(Err(_)) => Err("it stopped before answering".into()),
        Err(_) => {
            waiting.lock().remove(&r);
            Err(format!("no answer in {timeout}s"))
        }
    }
}

/// Run it, and again when it stops: after 1 s, then twice as long each time up to a minute.
async fn supervise(c: Ctx, mut rx: mpsc::UnboundedReceiver<String>) {
    let mut wait = 1.0;
    loop {
        while rx.try_recv().is_ok() {} // what was meant for the last run isn't for this one
        let t0 = Instant::now();
        let why = run(&c, &mut rx).await;
        c.up.store(false, Ordering::Relaxed);
        c.waiting.lock().clear(); // each ask still waiting hears that it stopped
        if t0.elapsed().as_secs_f64() > BACKOFF_MAX {
            wait = 1.0;
        }
        (c.emit)(json!({"kind": "plugin-session", "id": c.m.id, "state": "down", "detail": why, "retry_in": wait}));
        tokio::time::sleep(Duration::from_secs_f64(wait)).await;
        wait = (wait * 2.0).min(BACKOFF_MAX);
    }
}

/// One run, to its end; why it ended.
async fn run(c: &Ctx, rx: &mut mpsc::UnboundedReceiver<String>) -> String {
    let child = plugins::command(&c.m).and_then(|mut cmd| cmd.spawn().map_err(|e| crate::health::scrub(&e.to_string())));
    let mut child = match child {
        Ok(ch) => ch,
        Err(e) => return e,
    };
    let (mut stdin, stdout, stderr) = (child.stdin.take().unwrap(), child.stdout.take().unwrap(), child.stderr.take().unwrap());
    let errs = plugins::tail(stderr);
    let (ltx, mut lines) = mpsc::channel::<Vec<u8>>(64);
    tokio::spawn(async move {
        let mut r = BufReader::new(stdout);
        loop {
            let mut buf = Vec::new();
            match (&mut r).take(MAX_LINE).read_until(b'\n', &mut buf).await {
                Ok(0) | Err(_) => break,
                Ok(_) if ltx.send(buf).await.is_err() => break,
                Ok(_) => {}
            }
        }
    });
    let mut writer = None;
    if stdin.write_all(format!("{}\n", c.hello).as_bytes()).await.is_ok() {
        c.up.store(true, Ordering::Relaxed);
        (c.emit)(json!({"kind": "plugin-session", "id": c.m.id, "state": "running", "detail": "", "retry_in": 0}));
        // its stdin is written by a task of its own, so one that stops reading never stops this loop hearing it
        let (wtx, mut wrx) = mpsc::channel::<String>(QUEUE);
        writer = Some(tokio::spawn(async move {
            while let Some(line) = wrx.recv().await {
                if stdin.write_all(line.as_bytes()).await.is_err() {
                    break;
                }
            }
        }));
        let mut dropped = 0u64;
        let mut held: Option<Value> = None; // the latest view it pushed, not drawn yet
        let mut drawn = Instant::now().checked_sub(VIEW_EVERY).unwrap_or_else(Instant::now);
        loop {
            let due = tokio::time::sleep_until((drawn + VIEW_EVERY).into());
            tokio::select! {
                out = rx.recv() => match out {
                    Some(line) => match wtx.try_send(line) {
                        Ok(()) => dropped = 0,
                        Err(mpsc::error::TrySendError::Full(_)) => {
                            if dropped % 1000 == 0 {
                                (c.emit)(json!({"kind": "plugin-log", "id": c.m.id, "warn": true,
                                    "text": format!("isn't reading what Oni sends it: {QUEUE} messages wait, newer ones are dropped")}));
                            }
                            dropped += 1;
                        }
                        Err(mpsc::error::TrySendError::Closed(_)) => break, // its stdin closed
                    },
                    None => break,
                },
                line = lines.recv() => match line {
                    Some(l) => held = handle(c, &l).or(held),
                    None => break, // its stdout closed: it's exiting
                },
                _ = due, if held.is_some() => {
                    draw(c, held.take().unwrap_or_default());
                    drawn = Instant::now();
                }
            }
        }
        if let Some(v) = held {
            draw(c, v); // its last word before it stopped
        }
    }
    c.up.store(false, Ordering::Relaxed);
    if let Some(w) = writer {
        w.abort(); // drops its stdin, the sign to exit, even mid-write to one that isn't reading
    }
    let code = match tokio::time::timeout(Duration::from_secs(2), child.wait()).await {
        Ok(Ok(s)) => s.code().unwrap_or(-1),
        _ => {
            let _ = child.kill().await;
            -1
        }
    };
    // ponytail: on Windows its own children outlive a kill of the cmd wrapper; they see stdin close. Job objects if not enough
    let err = tokio::time::timeout(Duration::from_secs(1), errs).await.ok().and_then(Result::ok).unwrap_or_default();
    plugins::last_err(&err, format!("exited {code}"))
}

/// A view it pushed, shaped and drawn.
fn draw(c: &Ctx, v: Value) {
    let mut view = plugins::normalise_view(&v);
    plugins::stash(&c.m.id, &mut view);
    (c.emit)(json!({"kind": "plugin-view", "id": c.m.id, "view": view}));
}

/// One line it wrote; a view it pushed is handed back, to be drawn when one is due. A line that isn't a JSON object
/// is ignored, like a stray print.
fn handle(c: &Ctx, line: &[u8]) -> Option<Value> {
    let Ok(v @ Value::Object(_)) = serde_json::from_slice::<Value>(line) else { return None };
    if let Some(r) = v.get("ref").and_then(Value::as_u64) {
        if let Some(w) = c.waiting.lock().remove(&r) {
            let _ = w.send(v);
        }
        return None;
    }
    let (id, emit) = (&c.m.id, &c.emit);
    match v["type"].as_str().unwrap_or_default() {
        "view" => return Some(v),
        "notify" => {
            let level = match v["level"].as_str() {
                Some("warn" | "warning") => "warning",
                Some("error" | "alert") => "error",
                _ => "information",
            };
            emit(json!({"kind": "plugin-notify", "id": id, "level": level, "text": plugins::sn(v.get("text"), 500)}));
        }
        "log" => emit(json!({"kind": "plugin-log", "id": id, "text": plugins::sn(v.get("text"), 500)})),
        "rcon" => {
            let rid = match v.get("id") {
                Some(x @ Value::Number(_)) => x.clone(),
                Some(Value::String(t)) => json!(plugins::cut(t, 100)),
                _ => Value::Null,
            };
            let n = plugins::normalise_action(&json!({"rcon": [&v]}), &c.m.rcon, &c.names);
            match n["rcon"].get(0) {
                Some(x) => emit(json!({"kind": "plugin-rcon", "id": id, "ref": rid, "server": x["server"], "command": x["command"], "args": x["args"]})),
                None => {
                    let why = n["dropped"][0].as_str().unwrap_or("not sent").to_string();
                    let _ = c.tx.send(format!("{}\n", json!({"type": "rcon-reply", "id": rid, "ok": false, "text": format!("not sent: {why}")})));
                    emit(json!({"kind": "plugin-log", "id": id, "text": format!("asked for {why}: not sent"), "warn": true}));
                }
            }
        }
        _ => {}
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    const ECHO: &str = r#"import sys, json
def out(m): print(json.dumps(m), flush=True)
for line in sys.stdin:
    m = json.loads(line)
    t = m.get("type")
    if t == "hello": out({"type": "view", "title": "hi " + m["servers"][0]["name"], "badge": {"level": "warn", "text": "X"}})
    elif "ref" in m: out({"ref": m["ref"], "title": m["type"]})
    elif t == "event" and m["ev"].get("die"): sys.exit(3)
    elif t == "event": out({"type": "rcon", "id": 7, "server": "A", "command": m["ev"]["cmd"], "args": ["hi"]})
    elif t == "rcon-reply": out({"type": "log", "text": m["text"]})
"#;

    async fn until(seen: &Arc<Mutex<Vec<Value>>>, f: impl Fn(&Value) -> bool) -> Value {
        for _ in 0..200 {
            if let Some(v) = seen.lock().iter().find(|v| f(v)) {
                return v.clone();
            }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
        panic!("never saw it in {:?}", seen.lock());
    }

    #[tokio::test]
    async fn a_session_says_hello_answers_asks_has_rcon_filtered_and_is_restarted() {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("echo.py"), ECHO).unwrap();
        let py = if crate::util::which("python").is_some() { "python" } else { "python3" };
        let m = Manifest { id: "echo-session".into(), name: "E".into(), mode: "session".into(), exec: format!("{py} echo.py"),
            rcon: vec!["say".into()], folder: Some(d.path().to_path_buf()), ..Default::default() };
        let seen = Arc::new(Mutex::new(Vec::<Value>::new()));
        let s2 = seen.clone();
        start(m, vec![json!({"index": 0, "name": "A"})], Arc::new(move |v| s2.lock().push(v)));
        let v = until(&seen, |v| v["kind"] == "plugin-view").await;
        assert_eq!((&v["view"]["title"], &v["view"]["badge"]["level"]), (&json!("hi A"), &json!("warn")));
        until(&seen, |v| v["state"] == "running").await;
        assert_eq!(ask("echo-session", json!({"type": "view"}), 5.0).await.unwrap()["title"], "view");
        send("echo-session", &json!({"type": "event", "ev": {"cmd": "say"}})).unwrap();
        let r = until(&seen, |v| v["kind"] == "plugin-rcon").await;
        assert_eq!((&r["ref"], &r["command"], &r["args"]), (&json!(7), &json!("say"), &json!(["hi"])));
        send("echo-session", &json!({"type": "event", "ev": {"cmd": "ban"}})).unwrap();
        until(&seen, |v| v["text"] == "not sent: ban: not in its permissions").await; // its own reply, logged back
        seen.lock().clear();
        send("echo-session", &json!({"type": "event", "ev": {"die": true}})).unwrap();
        let down = until(&seen, |v| v["state"] == "down").await;
        assert_eq!((&down["detail"], &down["retry_in"]), (&json!("exited 3"), &json!(1.0)));
        assert_eq!(ask("echo-session", json!({"type": "view"}), 5.0).await.unwrap_err(), "it isn't running");
        until(&seen, |v| v["kind"] == "plugin-view").await; // back after its second
        stop_all();
        assert!(send("echo-session", &json!({})).is_err());
    }

    #[tokio::test]
    async fn one_that_stops_reading_is_still_heard() {
        // it never reads its stdin: once the pipe filled, writing to it blocked the loop that reads its stdout as well
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("deaf.py"), "import json, time\ni = 0\nwhile True:\n    print(json.dumps({\"type\": \"log\", \"text\": str(i)}), flush=True)\n    i += 1\n    time.sleep(0.01)\n").unwrap();
        let py = if crate::util::which("python").is_some() { "python" } else { "python3" };
        let m = Manifest { id: "deaf".into(), name: "D".into(), mode: "session".into(), exec: format!("{py} deaf.py"),
            folder: Some(d.path().to_path_buf()), ..Default::default() };
        let seen = Arc::new(Mutex::new(Vec::<Value>::new()));
        let s2 = seen.clone();
        start(m, vec![], Arc::new(move |v| s2.lock().push(v)));
        until(&seen, |v| v["kind"] == "plugin-log").await;
        let pad = "x".repeat(1000);
        for _ in 0..3000 {
            let _ = send("deaf", &json!({"type": "event", "ev": {"pad": pad}})); // 3 MB: far past any pipe's buffer
        }
        let heard = || seen.lock().iter().filter(|v| v["kind"] == "plugin-log" && v["warn"].is_null()).count();
        let before = heard();
        until(&seen, |v| v["warn"] == true && v["text"].as_str().is_some_and(|t| t.contains("isn't reading"))).await;
        for _ in 0..200 {
            if heard() > before + 50 {
                break;
            }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
        assert!(heard() > before + 50, "its log stopped at {} after {before}", heard());
        stop("deaf");
    }

    #[tokio::test]
    async fn a_burst_of_views_is_drawn_a_few_times_ending_with_the_last() {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("burst.py"), "import json, sys\nfor i in range(200):\n    print(json.dumps({\"type\": \"view\", \"title\": str(i)}), flush=True)\nfor line in sys.stdin:\n    pass\n").unwrap();
        let py = if crate::util::which("python").is_some() { "python" } else { "python3" };
        let m = Manifest { id: "burst".into(), name: "B".into(), mode: "session".into(), exec: format!("{py} burst.py"),
            folder: Some(d.path().to_path_buf()), ..Default::default() };
        let seen = Arc::new(Mutex::new(Vec::<Value>::new()));
        let s2 = seen.clone();
        start(m, vec![], Arc::new(move |v| s2.lock().push(v)));
        until(&seen, |v| v["view"]["title"] == "199").await;
        let views = seen.lock().iter().filter(|v| v["kind"] == "plugin-view").count();
        assert!(views <= 10, "{views} redraws for 200 views");
        stop("burst");
    }

    #[tokio::test]
    async fn starts_from_a_thread_with_no_runtime() {
        // a sync command runs on the main thread, outside any runtime: a bare tokio::spawn there panicked, and the
        // release build's panic = "abort" took the whole console down the moment a session plug-in was approved
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("echo.py"), ECHO).unwrap();
        let py = if crate::util::which("python").is_some() { "python" } else { "python3" };
        let m = Manifest { id: "no-runtime".into(), name: "E".into(), mode: "session".into(), exec: format!("{py} echo.py"),
            folder: Some(d.path().to_path_buf()), ..Default::default() };
        let seen = Arc::new(Mutex::new(Vec::<Value>::new()));
        let s2 = seen.clone();
        std::thread::spawn(move || start(m, vec![json!({"index": 0, "name": "A"})], Arc::new(move |v| s2.lock().push(v)))).join().unwrap();
        until(&seen, |v| v["kind"] == "plugin-view").await;
        stop("no-runtime");
    }
}
