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
        return self.bot.start_location

    @property
    def ready_townhalls(self) -> Units:
        return self.bot.townhalls.ready

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
