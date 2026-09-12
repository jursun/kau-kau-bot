"""Protoss support micro: Warp Prism, Observer, Adept, scout Probe.

Chargelot (and other P builds) keep these units out of DEFENDING/ATTACKING
via `core.roles.SUPPORT_ROLES`. These routines are what actually drive them.
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
from ares.consts import UnitRole, UnitTreeQueryType
from cython_extensions import cy_center, cy_closest_to, cy_distance_to, cy_towards
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.routines import targeting

if TYPE_CHECKING:
    from bot.core.context import BotContext

# Timings (game seconds) — Jason Chargelot brief 2026-09-12.
SCOUT_PROBE_HOME_TIME: float = 2 * 60
ADEPT_NATURAL_HARASS_TIME: float = 3 * 60
ARMY_LEAVE_TIME: float = 5 * 60 + 20  # used by build wave_gate; documented here

PRISM_STANDOFF: float = 6.0
"""How far behind the army center the Prism sits while phasing."""
OBS_FOLLOW_RADIUS: float = 3.0
"""How tightly the Observer hugs the army destination / center."""
SCOUT_WORKER_TYPES = frozenset({UnitTypeId.SCV, UnitTypeId.PROBE, UnitTypeId.DRONE})
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
    """Phase behind the army; reposition in transport mode while WG on CD."""

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

        for prism in prisms:
            if army is None:
                # No ball yet — sit near natural / production.
                hold = targeting.rally_point(ctx)
                if prism.type_id == UnitTypeId.WARPPRISMPHASING:
                    ctx.bot.register_behavior(
                        UseAbility(
                            AbilityId.MORPH_WARPPRISMTRANSPORTMODE, prism
                        )
                    )
                ctx.bot.register_behavior(AMove(unit=prism, target=hold))
                continue

            # Behind the army: from army toward our natural.
            behind = Point2(cy_towards(army, home, PRISM_STANDOFF))

            if can_warp:
                # Phase at the warp pocket behind the ball.
                if prism.type_id != UnitTypeId.WARPPRISMPHASING:
                    if AbilityId.MORPH_WARPPRISMPHASINGMODE in prism.abilities:
                        ctx.bot.register_behavior(
                            UseAbility(
                                AbilityId.MORPH_WARPPRISMPHASINGMODE, prism
                            )
                        )
                        continue
                dist = cy_distance_to(prism.position, behind)
                if dist > 2.5:
                    # Need to move — drop to transport if phased, then move.
                    if prism.type_id == UnitTypeId.WARPPRISMPHASING:
                        ctx.bot.register_behavior(
                            UseAbility(
                                AbilityId.MORPH_WARPPRISMTRANSPORTMODE, prism
                            )
                        )
                    else:
                        ctx.bot.register_behavior(
                            AMove(unit=prism, target=behind)
                        )
                # else already phased on spot — stay
            else:
                # Warpgates cooling: reposition in transport mode.
                if prism.type_id == UnitTypeId.WARPPRISMPHASING:
                    ctx.bot.register_behavior(
                        UseAbility(
                            AbilityId.MORPH_WARPPRISMTRANSPORTMODE, prism
                        )
                    )
                else:
                    ctx.bot.register_behavior(AMove(unit=prism, target=behind))

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


def _enemy_building_workers(ctx: "BotContext"):
    workers = []
    for u in ctx.bot.enemy_units:
        if u.type_id not in SCOUT_WORKER_TYPES:
            continue
        # Building: constructing flag or order targeting a build ability.
        if getattr(u, "is_constructing_scv", False):
            workers.append(u)
            continue
        orders = getattr(u, "orders", []) or []
        for order in orders:
            ability = getattr(order, "ability", None)
            name = getattr(ability, "id", None) or getattr(ability, "link_name", "")
            name_s = str(name)
            if "Build" in name_s or "Morph" in name_s:
                workers.append(u)
                break
    return workers


def _claim_scout_probe(ctx: "BotContext") -> None:
    if ctx.state.scout_tags:
        return
    if ctx.bot.time >= SCOUT_PROBE_HOME_TIME:
        return
    # Prefer a probe already near the enemy half; else any gathering probe.
    enemy = ctx.bot.enemy_start_locations[0]
    gathering = {
        u.tag
        for u in ctx.mediator.get_units_from_role(role=UnitRole.GATHERING)
    }
    probes = [
        u
        for u in ctx.bot.workers
        if u.type_id == UnitTypeId.PROBE and u.tag in gathering
    ]
    if not probes:
        probes = [
            u for u in ctx.bot.units(UnitTypeId.PROBE) if u.tag not in ctx.state.scout_tags
        ]
    if not probes:
        return
    # Don't yank the only builders early — need at least a few home.
    if len(ctx.bot.workers) < 13:
        # Opening often scouts around 13/14 — allow from 12+
        if len(ctx.bot.workers) < 12:
            return
    scout = cy_closest_to(position=enemy, units=probes)
    ctx.state.scout_tags.add(scout.tag)
    ctx.mediator.assign_role(tag=scout.tag, role=UnitRole.SCOUTING)
    ctx.log("SCOUT probe assigned")


def scout_probe_harass():
    """Opening Probe: harass builders / disrupt mining; home at 2:00."""

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
        # Also catch role drift
        if not scouts:
            scouts = [
                u
                for u in ctx.bot.units(UnitTypeId.PROBE)
                if u.tag in ctx.state.scout_tags
            ]
        if not scouts:
            ctx.state.scout_tags.clear()
            return

        if ctx.bot.time >= SCOUT_PROBE_HOME_TIME:
            home_minerals = ctx.bot.mineral_field.closer_than(
                12, ctx.bot.start_location
            )
            for scout in scouts:
                ctx.mediator.assign_role(tag=scout.tag, role=UnitRole.GATHERING)
                # One durable order: path home and mine (no AMove+gather fight).
                if home_minerals:
                    patch = cy_closest_to(
                        position=scout.position, units=home_minerals
                    )
                    scout.gather(patch)
                else:
                    ctx.bot.register_behavior(
                        AMove(unit=scout, target=ctx.bot.start_location)
                    )
                ctx.log("SCOUT probe returning home @2:00")
            ctx.state.scout_tags.clear()
            return

        enemy_main = ctx.bot.enemy_start_locations[0]
        builders = _enemy_building_workers(ctx)
        minerals = ctx.bot.mineral_field.closer_than(15, enemy_main)

        for scout in scouts:
            if builders:
                target = cy_closest_to(position=scout.position, units=builders)
                ctx.bot.register_behavior(
                    AttackTarget(unit=scout, target=target)
                )
                continue
            # Disrupt mining: exactly one command per frame (no AMove+gather).
            if minerals:
                patch = cy_closest_to(position=scout.position, units=minerals)
                if cy_distance_to(scout.position, patch.position) < 1.5:
                    scout.gather(patch)
                else:
                    ctx.bot.register_behavior(
                        AMove(unit=scout, target=patch.position)
                    )
            else:
                ctx.bot.register_behavior(AMove(unit=scout, target=enemy_main))

    return routine


def harassing_adept():
    """Shade chase low-HP flee / shade out when shields gone; @3:00 natural."""

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
                maneuver.add(AttackTarget(unit=adept, target=prey))
                ctx.bot.register_behavior(maneuver)
                continue

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
