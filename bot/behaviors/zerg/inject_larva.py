"""Queen larva injects as an ares `MacroBehavior`.

Ares ships no inject behavior of its own, so this is the one piece of the old
`EconomyManager` that survives as bot-owned logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from cython_extensions import cy_distance_to_squared
from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.unit import Unit

from ares.behaviors.macro.macro_behavior import MacroBehavior
from ares.consts import UnitRole
from ares.managers.manager_mediator import ManagerMediator

if TYPE_CHECKING:
    from ares import AresBot

# SC2 rejects EFFECT_INJECTLARVA beyond ~3 center tiles even though
# game-data cast_range (0.1) + radii implies ~3.7 via in_ability_cast_range
# (confirmed live: inject_cast at 3.27–3.48 never spent energy / OOR spam).
_INJECT_RANGE_SQ: float = 9.0  # center dist <= 3
# Stand beside the hatch, never on the blocked center tile.
_INJECT_STAND_RADIUS: float = 2.5
# After issuing inject, do not re-issue (cancels the cast).
_INJECT_PENDING_UNTIL: dict[int, float] = {}
_INJECT_PENDING_TIMEOUT: float = 12.0


def _already_injecting(queen: Unit, th: Unit) -> bool:
    """True if the Queen already has an inject (or inject-walk) on `th`."""
    orders = queen.orders
    if not orders:
        return False
    order = orders[0]
    target = order.target
    th_match = target == th.tag or getattr(target, "tag", None) == th.tag
    if not th_match:
        return False
    ability = getattr(order, "ability", None)
    ability_id = getattr(ability, "id", None)
    # During the engine walk, ability id may be missing — still treat as
    # in-progress inject if the order targets this hatch.
    if ability_id is None or ability_id == AbilityId.EFFECT_INJECTLARVA:
        return True
    return False


def _stand_beside(th: Unit, queen: Unit) -> Point2:
    """Pathable rim facing the queen — not the hatch center."""
    return Point2(th.position).towards(queen.position, _INJECT_STAND_RADIUS)


@dataclass
class InjectLarva(MacroBehavior):
    """Each inject queen only injects her own home townhall.

    Register this directly (not inside a `MacroPlan`) so it runs every step —
    a `MacroPlan` short-circuits after the first behavior that acts.

    Only considers queens holding `UnitRole.QUEEN_INJECT` (every queen is
    born with that role — see `core/roles.py`) rather than every queen that
    exists: a queen reassigned to `UnitRole.QUEEN_CREEP`
    (`routines.creep.spread_creep`) would otherwise still get swept into
    injecting whenever it's the closest one to a townhall, trading duties
    with the actual creep queen instead of staying dedicated to either job.

    When `home_townhall` is set (Macro Zerg), a queen never walks to another
    base to inject — she only targets the hatch that trained her. Without
    the map (legacy callers), falls back to closest-available matching.

    Issue `EFFECT_INJECTLARVA` only when within cast range (3). When farther,
    walk to a stand point *beside* the hatch (not into the center square),
    then inject once and leave the order alone until it finishes.

    Attributes:
        min_energy: Energy a queen needs before it will be used for an inject.
        home_townhall: Queen tag -> townhall tag recorded at creation.
    """

    min_energy: int = 25
    home_townhall: dict[int, int] | None = None

    def execute(self, ai: "AresBot", config: dict, mediator: ManagerMediator) -> bool:
        import time

        now = time.time()
        townhalls = [th for th in ai.townhalls.ready]
        if not townhalls:
            return False

        available: list[Unit] = [
            q
            for q in mediator.get_units_from_role(
                role=UnitRole.QUEEN_INJECT, unit_type=UnitTypeId.QUEEN
            )
            if q.is_ready
            and q.energy >= self.min_energy
            and q.tag not in ai.unit_tags_received_action
        ]
        if not available:
            return False

        homes = self.home_townhall
        did_action: bool = False
        for th in townhalls:
            if not available:
                break
            if th.has_buff(BuffId.QUEENSPAWNLARVATIMER):
                # Inject landed — clear pending for anyone targeting this hatch.
                for tag, until in list(_INJECT_PENDING_UNTIL.items()):
                    if until <= now:
                        _INJECT_PENDING_UNTIL.pop(tag, None)
                continue
            if homes is not None:
                candidates = [q for q in available if homes.get(q.tag) == th.tag]
            else:
                candidates = list(available)
            if not candidates:
                continue
            queen: Unit = min(
                candidates,
                key=lambda q: cy_distance_to_squared(q.position, th.position),
            )
            if _already_injecting(queen, th):
                available.remove(queen)
                continue
            pending_until = _INJECT_PENDING_UNTIL.get(queen.tag, 0.0)
            if pending_until > now:
                available.remove(queen)
                continue
            _INJECT_PENDING_UNTIL.pop(queen.tag, None)

            dist_sq = cy_distance_to_squared(queen.position, th.position)
            if dist_sq > _INJECT_RANGE_SQ:
                stand = _stand_beside(th, queen)
                order_target = queen.order_target
                already = (
                    isinstance(order_target, Point2)
                    and cy_distance_to_squared(order_target, stand) < 4.0
                )
                if not already:
                    queen.move(stand)
                available.remove(queen)
                did_action = True
                continue

            _INJECT_PENDING_UNTIL[queen.tag] = now + _INJECT_PENDING_TIMEOUT
            queen(AbilityId.EFFECT_INJECTLARVA, th)
            available.remove(queen)
            did_action = True

        return did_action
