"""Regression tests for `MacroZergValidator`'s own report shape: full six
stages, no Stage 1B, titled "Roach Pushes", Stage 3 actually tracking Roach
Warren/Lair/Infestation Pit/Spire despite an upgrade list that wouldn't
auto-derive any of them, and Stage 2's from-scratch opening-timing checklist.
Generic tracking mechanics are covered once in `test_base_validator.py`.
"""

from __future__ import annotations

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from tests.validators._fakes import FakeAI, _FakeUnit, _FakeUnits
from tests.validators.macro_zerg_validator import MacroZergValidator


def test_validate_returns_all_six_stages() -> None:
    ai = FakeAI(
        upgrades=(UpgradeId.BURROW, UpgradeId.TUNNELINGCLAWS),
        wave_stage_label="Roach Pushes",
    )
    validator = MacroZergValidator(ai)
    validator.on_step(0)

    result = validator.validate()
    assert list(result) == [
        "Stage 1: Opening Economy",
        "Stage 2: Opening Timing",
        "Stage 3: Tech Structures",
        "Stage 4: Upgrades",
        "Stage 5: Roach Pushes",
        "Stage 6: Combat QA",
    ]


def test_report_never_includes_stage_1b() -> None:
    """Regression test: Stage 1B used to leak in from the old shared
    validator class. `MacroZergValidator.validate()` structurally never
    calls `_validate_crew()`, so this key can't appear regardless of
    `ctx.build.crew`."""
    ai = FakeAI(upgrades=(UpgradeId.BURROW,), wave_stage_label="Roach Pushes")
    validator = MacroZergValidator(ai)
    validator.on_step(0)

    assert "Stage 1B: Proxy Crew Choreography" not in validator.validate()


def test_report_title_stays_roach_pushes() -> None:
    """Regression test: a wave stage title used to leak "All-In Attack" from
    `Four Rax Proxy` into every build. This file only reads its own build's
    `wave_stage_label`."""
    ai = FakeAI(upgrades=(), wave_stage_label="Roach Pushes")
    validator = MacroZergValidator(ai)
    validator.on_step(0)

    result = validator.validate()
    assert "Stage 5: Roach Pushes" in result
    assert "Stage 5: All-In Attack" not in result


def test_stage_3_tracks_the_full_roach_swarm_host_tech_chain() -> None:
    """Regression test for the actual reason this build needs its own
    `_init_milestones` override: Burrow/Tunneling Claws alone wouldn't
    auto-derive any Stage 3 trackers from `BaseValidator`, which would
    otherwise silently report "No tech structures required by this build"
    despite Roach Warren/Lair/Infestation Pit/Spire being exactly what this
    build's identity depends on."""
    ai = FakeAI(
        upgrades=(UpgradeId.BURROW, UpgradeId.TUNNELINGCLAWS),
        wave_stage_label="Roach Pushes",
    )
    validator = MacroZergValidator(ai)
    validator.on_step(0)

    result = validator.validate()
    steps = result["Stage 3: Tech Structures"]
    names = [step.name for step in steps]
    assert set(names) == {"Roach Warren", "Lair", "Infestation Pit", "Spire"}
    assert len(names) == len(set(names)), (
        f"duplicate tracker: {names} - Tunneling Claws already requires "
        "Lair, so the base class's own upgrade-driven derivation adds one "
        "on its own; the override must not add a second"
    )


# ── Stage 2: opening timing ─────────────────────────────────────────────────


def _validator() -> MacroZergValidator:
    ai = FakeAI(upgrades=(UpgradeId.BURROW,), wave_stage_label="Roach Pushes")
    return MacroZergValidator(ai)


def test_all_fourteen_opening_timing_checks_are_reported_in_order() -> None:
    validator = _validator()
    validator.on_step(0)

    result = validator.validate()
    names = [step.name for step in result["Stage 2: Opening Timing"]]
    assert names == [
        "Overlord",
        "Natural Hatchery",
        "Gas",
        "Spawning Pool",
        "4 Zergling",
        "3rd base Hatchery",
        "3 Drone off gas",
        "Speed",
        "4th Overlord",
        "3 Drone on gas",
        "Roach Warren",
        "2nd Gas",
        "Lair",
        "3 Spore Crawlers",
    ]


def test_a_milestone_hit_before_its_deadline_passes() -> None:
    validator = _validator()
    validator.ai.time = 10.0  # Overlord's deadline is 12.0s
    validator.ai._units[UnitTypeId.OVERLORD] = [_FakeUnit(1, UnitTypeId.OVERLORD)]
    validator.ai._pending_counts[UnitTypeId.OVERLORD] = 1  # 1 live + 1 pending = 2
    validator.on_step(0)

    result = validator.validate()
    overlord = next(r for r in result["Stage 2: Opening Timing"] if r.name == "Overlord")
    assert overlord.passed, overlord.detail
    assert "10.0s" in overlord.detail


def test_a_milestone_hit_after_its_deadline_fails() -> None:
    validator = _validator()
    validator.ai.time = 20.0  # Overlord's deadline is 12.0s
    validator.ai._pending_counts[UnitTypeId.OVERLORD] = 2
    validator.on_step(0)

    result = validator.validate()
    overlord = next(r for r in result["Stage 2: Opening Timing"] if r.name == "Overlord")
    assert not overlord.passed
    assert "20.0s" in overlord.detail
    assert "deadline 12s" in overlord.detail


def test_a_milestone_never_reached_fails_with_a_clear_detail() -> None:
    validator = _validator()
    validator.ai.time = 300.0
    validator.on_step(0)

    result = validator.validate()
    pool = next(r for r in result["Stage 2: Opening Timing"] if r.name == "Spawning Pool")
    assert not pool.passed
    assert pool.detail == "never happened (deadline 75s)"


def test_a_milestone_latches_and_does_not_un_report_once_passed() -> None:
    """A structure that's later destroyed (or a unit that dies) must not
    make an already-hit milestone read as never-happened again - this is a
    "did it start in time" check, not a "does it still exist" check."""
    validator = _validator()
    validator.ai._structure_counts[UnitTypeId.SPAWNINGPOOL] = 1
    validator.on_step(0)
    validator.ai._structure_counts[UnitTypeId.SPAWNINGPOOL] = 0  # pool destroyed
    validator.on_step(0)

    result = validator.validate()
    pool = next(r for r in result["Stage 2: Opening Timing"] if r.name == "Spawning Pool")
    assert pool.passed


def test_gas_off_requires_gas_to_have_been_staffed_first() -> None:
    """An opening that never even builds up to 3 gas workers must not
    trivially pass "3 Drone off gas" just because the (never-reached)
    count happens to already read 0."""
    validator = _validator()
    validator.on_step(0)  # gas_buildings defaults to empty/0 assigned

    result = validator.validate()
    gas_off = next(
        r for r in result["Stage 2: Opening Timing"] if r.name == "3 Drone off gas"
    )
    assert not gas_off.passed


def test_gas_pull_off_and_resume_track_in_order() -> None:
    validator = _validator()
    # 3 workers staffed on one gas building.
    validator.ai.gas_buildings = _FakeUnits(
        [_FakeUnit(1, UnitTypeId.EXTRACTOR, assigned_harvesters=3)]
    )
    validator.on_step(0)
    # Pulled off (see steps.common.gas_workers's pull_off).
    validator.ai.time = 100.0
    validator.ai.gas_buildings[0].assigned_harvesters = 0
    validator.on_step(0)
    # Bank spent back down, resumes mining.
    validator.ai.time = 200.0
    validator.ai.gas_buildings[0].assigned_harvesters = 3
    validator.on_step(0)

    result = validator.validate()
    by_name = {r.name: r for r in result["Stage 2: Opening Timing"]}
    assert by_name["3 Drone off gas"].passed
    assert "100.0s" in by_name["3 Drone off gas"].detail
    assert by_name["3 Drone on gas"].passed
    assert "200.0s" in by_name["3 Drone on gas"].detail


def test_lair_milestone_tracks_structure_count() -> None:
    validator = _validator()
    validator.ai.time = 200.0  # Lair's deadline is 256.0s
    validator.ai._structure_counts[UnitTypeId.LAIR] = 1
    validator.on_step(0)

    result = validator.validate()
    lair = next(r for r in result["Stage 2: Opening Timing"] if r.name == "Lair")
    assert lair.passed, lair.detail
    assert "200.0s" in lair.detail


def test_spores3_requires_three_not_two() -> None:
    """One Spore Crawler per base (`steps.zerg.spore_crawlers(per_base=1)`)
    - two up (only main + natural) must not satisfy a check meant to
    confirm the 3rd base is covered too."""
    validator = _validator()
    validator.ai._structure_counts[UnitTypeId.SPORECRAWLER] = 2
    validator.on_step(0)

    result = validator.validate()
    spores = next(
        r for r in result["Stage 2: Opening Timing"] if r.name == "3 Spore Crawlers"
    )
    assert not spores.passed


def test_spores3_passes_once_the_third_is_up() -> None:
    validator = _validator()
    validator.ai.time = 250.0  # deadline is 265.0s
    validator.ai._structure_counts[UnitTypeId.SPORECRAWLER] = 3
    validator.on_step(0)

    result = validator.validate()
    spores = next(
        r for r in result["Stage 2: Opening Timing"] if r.name == "3 Spore Crawlers"
    )
    assert spores.passed, spores.detail
    assert "250.0s" in spores.detail


def test_gas_on_never_latches_without_gas_off_happening_first() -> None:
    """Straight to 3 assigned with no prior pull-off must not satisfy
    "3 Drone on gas" - that milestone is specifically about *resuming*
    after the pull-off, not merely "gas has 3 workers at some point"."""
    validator = _validator()
    validator.ai.gas_buildings = _FakeUnits(
        [_FakeUnit(1, UnitTypeId.EXTRACTOR, assigned_harvesters=3)]
    )
    validator.on_step(0)

    result = validator.validate()
    gas_on = next(
        r for r in result["Stage 2: Opening Timing"] if r.name == "3 Drone on gas"
    )
    assert not gas_on.passed
