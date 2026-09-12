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


# --- targeting.hunt_remaining_bases --------------------------------------


def test_hunt_remaining_bases_ignores_leftover_main_structures_to_scout() -> None:
    """Pylons in a dead main must not block the expansion sweep."""
    pylon = _structure(FROM_POS, type_id=UnitTypeId.PYLON)
    hidden = Point2((200.0, 50.0))
    ctx = _ctx(structures=[pylon])
    ctx.bot.start_location = Point2((10.0, 10.0))
    ctx.mediator.get_own_nat = Point2((20.0, 10.0))
    ctx.mediator.get_enemy_expansions = [(hidden, 30.0)]
    ctx.bot.is_visible.side_effect = lambda p: p != hidden
    ctx.bot.enemy_start_locations = [Point2((70.0, 70.0))]
    ctx.state.hunt_objective = None
    ctx.state.scouted_expansions = set()

    assert targeting.hunt_remaining_bases(ctx, FROM_POS) == hidden
    assert ctx.state.hunt_objective == hidden


def test_hunt_remaining_bases_keeps_a_pinned_expansion_for_the_whole_army() -> None:
    """A pinned scout point must not flip when another expansion is also dark."""
    first = Point2((200.0, 50.0))
    second = Point2((220.0, 80.0))
    ctx = _ctx(structures=[])
    ctx.bot.start_location = Point2((10.0, 10.0))
    ctx.mediator.get_own_nat = Point2((20.0, 10.0))
    ctx.mediator.get_enemy_expansions = [(second, 20.0), (first, 30.0)]
    ctx.bot.is_visible.return_value = False
    ctx.bot.enemy_start_locations = [Point2((70.0, 70.0))]
    ctx.state.hunt_objective = first
    ctx.state.scouted_expansions = set()

    assert targeting.hunt_remaining_bases(ctx, FROM_POS) == first


def test_hunt_remaining_bases_does_not_revisit_an_explored_expansion() -> None:
    """Fog after leaving the natural must not bounce the army back from the third."""
    natural = Point2((100.0, 50.0))
    third = Point2((200.0, 50.0))
    ctx = _ctx(structures=[])
    ctx.bot.start_location = Point2((10.0, 10.0))
    ctx.mediator.get_own_nat = Point2((20.0, 10.0))
    ctx.mediator.get_enemy_expansions = [(natural, 10.0), (third, 20.0)]
    # Natural was checked then fogged; third not yet.
    ctx.bot.is_visible.return_value = False
    ctx.bot.enemy_start_locations = [Point2((70.0, 70.0))]
    ctx.state.hunt_objective = natural
    ctx.state.scouted_expansions = {natural}

    assert targeting.hunt_remaining_bases(ctx, FROM_POS) == third
    assert ctx.state.hunt_objective == third

    # Still fogged — must stay on third, not bounce to natural.
    assert targeting.hunt_remaining_bases(ctx, FROM_POS) == third


def test_hunt_remaining_bases_marks_checked_on_arrival() -> None:
    """Arriving at the pin (even without lasting vision) advances the sweep."""
    natural = Point2((100.0, 50.0))
    third = Point2((200.0, 50.0))
    ctx = _ctx(structures=[])
    ctx.bot.start_location = Point2((10.0, 10.0))
    ctx.mediator.get_own_nat = Point2((20.0, 10.0))
    ctx.mediator.get_enemy_expansions = [(natural, 10.0), (third, 20.0)]
    ctx.bot.is_visible.return_value = False
    ctx.bot.enemy_start_locations = [Point2((70.0, 70.0))]
    ctx.state.hunt_objective = natural
    ctx.state.scouted_expansions = set()

    assert targeting.hunt_remaining_bases(ctx, natural) == third
    assert natural in ctx.state.scouted_expansions


def test_hunt_remaining_bases_prefers_a_visible_townhall_over_scouting() -> None:
    hatchery = _structure(HATCHERY)
    hidden = Point2((200.0, 50.0))
    ctx = _ctx(structures=[hatchery])
    ctx.bot.start_location = Point2((10.0, 10.0))
    ctx.mediator.get_own_nat = Point2((20.0, 10.0))
    ctx.mediator.get_enemy_expansions = [(hidden, 30.0)]
    ctx.bot.is_visible.return_value = False
    ctx.state.hunt_objective = hidden
    ctx.state.scouted_expansions = set()

    assert targeting.hunt_remaining_bases(ctx, FROM_POS) == HATCHERY
    assert ctx.state.hunt_objective is None


def test_hunt_remaining_bases_skips_our_own_expansions() -> None:
    our_nat = Point2((20.0, 10.0))
    hidden = Point2((200.0, 50.0))
    ctx = _ctx(structures=[])
    ctx.bot.start_location = Point2((10.0, 10.0))
    ctx.mediator.get_own_nat = our_nat
    ctx.mediator.get_enemy_expansions = [(our_nat, 10.0), (hidden, 30.0)]
    ctx.bot.is_visible.return_value = False
    ctx.bot.enemy_start_locations = [Point2((70.0, 70.0))]
    ctx.build.combat.focus = ()
    ctx.state.hunt_objective = None
    ctx.state.scouted_expansions = set()

    assert targeting.hunt_remaining_bases(ctx, FROM_POS) == hidden


def test_hunt_remaining_bases_cleans_up_structures_after_expansions_checked() -> None:
    pylon = _structure(FROM_POS, type_id=UnitTypeId.PYLON)
    visible_exp = Point2((200.0, 50.0))
    ctx = _ctx(structures=[pylon])
    ctx.bot.start_location = Point2((10.0, 10.0))
    ctx.mediator.get_own_nat = Point2((20.0, 10.0))
    ctx.mediator.get_enemy_expansions = [(visible_exp, 30.0)]
    ctx.bot.is_visible.return_value = True
    ctx.bot.enemy_start_locations = [Point2((70.0, 70.0))]
    ctx.state.hunt_objective = None
    ctx.state.scouted_expansions = set()

    assert targeting.hunt_remaining_bases(ctx, FROM_POS) == pylon.position
    assert ctx.state.hunt_objective == pylon.position
    assert visible_exp in ctx.state.scouted_expansions


def test_hunt_remaining_bases_sticks_to_a_pinned_cleanup_structure() -> None:
    near = _structure(FROM_POS, type_id=UnitTypeId.PYLON)
    far = _structure(Point2((150.0, 150.0)), type_id=UnitTypeId.PYLON)
    ctx = _ctx(structures=[near, far])
    ctx.bot.start_location = Point2((10.0, 10.0))
    ctx.mediator.get_own_nat = Point2((20.0, 10.0))
    ctx.mediator.get_enemy_expansions = []
    ctx.bot.is_visible.return_value = True
    ctx.bot.enemy_start_locations = [Point2((70.0, 70.0))]
    ctx.state.hunt_objective = far.position
    ctx.state.scouted_expansions = set()

    assert targeting.hunt_remaining_bases(ctx, FROM_POS) == far.position


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


def test_enemy_ramp_bottom_reads_the_ramp() -> None:
    ctx = MagicMock()
    bottom = Point2((10.0, 20.0))
    ctx.mediator.get_enemy_ramp.bottom_center = bottom
    assert targeting.enemy_ramp_bottom(ctx) == bottom


def test_enemy_natural_cleared_when_no_townhall_near_nat() -> None:
    ctx = MagicMock()
    nat = Point2((50.0, 50.0))
    ctx.mediator.get_enemy_nat = nat
    far_hatch = _structure(Point2((200.0, 200.0)), UnitTypeId.HATCHERY)
    ctx.bot.enemy_structures.of_type.return_value = [far_hatch]

    assert targeting.enemy_natural_cleared(ctx)


def test_enemy_natural_not_cleared_with_townhall_at_nat() -> None:
    ctx = MagicMock()
    nat = Point2((50.0, 50.0))
    ctx.mediator.get_enemy_nat = nat
    hatch = _structure(nat, UnitTypeId.HATCHERY)
    ctx.bot.enemy_structures.of_type.return_value = [hatch]

    assert not targeting.enemy_natural_cleared(ctx)


def test_enemy_main_cleared_when_no_townhall_near_start() -> None:
    ctx = MagicMock()
    main = Point2((70.0, 70.0))
    ctx.bot.enemy_start_locations = [main]
    far_hatch = _structure(Point2((200.0, 200.0)), UnitTypeId.HATCHERY)
    ctx.bot.enemy_structures.of_type.return_value = [far_hatch]

    assert targeting.enemy_main_cleared(ctx)


def test_enemy_main_not_cleared_with_townhall_at_start() -> None:
    ctx = MagicMock()
    main = Point2((70.0, 70.0))
    ctx.bot.enemy_start_locations = [main]
    hatch = _structure(main, UnitTypeId.NEXUS)
    ctx.bot.enemy_structures.of_type.return_value = [hatch]

    assert not targeting.enemy_main_cleared(ctx)


def test_enemy_main_fallen_false_before_main_is_ever_seen() -> None:
    """Fog of war: no visible TH must not open cleanup / scout mode."""
    ctx = MagicMock()
    ctx.bot.enemy_start_locations = [Point2((70.0, 70.0))]
    ctx.bot.enemy_structures.of_type.return_value = []
    ctx.state.enemy_main_townhall_seen = False

    assert not targeting.enemy_main_fallen(ctx)
    assert ctx.state.enemy_main_townhall_seen is False


def test_enemy_main_fallen_true_after_seen_then_gone() -> None:
    ctx = MagicMock()
    main = Point2((70.0, 70.0))
    ctx.bot.enemy_start_locations = [main]
    hatch = _structure(main, UnitTypeId.NEXUS)
    ctx.bot.enemy_structures.of_type.return_value = [hatch]
    ctx.state.enemy_main_townhall_seen = False

    assert not targeting.enemy_main_fallen(ctx)
    assert ctx.state.enemy_main_townhall_seen is True

    ctx.bot.enemy_structures.of_type.return_value = []
    assert targeting.enemy_main_fallen(ctx)


def test_squad_destination_uses_attack_objective_when_set() -> None:
    ctx = MagicMock()
    objective = Point2((7.0, 8.0))
    ctx.build.combat.attack_objective = lambda _c: objective

    assert targeting.squad_destination(ctx, FROM_POS) == objective


def test_squad_destination_falls_back_to_attack_target() -> None:
    ctx = _ctx(structures=[_structure(HATCHERY)])
    ctx.build.combat.attack_objective = None

    assert targeting.squad_destination(ctx, FROM_POS) == HATCHERY


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
