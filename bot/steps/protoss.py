"""Protoss-specific macro steps.

Anything that names a Protoss-only structure or unit belongs here (not in
`steps/common.py`), mirroring how `steps/zerg.py` owns queens and hatcheries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ares.consts import BuildingSize
from cython_extensions import cy_closest_to, cy_distance_to
from sc2.ids.unit_typeid import UnitTypeId

from bot.behaviors.protoss import (
    ProtossAutoSupply,
    ProtossBuildStructure,
    ProtossChronoBoost,
    ProtossExpansionController,
    ProtossGasBuildingController,
)
from bot.behaviors.protoss.chrono_boost import CHRONO_DURATION_S, CHRONO_ENERGY_COST
from bot.builds.definition import _always
from bot.core.types import Gate, MacroStep

if TYPE_CHECKING:
    from bot.core.context import BotContext


def _nat_wall_first_pylon_present(ctx: "BotContext") -> bool:
    """True if a pylon occupies (or is building on) the YAML FirstPylon slot."""
    nat = ctx.own_nat
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


def _nat_wall_3x3_available(
    ctx: "BotContext",
    structure_type: UnitTypeId,
    *,
    powered: bool,
) -> bool:
    """True if an `is_wall` 3x3 at own nat is free (powered filter optional)."""
    return (
        ctx.mediator.request_building_placement(
            base_location=ctx.own_nat,
            structure_type=structure_type,
            wall=True,
            production=True,
            find_alternative=False,
            within_psionic_matrix=powered,
            reserve_placement=False,
        )
        is not None
    )


def _powered_nat_wall_3x3(
    ctx: "BotContext",
    structure_type: UnitTypeId,
) -> bool:
    """True if a powered `is_wall` 3x3 is free at own nat (probe only)."""
    return _nat_wall_3x3_available(ctx, structure_type, powered=True)


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

        nat = ctx.own_nat
        ctx.log_once(
            "macro_nat_wall_pylon",
            "MACRO pylon: placing nat wall FirstPylon for wall power",
        )
        return ProtossBuildStructure(
            base_location=nat,
            structure_id=UnitTypeId.PYLON,
            wall=True,
            first_pylon=True,
            find_alternative=False,
        )

    return step


def auto_supply(gate: Gate = _always) -> MacroStep:
    """Protoss supply via the dedicated macro builder Probe."""

    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None
        return ProtossAutoSupply(ctx.production_location)

    return step


def chrono_boost_army(gate: Gate = _always) -> MacroStep:
    """Spend Nexus energy on Chrono Boost, in priority order: the natural
    Nexus boosting its own probe production (once it exists and is
    training), then Robo (Prism/Observer), then a Warp Gate (faster
    warp-in cycling) once Robo no longer needs it.

    Belongs in `always`: unlike `macro_steps`, `always` entries are each
    registered independently (see `core.macro_engine.MacroEngine.execute` -
    no first-acts-wins collapsing across the tuple), so this never competes
    with Pylon/Gateway spending for priority - only for Nexus energy, a
    separate resource.

    Gated on `ctx.build_completed` so it never fires during the opening,
    which has its own scripted `chrono @ ...` build-order steps (Nexus for
    probes, Gateway for the opening Adept, Twilight Council for Charge) that
    must get first claim on that energy - see `protoss_builds.yml`.

    Target selection happens here (not in `ProtossChronoBoost.execute()`)
    because it needs `ctx.state` to track each target's own cooldown -
    `has_buff` alone was observed to miss a just-issued cast on the very
    next frame, re-targeting the same structures every frame for a full
    minute before this existed.
    """

    def step(ctx: "BotContext"):
        if not ctx.build_completed or not gate(ctx):
            return None

        townhalls = list(ctx.bot.townhalls.ready)
        if not townhalls:
            return None

        now = ctx.bot.time
        cooldowns = ctx.state.chrono_target_cooldowns

        def _ready(tag: int) -> bool:
            last = cooldowns.get(tag)
            return last is None or now - last >= CHRONO_DURATION_S

        # Priority 1: the natural boosts its own probe production, using
        # only its own energy - never borrows from the main.
        natural = cy_closest_to(ctx.own_nat, townhalls)
        if (
            natural.orders
            and natural.energy >= CHRONO_ENERGY_COST
            and _ready(natural.tag)
        ):
            cooldowns[natural.tag] = now
            return ProtossChronoBoost(caster=natural, target=natural)

        casters = [n for n in townhalls if n.energy >= CHRONO_ENERGY_COST]
        if not casters:
            return None

        robos = [
            r
            for r in ctx.bot.structures(UnitTypeId.ROBOTICSFACILITY).ready
            if r.orders and _ready(r.tag)
        ]
        warpgates = [
            w
            for w in ctx.bot.structures(UnitTypeId.WARPGATE).ready
            if _ready(w.tag)
        ]
        target = robos[0] if robos else (warpgates[0] if warpgates else None)
        if target is None:
            return None

        cooldowns[target.tag] = now
        return ProtossChronoBoost(caster=casters[0], target=target)

    return step


def expansions() -> MacroStep:
    def step(ctx: "BotContext"):
        return ProtossExpansionController(to_count=ctx.build.economy.max_bases)

    return step


def gas_buildings() -> MacroStep:
    def step(ctx: "BotContext"):
        return ProtossGasBuildingController(to_count=ctx.gas_target)

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
            nat = ctx.own_nat
            # PoweredPlacementStrategy falls through to non-wall near-pylon
            # when wall slots are unpowered — wait for FirstPylon coverage.
            # If no wall 3x3 remains (claimed / rejected), continue in main.
            if _powered_nat_wall_3x3(ctx, UnitTypeId.GATEWAY):
                ctx.log_once(
                    "macro_gateways_nat_wall",
                    f"MACRO gateways: nat wall slot "
                    f"(have {total}, want {wall_natural} at nat)",
                )
                return ProtossBuildStructure(
                    base_location=nat,
                    structure_id=UnitTypeId.GATEWAY,
                    to_count=max(0, count - warpgates),
                    wall=True,
                    production=True,
                    find_alternative=False,
                )
            unpowered = _nat_wall_3x3_available(
                ctx, UnitTypeId.GATEWAY, powered=False
            )
            # Wait only while FirstPylon is still missing. Once it exists,
            # leftover unpowered wall slots will never gain power — continue
            # in main instead of stalling the 8-Gate commit.
            if unpowered and not _nat_wall_first_pylon_present(ctx):
                ctx.log_once(
                    "macro_gateways_nat_wall_wait",
                    "MACRO gateways: waiting for powered nat wall 3x3",
                )
                return None
            if unpowered:
                ctx.log_once(
                    "macro_gateways_nat_wall_unpowered",
                    "MACRO gateways: wall slots unpowered after FirstPylon; "
                    "remaining in main",
                )
            else:
                ctx.log_once(
                    "macro_gateways_nat_wall_full",
                    "MACRO gateways: nat wall full; remaining in main",
                )

        return ProtossBuildStructure(
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

    Shares `ThreeByThreesWall` with nat Gateways. Wait while FirstPylon is
    still missing. Fall back to main when no powered wall 3x3 remains.
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

        nat = ctx.own_nat
        if _powered_nat_wall_3x3(ctx, UnitTypeId.ROBOTICSFACILITY):
            ctx.log_once(
                "macro_robo_nat_wall",
                f"MACRO robotics: nat wall slot (have {have}, want {count})",
            )
            return ProtossBuildStructure(
                base_location=nat,
                structure_id=UnitTypeId.ROBOTICSFACILITY,
                to_count=count,
                wall=True,
                production=True,
                find_alternative=False,
            )

        # Unpowered wall slot still free → wait only until FirstPylon exists.
        if _nat_wall_3x3_available(
            ctx, UnitTypeId.ROBOTICSFACILITY, powered=False
        ) and not _nat_wall_first_pylon_present(ctx):
            ctx.log_once(
                "macro_robo_nat_wall_wait",
                "MACRO robotics: waiting for powered nat wall 3x3",
            )
            return None

        ctx.log_once(
            "macro_robo_main_fallback",
            "MACRO robotics: no powered nat wall 3x3; placing in main",
        )
        return ProtossBuildStructure(
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
        return ProtossBuildStructure(
            base_location=ctx.production_location,
            structure_id=UnitTypeId.PYLON,
            find_alternative=True,
        )

    return step
