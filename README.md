# KauKauBot

**_you cook, we eat_**

StarCraft II bot for [AI Arena](https://aiarena.net/), built on
[ares-sc2](https://github.com/AresSC2/ares-sc2). Ships as Terran
(`MyBotRace` in `config.yml`); Zerg openings remain in-tree for a race flip.

## Status

| Opening | Intent | State |
|---------|--------|-------|
| Four Rax Proxy | Proxy Barracks Marine all-in at the enemy fourth | Ladder Ready |
| Speedling All-In | Overlord, pool, gas, metabolic boost, macro hatch, zergling flood into the enemy main | Ready (set `MyBotRace: Zerg`) |
| Upgrade Rush | Zergling flood by maximizing upgrades with double evo | Ready (set `MyBotRace: Zerg`) |

## Getting started

**Needs:** Python 3.11 or 3.12, Git, StarCraft II, and the AI Arena map pack
in your SC2 Maps folder.

### Install Poetry

Poetry doesn't come with Python — every `poetry` command below assumes it's
already installed. Use the official installer:

```bash
# macOS / Linux / WSL
curl -sSL https://install.python-poetry.org | python3 -
```

```powershell
# Windows (PowerShell)
(Invoke-WebRequest -Uri https://install.python-poetry.org -UseBasicParsing).Content | py -
```

**If `poetry` then comes back "not recognized"** (common on Windows — the
installer's shim didn't land on PATH), don't fight PATH: install via pip
instead, and swap `py -m poetry ...` in for `poetry ...` in every command
below — it works identically:

```powershell
py -m pip install --user poetry
py -m poetry --version   # confirms it's reachable this way
```

### Clone with the `ares-sc2` submodule

> **Don't `git clone` without `--recursive`.** This repo vendors ares-sc2 as
> a git submodule; a plain clone leaves `ares-sc2/` empty and every step
> after this one fails with confusing `ModuleNotFoundError`s that don't
> obviously point back to a missing submodule.

```bash
git clone --recursive git@github.com:jursun/kau-kau-bot.git
cd kau-kau-bot
```

Already cloned without `--recursive`? Fetch the submodule now rather than
re-cloning:

```bash
git submodule update --init --recursive
```

### Install dependencies

```bash
poetry install --no-root
```

`--no-root` is deliberate: the root package declaration only exists so Poetry
can resolve the `ares-sc2` path dependency, and `run.py` puts the submodule on
`sys.path` itself.

Set `LocalGame.MapPath` in `config.yml` if your Maps folder is non-standard.

Ares pins Python `>=3.11,<3.13`, so 3.13 will not resolve. On Windows, point
Poetry at a supported interpreter by absolute path — `poetry env use 3.11`
usually fails to discover it:

```powershell
$py311 = py -3.11 -c "import sys; print(sys.executable)"
py -m poetry env use $py311
py -m poetry install --no-root
```

### Play / validate

```bash
# vs the built-in computer, faster than realtime
poetry run python run.py

# with rush milestone checks (prefer UTF-8 logs on Windows)
set PYTHONUTF8=1
poetry run python run.py --validate
```

`run.py` flags override `config.yml`: `--map`, `--opponent-race`,
`--difficulty`, `--realtime`.

Cross-check registered builds against `<race>_builds.yml` (no pytest required):

```bash
python -m tests.test_builds
```

### Force an opening locally

Ares picks the opening (`BuildSelection: Cycle` in the active
`<race>_builds.yml`) and keeps it while it wins, switching after a defeat —
**by name**, not by cycle position. `DataManager._choose_opening_cycle`
looks up the last opening in `data/<opponent_id>-<race>.json` and, if that
opening is still in the cycle and won, repeats it no matter where it sits
in the list.

To force a specific opening:

1. Set `Debug: True` in `config.yml` so local games use the `test_123` cycle.
2. Put the opening you want first under `BuildChoices.test_123.Cycle` in
   `terran_builds.yml` or `zerg_builds.yml` (YAML key for Upgrade Rush is
   still `UpgradeRush`).
3. Clear the local data file for that opponent id (`data/None-terran.json` /
   `data/None-zerg.json` for a plain local game, or the whole `data/`
   folder). With no history, ares falls back to cycle position 0.

This replaces the old `FORCE_BUILD` in `config.py`.

**Set `Debug` back to `False` before building a ladder zip** —
`scripts/create_ladder_zip.py` asserts on it.

### Updating ares

```bash
poetry run python scripts/update_ares.py
```

## Layout

```
bot/
  main.py              # hooks only - never edited to add a build
  core/                # engines, context, registry, role tables
  builds/
    definition.py      # BuildDefinition / Economy / Army / Combat
    zerg/              # one module per build, each exporting BUILD
    terran/            # Four Rax Proxy
    protoss/           # empty
  steps/               # macro step factories (common.py + one per race)
  routines/            # combat / scouting routines and reusable gates
  behaviors/zerg/      # custom ares Behaviors (inject, queens)
ares-sc2/              # git submodule (framework + its python-sc2 fork)
config.yml             # ares config + local play settings (`MyBotRace`)
terran_builds.yml      # Terran openings (live when MyBotRace: Terran)
zerg_builds.yml        # Zerg openings (live when MyBotRace: Zerg)
run.py / ladder.py     # local play and AI Arena entry points
tests/test_builds.py   # registry <-> YAML consistency check
```

A build is **data, not a subclass**: it declares which steps and routines it
uses and in what order. Adding one means adding a single file under
`bot/builds/<race>/` — the registry discovers it automatically.
See [ARCHITECTURE.md](ARCHITECTURE.md) for the full pattern and a template.

## How the bot is wired

1. Ares' `BuildOrderRunner` runs the opening from the active
   `<race>_builds.yml` (`terran_builds.yml` when `MyBotRace: Terran`).
2. `KauKauBot.on_start` looks up `build_order_runner.chosen_opening` in the
   registry and builds a `BotContext` around the matching `BuildDefinition`.
3. `MacroEngine` registers `build.always` every frame, then — once the opening
   finishes — a `MacroPlan` of `build.macro_steps`.
4. `CombatEngine` runs `build.combat.routines`, which move units between
   `UnitRole.DEFENDING` and `UnitRole.ATTACKING` and drive them with ares
   `CombatManeuver`s.

A `MacroPlan` short-circuits on the first behavior that acts, so the order of
`macro_steps` *is* the spending priority.

## Ladder package

```bash
poetry run python scripts/create_ladder_zip.py
```

`Debug` must be `False` in `config.yml` — the script asserts on it. Pushing to
`main` also builds the zip via `.github/workflows/ladder_zip.yml`, and will
upload to AI Arena once `AutoUploadToAiarena: True` and the `UPLOAD_API_TOKEN` /
`UPLOAD_BOT_ID` repo secrets are set.

## Development notes

- Every python-sc2 hook you override **must** call
  `await super(KauKauBot, self).<hook>(...)` first. That super call is what
  drives ares' managers, build runner and behavior executioner.
  `on_upgrade_complete` is the exception — ares does not override it.
- `self.mediator` only exists after ares' `on_start` has run, which is why the
  managers are constructed there rather than in `__init__`.
- Ares writes opening win/loss data to `./data` (gitignored). Delete it to
  reset opening selection.
- Prefer a working ladder bot over an unfinished clever one.

## License

See [LICENSE](LICENSE).
