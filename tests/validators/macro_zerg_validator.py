"""`Macro Zerg`'s own Validation Report shape.

Full four-stage report (economy, tech structures, upgrades, waves) off this
build's own declared data, plus Stage 5 combat QA. No Stage 1B: this build
has no `ProxyCrewPlan`.

`_init_milestones` needs its own override here, unlike most builds:
`BaseValidator`'s own implementation only auto-derives Stage 2's structure
trackers from `ctx.build.army.upgrades` (Evolution Chamber, Lair,
Infestation Pit, Hive - gated on whether an upgrade is researched from, or
requires, one of those). Macro Zerg's own upgrade list (Glial
Reconstitution, Burrow, Tunneling Claws) doesn't trigger any of that,
despite the build's whole identity depending on Roach Warren, Lair,
Infestation Pit and Spire - so those four are added by hand instead of
coming along for free the way `UpgradeRushValidator` (mass-ling upgrades,
which do trip the Lair/Hive auto-derivation) used to get away with a bare
`validate()` override.
"""

from __future__ import annotations

from typing import Dict, List

from sc2.ids.unit_typeid import UnitTypeId

from tests.validators.base_validator import (
    STRUCTURE_LABELS,
    BaseValidator,
    StepResult,
    _StructureTracker,
)


class MacroZergValidator(BaseValidator):
    BUILD_NAME = "Macro Zerg"
    REPORT_TITLE = "MACRO ZERG VALIDATION REPORT"

    def _init_milestones(self) -> None:
        super()._init_milestones()
        # Tunneling Claws already requires Lair, so the base class's own
        # upgrade-driven derivation adds a Lair tracker on its own - guard
        # against a duplicate rather than assume which of this build's
        # upgrades will or won't trip that derivation.
        already = {tracker.structure for tracker in self._structures}
        self._structures.extend(
            _StructureTracker(structure, STRUCTURE_LABELS[structure])
            for structure in (
                UnitTypeId.ROACHWARREN,
                UnitTypeId.LAIR,
                UnitTypeId.INFESTATIONPIT,
                UnitTypeId.SPIRE,
            )
            if structure not in already
        )

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
