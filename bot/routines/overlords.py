"""Non-SCOUTING Overlord parking: early HG vision, mid-late Spore cover.

The opening SCOUTING Overlord stays on `routines.scouting.air_scout` (enemy-nat
overlook via `PathUnitToTarget`, no KeepUnitSafe). This routine only drives the
remaining Overlords.

Overseer scout KeepUnitSafe / bite-dodge fixes are deliberately out of scope —
see `routines.overseers` for Overseer roles. Do not peel parked Overlords with
KeepUnitSafe here; vision watch-first matches the opening scout intent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ares.behaviors.combat.individual import PathUnitToTarget
from ares.consts import UnitRole
from cython_extensions import cy_distance_to_squared
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.core.types import CombatRoutine

if TYPE_CHECKING:
    from sc2.unit import Unit

    from bot.core.context import BotContext

# Match air_scout PathUnitToTarget success radius.
_ARRIVE: float = 2.0
_ARRIVE_SQ: float = _ARRIVE**2
# Snap sticky dests onto the same ol-spot / spore slot.
_MATCH_SQ: float = 4.0**2

_PHASE_VISION = "vision"
_PHASE_SPORE = "spore"


def _scouting_overlord_tags(ctx: "BotContext") -> set[int]:
    tags = set(ctx.state.scout_tags)
    scouts = ctx.mediator.get_units_from_role(
        role=UnitRole.SCOUTING, unit_type=UnitTypeId.OVERLORD
    )
    for scout in scouts or ():
        tags.add(scout.tag)
    return tags


def _parkable_overlords(ctx: "BotContext") -> list["Unit"]:
    excluded = _scouting_overlord_tags(ctx)
    return [
        u
        for u in ctx.bot.units(UnitTypeId.OVERLORD)
        if u.is_ready and u.tag not in excluded
    ]


def _ready_spores(ctx: "BotContext") -> list["Unit"]:
    return list(ctx.bot.structures(UnitTypeId.SPORECRAWLER).ready)


def _near_any(point: Point2, candidates: list[Point2]) -> Point2 | None:
    for cand in candidates:
        if cy_distance_to_squared(point, cand) <= _MATCH_SQ:
            return cand
    return None


def _our_side_ol_spots(ctx: "BotContext") -> list[Point2]:
    """HG vision spots preferring our half of the map (not random scatter).

    Reads `get_ol_spots` as a copy so we never mutate Ares' cached list
    (claiming via `get_ol_spot_near_enemy_nat` pops the enemy-nat spot for
    the opening scout — we must not steal or reshuffle that).
    """
    raw = list(ctx.mediator.get_ol_spots or ())
    if not raw:
        return []
    home = ctx.production_location
    enemy_starts = list(getattr(ctx.bot, "enemy_start_locations", None) or ())
    enemy = enemy_starts[0] if enemy_starts else None

    scored: list[tuple[int, float, Point2]] = []
    for spot in raw:
        pt = Point2(spot)
        d_home = cy_distance_to_squared(pt, home)
        side = 0
        if enemy is not None:
            side = 0 if d_home <= cy_distance_to_squared(pt, enemy) else 1
        scored.append((side, d_home, pt))
    scored.sort(key=lambda row: (row[0], row[1]))
    return [spot for _, _, spot in scored]


def _spore_positions(spores: list["Unit"]) -> list[Point2]:
    return [Point2(s.position) for s in spores]


def _clear_dead_and_phase(
    ctx: "BotContext", alive_tags: set[int], phase: str
) -> dict[int, Point2]:
    dests = ctx.state.overlord_park_targets
    for tag in list(dests):
        if tag not in alive_tags:
            del dests[tag]

    if ctx.state.overlord_park_phase != phase:
        if ctx.state.overlord_park_phase is not None or phase:
            ctx.log(f"OVERLORD park phase -> {phase}")
        ctx.state.overlord_park_phase = phase
        dests.clear()
    return dests


def _assign_targets(
    overlords: list["Unit"],
    dests: dict[int, Point2],
    candidates: list[Point2],
    *,
    allow_stack: bool,
) -> None:
    """Sticky tag to Point2; fill uncovered candidates 1:1, then stack."""
    if not candidates:
        return

    claimed: list[Point2] = []
    for unit in overlords:
        current = dests.get(unit.tag)
        matched = _near_any(current, candidates) if current is not None else None
        if matched is not None:
            dests[unit.tag] = matched
            claimed.append(matched)

    uncovered = [c for c in candidates if _near_any(c, claimed) is None]
    for unit in overlords:
        if unit.tag in dests:
            continue
        if uncovered:
            pick = min(
                uncovered,
                key=lambda c: cy_distance_to_squared(unit.position, c),
            )
            uncovered.remove(pick)
            dests[unit.tag] = pick
            claimed.append(pick)
        else:
            # Stack / HG overflow: nearest candidate beats idling at hatch.
            _ = allow_stack
            pick = min(
                candidates,
                key=lambda c: cy_distance_to_squared(unit.position, c),
            )
            dests[unit.tag] = pick


def _path_to_parks(
    ctx: "BotContext", overlords: list["Unit"], dests: dict[int, Point2]
) -> None:
    grid = ctx.mediator.get_air_grid
    for unit in overlords:
        target = dests.get(unit.tag)
        if target is None:
            continue
        if cy_distance_to_squared(unit.position, target) <= _ARRIVE_SQ:
            continue
        ctx.bot.register_behavior(
            PathUnitToTarget(
                unit=unit,
                grid=grid,
                target=target,
                success_at_distance=_ARRIVE,
            )
        )


def manage_overlord_positions() -> CombatRoutine:
    """Park non-SCOUTING Overlords on HG vision early, then over Spores mid-late.

    Early (no ready Spore Crawler): path to distinct safe high-ground spots from
    `get_ol_spots`, preferring our side of the map. Mid-late (>=1 ready Spore):
    redistribute over Spore Crawlers at our bases (1:1, extras stack nearest).

    No KeepUnitSafe — peeling undoes the park / vision watch. Overseer scout
    KeepUnitSafe is out of scope (see module docstring).
    """

    def routine(ctx: "BotContext") -> None:
        overlords = _parkable_overlords(ctx)
        alive = {u.tag for u in overlords}
        spores = _ready_spores(ctx)

        if spores:
            phase = _PHASE_SPORE
            candidates = _spore_positions(spores)
        else:
            phase = _PHASE_VISION
            candidates = _our_side_ol_spots(ctx)

        dests = _clear_dead_and_phase(ctx, alive, phase)
        if not overlords or not candidates:
            return

        _assign_targets(overlords, dests, candidates, allow_stack=True)
        _path_to_parks(ctx, overlords, dests)

    return routine
