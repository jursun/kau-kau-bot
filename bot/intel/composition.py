"""Scout-driven counter composition.

Macro Zerg's base army (`bot.consts` comps) is picked from what tech is up.
This bends those proportions toward what the enemy is actually fielding:
a Protoss army full of Immortals walks through Roaches and Ravagers (armored),
so the mix shifts to Zerglings and Hydralisks, and to Infestors - Neural
Parasite on an Immortal turns their best unit against them.

Each enemy unit type carries a table of multipliers for our units. A type's
influence is scaled by its share of the visible enemy army supply, so a mixed
army blends its counters instead of one unit type deciding everything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId as U

from bot.intel.army import enemy_army

if TYPE_CHECKING:
    from bot.core.context import BotContext

MEMORY_S: float = 120.0
"""How long an enemy type counts after it was last seen - the cached army is
only what we can see right now, and a fight rarely shows the whole army."""

# enemy type -> {our unit type: multiplier at 100% share of the enemy army}.
COUNTER_MULTIPLIERS: dict[U, dict[U, float]] = {
    # Armored + Immortals' bonus damage: Roach / Ravager are the worst answers;
    # cheap Zerglings and Hydralisks are good, and Neural Parasite is best.
    U.IMMORTAL: {
        U.ROACH: 0.3,
        U.RAVAGER: 0.4,
        U.ZERGLING: 2.0,
        U.HYDRALISK: 1.5,
        U.INFESTOR: 3.0,
    },
    U.COLOSSUS: {
        U.ZERGLING: 0.5,
        U.HYDRALISK: 1.2,
        U.CORRUPTOR: 2.5,
        U.INFESTOR: 3.0,
    },
    U.ARCHON: {
        U.ZERGLING: 0.5,
        U.HYDRALISK: 0.7,
        U.ROACH: 1.2,
        U.INFESTOR: 2.0,
    },
    U.HIGHTEMPLAR: {
        U.ZERGLING: 0.6,
        U.HYDRALISK: 0.7,
        U.ROACH: 1.2,
        U.INFESTOR: 1.5,
    },
    U.DISRUPTOR: {
        U.ZERGLING: 0.5,
        U.HYDRALISK: 0.6,
        U.ROACH: 1.2,
        U.INFESTOR: 2.0,
    },
    U.ZEALOT: {
        U.ROACH: 1.2,
        U.RAVAGER: 1.1,
        U.ZERGLING: 0.7,
    },
    U.STALKER: {
        U.ZERGLING: 1.2,
        U.ROACH: 0.9,
    },
    U.SENTRY: {
        U.ROACH: 1.1,
    },
    # Air: Hydralisks and Corruptors; Ravagers/Roaches/lings can't shoot up.
    U.CARRIER: {
        U.HYDRALISK: 1.6,
        U.CORRUPTOR: 2.5,
        U.INFESTOR: 2.5,
        U.ROACH: 0.5,
        U.RAVAGER: 0.3,
        U.ZERGLING: 0.4,
    },
    U.TEMPEST: {
        U.HYDRALISK: 1.4,
        U.CORRUPTOR: 2.0,
        U.INFESTOR: 2.5,
        U.ROACH: 0.6,
        U.RAVAGER: 0.3,
        U.ZERGLING: 0.5,
    },
    U.VOIDRAY: {
        U.HYDRALISK: 1.6,
        U.CORRUPTOR: 1.6,
        U.INFESTOR: 1.5,
        U.ROACH: 0.5,
        U.RAVAGER: 0.3,
        U.ZERGLING: 0.4,
    },
}

DROP_BELOW: float = 0.02
"""Proportions under this after adjusting are dropped (and the rest
renormalised) - a 1% slice of a unit type never gets built anyway."""
ADDED_INFESTOR_BASE: float = 0.06
"""Starting share for an Infestor the base comp lacked (Pit up + Neural
Parasite researched), before its multiplier is applied."""


def enemy_supply_shares(ctx: "BotContext") -> dict[U, float]:
    """Each enemy unit type's share (0..1) of the enemy army supply we know
    of: what is visible now, plus anything seen within `MEMORY_S`."""
    state = ctx.state
    now = float(ctx.bot.time)
    memory = state.enemy_comp_memory

    current: dict[U, float] = {}
    for unit in enemy_army(ctx):
        current[unit.type_id] = current.get(unit.type_id, 0.0) + float(
            ctx.bot.calculate_supply_cost(unit.type_id)
        )
    for type_id, supply in current.items():
        previous, _ = memory.get(type_id, (0.0, now))
        memory[type_id] = (max(previous, supply), now)
    for type_id in [t for t, (_, seen) in memory.items() if now - seen > MEMORY_S]:
        del memory[type_id]

    total = sum(supply for supply, _ in memory.values())
    if total <= 0.0:
        return {}
    return {type_id: supply / total for type_id, (supply, _) in memory.items()}


def counter_comp(
    base: dict[U, dict[str, float | int]],
    shares: dict[U, float],
    *,
    add_infestor: bool = False,
) -> dict[U, dict[str, float | int]]:
    """`base` re-weighted by the counter tables of the enemy types in `shares`.

    Only unit types already in `base` are scaled (their tech is up) - except
    Infestor, which `add_infestor` lets in when the base lacked it. Returns a
    new dict whose proportions sum to 1; `base` is untouched. Priorities are
    kept (new entries get 0).
    """
    comp = {unit: dict(info) for unit, info in base.items()}
    if add_infestor and U.INFESTOR not in comp and shares:
        comp[U.INFESTOR] = {"proportion": ADDED_INFESTOR_BASE, "priority": 0}
    if not shares:
        return _normalized(comp)

    for unit, info in comp.items():
        factor = 1.0
        for enemy_type, share in shares.items():
            multiplier = COUNTER_MULTIPLIERS.get(enemy_type, {}).get(unit)
            if multiplier is not None:
                factor *= 1.0 + share * (multiplier - 1.0)
        info["proportion"] = float(info["proportion"]) * factor
    return _normalized(comp)


def _normalized(comp: dict[U, dict[str, float | int]]) -> dict[U, dict[str, float | int]]:
    total = sum(float(info["proportion"]) for info in comp.values())
    if total <= 0.0:
        return comp
    scaled = {u: float(i["proportion"]) / total for u, i in comp.items()}
    keep = {u: p for u, p in scaled.items() if p >= DROP_BELOW}
    if not keep:
        return comp
    kept_total = sum(keep.values())
    return {
        u: {"proportion": keep[u] / kept_total, "priority": comp[u]["priority"]}
        for u in keep
    }


RAVAGER_CAP: int = 8
"""Ravagers are expensive (a Roach plus 25/75): never more than this many."""


def cap_unit(
    comp: dict[U, dict[str, float | int]], unit: U, current: int, cap: int
) -> dict[U, dict[str, float | int]]:
    """`comp` without `unit` once `current` has reached `cap` (the rest is
    renormalised). `comp` is not modified."""
    if current < cap or unit not in comp:
        return comp
    rest = {u: dict(info) for u, info in comp.items() if u != unit}
    return _normalized(rest) if rest else comp
