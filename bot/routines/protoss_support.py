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
    DropCargo,
    KeepUnitSafe,
    MoveToSafeTarget,
    PickUpCargo,
    UseAbility,
)
from ares.consts import SHADE_OWNER, UnitRole, UnitTreeQueryType
from cython_extensions import (
    cy_center,
    cy_closest_to,
    cy_distance_to,
    cy_in_attack_range,
    cy_pick_enemy_target,
    cy_towards,
)
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.routines import targeting
from bot.routines.worker_harass import ENEMY_WORKERS

if TYPE_CHECKING:
    from bot.core.context import BotContext

# Timings (game seconds) — Jason Chargelot brief 2026-09-12.
ADEPT_NATURAL_HARASS_TIME: float = 3 * 60
ARMY_LEAVE_TIME: float = 5 * 60 + 15
"""When Prism leaves home rally for chargelot staging (matches build wave_gate)."""

PRISM_STANDOFF: float = 6.0
"""How far behind the army center the Prism sits while phasing."""
PRISM_WARP_FIELD_RADIUS: float = 10.0
"""Keep field up while incomplete warp-ins are within this of the Prism."""
PRISM_PHASE_APPROACH: float = 10.0
"""Phase for warps once within this of the army pocket."""
PRISM_REPOSITION_DIST: float = 16.0
"""Unphase to catch up only beyond this (hysteresis vs PHASE_APPROACH)."""
PRISM_ENEMY_PHASE_RANGE: float = 28.0
"""Prism must be within this of the enemy natural before phasing. Kept at
CHARGELOT_STAGING_OFFSET + PRISM_PHASE_APPROACH so a Prism approaching
staging from directly behind (the home side) is never rejected as
"not near_enemy" while still counting as "near_staging"."""
OBS_FOLLOW_RADIUS: float = 3.0
"""How tightly the Observer hugs the army destination / center."""
CHARGELOT_STAGING_OFFSET: float = 18.0
"""Pull-back from enemy natural toward our start for the pre-attack muster -
far enough that the ball forms up out of range of nat defenses before
committing. See PRISM_ENEMY_PHASE_RANGE for why this and
PRISM_PHASE_APPROACH must stay in sync."""
CHARGELOT_MUSTER_RADIUS: float = 7.0
"""Wider muster for the ~12 Zealot first wave so it commits as one squad.
Owned here (not `routines.combat`, which imports it) so the drop-harass
squad-pick below can reuse the exact same "at the muster" radius without
`protoss_support` importing from `combat` (which itself imports from here)."""

# One-shot Prism drop-harass: the instant the first wave commits ("attack
# timing" - `chargelot_attack`'s `chargelot_muster_committed_at`), peel a
# small squad off the muster, sneak it into the enemy main while their army
# is held up at the natural/ramp, and use the Prism as a second warp-in
# point on the high ground there. See `escort_warp_prism`/`_drop_maybe_start`.
PRISM_DROP_SQUAD_SIZE: int = 4
PRISM_DROP_THREAT_RANGE: float = 8.0
"""Radius used to count "sources of damage" near the drop Prism. There is
no direct "who is currently hitting me" API, so this approximates it as
distinct nearby anti-air-capable enemies."""
PRISM_DROP_ABORT_THREATS: int = 1
"""Abort/retreat once more than this many distinct threats are near the
Prism during the drop attempt."""
PRISM_DROP_ARRIVE_DIST: float = 3.0
PRISM_DROP_REPHASE_TIMEOUT_S: float = 15.0
"""Give up trying to re-phase in the enemy base after this long under
threat, and resume normal escort duty instead of stalling forever."""
PRISM_DROP_BASE_HOLD_S: float = 8.0
"""How long to stay phased in the enemy base warping in reinforcements
before handing control back to normal escort duty - long enough for one
production wave to land there."""

# Adept shade — lifetime ~7s. Always wait until 6.5s before CANCEL
# (0.5s remaining). While pathing, cast shade ahead toward the destination.
ADEPT_SHADE_RANGE: float = 9.0
ADEPT_SHADE_MIN_GAP: float = 5.0
ADEPT_SHADE_LIFETIME: float = 7.1
ADEPT_SHADE_CONFIRM_AT: float = 6.5
ADEPT_SHADE_DANGER_RADIUS: float = 8.0
ADEPT_FLEE_SHIELD: float = 0.35
ADEPT_THREAT_RANGE: float = 10.0
ADEPT_KITE_STEP: float = 2.5
"""How far to step back while weapon is on cooldown."""

# Structures that make a shade landing suicidal (workers ignored).
_SHADE_DANGER_STRUCTURES: set[UnitTypeId] = {
    UnitTypeId.BUNKER,
    UnitTypeId.PLANETARYFORTRESS,
    UnitTypeId.SPINECRAWLER,
    UnitTypeId.PHOTONCANNON,
}


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


WARP_WAVE_MIN: int = 4
"""Hold warp-ins until at least this many Warp Gates are idle at once (or
all of them, if fewer exist) - see `warp_wave_ready`. Batching into waves
instead of warping the instant each Gate comes off cooldown lets the Prism
actually reposition between drops rather than trickling reinforcements in
one at a time while pinned in place by a warp field that never empties."""

WARP_WAVE_PREPHASE_MARGIN: int = 1
"""`warp_wave_imminent` fires this many Gates early so the Prism's
phase-mode morph has time to finish before `warp_wave_ready` actually
releases the wave (see `steps.common._chargelot_spawn`)."""


def _ready_warpgates(ctx: "BotContext") -> list:
    """Warp Gates currently off cooldown (a TRAINWARP_* ability is up)."""
    return [
        gate
        for gate in ctx.bot.structures(UnitTypeId.WARPGATE).ready
        if any(a.name.startswith("TRAINWARP") for a in gate.abilities)
    ]


def _warpgates_ready_to_warp(ctx: "BotContext") -> bool:
    """True when at least one Warp Gate can currently train-warp."""
    return bool(_ready_warpgates(ctx))


def warp_wave_ready(ctx: "BotContext") -> bool:
    """True once enough Warp Gates are idle at once to release a wave.

    The threshold is `min(WARP_WAVE_MIN, total warp gates)`, so this can't
    stall production below `WARP_WAVE_MIN` total Gates - early on on when we
    only have 1-2, it fires on whatever's ready, same as before batching
    existed. A Gate held back doesn't lose its readiness (cooldown only
    starts on its next cast), so idle Gates just accumulate here until the
    threshold is met.
    """
    total = ctx.bot.structures(UnitTypeId.WARPGATE).ready.amount
    if total == 0:
        return False
    return len(_ready_warpgates(ctx)) >= min(WARP_WAVE_MIN, total)


def warp_wave_imminent(ctx: "BotContext") -> bool:
    """True slightly before `warp_wave_ready` - see `WARP_WAVE_PREPHASE_MARGIN`."""
    total = ctx.bot.structures(UnitTypeId.WARPGATE).ready.amount
    if total == 0:
        return False
    threshold = max(min(WARP_WAVE_MIN, total) - WARP_WAVE_PREPHASE_MARGIN, 1)
    return len(_ready_warpgates(ctx)) >= threshold


def _incomplete_warps_near(ctx: "BotContext", pos: Point2) -> int:
    """Count own units still materializing in the Prism / pylon field."""
    n = 0
    for u in ctx.bot.units:
        if not (0.0 < u.build_progress < 1.0):
            continue
        if cy_distance_to(u.position, pos) <= PRISM_WARP_FIELD_RADIUS:
            n += 1
    return n


def chargelot_staging(ctx: "BotContext") -> Point2:
    """Muster point in front of the enemy natural (toward our base)."""
    return Point2(
        cy_towards(
            ctx.mediator.get_enemy_nat,
            ctx.mediator.get_own_nat,
            CHARGELOT_STAGING_OFFSET,
        )
    )


def _drop_threats_near(ctx: "BotContext", pos: Point2) -> int:
    """Distinct nearby enemies that can hit an air unit - the closest proxy
    available for "sources of damage" near the drop Prism (there is no
    direct API for who is currently dealing it damage)."""
    ground = ctx.mediator.get_units_in_range(
        start_points=[pos],
        distances=PRISM_DROP_THREAT_RANGE,
        query_tree=UnitTreeQueryType.EnemyGround,
    )[0]
    air = ctx.mediator.get_units_in_range(
        start_points=[pos],
        distances=PRISM_DROP_THREAT_RANGE,
        query_tree=UnitTreeQueryType.EnemyFlying,
    )[0]
    seen: set[int] = set()
    for group in (ground, air):
        for u in group:
            if u.can_attack_air:
                seen.add(u.tag)
    return len(seen)


def _drop_pick_point(ctx: "BotContext") -> Point2:
    """High-ground point in the enemy main, at the edge nearest their
    natural - minimizes how long the Prism spends exposed crossing the
    plateau to get there and back."""
    main = ctx.bot.enemy_start_locations[0]
    nat = ctx.mediator.get_enemy_nat
    main_height = ctx.bot.get_terrain_height(main)
    point = Point2((main.x, main.y))
    for offset in range(2, 14, 2):
        candidate = Point2(cy_towards(main, nat, offset))
        if ctx.bot.get_terrain_height(candidate) != main_height:
            break
        point = candidate
    return point


def _pick_muster_squad(ctx: "BotContext", count: int = PRISM_DROP_SQUAD_SIZE) -> list:
    """Up to `count` ATTACKING units closest to the muster point - used to
    peel the drop-harass squad off the first wave at the exact moment it
    commits (see `_drop_maybe_start`)."""
    staging = chargelot_staging(ctx)
    near = [
        u
        for u in ctx.units_in_role(UnitRole.ATTACKING)
        if cy_distance_to(u.position, staging) <= CHARGELOT_MUSTER_RADIUS + 3.0
    ]
    near.sort(key=lambda u: cy_distance_to(u.position, staging))
    return near[:count]


def _drop_maybe_start(ctx: "BotContext") -> None:
    """The instant the first wave commits ("attack timing" - see
    `chargelot_attack`'s `chargelot_muster_committed_at`), peel
    `PRISM_DROP_SQUAD_SIZE` units already standing at the muster into the
    one-shot drop-harass squad instead of letting them march out with the
    rest - see module docstring above `PRISM_DROP_SQUAD_SIZE`.

    Used to instead wait for freshly-*warped* units to trickle in one at a
    time before the squad could even start loading. Taking already-
    mustered units at the commit instant means there's nothing left to
    wait for, and the squad's departure can never race ahead of or lag
    behind the main wave's own attack timing - they're the same event.
    """
    if ctx.state.prism_drop_phase is not None:
        return
    if ctx.state.chargelot_muster_committed_at is None:
        return
    squad = _pick_muster_squad(ctx)
    if len(squad) < PRISM_DROP_SQUAD_SIZE:
        ctx.state.prism_drop_phase = "done"
        ctx.log(f"PRISM_DROP skipped - only {len(squad)} at muster to pick from")
        return
    for unit in squad:
        unit.hold_position()
        ctx.state.prism_drop_squad_tags.add(unit.tag)
        ctx.mediator.assign_role(tag=unit.tag, role=UnitRole.DROP_UNITS_TO_LOAD)
    ctx.state.prism_drop_phase = "loading"
    ctx.state.prism_drop_phase_entered_at = ctx.bot.time
    ctx.log(f"PRISM_DROP peeled {PRISM_DROP_SQUAD_SIZE} from the muster, loading")


def _drop_return_squad_home(ctx: "BotContext") -> None:
    """Give the still-held squad back to the main assault force - used when
    the Prism retreats with them still aboard (abort mid-flight) and drops
    them back off near staging instead of in the enemy base. They came
    from the ATTACKING muster, not a fresh DEFENDING warp-in, so they
    rejoin the attack rather than heading home."""
    for tag in ctx.state.prism_drop_squad_tags:
        ctx.mediator.assign_role(tag=tag, role=UnitRole.ATTACKING)
    ctx.state.prism_drop_squad_tags.clear()


def _run_prism_drop(ctx: "BotContext", prism, grid) -> None:
    """Drive the one-shot drop-harass state machine for `prism`. Takes full
    control of the unit for every phase - the squad is picked and claimed
    in one shot by `_drop_maybe_start`, so there is no longer a waiting
    phase where normal escort logic shares control of the Prism."""
    phase = ctx.state.prism_drop_phase

    maneuver = CombatManeuver()
    maneuver.add(KeepUnitSafe(unit=prism, grid=grid))
    phased = prism.type_id == UnitTypeId.WARPPRISMPHASING

    if phase == "loading":
        squad = list(
            ctx.mediator.get_units_from_role(role=UnitRole.DROP_UNITS_TO_LOAD)
        )
        for unit in squad:
            if not unit.orders:
                unit.hold_position()
        if phased:
            maneuver.add(
                UseAbility(AbilityId.MORPH_WARPPRISMTRANSPORTMODE, prism)
            )
        else:
            maneuver.add(
                PickUpCargo(
                    unit=prism,
                    grid=grid,
                    pickup_targets=squad,
                    cargo_switch_to_role=UnitRole.DROP_UNITS_ATTACKING,
                )
            )
            if prism.cargo_used >= PRISM_DROP_SQUAD_SIZE:
                ctx.state.prism_drop_target = _drop_pick_point(ctx)
                ctx.state.prism_drop_phase = "flying_in"
                ctx.log(f"PRISM_DROP loaded, flying to {ctx.state.prism_drop_target}")

    elif phase == "flying_in":
        if _drop_threats_near(ctx, prism.position) > PRISM_DROP_ABORT_THREATS:
            ctx.state.prism_drop_phase = "retreating"
            ctx.log("PRISM_DROP abort - multiple threats while flying in")
        else:
            target = ctx.state.prism_drop_target
            if cy_distance_to(prism.position, target) <= PRISM_DROP_ARRIVE_DIST:
                ctx.state.prism_drop_phase = "dropping"
            else:
                maneuver.add(MoveToSafeTarget(unit=prism, grid=grid, target=target))

    elif phase == "dropping":
        if (
            prism.cargo_used > 0
            and _drop_threats_near(ctx, prism.position) > PRISM_DROP_ABORT_THREATS
        ):
            ctx.state.prism_drop_phase = "retreating"
            ctx.log("PRISM_DROP abort - multiple threats before offload")
        else:
            maneuver.add(DropCargo(unit=prism, target=ctx.state.prism_drop_target))
            if prism.cargo_used == 0:
                ctx.state.prism_drop_phase = "rephasing"
                ctx.state.prism_drop_phase_entered_at = ctx.bot.time
                ctx.log("PRISM_DROP offloaded")

    elif phase == "rephasing":
        entered = ctx.state.prism_drop_phase_entered_at or ctx.bot.time
        if _drop_threats_near(ctx, prism.position) == 0:
            if AbilityId.MORPH_WARPPRISMPHASINGMODE in prism.abilities:
                maneuver.add(
                    UseAbility(AbilityId.MORPH_WARPPRISMPHASINGMODE, prism)
                )
            if phased:
                ctx.state.prism_drop_phase = "phased_in_base"
                ctx.state.prism_drop_phase_entered_at = ctx.bot.time
                ctx.log("PRISM_DROP phased in enemy base")
        elif ctx.bot.time - entered >= PRISM_DROP_REPHASE_TIMEOUT_S:
            ctx.state.prism_drop_phase = "done"
            ctx.log("PRISM_DROP rephase timed out, resuming escort")

    elif phase == "phased_in_base":
        entered = ctx.state.prism_drop_phase_entered_at or ctx.bot.time
        if _drop_threats_near(ctx, prism.position) > PRISM_DROP_ABORT_THREATS:
            if phased:
                maneuver.add(
                    UseAbility(AbilityId.MORPH_WARPPRISMTRANSPORTMODE, prism)
                )
            ctx.state.prism_drop_phase = "done"
            ctx.log("PRISM_DROP threatened in base, resuming escort")
        elif ctx.bot.time - entered >= PRISM_DROP_BASE_HOLD_S:
            ctx.state.prism_drop_phase = "done"
            ctx.log("PRISM_DROP base window done, resuming escort")

    elif phase == "retreating":
        home_point = chargelot_staging(ctx)
        if prism.cargo_used == 0:
            ctx.state.prism_drop_phase = "done"
            ctx.log("PRISM_DROP retreat complete, resuming escort")
        elif cy_distance_to(prism.position, home_point) <= PRISM_DROP_ARRIVE_DIST:
            maneuver.add(DropCargo(unit=prism, target=home_point))
            if prism.cargo_used == 0:
                _drop_return_squad_home(ctx)
                ctx.state.prism_drop_phase = "done"
                ctx.log("PRISM_DROP recovered squad, resuming escort")
        else:
            maneuver.add(MoveToSafeTarget(unit=prism, grid=grid, target=home_point))

    ctx.bot.register_behavior(maneuver)


def escort_warp_prism():
    """Escort the army across the map; phase only near the enemy.

    Priority: (1) fly with the ball, (2) phase on station once the Prism is
    near the enemy natural and the army pocket, (3) finish incomplete warps
    before dropping the field, then transport-catch-up if the pocket pulls away.
    """

    _last_mode: dict[int, str] = {}

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
        enemy = ctx.mediator.get_enemy_nat
        can_warp = _warpgates_ready_to_warp(ctx)
        grid = ctx.mediator.get_air_grid
        _drop_maybe_start(ctx)

        for prism in prisms:
            if ctx.state.prism_drop_phase not in (None, "done"):
                _run_prism_drop(ctx, prism, grid)
                continue

            maneuver = CombatManeuver()
            maneuver.add(KeepUnitSafe(unit=prism, grid=grid))
            phased = prism.type_id == UnitTypeId.WARPPRISMPHASING
            # Only trust incomplete counts under our own field — pylon warps
            # near the rally otherwise look like Prism warp-ins.
            incomplete = (
                _incomplete_warps_near(ctx, prism.position) if phased else 0
            )
            prism_to_enemy = cy_distance_to(prism.position, enemy)
            near_enemy = prism_to_enemy <= PRISM_ENEMY_PHASE_RANGE

            if army is None:
                # Once leave time hits, fly to Chargelot staging even with no
                # ATTACKING ball yet. Sitting at home rally until a late force
                # Wave (Terran pressure ~6:35) leaves no time to phase.
                leave_time = ARMY_LEAVE_TIME
                if ctx.bot.time >= leave_time:
                    hold = chargelot_staging(ctx)
                    mode = "preposition"
                    near_hold = (
                        cy_distance_to(prism.position, hold)
                        <= PRISM_PHASE_APPROACH
                    )
                    # Phase at staging while waiting for the ball so warps
                    # can land as soon as Wave 1 arrives.
                    if (
                        near_enemy
                        and near_hold
                        and not phased
                        and AbilityId.MORPH_WARPPRISMPHASINGMODE
                        in prism.abilities
                    ):
                        mode = "phase_preposition"
                        maneuver.add(
                            UseAbility(
                                AbilityId.MORPH_WARPPRISMPHASINGMODE, prism
                            )
                        )
                    elif phased and incomplete == 0 and not (
                        near_enemy and near_hold
                    ):
                        maneuver.add(
                            UseAbility(
                                AbilityId.MORPH_WARPPRISMTRANSPORTMODE, prism
                            )
                        )
                    elif phased and incomplete > 0:
                        mode = "finish_warps_no_army"
                else:
                    hold = targeting.rally_point(ctx)
                    mode = "rally"
                    if phased and incomplete == 0:
                        maneuver.add(
                            UseAbility(
                                AbilityId.MORPH_WARPPRISMTRANSPORTMODE, prism
                            )
                        )
                    elif phased and incomplete > 0:
                        mode = "finish_warps_no_army"
                if mode != "phase_preposition":
                    maneuver.add(
                        MoveToSafeTarget(unit=prism, grid=grid, target=hold)
                    )
                if _last_mode.get(prism.tag) != mode:
                    _last_mode[prism.tag] = mode
                    ctx.log(f"PRISM {mode}")
                ctx.bot.register_behavior(maneuver)
                continue

            behind = Point2(cy_towards(army, home, PRISM_STANDOFF))
            dist = cy_distance_to(prism.position, behind)
            near_army = dist <= PRISM_PHASE_APPROACH
            far_from_pocket = dist > PRISM_REPOSITION_DIST
            staging = chargelot_staging(ctx)
            near_staging = (
                cy_distance_to(prism.position, staging) <= PRISM_PHASE_APPROACH
            )

            # Phase when near the enemy natural and either the army pocket or
            # Chargelot staging (prepositioned Prism before the ball arrives)
            # AND the next warp wave is imminent - not just because we are in
            # position. Phasing pins the Prism in place, so holding phase
            # open just for being "on station" never gave it a chance to
            # reposition between waves; only an in-progress warp (incomplete)
            # or an about-to-fire wave should keep the field up.
            on_station = near_enemy and (near_army or near_staging)
            hold_phase = incomplete > 0 or (on_station and warp_wave_imminent(ctx))

            if hold_phase:
                if not phased:
                    if on_station and (
                        AbilityId.MORPH_WARPPRISMPHASINGMODE in prism.abilities
                    ):
                        mode = "phase_on_station"
                        maneuver.add(
                            UseAbility(
                                AbilityId.MORPH_WARPPRISMPHASINGMODE, prism
                            )
                        )
                    elif on_station:
                        mode = "wait_phase"
                    else:
                        mode = "escort"
                        maneuver.add(
                            MoveToSafeTarget(
                                unit=prism, grid=grid, target=behind
                            )
                        )
                else:
                    mode = (
                        "on_station"
                        if near_enemy and (near_army or near_staging)
                        else "crawl_with_warps"
                    )
                    hold_target = behind if near_army else staging
                    if cy_distance_to(prism.position, hold_target) > 2.0:
                        maneuver.add(
                            MoveToSafeTarget(
                                unit=prism, grid=grid, target=hold_target
                            )
                        )
            else:
                # Between waves (or genuinely off station): drop phase mode
                # if we're still holding it, then fly to keep up with the
                # army - `behind` is recomputed from the live army position
                # every frame, so this is how the Prism actually repositions.
                if phased and incomplete == 0:
                    mode = "reposition"
                    maneuver.add(
                        UseAbility(
                            AbilityId.MORPH_WARPPRISMTRANSPORTMODE, prism
                        )
                    )
                elif on_station:
                    mode = "between_waves"
                else:
                    mode = "escort"
                maneuver.add(
                    MoveToSafeTarget(unit=prism, grid=grid, target=behind)
                )

            if _last_mode.get(prism.tag) != mode:
                _last_mode[prism.tag] = mode
                ctx.log(
                    f"PRISM {mode} dist={dist:.0f} "
                    f"enemy={prism_to_enemy:.0f} "
                    f"warp={int(can_warp)} inc={incomplete}"
                )

            ctx.bot.register_behavior(maneuver)

    return routine


def drop_squad_harass():
    """Once the drop-harass squad has been unloaded (`UnitRole.
    DROP_UNITS_ATTACKING` - see `escort_warp_prism`'s "dropping" phase),
    attack-move it into the enemy main. No muster/kite machinery needed for
    4 units that are already standing in the middle of it - plain AMove
    auto-engages whatever it bumps into (workers, buildings) along the way.
    """

    def routine(ctx: "BotContext") -> None:
        squad = ctx.mediator.get_units_from_role(role=UnitRole.DROP_UNITS_ATTACKING)
        if not squad:
            return
        target = ctx.bot.enemy_start_locations[0]
        for unit in squad:
            ctx.bot.register_behavior(AMove(unit=unit, target=target))

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


def _adept_log(ctx: "BotContext", action: str) -> None:
    if ctx.state.adept_last_action == action:
        return
    ctx.state.adept_last_action = action
    ctx.log(f"ADEPT {action}")


def _adept_clear_shade_state(ctx: "BotContext", tag: int) -> None:
    ctx.state.adept_shade_cast_at.pop(tag, None)
    ctx.state.adept_shade_goal.pop(tag, None)
    ctx.state.adept_shade_aborted.discard(tag)
    ctx.state.adept_shade_last_pos.pop(tag, None)
    ctx.state.adept_shade_awaiting_teleport.discard(tag)


def _adept_shade_unit(ctx: "BotContext", adept):
    """Shade projection for this Adept, if one is active."""
    shades = ctx.bot.units(UnitTypeId.ADEPTPHASESHIFT)
    if not shades:
        return None
    tracked = getattr(ctx.bot, "adept_shades", None) or {}
    for shade in shades:
        info = tracked.get(shade.tag)
        if info and info.get(SHADE_OWNER) == adept.tag:
            return shade
    return cy_closest_to(adept.position, shades)


def _adept_can_shade(adept) -> bool:
    return AbilityId.ADEPTPHASESHIFT_ADEPTPHASESHIFT in adept.abilities


def _adept_cancel_ready(adept) -> bool:
    """CANCEL dismisses the shade with NO teleport (danger abort only)."""
    return AbilityId.CANCEL_ADEPTPHASESHIFT in adept.abilities


def _shade_cast_point(from_pos: Point2, to_pos: Point2) -> Point2:
    """Point within shade cast range toward `to_pos`."""
    if cy_distance_to(from_pos, to_pos) <= ADEPT_SHADE_RANGE:
        return Point2((to_pos.x, to_pos.y))
    return Point2(cy_towards(from_pos, to_pos, ADEPT_SHADE_RANGE))


def _shade_age(ctx: "BotContext", adept_tag: int) -> float | None:
    cast_at = ctx.state.adept_shade_cast_at.get(adept_tag)
    if cast_at is None:
        return None
    return ctx.bot.time - cast_at


def _imminent_danger_at(ctx: "BotContext", position: Point2) -> bool:
    """True if landing near `position` is suicidal.

    Workers are ignored. A single army unit (e.g. one Ling/Queen) is not
    enough to abort — that was cancelling every hop into a Zerg natural.
    Abort on static defense or ≥2 non-worker combat units in radius.
    """
    near = ctx.mediator.get_units_in_range(
        start_points=[position],
        distances=ADEPT_SHADE_DANGER_RADIUS,
        query_tree=UnitTreeQueryType.EnemyGround,
    )[0]
    army_n = 0
    for enemy in near:
        if enemy.type_id in ENEMY_WORKERS:
            continue
        if enemy.is_structure:
            if (
                enemy.type_id in _SHADE_DANGER_STRUCTURES
                or enemy.can_attack_ground
            ):
                return True
            continue
        army_n += 1
        if army_n >= 2:
            return True
    return False


def _cast_shade(
    ctx: "BotContext",
    maneuver: CombatManeuver,
    adept,
    destination: Point2,
    reason: str,
) -> bool:
    """Queue a shade cast toward `destination`. Returns True if cast."""
    can = _adept_can_shade(adept)
    dist = cy_distance_to(adept.position, destination)
    if not can or dist < ADEPT_SHADE_MIN_GAP:
        return False
    # Initial cast must land within ~9 range; shade is re-pathed to `destination`
    # each frame while active so it keeps traveling toward the real goal.
    cast_target = _shade_cast_point(adept.position, destination)
    maneuver.add(
        UseAbility(
            AbilityId.ADEPTPHASESHIFT_ADEPTPHASESHIFT,
            adept,
            cast_target,
        )
    )
    ctx.state.adept_shade_cast_at[adept.tag] = ctx.bot.time
    ctx.state.adept_shade_goal[adept.tag] = Point2((destination.x, destination.y))
    _adept_log(ctx, f"shade {reason}")
    return True


def _adept_do_stutter(
    ctx: "BotContext",
    adept,
    enemies,
    *,
    chase_target=None,
    advance_to: Point2 | None = None,
) -> str:
    """Stutter-step with direct orders (no KeepUnitSafe before the shot).

    1) Weapon ready + enemies in range → attack lowest-HP in range.
    2) Weapon on cooldown + enemies in range → plain move kite step.
    3) Else re-engage chase target / advance (do not influence-retreat here;
       that was canceling shots and blocking re-entry into range).
    """
    ground = [e for e in enemies if not e.is_structure]
    in_range = list(cy_in_attack_range(adept, ground)) if ground else []
    cd = float(adept.weapon_cooldown)
    pick = cy_pick_enemy_target(in_range) if in_range else None
    branch = "advance"

    if in_range and pick is not None:
        if cd > 0.0:
            ctx.state.adept_attack_pending.pop(adept.tag, None)
            closest = cy_closest_to(adept.position, in_range)
            away = cy_distance_to(closest.position, adept.position) + ADEPT_KITE_STEP
            retreat = Point2(cy_towards(closest.position, adept.position, away))
            adept.move(retreat)
            branch = "kite"
        elif adept.tag in ctx.state.adept_attack_pending:
            # Shot commanded; wait until weapon_cooldown rises. Re-issuing
            # attack here cancels the animation (orders often lag a frame).
            branch = "attack_hold"
        else:
            adept.attack(pick)
            ctx.state.adept_attack_pending[adept.tag] = pick.tag
            branch = "attack"
    elif chase_target is not None:
        ctx.state.adept_attack_pending.pop(adept.tag, None)
        adept.attack(chase_target)
        branch = "chase"
    elif advance_to is not None:
        ctx.state.adept_attack_pending.pop(adept.tag, None)
        adept.move(advance_to)
        branch = "advance"
    else:
        ctx.state.adept_attack_pending.pop(adept.tag, None)

    return branch


def harassing_adept():
    """Shade ahead while pathing; stutter-step while fighting; auto-teleport.

    Cast drops the shade within ability range, then the shade is ordered to
    the Adept's real destination each frame. The Adept teleports when the
    shade expires (~7s). `CANCEL_ADEPTPHASESHIFT` aborts with NO teleport —
    issue it only from 6.5s onward when landing/dest has combat danger.

    Combat micro: if weapon ready and enemies in range, attack the lowest-HP
    target; while on cooldown, kite back; then re-engage.
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
        home = ctx.mediator.get_own_nat

        live_tags = {a.tag for a in adepts}
        for tag in list(ctx.state.adept_shade_cast_at):
            if tag not in live_tags:
                _adept_clear_shade_state(ctx, tag)
        for tag in list(ctx.state.adept_attack_pending):
            if tag not in live_tags:
                ctx.state.adept_attack_pending.pop(tag, None)

        for adept in adepts:
            maneuver = CombatManeuver()
            shade = _adept_shade_unit(ctx, adept)

            # Confirm auto-teleport after a shade expired without CANCEL.
            if (
                adept.tag in ctx.state.adept_shade_awaiting_teleport
                and shade is None
            ):
                last = ctx.state.adept_shade_last_pos.get(adept.tag)
                jump = (
                    cy_distance_to(adept.position, last)
                    if last is not None
                    else None
                )
                if jump is not None and jump < 3.0:
                    _adept_log(ctx, "shade teleport")
                _adept_clear_shade_state(ctx, adept.tag)

            enemies_near = ctx.mediator.get_units_in_range(
                start_points=[adept.position],
                distances=ADEPT_THREAT_RANGE,
                query_tree=UnitTreeQueryType.EnemyGround,
            )[0]
            combat_enemies = [e for e in enemies_near if not e.is_structure]
            army_threats = [
                e
                for e in combat_enemies
                if e.type_id not in ENEMY_WORKERS
            ]

            fleeing = (
                adept.shield_percentage <= ADEPT_FLEE_SHIELD
                or (army_threats and adept.shield_percentage <= 0.55)
            )

            chase_target = None
            low = [
                e
                for e in combat_enemies
                if e.health_percentage < 0.35 and not e.is_structure
            ]
            if low:
                chase_target = cy_closest_to(position=adept.position, units=low)
            elif go_natural:
                workers = [
                    u
                    for u in ctx.bot.enemy_units
                    if u.type_id in ENEMY_WORKERS
                    and cy_distance_to(u.position, natural) < 16.0
                ]
                if workers:
                    chase_target = cy_closest_to(
                        position=adept.position, units=workers
                    )

            if fleeing:
                dest = home
                mode = "flee"
            elif chase_target is not None:
                dest = chase_target.position
                mode = "chase"
            else:
                dest = natural
                mode = "travel"

            age = _shade_age(ctx, adept.tag)
            if shade is not None or age is not None:
                age = age or 0.0
                if adept.tag in ctx.state.adept_shade_aborted:
                    if age < ADEPT_SHADE_LIFETIME:
                        # After abort: still poke workers if already in range;
                        # otherwise keep safe and drift toward the mineral line.
                        workers_in_range = [
                            e
                            for e in combat_enemies
                            if e.type_id in ENEMY_WORKERS
                            and cy_distance_to(adept.position, e.position)
                            <= 5.0
                        ]
                        if workers_in_range and len(army_threats) < 2:
                            branch = _adept_do_stutter(
                                ctx,
                                adept,
                                workers_in_range,
                                chase_target=workers_in_range[0],
                            )
                            _adept_log(ctx, f"abort poke {branch}")
                        else:
                            maneuver.add(KeepUnitSafe(unit=adept, grid=grid))
                            maneuver.add(AMove(unit=adept, target=dest))
                            ctx.bot.register_behavior(maneuver)
                        continue
                    _adept_clear_shade_state(ctx, adept.tag)
                elif shade is not None:
                    ctx.state.adept_shade_goal[adept.tag] = Point2(
                        (dest.x, dest.y)
                    )
                    ctx.state.adept_shade_last_pos[adept.tag] = Point2(
                        (shade.position.x, shade.position.y)
                    )
                    shade.move(dest)
                    # From 6.5s: CANCEL only to abort into danger (no teleport).
                    if age >= ADEPT_SHADE_CONFIRM_AT and _adept_cancel_ready(
                        adept
                    ):
                        land_danger = _imminent_danger_at(ctx, shade.position)
                        dest_danger = _imminent_danger_at(ctx, dest)
                        if land_danger or dest_danger:
                            ctx.state.adept_shade_aborted.add(adept.tag)
                            from bot.intel import chargelot_metrics

                            chargelot_metrics.note_adept_shade_abort(ctx)
                            maneuver.add(
                                UseAbility(
                                    AbilityId.CANCEL_ADEPTPHASESHIFT, adept
                                )
                            )
                            _adept_log(ctx, "shade abort danger")
                            ctx.bot.register_behavior(maneuver)
                            continue
                        # Safe: do not CANCEL — let shade expire → auto-teleport.
                    if mode == "flee":
                        maneuver.add(KeepUnitSafe(unit=adept, grid=grid))
                        maneuver.add(AMove(unit=adept, target=dest))
                        ctx.bot.register_behavior(maneuver)
                    elif combat_enemies or mode == "chase":
                        branch = _adept_do_stutter(
                            ctx,
                            adept,
                            combat_enemies,
                            chase_target=chase_target,
                            advance_to=dest if chase_target is None else None,
                        )
                        _adept_log(ctx, f"stutter {branch}")
                    else:
                        maneuver.add(AMove(unit=adept, target=dest))
                        ctx.bot.register_behavior(maneuver)
                    continue
                # Shade unit gone while we still track a cast — awaiting teleport
                # or expired after abort / failed cast.
                if age < ADEPT_SHADE_LIFETIME + 0.5:
                    if adept.tag not in ctx.state.adept_shade_aborted:
                        ctx.state.adept_shade_awaiting_teleport.add(adept.tag)
                    if combat_enemies and mode != "flee":
                        _adept_do_stutter(
                            ctx,
                            adept,
                            combat_enemies,
                            chase_target=chase_target,
                            advance_to=dest if chase_target is None else None,
                        )
                    else:
                        maneuver.add(KeepUnitSafe(unit=adept, grid=grid))
                        maneuver.add(AMove(unit=adept, target=dest))
                        ctx.bot.register_behavior(maneuver)
                    continue
                _adept_clear_shade_state(ctx, adept.tag)

            # Pathing to a destination — send shade ahead whenever ready.
            if _cast_shade(ctx, maneuver, adept, dest, mode):
                ctx.bot.register_behavior(maneuver)
                continue

            if mode == "flee":
                maneuver.add(KeepUnitSafe(unit=adept, grid=grid))
                maneuver.add(AMove(unit=adept, target=dest))
                _adept_log(ctx, "flee move")
                ctx.bot.register_behavior(maneuver)
            elif combat_enemies or mode == "chase":
                branch = _adept_do_stutter(
                    ctx,
                    adept,
                    combat_enemies,
                    chase_target=chase_target,
                    advance_to=dest if chase_target is None else None,
                )
                _adept_log(ctx, f"stutter {branch}")
            else:
                maneuver.add(KeepUnitSafe(unit=adept, grid=grid))
                maneuver.add(AMove(unit=adept, target=dest))
                _adept_log(ctx, f"{mode} move")
                ctx.bot.register_behavior(maneuver)

    return routine
