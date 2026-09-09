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
    Mining,
    SpawnController,
    UpgradeController,
)

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
