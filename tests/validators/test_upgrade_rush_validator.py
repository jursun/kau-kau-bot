"""Regression tests for `UpgradeRushValidator`'s own Validation Report
shape: the full four stages, no Stage 1B, titled from this build's own
`wave_stage_label` (the default "Attack Waves").

Generic tracking mechanics are covered once in `test_base_validator.py` -
these tests are specifically about what this build's report shows and, just
as importantly, what it never picks up from another build's own file.
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
    """Regression test for the leak this validator split fixes: Stage 1B
    used to be added unconditionally by the one shared validator class, so
    `UpgradeRush` (no `ProxyCrewPlan`) picked up a trivially-passing "No
    proxy crew declared" line it never had before `Four Rax Proxy`'s crew
    mechanism existed. `UpgradeRushValidator.validate()` structurally never
    calls `_validate_crew()` at all now, so there's no runtime condition
    left to get wrong - this key cannot appear no matter what `ctx.build.
    crew` is set to."""
    ai = FakeAI(upgrades=(UpgradeId.ZERGLINGMOVEMENTSPEED,))
    validator = UpgradeRushValidator(ai)
    validator.on_step(0)

    assert "Stage 1B: Proxy Crew Choreography" not in validator.validate()


def test_report_title_stays_attack_waves() -> None:
    """Regression test for the other half of the same leak: Stage 4 used to
    get globally renamed to "All-In Attack" for every build when that
    change was made "for" `Four Rax Proxy`. This build's own file only ever
    reads its own build's `wave_stage_label` (default "Attack Waves")."""
    ai = FakeAI(upgrades=())
    validator = UpgradeRushValidator(ai)
    validator.on_step(0)

    result = validator.validate()
    assert "Stage 4: Attack Waves" in result
    assert "Stage 4: All-In Attack" not in result
