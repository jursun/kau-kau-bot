"""Cross-checks every registered build against its `<race>_builds.yml`.

At dozens of builds the likeliest bug is name drift between the Python
registry and the YAML ares actually reads — a mismatch that would otherwise
surface only as "why is it playing the wrong build?" mid-game.

Runs under pytest, or standalone with no test dependency:

    python -m tests.test_builds
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from sc2.data import Race
from sc2.dicts.upgrade_researched_from import UPGRADE_RESEARCHED_FROM

from bot.core.registry import RACE_PACKAGES, UnknownBuild, all_builds, get_build

REPO_ROOT = Path(__file__).resolve().parent.parent


def builds_yaml(race: Race) -> dict | None:
    """Ares looks for `<race>_builds.yml` in the repo root, lowercase."""
    path = REPO_ROOT / f"{race.name.lower()}_builds.yml"
    if not path.is_file():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_registry_is_not_empty() -> None:
    assert all_builds(), "no builds discovered"


def test_every_build_has_a_yaml_opening() -> None:
    for name, build in all_builds().items():
        config = builds_yaml(build.race)
        assert config is not None, (
            f"{name} is registered but {build.race.name.lower()}_builds.yml "
            f"does not exist"
        )
        assert name in config.get("Builds", {}), (
            f"{name} has no opening in {build.race.name.lower()}_builds.yml"
        )


def test_every_yaml_opening_has_a_build() -> None:
    for race in RACE_PACKAGES:
        config = builds_yaml(race)
        if config is None:
            continue
        for opening in config.get("Builds", {}):
            get_build(opening, race)  # raises UnknownBuild if missing


def test_build_choices_reference_known_openings() -> None:
    for race in RACE_PACKAGES:
        config = builds_yaml(race)
        if config is None:
            continue
        openings = set(config.get("Builds", {}))
        for key, choice in config.get("BuildChoices", {}).items():
            for opening in choice.get("Cycle", []):
                assert opening in openings, (
                    f"{race.name}: BuildChoices[{key}] references unknown "
                    f"opening {opening!r}"
                )


def test_army_comp_proportions_sum_to_one() -> None:
    for name, build in all_builds().items():
        total = sum(v["proportion"] for v in build.army.comp.values())
        assert abs(total - 1.0) < 1e-6, f"{name}: proportions sum to {total}"


def test_army_types_are_declared() -> None:
    for name, build in all_builds().items():
        assert build.army.types, f"{name}: army.types is empty"


def test_upgrades_are_researchable() -> None:
    for name, build in all_builds().items():
        for upgrade in build.army.upgrades:
            assert upgrade in UPGRADE_RESEARCHED_FROM, (
                f"{name}: {upgrade} has no UPGRADE_RESEARCHED_FROM entry"
            )


def test_steps_and_routines_are_callable() -> None:
    for name, build in all_builds().items():
        for step in (*build.macro_steps, *build.always):
            assert callable(step), f"{name}: non-callable macro step {step!r}"
        for routine in build.combat.routines:
            assert callable(routine), f"{name}: non-callable routine {routine!r}"
        assert callable(build.combat.wave_gate), f"{name}: wave_gate not callable"


def test_unknown_build_raises() -> None:
    try:
        get_build("NoSuchBuild", Race.Zerg)
    except UnknownBuild:
        return
    raise AssertionError("get_build silently accepted an unknown name")


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
    known = ", ".join(sorted(all_builds())) or "(none)"
    print(f"\n{len(tests) - failures}/{len(tests)} passed. Registered builds: {known}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
