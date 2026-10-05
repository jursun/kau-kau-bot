"""`bot.common.home` and the `BotContext` fallbacks built on it: once the main
(or natural) falls in a base trade, "home" moves to a surviving base."""

from __future__ import annotations

from unittest.mock import MagicMock

from sc2.position import Point2

from bot.common import home
from bot.core.context import BotContext
from bot.core.state import RunState

START = Point2((10.0, 10.0))
NAT = Point2((30.0, 20.0))
THIRD = Point2((50.0, 40.0))
FOURTH = Point2((70.0, 60.0))
ENEMY = Point2((150.0, 150.0))


def _townhall(position: Point2, ready: bool = True) -> MagicMock:
    th = MagicMock()
    th.position = position
    th.is_ready = ready
    return th


def _ai(*positions: Point2) -> MagicMock:
    ai = MagicMock()
    ai.start_location = START
    ai.enemy_start_locations = [ENEMY]
    ai.townhalls = [_townhall(p) for p in positions]
    return ai


def _ctx(*positions: Point2) -> BotContext:
    ctx = BotContext(bot=_ai(*positions), build=MagicMock(), state=RunState())
    ctx.mediator.get_own_expansions = [(NAT, 10.0), (THIRD, 20.0)]
    ctx.mediator.get_own_nat = NAT
    return ctx


def test_main_is_start_while_a_townhall_stands_there() -> None:
    ctx = _ctx(START, NAT)

    assert ctx.production_location == START


def test_main_falls_back_to_the_base_farthest_from_the_enemy() -> None:
    # Main dead. Natural, third and fourth survive; the fourth is nearest the
    # enemy so the natural (farthest from them) is the safest.
    ctx = _ctx(NAT, THIRD, FOURTH)

    assert ctx.production_location == NAT


def test_main_prefers_a_ready_survivor_over_one_still_building() -> None:
    ai = _ai()
    ai.townhalls = [_townhall(NAT, ready=False), _townhall(THIRD, ready=True)]

    assert home.surviving_main(ai, START) == THIRD


def test_main_stays_start_when_no_base_survives() -> None:
    ctx = _ctx()

    assert ctx.production_location == START


def test_natural_is_static_before_it_was_ever_taken() -> None:
    """Rally / scout anchors must not jump to the main just because the
    natural doesn't exist yet."""
    ctx = _ctx(START)

    assert ctx.own_nat == NAT
    assert ctx.state.natural_established is False


def test_natural_latches_established_once_a_townhall_stands_there() -> None:
    ctx = _ctx(START, NAT)

    assert ctx.own_nat == NAT
    assert ctx.state.natural_established is True


def test_natural_falls_back_to_the_nearest_surviving_base_after_it_is_lost() -> None:
    ctx = _ctx(START, NAT, THIRD)
    assert ctx.own_nat == NAT  # established

    ctx.bot.townhalls = [_townhall(START), _townhall(THIRD)]

    assert ctx.own_nat == THIRD


def test_natural_falls_back_to_the_main_when_nothing_else_survives() -> None:
    ctx = _ctx(START, NAT)
    assert ctx.own_nat == NAT

    ctx.bot.townhalls = [_townhall(START)]

    assert ctx.own_nat == START


def test_both_main_and_natural_lost_rally_follows_the_survivor() -> None:
    ctx = _ctx(START, NAT, THIRD)
    assert ctx.own_nat == NAT

    ctx.bot.townhalls = [_townhall(THIRD)]

    assert ctx.production_location == THIRD
    # The natural's replacement is "any non-main base": the main is THIRD now,
    # so nothing else is left and it collapses onto it.
    assert ctx.own_nat == THIRD
