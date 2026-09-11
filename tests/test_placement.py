"""Regression tests for `bot.routines.placement.near_point`.

`near_point` exists because `request_building_placement`'s `base_location`
snaps to a precomputed per-expansion formation that can put a structure's
slot somewhere unusable (see that function's module docstring, and
`builds.terran.four_rax_proxy.proxy_barracks_position` for the real bug this
was written to fix: a proxy Depot landing deep behind a mineral line). These
tests exercise the ring-search/filter/validate pipeline directly, with a
`MagicMock` bot and mediator rather than a real game.

Runs under pytest, or standalone with no test dependency:

    python -m tests.test_placement
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from cython_extensions import cy_distance_to_squared
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.routines.placement import near_point

REFERENCE = Point2((100.0, 100.0))


def _ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.bot.mineral_field = []
    ctx.bot.vespene_geyser = []
    ctx.bot.in_pathing_grid.return_value = True
    ctx.mediator.can_place_structure.return_value = True
    return ctx


def _resource(position: Point2) -> MagicMock:
    resource = MagicMock()
    resource.position = position
    return resource


def test_near_point_returns_the_closest_legal_candidate() -> None:
    ctx = _ctx()

    result = near_point(ctx, REFERENCE, UnitTypeId.SUPPLYDEPOT)

    assert result is not None
    # Every candidate is legal here, so the closest ring (min_radius=2.0)
    # must win - snapping to a `.5` centre can pull it in slightly, but not
    # out past the second ring.
    assert cy_distance_to_squared(result, REFERENCE) <= 3.0**2


def test_near_point_passes_the_requested_structure_type_through() -> None:
    ctx = _ctx()

    near_point(ctx, REFERENCE, UnitTypeId.BARRACKS)

    assert (
        ctx.mediator.can_place_structure.call_args.kwargs["structure_type"]
        == UnitTypeId.BARRACKS
    )


def test_near_point_skips_candidates_too_close_to_a_resource() -> None:
    """A mineral patch sitting on the reference point itself must push the
    result out past `resource_clearance`, even though `can_place_structure`
    (a MagicMock, always True here) would happily accept the inner ring."""
    ctx = _ctx()
    ctx.bot.mineral_field = [_resource(REFERENCE)]

    result = near_point(ctx, REFERENCE, UnitTypeId.SUPPLYDEPOT, resource_clearance=2.5)

    assert result is not None
    assert cy_distance_to_squared(result, REFERENCE) > 2.5**2


def test_near_point_skips_unpathable_candidates() -> None:
    """Same idea as the resource test, via `in_pathing_grid` instead."""
    ctx = _ctx()
    ctx.bot.in_pathing_grid.side_effect = (
        lambda p: cy_distance_to_squared(p, REFERENCE) > 2.5**2
    )

    result = near_point(ctx, REFERENCE, UnitTypeId.SUPPLYDEPOT)

    assert result is not None
    assert cy_distance_to_squared(result, REFERENCE) > 2.5**2


def test_near_point_returns_none_when_nothing_is_legal() -> None:
    ctx = _ctx()
    ctx.mediator.can_place_structure.return_value = False

    assert near_point(ctx, REFERENCE, UnitTypeId.SUPPLYDEPOT) is None


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
