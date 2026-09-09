"""Regression tests for ZergRushValidator's two counting bugs.

Both bugs share a shape: a milestone counter that only ever grows
(`_pool_count`, `_supply_blocked_frames`) latches a bad early reading for
the rest of the game. Drives `on_step` across fake frames with a minimal
duck-typed stand-in for the BotAI surface it reads, then inspects the
real `validate()` output.

Runs under pytest, or standalone with no test dependency:

    python -m tests.test_zerg_rush_validator
"""

from __future__ import annotations

import asyncio
import sys

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from tests.zerg_rush_validator import ZergRushValidator


class _Counted:
    """Stands in for a `Units` collection: `.amount`, truthiness, iteration."""

    def __init__(self, amount: int = 0):
        self.amount = amount

    def __bool__(self) -> bool:
        return self.amount > 0

    def __iter__(self):
        return iter(())


class FakeAI(ZergRushValidator):
    """Just enough of the BotAI surface for `ZergRushValidator.on_step`."""

    def __init__(self):
        self.time = 0.0
        self.supply_left = 10
        self.supply_used = 14
        self.workers = _Counted(12)
        self.gas_buildings = _Counted(0)
        self.start_location = None
        self.enemy_start_locations = []
        self._structure_counts: dict[UnitTypeId, int] = {}
        self._pending_counts: dict[UnitTypeId, int] = {}

    def structures(self, unit_type: UnitTypeId) -> _Counted:
        return _Counted(self._structure_counts.get(unit_type, 0))

    def units(self, unit_type: UnitTypeId) -> _Counted:
        return _Counted(0)

    def already_pending(self, unit_type: UnitTypeId) -> int:
        return self._pending_counts.get(unit_type, 0)

    def already_pending_upgrade(self, upgrade: UpgradeId) -> int:
        return 0


def _step(ai: FakeAI) -> None:
    asyncio.run(ai.on_step(0))


def test_pool_under_construction_is_not_double_counted() -> None:
    """A single pool, mid-build, must not read as `pool count: 2`.

    Regression test for the `pool_count + pending` bug: while a structure
    is placed but not yet ready, `already_pending` counts it (its own
    docstring: "buildings already in progress") in addition to
    `structures(...).amount` counting the same building.
    """
    ai = FakeAI()

    # Frame the pool is placed: it exists (structures=1) AND is still
    # "in progress" (already_pending also reports 1) — this is the exact
    # state a real mid-build pool sits in, not a contrived edge case.
    ai._structure_counts[UnitTypeId.SPAWNINGPOOL] = 1
    ai._pending_counts[UnitTypeId.SPAWNINGPOOL] = 1
    _step(ai)

    # Keep stepping while it finishes building (still not "pending" once
    # already_pending would naturally drop away is irrelevant here — the
    # bug already latched, if it exists, on the very first frame above).
    for _ in range(5):
        _step(ai)

    result = ai.validate()
    only_one_pool = next(
        r for r in result["Stage 2: The Rush Core"] if r.name == "Only One Pool"
    )
    assert only_one_pool.passed, only_one_pool.detail


def test_supply_block_within_grace_period_is_ignored() -> None:
    ai = FakeAI()
    ai.supply_left = 0
    for frame in range(200):  # well under SUPPLY_BLOCK_GRACE_PERIOD
        ai.time = frame * 0.1  # up to 20.0s
        _step(ai)

    assert ai._supply_blocked_frames == 0


def test_supply_block_after_grace_period_still_counts() -> None:
    ai = FakeAI()
    ai.supply_left = 0
    for frame in range(1000):
        ai.time = 60.0 + frame * 0.1  # starts exactly at the grace period
        _step(ai)

    assert ai._supply_blocked_frames == 1000


def test_supply_block_after_pool_started_is_never_counted() -> None:
    """Once the pool has started, this Stage-1 metric stops tracking —
    unaffected by the grace period, and not something this fix changes."""
    ai = FakeAI()
    ai._structure_counts[UnitTypeId.SPAWNINGPOOL] = 1
    ai.time = 45.0
    _step(ai)
    assert ai._pool_started

    ai.supply_left = 0
    for frame in range(500):
        ai.time = 100.0 + frame * 0.1
        _step(ai)

    assert ai._supply_blocked_frames == 0


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
