"""KauKauBot — AI Arena bot built on ares-sc2.

This file is hooks only. Ares picks the opening; the opening name selects a
`BuildDefinition`, and the two engines run whatever steps and routines that
definition lists. Adding a build touches nothing here.

Every python-sc2 hook must call its ares superclass first — that super call
drives ares' manager hub, build order runner and behavior executioner.
Skipping it silently disables the framework.
"""

from __future__ import annotations

from typing import Optional

from ares import AresBot
from loguru import logger
from sc2.data import Result
from sc2.ids.unit_typeid import UnitTypeId
from sc2.unit import Unit

from bot.common.log import log_event
from bot.core import BotContext, CombatEngine, MacroEngine, RunState, roles
from bot.core.registry import UnknownBuild, default_build, get_build

# Team policy (Jason via CoS): local + validate end at 7:00 game time via
# `run_game(..., game_time_limit=...)`. Ladder never passes the limit.
# Extending past 7:00 needs explicit Jason confirmation via CoS — never silently.
# Do NOT call `client.leave()` mid-step — ares `_after_step` then hits
# ProtocolError: Not in a game.
LOCAL_GAME_TIME_LIMIT_SECONDS: float = 7 * 60

# Structures worth a line in the timeline log.
LOGGED_STRUCTURES: frozenset[UnitTypeId] = frozenset(
    {
        UnitTypeId.SPAWNINGPOOL,
        UnitTypeId.EXTRACTOR,
        UnitTypeId.HATCHERY,
        UnitTypeId.LAIR,
        UnitTypeId.HIVE,
        UnitTypeId.EVOLUTIONCHAMBER,
        UnitTypeId.INFESTATIONPIT,
        UnitTypeId.SPINECRAWLER,
        UnitTypeId.SPORECRAWLER,
        UnitTypeId.COMMANDCENTER,
        UnitTypeId.SUPPLYDEPOT,
        UnitTypeId.BARRACKS,
        UnitTypeId.NEXUS,
        UnitTypeId.PYLON,
        UnitTypeId.GATEWAY,
        UnitTypeId.WARPGATE,
        UnitTypeId.ASSIMILATOR,
        UnitTypeId.CYBERNETICSCORE,
        UnitTypeId.TWILIGHTCOUNCIL,
        UnitTypeId.ROBOTICSFACILITY,
        UnitTypeId.SHIELDBATTERY,
    }
)

# Army / tech units worth a timeline line (Chargelot timing checks, etc.).
LOGGED_UNITS: frozenset[UnitTypeId] = frozenset(
    {
        UnitTypeId.ADEPT,
        UnitTypeId.STALKER,
        UnitTypeId.ZEALOT,
        UnitTypeId.WARPPRISM,
        UnitTypeId.OBSERVER,
    }
)


def _completion_message(logged: set[int], unit: Unit, count: int) -> str | None:
    """The `COMPLETE` log line for `unit`'s first-seen completion, or `None`
    for a type nobody logs or a tag already logged once. Split out from
    `on_building_construction_complete` as a plain function so the dedup
    logic - the actual point of interest, see that hook's docstring - is
    testable without an `AresBot` instance. Mutates `logged` on a hit, same
    as the `set` it wraps normally would."""
    if unit.type_id not in LOGGED_STRUCTURES or unit.tag in logged:
        return None
    logged.add(unit.tag)
    return f"COMPLETE {unit.type_id.name.lower()} ({count})"


def _structure_log_count(bot: "KauKauBot", unit_type: UnitTypeId) -> int:
    """Ready count for timeline logs. Gateways include Warp Gates so the
    running total does not drop when ares morphs them."""
    count = bot.structures(unit_type).amount
    if unit_type == UnitTypeId.GATEWAY:
        count += bot.structures(UnitTypeId.WARPGATE).amount
    return count


class KauKauBot(AresBot):
    ctx: BotContext

    def __init__(self, game_step_override: Optional[int] = None):
        super().__init__(game_step_override)
        # The context needs `self.mediator`, which only exists once ares'
        # on_start has built the manager hub.
        self.ctx = None  # type: ignore[assignment]
        self.macro = MacroEngine()
        self.combat = CombatEngine()
        self._logged_completions: set[int] = set()
        """Structure tags already logged by `on_building_construction_complete`
        - see that hook's docstring for why a tag can otherwise log twice
        (or more) in realtime mode."""

    # --- lifecycle -------------------------------------------------------

    async def on_start(self) -> None:
        await super(KauKauBot, self).on_start()

        opening: str = self.build_order_runner.chosen_opening
        build = self._resolve_build(opening)
        self.ctx = BotContext(bot=self, build=build, state=RunState())

        log_event(
            self,
            f"START race={self.race.name} map={self.game_info.map_name} "
            f"opening={opening} build={build.name}",
        )

    def _resolve_build(self, opening: str):
        """Fall back loudly rather than ending a ladder game over a typo."""
        try:
            return get_build(opening, self.race)
        except UnknownBuild as error:
            fallback = default_build(self.race)
            logger.critical(f"{error} Falling back to {fallback.name}.")
            return fallback

    async def on_step(self, iteration: int) -> None:
        await super(KauKauBot, self).on_step(iteration)

        self.macro.execute(self.ctx)
        self.combat.execute(self.ctx)

    async def on_end(self, game_result: Result) -> None:
        await super(KauKauBot, self).on_end(game_result)
        log_event(self, f"END result={game_result}")

    # --- roles -----------------------------------------------------------

    async def on_unit_created(self, unit: Unit) -> None:
        await super(KauKauBot, self).on_unit_created(unit)
        roles.assign_on_created(self.ctx, unit)
        if unit.type_id in LOGGED_UNITS:
            count = self.units(unit.type_id).amount
            log_event(self, f"TRAINED {unit.type_id.name.lower()} ({count})")

    async def on_unit_destroyed(self, unit_tag: int) -> None:
        await super(KauKauBot, self).on_unit_destroyed(unit_tag)
        roles.forget_destroyed(self.ctx, unit_tag)

    # --- logging ---------------------------------------------------------

    async def on_building_construction_complete(self, unit: Unit) -> None:
        """Log once per structure - python-sc2 can re-fire this in realtime."""
        await super(KauKauBot, self).on_building_construction_complete(unit)

        message = _completion_message(
            self._logged_completions,
            unit,
            _structure_log_count(self, unit.type_id),
        )
        if message is not None:
            log_event(self, message)

    async def on_upgrade_complete(self, upgrade) -> None:
        # AresBot does not override this python-sc2 hook, so there is no
        # super() implementation to chain into.
        log_event(self, f"COMPLETE upgrade {upgrade.name}")
