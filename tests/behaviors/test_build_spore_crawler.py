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


def _open_ground_ai() -> MagicMock:
    ai = MagicMock()
    ai.can_afford.return_value = True
    ai.mineral_field = []
    ai.vespene_geyser = []
    ai.in_pathing_grid.return_value = True
    ai.structures.return_value = []
    return ai


def _open_ground_mediator() -> MagicMock:
    mediator = MagicMock()
    mediator.can_place_structure.return_value = True
    mediator.select_worker.side_effect = lambda **_kw: MagicMock()
    mediator.build_with_specific_worker.return_value = True
    mediator.get_building_tracker_dict = {}
    return mediator


def _gap_ok(a: Point2, b: Point2) -> bool:
    """Two 2x2 footprints leave at least a 1-tile gap."""
    return max(abs(a.x - b.x), abs(a.y - b.y)) >= 3.0


def test_forward_wave_never_puts_two_crawlers_on_or_beside_each_other() -> None:
    """Six crawlers planned in one pulse used to resolve to the same few
    tiles (nothing is built yet when the second is picked), leaving drones
    standing on a spot that was already taken."""
    from bot.behaviors.zerg.forward_crawler_wave import ForwardCrawlerWave

    ai = _open_ground_ai()
    mediator = _open_ground_mediator()
    wave = ForwardCrawlerWave(
        anchor=Point2((60.0, 60.0)),
        structure_types=[UnitTypeId.SPINECRAWLER] * 3 + [UnitTypeId.SPORECRAWLER] * 3,
    )

    assert wave.execute(ai, {}, mediator) is True

    positions = [c.kwargs["pos"] for c in mediator.build_with_specific_worker.call_args_list]
    assert len(positions) == 6
    for i, a in enumerate(positions):
        for b in positions[i + 1 :]:
            assert _gap_ok(a, b), (a, b)


def test_crawler_sites_are_whole_tile_centres() -> None:
    """Spine/Spore are 2x2: their centre is an integer coordinate."""
    from bot.behaviors.zerg.forward_crawler_wave import ForwardCrawlerWave

    ai = _open_ground_ai()
    mediator = _open_ground_mediator()
    wave = ForwardCrawlerWave(
        anchor=Point2((60.4, 60.6)), structure_types=[UnitTypeId.SPINECRAWLER] * 2
    )
    wave.execute(ai, {}, mediator)

    for call in mediator.build_with_specific_worker.call_args_list:
        pos = call.kwargs["pos"]
        assert pos.x == int(pos.x) and pos.y == int(pos.y)


def test_new_crawler_avoids_a_site_a_drone_is_already_walking_to() -> None:
    from ares.consts import ID, TARGET

    from bot.behaviors.zerg.forward_crawler_wave import ForwardCrawlerWave

    ai = _open_ground_ai()
    mediator = _open_ground_mediator()
    claimed = Point2((60.0, 60.0))
    mediator.get_building_tracker_dict = {
        7: {ID: UnitTypeId.SPINECRAWLER, TARGET: claimed}
    }
    wave = ForwardCrawlerWave(
        anchor=Point2((60.0, 60.0)), structure_types=[UnitTypeId.SPINECRAWLER]
    )

    wave.execute(ai, {}, mediator)

    pos = mediator.build_with_specific_worker.call_args.kwargs["pos"]
    assert _gap_ok(pos, claimed)


def test_new_crawler_avoids_an_existing_crawler() -> None:
    ai = _open_ground_ai()
    existing = MagicMock()
    existing.position = Point2((60.0, 60.0))
    ai.structures.return_value = [existing]
    mediator = _open_ground_mediator()
    behavior = BuildSporeCrawler(
        base_location=Point2((50.0, 50.0)), structure_type=UnitTypeId.SPINECRAWLER
    )
    ai.game_info.map_center = Point2((60.0, 60.0))

    assert behavior.execute(ai, {}, mediator) is True

    pos = mediator.build_with_specific_worker.call_args.kwargs["pos"]
    assert _gap_ok(pos, existing.position)


def test_forward_wave_stops_dispatching_at_the_mineral_floor() -> None:
    from bot.behaviors.zerg.forward_crawler_wave import ForwardCrawlerWave

    ai = _open_ground_ai()
    ai.minerals = 2250  # 250 above the floor: two Spines, then stop
    mediator = _open_ground_mediator()
    wave = ForwardCrawlerWave(
        anchor=Point2((60.0, 60.0)),
        structure_types=[UnitTypeId.SPINECRAWLER] * 6,
        mineral_floor=2000.0,
    )

    assert wave.execute(ai, {}, mediator) is True

    assert mediator.build_with_specific_worker.call_count == 2


def test_forward_wave_without_a_floor_dispatches_everything_affordable() -> None:
    from bot.behaviors.zerg.forward_crawler_wave import ForwardCrawlerWave

    ai = _open_ground_ai()
    ai.minerals = 100
    mediator = _open_ground_mediator()
    wave = ForwardCrawlerWave(
        anchor=Point2((60.0, 60.0)), structure_types=[UnitTypeId.SPINECRAWLER] * 6
    )

    wave.execute(ai, {}, mediator)

    assert mediator.build_with_specific_worker.call_count == 6


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
