"""Terran-specific macro steps.

Factories here (not in `steps/common.py`) for anything that names a
Terran-only structure or unit, mirroring how `steps/zerg.py` owns queens and
hatcheries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ares.behaviors.macro import BuildStructure
from sc2.ids.unit_typeid import UnitTypeId

from bot.builds.definition import _always
from bot.core.types import Gate, MacroStep, PointLocator

if TYPE_CHECKING:
    from bot.core.context import BotContext


def proxy_barracks(
    to_count: int,
    where: PointLocator,
    gate: Gate = _always,
    max_on_route: int = 1,
) -> MacroStep:
    """Keep `to_count` Barracks standing at `where`, once `gate` passes.

    This is a plain `BuildStructure` pointed at somebody else's base location
    rather than our own, and that is the whole trick: ares'
    `_solve_terran_building_formation` walks **every** entry in
    `ai.expansion_locations_list` when it precomputes placements, enemy
    expansions included, so `request_building_placement` solves a real,
    legal Terran placement at the enemy's third exactly as happily as at our
    own main. None of the roll-your-own placement search that
    `behaviors/zerg/build_macro_hatch.py` needed applies here - that was
    forced by `_solve_zerg_building_formation` being an unimplemented stub
    (ARCHITECTURE.md gotchas 1 and 10), which is a Zerg-only problem.

    Which SCV goes is left to ares, and that is deliberate rather than lazy:
    `BuildStructure` selects via `mediator.select_worker(force_close=True)`,
    the closest *gathering* worker to the placement. Early on every worker is
    at home, so that means "pull one off the mineral line"; once a builder is
    standing at the proxy having just finished a Barracks, it is by a wide
    margin the closest gathering worker to the next one, so the follow-up
    Barracks falls to it with no tag bookkeeping at all. Pinning specific
    SCVs by tag would encode the same outcome more brittlely - a dead builder
    would strand the step, where "closest worker" simply picks someone else.

    Attributes:
        to_count: Total Barracks to have standing (ready or building).
        where: Resolves the proxy base location, fresh each frame.
        gate: Extra condition; the caller stages the count with it.
        max_on_route: Workers allowed to be walking there at once.
    """

    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None
        return BuildStructure(
            base_location=where(ctx),
            structure_id=UnitTypeId.BARRACKS,
            to_count=to_count,
            max_on_route=max_on_route,
        )

    return step
