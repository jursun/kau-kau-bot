"""Upgrade-rush plan constants.

Economy focus (Burny-style): per-hatch drones + expand; no distribute_workers.
Local end goal: 100 workers.
"""


class UpgradeRush:
    """Tech-heavy mass ling plan (tech/lings paused while economy is rebuilt)."""

    NAME = "upgrade_rush"
    LABEL = "Upgrade Rush (mass lings)"

    POOL_SUPPLY = 12
    EXTRACTOR_SUPPLY = 11
    DRONE_TARGET = 16
    DRONE_TARGET_PER_BASE = 16
    WORKERS_PER_MINERAL = 2
    GAS_WORKER_COUNT = 3
    EXTRACTORS_PER_BASE = 2
    WORKERS_PER_BASE_TARGET = 22
    MAX_BASES = 5  # ~22*5 covers 100 workers
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
    WAVE_ATTACK_MODE = "natural_main"
    GOAL_WORKERS = 100
