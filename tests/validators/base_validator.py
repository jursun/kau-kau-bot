"""Shared validator engine — the tracking and report-formatting machinery
every build's own validator is built from.

`BaseValidator` does all of the actual game-state tracking (`on_step`) and
report formatting. It has no opinion on which stages a report shows or what
Stage 4 is called — that decision belongs to each build's own, separate
validator class under `tests/validators/` (`FourRaxProxyValidator`,
`UpgradeRushValidator`, `SpeedlingAllInValidator`, and whatever comes next).
See `tests/validators/registry.py` for how a build's name maps to its
validator class, and any of the concrete per-build files (they're all
short) for what a new build actually has to add.

This split exists because of a real bug: `Four Rax Proxy`, `UpgradeRush` and
`Speedling All-In` used to all run through one shared class
(`tests.upgrade_rush_validator.UpgradeRushValidator`), and a change made
"for" one build's report (adding Stage 1B, renaming Stage 4) silently
changed the other two builds' reports too, since there was only one
`validate()` method to edit. With plans for up to a dozen builds per race,
the fix isn't "be more careful next time" — it's "make that impossible":
each build's `validate()` now lives in its own file, so a bug or a change in
Four Rax Proxy's report cannot touch UpgradeRush's. There is no shared
method left for a change to leak through.

Every check below still reads its thresholds and gates off `ai.ctx.build` —
worker/gas targets, pool deadline, wave1_min, wave_growth, the Evolution
Chamber target/gate, `wave_stage_label` — or off python-sc2/ares' own
tech-requirement tables, never a number hardcoded for one build by name.
That part of the original design was never the problem; only sharing one
`validate()` method across builds was.

Stages 2 and 3 additionally report *resource-blocked* time per milestone:
how many frames the bot was tech-eligible for that milestone (everything but
money was ready) yet unable to afford it, as opposed to genuinely not being
ready for it yet (still teching up, still building the prerequisite).

`BaseValidator` is composition, not a mixin. Construct one with the live
bot — ``BaseValidator(ai)``, or normally one of its subclasses — once
``ai.ctx`` is set; `run.py`'s `build_bot_ai` does this the moment
`KauKauBot.on_start` has picked an opening, since which validator applies
isn't knowable any earlier than that (ares' own `BuildOrderRunner` decides
the opening, and it can cycle between several builds game to game). Anything
this class doesn't define itself — `ai.time`, `ai.workers`,
`ai.structures(...)`, `ai.ctx`, `ai.mediator`, `ai.can_afford(...)`, and so
on — is read straight off the bot via `__getattr__`, so every tracking
method below reads exactly the way it always did back when this was a mixin
sharing `self` with the bot directly.

Usage, from `run.py`::

    from tests.validators.registry import validator_for_build

    validator_cls = validator_for_build(ai.ctx.build.name)
    validator = validator_cls(ai)
    # each frame:
    validator.on_step(iteration)
    # at game end:
    validator.on_end()
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

from ares.consts import WORKER_TYPES, UnitRole
from sc2.data import Race
from sc2.dicts.unit_research_abilities import RESEARCH_INFO
from sc2.dicts.upgrade_researched_from import UPGRADE_RESEARCHED_FROM
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

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
    UpgradeId.CHARGE: "Charge",
    UpgradeId.WARPGATERESEARCH: "Warp Gate",
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
    our_supply: float
    enemy_supply: float


@dataclass
class _CrewClaimTracker:
    """One of a `ProxyCrewPlan`'s three worker slots (x/y/z), tracked from
    `ctx.state.proxy_crew` by whether it has a tag yet."""

    member: str
    label: str
    started: bool = False
    started_time: Optional[float] = None


@dataclass
class _CrewTaskTracker:
    """One `WorkerTask` in a `ProxyCrewPlan` slot's list, tracked by watching
    that slot's `task_index`/`queued` in `ctx.state.proxy_crew` - the same
    tracker-membership signal `steps.terran.proxy_crew` itself uses to know
    "still working it" from "done", read here instead of re-derived."""

    member: str
    index: int
    label: str
    started: bool = False
    started_time: Optional[float] = None
    completed: bool = False
    completed_time: Optional[float] = None


# ── Validator engine ────────────────────────────────────────────────────────


class BaseValidator:
    """Tracks every build's shared milestones during a live game and prints
    the report at the end. See the module docstring for the composition
    model (`BaseValidator(ai)`) and for why `validate()` — which stages
    appear, and what Stage 4 is titled — belongs to a per-build subclass
    instead of living here.

    `BaseValidator.validate()` itself is not abstract: it's the safe,
    minimal fallback (Stage 1 + Stage 4 only) `registry.validator_for_build`
    returns for a build with no dedicated validator file yet, the same
    "don't let a name mismatch end a game" reasoning as
    `bot.core.registry.default_build`.
    """

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
    REPORT_TITLE: str = "VALIDATION REPORT"
    """Printed report header. Every concrete build validator sets its own —
    e.g. `FourRaxProxyValidator` sets "FOUR RAX PROXY VALIDATION REPORT" —
    so this generic default only ever shows up for a build still running on
    the `BaseValidator` fallback."""

    BUILD_NAME: Optional[str] = None
    """Set by a concrete subclass to the exact `BuildDefinition.name` it
    validates (e.g. "Four Rax Proxy") - `__init_subclass__` below uses it to
    self-register into `_registry`, which `registry.validator_for_build`
    reads. Left `None` on `BaseValidator` itself, which is why it never
    registers."""

    _registry: Dict[str, type] = {}

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        if cls.BUILD_NAME is None:
            return
        existing = BaseValidator._registry.get(cls.BUILD_NAME)
        if existing is not None and existing is not cls:
            raise ValueError(
                f"duplicate validator BUILD_NAME {cls.BUILD_NAME!r}: "
                f"{existing.__name__} and {cls.__name__} both claim it - "
                "each build gets exactly one validator class."
            )
        BaseValidator._registry[cls.BUILD_NAME] = cls

    def __init__(self, ai) -> None:
        self.ai = ai
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
        # Stage 1B
        self._crew_claims: List[_CrewClaimTracker] = []
        self._crew_tasks: List[_CrewTaskTracker] = []
        # Stage 2 / 3
        self._structures: List[_StructureTracker] = []
        self._upgrades: List[_UpgradeTracker] = []
        # Stage 4
        self._known_attacking_tags: set = set()
        self._last_wave_number: int = 0
        self._last_wave_time: Optional[float] = None
        self._next_wave_expected_min: int = 0
        self._waves: List[_WaveRecord] = []

        # `ai.ctx` is guaranteed set by the time a validator is constructed
        # (see the module docstring) - unlike the old mixin, which could be
        # asked to track before `KauKauBot.on_start` had run, there is no
        # "ctx might not exist yet" window to guard against here.
        self._init_milestones()
        self._init_crew()

    def __getattr__(self, name: str):
        """Delegate anything this object doesn't define itself to the live
        bot - `ai.time`, `ai.workers`, `ai.structures(...)`, `ai.ctx`, and
        so on. Only reached once normal lookup (this instance, then this
        class and its bases) finds nothing, so it never shadows this
        class's own state or methods."""
        return getattr(self.ai, name)

    def _init_milestones(self) -> None:
        """Build the tech-structure/upgrade tracker lists from the live
        build's own declared data — the upgrade list, and the Evolution
        Chamber target/gate (`ctx.build.army.evolution_chambers` /
        `.evolution_chamber_gate`). Nothing here is a number this module
        made up on its own.
        """
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

    def _init_crew(self) -> None:
        """Build the claim/task tracker lists from `ctx.build.crew` (a
        `ProxyCrewPlan`, or `None` for a build that doesn't have one) -
        nothing here is specific to `Four Rax Proxy` by name; any build that
        sets `crew` gets these checks, any build that doesn't gets the
        single "not declared" line `_validate_crew` falls back to.
        """
        plan = getattr(self.ctx.build, "crew", None)
        if plan is None:
            self._crew_claims = []
            self._crew_tasks = []
            return

        self._crew_claims = [
            _CrewClaimTracker("x", "Crew X claimed"),
            _CrewClaimTracker("y", "Crew Y claimed"),
            _CrewClaimTracker("z", "Crew Z claimed (13th SCV)"),
        ]
        self._crew_tasks = [
            _CrewTaskTracker(
                member, index, task.label or task.structure_id.name.title()
            )
            for member, tasks in (
                ("x", plan.x_tasks),
                ("y", plan.y_tasks),
                ("z", plan.z_tasks),
            )
            for index, task in enumerate(tasks)
        ]

    # ── Tracking (call once per frame) ────────────────────────────────────

    def on_step(self, iteration: int) -> None:
        """Track milestones from live game state. Call this every frame
        from whatever's driving the bot - `run.py`'s `ValidatedKauKauBot`
        calls it from its own `on_step`, right alongside `KauKauBot`'s."""
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
        if gas_count > self.ctx.build.economy.max_gas:
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

        # ── Stage 1B tracking ────────────────────────────────────────
        if self._crew_claims:
            self._track_crew()

        # ── Stage 2 / 3 tracking ─────────────────────────────────────
        if self._upgrades:
            self._track_upgrades()
        if self._structures:
            self._track_structures()

        # ── Stage 4 tracking ─────────────────────────────────────────
        self._track_waves()

    def on_end(self) -> None:
        """Print the validation report. Call this once, at game end."""
        self._print_report(self.validate())

    # ── Stage 1B tracking ───────────────────────────────────────────────

    def _track_crew(self) -> None:
        """Read `ctx.state.proxy_crew` for claims and task progress.

        A task's completion signal mirrors `steps.terran.proxy_crew`'s own:
        that slot's `task_index` moving past this task's position. Nothing
        here re-derives it from structure counts, for the identical reason
        that function's docstring gives - two crew workers can be building
        the same structure type at once.
        """
        crew_state = self.ctx.state.proxy_crew

        for claim in self._crew_claims:
            if claim.started:
                continue
            if getattr(crew_state, claim.member).tag is not None:
                claim.started = True
                claim.started_time = self.time

        for task in self._crew_tasks:
            if task.completed:
                continue
            member_state = getattr(crew_state, task.member)
            if not task.started and (
                member_state.task_index == task.index
                and member_state.queued
                or member_state.task_index > task.index
            ):
                task.started = True
                task.started_time = self.time
            if member_state.task_index > task.index:
                task.completed = True
                task.completed_time = self.time

    # ── Stage 2 / 3 tracking helpers ─────────────────────────────────────

    def _upgrades_before(self, upgrade: UpgradeId) -> List[UpgradeId]:
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

    def _enemy_army_supply(self) -> float:
        """Total supply of the enemy's known army (cached, so units that have
        gone back out of vision since still count) — mirrors ares' own
        `enemy_army_value` (`UnitCacheManager`), which sums resource cost the
        same way: everything in `get_cached_enemy_army` except workers,
        which that cache includes but doesn't itself filter out."""
        return sum(
            self.calculate_supply_cost(unit.type_id)
            for unit in self.mediator.get_cached_enemy_army
            if unit.type_id not in WORKER_TYPES
        )

    def _track_waves(self) -> None:
        ctx = self.ctx
        attacking_units = ctx.units_in_role(UnitRole.ATTACKING)
        current_attacking = {u.tag for u in attacking_units}
        wave_number = ctx.state.wave_number
        if wave_number > self._last_wave_number:
            new_tags = current_attacking - self._known_attacking_tags
            new_units = attacking_units.tags_in(new_tags)
            size = len(new_tags)
            gap = (
                None
                if self._last_wave_time is None
                else self.time - self._last_wave_time
            )
            our_supply = sum(
                self.calculate_supply_cost(unit.type_id) for unit in new_units
            )
            self._waves.append(
                _WaveRecord(
                    number=wave_number,
                    time=self.time,
                    size=size,
                    expected_min=self._next_wave_expected_min,
                    gap=gap,
                    our_supply=our_supply,
                    enemy_supply=self._enemy_army_supply(),
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
        """Evaluate this build's own milestones and return results grouped
        by stage.

        `BaseValidator`'s own version — Stage 1 and Stage 4 only, Stage 4
        titled from `ctx.build.combat.wave_stage_label` the same as every
        build — is the generic fallback for a build with no dedicated
        validator class yet (see `registry.validator_for_build`). A build
        with more to report than that (Proxy Crew Choreography, tech
        structures, upgrades) gets its own subclass overriding this method —
        see `FourRaxProxyValidator` / `UpgradeRushValidator` for the two
        existing shapes, and either as a template for a new build's own
        validator file.
        """
        stages: Dict[str, List[StepResult]] = {
            "Stage 1: Opening Economy": self._validate_economy(),
        }
        wave_label = self.ctx.build.combat.wave_stage_label
        stages[f"Stage 4: {wave_label}"] = self._validate_waves()
        return stages

    def _validate_economy(self) -> List[StepResult]:
        """Worker, gas and supply checks.

        Split into the race-neutral checks and the Zerg-specific ones, for
        the same reason gotchas 12 and 17 pushed the tech tree and the pool
        deadline onto the build: a check the build can never satisfy is
        noise, not a finding. A Terran or Protoss build has no Spawning Pool
        and no Extractor, so reporting "no pool" as a FAIL four lines running
        would bury the checks that do apply to it.
        """
        target = self.ctx.build.economy.worker_target
        max_gas = self.ctx.build.economy.max_gas
        pool_deadline = self.ctx.build.pool_deadline
        race = self.ctx.build.race

        results: List[StepResult] = [
            StepResult(
                "Workers Massed",
                self._max_workers >= target,
                f"max workers: {self._max_workers} (target {target})",
            ),
        ]

        if race == Race.Zerg:
            results += [
                StepResult(
                    "Workers Before Pool",
                    self._workers_at_pool_start >= 12,
                    f"workers at pool start: {self._workers_at_pool_start}",
                ),
                StepResult(
                    "Pool Timing",
                    self._pool_start_time is not None
                    and self._pool_start_time <= pool_deadline,
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
            ]

        results.append(
            StepResult(
                "Supply Management",
                self._supply_blocked_frames < 50,  # less than ~2s of block
                f"supply-blocked frames: {self._supply_blocked_frames}",
            )
        )
        return results

    def _validate_crew(self) -> List[StepResult]:
        """Proxy Crew Choreography (Stage 1B). Only meaningful for a build
        that declares `ctx.build.crew` — call this from a subclass's
        `validate()` only when that build actually has one (see
        `FourRaxProxyValidator`); the fallback line below exists purely for
        safety if it's ever called for a build that doesn't."""
        if not self._crew_claims and not self._crew_tasks:
            return [StepResult("No proxy crew declared by this build", True)]

        results = [
            StepResult(
                claim.label,
                claim.started,
                f"claimed at {claim.started_time:.1f}s" if claim.started else "never",
            )
            for claim in self._crew_claims
        ]
        for task in self._ordered_crew_tasks():
            if task.completed:
                detail = f"done at {task.completed_time:.1f}s"
            elif task.started:
                detail = f"in progress, started {task.started_time:.1f}s"
            else:
                detail = "not started"
            results.append(StepResult(task.label, task.completed, detail))
        return results

    def _ordered_crew_tasks(self) -> List[_CrewTaskTracker]:
        """`self._crew_tasks` in the order they actually finished this game,
        not the declared order (each crew member's own task list, x/y/z in
        turn) `_init_crew` built the list in.

        That declared order is a fine default while nothing has happened
        yet, but it stops meaning much once a gated task can finish after a
        later, ungated one - `Four Rax Proxy`'s Barracks D (X's second task,
        held until Marine training starts) regularly finishes after Depot
        (proxy) (Y's second task, ungated) despite the build order naming
        Barracks D first. Completed tasks sort by `completed_time`;
        anything not yet completed keeps its original declared position,
        trailing after everything that has finished.
        """
        return [
            task
            for _index, task in sorted(
                enumerate(self._crew_tasks),
                key=lambda pair: (
                    (0, pair[1].completed_time) if pair[1].completed else (1, pair[0])
                ),
            )
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
            detail += (
                f" | supply us={wave.our_supply:.0f} vs enemy={wave.enemy_supply:.0f}"
            )
            results.append(
                StepResult(f"Wave {wave.number}", size_ok and gap_ok, detail)
            )
        return results

    # ── Report formatting ───────────────────────────────────────────────

    def _print_report(self, stages: Dict[str, List[StepResult]]) -> None:
        """Print a human-readable validation report to stdout, titled from
        `self.REPORT_TITLE` — each concrete build validator's own."""
        total_passed = 0
        total_steps = 0

        width = 52
        print()
        print("═" * width)
        print(self.REPORT_TITLE)
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
