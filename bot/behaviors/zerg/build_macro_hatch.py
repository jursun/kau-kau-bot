"""In-base macro hatchery.

`BuildStructure` cannot do this job on Zerg. It defers placement to ares'
`_do_zerg_build_placement`, which calls `find_placement(..., 30)` — a thirty
tile search radius from the base location. A 5x5 hatchery rarely fits cleanly
in a Zerg main, so that search walks outward and happily settles on the
natural expansion, which is exactly the wrong place for a larva hatch.

This behavior keeps the hatch in the main by construction: candidates must sit
on the same terrain height as the start location (the natural is on lower
ground) and must be clear of every known expansion.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, pi, sin
from typing import TYPE_CHECKING

from cython_extensions import cy_distance_to_squared, cy_towards
from loguru import logger
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from ares.behaviors.macro.macro_behavior import MacroBehavior
from ares.managers.manager_mediator import ManagerMediator

if TYPE_CHECKING:
    from ares import AresBot

# Squared distance within which an expansion counts as "our own main".
_OWN_MAIN_TOLERANCE_SQ: float = 36.0


@dataclass
class BuildMacroHatch(MacroBehavior):
    """Keep `to_count` hatcheries, placing extras inside the main.

    Attributes:
        to_count: Total townhall count to stop at (main included).
        search_radius: How far from the start location to look.
        expansion_clearance: Reject candidates this close to any expansion.
        max_on_route: Workers allowed to be walking to build one.
        ring_step: Spacing between candidate rings.
        rays: Candidate positions sampled per ring.
    """

    to_count: int
    search_radius: float = 14.0
    expansion_clearance: float = 12.0
    max_on_route: int = 1
    ring_step: float = 2.0
    rays: int = 16

    def execute(self, ai: "AresBot", config: dict, mediator: ManagerMediator) -> bool:
        if (
            ai.not_started_but_in_building_tracker(UnitTypeId.HATCHERY)
            >= self.max_on_route
        ):
            return False
        if self._hatchery_count(ai) >= self.to_count:
            return False
        if not ai.can_afford(UnitTypeId.HATCHERY):
            return False

        position = self._in_base_placement(ai, mediator)
        if position is None:
            return False

        worker = mediator.select_worker(target_position=position, force_close=True)
        if worker is None:
            return False

        mediator.build_with_specific_worker(
            worker=worker, structure_type=UnitTypeId.HATCHERY, pos=position
        )
        logger.info(f"{ai.time_formatted} Macro hatch in main at {position}")
        return True

    @staticmethod
    def _hatchery_count(ai: "AresBot") -> int:
        # `townhalls` already includes ones under construction.
        return len(ai.townhalls) + ai.not_started_but_in_building_tracker(
            UnitTypeId.HATCHERY
        )

    def _candidate_positions(self, ai: "AresBot") -> list[Point2]:
        """Ring-sample the main, dropping anything off-plateau or near an expansion."""
        base: Point2 = ai.start_location
        home_height = ai.get_terrain_height(base)
        clearance_sq = self.expansion_clearance**2
        other_expansions = [
            e
            for e in ai.expansion_locations_list
            if cy_distance_to_squared(e, base) > _OWN_MAIN_TOLERANCE_SQ
        ]

        seen: set[Point2] = set()
        radius = 5.0
        while radius <= self.search_radius:
            for index in range(self.rays):
                angle = 2.0 * pi * index / self.rays
                point = Point2(
                    (base.x + radius * cos(angle), base.y + radius * sin(angle))
                ).rounded
                if point in seen:
                    continue
                seen.add(point)
            radius += self.ring_step

        return [
            p
            for p in seen
            if ai.get_terrain_height(p) == home_height
            and not any(
                cy_distance_to_squared(p, e) < clearance_sq for e in other_expansions
            )
        ]

    def _in_base_placement(
        self, ai: "AresBot", mediator: ManagerMediator
    ) -> Point2 | None:
        """Cheap filters first, then the placement check on the best candidates."""
        candidates = self._candidate_positions(ai)
        if not candidates:
            return None

        # Bias toward the map-center side of the main, nearer the rally.
        preferred = Point2(cy_towards(ai.start_location, ai.game_info.map_center, 5.0))
        candidates.sort(key=lambda p: cy_distance_to_squared(p, preferred))

        for point in candidates:
            if mediator.can_place_structure(
                position=point, structure_type=UnitTypeId.HATCHERY
            ):
                return point
        return None
