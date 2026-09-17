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
build's exact opening (see `bot.builds.zerg.macro_zerg`'s `_SEQUENCE` -
the opening is scripted directly in Python there, not in a YAML build
order): a "must start by real game-time X" deadline for each of its 14
opening milestones, both economy and race-timing (unlike `pool_deadline`,
which is a generic, deliberately loose safety net shared by every Zerg
build, these are precise regression numbers matching the exact supply/
time build order `macro_zerg.py` implements). Nothing here is reusable by
another build, so it lives entirely in this file rather than
`base_validator.py` - `_TimingMilestone` is a much simpler tracker than
`_StructureTracker`/`_UpgradeTracker` (no resource-blocked-frames
accounting; the constant timestamps make plain "did it start late"
reporting all that's needed) and deliberately doesn't try to generalize
across builds.

Queen has no timing milestone here (it used to: "2 Queens"/"3rd Queen @
Main"): `macro_zerg.py` no longer scripts Queen production to a fixed
supply/time at all - it's an always-on "1 per ready townhall, plus 1
spare" rule with no fixed target to regress against, so a deadline-based
check would just be measuring townhall-readiness variance, not the build.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from bot.behaviors.zerg.train_from_larva import pending_larva_trained
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

    # (internal key, label, deadline in seconds) - matches
    # `bot.builds.zerg.macro_zerg._SEQUENCE` and its "additional timings"
    # exactly (0:12, 0:50, 1:00, 1:15, 2:03, 2:05, 2:17, 2:47, 2:59, 3:32,
    # 3:35, 4:05, 4:16, 4:30 spores - `gas_off`/`gas_on` come from the
    # gas-worker-count timings, not the production sequence itself),
    # and is what Stage 2 reports in. Natural/Gas/gas-off were tightened
    # after smoke showed consistent ~5-15s headroom on the prior 54/70/135
    # windows. Spores open at 4:30 with a 15s missing-base recheck, so the
    # third crawler is allowed through ~5:30.
    _OPENING_TIMING_SPEC: tuple[tuple[str, str, float], ...] = (
        ("overlord2", "Overlord", 12.0),
        ("natural_hatch", "Natural Hatchery", 50.0),
        ("gas1", "Gas", 60.0),
        ("pool", "Spawning Pool", 75.0),
        ("zerglings4", "4 Zergling", 123.0),
        ("hatch3", "3rd base Hatchery", 137.0),
        ("gas_off", "3 Drone off gas", 125.0),
        ("speed", "Speed", 167.0),
        ("overlord4", "4th Overlord", 179.0),
        ("gas_on", "3 Drone on gas", 212.0),
        ("roach_warren", "Roach Warren", 215.0),
        ("gas2", "2nd Gas", 245.0),
        ("lair", "Lair", 256.0),
        ("spores3", "3 Spore Crawlers", 330.0),
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

        # Count individual Zerglings, not Eggs: `already_pending(ZERGLING)`
        # is 1 per Egg, but each Egg yields 2 Lings. Same correction the
        # opening itself uses (`pending_larva_trained`) - without it, Stage
        # 2 latched only when both Eggs hatched (~+17s late) even though
        # both morphs were commanded on time (confirmed live: yield hit 4
        # at 121.5s / deadline 123s; raw egg-count hit 4 at 138.6s).
        zerglings = self.units(UnitTypeId.ZERGLING).amount + pending_larva_trained(
            self, UnitTypeId.ZERGLING
        )
        latch("zerglings4", zerglings >= 4)

        latch("roach_warren", self.structures(UnitTypeId.ROACHWARREN).amount >= 1)

        # Lair is a Hatchery morph: `structures(LAIR)` stays 0 until the
        # ~57s morph finishes, so latching on the structure alone reported
        # "started at 309s" when the morph was commanded at ~252s (deadline
        # 256s). Match the opening's own "commanded" signal - pending or a
        # live UPGRADETOLAIR order on a townhall.
        lair_started = (
            self.structures(UnitTypeId.LAIR).amount >= 1
            or self.already_pending(UnitTypeId.LAIR) > 0
            or any(
                any(o.ability.id.name == "UPGRADETOLAIR_LAIR" for o in th.orders)
                for th in self.townhalls
            )
        )
        latch("lair", lair_started)

        # One per base (`steps.zerg.spore_crawlers(per_base=1)`), maintenance
        # opens at 4:30 — "3" is this opening's own base count by then
        # (main, natural, 3rd), not a generic per-base check the way the
        # structure count in Stage 3 is.
        latch("spores3", self.structures(UnitTypeId.SPORECRAWLER).amount >= 3)

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
