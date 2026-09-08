"""Combat: pre-speed home scout/defend, then attack waves + expansion hunt."""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Set

from sc2.bot_ai import BotAI
from sc2.data import race_townhalls
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2
from sc2.unit import Unit

from bot.common.log import log_event

_ALL_TOWNHALLS = {th for ths in race_townhalls.values() for th in ths}
_SCOUTS_PER_EXPANSION = 2
_MAX_SCOUT_FRACTION = 0.25
_MAIN_RADIUS = 18
_EXP_TH_RADIUS = 15
_HOME_DEFENSE_RADIUS = 45
_PRE_SPEED_RALLY_SECONDS = 15.0
_SPEED_RESEARCH_FALLBACK = 110.0
_WAVE_GROWTH = 1.10
_WAVE1_MIN_SIZE = 20
_RALLY_RADIUS = 8.0

_HARASS_TYPES = {
    UnitTypeId.REAPER,
    UnitTypeId.ADEPT,
    UnitTypeId.ZERGLING,
}
_WORKER_TYPES = {
    UnitTypeId.SCV,
    UnitTypeId.PROBE,
    UnitTypeId.DRONE,
}


class CombatManager:
    """Ling rush combat + early home defense + wave attacks + expansion hunting."""

    def __init__(self, bot: BotAI):
        self.bot = bot
        self._logged_attack = False
        self._main_townhall_seen = False
        self._main_destroyed = False
        self._logged_main_destroyed = False
        self._logged_scout = False
        self._found_th_logged: Set[Point2] = set()
        self._scout_assignments: Dict[Point2, Set[int]] = {}
        self._cleared_expansions: Set[Point2] = set()
        self._known_th_points: List[Point2] = []
        # Pre-speed home defense
        self._logged_home_scout = False
        self._logged_collapse = False
        self._logged_harass_retreat = False
        self._logged_spine = False
        self._need_spine = False
        self._home_patrol_index = 0
        self._logged_pre_rally = False
        # Attack waves
        self._wave_sent_tags: Set[int] = set()
        self._last_wave_size = 0
        self._next_wave_target = 0
        self._wave_number = 0
        self._logged_gathering = False
        self._wave_focus = "third"

    @property
    def _wave1_min(self) -> int:
        return int(getattr(self.bot.build_plan, "WAVE1_MIN_SIZE", _WAVE1_MIN_SIZE))

    @property
    def _wave_mode(self) -> str:
        return getattr(self.bot.build_plan, "WAVE_ATTACK_MODE", "third_natural")

    def _enemy_main(self) -> Point2:
        return self.bot.enemy_start_locations[0]

    def _townhalls(self):
        return self.bot.enemy_structures.of_type(_ALL_TOWNHALLS)

    def _home_anchor(self) -> Point2:
        bot = self.bot
        if bot.townhalls:
            return bot.townhalls.first.position
        return bot.start_location

    def _half_map_dist(self) -> float:
        return self._home_anchor().distance_to(self._enemy_main()) * 0.5

    def _past_leash(self, pos: Point2) -> bool:
        return pos.distance_to(self._home_anchor()) > self._half_map_dist()

    def _natural_base(self) -> Point2:
        """Second-closest expansion to home (natural); falls back to main."""
        home = self._home_anchor()
        exps = sorted(self.bot.expansion_locations_list, key=lambda p: p.distance_to(home))
        if len(exps) >= 2:
            return exps[1]
        if exps:
            return exps[0]
        return home

    def _natural_rally(self) -> Point2:
        """In front of the natural, toward the enemy."""
        natural = self._natural_base()
        return natural.towards(self._enemy_main(), 8)

    def _mineral_line_pos(self) -> Point2:
        bot = self.bot
        anchor = self._home_anchor()
        local = bot.mineral_field.closer_than(12, anchor)
        if local:
            return local.center
        return anchor.towards(bot.game_info.map_center, -3)

    def _nearby_patrol_bases(self) -> List[Point2]:
        """Closest 3-4 expansion spots to home, leashed to our half of the map."""
        bot = self.bot
        home = self._home_anchor()
        half = self._half_map_dist()
        exps = sorted(bot.expansion_locations_list, key=lambda p: p.distance_to(home))
        exps = [e for e in exps if e.distance_to(home) <= half]
        if not exps:
            return [home]
        return exps[:4]

    def _behind_mineral_line(self, expansion: Point2) -> Point2:
        """Point just behind the mineral line at an expansion (away from hatch)."""
        bot = self.bot
        local = bot.mineral_field.closer_than(12, expansion)
        if not local:
            center = bot.game_info.map_center
            return expansion.towards(center, -6)
        mcenter = local.center
        dist = expansion.distance_to(mcenter)
        return expansion.towards(mcenter, dist + 4)

    def _home_patrol_points(self) -> List[Point2]:
        """Patrol nearest 3-4 bases; waypoints behind mineral lines; half-map leash."""
        bot = self.bot
        home = self._home_anchor()
        half = self._half_map_dist()
        bases = self._nearby_patrol_bases()
        points: List[Point2] = []

        for base in bases:
            behind = self._behind_mineral_line(base)
            if behind.distance_to(home) <= half:
                points.append(behind)
            local = bot.mineral_field.closer_than(12, base)
            if local:
                hatch_side = base.towards(local.center, 3)
            else:
                hatch_side = base
            if hatch_side.distance_to(home) <= half:
                points.append(hatch_side)

        unique: List[Point2] = []
        for pt in points:
            if pt.distance_to(home) > half:
                continue
            if all(pt.distance_to(u) > 3 for u in unique):
                unique.append(pt)
        if not unique:
            unique = [home]
        return unique

    def _speed_research_time(self) -> float:
        try:
            return float(
                self.bot.game_data.upgrades[
                    UpgradeId.ZERGLINGMOVEMENTSPEED.value
                ].research_time
            )
        except Exception:
            return _SPEED_RESEARCH_FALLBACK

    def _speed_progress(self) -> float:
        return float(self.bot.already_pending_upgrade(UpgradeId.ZERGLINGMOVEMENTSPEED))

    def _should_pre_rally(self) -> bool:
        prog = self._speed_progress()
        if prog <= 0 or prog >= 1:
            return False
        remaining = (1.0 - prog) * self._speed_research_time()
        return remaining <= _PRE_SPEED_RALLY_SECONDS

    def _threats_near_home(self):
        bot = self.bot
        home = self._home_anchor()
        units = bot.enemy_units.closer_than(_HOME_DEFENSE_RADIUS, home)
        structures = bot.enemy_structures.closer_than(_HOME_DEFENSE_RADIUS, home)
        return units, structures

    def _home_collapse_target(self):
        """Closest ground enemy unit/building near home — collapse on anything grounded."""
        enemy_units, enemy_structures = self._threats_near_home()
        home = self._home_anchor()
        ground = enemy_units.filter(lambda u: not u.is_flying)
        if ground:
            return ground.closest_to(home)
        # Ground structures (bunker, pylon, etc.)
        ground_structs = enemy_structures.filter(lambda s: not s.is_flying)
        if ground_structs:
            return ground_structs.closest_to(home)
        return None

    def _collapse_lings_home(self, lings) -> bool:
        """Attack home lings onto a nearby threat. True if collapsing (skip move spam)."""
        target = self._home_collapse_target()
        if target is None or self._past_leash(target.position):
            return False
        for ling in lings:
            if ling.tag in self._wave_sent_tags:
                continue
            if self._past_leash(ling.position):
                continue
            ling.attack(target)
        if not self._logged_collapse:
            log_event(
                self.bot,
                f"COLLAPSE on {target.type_id.name} at {target.position}",
            )
            self._logged_collapse = True
        return True

    def _pull_past_leash(self, lings) -> None:
        """Yank any non-wave ling that drifted past half-map back onto patrol."""
        rally = self._natural_rally()
        points = self._home_patrol_points()
        fallback = points[0] if points else rally
        for ling in lings:
            if ling.tag in self._wave_sent_tags:
                continue
            if self._past_leash(ling.position):
                ling.move(fallback)

    async def _build_spine_near_minerals(self) -> None:
        bot = self.bot
        if not self._need_spine:
            return
        if bot.structures(UnitTypeId.SPINECRAWLER).amount + bot.already_pending(
            UnitTypeId.SPINECRAWLER
        ) > 0:
            return
        if not bot.structures(UnitTypeId.SPAWNINGPOOL).ready:
            return
        if not bot.can_afford(UnitTypeId.SPINECRAWLER):
            return

        near = self._mineral_line_pos()
        built = await bot.build(UnitTypeId.SPINECRAWLER, near=near, max_distance=10)
        if built and not self._logged_spine:
            log_event(bot, f"ORDER spine crawler near minerals ({near})")
            self._logged_spine = True

    async def _pre_speed_home_control(self) -> None:
        """Before ling speed: scout home, collapse, harass retreat, pre-rally, leash."""
        bot = self.bot
        lings = bot.units(UnitTypeId.ZERGLING).ready
        if not lings:
            await self._build_spine_near_minerals()
            return

        if not self._logged_home_scout:
            log_event(bot, f"HOME SCOUT with lings ({lings.amount})")
            self._logged_home_scout = True

        # Always leash first so chase/attack-move cannot cross the map
        self._pull_past_leash(lings)

        enemy_units, enemy_structures = self._threats_near_home()
        harass = enemy_units.filter(lambda u: u.type_id in _HARASS_TYPES)
        workers = enemy_units.filter(lambda u: u.type_id in _WORKER_TYPES)

        # Harassment still warrants a spine, but collapse onto them (no retreat move-spam)
        if harass:
            self._need_spine = True
            if not self._logged_harass_retreat:
                kinds = sorted({u.type_id.name for u in harass})
                log_event(bot, f"HARASS detected {kinds} - collapse + spine")
                self._logged_harass_retreat = True
            await self._build_spine_near_minerals()
            # fall through to collapse on any ground threat

        # Army / workers / buildings near base: collapse (skip move spam)
        if self._collapse_lings_home(lings):
            await self._build_spine_near_minerals()
            return


        # ~15s before speed finishes: group everyone at natural
        if self._should_pre_rally():
            rally = self._natural_rally()
            for ling in lings:
                if ling.distance_to(rally) > 3:
                    ling.move(rally)
            if not self._logged_pre_rally:
                rem = (1.0 - self._speed_progress()) * self._speed_research_time()
                log_event(
                    bot,
                    f"RALLY natural pre-speed (lings={lings.amount}, ~{rem:.0f}s left)",
                )
                self._logged_pre_rally = True
            await self._build_spine_near_minerals()
            return

        # No threat: patrol leashed waypoints
        points = self._home_patrol_points()
        if not points:
            await self._build_spine_near_minerals()
            return
        for i, ling in enumerate(lings):
            if self._past_leash(ling.position):
                continue
            if not ling.is_idle:
                continue
            wp = points[(self._home_patrol_index + i) % len(points)]
            ling.attack(wp)
        if bot.state.game_loop % 16 == 0:
            self._home_patrol_index = (self._home_patrol_index + 1) % len(points)

        await self._build_spine_near_minerals()

    def _enemy_bases_near_main(self) -> List[Point2]:
        """Enemy expansions sorted by distance to their main (main, natural, third, ...)."""
        main = self._enemy_main()
        return sorted(self.bot.expansion_locations_list, key=lambda p: p.distance_to(main))

    def _enemy_natural_pos(self) -> Point2:
        bases = self._enemy_bases_near_main()
        return bases[1] if len(bases) > 1 else self._enemy_main()

    def _enemy_third_pos(self) -> Point2:
        bases = self._enemy_bases_near_main()
        return bases[2] if len(bases) > 2 else self._enemy_natural_pos()

    def _mineral_line_at(self, expansion: Point2) -> Point2:
        local = self.bot.mineral_field.closer_than(12, expansion)
        if local:
            return local.center
        return expansion

    def _enemy_workers_near(self, expansion: Point2, radius: float = 16):
        return self.bot.enemy_units.filter(
            lambda u: u.type_id in _WORKER_TYPES and u.distance_to(expansion) < radius
        )

    def _update_wave_focus(self) -> None:
        """Advance wave focus when the current base is cleared of workers/TH."""
        if self._wave_focus == "third":
            pos = self._enemy_third_pos()
            nxt, note = "natural", "WAVE focus -> natural (third clear/empty)"
        elif self._wave_focus == "natural":
            pos = self._enemy_natural_pos()
            nxt, note = "main", "WAVE focus -> main (natural clear/empty)"
        else:
            return
        workers = self._enemy_workers_near(pos)
        ths = self._townhalls().closer_than(_EXP_TH_RADIUS, pos)
        if not workers and not ths:
            self._wave_focus = nxt
            log_event(self.bot, note)

    def _assign_harass_targets(self, army) -> None:
        """Focus fire enemy workers at current wave focus, then the next base."""
        self._update_wave_focus()
        third = self._enemy_third_pos()
        natural = self._enemy_natural_pos()
        main = self._enemy_main()
        if self._wave_focus == "third":
            focus_pos, secondary = third, natural
        elif self._wave_focus == "natural":
            focus_pos, secondary = natural, main
        else:
            focus_pos, secondary = main, natural
        workers = self._enemy_workers_near(focus_pos)
        minerals = self._mineral_line_at(focus_pos)
        for ling in army:
            if workers:
                ling.attack(workers.closest_to(ling))
            else:
                other_workers = self._enemy_workers_near(secondary)
                if other_workers:
                    ling.attack(other_workers.closest_to(ling))
                elif self._wave_focus == "main":
                    ling.attack(main)
                else:
                    ling.attack(minerals)

    def _prune_wave_tags(self, alive: Set[int]) -> None:
        self._wave_sent_tags &= alive

    def _manage_waves(self) -> None:
        """Gather at natural; WAVE 1 size gate, then attack by WAVE_ATTACK_MODE."""
        bot = self.bot
        lings = bot.units(UnitTypeId.ZERGLING).ready
        if not lings:
            return

        alive = {u.tag for u in lings}
        self._prune_wave_tags(alive)

        rally = self._natural_rally()
        home_lings = lings.tags_not_in(self._wave_sent_tags)

        sent = lings.tags_in(self._wave_sent_tags)
        if sent and (bot.state.game_loop % 8 == 0 or sent.idle.amount > 0):
            targets = sent if bot.state.game_loop % 8 == 0 else sent.idle
            if self._wave_mode == "main":
                main = self._enemy_main()
                for ling in targets:
                    ling.attack(main)
            else:
                self._assign_harass_targets(targets)

        if not home_lings:
            return

        # Defend first: collapse onto marines/army at home before rally moves
        if self._collapse_lings_home(home_lings):
            return

        # Only re-issue move when idle / far — avoid canceling attack orders
        for ling in home_lings:
            if ling.distance_to(rally) <= 3:
                continue
            if ling.is_attacking:
                continue
            ling.move(rally)

        gathered = home_lings.closer_than(_RALLY_RADIUS, rally)

        if self._last_wave_size == 0:
            need = self._wave1_min
            if home_lings.amount < need or gathered.amount < int(need * 0.7):
                if not self._logged_gathering:
                    log_event(
                        bot,
                        f"GATHER wave 1 at natural ({gathered.amount}/{need}, pool={home_lings.amount})",
                    )
                    self._logged_gathering = True
                return

            if self._wave_mode == "main":
                main = self._enemy_main()
                for ling in home_lings:
                    ling.attack(main)
                    self._wave_sent_tags.add(ling.tag)
                focus_note = "main"
            elif self._wave_mode == "natural_main":
                self._wave_focus = "natural"
                self._assign_harass_targets(home_lings)
                for ling in home_lings:
                    self._wave_sent_tags.add(ling.tag)
                focus_note = "natural->main"
            else:
                self._wave_focus = "third"
                self._assign_harass_targets(home_lings)
                for ling in home_lings:
                    self._wave_sent_tags.add(ling.tag)
                focus_note = "third->natural"
            self._last_wave_size = home_lings.amount
            self._next_wave_target = max(
                need + 1, math.ceil(self._last_wave_size * _WAVE_GROWTH)
            )
            self._wave_number = 1
            log_event(
                bot,
                f"WAVE {self._wave_number} attack {focus_note} "
                f"(size={self._last_wave_size}, next>={self._next_wave_target})",
            )
            self._logged_attack = True
            self._logged_gathering = False
            return

        if gathered.amount >= self._next_wave_target:
            if self._wave_mode == "main":
                main = self._enemy_main()
                for ling in home_lings:
                    ling.attack(main)
                    self._wave_sent_tags.add(ling.tag)
                focus_note = "main"
            elif self._wave_mode == "natural_main":
                self._assign_harass_targets(home_lings)
                for ling in home_lings:
                    self._wave_sent_tags.add(ling.tag)
                focus_note = "natural->main"
            else:
                self._assign_harass_targets(home_lings)
                for ling in home_lings:
                    self._wave_sent_tags.add(ling.tag)
                focus_note = "third->natural"
            size = home_lings.amount
            self._last_wave_size = size
            self._next_wave_target = max(
                self._next_wave_target + 1, math.ceil(size * _WAVE_GROWTH)
            )
            self._wave_number += 1
            log_event(
                bot,
                f"WAVE {self._wave_number} attack {focus_note} "
                f"(size={size}, next>={self._next_wave_target})",
            )
            self._logged_gathering = False
            return

        if not self._logged_gathering:
            log_event(
                bot,
                f"GATHER wave {self._wave_number + 1} at natural "
                f"({gathered.amount}/{self._next_wave_target})",
            )
            self._logged_gathering = True

    def _expansion_candidates(self) -> List[Point2]:
        bot = self.bot
        main = self._enemy_main()
        our = bot.start_location
        spots = []
        for exp in bot.expansion_locations_list:
            if exp.distance_to(our) < 12:
                continue
            if exp.distance_to(main) < 12:
                continue
            spots.append(exp)
        spots.sort(key=lambda p: p.distance_to(main))
        return spots

    def _update_main_destroyed(self) -> None:
        main = self._enemy_main()
        main_ths = self._townhalls().closer_than(_MAIN_RADIUS, main)
        if main_ths:
            self._main_townhall_seen = True
            return
        if self._main_townhall_seen and not main_ths:
            if not self._main_destroyed:
                self._main_destroyed = True
                if not self._logged_main_destroyed:
                    log_event(self.bot, "MAIN DESTROYED - scouting expansions")
                    self._logged_main_destroyed = True

    def _refresh_known_townhalls(self) -> None:
        bot = self.bot
        self._known_th_points = [th.position for th in self._townhalls()]
        for th in self._townhalls():
            key = Point2((round(th.position.x), round(th.position.y)))
            if key not in self._found_th_logged and self._main_destroyed:
                if th.distance_to(self._enemy_main()) > _MAIN_RADIUS:
                    log_event(bot, f"FOUND townhall at {th.position}")
                    self._found_th_logged.add(key)

    def _scout_tags(self) -> Set[int]:
        tags: Set[int] = set()
        for assigned in self._scout_assignments.values():
            tags |= assigned
        return tags

    def _assign_expansion_scouts(self) -> None:
        bot = self.bot
        lings = bot.units(UnitTypeId.ZERGLING).ready
        if not lings:
            return

        candidates = self._expansion_candidates()
        already = self._scout_tags()
        max_scouts = max(_SCOUTS_PER_EXPANSION, int(lings.amount * _MAX_SCOUT_FRACTION))

        for exp in candidates:
            if exp in self._cleared_expansions:
                continue

            near = lings.closer_than(10, exp)
            th_here = self._townhalls().closer_than(_EXP_TH_RADIUS, exp)
            if near and not th_here and exp in self._scout_assignments:
                self._cleared_expansions.add(exp)
                already -= self._scout_assignments.get(exp, set())
                self._scout_assignments.pop(exp, None)
                continue

            assigned = self._scout_assignments.get(exp, set())
            alive = {u.tag for u in lings}
            assigned = {t for t in assigned if t in alive}

            if len(already) >= max_scouts and not assigned:
                continue

            need = _SCOUTS_PER_EXPANSION - len(assigned)
            budget = max_scouts - len(already)
            need = min(need, budget)
            if need > 0:
                pool = lings.tags_not_in(already | assigned)
                for ling in pool.take(need):
                    assigned.add(ling.tag)
                    already.add(ling.tag)
                    ling.attack(exp)
            if assigned:
                self._scout_assignments[exp] = assigned
                for ling in lings.tags_in(assigned):
                    if ling.is_idle:
                        ling.attack(exp)

        if not self._logged_scout and self._scout_assignments:
            n = sum(len(v) for v in self._scout_assignments.values())
            log_event(
                bot,
                f"SCOUT expansions (scouts={n}/{lings.amount}, active_spots={len(self._scout_assignments)})",
            )
            self._logged_scout = True

        pending = [
            e
            for e in candidates
            if e not in self._cleared_expansions
            and not self._townhalls().closer_than(_EXP_TH_RADIUS, e)
        ]
        bot.expansion_scout_done = len(pending) == 0 and self._main_destroyed

    def _send_army_to_townhalls(self) -> None:
        bot = self.bot
        lings = bot.units(UnitTypeId.ZERGLING).ready
        if not lings:
            return

        scout_tags = self._scout_tags()
        if self._known_th_points:
            targets = list(self._known_th_points)
            army = lings.tags_not_in(scout_tags) if scout_tags else lings
            if army.amount < max(1, int(lings.amount * 0.5)):
                army = lings
        else:
            targets = [self._enemy_main()]
            army = lings.tags_not_in(scout_tags) if scout_tags else lings

        if not targets or not army:
            return

        i = 0
        for ling in army:
            ling.attack(targets[i % len(targets)])
            i += 1

    def _post_speed_attack(self) -> None:
        bot = self.bot
        if not hasattr(bot, "expansion_scout_done"):
            bot.expansion_scout_done = False

        if not bot.units(UnitTypeId.ZERGLING).ready:
            return

        self._update_main_destroyed()
        self._refresh_known_townhalls()

        if not self._main_destroyed:
            self._manage_waves()
            return

        self._assign_expansion_scouts()
        self._send_army_to_townhalls()

    async def manage(self) -> None:
        """Pre-speed home scout/defend; after speed, waves then expand-hunt."""
        bot = self.bot
        speed_done = bot.already_pending_upgrade(UpgradeId.ZERGLINGMOVEMENTSPEED) == 1
        if not speed_done:
            await self._pre_speed_home_control()
            return
        self._post_speed_attack()


