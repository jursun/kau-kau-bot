"""Build discovery.

Every module under `bot/builds/<race>/` that defines a module-level `BUILD`
is registered automatically, so adding a build means adding one file — no
registry edit, and no merge conflict when several land at once.
"""

from __future__ import annotations

import importlib
import pkgutil
from functools import lru_cache

from sc2.data import Race

from bot.builds.definition import BuildDefinition

RACE_PACKAGES: dict[Race, str] = {
    Race.Zerg: "bot.builds.zerg",
    Race.Terran: "bot.builds.terran",
    Race.Protoss: "bot.builds.protoss",
}


class UnknownBuild(KeyError):
    """Raised when an opening name has no matching BuildDefinition."""


@lru_cache(maxsize=1)
def all_builds() -> dict[str, BuildDefinition]:
    """Import every build module once and index the results by name."""
    found: dict[str, BuildDefinition] = {}
    for race, package_name in RACE_PACKAGES.items():
        package = importlib.import_module(package_name)
        for module_info in pkgutil.iter_modules(package.__path__):
            if module_info.name.startswith("_"):
                continue
            module = importlib.import_module(f"{package_name}.{module_info.name}")
            build = getattr(module, "BUILD", None)
            if build is None:
                continue
            if not isinstance(build, BuildDefinition):
                raise TypeError(
                    f"{package_name}.{module_info.name}.BUILD is "
                    f"{type(build).__name__}, expected BuildDefinition"
                )
            if build.race is not race:
                raise ValueError(
                    f"{build.name} declares race {build.race} but lives in "
                    f"{package_name}"
                )
            if build.name in found:
                raise ValueError(f"duplicate build name: {build.name}")
            found[build.name] = build
    return found


def builds_for_race(race: Race) -> dict[str, BuildDefinition]:
    return {n: b for n, b in all_builds().items() if b.race is race}


def get_build(name: str, race: Race) -> BuildDefinition:
    """Look up an opening by name. Raises rather than silently substituting.

    A silent fallback here is how a typo in `<race>_builds.yml` turns into an
    afternoon of debugging the wrong build.
    """
    builds = all_builds()
    if name in builds:
        return builds[name]

    lowered = name.lower() if name else ""
    for build_name, build in builds.items():
        if build_name.lower() == lowered:
            return build

    available = sorted(builds_for_race(race))
    raise UnknownBuild(
        f"No BuildDefinition named {name!r} for {race.name}. "
        f"Known {race.name} builds: {available or '(none)'}. "
        f"Opening names in <race>_builds.yml must match a BUILD.name."
    )


def default_build(race: Race) -> BuildDefinition:
    """Last-resort pick so a name mismatch cannot end a ladder game."""
    builds = builds_for_race(race)
    if not builds:
        raise UnknownBuild(f"No builds registered for {race.name}")
    return builds[sorted(builds)[0]]
