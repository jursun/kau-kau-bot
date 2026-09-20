"""Light econ pause: no 4th/overflow/Pit/Spire while early_aggression is on."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from sc2.position import Point2

from bot.core.context import BotContext
from bot.core.state import RunState
from bot.builds.zerg import macro_zerg
from bot.routines import gates


def _ctx(*, early: bool, opening_index: int) -> BotContext:
    bot = MagicMock()
    bot.start_location = Point2((20.0, 20.0))
    build = MagicMock()
    build.economy.max_bases = 5
    ctx = BotContext(bot=bot, build=build, state=RunState())
    ctx.state.early_aggression = early
    ctx.state.opening_step_index = opening_index
    return ctx


def test_expansions_hold_under_early_aggression() -> None:
    third = macro_zerg._THIRD_EXPAND_INDEX
    ctx = _ctx(early=True, opening_index=third + 1)
    assert macro_zerg._expansions_after_scripted_third(ctx) is None


def test_expansions_resume_when_latch_clears() -> None:
    third = macro_zerg._THIRD_EXPAND_INDEX
    ctx = _ctx(early=False, opening_index=third + 1)
    with patch.object(
        macro_zerg.c, "expansions", return_value=lambda ctx: "EXPAND"
    ) as expansions:
        result = macro_zerg._expansions_after_scripted_third(ctx)
    assert result == "EXPAND"
    expansions.assert_called_once()


def test_overflow_holds_under_early_aggression() -> None:
    ctx = _ctx(early=True, opening_index=len(macro_zerg._SEQUENCE))
    assert macro_zerg._overflow_after_scripted_opening(ctx) is None


def test_overflow_still_waits_for_opening() -> None:
    ctx = _ctx(early=False, opening_index=0)
    assert macro_zerg._overflow_after_scripted_opening(ctx) is None


def test_negate_early_aggression_gate() -> None:
    ctx = _ctx(early=True, opening_index=0)
    assert gates.negate(gates.early_aggression())(ctx) is False
    ctx.state.early_aggression = False
    assert gates.negate(gates.early_aggression())(ctx) is True


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
        except Exception as error:  # noqa: BLE001
            failures += 1
            print(f"  FAIL  {test.__name__}: {error}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
