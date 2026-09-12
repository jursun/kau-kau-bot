"""Shared duck-typed test doubles for `tests/validators/`'s own test files.

Leading underscore keeps `registry.py`'s package scan from ever trying to
import this as a build's validator module.
"""

from __future__ import annotations

from types import SimpleNamespace

from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId


class _Counted:
    """Stands in for a `Units` collection: `.amount`, `.ready`, truthiness."""

    def __init__(self, amount: int = 0, ready_amount: int | None = None):
        self.amount = amount
        self.ready = self if ready_amount is None else _Counted(ready_amount)

    def __bool__(self) -> bool:
        return self.amount > 0

    def __iter__(self):
        return iter(())


class _FakeUnit:
    def __init__(self, tag: int, type_id=UnitTypeId.ZERGLING):
        self.tag = tag
        self.type_id = type_id


class _FakeUnits(list):
    """Stands in for python-sc2's `Units`: just the `.tags_in` the validator
    needs to pick the newly-released wave's actual unit objects back out of
    the current ATTACKING group."""

    def tags_in(self, tags) -> "_FakeUnits":
        return _FakeUnits(u for u in self if u.tag in tags)


def _fake_crew_member() -> SimpleNamespace:
    return SimpleNamespace(tag=None, task_index=0, queued=False)


def _fake_task(structure_id=UnitTypeId.BARRACKS, label: str = "") -> SimpleNamespace:
    return SimpleNamespace(structure_id=structure_id, label=label)


def _fake_crew_plan() -> SimpleNamespace:
    return SimpleNamespace(
        x_tasks=(
            _fake_task(UnitTypeId.BARRACKS, "Barracks A"),
            _fake_task(UnitTypeId.BARRACKS, "Barracks D"),
        ),
        y_tasks=(
            _fake_task(UnitTypeId.BARRACKS, "Barracks B"),
            _fake_task(UnitTypeId.SUPPLYDEPOT, "Depot (proxy)"),
        ),
        z_tasks=(
            _fake_task(UnitTypeId.SUPPLYDEPOT, "Depot (home)"),
            _fake_task(UnitTypeId.BARRACKS, "Barracks C"),
        ),
    )


class _FakeCtx:
    """Duck-typed `BotContext`: just what the validator reads."""

    def __init__(
        self,
        upgrades: tuple = (),
        max_gas: int = 2,
        wave1_min: int = 20,
        wave_growth: float = 1.25,
        wave_stage_label: str = "Attack Waves",
        evolution_chambers: int = 1,
        evolution_chamber_gate=lambda ctx: True,
        pool_deadline: float = 50.0,
        race: Race = Race.Zerg,
        crew=None,
    ):
        self.build = SimpleNamespace(
            race=race,
            army=SimpleNamespace(
                upgrades=upgrades,
                evolution_chambers=evolution_chambers,
                evolution_chamber_gate=evolution_chamber_gate,
            ),
            economy=SimpleNamespace(worker_target=60, max_gas=max_gas),
            combat=SimpleNamespace(
                wave1_min=wave1_min,
                wave_growth=wave_growth,
                wave_stage_label=wave_stage_label,
            ),
            pool_deadline=pool_deadline,
            crew=crew,
        )
        self.state = SimpleNamespace(
            wave_number=0,
            proxy_crew=SimpleNamespace(
                x=_fake_crew_member(), y=_fake_crew_member(), z=_fake_crew_member()
            ),
        )
        self.attacking: list = []

    def units_in_role(self, role) -> _FakeUnits:
        return _FakeUnits(self.attacking)


class FakeAI:
    """Just enough of the `BotAI` surface for `BaseValidator` (or one of its
    subclasses) to track a game against. Composition means this is a plain
    object the validator wraps now, not something the validator subclasses
    the way the old mixin design needed."""

    def __init__(
        self,
        upgrades: tuple = (),
        max_gas: int = 2,
        wave_stage_label: str = "Attack Waves",
        evolution_chambers: int = 1,
        evolution_chamber_gate=lambda ctx: True,
        pool_deadline: float = 50.0,
        race: Race = Race.Zerg,
        crew=None,
    ):
        self.time = 0.0
        self.supply_left = 10
        self.supply_used = 14
        self.workers = _Counted(12)
        self.gas_buildings = _Counted(0)
        # `.get_cached_enemy_army` is a plain attribute here (a test sets it
        # directly), standing in for ares' real `ManagerMediator` property.
        self.mediator = SimpleNamespace(get_cached_enemy_army=[])
        self.ctx = _FakeCtx(
            upgrades,
            max_gas=max_gas,
            wave_stage_label=wave_stage_label,
            evolution_chambers=evolution_chambers,
            evolution_chamber_gate=evolution_chamber_gate,
            pool_deadline=pool_deadline,
            race=race,
            crew=crew,
        )
        self._structure_counts: dict = {}
        self._structure_ready_counts: dict = {}
        self._pending_counts: dict = {}
        self._pending_upgrades: set = set()
        self._affordable: set = set()

    def structures(self, unit_type) -> _Counted:
        amount = self._structure_counts.get(unit_type, 0)
        ready = self._structure_ready_counts.get(unit_type, amount)
        return _Counted(amount, ready)

    def already_pending(self, unit_type) -> int:
        return self._pending_counts.get(unit_type, 0)

    def already_pending_upgrade(self, upgrade) -> float:
        return 1.0 if upgrade in self._pending_upgrades else 0.0

    def pending_or_complete_upgrade(self, upgrade) -> bool:
        return upgrade in self._pending_upgrades

    def can_afford(self, item) -> bool:
        return item in self._affordable

    def tech_requirement_progress(self, structure_type) -> float:
        return 1.0

    # Real per-unit supply costs, just for the couple of types these tests
    # use - not a general `sc2.BotAI.calculate_supply_cost` stand-in.
    _SUPPLY_COSTS = {UnitTypeId.ZERGLING: 0.5, UnitTypeId.ROACH: 2.0}

    def calculate_supply_cost(self, unit_type) -> float:
        return self._SUPPLY_COSTS.get(unit_type, 1.0)
