"""Upgrade rush — 3-base mass lings behind double evo / lair / hive.

Opening: `UpgradeRush` in `zerg_builds.yml` — 13 overlord, 15 hatch before
pool, 16 pool, 17 gas, 19 overlord, metabolic boost, queen, second gas.

After the opening the macro steps drone up, take the third and beyond, and
`UpgradeController` auto-techs evo -> lair -> hive on the way through +1/+1
to 3/3 and adrenal glands. Wave 1 waits until both +1s are about to land.

Never played. See MIGRATION.md.
"""

from __future__ import annotations

from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from bot.builds.definition import Army, BuildDefinition, Combat, Economy
from bot.consts import FOCUS_MAIN, FOCUS_NATURAL, FULL_LING_UPGRADES, MASS_LING_COMP
from bot.routines import combat, gates, scouting
from bot.steps import common as c
from bot.steps import zerg as z

PLUS_ONE = (UpgradeId.ZERGMELEEWEAPONSLEVEL1, UpgradeId.ZERGGROUNDARMORSLEVEL1)
PLUS_ONE_RESEARCH_TIME = 114.0
LEAVE_BEFORE_PLUS_ONE = 10.0

BUILD = BuildDefinition(
    name="UpgradeRush",
    label="Upgrade Rush (mass lings)",
    race=Race.Zerg,
    economy=Economy(
        worker_target=80,
        workers_per_base=22,
        max_bases=5,
        gas_per_base=2,
        max_gas=4,
        workers_per_gas=3,
        long_distance_mine=True,
    ),
    army=Army(
        comp=MASS_LING_COMP,
        types=frozenset({UnitTypeId.ZERGLING}),
        upgrades=FULL_LING_UPGRADES,
    ),
    combat=Combat(
        routines=(
            combat.release_waves(),
            combat.defend_home(),
            combat.attack_squads(),
            scouting.air_scout(UnitTypeId.OVERLORD),
        ),
        # Wave 1 holds for +1/+1; later waves leave on size alone.
        wave_gate=gates.all_of(
            gates.upgrade_done(z.LING_SPEED),
            gates.any_of(
                gates.after_wave(1),
                gates.upgrades_within(
                    PLUS_ONE, LEAVE_BEFORE_PLUS_ONE, PLUS_ONE_RESEARCH_TIME
                ),
            ),
        ),
        wave1_min=20,
        wave_growth=1.10,
        focus=(FOCUS_NATURAL, FOCUS_MAIN),
    ),
    # This build needs gas all game for the upgrade path, so no pull-off gate.
    always=(c.mining(), c.gas_workers(), z.inject_larva()),
    macro_steps=(
        c.auto_supply(),
        z.train_queens(per_base=1, maximum=4),
        # UpgradeController only ever builds one evo; two enable parallel +1/+1.
        z.evolution_chambers(2, gate=gates.upgrade_started(z.LING_SPEED)),
        c.expansions(),
        c.gas_buildings(),
        c.upgrades(),
        c.build_workers(),
        c.spawn_army(),
    ),
)
