"""Place a Zerg tech/production structure without `request_zerg_placement`.

ares `BuildStructure` for Zerg only appends to `ai._requested_zerg_placements`,
and `_after_step` replays that entire list every frame for the rest of the
game (never cleared after processing). That both spam-builds and parks a
Drone on an unreachable `find_placement` spot — confirmed live for Spire:
`Building SPIRE` logged twice, structure never started, Drone stuck in main.

Same fix as `BuildSporeCrawler` / `BuildMacroHatch` / `ForwardCrawlerWave`:
find a legal tile with `can_place_structure`, pull one worker, dispatch once.

CheatInsane (10:00 leave) still saw Spire thrash + Stage-3 never: MacroPlan
is `any(...)`, so returning False while a Drone is on-route (or while we
cannot yet afford 200/200) let `_reserve_upgrade_bank` / army spend the
bank before the build started. `prioritize=True` holds the plan like
`UpgradeController.prioritize` until the structure is pending or alive.
Sticky multi-base placement cuts re-pick thrash when BuildingManager
drops a Zerg worker after a transient `can_place_structure` miss.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import cos, floor, pi, sin
from typing import TYPE_CHECKING, ClassVar

from cython_extensions import cy_distance_to_squared, cy_towards
from loguru import logger
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from ares.behaviors.macro.macro_behavior import MacroBehavior
from ares.managers.manager_mediator import ManagerMediator

if TYPE_CHECKING:
    from ares import AresBot


# Last good tile per structure type — reused while still placeable so we do
# not thrash a new ring-search every time BuildingManager clears a Zerg
# worker after a one-frame placement miss.
_STICKY_PLACEMENT: dict[UnitTypeId, Point2] = {}


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


def _clear_sticky(structure_id: UnitTypeId) -> None:
    _STICKY_PLACEMENT.pop(structure_id, None)


@dataclass
class BuildZergStructure(MacroBehavior):
    """One-shot place+dispatch for a Zerg structure near `base_location`.

    Attributes:
        base_location: Townhall / production anchor to build near first.
        structure_id: Structure to place (Spire, Infestation Pit, …).
        to_count: Stop once this many exist or are pending/on-route.
        max_on_route: Max workers already walking to this type.
        min_radius / max_radius: Search ring around each base.
        resource_clearance: Keep clear of mineral/gas tiles so the Drone
            can path to within BuildingManager's 1.0 build range.
        prioritize: If True, return True while on-route or while we cannot
            yet afford — holds MacroPlan spend so upgrades/army cannot
            empty the bank before the structure starts (Spire 200/200).
        try_all_bases: Also search other ready townhalls when the primary
            base has no legal tile.
    """

    base_location: Point2
    structure_id: UnitTypeId
    to_count: int = 1
    max_on_route: int = 1
    min_radius: float = 5.0
    max_radius: float = 18.0
    resource_clearance: float = 5.0
    prioritize: bool = True
    try_all_bases: bool = True

    def execute(self, ai: "AresBot", config: dict, mediator: ManagerMediator) -> bool:
        existing = len(mediator.get_own_structures_dict[self.structure_id])
        pending = ai.structure_pending(self.structure_id)
        if existing + pending >= self.to_count:
            _clear_sticky(self.structure_id)
            return False

        on_route = ai.not_started_but_in_building_tracker(self.structure_id)
        # Drone already walking: hold MacroPlan so nothing below spends the
        # 200/200 before BUILD starts. Was `return False` — that is what let
        # Glial/army drain the bank mid-walk (CheatInsane Spire thrash).
        if on_route >= self.max_on_route:
            return bool(self.prioritize)

        if ai.tech_requirement_progress(self.structure_id) < 1.0:
            return False

        if not ai.can_afford(self.structure_id):
            # Same UpgradeController.prioritize pattern: gated + not yet
            # affordable must still block later spenders, or Spire never
            # accumulates 200/200 under an active upgrade bank.
            return bool(self.prioritize)

        position = self._resolve_position(ai, mediator)
        if position is None:
            return False

        worker = mediator.select_worker(target_position=position, force_close=True)
        if worker is None:
            # Hold bank while we wait for a free Drone rather than letting
            # army/upgrades spend and restart the starve loop.
            return bool(self.prioritize)

        mediator.build_with_specific_worker(
            worker=worker, structure_type=self.structure_id, pos=position
        )
        _STICKY_PLACEMENT[self.structure_id] = position
        logger.info(
            f"{ai.time_formatted} Building {self.structure_id.name} at {position}"
        )
        return True

    def _resolve_position(
        self, ai: "AresBot", mediator: ManagerMediator
    ) -> Point2 | None:
        sticky = _STICKY_PLACEMENT.get(self.structure_id)
        if sticky is not None and mediator.can_place_structure(
            position=sticky, structure_type=self.structure_id
        ):
            return sticky
        if sticky is not None:
            _clear_sticky(self.structure_id)

        bases: list[Point2] = [self.base_location]
        if self.try_all_bases:
            for th in ai.townhalls.ready:
                pos = th.position
                if all(cy_distance_to_squared(pos, b) > 1.0 for b in bases):
                    bases.append(pos)

        for base in bases:
            found = _find_near_base(
                ai,
                mediator,
                base,
                self.structure_id,
                min_radius=self.min_radius,
                max_radius=self.max_radius,
                resource_clearance=self.resource_clearance,
            )
            if found is not None:
                return found
        return None
