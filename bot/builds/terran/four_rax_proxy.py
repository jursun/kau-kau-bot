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
production caps for good at 14 — the starting 12 plus the two "SCV" line
items in the build order above (step 1, which becomes `Z`, and step 6,
which stays on minerals) — so nothing pulls resources away from Marines
past that point. Step 6's SCV doesn't train the moment step 1's does,
though: it waits for the 3rd Barracks (Barracks C, Z's own second task) to
have started construction first, matching the build order's own placement
of "6. SCV" after "5. 13 Barracks C". The first five Marines muster and
leave together with whichever crew SCVs have finished their tasks by then;
every Marine after that streams to the front individually the moment it's
trained — one wait, then none.

Combat micro, once a wave is out: Marines kite (`MARINE_MIN_ENGAGE_RANGE`)
rather than trade in melee range; crew SCVs that finish their tasks join
the push and attack alongside the Marines (see `combat.builder_workers_
attack`'s docstring). Every attacking unit favors enemy units over enemy
structures when picking a target (`combat._prioritize_enemies`).

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

# A Marine's attack range is 5 - kiting at this distance keeps a 2-tile
# buffer, backing off anything that closes inside it rather than trading in
# melee range. Passed to `combat.attack_squads`, which drives this per unit
# (`combat._kite_maneuver`) instead of `StutterGroupForward`'s unconditional
# group trade - see that routine's own docstring for the mechanism.
MARINE_MIN_ENGAGE_RANGE = 3.0


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


def ramp_location(ctx) -> Point2:
    """Biases Z's home Depot toward the main ramp rather than wherever ares'
    placement solver would otherwise pick around the main.

    `ai.main_base_ramp.top_center` is python-sc2's own centroid of the
    ramp's upper tiles - the point at the top of the ramp, on the main's
    side - passed through as `WorkerTask.closest_to` on Z's first task,
    which `steps.terran._drive_crew_member` forwards to `request_building_
    placement`'s own `closest_to`. This only orders the candidates within
    `home_location`'s precalculated formation; it doesn't request a wall-off
    placement or any particular tile, just "closest to the ramp" among the
    spots already available.
    """
    return ctx.bot.main_base_ramp.top_center


def _marine_training_started(ctx) -> bool:
    """Gate for Barracks D (X's second task): build order step 8 wants this
    *after* step 7 ("Marine"), not merely after Barracks A. Barracks A alone
    would satisfy `gates.has_structure(BARRACKS)` immediately on completion,
    which is too early — the build order calls for Marine production to have
    actually begun first."""
    return gates.training_started(UnitTypeId.MARINE)(ctx)


def _third_barracks_started(ctx) -> bool:
    """Gate for the build order's second "SCV" line (step 6): wants this
    *after* step 5 ("13 Barracks C" - the 3rd Barracks overall), not the
    moment step 1's SCV (`Z`) is claimed. `structure_started` counts ready
    + pending the way ares itself does (ARCHITECTURE.md gotcha 8), so this
    goes true the instant Barracks C's construction begins, not once it
    completes."""
    return gates.structure_started(UnitTypeId.BARRACKS, 3)(ctx)


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
        WorkerTask(
            UnitTypeId.SUPPLYDEPOT,
            proxy_location,
            label="Depot (proxy)",
            # Redundancy for a bad-placement race: this is the 2nd Depot
            # overall (Z's home one is the 1st), so once both are ready or
            # pending the task is genuinely done. Without this, a placement
            # the game silently rejects can make Y's worker leave the
            # building tracker without ever having built anything, and
            # `_drive_crew_member` would move on believing it had - see
            # `WorkerTask.verify`.
            verify=gates.structure_started(UnitTypeId.SUPPLYDEPOT, 2),
        ),
    ),
    z_tasks=(
        WorkerTask(
            UnitTypeId.SUPPLYDEPOT,
            home_location,
            closest_to=ramp_location,
            label="Depot (home)",
        ),
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
            combat.attack_squads(min_engage_range=MARINE_MIN_ENGAGE_RANGE),
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
        # This build's own Validation Report title for Stage 4 - it really
        # is an all-in (no expansion, no gas, one fixed structure count),
        # unlike the generic "Attack Waves" default every other build keeps.
        wave_stage_label="All-In Attack",
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
        # No `c.auto_supply()` here, deliberately. `PROXY_CREW` already
        # places exactly two Depots (Z's home Depot, Y's proxy Depot) - the
        # nine-step build order names precisely that many and no more. ares'
        # generic `AutoSupply` doesn't know that: its own trigger fires once
        # `supply_left <= 2` with zero production structures up yet, which
        # tripped the instant the opening queued the 13th/14th SCV - well
        # before Z had even spawned to build the Depot itself - and pulled a
        # *different*, still-mining SCV to do it instead. That's the "SCV
        # ahead of Z" bug: a second, generic supply mechanism racing the
        # crew's own explicit one for the exact same structure. Once
        # production structures exist, `AutoSupply` also scales its target
        # Depot count up with them rather than stopping at two, which
        # conflicts with this build's fixed structure count on principle,
        # not just at the one collision that was actually observed - so this
        # is left out rather than merely gated around the early window. If a
        # crew member dies before placing its Depot there is no automatic
        # replacement, consistent with every other crew task already having
        # none if its worker dies (see `_drive_crew_member`'s dead-worker
        # branch). See `terran_builds.yml` for the matching removal of
        # `AutoSupplyAtSupply`, ares' other generic supply mechanism.
        #
        # Marines outrank SCVs every frame. A `MacroPlan` stops at the first
        # step that acts, so SCVs are only built on frames where every ready
        # Barracks is already busy — which is exactly "squeeze out more SCVs
        # when resources allow".
        c.spawn_army(),
        # Gated on the 3rd Barracks (Barracks C) having started, matching
        # build order step 6's own placement after step 5. Without this gate
        # nothing would stop the 14th SCV from training the moment worker
        # count allowed it, regardless of build order — see terran_builds.
        # yml's comment for the matching change there, since ares' own
        # `ConstantWorkerProductionTill` has no way to express a gate like
        # this one and had to be capped at 13 (Z only) instead.
        c.build_workers(gate=_third_barracks_started),
    ),
)
