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
    from sc2.position import Point2

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
