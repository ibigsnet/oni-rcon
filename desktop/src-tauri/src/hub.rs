//! The console's backend: one RCON connection per server (through SSH tunnels where they're set), the health
//! reports, the Forge client and its watcher, and the rounds tally. The window asks it to run commands and hears back
//! from it in batches: a fleet's events are gathered for a few milliseconds and sent as one message, so a busy fleet
//! is one repaint, not hundreds.
use crate::config::Server;
use crate::fstate::ForgeState;
use crate::forge::{self, ForgeClient, ForgeError};
use crate::health::{self, Monitor};
use crate::install;
use crate::rcon::{CallError, LinkNote, Rcon, Tunnel};
use crate::stats::Rounds;
use crate::util::{self, pick_str, scrub_value, Secret};
use parking_lot::Mutex;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter};
use tokio::sync::{mpsc, watch, Semaphore};
use tokio::task::JoinHandle;

pub const SIGN_INS: usize = 8;
const HEALTH_EVERY: f64 = 60.0;
const HEALTH_GATE: usize = 4;
const OVERLAP: i64 = 300;
const RECONCILE: f64 = 6.0 * 3600.0;
const BATCH_MS: u64 = 40;

pub struct ForgeSetup {
    pub key: Secret,
    pub source: String,
    pub error: String,
    pub url: String,
    pub poll: f64,
    pub cache_dir: Option<PathBuf>,
    pub config: Option<PathBuf>,
}

pub struct HealthSetup {
    pub min_free: i64,
    pub service: String,
    pub demo: Option<Arc<crate::demo::FakeHost>>,
}

#[derive(Default)]
struct HealthRt {
    check_at: f64, // monotonic seconds since the hub started
    checking: bool,
    ok_at: f64, // epoch of the last report
    err: String,
}

pub struct StationRt {
    pub server: Server,
    pub rcon: Rcon,
    url: String,
    ready: Option<watch::Receiver<bool>>,
    task: Mutex<Option<JoinHandle<()>>>,
    health: Mutex<HealthRt>,
}

pub struct Hub {
    pub stations: Vec<Arc<StationRt>>,
    pub tunnels: Vec<Arc<Tunnel>>,
    pub labels: Mutex<Vec<String>>,
    tasks: Mutex<Vec<JoinHandle<()>>>,
    ui: mpsc::UnboundedSender<Value>,
    link_tx: Mutex<HashMap<usize, mpsc::UnboundedSender<LinkNote>>>,
    gate: Arc<Semaphore>,
    t0: Instant,
    pub monitor: Mutex<Monitor>,
    pub hsetup: HealthSetup,
    service_at: Mutex<HashMap<String, f64>>,
    service_err: Mutex<HashMap<String, String>>,
    health_gate: Arc<Semaphore>,
    pub fsetup: Mutex<ForgeSetup>,
    pub forge: Mutex<Option<Arc<ForgeClient>>>,
    pub fstate: Mutex<ForgeState>,
    rounds: Mutex<Rounds>,
    watching: Mutex<bool>,
    watch_said: Mutex<String>,
    plans: Mutex<HashMap<String, (Value, Value, Vec<install::File>)>>,
}

pub fn toast(title: &str, text: &str, severity: &str, timeout: f64) -> Value {
    json!({"kind": "toast", "title": title, "text": text, "severity": severity, "timeout": timeout})
}

impl Hub {
    pub fn emit(&self, mut v: Value) {
        scrub_value(&mut v);
        let _ = self.ui.send(v);
    }

    fn mono(&self) -> f64 {
        self.t0.elapsed().as_secs_f64()
    }

    pub fn label(&self, i: usize) -> String {
        self.labels.lock().get(i).cloned().unwrap_or_else(|| {
            let s = &self.stations[i].server;
            if s.name.is_empty() { s.where_() } else { s.name.clone() }
        })
    }

    fn spawn(&self, f: impl std::future::Future<Output = ()> + Send + 'static) {
        let h = tokio::spawn(f);
        let mut t = self.tasks.lock();
        t.retain(|h| !h.is_finished());
        t.push(h);
    }

    pub fn watched(&self, i: usize) -> bool {
        self.hsetup.demo.is_some() || !self.stations[i].server.health_cmd.is_empty()
    }

    /// Build the hub and start everything: tunnels, sign-ins (for every server with a password), health, Forge.
    pub fn start(
        app: AppHandle,
        servers: Vec<Server>,
        by: String,
        hsetup: HealthSetup,
        fsetup: ForgeSetup,
        state_dir: Option<PathBuf>,
        waiting: &[usize],
        extra: Vec<JoinHandle<()>>,
    ) -> Arc<Hub> {
        let (ui, mut ui_rx) = mpsc::unbounded_channel::<Value>();
        let mut remotes: Vec<(String, Vec<(String, u16)>)> = vec![];
        for s in servers.iter().filter(|s| !s.ssh.is_empty() && s.url.is_empty()) {
            match remotes.iter_mut().find(|(d, _)| *d == s.ssh) {
                Some((_, r)) => r.push((s.host.clone(), s.port)),
                None => remotes.push((s.ssh.clone(), vec![(s.host.clone(), s.port)])),
            }
        }
        let tunnels: Vec<Arc<Tunnel>> = remotes.iter().map(|(d, r)| Arc::new(Tunnel::new(d, r))).collect();
        let stations: Vec<Arc<StationRt>> = servers
            .into_iter()
            .map(|s| {
                let (url, ready) = match tunnels.iter().find(|t| !s.ssh.is_empty() && s.url.is_empty() && t.dest == s.ssh) {
                    Some(t) => (format!("ws://127.0.0.1:{}", t.local_port(&s.host, s.port)), Some(t.ready.subscribe())),
                    None => (s.direct_url(), None),
                };
                Arc::new(StationRt { rcon: Rcon::new(&s.password, &by), server: s, url, ready, task: Mutex::new(None), health: Mutex::new(HealthRt::default()) })
            })
            .collect();
        let min_free = hsetup.min_free;
        let poll = fsetup.poll;
        let hub = Arc::new(Hub {
            labels: Mutex::new(vec![]),
            tunnels,
            tasks: Mutex::new(extra),
            ui,
            link_tx: Mutex::new(HashMap::new()),
            gate: Arc::new(Semaphore::new(SIGN_INS)),
            t0: Instant::now(),
            monitor: Mutex::new(Monitor::new(min_free, util::now())),
            hsetup,
            service_at: Mutex::new(HashMap::new()),
            service_err: Mutex::new(HashMap::new()),
            health_gate: Arc::new(Semaphore::new(HEALTH_GATE)),
            fsetup: Mutex::new(fsetup),
            forge: Mutex::new(None),
            fstate: Mutex::new(ForgeState::load(state_dir.as_ref().map(|d| d.join("forge-state.json")))),
            rounds: Mutex::new(Rounds::new(state_dir.as_ref().map(|d| d.join("rounds.jsonl")))),
            watching: Mutex::new(false),
            watch_said: Mutex::new(String::new()),
            plans: Mutex::new(HashMap::new()),
            stations,
        });
        // the batcher: a few milliseconds of whatever happened, as one message
        hub.spawn(async move {
            let mut batch: Vec<Value> = vec![];
            loop {
                let first = ui_rx.recv().await;
                let Some(first) = first else { return };
                batch.push(first);
                let until = tokio::time::Instant::now() + Duration::from_millis(BATCH_MS);
                while let Ok(Some(v)) = tokio::time::timeout_at(until, ui_rx.recv()).await {
                    batch.push(v);
                    if batch.len() >= 2000 {
                        break;
                    }
                }
                let _ = app.emit("oni", std::mem::take(&mut batch));
            }
        });
        let (ttx, mut trx) = mpsc::unbounded_channel::<(String, String, String)>();
        for t in &hub.tunnels {
            hub.spawn(t.clone().run(ttx.clone()));
        }
        let h = hub.clone();
        hub.spawn(async move {
            while let Some((dest, state, detail)) = trx.recv().await {
                h.emit(json!({"kind": "tunnel", "dest": dest, "state": state, "detail": detail}));
            }
        });
        for i in 0..hub.stations.len() {
            hub.forwarder(i);
            if !waiting.contains(&i) {
                hub.connect(i);
            }
            if !hub.stations[i].server.ping_log.is_empty() {
                let h = hub.clone();
                hub.spawn(async move { h.follow_pings(i).await });
            }
            hub.stations[i].health.lock().check_at = (i.min(20) as f64) * 0.5; // a fleet's first reports spread out
        }
        if !hub.hsetup.service.is_empty() {
            let mut at = hub.service_at.lock();
            for st in hub.stations.iter() {
                if hub.hsetup.demo.is_some() || st.server.url.is_empty() {
                    at.entry(st.server.ssh.clone()).or_insert(1.0);
                }
            }
        }
        let h = hub.clone();
        hub.spawn(async move {
            loop {
                tokio::time::sleep(Duration::from_secs(2)).await;
                h.health_tick();
            }
        });
        hub.forge_connect();
        let h = hub.clone();
        hub.spawn(async move {
            tokio::time::sleep(Duration::from_secs(5)).await; // once soon after start, for what changed while closed
            loop {
                h.clone().forge_watch().await;
                tokio::time::sleep(Duration::from_secs_f64(poll.max(10.0))).await;
            }
        });
        hub
    }

    pub fn stop(&self) {
        for st in &self.stations {
            if let Some(t) = st.task.lock().take() {
                t.abort();
            }
        }
        for t in self.tasks.lock().drain(..) {
            t.abort();
        }
        if let Some(f) = self.forge.lock().as_ref() {
            f.stop.store(true, std::sync::atomic::Ordering::Relaxed);
        }
    }

    /// What the station's connection says, turned into what the window wants and what health and the tally need.
    fn forwarder(self: &Arc<Self>, i: usize) {
        let (tx, mut rx) = mpsc::unbounded_channel::<LinkNote>();
        self.link_tx.lock().insert(i, tx);
        let h = self.clone();
        self.spawn(async move {
            let mut prev = "";
            while let Some(n) = rx.recv().await {
                match n {
                    LinkNote::State { state, detail, retry_in, info } => {
                        let st = &h.stations[i];
                        if state == "offline" && prev == "online" {
                            let tunnel_down = h.tunnels.iter().any(|t| t.dest == st.server.ssh && st.server.url.is_empty() && t.state.lock().0 == "down");
                            if !tunnel_down {
                                h.monitor.lock().dropped(&i.to_string(), util::now()); // the server restarting, until its log says otherwise
                                if h.watched(i) {
                                    let soon = h.mono() + 8.0; // it comes back in 5 s: read the log then
                                    let mut hr = st.health.lock();
                                    hr.check_at = hr.check_at.min(soon);
                                }
                            }
                        }
                        if state != "online" {
                            h.rounds.lock().lost(&st.server.where_());
                        }
                        if state != "connecting" {
                            prev = state;
                        }
                        h.emit(json!({"kind": "link", "index": i, "state": state, "detail": detail, "retry_in": retry_in, "info": info}));
                    }
                    LinkNote::Event(ev) => h.emit(json!({"kind": "event", "index": i, "ev": ev})),
                    LinkNote::Late { line, reply } => h.emit(json!({"kind": "late", "index": i, "line": line, "reply": reply})),
                }
            }
        });
    }

    pub fn connect(self: &Arc<Self>, i: usize) {
        let st = self.stations[i].clone();
        let Some(tx) = self.link_tx.lock().get(&i).cloned() else { return };
        let fut = st.rcon.clone().run(st.url.clone(), st.ready.clone(), self.gate.clone(), tx);
        let old = st.task.lock().replace(tokio::spawn(fut));
        if let Some(o) = old {
            o.abort();
        }
    }

    /// Run one command. `ok` is null when no reply came in time: it may still have run.
    pub async fn call(&self, i: usize, command: &str, args: Vec<String>, timeout: f64) -> Value {
        let Some(st) = self.stations.get(i) else { return json!({"ok": false, "text": "no such station"}) };
        let mut r = match st.rcon.call(command, &args, timeout).await {
            Ok(mut r) => {
                let ok = r.get("ok").and_then(Value::as_bool).unwrap_or(false);
                r["ok"] = json!(ok);
                r
            }
            Err(e @ CallError::Timeout(_)) => json!({"ok": null, "text": e.to_string()}),
            Err(e) => json!({"ok": false, "text": e.to_string()}),
        };
        if r["ok"] == true && r["data"].is_object() {
            let data = &r["data"];
            if command == "status" {
                let w = st.server.where_();
                let fs = &self.fstate;
                let credit = |m: &str, g: &str| fs.lock().playing(&w, m, g);
                self.rounds.lock().observe(&w, data, &credit);
            }
            if (command == "status" || command == "players") && self.watched(i) {
                let n = if command == "status" {
                    match &data["players"] {
                        Value::Array(a) => Some(a.len() as i64),
                        v => v.as_i64(),
                    }
                } else {
                    Some(data["players"].as_array().map(|a| a.len()).unwrap_or(0) as i64)
                };
                self.monitor.lock().sample(&i.to_string(), n, util::now());
            }
        }
        scrub_value(&mut r);
        r
    }

    async fn follow_pings(&self, i: usize) {
        let re = regex::Regex::new(r"player ID ([0-9a-f]+), ping (\d+) ms").unwrap();
        let s = self.stations[i].server.clone();
        let mut delay = 2u64;
        loop {
            let mut c = if s.ssh.is_empty() {
                let mut c = util::shell(&s.ping_log);
                c.stdin(std::process::Stdio::null());
                c
            } else {
                let Some(ssh) = util::which("ssh") else { return };
                // the remote shell holds ssh's stdin and kills the follower when it closes: a dropped link or a quit
                // leaves no `docker logs -f` behind
                let mut c = util::command(&ssh.to_string_lossy());
                c.args(crate::rcon::SSH_OPTS).arg(&s.ssh).arg(format!("{} & p=$!; cat >/dev/null; kill $p", s.ping_log));
                c.stdin(std::process::Stdio::piped());
                c
            };
            c.stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::null());
            if let Ok(mut child) = c.spawn() {
                let _stdin = child.stdin.take(); // held open for as long as this runs
                if let Some(out) = child.stdout.take() {
                    use tokio::io::AsyncBufReadExt;
                    let mut lines = tokio::io::BufReader::new(out).lines();
                    while let Ok(Some(line)) = lines.next_line().await {
                        if let Some(m) = re.captures(&line) {
                            delay = 2;
                            self.emit(json!({"kind": "ping", "index": i, "id": &m[1], "ms": m[2].parse::<i64>().unwrap_or(0)}));
                        }
                    }
                }
                let _ = child.kill().await;
            }
            tokio::time::sleep(Duration::from_secs(delay)).await;
            delay = (delay * 2).min(60);
        }
    }

    // --- health -------------------------------------------------------------------------------------------------
    fn health_tick(self: &Arc<Self>) {
        let now = self.mono();
        for i in 0..self.stations.len() {
            if !self.watched(i) {
                continue;
            }
            {
                let mut hr = self.stations[i].health.lock();
                if hr.checking || now < hr.check_at {
                    continue;
                }
                hr.checking = true;
            }
            let h = self.clone();
            self.spawn(async move { h.health_check(i).await });
        }
        let due: Vec<String> = self.service_at.lock().iter().filter(|(_, at)| now >= **at).map(|(h, _)| h.clone()).collect();
        for host in due {
            self.service_at.lock().insert(host.clone(), now + HEALTH_EVERY + 3600.0);
            let h = self.clone();
            self.spawn(async move { h.health_service(host).await });
        }
    }

    pub fn health_now(&self) {
        for st in &self.stations {
            st.health.lock().check_at = 0.0;
        }
        for v in self.service_at.lock().values_mut() {
            *v = 0.0;
        }
    }

    async fn health_check(self: Arc<Self>, i: usize) {
        let st = self.stations[i].clone();
        let key = i.to_string();
        let first = !self.monitor.lock().seen.contains(&key);
        let ok_at = st.health.lock().ok_at;
        let since = if first { "86400s".to_string() } else { format!("{}s", (((util::now() - ok_at) * 2.0) as i64 + 300).clamp(900, 86400)) };
        let result = {
            let _p = self.health_gate.acquire().await;
            match &self.hsetup.demo {
                Some(b) => Ok(b.report(st.server.port)),
                None => health::run(&st.server.health_cmd.replace("{since}", &since), &st.server.ssh, 30.0).await,
            }
        };
        let notes = match result {
            Ok(text) => {
                let n = self.monitor.lock().ingest(&key, &st.server.ssh, &text, util::now());
                let mut hr = st.health.lock();
                hr.ok_at = util::now();
                hr.err.clear();
                n
            }
            Err(e) => {
                let e = health::scrub(&e);
                let changed = st.health.lock().err != e;
                if changed {
                    self.emit(json!({"kind": "event", "index": i, "ev": {"event": "uplink", "text": format!("HEALTH REPORT FAILED  {e}")}}));
                }
                st.health.lock().err = e;
                vec![]
            }
        };
        {
            let mut hr = st.health.lock();
            hr.checking = false;
            hr.check_at = self.mono() + HEALTH_EVERY;
        }
        for n in notes {
            self.emit(json!({"kind": "health-notice", "index": if n.key.is_empty() { Value::Null } else { json!(n.key.parse::<usize>().unwrap_or(0)) }, "level": n.level, "text": n.text, "quiet": n.quiet}));
        }
        self.emit(json!({"kind": "health", "view": self.health_view()}));
    }

    async fn health_service(self: Arc<Self>, host: String) {
        let unit = self.hsetup.service.clone();
        let result = {
            let _p = self.health_gate.acquire().await;
            match &self.hsetup.demo {
                Some(b) => Ok(b.service(&unit)),
                None => match health::service_command(&unit) {
                    Ok(cmd) => health::run(&cmd, &host, 30.0).await,
                    Err(e) => Err(e),
                },
            }
        };
        match result {
            Ok(text) => {
                self.monitor.lock().services.insert(host.clone(), health::parse_service(&text, util::now()));
                self.service_err.lock().remove(&host);
            }
            Err(e) => {
                self.service_err.lock().insert(host.clone(), health::scrub(&e));
            }
        }
        self.service_at.lock().insert(host, self.mono() + HEALTH_EVERY);
        self.emit(json!({"kind": "health", "view": self.health_view()}));
    }

    pub fn health_view(&self) -> Value {
        let m = self.monitor.lock();
        let errs: Vec<Value> = self.stations.iter().map(|s| json!(s.health.lock().err)).collect();
        json!({
            "on": (0..self.stations.len()).any(|i| self.watched(i)),
            "watched": (0..self.stations.len()).map(|i| self.watched(i)).collect::<Vec<_>>(),
            "service": self.hsetup.service,
            "min_free": m.min_free,
            "crashes": m.recent(health::LAST),
            "frames": m.frame_rates(util::now()),
            "hosts": m.hosts,
            "boxes": m.boxes,
            "services": m.services,
            "service_err": *self.service_err.lock(),
            "errors": errs,
            "seen": !m.seen.is_empty(),
            "short": m.short(),
        })
    }

    // --- forge --------------------------------------------------------------------------------------------------
    pub fn forge_connect(&self) {
        let mut s = self.fsetup.lock();
        let mut client = None;
        if s.key.is_set() {
            match ForgeClient::new(s.key.clone(), &s.url, s.cache_dir.as_deref()) {
                Ok(c) => client = Some(Arc::new(c)),
                Err(e) => s.error = e,
            }
        }
        *self.forge.lock() = client;
    }

    pub fn client(&self) -> Result<Arc<ForgeClient>, ForgeError> {
        self.forge.lock().clone().ok_or_else(|| ForgeError::new(401, "no key loaded", None))
    }

    pub fn forge_status(&self) -> Value {
        let s = self.fsetup.lock();
        let quota = self.forge.lock().as_ref().map(|f| f.quota.lock().words()).unwrap_or_default();
        json!({"key": self.forge.lock().is_some(), "source": s.source, "error": s.error, "quota": quota,
            "can_remember": s.config.as_ref().is_some_and(|c| c.is_file()),
            "config_name": s.config.as_ref().and_then(|c| c.file_name()).map(|n| n.to_string_lossy().to_string())})
    }

    pub fn forge_state_view(&self) -> Value {
        let fs = self.fstate.lock();
        json!({"servers": fs.data["servers"], "withdrawn": fs.data["withdrawn"], "latest": fs.data["latest"], "alarms": fs.alarms()})
    }

    fn forge_changed(&self) {
        self.emit(json!({"kind": "forge-state", "state": self.forge_state_view()}));
    }

    pub fn forge_ack(&self, lid: &str) {
        self.fstate.lock().acknowledge(lid);
        self.forge_changed();
    }

    /// The stations that load content from the same folder as this one: an install there is theirs too.
    fn sharing(&self, i: usize) -> Vec<usize> {
        let here = install::place(&self.stations[i].server);
        (0..self.stations.len())
            .filter(|&o| {
                let s = &self.stations[o].server;
                !s.content_dir.is_empty() && s.url.is_empty() && install::place(s) == here
            })
            .collect()
    }

    /// What installing a version on a station would do, for the confirm dialog; Err is (title, words).
    pub async fn forge_plan(&self, i: usize, x: Value, v: Value) -> Result<Value, (String, String)> {
        let forge = self.client().map_err(|e| (e.short, e.text))?;
        let st = &self.stations[i];
        let label = self.label(i);
        let (title, lid) = (forge::title_of(&x), forge::listing_id(&x));
        let target = install::target_for(&st.server).map_err(|e| (format!("{label} · CAN'T INSTALL HERE"), e))?;
        if forge::is_withdrawn(&x) {
            return Err((format!("FORGE · {title}"), "It's been withdrawn from ReclaimerForge, so it isn't installed anywhere new.".into()));
        }
        if forge::version_id(&v).is_empty() {
            return Err((format!("FORGE · {title}"), "It has no version to install yet.".into()));
        }
        if !forge::usable(&v) {
            return Err((format!("FORGE · {title}"), "Its author has withdrawn that version, so it isn't installed. Pick another.".into()));
        }
        let (vid, vlabel) = (forge::version_id(&v), forge::version_label(&v));
        let manifest = forge.manifest(&lid, &vid).await.map_err(|e| (format!("FORGE · {}", e.short), e.text))?;
        let files = install::plan(&manifest).map_err(|e| (format!("FORGE · {title}"), e))?;
        let there = target.existing(&files.iter().map(|f| f.path.clone()).collect::<Vec<_>>()).await.map_err(|e| (format!("FORGE · {title}"), e))?;
        let have = self.fstate.lock().installed(&st.server.where_()).get(&lid).cloned();
        let n = files.len();
        let mut lines = vec![format!(
            "{n} file{}, {}, into {} on {}.",
            if n == 1 { "" } else { "s" },
            install::size_words(files.iter().map(|f| f.size).sum()),
            st.server.content_dir,
            target.where_()
        )];
        if let Some(h) = &have {
            if h["version_id"] != vid {
                lines.push(format!("Replaces v{}, installed {}.", h["version"].as_str().unwrap_or("?"), h["installed_at"].as_str().unwrap_or("").chars().take(10).collect::<String>()));
            } else {
                lines.push("This version is installed already: it's copied again.".into());
            }
        }
        if !there.is_empty() {
            let mut t = there.clone();
            t.sort();
            lines.push(format!("Already there, and replaced: {}.", t.join(", ")));
        }
        let others: Vec<String> = self.sharing(i).into_iter().filter(|&o| o != i).map(|o| self.label(o)).collect();
        if !others.is_empty() {
            lines.push(format!("{} load{} from the same folder, so it's theirs too.", others.join(", "), if others.len() == 1 { "s" } else { "" }));
        }
        lines.push("Every file is checked against the manifest's size and SHA-256 before it's copied and again once it's there. Loading it is a separate step.".into());
        self.plans.lock().insert(format!("{i}|{lid}|{vid}"), (x, manifest, files));
        Ok(json!({"title": format!("INSTALL  {title} v{vlabel}"), "body": lines.join("\n\n"), "replacing": have.is_some() || !there.is_empty(), "plan": format!("{i}|{lid}|{vid}")}))
    }

    /// Download, check and copy a planned install; it reports as it goes, and what it did once done.
    pub fn forge_put(self: &Arc<Self>, plan: String) {
        let Some((x, manifest, files)) = self.plans.lock().remove(&plan) else { return };
        let i: usize = plan.split('|').next().unwrap_or("0").parse().unwrap_or(0);
        let vid = plan.rsplit('|').next().unwrap_or_default().to_string();
        let h = self.clone();
        self.spawn(async move {
            let Ok(forge) = h.client() else { return };
            let st = h.stations[i].clone();
            let label = h.label(i);
            let (title, lid) = (forge::title_of(&x), forge::listing_id(&x));
            let v = forge::versions_of(&x).into_iter().find(|v| forge::version_id(v) == vid).unwrap_or_else(|| json!({"id": vid}));
            let vlabel = forge::version_label(&v);
            let n = files.len();
            let s = if n == 1 { "" } else { "s" };
            h.emit(json!({"kind": "log", "station": label, "text": format!("» forge install {title} v{vlabel}  ({lid} {vid})"), "color": "white"}));
            h.emit(toast(&format!("FORGE · {title}"), &format!("Downloading {n} file{s} and checking each one…"), "information", 6.0));
            let result: Result<(), String> = async {
                let target = install::target_for(&st.server)?;
                let mut local = vec![];
                for f in &files {
                    local.push(forge.download(&f.url, f.size, &f.sha256).await.map_err(|u| u.0)?);
                }
                for (f, p) in files.iter().zip(&local) {
                    target.put(p, f).await?;
                }
                Ok(())
            }
            .await;
            if let Err(e) = result {
                h.emit(json!({"kind": "log", "text": format!("  ✕ {e}"), "color": "red", "explain": true}));
                h.emit(json!({"kind": "toast", "title": "FORGE · NOT INSTALLED", "text": e, "severity": "error", "timeout": 20, "explain": true}));
                return;
            }
            let kind = { let k = forge::kind_of(&x); if k.is_empty() { forge::kind_of(&manifest) } else { k } };
            let entry = json!({"listing_id": lid, "version_id": vid, "version": vlabel, "title": title,
                "kind": kind,
                "author": forge::author_of(&x), "owner_id": forge::owner_of(&x),
                "reference": pick_str(&manifest, &["reference", "map_reference", "name"]),
                "files": files.iter().map(|f| json!({"path": f.path, "sha256": f.sha256, "size": f.size})).collect::<Vec<_>>(),
                "content_dir": st.server.content_dir, "installed_at": util::utc_iso()});
            let sharing = h.sharing(i);
            let wheres: Vec<String> = sharing.iter().map(|&o| h.stations[o].server.where_()).collect();
            h.fstate.lock().record(&wheres, entry.clone());
            h.emit(json!({"kind": "log", "text": format!("  ✓ {n} file{s} checked and in place"), "color": "green"}));
            h.forge_changed();
            h.emit(json!({"kind": "installed", "index": i, "entry": entry, "sharing": sharing}));
        });
    }

    async fn forge_watch(self: Arc<Self>) {
        let Ok(forge) = self.client() else { return };
        if self.fstate.lock().ids().is_empty() {
            return; // an idle console spends nothing of the key's quota
        }
        {
            let mut w = self.watching.lock();
            if *w {
                return;
            }
            *w = true;
        }
        let ids = self.fstate.lock().ids();
        let since = { let s = self.fstate.lock().since(); if s.is_empty() { util::utc_iso() } else { s } };
        match forge.changes(&forge::before(&since, OVERLAP)).await {
            Err(e) => {
                if matches!(e.status, 401 | 403 | 502) && *self.watch_said.lock() != e.short {
                    *self.watch_said.lock() = e.short.clone();
                    self.emit(toast(&format!("FORGE · {}", e.short), &format!("Checking for updates: {}", e.text), "warning", 15.0));
                }
            }
            Ok((changes, as_of)) => {
                self.watch_said.lock().clear();
                let mut keys: Vec<String> = vec![];
                for c in changes {
                    let (lid, key) = (forge::change_listing(&c), forge::change_key(&c));
                    if lid.is_empty() || self.fstate.lock().seen(&key) || keys.contains(&key) {
                        continue;
                    }
                    keys.push(key);
                    if ids.contains(&lid) {
                        self.forge_news(&forge, &lid, &c).await;
                    }
                }
                self.fstate.lock().saw(keys, &as_of);
                let rec = self.fstate.lock().data["reconciled"].as_f64().unwrap_or(0.0);
                if util::now() - rec > RECONCILE {
                    self.forge_reconcile(&forge, &ids).await;
                }
            }
        }
        *self.watching.lock() = false;
    }

    async fn forge_news(&self, forge: &ForgeClient, lid: &str, c: &Value) {
        let change = pick_str(c, &["change", "type", "event", "kind"]).to_lowercase();
        let status = pick_str(c, &["status"]).to_lowercase();
        forge.forget(lid);
        self.emit(json!({"kind": "forge-refresh", "lid": lid}));
        if change.contains("withdraw") || forge::WITHDRAWN.contains(&status.as_str()) {
            self.forge_withdrawn(lid, "");
            return;
        }
        if change.contains("restor") || status == "published" || status == "live" {
            self.forge_restored(lid);
        }
        let installed = self.fstate.lock().versions(lid);
        let mut gone: Vec<String> = forge::id_list(c.get("withdrawn_version_ids")).into_iter().filter(|v| installed.contains(v)).collect();
        gone.sort();
        if let Some(g) = gone.first() {
            self.forge_withdrawn(lid, g);
        }
        let mut fresh = forge::id_list(c.get("published_version_ids"));
        let old = pick_str(c, &["version_id", "latest_version_id"]);
        if !old.is_empty() && !fresh.contains(&old) {
            fresh.push(old);
        }
        if fresh.iter().any(|v| !installed.contains(v)) {
            match forge.listing(lid, true).await {
                Ok(d) => self.forge_judge(lid, &d),
                Err(e) => {
                    if e.status != 404 {
                        self.forge_newer(lid, fresh.last().unwrap(), "");
                    }
                }
            }
        }
    }

    fn forge_judge(&self, lid: &str, d: &Value) {
        self.emit(json!({"kind": "forge-refresh", "lid": lid}));
        if forge::is_withdrawn(d) {
            self.forge_withdrawn(lid, "");
            return;
        }
        self.forge_restored(lid);
        let installed = self.fstate.lock().versions(lid);
        for v in forge::versions_of(d) {
            if forge::is_withdrawn(&v) && installed.contains(&forge::version_id(&v)) {
                self.forge_withdrawn(lid, &forge::version_id(&v));
                break;
            }
        }
        if let Some(latest) = forge::latest_of(d).filter(|l| !forge::version_id(l).is_empty()) {
            let dates: HashMap<String, Option<chrono::DateTime<chrono::Utc>>> =
                forge::versions_of(d).iter().map(|v| (forge::version_id(v), forge::when_of(v))).collect();
            if let Some(lw) = forge::when_of(&latest) {
                if !installed.is_empty() && installed.iter().all(|i| dates.get(i).copied().flatten().is_some_and(|d| d >= lw)) {
                    return; // nothing newer than what's installed
                }
            }
            self.forge_newer(lid, &forge::version_id(&latest), &forge::version_label(&latest));
        }
    }

    async fn forge_reconcile(&self, forge: &ForgeClient, ids: &std::collections::BTreeSet<String>) {
        for lid in ids {
            match forge.listing(lid, true).await {
                Ok(d) => self.forge_judge(lid, &d),
                Err(e) if matches!(e.status, 0 | 401 | 403 | 429 | 503) => return,
                Err(_) => continue,
            }
            tokio::time::sleep(Duration::from_millis(500)).await;
        }
        let mut fs = self.fstate.lock();
        fs.data["reconciled"] = json!(util::now());
        fs.save();
    }

    fn names_at(&self, servers: &[String]) -> String {
        let n: Vec<String> = (0..self.stations.len()).filter(|&i| servers.contains(&self.stations[i].server.where_())).map(|i| self.label(i)).collect();
        if n.is_empty() { "your servers".into() } else { n.join(", ") }
    }

    fn forge_newer(&self, lid: &str, vid: &str, label: &str) {
        let behind = self.fstate.lock().newer(lid, vid, label);
        if !behind.is_empty() {
            let e = self.fstate.lock().entry(lid).unwrap_or_default();
            let names = self.names_at(&behind);
            let title = e["title"].as_str().filter(|t| !t.is_empty()).unwrap_or(lid).to_string();
            let what = if label.is_empty() { "has a new version".to_string() } else { format!("v{label}") };
            self.emit(toast("FORGE · UPDATE", &format!("{title} {what} is out on ReclaimerForge. {names} run{} v{}. Install it from F6 (▲).",
                if behind.len() == 1 { "s" } else { "" }, e["version"].as_str().unwrap_or("?")), "information", 20.0));
            self.emit(json!({"kind": "event", "index": null, "ev": {"event": "forge", "text": format!("UPDATE  {title} {}", if label.is_empty() { String::new() } else { format!("v{label}") }).trim()}}));
        }
        self.forge_changed();
    }

    fn forge_withdrawn(&self, lid: &str, vid: &str) {
        let news = self.fstate.lock().withdraw(lid, vid);
        if news {
            let fs = self.fstate.lock();
            let e = fs.entry(lid).unwrap_or_default();
            let title = e["title"].as_str().filter(|t| !t.is_empty()).unwrap_or(lid).to_string();
            let names = { let w = fs.where_(lid, vid); drop(fs); self.names_at(&w) };
            if !vid.is_empty() {
                let label = {
                    let fs = self.fstate.lock();
                    fs.data["servers"].as_object().into_iter().flat_map(|m| m.values()).filter_map(Value::as_object).flat_map(|es| es.values())
                        .find(|x| x["version_id"] == vid).and_then(|x| x["version"].as_str().map(str::to_string)).unwrap_or("?".into())
                };
                self.emit(toast("FORGE · VERSION WITHDRAWN", &format!("{title} v{label} was withdrawn from ReclaimerForge by its author. It's still installed on {names}, and flagged ⚠ in the rotation on F3. Install another version from F6, or acknowledge it there (a)."), "warning", 30.0));
                self.emit(json!({"kind": "event", "index": null, "ev": {"event": "forge", "text": format!("WITHDRAWN  {title} v{label}  on {names}")}}));
            } else {
                self.emit(toast("FORGE · WITHDRAWN", &format!("{title} was withdrawn from ReclaimerForge. It's still installed on {names}, and flagged ⚠ in the rotation on F3. Once you've dealt with it, acknowledge it on F6 (a)."), "warning", 30.0));
                self.emit(json!({"kind": "event", "index": null, "ev": {"event": "forge", "text": format!("WITHDRAWN  {title}  on {names}")}}));
            }
        }
        self.forge_changed();
    }

    fn forge_restored(&self, lid: &str) {
        let was = self.fstate.lock().is_withdrawn(lid);
        if was {
            self.fstate.lock().restore(lid);
            self.forge_changed();
        }
    }
}

/// (the Forge key, where it came from, why there's none): forge_api_key (only if the operator chose to remember it)
/// > forge_api_key_env > forge_api_key_command > $ONI_RCON_FORGE_KEY. A missing key never stops the console.
pub async fn load_key(settings: &toml::map::Map<String, toml::Value>) -> (Secret, String, String) {
    if let Some(k) = settings.get("forge_api_key").and_then(|v| v.as_str()).filter(|k| !k.is_empty()) {
        return (Secret::new(k), "the config file".into(), String::new());
    }
    let name = settings.get("forge_api_key_env").and_then(|v| v.as_str()).unwrap_or_default().to_string();
    if !name.is_empty() {
        if let Ok(v) = std::env::var(&name) {
            if !v.trim().is_empty() {
                return (Secret::new(&v), format!("${name}"), String::new());
            }
        }
    }
    if let Ok(cmd) = crate::config::Cmd::from_toml(settings.get("forge_api_key_command")) {
        if cmd.is_set() {
            return match crate::config::run_command(&cmd, "forge_api_key_command").await {
                Ok(out) if !out.trim().is_empty() => (Secret::new(&out), "forge_api_key_command".into(), String::new()),
                Ok(_) => (Secret::default(), "forge_api_key_command".into(), "forge_api_key_command printed nothing.".into()),
                Err(e) => (Secret::default(), "forge_api_key_command".into(), util::scrub(&e)),
            };
        }
    }
    if let Ok(v) = std::env::var("ONI_RCON_FORGE_KEY") {
        if !v.trim().is_empty() {
            return (Secret::new(&v), "$ONI_RCON_FORGE_KEY".into(), String::new());
        }
    }
    (Secret::default(), String::new(), if name.is_empty() { String::new() } else { format!("${name} is empty or not set.") })
}
