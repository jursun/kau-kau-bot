"""In-base macro hatchery.

`BuildStructure` cannot do this job on Zerg. It defers placement to ares'
`_do_zerg_build_placement`, which calls `find_placement(..., 30)` — a thirty
tile search radius from the base location. A 5x5 hatchery rarely fits cleanly
in a Zerg main, so that search walks outward and settles on the natural.

Getting the hatch into the main needs three separate things to hold, each of
which broke a test game in turn:

1. **A legal building centre.** A 5x5 structure centres on the middle of its
   centre tile, i.e. a `.5` coordinate. `can_place_structure` derives the
   footprint with `round(pos - 2.5)` and so accepts an integer centre, but the
   resulting build order is invalid and the drone silently never places.
2. **The main, not the natural.** Candidates must share the start location's
   terrain height (the natural is on lower ground) and stay clear of every
   known expansion.
3. **Somewhere the drone can actually reach.** `BuildingManager` only attempts
   `worker.build` once the drone is within *1.0* of the building centre, and
   paths there on the ground grid. A legal but cramped spot — tucked against
   the main hatchery, or behind the mineral line — is one the drone never
   reaches, so it moves, goes idle, and moves again forever. Candidates must
   therefore be standable and hold real clearance from resources and existing
   structures, not merely satisfy `can_place_structure`.
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

# Squared distance within which an expansion counts as "our own main".
_OWN_MAIN_TOLERANCE_SQ: float = 36.0

# Log the rejection funnel at most this often (game loops; ~10s at 22.4/s).
_DIAGNOSTIC_INTERVAL: int = 224


def _snap_to_building_centre(x: float, y: float) -> Point2:
    """5x5 structures centre on a `.5` coordinate. Integer centres are invalid."""
    return Point2((floor(x) + 0.5, floor(y) + 0.5))


@dataclass
class BuildMacroHatch(MacroBehavior):
    """Keep `to_count` hatcheries, placing extras inside the main.

    Attributes:
        to_count: Total townhall count to stop at (main included).
        min_radius: Closest to the main hatchery a candidate may sit. Below
            roughly 7 the two 5x5 footprints leave no lane for the drone.
        search_radius: How far from the start location to look.
        expansion_clearance: Reject candidates this close to any expansion.
        resource_clearance: Reject candidates this close to a mineral patch or
            geyser — mineral lines are where the drone gets stuck.
        structure_clearance: Reject candidates this close to a structure.
        max_on_route: Workers allowed to be walking to build one.
        ring_step: Spacing between candidate rings.
        rays: Candidate positions sampled per ring.
    """

    to_count: int
    min_radius: float = 8.0
    search_radius: float = 18.0
    expansion_clearance: float = 12.0
    resource_clearance: float = 6.0
    structure_clearance: float = 5.5
    max_on_route: int = 1
    ring_step: float = 1.5
    rays: int = 24

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

        funnel: dict[str, int] = {}
        position = self._in_base_placement(ai, mediator, funnel)
        if position is None:
            self._log_funnel(ai, funnel)
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

    def _ring_samples(self, base: Point2) -> set[Point2]:
        seen: set[Point2] = set()
        radius = self.min_radius
        while radius <= self.search_radius:
            for index in range(self.rays):
                angle = 2.0 * pi * index / self.rays
                seen.add(
                    _snap_to_building_centre(
                        base.x + radius * cos(angle), base.y + radius * sin(angle)
                    )
                )
            radius += self.ring_step
        return seen

    def _candidate_positions(
        self, ai: "AresBot", funnel: dict[str, int] | None = None
    ) -> list[Point2]:
        """Cheap filters, in order, recording how many survive each stage."""
        base: Point2 = ai.start_location
        home_height = ai.get_terrain_height(base)
        expansion_sq = self.expansion_clearance**2
        resource_sq = self.resource_clearance**2
        structure_sq = self.structure_clearance**2
        other_expansions = [
            e
            for e in ai.expansion_locations_list
            if cy_distance_to_squared(e, base) > _OWN_MAIN_TOLERANCE_SQ
        ]
        resources = [*ai.mineral_field, *ai.vespene_geyser]

        def record(stage: str, points: list[Point2]) -> list[Point2]:
            if funnel is not None:
                funnel[stage] = len(points)
            return points

        points = record("sampled", list(self._ring_samples(base)))
        # Snapping to a `.5` centre can pull a sample up to ~1.4 tiles inward,
        # so the ring radius alone does not enforce min_radius.
        min_radius_sq = self.min_radius**2
        points = record(
            "clear_of_main",
            [p for p in points if cy_distance_to_squared(p, base) >= min_radius_sq],
        )
        points = record(
            "on_plateau", [p for p in points if ai.get_terrain_height(p) == home_height]
        )
        points = record("standable", [p for p in points if ai.in_pathing_grid(p)])
        points = record(
            "off_expansions",
            [
                p
                for p in points
                if not any(
                    cy_distance_to_squared(p, e) < expansion_sq
                    for e in other_expansions
                )
            ],
        )
        points = record(
            "off_resources",
            [
                p
                for p in points
                if not any(
                    cy_distance_to_squared(p, r.position) < resource_sq
                    for r in resources
                )
            ],
        )
        return record(
            "off_structures",
            [
                p
                for p in points
                if not any(
                    cy_distance_to_squared(p, s.position) < structure_sq
                    for s in ai.structures
                )
            ],
        )

    def _in_base_placement(
        self,
        ai: "AresBot",
        mediator: ManagerMediator,
        funnel: dict[str, int] | None = None,
    ) -> Point2 | None:
        candidates = self._candidate_positions(ai, funnel)
        if not candidates:
            return None

        # Prefer the open, map-center side of the main (the ramp side), which
        # is where a drone can actually path to the building centre.
        preferred = Point2(cy_towards(ai.start_location, ai.game_info.map_center, 10.0))
        candidates.sort(key=lambda p: cy_distance_to_squared(p, preferred))

        for checked, point in enumerate(candidates, start=1):
            if mediator.can_place_structure(
                position=point, structure_type=UnitTypeId.HATCHERY
            ):
                if funnel is not None:
                    funnel["placement_checks"] = checked
                return point
        if funnel is not None:
            funnel["placement_checks"] = len(candidates)
        return None

    @staticmethod
    def _log_funnel(ai: "AresBot", funnel: dict[str, int]) -> None:
        """Say which filter emptied the list, so a failure needs no guesswork."""
        if ai.state.game_loop % _DIAGNOSTIC_INTERVAL:
            return
        breakdown = " -> ".join(f"{stage}={count}" for stage, count in funnel.items())
        logger.info(f"{ai.time_formatted} Macro hatch: no spot in main. {breakdown}")
