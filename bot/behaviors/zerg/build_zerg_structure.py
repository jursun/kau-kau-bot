"""Place a Zerg tech/production structure without `request_zerg_placement`.

ares `BuildStructure` for Zerg only appends to `ai._requested_zerg_placements`,
and `_after_step` replays that entire list every frame for the rest of the
game (never cleared after processing). That both spam-builds and parks a
Drone on an unreachable `find_placement` spot — confirmed live for Spire:
`Building SPIRE` logged twice, structure never started, Drone stuck in main.

Same fix as `BuildSporeCrawler` / `BuildMacroHatch` / `ForwardCrawlerWave`:
find a legal tile with `can_place_structure`, pull one worker, dispatch once.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, floor, pi, sin
from typing import TYPE_CHECKING

from cython_extensions import cy_distance_to_squared, cy_towards
from loguru import logger
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from ares.behaviors.macro.macro_behavior import MacroBehavior
from ares.managers.manager_mediator import ManagerMediator

if TYPE_CHECKING:
    from ares import AresBot


def _snap(x: float, y: float) -> Point2:
    """2x2 / 3x3 centres land on `.5` coordinates."""
    return Point2((floor(x) + 0.5, floor(y) + 0.5))


def _find_near_base(
    ai: "AresBot",
    mediator: ManagerMediator,
    base: Point2,
    structure_type: UnitTypeId,
    *,
    min_radius: float,
    max_radius: float,
    resource_clearance: float,
) -> Point2 | None:
    """Ring-search a reachable, buildable spot near `base` (on creep)."""
    anchor = Point2(cy_towards(base, ai.game_info.map_center, 8.0))
    resources = [*ai.mineral_field, *ai.vespene_geyser]
    resource_sq = resource_clearance**2
    home_height = ai.get_terrain_height(base)

    candidates: list[Point2] = []
    for origin in (anchor, base):
        radius = min_radius
        while radius <= max_radius:
            for index in range(20):
                angle = 2.0 * pi * index / 20
                candidates.append(
                    _snap(
                        origin.x + radius * cos(angle),
                        origin.y + radius * sin(angle),
                    )
                )
            radius += 1.0

    candidates.sort(key=lambda p: cy_distance_to_squared(p, anchor))
    seen: set[Point2] = set()
    for point in candidates:
        if point in seen:
            continue
        seen.add(point)
        if cy_distance_to_squared(point, base) < min_radius**2:
            continue
        if ai.get_terrain_height(point) != home_height:
            continue
        if not ai.in_pathing_grid(point):
            continue
        if any(
            cy_distance_to_squared(point, r.position) < resource_sq for r in resources
        ):
            continue
        if mediator.can_place_structure(position=point, structure_type=structure_type):
            return point
    return None


@dataclass
class BuildZergStructure(MacroBehavior):
    """One-shot place+dispatch for a Zerg structure near `base_location`.

    Attributes:
        base_location: Townhall / production anchor to build near.
        structure_id: Structure to place (Spire, Infestation Pit, …).
        to_count: Stop once this many exist or are pending/on-route.
        max_on_route: Max workers already walking to this type.
        min_radius / max_radius: Search ring around the base.
        resource_clearance: Keep clear of mineral/gas tiles so the Drone
            can path to within BuildingManager's 1.0 build range.
    """

    base_location: Point2
    structure_id: UnitTypeId
    to_count: int = 1
    max_on_route: int = 1
    min_radius: float = 5.0
    max_radius: float = 18.0
    resource_clearance: float = 5.0

    def execute(self, ai: "AresBot", config: dict, mediator: ManagerMediator) -> bool:
        if (
            ai.not_started_but_in_building_tracker(self.structure_id)
            >= self.max_on_route
        ):
            return False
        existing = len(mediator.get_own_structures_dict[self.structure_id])
        pending = ai.structure_pending(self.structure_id)
        if existing + pending >= self.to_count:
            return False
        if not ai.can_afford(self.structure_id):
            return False
        if ai.tech_requirement_progress(self.structure_id) < 1.0:
            return False

        position = _find_near_base(
            ai,
            mediator,
            self.base_location,
            self.structure_id,
            min_radius=self.min_radius,
            max_radius=self.max_radius,
            resource_clearance=self.resource_clearance,
        )
        if position is None:
            return False

        worker = mediator.select_worker(target_position=position, force_close=True)
        if worker is None:
            return False

        mediator.build_with_specific_worker(
            worker=worker, structure_type=self.structure_id, pos=position
        )
        logger.info(
            f"{ai.time_formatted} Building {self.structure_id.name} at {position}"
        )
        return True
