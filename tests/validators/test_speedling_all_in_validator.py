"""Regression tests for `SpeedlingAllInValidator`'s own Validation Report
shape - mirrors `test_upgrade_rush_validator.py` closely, since the two
builds currently declare the same report shape, but exercises this build's
own class and file so a future change to either one is provably isolated to
its own test file too.
"""

from __future__ import annotations

from sc2.ids.upgrade_id import UpgradeId

from tests.validators._fakes import FakeAI
from tests.validators.speedling_all_in_validator import SpeedlingAllInValidator


def test_validate_returns_all_four_base_stages() -> None:
    ai = FakeAI(upgrades=(UpgradeId.ZERGLINGMOVEMENTSPEED,))
    validator = SpeedlingAllInValidator(ai)
    validator.on_step(0)

    result = validator.validate()
    assert list(result) == [
        "Stage 1: Opening Economy",
        "Stage 2: Tech Structures",
        "Stage 3: Upgrades",
        "Stage 4: Attack Waves",
    ]


def test_report_never_includes_stage_1b() -> None:
    ai = FakeAI(upgrades=(UpgradeId.ZERGLINGMOVEMENTSPEED,))
    validator = SpeedlingAllInValidator(ai)
    validator.on_step(0)

    assert "Stage 1B: Proxy Crew Choreography" not in validator.validate()


def test_stage_2_falls_back_since_metabolic_boost_needs_no_structure() -> None:
    """This build's only upgrade (Metabolic Boost) needs no Evolution
    Chamber, Lair or Hive - Stage 2 should say so plainly rather than
    showing zero checks or failing ones it could never pass."""
    ai = FakeAI(upgrades=(UpgradeId.ZERGLINGMOVEMENTSPEED,))
    validator = SpeedlingAllInValidator(ai)
    validator.on_step(0)

    result = validator.validate()
    assert [r.name for r in result["Stage 2: Tech Structures"]] == [
        "No tech structures required by this build"
    ]
    assert [r.name for r in result["Stage 3: Upgrades"]] == ["Metabolic Boost"]


def test_report_title_stays_attack_waves() -> None:
    ai = FakeAI(upgrades=(UpgradeId.ZERGLINGMOVEMENTSPEED,))
    validator = SpeedlingAllInValidator(ai)
    validator.on_step(0)

    result = validator.validate()
    assert "Stage 4: Attack Waves" in result
    assert "Stage 4: All-In Attack" not in result
