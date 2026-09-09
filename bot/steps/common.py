"""Race-neutral macro steps.

Each factory returns a `MacroStep`: a function of the context that yields one
ares behavior, or `None` to sit this frame out. They are deliberately tiny —
that is what keeps the engine and the build files small.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId

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


def auto_supply() -> MacroStep:
    def step(ctx: "BotContext"):
        return AutoSupply(ctx.production_location)

    return step


def build_workers() -> MacroStep:
    def step(ctx: "BotContext"):
        return BuildWorkers(to_count=ctx.worker_target)

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
    """Alternate `build_workers`/`spawn_army` priority once `gate` passes, to
    keep economy and army investment roughly even instead of one having
    outright priority.

    `MacroPlan.execute()` runs its macros in order and stops at the first one
    that acts (see `macro_engine.py`) — so simply listing both unconditionally
    always favors whichever is listed first. This nests them in their own
    `MacroPlan`, reordered each frame by whichever side currently has less
    supply invested, so each gets a turn without ever wasting a frame outright
    — if the preferred one has nothing to do, the plan falls through to the
    other on the same frame.

    Before `gate` passes, economy keeps its usual priority (as if this were
    still separate `build_workers()` then `spawn_army()` calls).
    """

    def step(ctx: "BotContext"):
        workers = BuildWorkers(to_count=ctx.worker_target)
        army = SpawnController(dict(ctx.build.army.comp))

        if gate(ctx) and ctx.bot.supply_army < ctx.bot.supply_workers:
            first, second = army, workers
        else:
            first, second = workers, army

        plan = MacroPlan()
        plan.add(first)
        plan.add(second)
        return plan

    return step


def structure(
    structure_id: UnitTypeId, count: int, gate: Gate = _always
) -> MacroStep:
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
