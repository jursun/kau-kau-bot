"""Chrono Boost - once the opening's own scripted `chrono @ ...` build-order
steps are done (see `protoss_builds.yml`), keep spending Nexus energy on the
Robotics Facility (Warp Prism / Observer) or, once it no longer needs it, a
Warp Gate (faster warp-in cycling for the Zealot flood).

Ares ships no Chrono Boost behavior of its own (only the build-order-runner's
one-off `chrono @ <structure>` YAML command), so this is bot-owned.

Target selection and the per-tag cooldown live in `bot.steps.protoss.
chrono_boost_army` (it needs `ctx.state`, which a `MacroBehavior.execute()`
does not receive) - this class just issues the one cast it's told to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ares.behaviors.macro.macro_behavior import MacroBehavior
from sc2.ids.ability_id import AbilityId

from bot.common.log import log_event

if TYPE_CHECKING:
    from ares import AresBot
    from ares.managers.manager_mediator import ManagerMediator
    from sc2.unit import Unit

CHRONO_ENERGY_COST: int = 50
CHRONO_DURATION_S: float = 20.0
"""How long a Chrono Boost cast lasts - the cooldown before the same target
is eligible again (see `chrono_target_cooldowns` in `core.state.RunState`)."""


@dataclass
class ProtossChronoBoost(MacroBehavior):
    """Cast Chrono Boost from `caster` (a Nexus) onto `target`. Both are
    picked by the caller (`steps.protoss.chrono_boost_army`) - this just
    issues the one command."""

    caster: "Unit"
    target: "Unit"

    def execute(
        self, ai: "AresBot", config: dict, mediator: "ManagerMediator"
    ) -> bool:
        self.caster(AbilityId.EFFECT_CHRONOBOOST, self.target)
        log_event(
            ai,
            f"CHRONO {self.target.type_id.name.lower()} {self.target.tag} "
            f"(from nexus {self.caster.tag}, energy was {self.caster.energy:.0f})",
        )
        return True
