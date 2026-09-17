"""Unit tests for Macro Zerg post-5:00 posture helpers and UpgradeSlots.

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


def test_post_five_orders_army_first_when_behind() -> None:
    ctx = _ctx(supply_army=2, time=310.0)
    enemy = MagicMock()
    enemy.type_id = UnitTypeId.ZEALOT
    enemy.is_flying = False
    ctx.bot.mediator = SimpleNamespace(get_cached_enemy_army=[enemy, enemy, enemy])

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
    plan = mz._post_five_army_tech(ctx)
    assert plan is not None
    assert len(plan.macros) == 2
    assert plan.macros[0].__class__.__name__ == "SpawnController"
    assert isinstance(plan.macros[1], UpgradeSlots)


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
