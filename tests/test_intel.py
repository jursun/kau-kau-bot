"""Unit tests for `bot.intel` (no live SC2).

    python -m tests.test_intel
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from sc2.ids.unit_typeid import UnitTypeId

from bot.core.context import BotContext
from bot.core.state import RunState
from bot.intel import (
    OpponentNotes,
    SeenTech,
    enemy_army,
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
