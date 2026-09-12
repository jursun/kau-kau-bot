# Influence retreat — validate / QA gate

Ship gate for `soujirou/slice1-influence-retreat` (KeepGroupSafe / KeepUnitSafe
before stutter/AMove/kite, close army from `bot.intel.enemy_army`).

Reject “it should work” without one of: replay, `run.py --validate` stdout, or
`smoke_* --validate`.

## Checklist (supply / idle / influence parking)

1. **Supply Management** — Stage 1 already fails if supply-blocked frames ≥ 50
   after the opening grace period (`BaseValidator.SUPPLY_BLOCK_GRACE_PERIOD`).
2. **Idle Townhalls** — Stage 5 (Terran/Protoss only): ready townhalls sitting
   `is_idle` for ≥ ~2s of frames after grace. **Skipped for Zerg** — hatcheries
   stay `is_idle` while larva morphs (false-FAIL otherwise).
3. **Influence Parking** — Stage 5: ATTACKING units on unsafe ground influence
   (`mediator.is_position_safe` false) for ≥ ~2s of frames after grace. Retreat
   should clear this; sustained parking is a FAIL.

## Quirk notes

- Workers never pad close-army / force — use `bot.intel.enemy_army`.
- `air_scout` must still **not** use KeepUnitSafe (watch vs survive).
- Tags only across frames — never stash Unit objects from the army feed.
- Validate SoT = MAIN-PC with `ares-sc2` submodule populated.

## Helpers

`bot.intel.qa`: `is_supply_blocked`, `idle_ready_townhalls`,
`units_parked_in_influence`, `influence_parking_tags`.
