"""Queen production as an ares `MacroBehavior`."""

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
class TrainQueens(MacroBehavior):
    """Keep up to `to_count` queens, at most one per ready townhall.

    Queens are trained from the hatchery rather than from larva, so this does
    not compete with `SpawnController` for larva — but it does compete for
    minerals, which is why it sits high in the `MacroPlan`.

    Attributes:
        to_count: Total queen target across all bases.
        max_per_townhall: Queens to allow per ready townhall.
    """

    to_count: int
    max_per_townhall: int = 1

    def execute(self, ai: "AresBot", config: dict, mediator: ManagerMediator) -> bool:
        if not ai.structures(UnitTypeId.SPAWNINGPOOL).ready:
            return False

        townhalls: list[Unit] = [th for th in ai.townhalls.ready]
        if not townhalls:
            return False

        existing: int = mediator.get_own_unit_count(unit_type_id=UnitTypeId.QUEEN)
        pending: int = int(ai.already_pending(UnitTypeId.QUEEN))
        target: int = min(self.to_count, len(townhalls) * self.max_per_townhall)
        if existing + pending >= target:
            return False

        if not ai.can_afford(UnitTypeId.QUEEN):
            return False

        for th in townhalls:
            if not th.is_idle:
                continue
            th.train(UnitTypeId.QUEEN)
            return True

        return False
