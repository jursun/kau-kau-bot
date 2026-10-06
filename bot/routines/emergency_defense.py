"""Home emergency: an army attacking into our bases / Spine Crawlers.

When a timing attack arrives we hold the *core* (main + natural) and give up
the outlying bases rather than spread thin:

- Only enemies near the core count as a threat; a 3rd base under attack is
  left to its fate while the army and new Spines gather at the natural
  (`steps.zerg.early_aggression_spines` adds more while `home_threat_until`
  is live).
- Queens drop injects and creep, pre-position at the Spines, Transfuse damaged
  Spine/Spore Crawlers (and each other) and shoot what is in reach.
- Drones are pulled only once the enemy army has actually engaged us in
  range of the Spines, and only if the defenders are outmatched: they soak
  damage in front of the Spines while the Spines, Zerglings and Queens do
  the killing. Pulling them as the enemy walks in just feeds them to the
  army one at a time and wrecks the economy for nothing.

Pulled units sit on `UnitRole.BASE_DEFENDER` for the duration, which takes
them off the inject / creep / mining routines (those order units by role, and
two routines ordering one unit every frame is how Queens thrash). Roles are
restored when the threat has been gone for `PULL_HOLD_S`.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from ares.behaviors.combat import CombatManeuver
from ares.behaviors.combat.individual import (
    AttackTarget,
    PathUnitToTarget,
    ShootTargetInRange,
    UseAbility,
)
from ares.consts import UnitRole
from cython_extensions import cy_center, cy_closest_to, cy_distance_to
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.core.types import CombatRoutine
from bot.intel import enemy_army

if TYPE_CHECKING:
    from bot.core.context import BotContext

# Enemy army within this of a core townhall / Spine Crawler is "at home".
THREAT_TOWNHALL_RADIUS: float = 25.0
THREAT_SPINE_RADIUS: float = 14.0
CORE_RADIUS: float = 15.0
"""A townhall this close to the main or natural is part of the core we hold."""
SPINE_ENGAGE_RADIUS: float = 10.0
"""Spine range (7) + body + a step: the enemy is fighting the Spines."""
TOWNHALL_ENGAGE_RADIUS: float = 12.0
"""No Spines up: an enemy this close to a core townhall is attacking it."""
ENGAGE_MIN_UNITS: int = 2
# Fewer enemy units than this (by count and by supply) is a scout / poke.
THREAT_MIN_UNITS: int = 3
THREAT_MIN_SUPPLY: float = 6.0
PULL_HOLD_S: float = 4.0
"""Keep the pull this long after the enemy was last seen at home."""

# Drones pulled: about one per enemy supply, within these bounds.
DRONE_PULL_MIN: int = 8
DRONE_PULL_MAX: int = 24
# Defenders are "enough" - no drone pull - at this multiple of enemy supply.
DEFENSE_ENOUGH_RATIO: float = 1.3
DEFENSE_RADIUS: float = 30.0
SPINE_DEFENSE_SUPPLY: float = 4.0
QUEEN_DEFENSE_SUPPLY: float = 2.0

TRANSFUSE_ENERGY: float = 50.0
TRANSFUSE_RANGE: float = 7.0
TRANSFUSE_REACH_PAD: float = 0.5
TRANSFUSE_SEARCH: float = 14.0
TRANSFUSE_STATIC_BELOW: float = 0.75
"""Spines/Spores are Transfused below this health fraction - well above the
0.4 ares' `UseTransfuse` uses for units, since a crawler is the thing we are
trying to keep standing."""
TRANSFUSE_UNIT_BELOW: float = 0.5
TRANSFUSE_RETARGET_S: float = 7.0
"""Transfuse heals over ~7s and does not stack - don't re-cast sooner."""
QUEEN_ENGAGE_RADIUS: float = 16.0

_STATIC_D: frozenset[UnitTypeId] = frozenset(
    {UnitTypeId.SPINECRAWLER, UnitTypeId.SPORECRAWLER}
)


def _core_townhalls(ctx: "BotContext") -> list:
    """Positions of the main + natural townhalls (every townhall if neither is
    left): the bases we hold. A 3rd base and beyond are given up."""
    anchors = (ctx.production_location, ctx.own_nat)
    all_townhalls = [th.position for th in ctx.bot.townhalls]
    core = [
        p
        for p in all_townhalls
        if any(cy_distance_to(p, anchor) <= CORE_RADIUS for anchor in anchors)
    ]
    return core or all_townhalls


def _core_spines(ctx: "BotContext", core: list) -> list:
    return [
        s.position
        for s in ctx.bot.structures(UnitTypeId.SPINECRAWLER).ready
        if any(cy_distance_to(s.position, p) <= THREAT_TOWNHALL_RADIUS for p in core)
    ]


def _threat(ctx: "BotContext") -> list | None:
    """Enemy units attacking into our core bases / Spines, or None."""
    bot = ctx.bot
    townhalls = _core_townhalls(ctx)
    spines = _core_spines(ctx, townhalls)
    if not townhalls and not spines:
        return None
    near = []
    for enemy in enemy_army(ctx):
        if not getattr(enemy, "can_attack", True) or getattr(enemy, "is_structure", False):
            continue
        if any(
            cy_distance_to(enemy.position, p) <= THREAT_TOWNHALL_RADIUS
            for p in townhalls
        ) or any(
            cy_distance_to(enemy.position, p) <= THREAT_SPINE_RADIUS for p in spines
        ):
            near.append(enemy)
    if not near:
        return None
    supply = sum(bot.calculate_supply_cost(e.type_id) for e in near)
    if len(near) < THREAT_MIN_UNITS and supply < THREAT_MIN_SUPPLY:
        return None
    return near


def _engaged(ctx: "BotContext", enemies: list) -> list:
    """The part of the enemy army actually fighting us: in Spine range of a
    core Spine, or (no Spines up) on top of a core townhall."""
    core = _core_townhalls(ctx)
    spines = _core_spines(ctx, core)
    if spines:
        return [
            e
            for e in enemies
            if any(cy_distance_to(e.position, p) <= SPINE_ENGAGE_RADIUS for p in spines)
        ]
    return [
        e
        for e in enemies
        if any(cy_distance_to(e.position, p) <= TOWNHALL_ENGAGE_RADIUS for p in core)
    ]


def _defense_supply(ctx: "BotContext", center: Point2) -> float:
    """What we already have fighting at `center`: army units + Queens + Spines."""
    bot = ctx.bot
    total = 0.0
    for unit in bot.units:
        if cy_distance_to(unit.position, center) > DEFENSE_RADIUS:
            continue
        if unit.type_id == UnitTypeId.QUEEN:
            total += QUEEN_DEFENSE_SUPPLY
        elif unit.type_id in ctx.build.army.types:
            total += bot.calculate_supply_cost(unit.type_id)
    for spine in bot.structures(UnitTypeId.SPINECRAWLER).ready:
        if cy_distance_to(spine.position, center) <= DEFENSE_RADIUS:
            total += SPINE_DEFENSE_SUPPLY
    return total


def _release(ctx: "BotContext") -> None:
    state = ctx.state
    for tag in list(state.pulled_drone_tags):
        ctx.mediator.assign_role(tag=tag, role=UnitRole.GATHERING)
    for tag, role in list(state.pulled_queen_roles.items()):
        ctx.mediator.assign_role(tag=tag, role=role)
    state.pulled_drone_tags.clear()
    state.pulled_queen_roles.clear()


def _pull_queens(ctx: "BotContext", queens: list) -> None:
    creep = {
        q.tag
        for q in ctx.mediator.get_units_from_role(
            role=UnitRole.QUEEN_CREEP, unit_type=UnitTypeId.QUEEN
        )
    }
    for queen in queens:
        if queen.tag in ctx.state.pulled_queen_roles:
            continue
        ctx.state.pulled_queen_roles[queen.tag] = (
            UnitRole.QUEEN_CREEP if queen.tag in creep else UnitRole.QUEEN_INJECT
        )
        ctx.mediator.assign_role(tag=queen.tag, role=UnitRole.BASE_DEFENDER)


def _pull_drones(ctx: "BotContext", center: Point2, enemy_supply: float) -> None:
    state = ctx.state
    want = max(DRONE_PULL_MIN, min(DRONE_PULL_MAX, math.ceil(enemy_supply)))
    missing = want - len(state.pulled_drone_tags)
    if missing <= 0:
        return
    gatherers = [
        d
        for d in ctx.mediator.get_units_from_role(
            role=UnitRole.GATHERING, unit_type=UnitTypeId.DRONE
        )
        if d.tag not in state.pulled_drone_tags
    ]
    gatherers.sort(key=lambda d: cy_distance_to(d.position, center))
    for drone in gatherers[:missing]:
        ctx.mediator.assign_role(tag=drone.tag, role=UnitRole.BASE_DEFENDER)
        # `assign_role` alone leaves the worker in ares' mineral assignment
        # (the same call the building manager makes when it takes a miner).
        ctx.mediator.remove_worker_from_mineral(worker_tag=drone.tag)
        state.pulled_drone_tags.add(drone.tag)
    if gatherers[:missing]:
        ctx.log(
            f"HOME_DEFENSE pulled {len(gatherers[:missing])} drones "
            f"(enemy supply {enemy_supply:.0f}, "
            f"{len(state.pulled_drone_tags)} on defense)"
        )


def _transfuse_targets(ctx: "BotContext", queens: list) -> list:
    """Damaged Spines/Spores first, then badly hurt Queens."""
    hurt_static = [
        s
        for s in ctx.bot.structures(_STATIC_D).ready
        if s.health_percentage < TRANSFUSE_STATIC_BELOW
    ]
    hurt_static.sort(key=lambda s: s.health_percentage)
    hurt_queens = [q for q in queens if q.health_percentage < TRANSFUSE_UNIT_BELOW]
    hurt_queens.sort(key=lambda q: q.health_percentage)
    return hurt_static + hurt_queens


def _queen_micro(
    ctx: "BotContext", queens: list, enemies: list, rally: Point2
) -> None:
    now = ctx.bot.time
    state = ctx.state
    grid = ctx.mediator.get_ground_grid
    targets = _transfuse_targets(ctx, queens)
    claimed: set[int] = set()
    for queen in queens:
        maneuver = CombatManeuver()
        target = None
        if queen.energy >= TRANSFUSE_ENERGY:
            for candidate in targets:
                if candidate.tag == queen.tag or candidate.tag in claimed:
                    continue
                if now - state.transfuse_at.get(candidate.tag, -1e9) < TRANSFUSE_RETARGET_S:
                    continue
                if cy_distance_to(queen.position, candidate.position) > TRANSFUSE_SEARCH:
                    continue
                target = candidate
                break
        if target is not None:
            claimed.add(target.tag)
            reach = (
                TRANSFUSE_RANGE
                + float(getattr(queen, "radius", 0.0) or 0.0)
                + float(getattr(target, "radius", 0.0) or 0.0)
                - TRANSFUSE_REACH_PAD
            )
            if cy_distance_to(queen.position, target.position) <= reach:
                state.transfuse_at[target.tag] = now
                maneuver.add(UseAbility(AbilityId.TRANSFUSION_TRANSFUSION, queen, target))
            else:
                maneuver.add(
                    PathUnitToTarget(unit=queen, grid=grid, target=target.position)
                )
            ctx.bot.register_behavior(maneuver)
            continue

        in_reach = [
            e
            for e in enemies
            if cy_distance_to(queen.position, e.position) <= QUEEN_ENGAGE_RADIUS
        ]
        if in_reach:
            maneuver.add(ShootTargetInRange(unit=queen, targets=in_reach))
            maneuver.add(
                AttackTarget(
                    unit=queen, target=cy_closest_to(queen.position, in_reach)
                )
            )
        elif cy_distance_to(queen.position, rally) > 6.0:
            maneuver.add(PathUnitToTarget(unit=queen, grid=grid, target=rally))
        ctx.bot.register_behavior(maneuver)


def _drone_micro(ctx: "BotContext", enemies: list) -> None:
    drones = [d for d in ctx.bot.workers if d.tag in ctx.state.pulled_drone_tags]
    for drone in drones:
        closest = cy_closest_to(drone.position, enemies)
        maneuver = CombatManeuver()
        maneuver.add(AttackTarget(unit=drone, target=closest))
        ctx.bot.register_behavior(maneuver)


def pull_defense() -> CombatRoutine:
    """Queens (Transfuse + fight) and, when outmatched, Drones defend home.

    See the module docstring. Does nothing until an enemy army (not a scout)
    is within reach of a townhall or Spine Crawler.
    """

    def routine(ctx: "BotContext") -> None:
        state = ctx.state
        now = ctx.bot.time
        queens = list(ctx.bot.units(UnitTypeId.QUEEN))
        alive = {q.tag for q in queens} | {d.tag for d in ctx.bot.workers}
        state.pulled_drone_tags &= alive
        for tag in [t for t in state.pulled_queen_roles if t not in alive]:
            del state.pulled_queen_roles[tag]

        enemies = _threat(ctx)
        if enemies is None:
            if (state.pulled_drone_tags or state.pulled_queen_roles) and (
                now >= state.home_threat_until
            ):
                _release(ctx)
            return

        state.home_threat_until = now + PULL_HOLD_S
        center = Point2(cy_center([e.position for e in enemies]))
        enemy_supply = sum(ctx.bot.calculate_supply_cost(e.type_id) for e in enemies)
        ctx.log_once(
            f"home_threat:{int(now // 20)}",
            f"HOME_DEFENSE {len(enemies)} enemy units ({enemy_supply:.0f} supply) "
            f"at ({center.x:.0f},{center.y:.0f}) vs our defense "
            f"{_defense_supply(ctx, center):.0f} supply - pulling Queens",
        )

        _pull_queens(ctx, queens)
        engaged = _engaged(ctx, enemies)
        if len(engaged) >= ENGAGE_MIN_UNITS:
            state.drone_engage_until = now + PULL_HOLD_S
        fighting = now < state.drone_engage_until
        outmatched = _defense_supply(ctx, center) < enemy_supply * DEFENSE_ENOUGH_RATIO
        if fighting and outmatched:
            _pull_drones(ctx, center, enemy_supply)
        elif state.pulled_drone_tags and not (fighting and outmatched):
            # The enemy is not in Spine range (or our defense caught up):
            # drones back to mining, Queens stay.
            for tag in list(state.pulled_drone_tags):
                ctx.mediator.assign_role(tag=tag, role=UnitRole.GATHERING)
            state.pulled_drone_tags.clear()

        spines = [s.position for s in ctx.bot.structures(UnitTypeId.SPINECRAWLER).ready]
        rally = (
            Point2(cy_center(spines))
            if spines
            else Point2(cy_closest_to(center, [th.position for th in ctx.bot.townhalls]))
        )
        _queen_micro(ctx, queens, enemies, rally)
        _drone_micro(ctx, enemies)

    return routine
