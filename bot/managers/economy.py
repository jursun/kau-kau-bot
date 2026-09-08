"""Economy: injects, extractors, workers — ling rush only for now.

Upgrade rush economy is intentionally empty (stub) — rebuild from scratch.
"""

from __future__ import annotations

from typing import List

from sc2.bot_ai import BotAI
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId
from sc2.ids.upgrade_id import UpgradeId
from sc2.unit import Unit

from bot.common.helpers import pool_started
from bot.common.log import log_event


class EconomyManager:
    def __init__(self, bot: BotAI):
        self.bot = bot
        self._logged_extractor_order = False
        self._logged_first_inject = False
        self._logged_gas_saturated = False
        self._logged_gas_pull = False
        self._gas_goal_met = False

    @property
    def plan(self):
        return self.bot.build_plan

    def _is_upgrade_rush(self) -> bool:
        return getattr(self.plan, "NAME", "") == "upgrade_rush"

    def inject_larva(self) -> None:
        bot = self.bot
        if not bot.townhalls.ready:
            return
        queens = bot.units(UnitTypeId.QUEEN).ready
        if not queens:
            return
        targets = list(bot.townhalls.ready)
        # Ling rush: main only. Upgrade rush: all hatches (queen inject only).
        if not self._is_upgrade_rush() and targets:
            targets = [targets[0]]
        for hatch in targets:
            if hatch.has_buff(BuffId.QUEENSPAWNLARVATIMER):
                continue
            ready = queens.filter(lambda q: q.energy >= self.plan.QUEEN_INJECT_ENERGY)
            if not ready:
                continue
            q = ready.closest_to(hatch)
            q(AbilityId.EFFECT_INJECTLARVA, hatch)
            if not self._logged_first_inject:
                log_event(bot, "INJECT larva (first)")
                self._logged_first_inject = True

    def build_extractor(self) -> None:
        # Upgrade rush: no economy yet — rebuild later
        if self._is_upgrade_rush():
            return

        bot = self.bot
        if not bot.workers or not bot.townhalls:
            return
        if not pool_started(bot):
            return
        if (
            bot.structures(UnitTypeId.EXTRACTOR).amount
            + bot.already_pending(UnitTypeId.EXTRACTOR)
            > 0
        ):
            return
        if bot.supply_used < self.plan.EXTRACTOR_SUPPLY:
            return
        if not bot.can_afford(UnitTypeId.EXTRACTOR):
            return
        bot.workers.random.build(
            UnitTypeId.EXTRACTOR,
            bot.vespene_geyser.closest_to(bot.townhalls.first),
        )
        if not self._logged_extractor_order:
            log_event(bot, f"ORDER extractor (supply={bot.supply_used})")
            self._logged_extractor_order = True

    def _needs_gas(self) -> bool:
        if self._gas_goal_met:
            return False
        bot = self.bot
        # Ling rush only (upgrade rush never calls this while stubbed)
        speed = bot.already_pending_upgrade(UpgradeId.ZERGLINGMOVEMENTSPEED)
        if (
            speed > 0
            or bot.vespene >= self.plan.METABOLIC_BOOST_GAS
            or bot.can_afford(UpgradeId.ZERGLINGMOVEMENTSPEED)
        ):
            self._gas_goal_met = True
            return False
        return True

    def _worker_free(self, worker: Unit) -> bool:
        return worker.tag not in self.bot.unit_tags_received_action

    def _gas_workers(self, extractor: Unit) -> List[Unit]:
        return [
            w
            for w in self.bot.workers
            if w.order_target == extractor.tag
            or (w.is_carrying_vespene and w.distance_to(extractor) < 15)
        ]

    def _minerals_near(self, place) -> List[Unit]:
        return list(self.bot.mineral_field.closer_than(10, place))

    def _assign_gas(self) -> None:
        bot = self.bot
        target = self.plan.GAS_WORKER_COUNT
        extractors = list(bot.structures(UnitTypeId.EXTRACTOR).ready)
        if not extractors:
            return

        if not self._needs_gas():
            pulled = 0
            for ex in extractors:
                for w in list(self._gas_workers(ex)):
                    if not self._worker_free(w):
                        continue
                    minerals = self._minerals_near(bot.townhalls.ready.closest_to(ex)) or list(
                        bot.mineral_field
                    )
                    if minerals:
                        w.gather(min(minerals, key=lambda m: m.distance_to(w)))
                        pulled += 1
            if (pulled or self._gas_goal_met) and not self._logged_gas_pull:
                log_event(
                    bot,
                    f"GAS pull to minerals (vespene={bot.vespene}, pulled={pulled})",
                )
                self._logged_gas_pull = True
            return

        for ex in extractors:
            current = self._gas_workers(ex)
            while len(current) > target or ex.assigned_harvesters > target:
                w = next((c for c in current if self._worker_free(c)), current[0] if current else None)
                if w is None:
                    break
                current = [c for c in current if c.tag != w.tag]
                minerals = self._minerals_near(bot.townhalls.ready.closest_to(ex)) or list(
                    bot.mineral_field
                )
                if minerals:
                    w.gather(min(minerals, key=lambda m: m.distance_to(w)))
                else:
                    break

            current = self._gas_workers(ex)
            effective = max(len(current), int(ex.assigned_harvesters))
            deficit = target - effective
            if deficit <= 0:
                if not self._logged_gas_saturated and effective >= target:
                    log_event(
                        bot,
                        f"GAS saturated (target={target}, assigned~{effective})",
                    )
                    self._logged_gas_saturated = True
                continue
            pool = [
                w
                for w in bot.workers
                if self._worker_free(w)
                and w.tag not in {c.tag for c in current}
                and not w.is_carrying_vespene
                and (w.is_idle or w.is_gathering or w.is_carrying_minerals)
            ]
            pool.sort(key=lambda w: w.distance_to(ex))
            for w in pool[:deficit]:
                w.gather(ex)
            assigned = max(len(self._gas_workers(ex)), int(ex.assigned_harvesters))
            if not self._logged_gas_saturated and assigned >= target:
                log_event(
                    bot,
                    f"GAS saturated (target={target}, assigned~{assigned})",
                )
                self._logged_gas_saturated = True

    def _assign_minerals_ling_rush(self) -> None:
        bot = self.bot
        if not bot.townhalls.ready:
            return
        th = bot.townhalls.ready.first
        minerals = self._minerals_near(th) or list(bot.mineral_field)
        if not minerals:
            return

        gas_tags = {ex.tag for ex in bot.structures(UnitTypeId.EXTRACTOR).ready}

        def on_gas(w: Unit) -> bool:
            return w.order_target in gas_tags or (
                w.is_carrying_vespene
                and any(w.distance_to(ex) < 15 for ex in bot.structures(UnitTypeId.EXTRACTOR).ready)
            )

        gas_count = len([w for w in bot.workers if on_gas(w)])
        mineral_cap = max(0, self.plan.DRONE_TARGET - (gas_count if self._needs_gas() else 0))
        if not self._needs_gas():
            mineral_cap = self.plan.DRONE_TARGET

        mineral_tags = {m.tag for m in minerals}
        mineral_workers = [
            w
            for w in bot.workers
            if not on_gas(w)
            and (
                w.order_target in mineral_tags
                or (w.is_carrying_minerals and w.distance_to(th) < 15)
            )
        ]

        missing = mineral_cap - len(mineral_workers)
        if missing <= 0:
            return
        idle = [
            w
            for w in bot.workers
            if self._worker_free(w)
            and w.is_idle
            and not on_gas(w)
            and not w.is_carrying_vespene
        ]
        for w in idle[:missing]:
            w.gather(min(minerals, key=lambda m: m.distance_to(w)))

    async def manage_workers(self) -> None:
        # Upgrade rush: no worker economy yet — rebuild from scratch
        if self._is_upgrade_rush():
            return
        if not self.bot.workers or not self.bot.townhalls.ready:
            return
        self._assign_gas()
        self._assign_minerals_ling_rush()
