//! ONI RCON: an Office of Naval Intelligence styled admin console for Project Reclaimer Halo 3 dedicated servers.
//! The backend (this crate) holds every connection, command, credential and file; the window draws what it's told.
mod automation;
mod config;
mod demo;
mod fakeforge;
mod forge;
mod fstate;
mod health;
mod host;
mod hub;
mod install;
mod plugins;
mod rcon;
mod session;
mod stats;
mod studio;
mod util;

use config::Server;
use hub::{ForgeSetup, HealthSetup, Hub};
use parking_lot::Mutex;
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use tauri::{AppHandle, Emitter, Manager, State};
use util::Secret;

const DEMO_HINT: &str = "Three pretend servers to look around, and a pretend ReclaimerForge on F6. Click a player, try the buttons, and press ? for a guide. When you're ready, + ADD SERVER (left) connects your own.";
const NEW_HINT: &str = "You're in. Click a player for what you can do, or press ? for a guide.";

/// oni-rcon [targets...] [-c CONFIG] [--ssh DEST] [--by NAME] [--demo] [--setup] [--intro full|quick|off] [--no-intro]
#[derive(Default, Clone, Debug)]
struct Args {
    targets: Vec<String>,
    config: Option<PathBuf>,
    ssh: String,
    by: Option<String>,
    demo: bool,
    setup: bool,
    intro: Option<String>,
    no_intro: bool,
    devtools: bool,
}

fn parse_args() -> Result<Args, String> {
    let mut a = Args::default();
    let mut it = std::env::args().skip(1);
    while let Some(x) = it.next() {
        let mut val = |name: &str| it.next().ok_or_else(|| format!("{name} needs a value"));
        match x.as_str() {
            "-c" | "--config" => a.config = Some(val("--config")?.into()),
            "--ssh" => a.ssh = val("--ssh")?,
            "--by" => a.by = Some(val("--by")?),
            "--demo" => a.demo = true,
            "--setup" => a.setup = true,
            "--no-intro" => a.no_intro = true,
            "--devtools" => a.devtools = true,
            "--intro" => {
                let v = val("--intro")?;
                if !["full", "quick", "off"].contains(&v.as_str()) {
                    return Err("--intro is full, quick or off".into());
                }
                a.intro = Some(v);
            }
            "-V" | "--version" => {
                println!("oni-rcon {}", env!("CARGO_PKG_VERSION"));
                std::process::exit(0);
            }
            "-h" | "--help" => {
                println!("oni-rcon [targets...] [-c CONFIG] [--ssh DEST] [--by NAME] [--demo] [--setup] [--intro full|quick|off] [--no-intro] [--devtools]");
                std::process::exit(0);
            }
            s if s.starts_with('-') && !s.starts_with("--psn") => return Err(format!("unknown option {s}")),
            s => a.targets.push(s.into()),
        }
    }
    Ok(a)
}

fn login() -> String {
    let u = whoami::username();
    if u.is_empty() {
        "operator".into()
    } else {
        u
    }
}

#[derive(Default)]
struct Prefs {
    intro_seen: bool,
    full_intro: bool,
}

fn prefs_path() -> PathBuf {
    config::user_config().with_file_name("prefs.json")
}

/// prefs.json as an object; a missing or unreadable file reads as empty.
fn prefs_raw() -> Value {
    std::fs::read_to_string(prefs_path()).ok().and_then(|t| serde_json::from_str(&t).ok()).filter(Value::is_object).unwrap_or_else(|| json!({}))
}

fn prefs_load() -> Prefs {
    let v = prefs_raw();
    Prefs { intro_seen: v["intro_seen"] == true, full_intro: v["full_intro"] == true }
}

/// Read, change, write: keys this version doesn't know (plug-in approvals, a newer one's) are kept.
fn prefs_save(p: &Prefs) {
    let mut v = prefs_raw();
    (v["intro_seen"], v["full_intro"]) = (json!(p.intro_seen), json!(p.full_intro));
    let _ = util::write_atomic(&prefs_path(), &format!("{v}\n"), false);
}

struct App {
    args: Args,
    arg_error: String,
    hub: Mutex<Option<Arc<Hub>>>,
    typed: Mutex<HashMap<String, String>>, // passwords given to the setup screen but not saved: good for this session
    by: Mutex<String>,
    forced_setup: Mutex<bool>,
    demo: Mutex<bool>,
    hint: Mutex<String>,
    picked: Mutex<Vec<PathBuf>>, // files the operator chose to publish; the window never names a path itself
    plugins: Mutex<Vec<plugins::Manifest>>, // the ones the config turns on, read at start; none runs unapproved
    index: Mutex<Vec<plugins::Entry>>, // the plug-in index as last read: what plugin_index_install may install
}

fn config_path(a: &Args) -> Option<PathBuf> {
    a.config.clone().or_else(config::default_config)
}

/// Whether to open on the setup screen or the console, and what the setup screen starts with.
#[tauri::command]
fn launch_info(app: State<'_, App>) -> Value {
    let a = &app.args;
    let path = config_path(a);
    let setup = *app.forced_setup.lock() || a.setup || !(*app.demo.lock() || !a.targets.is_empty() || path.is_some());
    let (mut by, mut existing, mut error) = (String::new(), vec![], app.arg_error.clone());
    if let Some(p) = &path {
        match config::load_config(p) {
            Ok((b, s)) => {
                by = b;
                existing = s.iter().map(|s| json!({"where": s.where_(), "name": s.name})).collect();
            }
            Err(e) if setup => error = e,
            Err(_) => {}
        }
    }
    let by = a.by.clone().filter(|b| !b.is_empty()).or_else(|| (!by.is_empty()).then_some(by)).unwrap_or_else(login);
    json!({"mode": if setup { "setup" } else { "console" }, "version": env!("CARGO_PKG_VERSION"),
        "setup": {"path": path.clone().unwrap_or_else(config::user_config).display().to_string(), "existing": existing, "by": by,
            "ask_by": path.is_none() && a.by.is_none()}, "error": error})
}

#[derive(Deserialize)]
struct Spec {
    address: String,
    port: String,
    password: String,
    name: String,
    ssh: String,
}

/// What parse_target reads, from the setup screen's two boxes: an address may carry its own port, or be a URL.
fn spec_server(s: &Spec) -> Result<Server, String> {
    let (address, port) = (s.address.trim(), s.port.trim());
    let target = if address.contains("://") || port.is_empty() {
        address.to_string()
    } else if address.matches(':').count() > 1 && !address.starts_with('[') {
        format!("[{address}]:{port}")
    } else if address.rsplit(']').next().unwrap_or_default().contains(':') {
        address.to_string()
    } else {
        format!("{}:{port}", if address.is_empty() { "127.0.0.1" } else { address })
    };
    let mut srv = config::parse_target(&target, s.ssh.trim(), s.name.trim())
        .map_err(|_| "That address doesn't look right. Use an IP or host name, and the port number.".to_string())?;
    srv.password = s.password.clone();
    Ok(srv)
}

#[tauri::command]
async fn setup_probe(spec: Spec) -> Value {
    match spec_server(&spec) {
        Ok(s) => probe(s).await,
        Err(e) => json!({"ok": false, "text": e, "state": "invalid"}),
    }
}

/// Sign in once and hang up: (worked, what to tell the operator).
async fn probe(s: Server) -> Value {
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
    let mut tasks = vec![];
    let (url, ready) = if !s.ssh.is_empty() && s.url.is_empty() {
        let t = Arc::new(rcon::Tunnel::new(&s.ssh, &[(s.host.clone(), s.port)]));
        let (ttx, mut trx) = tokio::sync::mpsc::unbounded_channel();
        tasks.push(tokio::spawn(t.clone().run(ttx)));
        let tx2 = tx.clone();
        tasks.push(tokio::spawn(async move {
            while let Some((_, state, detail)) = trx.recv().await {
                if state == "down" {
                    let _ = tx2.send(rcon::LinkNote::State { state: "tunnel", detail, retry_in: 0, info: Value::Null });
                }
            }
        }));
        (format!("ws://127.0.0.1:{}", t.local_port(&s.host, s.port)), Some(t.ready.subscribe()))
    } else {
        (s.direct_url(), None)
    };
    let rc = rcon::Rcon::new(&s.password, "setup");
    tasks.push(tokio::spawn(rc.run(url, ready, Arc::new(tokio::sync::Semaphore::new(1)), tx)));
    let wait = async {
        while let Some(n) = rx.recv().await {
            if let rcon::LinkNote::State { state, detail, info, .. } = n {
                match state {
                    "online" => {
                        let name = util::pick_str(&info, &["server"]);
                        return json!({"ok": true, "text": format!("Connected to {}.", if name.is_empty() { s.where_() } else { name })});
                    }
                    "denied" | "offline" | "tunnel" => return json!({"ok": false, "detail": detail, "state": if state == "tunnel" { "offline" } else { state }}),
                    _ => {}
                }
            }
        }
        json!({"ok": false, "text": "No answer."})
    };
    let out = tokio::time::timeout(std::time::Duration::from_secs(12), wait)
        .await
        .unwrap_or_else(|_| json!({"ok": false, "text": "No answer in time. Check the address and port, and any firewall in between."}));
    for t in tasks {
        t.abort();
    }
    out
}

#[tauri::command]
fn setup_check(spec: Spec, app: State<'_, App>) -> Value {
    match spec_server(&spec) {
        Ok(s) => {
            let path = config_path(&app.args);
            let dup = path.and_then(|p| config::load_config(&p).ok()).is_some_and(|(_, ex)| ex.iter().any(|x| x.where_() == s.where_()));
            json!({"ok": !dup, "where": s.where_(), "text": if dup { format!("{} is already in your list.", s.where_()) } else { String::new() }})
        }
        Err(e) => json!({"ok": false, "text": e}),
    }
}

/// Add the servers to the config file; a password not kept there is held for this session.
#[tauri::command]
fn setup_save(specs: Vec<Spec>, by: String, remember: bool, app: State<'_, App>) -> Result<(), String> {
    let servers: Vec<Server> = specs.iter().map(spec_server).collect::<Result<_, _>>()?;
    let path = config_path(&app.args).unwrap_or_else(config::user_config);
    if !servers.is_empty() {
        config::add_servers(&path, &servers, by.trim(), remember).map_err(|e| format!("Couldn't save {}: {e}", path.display()))?;
    }
    let mut typed = app.typed.lock();
    for s in &servers {
        typed.insert(s.where_(), s.password.clone());
    }
    if !by.trim().is_empty() {
        *app.by.lock() = by.trim().into();
    }
    *app.forced_setup.lock() = false;
    *app.hint.lock() = NEW_HINT.into();
    *app.demo.lock() = false;
    Ok(())
}

/// Servers set up here or found on a game box go straight into the config; each line of progress is a setup-line event.
fn setup_found(found: Result<host::Hosted, String>, by: &str, remember: bool, app: &State<'_, App>) -> Result<Value, String> {
    let (servers, notes, templates) = found?;
    let path = config_path(&app.args).unwrap_or_else(config::user_config);
    let added = host::save_found(&path, servers.clone(), &templates, by.trim(), remember)?;
    let mut typed = app.typed.lock();
    for s in &servers {
        typed.insert(s.where_(), s.password.clone()); // a password not remembered is good for this session
    }
    Ok(json!({"found": servers.iter().map(|s| json!({"where": s.where_(), "name": s.name})).collect::<Vec<_>>(), "added": added.len(), "notes": notes}))
}

/// HOST ONE HERE: Project Reclaimer's dedicated server set up from the game in Steam (Windows) or Docker (Linux), started.
#[tauri::command]
async fn setup_host(by: String, remember: bool, settings: Option<host::Settings>, handle: AppHandle, app: State<'_, App>) -> Result<Value, String> {
    let say = move |l: &str| {
        let _ = handle.emit("setup-line", l);
    };
    let found = host::host_here(settings.as_ref(), &say).await.map(|(s, notes)| (s, notes, vec![]));
    setup_found(found, &by, remember, &app)
}

/// Where HOST ONE HERE would put the server, whether one is there, and what's missing: for the setup screen.
#[tauri::command]
fn host_status() -> Value {
    host::status()
}

/// FIND SERVERS ON IT: the Reclaimer containers and server folders on a game box over SSH, each tested.
#[tauri::command]
async fn setup_find(dest: String, by: String, handle: AppHandle, app: State<'_, App>) -> Result<Value, String> {
    let say = move |l: &str| {
        let _ = handle.emit("setup-line", l);
    };
    let found = host::connect_box(dest.trim(), &say).await;
    setup_found(found, &by, true, &app)
}

#[tauri::command]
fn setup_open(app: State<'_, App>) {
    if let Some(h) = app.hub.lock().take() {
        h.stop();
    }
    session::stop_all();
    *app.forced_setup.lock() = true;
}

/// REMOVE SERVER: taken out of the config file (the server itself is untouched); the console is rebuilt after, or the
/// setup screen opens when none are left.
#[tauri::command]
fn remove_server(index: usize, app: State<'_, App>) -> Result<(), String> {
    if *app.demo.lock() || !app.args.targets.is_empty() {
        return Err("These servers aren't from a config file: there's nothing to remove them from.".into());
    }
    let path = config_path(&app.args).ok_or("No config file.")?;
    if config::remove_server(&path, index)? == 0 {
        setup_open(app);
    }
    Ok(())
}

/// Build the console: the demo, the command line's targets, or the config file's servers.
#[tauri::command]
async fn start_console(demo: bool, handle: AppHandle, app: State<'_, App>) -> Result<Value, String> {
    if let Some(h) = app.hub.lock().take() {
        h.stop();
    }
    session::stop_all();
    let a = app.args.clone();
    let demo = demo || *app.demo.lock();
    *app.forced_setup.lock() = false;
    let mut hint = std::mem::take(&mut *app.hint.lock());
    let path = config_path(&a);
    let mut cfg_by = String::new();
    let mut extra = vec![];
    let (mut servers, hsetup, fsetup, state_dir) = if demo {
        *app.demo.lock() = true;
        let scratch = tempfile::Builder::new().prefix("oni-rcon-demo-").tempdir().map_err(|e| e.to_string())?.keep();
        studio::demo(scratch.join("studio"));
        let folders: Vec<PathBuf> = (1..=3).map(|i| scratch.join(format!("content-{i}"))).collect();
        let (ports, host) = demo::serve_fakes("demo", &folders, &mut extra).await;
        let servers: Vec<Server> = ports
            .iter()
            .zip(&folders)
            .map(|(p, d)| {
                let mut s = Server::new("127.0.0.1", *p);
                s.password = "demo".into();
                s.content_dir = d.display().to_string();
                s
            })
            .collect();
        let (url, key) = fakeforge::start(&mut extra).await;
        if hint.is_empty() {
            hint = DEMO_HINT.into();
        }
        (
            servers,
            HealthSetup { min_free: 1024, service: "blueflame-demo".into(), demo: Some(host) },
            ForgeSetup { key: Secret::new(&key), source: "the demo".into(), error: String::new(), url, poll: 30.0, cache_dir: Some(scratch.clone()), config: None },
            Some(scratch),
        )
    } else {
        let servers = if !a.targets.is_empty() {
            a.targets.iter().map(|t| config::parse_target(t, &a.ssh, "")).collect::<Result<Vec<_>, _>>()?
        } else {
            let p = path.clone().ok_or("No config file and no servers: open the setup screen to add one.")?;
            let (b, mut s) = config::load_config(&p)?;
            cfg_by = b;
            let typed = app.typed.lock();
            for srv in s.iter_mut() {
                if srv.password.is_empty() {
                    srv.password = typed.get(&srv.where_()).cloned().unwrap_or_default();
                }
            }
            s
        };
        let hs = config::top_level(path.as_deref(), "health_");
        let min_free = match hs.get("health_min_free_mb") {
            None => 1024,
            Some(toml::Value::Integer(n)) if *n > 0 => *n,
            Some(toml::Value::Float(f)) if *f > 0.0 => *f as i64,
            Some(_) => return Err(format!("{}: health_min_free_mb must be a number of megabytes above 0", path.as_ref().unwrap().display())),
        };
        let service = hs.get("health_service").and_then(|v| v.as_str()).unwrap_or_default().to_string();
        if !service.is_empty() && !health::UNIT.is_match(&service) {
            return Err(format!("{}: health_service must be a systemd unit name, such as halo-blueflame", path.as_ref().unwrap().display()));
        }
        let fs = config::top_level(path.as_deref(), "forge_");
        let (key, source, error) = hub::load_key(&fs).await;
        let poll = fs.get("forge_poll").and_then(|v| v.as_float().or(v.as_integer().map(|i| i as f64))).unwrap_or(600.0).max(60.0);
        (
            servers,
            HealthSetup { min_free, service, demo: None },
            ForgeSetup { key, source, error, url: forge::FORGE_URL.into(), poll, cache_dir: Some(config::cache_root()), config: path.clone() },
            Some(config::user_config().parent().unwrap().to_path_buf()),
        )
    };
    *app.plugins.lock() = if demo { vec![plugins::demo_manifest(), studio::manifest()] } else { load_plugins(path.as_deref())? };
    if !demo {
        studio::use_dir(path.as_deref().and_then(Path::parent).map(|d| d.join("studio")));
    }
    let waiting = config::resolve_passwords(&mut servers).await?;
    let by = {
        let typed_by = app.by.lock().clone();
        a.by.clone().filter(|b| !b.is_empty()).or((!typed_by.is_empty()).then_some(typed_by)).or((!cfg_by.is_empty()).then_some(cfg_by)).unwrap_or_else(login)
    };
    let prefs = prefs_load();
    let intro = if a.no_intro {
        "off".to_string()
    } else {
        a.intro.clone().unwrap_or_else(|| if prefs.full_intro || !prefs.intro_seen { "full".into() } else { "quick".into() })
    };
    let stations: Vec<Value> = servers
        .iter()
        .enumerate()
        .map(|(i, s)| json!({"index": i, "name": s.name, "where": s.where_(), "ssh": if s.url.is_empty() { s.ssh.clone() } else { String::new() },
            "url": s.url, "content_dir": s.content_dir, "host": s.host, "port": s.port, "restart": demo || !s.restart_cmd.is_empty()}))
        .collect();
    let hub = Hub::start(handle, servers, by.clone(), hsetup, fsetup, state_dir, &waiting, extra);
    let info = json!({"by": by, "stations": stations, "tunnels": hub.tunnels.iter().map(|t| t.dest.clone()).collect::<Vec<_>>(),
        "intro": intro, "hint": hint, "waiting": waiting, "demo": demo, "full_intro": prefs.full_intro,
        "forge": hub.forge_status(), "forge_state": hub.forge_state_view(), "health": hub.health_view(),
        "version": env!("CARGO_PKG_VERSION")});
    *app.hub.lock() = Some(hub);
    Ok(info)
}

fn hub_of(app: &State<'_, App>) -> Result<Arc<Hub>, String> {
    app.hub.lock().clone().ok_or_else(|| "the console isn't running".to_string())
}

#[tauri::command]
async fn call(index: usize, command: String, args: Vec<String>, timeout: Option<f64>, app: State<'_, App>) -> Result<Value, String> {
    let h = hub_of(&app)?;
    Ok(h.call(index, &command, args, timeout.unwrap_or(10.0)).await)
}

#[tauri::command]
async fn reconnect(index: usize, password: Option<String>, app: State<'_, App>) -> Result<(), String> {
    let h = hub_of(&app)?;
    if let Some(pw) = password {
        *h.stations[index].rcon.password.lock() = pw;
    }
    h.connect(index);
    Ok(())
}

/// The one password typed for every server that had none of its own, as the terminal console prompted for.
#[tauri::command]
async fn set_password(password: String, indices: Vec<usize>, app: State<'_, App>) -> Result<(), String> {
    let h = hub_of(&app)?;
    for i in indices {
        *h.stations[i].rcon.password.lock() = password.clone();
        h.connect(i);
    }
    Ok(())
}

/// RESTART SERVER: the server's restart_cmd, run where the server is (the demo restarts its own).
#[tauri::command]
async fn restart_server(index: usize, app: State<'_, App>) -> Result<(), String> {
    let h = hub_of(&app)?;
    if let Some(d) = h.hsetup.demo.clone() {
        d.restart(index).await;
        return Ok(());
    }
    let s = &h.stations.get(index).ok_or("no such server")?.server;
    if s.restart_cmd.is_empty() {
        return Err("no restart_cmd for this server in the config".into());
    }
    let out = health::run(&s.restart_cmd, &s.ssh, 180.0).await?;
    match out.trim().strip_prefix("NOT RESTARTED") {
        Some(_) => Err(out.trim().to_string()), // the servers are still up on their old settings
        None => Ok(()),
    }
}

/// The playlists in the server folder's playlists/, as dedicated.toml names them.
#[tauri::command]
async fn playlists(index: usize, app: State<'_, App>) -> Result<Vec<String>, String> {
    let h = hub_of(&app)?;
    let t = install::target_for(&h.stations.get(index).ok_or("no such server")?.server)?;
    let mut out: Vec<String> = t.list("../playlists").await?.into_iter().filter(|n| n.ends_with(".json")).map(|n| format!("playlists/{n}")).collect();
    out.sort();
    Ok(out)
}

/// What the server folder's playlists/ holds: each file's name and games, and whether this console made it.
#[tauri::command]
async fn playlist_library(index: usize, app: State<'_, App>) -> Result<Vec<Value>, String> {
    let h = hub_of(&app)?;
    let t = install::target_for(&h.stations.get(index).ok_or("no such server")?.server)?;
    let mut names: Vec<String> = t.list("../playlists").await?.into_iter().filter(|n| n.ends_with(".json")).collect();
    names.sort();
    let mut out = vec![];
    // ponytail: one read a file (one ssh each on a remote box); the first 60 are shown
    for n in names.into_iter().take(60) {
        let path = format!("playlists/{n}");
        let mut row = json!({"path": path, "here": n.ends_with(".playlist.json")});
        match t.read_text(&format!("../{path}")).await.and_then(|s| serde_json::from_str::<Value>(&s).map_err(|e| e.to_string())) {
            Ok(v) => {
                row["name"] = v["name"].clone();
                row["games"] = json!(v["games"].as_array().map_or(0, Vec::len));
            }
            Err(e) => row["problem"] = e.into(),
        }
        out.push(row);
    }
    Ok(out)
}

/// A playlist file as it is, to edit a copy of: one in playlists/, or a Forge one in the content folder ("content/...").
#[tauri::command]
async fn playlist_read(index: usize, path: String, app: State<'_, App>) -> Result<Value, String> {
    let h = hub_of(&app)?;
    let t = install::target_for(&h.stations.get(index).ok_or("no such server")?.server)?;
    let p = install::safe_path(&path)?;
    let rel = match (p.strip_prefix("content/"), p.starts_with("playlists/")) {
        (Some(inner), _) => inner.to_string(),
        (None, true) => format!("../{p}"),
        _ => return Err(format!("{path} isn't a playlist file.")),
    };
    serde_json::from_str(&t.read_text(&rel).await?).map_err(|e| format!("{path} doesn't read as a playlist: {e}"))
}

/// SAVE in the playlist editor: written into the server folder's playlists/ as a new file named for it, or over `path`
/// (a playlist this console made); an existing file is only replaced when asked.
#[tauri::command]
async fn save_playlist(index: usize, playlist: Value, path: Option<String>, replace: bool, app: State<'_, App>) -> Result<Value, String> {
    let h = hub_of(&app)?;
    let t = install::target_for(&h.stations.get(index).ok_or("no such server")?.server)?;
    let (made, json) = host::playlist_file(&playlist)?;
    let (path, replace) = match path {
        Some(p) => (host::console_playlist(&p)?, true),
        None => (made, replace),
    };
    let rel = format!("../{path}");
    if !replace && !t.existing(std::slice::from_ref(&rel)).await?.is_empty() {
        return Ok(json!({"path": path, "exists": true}));
    }
    t.write_text(&rel, &json).await?;
    Ok(json!({"path": path, "exists": false, "flood": flood_missing(&t, &playlist).await}))
}

/// Why a playlist's Flood Infection games won't load, or "": the server's game folder (beside its content folder, as
/// init lays it out) has Halo 3 but not the Flood's map. A game folder that isn't there (Docker's GAME_DIR) isn't judged.
async fn flood_missing(t: &install::Target, playlist: &Value) -> String {
    let games = playlist["games"].as_array().map_or(&[][..], Vec::as_slice);
    let flood: Vec<&str> = games.iter().filter_map(|g| g["game"].as_str()).filter(|g| host::plays_flood(g)).collect();
    if flood.is_empty() {
        return String::new();
    }
    let (dll, map) = ("../game/halo3/halo3.dll".to_string(), format!("../game/{}", host::FLOOD_MAP));
    match t.existing(&[dll.clone(), map.clone()]).await {
        Ok(have) if have.contains(&dll) && !have.contains(&map) => format!(
            "{} needs 050_floodvoi.map, and the server's game folder doesn't have it: copy it from MCC's halo3\\maps into game/halo3/maps beside the content folder, or a vote for it passes and the game fails to load.",
            flood[0]
        ),
        _ => String::new(),
    }
}

/// NEW GAME TYPE: written into the server's content folder, Game Modes/; an existing one is only replaced when asked.
#[tauri::command]
async fn save_game_type(index: usize, name: String, mode: String, score_to_win: Option<i64>, options: serde_json::Map<String, Value>, replace: bool, app: State<'_, App>) -> Result<Value, String> {
    let h = hub_of(&app)?;
    let t = install::target_for(&h.stations.get(index).ok_or("no such server")?.server)?;
    let (path, json) = host::new_game_type(&name, &mode, score_to_win, &options)?;
    if !replace && !t.existing(std::slice::from_ref(&path)).await?.is_empty() {
        return Ok(json!({"path": path, "exists": true}));
    }
    t.write_text(&path, &json).await?;
    Ok(json!({"path": path, "exists": false}))
}

/// A setting in the server's own dedicated.toml, the one beside its content_dir: server 0.9.18 or later reloads it
/// when told (the console sends `reload`), an older one takes it up at the next start.
#[tauri::command]
async fn server_setting(index: usize, key: String, value: Value, app: State<'_, App>) -> Result<(), String> {
    let h = hub_of(&app)?;
    let v: toml_edit::Value = match (key.as_str(), value.as_i64()) {
        ("max_players", Some(n)) if (1..=63).contains(&n) => n.into(),
        ("max_players", _) => return Err("A server holds 1 to 63 players.".into()),
        ("camera", _) => value.as_str().filter(|c| host::CAMERAS.contains(c)).ok_or_else(|| format!("{value} isn't a camera setting"))?.into(),
        ("playlist", _) => install::safe_path(value.as_str().unwrap_or_default()).map_err(|_| format!("Not a playlist in the server folder: {value}"))?.into(),
        ("admin_add" | "admin_remove", _) => value.as_str().ok_or("That's not a player ID.")?.into(),
        _ => return Err(format!("{key} can't be set from here")),
    };
    if let Some(d) = h.hsetup.demo.clone() {
        d.set(index, &key, value);
        return Ok(());
    }
    let s = &h.stations.get(index).ok_or("no such server")?.server;
    // ponytail: found beside content_dir, as every setup lays it out; a dedicated_toml key when a server doesn't
    let t = install::target_for(s)?;
    let text = t.read_text("../dedicated.toml").await.map_err(|e| format!("No dedicated.toml beside its content_dir: {e}"))?;
    let text = match key.as_str() {
        "admin_add" | "admin_remove" => host::set_server_admin(&text, s.port, v.as_str().unwrap_or_default(), key == "admin_add")?,
        _ => host::set_server_key(&text, s.port, &key, v)?,
    };
    t.write_text("../dedicated.toml", &text).await
}

#[tauri::command]
fn set_labels(labels: Vec<String>, app: State<'_, App>) -> Result<(), String> {
    *hub_of(&app)?.labels.lock() = labels;
    Ok(())
}

#[tauri::command]
fn health_now(app: State<'_, App>) -> Result<(), String> {
    hub_of(&app)?.health_now();
    Ok(())
}

#[tauri::command]
fn health_view(app: State<'_, App>) -> Result<Value, String> {
    Ok(hub_of(&app)?.health_view())
}

fn forge_err(e: forge::ForgeError) -> Value {
    json!({"error": {"status": e.status, "short": e.short, "text": e.text}})
}

#[tauri::command]
async fn forge_listings(sort: String, window: String, q: String, more: Option<Value>, fresh: bool, app: State<'_, App>) -> Result<Value, String> {
    let h = hub_of(&app)?;
    let f = match h.client() {
        Ok(f) => f,
        Err(e) => return Ok(forge_err(e)),
    };
    Ok(match f.listings(&sort, &window, &q, more.as_ref(), fresh).await {
        Ok((items, next, reply)) => json!({"items": items, "next": next, "stale": reply["_stale"], "status": h.forge_status()}),
        Err(e) => forge_err(e),
    })
}

#[tauri::command]
async fn forge_favourites(fresh: bool, app: State<'_, App>) -> Result<Value, String> {
    let h = hub_of(&app)?;
    let f = match h.client() {
        Ok(f) => f,
        Err(e) => return Ok(forge_err(e)),
    };
    Ok(match f.favourites(fresh).await {
        Ok((cols, reply)) => {
            let (items, notes) = forge::favourite_listings(&cols);
            json!({"items": items, "notes": notes, "stale": reply["_stale"], "status": h.forge_status()})
        }
        Err(e) => forge_err(e),
    })
}

#[tauri::command]
async fn forge_listing(lid: String, mine: Option<bool>, app: State<'_, App>) -> Result<Value, String> {
    let h = hub_of(&app)?;
    let f = match h.client() {
        Ok(f) => f,
        Err(e) => return Ok(forge_err(e)),
    };
    let got = if mine == Some(true) { f.my_listing(&lid).await } else { f.listing(&lid, false).await };
    Ok(match got {
        Ok(d) => json!({"listing": d}),
        Err(e) => forge_err(e),
    })
}

#[tauri::command]
async fn forge_mine(app: State<'_, App>) -> Result<Value, String> {
    let h = hub_of(&app)?;
    let f = match h.client() {
        Ok(f) => f,
        Err(e) => return Ok(forge_err(e)),
    };
    Ok(match f.mine().await {
        Ok(items) => json!({"items": items, "status": h.forge_status()}),
        Err(e) => forge_err(e),
    })
}

/// HOST A SERVER's playlist: a .json picked in a dialog of the backend's own; its path, or null if none was picked.
#[tauri::command]
async fn pick_playlist(handle: AppHandle) -> Result<Option<String>, String> {
    use tauri_plugin_dialog::DialogExt;
    let picked = tauri::async_runtime::spawn_blocking(move || handle.dialog().file().set_title("Pick a playlist").add_filter("Playlist", &["json"]).blocking_pick_file())
        .await
        .map_err(|e| e.to_string())?;
    Ok(picked.and_then(|p| p.into_path().ok()).map(|p| p.display().to_string()))
}

/// The operator picks what to publish in a dialog of the backend's own, so the window can't upload any file it names.
#[tauri::command]
async fn forge_pick(handle: AppHandle, app: State<'_, App>) -> Result<Value, String> {
    use tauri_plugin_dialog::DialogExt;
    let picked = tauri::async_runtime::spawn_blocking(move || {
        handle
            .dialog()
            .file()
            .set_title("Files to publish on ReclaimerForge")
            .add_filter("Reclaimer maps, gametypes and playlists", &["json", "mvar", "bin"])
            .add_filter("Any file", &["*"])
            .blocking_pick_files()
    })
    .await
    .map_err(|e| e.to_string())?;
    let paths: Vec<PathBuf> = picked.unwrap_or_default().into_iter().filter_map(|p| p.into_path().ok()).collect();
    let files: Vec<Value> = paths
        .iter()
        .map(|p| json!({"name": p.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default(),
            "size": std::fs::metadata(p).map(|m| m.len()).unwrap_or(0)}))
        .collect();
    *app.picked.lock() = paths;
    Ok(json!({"files": files}))
}

/// The picked files onto Forge: a new listing (`lid` None) or a new version of `lid`.
#[tauri::command]
async fn forge_publish(lid: Option<String>, info: Value, app: State<'_, App>) -> Result<Value, String> {
    let h = hub_of(&app)?;
    let f = match h.client() {
        Ok(f) => f,
        Err(e) => return Ok(forge_err(e)),
    };
    let paths = std::mem::take(&mut *app.picked.lock());
    if paths.is_empty() {
        return Ok(json!({"error": {"short": "NOTHING PICKED", "text": "Pick the files to publish first."}}));
    }
    let mut files = Vec::new();
    for p in &paths {
        let name = p.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
        let size = std::fs::metadata(p).map(|m| m.len()).unwrap_or(0);
        if size == 0 || size > forge::MAX_UPLOAD {
            return Ok(json!({"error": {"short": "CAN'T SEND THAT", "text": format!("{name} is {size} bytes. ReclaimerForge takes files of 1 byte to 100 MiB.")}}));
        }
        match std::fs::read(p) {
            Ok(b) => files.push((name, b)),
            Err(e) => return Ok(json!({"error": {"short": "CAN'T READ IT", "text": format!("{name}: {e}")}})),
        }
    }
    let h2 = h.clone();
    let say = move |t: String| h2.emit(json!({"kind": "log", "station": "FORGE", "text": t, "color": "white"}));
    Ok(match f.publish(lid.as_deref(), &info, &files, &say).await {
        Ok(x) => json!({"listing": x}),
        Err(e) => forge_err(e),
    })
}

/// A change to one of the operator's own listings: "edit", "notes", "withdraw" or "delete".
#[tauri::command]
async fn forge_write(what: String, lid: String, vid: Option<String>, change: Option<Value>, app: State<'_, App>) -> Result<Value, String> {
    let h = hub_of(&app)?;
    let f = match h.client() {
        Ok(f) => f,
        Err(e) => return Ok(forge_err(e)),
    };
    Ok(match f.change(&what, &lid, vid.as_deref().unwrap_or(""), &change.unwrap_or(json!({}))).await {
        Ok(x) => json!({"listing": x}),
        Err(e) => forge_err(e),
    })
}

#[tauri::command]
fn forge_set_key(key: String, remember: bool, app: State<'_, App>) -> Result<Value, String> {
    let h = hub_of(&app)?;
    let secret = Secret::new(&key);
    drop(key); // out of the arguments at once: only the Secret is kept
    let mut warn = String::new();
    {
        let mut s = h.fsetup.lock();
        s.key = secret.clone();
        s.source = "typed this session".into();
        s.error.clear();
        if remember {
            if let Some(cfg) = s.config.clone() {
                match config::remember_forge_key(&cfg, secret.value()) {
                    Ok(()) => s.source = cfg.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default(),
                    Err(_) => warn = "Couldn't save it. It's loaded for this session.".into(),
                }
            }
        }
    }
    h.forge_connect();
    Ok(json!({"status": h.forge_status(), "warn": warn}))
}

#[tauri::command]
fn forge_status(app: State<'_, App>) -> Result<Value, String> {
    Ok(hub_of(&app)?.forge_status())
}

#[tauri::command]
fn forge_state(app: State<'_, App>) -> Result<Value, String> {
    Ok(hub_of(&app)?.forge_state_view())
}

#[tauri::command]
async fn forge_plan(index: usize, listing: Value, version: Value, app: State<'_, App>) -> Result<Value, String> {
    let h = hub_of(&app)?;
    Ok(match h.forge_plan(index, listing, version).await {
        Ok(p) => p,
        Err((title, text)) => json!({"error": {"short": title, "text": text}}),
    })
}

#[tauri::command]
async fn forge_put(plan: String, app: State<'_, App>) -> Result<(), String> {
    hub_of(&app)?.forge_put(plan);
    Ok(())
}

#[tauri::command]
fn forge_ack(lid: String, app: State<'_, App>) -> Result<(), String> {
    hub_of(&app)?.forge_ack(&lid);
    Ok(())
}

#[tauri::command]
fn prefs_intro(full: Option<bool>, seen: Option<bool>) -> Value {
    let mut p = prefs_load();
    if let Some(f) = full {
        p.full_intro = f;
    }
    if let Some(s) = seen {
        p.intro_seen = s;
    }
    prefs_save(&p);
    json!({"full_intro": p.full_intro, "intro_seen": p.intro_seen})
}

// --- plug-ins (F10) ------------------------------------------------------------------------------------------------
/// Why a plug-in won't run, or "" when it will. The demo's own, and the one built in, count as approved.
fn plugin_state(m: &plugins::Manifest, demo: bool) -> String {
    if demo || m.builtin {
        m.error.clone()
    } else {
        plugins::state(m, &prefs_raw()["plugins_trusted"])
    }
}

/// The plug-ins the config turns on: each manifest, and `state`, why it won't run ("" when it will).
#[tauri::command]
fn plugins_list(app: State<'_, App>) -> Vec<Value> {
    let demo = *app.demo.lock();
    app.plugins
        .lock()
        .iter()
        .map(|m| {
            let mut v = json!(m);
            v["state"] = json!(plugin_state(m, demo));
            v
        })
        .collect()
}

/// plugins/ beside the config file.
fn plugins_dir(config: &std::path::Path) -> PathBuf {
    config.parent().unwrap_or(std::path::Path::new(".")).join("plugins")
}

/// The plug-ins the config turns on, read from disk, then the one built in (which a folder can't stand in for).
fn load_plugins(path: Option<&std::path::Path>) -> Result<Vec<plugins::Manifest>, String> {
    let ids = plugins::enabled(&config::top_level(path, "plugins")).map_err(|e| format!("{}: {e}", path.map(|p| p.display().to_string()).unwrap_or_default()))?;
    let mut list = plugins::discover(path.map(plugins_dir).as_deref(), &ids);
    list.retain(|m| m.id != studio::ID);
    list.push(studio::manifest());
    Ok(list)
}

/// The config file onboarding edits and installs beside; the demo has none.
fn plugin_config(app: &App) -> Result<PathBuf, String> {
    if *app.demo.lock() {
        return Err("Not in the demo: it has no config file to turn a plug-in on in.".into());
    }
    Ok(config_path(&app.args).unwrap_or_else(config::user_config))
}

/// Onboarding's first step: where plug-ins go (`dir`), and the folders already in it that the config doesn't turn on.
#[tauri::command]
fn plugins_found(app: State<'_, App>) -> Result<Value, String> {
    let path = plugin_config(&app)?;
    let dir = plugins_dir(&path);
    let mut on = plugins::enabled(&config::top_level(Some(&path), "plugins")).unwrap_or_default();
    on.push(studio::ID.into()); // load_plugins drops a folder by the built-in's name, so don't offer one
    Ok(json!({"dir": dir.display().to_string(), "found": plugins::available(&dir, &on)}))
}

/// `git clone` a plug-in into plugins/ (nothing runs, nothing is turned on): its id.
#[tauri::command]
async fn plugin_install(url: String, app: State<'_, App>) -> Result<String, String> {
    let dir = plugins_dir(&plugin_config(&app)?);
    plugins::install(&dir, url.trim()).await
}

/// Put a folder of plugins/ in, or take it out of, the config's `plugins = [...]` and read the list again. Taking one
/// out stops its session; its files and its approval stay, so turning it back on needs nothing more.
#[tauri::command]
fn plugin_enable(id: String, on: bool, app: State<'_, App>) -> Result<(), String> {
    let path = plugin_config(&app)?;
    if on && !(plugins::valid_id(&id) && plugins_dir(&path).join(&id).join(plugins::MANIFEST).is_file()) {
        return Err(format!("plugins/{id} has no {}.", plugins::MANIFEST));
    }
    config::set_plugin(&path, &id, on)?;
    if !on {
        session::stop(&id);
    }
    *app.plugins.lock() = load_plugins(Some(&path))?;
    Ok(())
}

/// Record the operator's approval against the hash of the manifest as it is now: any change asks again.
#[tauri::command]
fn plugin_approve(id: String, app: State<'_, App>) -> Result<(), String> {
    let hash = app.plugins.lock().iter().find(|m| m.id == id && m.error.is_empty()).map(|m| m.hash.clone()).ok_or("no such plug-in")?;
    let mut v = prefs_raw();
    if !v["plugins_trusted"].is_object() {
        v["plugins_trusted"] = json!({});
    }
    v["plugins_trusted"][&id] = json!(hash);
    util::write_atomic(&prefs_path(), &format!("{v}\n"), false).map_err(|e| e.to_string())
}

/// Run one plug-in that may run; the demo's is answered in here.
async fn plugin_ask(app: &State<'_, App>, id: &str, request: Value, timeout: f64) -> Result<(plugins::Manifest, Value), String> {
    let demo = *app.demo.lock();
    let m = app.plugins.lock().iter().find(|m| m.id == id).cloned().ok_or("no such plug-in")?;
    let why = plugin_state(&m, demo);
    if !why.is_empty() {
        return Err(why);
    }
    let raw = if m.builtin {
        studio::call(&request, app).await
    } else if demo {
        plugins::demo_call(&request).await
    } else if m.mode == "session" {
        session::ask(id, request, timeout).await?
    } else {
        plugins::call(&m, &request, timeout).await?
    };
    Ok((m, raw))
}

/// Start a session plug-in that may run (it's kept going until the console stops). `servers` are `{index, name}`.
#[tauri::command]
fn plugin_session_start(id: String, servers: Vec<Value>, handle: AppHandle, app: State<'_, App>) -> Result<(), String> {
    let demo = *app.demo.lock();
    let m = app.plugins.lock().iter().find(|m| m.id == id).cloned().ok_or("no such plug-in")?;
    let why = plugin_state(&m, demo);
    if !why.is_empty() || demo || m.mode != "session" {
        return Err(if why.is_empty() { "not a session plug-in".into() } else { why });
    }
    session::start(m, servers, std::sync::Arc::new(move |v| {
        let _ = handle.emit("oni", vec![v]);
    }));
    Ok(())
}

/// What its git upstream has that its folder hasn't (`{behind, log, source, to}`), or null: up to date, not a git
/// clone, or the demo.
#[tauri::command]
async fn plugin_update_check(id: String, app: State<'_, App>) -> Result<Value, String> {
    let m = app.plugins.lock().iter().find(|m| m.id == id).cloned().ok_or("no such plug-in")?;
    if *app.demo.lock() {
        return Ok(Value::Null);
    }
    Ok(plugins::update_check(&m).await?.unwrap_or(Value::Null))
}

/// Fast-forward its folder to its git upstream, stop its session and read its manifest again: a changed manifest
/// needs approving again, and the window's next plugins_list starts it afresh.
#[tauri::command]
async fn plugin_update(id: String, app: State<'_, App>) -> Result<(), String> {
    let m = app.plugins.lock().iter().find(|m| m.id == id).cloned().ok_or("no such plug-in")?;
    if *app.demo.lock() {
        return Err("not in the demo".into());
    }
    plugins::update(&m).await?;
    session::stop(&id);
    if let (Some(f), Some(x)) = (&m.folder, app.plugins.lock().iter_mut().find(|x| x.id == id)) {
        *x = plugins::load(f);
    }
    Ok(())
}

const PLUGIN_INDEX: &str = "https://raw.githubusercontent.com/viik2k/oni-rcon/main/plugins/index.json";

/// The plug-in index: fetched, and kept beside the config for offline, or that copy when the fetch fails or updates are
/// turned off (ONI_RCON_NO_UPDATE=1). Empty when there's neither.
#[tauri::command]
async fn plugin_index(app: State<'_, App>) -> Result<Vec<plugins::Entry>, String> {
    async fn get() -> Option<Value> {
        let c = reqwest::Client::builder().user_agent(format!("oni-rcon/{}", env!("CARGO_PKG_VERSION"))).timeout(std::time::Duration::from_secs(5)).build().ok()?;
        let mut r = c.get(PLUGIN_INDEX).send().await.ok()?.error_for_status().ok()?;
        let mut body = Vec::new();
        while let Some(b) = r.chunk().await.ok()? {
            body.extend_from_slice(&b);
            if body.len() > 1 << 20 {
                return None;
            }
        }
        serde_json::from_slice(&body).ok()
    }
    let cache = config::user_config().with_file_name("plugin-index.json");
    let fresh = if std::env::var_os("ONI_RCON_NO_UPDATE").is_some() { None } else { get().await };
    let raw = match fresh {
        Some(v) => {
            let _ = util::write_atomic(&cache, &format!("{v}\n"), false);
            v
        }
        None => std::fs::read_to_string(&cache).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or(Value::Null),
    };
    let index = plugins::index(&raw);
    *app.index.lock() = index.clone();
    Ok(index)
}

/// Install a plug-in the index lists into plugins/ (nothing runs, nothing is turned on). The window names only its id.
#[tauri::command]
async fn plugin_index_install(id: String, app: State<'_, App>) -> Result<(), String> {
    let dir = plugins_dir(&plugin_config(&app)?);
    let e = app.index.lock().iter().find(|e| e.id == id).cloned().ok_or("That plug-in isn't in the index.")?;
    plugins::install_entry(&dir, &e).await
}

/// One message to a running session plug-in: an event, or the reply to its RCON.
#[tauri::command]
fn plugin_send(id: String, msg: Value) -> Result<(), String> {
    session::send(&id, &msg)
}

/// One RCON command its page asked for (`{oni:"rcon"}`), through the same filter as an action's: `{rcon, dropped}`.
#[tauri::command]
fn plugin_filter(id: String, rcon: Value, servers: Vec<String>, app: State<'_, App>) -> Result<Value, String> {
    let m = app.plugins.lock().iter().find(|m| m.id == id).cloned().ok_or("no such plug-in")?;
    let why = plugin_state(&m, *app.demo.lock());
    if !why.is_empty() {
        return Err(why);
    }
    Ok(plugins::normalise_action(&json!({"rcon": [rcon]}), &m.rcon, &servers))
}

/// Its page's own request (`{oni:"call"}`), handed to its program as `{type:"call", data}`; the reply goes back to
/// the page as it is, at most MAX_OUT bytes, never shown by Oni itself.
#[tauri::command]
async fn plugin_call(id: String, data: Value, server: Option<String>, app: State<'_, App>) -> Result<Value, String> {
    let (_, raw) = plugin_ask(&app, &id, json!({"type": "call", "data": data, "server": server}), plugins::ACTION_TIMEOUT).await?;
    Ok(raw)
}

/// Its view for `server` (the selected server as a plug-in names it), shaped; `error` set when it gave none.
/// `snapshot` is that server's roster, map, mode and scores, already redacted to the plug-in's permissions; `replies`
/// the RCON it asked for since its last view, with what each server answered.
#[tauri::command]
async fn plugin_view(id: String, server: Option<String>, snapshot: Option<Value>, replies: Option<Value>, app: State<'_, App>) -> Result<Value, String> {
    let req = json!({"type": "view", "server": server, "snapshot": snapshot, "replies": replies.unwrap_or(json!([]))});
    let (_, raw) = plugin_ask(&app, &id, req, plugins::VIEW_TIMEOUT).await?;
    let mut v = plugins::normalise_view(&raw);
    if let Some(e) = v["error"].as_str() {
        v["error"] = json!(health::scrub(e)); // its own words about a failure: no address in them
    }
    plugins::stash(&id, &mut v);
    Ok(v)
}

/// Run an action: its reply shaped, with the RCON it asked for split into what its permissions and `servers` (every
/// server as plug-ins name them) allow, and why the rest was dropped.
#[tauri::command]
async fn plugin_action(id: String, action: String, params: Value, row: Option<String>, server: Option<String>, snapshot: Option<Value>, servers: Vec<String>, app: State<'_, App>) -> Result<Value, String> {
    let req = json!({"type": "action", "id": action, "params": params, "row": row, "server": server, "snapshot": snapshot});
    let mut r = match plugin_ask(&app, &id, req, plugins::ACTION_TIMEOUT).await {
        Ok((m, raw)) => plugins::normalise_action(&raw, &m.rcon, &servers),
        Err(e) => plugins::normalise_action(&json!({"ok": false, "error": e}), &[], &[]),
    };
    plugins::stash(&id, &mut r["view"]);
    Ok(r)
}

/// An html block's page for its frame, at oniplugin://localhost/<id>.<n>?redact=1 (http://oniplugin.localhost/... on Windows).
/// Its own CSP: inline script and style, data: images and fonts, and no network at all, so a page can't phone home.
/// The built-in studio's page may also start blob: workers, its scripts' sandboxes, which inherit the same CSP.
fn plugin_frame(uri: &tauri::http::Uri) -> tauri::http::Response<Vec<u8>> {
    let path = uri.path().trim_start_matches('/');
    let page = plugins::frame(path);
    let redact = uri.query().is_some_and(|q| q.split('&').any(|x| x == "redact=1"));
    let Some(page) = page else {
        return tauri::http::Response::builder().status(404).body(b"gone: open the plug-in again".to_vec()).unwrap();
    };
    let workers = if path.starts_with(&format!("{}.", studio::ID)) { " worker-src blob:;" } else { "" };
    tauri::http::Response::builder()
        .header("Content-Type", "text/html; charset=utf-8")
        .header("Content-Security-Policy", format!("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; form-action 'none'; base-uri 'none';{workers}"))
        .header("Cache-Control", "no-store")
        .body((if redact { health::hide_ips(&page) } else { page }).into_bytes())
        .unwrap()
}

/// The newest desktop release newer than `current`, as (version, page, notes): the terminal console's v* releases
/// share the repository, so only desktop-v* tags count, read without their prefix.
fn newer_desktop(releases: &Value, current: &str) -> Option<(String, String, String)> {
    let parse = |v: &str| v.split(['.', '-', '+']).take(3).map(|x| x.parse::<u64>().unwrap_or(0)).collect::<Vec<_>>();
    releases
        .as_array()?
        .iter()
        .filter(|r| r["draft"] != true && r["prerelease"] != true)
        .filter_map(|r| {
            let v = r["tag_name"].as_str()?.strip_prefix("desktop-v")?;
            Some((v.to_string(), r["html_url"].as_str().unwrap_or("https://github.com/viik2k/oni-rcon/releases").to_string(), r["body"].as_str().unwrap_or("").to_string()))
        })
        .max_by(|a, b| parse(&a.0).cmp(&parse(&b.0)))
        .filter(|(v, _, _)| parse(v) > parse(current))
}

fn github() -> Option<reqwest::Client> {
    reqwest::Client::builder().user_agent(format!("oni-rcon/{}", env!("CARGO_PKG_VERSION"))).timeout(std::time::Duration::from_secs(10)).build().ok()
}

/// The signed update for a newer desktop release, that release's page for when installing it fails, and its notes.
/// Each release carries its own latest.json: the repository's /releases/latest is the terminal console. None when up
/// to date, offline or turned off (ONI_RCON_NO_UPDATE=1).
async fn find_update(app: &AppHandle) -> Option<(tauri_plugin_updater::Update, String, String)> {
    use tauri_plugin_updater::UpdaterExt;
    if std::env::var_os("ONI_RCON_NO_UPDATE").is_some() {
        return None;
    }
    let v = github()?.get("https://api.github.com/repos/viik2k/oni-rcon/releases?per_page=30").send().await.ok()?.json::<Value>().await.ok()?;
    let (ver, page, notes) = newer_desktop(&v, env!("CARGO_PKG_VERSION"))?;
    let latest = format!("https://github.com/viik2k/oni-rcon/releases/download/desktop-v{ver}/latest.json").parse().ok()?;
    let update = app.updater_builder().endpoints(vec![latest]).ok()?.build().ok()?.check().await.ok()??;
    Some((update, page, notes))
}

/// A newer desktop release, as {version, notes}; null when there is none.
#[tauri::command]
async fn update_check(app: AppHandle) -> Value {
    find_update(&app).await.map_or(Value::Null, |(u, _, notes)| json!({"version": u.version, "notes": notes}))
}

/// This version's release notes, as {version, notes}, on its first start: after an update, or after one from before
/// prefs remembered the version. Null on every other start, on a first run ever (`fresh`), with no notes, offline
/// (asked again next start) or with updates turned off (ONI_RCON_NO_UPDATE=1).
#[tauri::command]
async fn whats_new(fresh: bool) -> Value {
    let now = env!("CARGO_PKG_VERSION");
    let seen = prefs_raw()["seen_version"].as_str().map(String::from);
    if std::env::var_os("ONI_RCON_NO_UPDATE").is_some() || seen.as_deref() == Some(now) {
        return Value::Null;
    }
    let notes = if seen.is_none() && fresh {
        String::new()
    } else {
        let url = format!("https://api.github.com/repos/viik2k/oni-rcon/releases/tags/desktop-v{now}");
        let Some(rel) = async { github()?.get(url).send().await.ok()?.error_for_status().ok()?.json::<Value>().await.ok() }.await else {
            return Value::Null;
        };
        rel["body"].as_str().unwrap_or("").to_string()
    };
    let mut p = prefs_raw();
    p["seen_version"] = json!(now);
    let _ = util::write_atomic(&prefs_path(), &format!("{p}\n"), false);
    if notes.trim().is_empty() {
        Value::Null
    } else {
        json!({"version": now, "notes": notes})
    }
}

/// The newest Project Reclaimer release, as "0.9.18", so a card can say its server is behind: servers admit only
/// players on exactly their version. Empty offline or with updates turned off (ONI_RCON_NO_UPDATE=1).
#[tauri::command]
async fn reclaimer_latest() -> String {
    async fn get() -> Option<String> {
        let v = github()?.get(host::RELEASES).send().await.ok()?.json::<Value>().await.ok()?;
        Some(v["tag_name"].as_str()?.trim_start_matches('v').to_string())
    }
    if std::env::var_os("ONI_RCON_NO_UPDATE").is_some() {
        return String::new();
    }
    get().await.unwrap_or_default()
}

/// Downloads the update, checks its signature, installs it and restarts the console.
#[tauri::command]
async fn update_install(app: AppHandle) -> Result<(), String> {
    let (update, page, _) = find_update(&app).await.ok_or("no update found")?;
    update.download_and_install(|_, _| {}, || {}).await.map_err(|e| format!("{e}. Download it from {page}"))?;
    app.restart()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_desktop_releases_are_updates() {
        let rel = |tag: &str| json!({"tag_name": tag, "html_url": format!("https://x/{tag}"), "body": format!("## {tag}"), "draft": false, "prerelease": false});
        let list = json!([rel("v0.9.5"), rel("desktop-v1.0.0"), rel("desktop-v1.2.0"), rel("desktop-v1.10.0"), {"tag_name": "desktop-v9.0.0", "draft": true}]);
        assert_eq!(newer_desktop(&list, "1.0.0"), Some(("1.10.0".into(), "https://x/desktop-v1.10.0".into(), "## desktop-v1.10.0".into())));
        assert_eq!(newer_desktop(&list, "1.10.0"), None);
        assert_eq!(newer_desktop(&json!([rel("v2.0.0")]), "1.0.0"), None);
    }
}

/// oni-rcon.log beside the config file: faults the window hit, and the backend's panics.
fn log_path() -> PathBuf {
    config::user_config().with_file_name("oni-rcon.log")
}

fn log_line(text: &str) {
    use std::io::Write;
    let p = log_path();
    let _ = std::fs::create_dir_all(p.parent().unwrap());
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&p) {
        let _ = writeln!(f, "{} oni-rcon {} {}: {}", util::utc_iso(), env!("CARGO_PKG_VERSION"), std::env::consts::OS, util::scrub(text));
    }
}

#[tauri::command]
fn log_error(text: String) {
    log_line(&text);
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let (args, arg_error) = match parse_args() {
        Ok(a) => (a, String::new()),
        Err(e) => (Args::default(), e),
    };
    std::panic::set_hook(Box::new(|info| log_line(&format!("panic: {info}"))));
    let devtools = args.devtools;
    let demo = args.demo; // until the setup screen saves servers of the operator's own
    tauri::Builder::default()
        .register_uri_scheme_protocol("oniplugin", |_, req| plugin_frame(req.uri()))
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_dialog::init())
        .manage(App {
            args,
            arg_error,
            hub: Mutex::new(None),
            typed: Mutex::new(HashMap::new()),
            by: Mutex::new(String::new()),
            forced_setup: Mutex::new(false),
            demo: Mutex::new(demo),
            hint: Mutex::new(String::new()),
            picked: Mutex::new(Vec::new()),
            plugins: Mutex::new(Vec::new()),
            index: Mutex::new(Vec::new()),
        })
        .setup(move |app| {
            if devtools {
                if let Some(w) = app.get_webview_window("main") {
                    w.open_devtools();
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            launch_info, setup_probe, setup_check, setup_save, setup_host, pick_playlist, host_status, setup_find, setup_open, start_console, log_error, call, reconnect, set_password, restart_server, remove_server, server_setting, playlists, save_playlist, playlist_library, playlist_read, save_game_type,
            set_labels, health_now, health_view, forge_listings, forge_favourites, forge_listing, forge_set_key,
            forge_status, forge_state, forge_plan, forge_put, forge_ack, forge_mine, forge_pick, forge_publish, forge_write, prefs_intro, plugins_list, plugins_found, plugin_install, plugin_enable, plugin_approve, plugin_view, plugin_action, plugin_session_start, plugin_send, plugin_call, plugin_filter, plugin_update_check, plugin_update, plugin_index, plugin_index_install, update_check, update_install, whats_new, reclaimer_latest,
            automation::automation_load, automation::automation_save, automation::discord_add, automation::discord_remove, automation::discord_post,
            automation::telegram_add, automation::telegram_remove, automation::telegram_post, automation::telegram_find_chats
        ])
        .on_window_event(|w, e| {
            if let tauri::WindowEvent::Destroyed = e {
                if let Some(h) = w.app_handle().state::<App>().hub.lock().take() {
                    h.stop();
                }
                session::stop_all();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running the console");
}
