"""WORKER-filtered enemy army feed for combat / QA.

`mediator.get_cached_enemy_army` includes workers. Callers that need the
fighting force must strip them. Prefer tags (`enemy_army_tags`) for anything
that survives a frame — never stash Unit objects.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from ares.consts import UnitRole
from sc2.ids.unit_typeid import UnitTypeId

from bot.consts import WORKER_TYPES
from bot.core.context import BotContext


def _cached_army(ctx: BotContext) -> Sequence[Any]:
    army = ctx.mediator.get_cached_enemy_army
    if army is None:
        return ()
    return army


def enemy_army(ctx: BotContext) -> list[Any]:
    """This-frame enemy combat units (workers stripped)."""
    return [unit for unit in _cached_army(ctx) if unit.type_id not in WORKER_TYPES]


def enemy_army_tags(ctx: BotContext) -> frozenset[int]:
    """Persistable tags for the filtered army — never Unit refs."""
    return frozenset(unit.tag for unit in enemy_army(ctx))


def enemy_army_type_ids(ctx: BotContext) -> frozenset[Any]:
    """Type ids present in the filtered army this frame."""
    return frozenset(unit.type_id for unit in enemy_army(ctx))


def filter_non_workers(units: Iterable[Any]) -> list[Any]:
    """Same worker strip for an arbitrary unit iterable."""
    return [unit for unit in units if unit.type_id not in WORKER_TYPES]


def enemy_has_air_units(ctx: BotContext) -> bool:
    """True if any currently-tracked enemy combat unit is flying - gates
    Macro Zerg's reactive Spire/Corruptor branch (see `steps.zerg.tech_up`/
    `spawn_macro_army`)."""
    return any(unit.is_flying for unit in enemy_army(ctx))


def enemy_army_supply(ctx: BotContext) -> float:
    """Scouted enemy combat supply this frame (workers already stripped).

    Sums `calculate_supply_cost` over `enemy_army`. Returns 0 when nothing
    is visible — callers that decide army-vs-tech posture should treat that
    as "not behind" so fog does not trigger army panic.
    """
    army = enemy_army(ctx)
    if not army:
        return 0.0
    return float(
        sum(ctx.bot.calculate_supply_cost(unit.type_id) for unit in army)
    )


def army_behind_on_supply(ctx: BotContext) -> bool:
    """True when visible enemy army supply exceeds ours.

    Empty/unseen enemy army → False (tech focus). Used by Macro Zerg's
    post-5:00 army-vs-tech posture.
    """
    enemy = enemy_army_supply(ctx)
    if enemy <= 0:
        return False
    return enemy > float(ctx.bot.supply_army)


def leave_enemy_army_supply(ctx: BotContext) -> float:
    """Best-known enemy combat supply for leave decisions.

    Returns ``max(peak_enemy_army_supply, enemy_army_supply(ctx))``. Peak only
    rises via ``observe_leave_intel`` — fog alone never invents supply.
    """
    current = enemy_army_supply(ctx)
    peak = float(getattr(ctx.state, "peak_enemy_army_supply", 0.0) or 0.0)
    return max(peak, current)


def observe_leave_intel(ctx: BotContext) -> float:
    """Latch peak from this frame's scouted combat supply; return leave supply.

    Call once per frame from ``main.on_step`` so leave gates keep a
    fog-stable floor. Does not invent units — empty vision leaves peak as-is.
    """
    current = enemy_army_supply(ctx)
    peak = float(getattr(ctx.state, "peak_enemy_army_supply", 0.0) or 0.0)
    if current > peak:
        ctx.state.peak_enemy_army_supply = current
        peak = current
    return max(peak, current)


def leave_army_supply(ctx: BotContext) -> float:
    """Commit-able our combat supply for leave gates — not raw `supply_army`.

    Sums `calculate_supply_cost` over `ctx.units_in_role(DEFENDING)`, which
    is already filtered to `build.army.types`. Queens never appear there;
    home Zerglings on `ZERGLING_DEFENDER_ROLE` stay off DEFENDING, so they
    cannot inflate the leave bar.

    Combat / gates should compare this to `leave_enemy_army_supply` (peak
    floor), never `ctx.bot.supply_army`. Same metric Soujirou shipped as
    `committed_leave_army_supply` — this is the intel home for it.
    """
    defenders = ctx.units_in_role(UnitRole.DEFENDING)
    if not defenders:
        return 0.0
    return float(
        sum(ctx.bot.calculate_supply_cost(unit.type_id) for unit in defenders)
    )


def early_aggression(ctx: BotContext) -> bool:
    """Fog-stable early-pressure latch for combat overlay.

    Kuuro's ``observe_early_aggression`` owns set/clear on
    ``RunState.early_aggression``. Combat / gates only read this.
    """
    return bool(getattr(ctx.state, "early_aggression", False))



# --- early aggression latch -------------------------------------------------

# Early window / clear hysteresis (seconds of game time).
_EARLY_WINDOW_S: float = 5 * 60.0
_CLEAR_HOLD_S: float = 12.0
# Visible combat near our main / natural.
_HOME_THREAT_RADIUS: float = 28.0
# Scouted bio/gateway ball size that counts as mid pressure.
_MID_BALL_MIN: int = 4
_MID_CENTER_RADIUS: float = 22.0
# Proxy production closer to us than to the enemy by this margin.
_PROXY_MARGIN: float = 20.0
# Sudden worker deaths between frames.
_WORKER_DEATH_SPIKE: int = 3
# Past opening + army OK clear floor (commit-able DEFENDING supply).
_CLEAR_ARMY_SUPPLY: float = 14.0

_EARLY_PRESSURE_TYPES: frozenset[UnitTypeId] = frozenset(
    {
        UnitTypeId.MARINE,
        UnitTypeId.REAPER,
        UnitTypeId.HELLION,
        UnitTypeId.HELLIONTANK,
        UnitTypeId.ZEALOT,
        UnitTypeId.ADEPT,
        UnitTypeId.STALKER,
        UnitTypeId.BANELING,
        UnitTypeId.ZERGLING,
    }
)

_PROXY_PRODUCTION: frozenset[UnitTypeId] = frozenset(
    {
        UnitTypeId.BARRACKS,
        UnitTypeId.BARRACKSFLYING,
        UnitTypeId.FACTORY,
        UnitTypeId.FACTORYFLYING,
        UnitTypeId.GATEWAY,
        UnitTypeId.WARPGATE,
        UnitTypeId.CYBERNETICSCORE,
        UnitTypeId.ROBOTICSFACILITY,
        UnitTypeId.SPINECRAWLER,
        UnitTypeId.SPORECRAWLER,
    }
)


def _our_home_points(ctx: BotContext) -> list[Any]:
    points: list[Any] = []
    try:
        points.append(ctx.production_location)
    except Exception:  # noqa: BLE001 - arcade / missing map data
        pass
    try:
        nat = ctx.own_nat
        if nat is not None:
            points.append(nat)
    except Exception:  # noqa: BLE001
        pass
    return points


def _enemy_start(ctx: BotContext) -> Any | None:
    starts = getattr(ctx.bot, "enemy_start_locations", None) or ()
    return starts[0] if starts else None


def _in_early_window(ctx: BotContext) -> bool:
    """Pre-Roach Warren (or hard 5:00)."""
    time_s = float(getattr(ctx.bot, "time", 0.0) or 0.0)
    if time_s > _EARLY_WINDOW_S:
        return False
    structures = getattr(ctx.bot, "structures", None)
    if structures is None:
        return True
    try:
        warren = structures(UnitTypeId.ROACHWARREN)
        ready = getattr(warren, "ready", warren)
        if ready is not None and len(ready) > 0:
            return False
    except Exception:  # noqa: BLE001
        return True
    return True


def _combat_near_home(ctx: BotContext) -> bool:
    homes = _our_home_points(ctx)
    if not homes:
        return False
    for unit in enemy_army(ctx):
        pos = getattr(unit, "position", None)
        if pos is None:
            continue
        for home in homes:
            try:
                if pos.distance_to(home) <= _HOME_THREAT_RADIUS:
                    return True
            except Exception:  # noqa: BLE001
                continue
    return False


def _proxy_production(ctx: BotContext) -> bool:
    """Enemy production closer to us than to their start — seen only."""
    enemy_structs = getattr(ctx.bot, "enemy_structures", None)
    if not enemy_structs:
        return False
    homes = _our_home_points(ctx)
    enemy_start = _enemy_start(ctx)
    if not homes or enemy_start is None:
        return False
    home = homes[0]
    for struct in enemy_structs:
        if getattr(struct, "type_id", None) not in _PROXY_PRODUCTION:
            continue
        pos = getattr(struct, "position", None)
        if pos is None:
            continue
        try:
            if pos.distance_to(home) + _PROXY_MARGIN < pos.distance_to(enemy_start):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _mid_ball(ctx: BotContext) -> bool:
    """Scouted early pressure types near map center or past mid toward us."""
    map_center = getattr(getattr(ctx.bot, "game_info", None), "map_center", None)
    if map_center is None:
        return False
    homes = _our_home_points(ctx)
    enemy_start = _enemy_start(ctx)
    count = 0
    for unit in enemy_army(ctx):
        if getattr(unit, "type_id", None) not in _EARLY_PRESSURE_TYPES:
            continue
        pos = getattr(unit, "position", None)
        if pos is None:
            continue
        try:
            near_center = pos.distance_to(map_center) <= _MID_CENTER_RADIUS
        except Exception:  # noqa: BLE001
            near_center = False
        past_mid = False
        if homes and enemy_start is not None:
            try:
                past_mid = pos.distance_to(homes[0]) < pos.distance_to(enemy_start)
            except Exception:  # noqa: BLE001
                past_mid = False
        if near_center or past_mid:
            count += 1
            if count >= _MID_BALL_MIN:
                return True
    return False


def _worker_death_spike(ctx: BotContext) -> bool:
    workers = getattr(ctx.bot, "workers", None)
    try:
        count = int(len(workers)) if workers is not None else 0
    except Exception:  # noqa: BLE001
        count = 0
    prev = getattr(ctx.state, "early_aggression_worker_count", None)
    spike = bool(prev is not None and prev - count >= _WORKER_DEATH_SPIKE)
    ctx.state.early_aggression_worker_count = count
    return spike


def _pressure_signal(ctx: BotContext) -> bool:
    """Seen early-aggression signal this frame — never invent units in fog."""
    # Worker spike always updates the counter; evaluate others independently.
    spike = _worker_death_spike(ctx)
    return spike or _combat_near_home(ctx) or _proxy_production(ctx) or _mid_ball(ctx)


def _army_ok_to_clear(ctx: BotContext) -> bool:
    try:
        return leave_army_supply(ctx) >= _CLEAR_ARMY_SUPPLY
    except Exception:  # noqa: BLE001
        return float(getattr(ctx.bot, "supply_army", 0.0) or 0.0) >= _CLEAR_ARMY_SUPPLY


def observe_early_aggression(ctx: BotContext) -> bool:
    """Set/clear fog-stable ``RunState.early_aggression`` each frame.

    Call once per frame from ``main.on_step`` (next to leave-intel observe).
    Raises on seen early pressure; clears after quiet hold when past the early
    window or our commit-able army is OK. Fog alone never invents units or
    clears the latch.
    """
    now = float(getattr(ctx.bot, "time", 0.0) or 0.0)
    signal = _pressure_signal(ctx)

    if signal:
        ctx.state.early_aggression_last_threat_at = now
        # Latch while still early, or whenever combat is already on our doorstep.
        if _in_early_window(ctx) or _combat_near_home(ctx):
            ctx.state.early_aggression = True
        return bool(ctx.state.early_aggression)

    if not getattr(ctx.state, "early_aggression", False):
        return False

    last = float(getattr(ctx.state, "early_aggression_last_threat_at", 0.0) or 0.0)
    quiet = (now - last) >= _CLEAR_HOLD_S
    if quiet and (not _in_early_window(ctx) or _army_ok_to_clear(ctx)):
        ctx.state.early_aggression = False
        return False
    return True
