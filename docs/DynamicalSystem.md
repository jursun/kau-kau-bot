# Dynamical System — tactical swarm plan

Future implementation plan for **per-unit tactical decisions** in Macro Zerg
(and eventually other comps). Not started; this is the design SoT until a
slice lands.

## Vision

In tactical battles, each army unit decides on its own game step.

- **Goal** is shared (where the fight should go).
- **Behavior** is individual: each unit reads game state in its immediate
  vicinity and picks an action.
- The swarm *looks* coordinated because units couple through shared goals and
  overlapping local fields — not because a central planner micro-manages
  every tag.

This is a continuous / reactive controller under the existing role + leave
machinery, not a replacement for macro, intel latches, or production.

## Problem it addresses

Today `attack_squads` mixes:

- Squad-level group behaviors (`KeepGroupSafe`, `StutterGroupForward`,
  `AMoveGroup`)
- A few per-unit escapes (Roach kite, burrow regen, Corruptor escort)

That works until force composition, choke geometry, or HP scatter make one
group order wrong for half the ball. Live thrash (re-issue every frame,
KeepUnitSafe flipping points, melee peeling forever) showed that **sticky
per-unit intent** beats smarter global re-planning.

A dynamical / local-decision layer makes that the default for engage micro.

## Design principles

1. **Goal writer is rare; unit readers are every step.** Leave, muster,
   attack objective, early_aggression stay discrete outer loops.
2. **Local sensors only.** No perfect global optimization in the unit
   policy. Vicinity radius ≈ weapon range + kite margin.
3. **Small action set.** Discrete intents map to ares behaviors / one SC2
   order — not free-form path math that fights BuildingManager-style
   cancel loops.
4. **Sticky destinations.** Re-issue only when intent or target tile changes
   beyond a hysteresis threshold (same lesson as ling scouts, queen tumors,
   burrowed Roach path home).
5. **Type-colored gains, shared loop.** One `decide(unit, goal, local)`;
   Roach / Zergling / Queen / Corruptor differ by parameters, not forks of
   unrelated control flow.
6. **Do not thrash roles.** Permanent scouts (`ZERGLING_SCOUT_ROLE`) and
   home defenders stay outside this system until explicitly opted in.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Outer (unchanged)                                      │
│  roles · leave / wave · early_aggression · muster       │
│  targeting.squad_destination / attack_objective         │
└───────────────────────────┬─────────────────────────────┘
                            │ GoalSnapshot (read-only)
                            ▼
┌─────────────────────────────────────────────────────────┐
│  Dynamical engage (new)                                 │
│  for unit in ATTACKING ∩ opt-in types:                  │
│    local = sense(unit, vicinity)                        │
│    intent = policy(unit.type, goal, local, unit_state)  │
│    maybe_issue(unit, intent)  # sticky                  │
└───────────────────────────┬─────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│  ares / SC2                                             │
│  ShootTargetInRange · PathUnitToTarget · AMove · …      │
└─────────────────────────────────────────────────────────┘
```

Squad grouping can remain for **goal assignment** (one destination per
squad) while **execution** becomes per-unit.

### GoalSnapshot (shared, slow)

Written once per squad (or once per step from existing targeting):

| Field | Source (today) | Notes |
|-------|----------------|-------|
| `destination` | `targeting.squad_destination` | Fixed-ish attack point |
| `mode` | leave / defend / muster | Discrete; units bias, don’t invent |
| `focus_hint` | optional HVT tag / type | Soft preference for shoot target |
| `never_retreat` | build flag | Melee comps: no influence peel |

Units never write the goal.

### LocalState (per unit, every step)

Suggested sensors (keep cheap; cython distance queries):

| Sensor | Purpose |
|--------|---------|
| Nearest threat (army, not structure-first) | Engage / kite |
| Threat distance vs weapon range | Shoot vs close vs peel |
| Weapon cooldown ready? | Stutter vs move |
| Own HP% | Dig-in / back off (Roach) |
| Ally count / centroid in ~5–8 | Cohesion / surround |
| Ground influence underfoot (optional) | Unsafe tile bias |
| Distance to goal | Goal tether so locals don’t wander |

Workers stripped from “threat” the same way `bot.intel.enemy_army` does for
squad engage.

### Intent (discrete action)

Start minimal:

| Intent | Typical order |
|--------|----------------|
| `SHOOT` | Attack current / best in-range target |
| `ADVANCE` | Path / AMove toward goal (or surround offset) |
| `KITE` | Path away from nearest threat along goal-biased retreat |
| `HOLD` | No re-issue if already correct |

Later (type-specific): `BURROW_REGEN`, `SURROUND_OFFSET`, `FOCUS_AIR`.

### UnitDynamicsState (persisted)

In `RunState` (or a small module dict keyed by tag):

- Last intent + last target key (point rounded / unit tag)
- Optional smoothed velocity / desired point for hysteresis
- Clear on `forget_destroyed`

Sticky rule: if new intent and target key match last within threshold →
do not register a new behavior.

## Integration with current code

| Existing | Relationship |
|----------|----------------|
| `combat.attack_squads` | Outer shell: muster, squad destination, who is ATTACKING. Engage branch optionally delegates to dynamical policy for `kite_types` / opt-in set. |
| `_kite_maneuver` / Roach kite | First policy specialization to absorb. |
| `regen_burrow_roaches` | Stays a prior routine (or becomes `BURROW_REGEN` intent) so dig-in still wins over kite/shoot. |
| `never_retreat=True` | Goal flag: policy omits influence peel / KITE-from-grid. |
| `KeepGroupSafe` group path | Optional fallback for non-opt-in types until migrated. |
| Permanent ling scouts | **Out of scope** — already local kite + park; do not pull into army dynamics. |

Preferred insertion point: inside `attack_squads` after muster/regen skip,
instead of (or before) `_squad_maneuver_*` for opted-in unit types.

## Phased rollout

### Phase 0 — Spec freeze (this doc)

Agree action set, sensors, sticky thresholds, opt-in types.

### Phase 1 — Roach-only prototype

- Opt-in: `ROACH` (surface only; burrowed still owned by regen routine).
- Intents: `SHOOT` / `ADVANCE` / `KITE` / `HOLD`.
- Goal: existing squad destination + `never_retreat` from Macro Zerg.
- Feature flag or build kwarg, e.g. `attack_squads(..., dynamical_types={ROACH})`, default off.
- Unit tests: pure `policy(...)` with fake LocalState (no SC2).
- Live: one `--validate` / quick tier vs Easy; watch thrash detector if Debug on.

**Exit criteria:** Roaches hold a range band when outnumbered without
order-target flip spam; when ahead or on choke, they still ADVANCE/SHOOT
into the ball (no mass peel).

### Phase 2 — Zergling surround

- Add ling gains: stronger ADVANCE, weaker KITE, optional surround offset
  around threat / goal.
- Keep home defenders and scouts excluded.

### Phase 3 — Retire duplicate micro

- Delete or thin `_kite_maneuver` paths covered by policy.
- Optionally drop group stutter for fully migrated comps.

### Phase 4 — (Optional) continuous field

- Explicit potential: attraction to goal, repulsion from threats, mild
  separation from allies; discretize to intents or desired point.
- Only after Phase 1–2 prove sticky local decisions are stable.

## Non-goals

- Replacing macro, inject, creep, or production with dynamics.
- Learning / RL in v1 (hand-tuned gains first).
- Per-frame perfect pathfinding; ares grids remain the path oracle.
- Centralized assignment of every unit to a unique enemy (can be a later
  soft focus_hint, not required for v1).

## Testing & QA

- **Unit:** `policy` and sticky `maybe_issue` with mocks (intent transitions,
  hysteresis, never_retreat).
- **Live:** Debug thrash probe (`bot.debug.thrash`) on ATTACKING Roaches —
  flips should drop vs pre-change baselines.
- **Validate:** Influence Parking still meaningful for types that may peel;
  Macro Zerg `never_retreat` comps should not regress Wave 1 timing.
- Prefer quick/smoke with `--validate` before any default-on flip.

## Open questions

1. Desired point vs discrete intents only — start discrete; add field in
   Phase 4?
2. Should Queens in the attack ball opt in, or stay inject/creep-only?
3. Focus fire: local “lowest HP in range” vs shared `focus_hint` from
   targeting?
4. GameStep 2: is vicinity query cost OK at 80+ supply, or sense every N
   steps with sticky intent between senses?
5. Where do surround offsets live — policy params table per `UnitTypeId`?

## Suggested code layout (when implementing)

```
bot/tactics/
  __init__.py
  goal.py          # GoalSnapshot builders from ctx / squad
  sense.py         # LocalState from unit + ctx
  policy.py        # decide(...) → Intent
  issue.py         # sticky register_behavior mapping
  params.py        # per-type gains / radii
bot/routines/combat.py   # thin hook from attack_squads
tests/tactics/           # pure policy tests
```

Keep `combat.py` as the routine façade; avoid growing it with another
500-line micro dialect.

## Success picture

A Roach/Zergling ball advances on one goal; each unit micro-adjusts in its
bubble — shoot when ready, kite when overcommitted, close when free —
without canceling orders every frame, and without permanent scouts or
regen Roaches getting swept into the wrong intent.
