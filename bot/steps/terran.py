"""Terran-specific macro steps.

Factories here (not in `steps/common.py`) for anything that names a
Terran-only structure or unit, mirroring how `steps/zerg.py` owns queens and
hatcheries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ares.behaviors.macro import BuildStructure
from cython_extensions import cy_distance_to, cy_distance_to_squared
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.builds.definition import _always
from bot.consts import SUPPLY_BUILDER_ROLE
from bot.core.types import Gate, MacroStep, PointLocator, UnitCreatedHook
from bot.routines.placement import near_point

if TYPE_CHECKING:
    from sc2.unit import Unit

    from bot.builds.definition import WorkerTask
    from bot.core.context import BotContext
    from bot.core.state import CrewMember


def proxy_barracks(
    to_count: int,
    where: PointLocator,
    gate: Gate = _always,
    max_on_route: int = 1,
) -> MacroStep:
    """Keep `to_count` Barracks standing at `where`, once `gate` passes.

    This is a plain `BuildStructure` pointed at somebody else's base location
    rather than our own, and that is the whole trick: ares'
    `_solve_terran_building_formation` walks **every** entry in
    `ai.expansion_locations_list` when it precomputes placements, enemy
    expansions included, so `request_building_placement` solves a real,
    legal Terran placement at the enemy's third exactly as happily as at our
    own main. None of the roll-your-own placement search that
    `behaviors/zerg/build_macro_hatch.py` needed applies here - that was
    forced by `_solve_zerg_building_formation` being an unimplemented stub
    (ARCHITECTURE.md gotchas 1 and 10), which is a Zerg-only problem.

    Which SCV goes is left to ares, and that is deliberate rather than lazy:
    `BuildStructure` selects via `mediator.select_worker(force_close=True)`,
    the closest *gathering* worker to the placement. Early on every worker is
    at home, so that means "pull one off the mineral line"; once a builder is
    standing at the proxy having just finished a Barracks, it is by a wide
    margin the closest gathering worker to the next one, so the follow-up
    Barracks falls to it with no tag bookkeeping at all. Pinning specific
    SCVs by tag would encode the same outcome more brittlely - a dead builder
    would strand the step, where "closest worker" simply picks someone else.

    Attributes:
        to_count: Total Barracks to have standing (ready or building).
        where: Resolves the proxy base location, fresh each frame.
        gate: Extra condition; the caller stages the count with it.
        max_on_route: Workers allowed to be walking there at once.
    """

    def step(ctx: "BotContext"):
        if not gate(ctx):
            return None
        return BuildStructure(
            base_location=where(ctx),
            structure_id=UnitTypeId.BARRACKS,
            to_count=to_count,
            max_on_route=max_on_route,
        )

    return step


def claim_z_on_first_scv() -> UnitCreatedHook:
    """Claim the first SCV beyond the starting 12 as `z` in
    `ctx.state.proxy_crew` - a build's `on_unit_created` for a
    `ProxyCrewPlan` whose `z_tasks` should run on "the 13th SCV".

    Guards on `len(ctx.bot.workers) >= 13` at the moment of the event, not
    merely "the first SCV-creation event seen". An earlier version trusted
    ares/python-sc2 to never fire `on_unit_created` for a game's starting
    units - the same guarantee `roles.assign_on_created`'s scout assignment
    leans on for the second Overlord - and claimed whichever SCV triggered
    the very first such event. In an actual game that claimed one of the
    starting 12: a third worker was seen peeling off the mineral line at
    game start alongside X and Y, instead of only the two of them, meaning
    the event fired for a starting SCV too. Counting workers directly
    sidesteps the assumption rather than depending on it - regardless of
    what fires this event or when, only a call that lands once a 13th SCV
    genuinely exists gets to claim one.
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


def _drive_crew_member(ctx: "BotContext", member: "CrewMember", tasks: "tuple") -> None:
    """Work one crew member through its task list by one step.

    A task enters ares' building tracker once - placement then
    `build_with_specific_worker(..., assign_role=False)` - and only when
    we can already afford it, its tech requirement is met, *and* the
    worker is inside `BUILD_ISSUE_RADIUS`. Until then the worker is path'd
    toward the placement by hand. That matters for X and Y's opening
    Barracks: putting both in the tracker on frame one left
    BuildingManager to issue both builds the same frame minerals hit 300,
    so neither started at 150. Reserving the cost on the bot's mineral
    count when we queue (mirroring `Unit.build`'s own subtract) means a
    second crew member driven later this same frame sees the remainder and
    keeps pathing instead of also queueing.

    Formation slots are reserved separately from the building tracker:
    `WorkerTask.reserve_early` (Barracks A) locks a slot immediately so Y
    cannot take it; Y paths to the proxy first and only then reserves
    (see `_ensure_reserved_placement`). Reusing `member.reserved_placement`
    at build time avoids a second `request_building_placement` that would
    abandon the first reserved slot.

    Waiting on minerals/tech: hold still inside `HOLD_RADIUS`, otherwise
    path. Ready to pay: keep pathing ourselves until inside
    `BUILD_ISSUE_RADIUS`, then enter the tracker and fire `worker.build`
    the same frame. Handing off to BuildingManager any earlier lets its
    move-to-1.0 loop cancel the build order every frame.

    Once queued, the worker is left alone until its tag drops out of
    `mediator.get_building_tracker_dict`, which ares itself removes it from
    the instant the structure completes (`BuildingManager.
    _handle_construction_orders`). That tracker-membership check is the
    entire completion signal - nothing here infers it from structure counts,
    which would misattribute one worker's completion to another's identical
    structure type (Barracks A and B finish within moments of each other,
    both via this same mechanism).

    `assign_role=False` is load-bearing: see `bot.consts.PROXY_CREW_ROLE` for
    why the crew's own role must be the only thing that ever touches these
    workers' roles between claim and hand-off.

    A task's `closest_to`, when set, is forwarded to `request_building_
    placement` as-is - it only orders the precalculated spots `where`
    already resolved to, e.g. biasing a home Depot toward the main ramp.

    A task's `near`, when set, skips `where`'s formation lookup in favor of
    `routines.placement.near_point` - see `WorkerTask.near`.

    A task's optional `verify` runs once tracker departure would otherwise
    mark it done, and must pass before `task_index` actually advances - see
    `WorkerTask.verify`'s own docstring for why. A failed `verify` clears
    `queued` and leaves `task_index` alone, so the very next call re-issues
    the identical task.
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
    """Drive the three hand-walked SCVs declared by `ctx.build.crew`
    (`bot.builds.definition.ProxyCrewPlan`) through their own task lists.

    Belongs in `always`, not `macro_steps`: `x` and `y` peel off on the very
    first frame of the game, well before `macro_steps` starts running (it
    waits for `build_completed` - see `macro_engine.py`). A build using this
    plan should not also list any of the plan's own structures in
    `OpeningBuildOrder`/`macro_steps` - ares' generic `BuildStructure`/
    `select_worker` machinery knows nothing about this plan and would happily
    build them a second time with a worker pulled fresh off the minerals.

    `z` is claimed separately, by `on_unit_created` (see
    `claim_z_on_first_scv`) - unlike `x`/`y` it does not exist yet at the
    frame this step first runs.

    A task's `gate` is checked only once its task list actually reaches it,
    not the moment the plan is declared - so e.g. a Barracks gated on Marine
    production having started simply leaves its worker idle wherever its
    previous task finished, for as long as the gate fails, with nothing else
    for it to do since it was never in `UnitRole.GATHERING` to begin with.

    Affordability, tech, and proximity are checked before a task enters
    the building tracker: until all three pass, the worker is path'd by
    hand (holding still only while waiting on money/tech inside
    `HOLD_RADIUS`). That keeps X and Y from both sitting in the tracker
    as pending Barracks that BuildingManager only issues together once
    300 minerals are banked - and keeps BM from move-cancelling a build
    issued outside its 1.0 radius. With 150 minerals, the first crew
    member driven this frame that is already on its tile queues and
    reserves the cost; the second keeps walking.

    Once a slot's task list is exhausted this step stops touching it -
    `combat.builder_workers_attack`'s proximity-based claiming (identity-
    blind; it has no idea this was "x") picks it up from there once its own
    `claim_gate` passes *and* the worker is no longer listed as an active
    crew member (see `_active_crew_tags`). Claiming earlier while tasks
    remain is what made Y oscillate between Depot pathing and attack orders.
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
    under 500) - otherwise a spent Depot would park the SCV forever.
    Deliberately not `AutoSupply`: that races the opening crew for the
    first Depot. Four Rax opens this on `minerals_at_least(500)`.
    """

    def step(ctx: "BotContext"):
        if ctx.bot.supply_cap >= 200:
            return None

        tag = ctx.state.cleanup_depot_builder_tag
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
            ctx.state.cleanup_depot_builder_tag = worker.tag
            ctx.state.cleanup_depot_queued = False
            ctx.log("CLEANUP: SCV claimed for continuous Depots")

        if ctx.state.cleanup_depot_queued:
            if worker.tag in ctx.mediator.get_building_tracker_dict:
                return None
            ctx.state.cleanup_depot_queued = False

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
            ctx.state.cleanup_depot_queued = True
            ctx.log("CLEANUP: Supply Depot started")
        return None

    return step
