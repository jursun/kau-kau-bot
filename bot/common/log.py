"""ASCII-safe event logging for rush timings."""

from sc2.bot_ai import BotAI


def log_event(bot: BotAI, message: str) -> None:
    """Print [mm:ss] message using game clock when available."""
    try:
        t = bot.time_formatted
    except Exception:
        t = "--:--"
    print(f"[{t}] {message}")
