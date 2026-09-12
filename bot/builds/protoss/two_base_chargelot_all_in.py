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
PvT guide). Scout Probe harasses until 2:00; Adept shades and hits the
natural at 3:00. Gateways 2-4 wall the natural.
"""

from __future__ import annotations

from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from bot.builds.definition import Army, BuildDefinition, Combat, Economy
from bot.consts import CHARGELOT_COMP, FOCUS_MAIN, FOCUS_NATURAL
from bot.routines import combat, gates, protoss_support as ps
from bot.steps import common as c
from bot.steps import protoss as p

# Full gateway count once the all-in commits (1 from the opening + 7 more).
GATEWAY_COUNT = 8

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
            combat.release_first_wave_then_stream(muster=False),
            combat.defend_home(),
            combat.attack_squads(),
            ps.escort_warp_prism(),
            ps.escort_observer(),
            ps.harassing_adept(),
            ps.scout_probe_harass(),
        ),
        # Charge done AND 5:20 — army on the map by then, base hit ~5:45.
        wave_gate=gates.all_of(
            gates.upgrade_done(CHARGE),
            gates.after_time(ARMY_LEAVE_TIME),
        ),
        wave1_min=FIRST_WAVE,
        wave_growth=1.0,  # unused once streaming; kept for validator math
        focus=(FOCUS_NATURAL, FOCUS_MAIN),
        wave_stage_label="Chargelot All-In",
    ),
    always=(
        c.mining(),
        # Full gas until Charge is under way, then peel for mineral flood.
        # Keep one per geyser so Robo units / extra Stalkers stay fundable.
        c.gas_workers(
            pull_off=gates.upgrade_started(CHARGE),
            when_pulled=1,
        ),
        # Outside MacroPlan so 8-Gate warpins cannot starve pylon placement.
        c.auto_supply(gate=lambda ctx: ctx.build_completed),
        p.pylon_buffer(min_left=32, max_pending=4, gate=lambda ctx: ctx.build_completed),
    ),
    macro_steps=(
        # Gates before Robo so the 8-Gate commit is never waiting on gas units.
        # Gates 2-4 at the natural choke (closest_to gatekeeper/enemy; not ares
        # wall=True — that is main-ramp-only). Exit path left by formation.
        # Opening already placed Gateway 1; wall_natural covers the next 3.
        p.gateways(
            GATEWAY_COUNT,
            gate=gates.upgrade_started(CHARGE),
            wall_natural=3,
        ),
        c.structure(
            UnitTypeId.ROBOTICSFACILITY,
            1,
            gate=gates.upgrade_started(CHARGE),
        ),
        c.expansions(),
        c.gas_buildings(),
        c.upgrades(),
        c.build_workers(),
        c.spawn_army(),
    ),
)
