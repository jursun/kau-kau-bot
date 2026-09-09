"""Shared constants for KauKauBot."""

from __future__ import annotations

from sc2.data import race_townhalls
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

# Every townhall type across all races — used when hunting enemy bases.
ALL_TOWNHALL_TYPES: frozenset[UnitTypeId] = frozenset(
    th for types in race_townhalls.values() for th in types
)

WORKER_TYPES: frozenset[UnitTypeId] = frozenset(
    {UnitTypeId.SCV, UnitTypeId.PROBE, UnitTypeId.DRONE, UnitTypeId.MULE}
)

# Cheap early aggression we want to collapse on rather than run from.
HARASS_TYPES: frozenset[UnitTypeId] = frozenset(
    {UnitTypeId.REAPER, UnitTypeId.ADEPT, UnitTypeId.ZERGLING, UnitTypeId.ZEALOT}
)

# Mass-ling army composition. `SpawnController` requires proportions summing
# to 1.0, with 0 being the highest priority.
MASS_LING_COMP: dict[UnitTypeId, dict[str, float | int]] = {
    UnitTypeId.ZERGLING: {"proportion": 1.0, "priority": 0},
}

# Ordered upgrade path. `UpgradeController` walks this list in order and
# auto-techs (evo chamber -> lair -> hive) for whatever it can't research yet.
LING_SPEED_ONLY: tuple[UpgradeId, ...] = (UpgradeId.ZERGLINGMOVEMENTSPEED,)

FULL_LING_UPGRADES: tuple[UpgradeId, ...] = (
    UpgradeId.ZERGLINGMOVEMENTSPEED,
    UpgradeId.ZERGMELEEWEAPONSLEVEL1,
    UpgradeId.ZERGGROUNDARMORSLEVEL1,
    UpgradeId.ZERGMELEEWEAPONSLEVEL2,
    UpgradeId.ZERGGROUNDARMORSLEVEL2,
    UpgradeId.ZERGMELEEWEAPONSLEVEL3,
    UpgradeId.ZERGGROUNDARMORSLEVEL3,
    UpgradeId.ZERGLINGATTACKSPEED,
)

# Attack focus keywords understood by CombatManager._focus_points().
FOCUS_MAIN: str = "main"
FOCUS_NATURAL: str = "natural"
FOCUS_THIRD: str = "third"
