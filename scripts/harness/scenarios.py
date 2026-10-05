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

_TARGETS: frozenset[str] = frozenset({"closest", "gas"})


@dataclass(frozen=True)
class WorkerLossEvent:
    at_time: float
    count: int
    target: str = "closest"
    """Which workers to kill: "closest" (default) picks the `count` workers
    nearest `bot.start_location` - where a real mineral-line runby lands.
    "gas" instead picks from workers ares' `ResourceManager` currently has
    assigned to a geyser (`mediator.get_worker_to_vespene_dict`) - for
    testing a harass that specifically snipes gas rather than the whole
    mineral line."""


def parse_worker_loss_spec(spec: str) -> tuple[WorkerLossEvent, ...]:
    """Parse "TIME:COUNT[:TARGET][,TIME:COUNT[:TARGET]...]" (seconds, worker
    count, optional "closest"/"gas" - defaults to "closest").

    e.g. "45:2,90:3:gas" -> lose 2 workers closest to home at 0:45, then the
    3 workers on gas at 1:30. Events are returned sorted by time regardless
    of input order, since `attach_worker_loss_scenario` fires them in order.
    """
    events = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(":")
        if len(parts) not in (2, 3):
            raise ValueError(
                f"Bad worker-loss event {chunk!r}, expected TIME:COUNT[:TARGET]"
            )
        time_str, count_str, *rest = parts
        target = rest[0] if rest else "closest"
        if target not in _TARGETS:
            raise ValueError(
                f"Bad worker-loss target {target!r} in {chunk!r}, "
                f"expected one of {sorted(_TARGETS)}"
            )
        events.append(
            WorkerLossEvent(at_time=float(time_str), count=int(count_str), target=target)
        )
    return tuple(sorted(events, key=lambda e: e.at_time))


def _victims(bot: Any, event: WorkerLossEvent) -> Any:
    if event.target == "gas":
        # `get_worker_to_vespene_dict` is a `@property` on ManagerMediator,
        # not a method - no call parens.
        gas_tags = set(bot.mediator.get_worker_to_vespene_dict.keys())
        return bot.workers.tags_in(gas_tags).take(event.count)
    return bot.workers.closest_n_units(bot.start_location, event.count)


def attach_worker_loss_scenario(bot: Any, events: tuple[WorkerLossEvent, ...]) -> None:
    """Wrap `bot.on_step` to debug-kill `count` workers the first frame
    `bot.time >= at_time`, once per event, then leave the build to react on
    its own from there. See `WorkerLossEvent.target` for victim selection.
    """
    if not events:
        return

    original_on_step = bot.on_step
    pending = sorted(events, key=lambda e: e.at_time)

    async def on_step_with_worker_loss(iteration: int) -> None:
        while pending and bot.time >= pending[0].at_time:
            event = pending.pop(0)
            victims = _victims(bot, event)
            if not victims:
                logger.warning(
                    f"Worker-loss scenario: no {event.target!r} workers to "
                    f"kill (wanted {event.count} at {event.at_time}s)"
                )
                continue
            await bot.client.debug_kill_unit(victims.tags)
            logger.info(
                f"Worker-loss scenario: killed {len(victims)} {event.target!r} "
                f"workers at t={bot.time:.1f}s (requested {event.count} at "
                f"{event.at_time}s)"
            )
        await original_on_step(iteration)

    bot.on_step = on_step_with_worker_loss  # type: ignore[method-assign]


MAIN_RADIUS: float = 25.0
"""How far from `bot.start_location` the "main" pseudo-target reaches."""


@dataclass(frozen=True)
class StructureLossEvent:
    at_time: float
    names: tuple[str, ...]
    """`UnitTypeId` names (e.g. "SPAWNINGPOOL") and/or the pseudo-target
    "main" (every structure within `MAIN_RADIUS` of `bot.start_location`,
    townhall included - what a lost base trade leaves behind)."""


def parse_structure_loss_spec(spec: str) -> tuple[StructureLossEvent, ...]:
    """Parse "TIME:NAME[+NAME...][,TIME:NAME...]" - e.g.
    "420:SPAWNINGPOOL+ROACHWARREN,600:main". Events come back sorted by time.
    """
    events = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(":")
        if len(parts) != 2 or not parts[1].strip():
            raise ValueError(
                f"Bad structure-loss event {chunk!r}, expected TIME:NAME[+NAME...]"
            )
        names = tuple(n.strip().upper() if n.strip().lower() != "main" else "main"
                      for n in parts[1].split("+") if n.strip())
        events.append(StructureLossEvent(at_time=float(parts[0]), names=names))
    return tuple(sorted(events, key=lambda e: e.at_time))


def _structure_victims(bot: Any, event: StructureLossEvent) -> list[Any]:
    victims: list[Any] = []
    for name in event.names:
        if name == "main":
            victims.extend(
                s
                for s in bot.structures
                if s.position.distance_to(bot.start_location) <= MAIN_RADIUS
            )
        else:
            victims.extend(s for s in bot.structures if s.type_id.name == name)
    seen: set[int] = set()
    return [v for v in victims if not (v.tag in seen or seen.add(v.tag))]


def attach_structure_loss_scenario(
    bot: Any, events: tuple[StructureLossEvent, ...]
) -> None:
    """Wrap `bot.on_step` to debug-kill the named structures the first frame
    `bot.time >= at_time` - a stand-in for a base trade, to see whether the
    build re-places its tech, bases and army from a surviving base."""
    if not events:
        return

    original_on_step = bot.on_step
    pending = sorted(events, key=lambda e: e.at_time)

    async def on_step_with_structure_loss(iteration: int) -> None:
        while pending and bot.time >= pending[0].at_time:
            event = pending.pop(0)
            victims = _structure_victims(bot, event)
            if not victims:
                logger.warning(
                    f"Structure-loss scenario: nothing matching {event.names} "
                    f"at t={bot.time:.1f}s"
                )
                continue
            await bot.client.debug_kill_unit([v.tag for v in victims])
            logger.info(
                f"Structure-loss scenario: killed {len(victims)} structures "
                f"{event.names} at t={bot.time:.1f}s (requested {event.at_time}s)"
            )
        await original_on_step(iteration)

    bot.on_step = on_step_with_structure_loss  # type: ignore[method-assign]
