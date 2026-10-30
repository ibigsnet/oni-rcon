//! Rounds played, per server, map and gametype, from the status polls the console already makes: one JSON line each
//! in rounds.jsonl, beside the config. Nothing is sent anywhere, and no player names, IDs or addresses go in; the
//! server goes by the public name it reports, never by how this console reaches it.
use crate::fstate::norm;
use crate::util::{num, utc_iso};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::Write;
use std::path::PathBuf;
use std::time::Instant;

const IN_GAME: &[&str] = &["in_game", "ingame", "playing"];

struct Open {
    map: String,
    mode: String,
    started_at: String,
    t0: Instant,
    peak: i64,
    seen_whole: bool,
    name: String,
}

pub struct Rounds {
    path: Option<PathBuf>,
    open: HashMap<String, Open>,
    last: HashMap<String, (String, String, String)>,
}

impl Rounds {
    pub fn new(path: Option<PathBuf>) -> Self {
        Rounds { path, open: HashMap::new(), last: HashMap::new() }
    }

    /// One status poll from `server`. Returns the round it closed, if it closed one.
    pub fn observe(&mut self, server: &str, status: &Value, credit: &dyn Fn(&str, &str) -> Vec<Value>) -> Option<Value> {
        if !status.is_object() {
            return None;
        }
        let phase = norm(status["phase"].as_str().unwrap_or_default());
        let s = |k: &str| match &status[k] {
            Value::String(s) => s.clone(),
            Value::Null => String::new(),
            v => v.to_string(),
        };
        let (mp, md) = (s("map"), s("mode"));
        let n = match &status["players"] {
            Value::Array(a) => Some(a.len() as i64),
            v => num(Some(v)).map(|f| f as i64),
        };
        let playing = IN_GAME.contains(&phase.as_str());
        let before = self.last.get(server).cloned();
        let mut done = None;
        if let Some(cur) = self.open.get(server) {
            if !playing || (mp.as_str(), md.as_str()) != (cur.map.as_str(), cur.mode.as_str()) {
                done = Some(self.close(server, n, credit));
            }
        }
        if playing && !self.open.contains_key(server) {
            let whole = before.as_ref().is_some_and(|b| !IN_GAME.contains(&b.0.as_str()) || (b.1.as_str(), b.2.as_str()) != (mp.as_str(), md.as_str()));
            self.open.insert(server.into(), Open { map: mp.clone(), mode: md.clone(), started_at: utc_iso(), t0: Instant::now(), peak: n.unwrap_or(0), seen_whole: whole, name: s("name") });
        } else if playing {
            if let (Some(o), Some(n)) = (self.open.get_mut(server), n) {
                o.peak = o.peak.max(n);
            }
        }
        self.last.insert(server.into(), (phase, mp, md));
        done
    }

    /// The connection dropped: how the round under way ended is unknown, so it isn't counted.
    pub fn lost(&mut self, server: &str) {
        self.open.remove(server);
        self.last.remove(server);
    }

    fn close(&mut self, server: &str, end: Option<i64>, credit: &dyn Fn(&str, &str) -> Vec<Value>) -> Value {
        let r = self.open.remove(server).unwrap();
        let forge: Vec<Value> = credit(&r.map, &r.mode)
            .iter()
            .map(|e| json!({"listing_id": e["listing_id"], "version_id": e["version_id"], "kind": e["kind"]}))
            .collect();
        let rec = json!({"schema": 1, "id": uuid::Uuid::new_v4().simple().to_string(), "server": r.name, "map": r.map,
            "mode": r.mode, "forge": forge, "started_at": r.started_at, "ended_at": utc_iso(),
            "seconds": r.t0.elapsed().as_secs(), "players_peak": r.peak, "players_end": end, "seen_whole": r.seen_whole,
            "client": format!("oni-rcon-desktop/{}", env!("CARGO_PKG_VERSION"))});
        if let Some(p) = &self.path {
            if let Some(d) = p.parent() {
                let _ = std::fs::create_dir_all(d);
            }
            if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(p) {
                let _ = writeln!(f, "{rec}");
            }
        }
        rec
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_round_seen_whole() {
        let mut r = Rounds::new(None);
        let none = |_: &str, _: &str| vec![];
        assert!(r.observe("s", &json!({"phase": "lobby", "map": "a"}), &none).is_none());
        assert!(r.observe("s", &json!({"phase": "in_game", "map": "a", "mode": "m", "players": 4}), &none).is_none());
        r.observe("s", &json!({"phase": "in_game", "map": "a", "mode": "m", "players": 7}), &none);
        let done = r.observe("s", &json!({"phase": "postgame", "map": "a", "players": 6}), &none).unwrap();
        assert_eq!((done["players_peak"].as_i64(), done["seen_whole"].as_bool()), (Some(7), Some(true)));
    }
}
