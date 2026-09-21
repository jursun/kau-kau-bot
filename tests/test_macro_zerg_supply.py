"""Regression: auto_supply must stay open after scripted Overlords finish.

Losing or morphing an Overlord used to drop the live count below 6 and
flip `_scripted_overlords_exhausted` false, which shut off `c.auto_supply`
and pooled minerals under a supply block.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from sc2.ids.unit_typeid import UnitTypeId

from bot.builds.zerg import macro_zerg as mz
from bot.core.context import BotContext
from bot.core.state import RunState


def _ctx(*, opening_index: int = 0, overlords: int = 0, overseers: int = 0) -> BotContext:
    build = MagicMock()
    ctx = BotContext(bot=MagicMock(), build=build, state=RunState())
    ctx.state.opening_step_index = opening_index
    ctx.bot.already_pending.return_value = 0

    def _units(unit_type):
        amount = 0
        if unit_type == UnitTypeId.OVERLORD:
            amount = overlords
        elif unit_type in (UnitTypeId.OVERSEER, UnitTypeId.OVERSEERSIEGEMODE):
            amount = overseers if unit_type == UnitTypeId.OVERSEER else 0
        result = MagicMock()
        result.amount = amount
        return result

    ctx.bot.units = MagicMock(side_effect=_units)
    return ctx


def test_auto_supply_gate_stays_open_after_overlord_dies_past_opening() -> None:
    """Past the last scripted Overlord step, a kill must not re-close the gate."""
    ctx = _ctx(
        opening_index=mz._LAST_OVERLORD_STEP_INDEX + 1,
        overlords=5,  # one died
    )
    assert mz._scripted_overlords_exhausted(ctx) is True


def test_auto_supply_gate_closed_while_still_on_scripted_overlords() -> None:
    ctx = _ctx(opening_index=mz._LAST_OVERLORD_STEP_INDEX, overlords=4)
    assert mz._scripted_overlords_exhausted(ctx) is False


def test_auto_supply_gate_opens_on_provider_count_including_overseers() -> None:
    """Morphing Overlord→Overseer still counts toward the scripted total."""
    ctx = _ctx(opening_index=0, overlords=5, overseers=1)
    assert mz._scripted_overlords_exhausted(ctx) is True


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
