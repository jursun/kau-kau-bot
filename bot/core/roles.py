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

from bot.consts import CORRUPTOR_ROLE, SWARM_HOST_ROLE, ZERGLING_DEFENDER_ROLE

if TYPE_CHECKING:
    from bot.core.context import BotContext

# Non-army support units that want a specific role on creation.
SUPPORT_ROLES: dict[Race, dict[UnitTypeId, UnitRole]] = {
    Race.Zerg: {
        UnitTypeId.QUEEN: UnitRole.QUEEN_INJECT,
        # Keep these out of DEFENDING/ATTACKING (see Protoss's Prism/Observer/
        # Adept below) - `release_waves()` would otherwise promote a Swarm
        # Host or Corruptor straight into a squad muster it must never join,
        # and a Zergling into an offensive wave it's meant to stay out of
        # (see `consts.ZERGLING_DEFENDER_ROLE`).
        UnitTypeId.SWARMHOSTMP: SWARM_HOST_ROLE,
        UnitTypeId.CORRUPTOR: CORRUPTOR_ROLE,
        UnitTypeId.ZERGLING: ZERGLING_DEFENDER_ROLE,
    },
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


def assign_starting_scout(ctx: "BotContext") -> None:
    """Send the game-start scout unit (Zerg's starting Overlord) scouting.

    It exists before `on_start` runs, so it never fires `on_unit_created` -
    the hook `assign_on_created` below relies on to spend a scout, which
    would otherwise wait for a freshly *produced* unit of the same type and
    leave the starting one sitting idle at home forever.
    """
    race = ctx.build.race
    scout_type = SCOUT_TYPES.get(race)
    if scout_type is None or ctx.state.scout_tags:
        return
    existing = ctx.bot.units(scout_type)
    if not existing:
        return
    unit = existing.first
    ctx.state.scout_tags.add(unit.tag)
    ctx.mediator.assign_role(tag=unit.tag, role=UnitRole.SCOUTING)
    ctx.log("SCOUT assigned (starting unit)")


def assign_on_created(ctx: "BotContext", unit: Unit) -> None:
    """Ares auto-assigns GATHERING to workers; everything else starts roleless.

    `ctx.build.on_unit_created` always fires last, regardless of which (if
    any) role branch above it matched - a build reacting to a specific unit
    type that's also in `army.types` or `SUPPORT_ROLES` (e.g. `chargelot_
    metrics.note_unit_created`, which watches Stalkers/Zealots) must not be
    skipped by an early return here.
    """
    race = ctx.build.race

    if unit.type_id in ctx.build.army.types:
        ctx.mediator.assign_role(tag=unit.tag, role=UnitRole.DEFENDING)
    else:
        support = SUPPORT_ROLES.get(race, {}).get(unit.type_id)
        if support is not None:
            ctx.mediator.assign_role(tag=unit.tag, role=support)
        elif unit.type_id == SCOUT_TYPES.get(race) and not ctx.state.scout_tags:
            # Normally already spent by `assign_starting_scout` on the
            # game-start unit; this only fires if that unit died before this
            # one was created (`forget_destroyed` clears `scout_tags`), or
            # for a race whose scout type isn't the starting supply unit.
            ctx.state.scout_tags.add(unit.tag)
            ctx.mediator.assign_role(tag=unit.tag, role=UnitRole.SCOUTING)
            ctx.log("SCOUT assigned")

    if ctx.build.on_unit_created is not None:
        ctx.build.on_unit_created(ctx, unit)


def forget_destroyed(ctx: "BotContext", unit_tag: int) -> None:
    """Drop destroyed scout / worker-harass tags."""
    if unit_tag in ctx.state.scout_tags:
        ctx.state.scout_tags.discard(unit_tag)

    if unit_tag not in ctx.state.worker_harass_tags:
        return
    ctx.state.worker_harass_tags.discard(unit_tag)
    ctx.log(
        f"HARASS combat (worker died): "
        f"dealt {ctx.state.worker_harass_damage_dealt:.0f}, "
        f"took {ctx.state.worker_harass_damage_taken:.0f}"
    )
    ctx.state.worker_harass_done = True
    ctx.state.worker_harass_prey_hp.clear()
