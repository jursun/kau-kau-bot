"""`Speedling All-In`'s own Validation Report shape.

Same four stages as `UpgradeRushValidator` (no crew plan), own file/class/
title. Stage 2 falls back to "No tech structures required" since Metabolic
Boost needs no structure; Stage 3 lists it normally. The two `validate()`
bodies read identically today - that's coincidence, not a merge signal; see
`base_validator`'s module docstring for why each build keeps its own file.
"""

from __future__ import annotations

from typing import Dict, List

from tests.validators.base_validator import BaseValidator, StepResult


class SpeedlingAllInValidator(BaseValidator):
    BUILD_NAME = "Speedling All-In"
    REPORT_TITLE = "SPEEDLING ALL-IN VALIDATION REPORT"

    def validate(self) -> Dict[str, List[StepResult]]:
        stages: Dict[str, List[StepResult]] = {
            "Stage 1: Opening Economy": self._validate_economy(),
            "Stage 2: Tech Structures": self._validate_structures(),
            "Stage 3: Upgrades": self._validate_upgrades(),
        }
        wave_label = self.ctx.build.combat.wave_stage_label
        stages[f"Stage 4: {wave_label}"] = self._validate_waves()
        stages["Stage 5: Combat QA"] = self._validate_combat_qa()
        return stages
