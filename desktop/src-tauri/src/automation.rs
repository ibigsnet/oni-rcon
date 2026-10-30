//! Automation rules' storage, Discord and Telegram. The rules and roles live in automation.json beside the config;
//! Discord webhook URLs live in discord.json and Telegram bot tokens in telegram.json (both owner-only) and never reach
//! the window, which only sees them masked and posts by name. From ibigs/oni-rcon's rules_store.py and rules_run.py, by
//! IBIGS; the Telegram half is new.
use crate::{config, util};
use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

const MAX_WEBHOOKS: usize = 25;
const MAX_CHATS: usize = 25;
const TELEGRAM_API: &str = "https://api.telegram.org";
const NAME_HELP: &str = "Name it with letters, numbers, - or _ (up to 32), like alerts.";
const BAD_TOKEN: &str = "That doesn't look like a bot token (123456789:ABC…). @BotFather hands it out.";
const MAX_REPLY: usize = 1 << 20; // Telegram's answers are small; one that isn't is not read
const MAX_RETRY_WAIT: u64 = 30;
/// What the bot is asked to hand over when looking for chats: a message anywhere, or being added to or removed from one.
const UPDATE_KEYS: [&str; 5] = ["message", "edited_message", "channel_post", "edited_channel_post", "my_chat_member"];
const MAX_FILE: usize = 2_000_000;

fn rules_path() -> PathBuf {
    config::user_config().with_file_name("automation.json")
}

fn hooks_path() -> PathBuf {
    config::user_config().with_file_name("discord.json")
}

fn chats_path() -> PathBuf {
    config::user_config().with_file_name("telegram.json")
}

fn name_ok(name: &str) -> bool {
    !name.is_empty() && name.len() <= 32 && name.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
}

fn read(path: &PathBuf) -> Value {
    std::fs::read_to_string(path).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or_else(|| json!({}))
}

/// The shape Discord hands out: https://discord.com/api/webhooks/<id>/<token>.
fn webhook_ok(url: &str) -> bool {
    static RE: OnceLock<regex::Regex> = OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/\d{5,30}/[A-Za-z0-9_-]{20,100}$").unwrap()).is_match(url)
}

/// The URL with its secret part hidden, for the screen.
fn masked(url: &str) -> String {
    format!("{}/••••••", url.rsplit_once('/').map_or(url, |(head, _)| head))
}

fn hooks() -> Map<String, Value> {
    read(&hooks_path()).as_object().cloned().unwrap_or_default()
}

fn save_hooks(h: &Map<String, Value>) -> Result<(), String> {
    util::write_atomic(&hooks_path(), &format!("{}\n", serde_json::to_string_pretty(&Value::Object(h.clone())).unwrap()), true).map_err(|e| format!("couldn't save the Discord webhooks: {e}"))
}

/// The shape @BotFather hands out: the bot's number, a colon, and the secret.
fn token_ok(t: &str) -> bool {
    static RE: OnceLock<regex::Regex> = OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r"^\d{5,16}:[A-Za-z0-9_-]{30,64}$").unwrap()).is_match(t)
}

/// A chat Telegram takes: its number (negative for a group or channel, at most 18 digits so it always fits a 64-bit
/// integer) or a public channel's @name.
fn chat_ok(c: &str) -> bool {
    static RE: OnceLock<regex::Regex> = OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r"^(?:-?\d{1,18}|@[A-Za-z][A-Za-z0-9_]{4,31})$").unwrap()).is_match(c)
}

/// The bot's number (not secret) and the chat, with the token's secret part hidden, for the screen.
fn chat_masked(token: &str, chat: &str) -> String {
    format!("{}:•••••• → {chat}", token.split(':').next().unwrap_or_default())
}

/// One saved chat, if its entry is whole: a token and a chat that pass the checks they passed when added. Whatever a
/// hand edit or a damaged file holds, nothing but a well-formed token ever reaches a request's URL.
fn entry(v: &Value) -> Option<(&str, &str)> {
    let (t, c) = (v["token"].as_str()?, v["chat"].as_str()?);
    (token_ok(t) && chat_ok(c)).then_some((t, c))
}

/// One change to telegram.json at a time: a read, an edit and a write must not interleave with another's.
static CHATS_LOCK: parking_lot::Mutex<()> = parking_lot::Mutex::new(());

/// The saved chats. No file is none; a file that isn't a table is an error, so a damaged file is never mistaken for an
/// empty one and written over, taking every other token with it.
fn load_chats(path: &Path) -> Result<Map<String, Value>, String> {
    let text = match std::fs::read_to_string(path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Map::new()),
        Err(e) => return Err(format!("couldn't read {}: {e}", path.display())),
    };
    if text.trim().is_empty() {
        return Ok(Map::new());
    }
    match serde_json::from_str::<Value>(&text) {
        Ok(Value::Object(m)) => Ok(m),
        _ => Err(format!("{} isn't a table of chats any more. Fix it, or delete it and add the chats again.", path.display())),
    }
}

fn save_chats(path: &Path, c: &Map<String, Value>) -> Result<(), String> {
    util::write_atomic(path, &format!("{}\n", serde_json::to_string_pretty(&Value::Object(c.clone())).unwrap()), true).map_err(|e| format!("couldn't save the Telegram chats: {e}"))
}

/// Check and keep a chat under a name; one already called that is replaced.
fn add_chat(path: &Path, name: &str, token: &str, chat: &str) -> Result<(), String> {
    let (name, token, chat) = (name.trim().to_lowercase(), token.trim(), chat.trim());
    if !name_ok(&name) {
        return Err(NAME_HELP.into());
    }
    if !token_ok(token) {
        return Err(BAD_TOKEN.into());
    }
    if !chat_ok(chat) {
        return Err("A chat is its number (like -1001234567890) or a public channel's @name.".into());
    }
    let _one = CHATS_LOCK.lock();
    let mut c = load_chats(path)?;
    if !c.contains_key(&name) && c.len() >= MAX_CHATS {
        return Err(format!("{MAX_CHATS} chats is the limit."));
    }
    c.insert(name, json!({"token": token, "chat": chat}));
    save_chats(path, &c)
}

fn remove_chat(path: &Path, name: &str) -> Result<(), String> {
    let _one = CHATS_LOCK.lock();
    let mut c = load_chats(path)?;
    if c.remove(name).is_none() {
        return Ok(());
    }
    save_chats(path, &c)
}

/// The chat names with their tokens hidden. An entry that isn't whole says so rather than vanishing.
fn chats_masked(path: &Path) -> Map<String, Value> {
    load_chats(path).unwrap_or_default().into_iter().map(|(k, v)| (k, json!(entry(&v).map_or_else(|| "damaged: add it again".to_string(), |(t, c)| chat_masked(t, c))))).collect()
}

/// The rules, the roles, and the webhook and Telegram chat names with their secrets masked.
#[tauri::command]
pub fn automation_load() -> Value {
    let f = read(&rules_path());
    let hooks: Map<String, Value> = hooks().into_iter().map(|(k, v)| (k, json!(masked(v.as_str().unwrap_or_default())))).collect();
    let chats = chats_masked(&chats_path());
    json!({"rules": if f["rules"].is_array() { f["rules"].clone() } else { json!([]) }, "roles": if f["roles"].is_object() { f["roles"].clone() } else { json!({}) }, "webhooks": hooks, "telegram": chats})
}

/// The window has already checked each rule against the catalog; this only keeps the file the right shape and size.
#[tauri::command]
pub fn automation_save(rules: Value, roles: Value) -> Result<(), String> {
    if !rules.is_array() || !roles.is_object() {
        return Err("Rules are a list and roles a table.".into());
    }
    let text = serde_json::to_string_pretty(&json!({"schema": 1, "rules": rules, "roles": roles})).unwrap();
    if text.len() > MAX_FILE {
        return Err("The rules file would be too large.".into());
    }
    util::write_atomic(&rules_path(), &format!("{text}\n"), false).map_err(|e| format!("couldn't save the rules: {e}"))
}

#[tauri::command]
pub fn discord_add(name: String, url: String) -> Result<(), String> {
    let (name, url) = (name.trim().to_lowercase(), url.trim().to_string());
    if !name_ok(&name) {
        return Err(NAME_HELP.into());
    }
    if !webhook_ok(&url) {
        return Err("That doesn't look like a Discord webhook URL (https://discord.com/api/webhooks/…).".into());
    }
    let mut h = hooks();
    if !h.contains_key(&name) && h.len() >= MAX_WEBHOOKS {
        return Err(format!("{MAX_WEBHOOKS} webhooks is the limit."));
    }
    h.insert(name, json!(url));
    save_hooks(&h)
}

#[tauri::command]
pub fn discord_remove(name: String) -> Result<(), String> {
    let mut h = hooks();
    h.remove(&name);
    save_hooks(&h)
}

/// One message to a webhook, by name; the HTTP status comes back. Nothing in an error carries the URL.
#[tauri::command]
pub async fn discord_post(name: String, text: String, app: tauri::State<'_, crate::App>) -> Result<u16, String> {
    let url = hooks().get(&name).and_then(|v| v.as_str().map(String::from)).ok_or_else(|| format!("no webhook called {name}"))?;
    if *app.demo.lock() {
        return Ok(204); // the pretend servers never post to a real channel
    }
    let body = json!({"content": text.chars().take(2000).collect::<String>(), "username": "ONI RCON", "allowed_mentions": {"parse": []}});
    client()?.post(url).json(&body).send().await.map(|r| r.status().as_u16()).map_err(|e| e.without_url().to_string())
}

/// For posting out: no redirects (a secret in a URL goes nowhere else) and a short timeout.
fn client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .user_agent(format!("oni-rcon/{}", env!("CARGO_PKG_VERSION")))
        .timeout(std::time::Duration::from_secs(10))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|e| e.without_url().to_string())
}

#[tauri::command]
pub fn telegram_add(name: String, token: String, chat: String) -> Result<(), String> {
    add_chat(&chats_path(), &name, &token, &chat)
}

#[tauri::command]
pub fn telegram_remove(name: String) -> Result<(), String> {
    remove_chat(&chats_path(), &name)
}

/// One message to a saved chat, by name; the HTTP status comes back. Nothing in an error carries the token.
#[tauri::command]
pub async fn telegram_post(name: String, text: String, app: tauri::State<'_, crate::App>) -> Result<u16, String> {
    let c = load_chats(&chats_path())?;
    let v = c.get(&name).ok_or_else(|| format!("no chat called {name}"))?;
    let (token, chat) = entry(v).map(|(t, c)| (t.to_string(), c.to_string())).ok_or_else(|| format!("{name} is damaged in telegram.json: add it again."))?;
    if text.trim().is_empty() {
        return Err("There is nothing to send.".into());
    }
    if *app.demo.lock() {
        return Ok(200); // the pretend servers never post to a real chat
    }
    send_message(TELEGRAM_API, &token, &chat, &text).await
}

/// The bot behind a token and the chats it has lately heard from, so a chat's number needn't be dug out by hand.
/// Reading them confirms nothing with Telegram, so they stay there for the next look.
#[tauri::command]
pub async fn telegram_find_chats(token: String) -> Result<Value, String> {
    find_chats(TELEGRAM_API, token.trim()).await
}

/// One Bot API call. The token is part of the URL, so it must be the shape of a token (nothing else can steer the
/// request's path), the reply is read only up to a size, and no error leaves here with a URL in it.
async fn bot_call(base: &str, token: &str, method: &str, body: &Value) -> Result<(u16, Value), String> {
    if !token_ok(token) {
        return Err(BAD_TOKEN.into());
    }
    let mut r = client()?.post(format!("{base}/bot{token}/{method}")).json(body).send().await.map_err(|e| e.without_url().to_string())?;
    let status = r.status().as_u16();
    let mut raw = Vec::new();
    while let Some(chunk) = r.chunk().await.map_err(|e| e.without_url().to_string())? {
        if raw.len() + chunk.len() > MAX_REPLY {
            return Err("Telegram's reply was too big to read.".into());
        }
        raw.extend_from_slice(&chunk);
    }
    Ok((status, serde_json::from_slice(&raw).unwrap_or(Value::Null)))
}

/// What a lookup call came to: its result, or a sentence saying what to do about it.
fn answer(status: u16, v: &Value) -> Result<Value, String> {
    match status {
        200..=299 if v["ok"] == true => Ok(v["result"].clone()),
        401 | 404 => Err("Telegram refused that token. It should be the whole line @BotFather gave you, like 123456789:ABC…".into()),
        409 => Err("Something else is reading this bot's messages (a webhook, or another program), so they can't be listed here. Type the chat ID instead.".into()),
        _ => Err(format!("Telegram answered {status}{}", v["description"].as_str().map(|d| format!(": {d}")).unwrap_or_default())),
    }
}

/// Plain text only (no formatting to break on a player's name), no link previews, at most the 4096 characters Telegram takes.
async fn send_message(base: &str, token: &str, chat: &str, text: &str) -> Result<u16, String> {
    deliver(base, token, chat, text, std::time::Duration::from_secs(1)).await
}

/// `send_message`, with one second chance. Telegram says when to try again after a flood limit ("retry_after", taken up
/// to 30 seconds; longer is the caller's to know about), and a gateway that stumbled (502, 503, 504) gets 2 seconds.
/// Neither took the message, so nothing is sent twice. Any other answer, a refusal included, is final. `second` is how
/// long a second lasts, so the tests needn't wait.
async fn deliver(base: &str, token: &str, chat: &str, text: &str, second: std::time::Duration) -> Result<u16, String> {
    let chat_id = chat.parse::<i64>().map_or_else(|_| json!(chat), |n| json!(n));
    let body = json!({"chat_id": chat_id, "text": text.chars().take(4096).collect::<String>(), "link_preview_options": {"is_disabled": true}});
    let (status, v) = bot_call(base, token, "sendMessage", &body).await?;
    let wait = match status {
        429 => v["parameters"]["retry_after"].as_u64().filter(|s| (1..=MAX_RETRY_WAIT).contains(s)),
        502..=504 => Some(2),
        _ => None,
    };
    let Some(wait) = wait else { return Ok(status) };
    tokio::time::sleep(second * wait as u32).await;
    bot_call(base, token, "sendMessage", &body).await.map(|(status, _)| status)
}

async fn find_chats(base: &str, token: &str) -> Result<Value, String> {
    let (status, v) = bot_call(base, token, "getMe", &json!({})).await?;
    let bot = answer(status, &v)?["username"].as_str().map_or_else(|| "your bot".to_string(), |u| format!("@{}", shown(u, 40)));
    let (status, v) = bot_call(base, token, "getUpdates", &json!({"limit": 100, "timeout": 0, "allowed_updates": UPDATE_KEYS})).await?;
    Ok(json!({"bot": bot, "chats": chats_from(&answer(status, &v)?)}))
}

/// Text a stranger chose, fit to show on one line: no control characters, and none of the invisible or
/// direction-changing ones that can make one line read as another.
fn shown(s: &str, max: usize) -> String {
    s.chars()
        .filter(|c| !c.is_control() && !matches!(c, '\u{AD}' | '\u{61C}' | '\u{200B}'..='\u{200F}' | '\u{2028}'..='\u{202E}' | '\u{2060}'..='\u{2069}' | '\u{FEFF}'))
        .take(max)
        .collect()
}

/// Each distinct chat in a batch of updates, the newest first: its number and a label for the picker. Anyone who can
/// message the bot, or add it to a group, shows up here under a name of their own choosing, so the label leads with the
/// kind of chat as Telegram reports it (which they can't set) and a chat the bot has since been removed from is left out.
fn chats_from(updates: &Value) -> Vec<Value> {
    let mut seen = std::collections::HashSet::new();
    let mut out = Vec::new();
    for u in updates.as_array().into_iter().flatten().rev() {
        for key in UPDATE_KEYS {
            let ev = &u[key];
            let Some(id) = ev["chat"]["id"].as_i64() else { continue };
            if !seen.insert(id) || out.len() >= 20 || matches!(ev["new_chat_member"]["status"].as_str(), Some("left" | "kicked")) {
                continue;
            }
            let c = &ev["chat"];
            let who = c["title"].as_str().map(String::from).unwrap_or_else(|| [c["first_name"].as_str(), c["last_name"].as_str()].into_iter().flatten().collect::<Vec<_>>().join(" "));
            let who = match shown(&who, 48) {
                w if w.trim().is_empty() => c["username"].as_str().map_or_else(|| "(unnamed)".to_string(), |n| format!("@{}", shown(n, 40))),
                w => w,
            };
            let kind = match c["type"].as_str().unwrap_or("chat") {
                "private" => "PRIVATE".to_string(),
                "group" | "supergroup" => "GROUP".to_string(),
                "channel" => "CHANNEL".to_string(),
                other => shown(other, 12).to_uppercase(),
            };
            out.push(json!({"id": id.to_string(), "label": format!("{kind} · {who}")}));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn webhooks_are_checked_and_masked() {
        let ok = "https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz_-ABCDEF";
        assert!(webhook_ok(ok) && webhook_ok(&ok.replace("discord.com", "ptb.discord.com")));
        for bad in ["http://discord.com/api/webhooks/123456/abcdefghijklmnopqrstuvwxyz", "https://evil.com/api/webhooks/123456/abcdefghijklmnopqrstuvwxyz", "https://discord.com/api/webhooks/12/short", "https://discord.com.evil.com/api/webhooks/123456/abcdefghijklmnopqrstuvwxyz"] {
            assert!(!webhook_ok(bad), "{bad}");
        }
        assert_eq!(masked(ok), "https://discord.com/api/webhooks/123456789012345678/••••••");
    }

    const TOKEN: &str = "123456789:AAH-secretSECRETsecret_SECRETsecret12";
    const OTHER: &str = "987654321:BBJ-otherOTHERother_OTHERother3456789";

    #[test]
    fn tokens_and_chats_are_checked_and_masked() {
        assert!(token_ok(TOKEN));
        for bad in ["", "123456789", "123456789:short", "abc:AAH-secretSECRETsecret_SECRETsecret12", "123456789:AAH-secretSECRETsecret_SECRETsecret12/../x", "123456789:AAH-secretSECRETsecret_SECRETsecret12?x=1", "https://api.telegram.org/bot123456789:AAH-secretSECRETsecret_SECRETsecret12", "123456789:AAH-secretSECRETsecret_SECRETsecret12\n"] {
            assert!(!token_ok(bad), "{bad:?}");
        }
        for ok in ["-1001234567890", "123456789", "@oni_alerts", "123456789012345678"] {
            assert!(chat_ok(ok), "{ok}");
        }
        for bad in ["", "-", "12a", "@a", "@1abcdef", "oni_alerts", "@oni alerts", "-100 1", "@oni_alerts/../x", "1234567890123456789", "-1234567890123456789", "@oni_alerts\n"] {
            assert!(!chat_ok(bad), "{bad:?}");
        }
        let m = chat_masked(TOKEN, "-1001234567890");
        assert_eq!(m, "123456789:•••••• → -1001234567890");
        assert!(!m.contains("secret"));
        assert!(name_ok("alerts-2_x") && !name_ok("") && !name_ok("a b") && !name_ok(&"a".repeat(33)));
    }

    #[test]
    fn chats_come_from_updates_newest_first_without_repeats() {
        let updates = json!([
            {"update_id": 1, "message": {"chat": {"id": 42, "type": "private", "first_name": "Ann", "last_name": "Lee", "username": "ann"}}},
            {"update_id": 2, "my_chat_member": {"chat": {"id": -1001, "type": "supergroup", "title": "Ops\u{7}room"}, "new_chat_member": {"status": "administrator"}}},
            {"update_id": 3, "message": {"chat": {"id": 42, "type": "private", "first_name": "Ann"}}},
            {"update_id": 4, "channel_post": {"chat": {"id": -1002, "type": "channel", "username": "oni_news"}}},
            {"update_id": 5, "callback_query": {"id": "x"}},
        ]);
        let got: Vec<(String, String)> = chats_from(&updates).iter().map(|c| (c["id"].as_str().unwrap().into(), c["label"].as_str().unwrap().into())).collect();
        assert_eq!(got, [("-1002".into(), "CHANNEL · @oni_news".into()), ("42".into(), "PRIVATE · Ann".into()), ("-1001".into(), "GROUP · Opsroom".into())]);
        assert!(chats_from(&Value::Null).is_empty() && chats_from(&json!([])).is_empty());
    }

    #[test]
    fn a_stranger_cannot_dress_a_chat_up_as_another_kind() {
        // anyone who can message the bot picks the name they appear under; the kind is Telegram's own and comes first
        let spoof = "Ops \u{202E}(GROUP)\u{202C}\u{200B} team\u{2028}GROUP · Ops";
        let updates = json!([{"update_id": 1, "message": {"chat": {"id": 666, "type": "private", "first_name": spoof}}}]);
        let label = chats_from(&updates)[0]["label"].as_str().unwrap().to_string();
        assert!(label.starts_with("PRIVATE · Ops "), "{label}");
        assert!(!label.chars().any(|c| matches!(c, '\u{202E}' | '\u{202C}' | '\u{200B}' | '\u{2028}')), "{label:?}");
        assert_eq!(chats_from(&json!([{"update_id": 1, "message": {"chat": {"id": 1, "type": "private", "first_name": "\u{200B}"}}}]))[0]["label"], "PRIVATE · (unnamed)");
        assert!(chats_from(&json!([{"update_id": 1, "message": {"chat": {"id": 1, "type": "group", "title": "x".repeat(500)}}}]))[0]["label"].as_str().unwrap().chars().count() < 70);
    }

    #[test]
    fn a_chat_the_bot_was_removed_from_is_not_offered() {
        let updates = json!([
            {"update_id": 1, "message": {"chat": {"id": -1001, "type": "group", "title": "Old"}}},
            {"update_id": 2, "my_chat_member": {"chat": {"id": -1001, "type": "group", "title": "Old"}, "new_chat_member": {"status": "kicked"}}},
            {"update_id": 3, "my_chat_member": {"chat": {"id": 42, "type": "private", "first_name": "Ann"}, "new_chat_member": {"status": "kicked"}}},
            {"update_id": 4, "my_chat_member": {"chat": {"id": -1002, "type": "group", "title": "Left"}, "new_chat_member": {"status": "left"}}},
            {"update_id": 5, "my_chat_member": {"chat": {"id": -1002, "type": "group", "title": "Left"}, "new_chat_member": {"status": "member"}}},
            {"update_id": 6, "message": {"chat": {"id": 7, "type": "private", "first_name": "Bo"}}},
        ]);
        let ids: Vec<String> = chats_from(&updates).iter().map(|c| c["id"].as_str().unwrap().into()).collect();
        assert_eq!(ids, ["7", "-1002"]); // -1002 was left, then re-joined; the newest word on each chat wins
    }

    fn path(dir: &tempfile::TempDir) -> PathBuf {
        dir.path().join("telegram.json")
    }

    #[test]
    fn chats_are_saved_masked_replaced_and_removed() {
        let dir = tempfile::tempdir().unwrap();
        let p = path(&dir);
        assert!(chats_masked(&p).is_empty());
        add_chat(&p, "  Alerts ", &format!(" {TOKEN}\n"), " -1001 ").unwrap();
        add_chat(&p, "ops", OTHER, "@oni_ops").unwrap();
        let shown = chats_masked(&p);
        assert_eq!(shown["alerts"], "123456789:•••••• → -1001");
        assert_eq!(shown["ops"], "987654321:•••••• → @oni_ops");
        assert!(!serde_json::to_string(&shown).unwrap().contains("ecret") && !serde_json::to_string(&shown).unwrap().contains("ther"));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(std::fs::metadata(&p).unwrap().permissions().mode() & 0o777, 0o600);
        }
        add_chat(&p, "alerts", OTHER, "42").unwrap(); // the same name is replaced, not doubled
        let saved = load_chats(&p).unwrap();
        assert_eq!((saved.len(), entry(&saved["alerts"])), (2, Some((OTHER, "42"))));
        remove_chat(&p, "alerts").unwrap();
        remove_chat(&p, "never-there").unwrap();
        assert_eq!(load_chats(&p).unwrap().keys().collect::<Vec<_>>(), ["ops"]);
    }

    #[test]
    fn a_bad_chat_is_refused_before_anything_is_written() {
        let dir = tempfile::tempdir().unwrap();
        let p = path(&dir);
        for (name, token, chat, says) in [("", TOKEN, "-100", "Name it"), ("a b", TOKEN, "-100", "Name it"), ("ok", "nope", "-100", "bot token"), ("ok", TOKEN, "oni", "A chat is"), ("ok", TOKEN, "@a", "A chat is")] {
            assert!(add_chat(&p, name, token, chat).unwrap_err().contains(says), "{name} {chat}");
        }
        assert!(!p.exists());
    }

    #[test]
    fn there_are_at_most_25_chats_but_one_can_always_be_replaced() {
        let dir = tempfile::tempdir().unwrap();
        let p = path(&dir);
        for i in 0..MAX_CHATS {
            add_chat(&p, &format!("c{i}"), TOKEN, "-100").unwrap();
        }
        assert!(add_chat(&p, "one-more", TOKEN, "-100").unwrap_err().contains("limit"));
        add_chat(&p, "c3", OTHER, "-200").unwrap();
        assert_eq!(load_chats(&p).unwrap().len(), MAX_CHATS);
    }

    #[test]
    fn a_damaged_file_is_never_written_over() {
        let dir = tempfile::tempdir().unwrap();
        let p = path(&dir);
        add_chat(&p, "alerts", TOKEN, "-100").unwrap();
        for junk in ["{\"alerts\": {\"token\": ", "[1, 2]", "\"text\"", "null"] {
            std::fs::write(&p, junk).unwrap();
            assert!(add_chat(&p, "new", OTHER, "-200").unwrap_err().contains("isn't a table of chats"), "{junk}");
            assert!(remove_chat(&p, "alerts").is_err());
            assert_eq!(std::fs::read_to_string(&p).unwrap(), junk, "left exactly as found");
            assert!(chats_masked(&p).is_empty()); // and the rest of the console still loads
        }
        std::fs::write(&p, "  \n").unwrap(); // a file emptied out is just empty
        add_chat(&p, "new", OTHER, "-200").unwrap();
    }

    #[test]
    fn an_entry_that_is_not_whole_is_flagged_and_never_used() {
        let dir = tempfile::tempdir().unwrap();
        let p = path(&dir);
        let evil = "123456789:AAH-secretSECRETsecret_SECRETsecret12/../../botX/sendMessage?";
        std::fs::write(&p, json!({"good": {"token": TOKEN, "chat": "-100"}, "evil": {"token": evil, "chat": "-100"}, "nochat": {"token": TOKEN}, "weird": 7}).to_string()).unwrap();
        let shown = chats_masked(&p);
        assert_eq!(shown["good"], "123456789:•••••• → -100");
        for k in ["evil", "nochat", "weird"] {
            assert_eq!(shown[k], "damaged: add it again", "{k}");
        }
        let c = load_chats(&p).unwrap();
        assert!(entry(&c["good"]).is_some() && entry(&c["evil"]).is_none() && entry(&c["nochat"]).is_none() && entry(&c["weird"]).is_none());
    }

    #[test]
    fn saves_from_many_threads_all_land() {
        let dir = tempfile::tempdir().unwrap();
        let p = std::sync::Arc::new(path(&dir));
        let threads: Vec<_> = (0..12).map(|i| { let p = p.clone(); std::thread::spawn(move || add_chat(&p, &format!("c{i}"), TOKEN, "-100").unwrap()) }).collect();
        threads.into_iter().for_each(|t| t.join().unwrap());
        assert_eq!(load_chats(&p).unwrap().len(), 12);
    }

    use axum::{extract::Path as UrlPath, routing::post, Json, Router};
    use std::sync::Arc;
    use std::time::Duration;

    type Log = Arc<parking_lot::Mutex<Vec<(String, Value)>>>;

    /// A stand-in for the Bot API on a local port. Each method answers with its replies in turn (the last one repeats),
    /// and everything it was asked is kept.
    async fn fake(replies: Vec<(&'static str, Vec<(u16, Value)>)>) -> (String, Log) {
        let (log, replies) = (Log::default(), Arc::new(replies));
        let app = Router::new().route("/{*rest}", post({
            let log = log.clone();
            move |UrlPath(rest): UrlPath<String>, Json(body): Json<Value>| {
                let (log, replies) = (log.clone(), replies.clone());
                async move {
                    let turn = log.lock().iter().filter(|(r, _)| r == &rest).count();
                    log.lock().push((rest.clone(), body));
                    let (code, v) = replies.iter().find(|(m, _)| rest.ends_with(m)).map_or((404, json!({})), |(_, r)| r[turn.min(r.len() - 1)].clone());
                    (axum::http::StatusCode::from_u16(code).unwrap(), Json(v))
                }
            }
        }));
        let l = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}", l.local_addr().unwrap());
        tokio::spawn(async move { axum::serve(l, app).await.unwrap() });
        (base, log)
    }

    const MS: Duration = Duration::from_millis(1);

    #[tokio::test]
    async fn a_message_goes_out_as_plain_text_to_the_chat() {
        let (base, log) = fake(vec![("sendMessage", vec![(200, json!({"ok": true, "result": {}}))])]).await;
        assert_eq!(send_message(&base, TOKEN, "-1001234567890", &"x".repeat(5000)).await, Ok(200));
        assert_eq!(send_message(&base, TOKEN, "@oni_alerts", "hi").await, Ok(200));
        let log = log.lock();
        assert_eq!(log[0].0, format!("bot{TOKEN}/sendMessage"));
        assert_eq!(log[0].1["chat_id"], json!(-1001234567890_i64));
        assert_eq!(log[0].1["text"].as_str().unwrap().len(), 4096);
        assert_eq!(log[0].1["link_preview_options"], json!({"is_disabled": true}));
        assert!(log[0].1.get("parse_mode").is_none());
        assert_eq!(log[1].1["chat_id"], json!("@oni_alerts"));
    }

    #[tokio::test]
    async fn a_refusal_comes_back_as_its_status_and_is_not_retried() {
        for code in [400, 401, 403, 404] {
            let (base, log) = fake(vec![("sendMessage", vec![(code, json!({"ok": false, "description": "Forbidden: bot was kicked from the group chat"}))])]).await;
            assert_eq!(deliver(&base, TOKEN, "-100", "hi", MS).await, Ok(code));
            assert_eq!(log.lock().len(), 1, "{code}");
        }
    }

    #[tokio::test]
    async fn a_flood_limit_waits_as_told_once_and_a_gateway_hiccup_once() {
        let limited = |after: Value| json!({"ok": false, "error_code": 429, "parameters": after});
        let ok = json!({"ok": true, "result": {}});
        // told to wait 1 second: waits, tries again, gets through
        let (base, log) = fake(vec![("sendMessage", vec![(429, limited(json!({"retry_after": 1}))), (200, ok.clone())])]).await;
        assert_eq!(deliver(&base, TOKEN, "-100", "hi", MS).await, Ok(200));
        let asked = log.lock();
        assert_eq!(asked.len(), 2);
        assert_eq!(asked[0].1, asked[1].1, "the same message, once more");
        drop(asked);
        // told to wait longer than it will, or nothing at all: the 429 is the answer
        for after in [json!({"retry_after": 31}), json!({"retry_after": 0}), json!({}), json!(null)] {
            let (base, log) = fake(vec![("sendMessage", vec![(429, limited(after.clone())), (200, ok.clone())])]).await;
            assert_eq!(deliver(&base, TOKEN, "-100", "hi", MS).await, Ok(429), "{after}");
            assert_eq!(log.lock().len(), 1);
        }
        // limited again after waiting: that answer stands, there is no third try
        let (base, log) = fake(vec![("sendMessage", vec![(429, limited(json!({"retry_after": 1})))])]).await;
        assert_eq!(deliver(&base, TOKEN, "-100", "hi", MS).await, Ok(429));
        assert_eq!(log.lock().len(), 2);
        // a gateway error and then a good answer; a gateway that stays down is reported after the one retry
        for code in [502, 503, 504] {
            let (base, log) = fake(vec![("sendMessage", vec![(code, json!({})), (200, ok.clone())])]).await;
            assert_eq!(deliver(&base, TOKEN, "-100", "hi", MS).await, Ok(200), "{code}");
            assert_eq!(log.lock().len(), 2);
        }
        let (base, log) = fake(vec![("sendMessage", vec![(503, json!({}))])]).await;
        assert_eq!(deliver(&base, TOKEN, "-100", "hi", MS).await, Ok(503));
        assert_eq!(log.lock().len(), 2);
        let (base, log) = fake(vec![("sendMessage", vec![(500, json!({})), (200, ok)])]).await;
        assert_eq!(deliver(&base, TOKEN, "-100", "hi", MS).await, Ok(500), "an ordinary server error isn't known to be undelivered");
        assert_eq!(log.lock().len(), 1);
    }

    #[tokio::test]
    async fn an_error_never_carries_the_token() {
        let port = std::net::TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port(); // free, and nothing listens on it
        let base = format!("http://127.0.0.1:{port}");
        for e in [send_message(&base, TOKEN, "-100", "hi").await.unwrap_err(), find_chats(&base, TOKEN).await.unwrap_err()] {
            assert!(!e.contains("secret") && !e.contains(TOKEN) && !e.contains("/bot"), "{e}");
        }
    }

    #[tokio::test]
    async fn nothing_but_a_token_ever_reaches_a_url() {
        let (base, log) = fake(vec![("sendMessage", vec![(200, json!({"ok": true}))]), ("getMe", vec![(200, json!({"ok": true}))])]).await;
        for bad in ["123456789:AAH-secretSECRETsecret_SECRETsecret12/../../botX", "123456789:AAH-secretSECRETsecret_SECRETsecret12?x=1", "x", "", "123456789:AAH-secretSECRETsecret_SECRETsecret12 "] {
            assert_eq!(send_message(&base, bad, "-100", "hi").await.unwrap_err(), BAD_TOKEN);
            assert_eq!(find_chats(&base, bad).await.unwrap_err(), BAD_TOKEN);
        }
        assert!(log.lock().is_empty(), "no request was made");
    }

    #[tokio::test]
    async fn a_reply_too_big_to_trust_is_not_read() {
        let huge = json!({"ok": true, "result": "x".repeat(MAX_REPLY + 1)});
        let (base, _) = fake(vec![("getMe", vec![(200, huge)])]).await;
        assert_eq!(find_chats(&base, TOKEN).await.unwrap_err(), "Telegram's reply was too big to read.");
    }

    #[tokio::test]
    async fn chats_are_found_from_what_the_bot_has_heard() {
        let updates = json!([{"update_id": 1, "message": {"chat": {"id": -1001, "type": "group", "title": "Ops"}}}]);
        let (base, log) = fake(vec![("getMe", vec![(200, json!({"ok": true, "result": {"username": "oni_bot"}}))]), ("getUpdates", vec![(200, json!({"ok": true, "result": updates}))])]).await;
        let got = find_chats(&base, TOKEN).await.unwrap();
        assert_eq!(got, json!({"bot": "@oni_bot", "chats": [{"id": "-1001", "label": "GROUP · Ops"}]}));
        let log = log.lock();
        assert!(log[1].1.get("offset").is_none(), "reading must not confirm the updates away");
    }

    #[tokio::test]
    async fn a_bad_token_or_something_else_reading_says_what_to_do() {
        assert_eq!(find_chats("http://unused", "nope").await.unwrap_err(), BAD_TOKEN);
        let (base, _) = fake(vec![("getMe", vec![(401, json!({"ok": false, "description": "Unauthorized"}))])]).await;
        assert!(find_chats(&base, TOKEN).await.unwrap_err().contains("refused that token"));
        let (base, _) = fake(vec![("getMe", vec![(200, json!({"ok": true, "result": {}}))]), ("getUpdates", vec![(409, json!({"ok": false, "description": "Conflict: terminated by other getUpdates request"}))])]).await;
        let e = find_chats(&base, TOKEN).await.unwrap_err();
        assert!(e.contains("webhook, or another program") && e.contains("Type the chat ID"), "{e}");
        let (base, _) = fake(vec![("getMe", vec![(502, json!({"ok": false, "description": "Bad Gateway"}))])]).await;
        assert_eq!(find_chats(&base, TOKEN).await.unwrap_err(), "Telegram answered 502: Bad Gateway");
    }
}
