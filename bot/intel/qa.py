"""Frame-level QA helpers for the influence-retreat ship gate.

Used by validators and post-game notes. Detects supply blocks, idle ready
townhalls, and ATTACKING units parked on unsafe ground influence.
"""

from __future__ import annotations

from typing import Any, Iterable

from ares.consts import UnitRole

def is_supply_blocked(bot: Any) -> bool:
    """True when supply_left is 0 (caller applies grace period)."""
    return getattr(bot, "supply_left", 1) == 0


def idle_ready_townhalls(bot: Any) -> list[Any]:
    """Ready townhalls that report idle this frame.

    Meaningful for Terran/Protoss only. Zerg hatcheries stay `is_idle` while
    larva morphs, so callers must race-gate before treating this as idle prod.
    """
    townhalls = getattr(bot, "townhalls", None)
    if townhalls is None:
        return []
    ready = getattr(townhalls, "ready", townhalls)
    return [th for th in ready if getattr(th, "is_idle", False)]


def attacking_units(bot: Any) -> list[Any]:
    """Current ATTACKING-role units (this frame only — do not stash)."""
    mediator = getattr(bot, "mediator", None)
    if mediator is None:
        return []
    getter = getattr(mediator, "get_units_from_role", None)
    if getter is None:
        ctx = getattr(bot, "ctx", None)
        if ctx is not None and hasattr(ctx, "units_in_role"):
            return list(ctx.units_in_role(UnitRole.ATTACKING))
        return []
    units = getter(role=UnitRole.ATTACKING)
    return list(units) if units is not None else []


def units_parked_in_influence(bot: Any, units: Iterable[Any] | None = None) -> list[Any]:
    """Units whose position is unsafe on the ground influence grid.

    Uses `mediator.is_position_safe` — the same predicate `KeepUnitSafe`
    consults before pathing off bad tiles. Empty if grid/mediator missing.
    """
    mediator = getattr(bot, "mediator", None)
    if mediator is None:
        return []
    grid = getattr(mediator, "get_ground_grid", None)
    is_safe = getattr(mediator, "is_position_safe", None)
    if grid is None or is_safe is None:
        return []
    group = list(units) if units is not None else attacking_units(bot)
    parked = []
    for unit in group:
        position = getattr(unit, "position", None)
        if position is None:
            continue
        try:
            safe = is_safe(grid=grid, position=position)
        except TypeError:
            safe = is_safe(grid, position)
        if not safe:
            parked.append(unit)
    return parked


def influence_parking_tags(bot: Any) -> frozenset[int]:
    """Persistable tags of ATTACKING units on unsafe ground this frame."""
    return frozenset(
        getattr(unit, "tag", None)
        for unit in units_parked_in_influence(bot)
        if getattr(unit, "tag", None) is not None
    )
