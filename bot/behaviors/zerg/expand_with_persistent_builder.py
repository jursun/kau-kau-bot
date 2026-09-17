"""Expand, preferring a pre-positioned Drone over grabbing a fresh one."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ares.behaviors.macro.expansion_controller import ExpansionController
from ares.behaviors.macro.macro_behavior import MacroBehavior
from ares.managers.manager_mediator import ManagerMediator

if TYPE_CHECKING:
    from ares import AresBot


@dataclass
class ExpandWithPersistentBuilder(MacroBehavior):
    """Same eligibility/location logic as `ares.behaviors.macro.
    ExpansionController`, but tries a `UnitRole.PERSISTENT_BUILDER` worker
    first via `select_worker(..., select_persistent_builder=True)` before
    falling back to `ExpansionController`'s own normal GATHERING-pool pick.

    `ExpansionController.execute()` calls `mediator.select_worker(target_
    position=location)` with no `select_persistent_builder` flag - a Drone
    pre-walked to the expansion site and parked on `UnitRole.
    PERSISTENT_BUILDER` (see `builds.zerg.macro_zerg._claim_natural_scout`/
    `_claim_third_base_scout`) is therefore invisible to it: it always
    grabs a fresh Drone off the mineral line instead, sending *two* Drones
    to the same spot - confirmed live, the pre-walked one just stood there
    while a second one got the actual build order, the two visibly
    blocking each other's pathing at the site.

    Delegates location-finding to a plain `ExpansionController` instance
    (composition, not inheritance - `execute()` can't be reused as-is
    since the whole point is calling `select_worker` differently) rather
    than duplicating `_get_next_expansion_location`'s own safety/placement
    checks a second time.
    """

    to_count: int
    can_afford_check: bool = True
    check_location_is_safe: bool = True
    max_pending: int = 1

    def execute(self, ai: "AresBot", config: dict, mediator: ManagerMediator) -> bool:
        controller = ExpansionController(
            to_count=self.to_count,
            can_afford_check=self.can_afford_check,
            check_location_is_safe=self.check_location_is_safe,
            max_pending=self.max_pending,
        )
        if (
            len([th for th in ai.townhalls if th.is_ready])
            + ai.structure_pending(ai.base_townhall_type)
            >= self.to_count
            or ai.structure_pending(ai.base_townhall_type) >= self.max_pending
            or (self.can_afford_check and not ai.can_afford(ai.base_townhall_type))
        ):
            return False

        location = controller._get_next_expansion_location(ai, mediator)
        if location is None:
            return False

        worker = mediator.select_worker(
            target_position=location, select_persistent_builder=True
        )
        if worker is None:
            return False

        mediator.build_with_specific_worker(
            worker=worker, structure_type=ai.base_townhall_type, pos=location
        )
        return True
