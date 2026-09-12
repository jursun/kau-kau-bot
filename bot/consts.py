"""Shared constants for KauKauBot."""

from __future__ import annotations

from ares.consts import UnitRole
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

# Mass-marine army composition, for bio all-ins that make nothing else.
MASS_MARINE_COMP: dict[UnitTypeId, dict[str, float | int]] = {
    UnitTypeId.MARINE: {"proportion": 1.0, "priority": 0},
}

# 2-base Chargelot: Zealot flood with a Stalker spine, plus Robo support.
# Proportions sum to 1.0; lower priority int = SpawnController builds first.
CHARGELOT_COMP: dict[UnitTypeId, dict[str, float | int]] = {
    UnitTypeId.ZEALOT: {"proportion": 0.70, "priority": 1},
    UnitTypeId.STALKER: {"proportion": 0.20, "priority": 0},
    UnitTypeId.WARPPRISM: {"proportion": 0.05, "priority": 0},
    UnitTypeId.OBSERVER: {"proportion": 0.05, "priority": 0},
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

# Zerg production units, not real targets - shared by `routines.combat`
# (which combat units treat as never-worth-shooting) and `routines.targeting`
# (which likewise never treats one as "a defender worth attacking instead of
# the structure"). Only Zerg ever fields these, so there is nothing to gate
# on "when attacking Zerg" - filtering them out unconditionally already has
# that exact effect against every other race, where the filter simply never
# matches anything.
IGNORED_ENEMY_TYPES: frozenset[UnitTypeId] = frozenset(
    {UnitTypeId.EGG, UnitTypeId.LARVA}
)

# Role for `ProxyCrewPlan` workers (`steps.terran.proxy_crew`). Unused by
# ares itself — `BUILDING` / `PERSISTENT_BUILDER` get swept back to GATHERING
# when a structure finishes or the opening completes, which would yank a
# multi-step crew SCV mid-choreography.
PROXY_CREW_ROLE: UnitRole = UnitRole.GATE_KEEPER

# Role for the continuous Depot SCV (`steps.terran.continuous_main_depots`).
# Same unused-slot trick: must not sit in GATHERING or BUILDING.
SUPPLY_BUILDER_ROLE: UnitRole = UnitRole.CONTROL_GROUP_ONE
