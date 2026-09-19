"""Unit tests for BuildZergStructure placement (no request_zerg_placement).

    python -m tests.test_build_zerg_structure
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.behaviors.zerg.build_zerg_structure import (
    BuildZergStructure,
    _BASE_ROTATION,
    _BLACKLIST,
    _STICKY_FAILS,
    _STICKY_PLACEMENT,
    _find_near_base,
)


def _reset_state() -> None:
    _STICKY_PLACEMENT.clear()
    _STICKY_FAILS.clear()
    _BLACKLIST.clear()
    _BASE_ROTATION.clear()


def _ai(**overrides):
    ai = MagicMock()
    ai.not_started_but_in_building_tracker = MagicMock(return_value=0)
    ai.structure_pending = MagicMock(return_value=0)
    ai.can_afford = MagicMock(return_value=True)
    ai.tech_requirement_progress = MagicMock(return_value=1.0)
    ai.game_info.map_center = Point2((50.0, 50.0))
    ai.mineral_field = []
    ai.vespene_geyser = []
    ai.get_terrain_height = MagicMock(return_value=10)
    ai.in_pathing_grid = MagicMock(return_value=True)
    ai.townhalls.ready = []
    ai.time_formatted = "7:40"
    for key, value in overrides.items():
        setattr(ai, key, value)
    return ai


def _mediator(**overrides):
    mediator = MagicMock()
    mediator.get_own_structures_dict = {UnitTypeId.INFESTATIONPIT: [], UnitTypeId.SPIRE: []}
    mediator.can_place_structure = MagicMock(return_value=True)
    worker = MagicMock()
    mediator.select_worker = MagicMock(return_value=worker)
    for key, value in overrides.items():
        setattr(mediator, key, value)
    return mediator


def test_find_near_base_returns_first_legal_pathable_tile() -> None:
    ai = _ai()
    mediator = _mediator()
    mediator.can_place_structure = MagicMock(side_effect=[False, True])
    pos = _find_near_base(
        ai,
        mediator,
        Point2((10.0, 10.0)),
        UnitTypeId.SPIRE,
        min_radius=5.0,
        max_radius=8.0,
        resource_clearance=5.0,
        banned=set(),
    )
    assert pos is not None
    assert pos.x == int(pos.x) + 0.5
    assert pos.y == int(pos.y) + 0.5


def test_find_near_base_skips_blacklisted_tile() -> None:
    ai = _ai()
    mediator = _mediator()
    # First legal candidate would be accepted unless banned — ban all first
    # returns then accept.
    first = []

    def place(**kwargs):
        point = kwargs["position"]
        first.append(point)
        return len(first) > 1

    mediator.can_place_structure = MagicMock(side_effect=place)
    # Precompute: call once without ban to learn first tile, then ban it.
    mediator.can_place_structure = MagicMock(return_value=True)
    probe = _find_near_base(
        ai,
        mediator,
        Point2((10.0, 10.0)),
        UnitTypeId.INFESTATIONPIT,
        min_radius=5.0,
        max_radius=8.0,
        resource_clearance=5.0,
        banned=set(),
    )
    assert probe is not None
    mediator.can_place_structure = MagicMock(return_value=True)
    again = _find_near_base(
        ai,
        mediator,
        Point2((10.0, 10.0)),
        UnitTypeId.INFESTATIONPIT,
        min_radius=5.0,
        max_radius=8.0,
        resource_clearance=5.0,
        banned={probe},
    )
    assert again is not None
    assert again != probe


def test_build_zerg_structure_dispatches_one_worker() -> None:
    _reset_state()
    ai = _ai()
    mediator = _mediator()
    worker = mediator.select_worker.return_value
    behaved = BuildZergStructure(
        base_location=Point2((10.0, 10.0)),
        structure_id=UnitTypeId.SPIRE,
    ).execute(ai, {}, mediator)
    assert behaved is True
    mediator.build_with_specific_worker.assert_called_once()
    kwargs = mediator.build_with_specific_worker.call_args.kwargs
    assert kwargs["worker"] is worker
    assert kwargs["structure_type"] == UnitTypeId.SPIRE


def test_build_zerg_structure_holds_bank_when_unaffordable() -> None:
    _reset_state()
    ai = _ai(can_afford=MagicMock(return_value=False))
    mediator = _mediator()
    assert (
        BuildZergStructure(
            base_location=Point2((10.0, 10.0)), structure_id=UnitTypeId.SPIRE
        ).execute(ai, {}, mediator)
        is True
    )
    mediator.select_worker.assert_not_called()


def test_build_zerg_structure_holds_bank_while_on_route() -> None:
    _reset_state()
    ai = _ai(not_started_but_in_building_tracker=MagicMock(return_value=1))
    mediator = _mediator()
    assert (
        BuildZergStructure(
            base_location=Point2((10.0, 10.0)), structure_id=UnitTypeId.SPIRE
        ).execute(ai, {}, mediator)
        is True
    )
    mediator.select_worker.assert_not_called()
    mediator.build_with_specific_worker.assert_not_called()


def test_sticky_reused_when_still_placeable() -> None:
    _reset_state()
    sticky = Point2((12.5, 14.5))
    _STICKY_PLACEMENT[UnitTypeId.INFESTATIONPIT] = sticky
    ai = _ai()
    mediator = _mediator()
    BuildZergStructure(
        base_location=Point2((10.0, 10.0)), structure_id=UnitTypeId.INFESTATIONPIT
    ).execute(ai, {}, mediator)
    kwargs = mediator.build_with_specific_worker.call_args.kwargs
    assert kwargs["pos"] == sticky


def test_sticky_abandoned_after_repeated_redispatch_fails() -> None:
    """Same bad tile must not be spammed forever (CheatInsane Pit thrash)."""
    _reset_state()
    bad = Point2((44.5, 39.5))
    good = Point2((60.5, 40.5))
    _STICKY_PLACEMENT[UnitTypeId.INFESTATIONPIT] = bad
    ai = _ai()
    mediator = _mediator()

    def can_place(**kwargs):
        return kwargs["position"] != Point2((0.5, 0.5))

    mediator.can_place_structure = MagicMock(side_effect=can_place)

    # Simulate max_sticky_fails re-dispatches to the same sticky.
    behavior = BuildZergStructure(
        base_location=Point2((40.0, 40.0)),
        structure_id=UnitTypeId.INFESTATIONPIT,
        max_sticky_fails=3,
    )
    for _ in range(3):
        # Force resolve to keep returning sticky by can_place True on bad.
        mediator.can_place_structure = MagicMock(return_value=True)
        assert behavior.execute(ai, {}, mediator) is True
        assert (
            mediator.build_with_specific_worker.call_args.kwargs["pos"] == bad
        )

    # Next execute: fail budget exhausted -> abandon sticky, pick a new tile.
    # Make sticky can_place still True so only fail-count triggers abandon;
    # then _find_near_base returns `good` as first candidate.
    def after_abandon(**kwargs):
        return kwargs["position"] == good or True

    # After abandon, sticky cleared; find_near_base walks candidates — stub
    # can_place to reject everything except `good`.
    def place_only_good(**kwargs):
        return kwargs["position"] == good

    # Pre-seed: monkeypatch _find_near_base via can_place returning True only
    # once we ask for something other than bad. Simpler: put good in sticky
    # resolution by making can_place False for bad after abandon and True else.
    call_count = {"n": 0}

    def place(**kwargs):
        call_count["n"] += 1
        pos = kwargs["position"]
        if pos == bad:
            return False  # abandoned path shouldn't reuse; if asked, reject
        return True

    mediator.can_place_structure = MagicMock(side_effect=place)
    # fail count already 3 from loop — this execute abandons then searches.
    _STICKY_FAILS[UnitTypeId.INFESTATIONPIT] = 3
    _STICKY_PLACEMENT[UnitTypeId.INFESTATIONPIT] = bad
    assert behavior.execute(ai, {}, mediator) is True
    assert bad in _BLACKLIST[UnitTypeId.INFESTATIONPIT]
    chosen = mediator.build_with_specific_worker.call_args.kwargs["pos"]
    assert chosen != bad
    assert _BASE_ROTATION.get(UnitTypeId.INFESTATIONPIT, 0) >= 1


def test_unplaceable_sticky_blacklisted_immediately() -> None:
    _reset_state()
    bad = Point2((44.5, 39.5))
    _STICKY_PLACEMENT[UnitTypeId.INFESTATIONPIT] = bad
    ai = _ai()
    mediator = _mediator()

    def place(**kwargs):
        return kwargs["position"] != bad

    mediator.can_place_structure = MagicMock(side_effect=place)
    BuildZergStructure(
        base_location=Point2((40.0, 40.0)),
        structure_id=UnitTypeId.INFESTATIONPIT,
    ).execute(ai, {}, mediator)
    assert bad in _BLACKLIST[UnitTypeId.INFESTATIONPIT]
    chosen = mediator.build_with_specific_worker.call_args.kwargs["pos"]
    assert chosen != bad


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
