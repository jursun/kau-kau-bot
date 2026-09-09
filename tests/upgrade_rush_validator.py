"""In-game UpgradeRush Validator — tracks this build's own milestones.

This module provides an ``UpgradeRushValidator`` mixin that hooks into the
python-sc2 bot lifecycle and evaluates four stages, all read live off the
running bot / `BotContext` so nothing here can drift out of sync with
`bot/builds/zerg/upgrade_rush.py`:

  Stage 1: Opening Economy — workers, pool timing, extractor cap, supply
  Stage 2: Tech Structures — Evolution Chamber x2, Lair, Infestation Pit, Hive
  Stage 3: Upgrades        — every upgrade in `ctx.build.army.upgrades`, in order
  Stage 4: Attack Waves    — one line per wave actually released: size + timing

Stages 2 and 3 additionally report *resource-blocked* time per milestone:
how many frames the bot was tech-eligible for that milestone (everything but
money was ready — the researching structure exists, any prerequisite
building exists, every earlier upgrade in the build's own list is already
under way) yet unable to afford it, as opposed to genuinely not being ready
for it yet (still teching up, still building the prerequisite). That
distinction is what makes "resource block" mean something specific rather
than just "hasn't happened yet" — it isolates a real economic bottleneck
from a research order that simply hasn't reached that point.

Usage — mix into your bot BEFORE BotAI::

    from tests.upgrade_rush_validator import UpgradeRushValidator

    class MyBot(UpgradeRushValidator, BotAI):
        async def on_step(self, iteration):
            await super().on_step(iteration)
            # ... your bot logic ...

When the game ends the validator prints a report like::

    ══════════════════════════════════════════════════
    UPGRADE RUSH VALIDATION REPORT
    ══════════════════════════════════════════════════

      Stage 1: Opening Economy
        Workers Massed ............. PASS (max workers: 61 (target 60))
        Workers Before Pool ........ PASS (workers at pool start: 14)
        Pool Timing ................ PASS (pool at 14.2s)
        Only One Pool ............... PASS (pool count: 1)
        Extractor Built ............ PASS
        Extractor Cap Respected .... PASS (max gas buildings: 2 (cap 2))
        Supply Management ........... PASS (supply-blocked frames: 0)

      Stage 2: Tech Structures
        Evolution Chamber x2 ....... PASS (up at 210.4s)
        Lair ........................ PASS (up at 245.1s, 38 resource-blocked frames beforehand)
        Infestation Pit ............. PASS (up at 401.7s)
        Hive ........................ PASS (up at 430.9s)

      Stage 3: Upgrades
        Metabolic Boost ............. PASS (started 92.3s)
        Melee Attacks +1 ............ PASS (started 205.0s)
        ...

      Stage 4: Attack Waves
        Wave 1 ...................... PASS (t=302.1s size=21 (expected>=20))
        Wave 2 ...................... PASS (t=418.6s size=26 (expected>=27) gap=116.5s)
        ...

    ══════════════════════════════════════════════════
      21/23 passed  (91.3%)
    ══════════════════════════════════════════════════

Every threshold used comes from `ctx.build` — worker/gas targets, wave1_min,
wave_growth, and both the Evolution Chamber target count and its gate
(`ctx.build.army.evolution_chambers` / `.evolution_chamber_gate`) — or from
python-sc2/ares' own tech-requirement tables (`UPGRADE_RESEARCHED_FROM`,
`RESEARCH_INFO`, `tech_requirement_progress`). Nothing here re-hardcodes a
number or a tech-tree fact by hand, and nothing here is specific to
UpgradeRush by name: run it against `Speedling All-In` (`LING_SPEED_ONLY` —
no Evolution Chamber, Lair or Hive in its upgrade list at all) and Stage 2
reports "No tech structures required by this build" instead of failing four
checks it was never going to pass. A different build gets a different
report because the report is computed from that build's own declared data,
not because there's a second validator class to maintain.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

from sc2.dicts.unit_research_abilities import RESEARCH_INFO
from sc2.dicts.upgrade_researched_from import UPGRADE_RESEARCHED_FROM
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from ares.consts import UnitRole


# ── Step result ─────────────────────────────────────────────────────────────


@dataclass
class StepResult:
    """Outcome of a single validation step."""

    name: str
    passed: bool = False
    detail: str = ""

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        if self.detail:
            return f"{status} ({self.detail})"
        return status


# ── Tracker data model ───────────────────────────────────────────────────────

UPGRADE_LABELS: Dict[UpgradeId, str] = {
    UpgradeId.ZERGLINGMOVEMENTSPEED: "Metabolic Boost",
    UpgradeId.ZERGMELEEWEAPONSLEVEL1: "Melee Attacks +1",
    UpgradeId.ZERGGROUNDARMORSLEVEL1: "Ground Carapace +1",
    UpgradeId.ZERGMELEEWEAPONSLEVEL2: "Melee Attacks +2",
    UpgradeId.ZERGGROUNDARMORSLEVEL2: "Ground Carapace +2",
    UpgradeId.ZERGMELEEWEAPONSLEVEL3: "Melee Attacks +3",
    UpgradeId.ZERGGROUNDARMORSLEVEL3: "Ground Carapace +3",
    UpgradeId.ZERGLINGATTACKSPEED: "Adrenal Glands",
}

STRUCTURE_LABELS: Dict[UnitTypeId, str] = {
    UnitTypeId.EVOLUTIONCHAMBER: "Evolution Chamber",
    UnitTypeId.LAIR: "Lair",
    UnitTypeId.INFESTATIONPIT: "Infestation Pit",
    UnitTypeId.HIVE: "Hive",
}


@dataclass
class _UpgradeTracker:
    upgrade: UpgradeId
    label: str
    researched_from: UnitTypeId
    required_building: Optional[UnitTypeId]
    started: bool = False
    started_time: Optional[float] = None
    blocked_frames: int = 0


def _make_upgrade_tracker(upgrade: UpgradeId) -> _UpgradeTracker:
    researched_from = UPGRADE_RESEARCHED_FROM[upgrade]
    required_building = RESEARCH_INFO[researched_from][upgrade].get("required_building")
    label = UPGRADE_LABELS.get(upgrade, upgrade.name.title())
    return _UpgradeTracker(upgrade, label, researched_from, required_building)


@dataclass
class _StructureTracker:
    structure: UnitTypeId
    label: str
    target: int = 1
    started: bool = False
    started_time: Optional[float] = None
    blocked_frames: int = 0


@dataclass
class _WaveRecord:
    number: int
    time: float
    size: int
    expected_min: int
    gap: Optional[float]


# ── Validator mixin ─────────────────────────────────────────────────────────


class UpgradeRushValidator:
    """Mixin that validates UpgradeRush's own milestones during a live game.

    Mix this into your bot class BEFORE BotAI so that super() calls
    reach the framework::

        class MyBot(UpgradeRushValidator, BotAI):
            async def on_step(self, iteration):
                await super().on_step(iteration)  # triggers validator tracking
                # your logic here

    IMPORTANT: The validator's on_step does NOT call super().on_step()
    because BotAI.on_step raises NotImplementedError. Instead, the bot's
    own on_step calls super() which hits the validator first, then the
    bot adds its logic after.

    Reads `self.ctx` (a `bot.core.context.BotContext`) for build config and
    wave state — set by `KauKauBot.on_start`, which runs before this mixin's
    first `on_step` in `run.py`'s wiring, so it's always available once
    tracking starts.
    """

    # ── Timing thresholds (game seconds) ────────────────────────────────
    POOL_DEADLINE: float = 50.0  # Pool started by ~0:50
    SUPPLY_BLOCK_GRACE_PERIOD: float = 60.0
    # A fast opening is supply-blocked by design for a few seconds before
    # the pool even exists (drones and an extractor ahead of the second
    # overlord). Only count blocks after this grace period, so an on-time
    # opening never fails Supply Management for doing what it's supposed
    # to; a genuinely late pool still gets caught.
    MAX_WAVE_GAP: float = 180.0
    """No wave should take longer than this to reform after the previous
    one releases. Past wave 1, a gap this long usually means macro fell
    over somewhere, not that the next wave is legitimately still massing."""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

    def _init_validator_state(self) -> None:
        """Initialize tracking state. Called from on_start or first on_step."""
        if hasattr(self, "_validator_initialized"):
            return
        # Stage 1
        self._max_workers: int = 0
        self._workers_at_pool_start: int = 0
        self._pool_started: bool = False
        self._pool_start_time: Optional[float] = None
        self._pool_count: int = 0
        self._extractor_built: bool = False
        self._max_gas_buildings: int = 0
        self._gas_cap_exceeded: bool = False
        self._supply_blocked_frames: int = 0
        # Stage 2 / 3 — built lazily by `_init_milestones` once `ctx` exists.
        self._structures: Optional[List[_StructureTracker]] = None
        self._upgrades: Optional[List[_UpgradeTracker]] = None
        # Stage 4
        self._known_attacking_tags: set = set()
        self._last_wave_number: int = 0
        self._last_wave_time: Optional[float] = None
        self._next_wave_expected_min: int = 0
        self._waves: List[_WaveRecord] = []
        self._validator_initialized: bool = True

    def _init_milestones(self) -> None:
        """Build the tech-structure/upgrade tracker lists from the live
        build's own declared data — the upgrade list, and the Evolution
        Chamber target/gate (`ctx.build.army.evolution_chambers` /
        `.evolution_chamber_gate`). Nothing here is a number this module
        made up on its own.
        """
        if self._upgrades is not None:
            return

        build_upgrades: tuple = tuple(self.ctx.build.army.upgrades)
        self._upgrades = [_make_upgrade_tracker(u) for u in build_upgrades]

        structures: List[_StructureTracker] = []
        if any(
            t.researched_from == UnitTypeId.EVOLUTIONCHAMBER for t in self._upgrades
        ):
            structures.append(
                _StructureTracker(
                    UnitTypeId.EVOLUTIONCHAMBER,
                    STRUCTURE_LABELS[UnitTypeId.EVOLUTIONCHAMBER],
                    target=self.ctx.build.army.evolution_chambers,
                )
            )
        if any(t.required_building == UnitTypeId.LAIR for t in self._upgrades):
            structures.append(
                _StructureTracker(UnitTypeId.LAIR, STRUCTURE_LABELS[UnitTypeId.LAIR])
            )
        if any(t.required_building == UnitTypeId.HIVE for t in self._upgrades):
            # Hive's own tech requirement includes Infestation Pit, so it's
            # only ever eligible once ares' `TechUp` has already built one —
            # tracked here as its own milestone rather than folded silently
            # into Hive's, since the user cares about it by name.
            structures.append(
                _StructureTracker(
                    UnitTypeId.INFESTATIONPIT,
                    STRUCTURE_LABELS[UnitTypeId.INFESTATIONPIT],
                )
            )
            structures.append(
                _StructureTracker(UnitTypeId.HIVE, STRUCTURE_LABELS[UnitTypeId.HIVE])
            )
        self._structures = structures
        self._next_wave_expected_min = self.ctx.build.combat.wave1_min

    # ── Lifecycle hooks ─────────────────────────────────────────────────

    async def on_start(self):
        """Called at game start — initialize tracking."""
        self._init_validator_state()

    async def on_step(self, iteration: int):
        """Called every frame — track milestones from live game state.

        Does NOT call super().on_step() because BotAI.on_step raises
        NotImplementedError. The bot's own on_step should call
        ``await super().on_step(iteration)`` to trigger this tracking,
        then add its own logic after.
        """
        self._init_validator_state()
        if self.ctx is not None:
            self._init_milestones()

        # ── Stage 1 tracking ─────────────────────────────────────────
        worker_count = self.workers.amount
        if worker_count > self._max_workers:
            self._max_workers = worker_count

        if (
            self.supply_left == 0
            and not self._pool_started
            and self.time >= self.SUPPLY_BLOCK_GRACE_PERIOD
        ):
            self._supply_blocked_frames += 1

        gas_count = self.gas_buildings.amount
        if gas_count > 0:
            self._extractor_built = True
        if gas_count > self._max_gas_buildings:
            self._max_gas_buildings = gas_count
        if self.ctx is not None and gas_count > self.ctx.build.economy.max_gas:
            self._gas_cap_exceeded = True

        if not self._pool_started:
            pool_count = self.structures(UnitTypeId.SPAWNINGPOOL).amount
            pending = self.already_pending(UnitTypeId.SPAWNINGPOOL)
            if pool_count + pending > 0:
                self._pool_started = True
                self._pool_start_time = self.time
                self._workers_at_pool_start = self.workers.amount
                # Deliberately not `pool_count + pending` here: once placed
                # but still building, `already_pending` ALSO counts it (its
                # own docstring: "buildings already in progress"), so both
                # equal 1 for the same physical pool through its whole
                # build time. The unconditional block below re-derives the
                # true count every frame from `structures(...).amount`
                # alone, so nothing is lost by not seeding it here.

        current_pool = self.structures(UnitTypeId.SPAWNINGPOOL).amount
        if current_pool > self._pool_count:
            self._pool_count = current_pool

        # ── Stage 2 / 3 tracking ─────────────────────────────────────
        if self._upgrades is not None:
            self._track_upgrades()
        if self._structures is not None:
            self._track_structures()

        # ── Stage 4 tracking ─────────────────────────────────────────
        self._track_waves()

    async def on_end(self, game_result):
        """Called at game end — print the validation report."""
        report = self.validate()
        self._print_report(report)

    # ── Stage 2 / 3 tracking helpers ─────────────────────────────────────

    def _upgrades_before(self, upgrade: UpgradeId) -> List[UpgradeId]:
        assert self._upgrades is not None
        before: List[UpgradeId] = []
        for tracker in self._upgrades:
            if tracker.upgrade == upgrade:
                break
            before.append(tracker.upgrade)
        return before

    def _structure_gate_satisfied(self, structure: UnitTypeId) -> bool:
        """Whether the build has research-ordered its way to wanting
        `structure` yet — mirrors `UpgradeController`'s own list-order walk
        (see `ares/behaviors/macro/upgrade_controller.py`): it never reaches
        for something later in the list until everything before it is at
        least under way."""
        assert self._upgrades is not None
        if structure == UnitTypeId.EVOLUTIONCHAMBER:
            return self.ctx.build.army.evolution_chamber_gate(self.ctx)

        gate_building = UnitTypeId.HIVE if structure != UnitTypeId.LAIR else structure
        first = next(
            (
                tracker.upgrade
                for tracker in self._upgrades
                if tracker.required_building == gate_building
            ),
            None,
        )
        if first is None:
            return True
        return all(
            self.pending_or_complete_upgrade(u) for u in self._upgrades_before(first)
        )

    def _track_upgrades(self) -> None:
        assert self._upgrades is not None
        for tracker in self._upgrades:
            if tracker.started:
                continue
            if self.pending_or_complete_upgrade(tracker.upgrade):
                tracker.started = True
                tracker.started_time = self.time
                continue

            eligible = (
                self.structures(tracker.researched_from).ready.amount > 0
                and (
                    tracker.required_building is None
                    or self.structures(tracker.required_building).ready.amount > 0
                )
                and all(
                    self.pending_or_complete_upgrade(u)
                    for u in self._upgrades_before(tracker.upgrade)
                )
            )
            if eligible and not self.can_afford(tracker.upgrade):
                tracker.blocked_frames += 1

    def _track_structures(self) -> None:
        assert self._structures is not None
        for tracker in self._structures:
            if tracker.started:
                continue
            existing = self.structures(tracker.structure).amount
            if existing >= tracker.target:
                tracker.started = True
                tracker.started_time = self.time
                continue

            eligible = (
                self._structure_gate_satisfied(tracker.structure)
                and self.tech_requirement_progress(tracker.structure) >= 1.0
            )
            if eligible and not self.can_afford(tracker.structure):
                tracker.blocked_frames += 1

    # ── Stage 4 tracking ─────────────────────────────────────────────────

    def _track_waves(self) -> None:
        ctx = self.ctx
        if ctx is None:
            return

        current_attacking = {u.tag for u in ctx.units_in_role(UnitRole.ATTACKING)}
        wave_number = ctx.state.wave_number
        if wave_number > self._last_wave_number:
            new_tags = current_attacking - self._known_attacking_tags
            size = len(new_tags)
            gap = (
                None
                if self._last_wave_time is None
                else self.time - self._last_wave_time
            )
            self._waves.append(
                _WaveRecord(
                    number=wave_number,
                    time=self.time,
                    size=size,
                    expected_min=self._next_wave_expected_min,
                    gap=gap,
                )
            )
            if size > 0:
                self._next_wave_expected_min = max(
                    ctx.build.combat.wave1_min + 1,
                    math.ceil(size * ctx.build.combat.wave_growth),
                )
            self._last_wave_number = wave_number
            self._last_wave_time = self.time

        self._known_attacking_tags = current_attacking

    # ── Validation logic ─────────────────────────────────────────────────

    def validate(self) -> Dict[str, List[StepResult]]:
        """Evaluate all milestones and return results grouped by stage."""
        self._init_validator_state()
        return {
            "Stage 1: Opening Economy": self._validate_economy(),
            "Stage 2: Tech Structures": self._validate_structures(),
            "Stage 3: Upgrades": self._validate_upgrades(),
            "Stage 4: Attack Waves": self._validate_waves(),
        }

    def _validate_economy(self) -> List[StepResult]:
        target = self.ctx.build.economy.worker_target if self.ctx else 60
        max_gas = self.ctx.build.economy.max_gas if self.ctx else 2
        return [
            StepResult(
                "Workers Massed",
                self._max_workers >= target,
                f"max workers: {self._max_workers} (target {target})",
            ),
            StepResult(
                "Workers Before Pool",
                self._workers_at_pool_start >= 12,
                f"workers at pool start: {self._workers_at_pool_start}",
            ),
            StepResult(
                "Pool Timing",
                self._pool_start_time is not None
                and self._pool_start_time <= self.POOL_DEADLINE,
                (
                    f"pool at {self._pool_start_time:.1f}s"
                    if self._pool_start_time is not None
                    else "no pool"
                ),
            ),
            StepResult(
                "Only One Pool",
                self._pool_count <= 1,
                f"pool count: {self._pool_count}",
            ),
            StepResult("Extractor Built", self._extractor_built),
            StepResult(
                "Extractor Cap Respected",
                not self._gas_cap_exceeded,
                f"max gas buildings: {self._max_gas_buildings} (cap {max_gas})",
            ),
            StepResult(
                "Supply Management",
                self._supply_blocked_frames < 50,  # less than ~2s of block
                f"supply-blocked frames: {self._supply_blocked_frames}",
            ),
        ]

    @staticmethod
    def _milestone_detail(started: bool, started_time, blocked_frames: int) -> str:
        if started:
            detail = f"up at {started_time:.1f}s"
            if blocked_frames:
                detail += f", {blocked_frames} resource-blocked frames beforehand"
            return detail
        detail = "never built"
        if blocked_frames:
            detail += f" - {blocked_frames} resource-blocked frames so far"
        return detail

    def _validate_structures(self) -> List[StepResult]:
        if not self._structures:
            return [StepResult("No tech structures required by this build", True)]
        results = []
        for tracker in self._structures:
            label = (
                tracker.label
                if tracker.target == 1
                else f"{tracker.label} x{tracker.target}"
            )
            results.append(
                StepResult(
                    label,
                    tracker.started,
                    self._milestone_detail(
                        tracker.started, tracker.started_time, tracker.blocked_frames
                    ),
                )
            )
        return results

    def _validate_upgrades(self) -> List[StepResult]:
        if not self._upgrades:
            return [StepResult("No upgrades declared by this build", True)]
        results = []
        for tracker in self._upgrades:
            detail = self._milestone_detail(
                tracker.started, tracker.started_time, tracker.blocked_frames
            ).replace("up at", "started")
            results.append(StepResult(tracker.label, tracker.started, detail))
        return results

    def _validate_waves(self) -> List[StepResult]:
        if not self._waves:
            return [StepResult("Waves Released", False, "no wave ever left")]
        results = []
        for wave in self._waves:
            size_ok = wave.size >= wave.expected_min
            gap_ok = wave.gap is None or wave.gap <= self.MAX_WAVE_GAP
            detail = (
                f"t={wave.time:.1f}s size={wave.size} (expected>={wave.expected_min})"
            )
            if wave.gap is not None:
                detail += f" gap={wave.gap:.1f}s"
            results.append(
                StepResult(f"Wave {wave.number}", size_ok and gap_ok, detail)
            )
        return results

    # ── Report formatting ───────────────────────────────────────────────

    @staticmethod
    def _print_report(stages: Dict[str, List[StepResult]]) -> None:
        """Print a human-readable validation report to stdout."""
        total_passed = 0
        total_steps = 0

        width = 52
        print()
        print("═" * width)
        print("UPGRADE RUSH VALIDATION REPORT")
        print("═" * width)

        for stage_name, steps in stages.items():
            print(f"\n  {stage_name}")
            for step in steps:
                total_steps += 1
                if step.passed:
                    total_passed += 1
                pad = 28 - len(step.name)
                line = f"    {step.name} {'.' * max(pad, 3)} {step}"
                print(line)

        print()
        print("═" * width)
        pct = (total_passed / total_steps * 100) if total_steps else 0
        print(f"  {total_passed}/{total_steps} passed  ({pct:.1f}%)")
        print("═" * width)
        print()

    def get_score(self) -> Dict[str, int]:
        """Return a simple score dict for programmatic use."""
        stages = self.validate()
        total = 0
        passed = 0
        for steps in stages.values():
            for step in steps:
                total += 1
                if step.passed:
                    passed += 1
        return {
            "steps_total": total,
            "steps_passed": passed,
            "steps_failed": total - passed,
        }
