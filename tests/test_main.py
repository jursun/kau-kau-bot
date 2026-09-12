"""Regression tests for `bot/main.py`'s realtime duplicate-log guard.

`_completion_message` exists because `on_building_construction_complete`
can fire more than once for the same structure when playing in realtime -
see that hook's own docstring in `bot/main.py` for the exact mechanism
(ares skips refreshing python-sc2's previous-frame snapshot for up to 4
game loops, but the outer loop keeps calling the hook's trigger every
iteration regardless). Pulled out as a plain function, tested here with a
bare `set` and `MagicMock` units, rather than exercising the real
`AresBot`/`KauKauBot` construction chain.

Runs under pytest, or standalone with no test dependency:

    python -m tests.test_main
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from sc2.ids.unit_typeid import UnitTypeId

from bot.main import _completion_message


def _structure(tag: int, type_id: UnitTypeId) -> MagicMock:
    unit = MagicMock()
    unit.tag = tag
    unit.type_id = type_id
    return unit


def test_logs_a_structure_seen_for_the_first_time() -> None:
    logged: set[int] = set()
    barracks = _structure(1, UnitTypeId.BARRACKS)

    message = _completion_message(logged, barracks, count=1)

    assert message == "COMPLETE barracks (1)"
    assert logged == {1}


def test_does_not_log_the_same_tag_twice() -> None:
    """The whole point of this round's fix: a repeat call for a tag already
    logged - exactly what realtime's duplicate hook firing produces -
    must not log again."""
    logged = {1}
    barracks = _structure(1, UnitTypeId.BARRACKS)

    message = _completion_message(logged, barracks, count=1)

    assert message is None
    assert logged == {1}, "must not be removed or altered on a repeat call"


def test_ignores_a_structure_type_nobody_logs() -> None:
    logged: set[int] = set()
    refinery = _structure(2, UnitTypeId.REFINERY)

    message = _completion_message(logged, refinery, count=1)

    assert message is None
    assert logged == set(), "an unlogged type must not be added to the set either"


def test_two_different_structures_both_log() -> None:
    logged: set[int] = set()
    first = _structure(1, UnitTypeId.SUPPLYDEPOT)
    second = _structure(2, UnitTypeId.SUPPLYDEPOT)

    first_message = _completion_message(logged, first, count=1)
    second_message = _completion_message(logged, second, count=2)

    assert first_message == "COMPLETE supplydepot (1)"
    assert second_message == "COMPLETE supplydepot (2)"
    assert logged == {1, 2}


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
