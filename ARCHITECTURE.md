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
  follow-on gotcha below.
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
  for the second one — see `z.evolution_chambers()`.
- **`already_pending_upgrade` returns a 0.0-1.0 float**, not a count — how
  close a researching upgrade is to finishing, for gates like
  `gates.upgrades_within` that need to leave *before* an upgrade lands
  rather than waiting for it to actually finish.
