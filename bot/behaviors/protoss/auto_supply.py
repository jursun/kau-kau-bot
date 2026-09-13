"""Protoss supply through the dedicated macro builder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ares.behaviors.macro.auto_supply import AutoSupply
from ares.consts import RACE_SUPPLY
from bot.behaviors.protoss.build_structure import ProtossBuildStructure

if TYPE_CHECKING:
    from ares import AresBot
    from ares.managers.manager_mediator import ManagerMediator


@dataclass
class ProtossAutoSupply(AutoSupply):
    """Like ares `AutoSupply`, but places pylons via the dedicated macro Probe."""

    def execute(self, ai: "AresBot", config: dict, mediator: "ManagerMediator") -> bool:
        if self._num_supply_required(ai, mediator) <= 0:
            return False

        ProtossBuildStructure(
            self.base_location,
            RACE_SUPPLY[ai.race],
            closest_to=self.closest_to,
        ).execute(ai, config, mediator)
        return self.return_true_if_supply_required
