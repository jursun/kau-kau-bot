# KauKauBot — next week

Notes from Jason (2026-09-07 wrap), re-scoped after the ares-sc2 migration:

1. **Add scouting for early rush and defensive behavior**
   Partly done: an overlord now sits on `mediator.get_ol_spot_near_enemy_nat`.
   Ares also exposes rush intel for free — `mediator.get_enemy_ling_rushed`,
   `get_enemy_worker_rushed`, `get_enemy_marine_rush`, `get_did_enemy_rush` —
   which `CombatManager.attack_ready()` could read to hold a wave home.
2. **Add spore crawlers to mineral lines**
   Not done. `BuildStructure(base_location=th.position,
   structure_id=UnitTypeId.SPORECRAWLER, static_defence=True,
   to_count_per_base=1)` in `MacroManager.structure_behaviors()`.
3. **Fix 3rd and 4th Extractor timing**
   Now handled by `GasBuildingController(to_count=...)`, driven by
   `EXTRACTORS_PER_BASE` / `MAX_EXTRACTORS` on the plan. Needs a real game to
   confirm the timing is sane.
4. **More zerglings after the 2nd Evo Chamber starts**
   `SpawnController` sits last in the `MacroPlan`, so anything above it can
   starve it. If lings are thin after the evos, either raise ling priority or
   cap `BuildWorkers` sooner via `DRONE_TARGET_PER_BASE`.
5. **Refactor builds so each build has its own managers, over a common base**
   Done, then taken further. Subclassing was replaced by composition: a build
   is now a declarative `BuildDefinition` listing which steps and routines it
   uses, and the engines just run that list. Adding a build is one file under
   `bot/builds/<race>/`. See [ARCHITECTURE.md](ARCHITECTURE.md).

## New, from the migration

- Validate both openings in-game — see the "What still needs a real game"
  section in [MIGRATION.md](MIGRATION.md).
- Gas pull-off after speed is now implemented (`c.gas_workers(pull_off=...)`
  in the Speedling All-In build). Worth eyeballing the timing on a real run.
