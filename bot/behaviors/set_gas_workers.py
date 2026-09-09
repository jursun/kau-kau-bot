"""Control how many workers sit on each geyser.

`Mining(workers_per_gas=...)` looks like the lever for this but is not: ares
only consults that value when deciding whether to vespene-boost. Assignment
is owned by the `ResourceManager`, and `mediator.set_workers_per_gas` is what
actually moves drones — when the count on a geyser exceeds the target the
manager pulls one off per frame until it matches.

Race-neutral, so it lives outside the per-race behavior packages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ares.behaviors.macro.macro_behavior import MacroBehavior
from ares.managers.manager_mediator import ManagerMediator

if TYPE_CHECKING:
    from ares import AresBot


@dataclass
class SetGasWorkers(MacroBehavior):
    """Set the per-geyser worker target.

    Attributes:
        amount: Workers to keep on each geyser. 0 pulls everyone to minerals.
    """

    amount: int

    def execute(self, ai: "AresBot", config: dict, mediator: ManagerMediator) -> bool:
        mediator.set_workers_per_gas(amount=self.amount)
        # A setting, not an action: returning True here would short-circuit
        # the rest of a MacroPlan every single frame.
        return False
