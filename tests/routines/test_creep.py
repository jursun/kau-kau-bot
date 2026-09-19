"""Regression tests for `routines.creep` highway spread.

Runs under pytest, or standalone with no test dependency:

    python -m tests.routines.test_creep
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from ares.consts import UnitRole
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.core.context import BotContext
from bot.core.state import RunState
from bot.routines import creep


def _queen(tag: int, position: Point2 | None = None) -> MagicMock:
    queen = MagicMock()
    queen.tag = tag
    queen.position = position or Point2((10.0, 10.0))
    queen.abilities = {AbilityId.BUILD_CREEPTUMOR_QUEEN}
    queen.is_using_ability.return_value = False
    return queen


def _ctx(townhall_count: int = 3) -> BotContext:
    bot = MagicMock()
    bot.start_location = Point2((20.0, 20.0))
    bot.enemy_start_locations = [Point2((100.0, 100.0))]
    bot.townhalls.ready = []
    for i in range(townhall_count):
        th = MagicMock()
        th.position = Point2((20.0 + i * 30.0, 20.0))
        bot.townhalls.ready.append(th)
    bot.config = {}
    build = MagicMock()
    ctx = BotContext(bot=bot, build=build, state=RunState())
    # Opening claim fields default False/None on RunState; tests that
    # set main_queen_tag also leave natural unset (idle skip).
    ctx.mediator.get_own_nat = Point2((50.0, 20.0))
    ctx.mediator.get_creep_grid = object()
    ctx.mediator.get_ground_grid = object()
    ctx.mediator.should_calculate_tumor_spread = True
    ctx.mediator.get_next_tumor_on_path.return_value = Point2((25.0, 20.0))
    ctx.mediator.find_nearby_creep_edge_position.return_value = None
    return ctx


def _role_lookup(creep_queens=(), injectors=()):
    def get_units_from_role(*, role, unit_type):
        if role == UnitRole.QUEEN_CREEP:
            return list(creep_queens)
        if role == UnitRole.QUEEN_INJECT:
            return list(injectors)
        raise AssertionError(f"unexpected role {role}")

    return get_units_from_role


def test_no_queen_promoted_until_one_is_spare_beyond_one_per_base() -> None:
    ctx = _ctx(townhall_count=3)
    injectors = [_queen(i) for i in range(3)]
    ctx.mediator.get_units_from_role.side_effect = _role_lookup(injectors=injectors)

    creep.spread_creep()(ctx)

    ctx.mediator.assign_role.assert_not_called()


def test_newest_injector_promoted_once_one_is_spare() -> None:
    ctx = _ctx(townhall_count=3)
    injectors = [_queen(t) for t in (5, 9, 2, 1)]
    ctx.mediator.get_units_from_role.side_effect = _role_lookup(injectors=injectors)

    creep.spread_creep()(ctx)

    ctx.mediator.assign_role.assert_called_once_with(tag=9, role=UnitRole.QUEEN_CREEP)


def test_creep_queen_plants_toward_highway_not_enemy_nat() -> None:
    """Creep Queen uses get_next_tumor_on_path toward an own base gap."""
    import bot.routines.creep as creep_mod

    ctx = _ctx(townhall_count=3)
    queen = _queen(9, Point2((20.0, 20.0)))
    ctx.mediator.get_units_from_role.side_effect = _role_lookup(creep_queens=[queen])
    # Nat (50,20) and third (80,20) lack creep; highway should aim nat first.
    creep_mod.cy_has_creep = lambda grid, pos: False  # type: ignore[attr-defined]

    creep.spread_creep()(ctx)

    ctx.mediator.get_next_tumor_on_path.assert_called()
    kwargs = ctx.mediator.get_next_tumor_on_path.call_args.kwargs
    assert kwargs["to_pos"] == Point2((50.0, 20.0))
    queen.assert_called()  # cast BUILD_CREEPTUMOR_QUEEN


def test_spread_creep_skips_main_opening_claim_queen() -> None:
    import bot.routines.creep as creep_mod

    ctx = _ctx()
    main_claim = _queen(3)
    other = _queen(9, Point2((20.0, 20.0)))
    ctx.state.main_queen_tag = 3
    ctx.state.main_queen_tumor_done = False
    ctx.mediator.get_units_from_role.side_effect = _role_lookup(
        creep_queens=[main_claim, other]
    )
    creep_mod.cy_has_creep = lambda grid, pos: False  # type: ignore[attr-defined]

    creep.spread_creep()(ctx)

    # Drove the non-claim queen; main claim untouched.
    assert other.called or ctx.mediator.get_next_tumor_on_path.called
    main_claim.assert_not_called()


def test_spread_creep_idle_when_only_main_claim_queen() -> None:
    ctx = _ctx()
    main_claim = _queen(3)
    ctx.state.main_queen_tag = 3
    ctx.state.main_queen_tumor_done = False
    ctx.mediator.get_units_from_role.side_effect = _role_lookup(
        creep_queens=[main_claim]
    )

    creep.spread_creep()(ctx)

    ctx.mediator.get_next_tumor_on_path.assert_not_called()
    main_claim.assert_not_called()


def test_spread_creep_skips_natural_opening_claim_queen() -> None:
    import bot.routines.creep as creep_mod

    ctx = _ctx()
    natural_claim = _queen(5)
    other = _queen(9, Point2((20.0, 20.0)))
    ctx.state.natural_queen_tag = 5
    ctx.state.natural_queen_tumor_done = False
    ctx.mediator.get_units_from_role.side_effect = _role_lookup(
        creep_queens=[natural_claim, other]
    )
    creep_mod.cy_has_creep = lambda grid, pos: False  # type: ignore[attr-defined]

    creep.spread_creep()(ctx)

    assert other.called or ctx.mediator.get_next_tumor_on_path.called
    natural_claim.assert_not_called()


def _tumor(tag: int, position: Point2 | None = None) -> MagicMock:
    tumor = MagicMock()
    tumor.tag = tag
    tumor.position = position or Point2((20.0, 20.0))
    tumor.abilities = {AbilityId.BUILD_CREEPTUMOR_TUMOR}
    return tumor


def test_spread_tumors_does_nothing_without_a_burrowed_tumor() -> None:
    ctx = _ctx()
    ctx.mediator.get_own_structures_dict = {UnitTypeId.CREEPTUMORBURROWED: []}

    creep.spread_tumors()(ctx)

    ctx.mediator.get_next_tumor_on_path.assert_not_called()


def test_spread_tumors_paths_each_tumor_along_own_base_highway() -> None:
    import bot.routines.creep as creep_mod

    ctx = _ctx(townhall_count=3)
    tumors = [_tumor(1, Point2((20.0, 20.0))), _tumor(2, Point2((22.0, 20.0)))]
    ctx.mediator.get_own_structures_dict = {UnitTypeId.CREEPTUMORBURROWED: tumors}
    creep_mod.cy_has_creep = lambda grid, pos: False  # type: ignore[attr-defined]

    creep.spread_tumors()(ctx)

    assert ctx.mediator.get_next_tumor_on_path.call_count == 2
    for call in ctx.mediator.get_next_tumor_on_path.call_args_list:
        assert call.kwargs["to_pos"] == Point2((50.0, 20.0))
    for tumor in tumors:
        tumor.assert_called()


def test_highway_target_skips_bases_already_on_creep() -> None:
    import bot.routines.creep as creep_mod

    ctx = _ctx(townhall_count=3)
    # Nat on creep; third not ? should aim third (80,20).
    def has_creep(grid, pos):
        return pos == Point2((50.0, 20.0))

    creep_mod.cy_has_creep = has_creep  # type: ignore[attr-defined]
    target = creep._creep_highway_target(ctx, Point2((20.0, 20.0)))
    assert target == Point2((80.0, 20.0))


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
