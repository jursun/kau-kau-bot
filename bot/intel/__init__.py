"""Thin intel package: army feed, OpponentId notes, seen-tech.

Scouting routines stay in `bot.routines.scouting` — this package is the
read/state layer combat and QA consume.
"""

from bot.intel.army import (
    enemy_army,
    enemy_army_tags,
    enemy_army_type_ids,
    filter_non_workers,
)
from bot.intel.notes import OpponentNotes, get_opponent_id
from bot.intel.tech import SeenTech, observe_seen_tech

__all__ = [
    "OpponentNotes",
    "SeenTech",
    "enemy_army",
    "enemy_army_tags",
    "enemy_army_type_ids",
    "filter_non_workers",
    "get_opponent_id",
    "observe_seen_tech",
]
