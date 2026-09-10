"""Upgrade rush — 3-base mass lings behind double evo / lair / hive.

Opening: `UpgradeRush` in `zerg_builds.yml` — 13 overlord, 15 hatch before
pool, 16 pool, 17 gas, 19 overlord, metabolic boost, queen, second gas.

After the opening the macro steps drone up (to a 60-worker cap, 2 extractors
max), take the third and beyond, and `UpgradeController` auto-techs evo ->
lair -> hive on the way through +1/+1 to 3/3 and adrenal glands.

Wave 1 leaves once Speed is done and at least `wave1_min` lings are massed;
every wave after that is 25% bigger and leaves as soon as it is ready —
no extra tech gate. Once wave 1 is out, production priority alternates
between economy and army (see `common.split_production`) — economy keeps
first pick until workers hit their target, then army takes over — instead
of drones having outright priority the whole game. Spore Crawlers start
going up in each base's mineral line at the 4-minute mark, capped at one
per owned townhall.

A mid/late-game larva bottleneck shows up as minerals floating with
nowhere to go once economy and army are both larva-limited rather than
mineral-limited. `z.overflow_hatcheries()` catches that: whenever the bank
is sitting on more than 500 floating minerals, it takes another base (or,
if none are left, a macro hatch) beyond the normal `max_bases` cap - more
hatcheries means more larva slots, which is the actual fix for a larva
bottleneck.

Once there's a queen to spare beyond one per base, it peels off injecting to
spread creep (`routines.creep.spread_creep`); every burrowed tumor also
spawns a follow-on tumor toward the enemy on its own cooldown
(`routines.creep.spread_tumors`), so creep keeps crawling forward even
between queen-placed tumors. One Overseer comes out of Lair once the first
wave releases and heads for wherever the biggest attacking squad is going —
staying at the edge of enemy range rather than trailing into it
(`routines.combat.escort_overseers`) — for vision and detection on the push.

Validated in-game: win vs VeryHard Terran at 8:54 (TorchesAIE_v4).
"""

from __future__ import annotations

from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId

from bot.builds.definition import Army, BuildDefinition, Combat, Economy
from bot.consts import FOCUS_MAIN, FOCUS_NATURAL, FULL_LING_UPGRADES, MASS_LING_COMP
from bot.routines import combat, creep, gates, scouting
from bot.steps import common as c
from bot.steps import zerg as z

BUILD = BuildDefinition(
    name="UpgradeRush",
    label="Upgrade Rush (mass lings)",
    race=Race.Zerg,
    economy=Economy(
        worker_target=60,
        workers_per_base=22,
        max_bases=5,
        gas_per_base=2,
        max_gas=2,
        workers_per_gas=3,
        long_distance_mine=True,
    ),
    army=Army(
        comp=MASS_LING_COMP,
        types=frozenset({UnitTypeId.ZERGLING}),
        upgrades=FULL_LING_UPGRADES,
        # UpgradeController only ever builds one evo; two enable parallel +1/+1.
        evolution_chambers=2,
        evolution_chamber_gate=gates.upgrade_started(z.LING_SPEED),
    ),
    # Hatch (15) comes before pool (16) by design (see the opening above), so
    # the pool lands around a minute in rather than the ~15-20s of an
    # immediate-pool opening; the default `pool_deadline` assumes the latter,
    # so this build states its own.
    pool_deadline=75.0,
    combat=Combat(
        routines=(
            combat.release_waves(),
            combat.defend_home(),
            combat.attack_squads(),
            combat.escort_overseers(),
            creep.spread_creep(),
            creep.spread_tumors(),
            scouting.air_scout(UnitTypeId.OVERLORD),
        ),
        # Speed done + wave1_min massed is the only gate; later waves leave
        # on size alone.
        wave_gate=gates.upgrade_done(z.LING_SPEED),
        wave1_min=20,
        wave_growth=1.25,
        focus=(FOCUS_NATURAL, FOCUS_MAIN),
    ),
    # This build needs gas all game for the upgrade path, so no pull-off gate.
    always=(c.mining(), c.gas_workers(), z.inject_larva()),
    macro_steps=(
        c.auto_supply(),
        # One queen per base for injects, plus one to spare for creep spread.
        z.train_queens(per_base=1, maximum=6, extra=1),
        z.evolution_chambers(),
        z.spore_crawlers(per_base=1, gate=gates.after_time(240.0)),
        # One Overseer per wave released so far, capped — see combat.escort_overseers.
        z.overseers(per_wave=1, maximum=1),
        c.expansions(),
        c.gas_buildings(),
        c.upgrades(),
        c.split_production(gate=gates.after_wave(1)),
        # Last: only fires when nothing above had anywhere to put a mineral
        # surplus - see the module docstring and the step's own.
        z.overflow_hatcheries(mineral_threshold=500),
    ),
)
