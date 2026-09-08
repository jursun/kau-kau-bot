"""Production: pool, structures, units, research â€” behavior depends on bot.build_plan."""

from __future__ import annotations

from typing import List, Tuple

from sc2.bot_ai import BotAI
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from bot.common.helpers import pool_started, pool_pending_or_none
from bot.common.log import log_event

_UPGRADE_STEPS: List[Tuple[UpgradeId, ...]] = [
    (UpgradeId.ZERGLINGMOVEMENTSPEED,),
    (UpgradeId.ZERGMELEEWEAPONSLEVEL1, UpgradeId.ZERGGROUNDARMORSLEVEL1),
    (UpgradeId.ZERGMELEEWEAPONSLEVEL2, UpgradeId.ZERGGROUNDARMORSLEVEL2),
    (UpgradeId.ZERGMELEEWEAPONSLEVEL3, UpgradeId.ZERGGROUNDARMORSLEVEL3),
    (UpgradeId.ZERGLINGATTACKSPEED,),
]

_ALL_UPGRADES: Tuple[UpgradeId, ...] = tuple(u for step in _UPGRADE_STEPS for u in step)


def all_upgrades_complete(bot: BotAI) -> bool:
    return all(bot.already_pending_upgrade(u) == 1 for u in _ALL_UPGRADES)


class ProductionManager:
    def __init__(self, bot: BotAI):
        self.bot = bot
        self._logged_pool_order = False
        self._logged_pool_ready = False
        self._logged_macro_hatch = False
        self._logged_expand = False
        self._logged_evo = False
        self._logged_lair = False
        self._logged_pit = False
        self._logged_hive = False
        self._logged_first_queen = False
        self._logged_first_overlord = False
        self._logged_drone_cap = False
        self._logged_first_lings = False
        self._logged_supply_block = False
        self._logged_upgrades = set()
        self._logged_speed_started = False
        self._logged_speed_done = False

    @property
    def plan(self):
        return self.bot.build_plan

    def _is_upgrade_rush(self) -> bool:
        return getattr(self.plan, "NAME", "") == "upgrade_rush"

    def _hatch_count(self) -> int:
        bot = self.bot
        return (
            bot.townhalls.amount
            + bot.already_pending(UnitTypeId.HATCHERY)
            + bot.already_pending(UnitTypeId.LAIR)
            + bot.already_pending(UnitTypeId.HIVE)
        )

    def _drone_cap(self) -> int:
        plan = self.plan
        if self._is_upgrade_rush():
            bases = max(1, self.bot.townhalls.amount)
            per = getattr(plan, "WORKERS_PER_BASE", 22)
            return per * bases
        # Ling rush: 16 total workers (3 of them sit on gas until speed)
        return plan.DRONE_TARGET

    async def build_pool(self) -> None:
        bot = self.bot
        if pool_pending_or_none(bot) and bot.supply_used >= self.plan.POOL_SUPPLY:
            if bot.can_afford(UnitTypeId.SPAWNINGPOOL):
                near = bot.start_location.towards(
                    bot.game_info.map_center, distance=self.plan.POOL_NEAR_DISTANCE
                )
                built = await bot.build(UnitTypeId.SPAWNINGPOOL, near=near)
                if built and not self._logged_pool_order:
                    log_event(
                        bot,
                        f"ORDER pool (supply={bot.supply_used}, workers={bot.supply_workers})",
                    )
                    self._logged_pool_order = True
        if not self._logged_pool_ready and bot.structures(UnitTypeId.SPAWNINGPOOL).ready:
            log_event(bot, "READY spawning pool")
            self._logged_pool_ready = True

    async def build_macro_hatch(self) -> None:
        """Ling rush: second hatch inside main for larva (after speed is secured)."""
        bot = self.bot
        if self._is_upgrade_rush():
            return
        want = getattr(self.plan, "MACRO_HATCH_COUNT", 0)
        if want <= 0:
            return
        hatch_count = bot.townhalls.amount + bot.already_pending(UnitTypeId.HATCHERY)
        if hatch_count >= want:
            return
        if not bot.structures(UnitTypeId.SPAWNINGPOOL).ready:
            return

        speed = bot.already_pending_upgrade(UpgradeId.ZERGLINGMOVEMENTSPEED)
        # Prefer metabolic boost before the macro hatch
        if speed == 0:
            if bot.can_afford(UpgradeId.ZERGLINGMOVEMENTSPEED):
                return
            # Keep 100 minerals reserved for speed once gas is in hand / close
            if bot.vespene >= 50 and bot.minerals < 400:
                return

        if not bot.can_afford(UnitTypeId.HATCHERY):
            return
        near = bot.start_location.towards(
            bot.game_info.map_center, self.plan.MACRO_HATCH_NEAR_DISTANCE
        )
        built = await bot.build(UnitTypeId.HATCHERY, near=near, max_distance=15)
        if built and not self._logged_macro_hatch:
            log_event(
                bot,
                f"ORDER macro hatch (minerals={bot.minerals}, hatches={hatch_count}+1)",
            )
            self._logged_macro_hatch = True

    async def expand_bases(self) -> None:
        """Upgrade rush: take natural / third up to MAX_BASES."""
        bot = self.bot
        if not self._is_upgrade_rush():
            return
        if self._hatch_count() >= self.plan.MAX_BASES:
            return
        if not bot.structures(UnitTypeId.SPAWNINGPOOL).ready:
            return
        if not bot.can_afford(UnitTypeId.HATCHERY):
            return
        if self._hatch_count() < 2 and bot.supply_workers < 14:
            return
        if self._hatch_count() >= 2 and bot.supply_workers < 22:
            return
        location = await bot.get_next_expansion()
        if not location:
            return
        before = self._hatch_count()
        await bot.expand_now(location=location, max_distance=25)
        after = self._hatch_count()
        if after > before or bot.already_pending(UnitTypeId.HATCHERY) > 0:
            log_event(
                bot,
                f"ORDER expansion (bases={max(after, before + 1)}/{self.plan.MAX_BASES}, "
                f"minerals={bot.minerals})",
            )
            self._logged_expand = True

    async def build_evolution_chambers(self) -> None:
        bot = self.bot
        if not self._is_upgrade_rush():
            return
        if not bot.structures(UnitTypeId.SPAWNINGPOOL).ready:
            return
        if bot.already_pending_upgrade(UpgradeId.ZERGLINGMOVEMENTSPEED) == 0:
            return
        have = (
            bot.structures(UnitTypeId.EVOLUTIONCHAMBER).amount
            + bot.already_pending(UnitTypeId.EVOLUTIONCHAMBER)
        )
        while have < self.plan.EVO_COUNT and bot.can_afford(UnitTypeId.EVOLUTIONCHAMBER):
            near = bot.start_location.towards(bot.game_info.map_center, 8)
            if bot.townhalls:
                near = bot.townhalls.first.position.towards(bot.game_info.map_center, 6)
            built = await bot.build(UnitTypeId.EVOLUTIONCHAMBER, near=near, max_distance=12)
            if not built:
                break
            have += 1
            if not self._logged_evo:
                log_event(bot, f"ORDER evolution chamber ({have}/{self.plan.EVO_COUNT})")
                self._logged_evo = True

    def morph_lair(self) -> None:
        bot = self.bot
        if not self._is_upgrade_rush():
            return
        if bot.structures(UnitTypeId.LAIR).amount + bot.already_pending(UnitTypeId.LAIR):
            return
        if bot.structures(UnitTypeId.HIVE).amount + bot.already_pending(UnitTypeId.HIVE):
            return
        if not bot.structures(UnitTypeId.SPAWNINGPOOL).ready:
            return
        melee1 = bot.already_pending_upgrade(UpgradeId.ZERGMELEEWEAPONSLEVEL1)
        armor1 = bot.already_pending_upgrade(UpgradeId.ZERGGROUNDARMORSLEVEL1)
        if melee1 < 1 and armor1 < 1 and not (melee1 > 0 and armor1 > 0):
            return
        hatches = bot.townhalls(UnitTypeId.HATCHERY).ready.idle
        if not hatches or not bot.can_afford(UnitTypeId.LAIR):
            return
        hatches.first.build(UnitTypeId.LAIR)
        if not self._logged_lair:
            log_event(bot, "ORDER lair")
            self._logged_lair = True

    async def build_infestation_pit(self) -> None:
        bot = self.bot
        if not self._is_upgrade_rush():
            return
        if (
            bot.structures(UnitTypeId.INFESTATIONPIT).amount
            + bot.already_pending(UnitTypeId.INFESTATIONPIT)
        ):
            return
        if not bot.structures(UnitTypeId.LAIR).ready and not bot.structures(UnitTypeId.HIVE):
            return
        melee2 = bot.already_pending_upgrade(UpgradeId.ZERGMELEEWEAPONSLEVEL2)
        armor2 = bot.already_pending_upgrade(UpgradeId.ZERGGROUNDARMORSLEVEL2)
        if melee2 == 0 and armor2 == 0:
            return
        if not bot.can_afford(UnitTypeId.INFESTATIONPIT):
            return
        near = bot.start_location.towards(bot.game_info.map_center, 8)
        if bot.townhalls.ready:
            near = bot.townhalls.ready.first.position.towards(bot.game_info.map_center, 8)
        built = await bot.build(UnitTypeId.INFESTATIONPIT, near=near, max_distance=15)
        if built and not self._logged_pit:
            log_event(bot, "ORDER infestation pit")
            self._logged_pit = True

    def morph_hive(self) -> None:
        bot = self.bot
        if not self._is_upgrade_rush():
            return
        if bot.structures(UnitTypeId.HIVE).amount + bot.already_pending(UnitTypeId.HIVE):
            return
        if not bot.structures(UnitTypeId.INFESTATIONPIT).ready:
            return
        lairs = bot.townhalls(UnitTypeId.LAIR).ready.idle
        if not lairs or not bot.can_afford(UnitTypeId.HIVE):
            return
        lairs.first.build(UnitTypeId.HIVE)
        if not self._logged_hive:
            log_event(bot, "ORDER hive")
            self._logged_hive = True

    def research_upgrades(self) -> None:
        bot = self.bot
        if self._is_upgrade_rush():
            for step in _UPGRADE_STEPS:
                pending_or_done = [bot.already_pending_upgrade(u) for u in step]
                if all(p == 1 for p in pending_or_done):
                    for upgrade in step:
                        key = f"ready:{upgrade.name}"
                        if key not in self._logged_upgrades:
                            log_event(bot, f"READY {upgrade.name}")
                            self._logged_upgrades.add(key)
                    continue
                for upgrade in step:
                    progress = bot.already_pending_upgrade(upgrade)
                    if progress > 0:
                        continue
                    if not bot.can_afford(upgrade):
                        continue
                    started = bot.research(upgrade)
                    key = f"start:{upgrade.name}"
                    if started and key not in self._logged_upgrades:
                        log_event(bot, f"RESEARCH {upgrade.name} started")
                        self._logged_upgrades.add(key)
                return
            return

        # Ling rush: metabolic boost only
        progress = bot.already_pending_upgrade(UpgradeId.ZERGLINGMOVEMENTSPEED)
        if (
            progress == 0
            and bot.structures(UnitTypeId.SPAWNINGPOOL).ready
            and bot.can_afford(UpgradeId.ZERGLINGMOVEMENTSPEED)
        ):
            started = bot.research(UpgradeId.ZERGLINGMOVEMENTSPEED)
            if started and not self._logged_speed_started:
                log_event(bot, "RESEARCH metabolic boost started")
                self._logged_speed_started = True
        elif progress == 1 and not self._logged_speed_done:
            log_event(bot, "READY metabolic boost")
            self._logged_speed_done = True

    def train_queens(self) -> None:
        bot = self.bot
        if (
            bot.structures(UnitTypeId.SPAWNINGPOOL).ready
            and (
                bot.units(UnitTypeId.QUEEN).amount + bot.already_pending(UnitTypeId.QUEEN)
                < bot.townhalls.amount
            )
            and bot.can_afford(UnitTypeId.QUEEN)
        ):
            bot.train(UnitTypeId.QUEEN)
            if not self._logged_first_queen:
                log_event(bot, "ORDER queen")
                self._logged_first_queen = True

    def train_overlords(self) -> None:
        bot = self.bot
        if bot.supply_left <= 0 and not self._logged_supply_block:
            log_event(
                bot,
                f"SUPPLY BLOCKED (used={bot.supply_used}, cap={bot.supply_cap})",
            )
            self._logged_supply_block = True

        if (
            bot.supply_left <= self.plan.OVERLORD_SUPPLY_LEFT
            and bot.already_pending(UnitTypeId.OVERLORD) == 0
            and bot.larva
            and bot.can_afford(UnitTypeId.OVERLORD)
        ):
            bot.train(UnitTypeId.OVERLORD)
            if not self._logged_first_overlord:
                log_event(bot, f"ORDER overlord (supply_left={bot.supply_left})")
                self._logged_first_overlord = True

    def train_drones(self) -> None:
        bot = self.bot
        cap = self._drone_cap()
        if bot.supply_workers >= cap:
            if not self._logged_drone_cap:
                log_event(bot, f"DRONE CAP reached ({bot.supply_workers}/{cap})")
                self._logged_drone_cap = True
            return
        # Cap rose (new base) or workers died — allow refill logging again
        self._logged_drone_cap = False
        if bot.supply_left <= 1:
            return
        if not pool_started(bot):
            if bot.supply_used >= 14:
                return
            if bot.minerals >= 200 and bot.supply_used >= self.plan.POOL_SUPPLY:
                return

        if self._is_upgrade_rush():
            if (
                self._hatch_count() < self.plan.MAX_BASES
                and bot.structures(UnitTypeId.SPAWNINGPOOL).ready
                and bot.minerals >= 250
            ):
                return
        else:
            hatch_count = bot.townhalls.amount + bot.already_pending(UnitTypeId.HATCHERY)
            want = getattr(self.plan, "MACRO_HATCH_COUNT", 0)
            if (
                bot.structures(UnitTypeId.SPAWNINGPOOL).ready
                and want
                and hatch_count < want
                and bot.minerals >= 250
            ):
                return

        # Only train enough drones to reach cap (replace losses / fill new bases)
        need = cap - bot.supply_workers
        while need > 0 and bot.larva.amount > 0 and bot.can_afford(UnitTypeId.DRONE) and bot.supply_left > 1:
            bot.train(UnitTypeId.DRONE, 1)
            need -= 1


    def train_zerglings(self) -> None:
        bot = self.bot
        if not bot.structures(UnitTypeId.SPAWNINGPOOL).ready or bot.larva.amount <= 0:
            return
        if bot.supply_left <= 0:
            return

        if self._is_upgrade_rush():
            if self._hatch_count() < self.plan.MAX_BASES and bot.minerals >= 200:
                return
            # train_drones already refilled up to cap; remaining larva -> lings
        else:
            # Prefer drones to 16 before dumping larva into lings
            if bot.supply_workers < self.plan.DRONE_TARGET:
                return
            hatch_count = bot.townhalls.amount + bot.already_pending(UnitTypeId.HATCHERY)
            want = getattr(self.plan, "MACRO_HATCH_COUNT", 0)
            # Bank for macro hatch
            if want and hatch_count < want and bot.minerals >= 250:
                return
            # Bank 100 for speed if not started and gas is available
            speed = bot.already_pending_upgrade(UpgradeId.ZERGLINGMOVEMENTSPEED)
            if (
                speed == 0
                and bot.vespene >= self.plan.METABOLIC_BOOST_GAS
                and bot.minerals < 100
            ):
                return

        amount = bot.larva.amount
        bot.train(UnitTypeId.ZERGLING, amount)
        if not self._logged_first_lings:
            log_event(bot, f"ORDER first zerglings (larva={amount})")
            self._logged_first_lings = True


