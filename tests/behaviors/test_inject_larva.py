"""Regression test for `InjectLarva`'s queen-role filter.

Runs under pytest, or standalone with no test dependency:

    python -m tests.behaviors.test_inject_larva
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from ares.consts import UnitRole
from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.behaviors.zerg import InjectLarva
from bot.behaviors.zerg import inject_larva as inject_mod


def _townhall(position: Point2) -> MagicMock:
    th = MagicMock()
    th.position = position
    th.radius = 2.75
    th.has_buff.return_value = False
    return th


def _queen(tag: int, position: Point2, energy: int = 50) -> MagicMock:
    queen = MagicMock()
    queen.tag = tag
    queen.position = position
    queen.energy = energy
    queen.radius = 0.875
    queen.is_ready = True
    queen.orders = []
    queen.order_target = None
    queen.abilities = {AbilityId.EFFECT_INJECTLARVA}
    # Force fallback distance check in unit tests (no live game_data).
    queen.in_ability_cast_range.side_effect = Exception("no game_data")
    return queen


def _ai(townhalls, queens) -> MagicMock:
    inject_mod._INJECT_PENDING_UNTIL.clear()
    ai = MagicMock()
    ai.townhalls.ready = townhalls
    ai.unit_tags_received_action = set()
    return ai


def test_a_creep_queen_is_never_used_to_inject_even_when_closest() -> None:
    th_pos = Point2((10.0, 10.0))
    townhall = _townhall(th_pos)
    townhall.tag = 10

    creep_queen = _queen(1, Point2((10.5, 10.0)))
    inject_queen = _queen(2, Point2((10.5, 11.5)))  # in range

    ai = _ai([townhall], [inject_queen])
    mediator = MagicMock()
    mediator.get_units_from_role.return_value = [inject_queen]

    did_act = InjectLarva(
        min_energy=25, home_townhall={2: 10}
    ).execute(ai, {}, mediator)

    assert did_act is True
    mediator.get_units_from_role.assert_called_once_with(
        role=UnitRole.QUEEN_INJECT, unit_type=UnitTypeId.QUEEN
    )
    inject_queen.assert_called_once_with(AbilityId.EFFECT_INJECTLARVA, townhall)
    creep_queen.assert_not_called()


def test_skips_a_townhall_already_on_an_inject_timer() -> None:
    th_pos = Point2((10.0, 10.0))
    townhall = _townhall(th_pos)
    townhall.tag = 10
    townhall.has_buff.side_effect = lambda buff: buff == BuffId.QUEENSPAWNLARVATIMER

    ai = _ai([townhall], [])
    mediator = MagicMock()
    mediator.get_units_from_role.return_value = [_queen(1, th_pos)]

    did_act = InjectLarva(
        min_energy=25, home_townhall={1: 10}
    ).execute(ai, {}, mediator)

    assert did_act is False


def test_queen_does_not_travel_to_inject_another_base() -> None:
    main = _townhall(Point2((10.0, 10.0)))
    main.tag = 100
    natural = _townhall(Point2((50.0, 50.0)))
    natural.tag = 200

    main_queen = _queen(1, Point2((10.5, 11.5)))
    nat_queen = _queen(2, Point2((55.0, 55.0)), energy=10)

    ai = _ai([main, natural], [main_queen, nat_queen])
    mediator = MagicMock()
    mediator.get_units_from_role.return_value = [main_queen, nat_queen]

    did_act = InjectLarva(
        min_energy=25,
        home_townhall={1: 100, 2: 200},
    ).execute(ai, {}, mediator)

    assert did_act is True
    main_queen.assert_called_once_with(AbilityId.EFFECT_INJECTLARVA, main)
    nat_queen.assert_not_called()


def test_without_home_map_falls_back_to_closest_queen() -> None:
    natural = _townhall(Point2((50.0, 50.0)))
    natural.tag = 200
    near_nat = _queen(1, Point2((49.0, 49.0)))

    ai = _ai([natural], [near_nat])
    mediator = MagicMock()
    mediator.get_units_from_role.return_value = [near_nat]

    did_act = InjectLarva(min_energy=25).execute(ai, {}, mediator)

    assert did_act is True
    near_nat.assert_called_once_with(AbilityId.EFFECT_INJECTLARVA, natural)


def test_out_of_range_stands_beside_hatch_not_center() -> None:
    """API rejects inject from >3 — walk to rim, never hatch center."""
    th = _townhall(Point2((32.5, 139.5)))
    th.tag = 10
    queen = _queen(1, Point2((31.25, 135.43)))  # ~4.25 away

    ai = _ai([th], [queen])
    mediator = MagicMock()
    mediator.get_units_from_role.return_value = [queen]

    did_act = InjectLarva(min_energy=25, home_townhall={1: 10}).execute(
        ai, {}, mediator
    )

    assert did_act is True
    queen.assert_not_called()
    queen.move.assert_called_once()
    stand = queen.move.call_args.args[0]
    assert stand != th.position
    assert 2.0 <= stand.distance_to(th.position) <= 3.0


def test_does_not_reissue_inject_while_pending() -> None:
    th = _townhall(Point2((10.0, 10.0)))
    th.tag = 10
    queen = _queen(1, Point2((10.5, 11.5)))

    ai = _ai([th], [queen])
    mediator = MagicMock()
    mediator.get_units_from_role.return_value = [queen]

    assert InjectLarva(min_energy=25, home_townhall={1: 10}).execute(ai, {}, mediator)
    queen.reset_mock()
    # Same frame window — pending latch must block a second cast.
    did_act = InjectLarva(min_energy=25, home_townhall={1: 10}).execute(
        ai, {}, mediator
    )

    assert did_act is False
    queen.assert_not_called()
    queen.move.assert_not_called()


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
