"""Queen larva injects as an ares `MacroBehavior`.

Ares ships no inject behavior of its own, so this is the one piece of the old
`EconomyManager` that survives as bot-owned logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from cython_extensions import cy_distance_to_squared
from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.unit import Unit

from ares.behaviors.macro.macro_behavior import MacroBehavior
from ares.managers.manager_mediator import ManagerMediator

if TYPE_CHECKING:
    from ares import AresBot


@dataclass
class InjectLarva(MacroBehavior):
    """Inject every ready townhall that isn't already on an inject timer.

    Register this directly (not inside a `MacroPlan`) so it runs every step —
    a `MacroPlan` short-circuits after the first behavior that acts.

    Attributes:
        min_energy: Energy a queen needs before it will be used for an inject.
    """

    min_energy: int = 25

    def execute(self, ai: "AresBot", config: dict, mediator: ManagerMediator) -> bool:
        townhalls = [th for th in ai.townhalls.ready]
        if not townhalls:
            return False

        available: list[Unit] = [
            q
            for q in ai.units(UnitTypeId.QUEEN)
            if q.is_ready
            and q.energy >= self.min_energy
            and q.tag not in ai.unit_tags_received_action
        ]
        if not available:
            return False

        did_action: bool = False
        for th in townhalls:
            if not available:
                break
            if th.has_buff(BuffId.QUEENSPAWNLARVATIMER):
                continue
            queen: Unit = min(
                available,
                key=lambda q: cy_distance_to_squared(q.position, th.position),
            )
            queen(AbilityId.EFFECT_INJECTLARVA, th)
            available.remove(queen)
            did_action = True

        return did_action
