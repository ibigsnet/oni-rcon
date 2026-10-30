// npm run release -- 1.3.0 [--push]
// Bumps the version everywhere it lives, commits, and tags desktop-v<version>. Pushing that tag is what builds,
// signs and publishes the release, so it only happens with --push.
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";

const [version, flag] = process.argv.slice(2);
const tag = `desktop-v${version}`;
const git = (...a) => execFileSync("git", a, { encoding: "utf8" }).trim();
const die = (m) => { console.error(m); process.exit(1); };

const current = JSON.parse(readFileSync("package.json", "utf8")).version;
const newer = (a, b) => {
  const [x, y] = [a, b].map((s) => s.split(".").map(Number));
  const i = x.findIndex((n, k) => n !== y[k]);
  return i >= 0 && x[i] > y[i];
};
if (!/^\d+\.\d+\.\d+$/.test(version ?? "") || !newer(version, current)) die(`usage: npm run release -- X.Y.Z [--push]  (newer than ${current})`);
if (flag && flag !== "--push") die(`unknown option ${flag}`);
if (git("status", "--porcelain")) die("commit or stash your changes first");
if (git("tag", "-l", tag)) die(`${tag} already exists`);

// [file, pattern for the old version with the text before it captured, how many places]
const v = current.replaceAll(".", "\\.");
const places = [
  ["package.json", new RegExp(`(^  "version": )"${v}"`, "m"), 1],
  ["package-lock.json", new RegExp(`("name": "oni-rcon-desktop",\\s*"version": )"${v}"`, "g"), 2],
  ["src-tauri/tauri.conf.json", new RegExp(`(^  "version": )"${v}"`, "m"), 1],
  ["src-tauri/Cargo.toml", new RegExp(`(^version = )"${v}"`, "m"), 1],
  ["src-tauri/Cargo.lock", new RegExp(`(name = "oni-rcon"\\r?\\nversion = )"${v}"`), 1],
];
const edits = places.map(([file, re, n]) => {
  const text = readFileSync(file, "utf8");
  if ((text.match(new RegExp(re.source, "gm")) ?? []).length !== n) die(`${file}: expected ${n} place(s) at ${current}`);
  return [file, text.replace(re, `$1"${version}"`)];
});
edits.forEach(([file, text]) => writeFileSync(file, text));

git("add", ...places.map(([file]) => file));
git("commit", "-m", `Desktop ${version}`);
git("tag", tag);
if (flag) console.log(git("push", "origin", "HEAD", tag) || `pushed ${tag}`);
else console.log(`Tagged ${tag}. Push it to release:  git push origin HEAD ${tag}`);
