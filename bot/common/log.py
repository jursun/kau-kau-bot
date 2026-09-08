"""ASCII-safe event logging for rush timings."""

from __future__ import annotations

import sys

from sc2.bot_ai import BotAI


def log_event(bot: BotAI, message: str) -> None:
    """Print [mm:ss] message using game clock when available. Always flush."""
    try:
        t = bot.time_formatted
    except Exception:
        t = "--:--"
    print(f"[{t}] {message}", flush=True)
    try:
        sys.stdout.flush()
    except Exception:
        pass
