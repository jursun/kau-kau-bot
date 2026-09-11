"""Regression tests for the Terran proxy pieces.

Five things here are easy to get wrong and impossible to eyeball:

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
* `steps.terran.proxy_crew` must tell "still walking/building" from "just
  finished" purely off `get_building_tracker_dict` membership, never off
  structure counts — two crew workers building the same structure type
  finish within moments of each other, so a count-based check would
  misattribute one's completion to the other.
* `steps.terran.claim_z_on_first_scv` must claim exactly once, and must
  ignore anything that isn't an SCV (an Overlord scout, a lost Marine,
  whatever else might fire `on_unit_created` before the 13th SCV does).

Runs under pytest, or standalone with no test dependency:

    python -m tests.test_terran
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from ares.behaviors.combat import CombatManeuver
from ares.behaviors.combat.individual import AMove, ShootTargetInRange
from ares.behaviors.macro import BuildStructure
from ares.consts import UnitRole
from cython_extensions import cy_distance_to_squared
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.builds.definition import ProxyCrewPlan, WorkerTask
from bot.core import roles
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
    worker.is_structure = False
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
    """Every `AMove` target registered, wrapped in a `CombatManeuver` or
    bare - the hold-at-proxy phase registers `AMove` directly, the
    attack phase wraps it alongside `ShootTargetInRange`/`AttackTarget`."""
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


def test_claimed_workers_prioritize_enemy_units_over_structures() -> None:
    """The whole point of this round's change: an enemy unit in range crowds
    out an enemy structure as the target, same as every other attacker - see
    `combat._prioritize_enemies`."""
    ctx = _ctx()
    ctx.state.wave_number = 1
    structure = _worker(90, PROXY)
    structure.is_structure = True
    enemy_unit = _worker(91, PROXY)
    ctx.mediator.get_units_in_range.return_value = [[structure, enemy_unit]]
    ctx.mediator.get_units_from_role.return_value = [_worker(1, PROXY)]

    combat.builder_workers_attack(_proxy, claim_gate=lambda _c: False)(ctx)

    shoots = [
        m
        for call in ctx.bot.register_behavior.call_args_list
        for m in call.args[0].micros
        if isinstance(m, ShootTargetInRange)
    ]
    assert shoots[0].targets == [enemy_unit]


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


# --- gates.training_started -----------------------------------------------


def test_training_started_is_false_with_nothing_queued() -> None:
    ctx = _ctx()
    ctx.bot.already_pending.return_value = 0
    assert not gates.training_started(UnitTypeId.MARINE)(ctx)


def test_training_started_is_true_once_something_is_queued() -> None:
    ctx = _ctx()
    ctx.bot.already_pending.return_value = 1
    assert gates.training_started(UnitTypeId.MARINE)(ctx)


# --- targeting.enemy_fourth -------------------------------------------------


def test_enemy_fourth_reads_the_mediator_value() -> None:
    ctx = _ctx()
    ctx.mediator.get_enemy_fourth = PROXY
    assert targeting.enemy_fourth(ctx) == PROXY


# --- steps.terran.claim_z_on_first_scv --------------------------------------


def _crew_plan(role: UnitRole = UnitRole.GATE_KEEPER) -> ProxyCrewPlan:
    return ProxyCrewPlan(
        x_tasks=(WorkerTask(UnitTypeId.BARRACKS, _proxy),),
        y_tasks=(WorkerTask(UnitTypeId.BARRACKS, _proxy),),
        z_tasks=(WorkerTask(UnitTypeId.SUPPLYDEPOT, _proxy),),
        role=role,
    )


def test_claim_z_ignores_a_non_scv_unit() -> None:
    ctx = _ctx()
    ctx.build.crew = _crew_plan()
    unit = MagicMock()
    unit.type_id = UnitTypeId.MARINE
    unit.tag = 1

    t.claim_z_on_first_scv()(ctx, unit)

    assert ctx.state.proxy_crew.z.tag is None


def test_claim_z_claims_the_first_scv_and_assigns_the_crew_role() -> None:
    ctx = _ctx()
    ctx.build.crew = _crew_plan(role=UnitRole.GATE_KEEPER)
    ctx.bot.workers = [MagicMock() for _ in range(13)]  # the 13th now exists
    unit = MagicMock()
    unit.type_id = UnitTypeId.SCV
    unit.tag = 99

    t.claim_z_on_first_scv()(ctx, unit)

    assert ctx.state.proxy_crew.z.tag == 99
    assert ctx.mediator.assign_role.call_args.kwargs == {
        "tag": 99,
        "role": UnitRole.GATE_KEEPER,
    }


def test_claim_z_ignores_an_scv_event_before_a_13th_scv_actually_exists() -> None:
    """Regression test: an earlier version trusted `on_unit_created` to never
    fire for a game's starting units, and claimed whichever SCV triggered the
    very first such event. In a real game that fired for one of the starting
    12, pulling a third worker off the mineral line alongside X and Y.
    Counting `ctx.bot.workers` directly is what fixes that - only a call
    that lands once a 13th SCV genuinely exists may claim one."""
    ctx = _ctx()
    ctx.build.crew = _crew_plan()
    ctx.bot.workers = [MagicMock() for _ in range(10)]
    unit = MagicMock()
    unit.type_id = UnitTypeId.SCV
    unit.tag = 7

    t.claim_z_on_first_scv()(ctx, unit)

    assert ctx.state.proxy_crew.z.tag is None
    ctx.mediator.assign_role.assert_not_called()


def test_claim_z_ignores_every_scv_after_the_first() -> None:
    ctx = _ctx()
    ctx.build.crew = _crew_plan()
    ctx.bot.workers = [MagicMock() for _ in range(13)]
    hook = t.claim_z_on_first_scv()
    first, second = MagicMock(), MagicMock()
    first.type_id = second.type_id = UnitTypeId.SCV
    first.tag, second.tag = 1, 2

    hook(ctx, first)
    hook(ctx, second)

    assert ctx.state.proxy_crew.z.tag == 1


# --- roles.assign_on_created wiring -----------------------------------------


def test_assign_on_created_calls_the_builds_hook() -> None:
    ctx = _ctx()
    ctx.build.race = None  # matches nothing in SUPPORT_ROLES/SCOUT_TYPES
    ctx.build.army.types = frozenset()
    ctx.build.on_unit_created = MagicMock()
    unit = MagicMock()
    unit.type_id = UnitTypeId.SCV

    roles.assign_on_created(ctx, unit)

    ctx.build.on_unit_created.assert_called_once_with(ctx, unit)


# --- steps.terran.proxy_crew -------------------------------------------------


def test_proxy_crew_does_nothing_without_a_crew_plan() -> None:
    ctx = _ctx()
    ctx.build.crew = None

    t.proxy_crew()(ctx)

    assert ctx.state.proxy_crew.x.tag is None
    ctx.mediator.assign_role.assert_not_called()


def test_proxy_crew_claims_the_two_workers_closest_to_the_first_x_task() -> None:
    ctx = _ctx()
    ctx.build.crew = _crew_plan()
    near_one = _worker(1, Point2((99.0, 99.0)))
    near_two = _worker(2, Point2((98.0, 98.0)))
    far = _worker(3, HOME)
    ctx.bot.workers = [far, near_one, near_two]
    ctx.bot.unit_tag_dict = {}
    ctx.mediator.get_building_tracker_dict = {}

    t.proxy_crew()(ctx)

    assert {ctx.state.proxy_crew.x.tag, ctx.state.proxy_crew.y.tag} == {1, 2}
    assigned = {
        c.kwargs["tag"]: c.kwargs["role"]
        for c in ctx.mediator.assign_role.call_args_list
    }
    assert assigned == {1: UnitRole.GATE_KEEPER, 2: UnitRole.GATE_KEEPER}


def test_proxy_crew_does_not_reclaim_once_x_is_already_set() -> None:
    ctx = _ctx()
    ctx.build.crew = _crew_plan()
    ctx.state.proxy_crew.x.tag = 1
    ctx.state.proxy_crew.y.tag = 2
    ctx.bot.workers = [_worker(3, PROXY)]
    ctx.bot.unit_tag_dict = {}

    t.proxy_crew()(ctx)

    assert ctx.state.proxy_crew.x.tag == 1
    assert ctx.state.proxy_crew.y.tag == 2


def test_proxy_crew_issues_the_current_task_for_a_claimed_worker() -> None:
    ctx = _ctx()
    plan = _crew_plan()
    ctx.build.crew = plan
    ctx.state.proxy_crew.x.tag = 5
    worker = _worker(5, HOME)
    ctx.bot.unit_tag_dict = {5: worker}
    ctx.mediator.request_building_placement.return_value = PROXY
    ctx.mediator.build_with_specific_worker.return_value = True

    t.proxy_crew()(ctx)

    assert ctx.mediator.build_with_specific_worker.call_args.kwargs == {
        "worker": worker,
        "structure_type": UnitTypeId.BARRACKS,
        "pos": PROXY,
        "assign_role": False,
    }
    assert ctx.state.proxy_crew.x.queued is True
    assert "closest_to" not in ctx.mediator.request_building_placement.call_args.kwargs


def test_proxy_crew_forwards_closest_to_when_a_task_sets_it() -> None:
    ramp = Point2((50.0, 50.0))
    plan = ProxyCrewPlan(
        x_tasks=(
            WorkerTask(UnitTypeId.SUPPLYDEPOT, _proxy, closest_to=lambda _c: ramp),
        ),
        y_tasks=(WorkerTask(UnitTypeId.BARRACKS, _proxy),),
        z_tasks=(WorkerTask(UnitTypeId.SUPPLYDEPOT, _proxy),),
    )
    ctx = _ctx()
    ctx.build.crew = plan
    ctx.state.proxy_crew.x.tag = 5
    ctx.bot.unit_tag_dict = {5: _worker(5, HOME)}
    ctx.mediator.request_building_placement.return_value = PROXY

    t.proxy_crew()(ctx)

    assert (
        ctx.mediator.request_building_placement.call_args.kwargs["closest_to"] == ramp
    )


def test_proxy_crew_uses_near_search_when_a_task_sets_near() -> None:
    """A task's `near` bypasses `request_building_placement`/`where`
    entirely - `_drive_crew_member` must place via `routines.placement.
    near_point`, anchored on `near(ctx)`, instead."""
    target = Point2((5.0, 5.0))
    plan = ProxyCrewPlan(
        x_tasks=(WorkerTask(UnitTypeId.SUPPLYDEPOT, _proxy, near=lambda _c: target),),
        y_tasks=(WorkerTask(UnitTypeId.BARRACKS, _proxy),),
        z_tasks=(WorkerTask(UnitTypeId.SUPPLYDEPOT, _proxy),),
    )
    ctx = _ctx()
    ctx.build.crew = plan
    ctx.state.proxy_crew.x.tag = 5
    ctx.bot.unit_tag_dict = {5: _worker(5, HOME)}
    ctx.bot.mineral_field = []
    ctx.bot.vespene_geyser = []
    ctx.bot.in_pathing_grid.return_value = True
    ctx.mediator.can_place_structure.return_value = True

    t.proxy_crew()(ctx)

    ctx.mediator.request_building_placement.assert_not_called()
    placed = ctx.mediator.build_with_specific_worker.call_args.kwargs["pos"]
    assert cy_distance_to_squared(placed, target) <= 3.0**2, "must place near `near`"


def test_proxy_crew_does_not_reissue_while_still_in_the_tracker() -> None:
    ctx = _ctx()
    ctx.build.crew = _crew_plan()
    ctx.state.proxy_crew.x.tag = 5
    ctx.state.proxy_crew.x.queued = True
    ctx.bot.unit_tag_dict = {5: _worker(5, PROXY)}
    ctx.mediator.get_building_tracker_dict = {5: {}}

    t.proxy_crew()(ctx)

    ctx.mediator.build_with_specific_worker.assert_not_called()
    assert ctx.state.proxy_crew.x.task_index == 0
    assert ctx.state.proxy_crew.x.queued is True


def test_proxy_crew_advances_once_the_worker_leaves_the_tracker() -> None:
    ctx = _ctx()
    plan = _crew_plan()
    ctx.build.crew = plan
    ctx.state.proxy_crew.x.tag = 5
    ctx.state.proxy_crew.x.queued = True
    ctx.bot.unit_tag_dict = {5: _worker(5, PROXY)}
    ctx.mediator.get_building_tracker_dict = {}  # no longer tracked: done

    t.proxy_crew()(ctx)

    assert ctx.state.proxy_crew.x.task_index == 1
    assert ctx.state.proxy_crew.x.queued is False


def test_proxy_crew_retries_a_task_that_fails_verify() -> None:
    """`WorkerTask.verify` is the redundancy for a placement the game
    silently rejects: tracker departure alone must not be enough to advance
    when the task also declares a `verify` that hasn't passed."""
    ctx = _ctx()
    plan = ProxyCrewPlan(
        x_tasks=(WorkerTask(UnitTypeId.SUPPLYDEPOT, _proxy, verify=lambda _c: False),),
        y_tasks=(WorkerTask(UnitTypeId.BARRACKS, _proxy),),
        z_tasks=(WorkerTask(UnitTypeId.SUPPLYDEPOT, _proxy),),
    )
    ctx.build.crew = plan
    ctx.state.proxy_crew.x.tag = 5
    ctx.state.proxy_crew.x.queued = True
    ctx.bot.unit_tag_dict = {5: _worker(5, PROXY)}
    ctx.mediator.get_building_tracker_dict = {}  # left the tracker...
    ctx.mediator.request_building_placement.return_value = PROXY

    t.proxy_crew()(ctx)  # frame 1: sees departure, but verify says not done

    assert ctx.state.proxy_crew.x.task_index == 0
    assert ctx.state.proxy_crew.x.queued is False
    ctx.mediator.build_with_specific_worker.assert_not_called()

    t.proxy_crew()(ctx)  # frame 2: not queued anymore - retries the task

    ctx.mediator.build_with_specific_worker.assert_called_once()


def test_proxy_crew_advances_once_a_verified_task_passes() -> None:
    ctx = _ctx()
    plan = ProxyCrewPlan(
        x_tasks=(WorkerTask(UnitTypeId.SUPPLYDEPOT, _proxy, verify=lambda _c: True),),
        y_tasks=(WorkerTask(UnitTypeId.BARRACKS, _proxy),),
        z_tasks=(WorkerTask(UnitTypeId.SUPPLYDEPOT, _proxy),),
    )
    ctx.build.crew = plan
    ctx.state.proxy_crew.x.tag = 5
    ctx.state.proxy_crew.x.queued = True
    ctx.bot.unit_tag_dict = {5: _worker(5, PROXY)}
    ctx.mediator.get_building_tracker_dict = {}

    t.proxy_crew()(ctx)

    assert ctx.state.proxy_crew.x.task_index == 1
    assert ctx.state.proxy_crew.x.queued is False


def test_proxy_crew_holds_a_gated_task_until_its_gate_passes() -> None:
    ctx = _ctx()
    plan = ProxyCrewPlan(
        x_tasks=(
            WorkerTask(UnitTypeId.BARRACKS, _proxy),
            WorkerTask(UnitTypeId.BARRACKS, _proxy, gate=lambda _c: False),
        ),
        y_tasks=(WorkerTask(UnitTypeId.BARRACKS, _proxy),),
        z_tasks=(WorkerTask(UnitTypeId.SUPPLYDEPOT, _proxy),),
    )
    ctx.build.crew = plan
    ctx.state.proxy_crew.x.tag = 5
    ctx.state.proxy_crew.x.task_index = 1  # already past Barracks A
    ctx.bot.unit_tag_dict = {5: _worker(5, PROXY)}

    t.proxy_crew()(ctx)

    ctx.mediator.build_with_specific_worker.assert_not_called()
    assert ctx.state.proxy_crew.x.queued is False


def test_proxy_crew_gives_up_on_a_dead_worker() -> None:
    ctx = _ctx()
    plan = _crew_plan()
    ctx.build.crew = plan
    ctx.state.proxy_crew.x.tag = 5
    ctx.bot.unit_tag_dict = {}  # 5 is gone

    t.proxy_crew()(ctx)

    assert ctx.state.proxy_crew.x.task_index == len(plan.x_tasks)


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
