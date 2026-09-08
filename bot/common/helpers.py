"""Tiny shared helpers that do not change bot behavior."""

from sc2.bot_ai import BotAI
from sc2.ids.unit_typeid import UnitTypeId

__all__ = ["pool_started", "pool_pending_or_none", "safe_already_pending"]


def safe_already_pending(bot: BotAI, unit_type: UnitTypeId) -> int:
    """already_pending can KeyError on unknown ability ids late-game; never crash."""
    try:
        return int(bot.already_pending(unit_type))
    except (KeyError, AttributeError, TypeError, ValueError):
        return 0


def pool_started(bot: BotAI) -> bool:
    """True if a spawning pool exists or is pending."""
    return (
        bot.structures(UnitTypeId.SPAWNINGPOOL).amount
        + safe_already_pending(bot, UnitTypeId.SPAWNINGPOOL)
        != 0
    )


def pool_pending_or_none(bot: BotAI) -> bool:
    """True if there is no spawning pool and none pending."""
    return (
        bot.structures(UnitTypeId.SPAWNINGPOOL).amount
        + safe_already_pending(bot, UnitTypeId.SPAWNINGPOOL)
        == 0
    )
