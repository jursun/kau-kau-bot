"""Unit tests for Macro Zerg's army-vs-upgrade posture helpers and UpgradeSlots.

    python -m tests.test_macro_zerg_posture
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2

from bot.behaviors.zerg.upgrade_slots import UpgradeSlots, count_pending_upgrades
from bot.builds.zerg import macro_zerg as mz
from bot.core.context import BotContext
from bot.core.state import RunState
from bot.routines.overseers import assign_overseer_roles
from bot.steps import zerg as z


def _ctx(**bot_attrs) -> BotContext:
    bot = MagicMock()
    bot.time = 300.0
    bot.supply_army = 20
    bot.supply_used = 100
    bot.larva = MagicMock()
    bot.larva.__bool__ = lambda self: True
    bot.larva.__len__ = lambda self: 3
    bot.production_location = Point2((10.0, 10.0))
    bot.structures = MagicMock(return_value=MagicMock(ready=True, amount=1))
    bot.already_pending_upgrade = MagicMock(return_value=0.0)
    bot.mediator = SimpleNamespace(get_cached_enemy_army=[])
    bot.calculate_supply_cost = MagicMock(return_value=2.0)
    for key, value in bot_attrs.items():
        setattr(bot, key, value)
    build = MagicMock()
    build.army.upgrades = (
        UpgradeId.GLIALRECONSTITUTION,
        UpgradeId.BURROW,
        UpgradeId.ZERGMISSILEWEAPONSLEVEL1,
    )
    return BotContext(bot=bot, build=build, state=RunState())


def test_upgrade_slot_target_army_behind_is_one() -> None:
    ctx = _ctx(supply_army=4, supply_used=80)
    enemy = MagicMock()
    enemy.type_id = UnitTypeId.ZEALOT
    enemy.is_flying = False
    ctx.bot.mediator = SimpleNamespace(get_cached_enemy_army=[enemy, enemy, enemy])
    assert mz._upgrade_slot_target(ctx) == 1


def test_upgrade_slot_target_tech_focus_is_two() -> None:
    ctx = _ctx(supply_army=20, supply_used=80)
    assert mz._upgrade_slot_target(ctx) == 2


def test_upgrade_slot_target_extra_bumps_by_one() -> None:
    ctx = _ctx(supply_army=20, supply_used=200)
    assert mz._upgrade_slot_target(ctx) == 3
    ctx2 = _ctx(supply_army=4, supply_used=80)
    ctx2.bot.larva = []
    enemy = MagicMock()
    enemy.type_id = UnitTypeId.ZEALOT
    enemy.is_flying = False
    ctx2.bot.mediator = SimpleNamespace(get_cached_enemy_army=[enemy, enemy, enemy])
    assert mz._upgrade_slot_target(ctx2) == 2


def test_desired_upgrades_appends_air_when_enemy_flies() -> None:
    ctx = _ctx()
    flyer = MagicMock()
    flyer.type_id = UnitTypeId.MUTALISK
    flyer.is_flying = True
    ctx.bot.mediator = SimpleNamespace(get_cached_enemy_army=[flyer])
    ups = mz._desired_upgrades(ctx)
    assert UpgradeId.ZERGFLYERWEAPONSLEVEL1 in ups
    assert UpgradeId.ZERGFLYERARMORSLEVEL1 in ups


def test_count_pending_upgrades() -> None:
    ai = MagicMock()

    def pending(u):
        return {UpgradeId.BURROW: 0.4, UpgradeId.GLIALRECONSTITUTION: 1.0}.get(u, 0.0)

    ai.already_pending_upgrade.side_effect = pending
    assert (
        count_pending_upgrades(
            ai, [UpgradeId.BURROW, UpgradeId.GLIALRECONSTITUTION, UpgradeId.TUNNELINGCLAWS]
        )
        == 1
    )


def _army_ready_ctx(**bot_attrs) -> BotContext:
    """`_ctx()` plus enough state that `z.spawn_macro_army` (inside
    `_spawn_macro_army`) returns a real `SpawnController` (Lair/Roach
    Warren done, Roach tech-ready) - shared by the tests below, though
    `_reserve_upgrade_bank` itself no longer depends on it (it returns the
    `UpgradeSlots` behavior directly, not nested inside a combined plan)."""
    ctx = _ctx(time=310.0, **bot_attrs)

    def structures(unit_type):
        amount = 1 if unit_type in (UnitTypeId.LAIR, UnitTypeId.ROACHWARREN) else 0
        ready = MagicMock()
        ready.amount = amount
        result = MagicMock()
        result.amount = amount
        result.ready = ready
        return result

    ctx.bot.structures = structures
    ctx.bot.structure_pending = MagicMock(return_value=0)
    ctx.bot.townhalls = []
    ctx.bot.tech_requirement_progress = MagicMock(
        side_effect=lambda t: 1.0 if t == UnitTypeId.ROACH else 0.0
    )
    return ctx


def _with_enemy_army(ctx: BotContext, count: int = 3) -> None:
    enemy = MagicMock()
    enemy.type_id = UnitTypeId.ZEALOT
    enemy.is_flying = False
    ctx.bot.mediator = SimpleNamespace(get_cached_enemy_army=[enemy] * count)


def test_reserve_upgrade_bank_returns_none_before_lair_commanded() -> None:
    """Protects Lair's own gas bank - `_SEQUENCE` owns the morph itself."""
    ctx = _army_ready_ctx()
    ctx.bot.structures = MagicMock(return_value=MagicMock(amount=0, ready=MagicMock(amount=0)))
    ctx.bot.structure_pending = MagicMock(return_value=0)
    ctx.bot.townhalls = []
    assert mz._reserve_upgrade_bank(ctx) is None


def test_reserve_upgrade_bank_holds_when_not_behind() -> None:
    """Regression test for "upgrades should keep rolling": `UpgradeSlots`
    must hold the bank (`prioritize=True`) once Lair is commanded and we're
    not behind on army supply - see `_reserve_upgrade_bank`'s own docstring
    for the live-confirmed bug this fixes (Glial Reconstitution stuck in
    "shortage" for 100+ seconds because `_split_production_after_opening`'s
    own Roach spend kept winning the frame before this reservation, sitting
    lower in the old `macro_steps`, ever got a look)."""
    ctx = _army_ready_ctx()  # default mediator: no visible enemy army
    upgrades = mz._reserve_upgrade_bank(ctx)
    assert isinstance(upgrades, UpgradeSlots)
    assert upgrades.prioritize is True


def test_reserve_upgrade_bank_does_not_hold_when_behind() -> None:
    """While actually behind on army supply, this must not block army
    spend below it - a deliberate call to favor defense over teching. It
    still returns an `UpgradeSlots` (an already-affordable upgrade should
    still start outright - `UpgradeController` checks affordability before
    `prioritize`), just without the hold."""
    ctx = _army_ready_ctx(supply_army=2)
    _with_enemy_army(ctx)
    upgrades = mz._reserve_upgrade_bank(ctx)
    assert isinstance(upgrades, UpgradeSlots)
    assert upgrades.prioritize is False


def test_spawn_macro_army_returns_a_spawn_controller() -> None:
    ctx = _army_ready_ctx()
    army = mz._spawn_macro_army(ctx)
    assert army.__class__.__name__ == "SpawnController"


def test_overseers_step_fixed_count() -> None:
    ctx = _ctx()
    behavior = z.overseers(to_count=3, gate=lambda _c: True)(ctx)
    assert behavior is not None
    assert behavior.to_count == 3


def test_assign_overseer_roles_fills_three_slots() -> None:
    ctx = _ctx()
    units = []
    for tag in (1, 2, 3):
        u = MagicMock()
        u.tag = tag
        units.append(u)
    ctx.bot.units = MagicMock(return_value=units)
    assign_overseer_roles(ctx)
    assert ctx.state.overseer_home_tag == 1
    assert ctx.state.overseer_army_tag == 2
    assert ctx.state.overseer_scout_tag == 3


def test_assign_overseer_roles_clears_dead() -> None:
    ctx = _ctx()
    ctx.state.overseer_home_tag = 99
    ctx.state.overseer_army_tag = 1
    live = MagicMock()
    live.tag = 1
    ctx.bot.units = MagicMock(return_value=[live])
    assign_overseer_roles(ctx)
    assert ctx.state.overseer_home_tag is None
    assert ctx.state.overseer_army_tag == 1


def test_opponent_base_targets_enemy_side_and_visible_halls() -> None:
    from bot.routines.overseers import opponent_base_targets

    enemy_main = Point2((100.0, 100.0))
    enemy_nat = Point2((80.0, 100.0))
    our_main = Point2((10.0, 10.0))
    our_side = Point2((20.0, 10.0))
    ctx = _ctx()
    ctx.bot.enemy_start_locations = [enemy_main]
    ctx.bot.start_location = our_main
    ctx.bot.mediator = SimpleNamespace(
        get_enemy_expansions=[(enemy_nat, 1.0), (our_side, 50.0)]
    )
    hall = MagicMock()
    hall.position = Point2((60.0, 100.0))  # enemy third-ish
    structures = MagicMock()
    structures.of_type = MagicMock(return_value=[hall])
    ctx.bot.enemy_structures = structures

    bases = opponent_base_targets(ctx)
    assert enemy_main in bases
    assert enemy_nat in bases
    assert hall.position in bases
    assert our_side not in bases


def test_spread_changelings_sticky_and_no_move_when_arrived() -> None:
    from bot.routines.overseers import _spread_changelings

    enemy_main = Point2((100.0, 100.0))
    enemy_nat = Point2((80.0, 100.0))
    ctx = _ctx()
    ctx.bot.enemy_start_locations = [enemy_main]
    ctx.bot.start_location = Point2((10.0, 10.0))
    ctx.bot.mediator = SimpleNamespace(
        get_enemy_expansions=[(enemy_nat, 1.0)]
    )
    structures = MagicMock()
    structures.of_type = MagicMock(return_value=[])
    ctx.bot.enemy_structures = structures

    far = MagicMock()
    far.tag = 1
    far.type_id = UnitTypeId.CHANGELING
    far.position = Point2((10.0, 10.0))
    near = MagicMock()
    near.tag = 2
    near.type_id = UnitTypeId.CHANGELINGZEALOT
    near.position = Point2((100.5, 100.0))  # already on enemy main

    # Pre-seed sticky dests so arrival/no-move is independent of assign order.
    ctx.state.changeling_destinations = {1: enemy_nat, 2: enemy_main}

    ctx.bot.units = [far, near]
    _spread_changelings(ctx)
    first = dict(ctx.state.changeling_destinations)
    ctx.bot.register_behavior.reset_mock()
    ctx.bot.units = [near, far]
    _spread_changelings(ctx)
    assert ctx.state.changeling_destinations == first
    assert first[1] == enemy_nat
    assert first[2] == enemy_main

    moves = [
        call.args[0]
        for call in ctx.bot.register_behavior.call_args_list
        if call.args and getattr(call.args[0], "target", None) is not None
    ]
    # Only the far changeling should move after the reorder pass.
    assert {m.unit.tag for m in moves} == {1}
    assert all(m.target == enemy_nat for m in moves)


def test_army_supply_gate() -> None:
    from bot.routines import gates

    ctx = _ctx(supply_army=39)
    assert not gates.army_supply_at_least(40)(ctx)
    ctx.bot.supply_army = 40
    assert gates.army_supply_at_least(40)(ctx)


def test_intel_scaled_leave_sooner_when_unseen() -> None:
    """Fog / no combat scouted → leave at UNSEEN (32), not the old fixed 40."""
    from bot.routines import gates

    ctx = _ctx(supply_army=31)
    ctx.bot.mediator = SimpleNamespace(get_cached_enemy_army=[])
    assert gates.leave_army_supply_needed(ctx) == gates.LEAVE_ARMY_SUPPLY_UNSEEN
    assert not gates.intel_scaled_army_leave()(ctx)
    ctx.bot.supply_army = 32
    assert gates.intel_scaled_army_leave()(ctx)


def test_intel_scaled_leave_holds_vs_real_army() -> None:
    """Visible enemy army raises the leave floor (margin + clamp)."""
    from bot.routines import gates

    zealots = []
    for i in range(10):
        u = MagicMock()
        u.tag = i
        u.type_id = UnitTypeId.ZEALOT
        zealots.append(u)
    ctx = _ctx(supply_army=40)
    ctx.bot.mediator = SimpleNamespace(get_cached_enemy_army=zealots)
    # 10 zealots * 2 supply = 20 enemy → need 20+12=32, still leave at 40
    ctx.bot.calculate_supply_cost = MagicMock(return_value=2.0)
    need = gates.leave_army_supply_needed(ctx)
    assert need == 32.0
    assert gates.intel_scaled_army_leave()(ctx)

    # Heavier army: 20 stalkers * 2 = 40 → need 52
    stalkers = []
    for i in range(20):
        u = MagicMock()
        u.tag = 100 + i
        u.type_id = UnitTypeId.STALKER
        stalkers.append(u)
    ctx.bot.mediator = SimpleNamespace(get_cached_enemy_army=stalkers)
    ctx.bot.supply_army = 50
    assert gates.leave_army_supply_needed(ctx) == 52.0
    assert not gates.intel_scaled_army_leave()(ctx)
    ctx.bot.supply_army = 52
    assert gates.intel_scaled_army_leave()(ctx)


def test_intel_scaled_leave_threat_bump_and_cap() -> None:
    from bot.routines import gates

    colossus = MagicMock()
    colossus.tag = 1
    colossus.type_id = UnitTypeId.COLOSSUS
    ctx = _ctx(supply_army=30)
    ctx.bot.mediator = SimpleNamespace(get_cached_enemy_army=[colossus])
    ctx.bot.calculate_supply_cost = MagicMock(return_value=6.0)
    # enemy 6 + margin 12 + colossus bump 8 = 26 → clamp min 24 → 26
    assert gates.leave_army_supply_needed(ctx) == 26.0

    # Huge army clamps at MAX
    army = []
    for i in range(40):
        u = MagicMock()
        u.tag = i
        u.type_id = UnitTypeId.STALKER
        army.append(u)
    ctx.bot.mediator = SimpleNamespace(get_cached_enemy_army=army)
    ctx.bot.calculate_supply_cost = MagicMock(return_value=2.0)
    assert gates.leave_army_supply_needed(ctx) == gates.LEAVE_ARMY_SUPPLY_MAX


def test_macro_zerg_uses_intel_scaled_leave_gate() -> None:
    from bot.routines import gates

    assert mz.BUILD.combat.wave_gate.__name__ == gates.intel_scaled_army_leave().__name__ or (
        mz.BUILD.combat.wave_gate.__code__.co_filename.endswith("gates.py")
    )
    # Same factory identity: call both on an unseen ctx.
    ctx = _ctx(supply_army=32)
    ctx.bot.mediator = SimpleNamespace(get_cached_enemy_army=[])
    assert mz.BUILD.combat.wave_gate(ctx) is True
    ctx.bot.supply_army = 31
    assert mz.BUILD.combat.wave_gate(ctx) is False


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
