"""Regression tests for `bot.builds.terran.four_rax_proxy` placement and
attack-objective choices:

* Barracks C's crew task - must place via `WorkerTask.near=proxy_location`
  (the enemy fourth's townhall tile) rather than ares' Barracks formation
  around that base.
* The proxy Depot - must use ares' formation at `proxy_location` (`near` is
  unset), not a ring search next to standing Barracks.
* `attack_objective` - stutter destination is the enemy ramp bottom while
  the natural stands, the enemy main once the natural is cleared, and
  `targeting.attack_target` (scout remaining / hidden bases) once the main
  is cleared too.

Runs under pytest, or standalone with no test dependency:

    python -m tests.test_four_rax_proxy
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.builds.terran.four_rax_proxy import (
    PROXY_CREW,
    attack_objective,
    proxy_location,
)


def test_barracks_c_task_places_on_the_enemy_fourth_townhall_tile() -> None:
    """Barracks C must bypass ares' formation via `near=proxy_location` so
    it lands on the expansion's townhall centre, not a precomputed Barracks
    slot that competes with A and B."""
    barracks_c = PROXY_CREW.z_tasks[1]

    assert barracks_c.label == "Barracks C"
    assert barracks_c.where is proxy_location
    assert barracks_c.near is proxy_location


def test_barracks_a_reserves_early_while_b_does_not() -> None:
    """X locks a formation slot immediately; Y paths in before reserving."""
    assert PROXY_CREW.x_tasks[0].label == "Barracks A"
    assert PROXY_CREW.x_tasks[0].reserve_early is True
    assert PROXY_CREW.y_tasks[0].label == "Barracks B"
    assert PROXY_CREW.y_tasks[0].reserve_early is False


def test_proxy_depot_uses_ares_formation_at_the_enemy_fourth() -> None:
    depot = PROXY_CREW.y_tasks[1]

    assert depot.label == "Depot (proxy)"
    assert depot.where is proxy_location
    assert depot.near is None


def test_attack_objective_is_ramp_bottom_while_natural_stands() -> None:
    ctx = MagicMock()
    nat = Point2((50.0, 50.0))
    bottom = Point2((60.0, 60.0))
    ctx.mediator.get_enemy_nat = nat
    ctx.mediator.get_enemy_ramp.bottom_center = bottom
    ctx.bot.enemy_start_locations = [Point2((70.0, 70.0))]
    hatch = MagicMock()
    hatch.position = nat
    hatch.type_id = UnitTypeId.HATCHERY
    ctx.bot.enemy_structures.of_type.return_value = [hatch]

    assert attack_objective(ctx) == bottom


def test_attack_objective_is_enemy_main_once_natural_is_cleared() -> None:
    """Natural gone, main still standing → walk up the ramp into the start."""
    ctx = MagicMock()
    ctx.mediator.get_enemy_nat = Point2((50.0, 50.0))
    ctx.mediator.get_enemy_ramp.bottom_center = Point2((60.0, 60.0))
    main = Point2((70.0, 70.0))
    ctx.bot.enemy_start_locations = [main]
    # Townhall still at the enemy start — main not cleared yet.
    hatch = MagicMock()
    hatch.position = main
    hatch.type_id = UnitTypeId.COMMANDCENTER
    ctx.bot.enemy_structures.of_type.return_value = [hatch]

    assert attack_objective(ctx) == main


def test_attack_objective_scouts_once_enemy_main_is_cleared() -> None:
    """Main seen-and-gone → hunt via hunt_remaining_bases, not camp."""
    ctx = MagicMock()
    ctx.mediator.get_enemy_nat = Point2((50.0, 50.0))
    ctx.bot.enemy_start_locations = [Point2((70.0, 70.0))]
    ctx.bot.enemy_structures.of_type.return_value = []
    ctx.units_in_role.return_value = []
    ctx.state.enemy_main_townhall_seen = True
    scouted = Point2((120.0, 30.0))

    with patch(
        "bot.builds.terran.four_rax_proxy.targeting.hunt_remaining_bases",
        return_value=scouted,
    ) as hunt:
        assert attack_objective(ctx) == scouted
        hunt.assert_called_once()
        assert hunt.call_args.args[1] == ctx.bot.enemy_start_locations[0]


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
