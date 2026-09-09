"""Scouting routines."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from ares.behaviors.combat import CombatManeuver
from ares.behaviors.combat.individual import KeepUnitSafe, PathUnitToTarget
from ares.consts import UnitRole

from bot.core.types import CombatRoutine

if TYPE_CHECKING:
    from bot.core.context import BotContext

TargetFn = Callable[["BotContext"], Point2]


def enemy_natural_overlook(ctx: "BotContext") -> Point2:
    """Ares' safe high-ground vision spot near the enemy natural."""
    return ctx.mediator.get_ol_spot_near_enemy_nat


def air_scout(
    unit_type: UnitTypeId, target: TargetFn = enemy_natural_overlook
) -> CombatRoutine:
    """Park SCOUTING fliers on a vision spot, retreating from danger first."""

    def routine(ctx: "BotContext") -> None:
        scouts = ctx.mediator.get_units_from_role(
            role=UnitRole.SCOUTING, unit_type=unit_type
        )
        if not scouts:
            return
        grid = ctx.mediator.get_air_grid
        destination = target(ctx)
        for scout in scouts:
            maneuver = CombatManeuver()
            # Safety first: only advance while the path is clear.
            maneuver.add(KeepUnitSafe(unit=scout, grid=grid))
            maneuver.add(
                PathUnitToTarget(
                    unit=scout, grid=grid, target=destination, success_at_distance=2.0
                )
            )
            ctx.bot.register_behavior(maneuver)

    return routine
