"""Small reusable conditions.

Gates are the composable alternative to overriding `attack_ready()` in a
subclass. Combine them with `all_of` / `any_of` rather than writing new ones.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ares.consts import UnitRole
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from bot.core.types import Gate
from bot.intel.army import (
    enemy_army_type_ids,
    leave_enemy_army_supply,
)

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


def army_supply_at_least(supply: float) -> Gate:
    """True once `supply_army` (workers/overlords excluded) hits `supply`."""

    def gate(ctx: "BotContext") -> bool:
        return float(ctx.bot.supply_army) >= supply

    return gate


# Macro Zerg leave: scale our army-supply floor off scouted enemy combat
# supply (Kuuro `enemy_army_supply`) — leave sooner vs greedy / fog, hold
# longer vs a real army. Clamped so we never leave naked or turtle forever.
LEAVE_ARMY_SUPPLY_MIN: float = 28.0
LEAVE_ARMY_SUPPLY_MAX: float = 64.0
LEAVE_ARMY_SUPPLY_UNSEEN: float = 36.0
"""When no enemy combat is visible, leave a bit sooner than the old fixed 40."""
LEAVE_ARMY_SUPPLY_MARGIN: float = 16.0
"""Beat visible enemy army supply by this much before leaving."""

# Single biggest threat bump (not stacked) when these types are visible.
_LEAVE_THREAT_BUMP: dict[UnitTypeId, float] = {
    UnitTypeId.SIEGETANK: 6.0,
    UnitTypeId.SIEGETANKSIEGED: 6.0,
    UnitTypeId.LIBERATOR: 6.0,
    UnitTypeId.LIBERATORAG: 6.0,
    UnitTypeId.COLOSSUS: 8.0,
    UnitTypeId.DISRUPTOR: 6.0,
    UnitTypeId.HIGHTEMPLAR: 6.0,
    UnitTypeId.ARCHON: 4.0,
    UnitTypeId.BROODLORD: 8.0,
    UnitTypeId.LURKERMP: 6.0,
    UnitTypeId.LURKERMPBURROWED: 6.0,
}


def committed_leave_army_supply(ctx: "BotContext") -> float:
    """Supply the first leave will actually promote — not raw `supply_army`.

    Sums `calculate_supply_cost` over `ctx.units_in_role(DEFENDING)`, which
    is already filtered to `build.army.types`. Queens never appear there;
    home Zerglings on `ZERGLING_DEFENDER_ROLE` stay off DEFENDING, so they
    cannot inflate the leave bar. Live CheatInsane: gate thought us=8-12
    ready vs enemy 20-26 while Wave 1 only promoted 6-8 DEFENDING Roaches.
    """
    defenders = ctx.units_in_role(UnitRole.DEFENDING)
    if not defenders:
        return 0.0
    return float(
        sum(ctx.bot.calculate_supply_cost(unit.type_id) for unit in defenders)
    )


def leave_army_supply_needed(ctx: "BotContext") -> float:
    """Committed-army supply we want before the first Macro Zerg leave.

    Scales off Kuuro's peak `leave_enemy_army_supply` (+ a small bump for
    visible high-impact tech). Empty/unseen enemy -> `LEAVE_ARMY_SUPPLY_UNSEEN`.
    Compared against `committed_leave_army_supply`, never raw `supply_army`.
    """
    enemy = float(leave_enemy_army_supply(ctx))
    bump = 0.0
    for type_id in enemy_army_type_ids(ctx):
        bump = max(bump, _LEAVE_THREAT_BUMP.get(type_id, 0.0))
    if enemy <= 0.0:
        need = LEAVE_ARMY_SUPPLY_UNSEEN + bump
    else:
        need = enemy + LEAVE_ARMY_SUPPLY_MARGIN + bump
    return max(LEAVE_ARMY_SUPPLY_MIN, min(LEAVE_ARMY_SUPPLY_MAX, need))


def intel_scaled_army_leave() -> Gate:
    """True once committed DEFENDING supply meets `leave_army_supply_needed`."""

    def gate(ctx: "BotContext") -> bool:
        return committed_leave_army_supply(ctx) >= leave_army_supply_needed(ctx)

    return gate



def training_started(unit_type: UnitTypeId) -> Gate:
    """True once at least one is queued or in production - not merely
    unlocked. `already_pending` counts anything in a production queue, so
    this reads "has begun training", e.g. gating a build's last Barracks on
    its first Marine actually having started, not just on tech existing."""

    def gate(ctx: "BotContext") -> bool:
        return ctx.bot.already_pending(unit_type) > 0

    return gate
