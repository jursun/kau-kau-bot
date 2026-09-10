"""Combat routines: each issues orders for one concern, and nothing else."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from ares.behaviors.combat import CombatManeuver
from ares.behaviors.combat.group import AMoveGroup, StutterGroupForward
from ares.behaviors.combat.individual import (
    AMove,
    AttackTarget,
    KeepUnitSafe,
    MoveToSafeTarget,
    ShootTargetInRange,
)
from ares.consts import UnitRole, UnitTreeQueryType
from cython_extensions import cy_closest_to, cy_distance_to_squared
from sc2.ids.unit_typeid import UnitTypeId
from sc2.units import Units

from bot.builds.definition import _always
from bot.core.types import CombatRoutine, Gate, PointLocator
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

BUILDER_CLAIM_RADIUS: float = 30.0
"""How close to the proxy a worker has to be for `builder_workers_attack`
to consider it stranded there and claim it. Wide enough to cover a builder
that has wandered a screen away after finishing, tight enough that a worker
long-distance mining past the area is not swept up."""

MAX_SUPPLY: float = 200.0
"""Standard SC2 supply cap. Once here - and once `_army_fully_trained` says
nothing is still incubating, so the count reflects units actually on the
field - there is no "next wave" worth waiting for and nothing left to gain
by holding back, so the wave-release size/tech gate is bypassed: see
`_maxed_and_ready`."""


def _army_fully_trained(ctx: "BotContext") -> bool:
    """True once nothing in the build's own composition is still incubating
    (a Zerg egg, or any other race's production queue) - so `supply_used`
    reflects units actually on the field, not still cooking in production."""
    return all(
        ctx.bot.already_pending(unit_type) == 0 for unit_type in ctx.build.army.types
    )


def _maxed_and_ready(ctx: "BotContext") -> bool:
    """See `MAX_SUPPLY`."""
    return ctx.bot.supply_used >= MAX_SUPPLY and _army_fully_trained(ctx)


def release_waves() -> CombatRoutine:
    """Promote defenders to attackers once the size and tech gates both pass -
    or unconditionally once `_maxed_and_ready` (200 supply, nothing left to
    train): there is nothing to gain by continuing to wait once every
    possible unit the build can field is already on the ground.
    """

    def routine(ctx: "BotContext") -> None:
        plan = ctx.build.combat
        if ctx.state.next_wave_size <= 0:
            ctx.state.next_wave_size = plan.wave1_min

        defenders = ctx.units_in_role(UnitRole.DEFENDING)
        size = len(defenders)
        if size == 0:
            return

        maxed = _maxed_and_ready(ctx)
        if not maxed:
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
        reason = " - 200 supply, nothing left to train" if maxed else ""
        ctx.log(
            f"WAVE {ctx.state.wave_number} attack "
            f"(size={size}, next>={ctx.state.next_wave_size}){reason}"
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

    A squad that isn't still forming up fights whatever enemy is actually in
    range (`SQUAD_ENGAGE_RANGE`) via `StutterGroupForward` unconditionally -
    there is no supply-ratio check and no retreat. This build attacks with
    everything a wave has; a squad that finds itself outnumbered stutter-
    steps and trades rather than disengaging.
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

            close_enemy = _enemies_near(ctx, position, SQUAD_ENGAGE_RANGE)

            target = rally if mustering else targeting.attack_target(ctx, position)

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


def escort_overseers() -> CombatRoutine:
    """Send every Overseer toward the largest ATTACKING squad's destination,
    hanging back at the edge of enemy range instead of trailing into it.

    Two things went wrong with the first version of this (which just AMoved
    every Overseer at the squad's live `squad_position`):

    1. `squad_position` recedes as the squad advances, and Zerglings —
       especially with Metabolic Boost, which this build researches — are
       faster than an Overseer. A slower unit chasing a point that keeps
       moving away from it never closes the gap. Targeting the same
       destination `attack_squads` sends the squad toward instead
       (`targeting.attack_target`) fixes that: it's a fixed point, so the
       Overseer actually makes progress and tends to arrive ahead of or
       alongside the wave rather than perpetually trailing it.
    2. A bare `AMove` has no notion of danger, so the Overseer walked
       straight up to (and into) whatever it was escorting. `MoveToSafeTarget`
       resolves the destination down to the nearest *safe* spot near it
       (`radius` controls how near) before pathing there, and paths with its
       own built-in danger-sensing along the way — so it settles at the edge
       of enemy unit/structure range rather than in the middle of it.
       `KeepUnitSafe` runs first and, if the Overseer is already standing
       somewhere dangerous, retreats it before anything else does — the same
       "urgent response first, fall through to normal movement" shape
       `_defender_maneuver` uses above (`CombatManeuver.execute` is `any(...)`
       over `micros`, so it stops at the first behavior that acts).

    Overseers aren't part of `build.army.types` (only the composition units
    are — zerglings, here), so they never pick up an ATTACKING/DEFENDING
    role of their own via `release_waves`/`attack_squads`. This just points
    them at wherever the army is headed instead; see `steps.zerg.overseers`
    for what keeps them supplied.
    """

    def routine(ctx: "BotContext") -> None:
        overseers = ctx.bot.units(UnitTypeId.OVERSEER)
        if not overseers:
            return

        squads = ctx.mediator.get_squads(
            role=UnitRole.ATTACKING, squad_radius=SQUAD_RADIUS
        )
        if not squads:
            return

        biggest = max(squads, key=lambda squad: len(squad.squad_units))
        target = targeting.attack_target(ctx, biggest.squad_position)
        grid = ctx.mediator.get_air_grid

        for overseer in overseers:
            maneuver = CombatManeuver()
            maneuver.add(KeepUnitSafe(unit=overseer, grid=grid))
            maneuver.add(MoveToSafeTarget(unit=overseer, grid=grid, target=target))
            ctx.bot.register_behavior(maneuver)

    return routine


def builder_workers_attack(
    where: PointLocator,
    claim_gate: Gate = _always,
    claim_radius: float = BUILDER_CLAIM_RADIUS,
) -> CombatRoutine:
    """Turn workers stranded at a proxy into attackers once the first wave goes.

    A proxy build finishes with two or three SCVs standing on the far side of
    the map with nothing to do. Walking them home to mine is worth close to
    nothing at that point; adding them to the push is worth a Marine each.

    Claiming is by *situation*, not by tag: a worker is claimed if it is near
    `where`, not in ares' building tracker, and not currently constructing.
    Those two exclusions are what make it safe - a worker still walking to
    the proxy to build, or mid-build, is in the tracker and is left alone.
    Nothing has to remember which SCV built which Barracks.

    `claim_gate` is the part that is easy to get wrong, so it is explicit.
    Claiming assigns `UnitRole.PROXY_WORKER`, and `select_worker` only ever
    considers `UnitRole.GATHERING` - so a claimed worker is invisible to
    `steps.terran.proxy_barracks`. Claim too early and the builder standing
    right next to the site where the *next* Barracks goes stops being
    eligible to build it, and a fresh SCV gets pulled from home for the
    walk instead. So the gate should say "every Barracks this build wants is
    already standing or under way" - after that there is nothing left for a
    builder to be saved for.

    Before the first wave is released the claimed workers wait at the proxy
    rather than running in alone; from wave 1 on they attack the same target
    the squads do, shooting whatever comes into range on the way.

    Attributes:
        where: Resolves the proxy location, fresh each frame.
        claim_gate: When claiming may begin. See above - this is a
            correctness condition, not a preference.
        claim_radius: How close to `where` a worker must be to be claimed.
    """

    def _claim(ctx: "BotContext", point) -> None:
        tracker = ctx.mediator.get_building_tracker_dict
        already = {
            u.tag for u in ctx.mediator.get_units_from_role(role=UnitRole.PROXY_WORKER)
        }
        radius_sq = claim_radius**2
        for worker in ctx.bot.workers:
            if worker.tag in already or worker.tag in tracker:
                continue
            if worker.is_constructing_scv:
                continue
            if cy_distance_to_squared(worker.position, point) > radius_sq:
                continue
            ctx.mediator.assign_role(tag=worker.tag, role=UnitRole.PROXY_WORKER)
            ctx.log("PROXY worker joins the attack")

    def routine(ctx: "BotContext") -> None:
        point = where(ctx)

        if claim_gate(ctx):
            _claim(ctx, point)

        workers = ctx.mediator.get_units_from_role(role=UnitRole.PROXY_WORKER)
        if not workers:
            return

        # Hold at the proxy until the Marines actually leave.
        if ctx.state.wave_number < 1:
            for worker in workers:
                ctx.bot.register_behavior(AMove(unit=worker, target=point))
            return

        target = targeting.attack_target(ctx, point)
        for worker in workers:
            maneuver = CombatManeuver()
            in_range = _enemies_near(ctx, worker, DEFENDER_ENGAGE_RANGE)
            if in_range:
                maneuver.add(ShootTargetInRange(unit=worker, targets=in_range))
                maneuver.add(
                    AttackTarget(
                        unit=worker,
                        target=cy_closest_to(position=worker.position, units=in_range),
                    )
                )
            maneuver.add(AMove(unit=worker, target=target))
            ctx.bot.register_behavior(maneuver)

    return routine
