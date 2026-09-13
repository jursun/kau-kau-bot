"""Protoss expansion placement through the dedicated macro builder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ares.behaviors.macro.expansion_controller import ExpansionController
from bot.behaviors.protoss.builder import ensure_protoss_builder

if TYPE_CHECKING:
    from ares import AresBot
    from ares.managers.manager_mediator import ManagerMediator


@dataclass
class ProtossExpansionController(ExpansionController):
    """Like ares `ExpansionController`, but uses the dedicated macro Probe."""

    def execute(self, ai: "AresBot", config: dict, mediator: "ManagerMediator") -> bool:
        if (
            len([th for th in ai.townhalls if th.is_ready])
            + ai.structure_pending(ai.base_townhall_type)
            >= self.to_count
            or ai.structure_pending(ai.base_townhall_type) >= self.max_pending
            or (
                self.can_afford_check
                and not self.prioritize
                and not ai.can_afford(ai.base_townhall_type)
            )
        ):
            return False

        location = self._get_next_expansion_location(ai, mediator)
        if location is None:
            return False

        worker = ensure_protoss_builder(ai, mediator, location)
        if worker is None:
            return False

        return mediator.build_with_specific_worker(
            worker=worker,
            structure_type=ai.base_townhall_type,
            pos=location,
            assign_role=False,
        )
