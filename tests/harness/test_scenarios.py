"""Tests for scripts/harness/scenarios.py's worker-loss stress injection.

Pure Python, no live game — `attach_worker_loss_scenario` is exercised
against a duck-typed fake bot with an async `on_step`/`client.debug_kill_
unit`, the same style `tests/validators/_fakes.py` uses elsewhere.
"""

from __future__ import annotations

import asyncio

import pytest
from sc2.position import Point2

from scripts.harness.scenarios import (
    WorkerLossEvent,
    attach_worker_loss_scenario,
    parse_worker_loss_spec,
)


def test_parse_worker_loss_spec_parses_and_sorts_by_time() -> None:
    events = parse_worker_loss_spec("120:5,45:2")

    assert events == (
        WorkerLossEvent(at_time=45.0, count=2),
        WorkerLossEvent(at_time=120.0, count=5),
    )


def test_parse_worker_loss_spec_ignores_blank_chunks_and_whitespace() -> None:
    events = parse_worker_loss_spec(" 45:2 , , 90:3 ")

    assert events == (
        WorkerLossEvent(at_time=45.0, count=2),
        WorkerLossEvent(at_time=90.0, count=3),
    )


def test_parse_worker_loss_spec_rejects_a_malformed_chunk() -> None:
    with pytest.raises(ValueError):
        parse_worker_loss_spec("45")


class _FakeUnits(list):
    @property
    def tags(self) -> list[int]:
        return [u.tag for u in self]


class _FakeWorker:
    def __init__(self, tag: int, position: Point2):
        self.tag = tag
        self.position = position


class _FakeWorkers(list):
    """Stands in for `BotAI.workers`: `.closest_n_units` only, since that's
    all the scenario reads."""

    def closest_n_units(self, position: Point2, n: int) -> _FakeUnits:
        by_distance = sorted(self, key=lambda u: u.position.distance_to(position))
        return _FakeUnits(by_distance[:n])


class _FakeClient:
    """Mutates the shared `workers` list on kill, matching real SC2: a
    debug-killed unit is dead and gone from `BotAI.workers` on later
    frames, not just flagged."""

    def __init__(self, workers: _FakeWorkers):
        self.kill_calls: list[list[int]] = []
        self._workers = workers

    async def debug_kill_unit(self, tags) -> None:
        tags = list(tags)
        self.kill_calls.append(tags)
        self._workers[:] = [u for u in self._workers if u.tag not in tags]


class _FakeBot:
    def __init__(self, worker_count: int):
        self.time = 0.0
        self.start_location = Point2((0.0, 0.0))
        self.workers = _FakeWorkers(
            _FakeWorker(i, Point2((float(i), 0.0))) for i in range(worker_count)
        )
        self.client = _FakeClient(self.workers)
        self.on_step_calls: list[int] = []

        async def on_step(iteration: int) -> None:
            self.on_step_calls.append(iteration)

        self.on_step = on_step


def _run(coro) -> None:
    asyncio.run(coro)


def test_no_events_leaves_on_step_untouched() -> None:
    bot = _FakeBot(worker_count=6)
    original = bot.on_step

    attach_worker_loss_scenario(bot, ())

    assert bot.on_step is original


def test_kill_fires_once_time_reaches_the_event_and_targets_the_closest_workers() -> None:
    bot = _FakeBot(worker_count=6)
    attach_worker_loss_scenario(bot, (WorkerLossEvent(at_time=45.0, count=2),))

    bot.time = 44.9
    _run(bot.on_step(0))
    assert bot.client.kill_calls == []
    assert len(bot.workers) == 6

    bot.time = 45.0
    _run(bot.on_step(1))
    assert bot.client.kill_calls == [[0, 1]]  # closest to start_location (0, 0)
    assert bot.on_step_calls == [0, 1]  # wrapped on_step still runs every frame

    # Does not re-fire on a later frame.
    bot.time = 50.0
    _run(bot.on_step(2))
    assert bot.client.kill_calls == [[0, 1]]


def test_multiple_events_fire_in_time_order_each_once() -> None:
    bot = _FakeBot(worker_count=10)
    attach_worker_loss_scenario(
        bot,
        (
            WorkerLossEvent(at_time=90.0, count=3),
            WorkerLossEvent(at_time=45.0, count=2),
        ),
    )

    bot.time = 45.0
    _run(bot.on_step(0))
    assert bot.client.kill_calls == [[0, 1]]

    bot.time = 90.0
    _run(bot.on_step(1))
    assert bot.client.kill_calls == [[0, 1], [2, 3, 4]]


def test_no_workers_left_logs_and_does_not_crash() -> None:
    bot = _FakeBot(worker_count=0)
    attach_worker_loss_scenario(bot, (WorkerLossEvent(at_time=10.0, count=3),))

    bot.time = 10.0
    _run(bot.on_step(0))  # should not raise

    assert bot.client.kill_calls == []
