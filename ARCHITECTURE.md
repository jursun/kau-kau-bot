# Architecture

The bot is built to hold dozens of builds across three races without the
shared code growing. Three rules make that work:

1. **A build is data, not a subclass.** It declares which steps and routines
   it wants, in what order. There is no build-specific manager class.
2. **The engines never decide anything.** They iterate what the build listed.
3. **Race-specific code lives in a race-specific module.** If a function names
   a hatchery, a queen or a marine, it does not belong in `core/` or
   `steps/common.py`.

```
bot/
  main.py              hooks only; never edited to add a build
  core/
    context.py         BotContext - the one object steps/routines receive
    state.py           RunState - anything that persists between frames,
                       incl. ProxyCrewState (per-worker crew progress)
    types.py           MacroStep / CombatRoutine / Gate / PointLocator /
                       UnitCreatedHook aliases
    macro_engine.py    runs build.always + build.macro_steps
    combat_engine.py   runs build.combat.routines
    registry.py        auto-discovers builds/<race>/*.py
    roles.py           per-race unit -> UnitRole tables, then
                       build.on_unit_created(ctx, unit) if set
  builds/
    definition.py      BuildDefinition, Economy, Army, Combat,
                       WorkerTask, ProxyCrewPlan
    zerg/              one module per build, each exporting BUILD
    terran/            four_rax_proxy
    protoss/           empty
  steps/
    common.py          race-neutral macro steps
    zerg.py            queens, injects, hatcheries, evo chambers
    terran.py          proxy_crew, claim_z_on_first_scv,
                       continuous_main_depots
    protoss.py         empty
  routines/
    combat.py          release_waves, defend_home, attack_squads,
                       builder_workers_attack
    scouting.py        air_scout
    targeting.py       attack_target, rally_point, hold_positions,
                       enemy_fourth, enemy_main_fallen,
                       hunt_remaining_bases
    gates.py           reusable conditions, incl. training_started
  behaviors/zerg/      custom ares Behaviors (ares has no inject/queen behavior)
```

## One bot, one race

A bot is a single race: `MyBotRace` in `config.yml` decides which
`<race>_builds.yml` ares reads and which builds can run at all.
`create_ladder_zip.py` packages whatever race that key names. KauKauBot
ships as Terran (`Four Rax Proxy`); set `MyBotRace: Zerg` to zip the Zerg
openings instead. `terran_builds.yml` names the single Terran build in
every `BuildChoices` cycle, so nothing breaks when `Debug` is False.

## Adding a build

One file. Nothing else changes.

1. Add the opening to `<race>_builds.yml` under `Builds:`, and to the relevant
   `BuildChoices` cycles.
2. Create `bot/builds/<race>/my_build.py` with a module-level `BUILD`. The
   `name` must match the YAML opening exactly.
3. Run `python -m tests.test_builds`.

```python
BUILD = BuildDefinition(
    name="MyBuild",                      # must match zerg_builds.yml
    label="Human readable",
    race=Race.Zerg,
    economy=Economy(worker_target=60, workers_per_base=22, max_bases=4,
                    gas_per_base=2, max_gas=6),
    army=Army(comp={UnitTypeId.ROACH: {"proportion": 1.0, "priority": 0}},
              types=frozenset({UnitTypeId.ROACH}),
              upgrades=(UpgradeId.GLIALRECONSTITUTION,)),
    combat=Combat(
        routines=(combat.release_waves(), combat.defend_home(),
                  combat.attack_squads(), scouting.air_scout(UnitTypeId.OVERLORD)),
        wave_gate=gates.after_time(300.0),
        wave1_min=12,
        focus=(FOCUS_NATURAL, FOCUS_MAIN),
    ),
    always=(c.mining(), z.inject_larva()),
    macro_steps=(c.auto_supply(), z.train_queens(maximum=4),
                 c.expansions(), c.gas_buildings(), c.upgrades(),
                 c.build_workers(), c.spawn_army()),
)
```

The registry imports every module under `builds/<race>/` that defines `BUILD`,
so there is no registry file to edit and no merge conflict when several builds
land at once. A name that does not resolve raises `UnknownBuild` rather than
silently substituting a different build; `main.py` catches that at game start,
logs it at CRITICAL and falls back, so a typo cannot end a ladder game — but
`tests/test_builds.py` will have caught it first.

## Key concepts

**`MacroStep`** — `(ctx) -> MacroBehavior | None`. `None` means "not this
frame", which is how gating works. Steps are factories so they can be
parameterised at the call site: `z.macro_hatch(2, gate=...)`.

**Order is priority.** A `MacroPlan` short-circuits on the first behavior that
acts, so exactly one macro action happens per frame and earlier entries in
`macro_steps` win. `always` sits outside the plan because mining and injects
must run every frame, including during the opening.

**`Gate`** — `(ctx) -> bool`. Compose with `gates.all_of` / `any_of` /
`negate` instead of writing new ones. `UpgradeRush`'s "leave 10s before +1/+1
lands, but only for wave 1" is three existing gates combined, not new code.

**`CombatRoutine`** — `(ctx) -> None`. Registers its own maneuvers. Wave
release is itself a routine, so a purely defensive build simply omits it.
`attack_squads` musters a freshly-released wave at `targeting.rally_point`
before sending it at the real target, tracked via `RunState.mustering_tags`
so a squad that scatters mid-attack is not sent back to re-muster.

**`ctx.build.army.types`** is what keeps combat race-neutral: the engine asks
for "army units in role X", never for zerglings.

## Where to put a change

| Change | Goes in |
|--------|---------|
| A number for one build | that build's `BUILD` |
| A new reusable condition | `routines/gates.py` |
| A new race-neutral macro action | `steps/common.py` |
| Anything naming a race's units/structures | `steps/<race>.py` |
| A new kind of army control | `routines/combat.py` |
| Behaviour ares doesn't provide at all | `behaviors/<race>/` |

If you find yourself adding an `if build.name == ...` anywhere, that is the
signal to add a step or gate instead.

## ares-sc2 quirks worth knowing

Facts about how ares behaves that are not obvious from its source, surfaced
while migrating off python-sc2 and validating both openings in real games.
Kept here (rather than in a one-time migration log) because they stay true
regardless of what build is running.

- **One macro action happens per frame.** A `MacroPlan` short-circuits on
  the first behavior that acts, so `macro_steps` order is spending priority
  (see "Order is priority" above). This is also why two steps that should
  compete for the same frame — e.g. drones vs. army once
  `common.split_production` kicks in — have to be nested in their own
  `MacroPlan` rather than statically ordered: nesting lets whichever side
  should currently go first win the frame instead of one starving the other
  outright.
- **`split_production` used to compare `supply_army` against
  `supply_workers` directly — don't.** A zergling costs 0.5 supply and a
  drone costs 1.0, so that raw comparison read army as "behind" for nearly
  the whole game regardless of actual zergling count, handing it first pick
  far more often than the "keep both roughly even" docstring intended (and
  a likely contributor to the validator's "Workers Massed" FAIL and
  large resource-blocked-frame counts on later upgrades — production
  competing hard for the same mineral bank). Fixed by comparing
  `ctx.bot.supply_workers` against `ctx.worker_target` instead — economy
  keeps priority until it hits its own target, then army takes over
  outright. Comparing a side to its *own* target, not to the other side's
  raw supply, is the pattern worth repeating if another "alternate
  priority between two differently-costed things" step ever gets added.
- **`BuildStructure` cannot place an in-base hatchery on Zerg.** For
  `Race.Zerg` it defers unconditionally to ares' own
  `_do_zerg_build_placement`, which searches ~30 tiles out from the base
  location and tends to settle on the natural rather than a tight in-base
  spot. `bot/behaviors/zerg/build_macro_hatch.py` does its own search
  instead, constrained to the main's terrain height and kept clear of every
  expansion.
- **`BuildStructure.to_count_per_base` is a guaranteed `KeyError` for
  Zerg.** It's checked via `mediator.get_placements_dict[base_location]`,
  and that dict is only ever populated by
  `PlacementManager._solve_terran_building_formation` /
  `_solve_protoss_building_formation` — `_solve_zerg_building_formation` is
  an unimplemented stub in ares v3.13.1, so the dict never gets a single
  zerg key, for any base, ever. Count existing/pending structures yourself
  instead — see `steps/zerg.py`'s `spore_crawlers()`, which also shows the
  follow-on gotchas below.
- **`ai.request_zerg_placement()` (and so `BuildStructure` for any Zerg
  structure) is unusable for anything requested more than once per game.**
  It appends to `ai._requested_zerg_placements`, and `_after_step` replays
  that ENTIRE list every single frame — the list is only ever cleared once,
  at game start, never after being processed. So one request doesn't fire
  once: `find_placement` + `select_worker` keep succeeding on it again next
  frame, and the one after that, for the rest of the game, each success
  producing one more structure. Whichever base gets requested earliest eats
  the worst of it (in this codebase, the main — `owned_expansions` walks it
  first). `bot/behaviors/zerg/build_spore_crawler.py` does the
  find-placement / select-worker / build-with-specific-worker sequence
  itself, synchronously, instead — the same reasoning
  `build_macro_hatch.py` already applies to hatcheries, for the same
  underlying reason (see the bullet above).
- **Count pending, not just placed, structures when gating a build request
  per base.** A worker already dispatched to build something doesn't show
  up in `structures()` until it arrives and starts — only a second or two,
  but long enough that a naive per-frame per-base check re-requests a build
  every frame during that walk, piling several onto one base. Gate on
  `ai.structure_pending(structure_id)` (a ready-or-pending count, bot-wide)
  first; it's the same count `BuildStructure.to_count` already uses
  reliably elsewhere.
- **The gas pull-off lever is `mediator.set_workers_per_gas`, not
  `Mining(workers_per_gas=...)`.** The latter is only read by `Mining` when
  deciding whether to vespene-boost; the `ResourceManager` owns actual
  worker assignment. `bot/behaviors/set_gas_workers.py` calls
  `mediator.set_workers_per_gas` directly, one worker per frame off any
  over-staffed geyser.
- **`UpgradeController.auto_tech_up_enabled` only ever builds one
  Evolution Chamber.** A build that wants +1 melee and +1 carapace
  researching in parallel needs its own `BuildStructure(to_count=2)` step
  for the second one — see `z.evolution_chambers()`. That count and its
  gate live on `BuildDefinition.army.evolution_chambers` /
  `.evolution_chamber_gate` (defaulting to `1`/always-on, so a build that
  doesn't care never mentions them), not as arguments to
  `z.evolution_chambers()` itself — it takes none, and reads both off
  `ctx.build.army` at call time. That's what lets
  `tests/validators/base_validator.py` check the exact same target and gate
  the step is building toward instead of a second, separately-maintained
  copy of `2` and "once Speed is under way."
- **`already_pending_upgrade` returns a 0.0-1.0 float**, not a count — how
  close a researching upgrade is to finishing, for gates like
  `gates.upgrades_within` that need to leave *before* an upgrade lands
  rather than waiting for it to actually finish.
- **`ProductionController` is explicitly Terran/Protoss only** — it logs a
  warning and no-ops for `Race.Zerg`. There's no generic ares controller for
  "train unit type X up to a count" beyond `SpawnController` (larva-spawn,
  driven by an army composition dict); anything else — queens
  (`TrainQueens`), overseers (`MorphOverseers`) — is bot-owned, one-at-a-time
  logic, same shape both times.
- **`QueenSpreadCreep`'s docstring example doesn't match its own
  constructor.** The example shows `QueenSpreadCreep(queen, queen.position,
  target)`, but the dataclass only has one relevant field, `unit` — it works
  out its own path and target internally via `mediator.get_next_tumor_on_path`
  and `get_creep_coverage`. The call is just `QueenSpreadCreep(unit=queen)`.
- **`TumorSpreadCreep` needs no cadence gating of your own.** Its own
  `execute` already checks `AbilityId.BUILD_CREEPTUMOR_TUMOR in
  self.unit.abilities` (the per-tumor cooldown) and
  `mediator.should_calculate_tumor_spread` (ares' own internal throttle), so
  it's safe to register one for every `UnitTypeId.CREEPTUMORBURROWED` in
  `mediator.get_own_structures_dict` every frame — see
  `routines/creep.py`'s `spread_tumors`. `QueenSpreadCreep` above and
  `TumorSpreadCreep` are independent: nothing pauses the passive tumor->tumor
  spread while the dedicated Queen is placing her own.
- **A slower escort chasing a squad's live `squad_position` never catches
  up if the squad is faster.** `squad_position` recedes as the squad
  advances, so targeting it directly only works if the escort is at least
  as fast as what it's escorting. `routines/combat.py`'s `escort_overseers`
  targets the squad's actual destination instead
  (`targeting.attack_target(ctx, squad.squad_position)`, the same call
  `attack_squads` itself uses) — a fixed point the escort can actually make
  progress toward, arriving ahead of or alongside the wave rather than
  perpetually trailing it.
- **`CombatManeuver.execute` is `any(...)` over its `micros`, in the order
  added** — the first behavior that returns `True` wins and the rest never
  run. That's what makes a "check something urgent first, fall through to
  normal movement otherwise" maneuver just a matter of `add()` order:
  `KeepUnitSafe` (returns `False` when already safe) added before
  `MoveToSafeTarget` means an endangered unit retreats instead of being sent
  toward its target — same shape `_defender_maneuver` already used for
  shoot-in-range before attack-move. `MoveToSafeTarget` itself resolves a
  destination down to the nearest *safe* spot within `radius` of it before
  pathing there, so a unit sent at it settles at the edge of enemy range
  instead of walking into the middle of it.
- **The Zerg upgrade/tech tree doesn't need to be hand-encoded anywhere.**
  `sc2.dicts.upgrade_researched_from.UPGRADE_RESEARCHED_FROM` plus
  `sc2.dicts.unit_research_abilities.RESEARCH_INFO[researched_from][upgrade]`
  (its own `"required_building"` key) give the same tech-tree facts
  `UpgradeController` itself reads — which structure researches an upgrade,
  and what extra structure it needs on top of that (e.g. Melee Attacks +2
  needs Lair, +3 needs Hive). `ai.tech_requirement_progress(structure_type)`
  answers "could I build/morph this right now" without separately walking
  `ares.dicts.unit_tech_requirement.UNIT_TECH_REQUIREMENT`'s prerequisite
  chains by hand (Hive's, for instance, is `[SPAWNINGPOOL, LAIR,
  INFESTATIONPIT, LAIR]`). `tests/validators/base_validator.py` builds its
  entire Stage 2/3 milestone list this way, off `ctx.build.army.upgrades`,
  so it can't silently drift out of sync with the build it's validating.
- **One validator subclass per build for the Validation Report.** Tracking
  (thresholds/gates from `ctx.build`) stays in `BaseValidator`; each build
  overrides only `validate()` for which stages appear and what Stage 4 is
  called. `tests/validators/registry.py` maps opening name → class the same
  way `bot/core/registry.py` maps openings to `BuildDefinition`.
- **Danger-avoidance is a per-routine choice, not a blanket policy.**
  `scouting.air_scout()` used to wrap its `PathUnitToTarget` in a
  `KeepUnitSafe`-first `CombatManeuver` — the same shape `escort_overseers`
  uses (see the `CombatManeuver.execute` bullet above) — but that's wrong
  for what it's actually escorting: the opening scouting Overlord
  (`core/roles.py`'s `SCOUT_TYPES`) exists specifically to sit and watch a
  vision spot, so retreating the instant something worth watching showed up
  defeated its own purpose. It now registers a bare `PathUnitToTarget` with
  no danger check ahead of it. `KeepUnitSafe`/`MoveToSafeTarget` stay
  correct for anything that's actually trying to survive while moving
  (`escort_overseers`, `_defender_maneuver`) — the shape isn't universal,
  it depends on whether the unit is supposed to avoid the threat or watch it.
- **`sc2.BotAI.calculate_supply_cost(unit_type)` is the one true source for
  a unit type's supply cost** — it corrects for morphs the same way ares'
  `enemy_army_value` corrects for cost (e.g. a Ravager's true supply comes
  from the Roach it morphed from, not a second charge on top), so it beats
  hand-rolling a `{UnitTypeId: supply}` table. `BaseValidator`'s wave
  tracking uses it both ways: `ctx.units_in_role(UnitRole.ATTACKING)
  .tags_in(new_tags)` for our own released supply, and
  `mediator.get_cached_enemy_army` (ares' persisted-out-of-vision enemy
  unit cache — filter out `WORKER_TYPES` yourself, same as
  `enemy_army_value` does, since the cache doesn't) for the enemy's known
  army supply at that same instant. Every wave in the Stage 4 report now
  carries both numbers side by side.
- **`StutterGroupForward` takes a `target` argument and completely ignores
  it once there are enemies.** Reading its source (ares v3.13.1) shows
  `target` is only ever used to sort the group and pick a representative
  unit for the duplicate-order check — every actual order it issues
  (`ATTACK`/`MOVE`) is aimed at `enemy_center`, computed from `enemies`, not
  `target`. This is what sank an earlier attempt at a disengage-and-retreat
  feature: passing `target=rally` to `StutterGroupForward` while a squad
  was meant to be falling back compiled, passed every unit test (which only
  asserted on the `AMoveGroup` appended *after* it), and did nothing in a
  real game — `StutterGroupForward.execute()` always returned `True` while
  `close_enemy` was non-empty, and `CombatManeuver`'s `any()` short-circuit
  (see the bullet above) meant the `AMoveGroup(target=rally)` behind it
  never ran. Worth remembering if a future retreat/kite feature reaches for
  this behavior again: it is only ever safe to hand it a target other than
  the enemy when there truly are no enemies in `enemies` that frame.
  `attack_squads` no longer attempts a retreat at all (removed per Jason's
  call — this build always fights whatever a wave finds), but the framework
  fact stands on its own.
- **A validator threshold calibrated for one build silently breaks for
  another.** The validator's "Pool Timing" check used to compare against a
  single hardcoded `POOL_DEADLINE = 50.0`, which fit `Speedling All-In`'s
  immediate pool but not `UpgradeRush`'s deliberate hatch-before-pool
  opening (expand at 15, pool at 16 — pool routinely lands around 60s by
  design, not by lateness). Fixed by adding `BuildDefinition.pool_deadline`
  (default 50.0) so each build states its own expectation; `UpgradeRush`
  sets `pool_deadline=75.0`. Same lesson as the module's own stated
  philosophy elsewhere: a number the validator enforces belongs on the
  build, not baked into the validator. (`POOL_DEADLINE` on the validator
  class — originally kept as a fallback for a window where `ctx` wasn't set
  yet — was removed outright once the validator split moved construction to
  after `ctx` exists; see the `BaseValidator`/per-build-file gotcha above.)
- **`structure_pending` and `not_started_but_in_building_tracker` count the
  same structure type very differently — don't mix them across two
  behaviors expected to throttle each other.** `ExpansionController.max_pending`
  checks `ai.structure_pending(base_townhall_type)`, which counts a
  hatchery as pending for its *entire* build time (`build_progress < 1.0`,
  ~71s) — but `BuildMacroHatch.max_on_route` checks
  `ai.not_started_but_in_building_tracker(HATCHERY)`, which clears the
  instant the drone starts building. `zerg.overflow_hatcheries()`
  originally nested both behind the same computed `to_count`, trusting
  each to throttle itself — but since a macro hatch is also just a
  Hatchery, one under construction kept `ExpansionController` blocked for
  its whole build time while `BuildMacroHatch`'s narrower gate cleared
  almost immediately, letting it queue another macro hatch, and another,
  long before the first even finished — exactly the "went overboard with
  macro hatches" Jason reported, with real expansions barely getting a
  turn. Fixed by gating the whole step on `ai.structure_pending(HATCHERY)`
  itself (the same broad count `ExpansionController` already uses)
  *before* trying either behavior, throttling both to one hatchery in
  flight at a time regardless of kind — so `ExpansionController` (tried
  first) gets a fair, unblocked shot at the nearest safe expansion every
  time, and `BuildMacroHatch` only ever fires when no legal expansion
  exists that frame. Same shape as `spore_crawlers()`'s own
  `structure_pending` gate (see the postscript on gotcha 10) — worth
  reaching for whenever two behaviors sharing a structure type are meant
  to take turns rather than compete.

- **Terran/Protoss placements are precomputed at every expansion, including
  enemy ones — so a formation proxy is a `request_building_placement` call,
  while Zerg still needs ring-search workarounds** (`_solve_zerg_building_
  formation` is a stub; see gotchas 1 and 10). Four Rax's `proxy_crew` uses
  that for Barracks A/B/D and the proxy Depot; Barracks C uses
  `routines.placement.near_point` on the fourth's townhall tile instead so
  it does not compete for formation slots.
- **An army that spawns away from home needs `combat.rally`, or every unit
  it makes walks back across the map before it attacks.**
  `targeting.rally_point` defaults to "in front of our own natural", which
  is right for home production and wrong for a proxy: a Marine at the enemy
  fourth would muster at our natural first. `Combat.rally` overrides that
  and collapses `hold_positions` to that one place for one-base all-ins.
- **`select_worker` only ever considers `UnitRole.GATHERING`, so giving a
  worker any other role removes it from every future `BuildStructure`.**
  This is what makes `combat.builder_workers_attack`'s `claim_gate` a
  correctness condition rather than a preference. The routine claims
  workers stranded at the proxy into `UnitRole.PROXY_WORKER` so they join
  the attack instead of walking home to mine — but claim one Barracks too
  early and the builder standing *right next to* where the next Barracks
  goes is no longer eligible to build it, and ares pulls a fresh SCV from
  the mineral line for the whole walk instead. Hence the gate: claim only
  once every Barracks the build wants is standing or under way. The same
  role mechanic is what keeps claimed workers out of `Mining` (which also
  only touches GATHERING), so no extra bookkeeping is needed to stop them
  wandering back to a mineral patch.
- **A validator check the build cannot possibly satisfy is noise, not a
  finding.** `BaseValidator`'s Stage 1 checked Spawning Pool timing and
  Extractor count unconditionally, which for a Terran build is four
  guaranteed FAILs burying the checks that do apply (workers, supply, and
  the whole of Stage 4). Those four are now gated on
  `ctx.build.race == Race.Zerg`. Same lesson as gotchas 12 and 17, one
  level up: those pushed *thresholds* onto the build, this pushes
  *applicability* onto it.
- **`UnitRole.BUILDING` and `UnitRole.PERSISTENT_BUILDER` both carry ares-
  side side effects that make them wrong for a worker with a multi-step task
  list of its own.** `BuildingManager._handle_construction_orders` reverts a
  `BUILDING`-role worker straight to `GATHERING` the instant its tracked
  structure hits `build_progress >= 1.0` — fine for a one-shot builder, fatal
  for `Four Rax Proxy`'s crew, which flings the worker back into the mineral
  line one frame after finishing Barracks A, before its own choreography
  (`steps.terran.proxy_crew`) gets a chance to hand it Barracks D.
  `PERSISTENT_BUILDER` looks like the fix (ares itself never auto-reassigns
  it) until `BuildOrderRunner.set_build_completed()` sweeps every
  `PERSISTENT_BUILDER`-role unit back to `GATHERING` in one shot the moment
  the opening's own `OpeningBuildOrder` list is exhausted — which, for a
  build that deliberately keeps that list short (see the next bullet), can
  happen while the crew's work is barely started. The fix: call
  `mediator.build_with_specific_worker(..., assign_role=False)` so ares never
  touches the worker's role at all, and manage it entirely with a role ares
  itself never reads or writes anywhere in its own source —
  `bot.consts.PROXY_CREW_ROLE` picks `UnitRole.GATE_KEEPER` for exactly that
  reason, the same "borrow an unused enum value for our own bookkeeping"
  trick `UnitRole.SCOUTING`/`QUEEN_INJECT` already use.
- **`build_with_specific_worker`'s `assign_role=False` still gets the walk-
  and-build automation for free.** Its per-frame companion,
  `BuildingManager._handle_construction_orders`, drives every tag in
  `building_tracker` — pathing it to the target, then issuing the actual
  build order once in range and affordable — purely off tracker membership,
  regardless of that worker's current `UnitRole`. So `assign_role=False`
  doesn't mean "do the placement/pathing yourself" — it only opts out of
  ares' own role bookkeeping; `steps.terran.proxy_crew` still gets a fully
  automatic walk-then-build for X, Y and Z's every task from one call, the
  same as a normal `BuildStructure`-driven worker would.
- **`get_building_tracker_dict` membership is the completion signal for a
  hand-placed structure — structure counts are not, once two workers can be
  building the same structure type at the same time.** X and Y both start
  Barracks the instant the game does (Barracks A and B respectively), so
  they finish within moments of each other; a step that inferred "my task is
  done" from `structures(BARRACKS).ready.amount` increasing would have no
  way to tell whose Barracks that count bump belonged to. Both
  `steps.terran.proxy_crew` and `tests.validators.base_validator.
  BaseValidator._track_crew` (surfaced as Stage 1B by
  `FourRaxProxyValidator`, the only build that has one) track completion
  the same way instead: a worker's own tag dropping back out of
  `mediator.get_building_tracker_dict`, which ares removes it from the
  instant *that* worker's structure completes and not a frame before.
- **`request_building_placement`'s `reserve_placement=True` default makes
  same-frame concurrent calls placement-safe.** X and Y each request a
  Barracks placement at the same proxy base on literally the same frame (game
  start); since Python runs one call fully before the next starts, the first
  call reserves its spot before the second one asks, so the two placements
  never collide — no extra locking needed on this codebase's side for two
  crew workers targeting the same base at once.
- **Whether `on_unit_created` fires for a game's starting units is not a
  reliable guarantee — do not generalize the scout case past Zerg
  Overlords.** `core/roles.py`'s scout assignment gets away with treating
  "the first Overlord this hook sees" as "the second one" because it does,
  empirically, skip the starting Overlord. `steps.terran.claim_z_on_first_scv`
  originally copied that pattern for Terran's starting SCVs and was wrong: in
  an actual game the event fired for one of the starting 12 as well, pulling
  a third worker off the mineral line alongside X and Y instead of the
  intended two. The fix doesn't lean on event-firing semantics at all — it
  checks `len(ctx.bot.workers) >= 13` at the moment of the event, so only a
  call that lands once a 13th SCV genuinely exists can claim one, regardless
  of what fires the hook or when. Treat "does this event fire for starting
  units" as unit-type-dependent and unverified until checked, not as a
  cross-race guarantee.
