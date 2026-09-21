"""Creep spread: priority home tumors, then connect own bases from main."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ares.behaviors.combat.individual import KeepUnitSafe
from ares.consts import UnitRole
from cython_extensions import cy_distance_to_squared
from cython_extensions.general_utils import cy_has_creep
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.core.types import CombatRoutine

# Sticky next-tumor tile per Queen tag — stops highway spread from
# re-issuing move/cast to a new edge every frame (visual thrash).
# Kept through the cast until a tumor appears near the tile (or the tile
# goes stale); dropping it on cast was the hatch↔edge cancel loop.
_QUEEN_TUMOR_STICKY: dict[int, Point2] = {}
# Last tumor cast attempt (tag -> (spot, time)) — failed casts clear
# order_target so without this we re-issue every frame (out-of-range spam).
_QUEEN_TUMOR_CAST_AT: dict[int, tuple[Point2, float]] = {}
_QUEEN_TUMOR_CAST_COOLDOWN: float = 1.5
_TUMOR_CAST_AT: dict[int, tuple[Point2, float]] = {}
_TUMOR_CAST_COOLDOWN: float = 1.5

# A tumor within this of a priority spot counts as covering that spot.
_PRIORITY_TUMOR_RADIUS: float = 6.0
# Plant spots must stay this far from every live tumor (ares path + edge).
# Must stay under burrowed-tumor cast range (~10; ares search_radius=10.2).
# separation=12 forced every plant to 12–46 tiles → 4291 OOR casts/game
# (confirmed live Ultralove).
_TUMOR_MIN_SEPARATION: float = 8.0
_TUMOR_EDGE_SEARCH_RADIUS: float = 18.0
# Sticky plant succeeded once a tumor lands this close to the sticky tile.
_STICKY_DONE_RADIUS: float = 3.0
# Burrowed tumor Build Creep Tumor range.
_TUMOR_CAST_RANGE: float = 10.0
_TUMOR_CAST_RANGE_SQ: float = _TUMOR_CAST_RANGE**2
# Queen Build Creep Tumor: game-data cast_range is 0 — stand on the tile.
_QUEEN_TUMOR_CAST_RANGE_SQ: float = 9.0  # fallback for unit tests only

if TYPE_CHECKING:
    from bot.core.context import BotContext


def _queen_is_spreading(unit) -> bool:
    """True while a Queen is mid-cast on a Creep Tumor."""
    return unit.is_using_ability(AbilityId.BUILD_CREEPTUMOR) or unit.is_using_ability(
        AbilityId.BUILD_CREEPTUMOR_QUEEN
    )


def _queen_already_ordered_to(unit, spot: Point2) -> bool:
    """True if the Queen already has a move/cast order near `spot`."""
    target = unit.order_target
    if target is None:
        return False
    if isinstance(target, Point2):
        return cy_distance_to_squared(target, spot) < 4.0
    # Only trust objects that look like world points (not MagicMock / unit tags).
    pos = getattr(target, "position", None)
    if isinstance(pos, Point2):
        return cy_distance_to_squared(pos, spot) < 4.0
    if isinstance(pos, (tuple, list)) and len(pos) >= 2:
        try:
            return cy_distance_to_squared(Point2((float(pos[0]), float(pos[1]))), spot) < 4.0
        except (TypeError, ValueError):
            return False
    return False


def _own_bases_highway(ctx: "BotContext") -> list[Point2]:
    """Main, then every other ready townhall ordered by distance from main.

    Fan out from home — connect the nearest expansion first, then the next
    nearest, etc. Not a nearest-neighbor walk along the previous base, and
    not a path toward the enemy.
    """
    bot = ctx.bot
    main = bot.start_location
    others: list[Point2] = []
    for th in bot.townhalls.ready:
        pos = th.position
        if cy_distance_to_squared(pos, main) < 25.0:
            continue
        others.append(pos)
    others.sort(key=lambda p: cy_distance_to_squared(p, main))
    return [main, *others]


def _creep_tumor_positions(ctx: "BotContext") -> list[Point2]:
    """Positions of every live Creep Tumor (mid-cast or burrowed)."""
    tumors = ctx.bot.structures(UnitTypeId.CREEPTUMORQUEEN) | ctx.bot.structures(
        UnitTypeId.CREEPTUMORBURROWED
    )
    return [t.position for t in tumors]


def _nearest_tumor_distance_sq(ctx: "BotContext", pos: Point2) -> float:
    """Squared distance from `pos` to the nearest live tumor (inf if none)."""
    best = float("inf")
    for tumor_pos in _creep_tumor_positions(ctx):
        dist_sq = cy_distance_to_squared(pos, tumor_pos)
        if dist_sq < best:
            best = dist_sq
    return best


def _pick_furthest_from_tumors(
    ctx: "BotContext", candidates: list[Point2]
) -> Point2 | None:
    """Among plant candidates, choose the one furthest from existing tumors."""
    if not candidates:
        return None
    return max(candidates, key=lambda p: _nearest_tumor_distance_sq(ctx, p))


def _tumor_count_at(
    ctx: "BotContext", location: Point2, radius: float = _PRIORITY_TUMOR_RADIUS
) -> int:
    """How many tumors sit within `radius` of `location`."""
    radius_sq = radius**2
    return sum(
        1
        for pos in _creep_tumor_positions(ctx)
        if cy_distance_to_squared(pos, location) <= radius_sq
    )


def _priority_creep_locations(ctx: "BotContext") -> list[Point2]:
    """Home tumors we want before highway fan-out, in plant order.

    1. In front of the natural (same offset as the army rally)
    2. Top of our main ramp
    3. Main-base Nydus placement spot
    """
    points: list[Point2] = []

    nat = ctx.own_nat
    enemy_starts = ctx.bot.enemy_start_locations
    if nat is not None and enemy_starts:
        offset = float(getattr(ctx.build.combat, "rally_offset", 8.0) or 8.0)
        points.append(nat.towards(enemy_starts[0], offset))

    ramp = getattr(ctx.bot, "main_base_ramp", None)
    if ramp is not None:
        top = getattr(ramp, "top_center", None)
        if top is not None:
            points.append(Point2(top))

    nydus = getattr(ctx.mediator, "get_primary_nydus_own_main", None)
    if nydus is not None:
        points.append(Point2(nydus))
    return points


def _creep_spread_target(ctx: "BotContext", from_pos: Point2) -> Point2:
    """Next plant goal: uncovered priority spots, else own-base highway.

    Priority locations need at least one tumor within
    `_PRIORITY_TUMOR_RADIUS`. Once those are covered, aim at the next own
    base that still lacks creep (nearest-to-main first). Once every owned
    base tile is on creep, keep aiming at the furthest own base so tumors
    thicken the network — never the enemy natural/main.
    """
    del from_pos  # reserved for callers; target is global priority/fan order
    for location in _priority_creep_locations(ctx):
        if _tumor_count_at(ctx, location) < 1:
            return location

    bases = _own_bases_highway(ctx)
    creep_grid = ctx.mediator.get_creep_grid
    for base in bases[1:]:
        if not cy_has_creep(creep_grid, base):
            return base
    return bases[-1]


def _spot_has_creep(ctx: "BotContext", spot: Point2) -> bool:
    """True when `spot` is on live creep (required to plant a tumor)."""
    return cy_has_creep(ctx.mediator.get_creep_grid, spot)


def _spot_visible(ctx: "BotContext", spot: Point2) -> bool:
    """True when we have vision of `spot` (required to plant — CantSeeBuildLocation)."""
    try:
        return bool(ctx.bot.is_visible(spot))
    except Exception:
        return True


def _place_tumor_toward(
    ctx: "BotContext", unit, target: Point2, *, queen: bool
) -> bool:
    """Cast or move toward the next highway tumor spot. Returns True if acted.

    Plant tiles must clear `_TUMOR_MIN_SEPARATION` from every live tumor; when
    several candidates exist we keep the one furthest from existing tumors so
    creep leapfrogs instead of packing dense.

    Queens keep a sticky plant tile through the cast. Dropping that sticky the
    frame the cast was issued made the next frame re-pick a different
    "furthest" edge and cancel the morph — confirmed live as a walk↔cast loop.

    Never cast onto a tile without creep, and never cast from beyond the
    Queen's 3-tile tumor range — both produce a looping "out of range" error.
    """
    mediator = ctx.mediator
    ability = (
        AbilityId.BUILD_CREEPTUMOR_QUEEN
        if queen
        else AbilityId.BUILD_CREEPTUMOR_TUMOR
    )
    cast_ability = (
        AbilityId.BUILD_CREEPTUMOR_QUEEN if queen else AbilityId.BUILD_CREEPTUMOR
    )
    sep_sq = _TUMOR_MIN_SEPARATION**2

    if queen:
        grid = mediator.get_ground_grid
        if _queen_is_spreading(unit):
            if KeepUnitSafe(unit, grid).execute(ctx.bot, ctx.bot.config, mediator):
                return True
            return True

        sticky = _QUEEN_TUMOR_STICKY.get(unit.tag)
        # Prior plant landed — free the latch for the next tumor.
        if sticky is not None and _tumor_count_at(
            ctx, sticky, radius=_STICKY_DONE_RADIUS
        ) >= 1:
            _QUEEN_TUMOR_STICKY.pop(unit.tag, None)
            sticky = None
        # Stale sticky (creep retreated / bad pick) — drop before it loops
        # failed casts.
        if sticky is not None and not _spot_has_creep(ctx, sticky):
            _QUEEN_TUMOR_STICKY.pop(unit.tag, None)
            sticky = None

        ability_available = ability in unit.abilities
        if not ability_available:
            # Pre-walk toward the sticky (or a path toward the goal) without
            # re-picking a new furthest edge every energy-tick frame.
            dest = sticky
            if dest is None or not _spot_has_creep(ctx, dest):
                dest = mediator.get_next_tumor_on_path(
                    grid=grid,
                    from_pos=unit.position,
                    to_pos=target,
                    find_alternative=True,
                    min_separation=_TUMOR_MIN_SEPARATION,
                )
                if dest is not None:
                    dest = Point2(dest)
                    if _spot_has_creep(ctx, dest):
                        _QUEEN_TUMOR_STICKY[unit.tag] = dest
                    else:
                        dest = None
            if (
                dest is not None
                and cy_distance_to_squared(unit.position, dest)
                > _QUEEN_TUMOR_CAST_RANGE_SQ
                and not _queen_already_ordered_to(unit, dest)
            ):
                unit.move(dest)
            return True
    else:
        # Cast the moment the tumor has energy — do not wait on ares'
        # coverage-throttled `should_calculate_tumor_spread` (that deferred
        # plants for many frames once map creep was non-trivial).
        if ability not in unit.abilities:
            return False
        grid = mediator.get_ground_grid
        sticky = None

    if queen:
        sticky = _QUEEN_TUMOR_STICKY.get(unit.tag)
    if (
        sticky is not None
        and _spot_has_creep(ctx, sticky)
        and _nearest_tumor_distance_sq(ctx, sticky) >= sep_sq
    ):
        spot = sticky
    else:
        if queen:
            _QUEEN_TUMOR_STICKY.pop(unit.tag, None)
        candidates: list[Point2] = []
        path_spot = mediator.get_next_tumor_on_path(
            grid=grid,
            from_pos=unit.position,
            to_pos=target,
            find_alternative=True,
            min_separation=_TUMOR_MIN_SEPARATION,
        )
        if path_spot is not None:
            candidates.append(Point2(path_spot))
        # Queens may search farther and walk in; burrowed tumors must plant
        # in cast range (~10) — ares uses search_radius=10.2.
        edge_radius = (
            _TUMOR_EDGE_SEARCH_RADIUS if queen else _TUMOR_CAST_RANGE
        )
        edge = mediator.find_nearby_creep_edge_position(
            position=unit.position,
            search_radius=edge_radius,
            # Furthest edge from the caster among spread-valid tiles —
            # expands the frontier instead of packing near the unit.
            closest_valid=False,
            spread_dist=_TUMOR_MIN_SEPARATION,
            unit_tag=unit.tag if queen else None,
        )
        if edge is not None:
            candidates.append(Point2(edge))
        # Only plantable tiles (on creep + far enough from live tumors).
        # Visibility is required at cast time; queens may still walk onto a
        # fogged sticky so we do not filter it out of the candidate pool.
        on_creep = [c for c in candidates if _spot_has_creep(ctx, c)]
        spaced = [
            c
            for c in on_creep
            if _nearest_tumor_distance_sq(ctx, c) >= sep_sq
        ]
        pool = spaced or on_creep
        if not queen:
            # Burrowed tumors cannot walk — only visible, in-range spots.
            in_range = [
                c
                for c in pool
                if cy_distance_to_squared(unit.position, c) <= _TUMOR_CAST_RANGE_SQ
                and _spot_visible(ctx, c)
            ]
            pool = in_range or []
        spot = _pick_furthest_from_tumors(ctx, pool)
        if spot is None:
            return False
        if queen:
            _QUEEN_TUMOR_STICKY[unit.tag] = spot

    if not _spot_has_creep(ctx, spot):
        if queen:
            _QUEEN_TUMOR_STICKY.pop(unit.tag, None)
        return False

    cast_range_sq = _QUEEN_TUMOR_CAST_RANGE_SQ if queen else _TUMOR_CAST_RANGE_SQ
    dist_sq = cy_distance_to_squared(unit.position, spot)
    in_cast_range = dist_sq <= cast_range_sq
    tumor_cast_range = None
    if queen:
        # BUILD_CREEPTUMOR_QUEEN reports cast_range 0.0 in game data
        # (confirmed live). in_ability_cast_range asserts on that and we
        # used to fall back to center-dist 3 → OOR spam at dist ~2.9.
        # With range 0 the queen must stand on/near the plant tile.
        try:
            tumor_cast_range = float(
                ctx.bot.game_data.abilities[
                    AbilityId.BUILD_CREEPTUMOR_QUEEN.value
                ]._proto.cast_range
            )
        except Exception:
            tumor_cast_range = None
        if tumor_cast_range is not None and tumor_cast_range > 0.05:
            try:
                result = unit.in_ability_cast_range(
                    AbilityId.BUILD_CREEPTUMOR_QUEEN, spot
                )
                if isinstance(result, bool):
                    in_cast_range = result
            except Exception:
                pass
        else:
            # Range 0 / unknown: require nearly on-tile (queen radius).
            q_r = float(getattr(unit, "radius", 0.875) or 0.875)
            in_cast_range = dist_sq <= (q_r + 0.25) ** 2
    else:
        # Burrowed tumor: game-data cast_range is 0; real range ~10
        # (ares find_nearby_creep_edge search_radius=10.2). Confirmed live:
        # 4291 casts at dist 9.8–46, all in_cast_range=False.
        tumor_cast_range = _TUMOR_CAST_RANGE
        in_cast_range = dist_sq <= _TUMOR_CAST_RANGE_SQ
        if not in_cast_range or not _spot_visible(ctx, spot):
            return False
    if queen and not in_cast_range:
        if not _queen_already_ordered_to(unit, spot):
            unit.move(spot)
        return True
    # Need vision of the plant tile — CantSeeBuildLocation otherwise
    # (confirmed live after OOR fix: tumors spam at fogged creep edge).
    if not _spot_visible(ctx, spot):
        if queen and not _queen_already_ordered_to(unit, spot):
            unit.move(spot)
            return True
        return False
    # In cast range: cast now. Do NOT wait for a pending move onto the
    # tile — confirmed live: queen walked in from dist_sq 6.8→0 with
    # order_target=spot, skipped cast every frame, then planted on an
    # invalid tile (out-of-range spam).
    if queen and _queen_is_spreading(unit):
        return True
    if queen:
        import time

        prev = _QUEEN_TUMOR_CAST_AT.get(unit.tag)
        if (
            prev is not None
            and cy_distance_to_squared(prev[0], spot) < 1.0
            and (time.time() - prev[1]) < _QUEEN_TUMOR_CAST_COOLDOWN
        ):
            return True
        _QUEEN_TUMOR_CAST_AT[unit.tag] = (spot, time.time())
    else:
        import time

        prev = _TUMOR_CAST_AT.get(unit.tag)
        if (
            prev is not None
            and cy_distance_to_squared(prev[0], spot) < 1.0
            and (time.time() - prev[1]) < _TUMOR_CAST_COOLDOWN
        ):
            return True
        _TUMOR_CAST_AT[unit.tag] = (spot, time.time())
    unit(cast_ability, spot)
    # Keep queen sticky until a tumor appears near it — do not pop on cast.
    return True

# How many Queens beyond one-per-base should stay on `QUEEN_CREEP` for
# defense + highway creep (matches Macro Zerg `train_queens(..., extra=2)`).
_DESIRED_CREEP_QUEENS: int = 2


def spread_creep() -> CombatRoutine:
    """Dedicate `_DESIRED_CREEP_QUEENS` Queens (beyond one per base) to
    defense + creep.

    Creep Queens plant priority home spots first (natural front → ramp
    top → main Nydus), then the own-base highway via
    `_creep_spread_target`. Skips a Queen claimed by the natural opening
    tumor (`natural_queen_tag`) while that claim is active. Promotes spare
    injectors until the creep pool reaches `_DESIRED_CREEP_QUEENS`, leaving
    at least one injector per base.
    """

    def routine(ctx: "BotContext") -> None:
        creep_queens = list(
            ctx.mediator.get_units_from_role(
                role=UnitRole.QUEEN_CREEP, unit_type=UnitTypeId.QUEEN
            )
        )
        if len(creep_queens) < _DESIRED_CREEP_QUEENS:
            injectors = list(
                ctx.mediator.get_units_from_role(
                    role=UnitRole.QUEEN_INJECT, unit_type=UnitTypeId.QUEEN
                )
            )
            if len(injectors) > ctx.base_count:
                newest = max(injectors, key=lambda q: q.tag)
                ctx.mediator.assign_role(tag=newest.tag, role=UnitRole.QUEEN_CREEP)
                creep_queens.append(newest)

        if not creep_queens:
            return

        reserved: set[int] = set()
        if (
            ctx.state.natural_queen_tag is not None
            and not ctx.state.natural_queen_tumor_done
        ):
            reserved.add(ctx.state.natural_queen_tag)

        for queen in creep_queens:
            if queen.tag in reserved:
                continue
            target = _creep_spread_target(ctx, queen.position)
            _place_tumor_toward(ctx, queen, target, queen=True)

    return routine


def spread_tumors() -> CombatRoutine:
    """Burrowed tumors extend creep: priority spots, then own-base highway.

    Each ready tumor plants as soon as its ability is available, pathing
    via `_creep_spread_target` (no enemy push).
    """

    def routine(ctx: "BotContext") -> None:
        tumors = ctx.mediator.get_own_structures_dict[UnitTypeId.CREEPTUMORBURROWED]
        if not tumors:
            return

        for tumor in tumors:
            target = _creep_spread_target(ctx, tumor.position)
            _place_tumor_toward(ctx, tumor, target, queen=False)

    return routine
