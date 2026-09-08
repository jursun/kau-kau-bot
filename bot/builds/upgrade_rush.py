"""Upgrade-rush mass zerglings: double evo, lair/hive, 3 bases.

Worker cap: 22 per base (16 minerals + 3 + 3 on two extractors).
After metabolic boost: 50/50 larva into drones vs zerglings (until worker cap).
"""


class UpgradeRush:
    """Tech-heavy mass ling plan."""

    NAME = "upgrade_rush"
    LABEL = "Upgrade Rush (mass lings)"

    POOL_SUPPLY = 12
    EXTRACTOR_SUPPLY = 11
    DRONE_TARGET = 16  # minerals per base
    DRONE_TARGET_PER_BASE = 16
    WORKERS_PER_BASE = 22  # 16 minerals + 3 + 3 gas
    MAX_BASES = 3
    MACRO_HATCH_COUNT = 0
    MACRO_HATCH_NEAR_DISTANCE = 6
    EVO_COUNT = 2
    EXTRACTORS_PER_BASE = 2
    OVERLORD_SUPPLY_LEFT = 2
    QUEEN_INJECT_ENERGY = 25
    POOL_NEAR_DISTANCE = 5
    GAS_WORKER_COUNT = 3
    METABOLIC_BOOST_GAS = 100
    PULL_GAS_AFTER_SPEED = False  # keep gas for upgrade chain
    LONG_DISTANCE_MINE = False  # stay within 16 mineral slots / base
    WAVE1_MIN_SIZE = 20
    WAVE_ATTACK_MODE = "third_natural"
