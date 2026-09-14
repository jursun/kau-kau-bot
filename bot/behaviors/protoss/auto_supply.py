"""Protoss supply through the dedicated macro builder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ares.behaviors.macro.auto_supply import AutoSupply
from ares.behaviors.macro.build_structure import BuildStructure
from ares.consts import RACE_SUPPLY
from bot.behaviors.protoss.build_structure import ProtossBuildStructure

if TYPE_CHECKING:
    from ares import AresBot
    from ares.managers.manager_mediator import ManagerMediator


@dataclass
class ProtossAutoSupply(AutoSupply):
    """Like ares `AutoSupply`, but places pylons via the dedicated macro Probe.

    Returns True only when a Pylon build actually starts. If the dedicated
    builder cannot place, fall back to stock `BuildStructure` (any miner) so
    warps are not starved for supply while minerals float.
    """

    def execute(self, ai: "AresBot", config: dict, mediator: "ManagerMediator") -> bool:
        if self._num_supply_required(ai, mediator) <= 0:
            return False

        supply = RACE_SUPPLY[ai.race]
        placed = ProtossBuildStructure(
            self.base_location,
            supply,
            closest_to=self.closest_to,
            find_alternative=True,
        ).execute(ai, config, mediator)

        if not placed:
            placed = BuildStructure(
                self.base_location,
                supply,
                closest_to=self.closest_to,
                find_alternative=True,
            ).execute(ai, config, mediator)

        if not placed:
            metrics = getattr(getattr(ai, "ctx", None), "state", None)
            if metrics is not None:
                cm = getattr(metrics, "chargelot_metrics", None)
                if cm is not None:
                    cm.auto_supply_block_frames += 1
            return False

        return True
