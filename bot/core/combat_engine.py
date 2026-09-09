"""Runs a build's combat routines."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.core.context import BotContext


class CombatEngine:
    """Each routine issues orders for one concern; order is priority."""

    def execute(self, ctx: "BotContext") -> None:
        for routine in ctx.build.combat.routines:
            routine(ctx)
