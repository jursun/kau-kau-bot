# KauKauBot

Zerg StarCraft II bot for [AI Arena](https://aiarena.net/), built on [python-sc2](https://github.com/BurnySc2/python-sc2). Developed locally against the built-in computer, validated with rush milestones, then packaged for the ladder.

## Status

| Build | Intent | State |
|-------|--------|--------|
| ling_rush | Fast overlord, pool, gas, metabolic boost, early ling waves | **Working** - regularly wins --validate vs VeryHard Terran |
| upgrade_rush | 3-base mass lings with double evo / lair / hive upgrades | **In progress** - production/tech wired; economy stubbed pending rewrite |

Local games pick the build via FORCE_BUILD in config.py (omitted from the ladder zip). On ladder, config.py is absent so the bot picks a build at random.

## Layout

```
bot/
  bot.py              # CompetitiveBot - step orchestration
  builds/
    ling_rush.py      # Opening timings + caps for the ling rush
    upgrade_rush.py   # Plan constants for the upgrade path
    __init__.py       # choose_build() / FORCE_BUILD
  managers/
    economy.py        # Injects, extractors, worker assign (ling rush)
    production.py     # Pool, drones, lings, expands, tech, upgrades
    combat.py         # Pre-speed scout/defense, attack waves
    scouting.py       # Map / expansion scouting helpers
  common/             # Logging + shared helpers
config.py             # Local-only settings (not in ladder zip)
run.py                # Local play + --validate
create_ladder_zip.py  # Package for AI Arena upload
```

## Ling rush (current ladder-ready path)

Target shape that has been validating well:

1. First overlord, then spawning pool (~12-14), then extractor
2. Exactly 3 on gas; pull to minerals at ~100 vespene for speed
3. Hard drone cap **16**; macro hatch after speed is secured
4. Pre-speed home scout (nearby bases / behind mineral lines)
5. Rally at the natural ~15s before speed finishes
6. Wave 1 into the enemy **main** once speed is done (min size ~20), then growing waves

## Local setup

**Needs:** Python 3.8+, Git, StarCraft II, and the AI Arena map pack under your SC2 Maps folder.

`ash
git clone git@github.com:jursun/kau-kau-bot.git
cd kau-kau-bot

python -m venv venv
# Windows
.\venv\Scripts\activate
# Linux/macOS
source venv/bin/activate

pip install -r requirements.txt
`

Set MAP_PATH in config.py if your Maps folder is non-standard.

### Play / validate

`ash
# Faster than realtime vs computer (uses config.py)
python run.py

# Non-realtime + rush milestone checks (prefer UTF-8 logs on Windows)
set PYTHONUTF8=1
python run.py --validate
`

Useful config.py knobs:

- FORCE_BUILD - "ling_rush", "upgrade_rush", or None (random)
- OPPONENT_RACE / OPPONENT_DIFFICULTY - local computer opponent
- MAP_POOL - Persephone / Pylon / Torches AIE maps
- REALTIME - human-speed vs fast sim

## Ladder package

`ash
python create_ladder_zip.py
`

Produces a zip for AI Arena upload. **config.py is excluded** so ladder games never inherit local force-build / debug toggles.

## Development notes

- Prefer a working ladder bot over an unfinished clever one.
- After major bot changes, run python run.py --validate with PYTHONUTF8=1 and check the rush/gas timeline in the log.
- Validate logs (*.log) are gitignored.
- upgrade_rush still needs a fresh economy (gas/minerals per base); do not treat it as ladder-ready yet.

## License

See [LICENSE](LICENSE).
