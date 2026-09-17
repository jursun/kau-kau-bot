"""Custom ares behaviors for Zerg."""

from bot.behaviors.zerg.build_macro_hatch import BuildMacroHatch
from bot.behaviors.zerg.build_spore_crawler import BuildSporeCrawler
from bot.behaviors.zerg.expand_with_persistent_builder import (
    ExpandWithPersistentBuilder,
)
from bot.behaviors.zerg.gas_building_controller import ZergGasBuildingController
from bot.behaviors.zerg.inject_larva import InjectLarva
from bot.behaviors.zerg.morph_lair_at_main import MorphLairAtMain
from bot.behaviors.zerg.morph_overseers import MorphOverseers
from bot.behaviors.zerg.train_from_larva import TrainFromLarva, pending_larva_trained
from bot.behaviors.zerg.train_queens import TrainQueens
from bot.behaviors.zerg.upgrade_slots import UpgradeSlots, count_pending_upgrades

__all__ = [
    "BuildMacroHatch",
    "BuildSporeCrawler",
    "ExpandWithPersistentBuilder",
    "InjectLarva",
    "MorphLairAtMain",
    "MorphOverseers",
    "TrainFromLarva",
    "TrainQueens",
    "UpgradeSlots",
    "ZergGasBuildingController",
    "count_pending_upgrades",
    "pending_larva_trained",
]
