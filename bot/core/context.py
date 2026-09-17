"""The single object handed to every step, routine and gate.

`BotContext` owns the derived numbers (worker target, gas target, army units)
so the steps themselves stay two or three lines long.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sc2.position import Point2
from sc2.units import Units

from ares.consts import UnitRole
from ares.managers.manager_mediator import ManagerMediator

from bot.common.geometry import safe_start_location
from bot.core.state import RunState

if TYPE_CHECKING:
    from ares import AresBot

    from bot.builds.definition import BuildDefinition


@dataclass
class BotContext:
    bot: "AresBot"
    build: "BuildDefinition"
    state: RunState

    # --- passthrough -----------------------------------------------------

    @property
    def mediator(self) -> ManagerMediator:
        return self.bot.mediator

    @property
    def build_completed(self) -> bool:
        return self.bot.build_order_runner.build_completed

    @property
    def production_location(self) -> Point2:
        """Our main base location - see `safe_start_location`'s own
        comment for why this doesn't just return `self.bot.start_location`
        directly."""
        return safe_start_location(self.bot)

    @property
    def ready_townhalls(self) -> Units:
        return self.bot.townhalls.ready

    @property
    def own_nat(self) -> Point2:
        """Our natural base location - falls back through progressively
        safer substitutes instead of ever crashing.

        `ManagerMediator.get_own_nat` indexes `[0]` into an internal list
        that's built once at game start and left empty whenever `AresBot.
        arcade_mode` is True (`enemy_start_locations`/`townhalls` missing
        at that point) or a map's own expansion data fails to resolve any
        reachable expansion at all (see ares' own `_fix_broken_exp_
        locations`, a similar known-bad-map-data workaround already
        shipped for Ley Lines) - a real, ares-level edge case unrelated to
        any one build here, confirmed live: `IndexError: list index out
        of range` from `TerrainManager.own_nat` mid-game. `get_own_
        expansions` returns the same underlying list directly (safe to
        check for emptiness), so this checks that first rather than
        catching the exception after the fact.

        A second, deeper edge case: python-sc2's own `start_location` is
        *itself* sometimes `None` - confirmed live: `rally_point` crashed
        with `AttributeError: 'NoneType' object has no attribute
        'towards'` from a fallback (this property, before this fix) that
        assumed `start_location` was always safe. `safe_start_location`
        (see its own comment) covers that gap."""
        if self.mediator.get_own_expansions:
            return self.mediator.get_own_nat
        return safe_start_location(self.bot)

    @property
    def base_count(self) -> int:
        """At least 1, so per-base targets never collapse to zero."""
        return max(1, len(self.ready_townhalls))

    # --- derived targets -------------------------------------------------

    @property
    def worker_target(self) -> int:
        economy = self.build.economy
        return min(economy.worker_target, economy.workers_per_base * self.base_count)

    @property
    def gas_target(self) -> int:
        economy = self.build.economy
        return min(economy.max_gas, economy.gas_per_base * self.base_count)

    # --- army ------------------------------------------------------------

    def units_in_role(self, role: UnitRole) -> Units:
        """Army units of this build's own types holding `role`.

        Filtering by `build.army.types` is what keeps the engine race-neutral;
        nothing here knows what a zergling is.
        """
        return self.mediator.get_units_from_role(
            role=role, unit_type=self.build.army.types
        )

    def log(self, message: str) -> None:
        from bot.common.log import log_event

        log_event(self.bot, message)

    def log_once(self, key: str, message: str) -> bool:
        return self.state.log_once(self.bot, key, message)
