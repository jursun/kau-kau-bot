"""Small reusable conditions.

Gates are the composable alternative to overriding `attack_ready()` in a
subclass. Combine them with `all_of` / `any_of` rather than writing new ones.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from bot.core.types import Gate

if TYPE_CHECKING:
    from bot.core.context import BotContext


def always(ctx: "BotContext") -> bool:
    return True


def never(ctx: "BotContext") -> bool:
    return False


def all_of(*gates: Gate) -> Gate:
    def gate(ctx: "BotContext") -> bool:
        return all(g(ctx) for g in gates)

    return gate


def any_of(*gates: Gate) -> Gate:
    def gate(ctx: "BotContext") -> bool:
        return any(g(ctx) for g in gates)

    return gate


def negate(inner: Gate) -> Gate:
    def gate(ctx: "BotContext") -> bool:
        return not inner(ctx)

    return gate


def upgrade_done(upgrade: UpgradeId) -> Gate:
    """Fully researched, not merely started."""

    def gate(ctx: "BotContext") -> bool:
        return upgrade in ctx.bot.state.upgrades

    return gate


def upgrade_started(upgrade: UpgradeId) -> Gate:
    """Researching or already finished."""

    def gate(ctx: "BotContext") -> bool:
        return bool(ctx.bot.pending_or_complete_upgrade(upgrade))

    return gate


def upgrades_within(
    upgrades: tuple[UpgradeId, ...], seconds: float, research_time: float
) -> Gate:
    """True when every listed upgrade has started and the slowest is nearly done.

    `already_pending_upgrade` returns 0.0-1.0 progress, so remaining time is
    estimated from `research_time` rather than read from the game.
    """

    def remaining(ctx: "BotContext", upgrade: UpgradeId) -> float:
        progress = float(ctx.bot.already_pending_upgrade(upgrade))
        if progress <= 0.0:
            return research_time
        if progress >= 1.0:
            return 0.0
        return (1.0 - progress) * research_time

    def gate(ctx: "BotContext") -> bool:
        progresses = [float(ctx.bot.already_pending_upgrade(u)) for u in upgrades]
        if any(p <= 0.0 for p in progresses):
            return False
        return max(remaining(ctx, u) for u in upgrades) <= seconds

    return gate


def vespene_at_least(amount: int) -> Gate:
    """Careful: this un-latches as soon as the gas is spent. Combine it with
    `upgrade_started` via `any_of` when gating on "we have paid for X"."""

    def gate(ctx: "BotContext") -> bool:
        return ctx.bot.vespene >= amount

    return gate


def minerals_at_least(amount: int) -> Gate:
    def gate(ctx: "BotContext") -> bool:
        return ctx.bot.minerals >= amount

    return gate


def after_time(seconds: float) -> Gate:
    def gate(ctx: "BotContext") -> bool:
        return ctx.bot.time >= seconds

    return gate


def after_wave(number: int) -> Gate:
    """Lets wave 1 be gated on tech while later waves leave on size alone."""

    def gate(ctx: "BotContext") -> bool:
        return ctx.state.wave_number >= number

    return gate


def has_structure(structure_id: UnitTypeId, count: int = 1) -> Gate:
    def gate(ctx: "BotContext") -> bool:
        return ctx.bot.structures(structure_id).ready.amount >= count

    return gate


def structure_started(structure_id: UnitTypeId, count: int = 1) -> Gate:
    """Ready *or* under construction, unlike `has_structure` which wants ready.

    Counted the way ares counts it itself (`BuildStructure._enough_existing`):
    ready structures plus `structure_pending`. Note this is deliberately not
    `structures(...).amount + already_pending(...)` - `structures()` already
    includes the ones still building, so that pair double-counts every
    structure in progress (see ARCHITECTURE.md gotcha 8, which cost the
    validator a false FAIL for a whole game).
    """

    def gate(ctx: "BotContext") -> bool:
        ready: int = ctx.bot.structures(structure_id).ready.amount
        return ready + ctx.bot.structure_pending(structure_id) >= count

    return gate


def supply_at_least(supply: int) -> Gate:
    def gate(ctx: "BotContext") -> bool:
        return ctx.bot.supply_used >= supply

    return gate
