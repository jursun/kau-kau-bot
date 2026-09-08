"""Manager classes for CompetitiveBot."""

from .economy import EconomyManager
from .production import ProductionManager
from .combat import CombatManager
from .scouting import ScoutingManager

__all__ = [
    "EconomyManager",
    "ProductionManager",
    "CombatManager",
    "ScoutingManager",
]
