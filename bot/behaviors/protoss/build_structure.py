"""Protoss macro placement through one dedicated builder Probe."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger
from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId

from ares.behaviors.macro.build_structure import BuildStructure
from ares.dicts.structure_to_building_size import STRUCTURE_TO_BUILDING_SIZE
from bot.behaviors.protoss.builder import ensure_protoss_builder

if TYPE_CHECKING:
    from ares import AresBot
    from ares.managers.manager_mediator import ManagerMediator


@dataclass
class ProtossBuildStructure(BuildStructure):
    """Like ares `BuildStructure`, but routes all work through one Probe."""

    def execute(self, ai: "AresBot", config: dict, mediator: "ManagerMediator") -> bool:
        if self.structure_id not in STRUCTURE_TO_BUILDING_SIZE:
            logger.error(
                f"Invalid structure type passed to `ProtossBuildStructure`: "
                f"{self.structure_id}"
            )
            return False

        if (
            ai.not_started_but_in_building_tracker(self.structure_id)
            >= self.max_on_route
        ):
            return False
        if self.to_count and self._enough_existing(ai, mediator):
            return False
        if self.to_count_per_base and self._enough_existing_at_this_base(mediator):
            return False
        if (
            self.tech_progress_check
            and ai.tech_requirement_progress(self.structure_id)
            < self.tech_progress_check
        ):
            return False

        if ai.race == Race.Zerg:
            ai.request_zerg_placement(self.base_location, self.structure_id)
            return True

        within_psionic_matrix: bool = self.structure_id != UnitTypeId.PYLON

        if not (
            placement := mediator.request_building_placement(
                base_location=self.base_location,
                structure_type=self.structure_id,
                first_pylon=self.first_pylon,
                static_defence=self.static_defence,
                wall=self.wall,
                find_alternative=self.find_alternative,
                within_psionic_matrix=within_psionic_matrix,
                closest_to=self.closest_to,
                supply_depot=self.supply_depot,
                missile_turret=self.missile_turret,
                sensor_tower=self.sensor_tower,
                upgrade_structure=self.upgrade_structure,
                production=self.production,
                bunker=self.bunker,
                reaper_wall=self.reaper_wall,
                # Single builder: do not reserve before the Probe is free,
                # or wall slots stay phantom-busy and starve the 8-Gate commit.
                reserve_placement=False,
            )
        ):
            return False

        worker = ensure_protoss_builder(ai, mediator, placement)
        if worker is None:
            return False

        return mediator.build_with_specific_worker(
            worker=worker,
            structure_type=self.structure_id,
            pos=placement,
            assign_role=False,
        )
