"""What a build *is*: data, plus a few named callables.

Adding a build means adding one file that constructs one of these. There is
no subclassing: behaviour that differs between builds is expressed by which
steps and routines the build lists, and in what order.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ares.consts import UnitRole
from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from bot.consts import FOCUS_MAIN, PROXY_CREW_ROLE
from bot.core.types import CombatRoutine, Gate, MacroStep, PointLocator, UnitCreatedHook


def _always(ctx) -> bool:
    return True


@dataclass(frozen=True)
class Economy:
    """Worker, base and gas targets. Race-neutral on purpose."""

    worker_target: int = 16
    """Hard ceiling on workers across all bases."""
    workers_per_base: int = 16
    """Ceiling contribution of each ready base; the lower of the two wins."""
    max_bases: int = 1
    gas_per_base: int = 1
    max_gas: int = 1
    workers_per_gas: int = 3
    long_distance_mine: bool = False


@dataclass(frozen=True)
class Army:
    """What this build makes, and what counts as 'army' for roles and waves."""

    comp: Mapping[UnitTypeId, Mapping[str, float | int]]
    """`SpawnController` composition; proportions must sum to 1.0."""
    types: frozenset[UnitTypeId]
    """Unit types the combat engine assigns roles to and counts into waves."""
    upgrades: tuple[UpgradeId, ...] = ()
    """Researched in order; `UpgradeController` auto-techs toward each."""
    evolution_chambers: int = 1
    """`UpgradeController` only ever builds one on its own (see
    `steps/zerg.py`'s `evolution_chambers`); a build wanting more than one
    +1/+1 tier researching in parallel raises this. The single source of
    truth for that count — `steps.zerg.evolution_chambers()` and
    `UpgradeRushValidator` both read it from here rather than each build
    passing its own literal around."""
    evolution_chamber_gate: Gate = _always
    """When to start wanting the *next* Evolution Chamber beyond the first
    (e.g. once Metabolic Boost is under way) — read by both the same step
    and the validator, for the same reason as `evolution_chambers` above."""


@dataclass(frozen=True)
class Combat:
    """Wave shaping plus the routines that actually issue orders."""

    routines: tuple[CombatRoutine, ...] = ()
    wave_gate: Gate = _always
    """Extra condition beyond wave size before a wave is released."""
    wave1_min: int = 6
    wave_growth: float = 1.10
    rally_offset: float = 8.0
    """How far in front of our natural defenders gather. Ignored when
    `rally` is set."""
    rally: PointLocator | None = None
    """Overrides where a wave musters and where defenders hold, for a build
    whose army does not spawn at home. A proxy build's Marines pop out on the
    far side of the map, so the default "in front of our own natural" rally
    would walk every new Marine all the way home before it attacked. Setting
    this also collapses `targeting.hold_positions` to just this point: a
    build that pins its rally somewhere specific means it, and should not
    also be sending half its defenders back to guard mineral lines."""
    focus: tuple[str, ...] = (FOCUS_MAIN,)
    """Ordered places to walk to when no enemy structure is visible."""


@dataclass(frozen=True)
class WorkerTask:
    """One build order for one `ProxyCrewPlan` worker: put `structure_id`
    down at `where`, once `gate` passes. See `steps.terran.proxy_crew` for
    how a task list is actually worked."""

    structure_id: UnitTypeId
    where: PointLocator
    gate: Gate = _always
    label: str = ""
    """Free-text name for logs and the validation report, e.g. "Barracks D".
    Purely descriptive - never read for control flow. Defaults to
    `structure_id`'s own name when blank."""


@dataclass(frozen=True)
class ProxyCrewPlan:
    """Three SCVs a build hand-walks through their own construction tasks,
    entirely outside the mining pool and outside ares' generic
    `BuildStructure`/`select_worker` (which only ever draws from
    `UnitRole.GATHERING` - see `steps.terran.proxy_barracks`'s docstring).

    `x_tasks` and `y_tasks` run on two of the starting 12 workers - the two
    closest to `x_tasks[0].where(ctx)` - claimed once, on the first frame of
    the game. `z_tasks` run on whichever worker `BuildDefinition.
    on_unit_created` hands off (see `steps.terran.claim_z_on_first_scv`,
    which a build using this plan should set as that hook).

    Read by both `steps.terran.proxy_crew` (to run it) and
    `tests.upgrade_rush_validator` (to report on it) - one declared plan,
    not two things to keep in sync by hand.
    """

    x_tasks: tuple[WorkerTask, ...]
    y_tasks: tuple[WorkerTask, ...]
    z_tasks: tuple[WorkerTask, ...]
    role: UnitRole = PROXY_CREW_ROLE
    """What every crew worker is assigned to, from claim to hand-off. See
    `bot.consts.PROXY_CREW_ROLE` for why the default is what it is."""


@dataclass(frozen=True)
class BuildDefinition:
    name: str
    """Must match an opening key under `Builds:` in `<race>_builds.yml`."""
    label: str
    race: Race
    economy: Economy
    army: Army
    combat: Combat
    macro_steps: tuple[MacroStep, ...] = ()
    """Priority-ordered. A `MacroPlan` stops at the first step that acts."""
    always: tuple[MacroStep, ...] = field(default_factory=tuple)
    """Registered every frame, including during the opening (mining, injects)."""
    crew: ProxyCrewPlan | None = None
    """Declares a build's proxy-crew choreography, if it has one. Only data -
    `steps.terran.proxy_crew` (which a build using this must list in
    `always`) is what actually runs it."""
    on_unit_created: UnitCreatedHook | None = None
    """Called from `roles.assign_on_created` after its generic role table,
    for a build that needs to react to a specific freshly created unit (e.g.
    `steps.terran.claim_z_on_first_scv`, for `crew.z_tasks` above)."""
    pool_deadline: float = 50.0
    """Latest acceptable Spawning Pool start time (game seconds), read by
    `UpgradeRushValidator`'s "Pool Timing" check. Defaults to an immediate-pool
    opening's expectation; a build whose `OpeningBuildOrder` deliberately
    expands (or does anything else) before pool should raise this to match
    its own opening rather than let the validator enforce a deadline
    calibrated for a different build's timing."""

    def __post_init__(self) -> None:
        total = sum(v["proportion"] for v in self.army.comp.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"{self.name}: army comp proportions sum to {total}, expected 1.0"
            )
        if not self.army.types:
            raise ValueError(f"{self.name}: army.types must not be empty")
