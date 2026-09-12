"""Unit tests for `bot.intel.qa` (no live SC2).

    python -m tests.test_intel_qa
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from sc2.position import Point2

from bot.intel import qa


def _th(tag: int, idle: bool) -> MagicMock:
    unit = MagicMock()
    unit.tag = tag
    unit.is_idle = idle
    return unit


def _army_unit(tag: int, position: Point2) -> MagicMock:
    unit = MagicMock()
    unit.tag = tag
    unit.position = position
    return unit


def test_is_supply_blocked() -> None:
    assert qa.is_supply_blocked(SimpleNamespace(supply_left=0)) is True
    assert qa.is_supply_blocked(SimpleNamespace(supply_left=3)) is False


def test_idle_ready_townhalls() -> None:
    ready = [_th(1, True), _th(2, False), _th(3, True)]
    bot = SimpleNamespace(townhalls=SimpleNamespace(ready=ready))
    assert [t.tag for t in qa.idle_ready_townhalls(bot)] == [1, 3]


def test_units_parked_in_influence() -> None:
    unsafe = _army_unit(10, Point2((1.0, 1.0)))
    safe = _army_unit(11, Point2((2.0, 2.0)))

    def is_safe(*, grid, position):
        return position == Point2((2.0, 2.0))

    bot = SimpleNamespace(
        mediator=SimpleNamespace(
            get_ground_grid=object(),
            is_position_safe=is_safe,
            get_units_from_role=lambda **kwargs: [unsafe, safe],
        )
    )
    parked = qa.units_parked_in_influence(bot)
    assert [u.tag for u in parked] == [10]
    assert qa.influence_parking_tags(bot) == frozenset({10})


def test_units_parked_empty_without_grid() -> None:
    bot = SimpleNamespace(mediator=SimpleNamespace(get_cached_enemy_army=[]))
    assert qa.units_parked_in_influence(bot) == []



def test_idle_ready_townhalls_still_reports_idle_hatches() -> None:
    """Helper is race-agnostic; BaseValidator must race-gate Zerg callers."""
    ready = [_th(1, True)]
    bot = SimpleNamespace(townhalls=SimpleNamespace(ready=ready))
    assert [t.tag for t in qa.idle_ready_townhalls(bot)] == [1]


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
