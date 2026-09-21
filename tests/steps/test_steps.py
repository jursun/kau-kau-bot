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
from unittest.mock import MagicMock, patch

from ares.behaviors.macro import (
    BuildStructure,
    BuildWorkers,
    ExpansionController,
    MacroPlan,
    SpawnController,
    UpgradeController,
)
from ares.consts import ID, TARGET
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


def test_forward_crawler_wave_waits_on_minerals_and_interval() -> None:
    from bot.behaviors.zerg import ForwardCrawlerWave

    ctx = _ctx()
    ctx.bot.minerals = 4000
    ctx.bot.time = 100.0
    ctx.bot.start_location = Point2((10.0, 10.0))
    ctx.bot.enemy_start_locations = [Point2((50.0, 50.0))]
    ctx.bot.mediator.get_squads.return_value = []
    assert z.forward_crawler_wave()(ctx) is None

    ctx.bot.minerals = 5001
    wave = z.forward_crawler_wave()(ctx)
    assert isinstance(wave, ForwardCrawlerWave)
    assert list(wave.structure_types).count(UnitTypeId.SPINECRAWLER) == 3
    assert list(wave.structure_types).count(UnitTypeId.SPORECRAWLER) == 3
    assert ctx.state.last_forward_crawler_wave_at == 100.0
    assert z.forward_crawler_wave()(ctx) is None
    ctx.bot.time = 130.0
    assert z.forward_crawler_wave()(ctx) is not None


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
