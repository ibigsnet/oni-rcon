// The community projects a server can run. Oni speaks Project Reclaimer's RCON, and another project can speak it too
// (docs/OTHER-GAMES.md): its sign-in reply names it (`project`), and what is Reclaimer's alone (its releases and versions,
// ReclaimerForge, its dedicated.toml settings and file formats) is turned off for that server.
import { mixedVersions, type Dict } from "./util";

export interface Project { id: string; game: string; name: string; tag: string; reclaimer: boolean }

export const PROJECTS: Project[] = [
  { id: "reclaimer", game: "Halo 3", name: "Project Reclaimer", tag: "H3", reclaimer: true },
  { id: "gravemind", game: "Halo 2", name: "Project Gravemind", tag: "H2", reclaimer: false },
  { id: "renaissance", game: "Halo: Reach", name: "Reach Renaissance", tag: "REACH", reclaimer: false },
];

/** A server's words as one plain line within `n` characters. */
const line = (v: unknown, n: number) => String(v ?? "").replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, n);
const key = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");

/** The project a server's sign-in reply names: `project` (an id above, or its name), with `game` for one Oni doesn't
 *  know. Project Reclaimer when it names none, as Reclaimer's own reply doesn't. */
export function projectOf(info: Dict | null | undefined): Project {
  const named = line(info?.project, 40);
  if (!named) return PROJECTS[0];
  const k = key(named);
  const known = PROJECTS.find((p) => k === p.id || k === key(p.name) || k === `project${p.id}`);
  if (known) return known;
  const game = line(info?.game, 30);
  const words = (game || named).split(/[^A-Za-z0-9]+/).filter(Boolean); // "Halo: Combat Evolved" is HCE, "Halo 2" H2
  const tag = (words.length > 1 ? words.map((w) => (/^\d+$/.test(w) ? w : w[0])).join("") : words[0] ?? "?").toUpperCase().slice(0, 6);
  return { id: k || "other", game: game || "another game", name: named, tag, reclaimer: false };
}

/** Why a Reclaimer-only part of Oni isn't offered for this server, or "" on a Project Reclaimer one. */
export function reclaimerOnly(info: Dict | null | undefined, what = "This"): string {
  const p = projectOf(info);
  return p.reclaimer ? "" : `${what} is Project Reclaimer's (Halo 3), and this server runs ${p.name} (${p.game}).`;
}

/** mixedVersions for each project on its own: versions of different games don't compare. Named by project when the
 *  stations run more than one. */
export function mixedByProject(stations: { label: string; info: Dict }[]): string {
  const by = new Map<string, { p: Project; list: { label: string; version: unknown }[] }>();
  for (const s of stations) {
    const p = projectOf(s.info);
    if (!by.has(p.id)) by.set(p.id, { p, list: [] });
    by.get(p.id)!.list.push({ label: s.label, version: s.info?.version });
  }
  return [...by.values()].map(({ p, list }) => {
    const mix = mixedVersions(list);
    return mix && by.size > 1 ? `${p.name}: ${mix}` : mix;
  }).filter(Boolean).join("  ·  ");
}
