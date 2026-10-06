"""Periodic army-composition log line."""

from __future__ import annotations

from unittest.mock import MagicMock

from sc2.ids.unit_typeid import UnitTypeId

from bot.builds.zerg import macro_zerg as mz
from bot.core.context import BotContext
from bot.core.state import RunState


def _ctx(time: float = 100.0) -> BotContext:
    bot = MagicMock()
    bot.time = time
    bot.supply_army = 80.0
    bot.supply_workers = 70.0
    bot.minerals = 1500.0
    bot.vespene = 400.0
    bot.calculate_supply_cost.return_value = 2.0
    bot.units = MagicMock(
        side_effect=lambda t: {
            UnitTypeId.ROACH: [MagicMock()] * 12,
            UnitTypeId.HYDRALISK: [MagicMock()] * 5,
        }.get(t, [])
    )
    ctx = BotContext(bot=bot, build=MagicMock(), state=RunState())
    ctx.mediator.get_cached_enemy_army = []
    ctx.log = MagicMock()
    return ctx


def test_logs_our_army_by_type_and_the_bank() -> None:
    ctx = _ctx()

    mz._log_army_composition(ctx)

    message = ctx.log.call_args.args[0]
    assert message.startswith("ARMY ")
    assert "'ROACH': 12" in message and "'HYDRALISK': 5" in message
    assert "'ZERGLING'" not in message  # types we have none of are left out
    assert "army_supply=80" in message and "bank=1500/400" in message
    assert "enemy: unseen" in message


def test_logs_at_most_once_per_interval() -> None:
    ctx = _ctx()
    mz._log_army_composition(ctx)

    ctx.bot.time = 100.0 + mz.ARMY_LOG_INTERVAL_S - 1.0
    mz._log_army_composition(ctx)
    assert ctx.log.call_count == 1

    ctx.bot.time = 100.0 + mz.ARMY_LOG_INTERVAL_S + 1.0
    mz._log_army_composition(ctx)
    assert ctx.log.call_count == 2


def test_includes_the_enemy_composition_when_scouted() -> None:
    ctx = _ctx()
    immortal, zealot = MagicMock(), MagicMock()
    immortal.type_id, zealot.type_id = UnitTypeId.IMMORTAL, UnitTypeId.ZEALOT
    ctx.mediator.get_cached_enemy_army = [immortal, immortal, zealot]

    mz._log_army_composition(ctx)

    message = ctx.log.call_args.args[0]
    assert "IMMORTAL" in message and "ZEALOT" in message
