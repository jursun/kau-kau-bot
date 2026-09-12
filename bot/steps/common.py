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
from sc2.ids.unit_typeid import UnitTypeId

from bot.behaviors import SetGasWorkers
from bot.builds.definition import _always
from bot.core.types import Gate, MacroStep

if TYPE_CHECKING:
    from bot.core.context import BotContext


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


def spawn_army() -> MacroStep:
    def step(ctx: "BotContext"):
        return SpawnController(dict(ctx.build.army.comp))

    return step


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
        army = SpawnController(dict(ctx.build.army.comp))

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
