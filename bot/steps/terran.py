"""Terran-specific macro steps.

Factories here (not in `steps/common.py`) for anything that names a
Terran-only structure or unit, mirroring how `steps/zerg.py` owns queens and
hatcheries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ares.behaviors.macro import BuildStructure
from cython_extensions import cy_distance_to_squared
from sc2.ids.unit_typeid import UnitTypeId

from bot.builds.definition import _always
from bot.core.types import Gate, MacroStep, PointLocator, UnitCreatedHook

if TYPE_CHECKING:
    from sc2.unit import Unit

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


def _drive_crew_member(ctx: "BotContext", member: "CrewMember", tasks: "tuple") -> None:
    """Work one crew member through its task list by one step.

    A task is issued once - `request_building_placement` then
    `build_with_specific_worker(..., assign_role=False)` - then left alone
    until the worker's tag drops out of `mediator.get_building_tracker_dict`,
    which ares itself removes it from the instant the structure completes
    (`BuildingManager._handle_construction_orders`). That tracker-membership
    check is the entire completion signal - nothing here infers it from
    structure counts, which would misattribute one worker's completion to
    another's identical structure type (Barracks A and B finish within
    moments of each other, both via this same mechanism).

    `assign_role=False` is load-bearing: see `bot.consts.PROXY_CREW_ROLE` for
    why the crew's own role must be the only thing that ever touches these
    workers' roles between claim and hand-off.

    A task's `closest_to`, when set, is forwarded to `request_building_
    placement` as-is - it only orders the precalculated spots `where`
    already resolved to, e.g. biasing a home Depot toward the main ramp.
    """
    if member.tag is None or member.task_index >= len(tasks):
        return

    worker = ctx.bot.unit_tag_dict.get(member.tag)
    if worker is None:
        # Dead. Nothing left to build with it - stop waiting on this slot
        # rather than stalling the rest of the crew's logging/bookkeeping.
        member.task_index = len(tasks)
        return

    if member.queued:
        if member.tag not in ctx.mediator.get_building_tracker_dict:
            member.task_index += 1
            member.queued = False
        return

    task = tasks[member.task_index]
    if not task.gate(ctx):
        return

    placement_kwargs = {
        "base_location": task.where(ctx),
        "structure_type": task.structure_id,
    }
    if task.closest_to is not None:
        placement_kwargs["closest_to"] = task.closest_to(ctx)
    placement = ctx.mediator.request_building_placement(**placement_kwargs)
    if placement is None:
        return  # try again next frame

    if ctx.mediator.build_with_specific_worker(
        worker=worker,
        structure_type=task.structure_id,
        pos=placement,
        assign_role=False,
    ):
        member.queued = True
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

    Once a slot's task list is exhausted this step stops touching it -
    `combat.builder_workers_attack`'s existing proximity-based claiming
    (identity-blind; it has no idea this was "x") picks it up from there once
    its own `claim_gate` passes.
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
