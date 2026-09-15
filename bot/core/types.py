"""Callable contracts the engines run.

A build is data plus a handful of these small callables. Keeping them as
plain aliases (rather than classes) is what lets a build be declared as a
flat tuple of named factories instead of a subclass.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from ares.behaviors.macro import MacroBehavior
    from sc2.ids.upgrade_id import UpgradeId
    from sc2.position import Point2
    from sc2.unit import Unit

    from bot.core.context import BotContext

# Returns a behavior to add to this frame's MacroPlan, or None to skip.
MacroStep: TypeAlias = "Callable[[BotContext], MacroBehavior | None]"

# Registers its own maneuvers on the bot; returns nothing.
CombatRoutine: TypeAlias = "Callable[[BotContext], None]"

# A yes/no condition evaluated fresh each frame.
Gate: TypeAlias = "Callable[[BotContext], bool]"

# Resolves a map position fresh each frame. Used where a build needs to name
# a place rather than a number - a proxy site, an overridden rally point -
# and the place is only knowable once the game (and the map) exists.
PointLocator: TypeAlias = "Callable[[BotContext], Point2]"

# Reacts to one freshly created unit; returns nothing. Called from
# `roles.assign_on_created` after role assignment (every unit, regardless of
# which role branch matched, if any), so a build can react to a specific
# unit (see `steps.terran.claim_z_on_first_scv`, or `chargelot_metrics.
# note_unit_created`) without roles.py itself needing to know that build
# exists.
UnitCreatedHook: TypeAlias = "Callable[[BotContext, Unit], None]"

# Per-frame lifecycle reaction; returns nothing. Called from KauKauBot's own
# hook of the same name, unconditionally when set - lets a build own its own
# frame-tick bookkeeping (e.g. `chargelot_metrics.update_chargelot_metrics`)
# without bot/main.py needing to know that build exists.
StepHook: TypeAlias = "Callable[[BotContext], None]"

# End-of-game reaction; returns nothing. Called from KauKauBot.on_end after
# the generic END log line.
GameEndHook: TypeAlias = "Callable[[BotContext], None]"

# Reacts to a unit's destruction; returns nothing. Called from KauKauBot.
# on_unit_destroyed before `roles.forget_destroyed` clears any tags for it -
# a build watching a specific tag set (e.g. `worker_harass_tags`) needs to
# see it still there.
UnitDestroyedHook: TypeAlias = "Callable[[BotContext, int], None]"

# Reacts to a completed upgrade; returns nothing. Called from KauKauBot.
# on_upgrade_complete.
UpgradeCompleteHook: TypeAlias = "Callable[[BotContext, UpgradeId], None]"
