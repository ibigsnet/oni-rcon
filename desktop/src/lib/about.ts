// The Appropriate Legal Notices (AGPL-3.0, sections 0 and 5(d)): who made ONI RCON, its licence, that there is no
// warranty, and where its source is. NOTICE's additional terms (section 7) ask a modified version to keep them and to say
// it's modified: change MODIFIED and SOURCE for yours.
export const MADE_BY = "ONI RCON by Arche Labs (viik)";
export const SOURCE = "https://github.com/viik2k/oni-rcon";
/** For a modified version: who changed it, e.g. "Modified by Example Hosting". "" for the original. */
export const MODIFIED = "";

/** One line, for the field manual and the setup screen. */
export const aboutLine = () => `${MADE_BY}${MODIFIED ? ` · ${MODIFIED}` : ""} · AGPL-3.0-or-later · no warranty · ${SOURCE}`;

/** The About screen's text. */
export function aboutText(version: string): string {
  return [
    `${MADE_BY}${version ? `, version ${version}` : ""}.`,
    ...(MODIFIED ? [`${MODIFIED}: this isn't the original ONI RCON.`] : []),
    "Portions by ibigs, LLC (RifleJock), from github.com/ibigsnet/oni-rcon, under the GNU GPL version 3 or later.",
    "",
    "Free software under the GNU Affero General Public License, version 3 or later: you may share and change it under its "
      + "terms. There is NO WARRANTY, to the extent the law allows.",
    "The licence is LICENSE in the source, also at https://www.gnu.org/licenses/agpl-3.0.html. NOTICE in the source has "
      + "its additional terms: a modified version keeps these credits and says it's modified.",
    "",
    `Source: ${SOURCE}`,
    "",
    "Unofficial and fan-made: not affiliated with Microsoft, Halo Studios, the Project Reclaimer team or any other project "
      + "it works with.",
  ].join("\n");
}
