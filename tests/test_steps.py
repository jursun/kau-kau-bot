"""Regression tests for the composed macro steps that are hard to eyeball:
`common.split_production` and `zerg.spore_crawlers`. Both lean on
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
    # No spore crawlers anywhere by default; tests override per location.
    bot.structures.return_value.closer_than.return_value = []

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


def test_spore_crawlers_places_one_per_owned_expansion() -> None:
    # Regression test: `base_location` must be one of ares' own recognized
    # base points, not a townhall's literal position — passing the latter
    # crashed a real game with `KeyError: (60.5, 56.5)`. See the next test
    # for why `to_count_per_base` itself (a second crash, at a *different*
    # location) is avoided entirely rather than just fixing the location.
    ctx = _ctx()
    main = Point2((10.0, 10.0))
    natural = Point2((50.0, 50.0))
    ctx.bot.owned_expansions = {main: MagicMock(), natural: MagicMock()}

    plan = z.spore_crawlers(per_base=1, gate=lambda _ctx: True)(ctx)

    assert isinstance(plan, MacroPlan)
    assert len(plan.macros) == 2
    for behavior, location in zip(plan.macros, (main, natural)):
        assert isinstance(behavior, BuildStructure)
        assert behavior.structure_id == UnitTypeId.SPORECRAWLER
        assert behavior.base_location == location
        # Regression test: `to_count_per_base` must stay unset (0). Ares
        # never populates `placements_dict` for a Zerg bot
        # (`_solve_zerg_building_formation` is a stub), so any use of
        # `to_count_per_base` here is a guaranteed `KeyError` — crashed a
        # real game a second time, at `(90.5, 132.5)`, even after the first
        # crash's location fix. Per-base counting is done by
        # `spore_crawlers` itself instead (see the next test).
        assert behavior.to_count_per_base == 0


def test_spore_crawlers_skips_bases_that_already_have_enough() -> None:
    ctx = _ctx()
    covered = Point2((10.0, 10.0))
    uncovered = Point2((50.0, 50.0))
    ctx.bot.owned_expansions = {covered: MagicMock(), uncovered: MagicMock()}
    ctx.bot.structures.return_value.closer_than.side_effect = (
        lambda _radius, location: [MagicMock()] if location == covered else []
    )

    plan = z.spore_crawlers(per_base=1, gate=lambda _ctx: True)(ctx)

    assert len(plan.macros) == 1
    assert plan.macros[0].base_location == uncovered


def test_spore_crawlers_collapses_a_macro_hatch_onto_its_base() -> None:
    """Two hatcheries at the same expansion location (main + a macro hatch)
    must not produce two BuildStructure calls for that base."""
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
