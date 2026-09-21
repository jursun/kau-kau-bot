"""Temporary thrash detector for live smoke runs.

Tracks per-unit order-target flips; logs when a unit flips too often in a
short window (classic cancel-and-reissue thrash). Safe to leave in — it
only logs, never changes behavior.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import TYPE_CHECKING

from ares.consts import UnitRole
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.consts import ZERGLING_DEFENDER_ROLE, ZERGLING_SCOUT_ROLE
from bot.routines import creep as creep_mod

if TYPE_CHECKING:
    from bot.core.context import BotContext

# Flips inside this many game-seconds → thrash report.
_WINDOW_S: float = 3.0
_FLIP_LIMIT: int = 6

# tag → deque of (game_time, rounded_target_key)
_ORDER_HISTORY: dict[int, deque[tuple[float, object]]] = defaultdict(deque)
_LAST_KEY: dict[int, object] = {}
_REPORTED: set[int] = set()


def _target_key(unit) -> object:
    target = unit.order_target
    if target is None:
        orders = getattr(unit, "orders", None) or ()
        if not orders:
            return ("idle",)
        ability = getattr(getattr(orders[0], "ability", None), "id", None)
        return ("ability", ability)
    if isinstance(target, Point2):
        return ("point", target.rounded)
    tag = getattr(target, "tag", None)
    if tag is not None:
        return ("unit", tag)
    return ("other", type(target).__name__)


def _watch(ctx: "BotContext", unit, label: str) -> None:
    now = float(ctx.bot.time)
    key = _target_key(unit)
    hist = _ORDER_HISTORY[unit.tag]
    prev = _LAST_KEY.get(unit.tag)
    if prev is not None and prev != key:
        hist.append((now, key))
    _LAST_KEY[unit.tag] = key
    while hist and now - hist[0][0] > _WINDOW_S:
        hist.popleft()
    if len(hist) < _FLIP_LIMIT:
        return
    report_key = (unit.tag, label)
    if report_key in _REPORTED:
        return
    _REPORTED.add(report_key)
    sticky = creep_mod._QUEEN_TUMOR_STICKY.get(unit.tag)
    ctx.log(
        f"THRASH {label} tag={unit.tag} type={unit.type_id.name} "
        f"flips={len(hist)}/{_WINDOW_S:.0f}s last={key} sticky={sticky}"
    )


def observe_thrash(ctx: "BotContext") -> None:
    """Sample high-risk roles each frame and log thrash once per unit."""
    mediator = ctx.mediator
    for role, label in (
        (UnitRole.QUEEN_CREEP, "queen_creep"),
        (ZERGLING_SCOUT_ROLE, "ling_scout"),
        (ZERGLING_DEFENDER_ROLE, "ling_defend"),
        (UnitRole.ATTACKING, "attacking"),
        (UnitRole.DEFENDING, "defending"),
    ):
        units = mediator.get_units_from_role(role=role)
        for unit in units:
            # Army thrash: only sample Roaches (known kite/burrow thrash).
            if role in (UnitRole.ATTACKING, UnitRole.DEFENDING):
                if unit.type_id not in (
                    UnitTypeId.ROACH,
                    UnitTypeId.ROACHBURROWED,
                    UnitTypeId.QUEEN,
                ):
                    continue
            _watch(ctx, unit, label)
