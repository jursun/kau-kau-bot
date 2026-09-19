"""WORKER-filtered enemy army feed for combat / QA.

`mediator.get_cached_enemy_army` includes workers. Callers that need the
fighting force must strip them. Prefer tags (`enemy_army_tags`) for anything
that survives a frame — never stash Unit objects.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from ares.consts import UnitRole

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


def enemy_has_air_units(ctx: BotContext) -> bool:
    """True if any currently-tracked enemy combat unit is flying - gates
    Macro Zerg's reactive Spire/Corruptor branch (see `steps.zerg.tech_up`/
    `spawn_macro_army`)."""
    return any(unit.is_flying for unit in enemy_army(ctx))


def enemy_army_supply(ctx: BotContext) -> float:
    """Scouted enemy combat supply this frame (workers already stripped).

    Sums `calculate_supply_cost` over `enemy_army`. Returns 0 when nothing
    is visible — callers that decide army-vs-tech posture should treat that
    as "not behind" so fog does not trigger army panic.
    """
    army = enemy_army(ctx)
    if not army:
        return 0.0
    return float(
        sum(ctx.bot.calculate_supply_cost(unit.type_id) for unit in army)
    )


def army_behind_on_supply(ctx: BotContext) -> bool:
    """True when visible enemy army supply exceeds ours.

    Empty/unseen enemy army → False (tech focus). Used by Macro Zerg's
    post-5:00 army-vs-tech posture.
    """
    enemy = enemy_army_supply(ctx)
    if enemy <= 0:
        return False
    return enemy > float(ctx.bot.supply_army)


def leave_enemy_army_supply(ctx: BotContext) -> float:
    """Best-known enemy combat supply for leave decisions.

    Returns ``max(peak_enemy_army_supply, enemy_army_supply(ctx))``. Peak only
    rises via ``observe_leave_intel`` — fog alone never invents supply.
    """
    current = enemy_army_supply(ctx)
    peak = float(getattr(ctx.state, "peak_enemy_army_supply", 0.0) or 0.0)
    return max(peak, current)


def observe_leave_intel(ctx: BotContext) -> float:
    """Latch peak from this frame's scouted combat supply; return leave supply.

    Call once per frame from ``main.on_step`` so leave gates keep a
    fog-stable floor. Does not invent units — empty vision leaves peak as-is.
    """
    current = enemy_army_supply(ctx)
    peak = float(getattr(ctx.state, "peak_enemy_army_supply", 0.0) or 0.0)
    if current > peak:
        ctx.state.peak_enemy_army_supply = current
        peak = current
    return max(peak, current)


def leave_army_supply(ctx: BotContext) -> float:
    """Commit-able our combat supply for leave gates — not raw `supply_army`.

    Sums `calculate_supply_cost` over `ctx.units_in_role(DEFENDING)`, which
    is already filtered to `build.army.types`. Queens never appear there;
    home Zerglings on `ZERGLING_DEFENDER_ROLE` stay off DEFENDING, so they
    cannot inflate the leave bar.

    Combat / gates should compare this to `leave_enemy_army_supply` (peak
    floor), never `ctx.bot.supply_army`. Same metric Soujirou shipped as
    `committed_leave_army_supply` — this is the intel home for it.
    """
    defenders = ctx.units_in_role(UnitRole.DEFENDING)
    if not defenders:
        return 0.0
    return float(
        sum(ctx.bot.calculate_supply_cost(unit.type_id) for unit in defenders)
    )

