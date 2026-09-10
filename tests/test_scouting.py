"""Regression tests for `bot/routines/scouting.py`.

`air_scout` used to wrap its `PathUnitToTarget` in a `KeepUnitSafe`-first
`CombatManeuver`, the same danger-avoidance shape `escort_overseers` uses.
That's wrong for this routine specifically: its only ever user is the
opening scouting Overlord (see `core/roles.py`'s `SCOUT_TYPES`), which is
supposed to sit and watch a vision spot, not retreat the instant something
worth watching shows up. These pin down that it now registers a bare
`PathUnitToTarget` with no danger-avoidance step ahead of it.

Runs under pytest, or standalone with no test dependency:

    python -m tests.test_scouting
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from ares.behaviors.combat.individual import PathUnitToTarget
from sc2.position import Point2

from bot.core.context import BotContext
from bot.core.state import RunState
from bot.routines import scouting


def _ctx() -> BotContext:
    return BotContext(bot=MagicMock(), build=MagicMock(), state=RunState())


def _unit(tag: int) -> MagicMock:
    unit = MagicMock()
    unit.tag = tag
    return unit


def test_air_scout_registers_a_bare_path_with_no_danger_avoidance() -> None:
    ctx = _ctx()
    scout = _unit(7)
    ctx.mediator.get_units_from_role.return_value = [scout]
    ctx.mediator.get_air_grid = "air-grid"
    destination = Point2((30.0, 40.0))

    scouting.air_scout(unit_type=None, target=lambda _ctx: destination)(ctx)

    ctx.bot.register_behavior.assert_called_once()
    registered = ctx.bot.register_behavior.call_args.args[0]
    # A bare individual behavior, not a CombatManeuver wrapping a
    # KeepUnitSafe-then-PathUnitToTarget pair - nothing here should retreat.
    assert isinstance(registered, PathUnitToTarget)
    assert registered.unit is scout
    assert registered.target == destination
    assert registered.grid == "air-grid"


def test_air_scout_does_nothing_without_a_scout() -> None:
    ctx = _ctx()
    ctx.mediator.get_units_from_role.return_value = []

    scouting.air_scout(unit_type=None)(ctx)

    ctx.bot.register_behavior.assert_not_called()


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
