# KauKauBot — next week

Notes from Jason (2026-09-07 wrap), re-scoped after the ares-sc2 migration:

1. **Add scouting for early rush and defensive behavior**
   Partly done: an overlord now sits on `mediator.get_ol_spot_near_enemy_nat`.
   Ares also exposes rush intel for free — `mediator.get_enemy_ling_rushed`,
   `get_enemy_worker_rushed`, `get_enemy_marine_rush`, `get_did_enemy_rush` —
   which `CombatManager.attack_ready()` could read to hold a wave home.
2. **Add spore crawlers to mineral lines**
   Done for UpgradeRush: `zerg.spore_crawlers(per_base=1,
   gate=gates.after_time(240.0))`, one `BuildStructure` call per ready
   townhall. Not yet wired into Speedling All-In or confirmed in a real game.
3. **Fix 3rd and 4th Extractor timing**
   Now handled by `GasBuildingController(to_count=...)`, driven by
   `EXTRACTORS_PER_BASE` / `MAX_EXTRACTORS` on the plan. Needs a real game to
   confirm the timing is sane.
4. **More zerglings after the 2nd Evo Chamber starts**
   Done for UpgradeRush via `common.split_production()`: once wave 1 is out,
   `BuildWorkers`/`SpawnController` swap priority each frame based on which
   side (by supply) is behind, instead of `SpawnController` always sitting
   last and starving. Not yet applied to Speedling All-In, which still gives
   drones outright priority throughout.
5. **Refactor builds so each build has its own managers, over a common base**
   Done, then taken further. Subclassing was replaced by composition: a build
   is now a declarative `BuildDefinition` listing which steps and routines it
   uses, and the engines just run that list. Adding a build is one file under
   `bot/builds/<race>/`. See [ARCHITECTURE.md](ARCHITECTURE.md).

## New, from the migration

- Speedling All-In is validated: 9 straight wins vs VeryHard Terran, macro
  hatch placement fixed, gas pull-off confirmed at ~124 vespene (`c.gas_workers`
  in `bot/steps/common.py`), waves 25% bigger each time and mustering at the
  natural before attacking. History archived at `data/archive/` — see
  [MIGRATION.md](MIGRATION.md).
- Now validating `UpgradeRush` in-game — worker/gas caps tightened (60
  workers, 3 extractors), wave gate simplified to just ling speed, waves grow
  25% like Speedling All-In, production splits ~50/50 economy/army after
  wave 1, and Spore Crawlers now go up per-base at 4 minutes. Logging also
  switched from a raw `print()` to loguru's `logger`. See the "What still
  needs a real game" section in [MIGRATION.md](MIGRATION.md).
