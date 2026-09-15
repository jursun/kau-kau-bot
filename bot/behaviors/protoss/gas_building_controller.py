"""Protoss gas placement through the dedicated macro builder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from cython_extensions import cy_distance_to_squared, cy_sorted_by_distance_to
from sc2.units import Units

from ares.behaviors.macro.gas_building_controller import GasBuildingController
from bot.behaviors.protoss.builder import ensure_protoss_builder

if TYPE_CHECKING:
    from ares import AresBot
    from ares.managers.manager_mediator import ManagerMediator


@dataclass
class ProtossGasBuildingController(GasBuildingController):
    """Like ares `GasBuildingController`, but uses the dedicated macro Probe."""

    def execute(self, ai: "AresBot", config: dict, mediator: "ManagerMediator") -> bool:
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
                if cy_distance_to_squared(u.position, g.position) < 25.0
            ]
            and [
                th
                for th in ai.townhalls
                if cy_distance_to_squared(u.position, th.position) < 144.0
                and th.build_progress > 0.7
            ]
        ]
        if not available_geysers:
            return False

        if not self.closest_to:
            self.closest_to = ai.start_location

        geyser = cy_sorted_by_distance_to(available_geysers, self.closest_to)[0]
        worker = ensure_protoss_builder(ai, mediator, geyser.position)
        if worker is None:
            return False

        return mediator.build_with_specific_worker(
            worker=worker,
            structure_type=ai.gas_type,
            pos=geyser,
            assign_role=False,
        )
