"""Single dedicated Probe for Protoss macro construction."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ares.consts import ID, TARGET, TIME_ORDER_COMMENCED, UnitRole
from cython_extensions import cy_closest_to, cy_distance_to
from sc2.position import Point2
from sc2.unit import Unit

if TYPE_CHECKING:
    from ares import AresBot
    from ares.managers.manager_mediator import ManagerMediator

    from bot.core.context import BotContext

# Stuck thresholds — conservative so normal walk times do not false-positive.
BUILDER_STUCK_AT_SITE_IDLE_S = 4.0
BUILDER_STUCK_NO_CONSTRUCT_S = 12.0
BUILDER_STUCK_FAR_IDLE_S = 10.0
BUILDER_STUCK_FAR_DISTANCE = 8.0


def _ctx(ai: "AresBot") -> "BotContext | None":
    return getattr(ai, "ctx", None)


def _target_point(target: Point2 | Unit) -> Point2:
    return target.position if hasattr(target, "position") else target


def _assign_persistent_builder(mediator: "ManagerMediator", tag: int) -> None:
    if tag not in mediator.get_unit_role_dict.get(UnitRole.PERSISTENT_BUILDER, set()):
        mediator.assign_role(tag=tag, role=UnitRole.PERSISTENT_BUILDER)


def _is_protoss_builder_stuck(
    ai: "AresBot",
    worker: Unit,
    tracker_entry: dict,
) -> bool:
    """True when a tracked builder is unlikely to finish its current order."""
    if worker.is_constructing_scv:
        return False

    structure_id = tracker_entry[ID]
    # Idle at the site while waiting on minerals/tech is normal — not stuck.
    if not ai.can_afford(structure_id) or ai.tech_requirement_progress(structure_id) < 1.0:
        return False

    elapsed = ai.time - tracker_entry[TIME_ORDER_COMMENCED]
    target_pos = _target_point(tracker_entry[TARGET])
    at_site = cy_distance_to(worker.position, target_pos) <= 1.5
    far_from_site = (
        cy_distance_to(worker.position, target_pos) > BUILDER_STUCK_FAR_DISTANCE
    )

    if worker.is_idle and at_site and elapsed >= BUILDER_STUCK_AT_SITE_IDLE_S:
        return True
    if worker.is_idle and far_from_site and elapsed >= BUILDER_STUCK_FAR_IDLE_S:
        return True
    if elapsed >= BUILDER_STUCK_NO_CONSTRUCT_S:
        return True

    return False


def _release_protoss_builder_from_tracker(
    mediator: "ManagerMediator",
    worker_tag: int,
) -> None:
    """Drop a worker from ares' building tracker (mirrors BuildingManager.remove_unit)."""
    tracker = mediator.get_building_tracker_dict
    if worker_tag not in tracker:
        return
    mediator.get_building_counter[tracker[worker_tag][ID]] -= 1
    tracker.pop(worker_tag, None)
    mediator.assign_role(tag=worker_tag, role=UnitRole.GATHERING)


def _select_replacement_builder(
    ai: "AresBot",
    mediator: "ManagerMediator",
    near: Point2,
    exclude_tag: int,
) -> Unit | None:
    """Pick a gatherer for macro build duty, never reusing the stuck probe."""
    gatherers = mediator.get_units_from_role(
        role=UnitRole.GATHERING, unit_type=ai.worker_type
    ).filter(
        lambda u: u.tag != exclude_tag and not u.is_carrying_resource
    )
    if gatherers:
        return cy_closest_to(near, gatherers)

    worker = mediator.select_worker(target_position=near, force_close=True)
    if worker is not None and worker.tag == exclude_tag:
        return None
    return worker


def _replace_stuck_protoss_builder(
    ctx: "BotContext",
    ai: "AresBot",
    mediator: "ManagerMediator",
    stuck: Unit,
    near: Point2,
) -> Unit | None:
    stuck_tag = stuck.tag
    ctx.log_once(
        f"macro_protoss_builder_stuck_{stuck_tag}",
        f"MACRO builder: probe {stuck_tag} stuck; releasing to gather",
    )
    _release_protoss_builder_from_tracker(mediator, stuck_tag)
    ctx.state.protoss_builder_tag = None

    replacement = _select_replacement_builder(ai, mediator, near, exclude_tag=stuck_tag)
    if replacement is None:
        return None

    ctx.state.protoss_builder_tag = replacement.tag
    ctx.log_once(
        f"macro_protoss_builder_replaced_{replacement.tag}",
        f"MACRO builder: designated probe {replacement.tag} for macro builds",
    )
    _assign_persistent_builder(mediator, replacement.tag)
    return replacement


def ensure_protoss_builder(
    ai: "AresBot",
    mediator: "ManagerMediator",
    near: Point2,
) -> Unit | None:
    """Return the latched macro builder, or claim one as PERSISTENT_BUILDER.

    While the builder is in ares' building tracker (walking or constructing),
    returns None so callers wait instead of pulling another gatherer. If the
    latched builder appears stuck, release it to gather and latch a replacement.
    """
    ctx = _ctx(ai)
    if ctx is None:
        return mediator.select_worker(target_position=near, force_close=True)

    state = ctx.state
    tracker = mediator.get_building_tracker_dict

    worker: Unit | None = None
    if state.protoss_builder_tag is not None:
        worker = ai.unit_tag_dict.get(state.protoss_builder_tag)

    # Absent from unit_tag_dict means dead/despawned — Unit has no is_alive.
    if worker is None:
        for role in (UnitRole.PERSISTENT_BUILDER, UnitRole.BUILDING):
            units = mediator.get_units_from_role(
                role=role, unit_type=ai.worker_type
            )
            if units:
                worker = units.first
                break
        if worker is None:
            worker = mediator.select_worker(target_position=near, force_close=True)
        if worker is None:
            return None
        state.protoss_builder_tag = worker.tag

    if worker.tag in tracker:
        if _is_protoss_builder_stuck(ai, worker, tracker[worker.tag]):
            worker = _replace_stuck_protoss_builder(ctx, ai, mediator, worker, near)
            if worker is None or worker.tag in tracker:
                return None
        else:
            return None

    _assign_persistent_builder(mediator, worker.tag)
    return worker
