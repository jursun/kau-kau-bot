"""In-game UpgradeRush Validator — tracks this build's own milestones.

This module provides an ``UpgradeRushValidator`` mixin that hooks into the
python-sc2 bot lifecycle and evaluates a handful of stages, all read live off
the running bot / `BotContext` so nothing here can drift out of sync with
`bot/builds/zerg/upgrade_rush.py` or, for Stage 1B, any build declaring a
`bot.builds.definition.ProxyCrewPlan`:

  Stage 1: Opening Economy — workers, pool timing, extractor cap, supply
  Stage 1B: Proxy Crew     — for a build with `ctx.build.crew` set (e.g.
                             `Four Rax Proxy`): when X/Y/Z were claimed, then
                             one line per declared task (every Depot and
                             Barracks the crew places), in the order they
                             actually finished this game - not the order
                             each sits in its own crew member's list, since a
                             gated task (e.g. Barracks D, held until Marine
                             training starts) can easily finish after a
                             later, ungated one
  Stage 2: Tech Structures — Evolution Chamber x2, Lair, Infestation Pit, Hive
                             - omitted entirely for a build with no upgrades
                             declared at all (e.g. `Four Rax Proxy`), rather
                             than a placeholder line
  Stage 3: Upgrades        — every upgrade in `ctx.build.army.upgrades`, in
                             order - omitted under the same condition as
                             Stage 2, for the same build
  Stage 4: All-In Attack   — one line per wave actually released: size, timing,
                             and our supply released vs the enemy's known army
                             supply at that moment

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

When the game ends the validator prints a report like this, for a build
with tech structures and upgrades declared (e.g. `UpgradeRush`)::

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
        Lair ........................ PASS (up at 245.1s, 38 blocked frames)
        Infestation Pit ............. PASS (up at 401.7s)
        Hive ........................ PASS (up at 430.9s)

      Stage 3: Upgrades
        Metabolic Boost ............. PASS (started 92.3s)
        Melee Attacks +1 ............ PASS (started 205.0s)
        ...

      Stage 4: All-In Attack
        Wave 1 ...................... PASS (t=302.1s size=21 (expected>=20))
        Wave 2 ...................... PASS (t=418.6s size=26 (expected>=27))
        ...

    ══════════════════════════════════════════════════
      21/23 passed  (91.3%)
    ══════════════════════════════════════════════════

For a build with `crew` set and no upgrades declared at all (`Four Rax
Proxy`), Stage 1B replaces Stage 2/3 rather than sitting alongside a
placeholder for them - the report goes straight from Stage 1 to Stage 1B to
Stage 4::

      Stage 1B: Proxy Crew Choreography
        Crew X claimed .............. PASS (claimed at 0.6s)
        Crew Y claimed .............. PASS (claimed at 0.6s)
        Crew Z claimed (13th SCV) ... PASS (claimed at 12.7s)
        Depot (home) ................ PASS (done at 40.2s)
        Barracks A .................. PASS (done at 86.4s)
        Barracks B .................. PASS (done at 93.4s)
        Barracks C .................. PASS (done at 118.9s)
        Depot (proxy) ............... PASS (done at 122.1s)
        Barracks D .................. PASS (done at 135.0s)

Note Barracks D lands *after* Depot (proxy) here even though the build
order names it first (step 8 vs step 9) - Barracks D is gated on Marine
training having started, Depot (proxy) isn't, so which one actually
finishes first is a live game outcome, not something fixed at declaration
time. The six task lines are ordered by when they actually completed for
exactly that reason - see `_validate_crew`.

Every threshold used comes from `ctx.build` — worker/gas targets, pool
deadline, wave1_min, wave_growth, and both the Evolution Chamber target
count and its gate (`ctx.build.army.evolution_chambers` /
`.evolution_chamber_gate`) — or from
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
    POOL_DEADLINE: float = 50.0
    """Fallback only, used when `self.ctx` isn't set yet. The real deadline
    is `ctx.build.pool_deadline` — each build declares its own, since a
    hatch-before-pool opening (e.g. `UpgradeRush`) has a genuinely later,
    by-design pool time than an immediate-pool one (e.g. `Speedling
    All-In`), and a single shared constant here can't reflect both."""
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
        # Stage 1B — built lazily by `_init_crew` once `ctx` exists.
        self._crew_claims: Optional[List[_CrewClaimTracker]] = None
        self._crew_tasks: Optional[List[_CrewTaskTracker]] = None
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

    def _init_crew(self) -> None:
        """Build the claim/task tracker lists from `ctx.build.crew` (a
        `ProxyCrewPlan`, or `None` for a build that doesn't have one) -
        nothing here is specific to `Four Rax Proxy` by name; any build that
        sets `crew` gets these checks, any build that doesn't gets the single
        "not declared" line `_validate_crew` falls back to.
        """
        if self._crew_claims is not None:
            return

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
            self._init_crew()

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

        # ── Stage 1B tracking ────────────────────────────────────────
        if self._crew_claims:
            self._track_crew()

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
        if ctx is None:
            return

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
        """Evaluate all milestones and return results grouped by stage.

        Stage 2 and Stage 3 are omitted entirely - not included with a
        placeholder line - for a build with no upgrades declared at all
        (`ctx.build.army.upgrades` empty, so `self._upgrades` is too): tech
        structures only ever exist in this report because some upgrade in
        the build's own list requires one (see `_init_milestones`), so no
        upgrades declared means no structures either, and a Marine all-in
        like `Four Rax Proxy` has neither. A build with even one upgrade
        (e.g. `Speedling All-In`'s Metabolic Boost) still gets both stages,
        Stage 2 falling back to its own "no tech structures required"
        placeholder if that one upgrade doesn't need any.
        """
        self._init_validator_state()
        stages: Dict[str, List[StepResult]] = {
            "Stage 1: Opening Economy": self._validate_economy(),
            "Stage 1B: Proxy Crew Choreography": self._validate_crew(),
        }
        if self._upgrades:
            stages["Stage 2: Tech Structures"] = self._validate_structures()
            stages["Stage 3: Upgrades"] = self._validate_upgrades()
        stages["Stage 4: All-In Attack"] = self._validate_waves()
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
        target = self.ctx.build.economy.worker_target if self.ctx else 60
        max_gas = self.ctx.build.economy.max_gas if self.ctx else 2
        pool_deadline = self.ctx.build.pool_deadline if self.ctx else self.POOL_DEADLINE
        race = self.ctx.build.race if self.ctx else Race.Zerg

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
