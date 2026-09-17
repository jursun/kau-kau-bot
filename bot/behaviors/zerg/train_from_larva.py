"""Train a single Larva-based unit type up to a count, as an ares `MacroBehavior`.

Generalizes `ares.behaviors.macro.BuildWorkers` (worker-only, and pinned to
`ai.supply_workers` specifically) to any Larva-trained Zerg unit - Overlord
and Zergling both need the exact same "keep training until live+pending
hits N" shape for a build with a scripted, supply-gated build order
(`bot.builds.zerg.macro_zerg`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId

from ares.behaviors.macro.macro_behavior import MacroBehavior
from ares.managers.manager_mediator import ManagerMediator

if TYPE_CHECKING:
    from ares import AresBot


def pending_larva_trained(ai: "AresBot", unit_type: UnitTypeId) -> float:
    """`ai.already_pending` counts in-progress Eggs by *order* (one per
    Egg), not by resulting unit - exact for every Larva-trained type here
    except Zergling, which always pairs two off a single Egg. Left
    uncorrected, a `to_count=2` target (this module's own contract: "counts
    individual Zerglings, not morph commands") was satisfied by
    `already_pending` reading 1 - one Egg in progress, correctly one
    *order*, but only half the actual yield - so nothing ever saw the
    target as met until that Egg hatched, and a second Egg queued
    immediately behind it. Confirmed live: a scripted "2 Zergling" step
    alone produced 2 Eggs / 4 Zerglings, and the following "4 Zergling"
    step queued 2 more Eggs on top of that (4 Eggs / 8 Zerglings total,
    matching the exact user report) - both silently doubled, and the extra
    100 minerals burned cascaded into every later opening milestone
    landing past its deadline.
    """
    pending = ai.already_pending(unit_type)
    if unit_type == UnitTypeId.ZERGLING:
        return pending * 2
    return pending


@dataclass
class TrainFromLarva(MacroBehavior):
    """Keep `unit_type` (live + pending) at `to_count`, one Larva per call.

    Attributes:
        unit_type: The Larva-trained unit to produce (Overlord, Zergling, ...).
        to_count: Total (live + pending) target - e.g. for Zergling, which
            always pairs two off one Larva, this counts individual
            Zerglings, not morph commands.
    """

    unit_type: UnitTypeId
    to_count: int

    def execute(self, ai: "AresBot", config: dict, mediator: ManagerMediator) -> bool:
        have = ai.units(self.unit_type).amount + pending_larva_trained(
            ai, self.unit_type
        )
        if have >= self.to_count:
            return False
        if not ai.can_afford(self.unit_type):
            return False
        larvae = ai.units(UnitTypeId.LARVA)
        if not larvae:
            return False
        larvae.first.train(self.unit_type)
        return True
