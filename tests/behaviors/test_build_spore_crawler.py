"""Regression: Spines must not reuse Spore behind-mineral tiles.

Confirmed live: `SPINE maintain` logged every 15s with zero
`COMPLETE spinecrawler` while Spores filled the three
`get_behind_mineral_positions` candidates.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.behaviors.zerg.build_spore_crawler import BuildSporeCrawler


def test_spine_uses_front_of_base_not_behind_minerals() -> None:
    ai = MagicMock()
    ai.can_afford.return_value = True
    ai.game_info.map_center = Point2((100.0, 100.0))
    ai.mineral_field = []
    ai.vespene_geyser = []
    ai.in_pathing_grid.return_value = True
    ai.structures.return_value = []

    mediator = MagicMock()
    mediator.can_place_structure.return_value = True
    mediator.select_worker.return_value = MagicMock()
    mediator.build_with_specific_worker.return_value = True
    mediator.get_building_tracker_dict = {}

    base = Point2((10.0, 10.0))
    behavior = BuildSporeCrawler(
        base_location=base, structure_type=UnitTypeId.SPINECRAWLER
    )

    assert behavior.execute(ai, {}, mediator) is True
    mediator.get_behind_mineral_positions.assert_not_called()
    mediator.build_with_specific_worker.assert_called_once()
    pos = mediator.build_with_specific_worker.call_args.kwargs["pos"]
    # Front-of-base is toward map center, not behind minerals at base.
    assert pos.distance_to(Point2((100.0, 100.0))) < base.distance_to(
        Point2((100.0, 100.0))
    )


def test_second_spine_avoids_first_spine_pad() -> None:
    """2nd early-aggression Spine must not rebuild on the 1st's tile."""
    from bot.behaviors.zerg import build_spore_crawler as mod

    ai = MagicMock()
    ai.can_afford.return_value = True
    ai.game_info.map_center = Point2((100.0, 100.0))
    ai.mineral_field = []
    ai.vespene_geyser = []
    ai.in_pathing_grid.return_value = True
    first = MagicMock()
    first.position = Point2((16.5, 16.5))
    ai.structures.return_value = [first]

    mediator = MagicMock()
    mediator.can_place_structure.return_value = True
    mediator.select_worker.return_value = MagicMock()
    mediator.get_building_tracker_dict = {}

    base = Point2((10.0, 10.0))
    behavior = BuildSporeCrawler(
        base_location=base, structure_type=UnitTypeId.SPINECRAWLER
    )
    assert behavior.execute(ai, {}, mediator) is True
    pos = mediator.build_with_specific_worker.call_args.kwargs["pos"]
    assert pos.distance_to(first.position) >= mod._SPINE_MIN_SEP


def test_spore_still_uses_behind_mineral_positions() -> None:
    ai = MagicMock()
    ai.can_afford.return_value = True

    mediator = MagicMock()
    behind = Point2((5.0, 5.0))
    mediator.get_behind_mineral_positions.return_value = [behind]
    mediator.can_place_structure.return_value = True
    mediator.select_worker.return_value = MagicMock()

    behavior = BuildSporeCrawler(
        base_location=Point2((10.0, 10.0)),
        structure_type=UnitTypeId.SPORECRAWLER,
    )

    assert behavior.execute(ai, {}, mediator) is True
    mediator.get_behind_mineral_positions.assert_called_once()
    assert (
        mediator.build_with_specific_worker.call_args.kwargs["pos"] == behind
    )


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
