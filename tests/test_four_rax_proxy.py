"""Regression tests for `bot.builds.terran.four_rax_proxy.proxy_barracks_
position` - the reference point `WorkerTask.near` searches outward from for
the proxy Depot, in place of `request_building_placement`'s own formation
lookup for the enemy's fourth (which was landing that Depot behind the
base's mineral line - see that function's docstring for the full story).

Runs under pytest, or standalone with no test dependency:

    python -m tests.test_four_rax_proxy
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from sc2.position import Point2

from bot.builds.terran.four_rax_proxy import proxy_barracks_position

PROXY = Point2((100.0, 100.0))


def _ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.mediator.get_enemy_fourth = PROXY
    return ctx


def _barracks(position: Point2) -> MagicMock:
    barracks = MagicMock()
    barracks.position = position
    return barracks


def test_returns_the_nearest_barracks_to_the_proxy() -> None:
    ctx = _ctx()
    near = _barracks(Point2((99.0, 99.0)))
    far = _barracks(Point2((50.0, 50.0)))
    ctx.bot.structures.return_value = [far, near]

    assert proxy_barracks_position(ctx) == near.position


def test_falls_back_to_the_proxy_location_with_no_barracks_yet() -> None:
    ctx = _ctx()
    ctx.bot.structures.return_value = []

    assert proxy_barracks_position(ctx) == PROXY


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
