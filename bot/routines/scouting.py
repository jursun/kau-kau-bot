"""Scouting routines."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ares.behaviors.combat.individual import PathUnitToTarget
from ares.consts import UnitRole, UnitTreeQueryType
from cython_extensions import (
    cy_closest_to,
    cy_distance_to,
    cy_distance_to_squared,
    cy_towards,
)
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.consts import ZERGLING_SCOUT_ROLE
from bot.core.types import CombatRoutine

if TYPE_CHECKING:
    from bot.core.context import BotContext

TargetFn = Callable[["BotContext"], Point2]

# Opening ling scout layout (exactly 4 permanent scouts):
# 1) in front of the enemy natural
# 2) mid-map
# 3) watchtower 1, else outside the enemy 3rd
# 4) watchtower 2, else outside the enemy 4th
OPENING_LING_SCOUT_CAP: int = 4

# Scout micro: peel when any non-structure enemy is this close.
_SCOUT_THREAT_RANGE: float = 14.0
_SCOUT_KITE_STEP: float = 14.0
_SCOUT_AT_WATCH_SQ: float = 9.0
# Never path closer than this to an enemy expansion pad / main.
_ENEMY_BASE_LEASH: float = 18.0
_OUTSIDE_EXPANSION_DIST: float = 16.0
_ENEMY_NAT_FRONT_DIST: float = 16.0
_DEDUPE_SEP: float = 6.0
# Aliases kept for older tests.
_ENEMY_MAIN_LEASH: float = 26.0
_NATURAL_FRONT_DIST: float = 14.0


def enemy_natural_overlook(ctx: "BotContext") -> Point2:
    """Ares' safe high-ground vision spot near the enemy natural."""
    return ctx.mediator.get_ol_spot_near_enemy_nat


def air_scout(
    unit_type: UnitTypeId, target: TargetFn = enemy_natural_overlook
) -> CombatRoutine:
    """Park SCOUTING fliers on a vision spot.

    No danger-avoidance here on purpose: this is only ever the opening
    scouting Overlord (see `core/roles.py`'s `SCOUT_TYPES`), and it needs to
    reach its vision spot and stay there — a `KeepUnitSafe` check used to
    make it retreat the moment anything came near, which is exactly the
    threats it exists to keep watching. Combat units that DO need to dodge
    danger while moving (the escort Overseers, for instance) use
    `KeepUnitSafe`/`MoveToSafeTarget` in `routines/combat.py` instead.
    """

    def routine(ctx: "BotContext") -> None:
        scouts = ctx.mediator.get_units_from_role(
            role=UnitRole.SCOUTING, unit_type=unit_type
        )
        if not scouts:
            return
        grid = ctx.mediator.get_air_grid
        destination = target(ctx)
        for scout in scouts:
            ctx.bot.register_behavior(
                PathUnitToTarget(
                    unit=scout, grid=grid, target=destination, success_at_distance=2.0
                )
            )

    return routine


def _tower_reachable(
    ctx: "BotContext", home: Point2, tower: Point2, grid
) -> bool:
    """True when ground units can path from `home` to the tower (or beside it).

    Ultralove (and similar) block some Xel'Naga towers behind destructible
    rocks early — sending lings there just thrashes against the rocks.
    """
    offsets = (
        (0.0, 0.0),
        (1.0, 0.0),
        (-1.0, 0.0),
        (0.0, 1.0),
        (0.0, -1.0),
        (2.0, 0.0),
        (-2.0, 0.0),
        (0.0, 2.0),
        (0.0, -2.0),
    )
    for dx, dy in offsets:
        stand = Point2((tower.x + dx, tower.y + dy))
        path = ctx.mediator.find_raw_path(
            start=home, target=stand, grid=grid, sensitivity=2
        )
        if path:
            return True
    return False


def _observation_tower_points(ctx: "BotContext") -> list[Point2]:
    """Reachable Xel'Naga / observation tower positions (stable order)."""
    towers = getattr(ctx.bot, "watchtowers", None) or ()
    home = ctx.own_nat
    if home is None:
        home = ctx.production_location
    grid = ctx.mediator.get_ground_grid
    points: list[Point2] = []
    for t in towers:
        if not getattr(t, "position", None):
            continue
        pos = Point2(t.position)
        if home is not None and grid is not None:
            if not _tower_reachable(ctx, home, pos, grid):
                continue
        points.append(pos)
    points.sort(key=lambda p: (p.x, p.y))
    return points


def _home_and_enemy_main(ctx: "BotContext") -> tuple[Point2 | None, Point2 | None]:
    home = ctx.own_nat
    if home is None:
        home = ctx.production_location
    starts = getattr(ctx.bot, "enemy_start_locations", None) or ()
    enemy_main = Point2(starts[0]) if starts else None
    if home is None or enemy_main is None:
        return None, None
    return Point2(home), enemy_main


def _safe_point(ctx: "BotContext", name: str) -> Point2 | None:
    """Best-effort mediator expansion point (nat / third / fourth)."""
    try:
        value = getattr(ctx.mediator, name, None)
    except Exception:
        return None
    if value is None:
        return None
    try:
        return Point2(value)
    except Exception:
        return None


def _outside_expansion(base: Point2, toward: Point2, dist: float) -> Point2:
    """Park outside `base` on the side facing `toward` (not inside the pad)."""
    span = base.distance_to(toward)
    if span < 1.0:
        return Point2(base)
    return Point2(base.towards(toward, min(dist, max(span * 0.25, dist))))


def _leash_from_base(park: Point2, base: Point2, toward: Point2) -> Point2:
    """Push `park` out if it sits inside the expansion leash."""
    if park.distance_to(base) >= _ENEMY_BASE_LEASH:
        return park
    return _outside_expansion(base, toward, _ENEMY_BASE_LEASH)


def _mid_map_point(ctx: "BotContext") -> Point2 | None:
    home, enemy_main = _home_and_enemy_main(ctx)
    center = getattr(getattr(ctx.bot, "game_info", None), "map_center", None)
    if center is not None:
        return Point2(center)
    if home is None or enemy_main is None:
        return None
    dist = home.distance_to(enemy_main)
    if dist < 1.0:
        return None
    return Point2(home.towards(enemy_main, dist * 0.5))


def _enemy_nat_front(ctx: "BotContext") -> Point2 | None:
    """In front of the enemy natural (toward us) — moveout watch."""
    home, enemy_main = _home_and_enemy_main(ctx)
    if home is None:
        return None
    enemy_nat = _safe_point(ctx, "get_enemy_nat")
    if enemy_nat is None:
        enemy_nat = enemy_main
    if enemy_nat is None:
        return None
    park = _outside_expansion(enemy_nat, home, _ENEMY_NAT_FRONT_DIST)
    return _leash_from_base(park, enemy_nat, home)


def _dedupe_points(points: list[Point2], min_sep: float = _DEDUPE_SEP) -> list[Point2]:
    kept: list[Point2] = []
    for p in points:
        if any(p.distance_to(k) < min_sep for k in kept):
            continue
        kept.append(p)
    return kept


def opening_zergling_scout_cap(ctx: "BotContext") -> int:
    """Always 4 dedicated opening scouts (scout + kite only)."""
    del ctx
    return OPENING_LING_SCOUT_CAP


def _opening_ling_watch_points(ctx: "BotContext") -> list[Point2]:
    """Exactly four parks: enemy-nat front, mid, tower/3rd, tower/4th."""
    home, enemy_main = _home_and_enemy_main(ctx)
    if home is None or enemy_main is None:
        return []

    slots: list[Point2 | None] = [None, None, None, None]

    # 1) In front of the enemy natural.
    slots[0] = _enemy_nat_front(ctx)

    # 2) Mid-map.
    slots[1] = _mid_map_point(ctx)

    towers = _observation_tower_points(ctx)
    enemy_third = _safe_point(ctx, "get_enemy_third")
    enemy_fourth = _safe_point(ctx, "get_enemy_fourth")

    # 3) Watchtower 1, else outside enemy 3rd.
    if towers:
        slots[2] = towers[0]
    elif enemy_third is not None:
        slots[2] = _leash_from_base(
            _outside_expansion(enemy_third, home, _OUTSIDE_EXPANSION_DIST),
            enemy_third,
            home,
        )

    # 4) Watchtower 2, else outside enemy 4th.
    if len(towers) >= 2:
        slots[3] = towers[1]
    elif enemy_fourth is not None:
        slots[3] = _leash_from_base(
            _outside_expansion(enemy_fourth, home, _OUTSIDE_EXPANSION_DIST),
            enemy_fourth,
            home,
        )
    elif enemy_third is not None and slots[2] is not None:
        # No 4th / 2nd tower: offset beside the 3rd park.
        p = slots[2]
        slots[3] = Point2((p.x + 8.0, p.y - 8.0))

    # Fill any missing slot from mid / nat-front / path so we still seat 4.
    fillers = [
        slots[1],
        slots[0],
        Point2(home.towards(enemy_main, home.distance_to(enemy_main) * 0.35)),
        Point2(home.towards(enemy_main, home.distance_to(enemy_main) * 0.55)),
    ]
    points: list[Point2] = []
    for slot in slots:
        if slot is not None:
            points.append(slot)
    for fill in fillers:
        if len(points) >= OPENING_LING_SCOUT_CAP:
            break
        if fill is None:
            continue
        points = _dedupe_points(points + [fill])
    return points[:OPENING_LING_SCOUT_CAP]


def _dest_is_watch(dest: Point2, watches: list[Point2]) -> bool:
    return any(cy_distance_to_squared(dest, t) <= 1.0 for t in watches)


def _scout_threats_near(ctx: "BotContext", point: Point2, distance: float) -> list:
    """Enemy army/workers near `point` — structures do not count as threats."""
    raw = ctx.mediator.get_units_in_range(
        start_points=[point],
        distances=distance,
        query_tree=UnitTreeQueryType.EnemyGround,
    )[0]
    return [u for u in raw if not getattr(u, "is_structure", False)]


def _drive_ling_scout(ctx: "BotContext", scout, watch: Point2, grid) -> None:
    """Park on `watch`; kite toward home when threatened — never dive bases.

    Scouts stay on `ZERGLING_SCOUT_ROLE` forever; they only scout and kite.
    """
    home = ctx.own_nat
    if home is None:
        home = ctx.production_location
    _, enemy_main = _home_and_enemy_main(ctx)

    # Hard leash: too close to enemy main → peel home.
    inside_leash = (
        enemy_main is not None
        and cy_distance_to(scout.position, enemy_main) < _ENEMY_MAIN_LEASH
    )
    threats = _scout_threats_near(ctx, scout.position, _SCOUT_THREAT_RANGE)
    if inside_leash or threats:
        if threats:
            threat = cy_closest_to(scout.position, threats)
            if home is not None:
                current = cy_distance_to(threat.position, scout.position)
                desired = max(_SCOUT_KITE_STEP, current + 6.0)
                retreat = Point2(cy_towards(threat.position, home, desired))
            else:
                retreat = Point2(
                    cy_towards(threat.position, scout.position, _SCOUT_KITE_STEP)
                )
        else:
            retreat = Point2(home) if home is not None else Point2(scout.position)
        ctx.bot.register_behavior(
            PathUnitToTarget(
                unit=scout,
                grid=grid,
                target=retreat,
                success_at_distance=2.0,
            )
        )
        return

    target = watch
    if (
        enemy_main is not None
        and cy_distance_to(target, enemy_main) < _ENEMY_BASE_LEASH
    ):
        target = Point2(
            enemy_main.towards(home or scout.position, _ENEMY_BASE_LEASH)
        )

    if cy_distance_to_squared(scout.position, target) <= _SCOUT_AT_WATCH_SQ:
        return

    if _scout_threats_near(ctx, target, _SCOUT_THREAT_RANGE):
        return

    ctx.bot.register_behavior(
        PathUnitToTarget(
            unit=scout,
            grid=grid,
            target=target,
            success_at_distance=2.0,
        )
    )


def scout_with_zerglings() -> CombatRoutine:
    """Drive the 4 permanent opening Zergling scouts.

    Parks: enemy-nat front, mid-map, tower-or-enemy-3rd, tower-or-enemy-4th.
    Scouts only scout and kite — they are never reassigned to defense or
    the attack wave. Excess scouts (should be rare) drop to `DEFENDING`
    so they can stream with the army, not home garrison.
    """

    def routine(ctx: "BotContext") -> None:
        scouts = list(
            ctx.mediator.get_units_from_role(
                role=ZERGLING_SCOUT_ROLE, unit_type=UnitTypeId.ZERGLING
            )
        )
        alive = {u.tag for u in scouts}
        ctx.state.zergling_scout_destinations = {
            tag: pt
            for tag, pt in ctx.state.zergling_scout_destinations.items()
            if tag in alive
        }
        if not scouts:
            return

        watches = _opening_ling_watch_points(ctx)
        if not watches:
            return

        ordered = sorted(scouts, key=lambda u: u.tag)
        keepers = ordered[: len(watches)]
        dests = ctx.state.zergling_scout_destinations
        for scout in ordered[len(watches) :]:
            # Extra beyond the 4 parks → army stream, never home defense.
            ctx.mediator.assign_role(tag=scout.tag, role=UnitRole.DEFENDING)
            dests.pop(scout.tag, None)
            ctx.log_once(
                f"ling_scout_extra_{scout.tag}",
                f"LING_SCOUT {scout.tag} -> DEFENDING (extra vs 4 watches)",
            )

        grid = ctx.mediator.get_ground_grid
        for i, scout in enumerate(keepers):
            target = dests.get(scout.tag)
            if target is None or not _dest_is_watch(target, watches):
                target = watches[i]
                dests[scout.tag] = target
            _drive_ling_scout(ctx, scout, target, grid)

    return routine
