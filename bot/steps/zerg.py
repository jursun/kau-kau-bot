"""Zerg-specific macro steps.

Anything that mentions a hatchery, a queen or larva belongs here rather than
in `steps/common.py`, so the shared engine stays race-neutral.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from bot.behaviors.zerg import InjectLarva, TrainQueens
from bot.builds.definition import _always
from bot.core.types import Gate, MacroStep
from bot.steps import common

if TYPE_CHECKING:
    from bot.core.context import BotContext


def inject_larva(min_energy: int = 25) -> MacroStep:
    """Belongs in `always` — injects must keep running during the opening."""

    def step(ctx: "BotContext"):
        return InjectLarva(min_energy=min_energy)

    return step


def train_queens(per_base: int = 1, maximum: int = 4) -> MacroStep:
    def step(ctx: "BotContext"):
        return TrainQueens(
            to_count=min(maximum, ctx.base_count * per_base),
            max_per_townhall=per_base,
        )

    return step


def macro_hatch(count: int, gate: Gate = _always) -> MacroStep:
    """Extra in-base hatcheries, purely for larva."""
    return common.structure(UnitTypeId.HATCHERY, count, gate)


def evolution_chambers(count: int, gate: Gate = _always) -> MacroStep:
    """`UpgradeController` only ever builds one; a second enables +1/+1 in parallel."""
    return common.structure(UnitTypeId.EVOLUTIONCHAMBER, count, gate)


def spore_crawlers(count: int, gate: Gate = _always) -> MacroStep:
    return common.structure(UnitTypeId.SPORECRAWLER, count, gate)


def spine_crawlers(count: int, gate: Gate = _always) -> MacroStep:
    return common.structure(UnitTypeId.SPINECRAWLER, count, gate)


LING_SPEED: UpgradeId = UpgradeId.ZERGLINGMOVEMENTSPEED
