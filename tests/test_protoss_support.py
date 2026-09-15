"""Regression tests for pure decision logic in `routines.protoss_support`:
the warp-in wave-batching thresholds `warp_wave_ready` / `warp_wave_imminent`
(see `steps.common._chargelot_spawn` and `escort_warp_prism` for the two
call sites this coordinates), and the Prism drop-harass state machine's
pure/near-pure pieces (`_drop_maybe_start`, `_pick_muster_squad`,
`_drop_pick_point`, `_drop_threats_near`).

Runs under pytest, or standalone with no test dependency:

    python -m tests.test_protoss_support
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from ares.consts import UnitRole
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
    # Total exceeds WARP_WAVE_MIN so the threshold does NOT self-limit down -
    # a real shortfall (some Gates still on cooldown), not just "we don't
    # have that many Gates yet" (see the self-limiting test below).
    idle = [_gate(ready_to_warp=True) for _ in range(ps.WARP_WAVE_MIN - 1)]
    on_cooldown = [_gate(ready_to_warp=False) for _ in range(2)]
    assert ps.warp_wave_ready(_ctx(*idle, *on_cooldown)) is False


def test_wave_ready_once_the_minimum_is_idle_at_once() -> None:
    gates = [_gate(ready_to_warp=True) for _ in range(ps.WARP_WAVE_MIN)]
    assert ps.warp_wave_ready(_ctx(*gates)) is True


def test_gates_still_on_cooldown_do_not_count_toward_the_wave() -> None:
    gates = [_gate(ready_to_warp=True) for _ in range(ps.WARP_WAVE_MIN - 1)]
    gates.append(_gate(ready_to_warp=False))
    assert ps.warp_wave_ready(_ctx(*gates)) is False


def test_threshold_self_limits_to_however_many_warpgates_exist() -> None:
    """Early on (e.g. right after Warp Gate research, wall trio still
    building) we may only have 1-3 total Gates - waiting for
    `WARP_WAVE_MIN` would stall production forever. The threshold should
    never exceed the total Gate count."""
    gates = [_gate(ready_to_warp=True) for _ in range(ps.WARP_WAVE_MIN - 2)]
    ctx = _ctx(*gates)

    assert ps.warp_wave_ready(ctx) is True


def test_wave_imminent_fires_before_wave_ready_by_the_prephase_margin() -> None:
    idle_count = ps.WARP_WAVE_MIN - ps.WARP_WAVE_PREPHASE_MARGIN
    idle = [_gate(ready_to_warp=True) for _ in range(idle_count)]
    # Pad total up to WARP_WAVE_MIN so the threshold doesn't self-limit.
    on_cooldown = [
        _gate(ready_to_warp=False) for _ in range(ps.WARP_WAVE_PREPHASE_MARGIN)
    ]
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


def test_drop_maybe_start_skips_the_maneuver_if_too_few_units_are_mustered() -> None:
    ctx = _drop_ctx(muster_committed_at=300.0)
    ctx.units_in_role = MagicMock(return_value=[_attacker(1, 0.0)])

    with _staging_at_origin():
        ps._drop_maybe_start(ctx)

    assert ctx.state.prism_drop_squad_tags == set()
    assert ctx.state.prism_drop_phase == "done", "not enough units - skip, don't wait"


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


def test_drop_pick_point_stops_at_the_edge_of_the_plateau() -> None:
    # Same height until 8 units south, then the ramp drops off.
    low_ground = {(100.0, 108.0): 5}
    ctx = _height_ctx(main_height=10, heights=low_ground)

    point = ps._drop_pick_point(ctx)

    assert round(point.y, 1) == 106.0, "last point still on the high ground"


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
