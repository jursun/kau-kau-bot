"""Protoss support micro: Warp Prism, Observer, Adept, scout Probe.

Chargelot (and other P builds) keep these units out of DEFENDING/ATTACKING
via `core.roles.SUPPORT_ROLES`. These routines are what actually drive them.

Scout Probe: prefer the opening-Pylon builder; walk enemy main geysers to
check for gas before harassing (builders interrupt the gas walk); then harass
by priority (builder → gas if present → low HP → minerals) with sticky
lowest-HP focus to secure kills; kite in the enemy main (commit kill shots);
path home@1:48 (stay SCOUTING until near base so mining does not reassign
onto enemy minerals); leave earlier on Marine/Queen/Stalker/Sentry.
Mineral-walk peels gather enemy natural minerals to break a surround.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ares.behaviors.combat import CombatManeuver
from ares.behaviors.combat.individual import (
    AMove,
    AttackTarget,
    KeepUnitSafe,
    MoveToSafeTarget,
    ShootTargetInRange,
    UseAbility,
)
from ares.consts import ID, UnitRole, UnitTreeQueryType
from cython_extensions import cy_center, cy_closest_to, cy_distance_to, cy_towards
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.routines import targeting

if TYPE_CHECKING:
    from bot.core.context import BotContext

# Timings (game seconds) — Jason Chargelot brief 2026-09-12.
SCOUT_PROBE_HOME_TIME: float = 1 * 60 + 48
ADEPT_NATURAL_HARASS_TIME: float = 3 * 60
ARMY_LEAVE_TIME: float = 5 * 60 + 20  # used by build wave_gate; documented here

PRISM_STANDOFF: float = 6.0
"""How far behind the army center the Prism sits while phasing."""
OBS_FOLLOW_RADIUS: float = 3.0
"""How tightly the Observer hugs the army destination / center."""
SCOUT_WORKER_TYPES = frozenset({UnitTypeId.SCV, UnitTypeId.PROBE, UnitTypeId.DRONE})
SCOUT_EARLY_HOME_THREATS = frozenset(
    {
        UnitTypeId.MARINE,
        UnitTypeId.QUEEN,
        UnitTypeId.STALKER,
        UnitTypeId.SENTRY,
    }
)
SCOUT_GAS_STRUCTURES = frozenset(
    {
        UnitTypeId.REFINERY,
        UnitTypeId.REFINERYRICH,
        UnitTypeId.EXTRACTOR,
        UnitTypeId.EXTRACTORRICH,
        UnitTypeId.ASSIMILATOR,
        UnitTypeId.ASSIMILATORRICH,
    }
)
SCOUT_SHIELD_RESUME_AT: float = 0.35
"""Resume harass once shields recover to this (or pressure clears)."""
SCOUT_RESUME_NO_PRESSURE: float = 0.12
"""If nobody is targeting us, resume harass above this shield fraction."""
SCOUT_GAS_WORKER_RADIUS: float = 4.0
SCOUT_MINERAL_WORKER_RADIUS: float = 3.0
SCOUT_LOW_HP_WORKER: float = 0.45
"""Health+shield fraction below which a worker is "low HP" prey."""
SCOUT_DEAL_RADIUS: float = 2.0
"""Workers this close to the Probe count toward damage-dealt tracking."""
SCOUT_BUILDER_RADIUS: float = 8.0
"""Workers this close to an incomplete building count as constructing."""
SCOUT_BUILDER_HUNT_RADIUS: float = 12.0
"""Path toward incomplete buildings / soft-match nearby workers at the job."""
SCOUT_KILL_SHOT_HP: float = 35.0
"""Commit through kite when the focus worker is this low and in melee."""
SCOUT_KILL_SHOT_RANGE: float = 3.5
SCOUT_HOME_ARRIVE: float = 15.0
"""Distance to start before handing the returning scout back to mining."""
SCOUT_APPROACH_RADIUS: float = 28.0
"""Farther than this from enemy main → still approaching (nat/main path)."""
SCOUT_GAS_CHECK_RADIUS: float = 8.0
"""Close enough to a geyser to confirm whether gas is built on it."""
SCOUT_KITE_MAIN_RADIUS: float = 14.0
"""Keep shield-regen kite destinations within this of enemy main."""
SCOUT_PRESSURE_RADIUS: float = 6.0
"""Workers inside this can trigger proactive kite."""
SCOUT_SURROUND_RADIUS: float = 4.0
SCOUT_SURROUND_COUNT: int = 2
"""Approaching/on-top workers inside SURROUND_RADIUS → mineral-walk escape."""
SCOUT_PRESSURE_CLEAR_FRAMES: int = 8
"""Stay in kite until pressure is clear this many frames (~0.35s)."""
SCOUT_MW_BURST: float = 0.8
"""Seconds to keep gathering during a mineral-walk peel."""


def _biggest_attacking_squad(ctx: "BotContext"):
    squads = ctx.mediator.get_squads(role=UnitRole.ATTACKING, squad_radius=9.0)
    if not squads:
        return None
    return max(squads, key=lambda s: len(s.squad_units))


def _army_anchor(ctx: "BotContext") -> Point2 | None:
    """Live army center if attacking; else rally / natural."""
    squad = _biggest_attacking_squad(ctx)
    if squad is not None and squad.squad_units:
        return Point2(cy_center(squad.squad_units))
    attackers = ctx.units_in_role(UnitRole.ATTACKING)
    if attackers:
        return Point2(cy_center(attackers))
    return None


def _warpgates_ready_to_warp(ctx: "BotContext") -> bool:
    """True when at least one Warp Gate can currently train-warp."""
    for gate in ctx.bot.structures(UnitTypeId.WARPGATE).ready:
        abilities = gate.abilities
        # Any TRAINWARP_* means a warp-in slot is open this frame.
        if any(a.name.startswith("TRAINWARP") for a in abilities):
            return True
    return False


def escort_warp_prism():
    """Phase behind the army; reposition in transport mode while WG on CD.

    One `CombatManeuver` per Prism per frame (APM-safe): air influence retreat
    first, then morph / move so orders do not fight each other.
    """

    def routine(ctx: "BotContext") -> None:
        prisms = [
            u
            for u in ctx.mediator.get_units_from_role(role=UnitRole.DROP_SHIP)
            if u.type_id in (UnitTypeId.WARPPRISM, UnitTypeId.WARPPRISMPHASING)
        ]
        if not prisms:
            return

        army = _army_anchor(ctx)
        home = ctx.mediator.get_own_nat
        can_warp = _warpgates_ready_to_warp(ctx)
        grid = ctx.mediator.get_air_grid

        for prism in prisms:
            maneuver = CombatManeuver()
            maneuver.add(KeepUnitSafe(unit=prism, grid=grid))

            if army is None:
                hold = targeting.rally_point(ctx)
                if prism.type_id == UnitTypeId.WARPPRISMPHASING:
                    maneuver.add(
                        UseAbility(
                            AbilityId.MORPH_WARPPRISMTRANSPORTMODE, prism
                        )
                    )
                maneuver.add(
                    MoveToSafeTarget(unit=prism, grid=grid, target=hold)
                )
                ctx.bot.register_behavior(maneuver)
                continue

            # Behind the army: from army toward our natural.
            behind = Point2(cy_towards(army, home, PRISM_STANDOFF))

            if can_warp:
                dist = cy_distance_to(prism.position, behind)
                if dist > 2.5:
                    # Must move — drop phase first, then path on the air grid.
                    if prism.type_id == UnitTypeId.WARPPRISMPHASING:
                        maneuver.add(
                            UseAbility(
                                AbilityId.MORPH_WARPPRISMTRANSPORTMODE, prism
                            )
                        )
                    maneuver.add(
                        MoveToSafeTarget(
                            unit=prism, grid=grid, target=behind
                        )
                    )
                elif prism.type_id != UnitTypeId.WARPPRISMPHASING:
                    if AbilityId.MORPH_WARPPRISMPHASINGMODE in prism.abilities:
                        maneuver.add(
                            UseAbility(
                                AbilityId.MORPH_WARPPRISMPHASINGMODE, prism
                            )
                        )
                # else already phased on the pocket — stay
            else:
                if prism.type_id == UnitTypeId.WARPPRISMPHASING:
                    maneuver.add(
                        UseAbility(
                            AbilityId.MORPH_WARPPRISMTRANSPORTMODE, prism
                        )
                    )
                maneuver.add(
                    MoveToSafeTarget(unit=prism, grid=grid, target=behind)
                )

            ctx.bot.register_behavior(maneuver)

    return routine


def escort_observer():
    """Keep Observer over the army; kite if targeted, stay near the ball."""

    def routine(ctx: "BotContext") -> None:
        observers = ctx.bot.units(UnitTypeId.OBSERVER)
        if not observers:
            return

        squad = _biggest_attacking_squad(ctx)
        if squad is None:
            # Hover natural until the push leaves.
            hold = targeting.rally_point(ctx)
            grid = ctx.mediator.get_air_grid
            for obs in observers:
                maneuver = CombatManeuver()
                maneuver.add(KeepUnitSafe(unit=obs, grid=grid))
                maneuver.add(
                    MoveToSafeTarget(unit=obs, grid=grid, target=hold)
                )
                ctx.bot.register_behavior(maneuver)
            return

        target = targeting.squad_destination(ctx, squad.squad_position)
        # Prefer slightly above the live center so detection covers the ball.
        follow = Point2(cy_towards(squad.squad_position, target, OBS_FOLLOW_RADIUS))
        grid = ctx.mediator.get_air_grid

        for obs in observers:
            maneuver = CombatManeuver()
            # Kite when under fire, but MoveToSafeTarget keeps it near follow.
            maneuver.add(KeepUnitSafe(unit=obs, grid=grid))
            maneuver.add(
                MoveToSafeTarget(
                    unit=obs, grid=grid, target=follow, radius=6.0
                )
            )
            ctx.bot.register_behavior(maneuver)

    return routine


def _unit_by_tag(ctx: "BotContext", tag: int | None):
    if tag is None:
        return None
    for u in ctx.bot.enemy_units:
        if u.tag == tag:
            return u
    return None


def _pick_focus_worker(scout, group, focus_tag: int | None):
    """Sticky focus if still in the group; else lowest-HP (secure kills)."""
    if focus_tag is not None:
        for u in group:
            if u.tag == focus_tag:
                return u
    return min(group, key=lambda u: float(u.health + u.shield))


def _enemy_incomplete_structures(ctx: "BotContext"):
    return [
        s
        for s in ctx.bot.enemy_structures
        if 0 < s.build_progress < 1
    ]


def _enemy_building_workers(ctx: "BotContext"):
    """Workers constructing a building (enemy API has no is_constructing_scv)."""
    workers = []
    seen: set[int] = set()
    incomplete = _enemy_incomplete_structures(ctx)

    # Prefer the closest worker on each incomplete structure.
    for structure in incomplete:
        closest = None
        closest_dist = 999.0
        for u in ctx.bot.enemy_units:
            if u.type_id not in SCOUT_WORKER_TYPES or u.is_structure:
                continue
            dist = cy_distance_to(u.position, structure.position)
            if dist < closest_dist:
                closest_dist = dist
                closest = u
        if closest is None or closest.tag in seen:
            continue
        if closest_dist <= SCOUT_BUILDER_RADIUS:
            workers.append(closest)
            seen.add(closest.tag)
            ctx.state.scout_known_builder_tags.add(closest.tag)

    # Fallback when orders are visible (ability names vary in casing).
    for u in ctx.bot.enemy_units:
        if u.type_id not in SCOUT_WORKER_TYPES or u.is_structure or u.tag in seen:
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
                ctx.state.scout_known_builder_tags.add(u.tag)
                break

    # Chase known builders only while they remain near an incomplete building.
    # (Do not chase peeled miners across the main — that blocks switching to
    # the next Barracks/Refinery/Depot SCV.)
    if incomplete:
        for tag in list(ctx.state.scout_known_builder_tags):
            unit = _unit_by_tag(ctx, tag)
            if unit is None:
                ctx.state.scout_known_builder_tags.discard(tag)
                continue
            if tag in seen:
                continue
            near_job = any(
                cy_distance_to(unit.position, s.position) <= SCOUT_BUILDER_HUNT_RADIUS
                for s in incomplete
            )
            if near_job:
                workers.append(unit)
                seen.add(tag)
            else:
                ctx.state.scout_known_builder_tags.discard(tag)
    else:
        ctx.state.scout_known_builder_tags.clear()

    return workers


def _closest_incomplete_structure(ctx: "BotContext", scout):
    incomplete = _enemy_incomplete_structures(ctx)
    if not incomplete:
        return None
    return cy_closest_to(position=scout.position, units=incomplete)


def _enemy_gas_workers(ctx: "BotContext"):
    """Workers mining gas (carrying vespene or parked on a geyser building)."""
    gases = [
        s
        for s in ctx.bot.enemy_structures
        if s.type_id in SCOUT_GAS_STRUCTURES
    ]
    workers = []
    for u in ctx.bot.enemy_units:
        if u.type_id not in SCOUT_WORKER_TYPES or u.is_structure:
            continue
        if getattr(u, "is_carrying_vespene", False):
            workers.append(u)
            continue
        for gas in gases:
            if cy_distance_to(u.position, gas.position) < SCOUT_GAS_WORKER_RADIUS:
                workers.append(u)
                break
    return workers


def _enemy_mineral_workers(ctx: "BotContext", enemy_main: Point2):
    """Workers mining minerals near the enemy main (not gas / not building)."""
    minerals = ctx.bot.mineral_field.closer_than(15, enemy_main)
    if not minerals:
        return []
    builders = {u.tag for u in _enemy_building_workers(ctx)}
    gas = {u.tag for u in _enemy_gas_workers(ctx)}
    workers = []
    for u in ctx.bot.enemy_units:
        if u.type_id not in SCOUT_WORKER_TYPES or u.is_structure:
            continue
        if u.tag in builders or u.tag in gas:
            continue
        if getattr(u, "is_carrying_minerals", False):
            workers.append(u)
            continue
        for patch in minerals:
            if cy_distance_to(u.position, patch.position) < SCOUT_MINERAL_WORKER_RADIUS:
                workers.append(u)
                break
    return workers


def _enemy_low_hp_workers(ctx: "BotContext"):
    """Any visible worker below the low-HP threshold."""
    workers = []
    for u in ctx.bot.enemy_units:
        if u.type_id not in SCOUT_WORKER_TYPES or u.is_structure:
            continue
        max_hp = float(u.health_max + u.shield_max)
        if max_hp <= 0:
            continue
        if (u.health + u.shield) / max_hp < SCOUT_LOW_HP_WORKER:
            workers.append(u)
    return workers


def _geyser_key(pos: Point2) -> tuple[float, float]:
    return (round(float(pos.x), 1), round(float(pos.y), 1))


def _enemy_main_geysers(ctx: "BotContext", enemy_main: Point2):
    return list(ctx.bot.vespene_geyser.closer_than(18, enemy_main))


def _enemy_main_gas_buildings(ctx: "BotContext", enemy_main: Point2):
    return [
        s
        for s in ctx.bot.enemy_structures
        if s.type_id in SCOUT_GAS_STRUCTURES
        and cy_distance_to(s.position, enemy_main) < 18.0
    ]


def _gas_scout_move_target(
    ctx: "BotContext", scout, enemy_main: Point2
) -> Point2 | None:
    """Move target while checking enemy gas. None once the check is done."""
    if ctx.state.scout_gas_scouted:
        return None

    gases = _enemy_main_gas_buildings(ctx, enemy_main)
    if gases:
        ctx.state.scout_gas_scouted = True
        ctx.state.scout_enemy_has_gas = True
        g = gases[0]
        ctx.log(
            f"SCOUT gas check: {g.type_id.name} present "
            f"(progress={g.build_progress:.0%})"
        )
        return None

    geysers = _enemy_main_geysers(ctx, enemy_main)
    if not geysers:
        return enemy_main

    for geyser in geysers:
        if cy_distance_to(scout.position, geyser.position) > SCOUT_GAS_CHECK_RADIUS:
            continue
        key = _geyser_key(geyser.position)
        if key in ctx.state.scout_geysers_seen:
            continue
        ctx.state.scout_geysers_seen.add(key)
        ctx.log(f"SCOUT gas check: geyser clear @ {geyser.position}")

    unchecked = [
        g
        for g in geysers
        if _geyser_key(g.position) not in ctx.state.scout_geysers_seen
    ]
    if not unchecked:
        ctx.state.scout_gas_scouted = True
        ctx.state.scout_enemy_has_gas = False
        ctx.log("SCOUT gas check: no gas buildings on main geysers")
        return None

    return cy_closest_to(position=scout.position, units=unchecked).position


def _scout_harass_target(ctx: "BotContext", scout, enemy_main: Point2):
    """Pick harass prey: builder → gas (if present) → low HP → minerals.

    Sticky focus + lowest-HP within the winning tier to secure kills.
    Returns `(priority_name, unit)` or `(None, None)`.
    """
    tiers: list[tuple[str, list]] = [
        ("builder", _enemy_building_workers(ctx)),
    ]
    if ctx.state.scout_enemy_has_gas:
        tiers.append(("gas", _enemy_gas_workers(ctx)))
    tiers.append(("low_hp", _enemy_low_hp_workers(ctx)))
    tiers.append(("minerals", _enemy_mineral_workers(ctx, enemy_main)))
    focus = ctx.state.scout_focus_tag
    for name, group in tiers:
        if group:
            return name, _pick_focus_worker(scout, group, focus)
    return None, None


def _attack_harass_target(ctx: "BotContext", scout, priority: str, target) -> None:
    changed = ctx.state.scout_focus_tag != target.tag
    ctx.state.scout_focus_tag = target.tag
    # Log only on focus change — hp ticks / interrupt lines were drowning the log.
    if changed:
        _scout_action(ctx, f"attack {priority} {target.type_id.name}")
    orders = scout.orders
    already = False
    if orders:
        ability = getattr(orders[0], "ability", None)
        ability_name = str(
            getattr(ability, "id", None)
            or getattr(ability, "link_name", "")
            or ""
        ).lower()
        # Gather from a mineral-walk peel must not block re-engage.
        if "gather" in ability_name or "harvest" in ability_name:
            already = False
        else:
            tgt = orders[0].target
            already = tgt == target.tag or getattr(tgt, "tag", None) == target.tag
    if already:
        return
    ctx.bot.register_behavior(AttackTarget(unit=scout, target=target))


def _scout_action(ctx: "BotContext", action: str) -> None:
    """Log a scout decision only when it changes (avoids per-frame spam)."""
    if ctx.state.scout_last_action == action:
        return
    ctx.state.scout_last_action = action
    ctx.log(f"SCOUT {action}")


def _clamp_to_enemy_main(point: Point2, enemy_main: Point2) -> Point2:
    """Pull a point back onto the enemy-main kite circle if it drifted out."""
    if cy_distance_to(point, enemy_main) <= SCOUT_KITE_MAIN_RADIUS:
        return point
    return Point2(cy_towards(enemy_main, point, SCOUT_KITE_MAIN_RADIUS))


def _nearby_enemy_workers(ctx: "BotContext", scout, radius: float):
    return [
        u
        for u in ctx.bot.enemy_units
        if u.type_id in SCOUT_WORKER_TYPES
        and not u.is_structure
        and cy_distance_to(scout.position, u.position) <= radius
    ]


def _refresh_scout_pressure(ctx: "BotContext", scout) -> tuple[bool, bool]:
    """Update closing-worker cache once per frame.

    Returns `(under_pressure, surrounded)`. Surround only counts workers that
    are on top of us or closing in — idle mineral-line SCVs do not lock MW.
    """
    near = _nearby_enemy_workers(ctx, scout, max(SCOUT_PRESSURE_RADIUS, 8.0))
    prev = ctx.state.scout_worker_dists
    new_dists: dict[int, float] = {}
    closing = 0
    on_top_n = 0
    focus = ctx.state.scout_focus_tag
    aggressor_close = 0
    for w in near:
        dist = cy_distance_to(scout.position, w.position)
        new_dists[w.tag] = dist
        if w.tag == focus:
            continue
        old = prev.get(w.tag)
        on_top = dist <= 3.2
        approaching = (
            old is not None
            and dist < old - 0.12
            and dist <= SCOUT_PRESSURE_RADIUS
        )
        if on_top:
            on_top_n += 1
        if on_top or approaching:
            closing += 1
            if dist <= SCOUT_SURROUND_RADIUS:
                aggressor_close += 1
    ctx.state.scout_worker_dists = new_dists
    surrounded = aggressor_close >= SCOUT_SURROUND_COUNT
    shields_full = scout.shield_percentage >= 0.99
    if shields_full:
        instant = surrounded or on_top_n >= 1 or closing >= 2
    else:
        instant = surrounded or closing > 0 or on_top_n >= 1

    if instant:
        ctx.state.scout_pressure_clear_frames = 0
        return True, surrounded

    # Sticky peel only while regenerating after taking damage.
    if ctx.state.scout_probe_regen and not shields_full:
        ctx.state.scout_pressure_clear_frames += 1
        if ctx.state.scout_pressure_clear_frames < SCOUT_PRESSURE_CLEAR_FRAMES:
            return True, surrounded
    else:
        ctx.state.scout_pressure_clear_frames = 0
    return False, surrounded


def _scout_surrounded(scout, workers, focus_tag: int | None = None) -> bool:
    """True when enough approaching/close non-focus workers pile on."""
    close = 0
    for w in workers:
        if focus_tag is not None and w.tag == focus_tag:
            continue
        if cy_distance_to(scout.position, w.position) <= SCOUT_SURROUND_RADIUS:
            close += 1
            if close >= SCOUT_SURROUND_COUNT:
                return True
    return False


def _can_kill_shot(ctx: "BotContext", scout) -> bool:
    focus = _unit_by_tag(ctx, ctx.state.scout_focus_tag)
    if focus is None:
        return False
    return (
        (focus.health + focus.shield) <= SCOUT_KILL_SHOT_HP
        and cy_distance_to(scout.position, focus.position) <= SCOUT_KILL_SHOT_RANGE
    )


def _mineral_walk_escape(
    ctx: "BotContext", scout, enemy_nat: Point2, threats
) -> bool:
    """Gather natural minerals to path out of the main surround."""
    minerals = list(ctx.bot.mineral_field.closer_than(15, enemy_nat))
    if not minerals:
        minerals = list(ctx.bot.mineral_field.closer_than(22, enemy_nat))
    if not minerals:
        return False
    if threats:
        threat_pos = Point2(cy_center(threats))
    else:
        threat_pos = scout.position
    # Pull toward the natural and away from the pile-on.
    patch = max(
        minerals,
        key=lambda m: (
            cy_distance_to(m.position, threat_pos),
            -cy_distance_to(m.position, enemy_nat),
        ),
    )
    scout.gather(patch)
    return True


def _kite_scout(
    ctx: "BotContext",
    scout,
    enemy_main: Point2,
    enemy_nat: Point2,
    *,
    surrounded: bool,
) -> None:
    """Peel from threats inside the enemy main; MW bursts toward nat minerals."""
    if cy_distance_to(scout.position, enemy_main) > SCOUT_KITE_MAIN_RADIUS + 2.0:
        scout.move(enemy_main)
        return

    near = _nearby_enemy_workers(ctx, scout, 8.0)
    now = ctx.bot.time
    if surrounded:
        if now <= ctx.state.scout_mw_until:
            if _mineral_walk_escape(ctx, scout, enemy_nat, near):
                return
        elif now >= ctx.state.scout_mw_until + 0.4:
            if _mineral_walk_escape(ctx, scout, enemy_nat, near):
                ctx.state.scout_mw_until = now + SCOUT_MW_BURST
                _scout_action(ctx, "mineral-walk escape (nat minerals)")
                return

    threats = list(near)
    if not threats:
        ground = ctx.mediator.get_units_in_range(
            start_points=[scout.position],
            distances=8.0,
            query_tree=UnitTreeQueryType.EnemyGround,
        )[0]
        threats = [u for u in ground if not u.is_structure]

    if threats:
        enemy = cy_closest_to(position=scout.position, units=threats)
        away = Point2(cy_towards(enemy.position, scout.position, 8.0))
    else:
        away = Point2(cy_towards(scout.position, enemy_main, 6.0))

    scout.move(_clamp_to_enemy_main(away, enemy_main))


def _update_scout_combat_stats(ctx: "BotContext", scout) -> None:
    """Accumulate Probe damage taken / dealt vs nearby workers."""
    hp = float(scout.health + scout.shield)
    prev = ctx.state.scout_last_hp
    if prev is not None and hp < prev:
        taken = prev - hp
        ctx.state.scout_damage_taken += taken
        if taken >= 5:
            ctx.log(
                f"SCOUT took {taken:.0f} dmg "
                f"(total taken {ctx.state.scout_damage_taken:.0f})"
            )

    near_workers = [
        u
        for u in ctx.bot.enemy_units
        if u.type_id in SCOUT_WORKER_TYPES
        and not u.is_structure
        and cy_distance_to(scout.position, u.position) <= SCOUT_DEAL_RADIUS
    ]
    seen: set[int] = set()
    for u in near_workers:
        seen.add(u.tag)
        cur = float(u.health + u.shield)
        old = ctx.state.scout_prey_hp.get(u.tag)
        if old is not None and cur < old:
            dealt = old - cur
            ctx.state.scout_damage_dealt += dealt
            if dealt >= 5:
                ctx.log(
                    f"SCOUT dealt {dealt:.0f} to {u.type_id.name} "
                    f"(total dealt {ctx.state.scout_damage_dealt:.0f})"
                )
        ctx.state.scout_prey_hp[u.tag] = cur
    for tag in list(ctx.state.scout_prey_hp):
        if tag not in seen:
            del ctx.state.scout_prey_hp[tag]


def _log_scout_combat_stats(ctx: "BotContext", reason: str) -> None:
    ctx.log(
        f"SCOUT combat ({reason}): "
        f"dealt {ctx.state.scout_damage_dealt:.0f}, "
        f"took {ctx.state.scout_damage_taken:.0f}"
    )


def _clear_scout_mission(ctx: "BotContext") -> None:
    ctx.state.scout_probe_done = True
    ctx.state.scout_tags.clear()
    ctx.state.scout_probe_miss_frames = 0
    ctx.state.scout_probe_regen = False
    ctx.state.scout_last_hp = None
    ctx.state.scout_prey_hp.clear()
    ctx.state.scout_probe_returning = False
    ctx.state.scout_last_action = None
    ctx.state.scout_gas_scouted = False
    ctx.state.scout_enemy_has_gas = False
    ctx.state.scout_geysers_seen.clear()
    ctx.state.scout_focus_tag = None
    ctx.state.scout_known_builder_tags.clear()
    ctx.state.scout_worker_dists.clear()
    ctx.state.scout_pressure_clear_frames = 0
    ctx.state.scout_mw_until = 0.0


def _begin_scout_return(ctx: "BotContext", scouts, reason: str) -> None:
    """Start pathing home. Keep SCOUTING until near base — GATHERING early lets
    the mining manager reassign the Probe to enemy minerals."""
    if not ctx.state.scout_probe_returning:
        for scout in scouts:
            dist = cy_distance_to(scout.position, ctx.bot.start_location)
            ctx.log(
                f"SCOUT probe returning home ({reason}) "
                f"from {dist:.0f} away"
            )
        _log_scout_combat_stats(ctx, reason)
        ctx.state.scout_probe_returning = True
        ctx.state.scout_last_action = None
    for scout in scouts:
        scout.move(ctx.bot.start_location)


def _finish_scout_return(ctx: "BotContext", scout) -> None:
    home_minerals = ctx.bot.mineral_field.closer_than(12, ctx.bot.start_location)
    ctx.mediator.assign_role(tag=scout.tag, role=UnitRole.GATHERING)
    if home_minerals:
        patch = cy_closest_to(
            position=ctx.bot.start_location, units=home_minerals
        )
        scout.gather(patch)
        ctx.log("SCOUT arrived home; gathering")
    else:
        ctx.bot.register_behavior(
            AMove(unit=scout, target=ctx.bot.start_location)
        )
        ctx.log("SCOUT arrived home; no minerals found")
    _clear_scout_mission(ctx)


def _remember_pylon_builder(ctx: "BotContext") -> None:
    """Latch the Probe building the opening Pylon."""
    if ctx.state.scout_pylon_builder_tag is not None:
        return
    if ctx.bot.time > 90.0:
        return
    tracker = ctx.mediator.get_building_tracker_dict
    for tag, info in tracker.items():
        if info.get(ID) == UnitTypeId.PYLON:
            ctx.state.scout_pylon_builder_tag = tag
            ctx.log(f"SCOUT pylon builder latched (tag={tag})")
            return


def _claim_scout_probe(ctx: "BotContext") -> None:
    """Own exactly one harass Probe. Prefer the opening-Pylon builder."""
    _remember_pylon_builder(ctx)
    if ctx.state.scout_probe_done:
        return
    if ctx.bot.time >= SCOUT_PROBE_HOME_TIME:
        return

    enemy = ctx.bot.enemy_start_locations[0]
    runner_scouts = [
        u
        for u in ctx.mediator.get_units_from_role(
            role=UnitRole.BUILD_RUNNER_SCOUT, unit_type=UnitTypeId.PROBE
        )
    ]
    if runner_scouts:
        keep = cy_closest_to(position=enemy, units=runner_scouts)
        for u in runner_scouts:
            if u.tag == keep.tag:
                continue
            ctx.mediator.assign_role(tag=u.tag, role=UnitRole.GATHERING)
        ctx.state.scout_tags = {keep.tag}
        keep.stop()
        ctx.mediator.assign_role(tag=keep.tag, role=UnitRole.SCOUTING)
        ctx.state.scout_probe_done = True
        ctx.log(f"SCOUT probe assigned (build-runner scout, tag={keep.tag})")
        return

    if ctx.state.scout_tags:
        return

    tracker = ctx.mediator.get_building_tracker_dict
    pylon_tag = ctx.state.scout_pylon_builder_tag
    if pylon_tag is not None and pylon_tag in tracker:
        building = tracker[pylon_tag].get(ID)
        name = getattr(building, "name", str(building))
        ctx.log_once(
            f"scout_wait_builder_{name}",
            f"SCOUT waiting for pylon builder (still on {name})",
        )
        return

    if pylon_tag is not None and pylon_tag not in tracker:
        probe = None
        for u in ctx.bot.workers:
            if u.tag == pylon_tag:
                probe = u
                break
        if probe is None:
            for u in ctx.bot.units(UnitTypeId.PROBE):
                if u.tag == pylon_tag:
                    probe = u
                    break
        if probe is not None and (
            ctx.bot.structures(UnitTypeId.PYLON)
            or ctx.bot.structure_pending(UnitTypeId.PYLON)
        ):
            probe.stop()
            ctx.state.scout_tags = {probe.tag}
            ctx.mediator.assign_role(tag=probe.tag, role=UnitRole.SCOUTING)
            ctx.state.scout_probe_done = True
            ctx.log(f"SCOUT probe assigned (pylon builder, tag={probe.tag})")
            return
        # Builder died or never spawned — fall through to generic claim.
        if probe is None and ctx.bot.time > 45.0:
            ctx.log(
                f"SCOUT pylon builder tag={pylon_tag} gone; falling back"
            )
            ctx.state.scout_pylon_builder_tag = None

    gathering = {
        u.tag for u in ctx.mediator.get_units_from_role(role=UnitRole.GATHERING)
    }
    probes = [
        u
        for u in ctx.bot.workers
        if u.type_id == UnitTypeId.PROBE and u.tag in gathering
    ]
    if not probes:
        probes = list(ctx.bot.units(UnitTypeId.PROBE))
    if not probes:
        return
    # Wait for the latched pylon builder to free up from the building tracker.
    if ctx.state.scout_pylon_builder_tag is not None:
        return
    if len(ctx.bot.workers) < 14:
        ctx.log_once(
            "scout_wait_workers",
            f"SCOUT waiting for 14 workers (have {len(ctx.bot.workers)})",
        )
        return
    scout = cy_closest_to(position=enemy, units=probes)
    scout.stop()
    ctx.state.scout_tags = {scout.tag}
    ctx.mediator.assign_role(tag=scout.tag, role=UnitRole.SCOUTING)
    ctx.state.scout_probe_done = True
    ctx.log(f"SCOUT probe assigned (fallback, tag={scout.tag})")


def scout_probe_harass():
    """Opening Probe: check enemy gas, then harass by priority, home@2:00.

    Prefer the first-Pylon builder. Before harassing, walk main geysers to see
    if gas exists (gas workers only targeted when it does). Then:
    constructing → gas → minerals → low-HP. Kite on damage; early threat / 2:00
    home.
    """

    def routine(ctx: "BotContext") -> None:
        _claim_scout_probe(ctx)
        if not ctx.state.scout_tags:
            return

        scouts = [
            u
            for u in ctx.mediator.get_units_from_role(
                role=UnitRole.SCOUTING, unit_type=UnitTypeId.PROBE
            )
            if u.tag in ctx.state.scout_tags
        ]
        if not scouts:
            scouts = [
                u
                for u in ctx.bot.units(UnitTypeId.PROBE)
                if u.tag in ctx.state.scout_tags
            ]
        if not scouts:
            ctx.state.scout_probe_miss_frames += 1
            if ctx.state.scout_probe_miss_frames >= 16:
                _log_scout_combat_stats(ctx, "probe died")
                _clear_scout_mission(ctx)
            return

        ctx.state.scout_probe_miss_frames = 0

        if len(scouts) > 1:
            enemy = ctx.bot.enemy_start_locations[0]
            keep = cy_closest_to(position=enemy, units=scouts)
            for u in scouts:
                if u.tag == keep.tag:
                    continue
                ctx.mediator.assign_role(tag=u.tag, role=UnitRole.GATHERING)
                ctx.state.scout_tags.discard(u.tag)
            scouts = [keep]
            ctx.log("SCOUT released extra probe (one-scout cap)")

        leave_reason: str | None = None
        if ctx.state.scout_probe_returning:
            leave_reason = "returning"
        elif ctx.bot.time >= SCOUT_PROBE_HOME_TIME:
            leave_reason = "@1:48"
        elif any(
            u.type_id in SCOUT_EARLY_HOME_THREATS for u in ctx.bot.enemy_units
        ):
            leave_reason = "early threat spotted"

        if leave_reason is not None:
            if not ctx.state.scout_probe_returning:
                _begin_scout_return(ctx, scouts, leave_reason)
            for scout in scouts:
                dist = cy_distance_to(scout.position, ctx.bot.start_location)
                if dist <= SCOUT_HOME_ARRIVE:
                    _finish_scout_return(ctx, scout)
                else:
                    bucket = int(dist // 10) * 10
                    _scout_action(ctx, f"pathing home (~{bucket} out)")
                    # Mineral-walk home minerals when still in enemy minerals.
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
            _update_scout_combat_stats(ctx, scout)
            hp = float(scout.health + scout.shield)
            prev = ctx.state.scout_last_hp
            if prev is not None and hp < prev - 0.5:
                if not ctx.state.scout_probe_regen:
                    ctx.log(
                        f"SCOUT damaged ({prev:.0f}->{hp:.0f}); kiting"
                    )
                ctx.state.scout_probe_regen = True
            ctx.state.scout_last_hp = hp

            under_pressure, surrounded = _refresh_scout_pressure(ctx, scout)

            # Proactive kite: workers closing in before we take the hit.
            # At full shields only peel on a true surround — otherwise we flap
            # kite/resume every frame and abandon builder harassment.
            if not ctx.state.scout_probe_regen and under_pressure:
                if not _can_kill_shot(ctx, scout):
                    shields_full = scout.shield_percentage >= 0.99
                    if not shields_full or surrounded:
                        ctx.state.scout_probe_regen = True
                        _scout_action(ctx, "kite early (incoming workers)")

            if scout.shield <= 0:
                if not ctx.state.scout_probe_regen:
                    ctx.log("SCOUT shields gone; kiting")
                ctx.state.scout_probe_regen = True

            if ctx.state.scout_probe_regen:
                # Secure a kill overrides kite.
                if _can_kill_shot(ctx, scout):
                    focus = _unit_by_tag(ctx, ctx.state.scout_focus_tag)
                    if focus is not None:
                        _scout_action(ctx, f"kill shot on {focus.type_id.name}")
                        ctx.bot.register_behavior(
                            AttackTarget(unit=scout, target=focus)
                        )
                        continue

                # Stay peeled while targeted — full shields must not cancel kite.
                if under_pressure:
                    bucket = int(scout.shield_percentage * 10) * 10
                    _scout_action(
                        ctx, f"kite in main (shields ~{bucket}%)"
                    )
                    _kite_scout(
                        ctx,
                        scout,
                        enemy_main,
                        enemy_nat,
                        surrounded=surrounded,
                    )
                    continue

                shields_ok = scout.shield_percentage >= SCOUT_SHIELD_RESUME_AT
                clear_to_harass = (
                    scout.shield_percentage >= SCOUT_RESUME_NO_PRESSURE
                )
                if shields_ok or clear_to_harass:
                    ctx.state.scout_probe_regen = False
                    ctx.state.scout_mw_until = 0.0
                    # Drop sticky focus — the peel often left us on a miner that
                    # is no longer building, and gather orders leave us idling
                    # on enemy nat minerals.
                    ctx.state.scout_focus_tag = None
                    why = "shields" if shields_ok else "pressure cleared"
                    ctx.log(
                        f"SCOUT resume harass ({why}, "
                        f"shields={scout.shield_percentage:.0%})"
                    )
                    if (
                        cy_distance_to(scout.position, enemy_main)
                        > SCOUT_KITE_MAIN_RADIUS
                    ):
                        _scout_action(ctx, "re-enter main after peel")
                        scout.move(enemy_main)
                        continue
                else:
                    bucket = int(scout.shield_percentage * 10) * 10
                    _scout_action(
                        ctx, f"kite in main (shields ~{bucket}%)"
                    )
                    _kite_scout(
                        ctx,
                        scout,
                        enemy_main,
                        enemy_nat,
                        surrounded=surrounded,
                    )
                    continue

            # Approach: get into the enemy main before gas / harass.
            if (
                not ctx.state.scout_gas_scouted
                and cy_distance_to(scout.position, enemy_main)
                > SCOUT_APPROACH_RADIUS
            ):
                builders = _enemy_building_workers(ctx)
                if builders:
                    target = _pick_focus_worker(
                        scout, builders, ctx.state.scout_focus_tag
                    )
                    _attack_harass_target(ctx, scout, "builder", target)
                    continue
                # After a mineral-walk peel we sit on enemy nat minerals —
                # always push back to main (not re-path nat) so we re-harass.
                if ctx.state.scout_geysers_seen or ctx.state.scout_known_builder_tags:
                    _scout_action(ctx, "re-enter main after peel")
                    scout.move(enemy_main)
                    continue
                waypoint = (
                    enemy_nat
                    if cy_distance_to(scout.position, enemy_nat) > 10.0
                    else enemy_main
                )
                where = "nat" if waypoint is enemy_nat else "main"
                _scout_action(ctx, f"path to enemy {where}")
                scout.move(waypoint)
                continue

            # Builders always beat the gas walk — don't idle past a Barracks SCV.
            builders = _enemy_building_workers(ctx)
            if builders:
                # Drop sticky focus if it is no longer an active builder so we
                # switch to the next structure SCV after a peel / kill.
                focus = ctx.state.scout_focus_tag
                if focus is not None and all(b.tag != focus for b in builders):
                    ctx.state.scout_focus_tag = None
                target = _pick_focus_worker(
                    scout, builders, ctx.state.scout_focus_tag
                )
                _attack_harass_target(ctx, scout, "builder", target)
                continue

            # Incomplete building in vision but no hard-matched builder — soft
            # match a nearby worker at the job site, else walk onto it.
            unfinished = _closest_incomplete_structure(ctx, scout)
            if unfinished is not None:
                near_job = [
                    u
                    for u in ctx.bot.enemy_units
                    if u.type_id in SCOUT_WORKER_TYPES
                    and not u.is_structure
                    and cy_distance_to(u.position, unfinished.position)
                    <= SCOUT_BUILDER_HUNT_RADIUS
                ]
                if near_job:
                    focus = ctx.state.scout_focus_tag
                    if focus is not None and all(b.tag != focus for b in near_job):
                        ctx.state.scout_focus_tag = None
                    target = _pick_focus_worker(
                        scout, near_job, ctx.state.scout_focus_tag
                    )
                    ctx.state.scout_known_builder_tags.add(target.tag)
                    _attack_harass_target(ctx, scout, "builder", target)
                    continue
                _scout_action(
                    ctx, f"hunt builder at {unfinished.type_id.name}"
                )
                ctx.state.scout_focus_tag = None
                scout.move(unfinished.position)
                continue

            # Gas check only while we are still in/near the enemy main.
            # After a peel to the nat, walking the second geyser looks "stuck".
            if (
                not ctx.state.scout_gas_scouted
                and cy_distance_to(scout.position, enemy_main)
                <= SCOUT_APPROACH_RADIUS
            ):
                gas_pos = _gas_scout_move_target(ctx, scout, enemy_main)
                if gas_pos is not None:
                    _scout_action(ctx, f"scout enemy gas @ {gas_pos}")
                    scout.move(gas_pos)
                    continue

            priority, target = _scout_harass_target(ctx, scout, enemy_main)
            if target is not None:
                _attack_harass_target(ctx, scout, priority, target)
                continue

            # Clear stale focus when prey is gone.
            if ctx.state.scout_focus_tag is not None:
                if _unit_by_tag(ctx, ctx.state.scout_focus_tag) is None:
                    ctx.log("SCOUT focus target dead/gone")
                    ctx.state.scout_focus_tag = None

            _scout_action(ctx, "hold at enemy main (no prey)")
            scout.move(enemy_main)

    return routine


def _adept_confirm_shade(adept) -> UseAbility | None:
    """Teleport into an already-placed shade — cast alone does not move the Adept."""
    if AbilityId.CANCEL_ADEPTPHASESHIFT in adept.abilities:
        return UseAbility(AbilityId.CANCEL_ADEPTPHASESHIFT, adept)
    if AbilityId.CANCEL_ADEPTSHADEPHASESHIFT in adept.abilities:
        return UseAbility(AbilityId.CANCEL_ADEPTSHADEPHASESHIFT, adept)
    return None


def harassing_adept():
    """Shade chase low-HP / shade out when shields gone; @3:00 natural.

    Shade placement (`ADEPTPHASESHIFT`) only drops the projection. Confirm with
    `CANCEL_ADEPTPHASESHIFT` (or shade cancel) once that ability is available,
    otherwise the Adept never teleports.
    """

    def routine(ctx: "BotContext") -> None:
        adepts = ctx.mediator.get_units_from_role(
            role=UnitRole.HARASSING_ADEPT, unit_type=UnitTypeId.ADEPT
        )
        if not adepts:
            return

        natural = ctx.mediator.get_enemy_nat
        go_natural = ctx.bot.time >= ADEPT_NATURAL_HARASS_TIME
        grid = ctx.mediator.get_ground_grid

        for adept in adepts:
            maneuver = CombatManeuver()
            # Confirm an outstanding shade before issuing new micro.
            confirm = _adept_confirm_shade(adept)
            if confirm is not None:
                maneuver.add(confirm)
                maneuver.add(KeepUnitSafe(unit=adept, grid=grid))
                ctx.bot.register_behavior(maneuver)
                continue

            enemies_near = ctx.mediator.get_units_in_range(
                start_points=[adept.position],
                distances=12.0,
                query_tree=UnitTreeQueryType.EnemyGround,
            )[0]
            combat_enemies = [e for e in enemies_near if not e.is_structure]

            # Out of shields: shade toward safety (own natural / away).
            if adept.shield_percentage <= 0.05:
                retreat = Point2(
                    cy_towards(adept.position, ctx.mediator.get_own_nat, 8.0)
                )
                if AbilityId.ADEPTPHASESHIFT_ADEPTPHASESHIFT in adept.abilities:
                    maneuver.add(
                        UseAbility(
                            AbilityId.ADEPTPHASESHIFT_ADEPTPHASESHIFT,
                            adept,
                            retreat,
                        )
                    )
                maneuver.add(KeepUnitSafe(unit=adept, grid=grid))
                maneuver.add(AMove(unit=adept, target=retreat))
                ctx.bot.register_behavior(maneuver)
                continue

            # Chase low-HP fleeing unit with shade.
            low = [
                e
                for e in combat_enemies
                if e.health_percentage < 0.35 and not e.is_structure
            ]
            if low and AbilityId.ADEPTPHASESHIFT_ADEPTPHASESHIFT in adept.abilities:
                prey = cy_closest_to(position=adept.position, units=low)
                maneuver.add(
                    UseAbility(
                        AbilityId.ADEPTPHASESHIFT_ADEPTPHASESHIFT,
                        adept,
                        prey.position,
                    )
                )
                maneuver.add(KeepUnitSafe(unit=adept, grid=grid))
                maneuver.add(AttackTarget(unit=adept, target=prey))
                ctx.bot.register_behavior(maneuver)
                continue

            maneuver.add(KeepUnitSafe(unit=adept, grid=grid))

            if go_natural:
                workers = [
                    u
                    for u in ctx.bot.enemy_units
                    if u.type_id in SCOUT_WORKER_TYPES
                    and cy_distance_to(u.position, natural) < 16.0
                ]
                if workers:
                    target = cy_closest_to(position=adept.position, units=workers)
                    if combat_enemies:
                        maneuver.add(
                            ShootTargetInRange(unit=adept, targets=combat_enemies)
                        )
                    maneuver.add(AttackTarget(unit=adept, target=target))
                else:
                    maneuver.add(AMove(unit=adept, target=natural))
                ctx.bot.register_behavior(maneuver)
                continue

            # Pre-3:00: poke near enemy main / natural approach.
            hold = natural
            if combat_enemies:
                maneuver.add(
                    ShootTargetInRange(unit=adept, targets=combat_enemies)
                )
                maneuver.add(
                    AttackTarget(
                        unit=adept,
                        target=cy_closest_to(
                            position=adept.position, units=combat_enemies
                        ),
                    )
                )
            maneuver.add(AMove(unit=adept, target=hold))
            ctx.bot.register_behavior(maneuver)

    return routine
