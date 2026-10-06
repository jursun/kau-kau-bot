"""Home emergency: queens + drones defend a timing attack into our Spines."""

from __future__ import annotations

from unittest.mock import MagicMock

from ares.behaviors.combat.individual import AttackTarget, PathUnitToTarget, UseAbility
from ares.consts import UnitRole
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.core.context import BotContext
from bot.core.state import RunState
from bot.routines import emergency_defense as ed

BASE = Point2((20.0, 20.0))
SPINE_AT = Point2((30.0, 20.0))


def _unit(tag: int, at: Point2, type_id=UnitTypeId.ZEALOT, **attrs) -> MagicMock:
    unit = MagicMock()
    unit.tag = tag
    unit.position = at
    unit.type_id = type_id
    unit.is_structure = False
    unit.can_attack = True
    unit.health_percentage = 1.0
    unit.radius = 0.5
    for key, value in attrs.items():
        setattr(unit, key, value)
    return unit


def _spine(tag: int, health: float = 1.0) -> MagicMock:
    return _unit(
        tag, SPINE_AT, UnitTypeId.SPINECRAWLER, is_structure=True, health_percentage=health
    )


def _queen(tag: int, at: Point2 = Point2((24.0, 20.0)), energy: float = 60.0, **kw) -> MagicMock:
    return _unit(tag, at, UnitTypeId.QUEEN, energy=energy, **kw)


def _drone(tag: int, at: Point2 = Point2((15.0, 20.0))) -> MagicMock:
    return _unit(tag, at, UnitTypeId.DRONE)


def _ctx(enemies=(), queens=(), drones=(), spines=(), army=()) -> BotContext:
    bot = MagicMock()
    bot.time = 100.0
    townhall = MagicMock()
    townhall.position = BASE
    bot.townhalls = [townhall]
    bot.calculate_supply_cost.side_effect = lambda t: {
        UnitTypeId.ZEALOT: 2.0,
        UnitTypeId.DRONE: 1.0,
        UnitTypeId.QUEEN: 2.0,
        UnitTypeId.ZERGLING: 0.5,
    }.get(t, 2.0)
    bot.units = MagicMock(side_effect=lambda t=None: None)
    everything = list(queens) + list(army)
    units = MagicMock()
    units.__iter__ = lambda self: iter(everything)
    bot.units = MagicMock(return_value=list(queens))
    bot.units.__iter__ = lambda self: iter(everything)
    bot.workers = list(drones)
    spine_list = list(spines)
    structures = MagicMock()
    structures.ready = spine_list
    bot.structures = MagicMock(return_value=structures)
    build = MagicMock()
    build.army.types = frozenset({UnitTypeId.ZERGLING, UnitTypeId.ROACH})
    ctx = BotContext(bot=bot, build=build, state=RunState())
    ctx.mediator.get_cached_enemy_army = list(enemies)
    ctx.mediator.get_ground_grid = "grid"
    ctx.mediator.get_units_from_role.side_effect = lambda *, role, unit_type=None: (
        list(drones) if role == UnitRole.GATHERING else []
    )
    return ctx


def _micros(ctx) -> list:
    return [
        m
        for c in ctx.bot.register_behavior.call_args_list
        for m in getattr(c.args[0], "micros", [c.args[0]])
    ]


def _attackers(count: int, at: Point2 = Point2((28.0, 20.0))) -> list:
    return [_unit(900 + i, Point2((at.x + 0.3 * i, at.y))) for i in range(count)]


def test_no_threat_does_nothing() -> None:
    ctx = _ctx(enemies=[], queens=[_queen(1)])

    ed.pull_defense()(ctx)

    ctx.mediator.assign_role.assert_not_called()
    ctx.bot.register_behavior.assert_not_called()


def test_a_lone_scout_is_not_a_threat() -> None:
    ctx = _ctx(enemies=_attackers(1), queens=[_queen(1)], spines=[_spine(5)])

    ed.pull_defense()(ctx)

    ctx.mediator.assign_role.assert_not_called()


def test_an_army_far_from_home_is_not_a_threat() -> None:
    ctx = _ctx(enemies=_attackers(5, at=Point2((120.0, 120.0))), queens=[_queen(1)])

    ed.pull_defense()(ctx)

    ctx.mediator.assign_role.assert_not_called()


def test_a_timing_attack_pulls_queens_off_inject_and_creep() -> None:
    creep_q = _queen(1)
    inject_q = _queen(2)
    ctx = _ctx(enemies=_attackers(5), queens=[creep_q, inject_q], spines=[_spine(5)])
    ctx.mediator.get_units_from_role.side_effect = lambda *, role, unit_type=None: (
        [creep_q] if role == UnitRole.QUEEN_CREEP else []
    )

    ed.pull_defense()(ctx)

    assert ctx.state.pulled_queen_roles == {
        1: UnitRole.QUEEN_CREEP,
        2: UnitRole.QUEEN_INJECT,
    }
    ctx.mediator.assign_role.assert_any_call(tag=1, role=UnitRole.BASE_DEFENDER)
    ctx.mediator.assign_role.assert_any_call(tag=2, role=UnitRole.BASE_DEFENDER)


def test_queen_transfuses_a_damaged_spine_in_reach() -> None:
    queen = _queen(1, at=Point2((27.0, 20.0)))
    spine = _spine(5, health=0.5)
    ctx = _ctx(enemies=_attackers(5), queens=[queen], spines=[spine])

    ed.pull_defense()(ctx)

    casts = [m for m in _micros(ctx) if isinstance(m, UseAbility)]
    assert len(casts) == 1
    assert casts[0].ability == AbilityId.TRANSFUSION_TRANSFUSION
    assert casts[0].target is spine
    assert ctx.state.transfuse_at[5] == 100.0


def test_queen_walks_to_a_damaged_spine_out_of_reach() -> None:
    queen = _queen(1, at=Point2((15.0, 20.0)))  # 15 from the spine, search 14+
    queen.position = Point2((19.0, 20.0))  # 11 away: inside search, outside reach
    ctx = _ctx(enemies=_attackers(5), queens=[queen], spines=[_spine(5, 0.4)])

    ed.pull_defense()(ctx)

    micros = _micros(ctx)
    assert not [m for m in micros if isinstance(m, UseAbility)]
    assert [m for m in micros if isinstance(m, PathUnitToTarget)]


def test_two_queens_transfuse_two_different_spines() -> None:
    a, b = _queen(1, at=Point2((27.0, 20.0))), _queen(2, at=Point2((27.0, 21.0)))
    s1, s2 = _spine(5, 0.3), _spine(6, 0.5)
    ctx = _ctx(enemies=_attackers(5), queens=[a, b], spines=[s1, s2])

    ed.pull_defense()(ctx)

    casts = [m for m in _micros(ctx) if isinstance(m, UseAbility)]
    assert {c.target.tag for c in casts} == {5, 6}


def test_transfuse_does_not_recast_on_the_same_spine_within_the_heal_window() -> None:
    queen = _queen(1, at=Point2((27.0, 20.0)))
    ctx = _ctx(enemies=_attackers(5), queens=[queen], spines=[_spine(5, 0.4)])
    ed.pull_defense()(ctx)
    ctx.bot.register_behavior.reset_mock()
    ctx.bot.time = 103.0

    ed.pull_defense()(ctx)

    assert not [m for m in _micros(ctx) if isinstance(m, UseAbility)]


def test_queen_without_energy_does_not_transfuse_and_fights_instead() -> None:
    queen = _queen(1, at=Point2((27.0, 20.0)), energy=10.0)
    ctx = _ctx(enemies=_attackers(5), queens=[queen], spines=[_spine(5, 0.4)])

    ed.pull_defense()(ctx)

    micros = _micros(ctx)
    assert not [m for m in micros if isinstance(m, UseAbility)]
    assert [m for m in micros if isinstance(m, AttackTarget)]


def test_healthy_spines_are_not_transfused() -> None:
    queen = _queen(1, at=Point2((27.0, 20.0)))
    ctx = _ctx(enemies=_attackers(5), queens=[queen], spines=[_spine(5, 0.9)])

    ed.pull_defense()(ctx)

    assert not [m for m in _micros(ctx) if isinstance(m, UseAbility)]


def test_drones_are_pulled_when_the_defenders_are_outmatched() -> None:
    drones = [_drone(100 + i, Point2((15.0 + i, 20.0))) for i in range(30)]
    ctx = _ctx(enemies=_attackers(8), queens=[_queen(1)], drones=drones, spines=[_spine(5)])

    ed.pull_defense()(ctx)

    # 8 zealots = 16 supply -> about 16 drones, nearest the fight first.
    assert 8 <= len(ctx.state.pulled_drone_tags) <= ed.DRONE_PULL_MAX
    assert len(ctx.state.pulled_drone_tags) == 16
    ctx.mediator.remove_worker_from_mineral.assert_called()
    attacks = [m for m in _micros(ctx) if isinstance(m, AttackTarget)]
    assert {a.unit.tag for a in attacks} >= ctx.state.pulled_drone_tags


def test_drones_stay_home_when_the_defense_is_enough() -> None:
    drones = [_drone(100 + i) for i in range(30)]
    army = [_unit(200 + i, Point2((24.0, 20.0)), UnitTypeId.ZERGLING) for i in range(40)]
    ctx = _ctx(
        enemies=_attackers(4), queens=[_queen(1)], drones=drones, spines=[_spine(5)], army=army
    )

    ed.pull_defense()(ctx)

    assert ctx.state.pulled_drone_tags == set()
    assert ctx.state.pulled_queen_roles  # queens still fight


def test_roles_are_restored_once_the_threat_has_been_gone_for_the_hold() -> None:
    drones = [_drone(100 + i) for i in range(20)]
    queen = _queen(1)
    ctx = _ctx(enemies=_attackers(8), queens=[queen], drones=drones, spines=[_spine(5)])
    ed.pull_defense()(ctx)
    pulled = set(ctx.state.pulled_drone_tags)
    assert pulled

    ctx.mediator.get_cached_enemy_army = []
    ctx.bot.time += ed.PULL_HOLD_S - 1.0
    ed.pull_defense()(ctx)
    assert ctx.state.pulled_drone_tags == pulled  # still holding

    ctx.bot.time += 2.0
    ed.pull_defense()(ctx)

    assert ctx.state.pulled_drone_tags == set()
    assert ctx.state.pulled_queen_roles == {}
    ctx.mediator.assign_role.assert_any_call(tag=100, role=UnitRole.GATHERING)
    ctx.mediator.assign_role.assert_any_call(tag=1, role=UnitRole.QUEEN_INJECT)


def test_dead_pulled_units_are_forgotten() -> None:
    drones = [_drone(100 + i) for i in range(20)]
    ctx = _ctx(enemies=_attackers(8), queens=[_queen(1)], drones=drones, spines=[_spine(5)])
    ed.pull_defense()(ctx)
    survivors = drones[10:]  # the ten nearest-pulled drones died
    ctx.bot.workers = survivors
    ctx.mediator.get_units_from_role.side_effect = lambda *, role, unit_type=None: (
        list(survivors) if role == UnitRole.GATHERING else []
    )

    ed.pull_defense()(ctx)

    assert not any(tag < 110 for tag in ctx.state.pulled_drone_tags)
