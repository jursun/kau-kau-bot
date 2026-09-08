"""Upgrade-rush plan constants.

Early opening (Jason authoritative):
  Keep droning (no soft-starve at 14)
  15 – Hatchery (natural if safe, else in-base)
  16 – Spawning Pool
  17 – Extractor
  19 – Overlord
  @extractor complete – yank 3 minerals→gas
  @pool complete – Metabolic Boost → first lings → 2nd Extractor (yank 3 on complete) → double evo;
                   keep droning natural minerals while first lings morph
  32 – third hatch

Later: +1/+1 / expands. Caps: 4 extractors, 80 drones. Wave1 leaves 10s before both +1s done.
Hatch before pool.
"""


class UpgradeRush:
    """Tech-heavy mass ling plan."""

    NAME = "upgrade_rush"
    LABEL = "Upgrade Rush (mass lings)"

    FIRST_OVERLORD_SUPPLY = 13
    EXPAND_SUPPLY = 15  # 2nd hatch before pool
    POOL_SUPPLY = 16
    EXTRACTOR_SUPPLY = 17
    SECOND_OVERLORD_SUPPLY = 19
    THIRD_HATCH_SUPPLY = 32

    DRONE_TARGET = 16
    GOAL_WORKERS = 80  # hard drone ceiling
    DRONE_TARGET_PER_BASE = 16
    WORKERS_PER_MINERAL = 2
    GAS_WORKER_COUNT = 3
    EXTRACTORS_PER_BASE = 2
    MAX_EXTRACTORS = 4  # hard total cap
    WORKERS_PER_BASE_TARGET = 22
    MAX_BASES = 5
    MACRO_HATCH_COUNT = 0
    MACRO_HATCH_NEAR_DISTANCE = 6
    EVO_COUNT = 2
    OVERLORD_SUPPLY_LEFT = 6
    QUEEN_INJECT_ENERGY = 25
    POOL_NEAR_DISTANCE = 5
    METABOLIC_BOOST_GAS = 100
    PULL_GAS_AFTER_SPEED = False
    LONG_DISTANCE_MINE = True
    WAVE1_MIN_SIZE = 20
    WAVE1_LEAVE_BEFORE_PLUS1 = 10.0
    PLUS1_RESEARCH_TIME = 114.0
    WAVE_ATTACK_MODE = "natural_main"
    INBASE_HATCH_THREAT_RADIUS = 40
