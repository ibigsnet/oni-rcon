"""The Appropriate Legal Notices (AGPL-3.0, sections 0 and 5(d)), as the desktop app's lib/about.ts has them: who made
ONI RCON, its licence, that there is no warranty, and where its source is. NOTICE's additional terms (section 7) ask a
modified version to keep them and to say it's modified: change MODIFIED and SOURCE for yours."""
MADE_BY = "ONI RCON by Arche Labs (viik)"
SOURCE = "https://github.com/viik2k/oni-rcon"
MODIFIED = "Modified by ibigs, LLC (source: https://github.com/ibigsnet/oni-rcon)"  # for a modified version: who changed it, e.g. "Modified by Example Hosting"; "" for the original


def about_line() -> str:
    """One line, for the field manual."""
    return f"{MADE_BY}{f' · {MODIFIED}' if MODIFIED else ''} · AGPL-3.0-or-later · no warranty · {SOURCE}"


def about_text(version: str) -> str:
    """The ABOUT screen's text."""
    return "\n".join([
        f"{MADE_BY}{f', version {version}' if version else ''}.",
        *([f"{MODIFIED}: this isn't the original ONI RCON."] if MODIFIED else []),
        "Portions by ibigs, LLC (RifleJock), from github.com/ibigsnet/oni-rcon, under the GNU GPL version 3 or later.",
        "",
        "Free software under the GNU Affero General Public License, version 3 or later: you may share and change it under "
        "its terms. There is NO WARRANTY, to the extent the law allows.",
        "The licence is LICENSE in the source, also at https://www.gnu.org/licenses/agpl-3.0.html. NOTICE in the source "
        "has its additional terms: a modified version keeps these credits and says it's modified.",
        "",
        f"Source: {SOURCE}",
        "",
        "Unofficial and fan-made: not affiliated with Microsoft, Halo Studios, the Project Reclaimer team or any other "
        "project it works with.",
    ])
