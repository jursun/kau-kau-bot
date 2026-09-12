"""`Four Rax Proxy`'s own Validation Report shape.

Stage 1 (opening economy) + Stage 1B (Proxy Crew Choreography, see
`base_validator.BaseValidator._validate_crew`) + Stage 4 ("All-In Attack").
No Stage 2/3: this build declares no upgrades (`BUILD.army.upgrades == ()`).
"""

from __future__ import annotations

from typing import Dict, List

from tests.validators.base_validator import BaseValidator, StepResult


class FourRaxProxyValidator(BaseValidator):
    BUILD_NAME = "Four Rax Proxy"
    REPORT_TITLE = "FOUR RAX PROXY VALIDATION REPORT"

    def validate(self) -> Dict[str, List[StepResult]]:
        stages: Dict[str, List[StepResult]] = {
            "Stage 1: Opening Economy": self._validate_economy(),
            "Stage 1B: Proxy Crew Choreography": self._validate_crew(),
        }
        wave_label = self.ctx.build.combat.wave_stage_label
        stages[f"Stage 4: {wave_label}"] = self._validate_waves()
        stages["Stage 5: Combat QA"] = self._validate_combat_qa()
        return stages
