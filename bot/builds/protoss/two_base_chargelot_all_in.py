"""2-base Chargelot All-In — blink-looking opener into mass Charge Zealots.

Opening (`protoss_builds.yml`): standard gate → gas → nexus → cyber → gas →
pylon timings so the build reads as a blink / macro opener. Chrono Adept,
Twilight + Warp Gate ASAP, chrono Charge, two Stalkers — then the dynamic
plan takes over.

After the opening: saturate two bases (~26 probes), keep building to eight
Gateways, Robotics Facility → Warp Prism (+ Observer when gas allows), and
flood Chargelots. Gas workers peel off once Charge is paid for so minerals
go into Gates and Zealots; leave a trickle for Stalkers / Prism / Obs.

Intended hit: leave ~5:20, on the enemy base ~5:45 with Warp Prism
phasing behind the ball, Observer overhead, and a few Stalkers (wshadows
PvT guide). Opening worker harasses until ~1:48; Adept shades and hits the
natural at 3:00. Around 3:30, Gateways 2-3 and the Robotics Facility fill
the natural wall (GateKeeper gap left open for army exit).
"""

from __future__ import annotations

from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from bot.builds.definition import Army, BuildDefinition, Combat, Economy
from bot.consts import CHARGELOT_COMP, FOCUS_MAIN, FOCUS_NATURAL
from bot.intel import chargelot_metrics
from bot.routines import combat, gates, protoss_support as ps
from bot.routines.worker_harass import worker_harass
from bot.steps import common as c
from bot.steps import protoss as p

# Full gateway count once the all-in commits (1 from the opening + 7 more).
GATEWAY_COUNT = 8
# Opening Gate + 2 nat-wall Gates before Robotics Facility (wall trio).
WALL_GATEWAY_COUNT = 3

# Two bases mining; probe chrono on both nexi during the opening aims for
# roughly this before minerals dump into Gates / Zealots.
WORKER_TARGET = 26

# Leave across the map at 5:20 once Charge is done and a Zealot ball exists;
# guide hit on the enemy base is ~5:45. Clock + Charge both required so we
# do not trickle out early without Charge, or sit home after 5:20.
FIRST_WAVE = 12
ARMY_LEAVE_TIME = 5 * 60 + 20

CHARGE = UpgradeId.CHARGE
WARPGATE = UpgradeId.WARPGATERESEARCH


def _chargelot_home_rally(ctx):
    """Nat-front rally — pinning `combat.rally` collapses mineral-line holds."""
    nat = ctx.mediator.get_own_nat
    return nat.towards(
        ctx.bot.enemy_start_locations[0], ctx.build.combat.rally_offset
    )


BUILD = BuildDefinition(
    name="2base Chargelot All-In",
    label="2base Chargelot All-In",
    race=Race.Protoss,
    economy=Economy(
        worker_target=WORKER_TARGET,
        workers_per_base=16,
        max_bases=2,
        gas_per_base=2,
        max_gas=2,
        workers_per_gas=3,
        long_distance_mine=False,
    ),
    army=Army(
        # Only ground army that should muster/attack. Prism / Obs / Adept are
        # trained via `comp` but get support roles in `roles.SUPPORT_ROLES`.
        comp=CHARGELOT_COMP,
        types=frozenset({UnitTypeId.ZEALOT, UnitTypeId.STALKER}),
        # Warp Gate is started in the opening; keep it here so macro still
        # finishes it if the opening handed off early.
        upgrades=(WARPGATE, CHARGE),
    ),
    combat=Combat(
        routines=(
            # First wave musters at the enemy-front staging point, then streams.
            combat.release_first_wave_then_stream(muster=True),
            combat.defend_home(),
            combat.chargelot_attack(),
            ps.escort_warp_prism(),
            ps.escort_observer(),
            ps.harassing_adept(),
            worker_harass(),
        ),
        # Charge done AND 5:20 — army on the map by then, base hit ~5:45.
        wave_gate=gates.all_of(
            gates.upgrade_done(CHARGE),
            gates.after_time(ARMY_LEAVE_TIME),
        ),
        wave1_min=FIRST_WAVE,
        wave_growth=1.0,  # unused once streaming; kept for validator math
        focus=(FOCUS_NATURAL, FOCUS_MAIN),
        # Pin rally so hold_positions is a single nat-front point — mineral
        # line extras remapped every frame (unstable townhall order) and
        # sent warping Zealots thrashing across the ramp.
        rally=_chargelot_home_rally,
        wave_stage_label="Chargelot All-In",
    ),
    always=(
        # Mining/gas every frame. Supply lives in macro_steps (after Gates/Robo)
        # so the dedicated builder prefers production over pylons.
        c.mining(),
        # Gas schedule 3 → 1 → 2 (Charge bank / Prism / Stalkers).
        c.chargelot_gas_workers(),
    ),
    on_step=chargelot_metrics.update_chargelot_metrics,
    on_end=chargelot_metrics.on_game_end,
    on_unit_created=chargelot_metrics.note_unit_created,
    on_unit_destroyed=chargelot_metrics.note_unit_destroyed,
    on_upgrade_complete=chargelot_metrics.note_upgrade_complete,
    macro_steps=(
        # Wall FirstPylon before Gate/Robo so ThreeByThreesWall slots have power.
        # Opening already places `pylon @ nat_wall`; this is a safety net.
        p.natural_wall_pylon(gate=gates.upgrade_started(CHARGE)),
        # Emergency supply before production: hard supply-locks (Persephone T /
        # LeyLines Z) never reached wave1_min because Gates outranked pylons.
        p.auto_supply(
            gate=lambda ctx: ctx.build_completed and ctx.bot.supply_left <= 10
        ),
        # Stalker #2 is queued in the opening YAML (before WG morph). Do not
        # also spawn_army(stalkers < 2) here — that double-queued #3.
        # MacroPlan short-circuits on the first successful spend. Build the
        # wall trio (Gate 1+2 + Robo) before dumping minerals into Gates 4-8,
        # otherwise Robo/Prism slip past the 5:20 leave timing.
        # Opening Gateway 1 is in main (@ ramp). Gates 2-3 take nat wall 3x3
        # slots (~3:30); Robo prefers a third wall slot, else falls back to main.
        # GateKeeper gap stays open for exit. See protoss_building_placements.yml.
        p.gateways(
            WALL_GATEWAY_COUNT,
            gate=gates.upgrade_started(CHARGE),
            wall_natural=2,
        ),
        p.robotics_facility_at_natural_wall(
            1,
            gate=gates.all_of(
                gates.upgrade_started(CHARGE),
                lambda ctx: c.chargelot_stalkers_out(ctx) >= 2,
            ),
        ),
        p.gateways(
            GATEWAY_COUNT,
            gate=gates.all_of(
                gates.upgrade_started(CHARGE),
                lambda ctx: (
                    ctx.bot.structures(UnitTypeId.ROBOTICSFACILITY).amount
                    + ctx.bot.structure_pending(UnitTypeId.ROBOTICSFACILITY)
                )
                > 0,
            ),
            wall_natural=2,
        ),
        # After production so the single builder prefers Gates/Robo when needed.
        p.auto_supply(gate=lambda ctx: ctx.build_completed),
        p.pylon_buffer(min_left=40, max_pending=5, gate=lambda ctx: ctx.build_completed),
        p.expansions(),
        p.gas_buildings(),
        c.upgrades(),
        c.build_workers(),
        c.spawn_army(),
    ),
)
