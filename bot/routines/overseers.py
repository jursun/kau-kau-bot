"""Overseer role assignment, movement, and changeling vision.

Macro Zerg maintains three Overseers after Lair: home detection, army
detection, and enemy-base scouting. Roles are sticky by tag in `RunState`
and only reassigned when a tagged Overseer dies.

Changelings get a sticky opponent-base destination (also in `RunState`) so
frame-to-frame unit-list reshuffles cannot bounce them between targets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ares.behaviors.combat import CombatManeuver
from ares.behaviors.combat.individual import AMove, KeepUnitSafe, MoveToSafeTarget, UseAbility
from ares.consts import UnitRole
from cython_extensions import cy_distance_to_squared, cy_towards
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.consts import ALL_TOWNHALL_TYPES
from bot.core.types import CombatRoutine
from bot.routines import targeting

if TYPE_CHECKING:
    from sc2.unit import Unit

    from bot.core.context import BotContext

# Match `routines.combat.SQUAD_RADIUS` (local to avoid import cycle).
_SQUAD_RADIUS: float = 9.0

_CHANGELING_TYPES: frozenset[UnitTypeId] = frozenset(
    {
        UnitTypeId.CHANGELING,
        UnitTypeId.CHANGELINGZERGLING,
        UnitTypeId.CHANGELINGZERGLINGWINGS,
        UnitTypeId.CHANGELINGMARINE,
        UnitTypeId.CHANGELINGMARINESHIELD,
        UnitTypeId.CHANGELINGZEALOT,
    }
)

_SPAWN_CHANGELING = AbilityId.SPAWNCHANGELING_SPAWNCHANGELING

# Sit for vision once this close; re-issuing AMove on top of the tile is
# what made parked changelings jitter in place.
_CHANGELING_ARRIVE_SQ: float = 3.0**2
# Snap visible townhalls / sticky dests onto the same base slot.
_BASE_MATCH_SQ: float = 10.0**2


def _alive_overseers(ctx: "BotContext") -> dict[int, "Unit"]:
    return {u.tag: u for u in ctx.bot.units(UnitTypeId.OVERSEER)}


def assign_overseer_roles(ctx: "BotContext") -> None:
    """Fill home / army / scout slots; clear tags for dead Overseers."""
    alive = _alive_overseers(ctx)
    state = ctx.state
    for attr in ("overseer_home_tag", "overseer_army_tag", "overseer_scout_tag"):
        tag = getattr(state, attr)
        if tag is not None and tag not in alive:
            setattr(state, attr, None)

    assigned = {
        t
        for t in (
            state.overseer_home_tag,
            state.overseer_army_tag,
            state.overseer_scout_tag,
        )
        if t is not None
    }
    free = [u for u in alive.values() if u.tag not in assigned]
    for attr in ("overseer_home_tag", "overseer_army_tag", "overseer_scout_tag"):
        if getattr(state, attr) is None and free:
            setattr(state, attr, free.pop(0).tag)


def _safe_air_move(ctx: "BotContext", unit: "Unit", target: Point2) -> None:
    grid = ctx.mediator.get_air_grid
    maneuver = CombatManeuver()
    maneuver.add(KeepUnitSafe(unit=unit, grid=grid))
    maneuver.add(MoveToSafeTarget(unit=unit, grid=grid, target=target))
    ctx.bot.register_behavior(maneuver)


def _home_target(ctx: "BotContext") -> Point2:
    holds = list(ctx.state.zergling_defender_hold.values()) or list(
        ctx.state.defender_hold.values()
    )
    if holds:
        return holds[0]
    return ctx.production_location


def _army_target(ctx: "BotContext") -> Point2 | None:
    squads = ctx.mediator.get_squads(
        role=UnitRole.ATTACKING, squad_radius=_SQUAD_RADIUS
    )
    if not squads:
        return None
    biggest = max(squads, key=lambda squad: len(squad.squad_units))
    return targeting.squad_destination(ctx, biggest.squad_position)


def _scout_target(ctx: "BotContext", scout: "Unit") -> Point2:
    """Next enemy-side expansion to skirt — prefer ones we aren't already on."""
    enemy_start = ctx.bot.enemy_start_locations[0]
    expansions = sorted(
        ctx.bot.expansion_locations_list,
        key=lambda loc: cy_distance_to_squared(loc, enemy_start),
    )
    # Skip our own bases; walk enemy-side ring.
    owned = set(ctx.bot.owned_expansions.keys())
    candidates = [
        loc
        for loc in expansions
        if loc not in owned
        and cy_distance_to_squared(loc, scout.position) > 25.0
    ]
    if not candidates:
        # Offset around enemy main so we don't sit on top of AA.
        return Point2(cy_towards(enemy_start, ctx.production_location, 25.0))
    return candidates[0]


def _cast_changelings(ctx: "BotContext") -> None:
    for overseer in ctx.bot.units(UnitTypeId.OVERSEER):
        if _SPAWN_CHANGELING in overseer.abilities:
            ctx.bot.register_behavior(UseAbility(_SPAWN_CHANGELING, overseer))


def _near_any(point: Point2, bases: list[Point2]) -> Point2 | None:
    for base in bases:
        if cy_distance_to_squared(point, base) <= _BASE_MATCH_SQ:
            return base
    return None


def opponent_base_targets(ctx: "BotContext") -> list[Point2]:
    """Enemy start + enemy-side expansions + any visible enemy townhalls.

    Expansions closer to us than to the enemy start are skipped so we do
    not park vision on our own half of the map.
    """
    enemy_starts = list(ctx.bot.enemy_start_locations)
    if not enemy_starts:
        return []
    enemy_start = enemy_starts[0]
    our_start = ctx.production_location

    bases: list[Point2] = []
    for loc in enemy_starts:
        if _near_any(loc, bases) is None:
            bases.append(Point2(loc))

    expansions = getattr(ctx.mediator, "get_enemy_expansions", None) or []
    for item in expansions:
        loc = item[0] if isinstance(item, tuple) else item
        if cy_distance_to_squared(loc, enemy_start) >= cy_distance_to_squared(
            loc, our_start
        ):
            continue
        if _near_any(loc, bases) is None:
            bases.append(Point2(loc))

    townhalls = ctx.bot.enemy_structures.of_type(ALL_TOWNHALL_TYPES)
    for th in townhalls:
        if _near_any(th.position, bases) is None:
            bases.append(Point2(th.position))
    return bases


def _least_covered_base(
    bases: list[Point2], assigned: dict[int, Point2]
) -> Point2:
    """Prefer opponent bases that currently have the fewest changelings."""
    counts = {id(b): 0 for b in bases}
    for dest in assigned.values():
        matched = _near_any(dest, bases)
        if matched is not None:
            counts[id(matched)] += 1
    return min(bases, key=lambda b: (counts[id(b)], cy_distance_to_squared(b, bases[0])))


def _spread_changelings(ctx: "BotContext") -> None:
    changelings = [u for u in ctx.bot.units if u.type_id in _CHANGELING_TYPES]
    dests = ctx.state.changeling_destinations
    alive_tags = {u.tag for u in changelings}
    for tag in list(dests):
        if tag not in alive_tags:
            del dests[tag]

    bases = opponent_base_targets(ctx)
    if not bases:
        return

    for unit in changelings:
        current = dests.get(unit.tag)
        matched = _near_any(current, bases) if current is not None else None
        if matched is None:
            dests.pop(unit.tag, None)
            matched = _least_covered_base(bases, dests)
            dests[unit.tag] = matched
        else:
            # Canonicalize onto the live base list entry.
            dests[unit.tag] = matched

        if cy_distance_to_squared(unit.position, matched) > _CHANGELING_ARRIVE_SQ:
            ctx.bot.register_behavior(AMove(unit=unit, target=matched))


def manage_overseers() -> CombatRoutine:
    """Home / army / scout Overseers plus changeling vision spam."""

    def routine(ctx: "BotContext") -> None:
        if not ctx.bot.units(UnitTypeId.OVERSEER):
            # Still drive any leftover changelings.
            _spread_changelings(ctx)
            return

        assign_overseer_roles(ctx)
        state = ctx.state
        alive = _alive_overseers(ctx)

        if (tag := state.overseer_home_tag) is not None and tag in alive:
            _safe_air_move(ctx, alive[tag], _home_target(ctx))

        if (tag := state.overseer_army_tag) is not None and tag in alive:
            army_target = _army_target(ctx)
            if army_target is not None:
                _safe_air_move(ctx, alive[tag], army_target)
            else:
                _safe_air_move(ctx, alive[tag], _home_target(ctx))

        if (tag := state.overseer_scout_tag) is not None and tag in alive:
            scout = alive[tag]
            _safe_air_move(ctx, scout, _scout_target(ctx, scout))

        _cast_changelings(ctx)
        _spread_changelings(ctx)

    return routine
