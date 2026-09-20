"""Thin intel package: army feed, OpponentId notes, seen-tech, QA helpers.

Scouting routines stay in ot.routines.scouting - this package is the
read/state layer combat and QA consume.
"""

from bot.intel.army import (
    army_behind_on_supply,
    enemy_army,
    enemy_army_supply,
    enemy_army_tags,
    enemy_army_type_ids,
    enemy_has_air_units,
    filter_non_workers,
    early_aggression,
    leave_army_supply,
    leave_enemy_army_supply,
    observe_leave_intel,
)
from bot.intel.notes import OpponentNotes, get_opponent_id
from bot.intel.qa import (
    idle_ready_townhalls,
    influence_parking_tags,
    is_supply_blocked,
    units_parked_in_influence,
)
from bot.intel.tech import SeenTech, observe_seen_tech

__all__ = [
    "OpponentNotes",
    "SeenTech",
    "army_behind_on_supply",
    "enemy_army",
    "enemy_army_supply",
    "enemy_army_tags",
    "enemy_army_type_ids",
    "early_aggression",
    "enemy_has_air_units",
    "filter_non_workers",
    "get_opponent_id",
    "idle_ready_townhalls",
    "influence_parking_tags",
    "is_supply_blocked",
    "leave_enemy_army_supply",
    "observe_leave_intel",
    "observe_seen_tech",
    "units_parked_in_influence",
]
