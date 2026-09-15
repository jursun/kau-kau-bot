"""Regression tests for pure decision logic in `routines.protoss_support`:
the warp-in wave-batching thresholds `warp_wave_ready` / `warp_wave_imminent`
(see `steps.common._chargelot_spawn` and `escort_warp_prism` for the two
call sites this coordinates), and the Prism drop-harass state machine's
pure/near-pure pieces (`_drop_maybe_start`, `claim_drop_squad_unit`,
`_drop_pick_point`, `_drop_threats_near`).

Runs under pytest, or standalone with no test dependency:

    python -m tests.test_protoss_support
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from ares.consts import UnitRole
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


def _drop_unit(type_id: UnitTypeId = UnitTypeId.ZEALOT, tag: int = 1) -> MagicMock:
    unit = MagicMock()
    unit.type_id = type_id
    unit.tag = tag
    unit.orders = []
    return unit


def test_drop_maybe_start_does_nothing_before_the_army_exists() -> None:
    ctx = _drop_ctx()
    ps._drop_maybe_start(ctx, army_exists=False)
    assert ctx.state.prism_drop_phase is None


def test_drop_maybe_start_does_nothing_once_muster_has_committed() -> None:
    ctx = _drop_ctx(muster_committed_at=280.0)
    ps._drop_maybe_start(ctx, army_exists=True)
    assert ctx.state.prism_drop_phase is None, "unsafe once the main wave committed"


def test_drop_maybe_start_begins_waiting_for_squad_when_eligible() -> None:
    ctx = _drop_ctx()
    ps._drop_maybe_start(ctx, army_exists=True)
    assert ctx.state.prism_drop_phase == "waiting_for_squad"


def test_drop_maybe_start_never_restarts_once_already_decided() -> None:
    ctx = _drop_ctx()
    ctx.state.prism_drop_phase = "done"
    ps._drop_maybe_start(ctx, army_exists=True)
    assert ctx.state.prism_drop_phase == "done", "a one-shot maneuver never re-enters"


def test_claim_drop_squad_unit_ignores_wrong_phase() -> None:
    ctx = _drop_ctx()
    ctx.state.prism_drop_phase = None
    ps.claim_drop_squad_unit(ctx, _drop_unit())
    assert ctx.state.prism_drop_squad_tags == set()


def test_claim_drop_squad_unit_ignores_non_army_types() -> None:
    ctx = _drop_ctx()
    ctx.state.prism_drop_phase = "waiting_for_squad"
    ps.claim_drop_squad_unit(ctx, _drop_unit(UnitTypeId.PROBE))
    assert ctx.state.prism_drop_squad_tags == set()


def test_claim_drop_squad_unit_holds_and_claims_zealots_and_stalkers() -> None:
    ctx = _drop_ctx()
    ctx.state.prism_drop_phase = "waiting_for_squad"
    unit = _drop_unit(UnitTypeId.STALKER, tag=7)

    ps.claim_drop_squad_unit(ctx, unit)

    assert ctx.state.prism_drop_squad_tags == {7}
    unit.hold_position.assert_called_once()
    ctx.mediator.assign_role.assert_called_once_with(
        tag=7, role=UnitRole.DROP_UNITS_TO_LOAD
    )
    assert ctx.state.prism_drop_phase == "waiting_for_squad", "not full yet"


def test_claim_drop_squad_unit_transitions_to_loading_once_full() -> None:
    ctx = _drop_ctx()
    ctx.state.prism_drop_phase = "waiting_for_squad"

    for tag in range(ps.PRISM_DROP_SQUAD_SIZE):
        ps.claim_drop_squad_unit(ctx, _drop_unit(tag=tag))

    assert len(ctx.state.prism_drop_squad_tags) == ps.PRISM_DROP_SQUAD_SIZE
    assert ctx.state.prism_drop_phase == "loading"
    assert ctx.state.prism_drop_phase_entered_at == 300.0


def test_claim_drop_squad_unit_stops_once_full() -> None:
    ctx = _drop_ctx()
    ctx.state.prism_drop_phase = "waiting_for_squad"
    for tag in range(ps.PRISM_DROP_SQUAD_SIZE):
        ps.claim_drop_squad_unit(ctx, _drop_unit(tag=tag))

    # Phase flipped to "loading", so a 5th fresh unit must not be claimed.
    ps.claim_drop_squad_unit(ctx, _drop_unit(tag=99))

    assert 99 not in ctx.state.prism_drop_squad_tags


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
