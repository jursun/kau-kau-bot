"""Runs a build's macro steps. Deliberately has nothing to decide."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ares.behaviors.macro import MacroPlan

if TYPE_CHECKING:
    from bot.core.context import BotContext


class MacroEngine:
    """Turns `build.always` and `build.macro_steps` into registered behaviors.

    All variation between builds lives in which steps they list, so this class
    should never need to grow.
    """

    def execute(self, ctx: "BotContext") -> None:
        for step in ctx.build.always:
            if (behavior := step(ctx)) is not None:
                ctx.bot.register_behavior(behavior)

        # The BuildOrderRunner owns spending until the opening finishes.
        if not ctx.build_completed:
            return

        ctx.log_once("build_done", f"OPENING complete ({ctx.build.label})")

        plan = MacroPlan()
        for step in ctx.build.macro_steps:
            if (behavior := step(ctx)) is not None:
                plan.add(behavior)
        ctx.bot.register_behavior(plan)
