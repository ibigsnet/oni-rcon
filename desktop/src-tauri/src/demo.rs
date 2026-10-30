//! Simulated Reclaimer servers for the demo: the real WebSocket protocol, fake players and events, and a pretend game
//! box whose health reports make F7 worth looking at.
use futures_util::{SinkExt, StreamExt};
use parking_lot::Mutex;
use rand::seq::IndexedRandom;
use rand::Rng;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::net::TcpListener;
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;

const CALLSIGNS: &[&str] = &["Bravo", "Echo 4", "Viper", "Nomad", "Ghost", "Rook", "Sable", "Kestrel", "Juno", "Tango", "Onyx",
    "Hollow", "Vesper", "Mako", "Quill", "Atlas", "Cinder", "Lark", "Drift", "Static"];
pub const MAPS: &[&str] = &["guardian", "the_pit", "narrows", "construct", "valhalla", "sandtrap", "high_ground", "last_resort",
    "isolation", "epitaph", "foundry", "standoff", "snowbound", "heretic"];
const MODES: &[&str] = &["Slayer", "Team Slayer", "Capture the Flag", "Oddball", "King of the Hill", "Territories", "Assault", "SWAT"];
const WEAPONS: &[&str] = &["battle rifle", "sniper rifle", "shotgun", "energy sword", "frag grenade", "rocket launcher", "melee"];
const CHAT: &[&str] = &["gg", "nice shot", "who has sniper?", "lag on blue base", "rematch?", "ez", "push top mid",
    "anyone up for MLG after?", "wp all"];
const CALLS: &[&str] = &["admin someone is spawn camping", "is there a mod on? red is hacking"];
pub const SERVERS: &[(&str, i64, usize)] = &[("Demo Ops | Slayer", 16, 9), ("Demo Ops | Big Team Battle", 32, 17), ("Demo Ops | MLG 4v4", 8, 6)];

type Clients = Vec<(u64, mpsc::UnboundedSender<Message>)>;

pub struct Fake {
    password: String,
    clients: Clients,
    next_client: u64,
    engine: i64,
    skill: HashMap<i64, f64>,
    content: Option<PathBuf>,
    pub status: Value,
    pub players: Vec<Value>,
    bans: Value,
    allowed: Vec<Value>,
    kicked: Vec<(String, String)>, // (player ID, name) until a pardon
    vote: Value,
    admins: Vec<Value>, // {player_id, name, source}: the "dedicated.toml" ones can't be removed over RCON
    vote_at: Instant,
    hot: (i64, Instant),
}

fn hex128() -> String {
    format!("{:032x}", rand::rng().random::<u128>())
}

impl Fake {
    fn new(name: &str, password: &str, max: i64, crowd: usize, content: Option<PathBuf>) -> Self {
        let mut rng = rand::rng();
        let mut f = Fake {
            password: password.into(),
            clients: vec![],
            next_client: 0,
            engine: 0,
            skill: HashMap::new(),
            content,
            status: json!({"name": name, "address": "203.0.113.7:49176", "map": MAPS.choose(&mut rng).unwrap(), "mode": "Team Slayer",
                "phase": "in_game", "max_players": max, "players": 0, "password_required": false, "max_ping": 0, "hidden": false,
                "anti_cheat": "enforce", "anti_cheat_active": true, "block_vpn": true, "text_chat": true, "voice": true,
                "vote": null, "votes": true, "next": null, "playlist_vote": true}),
            players: vec![],
            bans: json!({"players": [{"id": format!("9f3a{}", "0".repeat(28)), "name": "Griefer", "reason": "team killing", "expires": null, "banned_by": "Admin"}],
                "ips": [{"ip": "198.51.100.0/24", "reason": "ban evasion", "expires": crate::util::now() + 86400.0 * 6.0}], "devices": []}),
            allowed: vec![json!({"target": "192.0.2.44", "note": "mobile hotspot"})],
            kicked: Vec::new(),
            vote: Value::Null,
            admins: ["Overseer", "Bravo"].iter().map(|n| json!({"player_id": hex128(), "name": n, "source": "dedicated.toml"})).collect(),
            vote_at: Instant::now(),
            hot: (0, Instant::now()),
        };
        let names: Vec<&str> = CALLSIGNS.choose_multiple(&mut rng, crowd).copied().collect();
        f.players = names.into_iter().map(|n| f.player(n)).collect();
        f.renumber();
        f
    }

    fn player(&mut self, name: &str) -> Value {
        let mut rng = rand::rng();
        self.engine += 1;
        // a few players carry the lobby, for sprees: a lognormal-ish skill
        let g: f64 = (0..6).map(|_| rng.random::<f64>()).sum::<f64>() - 3.0;
        self.skill.insert(self.engine, (0.7 * g).exp());
        let (k, d): (i64, i64) = (rng.random_range(0..=25), rng.random_range(0..=18));
        let admin = self.admins.iter().find(|a| a["name"] == name).map(|a| a["player_id"].clone()); // the same player ID each time they join
        json!({"number": 0, "name": name, "player_id": admin.clone().unwrap_or_else(|| json!(hex128())), "address": format!("203.0.113.{}", rng.random_range(2..=250)),
            "admin": admin.is_some(), "muted": false, "team": *["red", "blue"].choose(&mut rng).unwrap(), "score": k, "kills": k,
            "deaths": d, "service_tag": name.chars().take(4).collect::<String>().to_uppercase(), "alive": rng.random::<f64>() > 0.2,
            "health": (rng.random::<f64>() * 100.0).round() / 100.0, "shields": (rng.random::<f64>() * 100.0).round() / 100.0,
            "seconds_since_last_death": rng.random_range(3..=300), "engine_id": self.engine, "guest_players": []})
    }

    fn renumber(&mut self) {
        for (i, p) in self.players.iter_mut().enumerate() {
            p["number"] = json!(i + 1);
        }
        self.status["players"] = json!(self.players.len());
    }

    fn installed(&self, suffix: &str) -> Vec<Value> {
        let Some(dir) = self.content.as_ref().filter(|d| d.is_dir()) else { return vec![] };
        let mut out = vec![];
        let mut stack = vec![dir.clone()];
        while let Some(d) = stack.pop() {
            for e in std::fs::read_dir(&d).into_iter().flatten().flatten() {
                let p = e.path();
                if p.is_dir() {
                    stack.push(p);
                } else if p.to_string_lossy().ends_with(suffix) {
                    let stem = p.file_stem().unwrap().to_string_lossy().to_string();
                    let name: String = stem.split(['_', '-']).map(|w| {
                        let mut c = w.chars();
                        c.next().map(|f| f.to_uppercase().collect::<String>() + c.as_str()).unwrap_or_default()
                    }).collect::<Vec<_>>().join(" ");
                    out.push(json!({"kind": if suffix == ".map" { "map" } else { "mode" }, "name": name, "reference": stem}));
                }
            }
        }
        out.sort_by_key(|v| v["reference"].as_str().unwrap_or_default().to_string());
        out
    }

    fn find(&mut self, who: &str) -> Option<usize> {
        self.renumber();
        self.players.iter().position(|p| {
            who == format!("#{}", p["number"]) || who == p["player_id"].as_str().unwrap_or_default()
                || p["name"].as_str().unwrap_or_default().to_lowercase() == who.to_lowercase()
        })
    }

    fn emit(&mut self, event: &str, fields: Value) {
        let mut msg = json!({"type": "event", "event": event, "time": crate::util::now() as i64});
        if let (Some(m), Some(f)) = (msg.as_object_mut(), fields.as_object()) {
            for (k, v) in f {
                m.insert(k.clone(), v.clone());
            }
        }
        let text = Message::text(msg.to_string());
        self.clients.retain(|(_, c)| c.send(text.clone()).is_ok());
    }

    fn command(&mut self, cmd: &str, args: &[String], by: &str) -> Value {
        let ok = |text: String, data: Option<Value>| {
            let mut r = json!({"ok": true, "text": text});
            if let Some(d) = data {
                r["data"] = d;
            }
            r
        };
        let no = |text: String| json!({"ok": false, "text": text});
        let mut rng = rand::rng();
        self.renumber();
        let a0 = args.first().cloned().unwrap_or_default();
        let rest = args.iter().skip(1).cloned().collect::<Vec<_>>().join(" ");
        match cmd {
            "players" => ok("players: see data".into(), Some(json!({"count": self.players.len(), "max_players": self.status["max_players"], "players": self.players}))),
            "status" => ok("status: see data".into(), Some(self.status.clone())),
            "maps" => {
                let mut e: Vec<Value> = MAPS.iter().map(|m| json!({"kind": "map", "name": titled(m), "reference": m})).collect();
                e.extend(self.installed(".map"));
                ok("maps: see data".into(), Some(json!({"entries": e})))
            }
            "modes" => {
                let mut e: Vec<Value> = MODES.iter().map(|m| json!({"kind": "mode", "name": m, "reference": m.to_lowercase().replace(' ', "_")})).collect();
                e.extend(self.installed(".gt"));
                e.extend(self.installed(".json")); // NEW GAME TYPE's, listed at once
                ok("modes: see data".into(), Some(json!({"entries": e})))
            }
            "bans" => ok("bans: see data".into(), Some(self.bans.clone())),
            "vpn" if args.is_empty() => {
                // the 0.9.22 shape: one list fresh, one a week and a half old that failed its last fetch
                let t = crate::util::now() as i64;
                let lists = json!({"refreshed_at": t - 600, "sources": [
                    {"list": "x4bnet", "ranges": 37958, "asns": 0, "fetched_at": t - 600, "problem": null},
                    {"list": "hosting-asns", "ranges": 3854, "asns": 212, "fetched_at": t - 10 * 86400, "problem": "HTTP 404 from the list's address; kept the last copy"}],
                    "table": {"table": "ip2asn", "rows": 512340, "fetched_at": t - 2 * 86400, "asns": 212, "announced": 3854, "blocks": 3854}});
                ok("vpn: see data".into(), Some(json!({"allowed": self.allowed, "block_vpn": true, "ranges": 41812, "fail_closed": false, "lists": lists})))
            }
            "vpn" if a0.starts_with("45.") => ok(format!("{a0} is refused: it is on a VPN list."),
                Some(json!({"address": a0, "local": false, "listed": true, "list": "x4bnet", "allowed": false, "refused": true, "fail_closed": false}))),
            "vpn" => ok(format!("{a0} doesn't look like a VPN."),
                Some(json!({"address": a0, "local": false, "listed": false, "list": null, "allowed": false, "refused": false, "fail_closed": false}))),
            "vote" => ok("vote: see data".into(), Some(json!({"vote": self.vote, "playlist": null, "subjects": ["kick", "endround", "endgame", "shuffle", "playlist"]}))),
            "nextmap" if args.is_empty() => {
                let rot: Vec<Value> = MAPS[..6].iter().map(|m| json!({"map": m, "mode": MODES.choose(&mut rng).unwrap()})).collect();
                ok("nextmap: see data".into(), Some(json!({"next": self.status["next"], "rotation": rot})))
            }
            "say" => {
                self.emit("chat", json!({"channel": "server", "text": args.join(" ")}));
                ok("Said.".into(), None)
            }
            "tell" | "kick" | "ban" | "mute" | "unmute" | "team" | "vpnallow" => {
                let Some(i) = (!args.is_empty()).then(|| self.find(&a0)).flatten() else {
                    if (cmd == "ban" || cmd == "vpnallow") && !args.is_empty() {
                        let ip = a0.contains('.');
                        if cmd == "ban" {
                            let list = if ip { "ips" } else { "players" };
                            let key = if ip { "ip" } else { "id" };
                            self.bans[list].as_array_mut().unwrap().push(json!({key: a0, "reason": rest}));
                        } else {
                            self.allowed.push(json!({"target": a0, "note": rest}));
                        }
                        return ok(format!("{cmd} {a0} done."), None);
                    }
                    return no(format!("No player is named '{a0}'; players lists them."));
                };
                let name = self.players[i]["name"].as_str().unwrap_or_default().to_string();
                match cmd {
                    "tell" => ok(format!("Told {name}."), None),
                    "kick" | "ban" => {
                        let p = self.players.remove(i);
                        if cmd == "ban" {
                            self.bans["players"].as_array_mut().unwrap().push(json!({"id": p["player_id"], "name": name, "reason": rest, "banned_by": by, "expires": null}));
                            self.emit(cmd, json!({"player": name, "by": by, "reason": rest}));
                        } else {
                            let id = p["player_id"].as_str().unwrap_or_default().to_string();
                            self.emit(cmd, json!({"name": name, "id": id, "by": by, "reason": rest, "seconds": 600}));
                            self.kicked.push((id, name.clone()));
                        }
                        ok(format!("{name} was {}ed by {by}.", if cmd == "ban" { "bann" } else { "kick" }), None)
                    }
                    "mute" | "unmute" => {
                        self.players[i]["muted"] = json!(cmd == "mute");
                        self.emit(cmd, json!({"player": name, "by": by}));
                        ok(format!("{name} was {cmd}d by {by}."), None)
                    }
                    "team" => {
                        if let Some(t) = args.get(1) {
                            self.players[i]["team"] = json!(t);
                        }
                        let t = self.players[i]["team"].as_str().unwrap_or_default().to_string();
                        self.emit("control", json!({"text": format!("{name} moved to {t}.")}));
                        ok(format!("Moving {name} to {t}."), None)
                    }
                    _ => {
                        self.allowed.push(json!({"target": self.players[i]["player_id"], "note": rest}));
                        ok(format!("{name} may join through a VPN."), None)
                    }
                }
            }
            "pardon" => {
                let Some(i) = self.kicked.iter().position(|(id, n)| *id == a0 || *n == a0) else {
                    return no(format!("Nobody kicked here matches '{a0}'."));
                };
                let (_, name) = self.kicked.remove(i);
                self.emit("pardon", json!({"target": a0, "name": [name], "by": by}));
                ok(format!("{name} may rejoin now."), None)
            }
            "unban" | "vpnrevoke" => {
                let matches = |e: &Value| ["id", "ip", "name", "target"].iter().any(|k| e[*k].as_str() == Some(a0.as_str()));
                if cmd == "vpnrevoke" {
                    if let Some(i) = self.allowed.iter().position(matches) {
                        self.allowed.remove(i);
                        return ok(format!("{cmd} {a0} done."), None);
                    }
                } else {
                    for list in ["players", "ips", "devices"] {
                        let l = self.bans[list].as_array_mut().unwrap();
                        if let Some(i) = l.iter().position(matches) {
                            l.remove(i);
                            return ok(format!("{cmd} {a0} done."), None);
                        }
                    }
                }
                no(format!("Nothing matches '{a0}'."))
            }
            "map" | "mode" | "load" | "nextmap" => {
                let joined = args.join(" / ");
                self.status["next"] = json!(joined);
                if cmd != "nextmap" {
                    self.emit("control", json!({"text": format!("Loading {joined}.")}));
                    if (cmd == "map" || cmd == "load") && !args.is_empty() {
                        self.status["map"] = json!(a0);
                    }
                    if cmd == "mode" && !args.is_empty() || cmd == "load" && args.len() > 1 {
                        self.status["mode"] = json!(args.last().unwrap());
                    }
                }
                ok(format!("Accepted: {cmd} {}.", args.join(" ")), None)
            }
            "endround" | "endgame" | "shuffle" | "teamcount" | "passvote" | "cancelvote" | "startvote" => {
                if cmd == "startvote" {
                    self.vote = json!({"subject": if a0.is_empty() { "?" } else { &a0 }, "yes": 0, "no": 0});
                    if let Some(t) = args.get(1) {
                        self.vote["target"] = json!(t);
                    }
                    self.vote_at = Instant::now();
                } else if cmd == "passvote" || cmd == "cancelvote" {
                    self.vote = Value::Null;
                }
                let kind = if cmd.contains("vote") { "vote" } else { "control" };
                self.emit(kind, json!({"text": format!("{cmd} {} by {by}", args.join(" ")).replace("  ", " ")}));
                ok(format!("{cmd} accepted."), None)
            }
            "servername" => {
                if !a0.is_empty() {
                    self.status["name"] = json!(a0);
                }
                let n = self.status["name"].clone();
                ok(n.as_str().unwrap_or_default().into(), Some(json!({"name": n})))
            }
            "password" => {
                self.status["password_required"] = json!(!a0.is_empty());
                ok(if a0.is_empty() { "No password." } else { "Password set." }.into(), Some(json!({"password_required": !a0.is_empty()})))
            }
            "maxping" => {
                let v = if a0.is_empty() || a0 == "off" { 0 } else { a0.parse::<i64>().unwrap_or(0) };
                self.status["max_ping"] = json!(v);
                ok(format!("Max ping {}.", if v == 0 { "off".to_string() } else { v.to_string() }), Some(json!({"max_ping": v})))
            }
            "admins" | "admin" if a0.is_empty() || a0 == "list" => {
                let ids: Vec<&Value> = self.players.iter().map(|p| &p["player_id"]).collect();
                let list: Vec<Value> = self.admins.iter().map(|a| {
                    let mut a = a.clone();
                    a["online"] = json!(ids.contains(&&a["player_id"]));
                    a
                }).collect();
                ok(format!("{} admins: see data", list.len()), Some(json!({"admins": list})))
            }
            "admin" if a0 == "add" || a0 == "remove" => {
                let Some(who) = args.get(1).filter(|w| !w.is_empty()).cloned() else { return no(format!("admin {a0} <player | player ID>")) };
                let found = self.find(&who);
                let id = found.map(|i| self.players[i]["player_id"].clone()).unwrap_or_else(|| json!(who));
                let name = found.map(|i| self.players[i]["name"].clone()).unwrap_or(Value::Null);
                let at = self.admins.iter().position(|a| a["player_id"] == id || a["name"].as_str().is_some_and(|n| n.eq_ignore_ascii_case(&who)));
                let shown = name.as_str().unwrap_or(&who).to_string();
                if a0 == "add" {
                    if at.is_some() {
                        return no(format!("{shown} is already an admin."));
                    }
                    if found.is_none() && !(who.len() >= 32 && who.chars().all(|c| c.is_ascii_hexdigit())) {
                        return no(format!("No player is named '{who}'; give their player ID."));
                    }
                    self.admins.push(json!({"player_id": id, "name": name, "source": "console"}));
                } else {
                    let Some(at) = at else { return no(format!("{shown} isn't an admin.")) };
                    if self.admins[at]["source"] != "console" {
                        return no(format!("{shown} is an admin in dedicated.toml: change it there and restart the server."));
                    }
                    self.admins.remove(at);
                }
                if let Some(i) = found {
                    self.players[i]["admin"] = json!(a0 == "add");
                }
                let text = format!("{shown} {} by {by}.", if a0 == "add" { "made an admin" } else { "is no longer an admin" });
                self.emit("admin", json!({"text": text}));
                ok(text, None)
            }
            "help" => ok("Demo server: status players say tell kick ban unban mute unmute team maps modes map mode load ...".into(), None),
            _ => no(format!("Unknown command '{cmd}'; help lists them.")),
        }
    }

    /// Keeps the feed alive: firefights, chat, votes, and the odd join or leave.
    fn tick(&mut self) {
        let mut rng = rand::rng();
        let alive: Vec<usize> = (0..self.players.len()).filter(|&i| self.players[i]["alive"] == true).collect();
        let roll: f64 = rng.random();
        if roll < 0.62 && alive.len() > 1 {
            let k = self.killer(&alive);
            let kt = self.players[k]["team"].clone();
            let foes: Vec<usize> = alive.iter().copied().filter(|&i| self.players[i]["team"] != kt).collect();
            let others: Vec<usize> = alive.iter().copied().filter(|&i| i != k).collect();
            let v = *if foes.is_empty() { &others } else { &foes }.choose(&mut rng).unwrap();
            let bump = |p: &mut Value, key: &str| p[key] = json!(p[key].as_i64().unwrap_or(0) + 1);
            bump(&mut self.players[k], "kills");
            bump(&mut self.players[k], "score");
            bump(&mut self.players[v], "deaths");
            self.players[k]["shields"] = json!((rng.random::<f64>() * 60.0).round() / 100.0);
            for (key, val) in [("alive", json!(false)), ("health", json!(0.0)), ("shields", json!(0.0)), ("seconds_since_last_death", json!(0))] {
                self.players[v][key] = val;
            }
            self.hot = (self.players[k]["engine_id"].as_i64().unwrap_or(0), Instant::now());
            let (ke, ve) = (self.players[k]["engine_id"].clone(), self.players[v]["engine_id"].clone());
            self.emit("kill", json!({"killer": ke, "victim": ve, "weapon": WEAPONS.choose(&mut rng).unwrap()}));
        } else if roll < 0.88 && !alive.is_empty() {
            let p = &self.players[*alive.choose(&mut rng).unwrap()];
            let team = p["team"].as_str().unwrap_or_default().to_string();
            let ch = if rng.random::<bool>() { "all".to_string() } else { format!("team {team}") };
            let text = if rng.random::<f64>() < 0.015 { CALLS.choose(&mut rng) } else { CHAT.choose(&mut rng) }.unwrap();
            let name = p["name"].clone();
            self.emit("chat", json!({"channel": ch, "name": name, "team": team, "text": text}));
        } else if roll < 0.94 && (self.players.len() as i64) < self.status["max_players"].as_i64().unwrap_or(16) {
            let taken: Vec<String> = self.players.iter().map(|p| p["name"].as_str().unwrap_or_default().to_string()).collect();
            let spare: Vec<&str> = CALLSIGNS.iter().copied().filter(|n| !taken.iter().any(|t| t == n)).collect();
            if let Some(n) = spare.choose(&mut rng) {
                let p = self.player(n);
                self.emit("join", json!({"name": p["name"], "player_id": p["player_id"], "address": p["address"]}));
                self.players.push(p);
            }
        } else if roll < 0.997 && self.players.len() > 2 {
            let i = rng.random_range(0..self.players.len());
            let p = self.players.remove(i);
            self.emit("leave", json!({"name": p["name"], "reason": "quit"}));
        } else {
            let name = alive.choose(&mut rng).map(|&i| self.players[i]["name"].clone()).unwrap_or(json!("?"));
            self.emit("cheat", json!({"name": name, "text": "speed out of range (sample)"}));
        }
        self.vote_tick();
        let alive: Vec<usize> = (0..self.players.len()).filter(|&i| self.players[i]["alive"] == true).collect();
        let n = rng.random_range(0..=3).min(alive.len());
        for &i in alive.choose_multiple(&mut rng, n) {
            let hit: f64 = rng.random_range(0.2..0.9);
            let p = &mut self.players[i];
            let (h, s) = (p["health"].as_f64().unwrap_or(1.0), p["shields"].as_f64().unwrap_or(1.0));
            p["health"] = json!(((h - (hit - s).max(0.0)).max(0.05) * 100.0).round() / 100.0);
            p["shields"] = json!(((s - hit).max(0.0) * 100.0).round() / 100.0);
        }
        for p in self.players.iter_mut() {
            if p["alive"] != true {
                if rng.random::<bool>() {
                    p["alive"] = json!(true);
                    p["health"] = json!(1.0);
                    p["shields"] = json!(1.0);
                }
            } else {
                let (h, s) = (p["health"].as_f64().unwrap_or(1.0), p["shields"].as_f64().unwrap_or(1.0));
                p["shields"] = json!(((s + 0.25).min(1.0) * 100.0).round() / 100.0);
                p["health"] = json!(((h + 0.08).min(1.0) * 100.0).round() / 100.0);
            }
            p["seconds_since_last_death"] = json!(p["seconds_since_last_death"].as_i64().unwrap_or(0) + 2);
        }
        self.renumber();
    }

    fn killer(&self, alive: &[usize]) -> usize {
        let mut rng = rand::rng();
        let (k, at) = self.hot;
        if let Some(&i) = alive.iter().find(|&&i| self.players[i]["engine_id"] == k) {
            if at.elapsed().as_secs_f64() < 3.5 && rng.random::<f64>() < 0.35 {
                return i;
            }
        }
        let weights: Vec<f64> = alive.iter().map(|&i| *self.skill.get(&self.players[i]["engine_id"].as_i64().unwrap_or(0)).unwrap_or(&1.0)).collect();
        let total: f64 = weights.iter().sum();
        let mut r = rng.random::<f64>() * total;
        for (j, w) in weights.iter().enumerate() {
            if r < *w {
                return alive[j];
            }
            r -= w;
        }
        *alive.last().unwrap()
    }

    fn vote_tick(&mut self) {
        let mut rng = rand::rng();
        if self.vote.is_object() {
            self.vote["yes"] = json!(self.vote["yes"].as_i64().unwrap_or(0) + [0, 0, 1, 1, 2].choose(&mut rng).unwrap());
            self.vote["no"] = json!(self.vote["no"].as_i64().unwrap_or(0) + [0, 0, 0, 1].choose(&mut rng).unwrap());
            let need = self.players.len() as i64 / 2 + 1;
            let yes = self.vote["yes"].as_i64().unwrap_or(0);
            if yes >= need || self.vote_at.elapsed().as_secs() > 30 {
                let text = format!("vote to {} {} ({} to {})", self.vote["subject"].as_str().unwrap_or("?"), if yes >= need { "passed" } else { "failed" }, yes, self.vote["no"]);
                self.emit("vote", json!({"text": text}));
                self.vote = Value::Null;
            }
        } else if rng.random::<f64>() < 0.012 && self.players.len() > 2 {
            let pair: Vec<Value> = self.players.choose_multiple(&mut rng, 2).cloned().collect();
            let subject = *["shuffle", "endround", "kick"].choose(&mut rng).unwrap();
            self.vote = json!({"subject": subject, "yes": 1, "no": 0});
            let mut text = format!("called a vote to {subject}");
            if subject == "kick" {
                self.vote["target"] = pair[1]["name"].clone();
                text += &format!(" {}", pair[1]["name"].as_str().unwrap_or_default());
            }
            self.vote_at = Instant::now();
            self.emit("vote", json!({"name": pair[0]["name"], "text": text}));
        }
    }
}

fn titled(s: &str) -> String {
    s.split('_')
        .map(|w| {
            let mut c = w.chars();
            c.next().map(|f| f.to_uppercase().collect::<String>() + c.as_str()).unwrap_or_default()
        })
        .collect::<Vec<_>>()
        .join(" ")
}

async fn handle(fake: Arc<Mutex<Fake>>, stream: tokio::net::TcpStream) {
    let Ok(ws) = tokio_tungstenite::accept_async(stream).await else { return };
    let (mut sink, mut read) = ws.split();
    let Some(Ok(Message::Text(first))) = read.next().await else { return };
    let msg: Value = serde_json::from_str(&first).unwrap_or_default();
    let (good, name) = {
        let f = fake.lock();
        (msg["type"] == "auth" && msg["password"].as_str() == Some(f.password.as_str()), f.status["name"].clone())
    };
    if !good {
        let _ = sink.send(Message::text(json!({"type": "auth", "ok": false, "error": "Wrong password."}).to_string())).await;
        return;
    }
    let _ = sink.send(Message::text(json!({"type": "auth", "ok": true, "protocol": 1, "server": name, "version": "0.9.7-demo"}).to_string())).await;
    let (tx, mut rx) = mpsc::unbounded_channel::<Message>();
    let id = {
        let mut f = fake.lock();
        f.next_client += 1;
        let id = f.next_client;
        f.clients.push((id, tx.clone()));
        id
    };
    let writer = tokio::spawn(async move {
        while let Some(m) = rx.recv().await {
            let close = matches!(m, Message::Close(_));
            if sink.send(m).await.is_err() || close {
                break;
            }
        }
    });
    while let Some(Ok(m)) = read.next().await {
        let Message::Text(t) = m else { continue };
        let msg: Value = serde_json::from_str(&t).unwrap_or_default();
        let command = msg["command"].as_str().unwrap_or_default().to_string();
        let mut parts = command.split_whitespace();
        let cmd = parts.next().unwrap_or_default().to_string();
        let mut args: Vec<String> = msg["args"].as_array().map(|a| a.iter().map(|x| x.as_str().map(str::to_string).unwrap_or_else(|| x.to_string())).collect()).unwrap_or_default();
        if args.is_empty() {
            args = parts.map(str::to_string).collect();
        }
        let by = msg["by"].as_str().filter(|s| !s.is_empty()).unwrap_or("an admin").to_string();
        let mut reply = fake.lock().command(&cmd, &args, &by);
        reply["type"] = json!("reply");
        reply["id"] = msg["id"].clone();
        if tx.send(Message::text(reply.to_string())).is_err() {
            break;
        }
    }
    fake.lock().clients.retain(|(c, _)| *c != id);
    writer.abort();
}

fn iso(t: f64) -> String {
    let d = chrono::DateTime::from_timestamp(t as i64, ((t.fract()) * 1e9) as u32).unwrap_or_default();
    d.format("%Y-%m-%dT%H:%M:%S%.9fZ").to_string()
}

/// A pretend game box: the report a health_cmd would print for each fake server, and a timeline that makes the HEALTH
/// tab worth looking at: a SIGNATURE crash on the first server, a BARE one on the second, then memory running short.
pub struct FakeHost {
    pub fakes: Vec<(u16, Arc<Mutex<Fake>>)>,
    logs: Mutex<HashMap<u16, Vec<String>>>,
    started: Mutex<HashMap<u16, f64>>,
    mem: Mutex<(i64, i64, i64, i64)>, // avail, swap used, swap total, oom
}

impl FakeHost {
    pub fn report(&self, port: u16) -> String {
        let n = crate::util::now() as i64;
        let (avail, used, total, oom) = *self.mem.lock();
        let Some((_, fake)) = self.fakes.iter().find(|(p, _)| *p == port) else { return String::new() };
        let name = fake.lock().status["name"].as_str().unwrap_or_default().to_string();
        let clock = chrono::DateTime::from_timestamp(n, 0).unwrap_or_default().format("%H:%M:%S");
        let mut lines = vec![
            format!("host now={n} clock={clock} mem_available_mb={avail} swap_used_mb={used} swap_total_mb={total} oom_kill={oom}"),
            format!("container started={} finished=0001-01-01T00:00:00Z oom_killed=false exit_code=0 restarts=0", iso(self.started.lock()[&port])),
            format!("{} [{name}] Bob connected from 203.0.113.9 (player ID {}, ping 77 ms).", iso((n - 3600) as f64), "ab12".repeat(16)),
        ];
        lines.push(format!("{} [{name}] Network upload over the last minute: 12 MB", iso(n as f64)));
        if self.fakes.last().is_some_and(|(p, _)| *p == port) {
            // the last server is the laggy one: a report for each of the last 12 minutes, on the minute so they repeat
            for k in 0..12 {
                let ts = (n / 60 * 60 - k * 60) as f64;
                let avg = 22 + (ts as i64 / 60 * 7) % 18;
                lines.push(format!("{} Server falling behind: under 54 frames a second for 58 of the last 58 seconds ({avg} on average, longest frame {} ms) with {} players. Each player gets one update a frame", iso(ts), 90 + avg * 3, 60 - k));
            }
        }
        lines.extend(self.logs.lock().get(&port).cloned().unwrap_or_default());
        lines.join("\n")
    }

    pub fn service(&self, unit: &str) -> String {
        let t = crate::util::now() as i64;
        let stamp = |ago: i64| chrono::DateTime::from_timestamp(t - ago, 0).unwrap_or_default().format("%Y-%m-%dT%H:%M:%S+0000").to_string();
        format!("active\n{} demo-box {unit}[412]: moved tag halo-latest to 0.9.11\n{} demo-box {unit}[412]: running 0.9.12-rc1, NOT the pinned build 0.9.11", stamp(7200), stamp(1800))
    }

    async fn crash(&self, i: usize, signature: bool) {
        let (port, fake) = &self.fakes[i];
        let t = crate::util::now();
        let name = fake.lock().status["name"].as_str().unwrap_or_default().to_string();
        let clock = chrono::DateTime::from_timestamp(t as i64, 0).unwrap_or_default().format("%H:%M:%S").to_string();
        let lines = if signature {
            vec![
                format!("{} Experimental startup exception 0xC0000005 in halo3.dll at RVA 0x14C73E", iso(t)),
                format!("{} Error: Engine probe worker failed: exit code: 1", iso(t)),
                format!("{} {clock} [{name}] stopped (exit code: 1); restarting in 5 s.", iso(t)),
            ]
        } else {
            vec!["Error: Engine probe worker failed: exit code: 1".into(), format!("{clock} [{name}] stopped (exit code: 1); restarting in 5 s.")]
        };
        self.logs.lock().entry(*port).or_default().extend(lines);
        self.bounce(i).await;
    }

    /// RESTART SERVER: like `docker restart`, the container starts again and everyone is dropped.
    pub async fn restart(&self, i: usize) {
        let Some((port, _)) = self.fakes.get(i) else { return };
        self.started.lock().insert(*port, crate::util::now());
        self.bounce(i).await;
    }

    /// A dedicated.toml setting: the demo's servers take it at once.
    pub fn set(&self, i: usize, key: &str, v: Value) {
        if let Some((_, f)) = self.fakes.get(i) {
            let mut f = f.lock();
            match key {
                "admin_add" => {
                    let name = f.players.iter().find(|p| p["player_id"] == v).map_or(json!(""), |p| p["name"].clone());
                    f.admins.push(json!({"player_id": v, "name": name, "source": "dedicated.toml"}));
                }
                "admin_remove" => f.admins.retain(|a| a["player_id"] != v),
                _ => f.status[key] = v,
            }
        }
    }

    /// Everyone dropped, and five seconds later as many back.
    async fn bounce(&self, i: usize) {
        let fake = &self.fakes[i].1;
        let crowd = {
            let mut f = fake.lock();
            let crowd = f.players.len();
            for (_, c) in f.clients.drain(..) {
                let _ = c.send(Message::Close(None));
            }
            f.players.clear();
            f.renumber();
            crowd
        };
        tokio::time::sleep(Duration::from_secs(5)).await;
        let mut f = fake.lock();
        let names: Vec<&str> = CALLSIGNS.choose_multiple(&mut rand::rng(), crowd).copied().collect();
        f.players = names.into_iter().map(|n| f.player(n)).collect();
        f.renumber();
    }

    async fn timeline(self: Arc<Self>) {
        tokio::time::sleep(Duration::from_secs(7)).await;
        self.crash(0, true).await;
        tokio::time::sleep(Duration::from_secs(1)).await;
        self.crash(1, false).await;
        tokio::time::sleep(Duration::from_secs(1)).await;
        *self.mem.lock() = (640, 1300, 2048, 0);
    }
}

/// Start the fake servers; returns (ports, the box). Their tasks run until the handles are aborted.
pub async fn serve_fakes(password: &str, content: &[PathBuf], tasks: &mut Vec<tokio::task::JoinHandle<()>>) -> (Vec<u16>, Arc<FakeHost>) {
    let mut fakes = vec![];
    for (i, (name, max, crowd)) in SERVERS.iter().enumerate() {
        let fake = Arc::new(Mutex::new(Fake::new(name, password, *max, *crowd, content.get(i).cloned())));
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("a local port for the demo");
        let port = listener.local_addr().unwrap().port();
        let f = fake.clone();
        tasks.push(tokio::spawn(async move {
            while let Ok((s, _)) = listener.accept().await {
                tokio::spawn(handle(f.clone(), s));
            }
        }));
        let f = fake.clone();
        tasks.push(tokio::spawn(async move {
            loop {
                let wait = rand::rng().random_range(0.8..3.0);
                tokio::time::sleep(Duration::from_secs_f64(wait)).await;
                f.lock().tick();
            }
        }));
        fakes.push((port, fake));
    }
    let now = crate::util::now();
    let ages = [5.0 * 3600.0, 3.0 * 3600.0 + 1200.0, 11.0 * 3600.0];
    let started = fakes.iter().zip(ages).map(|((p, _), a)| (*p, now - a)).collect();
    let host = Arc::new(FakeHost { fakes, logs: Mutex::new(HashMap::new()), started: Mutex::new(started), mem: Mutex::new((3400, 120, 2048, 0)) });
    tasks.push(tokio::spawn(host.clone().timeline()));
    (host.fakes.iter().map(|(p, _)| *p).collect(), host)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn a_restart_drops_everyone_and_they_come_back() {
        let (ports, host) = serve_fakes("pw", &[], &mut vec![]).await;
        let (before, crowd) = (host.report(ports[0]), host.fakes[0].1.lock().players.len());
        host.restart(0).await;
        assert_ne!(host.report(ports[0]).lines().nth(1), before.lines().nth(1), "a new container start time");
        assert_eq!(host.fakes[0].1.lock().players.len(), crowd);
    }

    #[test]
    fn admins_add_and_remove_like_the_server() {
        let mut f = Fake::new("T", "pw", 16, 4, None);
        let run = |f: &mut Fake, args: &[&str]| f.command("admin", &args.iter().map(|s| s.to_string()).collect::<Vec<_>>(), "me");
        let p = f.players[0]["name"].as_str().unwrap().to_string();
        let was = f.players[0]["admin"] == true;
        assert_eq!(run(&mut f, &["add", &p])["ok"], !was);
        assert_eq!(f.players[0]["admin"], true);
        assert_eq!(run(&mut f, &["remove", "Overseer"])["ok"], false, "a dedicated.toml admin stays");
        if !was {
            assert_eq!(run(&mut f, &["remove", &p])["ok"], true);
            assert_eq!(f.players[0]["admin"], false);
        }
        assert_eq!(run(&mut f, &["add", "nobody"])["ok"], false);
        assert!(f.command("admins", &[], "me")["data"]["admins"].as_array().unwrap().len() >= 2);
    }
}
