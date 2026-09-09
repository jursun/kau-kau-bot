"""Regression tests for the composed macro steps that are hard to eyeball:
`common.split_production`, `zerg.spore_crawlers`, `zerg.train_queens` and
`zerg.overseers`. The `spore_crawlers`/`split_production` cases lean on
`MacroPlan.execute()`'s "stop at the first behavior that acts" semantics
(see `macro_engine.py`), so what matters is the *order* and *shape* of the
behaviors each step hands back, not just that it returns something.

Runs under pytest, or standalone with no test dependency:

    python -m tests.test_steps
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from ares.behaviors.macro import (
    BuildStructure,
    BuildWorkers,
    MacroPlan,
    SpawnController,
)
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.behaviors.zerg import BuildSporeCrawler, MorphOverseers, TrainQueens
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

    build = MagicMock()
    build.economy.worker_target = 60
    build.economy.workers_per_base = 22
    build.army.comp = {UnitTypeId.ZERGLING: {"proportion": 1.0, "priority": 0}}

    return BotContext(bot=bot, build=build, state=RunState())


def test_split_production_favors_economy_before_gate() -> None:
    # Army is way ahead of economy, but the gate hasn't opened yet — should
    # still behave like the old unconditional build_workers-then-spawn_army.
    ctx = _ctx(supply_workers=10.0, supply_army=40.0)
    plan = c.split_production(gate=lambda _ctx: False)(ctx)

    assert isinstance(plan, MacroPlan)
    assert isinstance(plan.macros[0], BuildWorkers)
    assert isinstance(plan.macros[1], SpawnController)


def test_split_production_prioritizes_whichever_side_is_behind_once_gated() -> None:
    economy_ahead = _ctx(supply_workers=40.0, supply_army=10.0)
    plan = c.split_production(gate=lambda _ctx: True)(economy_ahead)
    assert isinstance(plan.macros[0], SpawnController), "army should go first"
    assert isinstance(plan.macros[1], BuildWorkers)

    army_ahead = _ctx(supply_workers=10.0, supply_army=40.0)
    plan = c.split_production(gate=lambda _ctx: True)(army_ahead)
    assert isinstance(plan.macros[0], BuildWorkers), "economy should go first"
    assert isinstance(plan.macros[1], SpawnController)


def test_split_production_never_drops_either_side() -> None:
    """Whichever order, both behaviors must still be present — a frame where
    the leading side can't act (e.g. workers at cap) must fall through."""
    ctx = _ctx(supply_workers=10.0, supply_army=40.0)
    plan = c.split_production(gate=lambda _ctx: True)(ctx)
    kinds = {type(behavior) for behavior in plan.macros}
    assert kinds == {BuildWorkers, SpawnController}


def test_evolution_chambers_reads_count_and_gate_from_the_build() -> None:
    """Regression test: `evolution_chambers()` used to take `count`/`gate`
    as its own params, duplicating the same numbers `UpgradeRushValidator`
    needed to know separately. Both now come from `ctx.build.army` — the
    single source of truth for a per-build target count and gate."""
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
        lambda _radius, location: [MagicMock()] if location == covered else []
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


def test_spore_crawlers_waits_while_one_is_already_in_flight() -> None:
    # Regression test: a worker already dispatched to build a spore crawler
    # doesn't show up in `structures()` until it actually starts, which can
    # take several seconds of walking. Without this gate, every frame in
    # that window re-requests a build for the same still-"uncovered" base —
    # a real game piled several crawlers onto one base this way.
    ctx = _ctx()
    ctx.bot.owned_expansions = {Point2((10.0, 10.0)): MagicMock()}
    ctx.bot.structure_pending.return_value = 1

    assert z.spore_crawlers(per_base=1, gate=lambda _ctx: True)(ctx) is None
    ctx.bot.structure_pending.assert_called_with(UnitTypeId.SPORECRAWLER)


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


def test_train_queens_extra_is_not_clipped_by_max_per_townhall() -> None:
    """Regression test: `max_per_townhall` feeds `TrainQueens`'s own
    `min(to_count, len(townhalls) * max_per_townhall)` formula, so passing
    `extra` without also widening `max_per_townhall` would silently clip the
    extra queen straight back off."""
    ctx = _ctx()
    ctx.bot.townhalls.ready = [MagicMock(), MagicMock(), MagicMock()]  # 3 bases

    behavior = z.train_queens(per_base=1, maximum=6, extra=1)(ctx)

    assert isinstance(behavior, TrainQueens)
    assert behavior.to_count == 4  # 3 bases + 1 extra
    assert behavior.max_per_townhall == 2  # per_base + extra


def test_train_queens_extra_still_respects_maximum() -> None:
    ctx = _ctx()
    ctx.bot.townhalls.ready = [MagicMock() for _ in range(5)]

    behavior = z.train_queens(per_base=1, maximum=4, extra=1)(ctx)

    assert behavior.to_count == 4  # capped, not 5 bases + 1


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
