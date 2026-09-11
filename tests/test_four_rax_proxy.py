"""Regression tests for `bot.builds.terran.four_rax_proxy` placement
choices on the proxy crew:

* Barracks C's crew task - must place via `WorkerTask.near=proxy_location`
  (the enemy fourth's townhall tile) rather than ares' Barracks formation
  around that base.
* The proxy Depot - must use ares' formation at `proxy_location` (`near` is
  unset), not a ring search next to standing Barracks.

Runs under pytest, or standalone with no test dependency:

    python -m tests.test_four_rax_proxy
"""

from __future__ import annotations

import sys

from bot.builds.terran.four_rax_proxy import PROXY_CREW, proxy_location


def test_barracks_c_task_places_on_the_enemy_fourth_townhall_tile() -> None:
    """Barracks C must bypass ares' formation via `near=proxy_location` so
    it lands on the expansion's townhall centre, not a precomputed Barracks
    slot that competes with A and B."""
    barracks_c = PROXY_CREW.z_tasks[1]

    assert barracks_c.label == "Barracks C"
    assert barracks_c.where is proxy_location
    assert barracks_c.near is proxy_location


def test_proxy_depot_uses_ares_formation_at_the_enemy_fourth() -> None:
    depot = PROXY_CREW.y_tasks[1]

    assert depot.label == "Depot (proxy)"
    assert depot.where is proxy_location
    assert depot.near is None


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
