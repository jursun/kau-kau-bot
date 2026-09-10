"""Regression tests for `UpgradeRushValidator`'s own report shape: full four
stages, no Stage 1B, titled "Attack Waves". Generic tracking mechanics are
covered once in `test_base_validator.py`.
"""

from __future__ import annotations

from sc2.ids.upgrade_id import UpgradeId

from tests.validators._fakes import FakeAI
from tests.validators.upgrade_rush_validator import UpgradeRushValidator


def test_validate_returns_all_four_base_stages() -> None:
    ai = FakeAI(upgrades=(UpgradeId.ZERGLINGMOVEMENTSPEED,))
    validator = UpgradeRushValidator(ai)
    validator.on_step(0)

    result = validator.validate()
    assert list(result) == [
        "Stage 1: Opening Economy",
        "Stage 2: Tech Structures",
        "Stage 3: Upgrades",
        "Stage 4: Attack Waves",
    ]


def test_report_never_includes_stage_1b() -> None:
    """Regression test: Stage 1B used to leak in from the old shared
    validator class. `UpgradeRushValidator.validate()` structurally never
    calls `_validate_crew()`, so this key can't appear regardless of
    `ctx.build.crew`."""
    ai = FakeAI(upgrades=(UpgradeId.ZERGLINGMOVEMENTSPEED,))
    validator = UpgradeRushValidator(ai)
    validator.on_step(0)

    assert "Stage 1B: Proxy Crew Choreography" not in validator.validate()


def test_report_title_stays_attack_waves() -> None:
    """Regression test: Stage 4's title used to leak "All-In Attack" from
    `Four Rax Proxy` into every build. This file only reads its own build's
    `wave_stage_label`."""
    ai = FakeAI(upgrades=())
    validator = UpgradeRushValidator(ai)
    validator.on_step(0)

    result = validator.validate()
    assert "Stage 4: Attack Waves" in result
    assert "Stage 4: All-In Attack" not in result
