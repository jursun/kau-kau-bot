"""Regression: Overseer thrash harden (watch-first scout, sticky no re-issue).

    python -m tests.routines.test_overseers
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ares.behaviors.combat.individual import KeepUnitSafe, PathUnitToTarget
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.core.context import BotContext
from bot.core.state import RunState
from bot.routines import overseers


class _Units(list):
    @property
    def ready(self) -> "_Units":
        return self


def _unit(tag: int, pos: Point2) -> MagicMock:
    unit = MagicMock()
    unit.tag = tag
    unit.position = pos
    unit.orders = []
    unit.order_target = None
    unit.is_moving = False
    unit.type_id = UnitTypeId.OVERSEER
    unit.abilities = []
    return unit


def _ctx(*, overseers_list: list[MagicMock] | None = None) -> BotContext:
    bot = MagicMock()
    home = Point2((10.0, 10.0))
    enemy = Point2((90.0, 90.0))
    bot.start_location = home
    bot.enemy_start_locations = [enemy]
    bot.expansion_locations_list = [
        home,
        Point2((80.0, 80.0)),
        Point2((70.0, 70.0)),
        Point2((60.0, 90.0)),
    ]
    bot.owned_expansions = {home: MagicMock()}
    bot.enemy_structures = MagicMock()
    bot.enemy_structures.of_type = MagicMock(return_value=[])
    ols = list(overseers_list or [])
    bot.units = MagicMock(side_effect=lambda t: _Units(ols if t == UnitTypeId.OVERSEER else []))

    state = RunState()
    ctx = BotContext(bot=bot, build=MagicMock(), state=state)
    ctx.mediator.get_air_grid = "air-grid"
    ctx.mediator.get_squads = MagicMock(return_value=[])
    return ctx


def _scout_ctx(scout: MagicMock) -> BotContext:
    ctx = _ctx(overseers_list=[scout])
    ctx.state.overseer_scout_tag = 7
    ctx.state.overseer_home_tag = None
    ctx.state.overseer_army_tag = None
    ctx.bot.game_info.map_center = Point2((50.0, 50.0))
    ctx.bot.game_info.playable_area = SimpleNamespace(x=0.0, y=0.0, width=100.0, height=100.0)
    ctx.bot.time = 100.0
    ctx.bot.enemy_units = []
    ctx.bot.enemy_structures.__iter__ = lambda self: iter([])
    return ctx


def _threat(pos: Point2, air_range: float = 6.0) -> SimpleNamespace:
    return SimpleNamespace(
        position=pos, can_attack_air=True, air_range=air_range, is_ready=True
    )


def test_scout_peels_then_paths_in_one_maneuver() -> None:
    """The scout now keeps a safety check every frame (the old watch-first
    version floated over the enemy base until it died)."""
    scout = _unit(7, Point2((40.0, 40.0)))
    ctx = _scout_ctx(scout)

    overseers.manage_overseers()(ctx)

    behaviors = [c.args[0] for c in ctx.bot.register_behavior.call_args_list]
    assert len(behaviors) == 1
    micros = behaviors[0].micros
    assert isinstance(micros[0], KeepUnitSafe)
    assert any(isinstance(m, PathUnitToTarget) for m in micros)


def test_scout_tour_is_natural_then_third_then_main() -> None:
    ctx = _scout_ctx(_unit(7, Point2((40.0, 40.0))))

    tour = overseers.scout_tour(ctx)

    # `_ctx` enemy start (90,90); enemy-side expansions nearest it first.
    assert tour == [Point2((80.0, 80.0)), Point2((70.0, 70.0)), Point2((90.0, 90.0))]


def test_scout_parks_at_the_edge_of_the_base_not_on_top_of_it() -> None:
    scout = _unit(7, Point2((40.0, 40.0)))
    ctx = _scout_ctx(scout)

    vantage = overseers._scout_target(ctx, scout)

    base = Point2((80.0, 80.0))
    assert vantage.distance_to(base) == pytest.approx(overseers._SCOUT_VANTAGE_RADIUS)


def test_scout_vantage_avoids_known_anti_air() -> None:
    scout = _unit(7, Point2((40.0, 40.0)))
    ctx = _scout_ctx(scout)
    base = Point2((80.0, 80.0))
    # A Cannon on the side of the base facing us.
    ctx.bot.enemy_structures = [_threat(Point2((74.0, 74.0)), air_range=7.0)]

    vantage = overseers._scout_target(ctx, scout)

    assert vantage.distance_to(Point2((74.0, 74.0))) > 7.0 + overseers._SCOUT_THREAT_MARGIN
    assert vantage.distance_to(base) == pytest.approx(overseers._SCOUT_VANTAGE_RADIUS)


def test_scout_vantage_stays_inside_the_playable_area() -> None:
    scout = _unit(7, Point2((40.0, 40.0)))
    ctx = _scout_ctx(scout)
    # Base at the map's corner: the outward points would leave the map.
    ctx.bot.enemy_start_locations = [Point2((97.0, 97.0))]
    ctx.bot.expansion_locations_list = [Point2((97.0, 97.0))]

    vantage = overseers._scout_target(ctx, scout)

    assert 1.0 <= vantage.x <= 99.0 and 1.0 <= vantage.y <= 99.0


def test_scout_repicks_the_vantage_when_a_threat_arrives_at_it() -> None:
    scout = _unit(7, Point2((40.0, 40.0)))
    ctx = _scout_ctx(scout)
    first = overseers._scout_target(ctx, scout)

    ctx.bot.enemy_units = [_threat(first, air_range=6.0)]  # Stalker lands on it
    second = overseers._scout_target(ctx, scout)

    assert second.distance_to(first) > 3.0


def test_scout_vantage_is_sticky_when_nothing_changes() -> None:
    scout = _unit(7, Point2((40.0, 40.0)))
    ctx = _scout_ctx(scout)
    first = overseers._scout_target(ctx, scout)
    scout.position = Point2((41.0, 39.0))

    assert overseers._scout_target(ctx, scout) == first


def test_scout_advances_to_the_next_base_after_dwelling() -> None:
    scout = _unit(7, Point2((40.0, 40.0)))
    ctx = _scout_ctx(scout)
    vantage = overseers._scout_target(ctx, scout)

    scout.position = vantage  # arrived
    overseers._scout_target(ctx, scout)
    assert ctx.state.overseer_scout_tour_index == 0  # just started dwelling

    ctx.bot.time += overseers._SCOUT_DWELL_S + 0.5
    overseers._scout_target(ctx, scout)

    assert ctx.state.overseer_scout_tour_index == 1
    assert ctx.state.overseer_scout_vantage is None

    nxt = overseers._scout_target(ctx, scout)
    assert nxt.distance_to(Point2((70.0, 70.0))) == pytest.approx(
        overseers._SCOUT_VANTAGE_RADIUS
    )


def test_scout_tour_wraps_after_the_main() -> None:
    scout = _unit(7, Point2((40.0, 40.0)))
    ctx = _scout_ctx(scout)
    ctx.state.overseer_scout_tour_index = 2  # the main
    vantage = overseers._scout_target(ctx, scout)
    scout.position = vantage
    overseers._scout_target(ctx, scout)
    ctx.bot.time += overseers._SCOUT_DWELL_S + 0.5

    overseers._scout_target(ctx, scout)

    assert ctx.state.overseer_scout_tour_index == 0


def test_skip_reissue_when_already_on_dest() -> None:
    ctx = _ctx()
    home = Point2((12.0, 12.0))
    ov = _unit(3, home)
    overseers._safe_air_move(ctx, ov, home)
    assert ctx.bot.register_behavior.call_count == 0


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
