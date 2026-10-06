"""Shared site bookkeeping for Spine/Spore Crawler placement.

`mediator.can_place_structure` only knows about structures that already
exist, so several crawlers planned in the same pulse (or while an earlier
drone is still walking) all resolved to the same or overlapping tiles. The
first drone built; the rest stood on their tiles waiting for a spot that was
no longer free (live: drones idle at their site with a full bank, never
building). Every picker therefore also avoids the sites other crawlers have
already claimed - built, under construction, or just assigned to a drone.
"""

from __future__ import annotations

from math import floor
from typing import TYPE_CHECKING, Iterable

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from ares.consts import ID, TARGET

if TYPE_CHECKING:
    from ares import AresBot
    from ares.managers.manager_mediator import ManagerMediator

CRAWLER_TYPES: frozenset[UnitTypeId] = frozenset(
    {UnitTypeId.SPINECRAWLER, UnitTypeId.SPORECRAWLER}
)

MIN_CENTER_GAP: float = 3.0
"""Spine/Spore are 2x2, so centres 3 apart on at least one axis leaves a
1-tile gap between the two footprints."""


def snap_2x2(x: float, y: float) -> Point2:
    """Nearest valid centre for a 2x2 structure: integer coordinates (the
    footprint's four tiles meet at the centre). The earlier x.5 snap is a
    3x3 centre and was never a legal 2x2 one."""
    return Point2((floor(x + 0.5), floor(y + 0.5)))


def reserved_crawler_sites(ai: "AresBot", mediator: "ManagerMediator") -> list[Point2]:
    """Where crawlers already stand, are building, or have a drone en route."""
    sites = [Point2(s.position) for s in ai.structures(CRAWLER_TYPES)]
    tracker = getattr(mediator, "get_building_tracker_dict", None)
    if tracker:
        for info in tracker.values():
            if info.get(ID) not in CRAWLER_TYPES:
                continue
            target = info.get(TARGET)
            if target is None:
                continue
            sites.append(Point2(getattr(target, "position", target)))
    return sites


def too_close(point: Point2, sites: Iterable[Point2], gap: float = MIN_CENTER_GAP) -> bool:
    """True when `point` would leave less than a 1-tile gap to any of `sites`."""
    return any(
        max(abs(point.x - site.x), abs(point.y - site.y)) < gap for site in sites
    )
