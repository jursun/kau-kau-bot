"""WORKER-filtered enemy army feed for combat / QA.

`mediator.get_cached_enemy_army` includes workers. Callers that need the
fighting force must strip them. Prefer tags (`enemy_army_tags`) for anything
that survives a frame — never stash Unit objects.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from bot.consts import WORKER_TYPES
from bot.core.context import BotContext


def _cached_army(ctx: BotContext) -> Sequence[Any]:
    army = ctx.mediator.get_cached_enemy_army
    if army is None:
        return ()
    return army


def enemy_army(ctx: BotContext) -> list[Any]:
    """This-frame enemy combat units (workers stripped)."""
    return [unit for unit in _cached_army(ctx) if unit.type_id not in WORKER_TYPES]


def enemy_army_tags(ctx: BotContext) -> frozenset[int]:
    """Persistable tags for the filtered army — never Unit refs."""
    return frozenset(unit.tag for unit in enemy_army(ctx))


def enemy_army_type_ids(ctx: BotContext) -> frozenset[Any]:
    """Type ids present in the filtered army this frame."""
    return frozenset(unit.type_id for unit in enemy_army(ctx))


def filter_non_workers(units: Iterable[Any]) -> list[Any]:
    """Same worker strip for an arbitrary unit iterable."""
    return [unit for unit in units if unit.type_id not in WORKER_TYPES]
