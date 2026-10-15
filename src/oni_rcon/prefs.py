"""What the console remembers between runs about its boot screen: whether the full sequence has played, and whether
the operator wants it every time. Kept next to the config file; a missing or unreadable file reads as the defaults."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .config import write_atomic


@dataclass
class Prefs:
    path: Path | None = None  # None: nothing is kept (tests, and anywhere there's no config directory)
    intro_seen: bool = False  # the full boot sequence has played once
    full_intro: bool = False  # before 0.9.0+ibigs.24: play the full sequence every time
    intro_choice: str = ""    # which single animation plays at start: "full" (the archive) or "quick" (the splash)

    @classmethod
    def load(cls, path: Path | None) -> Prefs:
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path else {}
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        choice = data.get("intro_choice")
        return cls(path, data.get("intro_seen") is True, data.get("full_intro") is True,
                   choice if choice in ("full", "quick") else "")

    def save(self) -> None:
        if self.path:
            try:
                write_atomic(self.path, json.dumps({"intro_seen": self.intro_seen, "full_intro": self.full_intro,
                                                    "intro_choice": self.intro_choice}, indent=2) + "\n")
            except OSError:
                pass  # a read-only profile mustn't stop the console: the sequence just plays again next time

    def intro_mode(self) -> str:
        """Which boot animation to play when nothing says otherwise: the long one (the Halo archive) the first
        time, then whichever single one the user picked - long or short, never both. Any key skips either."""
        if not self.intro_seen:
            return "full"
        return self.intro_choice or ("full" if self.full_intro else "quick")
