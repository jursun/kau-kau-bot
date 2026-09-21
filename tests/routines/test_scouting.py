"""Regression tests for `bot/routines/scouting.py`.

Runs under pytest, or standalone with no test dependency:

    python -m tests.routines.test_scouting
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from ares.behaviors.combat.individual import PathUnitToTarget
from ares.consts import UnitRole
from sc2.position import Point2

from bot.core.context import BotContext
from bot.core.state import RunState
from bot.routines import scouting


def _ctx() -> BotContext:
    ctx = BotContext(bot=MagicMock(), build=MagicMock(), state=RunState())
    ctx.bot.watchtowers = []
    ctx.bot.enemy_start_locations = [Point2((100.0, 100.0))]
    ctx.bot.game_info.map_center = Point2((50.0, 50.0))
    ctx.bot.start_location = Point2((10.0, 10.0))
    ctx.mediator.find_raw_path.return_value = [Point2((1.0, 1.0))]
    ctx.mediator.get_ground_grid = "ground-grid"
    ctx.mediator.get_own_expansions = [Point2((10.0, 10.0))]
    ctx.mediator.get_own_nat = Point2((10.0, 10.0))
    ctx.mediator.get_enemy_nat = Point2((85.0, 85.0))
    ctx.mediator.get_enemy_third = Point2((70.0, 40.0))
    ctx.mediator.get_enemy_fourth = Point2((40.0, 70.0))
    ctx.mediator.get_units_in_range.return_value = [[]]
    return ctx


def _unit(tag: int) -> MagicMock:
    unit = MagicMock()
    unit.tag = tag
    return unit


def _tower(x: float, y: float) -> MagicMock:
    tower = MagicMock()
    tower.position = Point2((x, y))
    return tower


def test_air_scout_registers_a_bare_path_with_no_danger_avoidance() -> None:
    ctx = _ctx()
    scout = _unit(7)
    ctx.mediator.get_units_from_role.return_value = [scout]
    ctx.mediator.get_air_grid = "air-grid"
    destination = Point2((30.0, 40.0))

    scouting.air_scout(unit_type=None, target=lambda _ctx: destination)(ctx)

    ctx.bot.register_behavior.assert_called_once()
    registered = ctx.bot.register_behavior.call_args.args[0]
    assert isinstance(registered, PathUnitToTarget)
    assert registered.unit is scout
    assert registered.target == destination
    assert registered.grid == "air-grid"


def test_air_scout_does_nothing_without_a_scout() -> None:
    ctx = _ctx()
    ctx.mediator.get_units_from_role.return_value = []

    scouting.air_scout(unit_type=None)(ctx)

    ctx.bot.register_behavior.assert_not_called()


def test_opening_watch_points_are_nat_mid_tower_or_expansions() -> None:
    """4 parks: enemy-nat front, mid, tower/3rd, tower/4th."""
    ctx = _ctx()
    ctx.bot.watchtowers = [_tower(30.0, 50.0), _tower(50.0, 30.0)]

    points = scouting._opening_ling_watch_points(ctx)

    assert len(points) == scouting.OPENING_LING_SCOUT_CAP
    assert scouting.opening_zergling_scout_cap(ctx) == 4
    enemy_nat = Point2((85.0, 85.0))
    home = Point2((10.0, 10.0))
    mid = Point2((50.0, 50.0))
    # Slot 0 near enemy nat (outside, toward home).
    assert points[0].distance_to(enemy_nat) < 25.0
    assert points[0].distance_to(home) < points[0].distance_to(
        Point2((100.0, 100.0))
    ) or points[0].distance_to(enemy_nat) >= scouting._ENEMY_BASE_LEASH - 1
    # Slot 1 is mid-map.
    assert points[1].distance_to(mid) < 1.0
    # Slots 2/3 are the towers.
    assert Point2((30.0, 50.0)) in points
    assert Point2((50.0, 30.0)) in points


def test_opening_watch_points_fallback_to_enemy_third_fourth() -> None:
    """No towers → slots 3/4 use outside enemy 3rd / 4th."""
    ctx = _ctx()
    ctx.bot.watchtowers = []

    points = scouting._opening_ling_watch_points(ctx)

    assert len(points) == 4
    third = Point2((70.0, 40.0))
    fourth = Point2((40.0, 70.0))
    # At least one park near each expansion (outside, not on the pad).
    assert any(p.distance_to(third) < 22.0 for p in points)
    assert any(p.distance_to(fourth) < 22.0 for p in points)


def test_scout_with_zerglings_paths_toward_watch_points() -> None:
    from bot.consts import ZERGLING_SCOUT_ROLE
    from sc2.ids.unit_typeid import UnitTypeId

    ctx = _ctx()
    ling = _unit(3)
    ling.position = Point2((20.0, 20.0))
    ctx.mediator.get_units_from_role.return_value = [ling]

    scouting.scout_with_zerglings()(ctx)

    ctx.mediator.get_units_from_role.assert_called_with(
        role=ZERGLING_SCOUT_ROLE, unit_type=UnitTypeId.ZERGLING
    )
    ctx.bot.register_behavior.assert_called_once()
    registered = ctx.bot.register_behavior.call_args.args[0]
    assert isinstance(registered, PathUnitToTarget)
    assert registered.unit is ling
    assert 3 in ctx.state.zergling_scout_destinations


def test_scout_with_zerglings_assigns_one_ling_per_watch() -> None:
    ctx = _ctx()
    a = _unit(1)
    a.position = Point2((10.0, 10.0))
    b = _unit(2)
    b.position = Point2((10.0, 10.0))
    ctx.mediator.get_units_from_role.return_value = [a, b]

    scouting.scout_with_zerglings()(ctx)

    assert 1 in ctx.state.zergling_scout_destinations
    assert 2 in ctx.state.zergling_scout_destinations
    assert ctx.bot.register_behavior.call_count == 2


def test_scout_extras_go_to_defending_not_home() -> None:
    """5th scout drops to DEFENDING (army), never home garrison."""
    ctx = _ctx()
    lings = []
    for tag in range(1, 6):
        u = _unit(tag)
        u.position = Point2((10.0, 10.0))
        lings.append(u)
    ctx.mediator.get_units_from_role.return_value = lings

    scouting.scout_with_zerglings()(ctx)

    ctx.mediator.assign_role.assert_called_once_with(
        tag=5, role=UnitRole.DEFENDING
    )


def test_scout_with_zerglings_kites_when_enemy_nearby() -> None:
    ctx = _ctx()
    ling = _unit(3)
    ling.position = Point2((70.0, 70.0))
    enemy = _unit(99)
    enemy.position = Point2((75.0, 75.0))
    enemy.is_structure = False
    ctx.mediator.get_units_from_role.return_value = [ling]
    ctx.mediator.get_units_in_range.return_value = [[enemy]]

    scouting.scout_with_zerglings()(ctx)

    registered = ctx.bot.register_behavior.call_args.args[0]
    assert isinstance(registered, PathUnitToTarget)
    home = Point2((10.0, 10.0))
    assert registered.target.distance_to(home) < ling.position.distance_to(home)


def test_scout_with_zerglings_leashes_out_of_enemy_main() -> None:
    ctx = _ctx()
    ling = _unit(3)
    ling.position = Point2((95.0, 95.0))
    ctx.mediator.get_units_from_role.return_value = [ling]
    ctx.mediator.get_units_in_range.return_value = [[]]

    scouting.scout_with_zerglings()(ctx)

    registered = ctx.bot.register_behavior.call_args.args[0]
    assert isinstance(registered, PathUnitToTarget)
    home = Point2((10.0, 10.0))
    assert registered.target.distance_to(home) < 5.0


def test_scout_never_pulled_on_early_aggression() -> None:
    """Scouts stay on SCOUT role through early aggression — scout+kite only."""
    from bot.consts import ZERGLING_SCOUT_ROLE
    from sc2.ids.unit_typeid import UnitTypeId

    ctx = _ctx()
    ctx.state.early_aggression = True
    ling = _unit(5)
    ling.position = Point2((40.0, 40.0))
    ctx.state.zergling_scout_destinations[5] = Point2((50.0, 50.0))
    ctx.mediator.get_units_from_role.return_value = [ling]

    scouting.scout_with_zerglings()(ctx)

    # Must not reassign to DEFENDER / DEFENDING for aggression.
    for call in ctx.mediator.assign_role.call_args_list:
        assert call.kwargs.get("role") not in {
            UnitRole.DEFENDING,
        } or call.kwargs.get("tag") != 5
    # Still driving as a scout (path or kite).
    ctx.mediator.get_units_from_role.assert_called_with(
        role=ZERGLING_SCOUT_ROLE, unit_type=UnitTypeId.ZERGLING
    )
    assert 5 in ctx.state.zergling_scout_destinations or ctx.bot.register_behavior.called


def test_scout_never_pulled_when_early_window_closes() -> None:
    from bot.consts import ZERGLING_SCOUT_ROLE
    from sc2.ids.unit_typeid import UnitTypeId

    ctx = _ctx()
    ctx.bot.time = 301.0
    ctx.state.early_aggression = False
    ling = _unit(5)
    ling.position = Point2((40.0, 40.0))
    ctx.state.zergling_scout_destinations[5] = Point2((50.0, 50.0))
    ctx.mediator.get_units_from_role.return_value = [ling]

    scouting.scout_with_zerglings()(ctx)

    ctx.mediator.assign_role.assert_not_called()
    ctx.mediator.get_units_from_role.assert_called_with(
        role=ZERGLING_SCOUT_ROLE, unit_type=UnitTypeId.ZERGLING
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
