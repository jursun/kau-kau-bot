"""Morph Hatchery → Lair on the main only.

ares `TechUp` picks `idle_townhalls[0]`, which often morphs the natural.
This waits for the hatchery closest to `base_location` (the main) to go
idle instead of falling back to another base.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from ares.behaviors.macro.macro_behavior import MacroBehavior
from ares.managers.manager_mediator import ManagerMediator

if TYPE_CHECKING:
    from ares import AresBot


@dataclass
class MorphLairAtMain(MacroBehavior):
    """Issue `UPGRADETOLAIR_LAIR` on the main hatchery when affordable.

    Attributes:
        base_location: Main-base position — the hatchery closest to this
            point is the only one allowed to morph.
    """

    base_location: Point2

    def execute(self, ai: "AresBot", config: dict, mediator: ManagerMediator) -> bool:
        structures = mediator.get_own_structures_dict
        if (
            not ai.can_afford(UnitTypeId.LAIR)
            or ai.already_pending(UnitTypeId.LAIR)
            or structures[UnitTypeId.LAIR]
            or structures[UnitTypeId.HIVE]
        ):
            return False

        hatches = [
            th
            for th in ai.townhalls
            if th.is_ready and th.type_id == UnitTypeId.HATCHERY
        ]
        if not hatches:
            return False

        main = min(hatches, key=lambda h: h.distance_to(self.base_location))
        if not main.is_idle:
            return False

        main(AbilityId.UPGRADETOLAIR_LAIR)
        return True
