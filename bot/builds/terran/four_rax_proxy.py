"""Four Rax Proxy — a Marine all-in built in the enemy's back yard.

Opening: `Four Rax Proxy` in `terran_builds.yml` — SCV, Supply Depot, SCV,
then two SCVs walk to the enemy third and put down the first two Barracks.

After the opening: Barracks three and four go up at the same proxy as their
triggers fire, all four never stop making Marines, SCVs trickle up to 22 on
whatever minerals the Barracks leave behind. The first five Marines muster
and leave together with the builder SCVs in tow; every Marine after that
streams to the front individually the moment it's trained — one wait, then
none.

Not yet validated in-game.
"""

from __future__ import annotations

from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.builds.definition import Army, BuildDefinition, Combat, Economy
from bot.consts import FOCUS_MAIN, FOCUS_NATURAL, MASS_MARINE_COMP
from bot.routines import combat, gates, targeting
from bot.steps import common as c
from bot.steps import terran as t

# Two go down in the opening; the other two are gated macro steps below.
PROXY_BARRACKS = 4

# Marines are what wins or loses this; SCVs are whatever is left over.
# One base's worth of saturation, no expansion, no gas — nothing this build
# makes costs gas, so a Refinery would be 75 minerals set on fire.
WORKER_TARGET = 22

# Five Marines is two Barracks' worth of production and the point at which
# a VeryHard opening has nothing that beats them. There is exactly one
# muster: `combat.release_first_wave_then_stream()` waits for this many,
# sends them together, and every Marine after that streams to the front
# individually, with no further waiting and no wave-growth math.
FIRST_WAVE = 5


def proxy_location(ctx) -> Point2:
    """Where all four Barracks go, and where Marines muster.

    The enemy third: far enough off the enemy's opening scouting path to
    usually go unseen, close enough that a fresh Marine's walk to the fight
    is a few seconds rather than half a minute.

    Its own named function rather than an inline lambda because four
    separate places have to agree on it - both `proxy_barracks` steps,
    `combat.rally`, and `builder_workers_attack`'s claim radius. A proxy
    build where those drift apart is a build that rallies its army to the
    wrong side of the map.

    Note this is NOT also the opening's proxy: `terran_builds.yml` names
    `enemy_third` itself, because ares' build runner resolves its own
    targets from a keyword. Those two have to be changed together.
    """
    return targeting.enemy_third(ctx)


BUILD = BuildDefinition(
    name="Four Rax Proxy",
    label="Four Rax Proxy",
    race=Race.Terran,
    economy=Economy(
        worker_target=WORKER_TARGET,
        workers_per_base=WORKER_TARGET,
        max_bases=1,  # main only; every mineral goes into Marines
        gas_per_base=0,
        max_gas=0,
        long_distance_mine=False,
    ),
    army=Army(
        comp=MASS_MARINE_COMP,
        types=frozenset({UnitTypeId.MARINE}),
        upgrades=(),  # no gas, so nothing to research
    ),
    combat=Combat(
        routines=(
            # One muster for the first five, then every later Marine streams
            # to the front on its own — no more waves, no more waiting. See
            # `combat.release_first_wave_then_stream`'s docstring for why
            # omitting `RunState.mustering_tags` after wave 1 is what makes
            # that happen: `attack_squads()` below reads it directly.
            combat.release_first_wave_then_stream(),
            combat.defend_home(),
            combat.attack_squads(),
            # Steps 9 and 10 of the build order: the SCVs that finished the
            # later Barracks join the push. Gated on all four Barracks being
            # accounted for, so a builder is never claimed out from under the
            # next Barracks that still needs building — see the routine.
            combat.builder_workers_attack(
                proxy_location,
                claim_gate=gates.structure_started(UnitTypeId.BARRACKS, PROXY_BARRACKS),
            ),
        ),
        wave_gate=gates.always,  # no tech to wait on; size is the only gate
        wave1_min=FIRST_WAVE,
        # Marines spawn at the proxy, not at home. Without this they would
        # walk back to our own natural to muster before every attack.
        rally=proxy_location,
        focus=(FOCUS_MAIN, FOCUS_NATURAL),
    ),
    always=(c.mining(),),
    macro_steps=(
        # Supply first: a Barracks that cannot make Marines is the one way
        # this build loses to itself.
        c.auto_supply(),
        # Barracks three and four, each on the trigger from the build order:
        # three once the Supply Depot is up (its SCV is then the closest
        # gathering worker to the proxy of anyone at home), four once the
        # first Barracks is done (that Barracks' own builder is standing
        # right there). `to_count` counts ready + pending, so each step
        # stops wanting anything the moment its Barracks is under way.
        t.proxy_barracks(
            3, proxy_location, gate=gates.has_structure(UnitTypeId.SUPPLYDEPOT)
        ),
        t.proxy_barracks(
            PROXY_BARRACKS,
            proxy_location,
            gate=gates.has_structure(UnitTypeId.BARRACKS),
        ),
        # Marines outrank SCVs every frame. A `MacroPlan` stops at the first
        # step that acts, so SCVs are only built on frames where all four
        # Barracks are already busy — which is exactly "squeeze out more
        # SCVs when resources allow".
        c.spawn_army(),
        c.build_workers(),
    ),
)
