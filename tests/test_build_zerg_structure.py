
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
    _STICKY_PLACEMENT,
    _find_near_base,
)


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
    ai.time_formatted = "6:04"
    for key, value in overrides.items():
        setattr(ai, key, value)
    return ai


def _mediator(**overrides):
    mediator = MagicMock()
    mediator.get_own_structures_dict = {UnitTypeId.SPIRE: []}
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
    )
    assert pos is not None
    assert pos.x == int(pos.x) + 0.5
    assert pos.y == int(pos.y) + 0.5


def test_build_zerg_structure_dispatches_one_worker() -> None:
    _STICKY_PLACEMENT.clear()
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
    _STICKY_PLACEMENT.clear()
    ai = _ai(can_afford=MagicMock(return_value=False))
    mediator = _mediator()
    assert (
        BuildZergStructure(
            base_location=Point2((10.0, 10.0)), structure_id=UnitTypeId.SPIRE
        ).execute(ai, {}, mediator)
        is True
    )
    mediator.select_worker.assert_not_called()


def test_build_zerg_structure_skips_when_unaffordable_without_prioritize() -> None:
    _STICKY_PLACEMENT.clear()
    ai = _ai(can_afford=MagicMock(return_value=False))
    mediator = _mediator()
    assert (
        BuildZergStructure(
            base_location=Point2((10.0, 10.0)),
            structure_id=UnitTypeId.SPIRE,
            prioritize=False,
        ).execute(ai, {}, mediator)
        is False
    )
    mediator.select_worker.assert_not_called()


def test_build_zerg_structure_holds_bank_while_on_route() -> None:
    _STICKY_PLACEMENT.clear()
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


def test_sticky_placement_reused_when_still_placeable() -> None:
    _STICKY_PLACEMENT.clear()
    sticky = Point2((12.5, 14.5))
    _STICKY_PLACEMENT[UnitTypeId.SPIRE] = sticky
    ai = _ai()
    mediator = _mediator()
    BuildZergStructure(
        base_location=Point2((10.0, 10.0)), structure_id=UnitTypeId.SPIRE
    ).execute(ai, {}, mediator)
    kwargs = mediator.build_with_specific_worker.call_args.kwargs
    assert kwargs["pos"] == sticky


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
