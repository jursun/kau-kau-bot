"""Economy: injects, extractors, workers — branched by bot.build_plan."""

from __future__ import annotations

from typing import List, Optional, Set

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
        self._known_ready_extractors: Set[int] = set()
        self._extractors_pending_gas_fill: Set[int] = set()

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
        bot = self.bot
        if not bot.workers or not bot.townhalls:
            return

        if self._is_upgrade_rush():
            if not pool_started(bot) or bot.supply_used < self.plan.EXTRACTOR_SUPPLY:
                return
            max_per_base = self.plan.EXTRACTORS_PER_BASE
            for th in bot.townhalls.ready:
                local = bot.structures(UnitTypeId.EXTRACTOR).closer_than(12, th)
                pending = bot.structures(UnitTypeId.EXTRACTOR).not_ready.closer_than(12, th).amount
                if local.amount + pending >= max_per_base:
                    continue
                geysers = bot.vespene_geyser.closer_than(12, th)
                taken = {e.position.rounded for e in local}
                free = [g for g in geysers if g.position.rounded not in taken]
                if not free or not bot.can_afford(UnitTypeId.EXTRACTOR):
                    continue
                # Prefer a local worker for this base
                local_w = [w for w in bot.workers if w.distance_to(th) < 18 and not w.is_carrying_vespene]
                builder = min(local_w, key=lambda w: w.distance_to(free[0])) if local_w else bot.workers.random
                builder.build(UnitTypeId.EXTRACTOR, free[0])
                if not self._logged_extractor_order:
                    log_event(bot, f"ORDER extractor (supply={bot.supply_used})")
                    self._logged_extractor_order = True
                return
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
        if self._is_upgrade_rush():
            return True
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

    def _extractors_near(self, th: Unit):
        return self.bot.structures(UnitTypeId.EXTRACTOR).ready.closer_than(12, th)

    def _bases_in_order(self) -> List[Unit]:
        bot = self.bot
        return sorted(list(bot.townhalls.ready), key=lambda th: th.distance_to(bot.start_location))

    def local_mineral_cap(self, th: Unit) -> int:
        per = int(getattr(self.plan, "WORKERS_PER_MINERAL", 2))
        return per * len(self._minerals_near(th))

    def local_gas_cap(self, th: Unit) -> int:
        per = int(getattr(self.plan, "GAS_WORKER_COUNT", 3))
        return per * self._extractors_near(th).amount

    def local_worker_cap(self, th: Unit) -> int:
        # Prefer explicit 22 target when both gases are up; else scale with resources
        mineral = self.local_mineral_cap(th)
        gas = self.local_gas_cap(th)
        target = int(getattr(self.plan, "WORKERS_PER_BASE_TARGET", 22))
        if gas >= 6 and mineral >= 16:
            return target
        return mineral + gas

    def _closest_ready_hatch(self, unit: Unit) -> Optional[Unit]:
        hatches = self.bot.townhalls.ready
        if not hatches:
            return None
        return min(hatches, key=lambda th: unit.distance_to(th))

    def local_workers(self, th: Unit) -> List[Unit]:
        """Workers owned by this base (resource miners + idle/moving whose closest hatch is this)."""
        bot = self.bot
        minerals = self._minerals_near(th)
        extractors = list(self._extractors_near(th))
        mineral_tags = {m.tag for m in minerals}
        gas_tags = {e.tag for e in extractors}
        out = []
        seen = set()
        for w in bot.workers:
            on_local_res = (
                w.order_target in mineral_tags
                or w.order_target in gas_tags
                or (w.is_carrying_minerals and w.distance_to(th) < 12)
                or (w.is_carrying_vespene and any(w.distance_to(e) < 15 for e in extractors))
            )
            owner = self._closest_ready_hatch(w)
            owned_here = owner is not None and owner.tag == th.tag
            if on_local_res or owned_here:
                if w.tag not in seen:
                    out.append(w)
                    seen.add(w.tag)
        return out

    def rally_hatches_to_local_minerals(self) -> None:
        """Rally new drones to underfilled gas first, else local minerals. Does not touch existing gatherers."""
        bot = self.bot
        gas_target = int(getattr(self.plan, "GAS_WORKER_COUNT", 3))
        for th in bot.townhalls.ready:
            # Prefer an underfilled extractor so gas fills from spawns, not reassigns
            underfilled = None
            for ex in self._extractors_near(th):
                assigned = max(len(self._gas_workers(ex)), int(ex.assigned_harvesters))
                if assigned < gas_target:
                    underfilled = ex
                    break
            if underfilled is not None:
                th(AbilityId.RALLY_HATCHERY_UNITS, underfilled)
                continue
            minerals = self._minerals_near(th)
            if minerals:
                th(AbilityId.RALLY_HATCHERY_UNITS, min(minerals, key=lambda m: m.distance_to(th)))

    def _assign_gas_ling(self) -> None:
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

    def _assign_gas_upgrade(self) -> None:
        """Fill gas: yank mineral workers only when an extractor just completed; otherwise idle-only."""
        bot = self.bot
        target = int(getattr(self.plan, "GAS_WORKER_COUNT", 3))
        ready = list(bot.structures(UnitTypeId.EXTRACTOR).ready)
        ready_tags = {e.tag for e in ready}

        # Drop destroyed extractors
        self._known_ready_extractors &= ready_tags
        self._extractors_pending_gas_fill &= ready_tags

        # Newly completed extractors get one yank window until filled
        for ex in ready:
            if ex.tag not in self._known_ready_extractors:
                self._known_ready_extractors.add(ex.tag)
                self._extractors_pending_gas_fill.add(ex.tag)
                log_event(bot, "EXTRACTOR ready — yank minerals to gas")

        all_gas_tags = ready_tags

        for th in self._bases_in_order():
            for ex in self._extractors_near(th):
                current = self._gas_workers(ex)
                effective = max(len(current), int(ex.assigned_harvesters))
                deficit = target - effective
                allow_yank = ex.tag in self._extractors_pending_gas_fill

                if deficit <= 0:
                    self._extractors_pending_gas_fill.discard(ex.tag)
                    if not self._logged_gas_saturated and effective >= target:
                        log_event(
                            bot,
                            f"GAS saturated (target={target}, assigned~{effective})",
                        )
                        self._logged_gas_saturated = True
                    continue

                mineral_tags = {m.tag for m in self._minerals_near(th)}
                current_tags = {c.tag for c in current}

                pool = []
                for w in bot.workers:
                    if not self._worker_free(w):
                        continue
                    if w.tag in current_tags:
                        continue
                    if w.order_target in all_gas_tags or w.is_carrying_vespene:
                        continue
                    if w.is_idle:
                        pool.append(w)
                        continue
                    if not allow_yank:
                        continue
                    # Completion yank: take local mineral gatherers only
                    if w.is_gathering or w.is_carrying_minerals:
                        if w.order_target in mineral_tags or (
                            w.is_carrying_minerals and w.distance_to(th) < 12
                        ):
                            pool.append(w)

                pool.sort(
                    key=lambda w: (
                        0 if w.is_idle else 1,
                        0
                        if (
                            self._closest_ready_hatch(w)
                            and self._closest_ready_hatch(w).tag == th.tag
                        )
                        else 1,
                        w.distance_to(ex),
                    )
                )
                sent = 0
                for w in pool[:deficit]:
                    w.gather(ex)
                    sent += 1
                assigned = max(
                    len(self._gas_workers(ex)),
                    int(ex.assigned_harvesters),
                    effective + sent,
                )
                if assigned >= target:
                    self._extractors_pending_gas_fill.discard(ex.tag)
                if sent:
                    kind = "yank" if allow_yank else "idle"
                    log_event(
                        bot,
                        f"GAS assign ({kind}) need={deficit} sent={sent} assigned~{assigned}",
                    )
                if not self._logged_gas_saturated and assigned >= target:
                    log_event(
                        bot,
                        f"GAS saturated (target={target}, assigned~{assigned})",
                    )
                    self._logged_gas_saturated = True

    def _next_base_minerals(self, th: Unit) -> List[Unit]:
        """Mineral field at the next base after 	h (ready hatch or next expansion spot)."""
        bases = self._bases_in_order()
        idx = next((i for i, b in enumerate(bases) if b.tag == th.tag), None)
        if idx is not None and idx + 1 < len(bases):
            return self._minerals_near(bases[idx + 1])

        # No ready next hatch — distance-mine the next unowned expansion
        home = self.bot.start_location
        for exp in sorted(self.bot.expansion_locations_list, key=lambda p: p.distance_to(home)):
            if any(own.distance_to(exp) < 15 for own in self.bot.townhalls):
                continue
            mins = self._minerals_near(exp)
            if mins:
                return mins
        return []

    def _assign_minerals_upgrade(self) -> None:
        """Idle-only mineral assign. Never reassign gatherers. Overflow distance-mines next base."""
        bot = self.bot
        per_patch = int(getattr(self.plan, "WORKERS_PER_MINERAL", 2))
        gas_tags = {e.tag for e in bot.structures(UnitTypeId.EXTRACTOR).ready}

        def load(m: Unit) -> int:
            return sum(1 for x in bot.workers if x.order_target == m.tag)

        def open_patches(minerals: List[Unit]) -> List[Unit]:
            return [m for m in minerals if load(m) < per_patch]

        idle = [
            w
            for w in bot.workers
            if self._worker_free(w)
            and w.is_idle
            and not w.is_carrying_vespene
            and not w.is_carrying_minerals
            and w.order_target not in gas_tags
        ]
        idle.sort(key=lambda w: w.distance_to(bot.start_location))

        for w in idle:
            owner = self._closest_ready_hatch(w)
            if owner is None:
                fields = list(bot.mineral_field)
                if fields:
                    w.gather(min(fields, key=lambda m: m.distance_to(w)))
                continue

            local = self._minerals_near(owner)
            local_open = open_patches(local) if local else []
            if local_open:
                patch = min(local_open, key=lambda m: (load(m), m.distance_to(w)))
                w.gather(patch)
                continue

            # Local full — distance mine next base (do not cut anyone)
            nxt = self._next_base_minerals(owner)
            if not nxt:
                continue
            nxt_open = open_patches(nxt)
            target_patches = nxt_open if nxt_open else nxt
            patch = min(target_patches, key=lambda m: (load(m), m.distance_to(w)))
            w.gather(patch)

    async def manage_workers(self) -> None:
        if not self.bot.workers or not self.bot.townhalls.ready:
            return
        if self._is_upgrade_rush():
            # No distribute_workers — it fights our 3-per-extractor gas assign.
            self.rally_hatches_to_local_minerals()
            self._assign_gas_upgrade()
            self._assign_minerals_upgrade()
            return
        self._assign_gas_ling()
        self._assign_minerals_ling_rush()
