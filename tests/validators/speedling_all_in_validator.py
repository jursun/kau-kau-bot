"""`Speedling All-In`'s own Validation Report shape.

Same four stages as `UpgradeRushValidator` — this build also has no crew
plan — but its own file, own class, own report title. `_validate_structures`
falls back to "No tech structures required by this build" for Stage 2,
since Metabolic Boost (this build's only upgrade) needs no Evolution
Chamber, Lair or Hive; Stage 3 lists Metabolic Boost normally.

Today the two `validate()` bodies (this file and `upgrade_rush_validator.
py`) read identically - that's a coincidence of what each build currently
declares, not a reason to merge them back into one shared method. See
`base_validator`'s module docstring for why sharing one `validate()` across
builds is exactly the bug this split exists to prevent: the moment either
build's report needs to diverge, only this file changes.
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
        return stages
