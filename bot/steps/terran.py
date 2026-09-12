"""Terran-specific macro steps.

Factories here (not in `steps/common.py`) for anything that names a
Terran-only structure or unit, mirroring how `steps/zerg.py` owns queens and
hatcheries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cython_extensions import cy_distance_to, cy_distance_to_squared
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.builds.definition import _always
from bot.consts import SUPPLY_BUILDER_ROLE
from bot.core.types import Gate, MacroStep, UnitCreatedHook
from bot.routines.placement import near_point

if TYPE_CHECKING:
    from sc2.unit import Unit

    from bot.builds.definition import WorkerTask
    from bot.core.context import BotContext
    from bot.core.state import CrewMember


def claim_z_on_first_scv() -> UnitCreatedHook:
    """Claim the 13th SCV as `proxy_crew.z` (`on_unit_created` hook).

    Requires `len(workers) >= 13` so a starting SCV cannot be claimed if
    `on_unit_created` fires for one of the opening twelve.
    """

    def hook(ctx: "BotContext", unit: "Unit") -> None:
        if unit.type_id != UnitTypeId.SCV:
            return
        crew = ctx.state.proxy_crew
        if crew.z.tag is not None:
            return
        if len(ctx.bot.workers) < 13:
            return
        crew.z.tag = unit.tag
        ctx.mediator.assign_role(tag=unit.tag, role=ctx.build.crew.role)
        ctx.log("PROXY CREW: Z claimed (13th SCV)")

    return hook


def _claim_starting_pair(ctx: "BotContext") -> None:
    """Pull `x` and `y` out of the mineral line, once, on the first frame
    `ctx.state.proxy_crew.x.tag` is still unset - the two starting workers
    closest to `x_tasks[0]`'s own target, so neither has any further to walk
    than it has to."""
    crew = ctx.state.proxy_crew
    if crew.x.tag is not None:
        return

    plan = ctx.build.crew
    reference = plan.x_tasks[0].where(ctx)
    closest = sorted(
        ctx.bot.workers, key=lambda w: cy_distance_to_squared(w.position, reference)
    )
    if len(closest) < 2:
        return  # not enough workers yet; try again next frame

    crew.x.tag, crew.y.tag = closest[0].tag, closest[1].tag
    ctx.mediator.assign_role(tag=closest[0].tag, role=plan.role)
    ctx.mediator.assign_role(tag=closest[1].tag, role=plan.role)
    ctx.log("PROXY CREW: X and Y peel off toward the proxy")


def _path_worker_toward(ctx: "BotContext", worker: "Unit", target: Point2) -> None:
    """Walk a crew SCV toward `target` without entering the building tracker.

    Prefer a already-reserved placement when one exists; otherwise the
    task's base / `near` anchor. Never call `request_building_placement`
    from here - that reserves by default, and asking every wait-frame is
    what exhausted the proxy base's slots. See `_drive_crew_member`.
    """
    point = ctx.mediator.find_path_next_point(
        start=worker.position,
        target=target,
        grid=ctx.mediator.get_ground_grid,
    )
    worker.move(point)


# How close Y (and any non-`reserve_early` task) must get to `task.where`
# before locking a formation slot while still waiting on minerals/tech.
# Wide enough to count as "arrived at the proxy", tight enough that the
# walk from home does not reserve on frame one alongside X.
RESERVE_APPROACH_RADIUS: float = 15.0

# While waiting on minerals/tech, stop move-spamming once this close to
# the reserved tile. Wider than BuildingManager's issue range on purpose:
# pathing often parks an SCV a tile or two off-centre, and fidgeting there
# is what kept X from settling.
HOLD_RADIUS: float = 3.0

# Must stay <= BuildingManager's non-gas build distance (1.0). Entering the
# tracker any farther lets BM `worker.move` every frame and cancel the
# `worker.build` we fire on arrival - X then sits on the tile with money
# until something luckily lands inside 1.0.
BUILD_ISSUE_RADIUS: float = 1.0


def _request_formation_placement(
    ctx: "BotContext", task: "WorkerTask"
) -> Point2 | None:
    """Ask ares for one formation slot at `task.where`, reserving it.

    `find_alternative=False` keeps a miss from spilling onto our own
    expansions - early-reserve callers especially must not wander.
    """
    placement_kwargs = {
        "base_location": task.where(ctx),
        "structure_type": task.structure_id,
        "find_alternative": False,
        "reserve_placement": True,
    }
    if task.closest_to is not None:
        placement_kwargs["closest_to"] = task.closest_to(ctx)
    return ctx.mediator.request_building_placement(**placement_kwargs)


def _ensure_reserved_placement(
    ctx: "BotContext",
    member: "CrewMember",
    task: "WorkerTask",
    worker: "Unit",
    can_pay: bool,
) -> None:
    """Lock a formation slot onto `member.reserved_placement` when allowed.

    X (`reserve_early`): first frame the task is active.
    Y (default): once close to the proxy, or once affordable so a late
    mineral spike still builds without waiting on the approach radius.
    """
    if member.reserved_placement is not None or task.near is not None:
        return
    if task.reserve_early:
        should = True
    elif can_pay:
        should = True
    else:
        should = (
            cy_distance_to_squared(worker.position, task.where(ctx))
            <= RESERVE_APPROACH_RADIUS**2
        )
    if not should:
        return
    placement = _request_formation_placement(ctx, task)
    if placement is not None:
        member.reserved_placement = placement


def _within(worker: "Unit", target: Point2, radius: float) -> bool:
    return cy_distance_to(worker.position, target) <= radius


def _drive_crew_member(
    ctx: "BotContext", member: "CrewMember", tasks: "tuple[WorkerTask, ...]"
) -> None:
    """Advance one crew member by one step.

    Queue into the building tracker only when affordable, tech-ready, and
    inside `BUILD_ISSUE_RADIUS`; otherwise path (or hold inside
    `HOLD_RADIUS`). Reserve minerals in-frame so X and Y do not both queue
    at 150. Formation slots use `reserved_placement` / `reserve_early`
    separately from the tracker. Completion is tracker departure only.
    `assign_role=False` — see `PROXY_CREW_ROLE`.
    """
    if member.tag is None or member.task_index >= len(tasks):
        return

    worker = ctx.bot.unit_tag_dict.get(member.tag)
    if worker is None:
        # Dead. Nothing left to build with it - stop waiting on this slot
        # rather than stalling the rest of the crew's logging/bookkeeping.
        member.task_index = len(tasks)
        member.reserved_placement = None
        return

    if member.queued:
        if member.tag not in ctx.mediator.get_building_tracker_dict:
            member.queued = False
            task = tasks[member.task_index]
            if task.verify is None or task.verify(ctx):
                member.task_index += 1
                member.reserved_placement = None
            else:
                ctx.log(
                    f"PROXY CREW: {task.label or task.structure_id.name.title()} "
                    "did not verify - retrying"
                )
        return

    task = tasks[member.task_index]
    if not task.gate(ctx):
        return

    cost = ctx.bot.calculate_cost(task.structure_id)
    tech_ready = ctx.bot.tech_requirement_progress(task.structure_id) >= 1.0
    can_pay = ctx.bot.minerals >= cost.minerals and ctx.bot.vespene >= cost.vespene

    _ensure_reserved_placement(ctx, member, task, worker, can_pay)

    if task.near is not None:
        placement = near_point(
            ctx, reference=task.near(ctx), structure_type=task.structure_id
        )
    elif member.reserved_placement is not None:
        placement = member.reserved_placement
    else:
        # Ready to pay with no slot yet (e.g. late Y) - reserve now so we
        # have a concrete tile to close on before handing off to BM.
        if tech_ready and can_pay:
            placement = _request_formation_placement(ctx, task)
            if placement is not None:
                member.reserved_placement = placement
        else:
            placement = None

    walk_target = (
        placement
        if placement is not None
        else (task.near(ctx) if task.near is not None else task.where(ctx))
    )

    if not tech_ready or not can_pay:
        # Waiting: hold still once close enough; do not enter the tracker.
        if not _within(worker, walk_target, HOLD_RADIUS):
            _path_worker_toward(ctx, worker, walk_target)
        return

    if placement is None:
        return  # try again next frame

    # Ready, but still outside BuildingManager's issue range: path ourselves.
    # Entering the tracker here is what made BM move-cancel our build.
    if not _within(worker, placement, BUILD_ISSUE_RADIUS):
        _path_worker_toward(ctx, worker, placement)
        return

    # On the tile with money+tech: hand off and fire the build this frame.
    # Reserving the cost stops a later crew member this same frame from
    # also queueing on the same minerals.
    if ctx.mediator.build_with_specific_worker(
        worker=worker,
        structure_type=task.structure_id,
        pos=placement,
        assign_role=False,
    ):
        ctx.bot.minerals -= cost.minerals
        ctx.bot.vespene -= cost.vespene
        member.queued = True
        worker.build(task.structure_id, placement)
        ctx.log(f"PROXY CREW: {task.label or task.structure_id.name.title()} started")


def proxy_crew() -> MacroStep:
    """Drive `ctx.build.crew` (X/Y/Z) through their task lists every frame.

    Belongs in `always` (not `macro_steps`). Do not also list crew structures
    in the opening YAML. Z is claimed via `claim_z_on_first_scv`. Finished
    slots are left alone for `builder_workers_attack` once `_active_crew_tags`
    drops them.
    """

    def step(ctx: "BotContext"):
        plan = ctx.build.crew
        if plan is None:
            return None
        _claim_starting_pair(ctx)
        crew = ctx.state.proxy_crew
        _drive_crew_member(ctx, crew.x, plan.x_tasks)
        _drive_crew_member(ctx, crew.y, plan.y_tasks)
        _drive_crew_member(ctx, crew.z, plan.z_tasks)
        return None

    return step


def continuous_main_depots(gate: Gate = _always) -> MacroStep:
    """Pull one mining SCV and keep laying Supply Depots at our main.

    Belongs in `always`: once `gate` opens it claims a single `GATHERING`
    worker into `SUPPLY_BUILDER_ROLE` and drives it the same way
    `proxy_crew` drives a task - `build_with_specific_worker` with
    `assign_role=False`, waiting on tracker membership between Depots.
    Stops issuing new Depots once supply is capped at 200.

    `gate` only gates the *claim*. Once a builder is tagged, this step
    keeps driving it even if the gate later fails (e.g. minerals dip back
    under the gate threshold) - otherwise a spent Depot would park the
    SCV forever. Deliberately not `AutoSupply`: that races the opening
    crew for the first Depot. Four Rax opens this on
    `minerals_at_least(DEPOT_MINERAL_TRIGGER)` (see
    `bot.builds.terran.four_rax_proxy`).
    """

    def step(ctx: "BotContext"):
        if ctx.bot.supply_cap >= 200:
            return None

        tag = ctx.state.supply_depot_builder_tag
        worker = ctx.bot.unit_tag_dict.get(tag) if tag is not None else None
        if worker is None:
            if not gate(ctx):
                return None
            worker = ctx.mediator.select_worker(
                target_position=ctx.production_location, force_close=True
            )
            if worker is None:
                return None
            ctx.mediator.assign_role(tag=worker.tag, role=SUPPLY_BUILDER_ROLE)
            ctx.state.supply_depot_builder_tag = worker.tag
            ctx.state.supply_depot_queued = False
            ctx.log("DEPOTS: SCV claimed for continuous Depots")

        if ctx.state.supply_depot_queued:
            if worker.tag in ctx.mediator.get_building_tracker_dict:
                return None
            ctx.state.supply_depot_queued = False

        cost = ctx.bot.calculate_cost(UnitTypeId.SUPPLYDEPOT)
        if ctx.bot.minerals < cost.minerals:
            return None

        placement = ctx.mediator.request_building_placement(
            base_location=ctx.production_location,
            structure_type=UnitTypeId.SUPPLYDEPOT,
        )
        if placement is None:
            return None

        if ctx.mediator.build_with_specific_worker(
            worker=worker,
            structure_type=UnitTypeId.SUPPLYDEPOT,
            pos=placement,
            assign_role=False,
        ):
            ctx.bot.minerals -= cost.minerals
            ctx.state.supply_depot_queued = True
            ctx.log("DEPOTS: Supply Depot started")
        return None

    return step
