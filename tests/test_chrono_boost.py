"""Regression tests for Chrono Boost: target/cooldown selection in
`steps.protoss.chrono_boost_army`, and the cast itself in
`ProtossChronoBoost`.

Runs under pytest, or standalone with no test dependency:

    python -m tests.test_chrono_boost
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.behaviors.protoss import ProtossChronoBoost
from bot.core.context import BotContext
from bot.core.state import RunState
from bot.steps import protoss as p

NAT = Point2((60.0, 60.0))
NOT_NAT = Point2((0.0, 0.0))
"""Anywhere far from NAT, so `cy_closest_to(NAT, [...])` never mistakes a
plain "extra nexus" fixture for the natural in a multi-nexus test."""


def _nexus(
    tag: int, energy: int = 0, *, position: Point2 = NOT_NAT, training: bool = False
) -> MagicMock:
    nexus = MagicMock()
    nexus.tag = tag
    nexus.energy = energy
    nexus.position = position
    nexus.orders = [MagicMock()] if training else []
    return nexus


def _robo(tag: int, *, training: bool = False) -> MagicMock:
    robo = MagicMock()
    robo.tag = tag
    robo.type_id = UnitTypeId.ROBOTICSFACILITY
    robo.orders = [MagicMock()] if training else []
    return robo


def _warpgate(tag: int) -> MagicMock:
    wg = MagicMock()
    wg.tag = tag
    wg.type_id = UnitTypeId.WARPGATE
    return wg


def _ctx(*, time: float = 300.0, nexi=(), robos=(), warpgates=()) -> BotContext:
    bot = MagicMock()
    bot.time = time
    bot.townhalls.ready = list(nexi)
    bot.structures.side_effect = lambda t: MagicMock(
        ready=list(robos) if t == UnitTypeId.ROBOTICSFACILITY else list(warpgates)
    )
    build = MagicMock()
    ctx = BotContext(bot=bot, build=build, state=RunState())
    ctx.mediator.get_own_nat = NAT
    return ctx


def _step(ctx: BotContext):
    """`ctx.build_completed` wraps `bot.build_order_runner`; stub it True."""
    ctx.bot.build_order_runner.build_completed = True
    return p.chrono_boost_army()(ctx)


def test_returns_none_before_the_opening_completes() -> None:
    ctx = _ctx(nexi=[_nexus(1, energy=60)], warpgates=[_warpgate(2)])
    ctx.bot.build_order_runner.build_completed = False

    assert p.chrono_boost_army()(ctx) is None


def test_returns_none_when_no_nexus_has_enough_energy() -> None:
    ctx = _ctx(nexi=[_nexus(1, energy=49)], robos=[_robo(2, training=True)])

    assert _step(ctx) is None


def test_returns_none_when_nothing_to_boost() -> None:
    ctx = _ctx(nexi=[_nexus(1, energy=60)])

    assert _step(ctx) is None


# --- priority 1: the natural boosting its own probe production -------------


def test_natural_boosts_itself_when_training_a_probe() -> None:
    natural = _nexus(1, energy=60, position=NAT, training=True)
    ctx = _ctx(nexi=[natural])

    behavior = _step(ctx)

    assert behavior is not None
    assert behavior.caster is natural
    assert behavior.target is natural


def test_natural_self_boost_does_not_borrow_energy_from_another_nexus() -> None:
    """"Boost itself" means only its own energy counts - a energized main
    must not be spent on the natural's behalf."""
    energized_main = _nexus(1, energy=200, position=NOT_NAT, training=False)
    empty_natural = _nexus(2, energy=0, position=NAT, training=True)
    ctx = _ctx(nexi=[energized_main, empty_natural])

    assert _step(ctx) is None


def test_natural_is_not_boosted_when_not_training() -> None:
    """An idle natural (workers already capped) falls through to Robo/Gate -
    chrono-ing it would just waste the cast on nothing being produced."""
    idle_natural = _nexus(1, energy=60, position=NAT, training=False)
    warpgate = _warpgate(2)
    ctx = _ctx(nexi=[idle_natural], warpgates=[warpgate])

    behavior = _step(ctx)

    assert behavior.target is warpgate


def test_natural_self_boost_outranks_robo_and_warp_gate() -> None:
    natural = _nexus(1, energy=60, position=NAT, training=True)
    robo = _robo(2, training=True)
    ctx = _ctx(nexi=[natural], robos=[robo])

    behavior = _step(ctx)

    assert behavior.target is natural


def test_natural_self_boost_respects_its_own_cooldown() -> None:
    natural = _nexus(1, energy=200, position=NAT, training=True)
    warpgate = _warpgate(2)
    ctx = _ctx(time=300.0, nexi=[natural], warpgates=[warpgate])

    first = _step(ctx)
    assert first.target is natural

    # Natural is on cooldown now; falls through to the warp gate instead of
    # returning None (it still has energy, just not for self-boosting).
    second = _step(ctx)
    assert second is not None
    assert second.target is warpgate


# --- priority 2/3: Robo, then Warp Gate -------------------------------------


def test_prefers_a_training_robo_over_a_warp_gate() -> None:
    caster = _nexus(1, energy=60)
    robo = _robo(2, training=True)
    warpgate = _warpgate(3)
    ctx = _ctx(nexi=[caster], robos=[robo], warpgates=[warpgate])

    behavior = _step(ctx)

    assert behavior.caster is caster
    assert behavior.target is robo


def test_falls_back_to_a_warp_gate_when_robo_is_not_training() -> None:
    caster = _nexus(1, energy=60)
    idle_robo = _robo(2, training=False)
    warpgate = _warpgate(3)
    ctx = _ctx(nexi=[caster], robos=[idle_robo], warpgates=[warpgate])

    behavior = _step(ctx)

    assert behavior.target is warpgate


def test_does_not_retarget_the_same_tag_within_the_cooldown() -> None:
    """This is the regression case: without cooldown tracking, the same
    structure got re-Chrono-Boosted every single frame for a full minute,
    since `has_buff` was observed not to reflect a just-issued cast on the
    very next frame."""
    caster = _nexus(1, energy=200)
    warpgate = _warpgate(2)
    ctx = _ctx(time=300.0, nexi=[caster], warpgates=[warpgate])

    first = _step(ctx)
    assert first is not None and first.target is warpgate

    # One frame later - well within the 20s Chrono Boost duration.
    ctx.bot.time = 300.5
    assert _step(ctx) is None


def test_retargets_the_same_tag_once_the_cooldown_expires() -> None:
    caster = _nexus(1, energy=200)
    warpgate = _warpgate(2)
    ctx = _ctx(time=300.0, nexi=[caster], warpgates=[warpgate])

    _step(ctx)
    ctx.bot.time = 300.0 + p.CHRONO_DURATION_S
    second = _step(ctx)

    assert second is not None
    assert second.target is warpgate


def test_cooldown_is_tracked_per_tag_not_globally() -> None:
    caster = _nexus(1, energy=200)
    warpgate_a = _warpgate(2)
    warpgate_b = _warpgate(3)
    ctx = _ctx(time=300.0, nexi=[caster], warpgates=[warpgate_a, warpgate_b])

    first = _step(ctx)
    assert first.target is warpgate_a

    # warpgate_a is now on cooldown; warpgate_b is still free this same frame.
    second = _step(ctx)
    assert second is not None
    assert second.target is warpgate_b


def test_protoss_chrono_boost_casts_and_returns_true() -> None:
    caster = _nexus(1, energy=150)
    target = _warpgate(2)

    did_act = ProtossChronoBoost(caster=caster, target=target).execute(
        MagicMock(), {}, MagicMock()
    )

    assert did_act is True
    caster.assert_called_once_with(AbilityId.EFFECT_CHRONOBOOST, target)


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
