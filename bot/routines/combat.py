"""Combat routines: each issues orders for one concern, and nothing else."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ares.behaviors.combat import CombatManeuver
from ares.behaviors.combat.group import AMoveGroup, KeepGroupSafe, StutterGroupForward
from ares.behaviors.combat.individual import (
    AMove,
    AttackTarget,
    KeepUnitSafe,
    MoveToSafeTarget,
    ShootTargetInRange,
)
from ares.consts import UnitRole, UnitTreeQueryType
from cython_extensions import (
    cy_center,
    cy_closest_to,
    cy_distance_to,
    cy_distance_to_squared,
    cy_in_attack_range,
    cy_towards,
)
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units

from bot.builds.definition import _always
from bot.consts import IGNORED_ENEMY_TYPES
from bot.core.types import CombatRoutine, Gate, PointLocator
from bot.intel import enemy_army
from bot.routines import targeting

if TYPE_CHECKING:
    from bot.core.context import BotContext


def _active_crew_tags(ctx: "BotContext") -> set[int]:
    """Tags of proxy-crew workers that still have tasks left.

    `builder_workers_attack` must not claim these: `proxy_crew` still issues
    path/build orders for them, and a `PROXY_WORKER` attack/`AMove` on the
    same frame is what stuck Y cycling between the Depot site and the attack
    objective once all four Barracks were under way (claim_gate open) while
    Y's second task was still pending.
    """
    plan = ctx.build.crew
    if plan is None:
        return set()
    crew = ctx.state.proxy_crew
    busy: set[int] = set()
    for member, tasks in (
        (crew.x, plan.x_tasks),
        (crew.y, plan.y_tasks),
        (crew.z, plan.z_tasks),
    ):
        if member.tag is not None and member.task_index < len(tasks):
            busy.add(member.tag)
    return busy


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


def release_first_wave_then_stream(muster: bool = True) -> CombatRoutine:
    """`release_waves()`'s single-wave sibling: one leave, then stream.

    Wait once for `combat.wave1_min` defenders and `combat.wave_gate`, and
    release them together as ATTACKING. From then on (`ctx.state.wave_number
    >= 1`), every new defender is promoted to ATTACKING the moment it
    exists, with no minimum size.

    When `muster` is True (default), the first wave is also added to
    `RunState.mustering_tags` so `attack_squads` holds it at the rally
    point until it clumps - good for proxy armies that spawn already
    forward. When False, the first wave skips that hold and marches on
    the same frame (Chargelot-style home production: units are already
    together at the natural, and holding just invites home defense fights
    that never leave for the enemy base).
    """

    def routine(ctx: "BotContext") -> None:
        defenders = ctx.units_in_role(UnitRole.DEFENDING)
        if not defenders:
            return
        tags = {u.tag for u in defenders}

        if ctx.state.wave_number >= 1:
            # Streaming: no size floor, no mustering - straight to the front.
            ctx.mediator.batch_assign_role(tags=tags, role=UnitRole.ATTACKING)
            return

        plan = ctx.build.combat
        size = len(defenders)
        if size < plan.wave1_min:
            return
        if not plan.wave_gate(ctx):
            ctx.log_once(
                "wave_wait_0",
                f"GATHER wave 1 ({size}/{plan.wave1_min}) - waiting on tech",
            )
            return

        ctx.mediator.batch_assign_role(tags=tags, role=UnitRole.ATTACKING)
        if muster:
            ctx.state.mustering_tags.update(tags)
        ctx.state.wave_number = 1
        ctx.log(
            f"WAVE 1 attack (size={size}) - streaming from here on"
            f"{'' if muster else ' (no muster)'}"
        )

    return routine


def _prioritize_enemies(enemies: Units) -> Units:
    """Enemy units outrank enemy structures as targets - a structure is only
    worth returning when nothing else is in range. `EnemyGround` mixes both
    into one tree (ares' own claim that it "ignores structures" doesn't hold
    - `enemy_ground` is split straight off `all_enemy_units`, which includes
    them), so every consumer of `_enemies_near` needs this or a Marine (or
    anything else) walking past a building with real targets nearby would be
    just as likely to shoot the building as the units."""
    combat_units = [u for u in enemies if not u.is_structure]
    return combat_units if combat_units else enemies


def _enemies_near(ctx: "BotContext", point, distance: float) -> Units:
    enemies = ctx.mediator.get_units_in_range(
        start_points=[point],
        distances=distance,
        query_tree=UnitTreeQueryType.EnemyGround,
    )[0]
    worthwhile = [u for u in enemies if u.type_id not in IGNORED_ENEMY_TYPES]
    return _prioritize_enemies(worthwhile)


@dataclass
class _Move:
    """Plain move, no attack semantics - for a unit (a claimed worker) that
    must never fight, where `AMove`'s attack-move risks it trading blows."""

    unit: Unit
    target: Point2

    def execute(self, ai, config, mediator, **kwargs) -> bool:
        self.unit.move(self.target)
        return True


def _combat_force_supply(ctx: "BotContext", units) -> float:
    """Supply cost of the fighting units in `units`, ignoring structures.

    Used by `attack_squads` to decide stutter vs kite: a Hatchery in range is
    something to shoot, not an army that outnumbers five Marines.
    """
    return sum(
        ctx.bot.calculate_supply_cost(u.type_id)
        for u in units
        if not u.is_structure
    )


def _our_force_larger(ctx: "BotContext", ours, theirs) -> bool:
    """True when `theirs` is strictly smaller than `ours` by supply."""
    return _combat_force_supply(ctx, theirs) < _combat_force_supply(ctx, ours)


def _intel_army_near(ctx: "BotContext", point, distance: float) -> list:
    """WORKER-filtered enemy combat units within `distance` — this frame only.

    Consumes `bot.intel.enemy_army` so workers never pad force comparisons or
    KeepGroupSafe's close-enemy list. Do not stash the returned Units.
    """
    radius_sq = distance * distance
    return [
        unit
        for unit in enemy_army(ctx)
        if cy_distance_to_squared(unit.position, point) <= radius_sq
    ]


def _squad_maneuver_with_influence_retreat(
    group,
    group_tags: set[int],
    group_position,
    target,
    close_enemy,
    grid,
) -> CombatManeuver:
    """Reusable ATTACKING-squad maneuver: leave bad ground influence first.

    `KeepGroupSafe` runs before stutter/AMove so a squad standing in enemy
    influence retreats (and may still shoot in-range) instead of parking.
    CombatManeuver short-circuits on the first behavior that acts.
    """
    maneuver = CombatManeuver()
    maneuver.add(
        KeepGroupSafe(
            group=list(group),
            close_enemy=close_enemy or [],
            grid=grid,
            attack_in_range_enemy=True,
        )
    )
    if close_enemy:
        maneuver.add(
            StutterGroupForward(
                group=group,
                group_tags=group_tags,
                group_position=group_position,
                target=target,
                enemies=close_enemy,
            )
        )
    maneuver.add(
        AMoveGroup(group=group, group_tags=group_tags, target=target)
    )
    return maneuver


def _kite_maneuver(
    unit: Unit,
    enemies: Units | list[Unit],
    min_engage_range: float,
    target,
    grid=None,
) -> CombatManeuver:
    """One unit's turn at `min_engage_range` kiting - see `attack_squads`.

    Influence retreat (`KeepUnitSafe`) runs first when `grid` is provided.
    Backs straight away from the nearest enemy(s) closer than
    `min_engage_range`; otherwise shoots the lowest-health enemy already in
    weapon range (`ShootTargetInRange`); otherwise advances on `target`.
    """
    maneuver = CombatManeuver()
    if grid is not None:
        maneuver.add(KeepUnitSafe(unit=unit, grid=grid))
    in_range = cy_in_attack_range(unit, enemies)
    crowding = [
        e
        for e in in_range
        if cy_distance_to(unit.position, e.position) < min_engage_range
    ]
    if crowding:
        retreat_from = (
            Point2(cy_center(crowding)) if len(crowding) > 1 else crowding[0].position
        )
        retreat_to = Point2(cy_towards(retreat_from, unit.position, min_engage_range))
        maneuver.add(_Move(unit=unit, target=retreat_to))
    elif in_range:
        maneuver.add(ShootTargetInRange(unit=unit, targets=in_range))
    else:
        maneuver.add(AMove(unit=unit, target=target))
    return maneuver


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


def attack_squads(
    squad_radius: float = SQUAD_RADIUS, min_engage_range: float | None = None
) -> CombatRoutine:
    """Drive each ATTACKING squad at its nearest worthwhile target.

    A freshly-promoted wave musters at the rally point in front of our
    natural (`targeting.rally_point`) before it advances, so it moves out as
    one group instead of trickling toward the enemy as units arrive from
    wherever they were defending. `RunState.mustering_tags` marks units still
    waiting to form up; once a squad clusters within `MUSTER_RADIUS` of the
    rally point its tags are released and it attacks like any other squad
    from then on, even if it later drifts away from the rally point.

    Destination when not mustering is `targeting.squad_destination` - the
    build's `Combat.attack_objective` if set, otherwise
    `targeting.attack_target`.

    A squad that isn't still forming up and finds a nearby enemy
    (`SQUAD_ENGAGE_RANGE`) engages. Close army comes from
    `bot.intel.enemy_army` (workers stripped); if that list is empty nearby,
    `_enemies_near` still supplies structures to shoot. How the squad fights
    depends on local force size (intel army supply) and `min_engage_range`:

    - Unsafe ground influence: `KeepGroupSafe` / `KeepUnitSafe` run first so
      the ball leaves bad tiles instead of parking (Zerg openings included).
    - Enemy force strictly smaller than ours: `StutterGroupForward` trades
      as one group toward the destination.
    - Enemy force equal or larger, and `min_engage_range` is set: each unit
      is driven individually via `_kite_maneuver` - backing away from
      anything closer than `min_engage_range`, otherwise shooting. Builds
      that leave `min_engage_range` unset keep group stutter after influence
      retreat rather than per-unit kite.
    """

    def routine(ctx: "BotContext") -> None:
        alive_attackers = {u.tag for u in ctx.units_in_role(UnitRole.ATTACKING)}
        ctx.state.mustering_tags &= alive_attackers

        squads = ctx.mediator.get_squads(
            role=UnitRole.ATTACKING, squad_radius=squad_radius
        )
        rally = targeting.rally_point(ctx)
        grid = ctx.mediator.get_ground_grid
        for squad in squads:
            position = squad.squad_position
            mustering = squad.tags & ctx.state.mustering_tags

            if (
                mustering
                and cy_distance_to_squared(position, rally) <= MUSTER_RADIUS**2
            ):
                ctx.state.mustering_tags -= mustering
                mustering = set()

            close_army = _intel_army_near(ctx, position, SQUAD_ENGAGE_RANGE)
            close_enemy = close_army or _enemies_near(
                ctx, position, SQUAD_ENGAGE_RANGE
            )

            target = rally if mustering else targeting.squad_destination(ctx, position)

            if (
                close_army
                and min_engage_range is not None
                and not _our_force_larger(ctx, squad.squad_units, close_army)
            ):
                for unit in squad.squad_units:
                    ctx.bot.register_behavior(
                        _kite_maneuver(
                            unit, close_army, min_engage_range, target, grid=grid
                        )
                    )
                continue

            ctx.bot.register_behavior(
                _squad_maneuver_with_influence_retreat(
                    group=squad.squad_units,
                    group_tags=squad.tags,
                    group_position=position,
                    target=target,
                    close_enemy=close_enemy,
                    grid=grid,
                )
            )

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
       (`targeting.squad_destination`) fixes that: it's a fixed point, so the
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
        target = targeting.squad_destination(ctx, biggest.squad_position)
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
    `where`, not in ares' building tracker, not currently constructing, and
    not still mid `ProxyCrewPlan` (any `ctx.state.proxy_crew` slot whose
    `task_index` has not yet exhausted that slot's task list). The tracker /
    constructing checks cover ares-dispatched builders; the crew check covers
    the gap between tasks (e.g. Y waiting on minerals for the proxy Depot
    after Barracks B) when the worker is near the proxy, idle of the tracker,
    and would otherwise be yanked into `PROXY_WORKER` while `proxy_crew` still
    path/build-orders it every frame.

    `claim_gate` is the part that is easy to get wrong, so it is explicit.
    Claiming assigns `UnitRole.PROXY_WORKER`, and `select_worker` only ever
    considers `UnitRole.GATHERING`. Open the gate only once every Barracks
    this build wants is standing or under way - otherwise a builder next to
    the next site gets yanked off and a fresh SCV walks from home. Crew
    workers with remaining tasks are still excluded via `_active_crew_tags`.

    Before the first wave is released the claimed workers wait at the proxy
    rather than running in alone; from wave 1 on they attack the same target
    the squads do, shooting whatever comes into range on the way (enemy
    units take priority over structures, and Eggs/Larva are never a target
    at all - see `_enemies_near`).

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
        busy_crew = _active_crew_tags(ctx)
        radius_sq = claim_radius**2
        for worker in ctx.bot.workers:
            if worker.tag in already or worker.tag in tracker or worker.tag in busy_crew:
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

        busy_crew = _active_crew_tags(ctx)
        # Skip anyone still mid-crew even if an earlier frame already assigned
        # PROXY_WORKER — otherwise path/build from `proxy_crew` and attack/
        # AMove from this routine keep overwriting each other on the same SCV.
        workers = [
            u
            for u in ctx.mediator.get_units_from_role(role=UnitRole.PROXY_WORKER)
            if u.tag not in busy_crew
        ]
        if not workers:
            return

        # Hold at the proxy until the Marines actually leave.
        if ctx.state.wave_number < 1:
            for worker in workers:
                ctx.bot.register_behavior(AMove(unit=worker, target=point))
            return

        target = targeting.squad_destination(ctx, point)
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
