"""Tests for the 4-tier local test harness (scripts/harness/harness_common.py).

The harness itself launches real StarCraft II matches, which these tests
don't touch — only the pure planning/reporting logic (game-plan
construction, build->race resolution, report field discovery) is covered.

Runs under pytest, or standalone with no test dependency:

    python -m tests.harness.test_harness_common
"""

from __future__ import annotations

import random
import sys
from dataclasses import replace

from sc2.data import Difficulty, Race

from scripts.harness import harness_common as hc

MAPS_7 = [
    "IncorporealAIE_v4",
    "LeyLinesAIE_v3",
    "MagannathaAIE_v2",
    "PersephoneAIE_v4",
    "PylonAIE_v4",
    "TorchesAIE_v4",
    "UltraloveAIE_v2",
]
RACES_3 = (Race.Terran, Race.Zerg, Race.Protoss)


def test_smoke_plan_has_one_game_per_race_on_distinct_maps() -> None:
    plan = hc.build_game_plan(hc.SMOKE, MAPS_7, RACES_3, random.Random(1))

    assert len(plan) == 3
    assert {race for _, race in plan} == set(RACES_3)
    maps_used = [m for m, _ in plan]
    assert len(set(maps_used)) == 3, "smoke should not repeat a map across games"
    assert all(m in MAPS_7 for m in maps_used)


def test_smoke_plan_repeats_maps_when_pool_smaller_than_race_count() -> None:
    plan = hc.build_game_plan(hc.SMOKE, ["OnlyOneMap"], RACES_3, random.Random(1))

    assert len(plan) == 3
    assert all(m == "OnlyOneMap" for m, _ in plan)


def test_quick_plan_is_a_single_game_on_a_random_map_and_race() -> None:
    plan = hc.build_game_plan(hc.QUICK, MAPS_7, RACES_3, random.Random(1))

    assert len(plan) == 1
    map_name, race = plan[0]
    assert map_name in MAPS_7
    assert race in RACES_3


def test_quick_plan_race_choice_is_random_not_fixed() -> None:
    plan_a = hc.build_game_plan(hc.QUICK, MAPS_7, RACES_3, random.Random(1))
    plan_b = hc.build_game_plan(hc.QUICK, MAPS_7, RACES_3, random.Random(2))

    assert plan_a != plan_b, (
        "two different seeds should not coincidentally pick an identical "
        "(map, race) pair"
    )


def test_quick_plan_honors_a_races_override_for_targeted_testing() -> None:
    plan = hc.build_game_plan(hc.QUICK, MAPS_7, (Race.Zerg,), random.Random(1))

    assert plan == [(plan[0][0], Race.Zerg)]


def test_sanity_plan_has_one_game_per_map_exactly() -> None:
    plan = hc.build_game_plan(hc.SANITY, MAPS_7, RACES_3, random.Random(2))

    assert len(plan) == len(MAPS_7)
    assert [m for m, _ in plan] == MAPS_7, "every map covered, in order, once each"
    assert all(race in RACES_3 for _, race in plan)


def test_sanity_plan_opponent_choice_is_random_not_fixed() -> None:
    plan_a = hc.build_game_plan(hc.SANITY, MAPS_7, RACES_3, random.Random(1))
    plan_b = hc.build_game_plan(hc.SANITY, MAPS_7, RACES_3, random.Random(2))

    assert [r for _, r in plan_a] != [r for _, r in plan_b], (
        "two different seeds should not coincidentally pick identical "
        "opponent sequences across 7 independent choices"
    )


def test_regression_plan_is_the_full_map_by_race_matrix() -> None:
    plan = hc.build_game_plan(hc.REGRESSION, MAPS_7, RACES_3, random.Random(3))

    assert len(plan) == len(MAPS_7) * len(RACES_3) == 21
    assert set(plan) == {(m, r) for m in MAPS_7 for r in RACES_3}
    assert len(set(plan)) == 21, "every (map, race) pair appears exactly once"


def test_build_game_plan_rejects_empty_maps() -> None:
    try:
        hc.build_game_plan(hc.SMOKE, [], RACES_3, random.Random(1))
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_build_game_plan_rejects_empty_races() -> None:
    try:
        hc.build_game_plan(hc.SMOKE, MAPS_7, (), random.Random(1))
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_build_game_plan_rejects_unsupported_strategy_combo() -> None:
    # All 4 (map_strategy, opponent_strategy) combos the Literal types allow
    # are supported - this has to reach for a value outside those Literals
    # to exercise the fallback at all.
    weird_tier = replace(hc.SMOKE, map_strategy="diagonal")
    try:
        hc.build_game_plan(weird_tier, MAPS_7, RACES_3, random.Random(1))
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_pick_distinct_samples_without_repeats_when_pool_is_large_enough() -> None:
    picks = hc._pick_distinct(MAPS_7, 3, random.Random(1))
    assert len(picks) == 3
    assert len(set(picks)) == 3


def test_pick_distinct_allows_repeats_when_pool_is_too_small() -> None:
    picks = hc._pick_distinct(["A", "B"], 5, random.Random(1))
    assert len(picks) == 5
    assert set(picks) <= {"A", "B"}


def test_resolve_build_race_finds_the_protoss_chargelot_build() -> None:
    assert hc.resolve_build_race("2base Chargelot All-In") is Race.Protoss


def test_resolve_build_race_finds_the_terran_build() -> None:
    assert hc.resolve_build_race("Four Rax Proxy") is Race.Terran


def test_resolve_build_race_is_case_insensitive() -> None:
    assert hc.resolve_build_race("four rax proxy") is Race.Terran


def test_resolve_build_race_rejects_unknown_build() -> None:
    try:
        hc.resolve_build_race("Not A Real Build")
        assert False, "expected SystemExit"
    except SystemExit as exc:
        assert "Not A Real Build" in str(exc)


def test_slug_normalizes_spaces_and_case() -> None:
    assert hc._slug("2base Chargelot All-In") == "2base_chargelot_all_in"


def test_discover_fields_unions_metrics_keys_in_first_seen_order() -> None:
    rows = [
        hc.GameRow(
            tier="smoke",
            opponent="Terran",
            map_name="A",
            result="Victory",
            metrics={"warpgate_peak": 8, "prism_produced": True},
        ),
        hc.GameRow(
            tier="smoke",
            opponent="Zerg",
            map_name="B",
            result="Defeat",
            metrics={"prism_produced": False, "scout_kills": 2},
        ),
    ]

    fields = hc.discover_fields(rows)

    assert fields == [
        *hc.BASE_FIELDS,
        "warpgate_peak",
        "prism_produced",
        "scout_kills",
    ]


def test_row_dict_merges_metrics_into_the_base_columns() -> None:
    row = hc.GameRow(
        tier="smoke",
        opponent="Protoss",
        map_name="Pylon",
        result="Victory",
        elapsed_wall_s=12.3,
        metrics={"warpgate_peak": 8},
    )

    out = hc._row_dict(row)

    assert out["opponent"] == "Protoss"
    assert out["map"] == "Pylon"
    assert out["result"] == "Victory"
    assert out["error"] == ""
    assert out["elapsed_wall_s"] == 12.3
    assert out["warpgate_peak"] == 8


def test_game_row_ok_is_false_after_a_crash() -> None:
    row = hc.GameRow(
        tier="smoke",
        opponent="Terran",
        map_name="A",
        result="CRASH",
        error="RuntimeError: boom",
    )
    assert row.ok is False


def test_tier_specs_match_the_documented_counts() -> None:
    assert hc.QUICK.expected_games == 1
    assert hc.SMOKE.expected_games == 3
    assert hc.SANITY.expected_games == 7
    assert hc.REGRESSION.expected_games == 21
    assert hc.QUICK.difficulty is Difficulty.Medium
    assert hc.SMOKE.difficulty is Difficulty.Easy
    assert hc.SANITY.difficulty is Difficulty.Medium
    assert hc.REGRESSION.difficulty is Difficulty.Hard


def test_quick_tier_defaults_to_random_map_and_race() -> None:
    assert hc.QUICK.map_strategy == "random"
    assert hc.QUICK.opponent_strategy == "random"
    assert hc.QUICK.races is None, "no fixed race - random across ALL_RACES"


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
