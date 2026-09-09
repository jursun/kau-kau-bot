# python-sc2 -> ares-sc2 migration

Branch: `feature/ares-migration`.

**Status: Speedling All-In validated in-game** — win vs VeryHard Terran at 5:10 on the
first run after migration (`data/None-zerg.json`, `Result: 2`). UpgradeRush has
still never been played; see "What still needs a real game" below.

Everything else was verified by importing and exercising the code against the
real ares-sc2 source (v3.13.1) rather than by playing a match.

## What was removed

| Path | Why |
|------|-----|
| `sc2/` (~50 files) | Vendored python-sc2. Ares ships its own fork; keeping this risks shadowing it. |
| `config.py` | Replaced by `config.yml` (`LocalGame:` section) + `zerg_builds.yml`. |
| `create_ladder_zip.py` | Replaced by `scripts/create_ladder_zip.py` from the ares template. |
| `requirements.txt` | Replaced by `pyproject.toml` / Poetry. |
| `update_sc2_library.sh` | Replaced by `scripts/update_ares.py`. |
| `upgrade_to_ares.py` | The one-shot migration helper. This migration is that script's job, done by hand. |
| `bot/bot.py`, `bot/common/helpers.py` | Superseded by `bot/main.py` and ares APIs. |
| `bot/managers/{economy,production}.py` | Replaced by ares macro behaviors (table below). |

## What replaced the hand-rolled logic

| Old code | Now |
|----------|-----|
| `EconomyManager` worker/gas assignment, gas pulls, long-distance mining | `Mining` behavior |
| `ProductionManager.train_overlords` | `AutoSupply` |
| `train_drones` + drone caps + saturation | `BuildWorkers` (cap scaled per ready base) |
| `expand_bases`, `get_next_expansion` | `ExpansionController` |
| `build_extractor` placement/timing | `GasBuildingController` |
| `build_evolution_chambers`, `morph_lair`, `build_infestation_pit`, `morph_hive`, `research_upgrades` | `UpgradeController` with `auto_tech_up_enabled` — it builds the evo chamber, morphs lair and hive on its own as it walks the upgrade list |
| `train_zerglings` larva juggling | `SpawnController` |
| `train_queens`, `inject_larva` | `bot/behaviors/train_queens.py`, `bot/behaviors/inject_larva.py` (ares has no inject behavior) |
| `CombatManager._wave_sent_tags` | `UnitRole.DEFENDING` / `UnitRole.ATTACKING` |
| manual squad splitting, attack-move spam | `mediator.get_squads()` + `CombatManeuver` group behaviors |
| `_home_patrol_points`, `_behind_mineral_line` | `mediator.get_behind_mineral_positions()` |
| `_natural_base`, `_enemy_third_pos`, `_enemy_natural_pos` | `mediator.get_own_nat` / `get_enemy_nat` / `get_enemy_third` |
| `_assign_expansion_scouts`, `_send_army_to_townhalls` | sweep of `mediator.get_enemy_expansions` in `CombatManager.attack_target` |
| `ScoutingManager` (empty placeholder) | real overlord scout on `mediator.get_ol_spot_near_enemy_nat` |

The build openings moved out of Python entirely into `zerg_builds.yml`, run by
ares' `BuildOrderRunner`. `FORCE_BUILD` is gone; force an opening with
`Debug: True` plus the `test_123` cycle.

## Behaviour changes worth knowing about

These are places where ares does something deliberately different from the old
code. They are the most likely causes if a game looks wrong.

1. **Supply is less tight.** The old ling rush used
   `OVERLORD_SUPPLY_LEFT = 2`. Ares' `AutoSupply` scales with townhall count
   and starts overlords earlier. Safer, slightly slower.
2. **Gas pull-off is restored** (it was missing in the first ares version).
   `Mining(workers_per_gas=...)` is *not* the lever — ares only reads that
   value when deciding whether to vespene-boost; the `ResourceManager` owns
   assignment. `bot/behaviors/set_gas_workers.py` calls
   `mediator.set_workers_per_gas`, which the manager honours by pulling one
   worker per frame off any over-staffed geyser. Speedling All-In gates it on
   `vespene >= 100 OR ling speed started`, so spending the 100 does not send
   drones back to the geyser.
3. **Openings are shorter.** They end at speed/queen (Speedling All-In) and second gas
   (UpgradeRush); everything after is the dynamic macro plan. The old code had
   the whole game hard-coded in `on_step` ordering.
4. **One macro action per frame.** A `MacroPlan` short-circuits on the first
   behavior that acts, so `MacroManager.behaviors()` order is the spending
   priority. At `GameStep: 2` that is roughly 11 actions/second.
5. **`BuildStructure` cannot place an in-base hatchery on Zerg.** It defers to
   ares' `_do_zerg_build_placement`, which searches 30 tiles from the base
   location and settles on the natural. `bot/behaviors/zerg/build_macro_hatch.py`
   does its own tight search instead, constrained to the main's terrain height
   and kept clear of every expansion.
6. **Double evo chambers** come from `EVO_COUNT = 2` +
   `BuildStructure(to_count=2)`, because `UpgradeController` alone only ever
   builds one. Two are needed for +1 melee and +1 carapace in parallel.
6. **Wave 1 gating is unchanged in intent**: Speedling All-In leaves on ling speed;
   UpgradeRush additionally waits until both +1s are within 10s of finishing
   (`PLUS1_RESEARCH_TIME = 114.0` is still the hardcoded estimate).
7. **`already_pending_upgrade` returns a 0.0-1.0 float**, and the +1/+1 timing
   maths depends on that. It is used the same way the old code used it.

## What was verified

Against a real ares-sc2 3.13.1 checkout with its actual dependencies:

- `bot.main.KauKauBot` imports and instantiates.
- Every `mediator` member, behavior dataclass field, `UnitRole`,
  `UnitTreeQueryType` and `AresBot` hook signature used by this bot exists and
  matches.
- Both openings parse through ares' own `BuildOrderParser` into exactly the
  intended steps (`gas` -> `EXTRACTOR`, `expand` -> `HATCHERY`, `*4` expands).
- `MacroManager.behaviors()` constructs for both builds, in both
  speed-secured states; drone/gas/queen targets scale as intended.
- `CombatManager` wave promotion fires at the size threshold and not below,
  holds without ling speed, holds UpgradeRush wave 1 until +1/+1 is close,
  targets a visible enemy townhall over a focus point, and issues maneuvers
  for defenders and attacking squads.
- `InjectLarva` and `TrainQueens` behave correctly across ten cases each
  (buffed hatch, low energy, no pool, busy hatch, pending queens, and the
  `unit_tags_received_action` guard).
- `run.py --validate` still composes: MRO is
  `ValidatedKauKauBot -> ZergRushValidator -> KauKauBot -> AresBot`, so
  `super(KauKauBot, self)` resolves to `AresBot`.
- `ruff check` (F, E9, W) clean; everything byte-compiles.

## What still needs a real game

- **The whole `UpgradeRush` opening.** `BuildSelection: Cycle` only advances
  after a defeat, so while Speedling All-In keeps winning this opening is never
  selected. Force it with `Debug: True` plus reordering the `test_123` cycle.
  Its supply numbers, the double evo chamber, the third hatch and the +1/+1
  wave gate have all never executed.
- **Evo chamber and lair/hive placement**, which only happens in UpgradeRush.
- **Whether the ling rush is leaving value on the table.** It wins, but gas is
  no longer pulled off at 100 vespene (change 2 above), and
  `WAVE1_MIN_SIZE = 6` was a guess — the old code used 1 behind a pre-speed
  natural rally that no longer exists in the same form.
- **`UpgradeRush` economy**, which the old README already flagged as unfinished.

Resolved by the first run: the Speedling All-In opening supply numbers are fine (the
build runner reached the end and handed over), and macro hatch placement works.

## First run

```bash
git submodule update --init --recursive
poetry install --no-root
poetry run python run.py --validate
```

Read the `[mm:ss]` lines: `START`, `COMPLETE spawningpool`, `OPENING complete`,
`WAVE 1 attack`. If `OPENING complete` never appears, a build step is stuck —
that is the supply-number problem described above.
