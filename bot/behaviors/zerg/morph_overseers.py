"""Overseer production as an ares `MacroBehavior`.

Ares has no unit-morph controller generic enough for this (`SpawnController`
is larva-spawn only, and `ProductionController` explicitly warns it isn't
supported for Zerg), so this rolls the same one-at-a-time pattern
`TrainQueens` uses for queens.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId
from sc2.unit import Unit

from ares.behaviors.macro.macro_behavior import MacroBehavior
from ares.managers.manager_mediator import ManagerMediator

if TYPE_CHECKING:
    from ares import AresBot


@dataclass
class MorphOverseers(MacroBehavior):
    """Morph Overlords into Overseers, one at a time, up to `to_count`.

    Attributes:
        to_count: Total overseers to maintain.
    """

    to_count: int

    def execute(self, ai: "AresBot", config: dict, mediator: ManagerMediator) -> bool:
        # Needs Lair. Checked here (not just via an outer gate) so this
        # behaves correctly even if a build calls it before teching up.
        if (
            not ai.structures(UnitTypeId.LAIR).ready
            and not ai.structures(UnitTypeId.HIVE).ready
        ):
            return False

        existing: int = mediator.get_own_unit_count(unit_type_id=UnitTypeId.OVERSEER)
        pending: int = int(ai.already_pending(UnitTypeId.OVERSEER))
        if existing + pending >= self.to_count:
            return False

        if not ai.can_afford(UnitTypeId.OVERSEER):
            return False

        overlords: list[Unit] = [
            ov for ov in ai.units(UnitTypeId.OVERLORD) if ov.is_ready
        ]
        if not overlords:
            return False

        overlords[0].train(UnitTypeId.OVERSEER)
        return True
