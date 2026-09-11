"""Synchronous "find a legal spot near an arbitrary point" placement.

`ares.managers.placement_manager.request_building_placement` snaps its
`base_location` to the nearest key in ares' own precomputed per-expansion
formation (`PlacementManager.placements_dict`) and can only reorder
candidates *within* that formation via `closest_to` - it cannot escape a bad
formation or offer a spot outside it. That's fine for placing at a base
(`steps.terran.proxy_barracks` leans on exactly that), but wrong for "next to
whatever is already standing here": the formation solved for an expansion has
no idea a cluster of Barracks got built somewhere inside its bounds, and its
own Depot slot can land well outside that cluster - behind the mineral line,
for a proxy at the enemy's fourth (see `builds.terran.four_rax_proxy.
proxy_barracks_position`, the caller this was written for).

`near_point` sidesteps the formation entirely: ring-sampled candidates around
an arbitrary reference point, filtered for standable ground and clearance
from resources, validated one at a time with the synchronous `mediator.
can_place_structure`. Same technique `behaviors/zerg/build_macro_hatch.py`'s
`BuildMacroHatch` already uses for an in-main hatch, generalized: any
structure type, any reference point - not tied to a `MacroBehavior`, and not
assuming a "home plateau" to match terrain height against, since a proxy site
is enemy ground with no such thing.
"""

from __future__ import annotations

from math import cos, floor, pi, sin
from typing import TYPE_CHECKING

from cython_extensions import cy_distance_to_squared
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

if TYPE_CHECKING:
    from bot.core.context import BotContext


def _snap_to_building_centre(x: float, y: float) -> Point2:
    """A structure's footprint centres on a `.5` coordinate; an integer
    centre reads as legal to `can_place_structure` but is not a valid
    placement - see `build_macro_hatch`'s own note on this same gotcha."""
    return Point2((floor(x) + 0.5, floor(y) + 0.5))


def _ring_samples(
    reference: Point2,
    min_radius: float,
    max_radius: float,
    ring_step: float,
    rays: int,
) -> set[Point2]:
    seen: set[Point2] = set()
    radius = min_radius
    while radius <= max_radius:
        for index in range(rays):
            angle = 2.0 * pi * index / rays
            seen.add(
                _snap_to_building_centre(
                    reference.x + radius * cos(angle),
                    reference.y + radius * sin(angle),
                )
            )
        radius += ring_step
    return seen


def near_point(
    ctx: "BotContext",
    reference: Point2,
    structure_type: UnitTypeId,
    min_radius: float = 2.0,
    max_radius: float = 14.0,
    ring_step: float = 1.0,
    rays: int = 16,
    resource_clearance: float = 2.5,
) -> Point2 | None:
    """The legal `structure_type` placement closest to `reference`, or `None`
    if nothing in range checks out.

    The snapped `reference` itself is tried first - so a caller that wants
    the expansion's townhall tile (Barracks C on the enemy fourth) gets that
    exact centre when it is legal, rather than being pushed onto a ring
    sample two tiles out. Ring candidates are still sorted by distance and
    used when the reference is blocked or otherwise illegal.

    `resource_clearance` exists for the reason `build_macro_hatch` keeps the
    same kind of check: a spot can satisfy `can_place_structure` and still
    sit right against a mineral patch, which is exactly the kind of place
    this function is trying to stay away from in the first place.
    """
    ai = ctx.bot
    resources = [*ai.mineral_field, *ai.vespene_geyser]
    resource_sq = resource_clearance**2

    # Prefer the reference itself when it is a legal centre (e.g. Barracks C
    # on the enemy fourth's townhall tile). Ring samples still cover "near
    # but not on" callers whose reference is already occupied.
    at_reference = _snap_to_building_centre(reference.x, reference.y)
    ring = _ring_samples(reference, min_radius, max_radius, ring_step, rays)
    ring.discard(at_reference)
    candidates = [at_reference, *sorted(ring, key=lambda p: cy_distance_to_squared(p, reference))]

    for point in candidates:
        if not ai.in_pathing_grid(point):
            continue
        if any(
            cy_distance_to_squared(point, r.position) < resource_sq for r in resources
        ):
            continue
        if ctx.mediator.can_place_structure(
            position=point, structure_type=structure_type
        ):
            return point
    return None
