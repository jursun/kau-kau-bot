"""Cap concurrent upgrade researches for Macro Zerg's post-5:00 posture.

ares `UpgradeController` starts at most one research (or tech-building
action) per `execute()`. This wrapper calls it until `max_slots` upgrades
from `upgrade_list` are already in progress, or until a call reports no
further action (including `prioritize=True` holding minerals/gas).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2

from ares.behaviors.macro.macro_behavior import MacroBehavior
from ares.behaviors.macro.upgrade_controller import UpgradeController
from ares.managers.manager_mediator import ManagerMediator

if TYPE_CHECKING:
    from ares import AresBot


def count_pending_upgrades(ai: "AresBot", upgrade_list: Sequence[UpgradeId]) -> int:
    """How many listed upgrades are currently researching (0 < progress < 1)."""
    return sum(
        1 for upgrade in upgrade_list if 0.0 < ai.already_pending_upgrade(upgrade) < 1.0
    )


@dataclass
class UpgradeSlots(MacroBehavior):
    """Research up to `max_slots` upgrades from `upgrade_list` in parallel.

    Attributes:
        upgrade_list: Desired upgrades in priority order.
        base_location: Where UpgradeController / TechUp place buildings.
        max_slots: Concurrent researching upgrades to maintain.
        prioritize: If True, block MacroPlan spend when an upgrade is
            ready but not yet affordable (floating-cash hold).
    """

    upgrade_list: list[UpgradeId]
    base_location: Point2
    max_slots: int = 1
    prioritize: bool = False

    def execute(self, ai: "AresBot", config: dict, mediator: ManagerMediator) -> bool:
        if self.max_slots <= 0 or not self.upgrade_list:
            return False

        acted = False
        # Bound iterations so a buggy controller cannot spin forever.
        for _ in range(self.max_slots + 2):
            pending = count_pending_upgrades(ai, self.upgrade_list)
            if pending >= self.max_slots:
                break
            started = UpgradeController(
                list(self.upgrade_list),
                base_location=self.base_location,
                prioritize=self.prioritize,
            ).execute(ai, config, mediator)
            if not started:
                break
            acted = True
            # Prioritize-hold or tech-building without a new research: stop
            # so we don't re-enter the same TechUp / hold every iteration.
            if count_pending_upgrades(ai, self.upgrade_list) <= pending:
                break
        return acted
