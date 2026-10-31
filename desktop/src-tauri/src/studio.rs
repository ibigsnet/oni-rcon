//! Automation Studio: the plug-in that ships inside Oni. Its page (studio.html) is a whole editor for small JavaScript
//! automations and the place they run, each in its own Web Worker inside the plug-in's sandboxed frame: no network, no
//! way into the app but the frame's messages, RCON only through the same permission filter as any plug-in's. This side
//! only keeps the scripts on disk and posts to Discord and Telegram by name. The script API is docs/STUDIO.md.
//!
//! Scripts live in studio/ beside the config: `<name>.js`, the script's `store` in `<name>.data.json`, and which are
//! on in `studio.json`.
use crate::plugins::Manifest;
use crate::{automation, config, util};
use parking_lot::Mutex;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

pub const ID: &str = "automation-studio";
const PAGE: &str = include_str!("studio.html");
const MAX_SCRIPT: usize = 256 * 1024;
const MAX_SCRIPTS: usize = 200;
const MAX_STORE: usize = 256 * 1024;
const NAME_HELP: &str = "A script's name is lower-case letters, digits, - and _ (up to 40), starting with a letter or digit.";
/// What its scripts may have Oni send: every command Oni knows (actions.ts COMMANDS).
const RCON: [&str; 37] = ["status", "players", "say", "tell", "kick", "ban", "banip", "unban", "bans", "vpn", "vpnallow", "vpnrevoke", "mute",
    "unmute", "endround", "endgame", "maps", "modes", "map", "mode", "load", "nextmap", "servername", "password", "maxping", "teamcount",
    "shuffle", "team", "vote", "startvote", "passvote", "cancelvote", "admin", "admins", "pardon", "reload", "help"];
/// For its approval-free listing only: the window hands it every event kind (see pluginEvent in engine.ts).
const EVENTS: [&str; 19] = ["chat", "kill", "join", "leave", "refused", "kick", "ban", "unban", "mute", "unmute", "cheat", "pardon",
    "admin_added", "admin_removed", "vote", "medal", "award", "lagged", "control"];

/// Scripts running and failed, as its page last said: the badge.
static STATUS: Mutex<(u64, u64)> = Mutex::new((0, 0));
/// One change to studio/ at a time: a read, an edit and a write of studio.json must not interleave.
static LOCK: Mutex<()> = Mutex::new(());

pub fn manifest() -> Manifest {
    Manifest {
        id: ID.into(), hash: "built-in".into(), name: "Automation Studio".into(), version: env!("CARGO_PKG_VERSION").into(), author: "oni-rcon".into(),
        description: "Write, test and run JavaScript automations against your servers.".into(), mode: "service".into(),
        exec: "(built into oni-rcon)".into(), events: EVENTS.map(String::from).into(), rcon: RCON.map(String::from).into(), builtin: true,
        ..Default::default()
    }
}

/// Where its scripts are: studio/ beside the config in use (its default when None), or the demo's scratch folder.
static DIR: Mutex<Option<PathBuf>> = Mutex::new(None);

pub fn use_dir(d: Option<PathBuf>) {
    *DIR.lock() = d;
}

/// The demo's own scripts folder, with one script on so there's something running to look at.
pub fn demo(d: PathBuf) {
    let _ = std::fs::create_dir_all(&d);
    let _ = std::fs::write(d.join("streaks.js"), DEMO_SCRIPT);
    let _ = set_on(&d, vec!["streaks".into()]);
    use_dir(Some(d));
}

const DEMO_SCRIPT: &str = r#"// Announce killing sprees, and count every player's kills in this script's store.
const streak = new Map();

on("kill", (ev) => {
  streak.set(ev.victim_name, 0);
  const n = (streak.get(ev.killer_name) ?? 0) + 1;
  streak.set(ev.killer_name, n);
  store.inc(`kills:${ev.killer_name}`);
  if (n % 5 === 0) say(ev.server, `${ev.killer_name} is on a ${n} kill streak!`);
});

command("!kills", (ev) => {
  ev.reply(`${ev.name}: ${store.get(`kills:${ev.name}`, 0)} kills`);
});

every("10m", () => log("tracking", streak.size, "players"));
"#;

fn dir() -> PathBuf {
    DIR.lock().clone().unwrap_or_else(|| config::user_config().with_file_name("studio"))
}

fn name_ok(n: &str) -> bool {
    let b = n.as_bytes();
    (1..=40).contains(&b.len()) && (b[0].is_ascii_lowercase() || b[0].is_ascii_digit())
        && b.iter().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'-' || *c == b'_')
}

fn name<'a>(d: &'a Value, key: &str) -> Result<&'a str, String> {
    d[key].as_str().filter(|n| name_ok(n)).ok_or_else(|| NAME_HELP.into())
}

fn code_path(dir: &Path, n: &str) -> PathBuf {
    dir.join(format!("{n}.js"))
}

fn store_path(dir: &Path, n: &str) -> PathBuf {
    dir.join(format!("{n}.data.json"))
}

fn mtime(p: &Path) -> u64 {
    std::fs::metadata(p).and_then(|m| m.modified()).ok().and_then(|t| t.duration_since(UNIX_EPOCH).ok()).map_or(0, |d| d.as_millis() as u64)
}

/// Its scripts' names, sorted.
fn names(dir: &Path) -> Vec<String> {
    let mut v: Vec<String> = std::fs::read_dir(dir).into_iter().flatten().flatten()
        .filter_map(|e| e.file_name().into_string().ok()?.strip_suffix(".js").map(String::from))
        .filter(|n| name_ok(n))
        .collect();
    v.sort();
    v
}

fn on_list(dir: &Path) -> Vec<String> {
    let f: Value = std::fs::read_to_string(dir.join("studio.json")).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or_default();
    f["on"].as_array().map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect()).unwrap_or_default()
}

fn set_on(dir: &Path, on: Vec<String>) -> Result<(), String> {
    write(&dir.join("studio.json"), &format!("{}\n", json!({"on": on})))
}

fn write(p: &Path, text: &str) -> Result<(), String> {
    util::write_atomic(p, text, false).map_err(|e| format!("couldn't save {}: {e}", p.file_name().unwrap_or_default().to_string_lossy()))
}

/// One of its page's file requests (`data.op`). An error is its reply's `error`.
fn op(dir: &Path, d: &Value) -> Result<Value, String> {
    let _one = LOCK.lock();
    match d["op"].as_str().unwrap_or_default() {
        "list" => {
            let on = on_list(dir);
            let scripts: Vec<Value> = names(dir).into_iter().map(|n| {
                let p = code_path(dir, &n);
                json!({"code": std::fs::read_to_string(&p).unwrap_or_default(), "mtime": mtime(&p), "on": on.contains(&n), "name": n})
            }).collect();
            Ok(json!({"dir": dir.display().to_string(), "scripts": scripts}))
        }
        "stat" => Ok(json!({"scripts": names(dir).into_iter().map(|n| json!({"mtime": mtime(&code_path(dir, &n)), "name": n})).collect::<Vec<_>>()})),
        "read" => {
            let n = name(d, "name")?;
            let p = code_path(dir, n);
            let code = std::fs::read_to_string(&p).map_err(|_| format!("there is no script called {n}"))?;
            Ok(json!({"name": n, "code": code, "mtime": mtime(&p)}))
        }
        "save" => {
            let n = name(d, "name")?;
            let code = d["code"].as_str().ok_or("nothing to save")?;
            let p = code_path(dir, n);
            if code.len() > MAX_SCRIPT {
                return Err(format!("A script can be at most {} KiB.", MAX_SCRIPT >> 10));
            }
            if !p.exists() && names(dir).len() >= MAX_SCRIPTS {
                return Err(format!("{MAX_SCRIPTS} scripts is the limit."));
            }
            if d["new"] == true && p.exists() {
                return Err(format!("There is already a script called {n}."));
            }
            write(&p, code)?;
            Ok(json!({"mtime": mtime(&p)}))
        }
        "delete" => {
            let n = name(d, "name")?;
            std::fs::remove_file(code_path(dir, n)).map_err(|e| format!("couldn't delete {n}: {e}"))?;
            let _ = std::fs::remove_file(store_path(dir, n));
            set_on(dir, on_list(dir).into_iter().filter(|x| x != n).collect())?;
            Ok(json!({}))
        }
        "rename" => {
            let (n, to) = (name(d, "name")?, name(d, "to")?);
            if code_path(dir, to).exists() {
                return Err(format!("There is already a script called {to}."));
            }
            std::fs::rename(code_path(dir, n), code_path(dir, to)).map_err(|e| format!("couldn't rename {n}: {e}"))?;
            if store_path(dir, n).exists() {
                std::fs::rename(store_path(dir, n), store_path(dir, to)).map_err(|e| format!("renamed {n}, but not its store: {e}"))?;
            }
            set_on(dir, on_list(dir).into_iter().map(|x| if x == n { to.to_string() } else { x }).collect())?;
            Ok(json!({}))
        }
        "enable" => {
            let n = name(d, "name")?;
            if !code_path(dir, n).exists() {
                return Err(format!("there is no script called {n}"));
            }
            let mut on: Vec<String> = on_list(dir).into_iter().filter(|x| x != n).collect();
            if d["on"] == true {
                on.push(n.into());
            }
            set_on(dir, on)?;
            Ok(json!({}))
        }
        "store" => {
            let n = name(d, "name")?;
            let v: Value = std::fs::read_to_string(store_path(dir, n)).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or_default();
            Ok(json!({"data": if v.is_object() { v } else { json!({}) }}))
        }
        "store-save" => {
            let n = name(d, "name")?;
            if !d["data"].is_object() {
                return Err("a store is an object".into());
            }
            let text = d["data"].to_string();
            if text.len() > MAX_STORE {
                return Err(format!("{n}'s store would be over {} KiB: nothing was saved.", MAX_STORE >> 10));
            }
            write(&store_path(dir, n), &text)?;
            Ok(json!({}))
        }
        "status" => {
            *STATUS.lock() = (d["running"].as_u64().unwrap_or(0), d["failed"].as_u64().unwrap_or(0));
            Ok(json!({}))
        }
        other => Err(format!("no op '{}'", other.chars().take(40).collect::<String>())),
    }
}

/// The badge: failures first, then how many run.
fn view() -> Value {
    let (running, failed) = *STATUS.lock();
    let badge = if failed > 0 {
        json!({"level": "warn", "text": format!("{failed} FAILED")})
    } else if running > 0 {
        json!({"level": "ok", "text": format!("{running} RUNNING")})
    } else {
        json!({"level": "off", "text": "IDLE"})
    };
    json!({"title": "Automation Studio", "badge": badge,
        "blocks": [{"type": "html", "html": PAGE, "persist": true, "fill": true, "data": {"dir": dir().display().to_string()}}]})
}

/// Its answer to a request, the way a service plug-in's program would give it.
pub async fn call(request: &Value, app: &tauri::State<'_, crate::App>) -> Value {
    match request["type"].as_str() {
        Some("view") => view(),
        Some("call") => {
            let d = &request["data"];
            let (to, text) = (d["to"].as_str().unwrap_or_default().to_string(), d["text"].as_str().unwrap_or_default().to_string());
            let r = match d["op"].as_str() {
                Some("hooks") => {
                    let a = automation::automation_load();
                    let keys = |k: &str| a[k].as_object().map(|o| o.keys().cloned().collect::<Vec<_>>()).unwrap_or_default();
                    Ok(json!({"discord": keys("webhooks"), "telegram": keys("telegram")}))
                }
                Some("discord") => automation::discord_post(to, text, app.clone()).await.map(|s| json!({"status": s})),
                Some("telegram") => automation::telegram_post(to, text, app.clone()).await.map(|s| json!({"status": s})),
                _ => op(&dir(), d),
            };
            r.unwrap_or_else(|e| json!({"error": e})) // plugin_call hands any reply back as ok: the page reads `error`
        }
        _ => json!({"ok": false, "error": "Automation Studio has no actions: its page does everything."}),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scripts_save_rename_enable_and_delete_with_their_store() {
        let d = tempfile::tempdir().unwrap();
        let dir = d.path().join("studio");
        let ask = |v: Value| op(&dir, &v);
        assert_eq!(ask(json!({"op": "list"})).unwrap()["scripts"], json!([]));
        ask(json!({"op": "save", "name": "welcome", "code": "log(1)", "new": true})).unwrap();
        assert!(ask(json!({"op": "save", "name": "welcome", "code": "x", "new": true})).unwrap_err().contains("already"));
        ask(json!({"op": "save", "name": "welcome", "code": "log(2)"})).unwrap();
        ask(json!({"op": "enable", "name": "welcome", "on": true})).unwrap();
        ask(json!({"op": "store-save", "name": "welcome", "data": {"seen": 3}})).unwrap();
        ask(json!({"op": "rename", "name": "welcome", "to": "greet"})).unwrap();
        let l = ask(json!({"op": "list"})).unwrap();
        assert_eq!((l["scripts"][0]["name"].as_str(), l["scripts"][0]["code"].as_str(), &l["scripts"][0]["on"]), (Some("greet"), Some("log(2)"), &json!(true)));
        assert_eq!(ask(json!({"op": "store", "name": "greet"})).unwrap()["data"], json!({"seen": 3}));
        ask(json!({"op": "delete", "name": "greet"})).unwrap();
        assert!(!dir.join("greet.data.json").exists());
        assert_eq!(on_list(&dir), Vec::<String>::new());
    }

    #[test]
    fn names_never_become_paths_and_sizes_are_capped() {
        let d = tempfile::tempdir().unwrap();
        for bad in ["", "../x", "A", "-x", "a.b", "a/b", "a\\b", &"a".repeat(41)] {
            assert!(op(d.path(), &json!({"op": "save", "name": bad, "code": ""})).is_err(), "{bad}");
        }
        assert!(op(d.path(), &json!({"op": "save", "name": "big", "code": "x".repeat(MAX_SCRIPT + 1)})).is_err());
        assert!(op(d.path(), &json!({"op": "store-save", "name": "big", "data": {"x": "y".repeat(MAX_STORE)}})).is_err());
        assert!(op(d.path(), &json!({"op": "enable", "name": "ghost", "on": true})).is_err());
        assert!(op(d.path(), &json!({"op": "nope"})).is_err());
    }
}
