"""Custom ares `Behavior` implementations.

Ares has no inject, queen-production or in-base-hatchery behavior, so those
live here. Race-specific ones go in a race subpackage; race-neutral ones sit
at this level.
"""

from bot.behaviors.set_gas_workers import SetGasWorkers

__all__ = ["SetGasWorkers"]
