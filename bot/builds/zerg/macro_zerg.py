"""Macro Zerg — Roach -> Swarm Host, scripted opening lives entirely here.

`zerg_builds.yml`'s `OpeningBuildOrder` is deliberately empty: the
BuildOrderRunner's YAML step DSL (`ConstantWorkerProductionTill` racing the
opening's own steps for minerals, `_produce_workers` competing with
whichever step was current, the supply-drop-on-morph step-ordering tricks a
sequential DSL forced) was a repeated source of subtle timing bugs across
this build's history. An empty `OpeningBuildOrder` makes
`build_order_runner.build_completed` true from frame 0, so `macro_steps`
below run from the very first frame - this module owns every production
timing itself, directly in Python, with no ares-owned opening state to
fight.

The opening is expressed as a hand-tuned supply/time build order (not
generated or auto-derived), driven by two independent mechanisms:

- `_scripted_production` walks `_SEQUENCE`, a strict single-file list -
  exactly one "current" entry at a time, matching ares' own
  `BuildOrderRunner.build_step` model. An entry only becomes eligible once
  every entry ahead of it has already been *commanded* (not finished),
  never merely once its own supply gate has opened - see `_SEQUENCE`'s own
  comment for why an earlier, per-kind-independent version live-tested
  badly (cheaper later purchases racing ahead of a pricier earlier one).
- `_scripted_worker_production` ("Auto Worker" in the build order) trains
  Drones only inside `_AUTO_WORKER_WINDOWS` - a [start supply, next entry's
  supply) range per window. Two of the listed windows are zero-width by
  construction (the next step's own supply equals the window's start) -
  not a bug, just the build order using "Auto Worker" purely as a
  placeholder while economy banks up for an expensive purchase with no
  further supply climb expected. Listed after `_scripted_production` in
  `macro_steps`, so the current scripted step always gets first claim on a
  frame's minerals - the same "current step first" priority the old YAML
  opening gave `_produce_workers`.

Everything the scripted opening also handles generically once the opening
is done (`c.expansions`, `c.upgrades`) is re-gated so the generic version
only ever continues growth *past* what the script already established
(e.g. 4th/5th base, Glial/Burrow/Tunneling Claws) rather than racing the
script for the same purchase. `z.train_queens` is the one exception - it's
always on rather than scripted or re-gated: 1 Queen per ready townhall
plus 1 spare for creep spread, for the whole game, not just the opening's
first few bases (see its own comment in `macro_steps`).
`z.spore_crawlers` is the same shape (1 per owned base in the mineral
line), opening at 4:30 with a 15s missing-base recheck.
`z.tech_up(INFESTATIONPIT)` waits until Tunneling Claws has started
(Glial/Burrow sit ahead of Claws on the upgrade list, so that also
keeps Pit from sniping the Glial gas bank) and sits below the upgrade
steps in `macro_steps`. `z.tech_up(SPIRE)` stays Lair-gated + air-scout
only; both Pit and Spire place via `BuildZergStructure` (not ares
`BuildStructure`/`request_zerg_placement`, which stuck a Spire Drone in
main while Voidrays were on the map). Lair itself stays `_SEQUENCE`-
owned — without those gates, `TechUp` would happily morph Lair itself
the moment it's economically able to.
`_scripted_gas_scaling` replaces `c.gas_buildings()` outright rather than
just being re-gated: after 5:00 it grows the gas target by 1 every 20s
until capped at 8 (`economy.max_gas` is raised to match, so the Stage 1
"Extractor Cap Respected" validator check stays meaningful). New Extractors
prefer main → natural → 3rd → 4th → … (`ZergGasBuildingController`).

`c.auto_supply()` is also re-gated, on `_scripted_overlords_exhausted`
rather than left unconditional - see that function's own comment for why
(ares' own `AutoSupply` reacts to a more cautious supply-headroom
threshold than this build's hand-tuned Overlord pacing needs, so an
unconditional safety net raced `_SEQUENCE`'s own next Overlord entry for
the same larva).

`c.split_production` is wrapped in `_split_production_after_opening`
rather than called with `gate=` directly - its own `gate` parameter only
reorders `BuildWorkers`/`SpawnController` priority, it doesn't stop either
from running before `gate` passes (that's its documented, tested
contract - see its own comment). Called directly, its embedded
`BuildWorkers(ctx.worker_target)` ran from frame 0, racing `_SEQUENCE`'s
own worker/overlord/zergling entries for the same minerals the whole
opening - confirmed live as the actual cause of the Overlord milestone
consistently missing its 12s deadline by ~3s (a 14th Drone, chasing
`ctx.worker_target`, trained ahead of it every time), not - as first
suspected - some unavoidable drone-morph-time floor.

`z.spawn_macro_army()` (actual Roach/Swarm Host production) is gated on
Roach Warren existing - NOT redundant with the tech-readiness check it
already does internally. That reasoning holds for Roach/Swarm Host
themselves, but `SpawnController`'s "only one tech-ready unit in the
comp" escape hatch fires for *Zergling* the moment Spawning Pool exists,
long before Roach Warren - confirmed live as a real bug: with nothing
else claiming the frame in the gaps between scripted supply gates, this
alone raced supply from 17 to 25 in a single frame, leaving `_SEQUENCE`'s
own "zergling" entries never commanded (their targets already satisfied
by the ungoverned overproduce) and starving the 19/21 Queens of the
minerals meant for them first.

The "additional timings" the build order lists alongside the main sequence
(gas worker counts, Queen inject/tumor, Zergling defend-base) aren't new
mechanisms - they're the expected, already-verified behavior of existing
machinery once this scripted pacing drives the game: `c.gas_workers`'s
`Economy(workers_per_gas=3)` plus a one-shot early `vespene≥100` pull-off
that ends the moment Metabolic Boost is commanded (opening Speed bank
only — never a late-game throttle), `InjectLarva`,
`_claim_natural_queen_tumor` below, and `combat.defend_with_zerglings` /
`core.roles.SUPPORT_ROLES` respectively. Live-verify against them rather
than re-implementing anything for them.

`_claim_natural_scout` and `_claim_natural_queen_tumor` below predate
this rewrite but are unaffected by it: both key off live game state
(supply, townhall count, Queen energy/position), not build-order position,
so they keep working unchanged under the new scripted opening.
`_claim_natural_scout` pre-walks a Drone to the natural at 0:36, well
ahead of `_SEQUENCE`'s own "expand" entry actually landing (~50-55s in
practice). The 3rd-base pre-walk used to exist too; it was removed once
overflow/expansions racing the scripted 3rd was fixed - an early pull at
2:00 just parked a second Drone at the site while something else built.
The natural pre-walk relies on `_step_behavior`'s "expand" kind using
`ExpandWithPersistentBuilder`, not plain `ExpansionController`, to
actually pick up the pre-walked Drone - see that behavior's own
docstring for why (`ExpansionController.execute()` never looks at
`UnitRole.PERSISTENT_BUILDER` at all, so without this it always sent a
*second* Drone from the mineral line, the two colliding at the site -
confirmed live for the natural).

After the opening, Glial → Burrow → Tunneling Claws claim gas before
Infestation Pit (Swarm Host — cheap, passive map-control damage from
Locusts, meant to be dug in at each base rather than committed to a
fight). Spire (Corruptor escort) still waits until the enemy has shown
air. Once Lair is commanded, `_reserve_upgrade_bank` always gets first
look at the frame's spend, ahead of both `SpawnController`s below it,
so the next upgrade's bank actually accumulates instead of leaking to
army production a few gas at a time - see its own docstring for the
live-confirmed bug this replaces. Concurrent-upgrade slot count and
army-vs-tech spend priority are supply-based (`intel.army.
army_behind_on_supply`): behind on army supply → more units / fewer
concurrent upgrades and army wins ties; ahead or even → tech focus.
`z.spawn_macro_army` handles the composition switch between phases —
see its own docstring for why a static comp dict alone would stall
production in the Roach-only window. Counter comps from scouting are a
placeholder; baseline is Roach + Swarm Host (+ Corruptor on air).

This is a continuous macro identity, not a scripted all-in leave time:
once army supply hits 40, `combat.release_first_wave_then_stream` puts
every DEFENDING army unit on ATTACKING and streams new ones forever after.
`combat.defend_home()` holds before that, and Swarm Host/Corruptor never
enter that pipeline at all — see `combat.dig_in_swarm_hosts`/
`combat.escort_corruptors` and `core.roles.SUPPORT_ROLES`. Home Zerglings
stay on `ZERGLING_DEFENDER_ROLE` (`combat.defend_with_zerglings`); extras
join the attack wave. Once Burrow is done, hurt Roaches (<25% HP) dig in
via `combat.regen_burrow_roaches` until fully healed, then unburrow. With
Tunneling Claws they also retreat toward home on the influence grid while
burrowed so they heal at a safe distance.

The starting Overlord is sent scouting from `bot.main.on_start` rather than
through `core.roles.assign_on_created` — it exists before the game-start
event stream begins, so it never fires `on_unit_created` (see `core.roles.
assign_starting_scout`).

`_claim_natural_queen_tumor` spends the natural's Queen's starting 25
energy on a Creep Tumor before it ever injects, by parking it on
`UnitRole.QUEEN_CREEP` — the same pool `routines.creep.spread_creep`
already drives with ares' own `QueenSpreadCreep` — until a tumor is
confirmed, then handing it back to `UnitRole.QUEEN_INJECT` for normal duty.
`_claim_main_queen_tumor` pulls the 3rd Queen (`z.train_queens`' "+1
extra") and *directly* plants two tumors on the main high-ground rim
(vision + full creep coverage) — it does not use `QueenSpreadCreep`, which
paths toward the enemy natural when map coverage is low and walked the
Queen outside the natural instead (confirmed live).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from math import cos, floor, pi, sin

from ares.behaviors.macro import (
    BuildStructure,
    BuildWorkers,
    ExpansionController,
    MacroPlan,
    UpgradeController,
)
from ares.consts import UnitRole
from cython_extensions import cy_distance_to_squared, cy_towards
from cython_extensions.general_utils import cy_has_creep
from sc2.data import Race
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2

from bot.behaviors.zerg import (
    ExpandWithPersistentBuilder,
    MorphLairAtMain,
    TrainFromLarva,
    UpgradeSlots,
    ZergGasBuildingController,
    pending_larva_trained,
)
from bot.builds.definition import Army, BuildDefinition, Combat, Economy
from bot.consts import HOME_ZERGLING_CAP, ROACH_SWARM_HOST_COMP, ZERGLING_DEFENDER_ROLE
from bot.intel import army as intel_army
from bot.routines import combat, creep, gates, overseers as overseer_routines, scouting
from bot.steps import common as c
from bot.steps import zerg as z

# ── Scripted opening ─────────────────────────────────────────────────────
# `_SEQUENCE` below is a strict, single-file walk - exactly one "current"
# entry at a time, matching ares' own BuildOrderRunner.build_step model
# (do_step only ever touches self.build_order[self.build_step], and a
# structure step's own end_condition is "build_progress just ticked above
# 0", i.e. "commanded", not "finished" - see
# ares.build_runner.build_order_parser's EXPAND/GAS entries). An entry
# only becomes eligible once EVERY entry ahead of it (in the exact order
# below, matching the build order the user provided) has already been
# commanded - not merely once its own supply gate has opened. A first,
# simpler version gated every entry independently on its own supply
# threshold, and live-tested badly: Gas (25 minerals) and Spawning Pool
# (200) both completed before Natural Expand (300) even started, because
# nothing was holding the mineral bank for the pricier, earlier-listed
# purchase while cheaper, later-listed ones were already affordable. This
# sequential lock is what actually protects that ordering, the same way it
# protected it for the old YAML opening.
#
# Each entry is (supply gate, kind, target). `target` is a cumulative
# count for that *kind* as of this entry (e.g. the two "36 Overlord" rows
# become two consecutive Overlord entries with target 5 then 6) - not a
# per-entry amount.


@dataclass(frozen=True)
class _Step:
    supply: int
    kind: str
    target: int = 0


# Supply 19-30 is a hand-ordered interleave, per an explicit request:
# Auto Worker stops at 21, not 19 (see `_AUTO_WORKER_WINDOWS`'s (4, 21)
# entry) - two Drones squeezed in at 19/20 first, then everything that
# used to start at 19 shifts up 2 to make room: two always-on Queens (21,
# 23 - not scripted here at all, see the module docstring on `z.
# train_queens`), the two Zergling commands (25/26), then the 3rd Expand
# (must land near 2:17 - confirmed live: leaving it after the worker
# interleave delayed hatch #3 to ~170s / Speed to the same beat; then
# gate 29 soft-locked forever at supply 27 = 21 drones + 2 queens + 4
# lings, because Auto Worker only opens after idx > expand and nothing
# else spends larva). Gate is 27 to match that live supply so the hatch
# can start as soon as minerals hit 300 (~2:15). Workers and the 3rd
# Overlord fill behind the expand; Metabolic Boost sits after that short
# fill so it can still hit ~2:45 rather than waiting on drones 23/24.
# The Overlord@26 entry stays at supply 26 on purpose - by list order it
# only becomes current after worker@22, so it fires as soon as that drone
# is issued (supply already past 26) rather than waiting on its own gate.
# Worker targets (22, 23, 24) assume the (4, 21) Auto Worker window
# settles at ~21 Drones when it closes.
_SEQUENCE: tuple[_Step, ...] = (
    _Step(12, "worker", 13),
    _Step(13, "overlord", 2),
    _Step(16, "expand", 2),
    _Step(18, "gas", 1),
    _Step(17, "spawning_pool"),
    _Step(25, "zergling", 2),  # 1st command (2 lings) - was 23
    _Step(26, "zergling", 4),  # 2nd command - was 24
    _Step(27, "expand", 3),  # 3rd base - gate 27 matches post-ling supply
    _Step(27, "worker", 22),
    _Step(26, "overlord", 3),  # deliberately NOT shifted - see comment above
    _Step(30, "metabolic_boost"),
    _Step(29, "worker", 23),
    _Step(30, "worker", 24),
    _Step(33, "overlord", 4),
    _Step(36, "overlord", 5),
    _Step(36, "overlord", 6),
    _Step(44, "roach_warren"),
    _Step(55, "gas", 2),
    _Step(54, "zergling", 6),  # 3rd of "Zerglings *3"
    _Step(54, "zergling", 8),  # 4th
    _Step(54, "zergling", 10),  # 5th
    _Step(57, "lair"),
)

# Index of the scripted 3rd-base expand - `c.expansions` must not run until
# this entry has been issued, or it races the opening the moment supply
# hits 27 (confirmed live: ExpansionController started hatch #3 at 129.6s
# while `_SEQUENCE` was still on worker@22, supply-locking drone production
# until the hatch finished ~201s and delaying Metabolic Boost by ~35s).
_THIRD_EXPAND_INDEX: int = next(
    i for i, s in enumerate(_SEQUENCE) if s.kind == "expand" and s.target == 3
)


def _step_issued(ctx, step: "_Step") -> bool:
    """True once `step` has been commanded - matches ares' own "build_progress
    just ticked above 0" / "training order issued" completion signal, not
    full completion. Zerg structures morph directly from a Drone, so they
    already show up in `structures(...)` the instant they're commanded."""
    bot = ctx.bot
    if step.kind == "worker":
        have = bot.units(UnitTypeId.DRONE).amount + bot.already_pending(
            UnitTypeId.DRONE
        )
        return have >= step.target
    if step.kind == "overlord":
        have = bot.units(UnitTypeId.OVERLORD).amount + pending_larva_trained(
            bot, UnitTypeId.OVERLORD
        )
        return have >= step.target
    if step.kind == "expand":
        return bot.structures(UnitTypeId.HATCHERY).amount >= step.target
    if step.kind == "gas":
        return bot.structures(UnitTypeId.EXTRACTOR).amount >= step.target
    if step.kind == "spawning_pool":
        return bot.structures(UnitTypeId.SPAWNINGPOOL).amount >= 1
    if step.kind == "zergling":
        # `pending_larva_trained` accounts for Zergling's 2-per-Egg yield -
        # see its own comment for the exact over-production bug plain
        # `already_pending` caused here.
        have = bot.units(UnitTypeId.ZERGLING).amount + pending_larva_trained(
            bot, UnitTypeId.ZERGLING
        )
        return have >= step.target
    if step.kind == "metabolic_boost":
        return bool(bot.pending_or_complete_upgrade(UpgradeId.ZERGLINGMOVEMENTSPEED))
    if step.kind == "roach_warren":
        return bot.structures(UnitTypeId.ROACHWARREN).amount >= 1
    if step.kind == "lair":
        # Unlike every other structure here, Lair is an *upgrade* of an
        # existing Hatchery (UPGRADETOLAIR_LAIR cast on it), not a fresh
        # Drone-morph - `structures(LAIR)` stays 0 until the morph
        # finishes (~57s). Prefer the live townhall order over bare
        # `already_pending`: confirmed live, a one-frame pending blip
        # advanced the opening past Lair while the morph hadn't stuck,
        # and nothing retried until UpgradeController reached
        # TunnelingClaws ~24s later.
        if (
            bot.structures(UnitTypeId.LAIR).amount
            + bot.structures(UnitTypeId.HIVE).amount
            >= 1
        ):
            return True
        return any(
            any(o.ability.id == AbilityId.UPGRADETOLAIR_LAIR for o in th.orders)
            for th in bot.townhalls
        )
    raise AssertionError(f"unhandled opening step kind {step.kind!r}")


def _step_behavior(ctx, step: "_Step"):
    if step.kind == "worker":
        # Not `BuildWorkers`: that requires `townhalls.idle`, and Zerg
        # hatches are routinely non-idle while Queens train from them -
        # confirmed live, worker@22 sat blocked with `idle_townhalls: 0`
        # / larva available for ~80s after overflow had already raced a
        # 3rd hatch. `TrainFromLarva` matches the Overlord/Zergling path
        # and `_step_issued`'s own drone+pending count.
        return TrainFromLarva(unit_type=UnitTypeId.DRONE, to_count=step.target)
    if step.kind == "overlord":
        return TrainFromLarva(unit_type=UnitTypeId.OVERLORD, to_count=step.target)
    if step.kind == "expand":
        # Not plain `ExpansionController` - see `ExpandWithPersistentBuilder`'s
        # own docstring for why (it prefers the pre-walked Drone
        # `_claim_natural_scout` parks on `UnitRole.PERSISTENT_BUILDER`).
        return ExpandWithPersistentBuilder(to_count=step.target)
    if step.kind == "gas":
        return ZergGasBuildingController(to_count=step.target)
    if step.kind == "spawning_pool":
        return BuildStructure(
            base_location=ctx.production_location,
            structure_id=UnitTypeId.SPAWNINGPOOL,
            to_count=1,
        )
    if step.kind == "zergling":
        return TrainFromLarva(unit_type=UnitTypeId.ZERGLING, to_count=step.target)
    if step.kind == "metabolic_boost":
        return UpgradeController(
            [UpgradeId.ZERGLINGMOVEMENTSPEED], base_location=ctx.production_location
        )
    if step.kind == "roach_warren":
        return BuildStructure(
            base_location=ctx.production_location,
            structure_id=UnitTypeId.ROACHWARREN,
            to_count=1,
        )
    if step.kind == "lair":
        # Not plain `TechUp`: that morphs `idle_townhalls[0]`, which live-
        # tested as the natural. Wait for the main hatch instead.
        return MorphLairAtMain(base_location=ctx.production_location)
    raise AssertionError(f"unhandled opening step kind {step.kind!r}")


def _current_opening_step(ctx) -> "_Step | None":
    """Advance `ctx.state.opening_step_index` past every already-issued
    entry, then return whatever's left - `None` once the whole scripted
    sequence is spent. The index only ever moves forward (see its own
    docstring in `core.state.RunState`)."""
    idx = ctx.state.opening_step_index
    while idx < len(_SEQUENCE) and _step_issued(ctx, _SEQUENCE[idx]):
        idx += 1
    ctx.state.opening_step_index = idx
    if idx >= len(_SEQUENCE):
        return None
    return _SEQUENCE[idx]


def _scripted_production(ctx):
    """The scripted opening's single "current step" - see `_SEQUENCE`."""
    step = _current_opening_step(ctx)
    if step is None:
        return None
    bot = ctx.bot
    if bot.supply_used < step.supply:
        return None
    if step.kind == "spawning_pool" and not bot.can_afford(UnitTypeId.SPAWNINGPOOL):
        # `BuildStructure`'s Zerg path (unlike every other ares behavior
        # this module hands back - `TrainFromLarva`, `ExpandWithPersistent
        # Builder`, `GasBuildingController`, `UpgradeController` all check
        # `can_afford` before committing anything) has no affordability
        # check of its own: it registers a pulled worker into ares' own
        # building tracker immediately and just lets the actual morph
        # command sit rejected by SC2 until the bank catches up, worker
        # standing idle at the site the whole time - confirmed live, ~5s
        # idle at the Spawning Pool site while minerals caught up. Held
        # back here instead, so that Drone keeps mining until affordable.
        #
        # Deliberately NOT extended to "roach_warren" (the only other
        # `BuildStructure` kind): by that point in `_SEQUENCE`, Zergling is
        # long tech-ready and Roach/Swarm Host aren't yet, so `z.
        # spawn_macro_army()`'s own single-tech-ready overproduce escape
        # hatch is live - holding this step back would open the exact
        # frame for it to spend the Warren's own minerals on Zerglings
        # instead, the same mineral-race class this build has repeatedly
        # hit. At spawning_pool's own point in the sequence nothing else
        # in `macro_steps` is active yet to race it, confirmed by reading
        # every step's gate.
        return None
    return _step_behavior(ctx, step)


# (after this `_SEQUENCE` index is issued, keep training Drones until this
# supply) - "Auto Worker" in the build order. Gated on *index*, not supply:
# a first version keyed windows purely off supply ranges (e.g. "13 to 16"
# for the window right after the first Overlord), and it live-tested badly
# - the window's own start (13) is the *same* supply as the Overlord's own
# gate, so the instant supply hit 13, Auto Worker became eligible
# immediately too, racing the still-unaffordable 100-mineral Overlord for
# the exact same bank the same way Gas/Pool once raced Natural Expand (see
# `_SEQUENCE`'s own comment) - confirmed live, the Overlord actually
# started once supply reached 14, not 13, because Auto Worker slipped a
# 14th Drone in first. Gating on "the entry this window follows has
# already been issued" closes that race the same way `_SEQUENCE`'s own
# strict lock does, without re-introducing Queen's cascading-delay problem
# (every remaining `_SEQUENCE` entry is blocked purely by minerals/larva,
# not by an open-ended non-mineral wait - see Queen's own comment for why
# *it* needed pulling out instead).
_AUTO_WORKER_WINDOWS: tuple[tuple[int, float], ...] = (
    (1, 16),  # after Overlord(13) -> Natural Expand's gate
    (2, 18),  # after Natural Expand(16) -> Gas's gate
    (4, 21),  # after Spawning Pool(17) -> stop at 21, not 25 (explicit
    # request: two Drones squeezed in at 19/20 first, then free mineral
    # priority for the 21/23 Queens and the 25/26 Zerglings instead of
    # racing them). No window covers 21 through expand@3 - that stretch
    # (Zergling x2, then the expand itself) is scripted in `_SEQUENCE`.
    (7, 30),  # after the 3rd base Expand -> Metabolic Boost's gate
    (10, 33),  # after Metabolic Boost -> Overlord's gate
    (13, 36),  # after Overlord(33) -> Overlord's gate
    (15, 44),  # after the 2nd Overlord@36 -> Roach Warren's gate
    (16, 55),  # after Roach Warren(44) -> 2nd Gas's gate
    (17, 54),  # after 2nd Gas(55) -> 54, zero-effect (supply's already past)
    (20, 59),  # after the last Zergling command(54) -> 59
    (21, math.inf),  # after Lair(57) -> stays open past the scripted opening
)


def _auto_worker_active(ctx) -> bool:
    idx = ctx.state.opening_step_index
    supply = ctx.bot.supply_used
    return any(
        idx > after_index and supply < until_supply
        for after_index, until_supply in _AUTO_WORKER_WINDOWS
    )


def _scripted_worker_production(ctx):
    """"Auto Worker": Drone production, gated to `_AUTO_WORKER_WINDOWS`.

    Listed after `_scripted_production` in `macro_steps`, so the current
    scripted step always gets first claim on a frame's minerals - and
    `_AUTO_WORKER_WINDOWS`' own index-gating (see its comment) keeps this
    from even being *eligible* until the step it's meant to follow has
    already been issued, so it can't out-race a same-supply, still-saving-up
    step the way a pure supply-range window once did.
    """
    if not _auto_worker_active(ctx):
        return None
    return BuildWorkers(to_count=ctx.worker_target)


# After this time, grow total gas buildings by 1 every `_GAS_SCALE_INTERVAL`
# seconds until capped at `_GAS_SCALE_MAX` - a later, separate concern from
# the scripted opening's own two gas entries (`_SEQUENCE`'s "gas" kind,
# which caps at `_GAS_SCALE_BASE`). Absolute, not incremental: re-derives
# the target from elapsed time every call rather than tracking state, so a
# lost Extractor gets rebuilt instead of permanently capping the total one
# short. Placement prefers main → natural → later bases (see
# `ZergGasBuildingController`).
_GAS_SCALE_START: float = 300.0  # 5:00
_GAS_SCALE_INTERVAL: float = 20.0
_GAS_SCALE_BASE: int = 2  # matches `_SEQUENCE`'s own final gas target
_GAS_SCALE_MAX: int = 8


# `c.auto_supply()`'s own gate (see its call site in `macro_steps`): true
# once the scripted opening's own Overlord production has run its full
# course (`_SEQUENCE`'s last "overlord" entry targets 6). Left ungated
# once, `AutoSupply` fired an unplanned extra Overlord around 0:44 -
# confirmed live: `AutoSupply._num_supply_required`'s own "low supply,
# restrict supply production" branch (ares.behaviors.macro.auto_supply)
# reacts to `supply_left <= 5` once `supply_used >= 13`, which is *more*
# cautious than this build's own hand-tuned Overlord pacing actually needs,
# so it kept racing ahead of `_SEQUENCE`'s own next scripted Overlord entry
# for the same larva. Gated on the *outcome* of the scripted Overlord
# entries (a live unit/pending count), not a raw supply threshold or
# `_SEQUENCE` index, so it can't itself race the identically-supply-gated
# 6th Overlord entry the same way the old Auto Worker windows once raced
# the 13-supply Overlord (see `_AUTO_WORKER_WINDOWS`'s own comment).
_FINAL_SCRIPTED_OVERLORD_COUNT: int = 6


def _scripted_overlords_exhausted(ctx) -> bool:
    have = ctx.bot.units(UnitTypeId.OVERLORD).amount + ctx.bot.already_pending(
        UnitTypeId.OVERLORD
    )
    return have >= _FINAL_SCRIPTED_OVERLORD_COUNT


def _gas_scale_target(time: float) -> int | None:
    """Total Extractor count wanted at `time`, or `None` before scaling opens."""
    if time < _GAS_SCALE_START:
        return None
    intervals = int((time - _GAS_SCALE_START) // _GAS_SCALE_INTERVAL) + 1
    return min(_GAS_SCALE_MAX, _GAS_SCALE_BASE + intervals)


def _scripted_gas_scaling(ctx):
    target = _gas_scale_target(ctx.bot.time)
    if target is None:
        return None
    return ZergGasBuildingController(to_count=target)


# Post-5:00 army vs tech posture (see module docstring).
_POST_FIVE: float = 300.0

_AIR_UPGRADES: tuple[UpgradeId, ...] = (
    UpgradeId.ZERGFLYERWEAPONSLEVEL1,
    UpgradeId.ZERGFLYERARMORSLEVEL1,
    UpgradeId.ZERGFLYERWEAPONSLEVEL2,
    UpgradeId.ZERGFLYERARMORSLEVEL2,
    UpgradeId.ZERGFLYERWEAPONSLEVEL3,
    UpgradeId.ZERGFLYERARMORSLEVEL3,
)


def _has_extra_upgrade_budget(ctx) -> bool:
    """200 supply or no larva left — spend floating cash on upgrades."""
    return ctx.bot.supply_used >= 200 or not ctx.bot.larva


def _upgrade_slot_target(ctx) -> int:
    """Concurrent researches: army-behind → 1(+1 extra); else 2(+1 extra)."""
    base = 1 if intel_army.army_behind_on_supply(ctx) else 2
    if _has_extra_upgrade_budget(ctx):
        return base + 1
    return base


def _desired_upgrades(ctx) -> list[UpgradeId]:
    upgrades = list(ctx.build.army.upgrades)
    if intel_army.enemy_has_air_units(ctx):
        upgrades.extend(_AIR_UPGRADES)
    return upgrades


def _reserve_upgrade_bank(ctx):
    """Whole game, once Lair is commanded: hold the bank for whichever
    upgrade is next, so we're always banking enough to keep at least one
    upgrade going rather than leaving it to however much gas happens to be
    left over once everything else has taken its cut.

    Must sit *above* `_spawn_macro_army`/`_split_production_after_opening`
    in `macro_steps` - both include their own `SpawnController`, which has
    no idea an upgrade wants the gas. Below them (the original placement,
    pre-5:00 only), the reservation was routinely bypassed the moment
    `_split_production_after_opening` claimed the frame first: confirmed
    live via a temporary diagnostic that vespene sawtoothed 19 -> 81 -> 8 ->
    100 -> 37 for 100+ seconds post-5:00, climbing while this check held
    but getting drained back down every time `_split_production_after_
    opening`'s own Roach spend won the frame instead - Glial Reconstitution
    itself sat in "shortage" that whole stretch. Moving the check up here
    (ahead of everything that spends army-comp gas) means nothing below it
    can touch the bank until this has had first look, every frame.

    `prioritize` (see `UpgradeSlots`'s own docstring) is `not behind`: while
    genuinely behind on army supply, this still starts an upgrade that's
    already affordable outright (never a downside - `UpgradeController`
    checks affordability before `prioritize`), but won't *hold* the bank
    against `army` below - a deliberate call to favor defense over teching
    when actually behind, not a rule this reservation should override.
    """
    if not _lair_commanded(ctx):
        return None
    behind = intel_army.army_behind_on_supply(ctx)
    return UpgradeSlots(
        upgrade_list=_desired_upgrades(ctx),
        base_location=ctx.production_location,
        max_slots=_upgrade_slot_target(ctx),
        prioritize=not behind,
    )


def _spawn_macro_army(ctx):
    """Actual Roach/Swarm Host production, whole game. Sits below
    `_reserve_upgrade_bank` (that reservation always gets first look) and
    below `_split_production_after_opening` (economy still gets its own
    priority there) - this is what fires whenever neither of those had
    anything to spend on this frame.
    """
    return z.spawn_macro_army(
        gate=gates.structure_started(UnitTypeId.ROACHWARREN)
    )(ctx)


def _split_production_after_opening(ctx):
    """`c.split_production`'s own `gate` only reorders `BuildWorkers`
    against `SpawnController` priority - it does NOT stop either from
    running before `gate` passes (see its own docstring: "Before `gate`
    passes, economy keeps its usual priority... as if this were still
    separate `build_workers()` then `spawn_army()` calls" - and its own
    tests, `test_split_production_favors_economy_before_gate`/`test_split_
    production_never_drops_either_side`, both assert a plan is *always*
    returned regardless of `gate`). Called as `c.split_production(gate=
    gates.after_wave(1))` directly, that meant `BuildWorkers(ctx.
    worker_target)` was competing with `_SEQUENCE`'s own worker/overlord/
    zergling entries for the same minerals from frame 0 - confirmed live:
    a 14th Drone got trained (target ctx.worker_target=22 at 1 base, well
    past `_SEQUENCE`'s own 13-drone target) before the scripted Overlord
    ever got a chance, delaying it from its 12s deadline to a consistent
    ~14.8s across every opponent/seed tested. Wrapping it so it doesn't run
    at all until the scripted opening is fully spent closes that race the
    same way `_expansions_after_scripted_third` / `c.upgrades` are re-gated
    above - `after_wave(1)` still governs its own economy/army
    re-prioritization once that's true, unchanged.
    """
    if ctx.state.opening_step_index < len(_SEQUENCE):
        return None
    return c.split_production(gate=gates.after_wave(1))(ctx)


def _expansions_after_scripted_third(ctx):
    """4th/5th bases only - never the scripted 3rd.

    `c.expansions(gate=supply>=27)` alone is not enough: supply hits 27
    while `_SEQUENCE` is still on the post-ling worker/overlord stretch
    (well before `expand@3`), and `ExpansionController(to_count=max_bases)`
    happily starts hatch #3 then. That morph drops a Drone and parks us
    supply-capped until the hatch finishes, which is what pushed Metabolic
    Boost from ~2:45 to ~3:21. Mirror `_split_production_after_opening`:
    don't return the controller at all until the scripted 3rd is issued.
    """
    if ctx.state.opening_step_index <= _THIRD_EXPAND_INDEX:
        return None
    return c.expansions(gate=gates.supply_at_least(27))(ctx)


def _overflow_after_scripted_opening(ctx):
    """Mineral-overflow hatches only after the scripted opening is spent.

    Confirmed live: at 115.8s with a 570 mineral bank (threshold 500),
    `overflow_hatcheries` → `ExpansionController` queued hatch #3 on a
    GATHERING Drone - well before `_SEQUENCE` reached `expand@3` (and
    even before the 2:00 pre-walk). That morph supply-locked the opening
    until ~3:20 and pushed Metabolic Boost ~35s past its deadline.
    """
    if ctx.state.opening_step_index < len(_SEQUENCE):
        return None
    return z.overflow_hatcheries(mineral_threshold=500)(ctx)


def _lair_commanded(ctx) -> bool:
    """True once Lair morph is actually on a townhall (or finished).

    Used to hold `c.upgrades` so Burrow can't spend the Lair gas bank
    while an incomplete warren makes Glial unresearchable - see the
    upgrades gate comment at the BUILD site.
    """
    bot = ctx.bot
    if bot.structures(UnitTypeId.LAIR).amount or bot.structures(UnitTypeId.HIVE).amount:
        return True
    return any(
        any(o.ability.id == AbilityId.UPGRADETOLAIR_LAIR for o in th.orders)
        for th in bot.townhalls
    )


# Game-time mark to pull a Drone aside and start it walking toward the
# natural expansion site - see `_claim_natural_scout`. Time-gated rather
# than supply-gated: the natural is the *first* expansion, so there's no
# earlier townhall count to key off, and `_SEQUENCE`'s own "expand" entry
# for it (supply 16) can sit current for a while behind Overlord/Gas/Pool
# - comfortably ahead of where that entry actually lands in practice
# (~50-55s, confirmed live).
_NATURAL_SCOUT_AT_TIME: float = 36.0


def _claim_natural_scout(ctx) -> None:
    """Pull one already-mining Drone aside, once game time passes 0:36, to
    walk it toward the natural expansion site ahead of `_scripted_
    production` actually placing it, so the travel time overlaps with the
    wait instead of happening entirely after.

    Pulls from the *existing* mining pool via `on_step` rather than
    intercepting a freshly-trained one via `on_unit_created`.
    `UnitRole.PERSISTENT_BUILDER` keeps `Mining` from dragging it back;
    `ExpandWithPersistentBuilder` is what actually picks it up for the
    morph (plain `ExpansionController` ignores that role). Location comes
    from `ExpansionController`'s own next-site lookup so the walk and the
    build agree; the Drone is picked via `mediator.select_worker` so a
    mid-mineral-carry isn't yanked mid-trip.
    """
    if ctx.state.natural_scout_claimed:
        return
    if ctx.bot.time < _NATURAL_SCOUT_AT_TIME:
        return

    location = ExpansionController(to_count=1)._get_next_expansion_location(
        ctx.bot, ctx.mediator
    )
    if location is None:
        return  # try again next frame

    scout = ctx.mediator.select_worker(target_position=location)
    if scout is None:
        return  # try again next frame

    ctx.state.natural_scout_claimed = True
    ctx.mediator.assign_role(tag=scout.tag, role=UnitRole.PERSISTENT_BUILDER)
    scout.move(location)
    ctx.log(f"MACRO_ZERG pre-walking Drone {scout.tag} toward natural at {location}")


# Radius from the natural townhall a Queen must spawn within to be claimed
# as "the natural's queen" - see `_claim_natural_queen_tumor`.
_NATURAL_QUEEN_RADIUS: float = 15.0


def _creep_tumor_tags(ctx) -> frozenset[int]:
    """Tags of every Creep Tumor (mid-cast or burrowed) that exists right
    now - the raw material both `_claim_natural_queen_tumor`/`_claim_main_
    queen_tumor` snapshot as a baseline at claim time, then diff against
    later to detect "a *new* one appeared" rather than "one exists".

    That distinction is load-bearing, not stylistic: ares' own `QueenSpread
    Creep` (what `routines.creep.spread_creep` drives a `QUEEN_CREEP`-role
    Queen with) doesn't place the first tumor near the claiming townhall at
    all - once total map creep coverage is low (always true this early), it
    walks the tumor chain toward `mediator.get_enemy_nat` instead, so a
    fixed-radius "did it land near home" check never actually saw either
    Queen's tumor (confirmed live - see git history on this module for the
    abandoned proximity-radius attempt). A bare "does any tumor exist"
    check has its own, different bug once a *second* claim mechanism
    exists: an already-placed tumor from an earlier, already-finished claim
    keeps existing on the map, so it satisfies the next claim's "done"
    check on its very first frame, before that Queen has moved or cast
    anything at all (confirmed live: the main's claim logged "pulled" and
    "back on inject duty" in the same frame). Diffing against a baseline
    fixes both: no assumption about where the tumor lands, and no
    confusion with a tumor some earlier claim already placed.

    Creep Tumors are structures, not units, in this API (`bot.units(...)`
    never matches them - confirmed live: without checking `structures(...)`
    specifically, a claimed Queen just kept casting indefinitely, walking
    the whole opening creep chain toward the enemy natural instead of
    handing back after its first tumor).
    """
    tumors = ctx.bot.structures(UnitTypeId.CREEPTUMORQUEEN) | ctx.bot.structures(
        UnitTypeId.CREEPTUMORBURROWED
    )
    return frozenset(t.tag for t in tumors)


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

    Detects "done" by diffing `_creep_tumor_tags` against the baseline
    snapshotted at claim time (see that function's own comment for why a
    plain existence or proximity check both fail) rather than an energy
    threshold: energy can drift above 25 while the queen is still walking
    to a valid edge, so "a new tumor now exists" is the more reliable
    signal that the one-shot cast already fired.
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
            townhalls, key=lambda th: th.distance_to(ctx.production_location)
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
        ctx.state.natural_queen_tumor_baseline = _creep_tumor_tags(ctx)
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

    if _creep_tumor_tags(ctx) <= ctx.state.natural_queen_tumor_baseline:
        return  # still walking to a placement spot - no new tumor yet

    ctx.state.natural_queen_tumor_done = True
    ctx.mediator.assign_role(tag=tag, role=UnitRole.QUEEN_INJECT)
    ctx.log(f"MACRO_ZERG natural Queen {tag} back on inject duty")


# Total (live + pending) Queen count that must exist before pulling one for
# the main's own opening Creep Tumors - see `_claim_main_queen_tumor`. `z.
# train_queens(per_base=1, maximum=6, extra=1)`'s own "+1 extra" slot is
# exactly this 3rd Queen (main and natural each already hold their own 1 by
# the time a 3rd trains at all), so claiming it here doesn't compete with
# either base's first, inject-critical Queen the way claiming Queen #1 or #2
# would.
_MAIN_QUEEN_TUMOR_AT_COUNT: int = 3
# How many Creep Tumors to plant on the main plateau before handing the
# Queen back. Two covers the rim for vision + fills creep the natural
# chain never reaches.
_MAIN_OPENING_TUMORS: int = 2
# Stay on the main high ground — past this, a spot is "outside" the main.
_MAIN_TUMOR_AREA_RADIUS: float = 18.0
# Keep successive main tumors apart so they cover different rim arcs.
_MAIN_TUMOR_CLEARANCE: float = 9.0


def _main_area_tumor_tags(ctx) -> frozenset[int]:
    """Creep Tumors currently sitting on the main plateau (not the natural)."""
    main = ctx.production_location
    home_height = ctx.bot.get_terrain_height(main)
    radius_sq = _MAIN_TUMOR_AREA_RADIUS**2
    tags: set[int] = set()
    tumors = ctx.bot.structures(UnitTypeId.CREEPTUMORQUEEN) | ctx.bot.structures(
        UnitTypeId.CREEPTUMORBURROWED
    )
    for tumor in tumors:
        if cy_distance_to_squared(tumor.position, main) > radius_sq:
            continue
        if ctx.bot.get_terrain_height(tumor.position) != home_height:
            continue
        tags.add(tumor.tag)
    return frozenset(tags)


def _pick_main_tumor_spot(ctx, queen) -> Point2 | None:
    """Creep-edge tile on the main high ground, clear of existing tumors."""
    main = ctx.production_location
    home_height = ctx.bot.get_terrain_height(main)
    area_sq = _MAIN_TUMOR_AREA_RADIUS**2
    clear_sq = _MAIN_TUMOR_CLEARANCE**2
    existing = [
        t.position
        for t in (
            ctx.bot.structures(UnitTypeId.CREEPTUMORQUEEN)
            | ctx.bot.structures(UnitTypeId.CREEPTUMORBURROWED)
        )
        if cy_distance_to_squared(t.position, main) <= area_sq
    ]
    creep_grid = ctx.mediator.get_creep_grid

    def _usable(point: Point2) -> bool:
        if cy_distance_to_squared(point, main) > area_sq:
            return False
        if ctx.bot.get_terrain_height(point) != home_height:
            return False
        if not cy_has_creep(creep_grid, point):
            return False
        if not ctx.bot.in_pathing_grid(point):
            return False
        return all(cy_distance_to_squared(point, e) >= clear_sq for e in existing)

    edge = ctx.mediator.find_nearby_creep_edge_position(
        position=main,
        search_radius=_MAIN_TUMOR_AREA_RADIUS,
        unit_tag=queen.tag,
        cache_result=False,
    )
    if edge is not None and _usable(edge):
        return Point2(edge)

    # Prefer the rim toward the natural / map center (high-ground edge
    # vision), then fill remaining arcs around the hatch.
    nat = ctx.own_nat
    toward = Point2(cy_towards(main, nat, 12.0)) if nat is not None else Point2(
        cy_towards(main, ctx.bot.game_info.map_center, 12.0)
    )
    candidates: list[Point2] = [toward]
    for radius in (8.0, 11.0, 14.0, 16.0):
        for index in range(12):
            angle = 2.0 * pi * index / 12
            candidates.append(
                Point2(
                    (
                        floor(main.x + radius * cos(angle)) + 0.5,
                        floor(main.y + radius * sin(angle)) + 0.5,
                    )
                )
            )
    candidates.sort(key=lambda p: cy_distance_to_squared(p, toward))
    for point in candidates:
        if _usable(point):
            return point
    return None


def _drive_main_queen_tumor(ctx, queen) -> None:
    """Move/cast a main-claim Queen onto a main-plateau tumor spot."""
    if queen.is_using_ability(AbilityId.BUILD_CREEPTUMOR):
        return
    spot = _pick_main_tumor_spot(ctx, queen)
    if spot is None:
        # Stay on the plateau while energy recharges / creep fills.
        if cy_distance_to_squared(queen.position, ctx.production_location) > 36.0:
            queen.move(ctx.production_location)
        return
    if cy_distance_to_squared(queen.position, spot) > 25.0:
        queen.move(spot)
        return
    if AbilityId.BUILD_CREEPTUMOR_QUEEN in queen.abilities:
        queen(AbilityId.BUILD_CREEPTUMOR_QUEEN, spot)


def _claim_main_queen_tumor(ctx) -> None:
    """Once the 3rd Queen exists, plant `_MAIN_OPENING_TUMORS` on the main
    high ground, then hand her back to inject.

    Does **not** hand off to `QueenSpreadCreep`: that behavior paths toward
    `get_enemy_nat` while map creep coverage is low, so the Queen walked
    past the natural and planted next to the forward tumor chain instead of
    covering the main rim (confirmed live). Placement is driven here each
    frame via `_drive_main_queen_tumor`; `spread_creep` skips this tag while
    the claim is active.
    """
    if ctx.state.main_queen_tumor_done:
        return

    if ctx.state.main_queen_tag is None:
        if not ctx.state.natural_queen_tumor_done:
            return  # let the natural's claim finish first - see older docstring
        have = ctx.bot.units(UnitTypeId.QUEEN).amount + ctx.bot.already_pending(
            UnitTypeId.QUEEN
        )
        if have < _MAIN_QUEEN_TUMOR_AT_COUNT:
            return  # 3rd queen not trained yet
        main = ctx.production_location
        candidates = ctx.mediator.get_units_from_role(
            role=UnitRole.QUEEN_INJECT, unit_type=UnitTypeId.QUEEN
        ).filter(lambda q: q.energy >= 25 and q.distance_to(main) < _NATURAL_QUEEN_RADIUS)
        if not candidates:
            return  # try again next frame
        queen = candidates.closest_to(main)
        ctx.state.main_queen_tag = queen.tag
        ctx.state.main_queen_tumor_baseline = _main_area_tumor_tags(ctx)
        ctx.mediator.assign_role(tag=queen.tag, role=UnitRole.QUEEN_CREEP)
        ctx.log(
            f"MACRO_ZERG main Queen {queen.tag} pulled for "
            f"{_MAIN_OPENING_TUMORS} main-plateau creep tumors"
        )
        return

    tag = ctx.state.main_queen_tag
    still_claimed = ctx.mediator.get_units_from_role(
        role=UnitRole.QUEEN_CREEP, unit_type=UnitTypeId.QUEEN
    )
    queen = next((q for q in still_claimed if q.tag == tag), None)
    if queen is None:
        ctx.state.main_queen_tag = None
        return

    placed = _main_area_tumor_tags(ctx) - ctx.state.main_queen_tumor_baseline
    if len(placed) < _MAIN_OPENING_TUMORS:
        _drive_main_queen_tumor(ctx, queen)
        return

    ctx.state.main_queen_tumor_done = True
    ctx.mediator.assign_role(tag=tag, role=UnitRole.QUEEN_INJECT)
    ctx.log(
        f"MACRO_ZERG main Queen {tag} back on inject duty "
        f"({len(placed)} main tumors placed)"
    )


def _macro_zerg_on_unit_created(ctx, unit) -> None:
    """Queen home snapshot + peel a home Zergling cap off the army.

    Queens: snapshot which townhall trained a new Queen, once, at the one
    moment that's unambiguous - see `TrainQueens`'s own `home_townhall`
    docstring for why a live-position lookup later can't be trusted.

    Zerglings: `army.types` includes Zergling so extras join attack waves,
    but the first `HOME_ZERGLING_CAP` stay on `ZERGLING_DEFENDER_ROLE` for
    `combat.defend_with_zerglings`.
    """
    if unit.type_id == UnitTypeId.ZERGLING:
        home = ctx.mediator.get_units_from_role(
            role=ZERGLING_DEFENDER_ROLE, unit_type=UnitTypeId.ZERGLING
        )
        if len(home) < HOME_ZERGLING_CAP:
            ctx.mediator.assign_role(tag=unit.tag, role=ZERGLING_DEFENDER_ROLE)
        return

    if unit.type_id != UnitTypeId.QUEEN:
        return
    townhalls = ctx.bot.townhalls.ready
    if not townhalls:
        return
    home = townhalls.closest_to(unit.position)
    ctx.state.queen_home_townhall[unit.tag] = home.tag


def _macro_zerg_on_step(ctx) -> None:
    """`BuildDefinition` only has one `on_step` slot - all of this
    build's per-frame concerns are called from here."""
    _claim_natural_scout(ctx)
    _claim_natural_queen_tumor(ctx)
    _claim_main_queen_tumor(ctx)


# Roach's own attack range is 4 - retreat once an enemy closes inside 3, so
# it holds anywhere from 3 to 4 rather than closing to melee. Same idiom as
# `four_rax_proxy.MARINE_MIN_ENGAGE_RANGE`/`combat.STALKER_MIN_ENGAGE_RANGE`,
# but deliberately passed via `attack_squads`' `kite_types` (no influence
# grid) rather than its `min_engage_range` alone - see that parameter's own
# comment for why handing Roach the grid here would reintroduce the exact
# "never engaged" bug `never_retreat` was added to fix.
_ROACH_MIN_ENGAGE_RANGE: float = 3.0


BUILD = BuildDefinition(
    name="Macro Zerg",
    label="Macro Zerg (Roach/Swarm Host)",
    race=Race.Zerg,
    economy=Economy(
        worker_target=80,
        workers_per_base=22,
        max_bases=5,
        gas_per_base=2,
        max_gas=8,  # matches `_GAS_SCALE_MAX` - see `_scripted_gas_scaling`
        workers_per_gas=3,
        long_distance_mine=True,
    ),
    army=Army(
        comp=ROACH_SWARM_HOST_COMP,
        # Roach + Zergling both wave-eligible; the first
        # `HOME_ZERGLING_CAP` Zerglings are peeled onto
        # `ZERGLING_DEFENDER_ROLE` in `_macro_zerg_on_unit_created`.
        types=frozenset({UnitTypeId.ROACH, UnitTypeId.ZERGLING}),
        upgrades=(
            UpgradeId.ZERGLINGMOVEMENTSPEED,  # opening upgrade, well before 5:00
            # Post-5:00 priority (`_desired_upgrades`/`UpgradeSlots` both
            # just walk this list in order - see their own docstrings):
            # Glial, Ground Carapace 1, Burrow, Tunneling Claws, then the
            # rest of the Evo Chamber tree, at the user's explicit request.
            UpgradeId.GLIALRECONSTITUTION,  # Roach speed
            UpgradeId.ZERGGROUNDARMORSLEVEL1,
            UpgradeId.BURROW,
            UpgradeId.TUNNELINGCLAWS,  # full use of burrowed Roach regen
            UpgradeId.ZERGMISSILEWEAPONSLEVEL1,
            UpgradeId.ZERGMISSILEWEAPONSLEVEL2,
            UpgradeId.ZERGGROUNDARMORSLEVEL2,
            UpgradeId.ZERGMISSILEWEAPONSLEVEL3,
            UpgradeId.ZERGGROUNDARMORSLEVEL3,
        ),
        # Second Evo from 5:00 so ground +1/+1 can research in parallel.
        evolution_chambers=2,
        evolution_chamber_gate=gates.after_time(_POST_FIVE),
    ),
    # The scripted opening's own Spawning Pool step lands well past the
    # ~15-20s of an immediate-pool opening (Natural Expand comes first); the
    # default `pool_deadline` assumes the latter, so this build states its
    # own with buffer in line with the ratio Upgrade Rush used for the same
    # "hatch before pool" shape.
    pool_deadline=110.0,
    combat=Combat(
        routines=(
            # Once army supply hits 40, promote everyone on DEFENDING and
            # stream every new army unit into ATTACKING from then on.
            combat.release_first_wave_then_stream(muster=True),
            combat.defend_home(),
            combat.defend_with_zerglings(),
            # never_retreat=True: Zergling (melee, still in this squad for
            # the breach-worker micro above) gains nothing from kiting off
            # a weapon cooldown - see `_squad_maneuver_commit`'s own
            # docstring for the live-confirmed bug this fixes (KeepGroupSafe
            # short-circuiting the whole squad's advance the instant any one
            # member was mid-cooldown on enemy-influenced ground, which a
            # close-range brawl makes true almost constantly). Roach is
            # split out via kite_types instead: it has an actual ranged
            # attack (4) worth kiting with, unlike Zergling - see
            # `_ROACH_MIN_ENGAGE_RANGE`'s own comment for the exact distance
            # and why it skips the influence grid `never_retreat` also
            # protects against.
            combat.attack_squads(
                never_retreat=True,
                kite_types=frozenset({UnitTypeId.ROACH}),
                min_engage_range=_ROACH_MIN_ENGAGE_RANGE,
            ),
            # After attack/defend so burrow/unburrow/retreat wins the frame.
            # Tunneling Claws: burrowed Roaches peel home while healing.
            combat.regen_burrow_roaches(),
            combat.dig_in_swarm_hosts(),
            combat.escort_corruptors(),
            overseer_routines.manage_overseers(),
            creep.spread_creep(),
            creep.spread_tumors(),
            scouting.air_scout(UnitTypeId.OVERLORD),
        ),
        # Leave the moment army supply hits 40 — wave1_min=1 so we do not
        # also wait on a unit-count floor after that.
        wave_gate=gates.army_supply_at_least(40),
        wave1_min=1,
        wave_growth=1.15,
        wave_stage_label="Roach Pushes",
        # Roach/Zergling stand and fight on bad ground on purpose (never_
        # retreat/kite_types above) - see Combat.ignore_influence_parking's
        # own docstring for the confirmed live false-positive this avoids.
        ignore_influence_parking=True,
    ),
    on_step=_macro_zerg_on_step,
    on_unit_created=_macro_zerg_on_unit_created,
    always=(
        c.mining(),
        # Opening-only gas pull-off: empty the geyser while banking ~100 for
        # Metabolic Boost, then resume 3/geyser for the rest of the game the
        # moment Speed is commanded. A continuous `vespene≥100` pull-off
        # confirmed live as a late-game stall (8 extractors, bank floated
        # ≥100 → zero drones on gas).
        c.gas_workers(
            pull_off=gates.all_of(
                gates.vespene_at_least(100),
                gates.negate(
                    gates.upgrade_started(UpgradeId.ZERGLINGMOVEMENTSPEED)
                ),
            ),
        ),
        z.inject_larva(),
    ),
    macro_steps=(
        # ── Scripted opening (see module docstring). `_scripted_production`
        # outranks `z.train_queens` outranks `_scripted_worker_production`,
        # so the structure/overlord/zergling chain always gets first claim
        # on the frame's minerals, Queen gets second, Auto Worker gets
        # whatever's left.
        _scripted_production,
        # Always-on, not scripted: 1 Queen per ready townhall (`per_base=1`),
        # plus 1 spare for creep spread (`extra`, see `routines.creep.
        # spread_creep`) - no supply gate or fixed count, since a Queen is
        # simply wanted the moment each base can support one. `TrainQueens`
        # already requires a ready Spawning Pool internally, so this is a
        # no-op before then regardless. Replaces the old scripted "1 queen
        # @ 21, 2 @ 23, 3 @ 28" entries outright - those were pinned to the
        # opening's own first three bases specifically; this scales to
        # however many bases actually exist, for the whole game.
        z.train_queens(per_base=1, maximum=6, extra=1),
        # Same maintenance shape as queens: 1 Spore Crawler per owned base
        # in the mineral line, forever. Opens at 4:30 and only re-scans for
        # missing crawlers every 15s so this does not fight larva every
        # frame, but still rebuilds after losses. Listed next to queens
        # (before army) so MacroPlan actually reaches it — at the bottom
        # behind `spawn_macro_army` it never spent (confirmed live: Stage 2
        # "3 Spore Crawlers" never happened through leave-330).
        z.spore_crawlers(
            per_base=1,
            gate=gates.after_time(270.0),
            check_interval=15.0,
        ),
        # Maintain 3 Overseers once Lair exists (home / army / scout roles
        # in `routines.overseers.manage_overseers`).
        z.overseers(
            to_count=3,
            gate=gates.structure_started(UnitTypeId.LAIR),
        ),
        _scripted_worker_production,
        # ── Post-opening / continuous macro - each re-gated so it only
        # ever continues growth past what the script already established,
        # never races it for the same purchase.
        # Safety net once the scripted Overlords run out - gated on
        # `_scripted_overlords_exhausted` rather than left unconditional,
        # see that function's own comment for why (an unplanned early
        # Overlord around 0:44 otherwise).
        c.auto_supply(gate=_scripted_overlords_exhausted),
        # Spire only - Pit/Hive sit below Tunneling Claws (see after
        # `_reserve_upgrade_bank`/`_spawn_macro_army`). Lair itself stays
        # `_SEQUENCE`-owned.
        z.tech_up(
            UnitTypeId.SPIRE,
            gate=gates.all_of(
                gates.structure_started(UnitTypeId.LAIR), intel_army.enemy_has_air_units
            ),
        ),
        z.evolution_chambers(),
        # 4th/5th only - `_expansions_after_scripted_third` (supply>=27 alone
        # raced hatch #3 ahead of the scripted expand; see that helper).
        _expansions_after_scripted_third,
        # Replaces the generic `c.gas_buildings()` continuation outright:
        # see `_scripted_gas_scaling`'s own docstring for the growth rule.
        _scripted_gas_scaling,
        # Whole game once Lair is commanded (Glial/Burrow/Claws, then the
        # rest of `Army.upgrades`) - see its own docstring for why this
        # must outrank both of the SpawnControllers below.
        _reserve_upgrade_bank,
        # Not `c.split_production(gate=gates.after_wave(1))` directly - see
        # `_split_production_after_opening`'s own comment for why that
        # doesn't actually gate anything.
        _split_production_after_opening,
        _spawn_macro_army,
        # After Tunneling Claws has started (implies Glial/Ground Carapace
        # 1/Burrow already pending) — was above upgrades and ate the Glial
        # 100/100 bank.
        z.tech_up(
            UnitTypeId.INFESTATIONPIT,
            gate=gates.all_of(
                gates.structure_started(UnitTypeId.LAIR),
                gates.upgrade_started(UpgradeId.TUNNELINGCLAWS),
            ),
        ),
        z.tech_up(
            UnitTypeId.HIVE,
            gate=gates.structure_started(UnitTypeId.INFESTATIONPIT),
        ),
        # Mineral sink: >5000 bank → every 30s pull 6 drones for 3 Spine +
        # 3 Spore beside the army (needs creep under the ball).
        z.forward_crawler_wave(),
        # Last: only fires when nothing above had anywhere to put a mineral
        # surplus - see the module docstring and the step's own. Gated via
        # `_overflow_after_scripted_opening` so a 500+ bank mid-opening
        # cannot race hatch #3 ahead of the script (confirmed live at 115.8s).
        _overflow_after_scripted_opening,
    ),
)
