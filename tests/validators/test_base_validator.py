"""Regression tests for `BaseValidator`'s shared tracking/reporting
machinery - the mechanics every build's own validator inherits. Per-build
report shape is each concrete validator's own test file instead.

Drives `on_step` against the duck-typed fakes in `_fakes.py`, then inspects
the tracking output. Most tests construct `BaseValidator` directly; a few
needing a Stage 2/3/4 key use `UpgradeRushValidator` as a convenient
four-stage concrete class.
"""

from __future__ import annotations

from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from tests.validators._fakes import FakeAI, _Counted, _FakeUnit
from tests.validators.base_validator import BaseValidator
from tests.validators.upgrade_rush_validator import UpgradeRushValidator

# ── Stage 1: carried over from ZergRushValidator ────────────────────────────


def test_pool_under_construction_is_not_double_counted() -> None:
    """A single pool, mid-build, must not read as `pool count: 2` — see
    `already_pending`'s docstring ("buildings already in progress"): it
    counts the same structure `structures(...).amount` already counts."""
    ai = FakeAI()
    ai._structure_counts[UnitTypeId.SPAWNINGPOOL] = 1
    ai._pending_counts[UnitTypeId.SPAWNINGPOOL] = 1
    validator = BaseValidator(ai)
    for _ in range(5):
        validator.on_step(0)

    result = validator.validate()
    only_one_pool = next(
        r for r in result["Stage 1: Opening Economy"] if r.name == "Only One Pool"
    )
    assert only_one_pool.passed, only_one_pool.detail


def test_supply_block_within_grace_period_is_ignored() -> None:
    ai = FakeAI()
    ai.supply_left = 0
    validator = BaseValidator(ai)
    for frame in range(200):
        ai.time = frame * 0.1  # up to 20.0s, well under the grace period
        validator.on_step(0)

    assert validator._supply_blocked_frames == 0


def test_supply_block_after_grace_period_still_counts() -> None:
    ai = FakeAI()
    ai.supply_left = 0
    validator = BaseValidator(ai)
    for frame in range(1000):
        ai.time = 60.0 + frame * 0.1  # starts exactly at the grace period
        validator.on_step(0)

    assert validator._supply_blocked_frames == 1000


# ── Stage 1: pool deadline comes from the build ─────────────────────────────


def test_pool_timing_deadline_comes_from_the_build_not_a_shared_constant() -> None:
    """A hatch-before-pool build (e.g. `UpgradeRush`, `pool_deadline=75.0`)
    pools later than an immediate-pool one by design - the check must use
    that build's own `ctx.build.pool_deadline`."""
    ai = FakeAI(pool_deadline=75.0)
    ai.time = 63.0  # past an immediate-pool deadline (50.0), within 75.0
    ai._structure_counts[UnitTypeId.SPAWNINGPOOL] = 1
    validator = BaseValidator(ai)
    validator.on_step(0)

    result = validator.validate()
    pool_check = next(
        r for r in result["Stage 1: Opening Economy"] if r.name == "Pool Timing"
    )
    assert pool_check.passed, pool_check.detail


def test_a_non_zerg_build_gets_no_pool_or_extractor_checks() -> None:
    """A Terran build can never have a Spawning Pool or Extractor, so
    reporting FAILs for them would bury checks that do apply."""
    ai = FakeAI(race=Race.Terran)
    ai.time = 120.0
    validator = BaseValidator(ai)
    validator.on_step(0)

    names = [r.name for r in validator.validate()["Stage 1: Opening Economy"]]
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
    validator = BaseValidator(ai)
    validator.on_step(0)

    names = [r.name for r in validator.validate()["Stage 1: Opening Economy"]]
    assert "Pool Timing" in names, names
    assert "Extractor Built" in names, names


def test_pool_timing_still_fails_past_the_builds_own_deadline() -> None:
    ai = FakeAI(pool_deadline=75.0)
    ai.time = 80.0
    ai._structure_counts[UnitTypeId.SPAWNINGPOOL] = 1
    validator = BaseValidator(ai)
    validator.on_step(0)

    result = validator.validate()
    pool_check = next(
        r for r in result["Stage 1: Opening Economy"] if r.name == "Pool Timing"
    )
    assert not pool_check.passed


# ── Stage 1: extractor cap ───────────────────────────────────────────────────


def test_extractor_cap_respected_when_within_cap() -> None:
    ai = FakeAI(max_gas=2)
    ai.gas_buildings = _Counted(2)
    validator = BaseValidator(ai)
    validator.on_step(0)

    result = validator.validate()
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
    validator = BaseValidator(ai)
    validator.on_step(0)

    result = validator.validate()
    cap_check = next(
        r
        for r in result["Stage 1: Opening Economy"]
        if r.name == "Extractor Cap Respected"
    )
    assert not cap_check.passed
    assert "cap 2" in cap_check.detail


# ── Stage 2/3: resource-block detection ──────────────────────────────────────


def test_upgrade_not_yet_eligible_never_counts_as_resource_blocked() -> None:
    """Melee Attacks +1 researches from an Evolution Chamber. With none
    built yet, it isn't eligible - being unable to afford it shouldn't
    count as a resource block, since money was never the blocker."""
    ai = FakeAI(upgrades=(UpgradeId.ZERGMELEEWEAPONSLEVEL1,))
    validator = BaseValidator(ai)
    # No Evolution Chamber, and never affordable either.
    for _ in range(50):
        validator.on_step(0)

    tracker = validator._upgrades[0]
    assert not tracker.started
    assert tracker.blocked_frames == 0


def test_upgrade_eligible_but_unaffordable_counts_as_resource_blocked() -> None:
    ai = FakeAI(upgrades=(UpgradeId.ZERGMELEEWEAPONSLEVEL1,))
    ai._structure_counts[UnitTypeId.EVOLUTIONCHAMBER] = 1
    ai._structure_ready_counts[UnitTypeId.EVOLUTIONCHAMBER] = 1
    validator = BaseValidator(ai)
    for _ in range(30):
        validator.on_step(0)

    tracker = validator._upgrades[0]
    assert not tracker.started
    assert tracker.blocked_frames == 30

    # Once affordable, it starts and stops accumulating blocked frames.
    ai._affordable.add(UpgradeId.ZERGMELEEWEAPONSLEVEL1)
    ai._pending_upgrades.add(UpgradeId.ZERGMELEEWEAPONSLEVEL1)  # research fires
    validator.on_step(0)
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
    validator = BaseValidator(ai)
    for _ in range(20):
        validator.on_step(0)

    melee2 = validator._upgrades[1]
    assert melee2.blocked_frames == 0

    # Now Melee +1 is under way - Melee +2 becomes genuinely eligible.
    ai._pending_upgrades.add(UpgradeId.ZERGMELEEWEAPONSLEVEL1)
    for _ in range(15):
        validator.on_step(0)

    assert melee2.blocked_frames == 15


# ── Stage 2/3: the report shape is generic, not one build's coincidence ─────


def test_a_build_with_no_tech_upgrades_gets_no_tech_structure_checks() -> None:
    """A build whose upgrades touch no tech structure (e.g. Metabolic Boost
    alone) should say so plainly instead of failing checks it could never
    pass. `_validate_structures` is the shared mechanic every build's own
    validator uses; per-build report shape belongs in each build's own test
    file, not here."""
    ai = FakeAI(upgrades=(UpgradeId.ZERGLINGMOVEMENTSPEED,))
    validator = BaseValidator(ai)
    validator.on_step(0)

    assert validator._structures == []
    result = validator._validate_structures()
    assert [r.name for r in result] == ["No tech structures required by this build"]


def test_evolution_chamber_target_and_gate_come_from_the_build_not_a_constant() -> None:
    """A second Evolution Chamber must not count as resource-blocked until
    this build's own gate opens, with nothing build-specific hardcoding
    that decision."""
    gate_open = False
    ai = FakeAI(
        upgrades=(UpgradeId.ZERGMELEEWEAPONSLEVEL1,),
        evolution_chambers=2,
        evolution_chamber_gate=lambda ctx: gate_open,
    )
    ai._structure_counts[UnitTypeId.EVOLUTIONCHAMBER] = 1
    ai._structure_ready_counts[UnitTypeId.EVOLUTIONCHAMBER] = 1
    validator = BaseValidator(ai)
    for _ in range(25):
        validator.on_step(0)

    evo = next(
        s for s in validator._structures if s.structure == UnitTypeId.EVOLUTIONCHAMBER
    )
    assert not evo.started  # only 1 of this build's target of 2
    assert evo.blocked_frames == 0  # gate closed - never eligible yet

    gate_open = True
    for _ in range(10):
        validator.on_step(0)
    assert evo.blocked_frames == 10  # gate open, still unaffordable


# ── Stage 4: attack waves ────────────────────────────────────────────────────


def test_wave_release_records_size_time_and_next_expected_minimum() -> None:
    ai = FakeAI(upgrades=())
    ai.ctx.build.combat.wave1_min = 20
    ai.ctx.build.combat.wave_growth = 1.25
    validator = UpgradeRushValidator(ai)

    wave1_units = [_FakeUnit(i) for i in range(21)]
    ai.time = 300.0
    ai.ctx.state.wave_number = 1
    ai.ctx.attacking = wave1_units
    validator.on_step(0)

    assert len(validator._waves) == 1
    wave1 = validator._waves[0]
    assert wave1.number == 1
    assert wave1.time == 300.0
    assert wave1.size == 21
    assert wave1.expected_min == 20  # wave1_min, before any wave has landed
    assert wave1.gap is None
    assert wave1.our_supply == 10.5  # 21 zerglings * 0.5 supply each

    # ceil(21 * 1.25) == 27 is what the *next* wave should be measured against.
    assert validator._next_wave_expected_min == 27

    wave2_units = wave1_units + [_FakeUnit(100 + i) for i in range(27)]
    ai.time = 420.0
    ai.ctx.state.wave_number = 2
    ai.ctx.attacking = wave2_units
    validator.on_step(0)

    assert len(validator._waves) == 2
    wave2 = validator._waves[1]
    assert wave2.size == 27
    assert wave2.expected_min == 27
    assert wave2.gap == 120.0

    result = validator.validate()
    wave_results = {r.name: r for r in result["Stage 4: Attack Waves"]}
    assert wave_results["Wave 1"].passed
    assert wave_results["Wave 2"].passed


def test_wave_records_our_supply_against_enemy_army_supply() -> None:
    """Each wave captures our released supply against the enemy's known army
    supply - not unit count, since a few Roaches outweigh many Zerglings."""
    ai = FakeAI(upgrades=())
    ai.mediator.get_cached_enemy_army = [
        _FakeUnit(900 + i, type_id=UnitTypeId.ROACH) for i in range(4)
    ]
    # A worker sitting in the cached enemy army (ares' cache doesn't filter
    # these out itself - see `_enemy_army_supply`'s docstring) must not
    # count as army supply.
    ai.mediator.get_cached_enemy_army.append(_FakeUnit(950, type_id=UnitTypeId.DRONE))
    validator = UpgradeRushValidator(ai)

    wave1_units = [_FakeUnit(i, type_id=UnitTypeId.ZERGLING) for i in range(20)]
    ai.time = 300.0
    ai.ctx.state.wave_number = 1
    ai.ctx.attacking = wave1_units
    validator.on_step(0)

    wave1 = validator._waves[0]
    assert wave1.our_supply == 10.0  # 20 zerglings * 0.5
    assert wave1.enemy_supply == 8.0  # 4 roaches * 2.0, drone excluded

    result = validator.validate()
    wave1_result = next(
        r for r in result["Stage 4: Attack Waves"] if r.name == "Wave 1"
    )
    assert "supply us=10 vs enemy=8" in wave1_result.detail


def test_no_wave_ever_released_fails_stage_4() -> None:
    ai = FakeAI(upgrades=())
    validator = UpgradeRushValidator(ai)
    validator.on_step(0)

    result = validator.validate()
    assert result["Stage 4: Attack Waves"] == [
        r for r in result["Stage 4: Attack Waves"] if not r.passed
    ]
