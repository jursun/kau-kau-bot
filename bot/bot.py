from sc2.bot_ai import BotAI
from sc2.data import Result

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId
from sc2.ids.upgrade_id import UpgradeId

from sc2.unit import Unit

class CompetitiveBot(BotAI):
    """Main bot class that handles the game logic."""
    
    def __init__(self):
        
        super().__init__()

    async def on_start(self):
        """
        This code runs once at the start of the game
        Do things here before the game starts
        """
        print("Game started")

    async def on_step(self, iteration: int):
        """
        This code runs continually throughout the game
        Populate this function with whatever your bot should do!
        """

        # Build 12 pool
        if (self.supply_used >= 12
            and (self.structures(UnitTypeId.SPAWNINGPOOL).amount + self.already_pending(UnitTypeId.SPAWNINGPOOL) == 0)
        ):
            map_center = self.game_info.map_center
            position_towards_map_center = self.start_location.towards(map_center, distance=5)
            await self.build(UnitTypeId.SPAWNINGPOOL, near=position_towards_map_center)

        # Build Queens
        if (self.structures(UnitTypeId.SPAWNINGPOOL).ready
            and (self.units(UnitTypeId.QUEEN).amount + self.already_pending(UnitTypeId.QUEEN) < self.townhalls.amount)
        ):
            self.train(UnitTypeId.QUEEN)

        # Queen inject larva
        main: Unit = self.townhalls.first
        if (self.units(UnitTypeId.QUEEN).ready):
            for queen in self.units(UnitTypeId.QUEEN).idle:
                if (queen.energy >= 25 and not main.has_buff(BuffId.QUEENSPAWNLARVATIMER)):
                    queen(AbilityId.EFFECT_INJECTLARVA, main)

        # Build Overlords
        if (self.supply_left <= 2
            and (self.structures(UnitTypeId.SPAWNINGPOOL).amount + self.already_pending(UnitTypeId.SPAWNINGPOOL) != 0)
            and self.already_pending(UnitTypeId.OVERLORD) == 0
        ):
            self.train(UnitTypeId.OVERLORD)

        # Build Extractor
        if (self.supply_used >= 11
            and (self.structures(UnitTypeId.SPAWNINGPOOL).amount + self.already_pending(UnitTypeId.SPAWNINGPOOL) != 0)
            and self.structures(UnitTypeId.EXTRACTOR).amount == 0
        ):
            self.workers.random.build(UnitTypeId.EXTRACTOR, self.vespene_geyser.closest_to(self.townhalls.first))

        # Build Workers
        if (self.supply_workers < 19
            and self.larva.amount > 0
        ):
            self.train(UnitTypeId.DRONE, 1)

        # Build Zerglings
        if (self.structures(UnitTypeId.SPAWNINGPOOL).ready
            and self.larva.amount > 0
        ):
            self.train(UnitTypeId.ZERGLING, self.larva.amount)

        # Send Zerglings to attack
        if (self.units(UnitTypeId.ZERGLING).ready
            and self.already_pending_upgrade(UpgradeId.ZERGLINGMOVEMENTSPEED) == 1
        ):
            for zergling in self.units(UnitTypeId.ZERGLING).idle:
                zergling.attack(self.enemy_start_locations[0])

        # Build movement speed upgrade
        if (self.already_pending_upgrade(UpgradeId.ZERGLINGMOVEMENTSPEED) == 0
            and self.structures(UnitTypeId.SPAWNINGPOOL).ready
        ):
            self.research(UpgradeId.ZERGLINGMOVEMENTSPEED)

        # Send Workers to gather
        await self.distribute_workers()

        pass


    async def on_end(self, result: Result):
        """
        This code runs once at the end of the game
        Do things here after the game ends
        """
        print("Game ended.")
