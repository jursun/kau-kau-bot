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

# A label WE own for a build's `ProxyCrewPlan` workers (`steps.terran.
# proxy_crew`), picked specifically because nothing in ares itself ever
# reads or writes it (checked against the whole ares-sc2 v3.13.1 source, not
# just the obvious managers). That matters because the two roles that would
# otherwise fit the name both carry side effects fatal to a worker mid
# multi-step choreography:
#   - `UnitRole.BUILDING` gets reverted to GATHERING by
#     `BuildingManager._handle_construction_orders` the instant its tracked
#     structure completes - flinging a builder back into the mineral line one
#     frame after finishing Barracks A, long before its next task is issued.
#   - `UnitRole.PERSISTENT_BUILDER` gets swept back to GATHERING in one shot
#     by `BuildOrderRunner.set_build_completed()` the moment the opening's own
#     `OpeningBuildOrder` list is exhausted - which, for a build using this
#     mechanism, happens within the first few seconds, i.e. long before a
#     proxy crew's work is anywhere near done.
# `GATE_KEEPER` is simply an unused slot in `ares.consts.UnitRole`'s enum, the
# same trick `UnitRole.SCOUTING`/`UnitRole.QUEEN_INJECT` already use elsewhere
# in this codebase for "our own bookkeeping, not ares'".
PROXY_CREW_ROLE: UnitRole = UnitRole.GATE_KEEPER

# Same unused-slot trick as `PROXY_CREW_ROLE`: a dedicated SCV that keeps
# laying Supply Depots after an all-in (see `steps.terran.continuous_main_
# depots`) must not sit in `GATHERING` (Mining would reclaim it) or
# `BUILDING`/`PERSISTENT_BUILDER` (ares sweeps those back to minerals).
# `CONTROL_GROUP_ONE` is never written by ares itself.
SUPPLY_BUILDER_ROLE: UnitRole = UnitRole.CONTROL_GROUP_ONE
