"""Tiny shared helpers that do not change bot behavior."""

from sc2.bot_ai import BotAI
from sc2.ids.unit_typeid import UnitTypeId

from bot.common.log import log_event

__all__ = ["pool_started", "pool_pending_or_none", "log_event"]


def pool_started(bot: BotAI) -> bool:
    """True if a spawning pool exists or is pending."""
    return (
        bot.structures(UnitTypeId.SPAWNINGPOOL).amount
        + bot.already_pending(UnitTypeId.SPAWNINGPOOL)
        != 0
    )


def pool_pending_or_none(bot: BotAI) -> bool:
    """True if there is no spawning pool and none pending."""
    return (
        bot.structures(UnitTypeId.SPAWNINGPOOL).amount
        + bot.already_pending(UnitTypeId.SPAWNINGPOOL)
        == 0
    )
