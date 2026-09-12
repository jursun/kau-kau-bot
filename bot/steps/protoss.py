"""Protoss-specific macro steps.

Anything that names a Protoss-only structure or unit belongs here (not in
`steps/common.py`), mirroring how `steps/zerg.py` owns queens and hatcheries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ares.behaviors.macro import BuildStructure
from cython_extensions import cy_towards
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.builds.definition import _always
from bot.core.types import Gate, MacroStep

if TYPE_CHECKING:
    from bot.core.context import BotContext


def _natural_choke_bias(ctx: "BotContext") -> Point2:
    """A point on the enemy-facing side of our natural.

    Used as `closest_to` so nat Gateway placements pick choke-side formation
    slots. Prefer ares' PvZ nat gatekeeper tile when map data has one; otherwise
    lean from the nat toward the enemy spawn.

    Do **not** pass `wall=True` here: ares only precomputes Protoss wall slots
    for the main ramp (`_calculate_protoss_main_ramp_placements`), so
    `wall=True` at the nat either falls back to non-wall nat spots or steals
    the main wall via `find_alternative`.
    """
    nat = ctx.mediator.get_own_nat
    gatekeeper = ctx.mediator.get_pvz_nat_gatekeeping_pos
    if gatekeeper is not None:
        return gatekeeper
    enemy = ctx.bot.enemy_start_locations[0]
    return Point2(cy_towards(nat, enemy, 10.0))


def gateways(
    count: int,
    gate: Gate = _always,
    wall_natural: int = 0,
) -> MacroStep:
    """Keep `count` Gateways + Warp Gates combined.

    `BuildStructure` only counts the type you ask for. Once Warp Gate
    finishes, idle Gateways morph to Warp Gates and drop out of that count,
    which would otherwise make ares keep laying Gateways forever. Subtract
    existing Warp Gates from `to_count` so the total production buildings
    stop at `count`.

    When `wall_natural` > 0, the next `wall_natural` Gateways after the
    opening one are placed at the natural, biased toward the choke (gatekeeper
    tile or toward-enemy) with `find_alternative=False` so they stay at the
    nat and do not steal main-ramp wall slots. Remaining Gateways go in the
    main / production base as before. Formation spacing leaves a unit exit.
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

        # Opening Gateway is usually in main (YAML `@ ramp`). While total is
        # still within 1 + wall_natural, place at the natural choke — not via
        # ares `wall=True` (main-ramp-only).
        use_nat_wall = wall_natural > 0 and total < 1 + wall_natural
        if use_nat_wall:
            nat = ctx.mediator.get_own_nat
            return BuildStructure(
                base_location=nat,
                structure_id=UnitTypeId.GATEWAY,
                to_count=max(0, count - warpgates),
                wall=False,
                production=True,
                closest_to=_natural_choke_bias(ctx),
                find_alternative=False,
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
