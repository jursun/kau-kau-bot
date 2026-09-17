"""Regression tests for `BotContext.own_nat`/`production_location`.

Runs under pytest, or standalone with no test dependency:

    python -m tests.core.test_context
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from sc2.position import Point2

from bot.core.context import BotContext
from bot.core.state import RunState

START = Point2((10.0, 10.0))
NAT = Point2((20.0, 15.0))


def _ctx() -> BotContext:
    bot = MagicMock()
    bot.start_location = START
    return BotContext(bot=bot, build=MagicMock(), state=RunState())


def test_own_nat_returns_ares_value_when_expansions_are_known() -> None:
    ctx = _ctx()
    ctx.mediator.get_own_expansions = [(NAT, 10.0), (Point2((30.0, 30.0)), 20.0)]
    ctx.mediator.get_own_nat = NAT

    assert ctx.own_nat == NAT


def test_own_nat_falls_back_to_start_location_when_expansions_are_empty() -> None:
    """Regression test: `ManagerMediator.get_own_nat` indexes `[0]` into an
    internal list that ares leaves empty whenever `AresBot.arcade_mode` is
    True, or a map's own expansion data fails to resolve any reachable
    expansion at all - confirmed live as an `IndexError: list index out of
    range` crash from `TerrainManager.own_nat` mid-game. `own_nat` must
    check `get_own_expansions` (a plain list, safe to inspect) before ever
    touching the crash-prone `get_own_nat` property."""
    ctx = _ctx()
    ctx.mediator.get_own_expansions = []

    assert ctx.own_nat == START


def test_own_nat_falls_back_through_safe_start_location_when_start_location_is_none() -> None:
    """Regression test for the exact user report: `rally_point` crashed
    with `AttributeError: 'NoneType' object has no attribute 'towards'`
    because this fallback used to hand back `start_location` unchecked -
    see `safe_start_location`'s own tests for the fallback chain itself."""
    ctx = _ctx()
    ctx.mediator.get_own_expansions = []
    ctx.bot.start_location = None
    ctx.bot.townhalls.ready.first.position = NAT

    assert ctx.own_nat == NAT


def test_production_location_is_start_location_normally() -> None:
    ctx = _ctx()

    assert ctx.production_location == START


def test_production_location_falls_back_when_start_location_is_none() -> None:
    ctx = _ctx()
    ctx.bot.start_location = None
    ctx.bot.townhalls.ready.first.position = NAT

    assert ctx.production_location == NAT


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
