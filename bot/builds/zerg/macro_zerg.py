"""Macro Zerg — 16 Hatch / 17 Pool into Roach -> Swarm Host.

Opening: `Macro Zerg` in `zerg_builds.yml` — 12 overlord, 16 hatch before
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

`ConstantWorkerProductionTill` is deliberately held at 12 (effectively off)
until `_resume_worker_production_after_overlord2` below confirms the very
first step (12 overlord) has been queued: `_produce_workers` runs every
frame regardless of which step is current, and at the build's real economy
target it was racing that first Overlord for the same minerals - a drone
only needs 50 to the Overlord's 100, so it reliably won every contested
bank and delayed the Overlord ~1-2s waiting for the bank to refill after a
drone purchase. The step is declared "12", not "13": at 13 it would need a
13th drone trained first just to become eligible - the exact same
competing purchase this is trying to avoid. See `zerg_builds.yml`'s own
comment on this same pair of settings.

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
`core.roles.SUPPORT_ROLES` for why they're kept off `army.types`.
"""

from __future__ import annotations

from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from bot.builds.definition import Army, BuildDefinition, Combat, Economy
from bot.consts import ROACH_SWARM_HOST_COMP
from bot.intel import army as intel_army
from bot.routines import combat, creep, gates, scouting
from bot.steps import common as c
from bot.steps import zerg as z

# Real economy target `ConstantWorkerProductionTill` resumes to, once the
# opening's first step (the 2nd Overlord) no longer needs protecting from
# it - see the module docstring and zerg_builds.yml's own comment on that
# setting. Matches this opening's own final supply value.
_WORKER_PRODUCTION_TARGET: int = 36


def _resume_worker_production_after_overlord2(ctx) -> None:
    runner = ctx.bot.build_order_runner
    if runner.constant_worker_production_till >= _WORKER_PRODUCTION_TARGET:
        return
    overlords = ctx.bot.units(UnitTypeId.OVERLORD).amount + ctx.bot.already_pending(
        UnitTypeId.OVERLORD
    )
    if overlords >= 2:
        runner.constant_worker_production_till = _WORKER_PRODUCTION_TARGET
        ctx.log("MACRO_ZERG resumed constant worker production after 2nd Overlord")


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
        types=frozenset({UnitTypeId.ZERGLING, UnitTypeId.ROACH}),
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
    on_step=_resume_worker_production_after_overlord2,
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
        # Last: only fires when nothing above had anywhere to put a mineral
        # surplus - see the module docstring and the step's own.
        z.overflow_hatcheries(mineral_threshold=500),
    ),
)
