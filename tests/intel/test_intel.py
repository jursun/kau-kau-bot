"""Unit tests for `bot.intel` (no live SC2).

    python -m tests.intel.test_intel
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from ares.consts import UnitRole
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.core.context import BotContext
from bot.core.state import RunState
from bot.intel import (
    OpponentNotes,
    SeenTech,
    army_behind_on_supply,
    enemy_army,
    enemy_army_supply,
    leave_army_supply,
    leave_enemy_army_supply,
    early_aggression,
    observe_early_aggression,
    observe_leave_intel,
    enemy_army_tags,
    enemy_army_type_ids,
    filter_non_workers,
    get_opponent_id,
    observe_seen_tech,
)


def _ctx(army=None, opponent_id=None) -> BotContext:
    bot = MagicMock()
    bot.opponent_id = opponent_id
    bot.mediator = SimpleNamespace(get_cached_enemy_army=list(army or []))
    return BotContext(bot=bot, build=MagicMock(), state=RunState())


def _unit(tag: int, type_id: UnitTypeId) -> MagicMock:
    unit = MagicMock()
    unit.tag = tag
    unit.type_id = type_id
    return unit


def test_enemy_army_strips_workers() -> None:
    ctx = _ctx(
        [
            _unit(1, UnitTypeId.ZEALOT),
            _unit(2, UnitTypeId.PROBE),
            _unit(3, UnitTypeId.STALKER),
            _unit(4, UnitTypeId.DRONE),
            _unit(5, UnitTypeId.SCV),
            _unit(6, UnitTypeId.MULE),
        ]
    )
    army = enemy_army(ctx)
    assert [u.tag for u in army] == [1, 3]
    assert enemy_army_tags(ctx) == frozenset({1, 3})
    assert enemy_army_type_ids(ctx) == frozenset(
        {UnitTypeId.ZEALOT, UnitTypeId.STALKER}
    )


def test_enemy_army_empty_when_missing() -> None:
    bot = MagicMock()
    bot.mediator = SimpleNamespace(get_cached_enemy_army=None)
    ctx = BotContext(bot=bot, build=MagicMock(), state=RunState())
    assert enemy_army(ctx) == []
    assert enemy_army_tags(ctx) == frozenset()


def test_enemy_army_supply_sums_combat_units() -> None:
    ctx = _ctx(
        [
            _unit(1, UnitTypeId.ZEALOT),
            _unit(2, UnitTypeId.PROBE),
            _unit(3, UnitTypeId.STALKER),
        ]
    )
    ctx.bot.calculate_supply_cost.side_effect = lambda t: {
        UnitTypeId.ZEALOT: 2.0,
        UnitTypeId.STALKER: 2.0,
        UnitTypeId.PROBE: 1.0,
    }[t]
    assert enemy_army_supply(ctx) == 4.0


def test_army_behind_on_supply_false_when_enemy_unseen() -> None:
    ctx = _ctx([])
    ctx.bot.supply_army = 10
    assert enemy_army_supply(ctx) == 0.0
    assert not army_behind_on_supply(ctx)


def test_army_behind_on_supply_when_enemy_has_more() -> None:
    ctx = _ctx([_unit(1, UnitTypeId.ZEALOT), _unit(2, UnitTypeId.ZEALOT)])
    ctx.bot.calculate_supply_cost.return_value = 2.0
    ctx.bot.supply_army = 2
    assert army_behind_on_supply(ctx)


def test_army_behind_on_supply_false_when_we_match_or_lead() -> None:
    ctx = _ctx([_unit(1, UnitTypeId.ZEALOT)])
    ctx.bot.calculate_supply_cost.return_value = 2.0
    ctx.bot.supply_army = 2
    assert not army_behind_on_supply(ctx)
    ctx.bot.supply_army = 6
    assert not army_behind_on_supply(ctx)


def test_filter_non_workers() -> None:
    units = [_unit(1, UnitTypeId.MARINE), _unit(2, UnitTypeId.SCV)]
    assert [u.tag for u in filter_non_workers(units)] == [1]


def test_opponent_notes_keyed_by_id() -> None:
    notes = OpponentNotes()
    notes.add("abc", "saw reaper")
    notes.add("abc", "factory")
    notes.add("xyz", "other")
    assert notes.get("abc") == ["saw reaper", "factory"]
    assert notes.get("xyz") == ["other"]
    assert notes.get(None) == []
    notes.clear("abc")
    assert notes.get("abc") == []


def test_opponent_notes_via_ctx() -> None:
    ctx = _ctx(opponent_id="opp-7")
    notes = OpponentNotes()
    notes.add_for_ctx(ctx, "proxy rax")
    assert get_opponent_id(ctx) == "opp-7"
    assert notes.get_for_ctx(ctx) == ["proxy rax"]


def test_get_opponent_id_missing() -> None:
    bot = SimpleNamespace()
    ctx = BotContext(bot=bot, build=MagicMock(), state=RunState())
    assert get_opponent_id(ctx) is None


def test_seen_tech_from_army_feed() -> None:
    ctx = _ctx(
        [
            _unit(1, UnitTypeId.ZEALOT),
            _unit(2, UnitTypeId.PROBE),
            _unit(3, UnitTypeId.STALKER),
        ]
    )
    seen = SeenTech()
    observe_seen_tech(seen, ctx)
    assert seen.seen(UnitTypeId.ZEALOT)
    assert seen.seen(UnitTypeId.STALKER)
    assert not seen.seen(UnitTypeId.PROBE)
    assert not seen.seen(UnitTypeId.COLOSSUS)



def test_leave_enemy_army_supply_peaks_through_fog() -> None:
    ctx = _ctx([_unit(1, UnitTypeId.ZEALOT), _unit(2, UnitTypeId.ZEALOT)])
    ctx.bot.calculate_supply_cost.return_value = 2.0
    assert observe_leave_intel(ctx) == 4.0
    assert ctx.state.peak_enemy_army_supply == 4.0
    # Fog: no army this frame - peak and leave floor stay.
    ctx.bot.mediator.get_cached_enemy_army = []
    assert enemy_army_supply(ctx) == 0.0
    assert leave_enemy_army_supply(ctx) == 4.0
    assert observe_leave_intel(ctx) == 4.0


def test_leave_enemy_army_supply_raises_when_seen_larger() -> None:
    ctx = _ctx([_unit(1, UnitTypeId.ZEALOT)])
    ctx.bot.calculate_supply_cost.return_value = 2.0
    observe_leave_intel(ctx)
    ctx.bot.mediator.get_cached_enemy_army = [
        _unit(1, UnitTypeId.ZEALOT),
        _unit(2, UnitTypeId.ZEALOT),
        _unit(3, UnitTypeId.ZEALOT),
    ]
    assert observe_leave_intel(ctx) == 6.0
    assert leave_enemy_army_supply(ctx) == 6.0



def test_leave_army_supply_sums_defending_only() -> None:
    ctx = _ctx([])
    ling = _unit(1, UnitTypeId.ZERGLING)
    roach = _unit(2, UnitTypeId.ROACH)

    def units_in_role(role):
        if role == UnitRole.DEFENDING:
            return [ling, roach]
        return []

    ctx.units_in_role = units_in_role  # type: ignore[method-assign]
    ctx.bot.calculate_supply_cost.side_effect = lambda t: {
        UnitTypeId.ZERGLING: 0.5,
        UnitTypeId.ROACH: 2.0,
    }[t]
    assert leave_army_supply(ctx) == 2.5


def test_leave_army_supply_zero_when_no_defenders() -> None:
    ctx = _ctx([])
    ctx.units_in_role = lambda role: []  # type: ignore[method-assign]
    assert leave_army_supply(ctx) == 0.0






def _point(x: float, y: float) -> Point2:
    return Point2((x, y))


def _combat_unit(tag: int, type_id: UnitTypeId, x: float, y: float) -> MagicMock:
    unit = _unit(tag, type_id)
    unit.position = _point(x, y)
    return unit


def _early_ctx(army=None, time: float = 90.0, workers: int = 12) -> BotContext:
    """BotContext with start/nat/mediator stubs for early-aggression observe."""
    home = _point(10, 10)
    nat = _point(25, 10)
    enemy = _point(100, 100)
    bot = MagicMock()
    bot.opponent_id = None
    bot.time = time
    bot.start_location = home
    bot.townhalls = MagicMock()
    bot.townhalls.ready = [MagicMock(position=home)]
    bot.workers = [MagicMock() for _ in range(workers)]
    bot.structures = MagicMock(return_value=MagicMock(ready=[]))
    bot.enemy_start_locations = [enemy]
    bot.enemy_structures = []
    bot.game_info = SimpleNamespace(map_center=_point(50, 50))
    bot.supply_army = 0.0
    bot.mediator = SimpleNamespace(
        get_cached_enemy_army=list(army or []),
        get_own_expansions=[nat],
        get_own_nat=nat,
    )
    return BotContext(bot=bot, build=MagicMock(), state=RunState())


def test_observe_early_aggression_latches_near_home() -> None:
    ctx = _early_ctx([_combat_unit(1, UnitTypeId.ZEALOT, 12, 10)])
    assert not early_aggression(ctx)
    assert observe_early_aggression(ctx) is True
    assert early_aggression(ctx) is True
    assert ctx.state.early_aggression is True


def test_observe_early_aggression_fog_does_not_clear_immediately() -> None:
    ctx = _early_ctx([_combat_unit(1, UnitTypeId.MARINE, 12, 10)], time=100.0)
    assert observe_early_aggression(ctx) is True
    # Fog: army gone; quiet hold not yet elapsed.
    ctx.bot.mediator.get_cached_enemy_army = []
    ctx.bot.time = 105.0
    assert observe_early_aggression(ctx) is True
    assert early_aggression(ctx)


def test_observe_early_aggression_clears_after_hold_past_window() -> None:
    ctx = _early_ctx([], time=400.0)
    ctx.state.early_aggression = True
    ctx.state.early_aggression_last_threat_at = 100.0
    assert observe_early_aggression(ctx) is False
    assert not early_aggression(ctx)


def test_observe_early_aggression_worker_spike() -> None:
    ctx = _early_ctx([], time=80.0, workers=12)
    ctx.state.early_aggression_worker_count = 16
    assert observe_early_aggression(ctx) is True
    assert early_aggression(ctx)


def test_observe_early_aggression_scout_ling_contact() -> None:
    """Opening ling scouts seeing pressure nearby latches defense mode."""
    from bot.consts import ZERGLING_SCOUT_ROLE

    ctx = _early_ctx([], time=90.0)
    scout = MagicMock()
    scout.tag = 9
    scout.position = _point(50, 50)
    enemy = _combat_unit(1, UnitTypeId.ZEALOT, 55, 50)
    ctx.bot.mediator.get_cached_enemy_army = [enemy]

    def get_units_from_role(*, role, unit_type):
        if role == ZERGLING_SCOUT_ROLE and unit_type == UnitTypeId.ZERGLING:
            return [scout]
        return []

    ctx.bot.mediator.get_units_from_role = get_units_from_role

    assert observe_early_aggression(ctx) is True
    assert early_aggression(ctx)


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
