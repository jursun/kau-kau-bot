"""Unit tests for Macro Zerg gas scaling timing and base-order preference.

    python -m tests.test_zerg_gas_building
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from sc2.position import Point2

from bot.behaviors.zerg.gas_building_controller import (
    pick_geyser_for_bases,
    preferred_base_locations,
)
from bot.builds.zerg.macro_zerg import _gas_scale_target


def test_gas_scale_closed_before_five_minutes() -> None:
    assert _gas_scale_target(299.9) is None


def test_gas_scale_opens_at_five_minutes_with_third_extractor() -> None:
    # Opening leaves 2; first scale tick bumps to 3.
    assert _gas_scale_target(300.0) == 3


def test_gas_scale_grows_every_twenty_seconds_to_cap_eight() -> None:
    assert _gas_scale_target(320.0) == 4
    assert _gas_scale_target(340.0) == 5
    assert _gas_scale_target(360.0) == 6
    assert _gas_scale_target(380.0) == 7
    assert _gas_scale_target(400.0) == 8
    assert _gas_scale_target(440.0) == 8


def test_preferred_bases_are_main_natural_then_distance() -> None:
    main = Point2((10.0, 10.0))
    natural = Point2((30.0, 10.0))
    third = Point2((50.0, 10.0))
    fourth = Point2((70.0, 10.0))
    # Pass owned out of order; result must still be expansion order.
    ordered = preferred_base_locations(
        [fourth, third, natural, main],
        start_location=main,
        natural=natural,
    )
    assert ordered == [main, natural, third, fourth]


def test_preferred_bases_skip_natural_slot_when_natural_not_owned() -> None:
    main = Point2((10.0, 10.0))
    natural = Point2((30.0, 10.0))
    third = Point2((50.0, 10.0))
    ordered = preferred_base_locations(
        [main, third],
        start_location=main,
        natural=natural,
    )
    assert ordered == [main, third]


def test_pick_geyser_fills_main_before_natural() -> None:
    main = Point2((10.0, 10.0))
    natural = Point2((40.0, 10.0))
    main_geyser = SimpleNamespace(position=Point2((12.0, 10.0)))
    nat_geyser = SimpleNamespace(position=Point2((42.0, 10.0)))
    # Natural geyser listed first — still pick main.
    picked = pick_geyser_for_bases(
        [nat_geyser, main_geyser],  # type: ignore[list-item]
        [main, natural],
    )
    assert picked is main_geyser


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
