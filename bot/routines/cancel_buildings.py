"""Cancel structures under construction that the enemy is about to kill.

A building that dies half-built returns nothing; cancelling it returns 75%
of its cost (and, for Zerg, the Drone). So when enemy units are shooting a
structure that is still under construction and it will not survive them,
cancel it.

Doomed means: it will not finish (progress < `FINISH_ALMOST`) and the enemy
units in range of it would kill it within `CANCEL_TTL_S` at their combined
ground DPS - or it is already below `CANCEL_HEALTH_FRACTION` of its max health
with enemies on top of it. A lone Zealot poking a Hatchery does not qualify.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ares.behaviors.combat.individual import UseAbility
from cython_extensions import cy_distance_to
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId

from bot.core.types import CombatRoutine
from bot.intel import enemy_army

if TYPE_CHECKING:
    from bot.core.context import BotContext

DANGER_RADIUS: float = 9.0
"""Enemy units this close to the structure count toward its incoming DPS."""
CANCEL_TTL_S: float = 3.5
"""Cancel when the enemy would kill the structure within this long."""
CANCEL_HEALTH_FRACTION: float = 0.2
"""...or when it is this hurt with enemies in range."""
FINISH_ALMOST: float = 0.9
"""Past this much progress it is about to finish: let it."""
REFUND_FRACTION: float = 0.75

NEVER_CANCEL: frozenset[UnitTypeId] = frozenset(
    {
        # Creep Tumors cost nothing to cancel for, and cancelling one only
        # loses the creep it was about to spread (live: three cancelled
        # "for -38 minerals").
        UnitTypeId.CREEPTUMOR,
        UnitTypeId.CREEPTUMORQUEEN,
        UnitTypeId.CREEPTUMORBURROWED,
    }
)


def _incoming_dps(structure, enemies: list) -> tuple[float, int]:
    """(combined ground DPS, unit count) of the enemies in range of `structure`."""
    dps = 0.0
    count = 0
    for enemy in enemies:
        if not getattr(enemy, "can_attack_ground", True):
            continue
        if cy_distance_to(structure.position, enemy.position) > DANGER_RADIUS:
            continue
        dps += float(getattr(enemy, "ground_dps", 0.0) or 0.0)
        count += 1
    return dps, count


def is_doomed(structure, enemies: list) -> bool:
    """True when `structure` (under construction) will die before it finishes."""
    if structure.is_ready or structure.build_progress >= FINISH_ALMOST:
        return False
    dps, count = _incoming_dps(structure, enemies)
    if count == 0 or dps <= 0.0:
        return False
    health = float(structure.health)
    if health / dps <= CANCEL_TTL_S:
        return True
    health_max = float(getattr(structure, "health_max", 0.0) or 0.0)
    return health_max > 0.0 and health <= CANCEL_HEALTH_FRACTION * health_max


def cancel_doomed_buildings() -> CombatRoutine:
    """Cancel every under-construction structure of ours that `is_doomed`.

    Only structures with enemies shooting them are looked at, so the check is
    cheap on a quiet map.
    """

    def routine(ctx: "BotContext") -> None:
        enemies = enemy_army(ctx)
        if not enemies:
            return
        cancelled = ctx.state.cancelled_building_tags
        for structure in ctx.bot.structures:
            if structure.tag in cancelled or structure.is_ready:
                continue
            if structure.type_id in NEVER_CANCEL:
                continue
            if not is_doomed(structure, enemies):
                continue
            cost = ctx.bot.calculate_cost(structure.type_id)
            if cost.minerals + cost.vespene <= 0:
                continue  # nothing to refund
            cancelled.add(structure.tag)
            ctx.bot.register_behavior(
                UseAbility(AbilityId.CANCEL_BUILDINPROGRESS, structure)
            )
            ctx.log(
                f"CANCEL {structure.type_id.name} {structure.health:.0f}/"
                f"{structure.health_max:.0f} hp at {structure.build_progress:.0%} - "
                f"refunds {REFUND_FRACTION * cost.minerals:.0f} minerals / "
                f"{REFUND_FRACTION * cost.vespene:.0f} gas"
            )

    return routine
