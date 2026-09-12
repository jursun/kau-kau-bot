"""Mutable per-game state.

Steps and routines are stateless functions, so anything that has to persist
between frames lives here and is reached through `ctx.state`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sc2.position import Point2

from bot.common.log import LogOnce


@dataclass
class CrewMember:
    """One slot (`x`, `y` or `z`) of a `bot.builds.definition.ProxyCrewPlan`.

    `tag` is `None` until the worker is claimed. `task_index` counts how many
    of that slot's tasks are already behind it; `queued` is True for the
    stretch between issuing the current task (`mediator.
    build_with_specific_worker`) and seeing its tag drop back out of
    `mediator.get_building_tracker_dict`, which is how `steps.terran.
    proxy_crew` tells "still walking/building" from "done, move on"."""

    tag: int | None = None
    task_index: int = 0
    queued: bool = False


@dataclass
class ProxyCrewState:
    """Live progress for a build's `ProxyCrewPlan`, if it has one. Unused
    (all three slots stay at their defaults) by any build that doesn't set
    `BuildDefinition.crew` - see `steps.terran.proxy_crew`."""

    x: CrewMember = field(default_factory=CrewMember)
    y: CrewMember = field(default_factory=CrewMember)
    z: CrewMember = field(default_factory=CrewMember)


@dataclass
class RunState:
    """Everything that changes over the course of one game."""

    wave_number: int = 0
    next_wave_size: int = 0
    scout_tags: set[int] = field(default_factory=set)
    mustering_tags: set[int] = field(default_factory=set)
    """Attacking units still forming up at the rally point (see
    `bot.routines.combat.attack_squads`)."""
    proxy_crew: ProxyCrewState = field(default_factory=ProxyCrewState)
    cleanup_depot_builder_tag: int | None = None
    """SCV claimed by `steps.terran.continuous_main_depots` - stays off the
    mineral line laying Depots once its gate opens."""
    enemy_main_townhall_seen: bool = False
    """Latches True the first time a townhall is visible near the enemy
    start - see `targeting.enemy_main_fallen`. Without this, fog of war
    makes "no townhall near start" true from frame one."""
    cleanup_depot_queued: bool = False
    """True while the cleanup Depot builder is in ares' building tracker
    for its current Depot - same meaning as `CrewMember.queued`."""
    hunt_objective: Point2 | None = None
    """Single army-wide scout/cleanup destination while hunting remaining
    bases after the enemy main falls - see `targeting.hunt_remaining_bases`.
    Pinned until the spot is checked (expansions) or nothing remains near
    it (leftover structures), so split squads do not each chase a different
    closest building."""
    scouted_expansions: set[Point2] = field(default_factory=set)
    """Expansions already checked during a post-main hunt. Latched on
    vision or army arrival so fog of war after leaving a base cannot send
    the army back there (the natural ↔ third oscillation)."""
    log_once: LogOnce = field(default_factory=LogOnce)
