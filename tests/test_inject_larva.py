"""Regression test for `InjectLarva`'s queen-role filter.

Runs under pytest, or standalone with no test dependency:

    python -m tests.test_inject_larva
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


def _townhall(position: Point2) -> MagicMock:
    th = MagicMock()
    th.position = position
    th.has_buff.return_value = False
    return th


def _queen(tag: int, position: Point2, energy: int = 50) -> MagicMock:
    queen = MagicMock()
    queen.tag = tag
    queen.position = position
    queen.energy = energy
    queen.is_ready = True
    return queen


def test_a_creep_queen_is_never_used_to_inject_even_when_closest() -> None:
    """Regression test: before this filtered by role, `InjectLarva` used
    every ready, energized queen regardless of role — so a queen just
    promoted to `UnitRole.QUEEN_CREEP` (`routines.creep.spread_creep`) would
    still get swept into injecting whenever it happened to be the closest
    one to a townhall, undoing the dedicated assignment."""
    th_pos = Point2((10.0, 10.0))
    townhall = _townhall(th_pos)

    creep_queen = _queen(1, Point2((10.5, 10.0)))  # closer to the townhall...
    inject_queen = _queen(2, Point2((30.0, 30.0)))  # ...but this one is the injector

    ai = MagicMock()
    ai.townhalls.ready = [townhall]
    ai.unit_tags_received_action = set()

    mediator = MagicMock()
    mediator.get_units_from_role.return_value = [inject_queen]  # role-filtered already

    did_act = InjectLarva(min_energy=25).execute(ai, {}, mediator)

    assert did_act is True
    mediator.get_units_from_role.assert_called_once_with(
        role=UnitRole.QUEEN_INJECT, unit_type=UnitTypeId.QUEEN
    )
    inject_queen.assert_called_once_with(AbilityId.EFFECT_INJECTLARVA, townhall)
    creep_queen.assert_not_called()


def test_skips_a_townhall_already_on_an_inject_timer() -> None:
    th_pos = Point2((10.0, 10.0))
    townhall = _townhall(th_pos)
    townhall.has_buff.side_effect = lambda buff: buff == BuffId.QUEENSPAWNLARVATIMER

    ai = MagicMock()
    ai.townhalls.ready = [townhall]
    ai.unit_tags_received_action = set()

    mediator = MagicMock()
    mediator.get_units_from_role.return_value = [_queen(1, th_pos)]

    did_act = InjectLarva(min_energy=25).execute(ai, {}, mediator)

    assert did_act is False


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
