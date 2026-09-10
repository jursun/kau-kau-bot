"""`Four Rax Proxy`'s own Validation Report shape.

Stage 1 (opening economy) plus this build's Proxy Crew Choreography
(Stage 1B — every claim and every `WorkerTask` X/Y/Z complete, in the order
they actually finished; see `base_validator.BaseValidator._validate_crew`
for the tracking), and this build's own "All-In Attack" title for Stage 4
(`bot.builds.terran.four_rax_proxy.BUILD.combat.wave_stage_label`).

No Stage 2/3: this build declares no upgrades at all
(`BUILD.army.upgrades == ()`), so there is nothing for either stage to
check, and this file simply never adds those keys to the report.
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
        return stages
