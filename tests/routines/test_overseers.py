"""Regression: Overseer thrash harden (watch-first scout, sticky no re-issue).

    python -m tests.routines.test_overseers
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from ares.behaviors.combat.individual import KeepUnitSafe, PathUnitToTarget
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.core.context import BotContext
from bot.core.state import RunState
from bot.routines import overseers


class _Units(list):
    @property
    def ready(self) -> "_Units":
        return self


def _unit(tag: int, pos: Point2) -> MagicMock:
    unit = MagicMock()
    unit.tag = tag
    unit.position = pos
    unit.orders = []
    unit.order_target = None
    unit.is_moving = False
    unit.type_id = UnitTypeId.OVERSEER
    unit.abilities = []
    return unit


def _ctx(*, overseers_list: list[MagicMock] | None = None) -> BotContext:
    bot = MagicMock()
    home = Point2((10.0, 10.0))
    enemy = Point2((90.0, 90.0))
    bot.start_location = home
    bot.enemy_start_locations = [enemy]
    bot.expansion_locations_list = [
        home,
        Point2((80.0, 80.0)),
        Point2((70.0, 70.0)),
        Point2((60.0, 90.0)),
    ]
    bot.owned_expansions = {home: MagicMock()}
    bot.enemy_structures = MagicMock()
    bot.enemy_structures.of_type = MagicMock(return_value=[])
    ols = list(overseers_list or [])
    bot.units = MagicMock(side_effect=lambda t: _Units(ols if t == UnitTypeId.OVERSEER else []))

    state = RunState()
    ctx = BotContext(bot=bot, build=MagicMock(), state=state)
    ctx.mediator.get_air_grid = "air-grid"
    ctx.mediator.get_squads = MagicMock(return_value=[])
    return ctx


def test_scout_uses_path_not_keep_unit_safe() -> None:
    scout = _unit(7, Point2((40.0, 40.0)))
    ctx = _ctx(overseers_list=[scout])
    ctx.state.overseer_scout_tag = 7
    ctx.state.overseer_home_tag = None
    ctx.state.overseer_army_tag = None

    overseers.manage_overseers()(ctx)

    behaviors = [c.args[0] for c in ctx.bot.register_behavior.call_args_list]
    assert behaviors, "expected a scout move"
    assert any(isinstance(b, PathUnitToTarget) for b in behaviors)
    assert not any(isinstance(b, KeepUnitSafe) for b in behaviors)
    for behavior in behaviors:
        assert type(behavior).__name__ != "CombatManeuver"


def test_skip_reissue_when_already_on_dest() -> None:
    ctx = _ctx()
    home = Point2((12.0, 12.0))
    ov = _unit(3, home)
    overseers._safe_air_move(ctx, ov, home)
    assert ctx.bot.register_behavior.call_count == 0


def test_scout_dest_sticky_across_frames() -> None:
    ctx = _ctx()
    scout = _unit(7, Point2((40.0, 40.0)))
    first = overseers._scout_target(ctx, scout)
    scout.position = Point2((41.0, 39.0))
    second = overseers._scout_target(ctx, scout)
    assert first == second


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
