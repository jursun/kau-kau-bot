"""`Macro Zerg`'s own Validation Report shape.

Six-stage report: economy, opening timing, tech structures, upgrades,
waves, combat QA. No Stage 1B: this build has no `ProxyCrewPlan`.

`_init_milestones` needs its own override here, unlike most builds:
`BaseValidator`'s own implementation only auto-derives Stage 3's structure
trackers from `ctx.build.army.upgrades` (Evolution Chamber, Lair,
Infestation Pit, Hive - gated on whether an upgrade is researched from, or
requires, one of those). Macro Zerg's own upgrade list (Glial
Reconstitution, Burrow, Tunneling Claws) doesn't trigger any of that,
despite the build's whole identity depending on Roach Warren, Lair,
Infestation Pit and Spire - so those four are added by hand instead of
coming along for free the way `UpgradeRushValidator` (mass-ling upgrades,
which do trip the Lair/Hive auto-derivation) used to get away with a bare
`validate()` override.

Stage 2 (Opening Timing) is a from-scratch addition specific to this
build's exact opening (see `zerg_builds.yml`'s `Macro Zerg` entry): a
"must start by real game-time X" deadline for each of its 14 opening
milestones, both economy and race-timing (unlike `pool_deadline`, which
is a generic, deliberately loose safety net shared by every Zerg build,
these are precise regression numbers for THIS opening's own intended
pace). Nothing here is reusable by another build, so it lives entirely
in this file rather than `base_validator.py` - `_TimingMilestone` is a
much simpler tracker than `_StructureTracker`/`_UpgradeTracker` (no
resource-blocked-frames accounting; the constant timestamps make plain
"did it start late" reporting all that's needed) and deliberately doesn't
try to generalize across builds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from tests.validators.base_validator import (
    STRUCTURE_LABELS,
    BaseValidator,
    StepResult,
    _StructureTracker,
)


@dataclass
class _TimingMilestone:
    label: str
    deadline: float
    started: bool = False
    started_time: Optional[float] = None


class MacroZergValidator(BaseValidator):
    BUILD_NAME = "Macro Zerg"
    REPORT_TITLE = "MACRO ZERG VALIDATION REPORT"

    MAIN_QUEEN_RADIUS: float = 15.0
    """How close a Queen must sit to `start_location` to count as "at the
    main" for the Stage 2 "3rd Queen @ Main" check - matches the "close
    enough to belong to this base" radius `steps.zerg.spore_crawlers`
    already uses (`ai.EXPANSION_GAP_THRESHOLD`, 15) for the same kind of
    "which base is this near" question."""

    # (internal key, label, deadline in seconds) - order matches the spec
    # this was built from, and is what Stage 2 reports in.
    _OPENING_TIMING_SPEC: tuple[tuple[str, str, float], ...] = (
        ("overlord2", "Overlord", 13.0),
        ("natural_hatch", "Natural Hatchery", 49.0),
        ("gas1", "Gas", 69.0),
        ("pool", "Spawning Pool", 75.0),
        ("queens2", "2 Queens", 122.0),
        ("zerglings4", "4 Zergling", 123.0),
        ("hatch3", "3rd base Hatchery", 136.0),
        ("gas_off", "3 Drone off gas", 137.0),
        ("queen3_main", "3rd Queen @ Main", 163.0),
        ("speed", "Speed", 167.0),
        ("overlord4", "4th Overlord", 179.0),
        ("gas_on", "3 Drone on gas", 215.0),
        ("roach_warren", "Roach Warren", 217.0),
        ("gas2", "2nd Gas", 240.0),
    )

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

        self._opening_timing: Dict[str, _TimingMilestone] = {
            key: _TimingMilestone(label, deadline)
            for key, label, deadline in self._OPENING_TIMING_SPEC
        }
        self._gas_ever_saturated: bool = False
        """Latched once 3+ workers have ever been seen on gas at once - the
        "3 Drone off gas" milestone only means anything once there was
        something to pull off in the first place."""

    def on_step(self, iteration: int) -> None:
        super().on_step(iteration)
        self._track_opening_timing()

    def _track_opening_timing(self) -> None:
        m = self._opening_timing

        def latch(key: str, condition: bool) -> None:
            tracker = m[key]
            if not tracker.started and condition:
                tracker.started = True
                tracker.started_time = self.time

        overlords = self.units(UnitTypeId.OVERLORD).amount + self.already_pending(
            UnitTypeId.OVERLORD
        )
        latch("overlord2", overlords >= 2)
        latch("overlord4", overlords >= 4)

        hatcheries = self.structures(UnitTypeId.HATCHERY).amount
        latch("natural_hatch", hatcheries >= 2)
        latch("hatch3", hatcheries >= 3)

        latch("gas1", self.gas_buildings.amount >= 1)
        latch("gas2", self.gas_buildings.amount >= 2)

        latch("pool", self.structures(UnitTypeId.SPAWNINGPOOL).amount >= 1)

        queens = self.units(UnitTypeId.QUEEN)
        queen_count = queens.amount + self.already_pending(UnitTypeId.QUEEN)
        latch("queens2", queen_count >= 2)
        at_main = any(
            queen.position.distance_to(self.start_location) <= self.MAIN_QUEEN_RADIUS
            for queen in queens
        )
        latch("queen3_main", queen_count >= 3 and at_main)

        zerglings = self.units(UnitTypeId.ZERGLING).amount + self.already_pending(
            UnitTypeId.ZERGLING
        )
        latch("zerglings4", zerglings >= 4)

        latch("roach_warren", self.structures(UnitTypeId.ROACHWARREN).amount >= 1)

        latch("speed", self.pending_or_complete_upgrade(UpgradeId.ZERGLINGMOVEMENTSPEED))

        # Gas pull-off/resume (see steps.common.gas_workers's `pull_off` on
        # this build): track total assigned harvesters across every gas
        # building. "off" only means something once gas was ever actually
        # staffed; "on" (resuming after the bank got spent back down) only
        # means something once "off" already happened.
        gas_assigned = sum(g.assigned_harvesters for g in self.gas_buildings)
        if gas_assigned >= 3:
            self._gas_ever_saturated = True
        if self._gas_ever_saturated:
            latch("gas_off", gas_assigned == 0)
        if m["gas_off"].started:
            latch("gas_on", gas_assigned >= 3)

    def _validate_opening_timing(self) -> List[StepResult]:
        results: List[StepResult] = []
        for key, label, deadline in self._OPENING_TIMING_SPEC:
            tracker = self._opening_timing[key]
            if tracker.started:
                on_time = tracker.started_time <= deadline
                detail = f"started at {tracker.started_time:.1f}s (deadline {deadline:.0f}s)"
            else:
                on_time = False
                detail = f"never happened (deadline {deadline:.0f}s)"
            results.append(StepResult(label, on_time, detail))
        return results

    def validate(self) -> Dict[str, List[StepResult]]:
        stages: Dict[str, List[StepResult]] = {
            "Stage 1: Opening Economy": self._validate_economy(),
            "Stage 2: Opening Timing": self._validate_opening_timing(),
            "Stage 3: Tech Structures": self._validate_structures(),
            "Stage 4: Upgrades": self._validate_upgrades(),
        }
        wave_label = self.ctx.build.combat.wave_stage_label
        stages[f"Stage 5: {wave_label}"] = self._validate_waves()
        stages["Stage 6: Combat QA"] = self._validate_combat_qa()
        return stages
