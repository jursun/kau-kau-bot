"""What a build *is*: data, plus a few named callables.

Adding a build means adding one file that constructs one of these. There is
no subclassing: behaviour that differs between builds is expressed by which
steps and routines the build lists, and in what order.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from bot.consts import FOCUS_MAIN
from bot.core.types import CombatRoutine, Gate, MacroStep


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
    """How far in front of our natural defenders gather."""
    focus: tuple[str, ...] = (FOCUS_MAIN,)
    """Ordered places to walk to when no enemy structure is visible."""


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

    def __post_init__(self) -> None:
        total = sum(v["proportion"] for v in self.army.comp.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"{self.name}: army comp proportions sum to {total}, expected 1.0"
            )
        if not self.army.types:
            raise ValueError(f"{self.name}: army.types must not be empty")
