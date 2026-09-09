"""Zerg-specific macro steps.

Anything that mentions a hatchery, a queen or larva belongs here rather than
in `steps/common.py`, so the shared engine stays race-neutral.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from ares.behaviors.macro import BuildStructure, MacroPlan

from bot.behaviors.zerg import BuildMacroHatch, InjectLarva, TrainQueens
from bot.builds.definition import _always
from bot.core.types import Gate, MacroStep
from bot.steps import common

if TYPE_CHECKING:
    from bot.core.context import BotContext


def inject_larva(min_energy: int = 25) -> MacroStep:
    """Belongs in `always` — injects must keep running during the opening."""

    def step(ctx: "BotContext"):
        return InjectLarva(min_energy=min_energy)

    return step


def train_queens(per_base: int = 1, maximum: int = 4) -> MacroStep:
    def step(ctx: "BotContext"):
        return TrainQueens(
            to_count=min(maximum, ctx.base_count * per_base),
            max_per_townhall=per_base,
        )

    return step


def macro_hatch(count: int, gate: Gate = _always) -> MacroStep:
    """Extra hatcheries inside the main, purely for larva.

    Uses `BuildMacroHatch` rather than `common.structure`: ares' zerg
    placement searches 30 tiles from the base location and will put the hatch
    at the natural. See that behavior's docstring.
    """

    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None
        return BuildMacroHatch(to_count=count)

    return step


def evolution_chambers(count: int, gate: Gate = _always) -> MacroStep:
    """`UpgradeController` only ever builds one; a second enables +1/+1 in parallel."""
    return common.structure(UnitTypeId.EVOLUTIONCHAMBER, count, gate)


def spore_crawlers(per_base: int, gate: Gate = _always) -> MacroStep:
    """One Spore Crawler (mineral-line placement) per owned base, once `gate` passes.

    Unlike `common.structure`, which caps a single global count at one
    location, this issues a `BuildStructure` call per base so each one gets
    its own, capped with `to_count_per_base` rather than `to_count`.

    Deliberately keyed off `ctx.bot.owned_expansions` rather than
    `ctx.ready_townhalls`: `to_count_per_base` looks up
    `mediator.get_placements_dict[base_location]`, which is only ever keyed
    by the map's precomputed expansion-location points
    (`ai.expansion_locations_list`) — passing a townhall's literal position
    is a `KeyError` the moment it doesn't land on that exact point (crashed a
    real game: `KeyError: (60.5, 56.5)`, the natural). `owned_expansions` is
    ares' own `{expansion_location: townhall}` mapping, built from those same
    precomputed points, so every key here is guaranteed to already exist in
    the placements dict. It also collapses a macro hatch sharing the main's
    location down to one entry instead of a second, redundant call there.

    Bundled into their own `MacroPlan` so a base that already has enough
    doesn't block the next base's turn on the same frame (see
    `MacroPlan.execute`, which stops at the first behavior that acts).
    """

    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None
        plan = MacroPlan()
        for location in ctx.bot.owned_expansions:
            plan.add(
                BuildStructure(
                    base_location=location,
                    structure_id=UnitTypeId.SPORECRAWLER,
                    to_count_per_base=per_base,
                )
            )
        return plan

    return step


def spine_crawlers(count: int, gate: Gate = _always) -> MacroStep:
    return common.structure(UnitTypeId.SPINECRAWLER, count, gate)


LING_SPEED: UpgradeId = UpgradeId.ZERGLINGMOVEMENTSPEED
