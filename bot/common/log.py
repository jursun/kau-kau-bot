"""Event logging for rush timings, routed through ares' loguru logger.

Kept from the python-sc2 version so the `[mm:ss] EVENT` timeline in local
`--validate` logs stays greppable, now going through the same loguru sink
Ares itself logs through instead of a raw `print()`.
"""

from __future__ import annotations

from typing import Any

from loguru import logger


def log_event(bot: Any, message: str) -> None:
    """Log `[mm:ss] message` at info level, using the game clock when available."""
    try:
        t = bot.time_formatted
    except Exception:
        t = "--:--"
    logger.info(f"[{t}] {message}")


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
