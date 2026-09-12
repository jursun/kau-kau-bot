"""Protoss-specific macro steps.

Anything that names a Protoss-only structure or unit belongs here (not in
`steps/common.py`), mirroring how `steps/zerg.py` owns queens and hatcheries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ares.behaviors.macro import BuildStructure
from sc2.ids.unit_typeid import UnitTypeId

from bot.builds.definition import _always
from bot.core.types import Gate, MacroStep

if TYPE_CHECKING:
    from bot.core.context import BotContext


def gateways(count: int, gate: Gate = _always) -> MacroStep:
    """Keep `count` Gateways + Warp Gates combined.

    `BuildStructure` only counts the type you ask for. Once Warp Gate
    finishes, idle Gateways morph to Warp Gates and drop out of that count,
    which would otherwise make ares keep laying Gateways forever. Subtract
    existing Warp Gates from `to_count` so the total production buildings
    stop at `count`.
    """

    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None

        ready_gates = ctx.bot.structures(UnitTypeId.GATEWAY).ready.amount
        pending_gates = ctx.bot.structure_pending(UnitTypeId.GATEWAY)
        warpgates = ctx.bot.structures(UnitTypeId.WARPGATE).amount
        total = ready_gates + pending_gates + warpgates
        if total >= count:
            return None

        ctx.log_once(
            "macro_gateways",
            f"MACRO gateways: building toward {count} "
            f"(now gates={ready_gates}+{pending_gates} warpgates={warpgates})",
        )

        return BuildStructure(
            base_location=ctx.production_location,
            structure_id=UnitTypeId.GATEWAY,
            to_count=max(0, count - warpgates),
        )

    return step


def pylon_buffer(
    min_left: int = 20,
    max_pending: int = 3,
    gate: Gate = _always,
) -> MacroStep:
    """Keep a larger supply cushion than stock `AutoSupply`.

    Eight Warp Gates can dump 16 supply in one volley; ares' AutoSupply
    threshold scales with production but still loses races mid-warp. This
    starts the next Pylon once `supply_left` drops below `min_left`, up to
    `max_pending` concurrent Pylons.
    """

    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None
        if ctx.bot.supply_cap >= 200:
            return None
        if ctx.bot.supply_left >= min_left:
            return None
        if ctx.bot.structure_pending(UnitTypeId.PYLON) >= max_pending:
            return None
        return BuildStructure(
            base_location=ctx.production_location,
            structure_id=UnitTypeId.PYLON,
        )

    return step
