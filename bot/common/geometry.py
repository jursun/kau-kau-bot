"""Shared geometry helpers usable from both `BotContext` and the `ai`-only
ares `MacroBehavior`/`CombatBehavior` classes, which only ever receive
`ai` directly, never `ctx`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sc2.position import Point2

if TYPE_CHECKING:
    from ares import AresBot


def safe_start_location(ai: "AresBot") -> Point2:
    """`ai.start_location` itself, or a fallback that's always non-None.

    python-sc2's own `start_location` is `None` whenever `ai.townhalls`
    was empty at the very first `on_step` observation (`BotAIInternal.
    _prepare_first_step`'s own `if self.townhalls: self.game_info.
    player_start_location = ...` guard, never recomputed afterward once
    missed - documented only in that property's own docstring: "None if
    ... a custom map that does not feature townhalls at game start").
    Confirmed live: `rally_point` crashed with `AttributeError:
    'NoneType' object has no attribute 'towards'` from code that assumed
    `start_location` was always safe.

    Explicit `is not None`, not `or` - `Point2.__bool__` is falsy for the
    origin `(0, 0)` (a numpy-backed all-zero check), which `or` would
    otherwise mistake for "missing" on a map whose start happens to sit
    there. A live ready townhall's own position is checked next (doesn't
    depend on that frozen first-frame snapshot at all), then `map_center`
    - the one position that's structurally always non-None (`GameInfo.
    __init__` sets it directly from the map's playable area) - for the,
    should be impossible mid-game, case where there's no base at all.
    """
    start = ai.start_location
    if start is not None:
        return start
    if ready := ai.townhalls.ready:
        return ready.first.position
    return ai.game_info.map_center
