"""Tests for the Chargelot milestone metrics (bot/intel/chargelot_metrics.py).

Runs under pytest, or standalone with no test dependency:

    python -m tests.test_chargelot_metrics
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from bot.core.context import BotContext
from bot.core.state import RunState
from bot.intel import chargelot_metrics as cm


def _ctx(time: float = 0.0) -> BotContext:
    bot = MagicMock()
    bot.time = time
    build = MagicMock()
    build.name = "2base Chargelot All-In"
    return BotContext(bot=bot, build=build, state=RunState())


def _unit(type_id: UnitTypeId, tag: int = 1) -> MagicMock:
    unit = MagicMock()
    unit.type_id = type_id
    unit.tag = tag
    return unit


# --- _pass_by / _alive_at (pure) ------------------------------------------


def test_pass_by_true_when_event_before_deadline() -> None:
    assert cm._pass_by(100.0, 200.0, 250.0) is True


def test_pass_by_false_when_event_after_deadline() -> None:
    assert cm._pass_by(250.0, 200.0, 250.0) is False


def test_pass_by_false_when_deadline_passed_with_no_event() -> None:
    assert cm._pass_by(None, 200.0, 200.0) is False


def test_pass_by_none_when_deadline_not_yet_reached() -> None:
    assert cm._pass_by(None, 200.0, 150.0) is None


def test_alive_at_true_when_death_after_checkpoint() -> None:
    assert cm._alive_at(250.0, 200.0, 300.0) is True


def test_alive_at_true_when_death_exactly_at_checkpoint() -> None:
    assert cm._alive_at(200.0, 200.0, 300.0) is True


def test_alive_at_false_when_death_before_checkpoint() -> None:
    assert cm._alive_at(100.0, 200.0, 300.0) is False


def test_alive_at_true_when_never_died_and_checkpoint_reached() -> None:
    assert cm._alive_at(None, 200.0, 200.0) is True


def test_alive_at_none_when_never_died_and_checkpoint_not_reached() -> None:
    assert cm._alive_at(None, 200.0, 100.0) is None


# --- note_unit_created ------------------------------------------------------


def test_note_unit_created_tracks_stalker_count_and_deadline_timestamp() -> None:
    ctx = _ctx(time=100.0)
    cm.note_unit_created(ctx, _unit(UnitTypeId.STALKER, tag=1))
    m = ctx.state.chargelot_metrics
    assert m.stalkers_trained == 1
    assert m.time_2_stalkers is None

    ctx.bot.time = 150.0
    cm.note_unit_created(ctx, _unit(UnitTypeId.STALKER, tag=2))
    assert m.stalkers_trained == 2
    assert m.time_2_stalkers == 150.0

    ctx.bot.time = 200.0
    cm.note_unit_created(ctx, _unit(UnitTypeId.STALKER, tag=3))
    assert m.stalkers_trained == 3
    assert m.time_2_stalkers == 150.0, "timestamp latches at the 2nd, not later"


def test_note_unit_created_tracks_zealot_count_and_deadline_timestamp() -> None:
    ctx = _ctx(time=0.0)
    m = ctx.state.chargelot_metrics
    for i in range(5):
        ctx.bot.time = 100.0 + i
        cm.note_unit_created(ctx, _unit(UnitTypeId.ZEALOT, tag=i))
    assert m.zealots_trained == 5
    assert m.time_6_zealots is None

    ctx.bot.time = 310.0
    cm.note_unit_created(ctx, _unit(UnitTypeId.ZEALOT, tag=99))
    assert m.zealots_trained == 6
    assert m.time_6_zealots == 310.0


def test_note_unit_created_tracks_prism_completion_time() -> None:
    ctx = _ctx(time=280.0)
    cm.note_unit_created(ctx, _unit(UnitTypeId.WARPPRISM))
    assert ctx.state.chargelot_metrics.time_prism_completed == 280.0

    ctx.bot.time = 400.0
    cm.note_unit_created(ctx, _unit(UnitTypeId.WARPPRISM))
    assert ctx.state.chargelot_metrics.time_prism_completed == 280.0, (
        "only the first Prism's completion time is kept"
    )


def test_note_unit_created_tracks_observer_production() -> None:
    ctx = _ctx(time=290.0)
    m = ctx.state.chargelot_metrics
    assert m.observer_produced is False

    cm.note_unit_created(ctx, _unit(UnitTypeId.OBSERVER))
    assert m.observer_produced is True
    assert m.time_observer == 290.0


def test_note_unit_created_still_counts_adept_produced() -> None:
    ctx = _ctx(time=50.0)
    cm.note_unit_created(ctx, _unit(UnitTypeId.ADEPT))
    assert ctx.state.chargelot_metrics.adept_produced == 1


# --- note_unit_destroyed -----------------------------------------------------


def test_note_unit_destroyed_records_scout_probe_death_time() -> None:
    ctx = _ctx(time=95.0)
    ctx.state.worker_harass_tags = {42}

    cm.note_unit_destroyed(ctx, 42)

    assert ctx.state.chargelot_metrics.scout_probe_death_time == 95.0


def test_note_unit_destroyed_ignores_tags_outside_worker_harass() -> None:
    ctx = _ctx(time=95.0)
    ctx.state.worker_harass_tags = {42}

    cm.note_unit_destroyed(ctx, 999)

    assert ctx.state.chargelot_metrics.scout_probe_death_time is None


def test_note_unit_destroyed_only_latches_first_scout_death() -> None:
    ctx = _ctx(time=95.0)
    ctx.state.worker_harass_tags = {42}
    cm.note_unit_destroyed(ctx, 42)

    ctx.bot.time = 200.0
    ctx.state.worker_harass_tags = {77}
    cm.note_unit_destroyed(ctx, 77)

    assert ctx.state.chargelot_metrics.scout_probe_death_time == 95.0


def test_note_unit_destroyed_records_adept_death_time_and_count() -> None:
    ctx = _ctx(time=260.0)
    m = ctx.state.chargelot_metrics
    m.adept_tag = 7
    m.adept_last_hp = 40.0

    cm.note_unit_destroyed(ctx, 7)

    assert m.adept_died == 1
    assert m.adept_death_time == 260.0
    assert m.adept_tag is None
    assert m.adept_damage_taken == 40.0


# --- note_upgrade_complete ---------------------------------------------------


def test_note_upgrade_complete_records_charge_time() -> None:
    ctx = _ctx(time=330.0)
    cm.note_upgrade_complete(ctx, UpgradeId.CHARGE)
    assert ctx.state.chargelot_metrics.charge_complete_time == 330.0


def test_note_upgrade_complete_ignores_other_upgrades() -> None:
    ctx = _ctx(time=330.0)
    cm.note_upgrade_complete(ctx, UpgradeId.WARPGATERESEARCH)
    assert ctx.state.chargelot_metrics.charge_complete_time is None


def test_note_upgrade_complete_only_latches_first_charge() -> None:
    ctx = _ctx(time=330.0)
    cm.note_upgrade_complete(ctx, UpgradeId.CHARGE)
    ctx.bot.time = 400.0
    cm.note_upgrade_complete(ctx, UpgradeId.CHARGE)
    assert ctx.state.chargelot_metrics.charge_complete_time == 330.0


# --- snapshot -----------------------------------------------------------------


def test_snapshot_reports_all_seven_pass_fail_checks() -> None:
    ctx = _ctx(time=420.0)  # 7:00 - past every deadline in the spec
    m = ctx.state.chargelot_metrics

    m.scout_probe_death_time = None  # survived
    m.adept_death_time = 100.0  # died early - should fail the 4:30 check
    m.charge_complete_time = 300.0  # by 5:45 (345s) - pass
    m.time_2_stalkers = 250.0  # after 4:00 (240s) - fail
    m.time_6_zealots = 300.0  # by 5:10 (310s) - pass
    m.time_prism_completed = None  # never built - fail
    m.time_observer = 340.0  # by 5:45 (345s) - pass

    snap = cm.snapshot(ctx)

    assert snap["scout_probe_alive_at_200"] is True
    assert snap["adept_alive_at_430"] is False
    assert snap["charge_by_545"] is True
    assert snap["stalkers_2_by_400"] is False
    assert snap["zealots_6_by_510"] is True
    assert snap["prism_by_525"] is False
    assert snap["observer_by_545"] is True


def test_snapshot_pass_fail_is_none_when_game_ended_before_deadline() -> None:
    ctx = _ctx(time=200.0)  # ended before every deadline except 2:00
    snap = cm.snapshot(ctx)

    assert snap["scout_probe_alive_at_200"] is True  # 200 >= 120, never died
    assert snap["adept_alive_at_430"] is None  # 200 < 270
    assert snap["charge_by_545"] is None  # 200 < 345
    assert snap["stalkers_2_by_400"] is None  # 200 < 240
    assert snap["zealots_6_by_510"] is None  # 200 < 310
    assert snap["prism_by_525"] is None  # 200 < 325
    assert snap["observer_by_545"] is None  # 200 < 345


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
