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

from loguru import logger
from sc2.ids.upgrade_id import UpgradeId

from ares.behaviors.macro.macro_behavior import MacroBehavior
from ares.managers.manager_mediator import ManagerMediator

if TYPE_CHECKING:
    from ares import AresBot

_LOGGED: dict[tuple[int, str, str], float] = {}
_LOG_EVERY_S: float = 60.0


def _log_blocked(ai: "AresBot", tag: int, upgrade: UpgradeId, reason: str, detail: str) -> None:
    """Say why a chain isn't advancing, at most once a minute per reason - a
    pinned building that never researches is otherwise silent."""
    key = (tag, upgrade.name, reason)
    now = float(ai.time)
    if now - _LOGGED.get(key, -1e9) < _LOG_EVERY_S:
        return
    _LOGGED[key] = now
    logger.info(
        f"[{int(now // 60):02d}:{int(now % 60):02d}] RESEARCH_CHAIN {upgrade.name} "
        f"on {tag} blocked: {reason} {detail}"
    )


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
        if structure is None or not structure.is_ready:
            return False
        if not structure.is_idle:
            for upgrade in self.upgrades:
                if not ai.pending_or_complete_upgrade(upgrade):
                    _log_blocked(
                        ai, self.structure_tag, upgrade, "structure busy",
                        f"(orders={[o.ability.id.name for o in structure.orders]})",
                    )
                    break
            return False
        for upgrade in self.upgrades:
            if ai.pending_or_complete_upgrade(upgrade):
                continue
            if not ai.can_afford(upgrade):
                _log_blocked(
                    ai, self.structure_tag, upgrade, "cannot afford",
                    f"(minerals={ai.minerals}, gas={ai.vespene})",
                )
                return False
            ability = ai.game_data.upgrades[upgrade.value].research_ability.id
            if ability not in structure.abilities:
                _log_blocked(
                    ai, self.structure_tag, upgrade, "ability not offered",
                    f"(wanted {ability.name}; offered "
                    f"{sorted(a.name for a in structure.abilities)})",
                )
                return False
            _log_blocked(
                ai, self.structure_tag, upgrade, "started", f"({ability.name})"
            )
            structure(ability)
            return True
        return False
