"""Regression tests for `FourRaxProxyValidator`'s own report shape: Stage 1B
present, Stage 2/3 absent, Stage 4 titled from this build's own
`wave_stage_label`. Generic tracking mechanics are covered once in
`test_base_validator.py`.
"""

from __future__ import annotations

from sc2.data import Race

from tests.validators._fakes import FakeAI, _fake_crew_plan
from tests.validators.four_rax_proxy_validator import FourRaxProxyValidator


def test_a_build_with_no_crew_plan_still_gets_stage_1b_with_a_fallback_line() -> None:
    """`FourRaxProxyValidator.validate()` always includes Stage 1B,
    regardless of whether `ctx.build.crew` is set - this covers the
    defensive fallback line in `BaseValidator._validate_crew`."""
    ai = FakeAI(race=Race.Terran)  # crew defaults to None
    validator = FourRaxProxyValidator(ai)
    validator.on_step(0)

    result = validator.validate()
    assert "Stage 1B: Proxy Crew Choreography" in result
    assert result["Stage 1B: Proxy Crew Choreography"] == [
        r
        for r in result["Stage 1B: Proxy Crew Choreography"]
        if r.name == "No proxy crew declared by this build"
    ]


def test_validate_never_includes_stage_2_or_3() -> None:
    """`validate()` never adds these keys, structurally, regardless of what
    `ctx.build.army.upgrades` contains."""
    ai = FakeAI(race=Race.Terran, crew=_fake_crew_plan())
    validator = FourRaxProxyValidator(ai)
    validator.on_step(0)

    result = validator.validate()
    assert "Stage 2: Tech Structures" not in result
    assert "Stage 3: Upgrades" not in result


def test_stage_4_title_comes_from_the_builds_own_wave_stage_label() -> None:
    """`Four Rax Proxy` sets `combat.wave_stage_label="All-In Attack"` -
    this report reflects that without any conditional logic."""
    ai = FakeAI(race=Race.Terran, wave_stage_label="All-In Attack")
    validator = FourRaxProxyValidator(ai)
    validator.on_step(0)

    result = validator.validate()
    assert "Stage 4: All-In Attack" in result
    assert "Stage 4: Attack Waves" not in result


def test_crew_stage_lists_every_declared_claim_and_task() -> None:
    ai = FakeAI(race=Race.Terran, crew=_fake_crew_plan())
    validator = FourRaxProxyValidator(ai)
    validator.on_step(0)

    names = [r.name for r in validator.validate()["Stage 1B: Proxy Crew Choreography"]]
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
    validator = FourRaxProxyValidator(ai)
    validator.on_step(0)

    x_claim = next(
        r
        for r in validator.validate()["Stage 1B: Proxy Crew Choreography"]
        if r.name == "Crew X claimed"
    )
    assert not x_claim.passed

    ai.ctx.state.proxy_crew.x.tag = 111
    ai.time = 0.2
    validator.on_step(0)

    x_claim = next(
        r
        for r in validator.validate()["Stage 1B: Proxy Crew Choreography"]
        if r.name == "Crew X claimed"
    )
    assert x_claim.passed
    assert "0.2" in x_claim.detail


def test_crew_task_tracks_started_then_completed_without_double_counting() -> None:
    """Barracks A (X) and Barracks B (Y) both start/finish moments apart -
    the tracker must key off (member, index), not structure type, or one
    would read as the other's completion."""
    ai = FakeAI(race=Race.Terran, crew=_fake_crew_plan())
    validator = FourRaxProxyValidator(ai)
    ai.ctx.state.proxy_crew.x.tag = 1
    ai.ctx.state.proxy_crew.y.tag = 2
    ai.time = 5.0
    ai.ctx.state.proxy_crew.x.queued = True  # Barracks A issued
    validator.on_step(0)

    stage = {
        r.name: r for r in validator.validate()["Stage 1B: Proxy Crew Choreography"]
    }
    assert not stage["Barracks A"].passed
    assert "started" in stage["Barracks A"].detail
    assert not stage["Barracks B"].passed  # Y hasn't even started yet

    # Barracks A completes (task_index advances); Barracks B still hasn't.
    ai.ctx.state.proxy_crew.x.queued = False
    ai.ctx.state.proxy_crew.x.task_index = 1
    ai.time = 40.0
    validator.on_step(0)

    stage = {
        r.name: r for r in validator.validate()["Stage 1B: Proxy Crew Choreography"]
    }
    assert stage["Barracks A"].passed
    assert "40.0" in stage["Barracks A"].detail
    assert not stage["Barracks B"].passed


def test_crew_tasks_are_reported_in_completion_order_not_declared_order() -> None:
    """`_fake_crew_plan()`'s X declares Barracks A then Barracks D, but the
    real build gates Barracks D on Marine training, so it can finish after
    Y's Depot (proxy) despite sitting earlier in X's list. The report must
    reflect actual completion order, not each member's declared order."""
    ai = FakeAI(race=Race.Terran, crew=_fake_crew_plan())
    validator = FourRaxProxyValidator(ai)
    crew = ai.ctx.state.proxy_crew
    ai.time = 10.0
    validator.on_step(0)

    crew.x.task_index = 1  # Barracks A (X's first task) completes
    ai.time = 20.0
    validator.on_step(0)

    crew.y.task_index = 2  # Depot (proxy) (Y's second task) completes
    ai.time = 50.0
    validator.on_step(0)

    crew.x.task_index = 2  # Barracks D (X's second task) finally completes
    ai.time = 80.0
    validator.on_step(0)

    names = [r.name for r in validator.validate()["Stage 1B: Proxy Crew Choreography"]]
    assert names.index("Barracks A") < names.index("Depot (proxy)")
    assert names.index("Depot (proxy)") < names.index("Barracks D")
