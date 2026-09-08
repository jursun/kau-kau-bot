from sc2.bot_ai import BotAI
from sc2.data import Result
from sc2.ids.unit_typeid import UnitTypeId
from sc2.unit import Unit

from bot.builds import choose_build
from bot.builds.ling_rush import LingRush
from bot.managers.economy import EconomyManager
from bot.managers.production import ProductionManager
from bot.managers.combat import CombatManager
from bot.managers.scouting import ScoutingManager
from bot.common.log import log_event


class CompetitiveBot(BotAI):
    """Main bot — picks ling rush or upgrade rush at random on game start."""

    def __init__(self):
        super().__init__()
        # Default until on_start (imports / validate may construct early)
        self.build_plan = LingRush
        self.economy = EconomyManager(self)
        self.production = ProductionManager(self)
        self.combat = CombatManager(self)
        self.scouting = ScoutingManager(self)
        self._macro_goal_done = False

    async def on_start(self):
        self.build_plan = choose_build()
        print(f"Game started — build={self.build_plan.LABEL}")
        log_event(
            self,
            f"START race={self.race} map={self.game_info.map_name} build={self.build_plan.NAME}",
        )

    async def on_building_construction_complete(self, unit: Unit):
        if unit.type_id == UnitTypeId.SPAWNINGPOOL:
            log_event(self, "COMPLETE spawning pool")
        elif unit.type_id == UnitTypeId.EXTRACTOR:
            log_event(self, "COMPLETE extractor")
        elif unit.type_id == UnitTypeId.HATCHERY:
            n = self.townhalls.amount
            if getattr(self.build_plan, "NAME", "") == "ling_rush" and n >= 2:
                log_event(self, "COMPLETE macro hatch")
            else:
                log_event(self, f"COMPLETE hatchery (bases={n})")
        elif unit.type_id == UnitTypeId.LAIR:
            log_event(self, "COMPLETE lair")
        elif unit.type_id == UnitTypeId.HIVE:
            log_event(self, "COMPLETE hive")
        elif unit.type_id == UnitTypeId.EVOLUTIONCHAMBER:
            n = self.structures(UnitTypeId.EVOLUTIONCHAMBER).amount
            log_event(self, f"COMPLETE evolution chamber ({n})")
        elif unit.type_id == UnitTypeId.INFESTATIONPIT:
            log_event(self, "COMPLETE infestation pit")
        elif unit.type_id == UnitTypeId.SPINECRAWLER:
            log_event(self, "COMPLETE spine crawler")

    async def on_upgrade_complete(self, upgrade):
        log_event(self, f"COMPLETE upgrade {upgrade.name}")

    async def on_step(self, iteration: int):
        # Ling rush: pool before first overlord; upgrade rush keeps overlord-first fine
        if getattr(self.build_plan, "NAME", "") == "ling_rush":
            # Overlord → pool → extractor (extractor gated on pool_started)
            self.production.train_overlords()
            await self.production.build_pool()
            self.economy.build_extractor()
        else:
            self.production.train_overlords()
            await self.production.build_pool()
        self.production.research_upgrades()
        await self.production.build_evolution_chambers()
        self.production.morph_lair()
        await self.production.build_infestation_pit()
        self.production.morph_hive()
        await self.production.build_macro_hatch()
        await self.production.expand_bases()
        self.production.train_queens()
        self.economy.inject_larva()
        self.economy.build_extractor()
        self.production.train_drones()
        self.production.train_zerglings()
        await self.combat.manage()
        await self.economy.manage_workers()
        await self.scouting.execute()
        if (
            getattr(self.build_plan, "NAME", "") == "upgrade_rush"
            and not self._macro_goal_done
            and self.production.check_macro_goal()
        ):
            self._macro_goal_done = True
            # End local game once macro goal hit (debug DeclareVictory)
            try:
                await self.client.debug_declare_victory()
                log_event(self, "LOCAL END macro goal (DeclareVictory)")
            except Exception as e:
                log_event(self, f"macro goal reached but could not end game: {e}")

    async def on_end(self, result: Result):
        log_event(self, f"END result={result}")
        print("Game ended.")

