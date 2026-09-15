"""Regression tests for the warp-in wave-hold gate in
`steps.common._chargelot_spawn` (see `routines.protoss_support.
warp_wave_ready`): the mass-production flood paths should batch into waves
of at least `WARP_WAVE_MIN`, but the Warp Prism purchase (Robotics-trained,
not warped in), the opening's time-critical Stalker recovery, and anything
before Warp Gate research must never be held back waiting for a wave.

Runs under pytest, or standalone with no test dependency:

    python -m tests.test_chargelot_spawn
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from ares.behaviors.macro import SpawnController
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from bot.core.context import BotContext
from bot.core.state import RunState
from bot.routines.protoss_support import WARP_WAVE_MIN
from bot.steps import common as c


class _Units(list):
    """Stands in for python-sc2's `Units`: `.ready` (itself) and `.amount`."""

    @property
    def ready(self) -> "_Units":
        return self

    @property
    def amount(self) -> int:
        return len(self)


def _warpgate(*, ready_to_warp: bool = True) -> MagicMock:
    gate = MagicMock()
    ability = MagicMock()
    ability.name = "TRAINWARP_ZEALOT" if ready_to_warp else "EFFECT_CHRONOBOOST"
    gate.abilities = [ability]
    return gate


def _flood_ctx(*, ready_gates: int, warpgate_research: bool = True) -> BotContext:
    """Prism already up and phasing, Observer already trained - selects
    `_chargelot_spawn`'s "post_prism_flood" path (Zealot/Stalker only)."""
    bot = MagicMock()
    bot.race = MagicMock()  # != Race.Protoss short-circuits _warp_spawn_target
    bot.time = 400.0
    bot.minerals = 1000
    bot.vespene = 500
    bot.state.upgrades = {UpgradeId.WARPGATERESEARCH} if warpgate_research else set()

    presence = {
        UnitTypeId.WARPPRISM: _Units([MagicMock()]),
        UnitTypeId.WARPPRISMPHASING: _Units([MagicMock()]),
        UnitTypeId.OBSERVER: _Units([MagicMock()]),
        UnitTypeId.STALKER: _Units([MagicMock() for _ in range(6)]),
    }
    bot.units.side_effect = lambda t: presence.get(t, _Units())
    bot.already_pending.side_effect = lambda t: 0

    # 2 extra Gates always on cooldown, so total Gates comfortably exceeds
    # WARP_WAVE_MIN and the threshold never self-limits down in these tests
    # (see test_protoss_support.py for that case specifically).
    idle = [_warpgate(ready_to_warp=True) for _ in range(ready_gates)]
    padding = [_warpgate(ready_to_warp=False) for _ in range(2)]
    gates = _Units([*idle, *padding])

    def _structures(t):
        if t == UnitTypeId.ROBOTICSFACILITY:
            return _Units([MagicMock()])
        if t == UnitTypeId.WARPGATE:
            return gates
        return _Units()

    bot.structures.side_effect = _structures

    return BotContext(bot=bot, build=MagicMock(), state=RunState())


def _opening_stalker2_ctx() -> BotContext:
    """Opening finished but the scripted Stalker died, so `_chargelot_spawn`
    falls back to `opening_stalker2` to recover it before Robo (gated on 2
    Stalkers) can start - this must fire immediately, not wait for other
    Gates to sync into a wave. Both Gates are on cooldown, which would hold
    a flood path back if it were wave-gated."""
    bot = MagicMock()
    bot.race = MagicMock()
    bot.time = 250.0
    bot.minerals = 500
    bot.vespene = 200
    bot.state.upgrades = {UpgradeId.WARPGATERESEARCH}
    bot.build_order_runner.build_completed = True

    presence = {UnitTypeId.STALKER: _Units([MagicMock()])}
    bot.units.side_effect = lambda t: presence.get(t, _Units())
    bot.already_pending.side_effect = lambda t: 0

    gates = _Units([_warpgate(ready_to_warp=False) for _ in range(2)])

    def _structures(t):
        if t == UnitTypeId.WARPGATE:
            return gates
        return _Units()

    bot.structures.side_effect = _structures

    return BotContext(bot=bot, build=MagicMock(), state=RunState())


def _prism_purchase_ctx(*, ready_gates: int) -> BotContext:
    """No Prism yet, Robo up, enough banked to afford one - selects the
    "prism" path (Warp Prism only, trained from Robo, not warped in)."""
    bot = MagicMock()
    bot.race = MagicMock()
    bot.time = 400.0
    bot.minerals = 1000
    bot.vespene = 500
    bot.state.upgrades = {UpgradeId.WARPGATERESEARCH}

    bot.units.side_effect = lambda t: _Units()
    bot.already_pending.side_effect = lambda t: 0

    gates = _Units([_warpgate() for _ in range(ready_gates)])

    def _structures(t):
        if t == UnitTypeId.ROBOTICSFACILITY:
            return _Units([MagicMock()])
        if t == UnitTypeId.WARPGATE:
            return gates
        return _Units()

    bot.structures.side_effect = _structures

    return BotContext(bot=bot, build=MagicMock(), state=RunState())


def test_flood_holds_when_fewer_than_the_wave_minimum_are_idle() -> None:
    ctx = _flood_ctx(ready_gates=WARP_WAVE_MIN - 1)

    assert c._chargelot_spawn(ctx) is None


def test_flood_releases_once_the_wave_minimum_is_idle_at_once() -> None:
    ctx = _flood_ctx(ready_gates=WARP_WAVE_MIN)

    result = c._chargelot_spawn(ctx)

    assert isinstance(result, SpawnController)
    assert set(result.army_composition_dict) == {
        UnitTypeId.STALKER,
        UnitTypeId.ZEALOT,
    }


def test_flood_is_not_held_before_warp_gate_research() -> None:
    """No warp-in mechanic exists yet - regular Gateway queueing has no
    wave to batch, so this must not stall waiting for Gates that can't
    exist yet."""
    ctx = _flood_ctx(ready_gates=0, warpgate_research=False)

    assert isinstance(c._chargelot_spawn(ctx), SpawnController)


def test_opening_stalker_recovery_is_never_held_for_a_warp_wave() -> None:
    ctx = _opening_stalker2_ctx()

    result = c._chargelot_spawn(ctx)

    assert isinstance(result, SpawnController)
    assert set(result.army_composition_dict) == {UnitTypeId.STALKER}


def test_prism_purchase_is_never_held_for_a_warp_wave() -> None:
    """The Warp Prism itself trains from Robo, not a Warp Gate - holding it
    back waiting for an unrelated Zealot wave would delay a one-time,
    time-critical purchase for no reason."""
    ctx = _prism_purchase_ctx(ready_gates=0)

    result = c._chargelot_spawn(ctx)

    assert isinstance(result, SpawnController)
    assert set(result.army_composition_dict) == {UnitTypeId.WARPPRISM}


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
