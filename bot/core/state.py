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
    """Air vision scout tags (e.g. Zerg Overlord) — not the worker harasser."""
    worker_harass_tags: set[int] = field(default_factory=set)
    """One-shot opening worker harass scout."""
    worker_harass_done: bool = False
    """Latched once claimed / finished / dead — never send a replacement."""
    worker_harass_miss_frames: int = 0
    worker_harass_kiting: bool = False
    worker_harass_last_hp: float | None = None
    worker_harass_damage_dealt: float = 0.0
    worker_harass_damage_taken: float = 0.0
    worker_harass_prey_hp: dict[int, float] = field(default_factory=dict)
    worker_harass_opening_builder_tag: int | None = None
    """Preferred harass worker — built the opening supply (Pylon/Depot)."""
    worker_harass_returning: bool = False
    worker_harass_last_action: str | None = None
    worker_harass_gas_scouted: bool = False
    worker_harass_enemy_has_gas: bool = False
    worker_harass_geysers_seen: set[tuple[float, float]] = field(
        default_factory=set
    )
    worker_harass_focus_tag: int | None = None
    worker_harass_known_builders: set[int] = field(default_factory=set)
    worker_harass_worker_dists: dict[int, float] = field(default_factory=dict)
    worker_harass_pressure_clear: int = 0
    worker_harass_mw_until: float = 0.0
    worker_harass_hit_sources: dict[int, float] = field(default_factory=dict)
    """tag → last time seen in hit radius on a damage frame (kite memory)."""
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
    protoss_builder_tag: int | None = None
    """Dedicated Probe for macro construction (`PERSISTENT_BUILDER`)."""
    adept_shade_cast_at: dict[int, float] = field(default_factory=dict)
    """Adept tag → game time when its current shade was cast."""
    adept_shade_goal: dict[int, Point2] = field(default_factory=dict)
    """Adept tag → final destination the shade should path toward."""
    adept_shade_aborted: set[int] = field(default_factory=set)
    """Adept tags whose shade was CANCEL'd (no teleport) for danger."""
    adept_shade_last_pos: dict[int, Point2] = field(default_factory=dict)
    """Adept tag → last seen shade position (teleport verification)."""
    adept_shade_awaiting_teleport: set[int] = field(default_factory=set)
    """Tags whose shade expired safely — next frame check adept jumped."""
    adept_attack_pending: dict[int, int] = field(default_factory=dict)
    """Adept tag → enemy tag for an attack issued while weapon_cd was 0.
    Cleared once weapon_cooldown rises so we do not re-issue and cancel windup."""
    adept_last_action: str | None = None
    """Last ADEPT action log line — only re-log on change."""
    chargelot_prism_gas_bank: bool = False
    """Latched once Charge gas covers a Prism — stay peeled for minerals
    until the Prism is started (avoids 3↔1 thrash when Stalkers spend gas)."""
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
