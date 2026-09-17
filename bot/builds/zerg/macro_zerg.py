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
`z.tech_up(INFESTATIONPIT)`/`z.tech_up(SPIRE)`/`z.spore_crawlers` are
re-gated on `gates.structure_started(UnitTypeId.LAIR)` specifically - `_
SEQUENCE`'s own "lair" entry is what actually morphs Lair; without this
gate, `TechUp` would happily morph it itself the moment it's economically
able to (it walks prerequisites and morphs whatever's missing along the
way), well ahead of where the scripted sequence wants it.
`_scripted_gas_scaling` replaces `c.gas_buildings()` outright rather than
just being re-gated: after 5:30 it grows the gas target by 1 every 30s
until capped at 6 (`economy.max_gas` is raised to match, so the Stage 1
"Extractor Cap Respected" validator check stays meaningful).

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
`Economy(workers_per_gas=3)` + `vespene_at_least(100)` pull-off, `InjectLarva`,
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

After the opening, macro steps take Infestation Pit (Swarm Host — cheap,
passive map-control damage from Locusts, meant to be dug in at each base
rather than committed to a fight) and, only once the enemy has actually
shown air units, Spire (Corruptor, anti-air escort). Burrow + Tunneling
Claws let Roaches burrow-regenerate and reposition between engagements
without giving up the sustain. `z.spawn_macro_army` handles the
composition switch between these phases — see its own docstring for why a
static comp dict alone would stall production in the Roach-only window.

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

import math
from dataclasses import dataclass

from ares.behaviors.macro import (
    BuildStructure,
    BuildWorkers,
    ExpansionController,
    GasBuildingController,
    UpgradeController,
)
from ares.consts import UnitRole
from sc2.data import Race
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from bot.behaviors.zerg import (
    ExpandWithPersistentBuilder,
    MorphLairAtMain,
    TrainFromLarva,
    pending_larva_trained,
)
from bot.builds.definition import Army, BuildDefinition, Combat, Economy
from bot.consts import ROACH_SWARM_HOST_COMP
from bot.intel import army as intel_army
from bot.routines import combat, creep, gates, scouting
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
        return GasBuildingController(to_count=step.target)
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
# short.
_GAS_SCALE_START: float = 330.0  # 5:30
_GAS_SCALE_INTERVAL: float = 30.0
_GAS_SCALE_BASE: int = 2  # matches `_SEQUENCE`'s own final gas target
_GAS_SCALE_MAX: int = 6


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


def _scripted_gas_scaling(ctx):
    time = ctx.bot.time
    if time < _GAS_SCALE_START:
        return None
    intervals = int((time - _GAS_SCALE_START) // _GAS_SCALE_INTERVAL) + 1
    target = min(_GAS_SCALE_MAX, _GAS_SCALE_BASE + intervals)
    return GasBuildingController(to_count=target)


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


def _macro_zerg_on_unit_created(ctx, unit) -> None:
    """Snapshot which townhall trained a new Queen, once, at the one
    moment that's unambiguous - see `TrainQueens`'s own `home_townhall`
    docstring for why a live-position lookup later can't be trusted
    (`InjectLarva` sends the closest *available* Queen to whichever
    townhall needs an inject next, not necessarily the one that trained
    it, so a Queen can be standing at a different base entirely by the
    time anything re-checks). A Queen spawns essentially on top of the
    townhall that trained it, so "closest ready townhall right now" is
    exact at creation even though it stops being trustworthy moments
    later.
    """
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


BUILD = BuildDefinition(
    name="Macro Zerg",
    label="Macro Zerg (Roach/Swarm Host)",
    race=Race.Zerg,
    economy=Economy(
        worker_target=60,
        workers_per_base=22,
        max_bases=5,
        gas_per_base=2,
        max_gas=6,  # matches `_GAS_SCALE_MAX` - see `_scripted_gas_scaling`
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
            UpgradeId.ZERGLINGMOVEMENTSPEED,
            UpgradeId.GLIALRECONSTITUTION,  # Roach speed
            UpgradeId.BURROW,
            UpgradeId.TUNNELINGCLAWS,  # full use of burrowed Roach regen
        ),
    ),
    # The scripted opening's own Spawning Pool step lands well past the
    # ~15-20s of an immediate-pool opening (Natural Expand comes first); the
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
    on_unit_created=_macro_zerg_on_unit_created,
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
        _scripted_worker_production,
        # ── Post-opening / continuous macro - each re-gated so it only
        # ever continues growth past what the script already established,
        # never races it for the same purchase.
        # Safety net once the scripted Overlords run out - gated on
        # `_scripted_overlords_exhausted` rather than left unconditional,
        # see that function's own comment for why (an unplanned early
        # Overlord around 0:44 otherwise).
        c.auto_supply(gate=_scripted_overlords_exhausted),
        # TechUp morphs further tech (Lair included) itself along the way,
        # but Lair is already handled by `_SEQUENCE`'s own "lair" entry -
        # gated on it existing so TechUp doesn't morph it prematurely.
        z.tech_up(
            UnitTypeId.INFESTATIONPIT, gate=gates.structure_started(UnitTypeId.LAIR)
        ),
        z.tech_up(
            UnitTypeId.SPIRE,
            gate=gates.all_of(
                gates.structure_started(UnitTypeId.LAIR), intel_army.enemy_has_air_units
            ),
        ),
        # 4th/5th only - `_expansions_after_scripted_third` (supply>=27 alone
        # raced hatch #3 ahead of the scripted expand; see that helper).
        _expansions_after_scripted_third,
        # Replaces the generic `c.gas_buildings()` continuation outright:
        # see `_scripted_gas_scaling`'s own docstring for the growth rule.
        _scripted_gas_scaling,
        # `UpgradeController` walks prerequisites the same way `TechUp`
        # does - Glial Reconstitution requires Roach Warren, and without
        # a warren gate it built one itself at supply 30 (confirmed live:
        # "Building UnitTypeId.ROACHWARREN for UpgradeId.GLIALRECONSTITUTION"
        # at 2:30). Gating on warren-*started* was still too early: with an
        # incomplete warren in the structures dict, Glial isn't researchable
        # yet so the controller falls through to Burrow, which spent the
        # 100 gas at 212.6s and left Lair `cant_afford` on gas until 252.1
        # (deadline 256). Gate on Lair commanded instead - Speed is already
        # done by `_SEQUENCE`, and Glial/Burrow/TunnelingClaws can share the
        # morph-window gas income.
        c.upgrades(gate=_lair_commanded),
        # Not `c.split_production(gate=gates.after_wave(1))` directly - see
        # `_split_production_after_opening`'s own comment for why that
        # doesn't actually gate anything.
        _split_production_after_opening,
        # Gated on Roach Warren existing - NOT redundant with its own
        # internal Roach-tech-readiness check. `SpawnController`'s "only
        # one tech-ready unit in the comp" escape hatch (`over_produce_
        # on_low_tech`) fires for *Zergling* the moment Spawning Pool
        # exists, well before Roach Warren - confirmed live: with nothing
        # else claiming the frame between the scripted opening's own
        # supply gates, this alone raced from supply 17 to 25 in a single
        # `on_step` call, producing Zerglings ungoverned by `_SEQUENCE`'s
        # own "zergling" entries and starving the 19/21 Queens of the
        # minerals they were supposed to get first. The original "already
        # gated on Roach Warren tech-readiness internally" reasoning here
        # was true for Roach/Swarm Host, not for this fallback branch.
        z.spawn_macro_army(gate=gates.structure_started(UnitTypeId.ROACHWARREN)),
        # One per base, mineral-line placement — static anti-air so Mutalisk/
        # air harass can't freely pick off drones while the army is Roach-
        # heavy (no native anti-air until Corruptor/Spire comes online).
        z.spore_crawlers(per_base=1, gate=gates.structure_started(UnitTypeId.LAIR)),
        # Last: only fires when nothing above had anywhere to put a mineral
        # surplus - see the module docstring and the step's own. Gated via
        # `_overflow_after_scripted_opening` so a 500+ bank mid-opening
        # cannot race hatch #3 ahead of the script (confirmed live at 115.8s).
        _overflow_after_scripted_opening,
    ),
)
