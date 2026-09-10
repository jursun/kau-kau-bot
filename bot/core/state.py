"""Mutable per-game state.

Steps and routines are stateless functions, so anything that has to persist
between frames lives here and is reached through `ctx.state`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bot.common.log import LogOnce


@dataclass
class RunState:
    """Everything that changes over the course of one game."""

    wave_number: int = 0
    next_wave_size: int = 0
    scout_tags: set[int] = field(default_factory=set)
    mustering_tags: set[int] = field(default_factory=set)
    """Attacking units still forming up at the rally point (see
    `bot.routines.combat.attack_squads`)."""
    retreating_tags: set[int] = field(default_factory=set)
    """Attacking units that disengaged an unfavorable fight and fell back to
    the rally point - unlike `mustering_tags`, these do NOT auto-release on
    arrival; they sit at the rally until `release_waves` sweeps them into
    the next wave it promotes, so a disengaged squad always attacks again
    alongside reinforcements rather than alone (see
    `bot.routines.combat.attack_squads`/`release_waves`)."""
    log_once: LogOnce = field(default_factory=LogOnce)
