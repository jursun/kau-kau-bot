"""Protoss-specific macro steps.

Anything that names a Protoss-only structure or unit belongs here (not in
`steps/common.py`), mirroring how `steps/zerg.py` owns queens and hatcheries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ares.behaviors.macro import BuildStructure
from ares.consts import BuildingSize
from cython_extensions import cy_distance_to
from sc2.ids.unit_typeid import UnitTypeId

from bot.builds.definition import _always
from bot.core.types import Gate, MacroStep

if TYPE_CHECKING:
    from bot.core.context import BotContext


def _nat_wall_first_pylon_present(ctx: "BotContext") -> bool:
    """True if a pylon occupies (or is building on) the YAML FirstPylon slot."""
    nat = ctx.mediator.get_own_nat
    placements = ctx.mediator.get_placements_dict
    if nat not in placements:
        return False
    first_slots = [
        pos
        for pos, info in placements[nat][BuildingSize.TWO_BY_TWO].items()
        if info.get("first_pylon")
    ]
    if not first_slots:
        return False
    pylons = ctx.bot.structures(UnitTypeId.PYLON)
    return any(
        cy_distance_to(pylon.position, slot) < 0.75
        for slot in first_slots
        for pylon in pylons
    )


def _powered_nat_wall_3x3(
    ctx: "BotContext",
    structure_type: UnitTypeId,
) -> bool:
    """True if a powered `is_wall` 3x3 is free at own nat (probe only)."""
    return (
        ctx.mediator.request_building_placement(
            base_location=ctx.mediator.get_own_nat,
            structure_type=structure_type,
            wall=True,
            production=True,
            find_alternative=False,
            within_psionic_matrix=True,
            reserve_placement=False,
        )
        is not None
    )


def natural_wall_pylon(gate: Gate = _always) -> MacroStep:
    """Ensure the nat-wall FirstPylon exists so wall 3x3s can receive power.

    Opening should place it via `pylon @ nat_wall`. A nearby production wall
    pylon is not enough — on Magannatha that only covers 1 of 3 wall 3x3s.
    """

    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None

        if _nat_wall_first_pylon_present(ctx):
            return None

        nat = ctx.mediator.get_own_nat
        ctx.log_once(
            "macro_nat_wall_pylon",
            "MACRO pylon: placing nat wall FirstPylon for wall power",
        )
        return BuildStructure(
            base_location=nat,
            structure_id=UnitTypeId.PYLON,
            wall=True,
            first_pylon=True,
            find_alternative=False,
        )

    return step


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
    opening one use ares nat `is_wall` 3x3 slots (`wall=True`, own nat,
    `find_alternative=False`). Those YAML walls leave a GateKeeper gap so
    units can exit — do not seal it with extra structures. Remaining
    Gateways go in the main / production base.
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
        # still within 1 + wall_natural, take nat wall 3x3 slots.
        use_nat_wall = wall_natural > 0 and total < 1 + wall_natural
        if use_nat_wall:
            nat = ctx.mediator.get_own_nat
            # PoweredPlacementStrategy falls through to non-wall near-pylon
            # when wall slots are unpowered — wait for FirstPylon coverage.
            # If no wall 3x3 remains (claimed / rejected), continue in main.
            if _powered_nat_wall_3x3(ctx, UnitTypeId.GATEWAY):
                ctx.log_once(
                    "macro_gateways_nat_wall",
                    f"MACRO gateways: nat wall slot "
                    f"(have {total}, want {wall_natural} at nat)",
                )
                return BuildStructure(
                    base_location=nat,
                    structure_id=UnitTypeId.GATEWAY,
                    to_count=max(0, count - warpgates),
                    wall=True,
                    production=True,
                    find_alternative=False,
                )
            unpowered = ctx.mediator.request_building_placement(
                base_location=nat,
                structure_type=UnitTypeId.GATEWAY,
                wall=True,
                production=True,
                find_alternative=False,
                within_psionic_matrix=False,
                reserve_placement=False,
            )
            if unpowered is not None:
                ctx.log_once(
                    "macro_gateways_nat_wall_wait",
                    "MACRO gateways: waiting for powered nat wall 3x3",
                )
                return None
            ctx.log_once(
                "macro_gateways_nat_wall_full",
                "MACRO gateways: nat wall full; remaining in main",
            )

        return BuildStructure(
            base_location=ctx.production_location,
            structure_id=UnitTypeId.GATEWAY,
            to_count=max(0, count - warpgates),
        )

    return step


def robotics_facility_at_natural_wall(
    count: int = 1,
    gate: Gate = _always,
) -> MacroStep:
    """Place Robotics Facility into a natural wall 3x3 slot when one is free.

    Shares `ThreeByThreesWall` with nat Gateways. Wait while wall slots exist
    but lack power (FirstPylon still missing). Fall back to main only when no
    wall 3x3 remains at all.
    """

    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None
        have = (
            ctx.bot.structures(UnitTypeId.ROBOTICSFACILITY).amount
            + ctx.bot.structure_pending(UnitTypeId.ROBOTICSFACILITY)
        )
        if have >= count:
            return None

        nat = ctx.mediator.get_own_nat
        wall_pos = ctx.mediator.request_building_placement(
            base_location=nat,
            structure_type=UnitTypeId.ROBOTICSFACILITY,
            wall=True,
            production=True,
            find_alternative=False,
            within_psionic_matrix=True,
            reserve_placement=False,
        )
        if wall_pos is not None:
            ctx.log_once(
                "macro_robo_nat_wall",
                f"MACRO robotics: nat wall slot (have {have}, want {count})",
            )
            return BuildStructure(
                base_location=nat,
                structure_id=UnitTypeId.ROBOTICSFACILITY,
                to_count=count,
                wall=True,
                production=True,
                find_alternative=False,
            )

        # Unpowered wall slot still free → wait for FirstPylon, don't main-bail.
        unpowered = ctx.mediator.request_building_placement(
            base_location=nat,
            structure_type=UnitTypeId.ROBOTICSFACILITY,
            wall=True,
            production=True,
            find_alternative=False,
            within_psionic_matrix=False,
            reserve_placement=False,
        )
        if unpowered is not None:
            ctx.log_once(
                "macro_robo_nat_wall_wait",
                "MACRO robotics: waiting for powered nat wall 3x3",
            )
            return None

        ctx.log_once(
            "macro_robo_main_fallback",
            "MACRO robotics: no nat wall 3x3 left; placing in main",
        )
        return BuildStructure(
            base_location=ctx.production_location,
            structure_id=UnitTypeId.ROBOTICSFACILITY,
            to_count=count,
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
