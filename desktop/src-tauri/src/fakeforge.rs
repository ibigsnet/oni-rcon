//! A pretend ReclaimerForge for the demo: the API exactly as forge.rs reads it, seeded listings, the rate-limit
//! headers and curated favourites. It never talks to the real site, and its key is no real key. Its files are filler
//! (.map, .gt, .playlist): the fake servers list what lands in their content folder, so an install can be loaded.
//! The demo key owns one listing, and can publish, edit, withdraw and delete as a real key with the write scopes.
use crate::util::{parse_iso, utc_iso, utc_iso_at};
use axum::body::Bytes;
use axum::extract::{Path as UrlPath, Query, State};
use axum::http::{HeaderMap, HeaderValue, Method, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{any, get};
use axum::Router;
use chrono::{Duration as Days, Utc};
use parking_lot::Mutex;
use rand::rngs::StdRng;
use rand::seq::IndexedRandom;
use rand::{Rng, SeedableRng};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::Arc;
use std::time::{Duration, Instant};

pub const DEMO_KEY: &str = "rfk_demo_notarealkey00";
const ME: (&str, &str) = ("usr_demo01", "You");
const CHUNK: usize = 64 << 10; // small, so even a demo file goes up in a few chunks
const AUTHORS: &[(&str, &str)] = &[("usr_7a1c03", "Kestrel"), ("usr_2be944", "lowgrav"), ("usr_91d0aa", "Onyx Works"), ("usr_c44e17", "Juno"), ("usr_5f0262", "Halcyon"), ("usr_e810b9", "Mako")];
const MAPS: &[(&str, &str)] = &[("Guardian Rebuilt", "guardian"), ("Pit Stop", "the_pit"), ("Narrows Siege", "narrows"), ("Valhalla Convoy", "valhalla"),
    ("Sandtrap Outpost", "sandtrap"), ("Foundry Arena", "foundry"), ("Epitaph Echo", "epitaph"), ("Construct Inferno", "construct"),
    ("Last Resort Breach", "last_resort"), ("Snowbound Night", "snowbound"), ("High Ground Assault", "high_ground"), ("Standoff Rally", "standoff"),
    ("Isolation Ward", "isolation"), ("Heretic Mini", "heretic")];
const GAMETYPES: &[(&str, &str)] = &[("Grifball", "Assault"), ("Infection Classic", "Infection"), ("SWAT Magnums", "Slayer"), ("Juggernaut Plus", "Juggernaut"),
    ("Oddball Rush", "Oddball"), ("Fiesta Slayer", "Slayer"), ("Duel Arena", "Slayer"), ("Team Snipers", "Slayer")];
const PLAYLISTS: &[&str] = &["Community Picks", "Big Team Remix", "Party Night"];
const BLURBS: &[&str] = &["**Rebuilt** from scratch for Reclaimer.\n\nSpawns are tuned for 8v8. See [the thread](https://example.org).",
    "A *fast* one. Short sightlines, three power weapons.", "Plays best with 12 or more.\n\n- Two bases\n- No vehicles", "Made for the weekend crowd."];
const NOTES: &[&str] = &["Spawns **rebalanced**.", "Fixed a gap behind red base.", "First release.\n\nThanks for playing.", "Weapon timers tuned.", "Lighting pass."];

fn slug(s: &str) -> String {
    crate::fstate::norm(s)
}

fn ext(kind: &str) -> &'static str {
    match kind {
        "map" => ".map",
        "gametype" => ".gt",
        _ => ".playlist",
    }
}

pub struct FakeForge {
    rnd: StdRng,
    listings: Vec<Value>,
    manifests: HashMap<String, Value>,
    blobs: HashMap<String, Vec<u8>>,
    changes: Vec<Value>,
    collections: Vec<Value>,
    hits: VecDeque<Instant>,
    pub fetched: HashSet<String>,
    base: String,
    uploads: HashMap<String, Up>,
    made: usize,
}

struct Up {
    lid: String,
    vid: String,
    name: String,
    size: usize,
    data: Vec<u8>,
    next: u64,
}

fn owned(x: &Value) -> bool {
    x["authors"].as_array().is_some_and(|a| a.iter().any(|a| a["user_id"] == ME.0))
}

fn invalid(m: &str) -> (u16, Value) {
    (422, json!({"detail": [{"loc": ["body"], "msg": m}]}))
}

impl FakeForge {
    fn new() -> Self {
        let mut f = FakeForge { rnd: StdRng::seed_from_u64(7), listings: vec![], manifests: HashMap::new(), blobs: HashMap::new(), changes: vec![], collections: vec![], hits: VecDeque::new(), fetched: HashSet::new(), base: String::new(), uploads: HashMap::new(), made: 0 };
        let mut n = 0;
        for (t, b) in MAPS {
            n += 1;
            f.add(n, t, "map", json!({"base_map": b}));
        }
        for (t, b) in GAMETYPES {
            n += 1;
            f.add(n, t, "gametype", json!({"base_mode": b}));
        }
        for t in PLAYLISTS {
            n += 1;
            f.add(n, t, "playlist", json!({}));
        }
        let ids: Vec<String> = f.listings.iter().map(|x| x["id"].as_str().unwrap().to_string()).collect();
        let t0 = Utc::now();
        f.feature("Staff picks", &ids[1..5], t0 - Days::days(1), t0 + Days::days(6));
        f.feature("Next weekend", &ids[6..8], t0 + Days::days(4), t0 + Days::days(6));
        f.feature("Last season", &ids[8..10], t0 - Days::days(30), t0 - Days::days(2));
        f.add(n + 1, "Proving Grounds", "map", json!({"base_map": "guardian", "visibility": "public", "tags": ["practice"]}));
        let mine = f.listings.last_mut().unwrap();
        mine["owner_id"] = json!(ME.0);
        mine["authors"] = json!([{"user_id": ME.0, "username": ME.1, "avatar_url": format!("/avatars/{}.png", ME.0), "is_owner": true}]);
        f
    }

    fn add(&mut self, i: usize, title: &str, kind: &str, extra: Value) {
        let r = &mut self.rnd;
        let (oid, author) = *AUTHORS.choose(r).unwrap();
        let mut authors = vec![json!({"user_id": oid, "username": author, "avatar_url": format!("/avatars/{oid}.png"), "is_owner": true})];
        if r.random::<f64>() < 0.3 {
            let others: Vec<_> = AUTHORS.iter().filter(|a| a.0 != oid).collect();
            let (o, a) = **others.choose(r).unwrap();
            authors.push(json!({"user_id": o, "username": a, "avatar_url": format!("/avatars/{o}.png"), "is_owner": false}));
        }
        let (mut up, total) = (r.random_range(0..=240), r.random_range(20..=9000));
        let mut down = r.random_range(0..=(up / 8).max(1));
        if r.random::<f64>() < 0.15 {
            up = 0;
            down = 0;
        }
        let t0 = Utc::now();
        let compat = *[">=0.9.5", ">=0.9.5", ">=0.9.6", "0.9.6"].choose(r).unwrap();
        let recent = json!({"24h": r.random_range(0..=40), "7d": r.random_range(10..=300), "30d": r.random_range(40..=1200)});
        let mut x = json!({"id": format!("lst_{i:03}{:08x}", r.random::<u32>()), "title": title, "kind": kind, "owner_id": oid, "authors": authors,
            "description": BLURBS.choose(r).unwrap(), "upvote_count": up, "downvote_count": down, "vote_count": up + down,
            "view_count": total * 3 + r.random_range(0..=500), "downloads": total,
            "metrics": {"recent_downloads": recent, "download_history_started_at": utc_iso_at(t0 - Days::days(*[3, 12, 45].choose(r).unwrap()))},
            "compatibility": compat, "status": "published",
            "created_at": utc_iso_at(t0 - Days::days(r.random_range(30..=400))), "versions": []});
        for (k, v) in extra.as_object().unwrap() {
            x[k] = v.clone();
        }
        let n = r.random_range(1..=3);
        for k in 0..n {
            self.version(&mut x, &format!("1.{k}"), t0 - Days::days(20 - 6 * k));
        }
        self.listings.push(x);
    }

    fn version(&mut self, x: &mut Value, label: &str, when: chrono::DateTime<Utc>) -> String {
        let size = self.rnd.random_range(3000..=60000usize);
        let line = format!("{} {label} | ONI demo filler\n", x["title"].as_str().unwrap());
        let blob: Vec<u8> = line.as_bytes().iter().cycle().take(size).copied().collect();
        let sha = hex::encode(Sha256::digest(&blob));
        self.blobs.insert(sha.clone(), blob);
        let vid = format!("ver_{:010x}", self.rnd.random::<u64>() & 0xff_ffff_ffff);
        let v = json!({"id": vid, "version_label": label, "created_at": utc_iso_at(when), "release_notes": NOTES.choose(&mut self.rnd).unwrap(), "status": "published"});
        let kind = x["kind"].as_str().unwrap().to_string();
        let r = slug(x["title"].as_str().unwrap());
        self.manifests.insert(vid.clone(), json!({"listing_id": x["id"], "version_id": vid, "version_label": label, "kind": kind, "reference": r,
            "assets": [{"path": format!("{r}{}", ext(&kind)), "size": size, "sha256": sha}]}));
        x["versions"].as_array_mut().unwrap().insert(0, v);
        x["latest_version"] = json!({"id": vid, "version_label": label});
        x["updated_at"] = json!(utc_iso_at(when));
        vid
    }

    fn feature(&mut self, title: &str, lids: &[String], starts: chrono::DateTime<Utc>, ends: chrono::DateTime<Utc>) {
        self.collections.push(json!({"id": format!("fav_{:03}", self.collections.len() + 1), "title": title, "listing_ids": lids,
            "starts_at": utc_iso_at(starts), "ends_at": utc_iso_at(ends), "updated_at": utc_iso()}));
    }

    fn find(&mut self, lid: &str) -> Option<usize> {
        self.listings.iter().position(|x| x["id"] == lid)
    }

    fn publish(&mut self, lid: &str) {
        let Some(i) = self.find(lid) else { return };
        let mut x = self.listings[i].clone();
        let label = x["latest_version"]["version_label"].as_str().unwrap_or("1.0").to_string();
        let (major, minor) = label.split_once('.').unwrap_or((&label, "0"));
        let next = format!("{major}.{}", minor.parse::<i64>().unwrap_or(0) + 1);
        let vid = self.version(&mut x, &next, Utc::now());
        self.listings[i] = x;
        self.changes.push(json!({"listing_id": lid, "published_version_ids": [vid], "updated_at": utc_iso()}));
    }

    fn withdraw_version(&mut self, lid: &str) {
        let Some(i) = self.find(lid) else { return };
        let x = &mut self.listings[i];
        let live: Vec<usize> = (0..x["versions"].as_array().unwrap().len()).filter(|&j| x["versions"][j]["status"] == "published").collect();
        if live.len() < 2 {
            return;
        }
        x["versions"][live[0]]["status"] = json!("withdrawn");
        let vid = x["versions"][live[0]]["id"].clone();
        let newest = x["versions"][live[1]].clone();
        x["latest_version"] = json!({"id": newest["id"], "version_label": newest["version_label"]});
        x["updated_at"] = json!(utc_iso());
        self.changes.push(json!({"listing_id": lid, "withdrawn_version_ids": [vid], "updated_at": utc_iso()}));
    }

    fn withdraw(&mut self, lid: &str) {
        let Some(i) = self.find(lid) else { return };
        let x = &mut self.listings[i];
        let mut ids = vec![];
        for v in x["versions"].as_array_mut().unwrap() {
            v["status"] = json!("withdrawn");
            ids.push(v["id"].clone());
        }
        x["status"] = json!("withdrawn");
        x["updated_at"] = json!(utc_iso());
        self.changes.push(json!({"listing_id": lid, "status": "withdrawn", "updated_at": utc_iso(), "withdrawn_version_ids": ids}));
    }

    fn summary(x: &Value) -> Value {
        let mut s = x.clone();
        s.as_object_mut().unwrap().remove("versions");
        s
    }

    fn order(items: &mut [Value], sort: &str, window: &str) {
        let f = |x: &Value, k: &str| x[k].as_f64().unwrap_or(0.0);
        let recent = |x: &Value| x["metrics"]["recent_downloads"][window].as_f64().unwrap_or(0.0);
        let key = |x: &Value| -> (f64, f64, String) {
            match sort {
                "latest" => (0.0, 0.0, x["created_at"].as_str().unwrap_or_default().into()),
                "updated" => (0.0, 0.0, x["updated_at"].as_str().unwrap_or_default().into()),
                "downloads" => (f(x, "downloads"), 0.0, String::new()),
                "rated" => (f(x, "upvote_count"), -f(x, "downvote_count"), String::new()),
                "unrated" => ((f(x, "vote_count") == 0.0) as i32 as f64, 0.0, x["created_at"].as_str().unwrap_or_default().into()),
                "overlooked" => ((f(x, "upvote_count") + 1.0) / (f(x, "vote_count") + 2.0) / (f(x, "downloads") + 3.0).ln(), 0.0, String::new()),
                "rising" => (recent(x) / (f(x, "downloads") + 50.0), 0.0, String::new()),
                _ => (recent(x), 0.0, String::new()),
            }
        };
        items.sort_by(|a, b| {
            let (ka, kb) = (key(a), key(b));
            kb.0.total_cmp(&ka.0).then(kb.1.total_cmp(&ka.1)).then(kb.2.cmp(&ka.2))
        });
    }

    fn route(&mut self, path: &str, q: &HashMap<String, String>) -> (u16, Value) {
        let bad = |m: &str| (422, json!({"error": "validation", "message": m}));
        let size: usize = match q.get("page_size").map(|s| s.parse()).unwrap_or(Ok(50)) {
            Ok(n) if (1..=100).contains(&n) => n,
            _ => return bad("page_size must be 1 to 100"),
        };
        let start: usize = q.get("cursor").and_then(|s| s.parse().ok()).unwrap_or(0);
        if path == "/api/listings" {
            let sort = q.get("sort").map(String::as_str).unwrap_or("latest");
            let window = q.get("window").map(String::as_str).unwrap_or("7d");
            if !crate::forge::SORTS.contains(&sort) || !["24h", "7d", "30d"].contains(&window) {
                return bad("unknown sort or window");
            }
            let text = q.get("q").map(|s| s.to_lowercase()).unwrap_or_default();
            let mut items: Vec<Value> = self.listings.iter().filter(|x| x["status"] == "published" && x["visibility"] != "private")
                .filter(|x| text.is_empty() || x["title"].as_str().unwrap().to_lowercase().contains(&text)
                    || x["authors"].as_array().unwrap().iter().any(|a| a["username"].as_str().unwrap().to_lowercase().contains(&text)))
                .cloned().collect();
            Self::order(&mut items, sort, window);
            let page: Vec<Value> = items.iter().skip(start).take(size).map(Self::summary).collect();
            let more = start + size < items.len();
            return (200, json!({"items": page, "next_cursor": if more { json!((start + size).to_string()) } else { Value::Null }}));
        }
        if path == "/api/favourites" {
            let now = Utc::now();
            let out: Vec<Value> = self.collections.iter().filter_map(|c| {
                let live: Vec<&Value> = c["listing_ids"].as_array().unwrap().iter()
                    .filter_map(|i| self.listings.iter().find(|x| x["id"] == *i && x["status"] == "published")).collect();
                (crate::forge::active_now(c, now) && !live.is_empty()).then(|| {
                    let mut c = c.clone();
                    c["listing_ids"] = json!(live.iter().map(|x| x["id"].clone()).collect::<Vec<_>>());
                    c["listings"] = json!(live.iter().map(|x| Self::summary(x)).collect::<Vec<_>>());
                    c
                })
            }).collect();
            return (200, json!(out));
        }
        if path == "/api/listings/changes" {
            let Some(since) = q.get("updated_since").and_then(|s| parse_iso(s)) else { return bad("updated_since is required, as an ISO 8601 time") };
            let upto = q.get("updated_before").and_then(|s| parse_iso(s)).unwrap_or_else(Utc::now);
            let items: Vec<Value> = self.changes.iter().filter(|c| parse_iso(c["updated_at"].as_str().unwrap()).is_some_and(|t| since < t && t <= upto)).cloned().collect();
            let page: Vec<Value> = items.iter().skip(start).take(size).cloned().collect();
            let more = start + size < items.len();
            return (200, json!({"as_of": utc_iso_at(upto), "items": page, "next_cursor": if more { json!((start + size).to_string()) } else { Value::Null }}));
        }
        if path == "/api/listings/mine" {
            return (200, json!(self.listings.iter().filter(|x| owned(x)).cloned().collect::<Vec<_>>()));
        }
        if let Some(lid) = path.strip_prefix("/api/listings/mine/") {
            return match self.listings.iter().find(|x| x["id"] == lid && owned(x)) {
                Some(x) => (200, x.clone()),
                None => (404, json!({"detail": "No such listing of yours."})),
            };
        }
        let parts: Vec<&str> = path.trim_start_matches("/api/listings/").split('/').collect();
        if path.starts_with("/api/listings/") && parts.len() == 4 && parts[1] == "versions" && parts[3] == "manifest" {
            let (lid, vid) = (parts[0], parts[2]);
            let x = self.listings.iter().find(|x| x["id"] == lid);
            let man = self.manifests.get(vid);
            let live = x.is_some_and(|x| x["status"] == "published" && x["versions"].as_array().unwrap().iter().any(|v| v["id"] == vid && v["status"] == "published"));
            let (Some(_), Some(man)) = (x, man) else { return (404, json!({"error": "not_found", "message": "That version is unavailable."})) };
            if !live || man["listing_id"] != lid {
                return (404, json!({"error": "not_found", "message": "That version is unavailable."}));
            }
            let mut m = man.clone();
            for a in m["assets"].as_array_mut().unwrap() {
                a["url"] = json!(format!("{}/assets/{}", self.base, a["sha256"].as_str().unwrap()));
            }
            self.fetched.insert(lid.to_string());
            return (200, m);
        }
        if path.starts_with("/api/listings/") && parts.len() == 1 {
            return match self.listings.iter().find(|x| x["id"] == parts[0] && x["status"] != "draft") {
                Some(x) => {
                    let mut x = x.clone();
                    x["versions"].as_array_mut().unwrap().retain(|v| v["status"] != "draft");
                    (200, x)
                }
                None => (404, json!({"error": "not_found", "message": "No such listing."})),
            };
        }
        (404, json!({"error": "not_found", "message": "No such endpoint."}))
    }

    fn draft(&mut self, i: usize, label: &str, notes: &str) -> String {
        self.made += 1;
        let vid = format!("ver_m{:04}", self.made);
        let v = json!({"id": vid, "version_label": label, "release_notes": notes, "created_at": utc_iso(), "status": "draft"});
        self.listings[i]["versions"].as_array_mut().unwrap().insert(0, v);
        vid
    }

    /// The write side of the API, as the site's own pages use it.
    fn write(&mut self, method: &Method, path: &str, body: &[u8]) -> (u16, Value) {
        let b: Value = serde_json::from_slice(body).unwrap_or(json!({}));
        let text = |k: &str| b[k].as_str().unwrap_or_default().trim().to_string();
        let parts: Vec<&str> = path.trim_start_matches("/api/").split('/').collect();
        let gone = (404, json!({"detail": "No such listing of yours."}));
        let mine = |f: &Self, lid: &str| f.listings.iter().position(|x| x["id"] == lid && owned(x));
        let version = |f: &Self, i: usize, vid: &str| f.listings[i]["versions"].as_array().unwrap().iter().position(|v| v["id"] == vid);
        match (method.as_str(), parts.as_slice()) {
            ("POST", ["listings"]) => {
                if text("title").is_empty() {
                    return invalid("title is required");
                }
                if text("version_label").is_empty() {
                    return invalid("version_label is required");
                }
                self.made += 1;
                let x = json!({"id": format!("lst_m{:04}", self.made), "title": text("title"), "kind": "map", "owner_id": ME.0,
                    "authors": [{"user_id": ME.0, "username": ME.1, "is_owner": true}], "description": text("description"),
                    "tags": b["tags"].as_array().cloned().unwrap_or_default(), "visibility": b["visibility"].as_str().unwrap_or("public"),
                    "upvote_count": 0, "downvote_count": 0, "vote_count": 0, "view_count": 0, "downloads": 0, "status": "draft",
                    "created_at": utc_iso(), "updated_at": utc_iso(), "versions": []});
                self.listings.push(x);
                let i = self.listings.len() - 1;
                self.draft(i, &text("version_label"), &text("release_notes"));
                (201, self.listings[i].clone())
            }
            ("PATCH", ["listings", lid]) => {
                let Some(i) = mine(self, lid) else { return gone };
                if text("title").is_empty() {
                    return invalid("title is required");
                }
                for k in ["title", "description", "tags", "visibility"] {
                    self.listings[i][k] = b.get(k).cloned().unwrap_or(Value::Null); // what's left out is reset, as Forge does
                }
                self.listings[i]["updated_at"] = json!(utc_iso());
                (200, self.listings[i].clone())
            }
            ("DELETE", ["listings", lid]) => {
                let Some(i) = mine(self, lid) else { return gone };
                self.listings.remove(i);
                self.changes.push(json!({"listing_id": lid, "status": "withdrawn", "updated_at": utc_iso()}));
                (204, Value::Null)
            }
            ("POST", ["listings", lid, "versions"]) => {
                let Some(i) = mine(self, lid) else { return gone };
                if self.listings[i]["versions"].as_array().unwrap().iter().any(|v| v["status"] == "draft") {
                    return (409, json!({"detail": "This listing already has a draft version. Publish or discard it first."}));
                }
                if text("version_label").is_empty() {
                    return invalid("version_label is required");
                }
                self.draft(i, &text("version_label"), &text("release_notes"));
                (201, self.listings[i].clone())
            }
            ("PATCH", ["listings", lid, "versions", vid]) => {
                let Some(i) = mine(self, lid) else { return gone };
                let Some(j) = version(self, i, vid) else { return gone };
                for k in ["version_label", "release_notes"] {
                    if let Some(v) = b.get(k) {
                        self.listings[i]["versions"][j][k] = v.clone();
                    }
                }
                (200, self.listings[i].clone())
            }
            ("DELETE", ["listings", lid, "versions", vid]) => {
                let Some(i) = mine(self, lid) else { return gone };
                let Some(j) = version(self, i, vid) else { return gone };
                let x = &mut self.listings[i];
                let live = x["versions"].as_array().unwrap().iter().filter(|v| v["status"] == "published").count();
                if x["versions"][j]["status"] == "draft" {
                    x["versions"].as_array_mut().unwrap().remove(j);
                } else if live < 2 {
                    return (409, json!({"detail": "At least one published version must remain. Delete the listing instead."}));
                } else {
                    x["versions"][j]["status"] = json!("withdrawn");
                    let newest = x["versions"].as_array().unwrap().iter().find(|v| v["status"] == "published").cloned().unwrap();
                    x["latest_version"] = json!({"id": newest["id"], "version_label": newest["version_label"]});
                    self.changes.push(json!({"listing_id": lid, "withdrawn_version_ids": [vid], "updated_at": utc_iso()}));
                }
                (204, Value::Null)
            }
            ("POST", ["listings", lid, "versions", vid, "uploads"]) => {
                let Some(i) = mine(self, lid) else { return gone };
                if !version(self, i, vid).is_some_and(|j| self.listings[i]["versions"][j]["status"] == "draft") {
                    return (409, json!({"detail": "Files go onto a draft version."}));
                }
                let name = text("original_filename");
                let size = b["file_size"].as_u64().unwrap_or(0) as usize;
                if !["json", "mvar", "bin"].iter().any(|e| name.to_lowercase().ends_with(&format!(".{e}"))) {
                    return (415, json!({"detail": "Unsupported file type."}));
                }
                if size == 0 || size as u64 > crate::forge::MAX_UPLOAD {
                    return (413, json!({"detail": "Files must be between 1 byte and 100 MiB."}));
                }
                self.made += 1;
                let id = format!("upl_m{:04}", self.made);
                self.uploads.insert(id.clone(), Up { lid: lid.to_string(), vid: vid.to_string(), name, size, data: vec![], next: 0 });
                (201, json!({"id": id, "chunk_size": CHUNK, "received_size": 0, "next_chunk_index": 0}))
            }
            ("PUT", ["files", "uploads", id, "chunks", index]) => {
                let Some(u) = self.uploads.get_mut(*id) else { return (410, json!({"detail": "This upload has expired."})) };
                if index.parse::<u64>().ok() != Some(u.next) || body.len() > CHUNK || u.data.len() + body.len() > u.size {
                    return (409, json!({"detail": "That isn't the chunk this upload expects next."}));
                }
                u.data.extend_from_slice(body);
                u.next += 1;
                (200, json!({"id": id, "received_size": u.data.len(), "next_chunk_index": u.next}))
            }
            ("POST", ["listings", "uploads", id, "complete"]) => {
                let Some(u) = self.uploads.remove(*id) else { return (410, json!({"detail": "This upload has expired."})) };
                if u.data.len() != u.size {
                    return (409, json!({"detail": "The upload isn't finished."}));
                }
                let Some(i) = mine(self, &u.lid) else { return gone };
                let sha = hex::encode(Sha256::digest(&u.data));
                let x = &self.listings[i];
                let label = x["versions"].as_array().unwrap().iter().find(|v| v["id"] == u.vid).map(|v| v["version_label"].clone()).unwrap_or_default();
                let man = self.manifests.entry(u.vid.clone()).or_insert_with(|| json!({"listing_id": x["id"], "version_id": u.vid, "version_label": label,
                    "kind": x["kind"], "reference": slug(x["title"].as_str().unwrap_or_default()), "assets": []}));
                man["assets"].as_array_mut().unwrap().push(json!({"path": u.name, "size": u.size, "sha256": sha}));
                self.blobs.insert(sha.clone(), u.data);
                (200, json!({"id": id, "status": "complete", "sha256": sha}))
            }
            ("POST", ["listings", lid, "versions", vid, "publish"]) => {
                let Some(i) = mine(self, lid) else { return gone };
                let Some(j) = version(self, i, vid) else { return gone };
                if !self.manifests.get(*vid).is_some_and(|m| !m["assets"].as_array().unwrap().is_empty()) {
                    return (409, json!({"detail": "Upload at least one file before publishing."}));
                }
                let x = &mut self.listings[i];
                x["versions"][j]["status"] = json!("published");
                x["status"] = json!("published");
                x["latest_version"] = json!({"id": vid, "version_label": x["versions"][j]["version_label"]});
                x["updated_at"] = json!(utc_iso());
                self.changes.push(json!({"listing_id": lid, "published_version_ids": [vid], "updated_at": utc_iso()}));
                (200, self.listings[i].clone())
            }
            _ => (405, json!({"detail": "Method not allowed."})),
        }
    }
}

type Shared = Arc<Mutex<FakeForge>>;

async fn api(State(f): State<Shared>, UrlPath(rest): UrlPath<String>, Query(q): Query<HashMap<String, String>>, method: Method, h: HeaderMap, body: Bytes) -> Response {
    let path = format!("/api/{rest}");
    let mut f = f.lock();
    if h.get("Authorization").and_then(|v| v.to_str().ok()) != Some(&format!("Bearer {DEMO_KEY}")) {
        return (StatusCode::UNAUTHORIZED, axum::Json(json!({"error": "unauthorized", "message": "Missing, invalid or expired API key."}))).into_response();
    }
    let now = Instant::now();
    while f.hits.front().is_some_and(|t| now.duration_since(*t) >= Duration::from_secs(60)) {
        f.hits.pop_front();
    }
    let reset = f.hits.front().map(|t| 60 - now.duration_since(*t).as_secs()).unwrap_or(60);
    let mut headers = HeaderMap::new();
    headers.insert("X-RateLimit-Limit", HeaderValue::from_static("120"));
    headers.insert("X-RateLimit-Reset", HeaderValue::from_str(&((crate::util::now() as u64) + reset).to_string()).unwrap());
    if f.hits.len() >= 120 {
        headers.insert("X-RateLimit-Remaining", HeaderValue::from_static("0"));
        headers.insert("Retry-After", HeaderValue::from_str(&reset.to_string()).unwrap());
        return (StatusCode::TOO_MANY_REQUESTS, headers, axum::Json(json!({"error": "rate_limited", "message": "Too many requests."}))).into_response();
    }
    f.hits.push_back(now);
    headers.insert("X-RateLimit-Remaining", HeaderValue::from_str(&(120 - f.hits.len()).to_string()).unwrap());
    let (status, body) = if method == Method::GET { f.route(&path, &q) } else { f.write(&method, &path, &body) };
    if status == 204 {
        return (StatusCode::NO_CONTENT, headers).into_response();
    }
    (StatusCode::from_u16(status).unwrap(), headers, axum::Json(body)).into_response()
}

async fn asset(State(f): State<Shared>, UrlPath(sha): UrlPath<String>) -> Response {
    match f.lock().blobs.get(&sha) {
        Some(b) => ([("Content-Type", "application/octet-stream")], b.clone()).into_response(),
        None => StatusCode::NOT_FOUND.into_response(),
    }
}

/// Start the fake on a local port, with a thread that now and then puts out a new version of something the demo
/// installed, and once in a while withdraws one. Returns (its URL, the key).
pub async fn start(tasks: &mut Vec<tokio::task::JoinHandle<()>>) -> (String, String) {
    let fake: Shared = Arc::new(Mutex::new(FakeForge::new()));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.expect("a local port for the demo Forge");
    let base = format!("http://127.0.0.1:{}", listener.local_addr().unwrap().port());
    fake.lock().base = base.clone();
    let app = Router::new().route("/api/{*rest}", any(api)).route("/assets/{sha}", get(asset)).with_state(fake.clone());
    tasks.push(tokio::spawn(async move {
        let _ = axum::serve(listener, app).await;
    }));
    tasks.push(tokio::spawn(async move {
        loop {
            let wait = rand::rng().random_range(45.0..90.0);
            tokio::time::sleep(Duration::from_secs_f64(wait)).await;
            let mut f = fake.lock();
            let mut live: Vec<String> = f.fetched.iter().filter(|l| f.listings.iter().any(|x| x["id"] == **l && x["status"] == "published")).cloned().collect();
            live.sort();
            if let Some(lid) = live.choose(&mut rand::rng()).cloned() {
                let roll: f64 = rand::rng().random();
                if roll < 0.15 {
                    f.withdraw(&lid);
                } else if roll < 0.35 {
                    f.withdraw_version(&lid);
                } else {
                    f.publish(&lid);
                }
            }
        }
    }));
    (base, DEMO_KEY.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::forge::{listing_id, versions_of, ForgeClient};
    use crate::util::Secret;

    #[tokio::test]
    async fn publish_edit_withdraw_delete() {
        let mut tasks = vec![];
        let (base, key) = start(&mut tasks).await;
        let f = ForgeClient::new(Secret::new(&key), &base, None).unwrap();
        let quiet = |_: String| {};
        let before = f.mine().await.unwrap().len();
        let file = vec![7u8; 2 * CHUNK + 5];
        let info = json!({"title": "Test Map", "version_label": "1.0", "visibility": "public", "tags": ["test"]});
        let x = f.publish(None, &info, &[("test.mvar".into(), file.clone())], &quiet).await.unwrap();
        let lid = listing_id(&x);
        assert_eq!(x["status"], "published");
        assert_eq!(f.mine().await.unwrap().len(), before + 1);
        let v1 = versions_of(&x)[0]["id"].as_str().unwrap().to_string();
        let man = f.get(&format!("/api/listings/{lid}/versions/{v1}/manifest"), &[], 0.0, false, false).await.unwrap();
        assert_eq!(man["assets"][0]["size"], file.len());

        let y = f.publish(Some(&lid), &json!({"version_label": "1.1", "release_notes": "more"}), &[("b.json".into(), b"{}".to_vec())], &quiet).await.unwrap();
        let v2 = versions_of(&y)[0]["id"].as_str().unwrap().to_string();
        assert_eq!(y["latest_version"]["id"], v2.as_str());
        let e = f.change("edit", &lid, "", &json!({"title": "Renamed"})).await.unwrap();
        assert_eq!((e["title"].as_str(), e["tags"][0].as_str()), (Some("Renamed"), Some("test"))); // the rest kept, not reset
        f.change("withdraw", &lid, &v2, &json!({})).await.unwrap();
        assert_eq!(f.change("withdraw", &lid, &v1, &json!({})).await.unwrap_err().status, 409); // the last one stays

        let bad = f.publish(None, &json!({"title": "Nope", "version_label": "1"}), &[("x.txt".into(), b"hi".to_vec())], &quiet).await.unwrap_err();
        assert_eq!(bad.status, 415);
        assert!(bad.text.contains("Nothing was left half-done"));
        assert_eq!(f.mine().await.unwrap().len(), before + 1);
        f.change("delete", &lid, "", &json!({})).await.unwrap();
        assert_eq!(f.mine().await.unwrap().len(), before);
        tasks.iter().for_each(|t| t.abort());
    }
}
