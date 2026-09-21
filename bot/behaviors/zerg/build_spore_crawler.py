"""Spore/Spine Crawler placement as an ares `MacroBehavior`, done directly
rather than through `ai.request_zerg_placement`.

`BuildStructure` cannot be used for this on Zerg, and not only for the
`to_count_per_base` reason documented in `steps/zerg.py`. For `Race.Zerg` it
always calls `ai.request_zerg_placement(base_location, structure_id)`, which
appends to `ai._requested_zerg_placements` — and `_after_step` replays that
ENTIRE list, unconditionally, every single frame for the rest of the game.
It is never cleared after processing (only reset once, at game start). So a
single request there doesn't fire once: `find_placement` + `select_worker`
succeed again on the next frame, and the one after that, and so on for as
long as the game runs, each success producing one more crawler. The base
that gets hit hardest is whichever one was requested earliest — the main,
in this build, since it's always the first entry the `spore_crawlers` step
walks — which is exactly the "multiple spore crawlers piling up in the
main, forever" symptom this fixes. (See `ares/main.py`: `request_zerg_placement`
appends at one line, `_after_step` loops the same list every frame, and no
line in between ever calls `.clear()` on it.)

Doing the find-placement / select-worker / build-with-specific-worker
sequence ourselves, synchronously, in this behavior's own `execute()`
instead of going through that queue means one call really is one build —
the same reasoning `bot/behaviors/zerg/build_macro_hatch.py` already applies
to hatcheries, for the same underlying reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, floor, pi, sin
from typing import TYPE_CHECKING

from cython_extensions import cy_distance_to_squared, cy_towards
from loguru import logger
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.unit import Unit

from ares.behaviors.macro.macro_behavior import MacroBehavior
from ares.managers.manager_mediator import ManagerMediator

if TYPE_CHECKING:
    from ares import AresBot


def _snap(x: float, y: float) -> Point2:
    return Point2((floor(x) + 0.5, floor(y) + 0.5))

_SPINE_MIN_SEP: float = 3.0
"""Spines closer than this count as the same pad — 2nd spine must not land here."""


def _spine_reserved_positions(ai: "AresBot", mediator: ManagerMediator) -> list[Point2]:
    """Existing + en-route Spine tiles so a second crawler picks a new pad."""
    reserved: list[Point2] = []
    for spine in ai.structures(UnitTypeId.SPINECRAWLER):
        reserved.append(Point2(spine.position))
    tracker = getattr(mediator, "get_building_tracker_dict", None)
    if tracker:
        from ares.consts import ID, TARGET

        for info in tracker.values():
            if info.get(ID) != UnitTypeId.SPINECRAWLER:
                continue
            target = info.get(TARGET)
            if target is None:
                continue
            pos = getattr(target, "position", target)
            reserved.append(Point2(pos))
    return reserved


def _find_near(
    ai: "AresBot",
    mediator: ManagerMediator,
    reference: Point2,
    structure_type: UnitTypeId,
    min_radius: float = 1.0,
    max_radius: float = 10.0,
    avoid: list[Point2] | None = None,
    min_sep: float = _SPINE_MIN_SEP,
) -> Point2 | None:
    """Ring-search a legal crawler spot near `reference` (needs creep)."""
    resources = [*ai.mineral_field, *ai.vespene_geyser]
    resource_sq = 2.5**2
    avoid = avoid or []
    min_sep_sq = min_sep * min_sep
    candidates: list[Point2] = [_snap(reference.x, reference.y)]
    radius = min_radius
    while radius <= max_radius:
        for index in range(16):
            angle = 2.0 * pi * index / 16
            candidates.append(
                _snap(
                    reference.x + radius * cos(angle),
                    reference.y + radius * sin(angle),
                )
            )
        radius += 1.0
    candidates.sort(key=lambda p: cy_distance_to_squared(p, reference))
    seen: set[Point2] = set()
    for point in candidates:
        if point in seen:
            continue
        seen.add(point)
        if not ai.in_pathing_grid(point):
            continue
        if any(cy_distance_to_squared(point, r.position) < resource_sq for r in resources):
            continue
        if any(cy_distance_to_squared(point, a) < min_sep_sq for a in avoid):
            continue
        if mediator.can_place_structure(
            position=point, structure_type=structure_type
        ):
            return point
    return None


def _spine_candidates(ai: "AresBot", base_location: Point2) -> list[Point2]:
    """Approach-side anchors for Spines — opposite the mineral-line Spores.

    Confirmed live: Spines that reused `get_behind_mineral_positions` never
    placed once a Spore already sat on those three tiles (`SPINE maintain`
    logged forever, zero `COMPLETE spinecrawler`). Spread anchors so a
    second Spine does not collapse onto the first pad.
    """
    center = ai.game_info.map_center
    toward = Point2(cy_towards(base_location, center, 7.0))
    # Perpendicular offsets give a second pad beside the first.
    dx = center.x - base_location.x
    dy = center.y - base_location.y
    length = (dx * dx + dy * dy) ** 0.5
    if length < 1.0:
        return [toward, Point2(cy_towards(base_location, center, 9.0))]
    px, py = -dy / length, dx / length
    return [
        toward,
        Point2((toward.x + px * 4.0, toward.y + py * 4.0)),
        Point2((toward.x - px * 4.0, toward.y - py * 4.0)),
        Point2(cy_towards(base_location, center, 5.0)),
        Point2(cy_towards(base_location, center, 9.0)),
    ]


@dataclass
class BuildSporeCrawler(MacroBehavior):
    """Place and dispatch a worker for one Spore/Spine Crawler near
    `base_location`.

    Spores try `get_behind_mineral_positions` (mineral-line AA). Spines
    ring-search toward the map center (front of the base) so they do not
    compete for the same tiles. Spine placement also skips tiles within
    `_SPINE_MIN_SEP` of an existing / en-route Spine (confirmed live: 2nd
    early-aggression Spine rebuilt on top of the first).

    Attributes:
        base_location: townhall position to place the crawler near.
        structure_type: `SPORECRAWLER` (default) or `SPINECRAWLER`.
    """

    base_location: Point2
    structure_type: UnitTypeId = UnitTypeId.SPORECRAWLER

    def execute(self, ai: "AresBot", config: dict, mediator: ManagerMediator) -> bool:
        if not ai.can_afford(self.structure_type):
            return False

        if self.structure_type == UnitTypeId.SPINECRAWLER:
            avoid = _spine_reserved_positions(ai, mediator)
            for anchor in _spine_candidates(ai, self.base_location):
                candidate = _find_near(
                    ai,
                    mediator,
                    anchor,
                    self.structure_type,
                    avoid=avoid,
                )
                if candidate is None:
                    continue
                if self._dispatch(mediator, candidate):
                    return True
            logger.info(
                f"SPINE place failed near {self.base_location} — no legal tile"
            )
            return False

        candidates = mediator.get_behind_mineral_positions(th_pos=self.base_location)
        for candidate in candidates:
            if not mediator.can_place_structure(
                position=candidate, structure_type=self.structure_type
            ):
                continue
            if self._dispatch(mediator, candidate):
                return True
        return False

    def _dispatch(self, mediator: ManagerMediator, candidate: Point2) -> bool:
        worker: Unit | None = mediator.select_worker(
            target_position=candidate, force_close=True
        )
        if worker is None:
            return False
        mediator.build_with_specific_worker(
            worker=worker, structure_type=self.structure_type, pos=candidate
        )
        return True
