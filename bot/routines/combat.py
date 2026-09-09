"""Combat routines: each issues orders for one concern, and nothing else."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from ares.behaviors.combat import CombatManeuver
from ares.behaviors.combat.group import AMoveGroup, StutterGroupForward
from ares.behaviors.combat.individual import AMove, AttackTarget, ShootTargetInRange
from ares.consts import UnitRole, UnitTreeQueryType
from cython_extensions import cy_closest_to, cy_distance_to_squared
from sc2.units import Units

from bot.core.types import CombatRoutine
from bot.routines import targeting

if TYPE_CHECKING:
    from bot.core.context import BotContext

DEFENDER_ENGAGE_RANGE: float = 12.0
SQUAD_ENGAGE_RANGE: float = 11.5
SQUAD_RADIUS: float = 9.0
MUSTER_RADIUS: float = 4.0
"""How tightly a freshly-released wave must cluster at the rally point
before it is let off to attack, rather than trickling toward the enemy
as units peel off from wherever they were defending."""


def release_waves() -> CombatRoutine:
    """Promote defenders to attackers once the size and tech gates both pass."""

    def routine(ctx: "BotContext") -> None:
        plan = ctx.build.combat
        if ctx.state.next_wave_size <= 0:
            ctx.state.next_wave_size = plan.wave1_min

        defenders = ctx.units_in_role(UnitRole.DEFENDING)
        size = len(defenders)
        if size < ctx.state.next_wave_size:
            return
        if not plan.wave_gate(ctx):
            ctx.log_once(
                f"wave_wait_{ctx.state.wave_number}",
                f"GATHER wave {ctx.state.wave_number + 1} "
                f"({size}/{ctx.state.next_wave_size}) - waiting on tech",
            )
            return

        tags = {u.tag for u in defenders}
        ctx.mediator.batch_assign_role(tags=tags, role=UnitRole.ATTACKING)
        ctx.state.mustering_tags.update(tags)
        ctx.state.wave_number += 1
        ctx.state.next_wave_size = max(
            plan.wave1_min + 1, math.ceil(size * plan.wave_growth)
        )
        ctx.log(
            f"WAVE {ctx.state.wave_number} attack "
            f"(size={size}, next>={ctx.state.next_wave_size})"
        )

    return routine


def _enemies_near(ctx: "BotContext", point, distance: float) -> Units:
    return ctx.mediator.get_units_in_range(
        start_points=[point],
        distances=distance,
        query_tree=UnitTreeQueryType.EnemyGround,
    )[0]


def _defender_maneuver(ctx: "BotContext", unit, home_threats, hold) -> CombatManeuver:
    maneuver = CombatManeuver()
    in_range = _enemies_near(ctx, unit, DEFENDER_ENGAGE_RANGE)
    if in_range:
        maneuver.add(ShootTargetInRange(unit=unit, targets=in_range))
        maneuver.add(
            AttackTarget(
                unit=unit, target=cy_closest_to(position=unit.position, units=in_range)
            )
        )
    elif home_threats:
        maneuver.add(
            AttackTarget(
                unit=unit,
                target=cy_closest_to(position=unit.position, units=home_threats),
            )
        )
    else:
        maneuver.add(AMove(unit=unit, target=hold))
    return maneuver


def defend_home() -> CombatRoutine:
    """Units still in DEFENDING hold the natural and collapse on anything near."""

    def routine(ctx: "BotContext") -> None:
        defenders = ctx.units_in_role(UnitRole.DEFENDING)
        if not defenders:
            return
        home_threats = ctx.mediator.get_main_ground_threats_near_townhall
        hold = targeting.hold_positions(ctx)
        for index, unit in enumerate(defenders):
            ctx.bot.register_behavior(
                _defender_maneuver(ctx, unit, home_threats, hold[index % len(hold)])
            )

    return routine


def attack_squads(squad_radius: float = SQUAD_RADIUS) -> CombatRoutine:
    """Drive each ATTACKING squad at its nearest worthwhile target.

    A freshly-promoted wave musters at the rally point in front of our
    natural (`targeting.rally_point`) before it advances, so it moves out as
    one group instead of trickling toward the enemy as units arrive from
    wherever they were defending. `RunState.mustering_tags` marks units still
    waiting to form up; once a squad clusters within `MUSTER_RADIUS` of the
    rally point its tags are released and it attacks like any other squad
    from then on, even if it later drifts away from the rally point.
    """

    def routine(ctx: "BotContext") -> None:
        alive_attackers = {u.tag for u in ctx.units_in_role(UnitRole.ATTACKING)}
        ctx.state.mustering_tags &= alive_attackers

        squads = ctx.mediator.get_squads(
            role=UnitRole.ATTACKING, squad_radius=squad_radius
        )
        rally = targeting.rally_point(ctx)
        for squad in squads:
            position = squad.squad_position
            mustering = squad.tags & ctx.state.mustering_tags

            if (
                mustering
                and cy_distance_to_squared(position, rally) <= MUSTER_RADIUS**2
            ):
                ctx.state.mustering_tags -= mustering
                mustering = set()

            target = rally if mustering else targeting.attack_target(ctx, position)
            close_enemy = _enemies_near(ctx, position, SQUAD_ENGAGE_RANGE)

            maneuver = CombatManeuver()
            if close_enemy:
                maneuver.add(
                    StutterGroupForward(
                        group=squad.squad_units,
                        group_tags=squad.tags,
                        group_position=position,
                        target=target,
                        enemies=close_enemy,
                    )
                )
            maneuver.add(
                AMoveGroup(
                    group=squad.squad_units, group_tags=squad.tags, target=target
                )
            )
            ctx.bot.register_behavior(maneuver)

    return routine
