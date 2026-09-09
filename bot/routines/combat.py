"""Combat routines: each issues orders for one concern, and nothing else."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from cython_extensions import cy_closest_to
from sc2.units import Units

from ares.behaviors.combat import CombatManeuver
from ares.behaviors.combat.group import AMoveGroup, StutterGroupForward
from ares.behaviors.combat.individual import AMove, AttackTarget, ShootTargetInRange
from ares.consts import UnitRole, UnitTreeQueryType

from bot.core.types import CombatRoutine
from bot.routines import targeting

if TYPE_CHECKING:
    from bot.core.context import BotContext

DEFENDER_ENGAGE_RANGE: float = 12.0
SQUAD_ENGAGE_RANGE: float = 11.5
SQUAD_RADIUS: float = 9.0


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

        ctx.mediator.batch_assign_role(
            tags={u.tag for u in defenders}, role=UnitRole.ATTACKING
        )
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
    """Drive each ATTACKING squad at its nearest worthwhile target."""

    def routine(ctx: "BotContext") -> None:
        squads = ctx.mediator.get_squads(
            role=UnitRole.ATTACKING, squad_radius=squad_radius
        )
        for squad in squads:
            position = squad.squad_position
            target = targeting.attack_target(ctx, position)
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
