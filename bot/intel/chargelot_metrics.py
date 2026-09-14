"""Per-game Chargelot regression metrics (scout/adept/tech/warps/float)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cython_extensions import cy_distance_to
from sc2.ids.unit_typeid import UnitTypeId

from bot.routines.protoss_support import PRISM_WARP_FIELD_RADIUS

if TYPE_CHECKING:
    from bot.core.context import BotContext
    from sc2.unit import Unit

_WARP_UNIT_TYPES: frozenset[UnitTypeId] = frozenset(
    {UnitTypeId.ZEALOT, UnitTypeId.STALKER, UnitTypeId.SENTRY, UnitTypeId.ADEPT}
)


def update_chargelot_metrics(ctx: "BotContext") -> None:
    """Frame tick: float caps, tech latches, warpgate peak."""
    m = ctx.state.chargelot_metrics
    bot = ctx.bot

    nexi = bot.townhalls.ready.amount
    if nexi >= 2:
        m.nat_nexus_ready = True
    if m.nat_nexus_ready:
        m.max_minerals_after_nat = max(m.max_minerals_after_nat, bot.minerals)
        m.max_gas_after_nat = max(m.max_gas_after_nat, bot.vespene)

    gates = (
        bot.structures(UnitTypeId.GATEWAY).amount
        + bot.structures(UnitTypeId.WARPGATE).amount
    )
    m.warpgate_peak = max(m.warpgate_peak, gates)

    stalkers = (
        bot.units(UnitTypeId.STALKER).amount
        + bot.already_pending(UnitTypeId.STALKER)
    )
    robo_up = (
        bot.structures(UnitTypeId.ROBOTICSFACILITY).amount
        + bot.structure_pending(UnitTypeId.ROBOTICSFACILITY)
    ) > 0
    if robo_up and m.time_robo_started is None:
        m.time_robo_started = bot.time
    if stalkers >= 2 and m.stalkers_before_robo is None:
        m.stalkers_before_robo = not robo_up
        m.time_second_stalker = bot.time

    if (
        bot.units(UnitTypeId.WARPPRISM)
        or bot.units(UnitTypeId.WARPPRISMPHASING)
        or bot.already_pending(UnitTypeId.WARPPRISM) > 0
    ):
        m.prism_produced = True
        if m.time_prism is None:
            m.time_prism = bot.time

    if bot.units(UnitTypeId.WARPPRISMPHASING) and m.time_prism_phased is None:
        m.time_prism_phased = bot.time

    _update_adept_combat(ctx)


def note_unit_created(ctx: "BotContext", unit: "Unit") -> None:
    """Count Adept / warp-ins (Prism field vs home Pylon)."""
    m = ctx.state.chargelot_metrics
    if unit.type_id == UnitTypeId.ADEPT:
        m.adept_produced += 1

    if unit.type_id not in _WARP_UNIT_TYPES:
        return
    prisms = [
        u
        for u in ctx.bot.units
        if u.type_id == UnitTypeId.WARPPRISMPHASING
    ]
    for prism in prisms:
        if cy_distance_to(unit.position, prism.position) <= PRISM_WARP_FIELD_RADIUS:
            m.prism_warps += 1
            return
    m.pylon_warps += 1


def note_unit_destroyed(ctx: "BotContext", unit_tag: int) -> None:
    """Credit Adept death-frame damage and death count."""
    m = ctx.state.chargelot_metrics
    if m.adept_tag is not None and unit_tag == m.adept_tag:
        if m.adept_last_hp is not None:
            m.adept_damage_taken += m.adept_last_hp
        m.adept_last_hp = None
        m.adept_tag = None
        m.adept_prey_hp.clear()
        m.adept_died += 1


def note_adept_shade_abort(ctx: "BotContext") -> None:
    ctx.state.chargelot_metrics.adept_shade_aborts += 1


def note_muster_commit(ctx: "BotContext") -> None:
    m = ctx.state.chargelot_metrics
    if m.muster_commit_time is None:
        m.muster_commit_time = ctx.bot.time
    m.muster_form_ready_since = None


def note_muster_waiting_prism(ctx: "BotContext") -> None:
    """Start the Prism-wait clock once form-up is ready."""
    m = ctx.state.chargelot_metrics
    if m.muster_form_ready_since is None:
        m.muster_form_ready_since = ctx.bot.time


def _vital(u) -> float:
    return float(u.health) + float(getattr(u, "shield", 0) or 0)


def _update_adept_combat(ctx: "BotContext") -> None:
    """Mirror scout harass damage tracking for the harassing Adept."""
    from ares.consts import UnitRole

    m = ctx.state.chargelot_metrics
    adepts = list(
        ctx.mediator.get_units_from_role(
            role=UnitRole.HARASSING_ADEPT, unit_type=UnitTypeId.ADEPT
        )
    )
    if not adepts:
        # Keep last_hp/tag for `note_unit_destroyed` death-frame credit.
        if m.adept_tag is None:
            m.adept_prey_hp.clear()
        return

    adept = adepts[0]
    m.adept_tag = adept.tag
    hp = _vital(adept)
    prev = m.adept_last_hp
    if prev is not None and hp < prev:
        m.adept_damage_taken += prev - hp
    m.adept_last_hp = hp

    near = [
        u
        for u in ctx.bot.enemy_units
        if not u.is_structure
        and cy_distance_to(adept.position, u.position) <= 7.0
    ]
    seen: set[int] = set()
    for u in near:
        seen.add(u.tag)
        cur = _vital(u)
        old = m.adept_prey_hp.get(u.tag)
        if old is not None and cur < old:
            m.adept_damage_dealt += old - cur
            if cur <= 0:
                m.adept_kills += 1
                continue
        m.adept_prey_hp[u.tag] = cur
    for tag in list(m.adept_prey_hp):
        if tag not in seen:
            last = m.adept_prey_hp.pop(tag)
            if last <= 5.0 and m.adept_damage_dealt > 0:
                m.adept_kills += 1


def snapshot(ctx: "BotContext") -> dict[str, Any]:
    """JSON-serializable end-of-game metrics row."""
    m = ctx.state.chargelot_metrics
    state = ctx.state
    return {
        "scout_damage_dealt": round(state.worker_harass_damage_dealt, 1),
        "scout_damage_taken": round(state.worker_harass_damage_taken, 1),
        "scout_kills": state.worker_harass_kills,
        "adept_damage_dealt": round(m.adept_damage_dealt, 1),
        "adept_damage_taken": round(m.adept_damage_taken, 1),
        "adept_kills": m.adept_kills,
        "adept_produced": m.adept_produced,
        "adept_died": m.adept_died,
        "adept_shade_aborts": m.adept_shade_aborts,
        "stalkers_before_robo": m.stalkers_before_robo,
        "time_second_stalker": (
            None if m.time_second_stalker is None else round(m.time_second_stalker, 1)
        ),
        "time_robo_started": (
            None if m.time_robo_started is None else round(m.time_robo_started, 1)
        ),
        "prism_produced": m.prism_produced,
        "time_prism": None if m.time_prism is None else round(m.time_prism, 1),
        "time_prism_phased": (
            None if m.time_prism_phased is None else round(m.time_prism_phased, 1)
        ),
        "muster_commit_time": (
            None if m.muster_commit_time is None else round(m.muster_commit_time, 1)
        ),
        "warpgate_peak": m.warpgate_peak,
        "prism_warps": m.prism_warps,
        "pylon_warps": m.pylon_warps,
        "auto_supply_block_frames": m.auto_supply_block_frames,
        "max_minerals_after_nat": m.max_minerals_after_nat,
        "max_gas_after_nat": m.max_gas_after_nat,
        "nat_nexus_ready": m.nat_nexus_ready,
    }
