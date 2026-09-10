"""Regression tests for `UpgradeRushValidator`.

Drives `on_step` across fake frames with a minimal duck-typed stand-in for
the BotAI/BotContext surface it reads, then inspects the real `validate()`
output. Covers both the Stage 1 counting bugs carried over from the old
`ZergRushValidator` (pool double-counting, supply-block grace period) and
the new build-specific logic: the extractor cap this session's earlier fix
introduced, resource-block detection gated on real tech eligibility (not
just "hasn't happened yet"), and per-wave size/timing tracking.

Runs under pytest, or standalone with no test dependency:

    python -m tests.test_upgrade_rush_validator
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from tests.upgrade_rush_validator import StepResult, UpgradeRushValidator


class _Counted:
    """Stands in for a `Units` collection: `.amount`, `.ready`, truthiness."""

    def __init__(self, amount: int = 0, ready_amount: int | None = None):
        self.amount = amount
        self.ready = self if ready_amount is None else _Counted(ready_amount)

    def __bool__(self) -> bool:
        return self.amount > 0

    def __iter__(self):
        return iter(())


class _FakeUnit:
    def __init__(self, tag: int, type_id=UnitTypeId.ZERGLING):
        self.tag = tag
        self.type_id = type_id


class _FakeUnits(list):
    """Stands in for python-sc2's `Units`: just the `.tags_in` the validator
    needs to pick the newly-released wave's actual unit objects back out of
    the current ATTACKING group."""

    def tags_in(self, tags) -> "_FakeUnits":
        return _FakeUnits(u for u in self if u.tag in tags)


def _fake_crew_member() -> SimpleNamespace:
    return SimpleNamespace(tag=None, task_index=0, queued=False)


class _FakeCtx:
    """Duck-typed `BotContext`: just what the validator reads."""

    def __init__(
        self,
        upgrades: tuple = (),
        max_gas: int = 2,
        wave1_min: int = 20,
        wave_growth: float = 1.25,
        evolution_chambers: int = 1,
        evolution_chamber_gate=lambda ctx: True,
        pool_deadline: float = 50.0,
        race: Race = Race.Zerg,
        crew=None,
    ):
        self.build = SimpleNamespace(
            race=race,
            army=SimpleNamespace(
                upgrades=upgrades,
                evolution_chambers=evolution_chambers,
                evolution_chamber_gate=evolution_chamber_gate,
            ),
            economy=SimpleNamespace(worker_target=60, max_gas=max_gas),
            combat=SimpleNamespace(wave1_min=wave1_min, wave_growth=wave_growth),
            pool_deadline=pool_deadline,
            crew=crew,
        )
        self.state = SimpleNamespace(
            wave_number=0,
            proxy_crew=SimpleNamespace(
                x=_fake_crew_member(), y=_fake_crew_member(), z=_fake_crew_member()
            ),
        )
        self.attacking: list = []

    def units_in_role(self, role) -> _FakeUnits:
        return _FakeUnits(self.attacking)


class FakeAI(UpgradeRushValidator):
    """Just enough of the BotAI surface for `UpgradeRushValidator.on_step`."""

    def __init__(
        self,
        upgrades: tuple = (),
        max_gas: int = 2,
        evolution_chambers: int = 1,
        evolution_chamber_gate=lambda ctx: True,
        pool_deadline: float = 50.0,
        race: Race = Race.Zerg,
        crew=None,
    ):
        self.time = 0.0
        self.supply_left = 10
        self.supply_used = 14
        self.workers = _Counted(12)
        self.gas_buildings = _Counted(0)
        # `.get_cached_enemy_army` is a plain attribute here (a test sets it
        # directly), standing in for ares' real `ManagerMediator` property.
        self.mediator = SimpleNamespace(get_cached_enemy_army=[])
        self.ctx = _FakeCtx(
            upgrades,
            max_gas=max_gas,
            evolution_chambers=evolution_chambers,
            evolution_chamber_gate=evolution_chamber_gate,
            pool_deadline=pool_deadline,
            race=race,
            crew=crew,
        )
        self._structure_counts: dict = {}
        self._structure_ready_counts: dict = {}
        self._pending_counts: dict = {}
        self._pending_upgrades: set = set()
        self._affordable: set = set()

    def structures(self, unit_type) -> _Counted:
        amount = self._structure_counts.get(unit_type, 0)
        ready = self._structure_ready_counts.get(unit_type, amount)
        return _Counted(amount, ready)

    def already_pending(self, unit_type) -> int:
        return self._pending_counts.get(unit_type, 0)

    def already_pending_upgrade(self, upgrade) -> float:
        return 1.0 if upgrade in self._pending_upgrades else 0.0

    def pending_or_complete_upgrade(self, upgrade) -> bool:
        return upgrade in self._pending_upgrades

    def can_afford(self, item) -> bool:
        return item in self._affordable

    def tech_requirement_progress(self, structure_type) -> float:
        return 1.0

    # Real per-unit supply costs, just for the couple of types these tests
    # use - not a general `sc2.BotAI.calculate_supply_cost` stand-in.
    _SUPPLY_COSTS = {UnitTypeId.ZERGLING: 0.5, UnitTypeId.ROACH: 2.0}

    def calculate_supply_cost(self, unit_type) -> float:
        return self._SUPPLY_COSTS.get(unit_type, 1.0)


def _step(ai: FakeAI) -> None:
    asyncio.run(ai.on_step(0))


# ── Stage 1: carried over from ZergRushValidator ────────────────────────────


def test_pool_under_construction_is_not_double_counted() -> None:
    """A single pool, mid-build, must not read as `pool count: 2` — see
    `already_pending`'s docstring ("buildings already in progress"): it
    counts the same structure `structures(...).amount` already counts."""
    ai = FakeAI()
    ai._structure_counts[UnitTypeId.SPAWNINGPOOL] = 1
    ai._pending_counts[UnitTypeId.SPAWNINGPOOL] = 1
    for _ in range(5):
        _step(ai)

    result = ai.validate()
    only_one_pool = next(
        r for r in result["Stage 1: Opening Economy"] if r.name == "Only One Pool"
    )
    assert only_one_pool.passed, only_one_pool.detail


def test_supply_block_within_grace_period_is_ignored() -> None:
    ai = FakeAI()
    ai.supply_left = 0
    for frame in range(200):
        ai.time = frame * 0.1  # up to 20.0s, well under the grace period
        _step(ai)

    assert ai._supply_blocked_frames == 0


def test_supply_block_after_grace_period_still_counts() -> None:
    ai = FakeAI()
    ai.supply_left = 0
    for frame in range(1000):
        ai.time = 60.0 + frame * 0.1  # starts exactly at the grace period
        _step(ai)

    assert ai._supply_blocked_frames == 1000


# ── Stage 1: pool deadline comes from the build ─────────────────────────────


def test_pool_timing_deadline_comes_from_the_build_not_a_shared_constant() -> None:
    """A hatch-before-pool build (e.g. `UpgradeRush`, `pool_deadline=75.0`)
    pools later than an immediate-pool one by design - the check must use
    that build's own `ctx.build.pool_deadline`, not `POOL_DEADLINE` (a
    fallback for when `ctx` isn't set yet, calibrated for an immediate-pool
    opening)."""
    ai = FakeAI(pool_deadline=75.0)
    ai.time = 63.0  # past the class-level POOL_DEADLINE (50.0), within 75.0
    ai._structure_counts[UnitTypeId.SPAWNINGPOOL] = 1
    _step(ai)

    result = ai.validate()
    pool_check = next(
        r for r in result["Stage 1: Opening Economy"] if r.name == "Pool Timing"
    )
    assert pool_check.passed, pool_check.detail


def test_a_non_zerg_build_gets_no_pool_or_extractor_checks() -> None:
    """A Terran build can never have a Spawning Pool or an Extractor, so
    reporting four FAILs for them would bury the checks that do apply. Same
    lesson as gotchas 12 and 17: a check the build cannot satisfy is noise.
    """
    ai = FakeAI(race=Race.Terran)
    ai.time = 120.0
    _step(ai)

    names = [r.name for r in ai.validate()["Stage 1: Opening Economy"]]
    assert "Pool Timing" not in names, names
    assert "Workers Before Pool" not in names, names
    assert "Only One Pool" not in names, names
    assert "Extractor Built" not in names, names
    # The race-neutral checks are still there.
    assert "Workers Massed" in names, names
    assert "Supply Management" in names, names


def test_a_zerg_build_still_gets_the_pool_checks() -> None:
    ai = FakeAI()  # defaults to Race.Zerg
    ai.time = 120.0
    _step(ai)

    names = [r.name for r in ai.validate()["Stage 1: Opening Economy"]]
    assert "Pool Timing" in names, names
    assert "Extractor Built" in names, names


def test_pool_timing_still_fails_past_the_builds_own_deadline() -> None:
    ai = FakeAI(pool_deadline=75.0)
    ai.time = 80.0
    ai._structure_counts[UnitTypeId.SPAWNINGPOOL] = 1
    _step(ai)

    result = ai.validate()
    pool_check = next(
        r for r in result["Stage 1: Opening Economy"] if r.name == "Pool Timing"
    )
    assert not pool_check.passed


# ── Stage 1: extractor cap (this session's earlier fix) ─────────────────────


def test_extractor_cap_respected_when_within_cap() -> None:
    ai = FakeAI(max_gas=2)
    ai.gas_buildings = _Counted(2)
    _step(ai)

    result = ai.validate()
    cap_check = next(
        r
        for r in result["Stage 1: Opening Economy"]
        if r.name == "Extractor Cap Respected"
    )
    assert cap_check.passed, cap_check.detail


def test_extractor_cap_respected_flags_when_exceeded() -> None:
    """Regression test for the earlier `_requested_zerg_placements` leak
    (see ARCHITECTURE.md) that could pile extra extractors past the cap."""
    ai = FakeAI(max_gas=2)
    ai.gas_buildings = _Counted(3)
    _step(ai)

    result = ai.validate()
    cap_check = next(
        r
        for r in result["Stage 1: Opening Economy"]
        if r.name == "Extractor Cap Respected"
    )
    assert not cap_check.passed
    assert "cap 2" in cap_check.detail


# ── Stage 1B: proxy crew choreography ────────────────────────────────────────


def _fake_task(structure_id=UnitTypeId.BARRACKS, label: str = "") -> SimpleNamespace:
    return SimpleNamespace(structure_id=structure_id, label=label)


def _fake_crew_plan() -> SimpleNamespace:
    return SimpleNamespace(
        x_tasks=(
            _fake_task(UnitTypeId.BARRACKS, "Barracks A"),
            _fake_task(UnitTypeId.BARRACKS, "Barracks D"),
        ),
        y_tasks=(
            _fake_task(UnitTypeId.BARRACKS, "Barracks B"),
            _fake_task(UnitTypeId.SUPPLYDEPOT, "Depot (proxy)"),
        ),
        z_tasks=(
            _fake_task(UnitTypeId.SUPPLYDEPOT, "Depot (home)"),
            _fake_task(UnitTypeId.BARRACKS, "Barracks C"),
        ),
    )


def test_a_build_with_no_crew_plan_gets_a_single_not_declared_line() -> None:
    ai = FakeAI(race=Race.Terran)  # crew defaults to None
    _step(ai)

    result = ai.validate()
    assert result["Stage 1B: Proxy Crew Choreography"] == [
        StepResult("No proxy crew declared by this build", True)
    ]


def test_crew_stage_lists_every_declared_claim_and_task() -> None:
    ai = FakeAI(race=Race.Terran, crew=_fake_crew_plan())
    _step(ai)

    names = [r.name for r in ai.validate()["Stage 1B: Proxy Crew Choreography"]]
    assert names == [
        "Crew X claimed",
        "Crew Y claimed",
        "Crew Z claimed (13th SCV)",
        "Barracks A",
        "Barracks D",
        "Barracks B",
        "Depot (proxy)",
        "Depot (home)",
        "Barracks C",
    ]


def test_crew_claim_is_tracked_the_frame_a_tag_appears() -> None:
    ai = FakeAI(race=Race.Terran, crew=_fake_crew_plan())
    ai.time = 0.1
    _step(ai)

    x_claim = next(
        r
        for r in ai.validate()["Stage 1B: Proxy Crew Choreography"]
        if r.name == "Crew X claimed"
    )
    assert not x_claim.passed

    ai.ctx.state.proxy_crew.x.tag = 111
    ai.time = 0.2
    _step(ai)

    x_claim = next(
        r
        for r in ai.validate()["Stage 1B: Proxy Crew Choreography"]
        if r.name == "Crew X claimed"
    )
    assert x_claim.passed
    assert "0.2" in x_claim.detail


def test_crew_task_tracks_started_then_completed_without_double_counting() -> None:
    """Barracks A and Barracks B are both plain `BARRACKS` tasks that start
    and finish within moments of each other on X and Y respectively - the
    tracker must key off (member, index), not structure type, or one would
    read as the other's completion."""
    ai = FakeAI(race=Race.Terran, crew=_fake_crew_plan())
    ai.ctx.state.proxy_crew.x.tag = 1
    ai.ctx.state.proxy_crew.y.tag = 2
    ai.time = 5.0
    ai.ctx.state.proxy_crew.x.queued = True  # Barracks A issued
    _step(ai)

    stage = {r.name: r for r in ai.validate()["Stage 1B: Proxy Crew Choreography"]}
    assert not stage["Barracks A"].passed
    assert "started" in stage["Barracks A"].detail
    assert not stage["Barracks B"].passed  # Y hasn't even started yet

    # Barracks A completes (task_index advances); Barracks B still hasn't.
    ai.ctx.state.proxy_crew.x.queued = False
    ai.ctx.state.proxy_crew.x.task_index = 1
    ai.time = 40.0
    _step(ai)

    stage = {r.name: r for r in ai.validate()["Stage 1B: Proxy Crew Choreography"]}
    assert stage["Barracks A"].passed
    assert "40.0" in stage["Barracks A"].detail
    assert not stage["Barracks B"].passed


def test_crew_tasks_are_reported_in_completion_order_not_declared_order() -> None:
    """Regression test: `_fake_crew_plan()`'s X declares Barracks A then
    Barracks D, in that order - but the real build gates Barracks D on
    Marine training having started, so it can genuinely finish after Y's
    ungated second task (Depot (proxy)) even though Barracks D sits earlier
    in X's own declared list. The report must reflect what actually
    happened this game, not each crew member's own task order."""
    ai = FakeAI(race=Race.Terran, crew=_fake_crew_plan())
    crew = ai.ctx.state.proxy_crew
    ai.time = 10.0
    _step(ai)

    crew.x.task_index = 1  # Barracks A (X's first task) completes
    ai.time = 20.0
    _step(ai)

    crew.y.task_index = 2  # Depot (proxy) (Y's second task) completes
    ai.time = 50.0
    _step(ai)

    crew.x.task_index = 2  # Barracks D (X's second task) finally completes
    ai.time = 80.0
    _step(ai)

    names = [r.name for r in ai.validate()["Stage 1B: Proxy Crew Choreography"]]
    assert names.index("Barracks A") < names.index("Depot (proxy)")
    assert names.index("Depot (proxy)") < names.index("Barracks D")


# ── Stage 2/3: resource-block detection ──────────────────────────────────────


def test_upgrade_not_yet_eligible_never_counts_as_resource_blocked() -> None:
    """Melee Attacks +1 researches from an Evolution Chamber. With none
    built yet, it isn't eligible - being unable to afford it shouldn't
    count as a resource block, since money was never the blocker."""
    ai = FakeAI(upgrades=(UpgradeId.ZERGMELEEWEAPONSLEVEL1,))
    # No Evolution Chamber, and never affordable either.
    for _ in range(50):
        _step(ai)

    tracker = ai._upgrades[0]
    assert not tracker.started
    assert tracker.blocked_frames == 0


def test_upgrade_eligible_but_unaffordable_counts_as_resource_blocked() -> None:
    ai = FakeAI(upgrades=(UpgradeId.ZERGMELEEWEAPONSLEVEL1,))
    ai._structure_counts[UnitTypeId.EVOLUTIONCHAMBER] = 1
    ai._structure_ready_counts[UnitTypeId.EVOLUTIONCHAMBER] = 1
    for _ in range(30):
        _step(ai)

    tracker = ai._upgrades[0]
    assert not tracker.started
    assert tracker.blocked_frames == 30

    # Once affordable, it starts and stops accumulating blocked frames.
    ai._affordable.add(UpgradeId.ZERGMELEEWEAPONSLEVEL1)
    ai._pending_upgrades.add(UpgradeId.ZERGMELEEWEAPONSLEVEL1)  # research fires
    _step(ai)
    assert tracker.started
    assert tracker.blocked_frames == 30


def test_upgrade_requiring_lair_waits_on_the_earlier_upgrade_in_list() -> None:
    """Melee +2 needs Lair *and* comes after Melee +1 in the build's own
    upgrade order - it must not count as resource-blocked while Melee +1
    hasn't even started yet, even with a Lair sitting ready."""
    ai = FakeAI(
        upgrades=(UpgradeId.ZERGMELEEWEAPONSLEVEL1, UpgradeId.ZERGMELEEWEAPONSLEVEL2)
    )
    ai._structure_counts[UnitTypeId.EVOLUTIONCHAMBER] = 1
    ai._structure_ready_counts[UnitTypeId.EVOLUTIONCHAMBER] = 1
    ai._structure_counts[UnitTypeId.LAIR] = 1
    ai._structure_ready_counts[UnitTypeId.LAIR] = 1
    for _ in range(20):
        _step(ai)

    melee2 = ai._upgrades[1]
    assert melee2.blocked_frames == 0

    # Now Melee +1 is under way - Melee +2 becomes genuinely eligible.
    ai._pending_upgrades.add(UpgradeId.ZERGMELEEWEAPONSLEVEL1)
    for _ in range(15):
        _step(ai)

    assert melee2.blocked_frames == 15


# ── Stage 2/3: the report is build-driven, not UpgradeRush-specific ─────────


def test_a_build_with_no_tech_upgrades_gets_no_tech_structure_checks() -> None:
    """`Speedling All-In` only ever researches Metabolic Boost - it has no
    Evolution Chamber, Lair or Hive in its upgrade list at all. The report
    should say so plainly instead of failing checks for milestones that
    build was never going to reach."""
    ai = FakeAI(upgrades=(UpgradeId.ZERGLINGMOVEMENTSPEED,))
    _step(ai)

    assert ai._structures == []
    result = ai.validate()
    assert result["Stage 2: Tech Structures"] == [
        StepResult("No tech structures required by this build", True)
    ]


def test_a_build_with_no_upgrades_at_all_gets_no_tech_or_upgrade_stages() -> None:
    """`Four Rax Proxy` declares no upgrades whatsoever - unlike `Speedling
    All-In` above (which still has Metabolic Boost), Stage 2 and Stage 3
    should be left out of the report entirely rather than each showing their
    own trivially-passing placeholder line."""
    ai = FakeAI(upgrades=(), race=Race.Terran)
    _step(ai)

    result = ai.validate()
    assert "Stage 2: Tech Structures" not in result
    assert "Stage 3: Upgrades" not in result


def test_evolution_chamber_target_and_gate_come_from_the_build_not_a_constant() -> None:
    """A second Evolution Chamber must never count as resource-blocked while
    this build's own gate hasn't opened - and must start counting the
    instant it does - with nothing UpgradeRush-specific (no reference to
    Metabolic Boost or any other hardcoded upgrade) making that decision."""
    gate_open = False
    ai = FakeAI(
        upgrades=(UpgradeId.ZERGMELEEWEAPONSLEVEL1,),
        evolution_chambers=2,
        evolution_chamber_gate=lambda ctx: gate_open,
    )
    ai._structure_counts[UnitTypeId.EVOLUTIONCHAMBER] = 1
    ai._structure_ready_counts[UnitTypeId.EVOLUTIONCHAMBER] = 1
    for _ in range(25):
        _step(ai)

    evo = next(s for s in ai._structures if s.structure == UnitTypeId.EVOLUTIONCHAMBER)
    assert not evo.started  # only 1 of this build's target of 2
    assert evo.blocked_frames == 0  # gate closed - never eligible yet

    gate_open = True
    for _ in range(10):
        _step(ai)
    assert evo.blocked_frames == 10  # gate open, still unaffordable


# ── Stage 4: attack waves ────────────────────────────────────────────────────


def test_wave_release_records_size_time_and_next_expected_minimum() -> None:
    ai = FakeAI(upgrades=())
    ai.ctx.build.combat.wave1_min = 20
    ai.ctx.build.combat.wave_growth = 1.25

    wave1_units = [_FakeUnit(i) for i in range(21)]
    ai.time = 300.0
    ai.ctx.state.wave_number = 1
    ai.ctx.attacking = wave1_units
    _step(ai)

    assert len(ai._waves) == 1
    wave1 = ai._waves[0]
    assert wave1.number == 1
    assert wave1.time == 300.0
    assert wave1.size == 21
    assert wave1.expected_min == 20  # wave1_min, before any wave has landed
    assert wave1.gap is None
    assert wave1.our_supply == 10.5  # 21 zerglings * 0.5 supply each

    # ceil(21 * 1.25) == 27 is what the *next* wave should be measured against.
    assert ai._next_wave_expected_min == 27

    wave2_units = wave1_units + [_FakeUnit(100 + i) for i in range(27)]
    ai.time = 420.0
    ai.ctx.state.wave_number = 2
    ai.ctx.attacking = wave2_units
    _step(ai)

    assert len(ai._waves) == 2
    wave2 = ai._waves[1]
    assert wave2.size == 27
    assert wave2.expected_min == 27
    assert wave2.gap == 120.0

    result = ai.validate()
    wave_results = {r.name: r for r in result["Stage 4: All-In Attack"]}
    assert wave_results["Wave 1"].passed
    assert wave_results["Wave 2"].passed


def test_wave_records_our_supply_against_enemy_army_supply() -> None:
    """Each wave should capture what it's actually walking into: our
    released supply next to the enemy's known army supply at that instant -
    not just our own size, and not the enemy's unit *count* either, since a
    handful of Roaches outweighs the same number of Zerglings."""
    ai = FakeAI(upgrades=())
    ai.mediator.get_cached_enemy_army = [
        _FakeUnit(900 + i, type_id=UnitTypeId.ROACH) for i in range(4)
    ]
    # A worker sitting in the cached enemy army (ares' cache doesn't filter
    # these out itself - see `_enemy_army_supply`'s docstring) must not
    # count as army supply.
    ai.mediator.get_cached_enemy_army.append(_FakeUnit(950, type_id=UnitTypeId.DRONE))

    wave1_units = [_FakeUnit(i, type_id=UnitTypeId.ZERGLING) for i in range(20)]
    ai.time = 300.0
    ai.ctx.state.wave_number = 1
    ai.ctx.attacking = wave1_units
    _step(ai)

    wave1 = ai._waves[0]
    assert wave1.our_supply == 10.0  # 20 zerglings * 0.5
    assert wave1.enemy_supply == 8.0  # 4 roaches * 2.0, drone excluded

    result = ai.validate()
    wave1_result = next(
        r for r in result["Stage 4: All-In Attack"] if r.name == "Wave 1"
    )
    assert "supply us=10 vs enemy=8" in wave1_result.detail


def test_no_wave_ever_released_fails_stage_4() -> None:
    ai = FakeAI(upgrades=())
    _step(ai)

    result = ai.validate()
    assert result["Stage 4: All-In Attack"] == [
        r for r in result["Stage 4: All-In Attack"] if not r.passed
    ]


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
        except Exception as error:  # noqa: BLE001 - report, don't stop
            failures += 1
            print(f"  FAIL  {test.__name__}: {error}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
