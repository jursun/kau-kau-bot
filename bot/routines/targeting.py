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


def rally_point(ctx: "BotContext") -> Point2:
    """In front of our natural, facing the enemy."""
    nat: Point2 = ctx.mediator.get_own_nat
    return nat.towards(
        ctx.bot.enemy_start_locations[0], ctx.build.combat.rally_offset
    )


def hold_positions(ctx: "BotContext") -> list[Point2]:
    """Rally, then behind each mineral line so worker harass is spotted early."""
    points: list[Point2] = [rally_point(ctx)]
    for th in ctx.ready_townhalls:
        points.extend(
            ctx.mediator.get_behind_mineral_positions(th_pos=th.position)[:1]
        )
    return points
