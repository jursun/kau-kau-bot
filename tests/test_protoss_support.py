"""Regression tests for pure decision logic in `routines.protoss_support`:
the warp-in wave-batching thresholds `warp_wave_ready` / `warp_wave_imminent`
(see `steps.common._chargelot_spawn` and `escort_warp_prism` for the two
call sites this coordinates).

Runs under pytest, or standalone with no test dependency:

    python -m tests.test_protoss_support
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

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
