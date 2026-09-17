"""Place one Spine or Spore Crawler near an army-forward anchor.

Same reason as `BuildSporeCrawler`: do not use `BuildStructure` /
`request_zerg_placement` (that queue replays every frame forever). Place
via `can_place_structure` + `build_with_specific_worker` once per call.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, floor, pi, sin
from typing import TYPE_CHECKING, Sequence

from cython_extensions import cy_distance_to_squared
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from ares.behaviors.macro.macro_behavior import MacroBehavior
from ares.managers.manager_mediator import ManagerMediator

if TYPE_CHECKING:
    from ares import AresBot

_CRAWLER_TYPES: frozenset[UnitTypeId] = frozenset(
    {UnitTypeId.SPINECRAWLER, UnitTypeId.SPORECRAWLER}
)


def _snap(x: float, y: float) -> Point2:
    return Point2((floor(x) + 0.5, floor(y) + 0.5))


def _find_near(
    ai: "AresBot",
    mediator: ManagerMediator,
    reference: Point2,
    structure_type: UnitTypeId,
    min_radius: float = 1.0,
    max_radius: float = 12.0,
) -> Point2 | None:
    """Ring-search a legal crawler spot near `reference` (needs creep)."""
    resources = [*ai.mineral_field, *ai.vespene_geyser]
    resource_sq = 2.5**2
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
        if mediator.can_place_structure(
            position=point, structure_type=structure_type
        ):
            return point
    return None


@dataclass
class ForwardCrawlerWave(MacroBehavior):
    """Pull workers to plant Spines/Spores beside the army in one pulse.

    Attributes:
        anchor: Army-forward point to place around.
        structure_types: Exact build order for this wave (e.g. 3 spines +
            3 spores). One worker is dispatched per successful placement.
    """

    anchor: Point2
    structure_types: Sequence[UnitTypeId]

    def execute(self, ai: "AresBot", config: dict, mediator: ManagerMediator) -> bool:
        acted = False
        # Slight angular spread so six crawlers do not fight one tile.
        offsets = [
            Point2((3.0 * cos(2.0 * pi * i / max(len(self.structure_types), 1)),
                    3.0 * sin(2.0 * pi * i / max(len(self.structure_types), 1))))
            for i in range(len(self.structure_types))
        ]
        for index, structure_type in enumerate(self.structure_types):
            if structure_type not in _CRAWLER_TYPES:
                continue
            if not ai.can_afford(structure_type):
                break
            reference = self.anchor + offsets[index]
            pos = _find_near(ai, mediator, reference, structure_type)
            if pos is None:
                continue
            worker = mediator.select_worker(target_position=pos, force_close=True)
            if worker is None:
                break
            if mediator.build_with_specific_worker(
                worker=worker, structure_type=structure_type, pos=pos
            ):
                acted = True
        return acted
