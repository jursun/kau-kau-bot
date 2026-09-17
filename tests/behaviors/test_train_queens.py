"""Regression tests for `TrainQueens`'s per-townhall fairness.

Runs under pytest, or standalone with no test dependency:

    python -m tests.behaviors.test_train_queens
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.behaviors.zerg import TrainQueens


class _Units(list):
    """Minimal stand-in for `sc2.units.Units`: iterable, plus `.amount`."""

    @property
    def amount(self) -> int:
        return len(self)


def _townhall(tag: int, position: Point2, idle: bool = True) -> MagicMock:
    th = MagicMock()
    th.tag = tag
    th.position = position
    th.is_idle = idle
    return th


def _queen(position: Point2, tag: int = 0) -> MagicMock:
    queen = MagicMock()
    queen.position = position
    queen.tag = tag
    return queen


def _ai(townhalls, queens, pending: int = 0, afford: bool = True) -> MagicMock:
    ai = MagicMock()
    ai.structures.return_value.ready = True
    ai.townhalls.ready = townhalls
    ai.units.return_value = _Units(queens)
    ai.already_pending.return_value = pending
    ai.can_afford.return_value = afford
    return ai


def test_a_fresh_townhall_is_preferred_over_one_that_already_has_a_queen() -> None:
    """Regression test: a first version picked whichever ready townhall was
    first in `ai.townhalls`' own iteration order every call, with
    `max_per_townhall` only feeding the *combined* target ceiling, not an
    actual per-townhall cap - so the main (always first, and free again as
    soon as its own Queen finished) could keep re-winning the "which
    townhall is idle right now" race and reach the combined target on its
    own, leaving a newer base with none. Main here already has a Queen;
    natural has none - natural must win even though it's second in the
    list ares itself would hand back."""
    main_pos = Point2((10.0, 10.0))
    natural_pos = Point2((50.0, 50.0))
    main = _townhall(1, main_pos)
    natural = _townhall(2, natural_pos)
    existing_queen = _queen(main_pos)

    ai = _ai(townhalls=[main, natural], queens=[existing_queen])
    mediator = MagicMock()

    did_act = TrainQueens(to_count=3, max_per_townhall=2).execute(ai, {}, mediator)

    assert did_act is True
    natural.train.assert_called_once_with(UnitTypeId.QUEEN)
    main.train.assert_not_called()


def test_a_townhall_at_its_own_cap_is_skipped_even_if_idle() -> None:
    """With only one base up, `max_per_townhall` is what actually stops a
    2nd Queen from over-filling it once the combined target allows more
    (e.g. an `extra` slot meant for a later base)."""
    pos = Point2((10.0, 10.0))
    main = _townhall(1, pos)
    queens = [_queen(pos), _queen(pos)]  # already at max_per_townhall

    ai = _ai(townhalls=[main], queens=queens)
    mediator = MagicMock()

    did_act = TrainQueens(to_count=3, max_per_townhall=2).execute(ai, {}, mediator)

    assert did_act is False
    main.train.assert_not_called()


def test_first_queen_trains_from_the_only_ready_townhall() -> None:
    main = _townhall(1, Point2((10.0, 10.0)))
    ai = _ai(townhalls=[main], queens=[])
    mediator = MagicMock()

    did_act = TrainQueens(to_count=1, max_per_townhall=1).execute(ai, {}, mediator)

    assert did_act is True
    main.train.assert_called_once_with(UnitTypeId.QUEEN)


def test_stops_once_the_combined_target_is_met() -> None:
    main = _townhall(1, Point2((10.0, 10.0)))
    ai = _ai(townhalls=[main], queens=[_queen(Point2((10.0, 10.0)))])
    mediator = MagicMock()

    did_act = TrainQueens(to_count=1, max_per_townhall=2).execute(ai, {}, mediator)

    assert did_act is False
    main.train.assert_not_called()


def test_a_busy_townhall_is_skipped_for_an_idle_one_with_more_queens() -> None:
    """Fairness (fewest queens first) only decides *priority* - a townhall
    still has to be idle to actually train anything this frame."""
    main_pos = Point2((10.0, 10.0))
    natural_pos = Point2((50.0, 50.0))
    main = _townhall(1, main_pos, idle=False)  # already training something
    natural = _townhall(2, natural_pos, idle=True)
    queens = [_queen(natural_pos), _queen(natural_pos)]  # natural already has 2

    ai = _ai(townhalls=[main, natural], queens=queens)
    mediator = MagicMock()

    did_act = TrainQueens(to_count=5, max_per_townhall=3).execute(ai, {}, mediator)

    assert did_act is True
    natural.train.assert_called_once_with(UnitTypeId.QUEEN)
    main.train.assert_not_called()


# --- cooldown_state: fixing realtime's stale-observation double-train -----


def test_cooldown_blocks_a_second_train_against_the_same_stale_observation() -> None:
    """Regression test for the exact user report: "the queen in the main
    didn't build". In realtime play, ares can call `execute()` several
    times before an already-issued `.train()` shows up in `already_
    pending`/`is_idle` - confirmed live, three Queens landed at the same
    townhall within 0.1 real seconds because every call still saw
    `existing=0, pending=0` and both townholds still `is_idle`. Simulating
    that exact staleness here (two calls, identical state, `ai.time`
    barely advanced) must train at most once."""
    main = _townhall(1, Point2((10.0, 10.0)))
    natural = _townhall(2, Point2((50.0, 50.0)))
    ai = _ai(townhalls=[main, natural], queens=[])
    ai.time = 100.0
    cooldown: dict[str, float] = {}

    first = TrainQueens(to_count=3, max_per_townhall=2, cooldown_state=cooldown)
    did_act_1 = first.execute(ai, {}, MagicMock())

    ai.time = 100.05  # same stale observation, a beat later - real bug's timing
    second = TrainQueens(to_count=3, max_per_townhall=2, cooldown_state=cooldown)
    did_act_2 = second.execute(ai, {}, MagicMock())

    assert did_act_1 is True
    assert did_act_2 is False
    assert main.train.call_count + natural.train.call_count == 1


def test_cooldown_allows_training_again_once_the_window_passes() -> None:
    main = _townhall(1, Point2((10.0, 10.0)))
    ai = _ai(townhalls=[main], queens=[])
    ai.time = 100.0
    cooldown: dict[str, float] = {}

    TrainQueens(to_count=2, max_per_townhall=2, cooldown_state=cooldown).execute(
        ai, {}, MagicMock()
    )

    ai.time = 103.0  # past the 2s default cooldown_seconds
    did_act = TrainQueens(
        to_count=2, max_per_townhall=2, cooldown_state=cooldown
    ).execute(ai, {}, MagicMock())

    assert did_act is True
    assert main.train.call_count == 2


def test_cooldown_disabled_when_no_state_dict_is_passed() -> None:
    """`cooldown_state=None` (the default) must not change behavior for any
    existing caller that doesn't opt in."""
    main = _townhall(1, Point2((10.0, 10.0)))
    ai = _ai(townhalls=[main], queens=[])
    ai.time = 100.0

    did_act = TrainQueens(to_count=1, max_per_townhall=1).execute(ai, {}, MagicMock())

    assert did_act is True
    main.train.assert_called_once_with(UnitTypeId.QUEEN)


# --- home_townhall: fixing "wandering for an inject" miscounting -----------


def test_without_home_townhall_the_visiting_queen_bug_reproduces() -> None:
    """Regression test for the exact user report: "the natural doesn't
    consistently get a queen". Both of main's own two Queens were
    correctly trained there, but `InjectLarva` sends the closest
    *available* Queen to whichever townhall needs an inject next, not
    necessarily its own trainer - so one of them can be standing at the
    natural doing an inject at the exact moment this recomputes counts
    from live position. That miscounts it as `{main: 1, natural: 1}` (a
    tie), and the stable sort's list-order tiebreak hands main a 3rd
    Queen instead of ever giving the natural one of its own."""
    main_pos = Point2((10.0, 10.0))
    natural_pos = Point2((50.0, 50.0))
    main = _townhall(1, main_pos)
    natural = _townhall(2, natural_pos)
    queen_at_home = _queen(main_pos, tag=101)
    # trained at main, but currently standing at natural mid-inject
    queen_visiting_natural = _queen(natural_pos, tag=102)

    ai = _ai(
        townhalls=[main, natural], queens=[queen_at_home, queen_visiting_natural]
    )
    mediator = MagicMock()

    did_act = TrainQueens(to_count=3, max_per_townhall=2).execute(ai, {}, mediator)

    assert did_act is True
    main.train.assert_called_once_with(UnitTypeId.QUEEN)
    natural.train.assert_not_called()


def test_home_townhall_credits_a_visiting_queen_to_where_it_was_trained() -> None:
    """Same setup as the bug reproduction above, with `home_townhall`
    recording both Queens as trained at main - counts correctly read
    `{main: 2, natural: 0}`, so the natural (not main) wins the next
    slot."""
    main_pos = Point2((10.0, 10.0))
    natural_pos = Point2((50.0, 50.0))
    main = _townhall(1, main_pos)
    natural = _townhall(2, natural_pos)
    queen_at_home = _queen(main_pos, tag=101)
    queen_visiting_natural = _queen(natural_pos, tag=102)

    ai = _ai(
        townhalls=[main, natural], queens=[queen_at_home, queen_visiting_natural]
    )
    mediator = MagicMock()

    did_act = TrainQueens(
        to_count=3,
        max_per_townhall=2,
        home_townhall={101: main.tag, 102: main.tag},
    ).execute(ai, {}, mediator)

    assert did_act is True
    natural.train.assert_called_once_with(UnitTypeId.QUEEN)
    main.train.assert_not_called()


def test_home_townhall_falls_back_to_live_position_for_an_untracked_queen() -> None:
    """A Queen with no entry in `home_townhall` (e.g. one that existed
    before this tracking was added) still gets counted via its live
    position, same as the original behavior."""
    main_pos = Point2((10.0, 10.0))
    natural_pos = Point2((50.0, 50.0))
    main = _townhall(1, main_pos)
    natural = _townhall(2, natural_pos)
    untracked_queen = _queen(main_pos, tag=1)

    ai = _ai(townhalls=[main, natural], queens=[untracked_queen])
    mediator = MagicMock()

    did_act = TrainQueens(
        to_count=3, max_per_townhall=2, home_townhall={}
    ).execute(ai, {}, mediator)

    assert did_act is True
    natural.train.assert_called_once_with(UnitTypeId.QUEEN)
    main.train.assert_not_called()


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
