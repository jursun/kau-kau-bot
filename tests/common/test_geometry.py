"""Regression tests for `safe_start_location`.

Runs under pytest, or standalone with no test dependency:

    python -m tests.common.test_geometry
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from sc2.position import Point2

from bot.common.geometry import safe_start_location

START = Point2((10.0, 10.0))
NAT = Point2((20.0, 15.0))


def _ai() -> MagicMock:
    ai = MagicMock()
    ai.start_location = START
    return ai


def test_returns_start_location_normally() -> None:
    assert safe_start_location(_ai()) == START


def test_falls_back_to_a_ready_townhall_when_start_location_is_none() -> None:
    """Regression test for the exact user report: `rally_point` crashed
    with `AttributeError: 'NoneType' object has no attribute 'towards'`.
    python-sc2's own `start_location` is *itself* `None` whenever `self.
    townhalls` was empty at the very first `on_step` observation
    (`BotAIInternal._prepare_first_step`'s own `if self.townhalls: ...`
    guard, never recomputed afterward once missed) - the old fallback
    handed that `None` straight back. A live ready townhall's own position
    doesn't depend on that frozen first-frame snapshot at all."""
    ai = _ai()
    ai.start_location = None
    ai.townhalls.ready.first.position = NAT

    assert safe_start_location(ai) == NAT


def test_falls_back_to_map_center_when_there_is_no_base_at_all() -> None:
    """The final, always-non-None fallback for the - should be impossible
    mid-game - case where even a live ready townhall can't be found."""
    ai = _ai()
    ai.start_location = None
    ai.townhalls.ready = []
    center = Point2((50.0, 50.0))
    ai.game_info.map_center = center

    assert safe_start_location(ai) == center


def test_start_location_at_the_map_origin_is_not_mistaken_for_missing() -> None:
    """`Point2.__bool__` is falsy for `(0, 0)` (a numpy-backed all-zero
    check) - this must check `is not None`, not truthiness, so a start
    location that legitimately sits at the origin isn't silently swapped
    for the townhall/map-center fallback."""
    ai = _ai()
    origin = Point2((0.0, 0.0))
    ai.start_location = origin

    assert safe_start_location(ai) == origin


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
