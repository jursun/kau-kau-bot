"""Spore Crawler placement as an ares `MacroBehavior`, done directly rather
than through `ai.request_zerg_placement`.

`BuildStructure` cannot be used for this on Zerg, and not only for the
`to_count_per_base` reason documented in `steps/zerg.py`. For `Race.Zerg` it
always calls `ai.request_zerg_placement(base_location, structure_id)`, which
appends to `ai._requested_zerg_placements` — and `_after_step` replays that
ENTIRE list, unconditionally, every single frame for the rest of the game.
It is never cleared after processing (only reset once, at game start). So a
single request there doesn't fire once: `find_placement` + `select_worker`
succeed again on the next frame, and the one after that, and so on for as
long as the game runs, each success producing one more crawler. The base
that gets hit hardest is whichever one was requested earliest — the main,
in this build, since it's always the first entry the `spore_crawlers` step
walks — which is exactly the "multiple spore crawlers piling up in the
main, forever" symptom this fixes. (See `ares/main.py`: `request_zerg_placement`
appends at one line, `_after_step` loops the same list every frame, and no
line in between ever calls `.clear()` on it.)

Doing the find-placement / select-worker / build-with-specific-worker
sequence ourselves, synchronously, in this behavior's own `execute()`
instead of going through that queue means one call really is one build —
the same reasoning `bot/behaviors/zerg/build_macro_hatch.py` already applies
to hatcheries, for the same underlying reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.unit import Unit

from ares.behaviors.macro.macro_behavior import MacroBehavior
from ares.managers.manager_mediator import ManagerMediator

if TYPE_CHECKING:
    from ares import AresBot


@dataclass
class BuildSporeCrawler(MacroBehavior):
    """Place and dispatch a worker for one Spore Crawler near `base_location`.

    Tries each of `get_behind_mineral_positions`' three candidates for
    `base_location` in turn — ares' own "out of typical cannon range" spots,
    which conveniently doubles as the mineral-line placement this build
    wants a Spore Crawler for — and takes the first one that is actually
    buildable.

    Attributes:
        base_location: townhall position to place the crawler near.
    """

    base_location: Point2

    def execute(self, ai: "AresBot", config: dict, mediator: ManagerMediator) -> bool:
        if not ai.can_afford(UnitTypeId.SPORECRAWLER):
            return False

        candidates = mediator.get_behind_mineral_positions(th_pos=self.base_location)
        for candidate in candidates:
            if not mediator.can_place_structure(
                position=candidate, structure_type=UnitTypeId.SPORECRAWLER
            ):
                continue

            worker: Unit | None = mediator.select_worker(
                target_position=candidate, force_close=True
            )
            if worker is None:
                return False

            mediator.build_with_specific_worker(
                worker=worker, structure_type=UnitTypeId.SPORECRAWLER, pos=candidate
            )
            return True

        return False
