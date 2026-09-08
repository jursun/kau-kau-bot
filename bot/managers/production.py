"""Production: pool, structures, units, research — behavior depends on bot.build_plan."""

from __future__ import annotations

from typing import List, Tuple

from sc2.bot_ai import BotAI
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from bot.common.helpers import pool_started, pool_pending_or_none, safe_already_pending
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

    def _log_once(self, flag: str, message: str) -> None:
        if getattr(self, flag):
            return
        log_event(self.bot, message)
        setattr(self, flag, True)

    def _structure_total(self, unit_type: UnitTypeId) -> int:
        bot = self.bot
        return bot.structures(unit_type).amount + bot.already_pending(unit_type)

    def _larva_near(self, th, radius: float = 10):
        larva = [lar for lar in self.bot.larva if lar.distance_to(th) < radius]
        larva.sort(key=lambda lar: lar.distance_to(th))
        return larva

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
            # Sum each ready base's local cap (2/mineral + 3/extractor)
            total = 0
            for th in self.bot.townhalls.ready:
                total += self.bot.economy.local_worker_cap(th)
            return max(total, 1)
        # Ling rush: 16 total workers (3 of them sit on gas until speed)
        return plan.DRONE_TARGET

    async def build_pool(self) -> None:
        bot = self.bot
        if self._is_upgrade_rush() and self._hatch_count() < 2:
            return  # hatch@15 before pool@16
        if pool_pending_or_none(bot) and bot.supply_used >= self.plan.POOL_SUPPLY:
            if bot.can_afford(UnitTypeId.SPAWNINGPOOL):
                near = bot.start_location.towards(
                    bot.game_info.map_center, distance=self.plan.POOL_NEAR_DISTANCE
                )
                built = await bot.build(UnitTypeId.SPAWNINGPOOL, near=near)
                if built:
                    self._log_once(
                        "_logged_pool_order",
                        f"ORDER pool (supply={bot.supply_used}, workers={bot.supply_workers})",
                    )
        if bot.structures(UnitTypeId.SPAWNINGPOOL).ready:
            self._log_once("_logged_pool_ready", "READY spawning pool")

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
        if built:
            self._log_once(
                "_logged_macro_hatch",
                f"ORDER macro hatch (minerals={bot.minerals}, hatches={hatch_count}+1)",
            )

    def _enemy_threat_near_home(self) -> bool:
        bot = self.bot
        radius = float(getattr(self.plan, "INBASE_HATCH_THREAT_RADIUS", 40))
        home = bot.start_location
        return any(u.distance_to(home) < radius for u in bot.enemy_units if not u.is_flying)

    async def expand_bases(self) -> None:
        """Upgrade rush: 2nd hatch @ EXPAND_SUPPLY (natural if safe else in-base); later after pool."""
        bot = self.bot
        if not self._is_upgrade_rush():
            return
        if self._hatch_count() >= self.plan.MAX_BASES:
            return
        if safe_already_pending(bot, UnitTypeId.HATCHERY) > 0:
            return
        if not bot.can_afford(UnitTypeId.HATCHERY):
            return

        n = self._hatch_count()
        expand_at = int(getattr(self.plan, "EXPAND_SUPPLY", 15))
        third_at = int(getattr(self.plan, "THIRD_HATCH_SUPPLY", 32))

        if n < 2:
            if bot.supply_used < expand_at:
                return
            if self._enemy_threat_near_home():
                near = bot.start_location.towards(
                    bot.game_info.map_center,
                    getattr(self.plan, "MACRO_HATCH_NEAR_DISTANCE", 6),
                )
                built = await bot.build(UnitTypeId.HATCHERY, near=near, max_distance=15)
                if built:
                    log_event(bot, f"ORDER in-base hatch (threat, supply={bot.supply_used})")
                    self._logged_expand = True
                return
            location = await bot.get_next_expansion()
            if not location:
                return
            before = n
            await bot.expand_now(location=location, max_distance=25)
            after = self._hatch_count()
            pending = safe_already_pending(bot, UnitTypeId.HATCHERY)
            log_event(
                bot,
                f"ORDER natural expand (bases={max(after, before)}, pending={pending}, supply={bot.supply_used}, minerals={bot.minerals})",
            )
            self._logged_expand = True
            return

        # 3rd+: need pool first; third hatch @ THIRD_HATCH_SUPPLY (32)
        if not bot.structures(UnitTypeId.SPAWNINGPOOL).ready:
            return
        if n < 3 and bot.supply_used < third_at:
            return
        location = await bot.get_next_expansion()
        if not location:
            return
        before = n
        await bot.expand_now(location=location, max_distance=25)
        after = self._hatch_count()
        if after > before or safe_already_pending(bot, UnitTypeId.HATCHERY) > 0:
            log_event(
                bot,
                f"ORDER expand (bases={max(after, before)}, supply={bot.supply_used})",
            )
            self._logged_expand = True


    async def build_evolution_chambers(self) -> None:
        bot = self.bot
        if not self._is_upgrade_rush():
            return
        if not bot.structures(UnitTypeId.SPAWNINGPOOL).ready:
            return
        # Opening: double evo after speed + first lings + 2nd extractor (on_step order)
        have = self._structure_total(UnitTypeId.EVOLUTIONCHAMBER)
        while have < self.plan.EVO_COUNT and bot.can_afford(UnitTypeId.EVOLUTIONCHAMBER):
            near = bot.start_location.towards(bot.game_info.map_center, 8)
            if bot.townhalls:
                near = bot.townhalls.first.position.towards(bot.game_info.map_center, 6)
            built = await bot.build(UnitTypeId.EVOLUTIONCHAMBER, near=near, max_distance=12)
            if not built:
                break
            have += 1
            self._log_once(
                "_logged_evo",
                f"ORDER evolution chamber ({have}/{self.plan.EVO_COUNT})",
            )

    def morph_lair(self) -> None:
        bot = self.bot
        if not self._is_upgrade_rush():
            return
        if self._structure_total(UnitTypeId.LAIR):
            return
        if self._structure_total(UnitTypeId.HIVE):
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
        self._log_once("_logged_lair", "ORDER lair")

    async def build_infestation_pit(self) -> None:
        bot = self.bot
        if not self._is_upgrade_rush():
            return
        if self._structure_total(UnitTypeId.INFESTATIONPIT):
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
        if built:
            self._log_once("_logged_pit", "ORDER infestation pit")

    def morph_hive(self) -> None:
        bot = self.bot
        if not self._is_upgrade_rush():
            return
        if self._structure_total(UnitTypeId.HIVE):
            return
        if not bot.structures(UnitTypeId.INFESTATIONPIT).ready:
            return
        lairs = bot.townhalls(UnitTypeId.LAIR).ready.idle
        if not lairs or not bot.can_afford(UnitTypeId.HIVE):
            return
        lairs.first.build(UnitTypeId.HIVE)
        self._log_once("_logged_hive", "ORDER hive")

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
            if started:
                self._log_once("_logged_speed_started", "RESEARCH metabolic boost started")
        elif progress == 1:
            self._log_once("_logged_speed_done", "READY metabolic boost")

    def _hatch_training_queen(self, th) -> bool:
        for order in th.orders:
            aid = getattr(order.ability, "id", None)
            if aid is None:
                continue
            if "QUEEN" in str(aid):
                return True
        return False

    def train_queens(self) -> None:
        """Hard cap: at most 1 queen per ready townhall."""
        bot = self.bot
        if not bot.structures(UnitTypeId.SPAWNINGPOOL).ready:
            return
        hatches = list(bot.townhalls.ready)
        if not hatches:
            return

        queens = list(bot.units(UnitTypeId.QUEEN))
        pending = safe_already_pending(bot, UnitTypeId.QUEEN)
        training = sum(1 for th in hatches if self._hatch_training_queen(th))
        # Global hard cap
        if len(queens) + pending + training >= len(hatches):
            return

        # Assign existing queens to nearest hatch; find hatches with none
        claimed = set()
        for q in queens:
            nearest = min(hatches, key=lambda th: q.distance_to(th))
            claimed.add(nearest.tag)

        for th in hatches:
            if th.tag in claimed:
                continue
            if self._hatch_training_queen(th):
                continue
            if not bot.can_afford(UnitTypeId.QUEEN):
                return
            if not th.is_idle and th.orders:
                # busy morphing/building something else
                continue
            th.train(UnitTypeId.QUEEN)
            self._log_once("_logged_first_queen", "ORDER queen")
            return  # one queen order per step


    def train_overlords(self) -> None:
        bot = self.bot
        if bot.supply_left <= 0:
            self._log_once(
                "_logged_supply_block",
                f"SUPPLY BLOCKED (used={bot.supply_used}, cap={bot.supply_cap})",
            )

        if not bot.larva or not bot.can_afford(UnitTypeId.OVERLORD):
            return
        if bot.already_pending(UnitTypeId.OVERLORD) > 0:
            return

        if self._is_upgrade_rush():
            ovis = bot.units(UnitTypeId.OVERLORD).amount + bot.structures(
                UnitTypeId.OVERLORDTRANSPORT
            ).amount
            # Opening: 13 ovi, then 19 ovi; afterward keep OVERLORD_SUPPLY_LEFT buffer
            need_opening = (
                (ovis < 1 and bot.supply_used >= getattr(self.plan, "FIRST_OVERLORD_SUPPLY", 13))
                or (ovis < 2 and bot.supply_used >= getattr(self.plan, "SECOND_OVERLORD_SUPPLY", 19))
            )
            need_buffer = bot.supply_left <= self.plan.OVERLORD_SUPPLY_LEFT
            if not (need_opening or need_buffer):
                return
            bot.train(UnitTypeId.OVERLORD)
            self._log_once(
                "_logged_first_overlord",
                f"ORDER overlord (supply={bot.supply_used}, ovis~{ovis}+1)",
            )
            return

        if bot.supply_left <= self.plan.OVERLORD_SUPPLY_LEFT:
            bot.train(UnitTypeId.OVERLORD)
            self._log_once(
                "_logged_first_overlord",
                f"ORDER overlord (supply_left={bot.supply_left})",
            )


    def train_drones(self) -> None:
        bot = self.bot
        if bot.supply_left <= 1:
            return

        # Upgrade rush: always run saturation fallback (never pool-first soft-starve).
        # Structures morph drones — replace promptly to mineral ideal_harvesters.
        if self._is_upgrade_rush():
            goal = int(getattr(self.plan, "GOAL_WORKERS", 80))
            workers_now = bot.supply_workers + safe_already_pending(bot, UnitTypeId.DRONE)
            if workers_now >= goal:
                if not self._logged_drone_cap:
                    log_event(bot, f"DRONE CAP reached ({workers_now}/{goal})")
                    self._logged_drone_cap = True
                return
            self._logged_drone_cap = False

            # Reserve 300 for hatch@15 / later expands without stopping saturation.
            # Only skip a drone when spending 50 would leave <300 while a hatch is due.
            expand_at = int(getattr(self.plan, "EXPAND_SUPPLY", 15))
            reserve_hatch = (
                self._hatch_count() < self.plan.MAX_BASES
                and safe_already_pending(bot, UnitTypeId.HATCHERY) == 0
                and (
                    (self._hatch_count() < 2 and bot.supply_used >= expand_at)
                    or (
                        self._hatch_count() >= 2
                        and bot.structures(UnitTypeId.SPAWNINGPOOL).ready
                    )
                )
            )

            for th in bot.townhalls.ready:
                workers_now = bot.supply_workers + safe_already_pending(bot, UnitTypeId.DRONE)
                if workers_now >= goal:
                    break
                if bot.supply_left <= 1:
                    break
                if not bot.can_afford(UnitTypeId.DRONE):
                    break
                if reserve_hatch and bot.minerals < 350:
                    break  # keep >=300 for expand_bases; still allow drones when richer
                ideal = getattr(th, "ideal_harvesters", 0) or 16
                assigned = getattr(th, "assigned_harvesters", 0)
                # Mineral line saturation only (gas filled by yank-3, not here)
                if assigned >= ideal and getattr(th, "surplus_harvesters", 0) >= 0:
                    continue
                larva = self._larva_near(th)
                if not larva:
                    continue
                larva[0].train(UnitTypeId.DRONE)
                log_event(
                    bot,
                    f"DRONE train hatch assigned={assigned}/{ideal} workers={bot.supply_workers}",
                )
            return

        # Ling rush: pool-first soft gates
        if not pool_started(bot):
            if bot.supply_used >= 14:
                return
            if bot.minerals >= 200 and bot.supply_used >= self.plan.POOL_SUPPLY:
                return

        # Ling rush
        cap = self._drone_cap()
        if bot.supply_workers >= cap:
            if not self._logged_drone_cap:
                log_event(bot, f"DRONE CAP reached ({bot.supply_workers}/{cap})")
                self._logged_drone_cap = True
            return
        self._logged_drone_cap = False

        hatch_count = bot.townhalls.amount + safe_already_pending(bot, UnitTypeId.HATCHERY)
        want = getattr(self.plan, "MACRO_HATCH_COUNT", 0)
        if (
            bot.structures(UnitTypeId.SPAWNINGPOOL).ready
            and want
            and hatch_count < want
            and bot.minerals >= 250
        ):
            return

        need = cap - bot.supply_workers
        while need > 0 and bot.larva.amount > 0 and bot.can_afford(UnitTypeId.DRONE) and bot.supply_left > 1:
            bot.train(UnitTypeId.DRONE, 1)
            need -= 1


    def _hatch_saturated(self, th) -> bool:
        ideal = getattr(th, "ideal_harvesters", 0) or 22
        assigned = getattr(th, "assigned_harvesters", 0)
        return assigned >= ideal and getattr(th, "surplus_harvesters", 0) >= 0

    def train_zerglings(self) -> None:
        bot = self.bot
        if not bot.structures(UnitTypeId.SPAWNINGPOOL).ready or bot.larva.amount <= 0:
            return
        if bot.supply_left <= 0:
            return

        if self._is_upgrade_rush():
            # @pool ready: make lings, but NEVER steal larva from an undersaturated
            # hatch — train_drones runs first and fills natural minerals (~2/patch)
            # while the first ling morphs.
            if not bot.structures(UnitTypeId.SPAWNINGPOOL).ready:
                return
            for th in bot.townhalls.ready:
                if not self._hatch_saturated(th):
                    continue  # leave larva for drones (esp. natural mineral line)
                larva = self._larva_near(th)
                if not larva:
                    continue
                for lar in larva:
                    if bot.supply_left <= 0:
                        return
                    if (
                        bot.supply_left <= self.plan.OVERLORD_SUPPLY_LEFT
                        and bot.can_afford(UnitTypeId.OVERLORD)
                    ):
                        lar.train(UnitTypeId.OVERLORD)
                        continue
                    if bot.can_afford(UnitTypeId.ZERGLING):
                        lar.train(UnitTypeId.ZERGLING)
                        self._log_once(
                            "_logged_first_lings",
                            "ORDER first zerglings (saturated hatch, natural drones continue)",
                        )
            return

        # Ling rush
        if bot.supply_workers < self.plan.DRONE_TARGET:
            return
        hatch_count = bot.townhalls.amount + safe_already_pending(bot, UnitTypeId.HATCHERY)
        want = getattr(self.plan, "MACRO_HATCH_COUNT", 0)
        if want and hatch_count < want and bot.minerals >= 250:
            return
        if bot.minerals < 100:
            return
        # Iterate larva explicitly — bot.train() does not reduce larva.amount
        # in-frame, so a while larva.amount loop spins forever after first ORDER.
        for larva in list(bot.larva):
            if bot.supply_left <= 0 or not bot.can_afford(UnitTypeId.ZERGLING):
                break
            larva.train(UnitTypeId.ZERGLING)
            self._log_once(
                "_logged_first_lings",
                f"ORDER first zerglings (larva={bot.larva.amount})",
            )

