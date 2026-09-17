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
    UseAbility,
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
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units

from bot.builds.definition import _always
from bot.consts import (
    CORRUPTOR_ROLE,
    IGNORED_ENEMY_TYPES,
    SWARM_HOST_ROLE,
    WORKER_TYPES,
    ZERGLING_DEFENDER_ROLE,
)
from bot.core.types import CombatRoutine, Gate, PointLocator
from bot.intel import chargelot_metrics, enemy_army
from bot.routines import targeting
from bot.routines.protoss_support import (
    CHARGELOT_KITE_WINDOW_S,
    CHARGELOT_MUSTER_RADIUS,
    PRISM_ENEMY_PHASE_RANGE,
    chargelot_staging,
)

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
DEFENDER_HOLD_ARRIVE: float = 3.0
"""Within this of the hold point: issue no move (settled)."""
SQUAD_ENGAGE_RANGE: float = 11.5
STALKER_MIN_ENGAGE_RANGE: float = 4.0
"""Chargelot Stalkers hold at least this far back (weapon range ~6) so they
stand behind the Zealot wall instead of crowding into melee range and
stealing the surface area Zealots need to surround the target."""
SQUAD_RADIUS: float = 9.0
MUSTER_RADIUS: float = 4.0
"""How tightly a freshly-released wave must cluster at the rally point
before it is let off to attack, rather than trickling toward the enemy
as units peel off from wherever they were defending."""
CHARGELOT_MUSTER_PRISM_TIMEOUT: float = 55.0
"""If form-up is ready but Prism never reaches enemy-nat range, commit anyway.
Long enough for a late Prism (~6:00) to fly from Robo to staging after leave."""
# CHARGELOT_KITE_WINDOW_S lives in protoss_support (shared with Prism depart).
ARMY_IDLE_CHECK_INTERVAL_S: float = 3.0
"""How often `nudge_idle_army` scans ATTACKING units for idle / HoldPosition
and re-issues an attack-move - catches Zealots that AMove's success radius
left sitting at the ramp bottom with nothing to do."""

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
    `RunState.mustering_tags` so `attack_squads` / `chargelot_attack` holds
    it at the rally point until it clumps - good when the rally is forward
    (proxy, or Chargelot staging in front of the enemy). When False, the
    first wave skips that hold and marches on the same frame.
    """

    # If tech/time gate has been ready this long without wave1_min, leave
    # with whatever army exists (LeyLines Zerg / Terran pressure never hit 12).
    _WAVE1_FORCE_AFTER: float = 25.0
    _WAVE1_FORCE_MIN_FRAC: float = 0.5

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
        gate_ready = plan.wave_gate(ctx)
        force_min = max(1, int(plan.wave1_min * _WAVE1_FORCE_MIN_FRAC))
        force_leave = False
        if gate_ready:
            # Start the clock as soon as leave tech/time is ready — do not
            # wait until size hits force_min (that delayed Terran to ~6:35).
            if ctx.state.chargelot_wave_gate_ready_since is None:
                ctx.state.chargelot_wave_gate_ready_since = ctx.bot.time
            ready_since = ctx.state.chargelot_wave_gate_ready_since
            if size >= force_min and size < plan.wave1_min:
                force_leave = ctx.bot.time - ready_since >= _WAVE1_FORCE_AFTER
            elif size >= plan.wave1_min:
                ctx.state.chargelot_wave_gate_ready_since = None

        if size < plan.wave1_min and not force_leave:
            return
        if not gate_ready:
            ctx.log_once(
                "wave_wait_0",
                f"GATHER wave 1 ({size}/{plan.wave1_min}) - waiting on tech",
            )
            return

        ctx.mediator.batch_assign_role(tags=tags, role=UnitRole.ATTACKING)
        if muster:
            ctx.state.mustering_tags.update(tags)
        ctx.state.wave_number = 1
        ctx.state.chargelot_wave_gate_ready_since = None
        why = f" force_min={size}" if force_leave else ""
        ctx.log(
            f"WAVE 1 attack (size={size}) - streaming from here on"
            f"{'' if muster else ' (no muster)'}{why}"
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
    must never fight, where `AMove`'s attack-move risks it trading blows.

    Skips re-issue when the unit is already moving to the same rounded tile —
    per-frame `move` spam is what made home Zealots/Stalkers thrash in place.
    """

    unit: Unit
    target: Point2

    def execute(self, ai, config, mediator, **kwargs) -> bool:
        if _already_ordered_to_point(self.unit, self.target):
            return False
        self.unit.move(self.target)
        return True


def _already_ordered_to_point(unit: Unit, target: Point2) -> bool:
    """True when the unit's current order already aims at `target`'s tile."""
    if not unit.orders:
        return False
    ability_id = getattr(getattr(unit.orders[0], "ability", None), "id", None)
    # SC2 often reports MOVE order_target as a path waypoint, not the final
    # hold — comparing tiles then re-issues every frame and thrash-cancels
    # pathing once many units crowd the rally.
    if ability_id == AbilityId.MOVE and bool(getattr(unit, "is_moving", False)):
        return True
    order_target = unit.order_target
    if isinstance(order_target, Point2):
        if order_target.rounded == target.rounded:
            return True
        # Pathing / float drift — still heading to nearly the same point.
        if cy_distance_to(order_target, target) <= 1.5:
            return True
        return False
    return False


def _already_attacking(unit: Unit, target: Unit) -> bool:
    """True when the unit is already ordered onto `target` (skip re-issue)."""
    return bool(unit.orders) and unit.order_target == target.tag




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


def _squad_maneuver_commit(
    group,
    group_tags: set[int],
    group_position,
    target,
    close_enemy,
) -> CombatManeuver:
    """Reusable ATTACKING-squad maneuver with no influence-retreat at all -
    for comps where retreating off a cooldown achieves nothing (Zergling is
    melee, zero benefit from kiting; Roach's whole identity here is "cheap
    to hold ground with", not hit-and-run - see `bot.builds.zerg.macro_
    zerg`'s own module docstring).

    `KeepGroupSafe` treats standing on enemy-influenced ground as unsafe
    whenever a unit's weapon is on cooldown (`ShootTargetInRange` -> `cy_
    attack_ready` fails, falls through to `KeepUnitSafe`) - during any
    sustained melee/short-range brawl that's true for close to the whole
    squad, staggered by each unit's own cooldown timer. `KeepGroupSafe`
    only needs ONE unit to want out to short-circuit the entire maneuver
    (`CombatManeuver.execute` stops at the first behavior that acts), so
    `StutterGroupForward`/`AMoveGroup` never got a turn - confirmed live:
    "the majority of our Roaches and Zerglings never attacked... maintained
    their distance and never engaged" while nominally ATTACKING. Same
    idiom as `chargelot_attack`'s Zealots ("never KeepUnitSafe — overwhelm"),
    generalized as an `attack_squads` flag instead of a whole separate
    routine, since Macro Zerg still wants `attack_squads`'s existing
    muster/breach-worker logic.
    """
    maneuver = CombatManeuver()
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


def _sticky_hold_point(
    ctx: "BotContext",
    unit: Unit,
    holds: list[Point2],
    hold_map: dict[int, Point2],
    log_label: str = "DEFEND",
) -> Point2:
    """Stable hold per unit — match by Point2, not list index. Shared by
    `defend_home` (`ctx.state.defender_hold`) and `dig_in_swarm_hosts`
    (`ctx.state.swarm_host_hold`) — same load-balancing problem either way:
    match an existing assignment first, else pick whichever point currently
    has the fewest occupants."""
    assigned = hold_map.get(unit.tag)
    if assigned is not None:
        nearest = min(holds, key=lambda h: cy_distance_to_squared(assigned, h))
        if cy_distance_to(assigned, nearest) <= 2.5:
            hold_map[unit.tag] = nearest
            return nearest
    loads = [0] * len(holds)
    for other_tag, other_pt in hold_map.items():
        if other_tag == unit.tag:
            continue
        nearest_i = min(
            range(len(holds)),
            key=lambda i: cy_distance_to_squared(other_pt, holds[i]),
        )
        if cy_distance_to(other_pt, holds[nearest_i]) <= 2.5:
            loads[nearest_i] += 1
    best = min(range(len(holds)), key=lambda i: loads[i])
    hold_map[unit.tag] = holds[best]
    ctx.log(
        f"{log_label} hold slot={best}/{len(holds)} "
        f"tag={unit.tag} type={unit.type_id.name}"
    )
    return holds[best]


def _defender_maneuver(ctx: "BotContext", unit, home_threats, hold) -> CombatManeuver | None:
    """Orders for one defender. Returns None when settled — no order spam."""
    # Lone scouting SCVs/Probes in 12 range made the whole ball Attack↔Move
    # thrash at the ramp once warps stacked (debug: 461/461 engages were SCV).
    near = [
        e
        for e in _enemies_near(ctx, unit.position, DEFENDER_ENGAGE_RANGE)
        if e.type_id not in WORKER_TYPES
    ]
    if near:
        maneuver = CombatManeuver()
        maneuver.add(ShootTargetInRange(unit=unit, targets=near))
        closest = cy_closest_to(position=unit.position, units=near)
        # Only chase when nothing is already under the weapon / order — AttackTarget
        # every frame cancels windup and looks like thrashing at home.
        if not _already_attacking(unit, closest):
            weapon_targets = list(cy_in_attack_range(unit, list(near)))
            if not weapon_targets:
                maneuver.add(AttackTarget(unit=unit, target=closest))
        return maneuver
    if home_threats:
        threat = cy_closest_to(position=unit.position, units=home_threats)
        if threat.type_id not in WORKER_TYPES:
            if _already_attacking(unit, threat):
                return None
            maneuver = CombatManeuver()
            maneuver.add(AttackTarget(unit=unit, target=threat))
            return maneuver

    dist = cy_distance_to(unit.position, hold)
    if dist <= DEFENDER_HOLD_ARRIVE:
        return None
    if _already_ordered_to_point(unit, hold):
        return None
    maneuver = CombatManeuver()
    # Plain move — AMove attack-moves and re-issues every frame past 7 range.
    maneuver.add(_Move(unit=unit, target=hold))
    return maneuver


def defend_home() -> CombatRoutine:
    """Units still in DEFENDING hold the natural and collapse on anything near."""

    def routine(ctx: "BotContext") -> None:
        defenders = ctx.units_in_role(UnitRole.DEFENDING)
        alive = {u.tag for u in defenders}
        ctx.state.defender_hold = {
            tag: pt
            for tag, pt in ctx.state.defender_hold.items()
            if tag in alive
        }
        if not defenders:
            return
        home_threats = ctx.mediator.get_main_ground_threats_near_townhall
        holds = targeting.hold_positions(ctx)
        if not holds:
            return
        for unit in defenders:
            hold = _sticky_hold_point(ctx, unit, holds, ctx.state.defender_hold)
            maneuver = _defender_maneuver(ctx, unit, home_threats, hold)
            if maneuver is not None:
                ctx.bot.register_behavior(maneuver)

    return routine


def defend_with_zerglings() -> CombatRoutine:
    """Drive Zerglings parked on `ZERGLING_DEFENDER_ROLE` as home defenders.

    Macro Zerg peels the first `HOME_ZERGLING_CAP` Zerglings onto this role
    (`builds.zerg.macro_zerg._macro_zerg_on_unit_created`); extras stay in
    `army.types` / DEFENDING and join attack waves. Mirrors `defend_home()`'s
    hold/engage logic, reading the defender role directly and keeping its
    own hold map (`ctx.state.zergling_defender_hold`).
    """

    def routine(ctx: "BotContext") -> None:
        zerglings = ctx.mediator.get_units_from_role(
            role=ZERGLING_DEFENDER_ROLE, unit_type=UnitTypeId.ZERGLING
        )
        alive = {u.tag for u in zerglings}
        ctx.state.zergling_defender_hold = {
            tag: pt
            for tag, pt in ctx.state.zergling_defender_hold.items()
            if tag in alive
        }
        if not zerglings:
            return
        home_threats = ctx.mediator.get_main_ground_threats_near_townhall
        holds = targeting.hold_positions(ctx)
        if not holds:
            return
        for unit in zerglings:
            hold = _sticky_hold_point(
                ctx,
                unit,
                holds,
                ctx.state.zergling_defender_hold,
                log_label="ZERGLING_DEFEND",
            )
            maneuver = _defender_maneuver(ctx, unit, home_threats, hold)
            if maneuver is not None:
                ctx.bot.register_behavior(maneuver)

    return routine


def _near_enemy_base(ctx: "BotContext", position: Point2) -> bool:
    """True when `position` sits in an enemy mineral line / townhall radius."""
    radius = ctx.bot.EXPANSION_GAP_THRESHOLD
    radius_sq = radius * radius
    for th in ctx.bot.enemy_structures:
        if th.type_id not in {
            UnitTypeId.COMMANDCENTER,
            UnitTypeId.ORBITALCOMMAND,
            UnitTypeId.PLANETARYFORTRESS,
            UnitTypeId.NEXUS,
            UnitTypeId.HATCHERY,
            UnitTypeId.LAIR,
            UnitTypeId.HIVE,
        }:
            continue
        if cy_distance_to_squared(position, th.position) <= radius_sq:
            return True
    for loc in ctx.bot.enemy_start_locations:
        if cy_distance_to_squared(position, loc) <= radius_sq:
            return True
    return False


def _breach_worker_target(ctx: "BotContext", unit: Unit) -> Unit | None:
    """Closest enemy worker when this unit is breaching an enemy base."""
    if not _near_enemy_base(ctx, unit.position):
        return None
    workers = [
        e
        for e in _enemies_near(ctx, unit.position, SQUAD_ENGAGE_RANGE)
        if e.type_id in WORKER_TYPES
    ]
    if not workers:
        return None
    return cy_closest_to(position=unit.position, units=workers)


def attack_squads(
    squad_radius: float = SQUAD_RADIUS,
    min_engage_range: float | None = None,
    never_retreat: bool = False,
    kite_types: frozenset = frozenset(),
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
    `_enemies_near` still supplies structures to shoot. `kite_types` splits
    the squad first - anything of one of those types always kites via `_
    kite_maneuver` at `min_engage_range`, unconditionally (not just when
    outnumbered), and deliberately *without* the influence grid (see below
    for why). Everything else in the squad falls through to the existing
    force-size-based dispatch:

    - `never_retreat=True`: skip influence-retreat entirely - see `_squad_
      maneuver_commit`'s own docstring for exactly why a comp would want
      this (in short: melee/short-range units that gain nothing from
      kiting get stuck peeling backward one at a time instead of ever
      landing damage together).
    - Otherwise, unsafe ground influence: `KeepGroupSafe` / `KeepUnitSafe`
      run first so the ball leaves bad tiles instead of parking.
    - Enemy force strictly smaller than ours: `StutterGroupForward` trades
      as one group toward the destination.
    - Enemy force equal or larger, and `min_engage_range` is set: each unit
      is driven individually via `_kite_maneuver` (this time *with* the
      influence grid, matching Four Rax Proxy's Marines) - backing away
      from anything closer than `min_engage_range`, otherwise shooting.
      Builds that leave `min_engage_range` unset keep group stutter after
      influence retreat rather than per-unit kite.

    `kite_types` omits the influence grid on purpose: `KeepUnitSafe` backs
    a unit off any ground `mediator.is_position_safe` calls unsafe the
    moment its weapon is on cooldown, regardless of `min_engage_range` -
    confirmed live as the exact mechanism behind "the majority of our
    Roaches and Zerglings never attacked" (see `_squad_maneuver_commit`'s
    own docstring for the full incident). A unit standing at its own
    `min_engage_range` from a same-range enemy (Roach vs Roach, both range
    4) sits in that enemy's influence for the entire fight, so passing the
    grid here would silently reintroduce that bug for exactly the comp
    `kite_types` is meant to protect. `min_engage_range`'s own crowding
    check already keeps distance; it doesn't need `KeepUnitSafe`'s help to
    do it safely.

    ATTACKING Zerglings that are already inside an enemy base peel off to
    prioritize workers (Macro Zerg breach micro) regardless of any of the
    above.
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

            # Zerglings breaching an enemy base prioritize workers.
            group_units = []
            group_tags = set()
            for unit in squad.squad_units:
                if unit.type_id == UnitTypeId.ZERGLING and not mustering:
                    worker = _breach_worker_target(ctx, unit)
                    if worker is not None:
                        if not _already_attacking(unit, worker):
                            maneuver = CombatManeuver()
                            maneuver.add(AttackTarget(unit=unit, target=worker))
                            ctx.bot.register_behavior(maneuver)
                        continue
                group_units.append(unit)
                group_tags.add(unit.tag)
            if not group_units:
                continue

            close_army = _intel_army_near(ctx, position, SQUAD_ENGAGE_RANGE)
            close_enemy = close_army or _enemies_near(
                ctx, position, SQUAD_ENGAGE_RANGE
            )

            target = rally if mustering else targeting.squad_destination(ctx, position)

            if kite_types and not mustering:
                kiters = [u for u in group_units if u.type_id in kite_types]
                if kiters:
                    for unit in kiters:
                        ctx.bot.register_behavior(
                            _kite_maneuver(unit, close_enemy, min_engage_range, target)
                        )
                    group_units = [u for u in group_units if u.type_id not in kite_types]
                    group_tags = {u.tag for u in group_units}
                    if not group_units:
                        continue

            if (
                not never_retreat
                and close_army
                and min_engage_range is not None
                and not _our_force_larger(ctx, group_units, close_army)
            ):
                for unit in group_units:
                    ctx.bot.register_behavior(
                        _kite_maneuver(
                            unit, close_army, min_engage_range, target, grid=grid
                        )
                    )
                continue

            if never_retreat:
                ctx.bot.register_behavior(
                    _squad_maneuver_commit(
                        group=group_units,
                        group_tags=group_tags,
                        group_position=position,
                        target=target,
                        close_enemy=close_enemy,
                    )
                )
                continue

            ctx.bot.register_behavior(
                _squad_maneuver_with_influence_retreat(
                    group=group_units,
                    group_tags=group_tags,
                    group_position=position,
                    target=target,
                    close_enemy=close_enemy,
                    grid=grid,
                )
            )

    return routine


def _enemies_near_ground_air(ctx: "BotContext", point, distance: float) -> list[Unit]:
    """Ground + air enemies near `point` (Stalkers need Medivacs)."""
    ground = ctx.mediator.get_units_in_range(
        start_points=[point],
        distances=distance,
        query_tree=UnitTreeQueryType.EnemyGround,
    )[0]
    air = ctx.mediator.get_units_in_range(
        start_points=[point],
        distances=distance,
        query_tree=UnitTreeQueryType.EnemyFlying,
    )[0]
    seen: set[int] = set()
    out: list[Unit] = []
    for group in (ground, air):
        for u in group:
            if u.tag in seen or u.type_id in IGNORED_ENEMY_TYPES:
                continue
            seen.add(u.tag)
            out.append(u)
    return out


def stalker_target_score(
    *, is_medivac: bool, is_repairing: bool, is_worker: bool, vital: float
) -> tuple[int, int, int, float]:
    """Pure priority key for `_stalker_pick_target` (lower sorts first).

    Order: Medivac → repairing-or-wall worker → any worker → lowest
    current HP+shield among what's left.
    """
    return (
        0 if is_medivac else 1,
        0 if is_repairing else 1,
        0 if is_worker else 1,
        vital,
    )


def _is_repairing(unit: Unit) -> bool:
    orders = getattr(unit, "orders", None) or ()
    for order in orders:
        ability = getattr(order, "ability", None)
        name = getattr(ability, "name", "") or ""
        if "Repair" in name or "REPAIR" in name:
            return True
    return False


def _stalker_pick_target(stalker: Unit, enemies: list[Unit]) -> Unit | None:
    """Prefer Medivacs / repairing-or-wall SCVs, else lowest-HP in range."""
    in_range = list(cy_in_attack_range(stalker, enemies))
    if not in_range:
        return None

    def _score(u: Unit) -> tuple:
        return stalker_target_score(
            is_medivac=u.type_id == UnitTypeId.MEDIVAC,
            is_repairing=_is_repairing(u),
            is_worker=u.type_id in WORKER_TYPES,
            vital=u.health + u.shield,
        )

    return min(in_range, key=_score)


def _stalker_retreat_point(
    stalker: Unit, crowding: list[Unit], min_engage_range: float
) -> Point2:
    """Where a stalker crowded by `crowding` should back off to.

    Backs straight away from the crowding units' center (or the lone
    crowder's position) to exactly `min_engage_range` - same shape as
    `_kite_maneuver`'s retreat calc, kept separate here so it can compose
    with `_stalker_pick_target`'s Medivac/repair-worker priority instead of
    `_kite_maneuver`'s generic lowest-HP targeting.
    """
    retreat_from = (
        Point2(cy_center(crowding)) if len(crowding) > 1 else crowding[0].position
    )
    return Point2(cy_towards(retreat_from, stalker.position, min_engage_range))


def _prism_near_enemy_for_muster(ctx: "BotContext") -> bool:
    """True when any Prism is in enemy-nat phase range.

    If a Prism (or Robo → Prism) is still coming, return False so muster
    holds. Soft-unlock only when there is no Robo path at all — otherwise
    Wave 1 commits at ~5:40 while Prism finishes at ~6:00 and never phases.
    """
    prisms = [
        u
        for u in ctx.mediator.get_units_from_role(role=UnitRole.DROP_SHIP)
        if u.type_id in (UnitTypeId.WARPPRISM, UnitTypeId.WARPPRISMPHASING)
    ]
    enemy = ctx.mediator.get_enemy_nat
    if prisms:
        return any(
            cy_distance_to(p.position, enemy) <= PRISM_ENEMY_PHASE_RANGE
            for p in prisms
        )
    bot = ctx.bot
    expecting_prism = (
        bot.structures(UnitTypeId.ROBOTICSFACILITY).amount
        + bot.structure_pending(UnitTypeId.ROBOTICSFACILITY)
        > 0
        or bot.already_pending(UnitTypeId.WARPPRISM) > 0
    )
    return not expecting_prism


def muster_commit_decision(
    form_ready: bool,
    prism_ready: bool,
    waiting_since: float | None,
    now: float,
    prism_timeout: float = CHARGELOT_MUSTER_PRISM_TIMEOUT,
) -> tuple[bool, bool]:
    """Pure muster-commit gate for `chargelot_attack`.

    The first wave commits once it has formed up (`form_ready`) and either
    the Warp Prism has reached phase range (`prism_ready`) or the Prism-wait
    clock — `waiting_since`, latched the first frame form-up was ready but
    the Prism wasn't — has run past `prism_timeout`.

    Returns `(commit, prism_wait_expired)`. `prism_wait_expired` is only
    meaningful when `commit` is True, to pick a "ready" vs "prism_timeout"
    log reason.
    """
    if not form_ready:
        return False, False
    if prism_ready:
        return True, False
    prism_wait_expired = (
        waiting_since is not None and now - waiting_since >= prism_timeout
    )
    return prism_wait_expired, prism_wait_expired


def chargelot_kiting(
    committed_at: float | None,
    now: float,
    window: float = CHARGELOT_KITE_WINDOW_S,
) -> bool:
    """True while the first-wave muster is inside its post-commit kite
    window - see `chargelot_attack`. `committed_at` is `None` before the
    muster has ever committed."""
    if committed_at is None:
        return False
    return now - committed_at < window


def chargelot_attack(squad_radius: float = SQUAD_RADIUS) -> CombatRoutine:
    """Chargelot all-in micro: Zealots commit, Stalkers snipe the backline.

    Unlike `attack_squads`, Zealots never influence-retreat — they AMove the
    attack objective and trade. Stalkers stutter at range and prioritize
    Medivacs and workers (repair / wall) over the marine ball.

    The first wave musters at `chargelot_staging` (in front of the enemy
    natural) as one ball before committing — not the home `rally_point`,
    so defenders stay home until WAVE 1 leaves. After committing, it holds
    at staging for `CHARGELOT_KITE_WINDOW_S` more (fighting anything that
    comes to it, but not pushing further in) before switching to full
    onslaught — see `chargelot_kiting`.
    """

    _last_sig: dict[str, object] = {"sig": None}

    def routine(ctx: "BotContext") -> None:
        attackers = list(ctx.units_in_role(UnitRole.ATTACKING))
        alive_attackers = {u.tag for u in attackers}
        ctx.state.mustering_tags &= alive_attackers

        staging = chargelot_staging(ctx)
        mustering_units = [u for u in attackers if u.tag in ctx.state.mustering_tags]
        muster_center_dist = None
        muster_near_frac = None
        if mustering_units:
            center = Point2(cy_center(mustering_units))
            muster_center_dist = cy_distance_to(center, staging)
            near_n = sum(
                1
                for u in mustering_units
                if cy_distance_to(u.position, staging)
                <= CHARGELOT_MUSTER_RADIUS + 3.0
            )
            muster_near_frac = near_n / len(mustering_units)
            # Release only when the whole first wave is on station — not
            # per-squad, or early arrivals trickle into the base alone.
            # Also wait for the Prism to reach enemy-nat phase range so the
            # ball does not dive before the field can go up.
            prism_ready = _prism_near_enemy_for_muster(ctx)
            form_ready = (
                muster_center_dist <= CHARGELOT_MUSTER_RADIUS
                and muster_near_frac >= 0.75
            )
            waiting_since = None
            if form_ready and not prism_ready:
                chargelot_metrics.note_muster_waiting_prism(ctx)
                waiting_since = ctx.state.chargelot_metrics.muster_form_ready_since
            commit, prism_wait_expired = muster_commit_decision(
                form_ready, prism_ready, waiting_since, ctx.bot.time
            )
            if commit:
                reason = "prism_timeout" if prism_wait_expired else "ready"
                ctx.log(
                    f"MUSTER commit n={len(mustering_units)} "
                    f"dist={muster_center_dist:.0f} reason={reason}"
                )
                chargelot_metrics.note_muster_commit(ctx)
                ctx.state.chargelot_muster_committed_at = ctx.bot.time
                ctx.state.mustering_tags.clear()
                mustering_units = []
            elif form_ready and not prism_ready:
                ctx.log_once(
                    "muster_hold_prism",
                    f"MUSTER holding for Prism n={len(mustering_units)} "
                    f"dist={muster_center_dist:.0f}",
                )

        still_mustering = bool(ctx.state.mustering_tags)
        kiting = chargelot_kiting(
            ctx.state.chargelot_muster_committed_at, ctx.bot.time
        )

        squads = ctx.mediator.get_squads(
            role=UnitRole.ATTACKING, squad_radius=squad_radius
        )
        if not squads:
            return

        enemy_nat = ctx.mediator.get_enemy_nat
        total_z = 0
        total_s = 0
        closest_enemy = 999.0
        mustering_n = len(ctx.state.mustering_tags)

        for squad in squads:
            position = squad.squad_position
            # Whole attack force holds at staging until the first-wave muster
            # commits — streamers must not dive past the ball into the base.
            # For CHARGELOT_KITE_WINDOW_S after commit, still hold at staging
            # (fighting anything that comes to it) instead of pushing to the
            # real objective, so units still closing on the muster point have
            # time to catch up rather than the wave committing alone.
            squad_mustering = still_mustering
            target = (
                staging
                if squad_mustering or kiting
                else targeting.squad_destination(ctx, position)
            )
            closest_enemy = min(closest_enemy, cy_distance_to(position, enemy_nat))

            zealots = [
                u for u in squad.squad_units if u.type_id == UnitTypeId.ZEALOT
            ]
            stalkers = [
                u for u in squad.squad_units if u.type_id == UnitTypeId.STALKER
            ]
            total_z += len(zealots)
            total_s += len(stalkers)

            # Zealots: never KeepUnitSafe — overwhelm. While mustering, only
            # path to staging (no AttackTarget short-circuit into the nat).
            # After commit, keep re-issuing AMove (success_at_distance=0) so
            # arriving near staging/objective does not leave them idle.
            for zealot in zealots:
                maneuver = CombatManeuver()
                near = _enemies_near(ctx, zealot.position, SQUAD_ENGAGE_RANGE)
                if squad_mustering:
                    maneuver.add(AMove(unit=zealot, target=target))
                else:
                    if near:
                        maneuver.add(ShootTargetInRange(unit=zealot, targets=near))
                        maneuver.add(
                            AttackTarget(
                                unit=zealot,
                                target=cy_closest_to(
                                    position=zealot.position, units=near
                                ),
                            )
                        )
                    maneuver.add(
                        AMove(unit=zealot, target=target, success_at_distance=0.0)
                    )
                ctx.bot.register_behavior(maneuver)

            # Stalkers: while mustering, stick with the ball; after commit,
            # pick Medivacs / repair SCVs and kite on cooldown.
            for stalker in stalkers:
                maneuver = CombatManeuver()
                if squad_mustering:
                    near = _enemies_near_ground_air(
                        ctx, stalker.position, SQUAD_ENGAGE_RANGE
                    )
                    if near and stalker.weapon_cooldown <= 0.1:
                        pick = _stalker_pick_target(stalker, near)
                        if pick is not None:
                            maneuver.add(AttackTarget(unit=stalker, target=pick))
                    maneuver.add(AMove(unit=stalker, target=target))
                else:
                    enemies = _enemies_near_ground_air(
                        ctx, stalker.position, SQUAD_ENGAGE_RANGE + 2.0
                    )
                    in_range = list(cy_in_attack_range(stalker, enemies))
                    crowding = [
                        e
                        for e in in_range
                        if cy_distance_to(stalker.position, e.position)
                        < STALKER_MIN_ENGAGE_RANGE
                    ]
                    if crowding:
                        # Too close - back off to exactly min-range before
                        # shooting again, regardless of cooldown, so Stalkers
                        # do not plant in melee range once they close in.
                        retreat_to = _stalker_retreat_point(
                            stalker, crowding, STALKER_MIN_ENGAGE_RANGE
                        )
                        maneuver.add(_Move(unit=stalker, target=retreat_to))
                    else:
                        pick = _stalker_pick_target(stalker, in_range)
                        if pick is not None and stalker.weapon_cooldown <= 0.1:
                            maneuver.add(AttackTarget(unit=stalker, target=pick))
                        elif pick is not None and stalker.weapon_cooldown > 0.1:
                            kite_to = Point2(
                                cy_towards(pick.position, stalker.position, 1.5)
                            )
                            maneuver.add(_Move(unit=stalker, target=kite_to))
                        else:
                            maneuver.add(AMove(unit=stalker, target=target))
                ctx.bot.register_behavior(maneuver)

            # Any other ATTACKING leftovers (should be rare) AMove with zealots.
            other = [
                u
                for u in squad.squad_units
                if u.type_id not in (UnitTypeId.ZEALOT, UnitTypeId.STALKER)
            ]
            for unit in other:
                ctx.bot.register_behavior(AMove(unit=unit, target=target))

        sig = (
            total_z,
            total_s,
            int(closest_enemy // 5),
            mustering_n > 0,
            kiting,
            None if muster_center_dist is None else int(muster_center_dist // 5),
        )
        if _last_sig["sig"] != sig:
            _last_sig["sig"] = sig
            if mustering_n:
                ctx.log(
                    f"MUSTER n={mustering_n} "
                    f"centerDist={muster_center_dist:.0f} "
                    f"near={muster_near_frac:.0%} "
                    f"enemyNat={closest_enemy:.0f}"
                )
            elif kiting:
                remaining = CHARGELOT_KITE_WINDOW_S - (
                    ctx.bot.time - ctx.state.chargelot_muster_committed_at
                )
                ctx.log(
                    f"KITE remaining={remaining:.0f} "
                    f"enemyNat={closest_enemy:.0f} z={total_z} s={total_s}"
                )
            elif closest_enemy < 40:
                ctx.log(
                    f"ENGAGE near enemy nat={closest_enemy:.0f} "
                    f"z={total_z} s={total_s}"
                )

    return routine


def _attacker_needs_work(unit: Unit) -> bool:
    """True when an ATTACKING unit has no useful order (idle or HoldPosition)."""
    if getattr(unit, "is_idle", False) or not unit.orders:
        return True
    ability_id = getattr(getattr(unit.orders[0], "ability", None), "id", None)
    return ability_id in (AbilityId.HOLDPOSITION, AbilityId.HOLDPOSITION_HOLD)


def nudge_idle_army(
    interval_s: float = ARMY_IDLE_CHECK_INTERVAL_S,
) -> CombatRoutine:
    """Periodic safety net: re-engage ATTACKING units that went idle.

    `AMove`'s default `success_at_distance` stops issuing once a unit is near
    its point - Zealots at staging / ramp bottom then sit with empty orders
    until something else wakes them. Every few seconds, scan and attack-move
    stragglers toward the current army destination.

    Skips first-wave mustering tags and Prism drop-load peels (intentional
    HoldPosition while boarding).
    """

    def routine(ctx: "BotContext") -> None:
        last = ctx.state.army_idle_check_at
        if last is not None and ctx.bot.time - last < interval_s:
            return
        ctx.state.army_idle_check_at = ctx.bot.time

        protected = set(ctx.state.mustering_tags)
        for unit in ctx.mediator.get_units_from_role(
            role=UnitRole.DROP_UNITS_TO_LOAD
        ):
            protected.add(unit.tag)

        idle = [
            u
            for u in ctx.units_in_role(UnitRole.ATTACKING)
            if u.tag not in protected and _attacker_needs_work(u)
        ]
        if not idle:
            return

        still_mustering = bool(ctx.state.mustering_tags)
        kiting = chargelot_kiting(
            ctx.state.chargelot_muster_committed_at, ctx.bot.time
        )
        if still_mustering or kiting:
            dest = chargelot_staging(ctx)
        else:
            dest = targeting.squad_destination(ctx, Point2(cy_center(idle)))

        for unit in idle:
            unit.attack(dest)
        ctx.log(f"ARMY_IDLE nudged {len(idle)} -> ({dest.x:.0f},{dest.y:.0f})")

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


SWARM_HOST_FORWARD_OFFSET: float = 8.0
"""How far in front of each owned base a Swarm Host's hold point sits -
close enough to stay defensive, far enough that Locusts reach past the
mineral line toward the likely approach."""
SWARM_HOST_LOCUST_RANGE: float = 10.0
"""How far past the hold point, toward the enemy, Spawn Locusts is cast."""


def _swarm_host_points(ctx: "BotContext") -> list[Point2]:
    """One forward point per owned base, facing the enemy start."""
    enemy = ctx.bot.enemy_start_locations[0]
    return [
        Point2(cy_towards(base, enemy, SWARM_HOST_FORWARD_OFFSET))
        for base in ctx.bot.owned_expansions
    ]


def dig_in_swarm_hosts() -> CombatRoutine:
    """Park Swarm Hosts at a forward point per owned base and let Spawn
    Locusts do the work - no squad clustering, no AMove-into-melee, and
    never promoted out of `SWARM_HOST_ROLE` (see `core.roles.SUPPORT_ROLES`
    - Swarm Host is deliberately kept out of `army.types` so `release_waves`
    can never sweep it into a muster). Reads the role directly via
    `ctx.mediator.get_units_from_role` rather than `ctx.units_in_role`,
    which filters by `army.types` and would always return nothing here.

    Burrows once in position, if Burrow is researched - purely for
    survivability; Spawn Locusts works the same either way. Both abilities
    are tried unconditionally every frame once settled: `UseAbility.execute`
    already checks `ability in unit.abilities` and no-ops otherwise, and
    `CombatManeuver.execute` stops at the first one that actually fires -
    burrowing and casting can never both go out on the same frame, which is
    correct (they're mutually exclusive actions in-game too).
    """

    def routine(ctx: "BotContext") -> None:
        hosts = list(ctx.mediator.get_units_from_role(role=SWARM_HOST_ROLE))
        alive = {u.tag for u in hosts}
        ctx.state.swarm_host_hold = {
            tag: pt for tag, pt in ctx.state.swarm_host_hold.items() if tag in alive
        }
        if not hosts:
            return
        points = _swarm_host_points(ctx)
        if not points:
            return

        grid = ctx.mediator.get_ground_grid
        enemy = ctx.bot.enemy_start_locations[0]
        burrow_done = UpgradeId.BURROW in ctx.bot.state.upgrades

        for host in hosts:
            hold = _sticky_hold_point(
                ctx, host, points, ctx.state.swarm_host_hold, log_label="SWARM_HOST"
            )
            maneuver = CombatManeuver()
            maneuver.add(KeepUnitSafe(unit=host, grid=grid))

            if cy_distance_to(host.position, hold) > DEFENDER_HOLD_ARRIVE:
                maneuver.add(MoveToSafeTarget(unit=host, grid=grid, target=hold))
            else:
                locust_target = Point2(
                    cy_towards(hold, enemy, SWARM_HOST_LOCUST_RANGE)
                )
                ctx.log_once(
                    f"swarm_host_dug_in_{host.tag}",
                    f"SWARM_HOST dug in at {hold}, spawning locusts "
                    f"toward {locust_target}",
                )
                if burrow_done:
                    maneuver.add(UseAbility(AbilityId.BURROWDOWN_SWARMHOST, host))
                maneuver.add(
                    UseAbility(
                        AbilityId.EFFECT_SPAWNLOCUSTS, host, target=locust_target
                    )
                )
                maneuver.add(
                    UseAbility(
                        AbilityId.SWARMHOSTSPAWNLOCUSTS_LOCUSTMP,
                        host,
                        target=locust_target,
                    )
                )
            ctx.bot.register_behavior(maneuver)

    return routine


def escort_corruptors() -> CombatRoutine:
    """Follow the biggest ATTACKING squad the way `escort_overseers` does,
    but actually fight - Corruptor is anti-air only, so it engages air
    targets in range directly rather than joining `attack_squads`'s
    ground-target-seeking AMove/kite maneuvers, which would waste it on
    objectives it cannot damage at all. Kept out of `army.types` and its
    own `CORRUPTOR_ROLE` for the same reason Swarm Host is (see
    `core.roles.SUPPORT_ROLES`)."""

    def routine(ctx: "BotContext") -> None:
        corruptors = list(ctx.mediator.get_units_from_role(role=CORRUPTOR_ROLE))
        if not corruptors:
            return

        squads = ctx.mediator.get_squads(
            role=UnitRole.ATTACKING, squad_radius=SQUAD_RADIUS
        )
        target = None
        if squads:
            biggest = max(squads, key=lambda squad: len(squad.squad_units))
            target = targeting.squad_destination(ctx, biggest.squad_position)

        grid = ctx.mediator.get_air_grid
        for corruptor in corruptors:
            maneuver = CombatManeuver()
            maneuver.add(KeepUnitSafe(unit=corruptor, grid=grid))
            air = ctx.mediator.get_units_in_range(
                start_points=[corruptor.position],
                distances=corruptor.air_range,
                query_tree=UnitTreeQueryType.EnemyFlying,
            )[0]
            if air:
                maneuver.add(ShootTargetInRange(unit=corruptor, targets=air))
                closest = cy_closest_to(position=corruptor.position, units=air)
                maneuver.add(AttackTarget(unit=corruptor, target=closest))
            elif target is not None:
                maneuver.add(MoveToSafeTarget(unit=corruptor, grid=grid, target=target))
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
