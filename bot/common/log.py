"""ASCII-safe event logging for rush timings.

Kept from the python-sc2 version so the `[mm:ss] EVENT` timeline in local
`--validate` logs stays greppable. Ares itself logs through loguru; this is
deliberately plain stdout so the two streams don't interleave awkwardly.
"""

from __future__ import annotations

import sys
from typing import Any


def log_event(bot: Any, message: str) -> None:
    """Print `[mm:ss] message` using the game clock when available."""
    try:
        t = bot.time_formatted
    except Exception:
        t = "--:--"
    print(f"[{t}] {message}", flush=True)
    try:
        sys.stdout.flush()
    except Exception:
        pass


class LogOnce:
    """Tiny helper so managers can log a milestone exactly once."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def __call__(self, bot: Any, key: str, message: str) -> bool:
        if key in self._seen:
            return False
        self._seen.add(key)
        log_event(bot, message)
        return True

    def reset(self, key: str) -> None:
        self._seen.discard(key)
