"""Opening worker harassment scout (any race).

Claims one worker, enters the enemy main, circles the base for vision,
then harasses by priority (builder → gas if present → low HP → minerals).
Unfinished buildings feed the builder hunt after the scout lap. Kites only
when taking hits from 2 nearby sources (1 source if under half vitals),
peels with a one-shot mineral-walk toward the natural, then resumes once
shields are ~25% regenerated (and melee/surround has cleared).
Returns home on timer or early threat. One-shot: death or home latches
done so no replacement leaves.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from ares.behaviors.combat.individual import AMove
from ares.consts import ID, UnitRole, UnitTreeQueryType
from cython_extensions import cy_center, cy_closest_to, cy_distance_to, cy_towards
from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

if TYPE_CHECKING:
    from bot.core.context import BotContext

# --- constants --------------------------------------------------------------

HOME_TIME: float = 1 * 60 + 46
MIN_WORKERS_TO_CLAIM: int = 14
HOME_ARRIVE: float = 15.0
APPROACH_RADIUS: float = 28.0
# Stay in the enemy main — do not chase scouting/fleeing workers map-wide.
HARASS_BASE_RADIUS: float = 16.0
# Get main vision before the circle-scout lap finishes.
GAS_SCOUT_ENTER: float = 12.0
KITE_MAIN_RADIUS: float = 14.0
# Circle the enemy main (radius / waypoint count) before picking prey.
BASE_SCOUT_RADIUS: float = 10.0
BASE_SCOUT_WAYPOINTS: int = 6
BASE_SCOUT_WP_RADIUS: float = 3.5

ENEMY_WORKERS = frozenset(
    {UnitTypeId.SCV, UnitTypeId.PROBE, UnitTypeId.DRONE}
)
EARLY_HOME_THREATS = frozenset(
    {
        UnitTypeId.MARINE,
        UnitTypeId.QUEEN,
        UnitTypeId.STALKER,
        UnitTypeId.SENTRY,
    }
)
GAS_STRUCTURES = frozenset(
    {
        UnitTypeId.REFINERY,
        UnitTypeId.REFINERYRICH,
        UnitTypeId.EXTRACTOR,
        UnitTypeId.EXTRACTORRICH,
        UnitTypeId.ASSIMILATOR,
        UnitTypeId.ASSIMILATORRICH,
    }
)

OUR_WORKER: dict[Race, UnitTypeId] = {
    Race.Protoss: UnitTypeId.PROBE,
    Race.Terran: UnitTypeId.SCV,
    Race.Zerg: UnitTypeId.DRONE,
}
# Prefer the worker that finished this building as the harass scout.
OPENING_BUILDING: dict[Race, UnitTypeId | None] = {
    Race.Protoss: UnitTypeId.PYLON,
    Race.Terran: UnitTypeId.SUPPLYDEPOT,
    Race.Zerg: None,
}

# Re-enter after kite once shields are ~25% regenerated.
SHIELD_RESUME: float = 0.25
# Below half vitals, one damage source starts kite; otherwise need two.
VITAL_KITE_SINGLE: float = 0.5
# Enemies this close count as hit sources when we lose vitals.
HIT_SOURCE_RADIUS: float = 3.0
# Remember recent hitters so sequential swings still count as multi-source.
HIT_SOURCE_MEMORY: float = 1.5
BUILDER_RADIUS: float = 3.0
# Only treat a worker as on the job if this close to the unfinished
# building; wider radii were tagging mineral-line SCVs as "builders".
BUILDER_HUNT_RADIUS: float = 3.0
GAS_WORKER_RADIUS: float = 4.0
MINERAL_WORKER_RADIUS: float = 3.0
LOW_HP_FRACTION: float = 0.45
DEAL_RADIUS: float = 2.0
# Workers deal 5 — only commit through kite when focus is one hit from dead.
KILL_SHOT_HP: float = 5.0
KILL_SHOT_RANGE: float = 3.5
PRESSURE_RADIUS: float = 6.0
SURROUND_RADIUS: float = 4.0
SURROUND_COUNT: int = 2
PRESSURE_CLEAR_FRAMES: int = 8
MW_BURST: float = 0.8
# worker_harass_mw_until: last mineral-walk peel end time (diagnostics).

# --- helpers ----------------------------------------------------------------

def _vital(unit) -> float:
    return float(unit.health + unit.shield)

def _vital_pct(unit) -> float:
    mx = float(unit.health_max + unit.shield_max)
    return _vital(unit) / mx if mx > 0 else 1.0

def _shield_pct(unit) -> float:
    mx = float(getattr(unit, "shield_max", 0) or 0)
    return float(unit.shield) / mx if mx > 0 else 1.0

def _log(ctx: "BotContext", action: str) -> None:
    if ctx.state.worker_harass_last_action == action:
        return
    ctx.state.worker_harass_last_action = action
    ctx.log(f"HARASS {action}")

def _order_name(unit) -> str:
    orders = getattr(unit, "orders", None) or ()
    if not orders:
        return ""
    ability = getattr(orders[0], "ability", None)
    return str(
        getattr(ability, "id", None)
        or getattr(ability, "link_name", "")
        or ""
    )

def _is_gather_order(name: str) -> bool:
    low = name.lower()
    return "gather" in low or "harvest" in low

def _cancel_gather(scout) -> bool:
    """Stop a stuck mineral-walk gather so attack/move can take over."""
    if not _is_gather_order(_order_name(scout)):
        return False
    scout.stop()
    return True

def _unit_by_tag(ctx: "BotContext", tag: int | None):
    if tag is None:
        return None
    for u in ctx.bot.enemy_units:
        if u.tag == tag:
            return u
    return None

def _hit_sources(ctx: "BotContext", scout) -> list:
    """Enemy workers close enough to be dealing the damage we just took."""
    return [
        u
        for u in ctx.bot.enemy_units
        if u.type_id in ENEMY_WORKERS
        and not u.is_structure
        and cy_distance_to(scout.position, u.position) <= HIT_SOURCE_RADIUS
    ]

def _remember_hit_sources(
    ctx: "BotContext",
    scout,
    *,
    damage_hint: float | None = None,
) -> int:
    """Stamp likely hitters; return unique count in the memory window.

    With a damage hint, only stamp ~dmg/5 nearest workers so one swing does
    not treat the whole mineral line as hit sources.
    """
    now = ctx.bot.time
    recent = ctx.state.worker_harass_hit_sources
    nearby = _hit_sources(ctx, scout)
    if damage_hint is not None and damage_hint > 0:
        n = max(1, int(round(float(damage_hint) / 5.0)))
        nearby = sorted(
            nearby,
            key=lambda u: cy_distance_to(scout.position, u.position),
        )[:n]
    for u in nearby:
        recent[u.tag] = now
    return _prune_hit_sources(ctx)

def _warm_hit_sources(ctx: "BotContext", scout) -> int:
    """While kiting, refresh only already-known hitters still in range."""
    now = ctx.bot.time
    recent = ctx.state.worker_harass_hit_sources
    nearby_tags = {u.tag for u in _hit_sources(ctx, scout)}
    for tag in list(recent):
        if tag in nearby_tags:
            recent[tag] = now
    return _prune_hit_sources(ctx)

def _prune_hit_sources(ctx: "BotContext") -> int:
    now = ctx.bot.time
    recent = ctx.state.worker_harass_hit_sources
    for tag in [
        tag for tag, t in recent.items() if now - t > HIT_SOURCE_MEMORY
    ]:
        del recent[tag]
    return len(recent)

def _should_kite_from_hits(scout, source_count: int) -> bool:
    """Kite gate: 2 hit sources normally; 1 when under half vitals."""
    need = 1 if _vital_pct(scout) < VITAL_KITE_SINGLE else 2
    return source_count >= need

def _pick_first(group, focus_tag: int | None):
    """Sticky focus; otherwise the lowest-tag unit (stable 'first')."""
    if focus_tag is not None:
        for u in group:
            if u.tag == focus_tag:
                return u
    return min(group, key=lambda u: u.tag)

def _incomplete(ctx: "BotContext"):
    return [s for s in ctx.bot.enemy_structures if 0 < s.build_progress < 1]

def _workers_on_site(ctx: "BotContext", structure, radius: float = BUILDER_RADIUS):
    """Enemy workers physically on an unfinished building."""
    return [
        u
        for u in ctx.bot.enemy_units
        if u.type_id in ENEMY_WORKERS
        and not u.is_structure
        and cy_distance_to(u.position, structure.position) <= radius
    ]

def _building_workers(ctx: "BotContext", enemy_main: Point2 | None = None):
    """Workers constructing (API has no is_constructing)."""
    workers = []
    seen: set[int] = set()
    incomplete = _incomplete(ctx)

    for structure in incomplete:
        on_site = _workers_on_site(ctx, structure)
        if not on_site:
            continue
        closest = min(
            on_site,
            key=lambda u: cy_distance_to(u.position, structure.position),
        )
        if closest.tag in seen:
            continue
        workers.append(closest)
        seen.add(closest.tag)
        ctx.state.worker_harass_known_builders.add(closest.tag)

    for u in ctx.bot.enemy_units:
        if u.type_id not in ENEMY_WORKERS or u.is_structure or u.tag in seen:
            continue
        for order in getattr(u, "orders", []) or []:
            ability = getattr(order, "ability", None)
            name = str(
                getattr(ability, "id", None)
                or getattr(ability, "link_name", "")
                or ""
            ).lower()
            if "build" in name or "morph" in name:
                workers.append(u)
                seen.add(u.tag)
                ctx.state.worker_harass_known_builders.add(u.tag)
                break

    if incomplete:
        for tag in list(ctx.state.worker_harass_known_builders):
            unit = _unit_by_tag(ctx, tag)
            # Zerg morph reuses the drone tag as the unfinished building.
            if (
                unit is None
                or unit.is_structure
                or unit.type_id not in ENEMY_WORKERS
            ):
                ctx.state.worker_harass_known_builders.discard(tag)
                continue
            if tag in seen:
                continue
            near = any(
                cy_distance_to(unit.position, s.position) <= BUILDER_HUNT_RADIUS
                for s in incomplete
            )
            if near:
                workers.append(unit)
                seen.add(tag)
            else:
                ctx.state.worker_harass_known_builders.discard(tag)
    else:
        ctx.state.worker_harass_known_builders.clear()

    if enemy_main is not None:
        workers = [u for u in workers if _in_enemy_base(u, enemy_main)]
    return workers

def _closest_incomplete(ctx: "BotContext", scout):
    incomplete = _incomplete(ctx)
    if not incomplete:
        return None
    return cy_closest_to(position=scout.position, units=incomplete)

def _gas_workers(ctx: "BotContext", enemy_main: Point2 | None = None):
    gases = [s for s in ctx.bot.enemy_structures if s.type_id in GAS_STRUCTURES]
    workers = []
    for u in ctx.bot.enemy_units:
        if u.type_id not in ENEMY_WORKERS or u.is_structure:
            continue
        if enemy_main is not None and not _in_enemy_base(u, enemy_main):
            continue
        if getattr(u, "is_carrying_vespene", False):
            workers.append(u)
            continue
        for gas in gases:
            if cy_distance_to(u.position, gas.position) < GAS_WORKER_RADIUS:
                workers.append(u)
                break
    return workers

def _in_enemy_base(unit, enemy_main: Point2) -> bool:
    return cy_distance_to(unit.position, enemy_main) <= HARASS_BASE_RADIUS

def _mineral_workers(ctx: "BotContext", enemy_main: Point2):
    minerals = ctx.bot.mineral_field.closer_than(15, enemy_main)
    if not minerals:
        return []
    skip = {u.tag for u in _building_workers(ctx, enemy_main)} | {
        u.tag for u in _gas_workers(ctx, enemy_main)
    }
    workers = []
    for u in ctx.bot.enemy_units:
        if u.type_id not in ENEMY_WORKERS or u.is_structure or u.tag in skip:
            continue
        if not _in_enemy_base(u, enemy_main):
            continue
        if getattr(u, "is_carrying_minerals", False):
            workers.append(u)
            continue
        for patch in minerals:
            if cy_distance_to(u.position, patch.position) < MINERAL_WORKER_RADIUS:
                workers.append(u)
                break
    return workers

def _low_hp_workers(ctx: "BotContext", enemy_main: Point2):
    """Low-HP workers still inside the enemy main (not map-wide chase)."""
    workers = []
    for u in ctx.bot.enemy_units:
        if u.type_id not in ENEMY_WORKERS or u.is_structure:
            continue
        if not _in_enemy_base(u, enemy_main):
            continue
        if _vital_pct(u) < LOW_HP_FRACTION:
            workers.append(u)
    return workers

def _geyser_key(pos: Point2) -> tuple[float, float]:
    return (round(float(pos.x), 1), round(float(pos.y), 1))

def _base_scout_waypoints(enemy_main: Point2) -> list[Point2]:
    """Evenly spaced points around the enemy main for a full vision lap."""
    points: list[Point2] = []
    for i in range(BASE_SCOUT_WAYPOINTS):
        ang = (2.0 * math.pi * i) / BASE_SCOUT_WAYPOINTS
        points.append(
            Point2(
                (
                    enemy_main.x + BASE_SCOUT_RADIUS * math.cos(ang),
                    enemy_main.y + BASE_SCOUT_RADIUS * math.sin(ang),
                )
            )
        )
    return points

def _note_enemy_gas(ctx: "BotContext", enemy_main: Point2) -> None:
    """Latch gas presence if a gas structure is already visible in the main."""
    if ctx.state.worker_harass_enemy_has_gas:
        return
    gases = [
        s
        for s in ctx.bot.enemy_structures
        if s.type_id in GAS_STRUCTURES
        and cy_distance_to(s.position, enemy_main) < 18.0
    ]
    if not gases:
        return
    ctx.state.worker_harass_enemy_has_gas = True
    g = gases[0]
    ctx.log(
        f"HARASS gas check: {g.type_id.name} present "
        f"(progress={g.build_progress:.0%})"
    )

def _base_scout_move_target(
    ctx: "BotContext", scout, enemy_main: Point2
) -> Point2 | None:
    """Next circle waypoint. Finishes once every waypoint has been visited."""
    if ctx.state.worker_harass_gas_scouted:
        return None

    _note_enemy_gas(ctx, enemy_main)

    waypoints = _base_scout_waypoints(enemy_main)
    for wp in waypoints:
        if cy_distance_to(scout.position, wp) > BASE_SCOUT_WP_RADIUS:
            continue
        key = _geyser_key(wp)
        if key in ctx.state.worker_harass_geysers_seen:
            continue
        ctx.state.worker_harass_geysers_seen.add(key)
        ctx.log(
            f"HARASS base scout: waypoint "
            f"{len(ctx.state.worker_harass_geysers_seen)}/"
            f"{BASE_SCOUT_WAYPOINTS} @ {wp}"
        )

    unchecked = [
        wp
        for wp in waypoints
        if _geyser_key(wp) not in ctx.state.worker_harass_geysers_seen
    ]
    if not unchecked:
        ctx.state.worker_harass_gas_scouted = True
        gas = "gas present" if ctx.state.worker_harass_enemy_has_gas else "no gas"
        ctx.log(f"HARASS base scout: circle done ({gas})")
        return None

    return min(
        unchecked,
        key=lambda wp: cy_distance_to(scout.position, wp),
    )

def _harass_target(ctx: "BotContext", scout, enemy_main: Point2):
    tiers: list[tuple[str, list]] = [
        ("builder", _building_workers(ctx, enemy_main))
    ]
    if ctx.state.worker_harass_enemy_has_gas:
        tiers.append(("gas", _gas_workers(ctx, enemy_main)))
    tiers.append(("low_hp", _low_hp_workers(ctx, enemy_main)))
    tiers.append(("minerals", _mineral_workers(ctx, enemy_main)))
    focus = ctx.state.worker_harass_focus_tag
    # Drop focus that left the main (no map-wide chase).
    if focus is not None:
        focus_u = _unit_by_tag(ctx, focus)
        if focus_u is None or not _in_enemy_base(focus_u, enemy_main):
            ctx.state.worker_harass_focus_tag = None
            focus = None
    for name, group in tiers:
        if group:
            return name, _pick_first(group, focus)
    return None, None

def _attack(
    ctx: "BotContext",
    scout,
    priority: str,
    target,
    enemy_main: Point2 | None = None,
) -> bool:
    # Workers only — never attack unfinished buildings / morph pads.
    if (
        target is None
        or target.is_structure
        or target.type_id not in ENEMY_WORKERS
    ):
        if target is not None:
            ctx.state.worker_harass_known_builders.discard(target.tag)
        ctx.state.worker_harass_focus_tag = None
        return False
    main = enemy_main or ctx.bot.enemy_start_locations[0]
    if not _in_enemy_base(target, main):
        ctx.state.worker_harass_focus_tag = None
        return False
    changed = ctx.state.worker_harass_focus_tag != target.tag
    ctx.state.worker_harass_focus_tag = target.tag
    if changed:
        _log(ctx, f"attack {priority} {target.type_id.name}")
    # Stay SCOUTING so Mining cannot reclaim an idle probe in their base.
    ctx.mediator.assign_role(tag=scout.tag, role=UnitRole.SCOUTING)
    if _is_gather_order(_order_name(scout)):
        _cancel_gather(scout)
    else:
        orders = scout.orders
        if orders:
            tgt = orders[0].target
            if tgt == target.tag or getattr(tgt, "tag", None) == target.tag:
                # Keep the order only while prey stays in the main.
                if _in_enemy_base(target, main):
                    return True
                ctx.state.worker_harass_focus_tag = None
                scout.stop()
                return False
    # Unit-target only — attack-move can hit morph pads near the worker.
    if _unit_by_tag(ctx, target.tag) is not None:
        scout.attack(target)
    else:
        scout.move(target.position)
    return True

def _break_out_of_base_chase(
    ctx: "BotContext", scout, enemy_main: Point2
) -> bool:
    """Stop chasing a worker that left the main. True if chase was broken."""
    focus = _unit_by_tag(ctx, ctx.state.worker_harass_focus_tag)
    chase = focus
    if chase is None:
        orders = scout.orders
        if orders:
            tgt = orders[0].target
            tag = tgt if isinstance(tgt, int) else getattr(tgt, "tag", None)
            if tag is not None:
                u = _unit_by_tag(ctx, tag)
                if u is not None and u.type_id in ENEMY_WORKERS:
                    chase = u
    if chase is None or _in_enemy_base(chase, enemy_main):
        return False
    ctx.log(
        f"HARASS drop chase (left main, "
        f"{cy_distance_to(chase.position, enemy_main):.0f} from nexus)"
    )
    ctx.state.worker_harass_focus_tag = None
    scout.stop()
    return True

def _nearby_workers(ctx: "BotContext", scout, radius: float):
    return [
        u
        for u in ctx.bot.enemy_units
        if u.type_id in ENEMY_WORKERS
        and not u.is_structure
        and cy_distance_to(scout.position, u.position) <= radius
    ]

def _refresh_pressure(ctx: "BotContext", scout) -> tuple[bool, bool]:
    near = _nearby_workers(ctx, scout, max(PRESSURE_RADIUS, 8.0))
    prev = ctx.state.worker_harass_worker_dists
    new_dists: dict[int, float] = {}
    closing = 0
    on_top_n = 0
    aggressor_close = 0
    for w in near:
        dist = cy_distance_to(scout.position, w.position)
        new_dists[w.tag] = dist
        old = prev.get(w.tag)
        on_top = dist <= 3.2
        approaching = (
            old is not None
            and dist < old - 0.12
            and dist <= PRESSURE_RADIUS
        )
        if on_top:
            on_top_n += 1
        if on_top or approaching:
            closing += 1
            if dist <= SURROUND_RADIUS:
                aggressor_close += 1
    ctx.state.worker_harass_worker_dists = new_dists
    surrounded = aggressor_close >= SURROUND_COUNT
    # Sensitive pressure until shields hit SHIELD_RESUME; after that require a
    # real surround / melee / multi-approach so we can re-enter sooner.
    shields_ready = _shield_pct(scout) >= SHIELD_RESUME
    if shields_ready:
        instant = surrounded or on_top_n >= 1 or closing >= 2
    else:
        instant = surrounded or closing > 0 or on_top_n >= 1

    if instant:
        ctx.state.worker_harass_pressure_clear = 0
        return True, surrounded

    if ctx.state.worker_harass_kiting and not shields_ready:
        ctx.state.worker_harass_pressure_clear += 1
        if ctx.state.worker_harass_pressure_clear < PRESSURE_CLEAR_FRAMES:
            return True, surrounded
    else:
        ctx.state.worker_harass_pressure_clear = 0
    return False, surrounded

def _can_kill_shot(ctx: "BotContext", scout) -> bool:
    focus = _unit_by_tag(ctx, ctx.state.worker_harass_focus_tag)
    if focus is None:
        return False
    return (
        _vital(focus) <= KILL_SHOT_HP
        and cy_distance_to(scout.position, focus.position) <= KILL_SHOT_RANGE
    )

def _mineral_walk(
    ctx: "BotContext",
    scout,
    enemy_main: Point2,
    enemy_nat: Point2,
    threats,
) -> bool:
    """Gather a natural mineral patch to peel through workers toward the nat."""
    minerals = list(ctx.bot.mineral_field.closer_than(15, enemy_nat))
    if not minerals:
        minerals = list(ctx.bot.mineral_field.closer_than(22, enemy_nat))
    if not minerals:
        return False
    threat_pos = Point2(cy_center(threats)) if threats else scout.position
    # Prefer patches at the natural (away from the main mineral line wrap).
    patch = min(
        minerals,
        key=lambda m: (
            cy_distance_to(m.position, enemy_nat),
            -cy_distance_to(m.position, threat_pos),
            -cy_distance_to(m.position, enemy_main),
        ),
    )
    scout.gather(patch)
    return True

def _kite(
    ctx: "BotContext",
    scout,
    enemy_main: Point2,
    enemy_nat: Point2,
    *,
    surrounded: bool,
) -> None:
    if cy_distance_to(scout.position, enemy_main) > KITE_MAIN_RADIUS + 2.0:
        scout.move(enemy_main)
        return

    near = _nearby_workers(ctx, scout, 8.0)

    # Surrounded: always peel toward the natural via mineral-walk. Do not
    # wait for unfinished buildings to clear — that blocked peels during
    # Barracks fights and left the scout dying in the mineral line.
    if surrounded:
        used_mw = _mineral_walk(ctx, scout, enemy_main, enemy_nat, near)
        if used_mw:
            _log(ctx, "mineral-walk escape (nat minerals)")
            ctx.state.worker_harass_mw_until = ctx.bot.time + MW_BURST
            return
        # No nat minerals visible yet — still run toward the natural.
        toward_nat = Point2(cy_towards(scout.position, enemy_nat, 8.0))
        _log(ctx, "peel toward natural")
        scout.move(toward_nat)
        return

    # Soft kite: bias toward the natural so we do not deepen a mineral wrap.
    threats = list(near)
    if not threats:
        ground = ctx.mediator.get_units_in_range(
            start_points=[scout.position],
            distances=8.0,
            query_tree=UnitTreeQueryType.EnemyGround,
        )[0]
        threats = [u for u in ground if not u.is_structure]

    toward_nat = Point2(cy_towards(scout.position, enemy_nat, 8.0))
    if threats:
        enemy = cy_closest_to(position=scout.position, units=threats)
        away = Point2(cy_towards(enemy.position, scout.position, 6.0))
        # Blend threat-away with natural so we exit the main line, not hug it.
        blended = Point2(
            (
                (away.x + toward_nat.x) * 0.5,
                (away.y + toward_nat.y) * 0.5,
            )
        )
        _log(ctx, "kite toward natural")
        scout.move(blended)
        return

    scout.move(toward_nat)
def _update_combat_stats(ctx: "BotContext", scout) -> None:
    hp = _vital(scout)
    prev = ctx.state.worker_harass_last_hp
    if prev is not None and hp < prev:
        taken = prev - hp
        ctx.state.worker_harass_damage_taken += taken
        if taken >= 5:
            ctx.log(
                f"HARASS took {taken:.0f} dmg "
                f"(total taken {ctx.state.worker_harass_damage_taken:.0f})"
            )

    near = [
        u
        for u in ctx.bot.enemy_units
        if u.type_id in ENEMY_WORKERS
        and not u.is_structure
        and cy_distance_to(scout.position, u.position) <= DEAL_RADIUS
    ]
    seen: set[int] = set()
    for u in near:
        seen.add(u.tag)
        cur = _vital(u)
        old = ctx.state.worker_harass_prey_hp.get(u.tag)
        if old is not None and cur < old:
            dealt = old - cur
            ctx.state.worker_harass_damage_dealt += dealt
            if dealt >= 5:
                ctx.log(
                    f"HARASS dealt {dealt:.0f} to {u.type_id.name} "
                    f"(total dealt {ctx.state.worker_harass_damage_dealt:.0f})"
                )
        ctx.state.worker_harass_prey_hp[u.tag] = cur
    for tag in list(ctx.state.worker_harass_prey_hp):
        if tag not in seen:
            del ctx.state.worker_harass_prey_hp[tag]

def _log_combat(ctx: "BotContext", reason: str) -> None:
    ctx.log(
        f"HARASS combat ({reason}): "
        f"dealt {ctx.state.worker_harass_damage_dealt:.0f}, "
        f"took {ctx.state.worker_harass_damage_taken:.0f}"
    )

def _clear_mission(ctx: "BotContext") -> None:
    ctx.state.worker_harass_done = True
    ctx.state.worker_harass_tags.clear()
    ctx.state.worker_harass_miss_frames = 0
    ctx.state.worker_harass_kiting = False
    ctx.state.worker_harass_last_hp = None
    ctx.state.worker_harass_prey_hp.clear()
    ctx.state.worker_harass_returning = False
    ctx.state.worker_harass_last_action = None
    ctx.state.worker_harass_gas_scouted = False
    ctx.state.worker_harass_enemy_has_gas = False
    ctx.state.worker_harass_geysers_seen.clear()
    ctx.state.worker_harass_focus_tag = None
    ctx.state.worker_harass_known_builders.clear()
    ctx.state.worker_harass_worker_dists.clear()
    ctx.state.worker_harass_pressure_clear = 0
    ctx.state.worker_harass_mw_until = 0.0
    ctx.state.worker_harass_hit_sources.clear()

def _begin_return(ctx: "BotContext", scouts, reason: str) -> None:
    if not ctx.state.worker_harass_returning:
        for scout in scouts:
            dist = cy_distance_to(scout.position, ctx.bot.start_location)
            ctx.log(
                f"HARASS returning home ({reason}) from {dist:.0f} away"
            )
        _log_combat(ctx, reason)
        ctx.state.worker_harass_returning = True
        ctx.state.worker_harass_last_action = None
    for scout in scouts:
        scout.move(ctx.bot.start_location)

def _finish_return(ctx: "BotContext", scout) -> None:
    home_mins = ctx.bot.mineral_field.closer_than(12, ctx.bot.start_location)
    ctx.mediator.assign_role(tag=scout.tag, role=UnitRole.GATHERING)
    if home_mins:
        patch = cy_closest_to(
            position=ctx.bot.start_location, units=home_mins
        )
        scout.gather(patch)
        ctx.log("HARASS arrived home; gathering")
    else:
        ctx.bot.register_behavior(
            AMove(unit=scout, target=ctx.bot.start_location)
        )
        ctx.log("HARASS arrived home; no minerals found")
    _clear_mission(ctx)

def _remember_opening_builder(ctx: "BotContext", building: UnitTypeId) -> None:
    if ctx.state.worker_harass_opening_builder_tag is not None:
        return
    if ctx.bot.time > 90.0:
        return
    tracker = ctx.mediator.get_building_tracker_dict
    for tag, info in tracker.items():
        if info.get(ID) == building:
            ctx.state.worker_harass_opening_builder_tag = tag
            ctx.log(f"HARASS opening builder latched (tag={tag})")
            return

def _claim_worker(
    ctx: "BotContext",
    *,
    worker_type: UnitTypeId,
    opening_building: UnitTypeId | None,
    home_time: float,
    min_workers: int,
) -> None:
    if opening_building is not None:
        _remember_opening_builder(ctx, opening_building)
    if ctx.state.worker_harass_done or ctx.bot.time >= home_time:
        return

    enemy = ctx.bot.enemy_start_locations[0]
    runners = list(
        ctx.mediator.get_units_from_role(
            role=UnitRole.BUILD_RUNNER_SCOUT, unit_type=worker_type
        )
    )
    if runners:
        keep = cy_closest_to(position=enemy, units=runners)
        for u in runners:
            if u.tag != keep.tag:
                ctx.mediator.assign_role(tag=u.tag, role=UnitRole.GATHERING)
        ctx.state.worker_harass_tags = {keep.tag}
        keep.stop()
        ctx.mediator.assign_role(tag=keep.tag, role=UnitRole.SCOUTING)
        ctx.state.worker_harass_done = True
        ctx.log(f"HARASS assigned (build-runner, tag={keep.tag})")
        return

    if ctx.state.worker_harass_tags:
        return

    tracker = ctx.mediator.get_building_tracker_dict
    opener = ctx.state.worker_harass_opening_builder_tag
    if opener is not None and opener in tracker:
        building = tracker[opener].get(ID)
        name = getattr(building, "name", str(building))
        ctx.log_once(
            f"harass_wait_builder_{name}",
            f"HARASS waiting for opening builder (still on {name})",
        )
        return

    if opener is not None and opener not in tracker:
        worker = next((u for u in ctx.bot.workers if u.tag == opener), None)
        if worker is None:
            worker = next(
                (u for u in ctx.bot.units(worker_type) if u.tag == opener),
                None,
            )
        ready = opening_building is None or (
            ctx.bot.structures(opening_building)
            or ctx.bot.structure_pending(opening_building)
        )
        if worker is not None and ready:
            worker.stop()
            ctx.mediator.remove_worker_from_mineral(worker_tag=worker.tag)
            ctx.state.worker_harass_tags = {worker.tag}
            ctx.mediator.assign_role(tag=worker.tag, role=UnitRole.SCOUTING)
            ctx.state.worker_harass_done = True
            ctx.log(f"HARASS assigned (opening builder, tag={worker.tag})")
            return
        if worker is None and ctx.bot.time > 45.0:
            ctx.log(f"HARASS opening builder tag={opener} gone; falling back")
            ctx.state.worker_harass_opening_builder_tag = None

    if ctx.state.worker_harass_opening_builder_tag is not None:
        return
    if len(ctx.bot.workers) < min_workers:
        ctx.log_once(
            "harass_wait_workers",
            f"HARASS waiting for {min_workers} workers "
            f"(have {len(ctx.bot.workers)})",
        )
        return

    gathering = {
        u.tag for u in ctx.mediator.get_units_from_role(role=UnitRole.GATHERING)
    }
    pool = [
        u
        for u in ctx.bot.workers
        if u.type_id == worker_type and u.tag in gathering
    ] or list(ctx.bot.units(worker_type))
    if not pool:
        return
    scout = cy_closest_to(position=enemy, units=pool)
    scout.stop()
    ctx.mediator.remove_worker_from_mineral(worker_tag=scout.tag)
    ctx.state.worker_harass_tags = {scout.tag}
    ctx.mediator.assign_role(tag=scout.tag, role=UnitRole.SCOUTING)
    ctx.state.worker_harass_done = True
    ctx.log(f"HARASS assigned (fallback, tag={scout.tag})")

# --- public routine ---------------------------------------------------------

def worker_harass(
    *,
    home_time: float = HOME_TIME,
    min_workers: int = MIN_WORKERS_TO_CLAIM,
):
    """One opening worker: enter main → circle scout → harass → home.

    Worker type and preferred opening builder follow `ctx.build.race`.
    """

    def routine(ctx: "BotContext") -> None:
        race = ctx.build.race
        worker_type = OUR_WORKER.get(race)
        if worker_type is None:
            return
        opening = OPENING_BUILDING.get(race)

        _claim_worker(
            ctx,
            worker_type=worker_type,
            opening_building=opening,
            home_time=home_time,
            min_workers=min_workers,
        )
        if not ctx.state.worker_harass_tags:
            return

        scouts = [
            u
            for u in ctx.mediator.get_units_from_role(
                role=UnitRole.SCOUTING, unit_type=worker_type
            )
            if u.tag in ctx.state.worker_harass_tags
        ]
        if not scouts:
            scouts = [
                u
                for u in ctx.bot.units(worker_type)
                if u.tag in ctx.state.worker_harass_tags
            ]
        if not scouts:
            ctx.state.worker_harass_miss_frames += 1
            if ctx.state.worker_harass_miss_frames >= 16:
                _log_combat(ctx, "scout died")
                _clear_mission(ctx)
            return

        ctx.state.worker_harass_miss_frames = 0

        if len(scouts) > 1:
            enemy = ctx.bot.enemy_start_locations[0]
            keep = cy_closest_to(position=enemy, units=scouts)
            for u in scouts:
                if u.tag == keep.tag:
                    continue
                ctx.mediator.assign_role(tag=u.tag, role=UnitRole.GATHERING)
                ctx.state.worker_harass_tags.discard(u.tag)
            scouts = [keep]
            ctx.log("HARASS released extra worker (one-scout cap)")

        leave_reason: str | None = None
        if ctx.state.worker_harass_returning:
            leave_reason = "returning"
        elif ctx.bot.time >= home_time:
            leave_reason = f"@{int(home_time) // 60}:{int(home_time) % 60:02d}"
        elif any(u.type_id in EARLY_HOME_THREATS for u in ctx.bot.enemy_units):
            leave_reason = "early threat spotted"

        if leave_reason is not None:
            if not ctx.state.worker_harass_returning:
                _begin_return(ctx, scouts, leave_reason)
            for scout in scouts:
                dist = cy_distance_to(scout.position, ctx.bot.start_location)
                if dist <= HOME_ARRIVE:
                    _finish_return(ctx, scout)
                else:
                    bucket = int(dist // 10) * 10
                    _log(ctx, f"pathing home (~{bucket} out)")
                    home_mins = ctx.bot.mineral_field.closer_than(
                        15, ctx.bot.start_location
                    )
                    if home_mins and cy_distance_to(
                        scout.position, ctx.bot.enemy_start_locations[0]
                    ) < 25:
                        patch = cy_closest_to(
                            position=ctx.bot.start_location, units=home_mins
                        )
                        scout.gather(patch)
                    else:
                        scout.move(ctx.bot.start_location)
            return

        enemy_main = ctx.bot.enemy_start_locations[0]
        enemy_nat = ctx.mediator.get_enemy_nat

        for scout in scouts:
            _update_combat_stats(ctx, scout)
            hp = _vital(scout)
            prev = ctx.state.worker_harass_last_hp
            took_hit = prev is not None and hp < prev - 0.5
            ctx.state.worker_harass_last_hp = hp

            _, surrounded = _refresh_pressure(ctx, scout)
            vitals = _vital_pct(scout)

            if (
                not ctx.state.worker_harass_kiting
                and _break_out_of_base_chase(ctx, scout, enemy_main)
            ):
                scout.move(enemy_main)
                continue

            if took_hit:
                dmg = (prev - hp) if prev is not None else None
                source_count = _remember_hit_sources(
                    ctx, scout, damage_hint=dmg
                )
                if (
                    not ctx.state.worker_harass_kiting
                    and _should_kite_from_hits(scout, source_count)
                ):
                    need = 1 if vitals < VITAL_KITE_SINGLE else 2
                    ctx.state.worker_harass_kiting = True
                    ctx.log(
                        f"HARASS kite ({source_count} hit sources, "
                        f"need {need}, vitals={vitals:.0%})"
                    )

            if ctx.state.worker_harass_kiting:
                # Refresh known hitters in range; do not re-stamp every neighbor.
                _warm_hit_sources(ctx, scout)
                if _can_kill_shot(ctx, scout):
                    focus = _unit_by_tag(ctx, ctx.state.worker_harass_focus_tag)
                    if (
                        focus is not None
                        and not focus.is_structure
                        and focus.type_id in ENEMY_WORKERS
                    ):
                        _log(ctx, f"kill shot on {focus.type_id.name}")
                        _cancel_gather(scout)
                        scout.attack(focus)
                        continue

                shield_pct = _shield_pct(scout)
                # Soft "closing" pressure was keeping the probe in kite for
                # the whole shield-regen window. Resume when shields hit
                # SHIELD_RESUME and we are not in a surround / melee pile.
                melee_n = len(_nearby_workers(ctx, scout, 3.2))
                can_resume = (
                    shield_pct >= SHIELD_RESUME
                    and not surrounded
                    and melee_n == 0
                )
                if not can_resume:
                    # Escaped melee: hold for shield regen instead of
                    # peeling further toward the natural (idle look).
                    if not surrounded and melee_n == 0:
                        _log(ctx, "hold for shield regen")
                        _cancel_gather(scout)
                        scout.stop()
                        continue
                    # Mode is logged inside _kite (MW peel / soft kite).
                    _kite(
                        ctx,
                        scout,
                        enemy_main,
                        enemy_nat,
                        surrounded=surrounded,
                    )
                    continue

                dist_main = cy_distance_to(scout.position, enemy_main)
                _cancel_gather(scout)
                ctx.state.worker_harass_kiting = False
                ctx.state.worker_harass_mw_until = 0.0
                ctx.state.worker_harass_hit_sources.clear()
                ctx.state.worker_harass_focus_tag = None
                ctx.log(
                    f"HARASS resume harass "
                    f"(shields={shield_pct:.0%}, vitals={vitals:.0%})"
                )
                if dist_main > KITE_MAIN_RADIUS:
                    _log(ctx, "re-enter main after peel")
                    scout.move(enemy_main)
                    continue

            dist_main = cy_distance_to(scout.position, enemy_main)
            if (
                not ctx.state.worker_harass_gas_scouted
                and dist_main > APPROACH_RADIUS
            ):
                # En route: only engage a builder we already see; otherwise
                # keep pathing in so the circle scout can start.
                builders = _building_workers(ctx, enemy_main)
                if builders and _attack(
                    ctx,
                    scout,
                    "builder",
                    _pick_first(builders, ctx.state.worker_harass_focus_tag),
                    enemy_main,
                ):
                    continue
                unfinished = _closest_incomplete(ctx, scout)
                if unfinished is not None:
                    _log(ctx, f"hunt builder at {unfinished.type_id.name}")
                    scout.move(unfinished.position)
                    continue
                if (
                    ctx.state.worker_harass_geysers_seen
                    or ctx.state.worker_harass_known_builders
                ):
                    _log(ctx, "re-enter main after peel")
                    scout.move(enemy_main)
                    continue
                waypoint = (
                    enemy_nat
                    if cy_distance_to(scout.position, enemy_nat) > 10.0
                    else enemy_main
                )
                where = "nat" if waypoint is enemy_nat else "main"
                _log(ctx, f"path to enemy {where}")
                scout.move(waypoint)
                continue

            # Inside approach range: circle for vision, but abort the lap the
            # moment a builder is spotted.
            if (
                not ctx.state.worker_harass_gas_scouted
                and dist_main <= APPROACH_RADIUS
            ):
                if dist_main > GAS_SCOUT_ENTER:
                    _log(ctx, "enter main before base scout")
                    scout.move(enemy_main)
                    continue

                builders = _building_workers(ctx, enemy_main)
                unfinished = _closest_incomplete(ctx, scout)
                on_site = (
                    _workers_on_site(ctx, unfinished) if unfinished else []
                )
                if not builders and on_site:
                    builders = on_site
                if builders:
                    ctx.state.worker_harass_gas_scouted = True
                    target = _pick_first(
                        builders, ctx.state.worker_harass_focus_tag
                    )
                    ctx.log(
                        "HARASS base scout: builder spotted — abort circle"
                    )
                    if _attack(ctx, scout, "builder", target, enemy_main):
                        continue

                scout_pos = _base_scout_move_target(ctx, scout, enemy_main)
                if scout_pos is not None:
                    seen_n = len(ctx.state.worker_harass_geysers_seen)
                    _log(
                        ctx,
                        f"circle base scout {seen_n}/{BASE_SCOUT_WAYPOINTS}",
                    )
                    scout.move(scout_pos)
                    continue

            builders = _building_workers(ctx, enemy_main)
            unfinished = _closest_incomplete(ctx, scout)
            # Sticky first builder — never thrash between multiple builders.
            if builders:
                target = _pick_first(
                    builders, ctx.state.worker_harass_focus_tag
                )
                if _attack(ctx, scout, "builder", target, enemy_main):
                    continue

            # Unfinished pad: prefer on-site builders / pad hunt over gas /
            # mineral workers so we do not skip the Barracks SCV.
            if unfinished is not None:
                on_site = _workers_on_site(ctx, unfinished)
                if on_site:
                    target = _pick_first(
                        on_site, ctx.state.worker_harass_focus_tag
                    )
                    ctx.state.worker_harass_known_builders.add(target.tag)
                    if _attack(ctx, scout, "builder", target, enemy_main):
                        continue
                dist_job = cy_distance_to(
                    scout.position, unfinished.position
                )
                if dist_job > BUILDER_RADIUS:
                    _log(ctx, f"hunt builder at {unfinished.type_id.name}")
                    scout.move(unfinished.position)
                    continue

            priority, target = _harass_target(ctx, scout, enemy_main)
            if target is not None and _attack(
                ctx, scout, priority, target, enemy_main
            ):
                continue

            if ctx.state.worker_harass_focus_tag is not None:
                if _unit_by_tag(ctx, ctx.state.worker_harass_focus_tag) is None:
                    ctx.log("HARASS focus target dead/gone")
                    ctx.state.worker_harass_focus_tag = None

            _log(ctx, "hold at enemy main (no prey)")
            scout.move(enemy_main)

    return routine
