"""Zerg-specific macro steps.

Anything that mentions a hatchery, a queen or larva belongs here rather than
in `steps/common.py`, so the shared engine stays race-neutral.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ares.behaviors.macro import ExpansionController, MacroPlan
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from bot.behaviors.zerg import (
    BuildMacroHatch,
    BuildSporeCrawler,
    InjectLarva,
    MorphOverseers,
    TrainQueens,
)
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


def train_queens(per_base: int = 1, maximum: int = 4, extra: int = 0) -> MacroStep:
    """Keep one queen per base (for injects), plus `extra` more for other
    duties (see `routines.creep.spread_creep`), capped at `maximum` total.

    `TrainQueens.max_per_townhall` only feeds the internal
    `min(to_count, len(townhalls) * max_per_townhall)` target formula — it
    doesn't restrict which townhall trains what — so it's padded by `extra`
    here too, otherwise that formula would silently clip the extra queen
    back off (`base_count * per_base` alone is always < `to_count` once
    `extra` is nonzero).
    """

    def step(ctx: "BotContext"):
        return TrainQueens(
            to_count=min(maximum, ctx.base_count * per_base + extra),
            max_per_townhall=per_base + extra,
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


def overflow_hatcheries(mineral_threshold: int = 500) -> MacroStep:
    """Keep adding hatcheries - a real expansion where a legal one is still
    available, else a macro hatch in the main - whenever the bank is
    floating more than `mineral_threshold` minerals.

    Meant as a release valve for a mid/late-game larva bottleneck: once
    `economy.max_bases` and any fixed `macro_hatch` target are both
    reached, nothing earlier in `macro_steps` has anywhere left to put
    surplus minerals - `BuildWorkers`/`SpawnController` (`split_production`)
    are larva-limited at that point, not mineral-limited, so minerals pile
    up with nowhere to go. More hatcheries mean more larva slots and more
    inject targets, which actually addresses a larva bottleneck, rather
    than just spending harder on the units the existing larva already
    produces.

    Belongs last in `macro_steps`: everything before it (queens, evolution
    chambers, spore crawlers, expansions up to the build's own cap, gas,
    upgrades, `split_production`) gets first refusal every frame, so this
    only ever fires on a frame where nothing else could act *and* the bank
    still shows a real surplus - exactly "floating minerals because we're
    larva-capped, not because nothing wants them."

    A real expansion is strongly preferred over a macro hatch, and the
    whole step is gated on `ai.structure_pending(HATCHERY) == 0` rather
    than trusting `ExpansionController`/`BuildMacroHatch`'s own internal
    pending checks - a first version that didn't do this piled up macro
    hatches almost exclusively, because those two checks read the exact
    same underlying count very differently. `ExpansionController.max_pending`
    compares against `structure_pending`, which counts a hatchery as
    "pending" for its entire ~71s build time (anything with
    `build_progress < 1.0`), so it stayed blocked practically the whole
    time a macro hatch was under construction. `BuildMacroHatch.max_on_route`
    compares against `not_started_but_in_building_tracker`, a much
    narrower count that clears the instant the drone starts building -
    so once one macro hatch was underway, `ExpansionController` kept
    declining while `BuildMacroHatch` was free to queue another, and
    another, well before the first one even finished. Gating the whole
    step on the same bot-wide `structure_pending` count keeps both
    equally throttled to one hatchery in flight at a time - real or
    macro - so `ExpansionController` (tried first) always gets a fair,
    unblocked shot at the closest safe expansion, and `BuildMacroHatch`
    only ever fires as an actual last resort, on a frame where no legal
    expansion location was available at all.

    Bundled into one `MacroPlan` so that actual fallback still happens on
    the same frame instead of wasting it - `ExpansionController` and
    `BuildMacroHatch` count `to_count` the same way (every townhall, main
    included - see `BuildMacroHatch`'s docstring and ares' own
    `ExpansionController` source), so handing them the same computed
    target keeps them working toward one shared goal.
    """

    def step(ctx: "BotContext"):
        if ctx.bot.minerals < mineral_threshold:
            return None
        if ctx.bot.structure_pending(UnitTypeId.HATCHERY):
            return None

        target = len(ctx.bot.townhalls) + 1

        plan = MacroPlan()
        plan.add(ExpansionController(to_count=target))
        plan.add(BuildMacroHatch(to_count=target))
        return plan

    return step


def evolution_chambers() -> MacroStep:
    """`UpgradeController` only ever builds one; a build wanting more than
    one +1/+1 tier researching in parallel needs another.

    Count and gate both come from `ctx.build.army.evolution_chambers` /
    `.evolution_chamber_gate` rather than being passed in here, so a build
    states them once and `UpgradeRushValidator` can read the exact same
    numbers instead of a second, separately-maintained copy.
    """

    def step(ctx: "BotContext"):
        return common.structure(
            UnitTypeId.EVOLUTIONCHAMBER,
            ctx.build.army.evolution_chambers,
            ctx.build.army.evolution_chamber_gate,
        )(ctx)

    return step


def spore_crawlers(per_base: int, gate: Gate = _always) -> MacroStep:
    """One Spore Crawler (mineral-line placement) per owned base, once `gate`
    passes — capped overall at `per_base * (number of owned townhalls)`.

    Uses `BuildSporeCrawler` rather than `BuildStructure`: `BuildStructure`
    cannot do this job on Zerg at all, for two separate reasons — see that
    behavior's docstring. In short, `to_count_per_base` is a guaranteed
    `KeyError` for a Zerg structure regardless of location (ares never
    populates `placements_dict` for Zerg), and even placement without it
    goes through `ai.request_zerg_placement`, which leaks: a single request
    gets replayed by ares every frame for the rest of the game, piling
    crawlers onto whichever base got requested first — the main, in
    practice, since `owned_expansions` walks it first. `BuildSporeCrawler`
    does placement and worker dispatch directly instead, so one call really
    is one build.

    `EXPANSION_GAP_THRESHOLD` (15) is python-sc2's own radius for "close
    enough to belong to this base" — the same radius `owned_expansions`
    itself uses to match a townhall to its expansion location, so a crawler
    is credited to a base on the same terms a townhall is.

    Two throttles, not one:

    - `ai.structure_pending(SPORECRAWLER)` gates the whole step: a
      dispatched worker walking to build doesn't show up in `structures()`
      until it arrives, so counting only placed structures would re-request
      a build for the same still-"uncovered" base on every frame of that
      walk. `structure_pending` is a combined ready-or-pending count, so
      nothing new is requested anywhere while one crawler is already in
      flight — bot-wide, not just at the base in question, which is a
      stricter throttle than strictly necessary but fine, since only one
      crawler was ever going to get built at a time regardless.
    - The explicit `per_base * len(owned bases)` total cap is a second,
      independent ceiling on top of that: an explicit invariant ("never
      more than one Spore Crawler per townhall") that holds even if the
      per-base loop below ever miscounts a base — e.g. two owned bases
      whose crawlers sit within `radius` of each other and get double
      credited to both.

    Bundled into their own `MacroPlan` so a base that already has enough
    doesn't block the next base's turn on the same frame (see
    `MacroPlan.execute`, which stops at the first behavior that acts).
    """

    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None
        if ctx.bot.structure_pending(UnitTypeId.SPORECRAWLER):
            return None

        bases = list(ctx.bot.owned_expansions)
        existing = ctx.bot.structures(UnitTypeId.SPORECRAWLER)
        if existing.amount >= per_base * len(bases):
            return None

        radius = ctx.bot.EXPANSION_GAP_THRESHOLD
        plan = MacroPlan()
        for location in bases:
            if len(existing.closer_than(radius, location)) >= per_base:
                continue
            plan.add(BuildSporeCrawler(base_location=location))
        return plan

    return step


def spine_crawlers(count: int, gate: Gate = _always) -> MacroStep:
    return common.structure(UnitTypeId.SPINECRAWLER, count, gate)


def overseers(per_wave: int = 1, maximum: int = 3, gate: Gate = _always) -> MacroStep:
    """Keep one Overseer per attack wave released so far, capped at `maximum`.

    `MorphOverseers` handles the Lair-tech check itself, so this step is
    safe to list before Lair even exists. See `routines.combat.escort_overseers`
    for what actually sends them along with the army.
    """

    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None
        target = min(maximum, ctx.state.wave_number * per_wave)
        if target <= 0:
            return None
        return MorphOverseers(to_count=target)

    return step


LING_SPEED: UpgradeId = UpgradeId.ZERGLINGMOVEMENTSPEED
