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
    reserved_placement: Point2 | None = None
    """Formation slot held for the current task via
    `request_building_placement` - set early for X's opening Barracks so Y
    cannot take the same spot, cleared when the task advances."""


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
    scout_probe_done: bool = False
    """Latches True once a harass Probe has been claimed (or finished), so we
    never send a replacement after death / home."""
    scout_probe_miss_frames: int = 0
    """Consecutive frames the claimed scout Probe was missing from unit lists."""
    scout_probe_regen: bool = False
    """True while the harass Probe is kiting after taking damage."""
    scout_last_hp: float | None = None
    """Prior frame health+shield — used to detect damage for kite."""
    scout_damage_dealt: float = 0.0
    """Cumulative HP+shield the harass Probe removed from nearby workers."""
    scout_damage_taken: float = 0.0
    """Cumulative HP+shield the harass Probe lost while scouting."""
    scout_prey_hp: dict[int, float] = field(default_factory=dict)
    """Last-seen HP+shield of workers near the scout (damage-dealt attribution)."""
    scout_pylon_builder_tag: int | None = None
    """Probe that built the opening Pylon — preferred harass scout."""
    scout_probe_returning: bool = False
    """True while the scout is pathing home; stay in SCOUTING until near base."""
    scout_last_action: str | None = None
    """Last SCOUT action log line — only re-log when the action changes."""
    scout_gas_scouted: bool = False
    """True after the Probe has checked enemy main geysers for gas buildings."""
    scout_enemy_has_gas: bool = False
    """Latched when a gas building was seen during the gas check."""
    scout_geysers_seen: set[tuple[float, float]] = field(default_factory=set)
    """Main-geyser positions already walked for the opening gas check."""
    scout_focus_tag: int | None = None
    """Sticky harass target — stay on them to secure the kill."""
    scout_known_builder_tags: set[int] = field(default_factory=set)
    """Builder workers seen on incomplete buildings; chase while near the job."""
    scout_worker_dists: dict[int, float] = field(default_factory=dict)
    """Prior-frame distances to nearby enemy workers (incoming detection)."""
    scout_pressure_clear_frames: int = 0
    """Consecutive frames without aggressors before ending kite."""
    scout_mw_until: float = 0.0
    """Game time until which mineral-walk escape may keep gathering."""
    mustering_tags: set[int] = field(default_factory=set)
    """Attacking units still forming up at the rally point (see
    `bot.routines.combat.attack_squads`)."""
    proxy_crew: ProxyCrewState = field(default_factory=ProxyCrewState)
    supply_depot_builder_tag: int | None = None
    """SCV claimed by `steps.terran.continuous_main_depots` - stays off the
    mineral line laying Depots once its gate opens."""
    enemy_main_townhall_seen: bool = False
    """Latches True the first time a townhall is visible near the enemy
    start - see `targeting.enemy_main_fallen`. Without this, fog of war
    makes "no townhall near start" true from frame one."""
    supply_depot_queued: bool = False
    """True while the Depot builder is in ares' building tracker for its
    current Depot - same meaning as `CrewMember.queued`."""
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
