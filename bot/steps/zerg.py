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

    `BuildStructure.to_count_per_base` cannot be used here: it is checked via
    `mediator.get_placements_dict[base_location]`, and that dict is only ever
    populated by `PlacementManager._solve_terran_building_formation` /
    `_solve_protoss_building_formation` — `_solve_zerg_building_formation` is
    an unimplemented stub (`# TODO: Implement zerg placements`) in ares
    v3.13.1. So for a Zerg bot the dict never gets a single key, and
    `to_count_per_base` is a guaranteed `KeyError` the first time it runs,
    for *any* location — not a location-matching bug (switching from
    `townhall.position` to the canonical `ctx.bot.owned_expansions` key
    didn't help; it crashed a real game a second time at a different
    location entirely). Zerg placement goes through the completely separate
    `ai.request_zerg_placement` -> `_do_zerg_build_placement` path instead
    (see gotcha 1), which knows nothing about `placements_dict`.

    So this counts existing/in-progress crawlers itself instead, the same
    way `bot/behaviors/zerg/build_macro_hatch.py` rolls its own zerg-specific
    logic rather than leaning on placement-solver machinery that only really
    supports Terran/Protoss. `EXPANSION_GAP_THRESHOLD` (15) is python-sc2's
    own radius for "close enough to belong to this base" — the same radius
    `owned_expansions` itself uses to match a townhall to its expansion
    location, so a crawler is credited to a base on the same terms a
    townhall is.

    `ai.structure_pending(SPORECRAWLER)` gates the whole step, not just a
    per-base count: a dispatched worker walking to build doesn't show up in
    `structures()` yet, so counting only structures re-requests the same
    still-uncovered base every frame for the whole walk time — a real game
    ended up with several crawlers piled onto one base. `structure_pending`
    is the same combined ready-or-pending count `BuildStructure.to_count`
    already uses successfully elsewhere in this file (`_enough_existing`),
    so nothing new is requested anywhere while one crawler is already in
    flight, and `BuildStructure`'s own `max_on_route` is a second, redundant
    line of defense rather than the only one.

    Bundled into their own `MacroPlan` so a base that already has enough
    doesn't block the next base's turn on the same frame (see
    `MacroPlan.execute`, which stops at the first behavior that acts).
    """

    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None
        if ctx.bot.structure_pending(UnitTypeId.SPORECRAWLER):
            return None
        radius = ctx.bot.EXPANSION_GAP_THRESHOLD
        existing = ctx.bot.structures(UnitTypeId.SPORECRAWLER)
        plan = MacroPlan()
        for location in ctx.bot.owned_expansions:
            if len(existing.closer_than(radius, location)) >= per_base:
                continue
            plan.add(
                BuildStructure(
                    base_location=location,
                    structure_id=UnitTypeId.SPORECRAWLER,
                )
            )
        return plan

    return step


def spine_crawlers(count: int, gate: Gate = _always) -> MacroStep:
    return common.structure(UnitTypeId.SPINECRAWLER, count, gate)


LING_SPEED: UpgradeId = UpgradeId.ZERGLINGMOVEMENTSPEED
