"""Where to attack. Shared by the combat routines, kept out of them."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cython_extensions import cy_closest_to, cy_distance_to_squared
from sc2.position import Point2

from bot.consts import (
    ALL_TOWNHALL_TYPES,
    FOCUS_MAIN,
    FOCUS_NATURAL,
    FOCUS_THIRD,
    IGNORED_ENEMY_TYPES,
)

if TYPE_CHECKING:
    from bot.core.context import BotContext

DEFENDER_SEARCH_RADIUS: float = 15.0
"""How far from a targeted structure to look for the units actually
defending it - e.g. drones stationed on the mineral line behind a Hatchery.
Wide enough to reach a base's own worker line from its townhall, not so wide
it reaches into an unrelated fight elsewhere on the same base."""

NATURAL_CLEAR_RADIUS: float = 15.0
"""How close to `get_enemy_nat` an enemy townhall must be to still count
the natural as standing - see `enemy_natural_cleared`."""

MAIN_CLEAR_RADIUS: float = 15.0
"""How close to the enemy start an enemy townhall must be to still count
the main as standing - see `enemy_main_cleared`."""

SCOUT_ARRIVAL_RADIUS: float = 8.0
"""How close the army must get to a pinned expansion before we treat it
as checked and move on - see `_expansion_checked`."""


def focus_points(ctx: "BotContext") -> list[Point2]:
    """Resolve the build's focus keywords to map positions."""
    lookup = {
        FOCUS_MAIN: lambda: ctx.bot.enemy_start_locations[0],
        FOCUS_NATURAL: lambda: ctx.mediator.get_enemy_nat,
        FOCUS_THIRD: lambda: ctx.mediator.get_enemy_third,
    }
    return [lookup[key]() for key in ctx.build.combat.focus if key in lookup]


def _nearest_defender(ctx: "BotContext", point: Point2) -> Point2 | None:
    """The closest real enemy unit within `DEFENDER_SEARCH_RADIUS` of
    `point`, or `None` if nothing but a structure is out there.

    `ctx.bot.enemy_units` is python-sc2's units-only tree - no structures at
    all - so nothing here can echo `_prioritize_enemies`' structure-fallback:
    an empty result genuinely means no defender was found nearby.
    """
    radius_sq = DEFENDER_SEARCH_RADIUS**2
    defenders = [
        u
        for u in ctx.bot.enemy_units
        if u.type_id not in IGNORED_ENEMY_TYPES
        and cy_distance_to_squared(u.position, point) <= radius_sq
    ]
    if not defenders:
        return None
    return cy_closest_to(position=point, units=defenders).position


def attack_target(ctx: "BotContext", from_pos: Point2) -> Point2:
    """Nearest visible enemy townhall, else structure, else somewhere
    unscouted - but a structure that has real defenders stationed near it
    (drones on the mineral line behind a Hatchery, say) sends the squad at
    the defenders instead of the building itself. A structure can't shoot
    back and is worth far less than the units guarding it, so there's
    nothing to gain by camping in range of it while its actual defenders
    sit just out of reach; once no defender is left nearby this falls back
    to the structure, so a cleared base still gets finished off.
    """
    structures = ctx.bot.enemy_structures
    if structures:
        townhalls = structures.of_type(ALL_TOWNHALL_TYPES)
        target = cy_closest_to(position=from_pos, units=townhalls or structures)
        return _nearest_defender(ctx, target.position) or target.position

    for point in focus_points(ctx):
        if not ctx.bot.is_visible(point):
            return point

    for location, _distance in ctx.mediator.get_enemy_expansions:
        if not ctx.bot.is_visible(location):
            return location

    return ctx.bot.enemy_start_locations[0]


def _is_our_expansion(ctx: "BotContext", location: Point2) -> bool:
    """True when `location` is our main or natural - those sit in
    `get_enemy_expansions` (every base except the enemy start) and must
    not pull a post-main scout sweep back onto our own side of the map."""
    radius_sq = MAIN_CLEAR_RADIUS**2
    if cy_distance_to_squared(location, ctx.bot.start_location) <= radius_sq:
        return True
    return cy_distance_to_squared(location, ctx.mediator.get_own_nat) <= radius_sq


def _mark_expansion_scouted(ctx: "BotContext", location: Point2) -> None:
    ctx.state.scouted_expansions.add(location)


def _expansion_checked(
    ctx: "BotContext", location: Point2, from_pos: Point2
) -> bool:
    """True once this expansion has been checked and should not be re-queued.

    Our burnysc2 fork has `is_visible` but no `is_explored`. Current vision
    alone is not enough: leaving a base puts it back in fog and would
    re-queue the natural the moment the army walked toward the third.
    So we latch into `scouted_expansions` the first time the spot is
    visible or the army arrives within `SCOUT_ARRIVAL_RADIUS`.
    """
    if location in ctx.state.scouted_expansions:
        return True
    arrived = (
        cy_distance_to_squared(from_pos, location) <= SCOUT_ARRIVAL_RADIUS**2
    )
    if arrived or ctx.bot.is_visible(location):
        _mark_expansion_scouted(ctx, location)
        return True
    return False


def hunt_remaining_bases(ctx: "BotContext", from_pos: Point2) -> Point2:
    """Find a hidden enemy base after the main townhall is gone.

    Unlike `attack_target`, leftover non-townhall structures (pylons,
    depots, production in a dead main) do **not** pin the army in place:
    visible townhalls win, then one shared unscouted expansion at a time.
    The expansion (or cleanup structure) is stored on
    `ctx.state.hunt_objective` until that expansion is checked / nothing
    remains near a cleanup pin, so every squad walks the same point.
    Checked expansions are latched in `scouted_expansions` so fog after
    leaving cannot pull the army back (natural ↔ third oscillation).

    Skips our own main/natural so a one-base all-in does not "scout" home.
    """
    townhalls = ctx.bot.enemy_structures.of_type(ALL_TOWNHALL_TYPES)
    if townhalls:
        ctx.state.hunt_objective = None
        target = cy_closest_to(position=from_pos, units=townhalls)
        return _nearest_defender(ctx, target.position) or target.position

    expansion_locations = {loc for loc, _ in ctx.mediator.get_enemy_expansions}
    pinned = ctx.state.hunt_objective

    if pinned is not None and pinned in expansion_locations:
        if not _expansion_checked(ctx, pinned, from_pos):
            return pinned
        ctx.state.hunt_objective = None
        pinned = None

    for location, _distance in ctx.mediator.get_enemy_expansions:
        if _is_our_expansion(ctx, location):
            continue
        if _expansion_checked(ctx, location, from_pos):
            continue
        ctx.state.hunt_objective = location
        return location

    structures = ctx.bot.enemy_structures
    if structures:
        if pinned is not None and pinned not in expansion_locations:
            still_there = [
                s
                for s in structures
                if cy_distance_to_squared(s.position, pinned) <= MAIN_CLEAR_RADIUS**2
            ]
            if still_there:
                return pinned
        target = cy_closest_to(position=from_pos, units=structures)
        ctx.state.hunt_objective = target.position
        return target.position

    ctx.state.hunt_objective = None
    for point in focus_points(ctx):
        if not _expansion_checked(ctx, point, from_pos):
            return point

    return ctx.bot.enemy_start_locations[0]


def enemy_third(ctx: "BotContext") -> Point2:
    """The enemy's third base. A `PointLocator`, for builds that want to put
    something there - a proxy, a rally - rather than merely walk to it."""
    return ctx.mediator.get_enemy_third


def enemy_fourth(ctx: "BotContext") -> Point2:
    """The enemy's fourth base. Same idea as `enemy_third`, one base further
    out - deep enough that early scouting rarely reaches it."""
    return ctx.mediator.get_enemy_fourth


def enemy_ramp_bottom(ctx: "BotContext") -> Point2:
    """Bottom of the enemy main ramp - the choke before walking into the
    main. A `PointLocator` for a push that wants to stutter-step to the
    ramp before committing up it."""
    return ctx.mediator.get_enemy_ramp.bottom_center


def enemy_natural_cleared(
    ctx: "BotContext", radius: float = NATURAL_CLEAR_RADIUS
) -> bool:
    """True once no enemy townhall stands near the enemy natural.

    Used to flip a push from "hold/stutter at the ramp bottom" to "up the
    ramp into the main" - the natural is the fight in front of that choke,
    and once its townhall is gone the ramp is the next step.
    """
    nat = ctx.mediator.get_enemy_nat
    radius_sq = radius**2
    return not any(
        cy_distance_to_squared(th.position, nat) <= radius_sq
        for th in ctx.bot.enemy_structures.of_type(ALL_TOWNHALL_TYPES)
    )


def enemy_main_cleared(
    ctx: "BotContext", radius: float = MAIN_CLEAR_RADIUS
) -> bool:
    """True when no *visible* enemy townhall stands near the enemy start.

    Fog of war makes this true from frame one before anything has been
    scouted - prefer `enemy_main_fallen` for anything that must wait until
    the main was actually seen and then destroyed.
    """
    main = ctx.bot.enemy_start_locations[0]
    radius_sq = radius**2
    return not any(
        cy_distance_to_squared(th.position, main) <= radius_sq
        for th in ctx.bot.enemy_structures.of_type(ALL_TOWNHALL_TYPES)
    )


def enemy_main_fallen(
    ctx: "BotContext", radius: float = MAIN_CLEAR_RADIUS
) -> bool:
    """True once a townhall was seen at the enemy start and is now gone.

    Latches `ctx.state.enemy_main_townhall_seen` on the first sighting so fog
    of war cannot open cleanup / scout behavior before the main has ever
    been found. Call from gates and combat every frame - the latch is
    cheap and idempotent.
    """
    main = ctx.bot.enemy_start_locations[0]
    radius_sq = radius**2
    has_th = any(
        cy_distance_to_squared(th.position, main) <= radius_sq
        for th in ctx.bot.enemy_structures.of_type(ALL_TOWNHALL_TYPES)
    )
    if has_th:
        ctx.state.enemy_main_townhall_seen = True
    return ctx.state.enemy_main_townhall_seen and not has_th


def squad_destination(ctx: "BotContext", from_pos: Point2) -> Point2:
    """Where an ATTACKING squad (or anything following it) should advance.

    Honors `Combat.attack_objective` when a build sets one; otherwise the
    default `attack_target` nearest-enemy / focus walk.
    """
    if (locator := ctx.build.combat.attack_objective) is not None:
        return locator(ctx)
    return attack_target(ctx, from_pos)


def map_center(ctx: "BotContext") -> Point2:
    """The playable map's centre point - a `PointLocator` for something that
    wants open room around it rather than any base's own formation.

    `game_info.map_center` is python-sc2's own centroid of the playable
    area, not tied to any expansion - so a caller placing there should pair
    this with `routines.placement.near_point` (see `WorkerTask.near`) rather
    than `where`'s formation lookup, which would just snap back to whichever
    base happens to be nearest.
    """
    return ctx.bot.game_info.map_center


def rally_point(ctx: "BotContext") -> Point2:
    """In front of our natural, facing the enemy - unless the build overrides
    it with `combat.rally` (see `BuildDefinition`), which a build whose army
    spawns away from home has to do."""
    if (locator := ctx.build.combat.rally) is not None:
        return locator(ctx)
    nat: Point2 = ctx.mediator.get_own_nat
    return nat.towards(ctx.bot.enemy_start_locations[0], ctx.build.combat.rally_offset)


def hold_positions(ctx: "BotContext") -> list[Point2]:
    """Rally, then behind each mineral line so worker harass is spotted early.

    A build that pins its rally (`combat.rally`) holds there and nowhere
    else: its army is deliberately not at home, and the mineral-line
    positions would drag defenders back across the map one at a time.
    """
    points: list[Point2] = [rally_point(ctx)]
    if ctx.build.combat.rally is not None:
        return points
    for th in ctx.ready_townhalls:
        points.extend(ctx.mediator.get_behind_mineral_positions(th_pos=th.position)[:1])
    return points
