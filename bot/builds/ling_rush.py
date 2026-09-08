"""12-pool zergling rush — finalized.

Opening (overlord → pool → extractor):
  - First overlord, then pool (~12–14), then extractor
  - Exactly 3 drones on gas; pull to minerals at 100 gas (no over-gather)
  - Metabolic boost as soon as pool is ready and 100 gas is up
  - Macro hatch in main after speed is secured
  - Pre-speed home scout / natural rally; waves into enemy main after speed
"""


class LingRush:
    """Finalized 12-pool ling rush plan."""

    NAME = "ling_rush"
    LABEL = "12-pool Ling Rush"

    POOL_SUPPLY = 12
    EXTRACTOR_SUPPLY = 12  # after overlord + pool started
    DRONE_TARGET = 16
    DRONE_TARGET_PER_BASE = 16
    MAX_BASES = 1  # main only; larva from MACRO_HATCH in main
    MACRO_HATCH_COUNT = 2  # main + macro hatch
    MACRO_HATCH_NEAR_DISTANCE = 6
    EVO_COUNT = 0
    EXTRACTORS_PER_BASE = 1
    OVERLORD_SUPPLY_LEFT = 2
    QUEEN_INJECT_ENERGY = 25
    POOL_NEAR_DISTANCE = 5
    GAS_WORKER_COUNT = 3
    METABOLIC_BOOST_GAS = 100
    # Economy
    PULL_GAS_AFTER_SPEED = True  # pull at 100 gas / speed affordable
    LONG_DISTANCE_MINE = False
    # Combat waves
    WAVE1_MIN_SIZE = 1  # send after pre-speed natural rally
    WAVE_ATTACK_MODE = "main"  # enemy main
