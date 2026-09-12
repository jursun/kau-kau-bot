"""`2base Chargelot All-In`'s own Validation Report shape.

Same four-stage layout as the Zerg all-ins (no proxy crew). Stage 2 covers
Twilight / Robo / Gate count via structure trackers; Stage 3 is Charge.
"""

from __future__ import annotations

from typing import Dict, List

from tests.validators.base_validator import BaseValidator, StepResult


class TwoBaseChargelotAllInValidator(BaseValidator):
    BUILD_NAME = "2base Chargelot All-In"
    REPORT_TITLE = "2BASE CHARGELOT ALL-IN VALIDATION REPORT"

    def validate(self) -> Dict[str, List[StepResult]]:
        stages: Dict[str, List[StepResult]] = {
            "Stage 1: Opening Economy": self._validate_economy(),
            "Stage 2: Tech Structures": self._validate_structures(),
            "Stage 3: Upgrades": self._validate_upgrades(),
        }
        wave_label = self.ctx.build.combat.wave_stage_label
        stages[f"Stage 4: {wave_label}"] = self._validate_waves()
        return stages
