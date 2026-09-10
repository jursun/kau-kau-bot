"""Creep spread: peel one queen off injecting duty to expand creep."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ares.behaviors.combat.individual import QueenSpreadCreep, TumorSpreadCreep
from ares.consts import UnitRole
from sc2.ids.unit_typeid import UnitTypeId

from bot.core.types import CombatRoutine

if TYPE_CHECKING:
    from bot.core.context import BotContext


def spread_creep() -> CombatRoutine:
    """Dedicate one Queen, beyond one per base, to spreading creep with
    ares' own `QueenSpreadCreep`.

    Every Queen is born `UnitRole.QUEEN_INJECT` (see `core/roles.py`), and
    `InjectLarva` (`behaviors/zerg/inject_larva.py`) only ever considers
    queens holding that role — so reassigning one to `UnitRole.QUEEN_CREEP`
    here is what actually makes the assignment stick, instead of that queen
    trading duties with whichever queen is closest to a townhall on a given
    frame. `steps.zerg.train_queens`'s `extra` parameter is what trains the
    queen this promotes in the first place — without it, `len(injectors)`
    would never exceed `base_count` and nothing would ever be spared.

    If the creep queen dies, `get_units_from_role` simply stops returning
    it, so the `if not creep_queens` branch fires again next frame and a
    fresh one is promoted automatically — no separate death-tracking needed.
    """

    def routine(ctx: "BotContext") -> None:
        creep_queens = ctx.mediator.get_units_from_role(
            role=UnitRole.QUEEN_CREEP, unit_type=UnitTypeId.QUEEN
        )
        if not creep_queens:
            injectors = ctx.mediator.get_units_from_role(
                role=UnitRole.QUEEN_INJECT, unit_type=UnitTypeId.QUEEN
            )
            # Only peel one off once there's a queen to spare beyond one per
            # base — otherwise every base's inject falls behind instead.
            if len(injectors) <= ctx.base_count:
                return
            newest = max(injectors, key=lambda q: q.tag)
            ctx.mediator.assign_role(tag=newest.tag, role=UnitRole.QUEEN_CREEP)
            return

        queen = creep_queens[0]
        ctx.bot.register_behavior(QueenSpreadCreep(unit=queen))

    return routine


def spread_tumors() -> CombatRoutine:
    """Have every burrowed Creep Tumor spawn a follow-on tumor, independent
    of whatever the Queen (`spread_creep`, above) is doing.

    This is the passive half of creep spread: `spread_creep` only ever
    drives the one dedicated Queen, so left alone a tumor she plants never
    does anything further once it burrows. Ares' own `TumorSpreadCreep`
    (a `CombatIndividualBehavior`) is what actually fires
    `BUILD_CREEPTUMOR_TUMOR`; both its own ability-cooldown check
    (`AbilityId.BUILD_CREEPTUMOR_TUMOR not in self.unit.abilities`) and
    ares' internal spread-cadence gate (`mediator.should_calculate_tumor_spread`)
    live inside its `execute`, so it's safe to register one for every
    burrowed tumor every frame - it's a no-op for any tumor not actually
    ready to fire.

    Targets the enemy's starting location, same as ares' own docstring
    example for this behavior, so tumors keep walking creep toward the
    opponent rather than spreading outward with no direction.
    """

    def routine(ctx: "BotContext") -> None:
        tumors = ctx.mediator.get_own_structures_dict[UnitTypeId.CREEPTUMORBURROWED]
        if not tumors:
            return

        target = ctx.bot.enemy_start_locations[0]
        for tumor in tumors:
            ctx.bot.register_behavior(TumorSpreadCreep(unit=tumor, target=target))

    return routine
