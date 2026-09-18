"""Scripted stress events for harness games.

The AI opponent can't be relied on to actually harass the mineral line at a
chosen time, so this injects the outcome directly via SC2's debug API
(`client.debug_kill_unit` - the same call `ares.chat_debug.ChatDebug` sends
from an in-game chat command, driven here from harness code instead since
these are already headless, `Debug: True` local test games). Lets a build's
opening resilience be tested on demand: "if we lose N workers at time T,
does the build order still recover" - without scripting an actual attack.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger


@dataclass(frozen=True)
class WorkerLossEvent:
    at_time: float
    count: int


def parse_worker_loss_spec(spec: str) -> tuple[WorkerLossEvent, ...]:
    """Parse "TIME:COUNT[,TIME:COUNT...]" (seconds, worker count).

    e.g. "45:2,120:5" -> lose 2 workers at 0:45, then 5 more at 2:00.
    Events are returned sorted by time regardless of input order, since
    `attach_worker_loss_scenario` fires them in that order.
    """
    events = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        time_str, _, count_str = chunk.partition(":")
        if not count_str:
            raise ValueError(
                f"Bad worker-loss event {chunk!r}, expected TIME:COUNT"
            )
        events.append(WorkerLossEvent(at_time=float(time_str), count=int(count_str)))
    return tuple(sorted(events, key=lambda e: e.at_time))


def attach_worker_loss_scenario(bot: Any, events: tuple[WorkerLossEvent, ...]) -> None:
    """Wrap `bot.on_step` to debug-kill `count` workers the first frame
    `bot.time >= at_time`, once per event, then leave the build to react on
    its own from there.

    Victims are the workers closest to `bot.start_location` - where a real
    mineral-line runby lands - rather than an arbitrary/oldest pick, which
    could grab a build-order scout instead of an actual gatherer.
    """
    if not events:
        return

    original_on_step = bot.on_step
    pending = sorted(events, key=lambda e: e.at_time)

    async def on_step_with_worker_loss(iteration: int) -> None:
        while pending and bot.time >= pending[0].at_time:
            event = pending.pop(0)
            victims = bot.workers.closest_n_units(bot.start_location, event.count)
            if not victims:
                logger.warning(
                    f"Worker-loss scenario: no workers left to kill "
                    f"(wanted {event.count} at {event.at_time}s)"
                )
                continue
            await bot.client.debug_kill_unit(victims.tags)
            logger.info(
                f"Worker-loss scenario: killed {len(victims)} workers at "
                f"t={bot.time:.1f}s (requested {event.count} at {event.at_time}s)"
            )
        await original_on_step(iteration)

    bot.on_step = on_step_with_worker_loss  # type: ignore[method-assign]
