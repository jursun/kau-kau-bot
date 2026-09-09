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
    state.py           RunState - anything that persists between frames
    types.py           MacroStep / CombatRoutine / Gate aliases
    macro_engine.py    runs build.always + build.macro_steps
    combat_engine.py   runs build.combat.routines
    registry.py        auto-discovers builds/<race>/*.py
    roles.py           per-race unit -> UnitRole tables
  builds/
    definition.py      BuildDefinition, Economy, Army, Combat
    zerg/              one module per build, each exporting BUILD
    terran/ protoss/   empty; Zerg is the near-term focus
  steps/
    common.py          race-neutral macro steps
    zerg.py            queens, injects, hatcheries, evo chambers
    terran.py protoss.py
  routines/
    combat.py          release_waves, defend_home, attack_squads
    scouting.py        air_scout
    targeting.py       attack_target, rally_point, hold_positions
    gates.py           reusable conditions
  behaviors/zerg/      custom ares Behaviors (ares has no inject/queen behavior)
```

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
  `MacroPlan` rather than statically ordered: nesting lets whichever side is
  behind win the frame instead of one starving the other outright.
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
  `tests/upgrade_rush_validator.py` check the exact same target and gate
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
  INFESTATIONPIT, LAIR]`). `tests/upgrade_rush_validator.py` builds its
  entire Stage 2/3 milestone list this way, off `ctx.build.army.upgrades`,
  so it can't silently drift out of sync with the build it's validating.
- **`UpgradeRushValidator` is one class shared by every registered build,
  not a per-build subclass — it stays correct per-build because every
  number and gate it checks is read off `ctx.build` (or off python-sc2/
  ares' own tech tables) rather than hardcoded for UpgradeRush by name.**
  Stage 1's worker/gas targets come from `ctx.build.economy`; Stage 3's
  upgrade list and Stage 4's wave-size math come from `ctx.build.army` /
  `ctx.build.combat`; Stage 2's tech-structure checklist is derived off
  `ctx.build.army.upgrades` via `UPGRADE_RESEARCHED_FROM` (so it's simply
  empty for a build with no Lair/Hive-gated upgrades — Speedling All-In's
  report shows "No tech structures required by this build" instead of
  failing four checks it could never pass); and the Evolution Chamber
  target/gate come from `ctx.build.army.evolution_chambers` /
  `.evolution_chamber_gate` (see above), the last piece that used to be a
  module constant plus a hardcoded `LING_SPEED` check. A build that wants
  genuinely different validation behavior — not just different numbers —
  is still a case for a new validator class; a build that just has
  different targets, a different upgrade list, or no tech structures at
  all is already handled by this one, for free.
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
  hand-rolling a `{UnitTypeId: supply}` table. `UpgradeRushValidator`'s wave
  tracking uses it both ways: `ctx.units_in_role(UnitRole.ATTACKING)
  .tags_in(new_tags)` for our own released supply, and
  `mediator.get_cached_enemy_army` (ares' persisted-out-of-vision enemy
  unit cache — filter out `WORKER_TYPES` yourself, same as
  `enemy_army_value` does, since the cache doesn't) for the enemy's known
  army supply at that same instant. Every wave in the Stage 4 report now
  carries both numbers side by side.
