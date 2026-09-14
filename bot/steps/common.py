"""Race-neutral macro steps.

Each factory returns a `MacroStep`: a function of the context that yields one
ares behavior, or `None` to sit this frame out. They are deliberately tiny —
that is what keeps the engine and the build files small.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ares.behaviors.macro import (
    AutoSupply,
    BuildStructure,
    BuildWorkers,
    ExpansionController,
    GasBuildingController,
    MacroPlan,
    Mining,
    SpawnController,
    UpgradeController,
)
from ares.consts import BuildingSize, UnitRole
from cython_extensions import cy_distance_to
from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2

from bot.behaviors import SetGasWorkers
from bot.builds.definition import _always
from bot.consts import CHARGELOT_COMP, CHARGELOT_FLOOD_COMP
from bot.core.types import Gate, MacroStep
from bot.routines import gates as gate_fns

if TYPE_CHECKING:
    from bot.core.context import BotContext

# Warp Prism cost — used to time gas peel vs mineral bank.
_PRISM_MINERALS: int = 200
_PRISM_GAS: int = 100
# Per-geyser workers during the attack (Stalker funding). 3-1-2 schedule.
_CHARGELOT_ATTACK_GAS_WORKERS: int = 2
# Temporary peel while banking minerals for the Prism (gas cost already covered).
_CHARGELOT_PRISM_BANK_GAS_WORKERS: int = 1
# Opening YAML trains one Stalker; spawn_army must secure the second before
# dumping mineral surplus into Zealots (guide: two Stalkers before the flood).
_CHARGELOT_OPENING_STALKERS: int = 2
_STALKER_MINERALS: int = 125
_STALKER_GAS: int = 50
_ZEALOT_MINERALS: int = 100

_SPAWN_LOG_PATHS: frozenset[str] = frozenset(
    {
        "opening_stalker2",
        "opening_wait_stalker2",
        "pre_prism_stalker2",
        "pre_prism_wait_stalker",
        "pre_prism_zealot",
        "flood_zealots",
    }
)
_last_spawn_path: dict[str, str | None] = {"path": None}


def _chargelot_has_prism(ctx: "BotContext") -> bool:
    """True once a Warp Prism exists or is already training."""
    units = ctx.bot.units
    if units(UnitTypeId.WARPPRISM) or units(UnitTypeId.WARPPRISMPHASING):
        return True
    return ctx.bot.already_pending(UnitTypeId.WARPPRISM) > 0


def _chargelot_stalkers_out(ctx: "BotContext") -> int:
    """Live + pending Stalkers (opening handoff counts both)."""
    return (
        ctx.bot.units(UnitTypeId.STALKER).amount
        + ctx.bot.already_pending(UnitTypeId.STALKER)
    )


def chargelot_stalkers_out(ctx: "BotContext") -> int:
    """Public helper for MacroPlan gates (2 Stalkers before Robo)."""
    return _chargelot_stalkers_out(ctx)


def _spawn_stalker_only(spawn_target: Point2 | None) -> SpawnController:
    return SpawnController(
        {UnitTypeId.STALKER: {"proportion": 1.0, "priority": 0}},
        spawn_target=spawn_target,
        freeflow_mode=True,
    )


def _spawn_zealot_only(spawn_target: Point2 | None) -> SpawnController:
    return SpawnController(
        {UnitTypeId.ZEALOT: {"proportion": 1.0, "priority": 0}},
        spawn_target=spawn_target,
        freeflow_mode=True,
    )


def _chargelot_gas_amount(ctx: "BotContext") -> int:
    """Gas schedule 3 → 1 → 2 for Chargelot.

    Before Charge: full saturation (3/geyser).
    After Charge, pre-Prism: once gas covers the Prism cost, latch a mineral
    bank peel (1/geyser) until Prism starts.
    After Prism: 2 per geyser for Stalkers without over-mining gas.
    """
    full = ctx.build.economy.workers_per_gas
    if _chargelot_has_prism(ctx) or ctx.state.chargelot_metrics.prism_produced:
        # First Prism done (or dead): never re-peel to the 1-worker bank.
        ctx.state.chargelot_prism_gas_bank = False
        return _CHARGELOT_ATTACK_GAS_WORKERS
    if not gate_fns.upgrade_started(UpgradeId.CHARGE)(ctx):
        return full
    if ctx.bot.vespene >= _PRISM_GAS:
        ctx.state.chargelot_prism_gas_bank = True
    if ctx.state.chargelot_prism_gas_bank:
        return _CHARGELOT_PRISM_BANK_GAS_WORKERS
    return full


def chargelot_gas_workers() -> MacroStep:
    """Dynamic per-geyser gas count for Chargelot (see `_chargelot_gas_amount`)."""

    _last_amount: dict[str, int] = {"n": -1}

    def step(ctx: "BotContext"):
        amount = _chargelot_gas_amount(ctx)
        if _last_amount["n"] != amount:
            _last_amount["n"] = amount
            ctx.log(
                f"GAS workers/geyser={amount} "
                f"min={ctx.bot.minerals} gas={ctx.bot.vespene}"
            )
        return SetGasWorkers(amount)

    return step


def _chargelot_spawn(ctx: "BotContext") -> SpawnController | None:
    """Guide BO: 2 Stalkers, then Zealot flood + Prism, then freeflow.

    Spawning Stalkers 3+ on Prism-bank surplus (pre_prism_stalker) ate gas and
    delayed Zealots the BO warps from ~4:32. Cap at `_CHARGELOT_OPENING_STALKERS`
    until the first Prism is pending/live; dump mineral surplus into Zealots.

    Hard Prism bank only for the *first* Prism — death must not freeze warps.
    After Prism: freeflow Stalker+Zealot (BO warps Stalkers ~5:54 at the fight).
    """
    spawn_target = _warp_spawn_target(ctx)
    has_prism = _chargelot_has_prism(ctx)
    prism_ever = bool(ctx.state.chargelot_metrics.prism_produced)
    robo_ready = bool(ctx.bot.structures(UnitTypeId.ROBOTICSFACILITY).ready)
    stalkers = _chargelot_stalkers_out(ctx)
    minerals = ctx.bot.minerals
    gas = ctx.bot.vespene
    path = "none"
    result: SpawnController | None = None

    # Hard Prism bank only for the *first* Prism — not after it dies.
    if not has_prism and robo_ready and not prism_ever:
        can_afford_prism = (
            minerals >= _PRISM_MINERALS and gas >= _PRISM_GAS
        )
        if can_afford_prism:
            path = "prism"
            result = SpawnController(
                {UnitTypeId.WARPPRISM: {"proportion": 1.0, "priority": 0}},
                spawn_target=spawn_target,
                freeflow_mode=False,
            )
        else:
            # Reserve 200/100 for Prism. Only Stalker #2 may use surplus gas;
            # never roll Stalkers 3+ here (BO is Zealot flood).
            surplus = minerals - _PRISM_MINERALS
            gas_surplus = gas - _PRISM_GAS
            if (
                stalkers < _CHARGELOT_OPENING_STALKERS
                and ctx.bot.already_pending(UnitTypeId.STALKER) <= 0
                and surplus >= _STALKER_MINERALS
                and gas_surplus >= _STALKER_GAS
            ):
                path = "pre_prism_stalker2"
                result = _spawn_stalker_only(spawn_target)
            elif (
                stalkers < _CHARGELOT_OPENING_STALKERS
                and gas_surplus >= _STALKER_GAS
            ):
                path = "pre_prism_wait_stalker"
                result = None
            elif surplus >= _ZEALOT_MINERALS:
                path = "pre_prism_zealot"
                result = _spawn_zealot_only(spawn_target)
            else:
                path = "pre_prism_hold"
                result = None
    elif not has_prism:
        # Robo not up yet, or Prism died — Zealot flood (Stalkers only if
        # still short of the opening two).
        if not ctx.build_completed:
            path = "opening_comp"
            result = SpawnController(
                dict(ctx.build.army.comp),
                spawn_target=spawn_target,
            )
        elif stalkers < _CHARGELOT_OPENING_STALKERS:
            # Opening YAML should have queued #2; only recover if nothing is
            # pending (avoid double-queue → Stalker #3).
            if ctx.bot.already_pending(UnitTypeId.STALKER) > 0:
                path = "opening_wait_stalker2"
                result = None
            elif minerals >= _STALKER_MINERALS and gas >= _STALKER_GAS:
                path = "opening_stalker2"
                result = _spawn_stalker_only(spawn_target)
            else:
                path = "opening_wait_stalker2"
                result = None
        else:
            # Zealots only until Prism — FLOOD_COMP would keep making Stalkers.
            path = "flood_zealots" if not prism_ever else "flood_after_prism_dead"
            if prism_ever:
                result = SpawnController(
                    dict(CHARGELOT_FLOOD_COMP),
                    spawn_target=spawn_target,
                    freeflow_mode=True,
                )
            else:
                result = _spawn_zealot_only(spawn_target)
    else:
        # Prism secured. BO keeps Zealot-warping through the leave (~5:20);
        # Stalker warps land with the Prism field (~5:54). Until the Prism
        # is phasing, dump minerals into Zealots — freeflow Stalker priority
        # was eating the Zealot flood the moment Prism started.
        units = ctx.bot.units
        has_obs = bool(units(UnitTypeId.OBSERVER))
        need_obs = (
            not has_obs and ctx.bot.already_pending(UnitTypeId.OBSERVER) <= 0
        )
        prism_phasing = bool(units(UnitTypeId.WARPPRISMPHASING))
        if need_obs and not prism_phasing:
            path = "post_prism_obs_zealot"
            result = SpawnController(
                {
                    UnitTypeId.OBSERVER: {
                        "proportion": 0.1,
                        "priority": 0,
                    },
                    UnitTypeId.ZEALOT: {
                        "proportion": 0.9,
                        "priority": 1,
                    },
                },
                spawn_target=spawn_target,
                freeflow_mode=True,
            )
        elif not prism_phasing:
            path = "post_prism_zealot"
            result = _spawn_zealot_only(spawn_target)
        elif need_obs:
            path = "post_prism_obs_flood"
            result = SpawnController(
                {
                    UnitTypeId.OBSERVER: {
                        "proportion": 0.05,
                        "priority": 0,
                    },
                    UnitTypeId.STALKER: {
                        "proportion": 0.35,
                        "priority": 0,
                    },
                    UnitTypeId.ZEALOT: {
                        "proportion": 0.60,
                        "priority": 1,
                    },
                },
                spawn_target=spawn_target,
                freeflow_mode=True,
            )
        else:
            path = "post_prism_flood"
            result = SpawnController(
                dict(CHARGELOT_FLOOD_COMP),
                spawn_target=spawn_target,
                freeflow_mode=True,
            )

    if (
        ctx.bot.time >= 150.0
        and path in _SPAWN_LOG_PATHS
        and _last_spawn_path["path"] != path
    ):
        _last_spawn_path["path"] = path
        ctx.log(
            f"SPAWN {path} stalkers={stalkers} "
            f"min={minerals} gas={gas}"
        )
    return result


def spawn_army(gate: Gate = _always) -> MacroStep:
    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None
        # Chargelot: dynamic Stalker-first flood after the opening.
        if ctx.build.army.comp is CHARGELOT_COMP or (
            UnitTypeId.ZEALOT in ctx.build.army.comp
            and UnitTypeId.STALKER in ctx.build.army.comp
            and UnitTypeId.WARPPRISM in ctx.build.army.comp
        ):
            return _chargelot_spawn(ctx)
        return SpawnController(
            dict(ctx.build.army.comp),
            spawn_target=_warp_spawn_target(ctx),
        )

    return step


def mining() -> MacroStep:
    """Worker and gas assignment. Belongs in `always`, not `macro_steps`."""

    def step(ctx: "BotContext"):
        return Mining(
            workers_per_gas=ctx.build.economy.workers_per_gas,
            long_distance_mine=ctx.build.economy.long_distance_mine,
        )

    return step


def gas_workers(pull_off: Gate | None = None, when_pulled: int = 0) -> MacroStep:
    """Keep `economy.workers_per_gas` on each geyser; pull off when gated.

    Belongs in `always` — it is a setting that must be reasserted each frame,
    and it is also the only thing that makes `economy.workers_per_gas` take
    effect at all (see `SetGasWorkers`).

    Attributes:
        pull_off: When this passes, drop to `when_pulled` workers per geyser.
        when_pulled: Workers to leave on gas once pulled. 0 empties the geyser.
    """

    def step(ctx: "BotContext"):
        amount = ctx.build.economy.workers_per_gas
        if pull_off is not None and pull_off(ctx):
            amount = when_pulled
        return SetGasWorkers(amount)

    return step


def auto_supply(gate: Gate = _always) -> MacroStep:
    """Keep Depots/Overlords ahead of production, once `gate` passes.

    Attributes:
        gate: Extra condition - e.g. a build that deliberately skips generic
            supply during its opening (crew Depots only) and only turns this
            on later.
    """

    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None
        return AutoSupply(ctx.production_location)

    return step


def build_workers(gate: Gate = _always, to_count: int | None = None) -> MacroStep:
    """Train workers up to `ctx.worker_target` (or `to_count`), once `gate`
    passes.

    Attributes:
        gate: Extra condition beyond `MacroPlan`'s usual "only if nothing
            higher-priority acted this frame" - e.g. a build that wants its
            *next* worker held back until some other condition of its own,
            not merely whenever resources allow it.
        to_count: Override the build's usual `ctx.worker_target`. A build
            that lifts its worker cap mid-game (cleanup after an all-in)
            passes a higher ceiling here without rewriting `Economy`.
    """

    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None
        return BuildWorkers(
            to_count=ctx.worker_target if to_count is None else to_count
        )

    return step


def expansions() -> MacroStep:
    def step(ctx: "BotContext"):
        return ExpansionController(to_count=ctx.build.economy.max_bases)

    return step


def gas_buildings() -> MacroStep:
    def step(ctx: "BotContext"):
        return GasBuildingController(to_count=ctx.gas_target)

    return step


def upgrades() -> MacroStep:
    def step(ctx: "BotContext"):
        if not ctx.build.army.upgrades:
            return None
        return UpgradeController(
            list(ctx.build.army.upgrades), base_location=ctx.production_location
        )

    return step


def _nat_wall_warp_anchor(ctx: "BotContext") -> Point2:
    """Natural-wall FirstPylon slot if known, else own natural / start."""
    nat = ctx.mediator.get_own_nat
    if nat is None:
        return ctx.bot.start_location
    placements = ctx.mediator.get_placements_dict
    if nat in placements:
        first_slots = [
            pos
            for pos, info in placements[nat][BuildingSize.TWO_BY_TWO].items()
            if info.get("first_pylon")
        ]
        if first_slots:
            # Prefer a live pylon on the wall slot; else the slot itself.
            pylons = ctx.bot.structures(UnitTypeId.PYLON).ready
            for slot in first_slots:
                for pylon in pylons:
                    if cy_distance_to(pylon.position, slot) < 0.75:
                        return pylon.position
            return Point2(first_slots[0])
    return Point2(nat)


def _warp_spawn_target(ctx: "BotContext") -> Point2 | None:
    """Warp priority: phasing Prism, then natural-wall pylon.

    Non-Protoss builds leave `spawn_target` unset (ares default).
    """
    if ctx.bot.race != Race.Protoss:
        return None
    phasing = list(ctx.bot.units(UnitTypeId.WARPPRISMPHASING))
    if not phasing:
        # DROP_SHIP role covers our Prism even if type filter races a morph.
        for unit in ctx.mediator.get_units_from_role(role=UnitRole.DROP_SHIP):
            if unit.type_id == UnitTypeId.WARPPRISMPHASING:
                phasing.append(unit)
    if phasing:
        army = ctx.units_in_role(UnitRole.ATTACKING)
        if army:
            anchor = Point2(
                (
                    sum(u.position.x for u in army) / len(army),
                    sum(u.position.y for u in army) / len(army),
                )
            )
            return min(
                phasing, key=lambda p: cy_distance_to(p.position, anchor)
            ).position
        return phasing[0].position
    return _nat_wall_warp_anchor(ctx)


def split_production(gate: Gate = _always) -> MacroStep:
    """Alternate `build_workers`/`spawn_army` priority once `gate` passes:
    economy keeps first pick until workers reach `ctx.worker_target`, then
    army takes over outright.

    `MacroPlan.execute()` runs its macros in order and stops at the first one
    that acts (see `macro_engine.py`) — so simply listing both unconditionally
    always favors whichever is listed first. This nests them in their own
    `MacroPlan`, reordered each frame by whether economy has hit its target
    yet, so neither wastes a frame outright — if the preferred one has
    nothing to do, the plan falls through to the other on the same frame.

    This compares `ctx.bot.supply_workers` against `ctx.worker_target` -
    i.e. workers against their own target - rather than against
    `ctx.bot.supply_army`. A worker and a zergling don't cost the same
    supply (1.0 vs. 0.5), so a raw `supply_army < supply_workers` check
    reads army as "behind" for most of the game regardless of how many
    zerglings are actually out, handing it first pick far more often than
    intended and never really "evening out" anything - comparing economy
    to its own target sidesteps that unit mismatch entirely, and matches
    what this is actually meant to gate: whether economy still needs
    investment, not a tug-of-war between two differently-priced unit types.

    Before `gate` passes, economy keeps its usual priority (as if this were
    still separate `build_workers()` then `spawn_army()` calls) - which is
    also what happens once `gate` passes and workers are still below target,
    so `gate` only changes behavior once economy is maxed.
    """

    def step(ctx: "BotContext"):
        workers = BuildWorkers(to_count=ctx.worker_target)
        army = SpawnController(
            dict(ctx.build.army.comp),
            spawn_target=_warp_spawn_target(ctx),
        )

        economy_at_target = ctx.bot.supply_workers >= ctx.worker_target
        if gate(ctx) and economy_at_target:
            first, second = army, workers
        else:
            first, second = workers, army

        plan = MacroPlan()
        plan.add(first)
        plan.add(second)
        return plan

    return step


def structure(structure_id: UnitTypeId, count: int, gate: Gate = _always) -> MacroStep:
    """Keep `count` of a structure at the production location, once `gate` passes."""

    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None
        return BuildStructure(
            base_location=ctx.production_location,
            structure_id=structure_id,
            to_count=count,
        )

    return step
