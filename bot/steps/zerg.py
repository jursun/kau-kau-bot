"""Zerg-specific macro steps.

Anything that mentions a hatchery, a queen or larva belongs here rather than
in `steps/common.py`, so the shared engine stays race-neutral.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ares.behaviors.macro import ExpansionController, MacroPlan, SpawnController, TechUp
from ares.consts import ID, TARGET
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2

from bot.behaviors.zerg import (
    BuildMacroHatch,
    BuildSporeCrawler,
    BuildZergStructure,
    ForwardCrawlerWave,
    InjectLarva,
    MorphOverseers,
    TrainQueens,
)
from bot.builds.definition import _always
from bot.consts import (
    GAS_STARVED_MINERAL_RATIO,
    LING_HEAVY_CORRUPTOR_COMP,
    LING_HEAVY_ROACH_COMP,
    ROACH_LING_COMP,
    ROACH_LING_CORRUPTOR_COMP,
    ROACH_LING_CORRUPTOR_INFESTOR_COMP,
    ROACH_LING_INFESTOR_COMP,
)
from bot.core.types import Gate, MacroStep
from bot.intel.army import early_aggression, enemy_has_air_units
from bot.routines import targeting
from bot.steps import common

if TYPE_CHECKING:
    from bot.core.context import BotContext


def inject_larva(min_energy: int = 25) -> MacroStep:
    """Belongs in `always` — injects must keep running during the opening.

    Each inject queen only targets her home townhall
    (`ctx.state.queen_home_townhall`), so she never walks across the map to
    inject another base.
    """

    def step(ctx: "BotContext"):
        return InjectLarva(
            min_energy=min_energy,
            home_townhall=ctx.state.queen_home_townhall,
        )

    return step


def train_queens(
    per_base: int = 1, maximum: int = 4, extra: int = 0, gate: Gate = _always
) -> MacroStep:
    """Keep one queen per base (for injects), plus `extra` more for defense
    and creep (see `routines.creep.spread_creep`), capped at `maximum` total.

    `TrainQueens.max_per_townhall` only feeds the internal
    `min(to_count, len(townhalls) * max_per_townhall)` target formula — it
    doesn't restrict which townhall trains what — so it's padded by `extra`
    here too, otherwise that formula would silently clip the extra queen
    back off (`base_count * per_base` alone is always < `to_count` once
    `extra` is nonzero).
    """

    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None
        return TrainQueens(
            to_count=min(maximum, ctx.base_count * per_base + extra),
            max_per_townhall=per_base + extra,
            home_townhall=ctx.state.queen_home_townhall,
            cooldown_state=ctx.state.queen_train_cooldown,
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
    states them once and `tests.validators.base_validator.BaseValidator`
    (shared by every build's own validator) can read the exact same numbers
    instead of a second, separately-maintained copy.
    """

    def step(ctx: "BotContext"):
        return common.structure(
            UnitTypeId.EVOLUTIONCHAMBER,
            ctx.build.army.evolution_chambers,
            ctx.build.army.evolution_chamber_gate,
        )(ctx)

    return step


def _crawlers_en_route_near(
    ctx: "BotContext", location: Point2, radius: float, structure_type: UnitTypeId
) -> int:
    """How many crawler workers are dispatched toward `location` (see
    `_spore_crawlers_en_route_near` history — must be per-base, not bot-wide).
    """
    count = 0
    for info in ctx.mediator.get_building_tracker_dict.values():
        if info.get(ID) != structure_type:
            continue
        target = info.get(TARGET)
        if target is None:
            continue
        pos = getattr(target, "position", target)
        if pos.distance_to(location) < radius:
            count += 1
    return count


def _spore_crawlers_en_route_near(ctx: "BotContext", location: Point2, radius: float) -> int:
    """Spore-specific wrapper — kept for existing call sites / tests."""
    return _crawlers_en_route_near(ctx, location, radius, UnitTypeId.SPORECRAWLER)


def _expansion_bases_for_spines(ctx: "BotContext") -> list[Point2]:
    """Owned expansions excluding main and natural (3rd base and up)."""
    radius = ctx.bot.EXPANSION_GAP_THRESHOLD
    skip: list[Point2] = [ctx.bot.start_location]
    try:
        nat = ctx.own_nat
    except Exception:  # noqa: BLE001 - mediator may not be ready
        nat = None
    if nat is not None:
        skip.append(nat)
    return [
        loc
        for loc in ctx.bot.owned_expansions
        if all(loc.distance_to(anchor) >= radius for anchor in skip)
    ]


def spore_crawlers(
    per_base: int,
    gate: Gate = _always,
    check_interval: float = 0.0,
) -> MacroStep:
    """One Spore Crawler (mineral-line placement) per owned base, once `gate`
    passes — capped overall at `per_base * (number of owned townhalls)`.

    Same maintenance shape as `train_queens`: keep the count topped up for
    every base that exists, for the rest of the game. Optional
    `check_interval` (seconds) throttles how often the missing-base scan
    runs — Macro Zerg uses 15s after 4:30 so this does not fight larva
    production every frame, but still rebuilds a destroyed crawler.

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

    - `_spore_crawlers_en_route_near` gates each base individually: a
      dispatched worker walking to build doesn't show up in `structures()`
      until it arrives, so counting only placed structures would re-request
      a build for the same still-"uncovered" base on every frame of that
      walk - see that function's own docstring for why this must be
      per-base rather than the bot-wide `ai.structure_pending(SPORECRAWLER)`
      an earlier version used. That bot-wide gate blocked every *other*
      base's turn behind whichever one worker was already walking, for that
      worker's entire ~20s+ walk-and-build time - confirmed live as the
      actual cause of "3 Spore Crawlers" consistently missing its deadline
      across maps (3 bases built strictly one after another took ~88s
      against a 60s window, not a minerals problem).
    - The explicit `per_base * len(owned bases)` total cap is a second,
      independent ceiling on top of that: an explicit invariant ("never
      more than one Spore Crawler per townhall") that holds even if the
      per-base loop below ever miscounts a base — e.g. two owned bases
      whose crawlers sit within `radius` of each other and get double
      credited to both.

    Bundled into their own `MacroPlan` so a base that already has enough
    (or already has a worker en route) doesn't block a *different* base's
    turn on the same frame (see `MacroPlan.execute`, which stops at the
    first behavior that acts - so only one dispatch actually happens per
    frame regardless, but which base gets first refusal now rotates
    instead of always re-trying whichever base is first in `owned_
    expansions` until its own worker finally arrives).
    """

    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None

        if check_interval > 0:
            last = ctx.state.last_spore_check_at
            if last is not None and ctx.bot.time - last < check_interval:
                return None
            ctx.state.last_spore_check_at = ctx.bot.time

        bases = list(ctx.bot.owned_expansions)
        existing = ctx.bot.structures(UnitTypeId.SPORECRAWLER)
        if existing.amount >= per_base * len(bases):
            return None

        radius = ctx.bot.EXPANSION_GAP_THRESHOLD
        plan = MacroPlan()
        for location in bases:
            have = len(existing.closer_than(radius, location))
            have += _spore_crawlers_en_route_near(ctx, location, radius)
            if have >= per_base:
                continue
            plan.add(BuildSporeCrawler(base_location=location))
        if plan.macros:
            from bot.common.log import log_event

            log_event(
                ctx.bot,
                f"SPORE maintain: {len(plan.macros)} base(s) missing "
                f"(have {existing.amount}/{per_base * len(bases)})",
            )
            return plan
        return None

    return step


def spine_crawlers(
    per_base: int = 1,
    gate: Gate = _always,
    check_interval: float = 0.0,
) -> MacroStep:
    """One Spine Crawler per expansion (3rd base and up), once `gate` passes.

    Same maintenance shape as `spore_crawlers` / `BuildSporeCrawler`, but
    skips main and natural — those stay covered by the early-aggression
    overlay (`early_aggression_spines`) and army, not a permanent mineral-
    line Spine. Optional `check_interval` throttles the missing-base scan
    the same way spores do.

    Early-aggression Spines at the natural are intentionally *not* counted
    toward the bot-wide cap here (unlike spores): a pair at the nat would
    otherwise look like the expansion quota was already filled and block
    every 3rd+ base.
    """

    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None

        if check_interval > 0:
            last = ctx.state.last_spine_check_at
            if last is not None and ctx.bot.time - last < check_interval:
                return None
            ctx.state.last_spine_check_at = ctx.bot.time

        bases = _expansion_bases_for_spines(ctx)
        if not bases:
            return None

        existing = ctx.bot.structures(UnitTypeId.SPINECRAWLER)
        radius = ctx.bot.EXPANSION_GAP_THRESHOLD
        plan = MacroPlan()
        for location in bases:
            have = len(existing.closer_than(radius, location))
            have += _crawlers_en_route_near(
                ctx, location, radius, UnitTypeId.SPINECRAWLER
            )
            if have >= per_base:
                continue
            plan.add(
                BuildSporeCrawler(
                    base_location=location,
                    structure_type=UnitTypeId.SPINECRAWLER,
                )
            )
        if plan.macros:
            from bot.common.log import log_event

            log_event(
                ctx.bot,
                f"SPINE maintain: {len(plan.macros)} expansion(s) missing "
                f"(3rd+ bases={len(bases)})",
            )
            return plan
        return None

    return step


def early_aggression_spines(count: int, gate: Gate = _always) -> MacroStep:
    """Fixed-count Spines at the natural while early-aggression is on.

    Uses `BuildSporeCrawler` (spine placement toward the front of the base)
    at `ctx.own_nat`, not `common.structure` — that path places at the main
    production location and left the nat undefended (confirmed: comment in
    Macro Zerg already said natural; implementation did not).
    """

    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None
        natural = ctx.own_nat
        radius = ctx.bot.EXPANSION_GAP_THRESHOLD
        existing = ctx.bot.structures(UnitTypeId.SPINECRAWLER)
        have = len(existing.closer_than(radius, natural))
        have += _crawlers_en_route_near(
            ctx, natural, radius, UnitTypeId.SPINECRAWLER
        )
        if have >= count:
            return None
        # One at a time — queuing `count - have` in one plan made both
        # BuildSporeCrawler calls pick the same pad before the tracker
        # registered the first (confirmed live: 2nd spine on 1st's tile).
        plan = MacroPlan()
        plan.add(
            BuildSporeCrawler(
                base_location=natural,
                structure_type=UnitTypeId.SPINECRAWLER,
            )
        )
        from bot.common.log import log_event

        log_event(
            ctx.bot,
            f"SPINE early-aggression: {count - have} at natural "
            f"(have {have}/{count})",
        )
        return plan

    return step


_FORWARD_CRAWLER_MINERALS: int = 5000
_FORWARD_CRAWLER_INTERVAL: float = 30.0
_FORWARD_CRAWLER_WAVE: tuple[UnitTypeId, ...] = (
    UnitTypeId.SPINECRAWLER,
    UnitTypeId.SPINECRAWLER,
    UnitTypeId.SPINECRAWLER,
    UnitTypeId.SPORECRAWLER,
    UnitTypeId.SPORECRAWLER,
    UnitTypeId.SPORECRAWLER,
)


def _army_forward_anchor(ctx: "BotContext"):
    """Largest ATTACKING squad position, else attack destination from home."""
    from ares.consts import UnitRole

    # Match `routines.combat.SQUAD_RADIUS` (local to avoid import cycle).
    squad_radius = 9.0
    squads = ctx.mediator.get_squads(
        role=UnitRole.ATTACKING, squad_radius=squad_radius
    )
    if squads:
        biggest = max(squads, key=lambda squad: len(squad.squad_units))
        return biggest.squad_position
    return targeting.attack_target(ctx, ctx.production_location)


def forward_crawler_wave(gate: Gate = _always) -> MacroStep:
    """When floating >5000 minerals, every 30s pull 6 workers to plant
    3 Spines + 3 Spores beside the army (mineral sink / forward static).

    Placement needs creep — if the army is off creep the wave no-ops and
    the interval still advances so we do not spam failed placement every
    frame.
    """

    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None
        if ctx.bot.minerals <= _FORWARD_CRAWLER_MINERALS:
            return None
        last = ctx.state.last_forward_crawler_wave_at
        if last is not None and ctx.bot.time - last < _FORWARD_CRAWLER_INTERVAL:
            return None

        anchor = _army_forward_anchor(ctx)
        ctx.state.last_forward_crawler_wave_at = ctx.bot.time
        from bot.common.log import log_event

        log_event(
            ctx.bot,
            "FORWARD_CRAWLER wave: 3 Spine + 3 Spore beside army "
            f"(minerals={ctx.bot.minerals})",
        )
        return ForwardCrawlerWave(
            anchor=anchor, structure_types=_FORWARD_CRAWLER_WAVE
        )

    return step


def overseers(
    per_wave: int = 1,
    maximum: int = 3,
    gate: Gate = _always,
    to_count: int | None = None,
) -> MacroStep:
    """Morph Overlords into Overseers.

    With `to_count`, maintain that many at all times (Macro Zerg: 3 after
    Lair). Otherwise keep one per attack wave released so far, capped at
    `maximum` (legacy wave-scaled path).

    `MorphOverseers` handles the Lair-tech check itself. See
    `routines.overseers.manage_overseers` for home/army/scout roles.
    """

    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None
        if to_count is not None:
            target = to_count
        else:
            target = min(maximum, ctx.state.wave_number * per_wave)
            if target <= 0:
                return None
        return MorphOverseers(to_count=target)

    return step


def tech_up(desired_tech: UnitTypeId, gate: Gate = _always) -> MacroStep:
    """Tech toward a structure whose prerequisite chain needs Lair/Hive.

    Hatchery→Lair / Lair→Hive morphs still go through ares `TechUp`.
    Placeable buildings (Spire, Infestation Pit, …) use `BuildZergStructure`
    instead of ares `BuildStructure`: the Zerg path of `BuildStructure` is
    `request_zerg_placement`, which leaks forever and parks Drones on
    unreachable spots — confirmed live for Spire (commanded twice vs
    Voidrays, never started, Drone stuck in main).
    """

    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None
        if desired_tech in {UnitTypeId.LAIR, UnitTypeId.HIVE}:
            return TechUp(
                desired_tech=desired_tech, base_location=ctx.production_location
            )
        return BuildZergStructure(
            base_location=ctx.production_location,
            structure_id=desired_tech,
            to_count=1,
        )

    return step



def spawn_macro_army(gate: Gate = _always) -> MacroStep:
    """Roach/Zergling by default (see `bot.consts.ROACH_LING_COMP`).

    Once Spire is up and the enemy has shown air (`intel.army.enemy_has_air_units`),
    fold Corruptor in. When mineral:gas > `GAS_STARVED_MINERAL_RATIO` (5:1),
    flip to the ling-heavy comps so larva spends on Zerglings instead of Roaches.

    Once Infestation Pit is ready - late game only, it's gated behind
    Tunneling Claws (see `macro_zerg.py`'s tech_up gate) - fold Infestor in
    too, same "at Roach's expense" pattern as Corruptor. Skipped while
    gas-starved: see `bot.consts.ROACH_LING_INFESTOR_COMP`'s own comment for
    why an already-gas-hungry unit has no place in that comp.

    TODO: scout-based counter composition (see intel.army composition).
    """

    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None
        if early_aggression(ctx):
            roach_ready = (
                ctx.bot.tech_requirement_progress(UnitTypeId.ROACH) >= 1.0
            )
            if roach_ready:
                return SpawnController(dict(LING_HEAVY_ROACH_COMP), spawn_target=None)
            return SpawnController(
                {UnitTypeId.ZERGLING: {"proportion": 1.0, "priority": 0}},
                spawn_target=None,
            )
        gas_starved = _gas_starved(ctx)
        air = (
            ctx.bot.structures(UnitTypeId.SPIRE).ready
            and enemy_has_air_units(ctx)
        )
        infestor_ready = bool(ctx.bot.structures(UnitTypeId.INFESTATIONPIT).ready)
        if gas_starved:
            comp = LING_HEAVY_CORRUPTOR_COMP if air else LING_HEAVY_ROACH_COMP
        elif air:
            comp = (
                ROACH_LING_CORRUPTOR_INFESTOR_COMP
                if infestor_ready
                else ROACH_LING_CORRUPTOR_COMP
            )
        else:
            comp = ROACH_LING_INFESTOR_COMP if infestor_ready else ROACH_LING_COMP
        return SpawnController(dict(comp), spawn_target=None)

    return step


def _gas_starved(ctx: "BotContext") -> bool:
    """True when minerals:gas exceeds 5:1 (or gas is empty with minerals)."""
    minerals = float(ctx.bot.minerals)
    gas = float(ctx.bot.vespene)
    if gas <= 0:
        return minerals > 0
    return minerals / gas > GAS_STARVED_MINERAL_RATIO


LING_SPEED: UpgradeId = UpgradeId.ZERGLINGMOVEMENTSPEED
