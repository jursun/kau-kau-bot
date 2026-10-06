"""Research a fixed chain of upgrades on one specific structure.

`UpgradeController` / `UpgradeSlots` pick whichever idle research building is
free and share a single priority queue and slot budget, so a building built
for a particular purpose (Macro Zerg's gas-surplus 3rd Evolution Chamber,
meant for melee attack) could sit idle behind the queue and never research
what it was built for. This pins one structure to one upgrade chain,
independent of that queue.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from sc2.ids.upgrade_id import UpgradeId

from ares.behaviors.macro.macro_behavior import MacroBehavior
from ares.managers.manager_mediator import ManagerMediator

if TYPE_CHECKING:
    from ares import AresBot


@dataclass
class ResearchChain(MacroBehavior):
    """Start the next not-yet-started upgrade of `upgrades` on the structure
    with `structure_tag`, once it is ready and idle.

    Returns True only when it issued a research. It deliberately never holds
    the frame's spend (returns False when it can't act): this is a side
    channel for floating resources, not a purchase that should starve the
    rest of `macro_steps`.

    Attributes:
        structure_tag: The building that does the researching.
        upgrades: Levels in order, e.g. melee attack 1, 2, 3. A level that
            isn't available yet (its prerequisite level / Lair / Hive isn't
            done) stops the chain for this frame.
    """

    structure_tag: int
    upgrades: Sequence[UpgradeId]

    def execute(self, ai: "AresBot", config: dict, mediator: ManagerMediator) -> bool:
        structure = ai.structures.find_by_tag(self.structure_tag)
        if structure is None or not structure.is_ready or not structure.is_idle:
            return False
        for upgrade in self.upgrades:
            if ai.pending_or_complete_upgrade(upgrade):
                continue
            if not ai.can_afford(upgrade):
                return False
            ability = ai.game_data.upgrades[upgrade.value].research_ability.id
            if ability not in structure.abilities:
                return False
            structure(ability)
            return True
        return False
