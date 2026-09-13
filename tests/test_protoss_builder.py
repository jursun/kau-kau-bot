"""Tests for the dedicated Protoss macro builder."""

from __future__ import annotations

from collections import defaultdict
from unittest.mock import MagicMock

from ares.consts import TIME_ORDER_COMMENCED, TARGET, UnitRole
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.behaviors.protoss.builder import ensure_protoss_builder
from bot.behaviors.protoss.build_structure import ProtossBuildStructure
from bot.core.context import BotContext
from bot.core.state import RunState
from bot.steps import protoss as p


def _ctx() -> BotContext:
    bot = MagicMock()
    bot.mediator = MagicMock()
    bot.worker_type = UnitTypeId.PROBE
    bot.unit_tag_dict = {}
    bot.time = 100.0
    bot.race.name = "Protoss"
    bot.not_started_but_in_building_tracker.return_value = 0
    bot.tech_requirement_progress.return_value = 1.0
    bot.structure_pending.return_value = 0
    bot.structures.return_value.amount = 0

    build = MagicMock()
    build.economy.max_bases = 2

    ctx = BotContext(bot=bot, build=build, state=RunState())
    bot.ctx = ctx
    return ctx


def test_ensure_protoss_builder_waits_while_busy() -> None:
    ctx = _ctx()
    worker = MagicMock()
    worker.tag = 42
    worker.is_constructing_scv = True
    worker.is_idle = False
    worker.is_carrying_resource = False
    worker.position = Point2((10, 10))
    ctx.bot.unit_tag_dict[42] = worker
    ctx.state.protoss_builder_tag = 42
    ctx.mediator.get_building_tracker_dict = {
        42: {
            "id": UnitTypeId.PYLON,
            TIME_ORDER_COMMENCED: 95.0,
            TARGET: Point2((10.5, 10.5)),
        }
    }
    ctx.mediator.get_unit_role_dict = {UnitRole.PERSISTENT_BUILDER: {42}}

    assert ensure_protoss_builder(ctx.bot, ctx.mediator, Point2((10, 10))) is None
    ctx.mediator.select_worker.assert_not_called()


def test_ensure_protoss_builder_latches_persistent_builder() -> None:
    ctx = _ctx()
    worker = MagicMock()
    worker.tag = 7
    ctx.mediator.get_building_tracker_dict = {}
    ctx.mediator.get_units_from_role.return_value = MagicMock(first=worker)
    ctx.mediator.get_unit_role_dict = {UnitRole.PERSISTENT_BUILDER: {7}}

    got = ensure_protoss_builder(ctx.bot, ctx.mediator, Point2((10, 10)))

    assert got is worker
    assert ctx.state.protoss_builder_tag == 7
    ctx.mediator.select_worker.assert_not_called()


def test_ensure_protoss_builder_replaces_stuck_probe() -> None:
    ctx = _ctx()
    target = Point2((10.5, 10.5))
    stuck = MagicMock()
    stuck.tag = 42
    stuck.is_constructing_scv = False
    stuck.is_idle = True
    stuck.is_carrying_resource = False
    stuck.position = Point2((10, 10))

    replacement = MagicMock()
    replacement.tag = 99

    ctx.bot.unit_tag_dict = {42: stuck, 99: replacement}
    ctx.bot.can_afford.return_value = True
    ctx.bot.tech_requirement_progress.return_value = 1.0
    ctx.state.protoss_builder_tag = 42
    ctx.mediator.get_building_tracker_dict = {
        42: {
            "id": UnitTypeId.PYLON,
            TIME_ORDER_COMMENCED: 85.0,
            TARGET: target,
        }
    }
    ctx.mediator.get_building_counter = defaultdict(int)
    ctx.mediator.get_unit_role_dict = {UnitRole.PERSISTENT_BUILDER: {42}}

    gatherers = MagicMock()
    gatherers.filter.return_value = [replacement]
    ctx.mediator.get_units_from_role.return_value = gatherers

    got = ensure_protoss_builder(ctx.bot, ctx.mediator, Point2((10, 10)))

    assert got is replacement
    assert ctx.state.protoss_builder_tag == 99
    assert 42 not in ctx.mediator.get_building_tracker_dict
    ctx.mediator.assign_role.assert_any_call(tag=42, role=UnitRole.GATHERING)
    ctx.mediator.assign_role.assert_any_call(
        tag=99, role=UnitRole.PERSISTENT_BUILDER
    )


def test_ensure_protoss_builder_does_not_replace_while_waiting_on_minerals() -> None:
    ctx = _ctx()
    worker = MagicMock()
    worker.tag = 42
    worker.is_constructing_scv = False
    worker.is_idle = True
    worker.position = Point2((10, 10))
    ctx.bot.unit_tag_dict[42] = worker
    ctx.bot.can_afford.return_value = False
    ctx.state.protoss_builder_tag = 42
    ctx.mediator.get_building_tracker_dict = {
        42: {
            "id": UnitTypeId.GATEWAY,
            TIME_ORDER_COMMENCED: 85.0,
            TARGET: Point2((10.5, 10.5)),
        }
    }
    ctx.mediator.get_unit_role_dict = {UnitRole.PERSISTENT_BUILDER: {42}}

    assert ensure_protoss_builder(ctx.bot, ctx.mediator, Point2((10, 10))) is None
    assert ctx.state.protoss_builder_tag == 42
    ctx.mediator.select_worker.assert_not_called()


def test_protoss_steps_return_protoss_build_structure() -> None:
    ctx = _ctx()
    behavior = p.pylon_buffer()(ctx)
    assert isinstance(behavior, ProtossBuildStructure)
