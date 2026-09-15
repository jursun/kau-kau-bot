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
class ChargelotMetrics:
    """Regression KPIs for 2base Chargelot (see `intel.chargelot_metrics`)."""

    adept_damage_dealt: float = 0.0
    adept_damage_taken: float = 0.0
    adept_kills: int = 0
    adept_last_hp: float | None = None
    adept_tag: int | None = None
    """Last harassing Adept tag (for death-frame damage credit)."""
    adept_prey_hp: dict[int, float] = field(default_factory=dict)
    adept_produced: int = 0
    adept_died: int = 0
    adept_shade_aborts: int = 0
    stalkers_before_robo: bool | None = None
    """True when the 2nd Stalker arrived before any Robo was started."""
    time_second_stalker: float | None = None
    time_robo_started: float | None = None
    """First frame any Robotics Facility is live or pending."""
    prism_produced: bool = False
    time_prism: float | None = None
    """First frame a Warp Prism is alive, phasing, or pending (queue-start
    inclusive) - see `time_prism_completed` for the actual finish time."""
    time_prism_phased: float | None = None
    """First frame a Warp Prism is in phasing mode."""
    time_prism_completed: float | None = None
    """Game time the Warp Prism finished training (`on_unit_created`)."""
    observer_produced: bool = False
    time_observer: float | None = None
    scout_probe_death_time: float | None = None
    """Game time the opening harass Probe died, or None if it survived."""
    adept_death_time: float | None = None
    """Game time the harassing Adept died, or None if it survived."""
    charge_complete_time: float | None = None
    stalkers_trained: int = 0
    """Cumulative Stalkers trained (never decremented on death)."""
    time_2_stalkers: float | None = None
    """Game time the 2nd Stalker was trained."""
    zealots_trained: int = 0
    """Cumulative Zealots trained (never decremented on death)."""
    time_6_zealots: float | None = None
    """Game time the 6th Zealot was trained."""
    muster_commit_time: float | None = None
    """Game time when Chargelot muster released the first wave."""
    muster_form_ready_since: float | None = None
    """When form-up criteria first met while waiting on Prism (timeout clock)."""
    warpgate_peak: int = 0
    prism_warps: int = 0
    """Zealot/Stalker (etc.) created inside a phasing Prism field."""
    pylon_warps: int = 0
    """Warp-ins that appeared without a phasing Prism field (home/nat)."""
    auto_supply_block_frames: int = 0
    """Frames AutoSupply needed a Pylon but ProtossBuildStructure failed."""
    nat_nexus_ready: bool = False
    max_minerals_after_nat: int = 0
    max_gas_after_nat: int = 0
    probes_trained: int = 0
    """Cumulative Probes trained (never decremented on death)."""
    time_last_probe: float | None = None
    """Game time the most recently completed Probe finished training -
    overwritten on every one, not just the first (unlike the other
    trained-unit timestamps here), so it reads as "when did worker
    production last happen" - useful for spotting when it capped out at
    `WORKER_TARGET` vs stalled early."""


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
    worker_harass_kills: int = 0
    """Enemy workers finished by the opening scout Probe."""
    chargelot_metrics: ChargelotMetrics = field(default_factory=ChargelotMetrics)
    worker_harass_prey_hp: dict[int, float] = field(default_factory=dict)
    worker_harass_opening_builder_tag: int | None = None
    """Preferred harass worker — built the opening supply (Pylon/Depot)."""
    worker_harass_returning: bool = False
    worker_harass_last_action: str | None = None
    worker_harass_gas_scouted: bool = False
    """True once the opening base-circle scout lap is finished."""
    worker_harass_enemy_has_gas: bool = False
    worker_harass_geysers_seen: set[tuple[float, float]] = field(
        default_factory=set
    )
    """Visited base-circle waypoint keys (rounded x,y) during the scout lap."""
    worker_harass_focus_tag: int | None = None
    worker_harass_known_builders: set[int] = field(default_factory=set)
    worker_harass_worker_dists: dict[int, float] = field(default_factory=dict)
    worker_harass_pressure_clear: int = 0
    worker_harass_hit_sources: dict[int, float] = field(default_factory=dict)
    """tag → last time seen in hit radius on a damage frame (kite memory)."""
    mustering_tags: set[int] = field(default_factory=set)
    """Attacking units still forming up at the rally point (see
    `bot.routines.combat.attack_squads`)."""
    defender_hold: dict[int, Point2] = field(default_factory=dict)
    """DEFENDING unit tag → sticky hold Point2 (matched to live holds by
    nearest, not list index — townhall iteration order is unstable)."""
    chargelot_wave_gate_ready_since: float | None = None
    """When wave_gate first passed while under wave1_min (force-leave clock)."""
    chargelot_muster_committed_at: float | None = None
    """Game time the first-wave muster committed (see `routines.combat.
    chargelot_attack`/`chargelot_kiting`) - drives the post-commit kite
    window before full onslaught. Kept separate from `chargelot_metrics.
    muster_commit_time` (pure observability) so metrics stay free to change
    independent of behavior."""
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
    chrono_target_cooldowns: dict[int, float] = field(default_factory=dict)
    """Tag -> game time last Chrono Boosted (see `steps.protoss.
    chrono_boost_army`). Tracked here rather than trusting `has_buff` alone,
    which was observed not to reliably reflect a just-issued Chrono Boost on
    the very next frame - reading it live re-targeted the same handful of
    structures every frame for a full minute before this existed."""
    prism_drop_phase: str | None = None
    """One-shot Prism drop-harass state machine (see `routines.
    protoss_support.escort_warp_prism`/`claim_drop_squad_unit`). `None`
    until eligible; then "waiting_for_squad" -> "loading" -> "flying_in" ->
    "dropping" -> "rephasing" -> "phased_in_base", with "retreating" as the
    abort path back to "done". "done" and (unused, reserved) "aborted" are
    terminal - normal escort resumes either way, and this never re-enters
    since it is a once-per-game maneuver."""
    prism_drop_squad_tags: set[int] = field(default_factory=set)
    """Zealots/Stalkers claimed for the drop-harass squad - see
    `claim_drop_squad_unit`."""
    prism_drop_target: Point2 | None = None
    """High-ground drop point in the enemy main, picked once
    (`_drop_pick_point`) and reused for the rest of the sequence."""
    prism_drop_phase_entered_at: float | None = None
    """Game time the current `prism_drop_phase` was entered - drives the
    "rephasing" and "phased_in_base" timeouts."""
    log_once: LogOnce = field(default_factory=LogOnce)
