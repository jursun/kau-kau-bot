"""Where to attack. Shared by the combat routines, kept out of them."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cython_extensions import cy_closest_to
from sc2.position import Point2

from bot.consts import ALL_TOWNHALL_TYPES, FOCUS_MAIN, FOCUS_NATURAL, FOCUS_THIRD

if TYPE_CHECKING:
    from bot.core.context import BotContext


def focus_points(ctx: "BotContext") -> list[Point2]:
    """Resolve the build's focus keywords to map positions."""
    lookup = {
        FOCUS_MAIN: lambda: ctx.bot.enemy_start_locations[0],
        FOCUS_NATURAL: lambda: ctx.mediator.get_enemy_nat,
        FOCUS_THIRD: lambda: ctx.mediator.get_enemy_third,
    }
    return [lookup[key]() for key in ctx.build.combat.focus if key in lookup]


def attack_target(ctx: "BotContext", from_pos: Point2) -> Point2:
    """Nearest visible enemy townhall, else structure, else somewhere unscouted."""
    structures = ctx.bot.enemy_structures
    if structures:
        townhalls = structures.of_type(ALL_TOWNHALL_TYPES)
        return cy_closest_to(position=from_pos, units=townhalls or structures).position

    for point in focus_points(ctx):
        if not ctx.bot.is_visible(point):
            return point

    for location, _distance in ctx.mediator.get_enemy_expansions:
        if not ctx.bot.is_visible(location):
            return location

    return ctx.bot.enemy_start_locations[0]


def enemy_third(ctx: "BotContext") -> Point2:
    """The enemy's third base. A `PointLocator`, for builds that want to put
    something there - a proxy, a rally - rather than merely walk to it."""
    return ctx.mediator.get_enemy_third


def enemy_fourth(ctx: "BotContext") -> Point2:
    """The enemy's fourth base. Same idea as `enemy_third`, one base further
    out - deep enough that early scouting rarely reaches it."""
    return ctx.mediator.get_enemy_fourth


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
