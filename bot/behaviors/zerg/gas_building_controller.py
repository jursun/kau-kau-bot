"""Gas buildings with expansion-order preference: main → natural → 3rd → …

ares' `GasBuildingController` sorts free geysers by distance to a single
`closest_to` point (default: start). That fills the main first, but after
that it follows map geometry, not the order we actually took bases — a
geyser at a closer 4th can beat the natural's second geyser. Macro Zerg
wants each earlier base topped up before later ones get another Extractor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from cython_extensions import cy_distance_to_squared
from sc2.data import Race
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units

from ares.behaviors.macro.gas_building_controller import GasBuildingController

if TYPE_CHECKING:
    from ares import AresBot
    from ares.managers.manager_mediator import ManagerMediator

# Same radii ares' GasBuildingController uses (squared).
_GEYSER_TAKEN_SQ: float = 25.0
_TOWNHALL_NEAR_SQ: float = 144.0


def preferred_base_locations(
    owned_expansions: list[Point2],
    start_location: Point2,
    natural: Point2 | None,
) -> list[Point2]:
    """Owned expansion sites in Macro Zerg gas order: main, natural, then
    remaining by distance to start (3rd, 4th, 5th, …).

    Main/natural are matched by proximity to `start_location` /
    `natural` rather than "closest remaining", so an un-taken natural
    does not steal the 3rd-base slot into the natural slot.
    """
    if not owned_expansions:
        return []

    # Expansion sites are discrete; 10 tiles is well under inter-base gap.
    match_sq = 100.0

    def _match(ref: Point2) -> Point2 | None:
        hits = [
            loc
            for loc in owned_expansions
            if cy_distance_to_squared(loc, ref) < match_sq
        ]
        if not hits:
            return None
        return min(hits, key=lambda loc: cy_distance_to_squared(loc, ref))

    ordered: list[Point2] = []
    main = _match(start_location)
    if main is not None:
        ordered.append(main)
    if natural is not None:
        nat = _match(natural)
        if nat is not None and nat not in ordered:
            ordered.append(nat)
    rest = [loc for loc in owned_expansions if loc not in ordered]
    rest.sort(key=lambda loc: cy_distance_to_squared(loc, start_location))
    ordered.extend(rest)
    return ordered


def pick_geyser_for_bases(
    available_geysers: list[Unit],
    preferred_bases: list[Point2],
) -> Unit | None:
    """First free geyser at the earliest preferred base that still has one."""
    for base in preferred_bases:
        at_base = [
            g
            for g in available_geysers
            if cy_distance_to_squared(g.position, base) < _TOWNHALL_NEAR_SQ
        ]
        if at_base:
            return min(
                at_base,
                key=lambda g: cy_distance_to_squared(g.position, base),
            )
    return None


@dataclass
class ZergGasBuildingController(GasBuildingController):
    """Like ares `GasBuildingController`, but next geyser follows base order
    (main → natural → later expansions by distance to start) instead of
    pure distance to `closest_to`."""

    def execute(self, ai: "AresBot", config: dict, mediator: "ManagerMediator") -> bool:
        num_gas: int
        if ai.race == Race.Terran:
            num_gas = ai.not_started_but_in_building_tracker(ai.gas_type) + len(
                ai.gas_buildings
            )
        else:
            num_gas = len(ai.gas_buildings) + mediator.get_building_counter[ai.gas_type]
        if (
            num_gas >= self.to_count
            or mediator.get_building_counter[ai.gas_type] >= self.max_pending
            or ai.minerals < 35
        ):
            return False

        existing_gas_buildings: Units = ai.all_gas_buildings
        available_geysers = [
            u
            for u in ai.vespene_geyser
            if not [
                g
                for g in existing_gas_buildings
                if cy_distance_to_squared(u.position, g.position) < _GEYSER_TAKEN_SQ
            ]
            and [
                th
                for th in ai.townhalls
                if cy_distance_to_squared(u.position, th.position) < _TOWNHALL_NEAR_SQ
                and th.build_progress > 0.7
            ]
        ]
        if not available_geysers:
            return False

        preferred = preferred_base_locations(
            list(ai.owned_expansions.keys()),
            ai.start_location,
            mediator.get_own_nat,
        )
        geyser = pick_geyser_for_bases(available_geysers, preferred)
        if geyser is None:
            return False

        if worker := mediator.select_worker(
            target_position=geyser, force_close=True
        ):
            mediator.build_with_specific_worker(
                worker=worker,
                structure_type=ai.gas_type,
                pos=geyser,
            )
            return True

        return False
