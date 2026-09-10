"""Regression tests for the Terran proxy pieces.

Three things here are easy to get wrong and impossible to eyeball:

* `gates.structure_started` counts ready + `structure_pending`, NOT
  `structures().amount + already_pending()` — the pair that double-counts
  every structure under construction (ARCHITECTURE.md gotcha 8).
* `combat.builder_workers_attack` must not claim a worker that ares has
  already dispatched to build something. A claimed worker leaves
  `UnitRole.GATHERING`, and `select_worker` only ever looks at GATHERING —
  so a wrong claim silently removes a builder from the pool that
  `proxy_barracks` draws from.
* A build that sets `combat.rally` must actually get that point back out of
  `targeting.rally_point`, and must not also be handed mineral-line hold
  positions on the other side of the map.

Runs under pytest, or standalone with no test dependency:

    python -m tests.test_terran
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from ares.behaviors.combat import CombatManeuver
from ares.behaviors.combat.individual import AMove
from ares.behaviors.macro import BuildStructure
from ares.consts import UnitRole
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.core.context import BotContext
from bot.core.state import RunState
from bot.routines import combat, gates, targeting
from bot.steps import terran as t

PROXY = Point2((100.0, 100.0))
HOME = Point2((20.0, 20.0))


def _worker(tag: int, position: Point2) -> MagicMock:
    worker = MagicMock()
    worker.tag = tag
    worker.position = position
    worker.is_constructing_scv = False
    return worker


def _ctx(rally=None) -> BotContext:
    build = MagicMock()
    build.army.types = frozenset({UnitTypeId.MARINE})
    build.combat.rally = rally
    build.combat.rally_offset = 8.0
    build.combat.focus = ()
    ctx = BotContext(bot=MagicMock(), build=build, state=RunState())
    ctx.bot.enemy_structures = []
    ctx.mediator.get_enemy_expansions = []
    ctx.bot.is_visible.return_value = True
    ctx.bot.enemy_start_locations = [Point2((150.0, 150.0))]
    ctx.mediator.get_building_tracker_dict = {}
    ctx.mediator.get_units_from_role.return_value = []
    ctx.bot.workers = []
    return ctx


def _proxy(_ctx_unused) -> Point2:
    return PROXY


# --- steps.terran.proxy_barracks -----------------------------------------


def test_proxy_barracks_returns_none_before_its_gate() -> None:
    ctx = _ctx()
    assert t.proxy_barracks(3, _proxy, gate=lambda _c: False)(ctx) is None


def test_proxy_barracks_builds_barracks_at_the_proxy() -> None:
    ctx = _ctx()
    behavior = t.proxy_barracks(4, _proxy)(ctx)

    assert isinstance(behavior, BuildStructure)
    assert behavior.structure_id == UnitTypeId.BARRACKS
    assert behavior.base_location == PROXY, "must build at the proxy, not at home"
    assert behavior.to_count == 4


# --- gates.structure_started ---------------------------------------------


def test_structure_started_counts_ready_plus_pending() -> None:
    ctx = _ctx()
    ctx.bot.structures.return_value.ready.amount = 2
    ctx.bot.structure_pending.return_value = 2

    assert gates.structure_started(UnitTypeId.BARRACKS, 4)(ctx)
    assert not gates.structure_started(UnitTypeId.BARRACKS, 5)(ctx)


def test_structure_started_does_not_double_count_one_in_progress() -> None:
    # One Barracks, under construction. `structures(...).amount` would say 1
    # and `already_pending` would also say 1; ready + structure_pending is
    # the pair that agrees with reality (gotcha 8).
    ctx = _ctx()
    ctx.bot.structures.return_value.ready.amount = 0
    ctx.bot.structure_pending.return_value = 1

    assert gates.structure_started(UnitTypeId.BARRACKS, 1)(ctx)
    assert not gates.structure_started(UnitTypeId.BARRACKS, 2)(
        ctx
    ), "one Barracks in progress must not read as two"


# --- combat.builder_workers_attack ---------------------------------------


def _run_claim(ctx: BotContext, workers: list, claim: bool = True) -> list:
    """Run the routine once and return the tags it claimed."""
    ctx.bot.workers = workers
    combat.builder_workers_attack(_proxy, claim_gate=lambda _c: claim)(ctx)
    return [call.kwargs["tag"] for call in ctx.mediator.assign_role.call_args_list]


def test_claims_a_worker_stranded_at_the_proxy() -> None:
    ctx = _ctx()
    claimed = _run_claim(ctx, [_worker(1, PROXY)])

    assert claimed == [1]
    assert ctx.mediator.assign_role.call_args.kwargs["role"] == UnitRole.PROXY_WORKER


def test_does_not_claim_while_the_gate_is_closed() -> None:
    ctx = _ctx()
    assert _run_claim(ctx, [_worker(1, PROXY)], claim=False) == []


def test_does_not_claim_a_worker_ares_has_sent_to_build() -> None:
    # The whole point of the tracker check: this worker is walking to the
    # proxy to put down the next Barracks. Claiming it takes it out of
    # UnitRole.GATHERING, which is the only pool `select_worker` draws from.
    ctx = _ctx()
    ctx.mediator.get_building_tracker_dict = {7: {}}

    assert _run_claim(ctx, [_worker(7, PROXY)]) == []


def test_does_not_claim_a_worker_mid_build() -> None:
    ctx = _ctx()
    building = _worker(8, PROXY)
    building.is_constructing_scv = True

    assert _run_claim(ctx, [building]) == []


def test_does_not_claim_a_worker_at_home() -> None:
    ctx = _ctx()
    assert _run_claim(ctx, [_worker(9, HOME)]) == []


def test_does_not_reclaim_a_worker_it_already_owns() -> None:
    ctx = _ctx()
    already = _worker(11, PROXY)
    ctx.mediator.get_units_from_role.return_value = [already]

    assert _run_claim(ctx, [already]) == []


def _amove_targets(ctx: BotContext) -> list[Point2]:
    targets: list[Point2] = []
    for call in ctx.bot.register_behavior.call_args_list:
        behavior = call.args[0]
        if isinstance(behavior, AMove):
            targets.append(behavior.target)
        elif isinstance(behavior, CombatManeuver):
            targets.extend(b.target for b in behavior.micros if isinstance(b, AMove))
    return targets


def test_claimed_workers_wait_at_the_proxy_before_the_first_wave() -> None:
    ctx = _ctx()
    ctx.state.wave_number = 0
    ctx.mediator.get_units_from_role.return_value = [_worker(1, PROXY)]

    combat.builder_workers_attack(_proxy, claim_gate=lambda _c: False)(ctx)

    assert _amove_targets(ctx) == [PROXY], "should hold, not run in alone"


def test_claimed_workers_attack_once_the_first_wave_is_out() -> None:
    ctx = _ctx()
    ctx.state.wave_number = 1
    ctx.mediator.get_units_in_range.return_value = [[]]  # nothing in range
    ctx.mediator.get_units_from_role.return_value = [_worker(1, PROXY)]

    combat.builder_workers_attack(_proxy, claim_gate=lambda _c: False)(ctx)

    targets = _amove_targets(ctx)
    assert targets == [ctx.bot.enemy_start_locations[0]], targets


# --- targeting rally override --------------------------------------------


def test_rally_point_uses_the_builds_override() -> None:
    ctx = _ctx(rally=_proxy)
    assert targeting.rally_point(ctx) == PROXY


def test_rally_point_falls_back_to_our_natural() -> None:
    ctx = _ctx(rally=None)
    ctx.mediator.get_own_nat = Point2((30.0, 30.0))
    assert targeting.rally_point(ctx) != PROXY


def test_hold_positions_collapses_to_the_pinned_rally() -> None:
    ctx = _ctx(rally=_proxy)
    ctx.bot.townhalls.ready = [MagicMock()]

    assert targeting.hold_positions(ctx) == [
        PROXY
    ], "a pinned rally must not also hand out mineral-line positions"


def test_hold_positions_still_covers_mineral_lines_without_an_override() -> None:
    ctx = _ctx(rally=None)
    townhall = MagicMock()
    townhall.position = HOME
    ctx.bot.townhalls.ready = [townhall]
    ctx.mediator.get_own_nat = Point2((30.0, 30.0))
    ctx.mediator.get_behind_mineral_positions.return_value = [Point2((18.0, 18.0))]

    assert len(targeting.hold_positions(ctx)) == 2


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
