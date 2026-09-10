"""`UpgradeRush`'s own Validation Report shape.

The full four-stage report — opening economy, tech structures, upgrades,
attack waves — computed entirely off this build's own declared data
(`bot.builds.zerg.upgrade_rush.BUILD.army.upgrades`, `.pool_deadline`,
`.combat`). No Stage 1B: this build has no `ProxyCrewPlan` (`BUILD.crew`
stays `None`), so this file simply never adds that key.
"""

from __future__ import annotations

from typing import Dict, List

from tests.validators.base_validator import BaseValidator, StepResult


class UpgradeRushValidator(BaseValidator):
    BUILD_NAME = "UpgradeRush"
    REPORT_TITLE = "UPGRADE RUSH VALIDATION REPORT"

    def validate(self) -> Dict[str, List[StepResult]]:
        stages: Dict[str, List[StepResult]] = {
            "Stage 1: Opening Economy": self._validate_economy(),
            "Stage 2: Tech Structures": self._validate_structures(),
            "Stage 3: Upgrades": self._validate_upgrades(),
        }
        wave_label = self.ctx.build.combat.wave_stage_label
        stages[f"Stage 4: {wave_label}"] = self._validate_waves()
        return stages
