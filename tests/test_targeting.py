"""Regression tests for `bot.routines.targeting`.

`attack_target` is the one place squads and builder-workers alike decide
*where* to head - see that function's own docstring for why a structure
with real defenders standing near it (drones on the mineral line behind a
Hatchery, say) sends everyone at the defenders instead of the building.
These exercise that decision, plus the plain locator functions
(`enemy_third`/`enemy_fourth`/`map_center`), against a `MagicMock` bot and
mediator rather than a real game.

Runs under pytest, or standalone with no test dependency:

    python -m tests.test_targeting
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.routines import targeting

HATCHERY = Point2((100.0, 100.0))
FROM_POS = Point2((90.0, 90.0))


def _structure(
    position: Point2, type_id: UnitTypeId = UnitTypeId.HATCHERY
) -> MagicMock:
    structure = MagicMock()
    structure.position = position
    structure.type_id = type_id
    structure.is_structure = True
    return structure


def _unit(position: Point2, type_id: UnitTypeId = UnitTypeId.DRONE) -> MagicMock:
    unit = MagicMock()
    unit.position = position
    unit.type_id = type_id
    unit.is_structure = False
    return unit


class _Structures(list):
    """Stand-in for `sc2.units.Units` - just enough of its API
    (`of_type`, truthiness, iteration) for `attack_target` to use."""

    def of_type(self, types) -> list:
        types = types if isinstance(types, (set, frozenset, tuple, list)) else (types,)
        return [s for s in self if s.type_id in types]


def _ctx(structures=(), enemy_units=()) -> MagicMock:
    ctx = MagicMock()
    ctx.bot.enemy_structures = _Structures(structures)
    ctx.bot.enemy_units = list(enemy_units)
    return ctx


# --- targeting.attack_target -------------------------------------------


def test_attack_target_prefers_a_nearby_defender_over_the_structure() -> None:
    """Drones standing behind the Hatchery must pull the target off the
    building itself - the whole point of this fix."""
    hatchery = _structure(HATCHERY)
    drone = _unit(HATCHERY.offset(Point2((5.0, 0.0))))
    ctx = _ctx(structures=[hatchery], enemy_units=[drone])

    assert targeting.attack_target(ctx, FROM_POS) == drone.position


def test_attack_target_falls_back_to_the_structure_with_no_defender_near() -> None:
    hatchery = _structure(HATCHERY)
    far_drone = _unit(HATCHERY.offset(Point2((500.0, 0.0))))
    ctx = _ctx(structures=[hatchery], enemy_units=[far_drone])

    assert targeting.attack_target(ctx, FROM_POS) == hatchery.position


def test_attack_target_ignores_eggs_and_larva_as_defenders() -> None:
    hatchery = _structure(HATCHERY)
    egg = _unit(HATCHERY.offset(Point2((2.0, 0.0))), type_id=UnitTypeId.EGG)
    ctx = _ctx(structures=[hatchery], enemy_units=[egg])

    assert targeting.attack_target(ctx, FROM_POS) == hatchery.position


def test_attack_target_picks_the_closest_defender_when_several_are_near() -> None:
    hatchery = _structure(HATCHERY)
    near = _unit(HATCHERY.offset(Point2((3.0, 0.0))))
    far = _unit(HATCHERY.offset(Point2((10.0, 0.0))))
    ctx = _ctx(structures=[hatchery], enemy_units=[far, near])

    assert targeting.attack_target(ctx, FROM_POS) == near.position


def test_attack_target_prefers_townhalls_among_structures_before_defenders() -> None:
    """The structure a defender is measured against must still be the
    nearest townhall, not just the nearest structure of any kind."""
    hatchery = _structure(HATCHERY)
    supply_depot = _structure(FROM_POS, type_id=UnitTypeId.SUPPLYDEPOT)
    drone = _unit(HATCHERY.offset(Point2((3.0, 0.0))))
    ctx = _ctx(structures=[supply_depot, hatchery], enemy_units=[drone])

    assert targeting.attack_target(ctx, FROM_POS) == drone.position


def test_attack_target_falls_back_to_any_structure_when_no_townhall_visible() -> None:
    supply_depot = _structure(FROM_POS, type_id=UnitTypeId.SUPPLYDEPOT)
    ctx = _ctx(structures=[supply_depot])

    assert targeting.attack_target(ctx, FROM_POS) == supply_depot.position


def test_attack_target_falls_through_to_focus_points_with_no_structures() -> None:
    ctx = _ctx(structures=[])
    ctx.build.combat.focus = ()
    ctx.mediator.get_enemy_expansions = []
    ctx.bot.enemy_start_locations = [Point2((1.0, 1.0))]

    assert targeting.attack_target(ctx, FROM_POS) == Point2((1.0, 1.0))


# --- targeting locators --------------------------------------------------


def test_enemy_third_reads_the_mediator_value() -> None:
    ctx = MagicMock()
    ctx.mediator.get_enemy_third = HATCHERY
    assert targeting.enemy_third(ctx) == HATCHERY


def test_enemy_fourth_reads_the_mediator_value() -> None:
    ctx = MagicMock()
    ctx.mediator.get_enemy_fourth = HATCHERY
    assert targeting.enemy_fourth(ctx) == HATCHERY


def test_map_center_reads_the_game_info_value() -> None:
    ctx = MagicMock()
    ctx.bot.game_info.map_center = HATCHERY
    assert targeting.map_center(ctx) == HATCHERY


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
