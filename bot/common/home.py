"""Where "home" is once the original main / natural have fallen.

Everything keyed off `start_location` / the static natural (tech buildings,
upgrades, rally, defense anchors) goes dark when a base trade kills those
bases. These helpers hand back a surviving base instead, so the build keeps
rebuilding tech, bases and army from wherever we still stand.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cython_extensions import cy_distance_to
from sc2.position import Point2

if TYPE_CHECKING:
    from ares import AresBot

HOME_BASE_RADIUS: float = 10.0
"""A townhall this close to a base location counts as that base being alive."""


def _townhalls(ai: "AresBot") -> list:
    try:
        return list(ai.townhalls)
    except TypeError:
        return []


def townhall_near(ai: "AresBot", point: Point2, radius: float = HOME_BASE_RADIUS) -> bool:
    return any(cy_distance_to(th.position, point) <= radius for th in _townhalls(ai))


def safest_surviving_base(ai: "AresBot") -> Point2 | None:
    """The owned townhall farthest from the enemy start, ready ones first.

    Farthest-from-enemy is the cheapest proxy for "least likely to be the
    next one hit" that needs no extra intel - the wave that just killed the
    main came from that side.
    """
    townhalls = _townhalls(ai)
    if not townhalls:
        return None
    ready = [th for th in townhalls if th.is_ready] or townhalls
    enemy_starts = getattr(ai, "enemy_start_locations", None)
    if not enemy_starts:
        return Point2(ready[0].position)
    enemy = enemy_starts[0]
    return Point2(max(ready, key=lambda th: cy_distance_to(th.position, enemy)).position)


def surviving_main(ai: "AresBot", start: Point2) -> Point2:
    """`start` while a townhall still stands there, else the safest
    surviving base, else `start` (nothing left to fall back to)."""
    if townhall_near(ai, start):
        return start
    survivor = safest_surviving_base(ai)
    return survivor if survivor is not None else start


def surviving_natural(
    ai: "AresBot", nat: Point2, main: Point2, established: bool
) -> Point2:
    """`nat` until it has been taken and then lost; after that the
    surviving non-main base closest to it (else `main`).

    `established` is the caller's latch for "a townhall has stood at `nat`
    at some point": before the natural exists the static spot is still the
    right rally / scout anchor and must not be swapped for the main.
    """
    if not established or townhall_near(ai, nat):
        return nat
    others = [
        th
        for th in _townhalls(ai)
        if cy_distance_to(th.position, main) > HOME_BASE_RADIUS
    ]
    if not others:
        return main
    return Point2(min(others, key=lambda th: cy_distance_to(th.position, nat)).position)
