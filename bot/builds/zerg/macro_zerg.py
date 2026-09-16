"""Macro Zerg — 16 Hatch / 17 Pool into Roach -> Swarm Host.

Opening: `Macro Zerg` in `zerg_builds.yml` — 13 overlord, 16 hatch before
pool, 18 gas, 17 pool, 19 overlord, double queen, 4 lings, expand, overlord,
queen, speed, overlord, overlord. Gas is listed before pool (and the second
expand before its own overlord) on purpose even though the supply number is
higher: morphing a drone into a structure drops `supply_used` by 1 the
instant it's commanded (every Zerg structure eats the drone that builds
it), so triggering the higher-numbered step first lands the lower one's
threshold immediately after, with no extra drone-production gap - reversing
either pair would send the lower one first and make the higher one wait for
supply to climb back up again.

No explicit drone steps: `ConstantWorkerProductionTill` alone drives worker
production for the whole opening - mixing it with explicit drone steps in
the same early range let the two race each other for larva, delaying steps
that were listed earlier.

`ConstantWorkerProductionTill` is deliberately held below the build's real
economy target through two stages, both driven by `_phase_worker_production`
below (using the till's own current value as the phase marker - no extra
state needed) since `_produce_workers` runs every frame regardless of which
OpeningBuildOrder step is current, competing for the same mineral/larva
pool as the opening's own steps:

1. Starts at 13 (one drone above where we start). The intended sequence is
   "first ~50 minerals -> the 13th drone (a larva this opening wants
   trained anyway), next ~100 minerals -> the 2nd Overlord (a different
   larva)" - at the real target a THIRD drone kept winning the race for
   that second block too (a drone only needs 50 to the Overlord's 100),
   delaying the Overlord ~1-2s waiting for the bank to refill after that
   extra purchase. Held at 13, `_produce_workers` trains exactly the drone
   this opening already wants (supply_workers 12 -> 13) and then stops on
   its own once that drone is pending or finished - ares counts
   `supply_workers + already_pending(worker)` against the till, because
   `food_workers` alone excludes eggs and used to let a second drone slip
   in before the first hatched (patches/ares-sc2/0005).
2. Once the 2nd Overlord is confirmed, raised to 19 rather than straight
   to the real target: `19 queen *2` and `19 zergling *4` come right after
   in the opening and want the same larva/minerals continuous drone
   production would otherwise keep spending for the next ~100s+.
3. Once both Queens and all 4 Zerglings are confirmed, raised to the real
   target - nothing later in the opening needs this kind of protection.

See `zerg_builds.yml`'s own comment on this same setting.

`_claim_third_base_scout` pulls one Drone aside once supply passes 21 and
starts it walking toward the 3rd base site ahead of time, rather than
leaving the full travel time to happen only after the real "23 expand"
step becomes current (see that function's own docstring for how it stays
selectable by that later step instead of just sitting there unclaimed).

After the opening, macro steps take Roach Warren (Roach is the frontline),
then tech toward Infestation Pit (Swarm Host — cheap, passive map-control
damage from Locusts, meant to be dug in at each base rather than committed
to a fight) and, only once the enemy has actually shown air units, Spire
(Corruptor, anti-air escort). Burrow + Tunneling Claws let Roaches
burrow-regenerate and reposition between engagements without giving up the
sustain. `z.spawn_macro_army` handles the composition switch between these
phases — see its own docstring for why a static comp dict alone would stall
production in the Roach-only window.

This is a continuous macro identity, not a scripted all-in leave time:
`combat.release_waves()` releases (and grows) waves on its own repeating
size/tech gate, `combat.defend_home()` holds between waves, and Swarm
Host/Corruptor never enter that pipeline at all — see
`combat.dig_in_swarm_hosts`/`combat.escort_corruptors` and
`core.roles.SUPPORT_ROLES` for why they're kept off `army.types`. Zergling
is kept off `army.types` too, for the opposite reason: it's a dedicated
home defender that must never be swept into a wave (`combat.defend_with_
zerglings`), not an offensive unit release_waves() would otherwise collect.

The starting Overlord is sent scouting from `bot.main.on_start` rather than
through `core.roles.assign_on_created` — it exists before the game-start
event stream begins, so it never fires `on_unit_created` (see `core.roles.
assign_starting_scout`).

`_claim_natural_queen_tumor` spends the natural's Queen's starting 25
energy on a Creep Tumor before it ever injects, by parking it on
`UnitRole.QUEEN_CREEP` — the same pool `routines.creep.spread_creep`
already drives with ares' own `QueenSpreadCreep` — until a tumor is
confirmed, then handing it back to `UnitRole.QUEEN_INJECT` for normal duty.
"""

from __future__ import annotations

from ares.behaviors.macro import ExpansionController
from ares.consts import UnitRole
from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from bot.builds.definition import Army, BuildDefinition, Combat, Economy
from bot.consts import ROACH_SWARM_HOST_COMP
from bot.intel import army as intel_army
from bot.routines import combat, creep, gates, scouting
from bot.steps import common as c
from bot.steps import zerg as z

# Supply mark to pull a Drone aside and start it walking toward the 3rd base
# site - see `_claim_third_base_scout`.
_THIRD_BASE_SCOUT_AT_SUPPLY: int = 21


def _claim_third_base_scout(ctx) -> None:
    """Pull one already-mining Drone aside, once supply passes 21, to walk
    it toward the 3rd base site ahead of the real "23 expand" step - ares
    only selects and dispatches a worker once a structure step is already
    current (`ares.build_runner.build_order_runner.BuildOrderRunner.
    do_step`), so today the full walk happens entirely *after* the wait,
    not overlapped with it.

    Pulls from the *existing* mining pool via `on_step` rather than
    intercepting a freshly-trained one via `on_unit_created` (the first
    version of this) - `ConstantWorkerProductionTill` is held at 19 until
    the opening's Queens/Zerglings are confirmed (see `_phase_worker_
    production`), so no *new* Drone gets trained anywhere near supply 21;
    the first version's own trigger didn't fire until 2:33, well after
    the real expand step (2:21) - too late to help at all.

    `UnitRole.PERSISTENT_BUILDER`, not a bare move order left in
    `GATHERING`, is what keeps `Mining` (scoped to `UnitRole.GATHERING`,
    reasserting a mineral-patch order every frame) from dragging it back -
    and it's also the one role `ResourceManager.select_worker` checks
    first, ahead of the general `GATHERING` pool, so the real "23 expand"
    step picks up this exact Drone later instead of interrupting a
    different, still-mining one (see that method's own docstring: "make
    sure to change the worker role once selected, otherwise it will be
    selected to mine again"). Ares' own auto-persistent-worker assignment
    (`BuildOrderRunner._assign_persistent_worker`) is Zerg-exempt, so
    there's no built-in claimant already using this role to conflict with.

    Reuses `ExpansionController`'s own next-location lookup (terrain
    safety / not-already-taken checks included) rather than a separate
    one, so this walks to the exact same spot the real "expand" step
    would otherwise pick.
    """
    if ctx.state.third_base_scout_claimed:
        return
    if ctx.bot.supply_used < _THIRD_BASE_SCOUT_AT_SUPPLY:
        return

    location = ExpansionController(to_count=1)._get_next_expansion_location(
        ctx.bot, ctx.mediator
    )
    if location is None:
        return  # try again next frame

    miners = ctx.mediator.get_units_from_role(role=UnitRole.GATHERING).filter(
        lambda u: u.type_id == UnitTypeId.DRONE
    )
    if not miners:
        return  # try again next frame

    scout = miners.closest_to(location)
    ctx.state.third_base_scout_claimed = True
    ctx.mediator.assign_role(tag=scout.tag, role=UnitRole.PERSISTENT_BUILDER)
    scout.move(location)
    ctx.log(f"MACRO_ZERG pre-walking Drone {scout.tag} toward 3rd base at {location}")


# Radius from the natural townhall a Queen must spawn within to be claimed
# as "the natural's queen" - see `_claim_natural_queen_tumor`.
_NATURAL_QUEEN_RADIUS: float = 15.0


def _claim_natural_queen_tumor(ctx) -> None:
    """One-shot: spend the natural's Queen's starting 25 energy on a Creep
    Tumor instead of its first inject, then hand it back to normal inject
    duty.

    Reassigns the claimed Queen to `UnitRole.QUEEN_CREEP` rather than
    casting the tumor ability directly - that's the same role/pool
    `routines.creep.spread_creep` drives with ares' own `QueenSpreadCreep`
    (walk-to-a-valid-edge, then cast), and it's also what keeps
    `InjectLarva` (scoped to `UnitRole.QUEEN_INJECT`) from sweeping this
    Queen into an inject mid-walk - exactly the reason that routine gives
    for using a separate role in the first place. `spread_creep` itself
    only ever promotes a *new* creep queen when its pool is empty, so once
    this claims one, the two routines hand off cleanly with no double-claim.

    Detects "done" by tracked Creep Tumor count rather than an energy
    threshold: energy can drift above 25 while the queen is still walking to
    a valid edge, so "some tumor now exists" is the more reliable signal
    that the one-shot cast already fired.
    """
    if ctx.state.natural_queen_tumor_done:
        return

    if ctx.state.natural_queen_tag is None:
        townhalls = ctx.bot.townhalls.ready
        if len(townhalls) < 2:
            return  # natural not up yet
        # 2nd-closest to home, not `furthest_to` - once a 3rd base exists,
        # "furthest from start" stops meaning "the natural" (confirmed live:
        # after the first claimed Queen died mid-walk, the retry picked the
        # 3rd base instead once it existed).
        natural = sorted(
            townhalls, key=lambda th: th.distance_to(ctx.bot.start_location)
        )[1]
        candidates = ctx.mediator.get_units_from_role(
            role=UnitRole.QUEEN_INJECT, unit_type=UnitTypeId.QUEEN
        ).filter(
            lambda q: q.energy >= 25 and q.distance_to(natural) < _NATURAL_QUEEN_RADIUS
        )
        if not candidates:
            return  # try again next frame
        queen = candidates.closest_to(natural)
        ctx.state.natural_queen_tag = queen.tag
        ctx.mediator.assign_role(tag=queen.tag, role=UnitRole.QUEEN_CREEP)
        ctx.log(f"MACRO_ZERG natural Queen {queen.tag} pulled for opening creep tumor")
        return

    tag = ctx.state.natural_queen_tag
    still_claimed = ctx.mediator.get_units_from_role(
        role=UnitRole.QUEEN_CREEP, unit_type=UnitTypeId.QUEEN
    )
    if not any(q.tag == tag for q in still_claimed):
        # Died before ever placing the tumor - let a fresh candidate claim
        # the slot instead of leaving this latched forever.
        ctx.state.natural_queen_tag = None
        return

    # Creep Tumors are structures, not units, in this API (`bot.units(...)`
    # never matches them - confirmed live: without this the Queen just kept
    # casting indefinitely, walking the whole opening creep chain toward the
    # enemy natural instead of handing back after its first tumor).
    tumors = ctx.bot.structures(UnitTypeId.CREEPTUMORQUEEN) | ctx.bot.structures(
        UnitTypeId.CREEPTUMORBURROWED
    )
    if not tumors:
        return  # still walking to a placement spot

    ctx.state.natural_queen_tumor_done = True
    ctx.mediator.assign_role(tag=tag, role=UnitRole.QUEEN_INJECT)
    ctx.log(f"MACRO_ZERG natural Queen {tag} back on inject duty")


# Middle stage: held here (once the 2nd Overlord is confirmed) so `19 queen
# *2` / `19 zergling *4` aren't competing against continuous drone
# production for the same larva/minerals - see the module docstring.
_HOLD_FOR_QUEENS_ZERGLINGS_TILL: int = 19
# Real economy target `ConstantWorkerProductionTill` resumes to once nothing
# later in the opening needs protecting from it - see the module docstring
# and zerg_builds.yml's own comment on that setting. Matches this opening's
# own final supply value.
_WORKER_PRODUCTION_TARGET: int = 36


def _phase_worker_production(ctx) -> None:
    """Two-stage hold on `ConstantWorkerProductionTill` - see the module
    docstring for why each stage exists. Uses the till's own current value
    as the phase marker rather than separate tracked state."""
    runner = ctx.bot.build_order_runner
    till = runner.constant_worker_production_till
    if till >= _WORKER_PRODUCTION_TARGET:
        return

    if till < _HOLD_FOR_QUEENS_ZERGLINGS_TILL:
        overlords = ctx.bot.units(UnitTypeId.OVERLORD).amount + ctx.bot.already_pending(
            UnitTypeId.OVERLORD
        )
        if overlords >= 2:
            runner.constant_worker_production_till = _HOLD_FOR_QUEENS_ZERGLINGS_TILL
            ctx.log(
                "MACRO_ZERG holding worker production at "
                f"{_HOLD_FOR_QUEENS_ZERGLINGS_TILL} for Queens/Zerglings"
            )
        return

    queens = ctx.bot.units(UnitTypeId.QUEEN).amount + ctx.bot.already_pending(
        UnitTypeId.QUEEN
    )
    zerglings = ctx.bot.units(UnitTypeId.ZERGLING).amount + ctx.bot.already_pending(
        UnitTypeId.ZERGLING
    )
    if queens >= 2 and zerglings >= 4:
        runner.constant_worker_production_till = _WORKER_PRODUCTION_TARGET
        ctx.log("MACRO_ZERG resumed constant worker production after Queens/Zerglings")


def _macro_zerg_on_step(ctx) -> None:
    """`BuildDefinition` only has one `on_step` slot - all of this
    build's per-frame concerns are called from here."""
    _phase_worker_production(ctx)
    _claim_third_base_scout(ctx)
    _claim_natural_queen_tumor(ctx)


BUILD = BuildDefinition(
    name="Macro Zerg",
    label="Macro Zerg (Roach/Swarm Host)",
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
        comp=ROACH_SWARM_HOST_COMP,
        # Zergling is deliberately not in `types`: it's a dedicated home
        # defender (`core.roles.SUPPORT_ROLES`, `combat.defend_with_
        # zerglings`), never promoted to an attack wave — Roach alone is
        # the offensive component here.
        types=frozenset({UnitTypeId.ROACH}),
        upgrades=(
            UpgradeId.ZERGLINGMOVEMENTSPEED,  # from the opening, 2:45
            UpgradeId.GLIALRECONSTITUTION,  # Roach speed
            UpgradeId.BURROW,
            UpgradeId.TUNNELINGCLAWS,  # full use of burrowed Roach regen
        ),
    ),
    # Hatch (16) comes before pool (17) by design (see the opening above), so
    # the pool lands well past the ~15-20s of an immediate-pool opening; the
    # default `pool_deadline` assumes the latter, so this build states its
    # own with buffer in line with the ratio Upgrade Rush used for the same
    # "hatch before pool" shape.
    pool_deadline=110.0,
    combat=Combat(
        routines=(
            combat.release_waves(),
            combat.defend_home(),
            combat.defend_with_zerglings(),
            combat.attack_squads(),
            combat.dig_in_swarm_hosts(),
            combat.escort_corruptors(),
            creep.spread_creep(),
            creep.spread_tumors(),
            scouting.air_scout(UnitTypeId.OVERLORD),
        ),
        # Roach mobility online is the gate — no scripted leave time, waves
        # keep releasing/growing off this same gate for the rest of the game.
        wave_gate=gates.upgrade_started(UpgradeId.GLIALRECONSTITUTION),
        wave1_min=10,
        wave_growth=1.15,
        wave_stage_label="Roach Pushes",
    ),
    on_step=_macro_zerg_on_step,
    always=(
        c.mining(),
        # Don't over-mine gas once there's a buffer to spend from: pulls off
        # at 100 banked, and un-latches (resumes mining) once that's spent
        # back down - a continuous throttle, not a one-time bank-and-forget
        # (unlike a single all-in upgrade purchase, this build always has
        # something gas-hungry queued: Roach, Swarm Host, Corruptor, upgrades).
        c.gas_workers(pull_off=gates.vespene_at_least(100)),
        z.inject_larva(),
    ),
    macro_steps=(
        c.auto_supply(),
        # One queen per base for injects, plus one to spare for creep spread.
        z.train_queens(per_base=1, maximum=6, extra=1),
        c.structure(
            UnitTypeId.ROACHWARREN,
            1,
            gate=gates.upgrade_started(UpgradeId.ZERGLINGMOVEMENTSPEED),
        ),
        # TechUp morphs Lair itself along the way — no separate Lair step.
        z.tech_up(UnitTypeId.INFESTATIONPIT),
        z.tech_up(UnitTypeId.SPIRE, gate=intel_army.enemy_has_air_units),
        c.expansions(),
        c.gas_buildings(),
        c.upgrades(),
        c.split_production(gate=gates.after_wave(1)),
        z.spawn_macro_army(),
        # One per base, mineral-line placement — static anti-air so Mutalisk/
        # air harass can't freely pick off drones while the army is Roach-
        # heavy (no native anti-air until Corruptor/Spire comes online).
        z.spore_crawlers(per_base=1, gate=gates.after_time(240.0)),
        # Last: only fires when nothing above had anywhere to put a mineral
        # surplus - see the module docstring and the step's own.
        z.overflow_hatcheries(mineral_threshold=500),
    ),
)
