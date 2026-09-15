"""Regression tests for `MacroZergValidator`'s own report shape: full five
stages, no Stage 1B, titled "Roach Pushes", and (the reason this build needs
its own `_init_milestones` override at all) Stage 2 actually tracking Roach
Warren/Lair/Infestation Pit/Spire despite an upgrade list that wouldn't
auto-derive any of them. Generic tracking mechanics are covered once in
`test_base_validator.py`.
"""

from __future__ import annotations

from sc2.ids.upgrade_id import UpgradeId

from tests.validators._fakes import FakeAI
from tests.validators.macro_zerg_validator import MacroZergValidator


def test_validate_returns_all_five_base_stages() -> None:
    ai = FakeAI(
        upgrades=(UpgradeId.BURROW, UpgradeId.TUNNELINGCLAWS),
        wave_stage_label="Roach Pushes",
    )
    validator = MacroZergValidator(ai)
    validator.on_step(0)

    result = validator.validate()
    assert list(result) == [
        "Stage 1: Opening Economy",
        "Stage 2: Tech Structures",
        "Stage 3: Upgrades",
        "Stage 4: Roach Pushes",
        "Stage 5: Combat QA",
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
    """Regression test: Stage 4's title used to leak "All-In Attack" from
    `Four Rax Proxy` into every build. This file only reads its own build's
    `wave_stage_label`."""
    ai = FakeAI(upgrades=(), wave_stage_label="Roach Pushes")
    validator = MacroZergValidator(ai)
    validator.on_step(0)

    result = validator.validate()
    assert "Stage 4: Roach Pushes" in result
    assert "Stage 4: All-In Attack" not in result


def test_stage_2_tracks_the_full_roach_swarm_host_tech_chain() -> None:
    """Regression test for the actual reason this build needs its own
    `_init_milestones` override: Burrow/Tunneling Claws alone wouldn't
    auto-derive any Stage 2 trackers from `BaseValidator`, which would
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
    steps = result["Stage 2: Tech Structures"]
    names = [step.name for step in steps]
    assert set(names) == {"Roach Warren", "Lair", "Infestation Pit", "Spire"}
    assert len(names) == len(set(names)), (
        f"duplicate tracker: {names} - Tunneling Claws already requires "
        "Lair, so the base class's own upgrade-driven derivation adds one "
        "on its own; the override must not add a second"
    )
