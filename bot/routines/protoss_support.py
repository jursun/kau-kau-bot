"""Protoss support micro: Warp Prism, Observer, Adept.

Chargelot (and other P builds) keep these units out of DEFENDING/ATTACKING
via `core.roles.SUPPORT_ROLES`. These routines are what actually drive them.

Opening worker harassment lives in `bot.routines.worker_harass`.
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
from bot.routines.worker_harass import ENEMY_WORKERS

if TYPE_CHECKING:
    from bot.core.context import BotContext

# Timings (game seconds) — Jason Chargelot brief 2026-09-12.
ADEPT_NATURAL_HARASS_TIME: float = 3 * 60
ARMY_LEAVE_TIME: float = 5 * 60 + 20  # used by build wave_gate; documented here

PRISM_STANDOFF: float = 6.0
"""How far behind the army center the Prism sits while phasing."""
OBS_FOLLOW_RADIUS: float = 3.0
"""How tightly the Observer hugs the army destination / center."""


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
                    if u.type_id in ENEMY_WORKERS
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
