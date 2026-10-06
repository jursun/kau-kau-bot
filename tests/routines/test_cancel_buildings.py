"""Cancel under-construction structures that are about to die."""

from __future__ import annotations

from unittest.mock import MagicMock

from ares.behaviors.combat.individual import UseAbility
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.core.context import BotContext
from bot.core.state import RunState
from bot.routines import cancel_buildings as cb

AT = Point2((50.0, 50.0))


def _structure(tag=1, health=100.0, health_max=300.0, progress=0.4, ready=False,
               type_id=UnitTypeId.SPINECRAWLER) -> MagicMock:
    s = MagicMock()
    s.tag = tag
    s.position = AT
    s.health = health
    s.health_max = health_max
    s.build_progress = progress
    s.is_ready = ready
    s.type_id = type_id
    return s


def _enemy(dps=20.0, at=Point2((53.0, 50.0)), ground=True) -> MagicMock:
    e = MagicMock()
    e.position = at
    e.ground_dps = dps
    e.can_attack_ground = ground
    return e


def _ctx(structures, enemies) -> BotContext:
    bot = MagicMock()
    bot.structures = structures
    bot.calculate_cost.return_value = MagicMock(minerals=100, vespene=0)
    ctx = BotContext(bot=bot, build=MagicMock(), state=RunState())
    ctx.mediator.get_cached_enemy_army = list(enemies)
    ctx.log = MagicMock()
    return ctx


def _cancels(ctx) -> list:
    return [
        c.args[0]
        for c in ctx.bot.register_behavior.call_args_list
        if isinstance(c.args[0], UseAbility)
    ]


def test_cancels_a_structure_the_enemy_will_kill_in_seconds() -> None:
    doomed = _structure(health=100.0)
    ctx = _ctx([doomed], [_enemy(dps=40.0)])  # 100 / 40 = 2.5s

    cb.cancel_doomed_buildings()(ctx)

    cancels = _cancels(ctx)
    assert len(cancels) == 1
    assert cancels[0].ability == AbilityId.CANCEL_BUILDINPROGRESS
    assert cancels[0].unit is doomed
    assert "refunds 75 minerals" in ctx.log.call_args.args[0]


def test_dps_stacks_across_the_enemy_units_in_range() -> None:
    structure = _structure(health=150.0)
    ctx = _ctx([structure], [_enemy(dps=25.0), _enemy(dps=25.0), _enemy(dps=25.0)])

    cb.cancel_doomed_buildings()(ctx)

    assert len(_cancels(ctx)) == 1  # 150 / 75 = 2s


def test_a_lone_poke_does_not_cancel() -> None:
    structure = _structure(health=250.0)
    ctx = _ctx([structure], [_enemy(dps=10.0)])  # 25s to die

    cb.cancel_doomed_buildings()(ctx)

    assert _cancels(ctx) == []


def test_a_nearly_dead_structure_is_cancelled_even_at_low_dps() -> None:
    structure = _structure(health=40.0, health_max=300.0)  # 13% hp
    ctx = _ctx([structure], [_enemy(dps=5.0)])  # 8s - slow, but nearly dead

    cb.cancel_doomed_buildings()(ctx)

    assert len(_cancels(ctx)) == 1


def test_enemies_out_of_range_do_not_count() -> None:
    structure = _structure(health=50.0)
    ctx = _ctx([structure], [_enemy(dps=100.0, at=Point2((90.0, 50.0)))])

    cb.cancel_doomed_buildings()(ctx)

    assert _cancels(ctx) == []


def test_enemies_that_cannot_hit_ground_do_not_count() -> None:
    structure = _structure(health=50.0)
    ctx = _ctx([structure], [_enemy(dps=100.0, ground=False)])

    cb.cancel_doomed_buildings()(ctx)

    assert _cancels(ctx) == []


def test_a_structure_about_to_finish_is_left_alone() -> None:
    structure = _structure(health=50.0, progress=0.95)
    ctx = _ctx([structure], [_enemy(dps=100.0)])

    cb.cancel_doomed_buildings()(ctx)

    assert _cancels(ctx) == []


def test_finished_structures_are_never_cancelled() -> None:
    structure = _structure(health=50.0, progress=1.0, ready=True)
    ctx = _ctx([structure], [_enemy(dps=100.0)])

    cb.cancel_doomed_buildings()(ctx)

    assert _cancels(ctx) == []


def test_each_structure_is_cancelled_once() -> None:
    structure = _structure(health=50.0)
    ctx = _ctx([structure], [_enemy(dps=100.0)])

    cb.cancel_doomed_buildings()(ctx)
    cb.cancel_doomed_buildings()(ctx)

    assert len(_cancels(ctx)) == 1


def test_no_enemies_does_nothing() -> None:
    ctx = _ctx([_structure(health=50.0)], [])

    cb.cancel_doomed_buildings()(ctx)

    ctx.bot.register_behavior.assert_not_called()


def test_creep_tumors_are_never_cancelled() -> None:
    for tumor_type in (
        UnitTypeId.CREEPTUMOR,
        UnitTypeId.CREEPTUMORQUEEN,
        UnitTypeId.CREEPTUMORBURROWED,
    ):
        tumor = _structure(health=10.0, type_id=tumor_type)
        ctx = _ctx([tumor], [_enemy(dps=100.0)])

        cb.cancel_doomed_buildings()(ctx)

        assert _cancels(ctx) == [], tumor_type


def test_a_free_structure_has_no_refund_to_cancel_for() -> None:
    structure = _structure(health=10.0)
    ctx = _ctx([structure], [_enemy(dps=100.0)])
    ctx.bot.calculate_cost.return_value = MagicMock(minerals=0, vespene=0)

    cb.cancel_doomed_buildings()(ctx)

    assert _cancels(ctx) == []
