"""Overseer role assignment, movement, and changeling vision.

Macro Zerg maintains three Overseers after Lair: home detection, army
detection, and enemy-base scouting. Roles are sticky by tag in `RunState`
and only reassigned when a tagged Overseer dies.

Changelings get a sticky opponent-base destination (also in `RunState`) so
frame-to-frame unit-list reshuffles cannot bounce them between targets.

Movement thrash harden (CheatInsane):
- Scout Overseer tours the enemy natural, third and main, parking at the
  edge of each base (`_pick_vantage`: clear of known anti-air, toward the
  map edge) and running `KeepUnitSafe` every frame - the earlier
  watch-first version hovered over the base centre and died.
- Home / army / scout destinations are sticky per tag; we skip re-issuing
  moves when already on the dest tile or already ordered toward it.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from ares.behaviors.combat import CombatManeuver
from ares.behaviors.combat.individual import (
    AMove,
    KeepUnitSafe,
    MoveToSafeTarget,
    PathUnitToTarget,
    UseAbility,
)
from ares.consts import UnitRole
from cython_extensions import cy_distance_to, cy_distance_to_squared, cy_towards
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

# Sit once this close; re-issuing KeepUnitSafe/MoveToSafeTarget every
# frame is what made home/army Overseers jitter (CheatInsane thrash).
_OVERSEER_ARRIVE: float = 2.0
_OVERSEER_ARRIVE_SQ: float = _OVERSEER_ARRIVE**2
# Sticky dest snap — army ball drift below this keeps the old point.
_OVERSEER_DEST_MATCH_SQ: float = 6.0**2
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


def _already_ordered_to_point(unit: "Unit", target: Point2) -> bool:
    """True when the unit's current order already aims at `target`'s tile."""
    if not unit.orders:
        return False
    ability_id = getattr(getattr(unit.orders[0], "ability", None), "id", None)
    # SC2 often reports MOVE order_target as a path waypoint — if already
    # moving, do not cancel/repath every frame (combat thrash pattern).
    if ability_id == AbilityId.MOVE and bool(getattr(unit, "is_moving", False)):
        return True
    order_target = unit.order_target
    if isinstance(order_target, Point2):
        if order_target.rounded == target.rounded:
            return True
        if cy_distance_to_squared(order_target, target) <= 1.5**2:
            return True
    return False


def _sticky_dest(
    ctx: "BotContext", tag: int, desired: Point2, *, force: bool = False
) -> Point2:
    """Latch per-Overseer destination; only retarget when it drifts far."""
    dests = ctx.state.overseer_destinations
    current = dests.get(tag)
    if (
        not force
        and current is not None
        and cy_distance_to_squared(current, desired) <= _OVERSEER_DEST_MATCH_SQ
    ):
        return current
    dests[tag] = Point2(desired)
    return dests[tag]


def _should_skip_move(unit: "Unit", target: Point2) -> bool:
    if cy_distance_to_squared(unit.position, target) <= _OVERSEER_ARRIVE_SQ:
        return True
    return _already_ordered_to_point(unit, target)


def _scout_air_move(ctx: "BotContext", unit: "Unit", target: Point2) -> None:
    """Scout path: `KeepUnitSafe` every frame, then the move (skipped while
    already on its way / arrived).

    The safety check runs even when the move itself is skipped - a scout
    already flying toward its vantage still has to notice a Stalker or
    Cannon appearing next to it. The old watch-first version skipped the
    peel entirely to keep vision, and the scout floated over the enemy base
    until it died; the vantage points below (edge of the base, clear of
    known anti-air) are what keep vision cheap now.
    """
    grid = ctx.mediator.get_air_grid
    maneuver = CombatManeuver()
    maneuver.add(KeepUnitSafe(unit=unit, grid=grid))
    if not _should_skip_move(unit, target):
        maneuver.add(
            PathUnitToTarget(
                unit=unit,
                grid=grid,
                target=target,
                success_at_distance=_SCOUT_ARRIVE,
            )
        )
    ctx.bot.register_behavior(maneuver)


def _safe_air_move(ctx: "BotContext", unit: "Unit", target: Point2) -> None:
    """Home / army path — KeepUnitSafe ok, but never re-issue every frame."""
    if _should_skip_move(unit, target):
        return
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


# How far from a base's townhall the scout parks. Overseer sight is 11, so
# this still sees the townhall and most of the mineral line from the edge
# instead of from on top of the army and the anti-air.
_SCOUT_VANTAGE_RADIUS: float = 9.5
_SCOUT_ARRIVE: float = 2.5
_SCOUT_DWELL_S: float = 8.0
"""Time spent watching one base before moving on to the next in the tour."""
_SCOUT_THREAT_MARGIN: float = 3.0
"""Extra clearance beyond a threat's air range before a vantage is 'safe'."""
_SCOUT_CLEARANCE_CAP: float = 12.0
_SCOUT_ANGLES: int = 16
_SCOUT_EDGE_MARGIN: float = 1.0
"""Keep vantage points this far inside the playable area."""
_SCOUT_TOUR_EXPANSIONS: int = 2
"""Enemy natural + third, then the main."""


def scout_tour(ctx: "BotContext") -> list[Point2]:
    """Bases the scout Overseer visits, in order: the enemy's natural and
    third (the enemy-side expansions closest to its start), then its main.

    The main goes last - it is the most heavily defended, and the other two
    are what tell us how greedy the enemy is.
    """
    starts = list(ctx.bot.enemy_start_locations)
    if not starts:
        return []
    enemy_start = starts[0]
    our_start = ctx.production_location
    enemy_side = sorted(
        (
            Point2(loc)
            for loc in ctx.bot.expansion_locations_list
            if cy_distance_to_squared(loc, enemy_start) > _BASE_MATCH_SQ
            and cy_distance_to_squared(loc, enemy_start)
            < cy_distance_to_squared(loc, our_start)
        ),
        key=lambda loc: cy_distance_to_squared(loc, enemy_start),
    )
    return enemy_side[:_SCOUT_TOUR_EXPANSIONS] + [Point2(enemy_start)]


def _air_threats(ctx: "BotContext") -> list[tuple[Point2, float]]:
    """(position, air range) of every visible enemy that can shoot up."""
    threats: list[tuple[Point2, float]] = []
    for group in (ctx.bot.enemy_units, ctx.bot.enemy_structures):
        for unit in group:
            if not getattr(unit, "can_attack_air", False):
                continue
            if not getattr(unit, "is_ready", True):
                continue
            threats.append((unit.position, float(getattr(unit, "air_range", 0.0) or 0.0)))
    return threats


def _clearance(point: Point2, threats: list[tuple[Point2, float]]) -> float:
    """Distance from `point` to the edge of the nearest threat's air range
    (negative inside it), capped so far-away threats stop mattering."""
    if not threats:
        return _SCOUT_CLEARANCE_CAP
    return min(
        _SCOUT_CLEARANCE_CAP,
        min(
            cy_distance_to(point, position) - air_range
            for position, air_range in threats
        ),
    )


def _in_playable_area(ctx: "BotContext", point: Point2) -> bool:
    area = ctx.bot.game_info.playable_area
    margin = _SCOUT_EDGE_MARGIN
    return (
        area.x + margin <= point.x <= area.x + area.width - margin
        and area.y + margin <= point.y <= area.y + area.height - margin
    )


def _pick_vantage(
    ctx: "BotContext",
    base: Point2,
    scout_position: Point2,
    threats: list[tuple[Point2, float]],
) -> Point2:
    """Where to hover to watch `base` from its edge.

    Samples a ring of `_SCOUT_VANTAGE_RADIUS` around the base and keeps the
    point with the most clearance from known anti-air, preferring points
    toward the map edge (away from the centre, where the enemy army walks)
    and ones the scout doesn't have to cross the base to reach.
    """
    center = ctx.bot.game_info.map_center
    best: Point2 | None = None
    best_score = float("-inf")
    for index in range(_SCOUT_ANGLES):
        angle = 2.0 * math.pi * index / _SCOUT_ANGLES
        point = Point2(
            (
                base.x + _SCOUT_VANTAGE_RADIUS * math.cos(angle),
                base.y + _SCOUT_VANTAGE_RADIUS * math.sin(angle),
            )
        )
        if not _in_playable_area(ctx, point):
            continue
        score = (
            _clearance(point, threats)
            + 0.05 * cy_distance_to(point, center)
            - 0.03 * cy_distance_to(point, scout_position)
        )
        if score > best_score:
            best, best_score = point, score
    if best is None:
        return Point2(cy_towards(base, scout_position, _SCOUT_VANTAGE_RADIUS))
    return best


def _scout_target(ctx: "BotContext", scout: "Unit") -> Point2 | None:
    """Vantage point the scout should be at right now, advancing the tour.

    Visits `scout_tour` in order, `_SCOUT_DWELL_S` at each base, then wraps
    around. The vantage is latched per base and only re-picked when a
    threat comes within range + margin of it (or the tour moves on), so the
    scout doesn't dither between ring points frame to frame.
    """
    tour = scout_tour(ctx)
    state = ctx.state
    if not tour:
        return None
    index = state.overseer_scout_tour_index % len(tour)
    base = tour[index]
    threats = _air_threats(ctx)

    vantage = state.overseer_scout_vantage
    stale = (
        vantage is None
        or cy_distance_to_squared(vantage, base)
        > (_SCOUT_VANTAGE_RADIUS + 1.0) ** 2
    )
    if stale or _clearance(vantage, threats) < _SCOUT_THREAT_MARGIN:
        vantage = _pick_vantage(ctx, base, scout.position, threats)
        state.overseer_scout_vantage = vantage

    if cy_distance_to_squared(scout.position, vantage) <= _SCOUT_ARRIVE**2:
        if state.overseer_scout_dwell_since is None:
            state.overseer_scout_dwell_since = ctx.bot.time
        elif ctx.bot.time - state.overseer_scout_dwell_since >= _SCOUT_DWELL_S:
            state.overseer_scout_tour_index = (index + 1) % len(tour)
            state.overseer_scout_dwell_since = None
            state.overseer_scout_vantage = None
    else:
        state.overseer_scout_dwell_since = None
    return vantage



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

        # Drop sticky dests for dead / unassigned Overseers.
        live_tags = set(alive)
        for slot in (
            state.overseer_home_tag,
            state.overseer_army_tag,
            state.overseer_scout_tag,
        ):
            if slot is not None:
                live_tags.add(slot)
        for tag in list(state.overseer_destinations):
            if tag not in alive:
                del state.overseer_destinations[tag]

        if (tag := state.overseer_home_tag) is not None and tag in alive:
            dest = _sticky_dest(ctx, tag, _home_target(ctx))
            _safe_air_move(ctx, alive[tag], dest)

        if (tag := state.overseer_army_tag) is not None and tag in alive:
            army_target = _army_target(ctx)
            desired = army_target if army_target is not None else _home_target(ctx)
            dest = _sticky_dest(ctx, tag, desired)
            _safe_air_move(ctx, alive[tag], dest)

        if (tag := state.overseer_scout_tag) is not None and tag in alive:
            scout = alive[tag]
            vantage = _scout_target(ctx, scout)
            if vantage is not None:
                _scout_air_move(ctx, scout, vantage)

        _cast_changelings(ctx)
        _spread_changelings(ctx)

    return routine
