"""Which role a freshly created unit gets.

A table rather than an if/elif chain: with three races and dozens of builds
the chain is the thing that would grow without bound.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ares.consts import UnitRole
from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.unit import Unit

if TYPE_CHECKING:
    from bot.core.context import BotContext

# Non-army support units that want a specific role on creation.
SUPPORT_ROLES: dict[Race, dict[UnitTypeId, UnitRole]] = {
    Race.Zerg: {UnitTypeId.QUEEN: UnitRole.QUEEN_INJECT},
    Race.Terran: {},
    Race.Protoss: {
        # Keep these out of DEFENDING/ATTACKING or they suicide with the ball
        # (Warp Prism especially) and pad wave1_min with non-Zealots.
        UnitTypeId.WARPPRISM: UnitRole.DROP_SHIP,
        UnitTypeId.OBSERVER: UnitRole.SCOUTING,
        UnitTypeId.ADEPT: UnitRole.HARASSING_ADEPT,
    },
}

# One flier per race is spent on map vision; the rest keep the default role.
SCOUT_TYPES: dict[Race, UnitTypeId | None] = {
    Race.Zerg: UnitTypeId.OVERLORD,
    Race.Terran: None,
    Race.Protoss: None,
}


def assign_on_created(ctx: "BotContext", unit: Unit) -> None:
    """Ares auto-assigns GATHERING to workers; everything else starts roleless."""
    race = ctx.build.race

    if unit.type_id in ctx.build.army.types:
        ctx.mediator.assign_role(tag=unit.tag, role=UnitRole.DEFENDING)
        return

    support = SUPPORT_ROLES.get(race, {}).get(unit.type_id)
    if support is not None:
        ctx.mediator.assign_role(tag=unit.tag, role=support)
        return

    if unit.type_id == SCOUT_TYPES.get(race) and not ctx.state.scout_tags:
        # The game-start supply unit never fires on_unit_created, so the first
        # one seen here is the second one — spend it on vision.
        ctx.state.scout_tags.add(unit.tag)
        ctx.mediator.assign_role(tag=unit.tag, role=UnitRole.SCOUTING)
        ctx.log("SCOUT assigned")

    if ctx.build.on_unit_created is not None:
        ctx.build.on_unit_created(ctx, unit)


def forget_destroyed(ctx: "BotContext", unit_tag: int) -> None:
    """Let a replacement take over scouting duty."""
    ctx.state.scout_tags.discard(unit_tag)
