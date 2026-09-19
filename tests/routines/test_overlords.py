"""Regression tests for `bot.routines.overlords.manage_overlord_positions`.

Pins: early HG spots from `get_ol_spots`; mid-late Spore parks; SCOUTING /
`scout_tags` Overlords left to `air_scout`.

    python -m tests.routines.test_overlords
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from ares.behaviors.combat.individual import PathUnitToTarget
from ares.consts import UnitRole
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.core.context import BotContext
from bot.core.state import RunState
from bot.routines import overlords


class _Units(list):
    @property
    def ready(self) -> "_Units":
        return self


def _unit(tag: int, pos: Point2, *, ready: bool = True) -> MagicMock:
    unit = MagicMock()
    unit.tag = tag
    unit.position = pos
    unit.is_ready = ready
    return unit


def _close(a: Point2, b: Point2, eps: float = 0.1) -> bool:
    return abs(a.x - b.x) < eps and abs(a.y - b.y) < eps


def _ctx(
    *,
    ol_units: list[MagicMock] | None = None,
    spores: list[MagicMock] | None = None,
    ol_spots: list[Point2] | None = None,
    scout_tags: set[int] | None = None,
    scouting_units: list[MagicMock] | None = None,
    home: Point2 | None = None,
    enemy: Point2 | None = None,
) -> BotContext:
    bot = MagicMock()
    home = home or Point2((10.0, 10.0))
    enemy = enemy or Point2((90.0, 90.0))
    bot.start_location = home
    bot.enemy_start_locations = [enemy]
    bot.units = MagicMock(side_effect=lambda _t: _Units(ol_units or []))
    bot.structures = MagicMock(side_effect=lambda _t: _Units(spores or []))
    bot.game_info = MagicMock()
    bot.game_info.map_center = Point2((50.0, 50.0))

    state = RunState()
    if scout_tags:
        state.scout_tags = set(scout_tags)

    ctx = BotContext(bot=bot, build=MagicMock(), state=state)
    ctx.mediator.get_ol_spots = list(ol_spots or [])
    ctx.mediator.get_air_grid = "air-grid"

    scouting = list(scouting_units or [])

    def get_units_from_role(*, role, unit_type):
        if role == UnitRole.SCOUTING and unit_type == UnitTypeId.OVERLORD:
            return scouting
        return []

    ctx.mediator.get_units_from_role.side_effect = get_units_from_role
    return ctx


def _path_targets(ctx: BotContext) -> list[tuple[MagicMock, Point2]]:
    out = []
    for call in ctx.bot.register_behavior.call_args_list:
        behavior = call.args[0]
        assert isinstance(behavior, PathUnitToTarget)
        out.append((behavior.unit, behavior.target))
    return out


def test_early_uses_ol_spots_near_our_side() -> None:
    home_spot = Point2((20.0, 20.0))
    mid_spot = Point2((50.0, 50.0))
    enemy_spot = Point2((80.0, 80.0))
    ol_a = _unit(1, Point2((12.0, 12.0)))
    ol_b = _unit(2, Point2((14.0, 14.0)))
    ctx = _ctx(
        ol_units=[ol_a, ol_b],
        spores=[],
        ol_spots=[enemy_spot, mid_spot, home_spot],
    )

    overlords.manage_overlord_positions()(ctx)

    targets = _path_targets(ctx)
    assert len(targets) == 2
    assigned = {t for _, t in targets}
    assert home_spot in assigned
    assert enemy_spot not in assigned or mid_spot in assigned
    assert ctx.state.overlord_park_phase == "vision"
    assert set(ctx.state.overlord_park_targets) == {1, 2}


def test_with_spores_targets_spore_positions() -> None:
    spore_a = _unit(10, Point2((15.0, 15.0)))
    spore_b = _unit(11, Point2((25.0, 25.0)))
    ol_a = _unit(1, Point2((12.0, 12.0)))
    ol_b = _unit(2, Point2((30.0, 30.0)))
    ol_extra = _unit(3, Point2((40.0, 40.0)))
    ctx = _ctx(
        ol_units=[ol_a, ol_b, ol_extra],
        spores=[spore_a, spore_b],
        ol_spots=[Point2((5.0, 5.0))],
    )

    overlords.manage_overlord_positions()(ctx)

    spore_pos = {Point2((15.0, 15.0)), Point2((25.0, 25.0))}
    parked = list(ctx.state.overlord_park_targets.values())
    assert len(parked) == 3
    assert all(any(_close(p, s) for s in spore_pos) for p in parked)
    assert ctx.state.overlord_park_phase == "spore"
    assert Point2((5.0, 5.0)) not in parked


def test_scouting_overlord_ignored() -> None:
    scout = _unit(7, Point2((12.0, 12.0)))
    other = _unit(8, Point2((14.0, 14.0)))
    spot = Point2((20.0, 20.0))
    ctx = _ctx(
        ol_units=[scout, other],
        spores=[],
        ol_spots=[spot],
        scout_tags={7},
        scouting_units=[scout],
    )

    overlords.manage_overlord_positions()(ctx)

    targets = _path_targets(ctx)
    assert len(targets) == 1
    assert targets[0][0] is other
    assert targets[0][1] == spot
    assert 7 not in ctx.state.overlord_park_targets


def test_no_keep_unit_safe_on_park() -> None:
    ol = _unit(1, Point2((12.0, 12.0)))
    ctx = _ctx(ol_units=[ol], ol_spots=[Point2((20.0, 20.0))])

    overlords.manage_overlord_positions()(ctx)

    for call in ctx.bot.register_behavior.call_args_list:
        behavior = call.args[0]
        assert type(behavior).__name__ == "PathUnitToTarget"


def test_sticky_targets_do_not_thrash() -> None:
    ol = _unit(1, Point2((12.0, 12.0)))
    spot_a = Point2((20.0, 20.0))
    spot_b = Point2((22.0, 18.0))
    ctx = _ctx(ol_units=[ol], ol_spots=[spot_a, spot_b])

    overlords.manage_overlord_positions()(ctx)
    first = ctx.state.overlord_park_targets[1]
    ctx.bot.register_behavior.reset_mock()

    overlords.manage_overlord_positions()(ctx)
    assert ctx.state.overlord_park_targets[1] == first


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
