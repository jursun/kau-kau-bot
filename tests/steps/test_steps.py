"""Regression tests for the composed macro steps that are hard to eyeball:
`common.split_production`, `zerg.spore_crawlers`, `zerg.spine_crawlers`,
`zerg.train_queens` and `zerg.overseers`. The crawler/`split_production`
cases lean on `MacroPlan.execute()`'s "stop at the first behavior that acts"
semantics (see `macro_engine.py`), so what matters is the *order* and
*shape* of the behaviors each step hands back, not just that it returns
something.

Runs under pytest, or standalone with no test dependency:

    python -m tests.steps.test_steps
"""

from __future__ import annotations

import sys

import pytest
from unittest.mock import MagicMock, patch

from ares.behaviors.macro import (
    BuildStructure,
    BuildWorkers,
    ExpansionController,
    MacroPlan,
    SpawnController,
    UpgradeController,
)
from ares.consts import ID, TARGET, UnitRole
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2

from bot.behaviors.zerg import (
    BuildMacroHatch,
    BuildSporeCrawler,
    MorphOverseers,
    TrainQueens,
)
from bot.core.context import BotContext
from bot.core.state import RunState
from bot.steps import common as c
from bot.steps import zerg as z


def _ctx(supply_workers: float = 10.0, supply_army: float = 10.0) -> BotContext:
    bot = MagicMock()
    bot.supply_workers = supply_workers
    bot.supply_army = supply_army
    bot.townhalls.ready = []  # base_count -> 1, so worker_target is well-defined
    bot.EXPANSION_GAP_THRESHOLD = 15  # real python-sc2 BotAI constant
    bot.owned_expansions = {}
    # No spore crawlers anywhere, and none in flight, by default; tests
    # override either per case.
    bot.structures.return_value.amount = 0
    bot.structures.return_value.closer_than.return_value = []
    bot.structure_pending.return_value = 0
    bot.mediator.get_building_tracker_dict = {}

    build = MagicMock()
    build.economy.worker_target = 60
    build.economy.workers_per_base = 22
    build.army.comp = {UnitTypeId.ZERGLING: {"proportion": 1.0, "priority": 0}}

    return BotContext(bot=bot, build=build, state=RunState())


# --- common.upgrades --------------------------------------------------


def test_upgrades_step_defaults_to_not_prioritizing_spend() -> None:
    ctx = _ctx()
    ctx.build.army.upgrades = (UpgradeId.GLIALRECONSTITUTION,)

    controller = c.upgrades()(ctx)

    assert isinstance(controller, UpgradeController)
    assert controller.prioritize is False


def test_upgrades_step_forwards_prioritize_to_upgrade_controller() -> None:
    """Regression test: `prioritize=True` must reach the real
    `UpgradeController`, not just get accepted and dropped - see
    `common.upgrades`'s own docstring for why a build needs this to stop a
    cheap, frequent purchase lower in `macro_steps` (Roach production)
    from perpetually draining the bank an expensive upgrade needs."""
    ctx = _ctx()
    ctx.build.army.upgrades = (UpgradeId.GLIALRECONSTITUTION,)

    controller = c.upgrades(prioritize=True)(ctx)

    assert isinstance(controller, UpgradeController)
    assert controller.prioritize is True
    assert controller.upgrade_list == [UpgradeId.GLIALRECONSTITUTION]


def test_split_production_favors_economy_before_gate() -> None:
    # Army is way ahead of economy, but the gate hasn't opened yet — should
    # still behave like the old unconditional build_workers-then-spawn_army.
    ctx = _ctx(supply_workers=10.0, supply_army=40.0)
    plan = c.split_production(gate=lambda _ctx: False)(ctx)

    assert isinstance(plan, MacroPlan)
    assert isinstance(plan.macros[0], BuildWorkers)
    assert isinstance(plan.macros[1], SpawnController)


def test_split_production_prioritizes_army_once_economy_hits_target() -> None:
    # _ctx() gives worker_target=min(60, 22*1)=22 (see base_count->1 above).
    economy_maxed = _ctx(supply_workers=22.0, supply_army=5.0)
    plan = c.split_production(gate=lambda _ctx: True)(economy_maxed)
    assert isinstance(plan.macros[0], SpawnController), "army should go first"
    assert isinstance(plan.macros[1], BuildWorkers)

    economy_short = _ctx(supply_workers=10.0, supply_army=40.0)
    plan = c.split_production(gate=lambda _ctx: True)(economy_short)
    assert isinstance(plan.macros[0], BuildWorkers), "economy should go first"
    assert isinstance(plan.macros[1], SpawnController)


def test_split_production_compares_workers_to_their_own_target_not_army_supply() -> (
    None
):
    """Regression test: the old check compared raw `supply_army` against raw
    `supply_workers`, which skews toward the army since a zergling costs half
    a worker's supply - so a wave of cheap lings could out-race worker supply
    and grab priority while economy was still short of its own target. This
    asserts economy keeps priority in exactly that scenario."""
    ctx = _ctx(supply_workers=20.0, supply_army=15.0)  # target is 22 - not maxed
    plan = c.split_production(gate=lambda _ctx: True)(ctx)
    assert isinstance(plan.macros[0], BuildWorkers), (
        "economy hasn't hit its target yet, so it should go first even "
        "though supply_army < supply_workers"
    )
    assert isinstance(plan.macros[1], SpawnController)


def test_split_production_never_drops_either_side() -> None:
    """Whichever order, both behaviors must still be present — a frame where
    the leading side can't act (e.g. workers at cap) must fall through."""
    ctx = _ctx(supply_workers=10.0, supply_army=40.0)
    plan = c.split_production(gate=lambda _ctx: True)(ctx)
    kinds = {type(behavior) for behavior in plan.macros}
    assert kinds == {BuildWorkers, SpawnController}


def test_overflow_hatcheries_does_nothing_below_the_mineral_threshold() -> None:
    ctx = _ctx()
    ctx.bot.minerals = 499

    assert z.overflow_hatcheries(mineral_threshold=500)(ctx) is None


def test_overflow_hatcheries_does_nothing_while_a_hatchery_is_already_pending() -> None:
    """Regression test for the actual "went overboard with macro hatches"
    bug: `ExpansionController.max_pending` and `BuildMacroHatch.max_on_route`
    read the bot-wide pending-hatchery count very differently (the former
    stays blocked for a hatchery's whole ~71s build time, the latter clears
    the instant the drone starts building), so a macro hatch under
    construction silently starved out `ExpansionController` while letting
    `BuildMacroHatch` queue more macro hatches back to back. Gating the
    whole step on `structure_pending(HATCHERY)` - the same broad count
    `ExpansionController` itself uses - throttles both to one hatchery in
    flight at a time, whichever kind it is."""
    ctx = _ctx()
    ctx.bot.minerals = 600
    ctx.bot.structure_pending.return_value = 1  # a hatchery is already going up

    assert z.overflow_hatcheries(mineral_threshold=500)(ctx) is None
    ctx.bot.structure_pending.assert_called_with(UnitTypeId.HATCHERY)


def test_overflow_hatcheries_prefers_expansion_targeting_one_more_than_current() -> (
    None
):
    """Once nothing is pending, both `ExpansionController` and
    `BuildMacroHatch` are handed the same target - current townhalls plus
    one - with `ExpansionController` listed first so a real expansion is
    always attempted before the macro hatch fallback; whichever can
    actually act takes the next hatchery, the other falls through on the
    same frame (`MacroPlan.execute`'s "stop at the first that acts")."""
    ctx = _ctx()
    ctx.bot.minerals = 600
    ctx.bot.townhalls = [MagicMock(), MagicMock(), MagicMock()]  # 3 existing
    ctx.bot.structure_pending.return_value = 0  # nothing already going up

    plan = z.overflow_hatcheries(mineral_threshold=500)(ctx)

    assert isinstance(plan, MacroPlan)
    expansion, macro_hatch = plan.macros
    assert isinstance(expansion, ExpansionController)
    assert isinstance(macro_hatch, BuildMacroHatch)
    assert expansion.to_count == 4  # 3 existing + 1
    assert macro_hatch.to_count == 4


def test_evolution_chambers_reads_count_and_gate_from_the_build() -> None:
    """Regression test: `evolution_chambers()` used to take `count`/`gate`
    as its own params, duplicating the same numbers the validator needed to
    know separately. Both now come from `ctx.build.army` — the single
    source of truth for a per-build target count and gate."""
    ctx = _ctx()
    ctx.build.army.evolution_chambers = 2
    ctx.build.army.evolution_chamber_gate = lambda _ctx: True

    behavior = z.evolution_chambers()(ctx)

    assert isinstance(behavior, BuildStructure)
    assert behavior.structure_id == UnitTypeId.EVOLUTIONCHAMBER
    assert behavior.to_count == 2


def test_evolution_chambers_returns_none_before_its_build_gate() -> None:
    ctx = _ctx()
    ctx.build.army.evolution_chambers = 2
    ctx.build.army.evolution_chamber_gate = lambda _ctx: False

    assert z.evolution_chambers()(ctx) is None


def test_spore_crawlers_places_one_per_owned_expansion() -> None:
    # Regression test: `BuildStructure` cannot be used here at all (two
    # separate reasons — see `BuildSporeCrawler`'s docstring), so this
    # dispatches its own placement/worker behavior per uncovered base.
    ctx = _ctx()
    main = Point2((10.0, 10.0))
    natural = Point2((50.0, 50.0))
    ctx.bot.owned_expansions = {main: MagicMock(), natural: MagicMock()}

    plan = z.spore_crawlers(per_base=1, gate=lambda _ctx: True)(ctx)

    assert isinstance(plan, MacroPlan)
    assert len(plan.macros) == 2
    for behavior, location in zip(plan.macros, (main, natural)):
        assert isinstance(behavior, BuildSporeCrawler)
        assert behavior.base_location == location


def test_spore_crawlers_skips_bases_that_already_have_enough() -> None:
    ctx = _ctx()
    covered = Point2((10.0, 10.0))
    uncovered = Point2((50.0, 50.0))
    ctx.bot.owned_expansions = {covered: MagicMock(), uncovered: MagicMock()}
    ctx.bot.structures.return_value.amount = 1
    ctx.bot.structures.return_value.closer_than.side_effect = (
        lambda _radius, location: ([MagicMock()] if location == covered else [])
    )

    plan = z.spore_crawlers(per_base=1, gate=lambda _ctx: True)(ctx)

    assert len(plan.macros) == 1
    assert plan.macros[0].base_location == uncovered


def test_spore_crawlers_caps_at_one_per_owned_townhall() -> None:
    """Regression test for "cap Spore Crawlers to the number of townhalls":
    an explicit total-count ceiling independent of the per-base loop, so it
    holds even if per-base counting (radius-based) ever double-credits a
    crawler to two nearby bases."""
    ctx = _ctx()
    ctx.bot.owned_expansions = {
        Point2((10.0, 10.0)): MagicMock(),
        Point2((50.0, 50.0)): MagicMock(),
    }
    ctx.bot.structures.return_value.amount = 2  # already at the 2-base cap

    assert z.spore_crawlers(per_base=1, gate=lambda _ctx: True)(ctx) is None


def test_spore_crawlers_waits_on_the_base_a_worker_is_already_en_route_to() -> None:
    """Regression test: a worker already dispatched to build a spore
    crawler doesn't show up in `structures()` until it actually arrives,
    which can take several seconds of walking. Without checking the
    building tracker too, every frame in that window re-requests a build
    for the same still-"uncovered" base - a real game piled several
    crawlers onto one base this way.

    Per-base, not bot-wide (`ai.structure_pending`) - see `_spore_crawlers_
    en_route_near`'s own docstring for why a bot-wide gate here is itself a
    live-confirmed bug: it blocks every *other* base's turn behind
    whichever one worker is already walking, for that worker's entire
    ~20s+ trip, which alone blew the "3 Spore Crawlers" deadline out to
    ~88s for 3 bases against a 60s window.
    """
    ctx = _ctx()
    base = Point2((10.0, 10.0))
    ctx.bot.owned_expansions = {base: MagicMock()}
    ctx.bot.mediator.get_building_tracker_dict = {
        999: {ID: UnitTypeId.SPORECRAWLER, TARGET: base}
    }

    assert z.spore_crawlers(per_base=1, gate=lambda _ctx: True)(ctx) is None


def _stuck_ctx(worker_at: Point2, site: Point2, afford: bool = True) -> BotContext:
    ctx = _ctx()
    worker = MagicMock()
    worker.position = worker_at
    ctx.bot.unit_tag_dict = {999: worker}
    ctx.bot.can_afford.return_value = afford
    ctx.bot.time = 100.0
    ctx.bot.mediator = ctx.mediator
    ctx.mediator.get_building_tracker_dict = {
        999: {ID: UnitTypeId.SPORECRAWLER, TARGET: site}
    }
    ctx.mediator.get_building_counter = {UnitTypeId.SPORECRAWLER: 1}
    return ctx


def test_release_stuck_crawlers_frees_a_drone_that_stops_making_progress() -> None:
    """The late Spore: a drone that can't reach its tile used to sit in the
    tracker for ares' 120s timeout, and the step counted it as covering the
    base the whole time. After ~10s without progress it is released, its
    site is blacklisted, and the base becomes 'missing' again."""
    site = Point2((50.0, 50.0))
    ctx = _stuck_ctx(Point2((20.0, 50.0)), site)

    z.release_stuck_crawlers(ctx)  # first sighting: starts the clock
    assert 999 in ctx.mediator.get_building_tracker_dict

    ctx.bot.time = 100.0 + z.CRAWLER_STUCK_S - 1.0
    z.release_stuck_crawlers(ctx)
    assert 999 in ctx.mediator.get_building_tracker_dict  # not yet

    ctx.bot.time = 100.0 + z.CRAWLER_STUCK_S + 0.5
    z.release_stuck_crawlers(ctx)

    assert 999 not in ctx.mediator.get_building_tracker_dict
    assert (50.0, 50.0) in ctx.state.bad_crawler_tiles
    assert ctx.mediator.get_building_counter[UnitTypeId.SPORECRAWLER] == 0
    ctx.mediator.assign_role.assert_called_once_with(tag=999, role=UnitRole.GATHERING)


def test_release_stuck_crawlers_leaves_a_drone_that_keeps_walking() -> None:
    """Progress-based, so a long walk across the map is never cut short."""
    site = Point2((90.0, 50.0))
    ctx = _stuck_ctx(Point2((10.0, 50.0)), site)
    worker = ctx.bot.unit_tag_dict[999]

    z.release_stuck_crawlers(ctx)
    for step in range(1, 12):  # 30s of walking, 3 tiles per 2.5s
        ctx.bot.time = 100.0 + step * 2.5
        worker.position = Point2((10.0 + step * 3.0, 50.0))
        z.release_stuck_crawlers(ctx)

    assert 999 in ctx.mediator.get_building_tracker_dict
    assert not ctx.state.bad_crawler_tiles


def test_release_stuck_crawlers_waits_for_minerals_at_the_site() -> None:
    """A drone standing on its tile only because the crawler is momentarily
    unaffordable is not stuck."""
    site = Point2((50.0, 50.0))
    ctx = _stuck_ctx(Point2((50.0, 50.5)), site, afford=False)

    z.release_stuck_crawlers(ctx)
    ctx.bot.time = 100.0 + 3 * z.CRAWLER_STUCK_S
    z.release_stuck_crawlers(ctx)

    assert 999 in ctx.mediator.get_building_tracker_dict
    assert not ctx.state.bad_crawler_tiles


def test_release_stuck_crawlers_reports_the_sc2_build_error() -> None:
    """Drones idle on their site with a full bank: the game's own error code
    is what says why, so it goes in the stuck log."""
    from types import SimpleNamespace

    from sc2.data import ActionResult

    site = Point2((50.0, 50.0))
    ctx = _stuck_ctx(Point2((50.0, 50.0)), site)
    ctx.bot.state.action_errors = [
        SimpleNamespace(
            ability_id=AbilityId.ZERGBUILD_SPORECRAWLER.value,
            unit_tag=999,
            result=ActionResult.CantBuildLocationInvalid.value,
        ),
        SimpleNamespace(  # not a crawler build: ignored
            ability_id=AbilityId.ATTACK.value, unit_tag=999, result=1
        ),
    ]

    z.release_stuck_crawlers(ctx)

    assert ctx.state.crawler_last_error == {999: "CantBuildLocationInvalid"}


def test_forward_crawler_wave_passes_stuck_sites_to_the_wave() -> None:
    ctx = _forward_ctx(creep_until=60.0)
    ctx.state.bad_crawler_tiles.add((72.0, 90.0))

    wave = _with_creep(ctx, lambda: z.forward_crawler_wave()(ctx))

    assert wave.avoid_tiles == frozenset({(72.0, 90.0)})


def test_release_stuck_crawlers_ignores_other_builders() -> None:
    site = Point2((50.0, 50.0))
    ctx = _stuck_ctx(Point2((20.0, 50.0)), site)
    ctx.mediator.get_building_tracker_dict = {
        999: {ID: UnitTypeId.EXTRACTOR, TARGET: site}
    }

    z.release_stuck_crawlers(ctx)
    ctx.bot.time = 500.0
    z.release_stuck_crawlers(ctx)

    assert 999 in ctx.mediator.get_building_tracker_dict


def test_spore_crawlers_passes_stuck_sites_to_the_builder() -> None:
    ctx = _ctx()
    base = Point2((10.0, 10.0))
    ctx.bot.owned_expansions = {base: MagicMock()}
    ctx.state.bad_crawler_tiles.add((12.0, 11.0))

    plan = z.spore_crawlers(per_base=1, gate=lambda _ctx: True)(ctx)

    assert plan.macros[0].avoid_tiles == frozenset({(12.0, 11.0)})


def test_spore_crawlers_does_not_wait_on_a_different_bases_en_route_worker() -> None:
    """The per-base fix's whole point: one base's in-flight worker must not
    block a *different* base's turn."""
    ctx = _ctx()
    covered = Point2((10.0, 10.0))
    missing = Point2((200.0, 200.0))
    ctx.bot.owned_expansions = {covered: MagicMock(), missing: MagicMock()}
    ctx.bot.mediator.get_building_tracker_dict = {
        999: {ID: UnitTypeId.SPORECRAWLER, TARGET: covered}
    }

    plan = z.spore_crawlers(per_base=1, gate=lambda _ctx: True)(ctx)

    assert isinstance(plan, MacroPlan)
    assert len(plan.macros) == 1
    assert plan.macros[0].base_location == missing


def test_spore_crawlers_collapses_a_macro_hatch_onto_its_base() -> None:
    """Two hatcheries at the same expansion location (main + a macro hatch)
    must not produce two BuildSporeCrawler calls for that base."""
    ctx = _ctx()
    main = Point2((10.0, 10.0))
    ctx.bot.owned_expansions = {main: MagicMock()}  # owned_expansions already
    # collapses same-location townhalls to one entry — this just documents
    # that spore_crawlers relies on that rather than counting townhalls itself.

    plan = z.spore_crawlers(per_base=1, gate=lambda _ctx: True)(ctx)

    assert len(plan.macros) == 1


def test_spore_crawlers_returns_none_before_gate() -> None:
    ctx = _ctx()
    ctx.bot.owned_expansions = {Point2((10.0, 10.0)): MagicMock()}
    assert z.spore_crawlers(per_base=1, gate=lambda _ctx: False)(ctx) is None


def test_spore_crawlers_check_interval_skips_until_due() -> None:
    """Macro Zerg's 15s recheck must not re-scan every frame."""
    ctx = _ctx()
    ctx.bot.time = 270.0
    ctx.bot.owned_expansions = {Point2((10.0, 10.0)): MagicMock()}
    ctx.state.last_spore_check_at = 260.0  # 10s ago, interval 15

    assert (
        z.spore_crawlers(per_base=1, gate=lambda _ctx: True, check_interval=15.0)(ctx)
        is None
    )
    assert ctx.state.last_spore_check_at == 260.0


def test_spore_crawlers_check_interval_runs_when_due() -> None:
    ctx = _ctx()
    ctx.bot.time = 275.0
    main = Point2((10.0, 10.0))
    ctx.bot.owned_expansions = {main: MagicMock()}
    ctx.state.last_spore_check_at = 260.0  # 15s ago

    plan = z.spore_crawlers(per_base=1, gate=lambda _ctx: True, check_interval=15.0)(
        ctx
    )

    assert isinstance(plan, MacroPlan)
    assert ctx.state.last_spore_check_at == 275.0


def test_spine_crawlers_places_one_per_expansion_skipping_main_and_nat() -> None:
    ctx = _ctx()
    main = Point2((10.0, 10.0))
    natural = Point2((50.0, 50.0))
    third = Point2((200.0, 200.0))
    fourth = Point2((300.0, 300.0))
    ctx.bot.start_location = main
    ctx.mediator.get_own_expansions = [natural]
    ctx.mediator.get_own_nat = natural
    ctx.bot.owned_expansions = {
        main: MagicMock(),
        natural: MagicMock(),
        third: MagicMock(),
        fourth: MagicMock(),
    }

    plan = z.spine_crawlers(per_base=1, gate=lambda _ctx: True)(ctx)

    assert isinstance(plan, MacroPlan)
    assert len(plan.macros) == 2
    for behavior, location in zip(plan.macros, (third, fourth)):
        assert isinstance(behavior, BuildSporeCrawler)
        assert behavior.base_location == location
        assert behavior.structure_type == UnitTypeId.SPINECRAWLER


def test_spine_crawlers_noop_with_only_main_and_natural() -> None:
    ctx = _ctx()
    main = Point2((10.0, 10.0))
    natural = Point2((50.0, 50.0))
    ctx.bot.start_location = main
    ctx.mediator.get_own_expansions = [natural]
    ctx.mediator.get_own_nat = natural
    ctx.bot.owned_expansions = {main: MagicMock(), natural: MagicMock()}

    assert z.spine_crawlers(per_base=1, gate=lambda _ctx: True)(ctx) is None


def test_spine_crawlers_skips_bases_that_already_have_enough() -> None:
    ctx = _ctx()
    main = Point2((10.0, 10.0))
    natural = Point2((50.0, 50.0))
    covered = Point2((200.0, 200.0))
    uncovered = Point2((300.0, 300.0))
    ctx.bot.start_location = main
    ctx.mediator.get_own_expansions = [natural]
    ctx.mediator.get_own_nat = natural
    ctx.bot.owned_expansions = {
        main: MagicMock(),
        natural: MagicMock(),
        covered: MagicMock(),
        uncovered: MagicMock(),
    }
    ctx.bot.structures.return_value.closer_than.side_effect = (
        lambda _radius, location: ([MagicMock()] if location == covered else [])
    )

    plan = z.spine_crawlers(per_base=1, gate=lambda _ctx: True)(ctx)

    assert len(plan.macros) == 1
    assert plan.macros[0].base_location == uncovered
    assert plan.macros[0].structure_type == UnitTypeId.SPINECRAWLER


def test_spine_crawlers_ignores_early_aggression_spines_at_natural() -> None:
    """Nat spines from early_aggression_spines must not satisfy the 3rd+
    quota — otherwise two nat spines would block every expansion Spine."""
    ctx = _ctx()
    main = Point2((10.0, 10.0))
    natural = Point2((50.0, 50.0))
    third = Point2((200.0, 200.0))
    ctx.bot.start_location = main
    ctx.mediator.get_own_expansions = [natural]
    ctx.mediator.get_own_nat = natural
    ctx.bot.owned_expansions = {
        main: MagicMock(),
        natural: MagicMock(),
        third: MagicMock(),
    }
    # Two spines already exist somewhere (e.g. natural) — bot-wide amount
    # must not short-circuit expansion placement.
    ctx.bot.structures.return_value.amount = 2
    ctx.bot.structures.return_value.closer_than.side_effect = (
        lambda _radius, location: (
            [MagicMock(), MagicMock()] if location == natural else []
        )
    )

    plan = z.spine_crawlers(per_base=1, gate=lambda _ctx: True)(ctx)

    assert isinstance(plan, MacroPlan)
    assert len(plan.macros) == 1
    assert plan.macros[0].base_location == third


def test_spine_crawlers_waits_on_en_route_worker() -> None:
    ctx = _ctx()
    main = Point2((10.0, 10.0))
    natural = Point2((50.0, 50.0))
    third = Point2((200.0, 200.0))
    ctx.bot.start_location = main
    ctx.mediator.get_own_expansions = [natural]
    ctx.mediator.get_own_nat = natural
    ctx.bot.owned_expansions = {
        main: MagicMock(),
        natural: MagicMock(),
        third: MagicMock(),
    }
    ctx.bot.mediator.get_building_tracker_dict = {
        999: {ID: UnitTypeId.SPINECRAWLER, TARGET: third}
    }

    assert z.spine_crawlers(per_base=1, gate=lambda _ctx: True)(ctx) is None


def test_spine_crawlers_check_interval_skips_until_due() -> None:
    ctx = _ctx()
    main = Point2((10.0, 10.0))
    natural = Point2((50.0, 50.0))
    third = Point2((200.0, 200.0))
    ctx.bot.start_location = main
    ctx.mediator.get_own_expansions = [natural]
    ctx.mediator.get_own_nat = natural
    ctx.bot.owned_expansions = {
        main: MagicMock(),
        natural: MagicMock(),
        third: MagicMock(),
    }
    ctx.bot.time = 270.0
    ctx.state.last_spine_check_at = 260.0

    assert (
        z.spine_crawlers(per_base=1, gate=lambda _ctx: True, check_interval=15.0)(ctx)
        is None
    )
    assert ctx.state.last_spine_check_at == 260.0


def test_spine_crawlers_returns_none_before_gate() -> None:
    ctx = _ctx()
    assert z.spine_crawlers(per_base=1, gate=lambda _ctx: False)(ctx) is None


def test_early_aggression_spines_place_at_natural() -> None:
    ctx = _ctx()
    main = Point2((10.0, 10.0))
    natural = Point2((50.0, 50.0))
    ctx.bot.start_location = main
    ctx.mediator.get_own_expansions = [natural]
    ctx.mediator.get_own_nat = natural
    ctx.bot.structures.return_value.closer_than.return_value = []

    plan = z.early_aggression_spines(2, gate=lambda _ctx: True)(ctx)

    # One at a time, not both in the same plan - see the step's own
    # docstring for the live-confirmed placement collision that fixed
    # (queuing both let the 2nd spine pick the 1st's still-unregistered
    # tile).
    assert isinstance(plan, MacroPlan)
    assert len(plan.macros) == 1
    macro = plan.macros[0]
    assert isinstance(macro, BuildSporeCrawler)
    assert macro.base_location == natural
    assert macro.structure_type == UnitTypeId.SPINECRAWLER


def test_early_aggression_spines_skip_when_natural_already_has_count() -> None:
    ctx = _ctx()
    natural = Point2((50.0, 50.0))
    ctx.mediator.get_own_expansions = [natural]
    ctx.mediator.get_own_nat = natural
    ctx.bot.structures.return_value.closer_than.return_value = [
        MagicMock(),
        MagicMock(),
    ]

    assert z.early_aggression_spines(2, gate=lambda _ctx: True)(ctx) is None


def test_train_queens_extra_is_not_clipped_by_max_per_townhall() -> None:
    """Regression test: `max_per_townhall` feeds `TrainQueens`'s own
    `min(to_count, len(townhalls) * max_per_townhall)` formula, so passing
    `extra` without also widening `max_per_townhall` would silently clip the
    extra queen straight back off."""
    ctx = _ctx()
    ctx.bot.townhalls.ready = [MagicMock(), MagicMock(), MagicMock()]  # 3 bases

    behavior = z.train_queens(per_base=1, maximum=8, extra=2)(ctx)

    assert isinstance(behavior, TrainQueens)
    assert behavior.to_count == 5  # 3 bases + 2 extras
    assert behavior.max_per_townhall == 3  # per_base + extra


def test_train_queens_extra_still_respects_maximum() -> None:
    ctx = _ctx()
    ctx.bot.townhalls.ready = [MagicMock() for _ in range(5)]

    behavior = z.train_queens(per_base=1, maximum=4, extra=2)(ctx)

    assert behavior.to_count == 4  # capped, not 5 bases + 2


def test_overseers_targets_one_per_wave_released() -> None:
    ctx = _ctx()
    ctx.state.wave_number = 2

    behavior = z.overseers(per_wave=1, maximum=3)(ctx)

    assert isinstance(behavior, MorphOverseers)
    assert behavior.to_count == 2


def test_overseers_caps_at_maximum() -> None:
    ctx = _ctx()
    ctx.state.wave_number = 5

    behavior = z.overseers(per_wave=1, maximum=3)(ctx)

    assert behavior.to_count == 3


def test_overseers_none_before_first_wave() -> None:
    ctx = _ctx()
    assert ctx.state.wave_number == 0
    assert z.overseers(per_wave=1, maximum=3)(ctx) is None


def test_overseers_returns_none_before_gate() -> None:
    ctx = _ctx()
    ctx.state.wave_number = 2
    assert z.overseers(per_wave=1, maximum=3, gate=lambda _ctx: False)(ctx) is None


# --- common.build_workers --------------------------------------------------


def test_build_workers_trains_up_to_the_worker_target_by_default() -> None:
    ctx = _ctx()
    behavior = c.build_workers()(ctx)

    assert isinstance(behavior, BuildWorkers)
    assert behavior.to_count == ctx.worker_target


def test_build_workers_returns_none_before_its_gate() -> None:
    ctx = _ctx()
    assert c.build_workers(gate=lambda _ctx: False)(ctx) is None


def test_build_workers_honors_an_explicit_to_count() -> None:
    ctx = _ctx()
    behavior = c.build_workers(to_count=22)(ctx)

    assert isinstance(behavior, BuildWorkers)
    assert behavior.to_count == 22


def test_auto_supply_returns_none_before_its_gate() -> None:
    ctx = _ctx()
    assert c.auto_supply(gate=lambda _ctx: False)(ctx) is None


def test_gas_starved_when_mineral_gas_ratio_exceeds_five() -> None:
    ctx = _ctx()
    ctx.bot.minerals = 600
    ctx.bot.vespene = 100
    assert z._gas_starved(ctx)
    ctx.bot.vespene = 150
    assert not z._gas_starved(ctx)
    ctx.bot.vespene = 0
    ctx.bot.minerals = 50
    assert z._gas_starved(ctx)


def test_spawn_macro_army_prefers_lings_when_gas_starved() -> None:
    ctx = _ctx()
    ctx.bot.minerals = 800
    ctx.bot.vespene = 50
    ctx.bot.tech_requirement_progress = MagicMock(
        side_effect=lambda t: 1.0 if t == UnitTypeId.ROACH else 0.0
    )
    ready = MagicMock()
    ready.__bool__ = lambda self: False
    ctx.bot.structures = MagicMock(
        return_value=MagicMock(ready=ready, amount=0)
    )
    behavior = z.spawn_macro_army(gate=lambda _c: True)(ctx)
    assert isinstance(behavior, SpawnController)
    comp = behavior.army_composition_dict
    assert UnitTypeId.ZERGLING in comp
    assert comp[UnitTypeId.ZERGLING]["proportion"] > comp[UnitTypeId.ROACH]["proportion"]




def test_spawn_macro_army_default_is_roach_ling() -> None:
    """Baseline army is Roach + Zergling."""
    ctx = _ctx()
    ctx.bot.minerals = 400
    ctx.bot.vespene = 400
    ctx.bot.tech_requirement_progress = MagicMock(return_value=1.0)
    ready = MagicMock()
    ready.__bool__ = lambda self: False
    ctx.bot.structures = MagicMock(
        return_value=MagicMock(ready=ready, amount=0)
    )
    behavior = z.spawn_macro_army(gate=lambda _c: True)(ctx)
    assert isinstance(behavior, SpawnController)
    assert UnitTypeId.SWARMHOSTMP not in behavior.army_composition_dict
    assert UnitTypeId.ROACH in behavior.army_composition_dict
    assert UnitTypeId.ZERGLING in behavior.army_composition_dict
    total = sum(
        float(v["proportion"]) for v in behavior.army_composition_dict.values()
    )
    assert abs(total - 1.0) < 1e-6


def _structures_ready(*ready_types: UnitTypeId) -> MagicMock:
    """`ctx.bot.structures(unit_type).ready` is truthy only for the given
    types - lets a test make Infestation Pit/Spire ready independently
    instead of the all-or-nothing single-mock pattern the other
    `spawn_macro_army` tests use."""

    def structures(unit_type: UnitTypeId) -> MagicMock:
        is_ready = unit_type in ready_types
        ready = MagicMock()
        ready.__bool__ = lambda self, v=is_ready: v
        return MagicMock(ready=ready, amount=1 if is_ready else 0)

    return MagicMock(side_effect=structures)


def test_spawn_macro_army_folds_in_infestor_once_infestation_pit_ready() -> None:
    ctx = _ctx()
    ctx.bot.minerals = 400
    ctx.bot.vespene = 400
    ctx.bot.tech_requirement_progress = MagicMock(return_value=1.0)
    ctx.bot.structures = _structures_ready(UnitTypeId.INFESTATIONPIT)

    behavior = z.spawn_macro_army(gate=lambda _c: True)(ctx)
    comp = behavior.army_composition_dict
    assert UnitTypeId.INFESTOR in comp
    assert UnitTypeId.CORRUPTOR not in comp
    total = sum(float(v["proportion"]) for v in comp.values())
    assert abs(total - 1.0) < 1e-6


def test_spawn_macro_army_folds_in_infestor_alongside_corruptor_when_air() -> None:
    ctx = _ctx()
    ctx.bot.minerals = 400
    ctx.bot.vespene = 400
    ctx.bot.tech_requirement_progress = MagicMock(return_value=1.0)
    ctx.bot.structures = _structures_ready(
        UnitTypeId.INFESTATIONPIT, UnitTypeId.SPIRE
    )

    with patch("bot.steps.zerg.enemy_has_air_units", return_value=True):
        behavior = z.spawn_macro_army(gate=lambda _c: True)(ctx)

    comp = behavior.army_composition_dict
    assert UnitTypeId.INFESTOR in comp
    assert UnitTypeId.CORRUPTOR in comp
    total = sum(float(v["proportion"]) for v in comp.values())
    assert abs(total - 1.0) < 1e-6


def test_spawn_macro_army_skips_infestor_while_gas_starved() -> None:
    """Regression test: Infestor is exactly as gas-hungry as Corruptor -
    folding it into a gas-starved comp would fight the ratio that comp is
    meant to fix. See `bot.consts.ROACH_LING_INFESTOR_COMP`'s own comment."""
    ctx = _ctx()
    ctx.bot.minerals = 800
    ctx.bot.vespene = 50
    ctx.bot.tech_requirement_progress = MagicMock(return_value=1.0)
    ctx.bot.structures = _structures_ready(UnitTypeId.INFESTATIONPIT)

    behavior = z.spawn_macro_army(gate=lambda _c: True)(ctx)
    assert UnitTypeId.INFESTOR not in behavior.army_composition_dict


def _rebuild_ctx(have: dict) -> BotContext:
    """`have` maps structure id -> how many of it exist; absent means 0."""
    ctx = _ctx()
    ctx.bot.time = 600.0
    base = MagicMock()
    base.position = Point2((10.0, 10.0))
    base.is_ready = True
    ctx.bot.townhalls = [base]
    ctx.bot.start_location = Point2((10.0, 10.0))

    def structures(structure_id):
        found = MagicMock()
        found.amount = have.get(structure_id, 0)
        return found

    ctx.bot.structures = structures
    return ctx


def _rebuilt(plan) -> list:
    return [
        getattr(m, "structure_id", type(m).__name__) for m in plan.macros
    ]


def test_rebuild_lost_tech_replaces_pool_warren_and_lair_after_a_base_trade() -> None:
    from bot.behaviors.zerg import BuildZergStructure, MorphLairAtMain

    ctx = _rebuild_ctx({})

    plan = z.rebuild_lost_tech()(ctx)

    assert _rebuilt(plan) == [
        UnitTypeId.SPAWNINGPOOL,
        UnitTypeId.ROACHWARREN,
        "MorphLairAtMain",
    ]
    assert isinstance(plan.macros[0], BuildZergStructure)
    assert isinstance(plan.macros[2], MorphLairAtMain)


def test_rebuild_lost_tech_only_replaces_what_is_missing() -> None:
    ctx = _rebuild_ctx(
        {UnitTypeId.SPAWNINGPOOL: 1, UnitTypeId.LAIR: 1}
    )

    plan = z.rebuild_lost_tech()(ctx)

    assert _rebuilt(plan) == [UnitTypeId.ROACHWARREN]


def test_rebuild_lost_tech_treats_a_hive_as_lair_tech() -> None:
    ctx = _rebuild_ctx(
        {
            UnitTypeId.SPAWNINGPOOL: 1,
            UnitTypeId.ROACHWARREN: 1,
            UnitTypeId.HIVE: 1,
        }
    )

    assert z.rebuild_lost_tech()(ctx) is None


def test_rebuild_lost_tech_ignores_a_lair_morph_in_flight() -> None:
    """`structures(LAIR)` is 0 for the whole ~57s morph - that is not a loss."""
    ctx = _rebuild_ctx(
        {UnitTypeId.SPAWNINGPOOL: 1, UnitTypeId.ROACHWARREN: 1}
    )
    order = MagicMock()
    order.ability.id = AbilityId.UPGRADETOLAIR_LAIR
    ctx.bot.townhalls[0].orders = [order]

    assert z.rebuild_lost_tech()(ctx) is None


def test_rebuild_lost_tech_does_nothing_without_a_base_to_build_from() -> None:
    ctx = _rebuild_ctx({})
    ctx.bot.townhalls = []

    assert z.rebuild_lost_tech()(ctx) is None


def test_rebuild_lost_tech_builds_at_the_surviving_base() -> None:
    survivor = MagicMock()
    survivor.position = Point2((60.0, 60.0))
    survivor.is_ready = True
    ctx = _rebuild_ctx({})
    ctx.bot.townhalls = [survivor]
    ctx.bot.enemy_start_locations = [Point2((150.0, 150.0))]

    plan = z.rebuild_lost_tech()(ctx)

    assert plan.macros[0].base_location == Point2((60.0, 60.0))


def _comp_ctx(hydra: bool, lurker: bool, pit: bool = False, air: bool = False):
    ctx = _ctx()
    ctx.bot.minerals = 100
    ctx.bot.vespene = 100  # not gas-starved

    def structures(unit_type):
        found = MagicMock()
        found.ready = {
            UnitTypeId.HYDRALISKDEN: hydra,
            UnitTypeId.LURKERDENMP: lurker,
            UnitTypeId.INFESTATIONPIT: pit,
            UnitTypeId.SPIRE: air,
        }.get(unit_type, False)
        found.amount = 0
        return found

    ctx.bot.structures = structures
    return ctx


def _chosen_comp(ctx) -> dict:
    with patch("bot.steps.zerg.enemy_has_air_units", lambda _c: False):
        behavior = z.spawn_macro_army(gate=lambda _c: True)(ctx)
    return behavior.army_composition_dict


def test_spawn_macro_army_adds_hydralisks_once_the_den_is_up() -> None:
    comp = _chosen_comp(_comp_ctx(hydra=True, lurker=False))

    assert UnitTypeId.HYDRALISK in comp
    assert UnitTypeId.LURKERMP not in comp


def test_spawn_macro_army_morphs_lurkers_once_both_dens_are_up() -> None:
    comp = _chosen_comp(_comp_ctx(hydra=True, lurker=True))

    assert UnitTypeId.HYDRALISK in comp and UnitTypeId.LURKERMP in comp
    assert UnitTypeId.INFESTOR not in comp


def test_spawn_macro_army_keeps_infestors_alongside_lurkers() -> None:
    comp = _chosen_comp(_comp_ctx(hydra=True, lurker=True, pit=True))

    assert UnitTypeId.LURKERMP in comp and UnitTypeId.INFESTOR in comp


def test_spawn_macro_army_stays_roach_ling_without_a_hydra_den() -> None:
    comp = _chosen_comp(_comp_ctx(hydra=False, lurker=False))

    assert UnitTypeId.HYDRALISK not in comp and UnitTypeId.LURKERMP not in comp


def test_spawn_macro_army_swaps_lurkers_for_corruptors_against_air() -> None:
    ctx = _comp_ctx(hydra=True, lurker=True, air=True)
    with patch("bot.steps.zerg.enemy_has_air_units", lambda _c: True):
        comp = z.spawn_macro_army(gate=lambda _c: True)(ctx).army_composition_dict

    assert UnitTypeId.HYDRALISK in comp and UnitTypeId.CORRUPTOR in comp
    assert UnitTypeId.LURKERMP not in comp


def test_every_late_comp_sums_to_one() -> None:
    from bot import consts

    for comp in (
        consts.ROACH_HYDRA_COMP,
        consts.HYDRA_LURKER_COMP,
        consts.HYDRA_LURKER_INFESTOR_COMP,
        consts.ROACH_HYDRA_CORRUPTOR_COMP,
    ):
        assert sum(v["proportion"] for v in comp.values()) == pytest.approx(1.0)


def test_forward_crawler_wave_waits_on_minerals_and_interval() -> None:
    from bot.behaviors.zerg import ForwardCrawlerWave

    ctx = _forward_ctx(creep_until=60.0)

    def wave():
        return _with_creep(ctx, lambda: z.forward_crawler_wave()(ctx))

    ctx.bot.minerals = 2000
    assert wave() is None

    ctx.bot.minerals = 2700
    found = wave()
    assert isinstance(found, ForwardCrawlerWave)
    assert len(found.structure_types) == 6
    assert list(found.structure_types).count(UnitTypeId.SPINECRAWLER) == 4
    assert list(found.structure_types).count(UnitTypeId.SPORECRAWLER) == 2
    assert ctx.state.last_forward_crawler_wave_at == 100.0
    assert wave() is None
    ctx.bot.time = 130.0
    assert wave() is not None


def _forward_ctx(creep_until: float) -> BotContext:
    """Our base at (10,10), map centre (110,110); creep covers every tile
    within `creep_until` of the base."""
    ctx = _ctx()
    ctx.bot.minerals = 5000
    ctx.bot.time = 100.0
    base = MagicMock()
    base.position = Point2((10.0, 10.0))
    ctx.bot.townhalls = [base]
    ctx.bot.game_info.map_center = Point2((110.0, 110.0))
    ctx.mediator.get_creep_grid = "creep"
    ctx.creep_until = creep_until
    return ctx


def _with_creep(ctx, fn):
    origin = Point2((10.0, 10.0))
    with patch(
        "cython_extensions.general_utils.cy_has_creep",
        lambda _grid, point: origin.distance_to(point) <= ctx.creep_until,
    ):
        return fn()


def _creep_patch(ctx_creep_fn):
    return patch("cython_extensions.general_utils.cy_has_creep", ctx_creep_fn)


def _wall_group_ctx(standing: int):
    """Our base (10,10), enemy base (150,150), centre (110,110). `standing`
    crawlers already built around the first anchor (60,60)."""
    ctx = _forward_ctx(creep_until=200.0)
    ctx.bot.enemy_start_locations = [Point2((150.0, 150.0))]
    ctx.bot.enemy_structures = MagicMock()
    ctx.bot.enemy_structures.of_type = MagicMock(return_value=[])
    first = Point2((60.0, 60.0))
    ctx.state.forward_anchors = [first]
    crawlers = []
    for i in range(standing):
        crawler = MagicMock()
        crawler.position = Point2((60.0 + i * 0.5, 60.0))
        crawlers.append(crawler)
    ctx.bot.structures = MagicMock(return_value=crawlers)
    return ctx, first


def test_forward_anchor_stays_until_the_group_is_about_ten() -> None:
    ctx, first = _wall_group_ctx(standing=9)

    anchor = _with_creep(ctx, lambda: z._forward_anchor(ctx))

    assert anchor == first
    assert ctx.state.forward_anchors == [first]


def test_forward_anchor_advances_toward_the_enemy_once_the_group_is_full() -> None:
    ctx, first = _wall_group_ctx(standing=10)

    anchor = _with_creep(ctx, lambda: z._forward_anchor(ctx))

    assert anchor != first
    assert anchor.distance_to(first) >= z._FORWARD_ADVANCE_MIN
    # Strictly closer to the enemy base than the previous group.
    enemy = Point2((150.0, 150.0))
    assert anchor.distance_to(enemy) < first.distance_to(enemy)
    assert ctx.state.forward_anchors == [first, anchor]


def test_forward_anchor_keeps_thickening_the_group_when_creep_stalls() -> None:
    """No creep further forward: pile onto the current group (up to the cap)
    instead of floating the bank."""
    ctx, first = _wall_group_ctx(standing=10)
    ctx.creep_until = 61.0  # creep ends right at the current group

    assert _with_creep(ctx, lambda: z._forward_anchor(ctx)) == first
    assert ctx.state.forward_anchors == [first]


def test_forward_anchor_stops_once_the_stalled_group_is_at_the_cap() -> None:
    ctx, first = _wall_group_ctx(standing=12)
    ctx.creep_until = 61.0

    with patch.object(z, "_FORWARD_STALLED_CAP", 12):
        assert _with_creep(ctx, lambda: z._forward_anchor(ctx)) is None


def test_forward_anchor_never_advances_into_the_enemy_base() -> None:
    ctx, first = _wall_group_ctx(standing=10)
    first_far = Point2((125.0, 125.0))  # already ~35 from the enemy base
    ctx.state.forward_anchors = [first_far]
    crawlers = []
    for i in range(10):
        crawler = MagicMock()
        crawler.position = Point2((125.0 + i * 0.3, 125.0))
        crawlers.append(crawler)
    ctx.bot.structures = MagicMock(return_value=crawlers)

    # Never advances toward the enemy: stays on the current group.
    assert _with_creep(ctx, lambda: z._forward_anchor(ctx)) == first_far
    assert ctx.state.forward_anchors == [first_far]


def test_forward_crawler_wave_anchors_at_the_creep_edge_toward_mid_map() -> None:
    """Not at the army (which is the natural whenever it's home or falling
    back): out along the line to the map centre, as far as creep reaches."""
    ctx = _forward_ctx(creep_until=60.0)

    wave = _with_creep(ctx, lambda: z.forward_crawler_wave()(ctx))

    anchor = wave.anchor
    assert Point2((10.0, 10.0)).distance_to(anchor) == pytest.approx(60.0, abs=2.5)
    # On the line from our base to the centre.
    assert anchor.x == pytest.approx(anchor.y)
    assert anchor.x > 10.0


def test_forward_crawler_wave_waits_until_creep_reaches_forward_ground() -> None:
    """Creep only around the natural -> nothing forward to build at; hold
    (and don't burn the interval) rather than crowd the natural."""
    ctx = _forward_ctx(creep_until=15.0)

    assert _with_creep(ctx, lambda: z.forward_crawler_wave()(ctx)) is None
    assert ctx.state.last_forward_crawler_wave_at is None


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
        except Exception as error:  # noqa: BLE001 - report, don't stop
            failures += 1
            print(f"  FAIL  {test.__name__}: {error}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())



def test_forward_wave_grows_with_the_bank() -> None:
    assert z._forward_wave_size(2000) == 6
    assert z._forward_wave_size(3000) == 9
    assert z._forward_wave_size(5000) == 15
    assert z._forward_wave_size(50000) == z._FORWARD_WAVE_MAX


def test_forward_wave_types_are_two_spines_per_spore() -> None:
    types = z._forward_wave_types(9)

    assert len(types) == 9
    assert types.count(UnitTypeId.SPINECRAWLER) == 6
    assert types.count(UnitTypeId.SPORECRAWLER) == 3


def test_forward_wave_pulses_faster_and_bigger_with_a_huge_bank() -> None:
    ctx = _forward_ctx(creep_until=60.0)
    ctx.bot.minerals = 8000

    first = _with_creep(ctx, lambda: z.forward_crawler_wave()(ctx))
    assert len(first.structure_types) == z._FORWARD_WAVE_MAX
    assert ctx.state.last_forward_crawler_wave_at == 100.0

    ctx.bot.time = 100.0 + z._FORWARD_CRAWLER_FAST_INTERVAL - 1.0
    assert _with_creep(ctx, lambda: z.forward_crawler_wave()(ctx)) is None
    ctx.bot.time = 100.0 + z._FORWARD_CRAWLER_FAST_INTERVAL + 0.5
    assert _with_creep(ctx, lambda: z.forward_crawler_wave()(ctx)) is not None


def test_forward_wave_keeps_the_slow_interval_below_the_fast_bank() -> None:
    ctx = _forward_ctx(creep_until=60.0)
    ctx.bot.minerals = 2500
    _with_creep(ctx, lambda: z.forward_crawler_wave()(ctx))

    ctx.bot.time = 100.0 + z._FORWARD_CRAWLER_FAST_INTERVAL + 0.5
    assert _with_creep(ctx, lambda: z.forward_crawler_wave()(ctx)) is None



def test_forward_wave_is_trimmed_to_the_bank_above_the_floor() -> None:
    """A pulse never plans more than the bank above 2000 can pay for."""
    ctx = _forward_ctx(creep_until=60.0)
    ctx.bot.minerals = 2250  # 250 above the floor

    wave = _with_creep(ctx, lambda: z.forward_crawler_wave()(ctx))

    cost = sum(z._FORWARD_CRAWLER_COST[t] for t in wave.structure_types)
    assert 0 < cost <= 250
    assert wave.mineral_floor == z._FORWARD_CRAWLER_MINERALS


def test_forward_wave_does_nothing_when_the_bank_cannot_pay_for_one() -> None:
    ctx = _forward_ctx(creep_until=60.0)
    ctx.bot.minerals = 2050

    assert _with_creep(ctx, lambda: z.forward_crawler_wave()(ctx)) is None
    assert ctx.state.last_forward_crawler_wave_at is None  # interval not burned


def test_within_budget_is_a_cost_prefix() -> None:
    types = (
        UnitTypeId.SPINECRAWLER,
        UnitTypeId.SPORECRAWLER,
        UnitTypeId.SPINECRAWLER,
    )
    assert z._within_budget(types, 0) == ()
    assert z._within_budget(types, 100) == (UnitTypeId.SPINECRAWLER,)
    assert z._within_budget(types, 175) == types[:2]
    assert z._within_budget(types, 10000) == types


def _poor_ctx(minerals: float, site: Point2, anchor=Point2((60.0, 60.0))):
    ctx = _forward_ctx(creep_until=200.0)
    ctx.bot.minerals = minerals
    ctx.state.forward_anchors = [anchor]
    ctx.mediator.get_building_tracker_dict = {
        501: {ID: UnitTypeId.SPINECRAWLER, TARGET: site}
    }
    ctx.mediator.get_building_counter = {UnitTypeId.SPINECRAWLER: 1}
    ctx.bot.mediator = ctx.mediator
    return ctx


def test_forward_drones_still_walking_are_recalled_below_the_floor() -> None:
    ctx = _poor_ctx(1500, Point2((62.0, 61.0)))

    _with_creep(ctx, lambda: z.forward_crawler_wave()(ctx))

    assert 501 not in ctx.mediator.get_building_tracker_dict
    assert ctx.mediator.get_building_counter[UnitTypeId.SPINECRAWLER] == 0
    ctx.mediator.assign_role.assert_called_once_with(tag=501, role=UnitRole.GATHERING)


def test_base_crawler_drones_are_not_recalled_below_the_floor() -> None:
    ctx = _poor_ctx(1500, Point2((12.0, 12.0)))  # a base Spore / Spine

    _with_creep(ctx, lambda: z.forward_crawler_wave()(ctx))

    assert 501 in ctx.mediator.get_building_tracker_dict
    ctx.mediator.assign_role.assert_not_called()


def test_forward_drones_are_left_alone_while_the_bank_is_above_the_floor() -> None:
    ctx = _poor_ctx(2600, Point2((62.0, 61.0)))

    _with_creep(ctx, lambda: z.forward_crawler_wave()(ctx))

    assert 501 in ctx.mediator.get_building_tracker_dict
