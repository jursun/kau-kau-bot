"""Four Rax Proxy — a Marine all-in built in the enemy's back yard.

Opening: three SCVs never gather a single mineral. Two of the starting 12
(`X`, `Y`) peel off for the enemy's fourth base the instant the game starts;
the 13th SCV trained (`Z`) builds a Supply Depot at home, then follows them.
Between them these three place every structure this build makes - two
Depots, four Barracks - entirely outside ares' generic worker selection. See
`bot.steps.terran.proxy_crew` for the mechanism and `PROXY_CREW` below for
the exact task lists.

    1. SCV
    2. 13 Depot                     (Z, at home)
    3. 13 Barracks A                (X, at the proxy)
    4. 13 Barracks B                (Y, at the proxy)
    5. 13 Barracks C                (Z, at the proxy, after its Depot)
    6. SCV
    7. Marine
    8. 15 Barracks D                (X, at the proxy, after Barracks A -
                                      but not before the first Marine has
                                      started training)
    9. 16 Depot                     (Y, at the proxy, after Barracks B)

After the opening: all four Barracks never stop making Marines. Worker
production stops for good at 14 — the starting 12 plus the two "SCV" line
items in the build order above (step 1, which becomes `Z`, and step 6,
which stays on minerals) — so nothing pulls resources away from Marines
past that point. The first five Marines muster and leave together with
whichever crew SCVs have finished their tasks by then; every Marine after
that streams to the front individually the moment it's trained — one wait,
then none.

Not yet validated in-game.
"""

from __future__ import annotations

from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.builds.definition import (
    Army,
    BuildDefinition,
    Combat,
    Economy,
    ProxyCrewPlan,
    WorkerTask,
)
from bot.consts import FOCUS_MAIN, FOCUS_NATURAL, MASS_MARINE_COMP
from bot.routines import combat, gates, targeting
from bot.steps import common as c
from bot.steps import terran as t

# Two go down as each of X/Y/Z's first task; the other two are their second.
PROXY_BARRACKS = 4

# The build order trains exactly two SCVs beyond the starting 12 (step 1,
# which becomes Z, and step 6) and not one more — this is an all-in, not an
# economy build, and every mineral past this point goes to Marines. No
# expansion, no gas either — nothing this build makes costs gas, so a
# Refinery would be 75 minerals set on fire. Counts all 14 SCVs the Command
# Center ever trains, X/Y/Z included — they just never mine any of it.
WORKER_TARGET = 14

# Five Marines is two Barracks' worth of production and the point at which
# a VeryHard opening has nothing that beats them. There is exactly one
# muster: `combat.release_first_wave_then_stream()` waits for this many,
# sends them together, and every Marine after that streams to the front
# individually, with no further waiting and no wave-growth math.
FIRST_WAVE = 5


def proxy_location(ctx) -> Point2:
    """Where every crew structure goes, and where Marines muster.

    The enemy's fourth base — deep enough that it usually goes unseen by
    early scouting, at the cost of a longer opening walk than a nearer proxy
    spot would need. (An earlier round of this build used the enemy's
    *third* instead; this is a deliberate change, not a typo — see
    `claude/four-rax-proxy.md` for the history.)

    Its own named function rather than an inline lambda because several
    separate places have to agree on it: every task in `PROXY_CREW` below,
    `combat.rally`, and `builder_workers_attack`'s claim radius. A proxy
    build where those drift apart is a build that rallies its army to the
    wrong side of the map.
    """
    return targeting.enemy_fourth(ctx)


def home_location(ctx) -> Point2:
    """Where Z's first task (the opening Depot) goes — our own main, not the
    proxy. Z only heads for `proxy_location` on its *second* task."""
    return ctx.production_location


def _marine_training_started(ctx) -> bool:
    """Gate for Barracks D (X's second task): build order step 8 wants this
    *after* step 7 ("Marine"), not merely after Barracks A. Barracks A alone
    would satisfy `gates.has_structure(BARRACKS)` immediately on completion,
    which is too early — the build order calls for Marine production to have
    actually begun first."""
    return gates.training_started(UnitTypeId.MARINE)(ctx)


# The nine-step opening, expressed as one task list per crew worker. See
# `bot.steps.terran.proxy_crew` for how these are actually worked, and
# `bot.builds.definition.ProxyCrewPlan` for why this is the single source of
# truth both the build and the validator read.
PROXY_CREW = ProxyCrewPlan(
    x_tasks=(
        WorkerTask(UnitTypeId.BARRACKS, proxy_location, label="Barracks A"),
        WorkerTask(
            UnitTypeId.BARRACKS,
            proxy_location,
            gate=_marine_training_started,
            label="Barracks D",
        ),
    ),
    y_tasks=(
        WorkerTask(UnitTypeId.BARRACKS, proxy_location, label="Barracks B"),
        WorkerTask(UnitTypeId.SUPPLYDEPOT, proxy_location, label="Depot (proxy)"),
    ),
    z_tasks=(
        WorkerTask(UnitTypeId.SUPPLYDEPOT, home_location, label="Depot (home)"),
        WorkerTask(UnitTypeId.BARRACKS, proxy_location, label="Barracks C"),
    ),
)


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
            # Every crew worker that has worked through its own task list
            # joins the push once all four Barracks are accounted for. This
            # doesn't know or care which of X/Y/Z it's claiming — see its
            # own docstring for why "near the proxy, not in the building
            # tracker, not mid-construction" is a safe stand-in for "done".
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
    crew=PROXY_CREW,
    on_unit_created=t.claim_z_on_first_scv(),
    always=(
        c.mining(),
        # X and Y peel off here on frame one; Z, once claimed, is driven by
        # this same step. See `steps.terran.proxy_crew`'s docstring for why
        # this has to run every frame rather than as a gated macro step.
        t.proxy_crew(),
    ),
    macro_steps=(
        # Supply first: a Barracks that cannot make Marines is the one way
        # this build loses to itself. This is a safety net on top of the two
        # Depots `PROXY_CREW` places explicitly — it only ever acts if those
        # two turn out not to be enough.
        c.auto_supply(),
        # Marines outrank SCVs every frame. A `MacroPlan` stops at the first
        # step that acts, so SCVs are only built on frames where every ready
        # Barracks is already busy — which is exactly "squeeze out more SCVs
        # when resources allow".
        c.spawn_army(),
        c.build_workers(),
    ),
)
