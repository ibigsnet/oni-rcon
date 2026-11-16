// Server frame rate, as F7, F3 and the station cards show it. From ibigs/oni-rcon's perf.py and perf_view.py, by
// RifleJock. The backend reads the lag reports out of each server's log (health.rs); this is what they mean.
import { AMBER, GREEN, RED } from "../art/palette";
import { projectOf } from "./projects";
import { semver, since, type Dict } from "./util";

export const TARGET = 60; // the server's frame rate when it keeps up
export const SLOW = 54; // what the server calls falling behind
export const BAD = 40; // under this play is visibly choppy
export const TARGET_MS = 1000 / TARGET;

export const level = (fps: number): "ok" | "warn" | "crit" => (fps >= SLOW ? "ok" : fps >= BAD ? "warn" : "crit");
export const LEVEL_COLOR = { ok: GREEN, warn: AMBER, crit: RED };

/** RCON `health` (frame rate, traffic) is Reclaimer 0.10's. Asked too when the sign-in named no version: the reply
 *  names one. */
export const asksHealth = (info: Dict) => projectOf(info).reclaimer && (since(info.version, "0.10.0") || !semver(info.version));

/** Reclaimer 0.10+'s RCON `health` reply as F7's row: fps null while idle (an empty lobby reports 0), frame times in
 *  ms, traffic in bytes a second. null when the reply has no frame rate (0.9, another game). */
export function rconFrames(d: unknown) {
  if (!d || typeof d !== "object") return null;
  const n = (k: string) => { const v = (d as Dict)[k]; return typeof v === "number" && Number.isFinite(v) ? v : null; };
  const fps = n("frame_rate");
  if (fps === null) return null;
  return { fps: fps > 0 ? fps : null, p50: n("frame_ms_p50"), p99: n("frame_ms_p99"), max: n("frame_ms_max"),
    up: n("upload_bytes_per_second"), down: n("download_bytes_per_second"), players: n("players") };
}

/** Bytes a second, the way a person reads them: 950 B/s, 12.3 KB/s, 1.2 MB/s. */
export const rate = (bps: number | null) => (bps === null ? "—" : bps >= 1e6 ? `${(bps / 1e6).toFixed(1)} MB/s`
  : bps >= 1e3 ? `${(bps / 1e3).toFixed(1)} KB/s` : `${Math.round(bps)} B/s`);

/** The chart's top in ms: room for the longest frame, never under 50 (3x the target), a round number, at most 500. */
export function scaleTop(hist: Dict[]): number {
  const peak = Math.max(50, ...hist.map((b) => b.longest || (b.fps ? 1000 / b.fps : 0)));
  const step = peak <= 150 ? 25 : 50;
  return Math.min(500, Math.ceil(peak / step) * step);
}

/** [what's happening, what to do], for someone who doesn't need to know what a thread is. `r` is a server's reading and
 *  `hist` its last 30 minutes, oldest first. */
export function advice(r: Dict | undefined, hist: Dict[]): [string, string] {
  if (!r) return ["Not measured yet.", "Frame rate comes from the server's log: health_cmd has to print it (see ? help)."];
  if (!r.behind && !r.slow_minutes) return [`Running smoothly: ${TARGET} frames a second.`, "Nothing to do."];
  const crowds = hist.filter((b) => b.players && b.fps !== null && b.fps < SLOW).map((b) => b.players as number);
  const crowd = crowds.length ? ` It has fallen behind with as few as ${Math.min(...crowds)} players in the last 30 minutes.` : "";
  const head = (r.behind ? `Running at ${Math.round(r.fps)} of ${TARGET} frames a second, so players see lag and rubber-banding.`
    : `Smooth now, but it dipped to ${r.worst} frames a second in the last 5 minutes.`) + crowd;
  return [head, "The game runs almost everything on one processor core, so more cores won't help: lower the player limit, or move this server to a machine with faster cores. If the host's memory is short (below), or a heavy map or mod is in the rotation, that does it too."];
}
