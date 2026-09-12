"""Regression tests for `TwoBaseChargelotAllInValidator`'s report shape."""

from __future__ import annotations

from sc2.data import Race
from sc2.ids.upgrade_id import UpgradeId

from tests.validators._fakes import FakeAI
from tests.validators.two_base_chargelot_all_in_validator import (
    TwoBaseChargelotAllInValidator,
)


def test_validate_returns_all_four_base_stages() -> None:
    ai = FakeAI(
        upgrades=(UpgradeId.CHARGE,),
        race=Race.Protoss,
        wave_stage_label="Chargelot All-In",
    )
    validator = TwoBaseChargelotAllInValidator(ai)
    validator.on_step(0)

    result = validator.validate()
    assert list(result) == [
        "Stage 1: Opening Economy",
        "Stage 2: Tech Structures",
        "Stage 3: Upgrades",
        "Stage 4: Chargelot All-In",
    ]


def test_report_never_includes_stage_1b() -> None:
    ai = FakeAI(upgrades=(UpgradeId.CHARGE,), race=Race.Protoss)
    validator = TwoBaseChargelotAllInValidator(ai)
    validator.on_step(0)

    assert "Stage 1B: Proxy Crew Choreography" not in validator.validate()


def test_stage_3_lists_charge() -> None:
    ai = FakeAI(upgrades=(UpgradeId.CHARGE,), race=Race.Protoss)
    validator = TwoBaseChargelotAllInValidator(ai)
    validator.on_step(0)

    result = validator.validate()
    assert [r.name for r in result["Stage 3: Upgrades"]] == ["Charge"]


def test_registry_resolves_this_validator() -> None:
    from tests.validators.registry import validator_for_build

    assert (
        validator_for_build("2base Chargelot All-In")
        is TwoBaseChargelotAllInValidator
    )
