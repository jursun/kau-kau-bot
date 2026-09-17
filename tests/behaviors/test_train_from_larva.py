"""Regression tests for `TrainFromLarva` / `pending_larva_trained`.

Runs under pytest, or standalone with no test dependency:

    python -m tests.behaviors.test_train_from_larva
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from sc2.ids.unit_typeid import UnitTypeId

from bot.behaviors.zerg import TrainFromLarva, pending_larva_trained


def _ai(amount: int = 0, pending: float = 0, afford: bool = True, has_larva: bool = True) -> MagicMock:
    ai = MagicMock()
    ai.units.return_value.amount = amount
    ai.already_pending.return_value = pending
    ai.can_afford.return_value = afford
    ai.units.return_value.__bool__ = lambda self: has_larva
    return ai


# --- pending_larva_trained: the 2-per-Egg Zergling correction ---------------


def test_pending_larva_trained_doubles_for_zergling() -> None:
    """One in-progress Egg (`already_pending` == 1, an accurate *order*
    count) yields 2 Zerglings - the caller's target counts individual
    Zerglings, so this must read 2, not 1."""
    ai = MagicMock()
    ai.already_pending.return_value = 1

    assert pending_larva_trained(ai, UnitTypeId.ZERGLING) == 2


def test_pending_larva_trained_passes_through_for_overlord() -> None:
    """Overlord morphs 1-for-1 from a single Egg - no correction needed."""
    ai = MagicMock()
    ai.already_pending.return_value = 1

    assert pending_larva_trained(ai, UnitTypeId.OVERLORD) == 1


# --- TrainFromLarva: the over-production bug this fixes ---------------------


def test_zergling_stops_after_one_egg_for_a_two_target() -> None:
    """Regression test for the exact user report: "we produced extra
    Zerglings (8 zerglings from 4 eggs)". Before `pending_larva_trained`,
    `to_count=2` was checked against raw `already_pending` (an Egg *order*
    count) - one in-progress Egg read as `pending=1`, so `have=0+1=1 < 2`
    never stopped a second Egg from queuing behind the first, before
    either had hatched."""
    ai = _ai(amount=0, pending=0)

    behavior = TrainFromLarva(unit_type=UnitTypeId.ZERGLING, to_count=2)
    did_act = behavior.execute(ai, {}, MagicMock())

    assert did_act is True
    larva = ai.units.return_value
    larva.first.train.assert_called_once_with(UnitTypeId.ZERGLING)

    # One Egg now in progress - `already_pending` correctly reports 1 order.
    ai.already_pending.return_value = 1
    second = TrainFromLarva(unit_type=UnitTypeId.ZERGLING, to_count=2)
    did_act_2 = second.execute(ai, {}, MagicMock())

    assert did_act_2 is False
    assert larva.first.train.call_count == 1


def test_zergling_four_target_needs_only_one_more_egg_once_two_are_live() -> None:
    """Same bug, later in the scripted sequence: once the first "2
    Zergling" step's Egg has hatched (2 live Zerglings, 0 pending), a "4
    Zergling" step should queue exactly one more Egg (worth 2 more
    Zerglings), not two."""
    ai = _ai(amount=2, pending=0)

    behavior = TrainFromLarva(unit_type=UnitTypeId.ZERGLING, to_count=4)
    did_act = behavior.execute(ai, {}, MagicMock())

    assert did_act is True
    larva = ai.units.return_value
    larva.first.train.assert_called_once_with(UnitTypeId.ZERGLING)

    # One Egg now in progress on top of the 2 already live.
    ai.already_pending.return_value = 1
    second = TrainFromLarva(unit_type=UnitTypeId.ZERGLING, to_count=4)
    did_act_2 = second.execute(ai, {}, MagicMock())

    assert did_act_2 is False
    assert larva.first.train.call_count == 1


def test_overlord_target_unaffected_by_the_zergling_correction() -> None:
    main = _ai(amount=1, pending=0)

    behavior = TrainFromLarva(unit_type=UnitTypeId.OVERLORD, to_count=2)
    did_act = behavior.execute(main, {}, MagicMock())

    assert did_act is True
    main.units.return_value.first.train.assert_called_once_with(UnitTypeId.OVERLORD)

    main.already_pending.return_value = 1
    second = TrainFromLarva(unit_type=UnitTypeId.OVERLORD, to_count=2)
    did_act_2 = second.execute(main, {}, MagicMock())

    assert did_act_2 is False
    assert main.units.return_value.first.train.call_count == 1


def test_no_larva_returns_false() -> None:
    ai = _ai(amount=0, pending=0, has_larva=False)

    did_act = TrainFromLarva(unit_type=UnitTypeId.ZERGLING, to_count=2).execute(
        ai, {}, MagicMock()
    )

    assert did_act is False


def test_cannot_afford_returns_false() -> None:
    ai = _ai(amount=0, pending=0, afford=False)

    did_act = TrainFromLarva(unit_type=UnitTypeId.ZERGLING, to_count=2).execute(
        ai, {}, MagicMock()
    )

    assert did_act is False


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
