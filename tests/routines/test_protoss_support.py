"""Regression tests for pure decision logic in `routines.protoss_support`:
the warp-in wave-batching thresholds `warp_wave_ready` / `warp_wave_imminent`
(see `steps.common._chargelot_spawn` and `escort_warp_prism` for the two
call sites this coordinates), and the Prism drop-harass state machine's
pure/near-pure pieces (`_drop_maybe_start`, `_pick_muster_squad`,
`_drop_pick_point`, `_drop_threats_near`).

Runs under pytest, or standalone with no test dependency:

    python -m tests.routines.test_protoss_support
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from ares.behaviors.combat import CombatManeuver
from ares.behaviors.combat.individual import DropCargo, MoveToSafeTarget
from ares.consts import UnitRole
from cython_extensions import cy_distance_to
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.core.context import BotContext
from bot.core.state import RunState
from bot.routines import protoss_support as ps


class _Units(list):
    """Stands in for python-sc2's `Units`: `.ready` (itself) and `.amount`."""

    @property
    def ready(self) -> "_Units":
        return self

    @property
    def amount(self) -> int:
        return len(self)


def _gate(*, ready_to_warp: bool) -> MagicMock:
    gate = MagicMock()
    ability = MagicMock()
    ability.name = "TRAINWARP_ZEALOT" if ready_to_warp else "EFFECT_CHRONOBOOST"
    gate.abilities = [ability]
    return gate


def _ctx(*gates: MagicMock) -> BotContext:
    bot = MagicMock()
    bot.structures.return_value = _Units(gates)
    return BotContext(bot=bot, build=MagicMock(), state=RunState())


def test_wave_not_ready_with_no_warpgates() -> None:
    assert ps.warp_wave_ready(_ctx()) is False


def test_wave_not_ready_below_the_minimum() -> None:
    # Fixed wall of 8 Gates: need ceil(0.75*8)=6. 5 idle is a real shortfall
    # (some still on cooldown), not "we don't have that many Gates yet".
    total = 8
    need = ps.warp_wave_threshold(total)
    idle = [_gate(ready_to_warp=True) for _ in range(need - 1)]
    on_cooldown = [_gate(ready_to_warp=False) for _ in range(total - (need - 1))]
    assert ps.warp_wave_ready(_ctx(*idle, *on_cooldown)) is False


def test_wave_ready_once_the_minimum_is_idle_at_once() -> None:
    total = 8
    need = ps.warp_wave_threshold(total)
    idle = [_gate(ready_to_warp=True) for _ in range(need)]
    on_cooldown = [_gate(ready_to_warp=False) for _ in range(total - need)]
    assert ps.warp_wave_ready(_ctx(*idle, *on_cooldown)) is True


def test_gates_still_on_cooldown_do_not_count_toward_the_wave() -> None:
    total = 8
    need = ps.warp_wave_threshold(total)
    idle = [_gate(ready_to_warp=True) for _ in range(need - 1)]
    on_cooldown = [_gate(ready_to_warp=False) for _ in range(total - (need - 1))]
    assert ps.warp_wave_ready(_ctx(*idle, *on_cooldown)) is False


def test_threshold_scales_with_gate_count() -> None:
    """ceil(0.75 * n): 8->6, 4->3, 1->1."""
    assert ps.warp_wave_threshold(8) == 6
    assert ps.warp_wave_threshold(4) == 3
    assert ps.warp_wave_threshold(1) == 1
    assert ps.warp_wave_threshold(0) == 0


def test_threshold_never_exceeds_total_gates() -> None:
    """Early on we may only have 1-3 Gates - waiting for a larger absolute
    count would stall forever. ceil(0.75*n) is always <= n."""
    for n in range(1, 9):
        gates = [_gate(ready_to_warp=True) for _ in range(n)]
        assert ps.warp_wave_ready(_ctx(*gates)) is True
        assert ps.warp_wave_threshold(n) <= n


def test_wave_imminent_fires_before_wave_ready_by_the_prephase_margin() -> None:
    total = 8
    need = ps.warp_wave_threshold(total)
    idle_count = need - ps.WARP_WAVE_PREPHASE_MARGIN
    idle = [_gate(ready_to_warp=True) for _ in range(idle_count)]
    on_cooldown = [_gate(ready_to_warp=False) for _ in range(total - idle_count)]
    ctx = _ctx(*idle, *on_cooldown)

    assert ps.warp_wave_ready(ctx) is False
    assert ps.warp_wave_imminent(ctx) is True


# --- Prism drop-harass -------------------------------------------------


def _drop_ctx(*, muster_committed_at: float | None = None) -> BotContext:
    bot = MagicMock()
    bot.time = 300.0
    ctx = BotContext(bot=bot, build=MagicMock(), state=RunState())
    ctx.state.chargelot_muster_committed_at = muster_committed_at
    return ctx


def _attacker(tag: int, x: float) -> MagicMock:
    unit = MagicMock()
    unit.tag = tag
    unit.position = Point2((x, 0.0))
    return unit


def _staging_at_origin():
    return patch.object(ps, "chargelot_staging", return_value=Point2((0.0, 0.0)))


def test_pick_muster_squad_returns_the_closest_units_to_staging() -> None:
    ctx = _drop_ctx()
    attackers = [_attacker(i, float(i)) for i in range(6)]
    ctx.units_in_role = MagicMock(return_value=attackers)

    with _staging_at_origin():
        squad = ps._pick_muster_squad(ctx)

    assert [u.tag for u in squad] == [0, 1, 2, 3], "closest 4, nearest first"


def test_pick_muster_squad_ignores_units_far_from_staging() -> None:
    ctx = _drop_ctx()
    ctx.units_in_role = MagicMock(return_value=[_attacker(1, 999.0)])

    with _staging_at_origin():
        squad = ps._pick_muster_squad(ctx)

    assert squad == []


def test_drop_maybe_start_does_nothing_before_the_muster_commits() -> None:
    ctx = _drop_ctx()
    ps._drop_maybe_start(ctx)
    assert ctx.state.prism_drop_phase is None


def test_drop_maybe_start_never_restarts_once_already_decided() -> None:
    ctx = _drop_ctx(muster_committed_at=300.0)
    ctx.state.prism_drop_phase = "done"
    ps._drop_maybe_start(ctx)
    assert ctx.state.prism_drop_phase == "done", "a one-shot maneuver never re-enters"


def test_drop_maybe_start_peels_the_squad_off_the_muster_on_commit() -> None:
    ctx = _drop_ctx(muster_committed_at=300.0)
    attackers = [_attacker(i, float(i)) for i in range(6)]
    ctx.units_in_role = MagicMock(return_value=attackers)

    with _staging_at_origin():
        ps._drop_maybe_start(ctx)

    assert ctx.state.prism_drop_squad_tags == {0, 1, 2, 3}
    assert ctx.state.prism_drop_phase == "loading"
    assert ctx.state.prism_drop_phase_entered_at == 300.0
    for unit in attackers[:4]:
        unit.hold_position.assert_called_once()
    ctx.mediator.assign_role.assert_any_call(tag=0, role=UnitRole.DROP_UNITS_TO_LOAD)
    ctx.mediator.assign_role.assert_any_call(tag=3, role=UnitRole.DROP_UNITS_TO_LOAD)


def test_drop_maybe_start_keeps_loading_when_muster_is_short() -> None:
    """Do not skip the drop - keep recruiting until 4 are aboard."""
    ctx = _drop_ctx(muster_committed_at=300.0)
    ctx.units_in_role = MagicMock(return_value=[_attacker(1, 0.0)])

    with _staging_at_origin():
        ps._drop_maybe_start(ctx)

    assert ctx.state.prism_drop_squad_tags == {1}
    assert ctx.state.prism_drop_phase == "loading"


def test_prism_passenger_count_uses_passengers_not_cargo_supply() -> None:
    prism = MagicMock()
    prism.passengers = [MagicMock(), MagicMock(), MagicMock(), MagicMock()]
    prism.cargo_used = 8  # 4 zealots × 2 supply
    assert ps._prism_passenger_count(prism) == 4

    prism.passengers = [MagicMock(), MagicMock()]
    prism.cargo_used = 4  # only 2 units - old bug treated this as "full"
    assert ps._prism_passenger_count(prism) == 2


def test_drop_issue_unload_fires_ability_and_ares_unload() -> None:
    ctx = _drop_ctx()
    prism = MagicMock()
    prism.abilities = {AbilityId.UNLOADALLAT_WARPPRISM}
    prism.position = Point2((1.0, 2.0))
    prism.tag = 42

    ps._drop_issue_unload(ctx, prism)

    prism.assert_called_once_with(
        AbilityId.UNLOADALLAT_WARPPRISM, prism.position
    )
    ctx.bot.do_unload_container.assert_called_once_with(42)


def test_drop_force_unload_moves_toward_main_while_cargo_remains() -> None:
    ctx = _drop_ctx()
    ctx.bot.enemy_start_locations = [Point2((100.0, 100.0))]
    prism = MagicMock()
    prism.type_id = UnitTypeId.WARPPRISM
    prism.cargo_used = 4
    prism.abilities = {AbilityId.UNLOADALLAT_WARPPRISM}
    prism.position = Point2((90.0, 90.0))
    prism.tag = 7
    maneuver = CombatManeuver()

    ps._drop_force_unload(ctx, prism, grid=MagicMock(), maneuver=maneuver, reason="test")

    assert ctx.state.prism_drop_phase == "aborting"
    assert any(isinstance(b, DropCargo) for b in maneuver.micros)
    assert any(isinstance(b, MoveToSafeTarget) for b in maneuver.micros)
    move = next(b for b in maneuver.micros if isinstance(b, MoveToSafeTarget))
    assert move.target == Point2((100.0, 100.0))
    assert move.sense_danger is False


def test_drop_depart_ready_waits_for_kite_window_then_engage_delay() -> None:
    """Army still holds staging for the kite window after commit - Prism
    must not fly during that, then waits the engage beat after."""
    ctx = _drop_ctx(muster_committed_at=300.0)
    kite = ps.CHARGELOT_KITE_WINDOW_S
    engage = ps.PRISM_DROP_DEPART_DELAY_S

    ctx.bot.time = 300.0 + kite - 0.1
    assert ps._drop_depart_ready(ctx) is False, "still inside kite window"

    ctx.bot.time = 300.0 + kite
    assert ps._drop_depart_ready(ctx) is False, "army just leaving; engage delay"

    ctx.bot.time = 300.0 + kite + engage - 0.1
    assert ps._drop_depart_ready(ctx) is False

    ctx.bot.time = 300.0 + kite + engage
    assert ps._drop_depart_ready(ctx) is True


def _height_ctx(main_height: int, heights: dict) -> BotContext:
    """`heights` maps a candidate Point2 (by rounded (x, y)) to a terrain
    height, for points other than `main`."""
    bot = MagicMock()
    bot.enemy_start_locations = [Point2((100.0, 100.0))]

    def _get_height(point):
        key = (round(point.x, 1), round(point.y, 1))
        if key == (100.0, 100.0):
            return main_height
        return heights.get(key, main_height)

    bot.get_terrain_height.side_effect = _get_height
    ctx = BotContext(bot=bot, build=MagicMock(), state=RunState())
    ctx.mediator.get_enemy_nat = Point2((100.0, 130.0))  # straight south
    return ctx


def _plateau_ctx(*, cliff_y: float = 107.0, half_width: float = 16.0) -> BotContext:
    """Rectangular high ground south of main with a east-west cliff lip.

    Main (100, 100), nat (100, 130). Anything with y >= cliff_y is low
    ground (ramp/nat side); |x-100| > half_width is also low.
    """
    bot = MagicMock()
    bot.enemy_start_locations = [Point2((100.0, 100.0))]

    def _get_height(point):
        if point.y >= cliff_y:
            return 5
        if abs(point.x - 100.0) > half_width:
            return 5
        return 10

    bot.get_terrain_height.side_effect = _get_height
    ctx = BotContext(bot=bot, build=MagicMock(), state=RunState())
    ctx.mediator.get_enemy_nat = Point2((100.0, 130.0))
    return ctx


def test_drop_on_enemy_highground_requires_plateau_inside_main() -> None:
    # Outside main radius even if same height as main (default heights).
    far = _height_ctx(main_height=10, heights={})
    assert ps._drop_on_enemy_highground(far, Point2((100.0, 125.0))) is False

    # Inside radius but low ground.
    low = _height_ctx(main_height=10, heights={(100.0, 105.0): 4})
    assert ps._drop_on_enemy_highground(low, Point2((100.0, 105.0))) is False

    # On the plateau inside the base.
    high = _height_ctx(main_height=10, heights={})
    assert ps._drop_on_enemy_highground(high, Point2((100.0, 105.0))) is True
    assert ps._drop_on_enemy_highground(high, Point2((100.0, 100.0))) is True


def test_drop_ramp_edge_stops_at_the_lip_toward_the_natural() -> None:
    ctx = _plateau_ctx(cliff_y=107.0)
    ramp = ps._drop_ramp_edge(
        ctx, Point2((100.0, 100.0)), Point2((100.0, 130.0)), height=10
    )
    assert round(ramp.x, 1) == 100.0
    assert round(ramp.y, 1) == 106.0


def test_drop_pick_point_offsets_along_the_cliff_away_from_the_ramp() -> None:
    ctx = _plateau_ctx(cliff_y=107.0)
    ramp = ps._drop_ramp_edge(
        ctx, Point2((100.0, 100.0)), Point2((100.0, 130.0)), height=10
    )

    point = ps._drop_pick_point(ctx)

    assert round(point.y, 0) == 106.0, "still on the south lip"
    assert abs(point.x - 100.0) >= ps.PRISM_DROP_RAMP_CLEARANCE - 0.5, (
        "laterally clear of the ramp top"
    )
    assert cy_distance_to(point, ramp) >= ps.PRISM_DROP_RAMP_CLEARANCE - 0.5


def test_drop_pick_point_falls_back_to_ramp_when_plateau_is_narrow() -> None:
    # Too narrow for lateral clearance - keep the ramp lip rather than
    # walking off the plateau.
    ctx = _plateau_ctx(cliff_y=107.0, half_width=3.0)

    point = ps._drop_pick_point(ctx)

    assert round(point.x, 1) == 100.0
    assert round(point.y, 1) == 106.0


def test_drop_pick_point_falls_back_to_main_when_edge_is_immediate() -> None:
    ctx = _height_ctx(main_height=10, heights={(100.0, 102.0): 4})

    point = ps._drop_pick_point(ctx)

    assert (round(point.x, 1), round(point.y, 1)) == (100.0, 100.0)


def _threat_ctx(ground: list, air: list) -> BotContext:
    bot = MagicMock()
    bot.mediator = MagicMock()
    ctx = BotContext(bot=bot, build=MagicMock(), state=RunState())
    ctx.mediator.get_units_in_range.side_effect = [[ground], [air]]
    return ctx


def _threat(tag: int, *, can_attack_air: bool) -> MagicMock:
    unit = MagicMock()
    unit.tag = tag
    unit.can_attack_air = can_attack_air
    return unit


def test_drop_threats_near_counts_only_anti_air_capable_units() -> None:
    ctx = _threat_ctx(
        ground=[_threat(1, can_attack_air=True), _threat(2, can_attack_air=False)],
        air=[_threat(3, can_attack_air=True)],
    )

    assert ps._drop_threats_near(ctx, Point2((0.0, 0.0))) == 2


def test_drop_threats_near_dedupes_by_tag() -> None:
    same = _threat(1, can_attack_air=True)
    ctx = _threat_ctx(ground=[same], air=[same])

    assert ps._drop_threats_near(ctx, Point2((0.0, 0.0))) == 1


def test_drop_threats_near_zero_when_nothing_can_hit_air() -> None:
    ctx = _threat_ctx(
        ground=[_threat(1, can_attack_air=False)],
        air=[],
    )

    assert ps._drop_threats_near(ctx, Point2((0.0, 0.0))) == 0


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
