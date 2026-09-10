"""Regression tests for `routines.creep.spread_creep`.

Runs under pytest, or standalone with no test dependency:

    python -m tests.test_creep
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from ares.behaviors.combat.individual import QueenSpreadCreep, TumorSpreadCreep
from ares.consts import UnitRole
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.core.context import BotContext
from bot.core.state import RunState
from bot.routines import creep


def _queen(tag: int) -> MagicMock:
    queen = MagicMock()
    queen.tag = tag
    return queen


def _ctx(townhall_count: int = 3) -> BotContext:
    bot = MagicMock()
    bot.townhalls.ready = [MagicMock() for _ in range(townhall_count)]
    build = MagicMock()
    return BotContext(bot=bot, build=build, state=RunState())


def _role_lookup(creep_queens=(), injectors=()):
    def get_units_from_role(*, role, unit_type):
        if role == UnitRole.QUEEN_CREEP:
            return list(creep_queens)
        if role == UnitRole.QUEEN_INJECT:
            return list(injectors)
        raise AssertionError(f"unexpected role {role}")

    return get_units_from_role


def test_no_queen_promoted_until_one_is_spare_beyond_one_per_base() -> None:
    # 3 bases, exactly 3 injectors — none to spare for creep yet.
    ctx = _ctx(townhall_count=3)
    injectors = [_queen(i) for i in range(3)]
    ctx.mediator.get_units_from_role.side_effect = _role_lookup(injectors=injectors)

    creep.spread_creep()(ctx)

    ctx.mediator.assign_role.assert_not_called()
    ctx.bot.register_behavior.assert_not_called()


def test_newest_injector_promoted_once_one_is_spare() -> None:
    # 3 bases, 4 injectors — one to spare. The newest (highest tag) is promoted.
    ctx = _ctx(townhall_count=3)
    injectors = [_queen(t) for t in (5, 9, 2, 1)]
    ctx.mediator.get_units_from_role.side_effect = _role_lookup(injectors=injectors)

    creep.spread_creep()(ctx)

    ctx.mediator.assign_role.assert_called_once_with(tag=9, role=UnitRole.QUEEN_CREEP)


def test_creep_queen_gets_driven_once_promoted() -> None:
    ctx = _ctx()
    queen = _queen(9)
    ctx.mediator.get_units_from_role.side_effect = _role_lookup(creep_queens=[queen])

    creep.spread_creep()(ctx)

    ctx.mediator.assign_role.assert_not_called()
    registered = ctx.bot.register_behavior.call_args.args[0]
    assert isinstance(registered, QueenSpreadCreep)
    assert registered.unit is queen


def _tumor(tag: int) -> MagicMock:
    tumor = MagicMock()
    tumor.tag = tag
    return tumor


def test_spread_tumors_does_nothing_without_a_burrowed_tumor() -> None:
    ctx = _ctx()
    ctx.mediator.get_own_structures_dict = {UnitTypeId.CREEPTUMORBURROWED: []}

    creep.spread_tumors()(ctx)

    ctx.bot.register_behavior.assert_not_called()


def test_spread_tumors_drives_every_burrowed_tumor_toward_the_enemy() -> None:
    ctx = _ctx()
    tumors = [_tumor(1), _tumor(2)]
    ctx.mediator.get_own_structures_dict = {UnitTypeId.CREEPTUMORBURROWED: tumors}
    enemy_start = Point2((123.0, 45.0))
    ctx.bot.enemy_start_locations = [enemy_start]

    creep.spread_tumors()(ctx)

    registered = [call.args[0] for call in ctx.bot.register_behavior.call_args_list]
    assert len(registered) == 2
    for behavior, tumor in zip(registered, tumors):
        assert isinstance(behavior, TumorSpreadCreep)
        assert behavior.unit is tumor
        assert behavior.target == enemy_start


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
