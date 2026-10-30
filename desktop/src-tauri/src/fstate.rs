//! What oni-rcon keeps between runs about Forge content: what's installed on which server, and what the update
//! watcher has seen. One small JSON file beside the config (forge-state.json), the same file the terminal console
//! keeps, written whole through a temp file.
use crate::util::{utc_iso, write_atomic};
use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Map, Value};
use std::collections::BTreeSet;
use std::path::PathBuf;

static NON_ALNUM: Lazy<Regex> = Lazy::new(|| Regex::new(r"[^a-z0-9]+").unwrap());
const SEEN: usize = 500;

/// A name as servers and catalogs both might spell it: "Guardian Rebuilt", "guardian_rebuilt", "guardian-rebuilt".
pub fn norm(s: &str) -> String {
    NON_ALNUM.replace_all(&s.to_lowercase(), "_").trim_matches('_').to_string()
}

/// Every name an installed listing might go by on a server.
pub fn refs_of(e: &Value) -> BTreeSet<String> {
    let mut names = vec![e["reference"].as_str().unwrap_or_default().to_string(), e["title"].as_str().unwrap_or_default().to_string()];
    if let Some(files) = e["files"].as_array() {
        for f in files {
            let p = f["path"].as_str().unwrap_or_default();
            let name = p.rsplit('/').next().unwrap_or_default();
            names.push(name.rsplit_once('.').map(|(a, _)| a).unwrap_or(name).to_string());
        }
    }
    names.iter().map(|n| norm(n)).filter(|n| !n.is_empty()).collect()
}

pub struct ForgeState {
    path: Option<PathBuf>,
    pub data: Value,
}

impl ForgeState {
    pub fn load(path: Option<PathBuf>) -> Self {
        let mut data = json!({"version": 1, "servers": {}});
        if let Some(p) = path.as_ref().filter(|p| p.is_file()) {
            match std::fs::read_to_string(p).ok().and_then(|t| serde_json::from_str::<Value>(&t).ok()) {
                Some(v) if v["version"] == 1 && v["servers"].is_object() => data = v,
                _ => {
                    // kept aside, never written over: it may be worth reading by hand
                    let _ = std::fs::rename(p, p.with_file_name(format!("{}.unreadable", p.file_name().unwrap().to_string_lossy())));
                }
            }
        }
        ForgeState { path, data }
    }

    pub fn save(&self) {
        if let Some(p) = &self.path {
            let _ = write_atomic(p, &serde_json::to_string_pretty(&self.data).unwrap_or_default(), false);
        }
    }

    fn servers(&self) -> &Map<String, Value> {
        self.data["servers"].as_object().unwrap()
    }

    fn table(&mut self, k: &str) -> &mut Map<String, Value> {
        if !self.data[k].is_object() {
            self.data[k] = json!({});
        }
        self.data[k].as_object_mut().unwrap()
    }

    pub fn installed(&self, server: &str) -> Map<String, Value> {
        self.servers().get(server).and_then(Value::as_object).cloned().unwrap_or_default()
    }

    pub fn record(&mut self, servers: &[String], entry: Value) {
        let lid = entry["listing_id"].as_str().unwrap_or_default().to_string();
        for s in servers {
            let all = self.data["servers"].as_object_mut().unwrap();
            let e = all.entry(s.clone()).or_insert(json!({}));
            e[&lid] = entry.clone();
        }
        let vid = self.data["withdrawn"][&lid]["version_id"].as_str().map(str::to_string);
        if let Some(v) = vid {
            if !self.versions(&lid).contains(&v) {
                self.table("withdrawn").remove(&lid); // the withdrawn version is gone from every server
            }
        }
        self.save();
    }

    pub fn versions(&self, lid: &str) -> BTreeSet<String> {
        self.servers()
            .values()
            .filter_map(|es| es.get(lid))
            .filter_map(|e| e["version_id"].as_str())
            .filter(|v| !v.is_empty())
            .map(str::to_string)
            .collect()
    }

    pub fn where_(&self, lid: &str, vid: &str) -> Vec<String> {
        self.servers()
            .iter()
            .filter(|(_, es)| es.get(lid).is_some_and(|e| vid.is_empty() || e["version_id"] == vid))
            .map(|(s, _)| s.clone())
            .collect()
    }

    pub fn ids(&self) -> BTreeSet<String> {
        self.servers().values().filter_map(Value::as_object).flat_map(|es| es.keys().cloned()).collect()
    }

    /// The installed listings a server is playing now: its map, its gametype, or both.
    pub fn playing(&self, server: &str, map: &str, mode: &str) -> Vec<Value> {
        let (m, g) = (norm(map), norm(mode));
        self.installed(server)
            .values()
            .filter(|e| {
                let refs = refs_of(e);
                let kind = e["kind"].as_str().unwrap_or_default();
                !m.is_empty() && refs.contains(&m) && kind != "gametype" || !g.is_empty() && refs.contains(&g) && kind != "map"
            })
            .cloned()
            .collect()
    }

    pub fn entry(&self, lid: &str) -> Option<Value> {
        self.servers().values().find_map(|es| es.get(lid).cloned())
    }

    pub fn since(&self) -> String {
        if let Some(s) = self.data["since"].as_str().filter(|s| !s.is_empty()) {
            return s.into();
        }
        self.servers()
            .values()
            .filter_map(Value::as_object)
            .flat_map(|es| es.values())
            .filter_map(|e| e["installed_at"].as_str())
            .filter(|t| !t.is_empty())
            .min()
            .unwrap_or_default()
            .to_string()
    }

    pub fn seen(&self, key: &str) -> bool {
        self.data["seen"].as_array().is_some_and(|a| a.iter().any(|x| x == key))
    }

    pub fn saw(&mut self, keys: Vec<String>, as_of: &str) {
        let mut all: Vec<Value> = self.data["seen"].as_array().cloned().unwrap_or_default();
        all.extend(keys.into_iter().map(Value::String));
        let n = all.len();
        self.data["seen"] = Value::Array(all.into_iter().skip(n.saturating_sub(SEEN)).collect());
        self.data["since"] = as_of.into();
        self.save();
    }

    /// A version Forge has put out: the servers running another one, the first time it's heard of.
    pub fn newer(&mut self, lid: &str, vid: &str, label: &str) -> Vec<String> {
        self.table("latest").insert(lid.into(), json!({"id": vid, "version": label}));
        let behind: Vec<String> = self
            .where_(lid, "")
            .into_iter()
            .filter(|s| self.installed(s)[lid]["version_id"] != vid)
            .collect();
        if behind.is_empty() || self.data["told"][lid] == vid {
            self.save();
            return vec![];
        }
        self.table("told").insert(lid.into(), vid.into());
        self.save();
        behind
    }

    pub fn withdraw(&mut self, lid: &str, vid: &str) -> bool {
        let w = &self.data["withdrawn"][lid];
        if w.is_object() && !(w["version_id"].is_string() && vid.is_empty()) {
            return false;
        }
        let mut e = json!({"at": utc_iso(), "acknowledged": false});
        if !vid.is_empty() {
            e["version_id"] = vid.into();
        }
        self.table("withdrawn").insert(lid.into(), e);
        self.save();
        true
    }

    pub fn restore(&mut self, lid: &str) {
        if self.data["withdrawn"][lid].is_object() && !self.data["withdrawn"][lid]["version_id"].is_string() {
            self.table("withdrawn").remove(lid);
            self.save();
        }
    }

    pub fn is_withdrawn(&self, lid: &str) -> bool {
        self.data["withdrawn"][lid].is_object()
    }

    #[cfg(test)]
    pub fn blocks(&self, lid: &str) -> bool {
        let w = &self.data["withdrawn"][lid];
        w.is_object() && !w["version_id"].is_string()
    }

    pub fn pulled(&self, lid: &str, entry: &Value) -> bool {
        let w = &self.data["withdrawn"][lid];
        w.is_object() && (!w["version_id"].is_string() || entry["version_id"] == w["version_id"])
    }

    pub fn acknowledge(&mut self, lid: &str) {
        if self.data["withdrawn"][lid].is_object() {
            self.data["withdrawn"][lid]["acknowledged"] = true.into();
            self.save();
        }
    }

    /// Withdrawn listings, or versions, still installed somewhere that nobody has acknowledged: CONDITION AMBER.
    pub fn alarms(&self) -> Vec<String> {
        let Some(w) = self.data["withdrawn"].as_object() else { return vec![] };
        w.iter()
            .filter(|(lid, x)| {
                x["acknowledged"] != true
                    && self.servers().values().filter_map(|es| es.get(lid.as_str())).any(|e| self.pulled(lid, e))
            })
            .map(|(lid, _)| lid.clone())
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn installs_withdrawals_and_alarms() {
        let mut s = ForgeState::load(None);
        let e = json!({"listing_id": "l1", "version_id": "v1", "title": "Guardian Rebuilt", "kind": "map", "files": [{"path": "guardian_rebuilt.map"}]});
        s.record(&["a".into(), "b".into()], e);
        assert_eq!(s.where_("l1", "v1").len(), 2);
        assert_eq!(s.playing("a", "guardian-rebuilt", "slayer").len(), 1);
        assert_eq!(s.newer("l1", "v2", "1.1").len(), 2);
        assert!(s.newer("l1", "v2", "1.1").is_empty(), "told once");
        assert!(s.withdraw("l1", "v1"));
        assert!(!s.withdraw("l1", "v1"));
        assert_eq!(s.alarms(), ["l1"]);
        assert!(!s.blocks("l1"));
        assert!(s.withdraw("l1", ""), "the whole listing after a version is news again");
        assert!(s.blocks("l1"));
        s.acknowledge("l1");
        assert!(s.alarms().is_empty());
    }
}
