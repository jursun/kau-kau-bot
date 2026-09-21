"""Regression tests for `routines.creep` highway spread.

Runs under pytest, or standalone with no test dependency:

    python -m tests.routines.test_creep
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from ares.consts import UnitRole
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.core.context import BotContext
from bot.core.state import RunState
from bot.routines import creep

# Fixed priority spots used by `_ctx` so highway tests can cover them.
_RAMP_TOP = Point2((22.0, 10.0))
_NAT = Point2((50.0, 20.0))
_ENEMY_START = Point2((100.0, 100.0))
_NAT_FRONT = _NAT.towards(_ENEMY_START, 8.0)
_NYDUS_MAIN = Point2((12.0, 22.0))


class _TumorList(list):
    """List that supports `|` the way python-sc2 Units does."""

    def __or__(self, other):
        return _TumorList(list(self) + list(other))


def _queen(tag: int, position: Point2 | None = None) -> MagicMock:
    queen = MagicMock()
    queen.tag = tag
    queen.position = position or Point2((10.0, 10.0))
    queen.abilities = {AbilityId.BUILD_CREEPTUMOR_QUEEN}
    queen.is_using_ability.return_value = False
    return queen


def _tumor_unit(tag: int, position: Point2) -> MagicMock:
    tumor = MagicMock()
    tumor.tag = tag
    tumor.position = position
    return tumor


def _set_tumors(ctx: BotContext, positions: list[Point2]) -> None:
    """Place mock tumors so priority spots count as covered."""
    tumors = _TumorList(
        _tumor_unit(1000 + i, pos) for i, pos in enumerate(positions)
    )

    def structures(unit_type):
        if unit_type == UnitTypeId.CREEPTUMORQUEEN:
            return tumors
        return _TumorList()

    ctx.bot.structures = MagicMock(side_effect=structures)


def _cover_priorities(ctx: BotContext) -> None:
    """Seed one tumor on each priority spot so targeting falls to highway."""
    _set_tumors(ctx, [_RAMP_TOP, _NAT_FRONT, _NYDUS_MAIN])


def _ctx(townhall_count: int = 3) -> BotContext:
    bot = MagicMock()
    bot.start_location = Point2((20.0, 20.0))
    bot.enemy_start_locations = [_ENEMY_START]
    bot.main_base_ramp = MagicMock(top_center=_RAMP_TOP)
    bot.townhalls.ready = []
    for i in range(townhall_count):
        th = MagicMock()
        th.position = Point2((20.0 + i * 30.0, 20.0))
        bot.townhalls.ready.append(th)
    bot.config = {}
    bot.structures = MagicMock(side_effect=lambda _ut: _TumorList())
    build = MagicMock()
    build.combat = SimpleNamespace(rally_offset=8.0, rally=None)
    ctx = BotContext(bot=bot, build=build, state=RunState())
    # Opening natural tumor claim finished — highway promotion tests need
    # a free Queen.
    ctx.state.natural_queen_tumor_done = True
    ctx.mediator.get_own_nat = _NAT
    ctx.mediator.get_own_expansions = [_NAT]
    ctx.mediator.get_enemy_nat = Point2((90.0, 90.0))
    ctx.mediator.get_primary_nydus_own_main = _NYDUS_MAIN
    ctx.mediator.get_creep_grid = object()
    ctx.mediator.get_ground_grid = object()
    ctx.mediator.should_calculate_tumor_spread = False  # must not gate casts
    ctx.mediator.get_next_tumor_on_path.return_value = Point2((25.0, 20.0))
    ctx.mediator.find_nearby_creep_edge_position.return_value = None
    return ctx


def _role_lookup(creep_queens=(), injectors=()):
    def get_units_from_role(*, role, unit_type):
        if role == UnitRole.QUEEN_CREEP:
            return list(creep_queens)
        if role == UnitRole.QUEEN_INJECT:
            return list(injectors)
        raise AssertionError(f"unexpected role {role}")

    return get_units_from_role


def test_no_queen_promoted_until_one_is_spare_beyond_one_per_base() -> None:
    ctx = _ctx(townhall_count=3)
    injectors = [_queen(i) for i in range(3)]
    ctx.mediator.get_units_from_role.side_effect = _role_lookup(injectors=injectors)

    creep.spread_creep()(ctx)

    ctx.mediator.assign_role.assert_not_called()


def test_newest_injector_promoted_once_one_is_spare() -> None:
    import bot.routines.creep as creep_mod

    ctx = _ctx(townhall_count=3)
    injectors = [_queen(t) for t in (5, 9, 2, 1)]
    ctx.mediator.get_units_from_role.side_effect = _role_lookup(injectors=injectors)
    creep_mod.cy_has_creep = lambda grid, pos: False  # type: ignore[attr-defined]

    creep.spread_creep()(ctx)

    ctx.mediator.assign_role.assert_called_once_with(tag=9, role=UnitRole.QUEEN_CREEP)


def test_second_spare_promoted_when_only_one_creep_queen() -> None:
    """Macro Zerg wants two extras on creep/defense — promote until 2."""
    import bot.routines.creep as creep_mod

    ctx = _ctx(townhall_count=3)
    creep_q = _queen(1, Point2((20.0, 20.0)))
    injectors = [_queen(t) for t in (5, 9, 2, 4)]  # 4 injectors on 3 bases
    ctx.mediator.get_units_from_role.side_effect = _role_lookup(
        creep_queens=[creep_q], injectors=injectors
    )
    creep_mod.cy_has_creep = lambda grid, pos: False  # type: ignore[attr-defined]

    creep.spread_creep()(ctx)

    ctx.mediator.assign_role.assert_called_once_with(tag=9, role=UnitRole.QUEEN_CREEP)


def test_creep_queen_plants_toward_highway_not_enemy_nat() -> None:
    """Creep Queen uses get_next_tumor_on_path toward an own base gap."""
    import bot.routines.creep as creep_mod

    ctx = _ctx(townhall_count=3)
    _cover_priorities(ctx)
    queen = _queen(9, Point2((25.0, 20.0)))  # within 3-tile cast range of plant
    ctx.mediator.get_units_from_role.side_effect = _role_lookup(creep_queens=[queen])
    plant = Point2((25.0, 20.0))
    ctx.mediator.get_next_tumor_on_path.return_value = plant
    # Nat (50,20) and third (80,20) lack creep; highway should aim nat first.
    # Plant tile itself must be on creep or the cast is skipped.
    creep_mod.cy_has_creep = (  # type: ignore[attr-defined]
        lambda grid, pos: pos == plant
    )

    creep.spread_creep()(ctx)

    ctx.mediator.get_next_tumor_on_path.assert_called()
    kwargs = ctx.mediator.get_next_tumor_on_path.call_args.kwargs
    assert kwargs["to_pos"] == Point2((50.0, 20.0))
    queen.assert_called()  # cast BUILD_CREEPTUMOR_QUEEN


def test_spread_creep_skips_natural_opening_claim_queen() -> None:
    import bot.routines.creep as creep_mod

    creep_mod._QUEEN_TUMOR_STICKY.clear()
    creep_mod._QUEEN_TUMOR_CAST_AT.clear()
    ctx = _ctx()
    natural_claim = _queen(5)
    other = _queen(9, Point2((25.0, 20.0)))
    ctx.state.natural_queen_tag = 5
    ctx.state.natural_queen_tumor_done = False
    ctx.mediator.get_units_from_role.side_effect = _role_lookup(
        creep_queens=[natural_claim, other]
    )
    plant = Point2((25.0, 20.0))
    ctx.mediator.get_next_tumor_on_path.return_value = plant
    creep_mod.cy_has_creep = (  # type: ignore[attr-defined]
        lambda grid, pos: pos == plant
    )

    creep.spread_creep()(ctx)

    assert other.called or ctx.mediator.get_next_tumor_on_path.called
    natural_claim.assert_not_called()


def _tumor(tag: int, position: Point2 | None = None) -> MagicMock:
    tumor = MagicMock()
    tumor.tag = tag
    tumor.position = position or Point2((20.0, 20.0))
    tumor.abilities = {AbilityId.BUILD_CREEPTUMOR_TUMOR}
    return tumor


def test_spread_tumors_does_nothing_without_a_burrowed_tumor() -> None:
    ctx = _ctx()
    ctx.mediator.get_own_structures_dict = {UnitTypeId.CREEPTUMORBURROWED: []}

    creep.spread_tumors()(ctx)

    ctx.mediator.get_next_tumor_on_path.assert_not_called()


def test_spread_tumors_paths_each_tumor_along_own_base_highway() -> None:
    import bot.routines.creep as creep_mod

    ctx = _ctx(townhall_count=3)
    _cover_priorities(ctx)
    tumors = [_tumor(1, Point2((20.0, 20.0))), _tumor(2, Point2((22.0, 20.0)))]
    ctx.mediator.get_own_structures_dict = {UnitTypeId.CREEPTUMORBURROWED: tumors}
    plant = Point2((25.0, 20.0))
    ctx.mediator.get_next_tumor_on_path.return_value = plant
    creep_mod.cy_has_creep = (  # type: ignore[attr-defined]
        lambda grid, pos: pos == plant
    )

    creep.spread_tumors()(ctx)

    assert ctx.mediator.get_next_tumor_on_path.call_count == 2
    for call in ctx.mediator.get_next_tumor_on_path.call_args_list:
        assert call.kwargs["to_pos"] == Point2((50.0, 20.0))
    for tumor in tumors:
        tumor.assert_called()


def test_highway_orders_bases_by_distance_from_main() -> None:
    """Fan from main: nearer expansions before far ones, not nearest-neighbor
    along the previous base."""
    ctx = _ctx(townhall_count=0)
    ctx.bot.start_location = Point2((0.0, 0.0))
    # Far base on the x-axis, nearer third off-axis — NN-from-previous would
    # prefer continuing along x; fan-from-main must take the nearer one first.
    far = MagicMock()
    far.position = Point2((80.0, 0.0))
    near = MagicMock()
    near.position = Point2((30.0, 30.0))
    main_th = MagicMock()
    main_th.position = Point2((0.0, 0.0))
    ctx.bot.townhalls.ready = [main_th, far, near]

    ordered = creep._own_bases_highway(ctx)

    assert ordered[0] == Point2((0.0, 0.0))
    assert ordered[1] == Point2((30.0, 30.0))
    assert ordered[2] == Point2((80.0, 0.0))


def test_highway_target_skips_bases_already_on_creep() -> None:
    import bot.routines.creep as creep_mod

    ctx = _ctx(townhall_count=3)
    _cover_priorities(ctx)
    # Nat on creep; third not — should aim third (80,20).
    def has_creep(grid, pos):
        return pos == Point2((50.0, 20.0))

    creep_mod.cy_has_creep = has_creep  # type: ignore[attr-defined]
    target = creep._creep_spread_target(ctx, Point2((20.0, 20.0)))
    assert target == Point2((80.0, 20.0))


def test_spread_target_stays_on_own_bases_once_connected() -> None:
    """Once every own base has creep, keep thickening the furthest hub —
    never push to the enemy natural."""
    import bot.routines.creep as creep_mod

    ctx = _ctx(townhall_count=3)
    _cover_priorities(ctx)
    own = {
        Point2((20.0, 20.0)),
        Point2((50.0, 20.0)),
        Point2((80.0, 20.0)),
    }
    creep_mod.cy_has_creep = (  # type: ignore[attr-defined]
        lambda grid, pos: pos in own
    )
    target = creep._creep_spread_target(ctx, Point2((20.0, 20.0)))
    assert target == Point2((80.0, 20.0))
    assert target != Point2((90.0, 90.0))


def test_priority_target_is_nat_front_when_uncovered() -> None:
    ctx = _ctx()
    target = creep._creep_spread_target(ctx, Point2((20.0, 20.0)))
    assert target == _NAT_FRONT


def test_priority_target_advances_to_ramp_after_nat_front_covered() -> None:
    ctx = _ctx()
    _set_tumors(ctx, [_NAT_FRONT])
    target = creep._creep_spread_target(ctx, Point2((20.0, 20.0)))
    assert target == _RAMP_TOP


def test_priority_target_nydus_after_nat_front_and_ramp() -> None:
    ctx = _ctx()
    _set_tumors(ctx, [_NAT_FRONT, _RAMP_TOP])
    target = creep._creep_spread_target(ctx, Point2((20.0, 20.0)))
    assert target == _NYDUS_MAIN


def test_priority_skips_spot_that_already_has_a_tumor() -> None:
    ctx = _ctx()
    # Tumor slightly offset still covers nat front (within radius 6).
    _set_tumors(ctx, [Point2((_NAT_FRONT.x + 2.0, _NAT_FRONT.y + 1.0))])
    target = creep._creep_spread_target(ctx, Point2((20.0, 20.0)))
    assert target == _RAMP_TOP


def test_pick_furthest_from_tumors_prefers_distant_candidate() -> None:
    ctx = _ctx()
    existing = Point2((20.0, 20.0))
    _set_tumors(ctx, [existing])
    near = Point2((22.0, 20.0))
    far = Point2((80.0, 20.0))
    pick = creep._pick_furthest_from_tumors(ctx, [near, far])
    assert pick == far


def test_place_tumor_uses_min_separation_and_furthest_candidate() -> None:
    """Plant path must request separation and keep the furthest *in-range* spot."""
    import bot.routines.creep as creep_mod

    creep_mod._TUMOR_CAST_AT.clear()
    ctx = _ctx(townhall_count=2)
    _set_tumors(ctx, [Point2((20.0, 20.0))])
    tumor = _tumor(1, Point2((20.0, 20.0)))
    tumor.abilities = {AbilityId.BUILD_CREEPTUMOR_TUMOR}
    near = Point2((25.0, 20.0))  # 5 tiles — in range
    far = Point2((29.0, 20.0))  # 9 tiles — still in range (≤10)
    ctx.mediator.get_next_tumor_on_path.return_value = near
    ctx.mediator.find_nearby_creep_edge_position.return_value = far
    creep_mod.cy_has_creep = lambda grid, pos: True  # type: ignore[attr-defined]

    acted = creep._place_tumor_toward(
        ctx, tumor, Point2((80.0, 20.0)), queen=False
    )

    assert acted is True
    kwargs = ctx.mediator.get_next_tumor_on_path.call_args.kwargs
    assert kwargs["min_separation"] == creep._TUMOR_MIN_SEPARATION
    edge_kwargs = ctx.mediator.find_nearby_creep_edge_position.call_args.kwargs
    assert edge_kwargs["spread_dist"] == creep._TUMOR_MIN_SEPARATION
    assert edge_kwargs["search_radius"] == creep._TUMOR_CAST_RANGE
    assert edge_kwargs["closest_valid"] is False
    tumor.assert_called_once()
    assert tumor.call_args.args[1] == far


def test_tumor_skips_spot_beyond_cast_range() -> None:
    """Regression: casting at 17+ tiles looped TargetIsOutOfRange."""
    import bot.routines.creep as creep_mod

    creep_mod._TUMOR_CAST_AT.clear()
    ctx = _ctx(townhall_count=2)
    _set_tumors(ctx, [Point2((20.0, 20.0))])
    tumor = _tumor(1, Point2((20.0, 20.0)))
    tumor.abilities = {AbilityId.BUILD_CREEPTUMOR_TUMOR}
    oor = Point2((38.0, 20.0))  # 18 tiles away
    ctx.mediator.get_next_tumor_on_path.return_value = oor
    ctx.mediator.find_nearby_creep_edge_position.return_value = None
    creep_mod.cy_has_creep = lambda grid, pos: True  # type: ignore[attr-defined]

    acted = creep._place_tumor_toward(
        ctx, tumor, Point2((80.0, 20.0)), queen=False
    )

    assert acted is False
    tumor.assert_not_called()


def test_tumor_skips_invisible_spot() -> None:
    """Regression: planting fogged creep edge → CantSeeBuildLocation."""
    import bot.routines.creep as creep_mod

    creep_mod._TUMOR_CAST_AT.clear()
    ctx = _ctx(townhall_count=2)
    _set_tumors(ctx, [Point2((20.0, 20.0))])
    tumor = _tumor(1, Point2((20.0, 20.0)))
    tumor.abilities = {AbilityId.BUILD_CREEPTUMOR_TUMOR}
    fogged = Point2((28.0, 20.0))  # in range, on creep, no vision
    ctx.mediator.get_next_tumor_on_path.return_value = fogged
    ctx.mediator.find_nearby_creep_edge_position.return_value = None
    creep_mod.cy_has_creep = lambda grid, pos: True  # type: ignore[attr-defined]
    ctx.bot.is_visible = MagicMock(return_value=False)

    acted = creep._place_tumor_toward(
        ctx, tumor, Point2((80.0, 20.0)), queen=False
    )

    assert acted is False
    tumor.assert_not_called()


def test_queen_keeps_sticky_after_cast_instead_of_repicking() -> None:
    """Regression: popping sticky on cast made the next frame re-pick a
    different furthest edge and cancel the morph (walk↔cast loop)."""
    import bot.routines.creep as creep_mod

    creep_mod._QUEEN_TUMOR_STICKY.clear()
    ctx = _ctx(townhall_count=2)
    _set_tumors(ctx, [Point2((10.0, 10.0))])
    far_a = Point2((55.0, 20.0))
    far_b = Point2((70.0, 40.0))
    # Stand on the furthest candidate so we cast, not walk.
    queen = _queen(7, far_b)
    queen.order_target = None
    ctx.mediator.get_next_tumor_on_path.return_value = far_a
    ctx.mediator.find_nearby_creep_edge_position.return_value = far_b
    creep_mod.cy_has_creep = lambda grid, pos: True  # type: ignore[attr-defined]

    assert creep._place_tumor_toward(ctx, queen, _NAT, queen=True) is True
    assert creep_mod._QUEEN_TUMOR_STICKY[7] == far_b  # furthest kept
    queen.assert_called_once_with(AbilityId.BUILD_CREEPTUMOR_QUEEN, far_b)

    # Next frame: ability still up, already ordered onto sticky — must not
    # re-cast or flip to another edge.
    queen.reset_mock()
    queen.order_target = far_b
    queen.is_using_ability.return_value = False
    ctx.mediator.get_next_tumor_on_path.return_value = far_a
    ctx.mediator.find_nearby_creep_edge_position.return_value = Point2((90.0, 90.0))

    assert creep._place_tumor_toward(ctx, queen, _NAT, queen=True) is True
    queen.assert_not_called()
    queen.move.assert_not_called()
    assert creep_mod._QUEEN_TUMOR_STICKY[7] == far_b


def test_queen_sticky_clears_once_tumor_lands_nearby() -> None:
    import bot.routines.creep as creep_mod

    creep_mod._QUEEN_TUMOR_STICKY.clear()
    sticky = Point2((55.0, 20.0))
    creep_mod._QUEEN_TUMOR_STICKY[7] = sticky
    ctx = _ctx(townhall_count=2)
    _set_tumors(ctx, [sticky])
    queen = _queen(7, Point2((55.0, 20.0)))
    queen.order_target = None
    queen.abilities = set()  # energy spent; pre-walk branch
    creep_mod.cy_has_creep = lambda grid, pos: True  # type: ignore[attr-defined]
    ctx.mediator.get_next_tumor_on_path.return_value = Point2((80.0, 20.0))

    creep._place_tumor_toward(ctx, queen, _NAT, queen=True)

    # Prior sticky cleared (may latch a new pre-walk dest while energy recovers).
    assert creep_mod._QUEEN_TUMOR_STICKY.get(7) != sticky


def test_tumors_cast_even_when_coverage_throttle_is_off() -> None:
    """Ready tumors must plant without waiting on should_calculate_tumor_spread."""
    import bot.routines.creep as creep_mod

    creep_mod._TUMOR_CAST_AT.clear()
    ctx = _ctx(townhall_count=2)
    ctx.mediator.should_calculate_tumor_spread = False
    tumor = _tumor(1, Point2((20.0, 20.0)))
    ctx.mediator.get_own_structures_dict = {UnitTypeId.CREEPTUMORBURROWED: [tumor]}
    plant = Point2((25.0, 20.0))
    ctx.mediator.get_next_tumor_on_path.return_value = plant
    ctx.mediator.find_nearby_creep_edge_position.return_value = None
    creep_mod.cy_has_creep = (  # type: ignore[attr-defined]
        lambda grid, pos: pos == plant
    )

    creep.spread_tumors()(ctx)

    ctx.mediator.get_next_tumor_on_path.assert_called()
    tumor.assert_called()


def test_queen_does_not_cast_tumor_off_creep() -> None:
    """Regression: casting off creep loops the game's out-of-range error."""
    import bot.routines.creep as creep_mod

    creep_mod._QUEEN_TUMOR_STICKY.clear()
    ctx = _ctx(townhall_count=2)
    queen = _queen(3, Point2((25.0, 20.0)))
    queen.order_target = None
    off_creep = Point2((40.0, 40.0))
    ctx.mediator.get_next_tumor_on_path.return_value = off_creep
    ctx.mediator.find_nearby_creep_edge_position.return_value = None
    creep_mod.cy_has_creep = lambda grid, pos: False  # type: ignore[attr-defined]

    acted = creep._place_tumor_toward(ctx, queen, _NAT, queen=True)

    assert acted is False
    queen.assert_not_called()
    assert 3 not in creep_mod._QUEEN_TUMOR_STICKY


def test_queen_walks_closer_instead_of_casting_from_4_tiles() -> None:
    """Cast range is 3; standing 4 tiles away must walk, not spam cast."""
    import bot.routines.creep as creep_mod

    creep_mod._QUEEN_TUMOR_STICKY.clear()
    ctx = _ctx(townhall_count=2)
    spot = Point2((30.0, 20.0))
    queen = _queen(4, Point2((26.0, 20.0)))  # 4 tiles away
    queen.order_target = None
    creep_mod._QUEEN_TUMOR_STICKY[4] = spot
    creep_mod.cy_has_creep = lambda grid, pos: True  # type: ignore[attr-defined]

    acted = creep._place_tumor_toward(ctx, queen, _NAT, queen=True)

    assert acted is True
    queen.assert_not_called()
    queen.move.assert_called_once_with(spot)


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
