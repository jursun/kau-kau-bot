"""Scouting routines."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from ares.behaviors.combat.individual import PathUnitToTarget
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
    """Park SCOUTING fliers on a vision spot.

    No danger-avoidance here on purpose: this is only ever the opening
    scouting Overlord (see `core/roles.py`'s `SCOUT_TYPES`), and it needs to
    reach its vision spot and stay there — a `KeepUnitSafe` check used to
    make it retreat the moment anything came near, which is exactly the
    threats it exists to keep watching. Combat units that DO need to dodge
    danger while moving (the escort Overseers, for instance) use
    `KeepUnitSafe`/`MoveToSafeTarget` in `routines/combat.py` instead.
    """

    def routine(ctx: "BotContext") -> None:
        scouts = ctx.mediator.get_units_from_role(
            role=UnitRole.SCOUTING, unit_type=unit_type
        )
        if not scouts:
            return
        grid = ctx.mediator.get_air_grid
        destination = target(ctx)
        for scout in scouts:
            ctx.bot.register_behavior(
                PathUnitToTarget(
                    unit=scout, grid=grid, target=destination, success_at_distance=2.0
                )
            )

    return routine
