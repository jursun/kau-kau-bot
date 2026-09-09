"""Speedling All-In — 12-pool zergling rush.

Opening: `Speedling All-In` in `zerg_builds.yml` — overlord, pool, extractor, 3 on
gas, metabolic boost, first lings, queen.

After the opening: hard drone cap of 16, one macro hatch in the main for
larva, waves into the enemy main the moment speed lands.

Validated in-game: win vs VeryHard Terran at 5:10.
"""

from __future__ import annotations

from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId

from bot.builds.definition import Army, BuildDefinition, Combat, Economy
from bot.consts import FOCUS_MAIN, MASS_LING_COMP
from bot.routines import combat, gates, scouting
from bot.steps import common as c
from bot.steps import zerg as z

# Main + one macro hatch. Also the queen target: one inject per hatchery.
MACRO_HATCH_COUNT = 2

BUILD = BuildDefinition(
    name="Speedling All-In",
    label="Speedling All-In (12 pool)",
    race=Race.Zerg,
    economy=Economy(
        worker_target=16,
        workers_per_base=16,
        max_bases=1,  # main only; the macro hatch is larva, not a base
        gas_per_base=1,
        max_gas=1,
        workers_per_gas=3,
        long_distance_mine=False,
    ),
    army=Army(
        comp=MASS_LING_COMP,
        types=frozenset({UnitTypeId.ZERGLING}),
        upgrades=(z.LING_SPEED,),  # speed and nothing else; the timing is the point
    ),
    combat=Combat(
        routines=(
            combat.release_waves(),
            combat.defend_home(),
            combat.attack_squads(),
            scouting.air_scout(UnitTypeId.OVERLORD),
        ),
        wave_gate=gates.upgrade_done(z.LING_SPEED),
        wave1_min=6,
        wave_growth=1.10,
        focus=(FOCUS_MAIN,),
    ),
    always=(c.mining(), z.inject_larva()),
    macro_steps=(
        c.auto_supply(),
        # One queen per hatchery — the macro hatch needs its own for injects.
        z.train_queens(per_base=1, maximum=MACRO_HATCH_COUNT),
        # Metabolic boost outranks the macro hatch; wait until it is paid for.
        z.macro_hatch(MACRO_HATCH_COUNT, gate=gates.upgrade_started(z.LING_SPEED)),
        c.expansions(),
        c.gas_buildings(),
        c.upgrades(),
        c.build_workers(),
        c.spawn_army(),
    ),
)
