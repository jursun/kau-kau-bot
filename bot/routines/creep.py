"""Creep spread: connect own bases first, then keep expanding the edge."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ares.behaviors.combat.individual import KeepUnitSafe
from ares.consts import UnitRole
from cython_extensions import cy_distance_to_squared
from cython_extensions.general_utils import cy_has_creep
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from bot.core.types import CombatRoutine

# Sticky next-tumor tile per Queen tag — stops highway spread from
# re-issuing move/cast to a new edge every frame (visual thrash).
_QUEEN_TUMOR_STICKY: dict[int, Point2] = {}

if TYPE_CHECKING:
    from bot.core.context import BotContext


def _own_bases_highway(ctx: "BotContext") -> list[Point2]:
    """Main -> natural -> other ready townhalls (nearest-next along the chain).

    Order is what Jason wants tumors to stitch: a creep highway between
    bases, not a straight walk to the enemy natural.
    """
    bot = ctx.bot
    main = bot.start_location
    ordered: list[Point2] = [main]
    try:
        nat = ctx.mediator.get_own_nat
    except Exception:  # noqa: BLE001 - mediator may not be ready in tests
        nat = None
    if nat is not None and cy_distance_to_squared(nat, main) > 1.0:
        ordered.append(nat)

    remaining: list[Point2] = []
    for th in bot.townhalls.ready:
        pos = th.position
        if any(cy_distance_to_squared(pos, existing) < 25.0 for existing in ordered):
            continue
        remaining.append(pos)

    while remaining:
        anchor = ordered[-1]
        nxt = min(remaining, key=lambda p: cy_distance_to_squared(p, anchor))
        remaining.remove(nxt)
        ordered.append(nxt)
    return ordered


def _creep_highway_target(ctx: "BotContext", from_pos: Point2) -> Point2:
    """Next own base that still needs creep, else the far end of the chain.

    A base "needs creep" when its townhall tile is not on creep - that is
    exactly the gap Hosts/Roaches hit when moving between bases.
    """
    bases = _own_bases_highway(ctx)
    creep_grid = ctx.mediator.get_creep_grid
    for base in bases[1:]:
        if not cy_has_creep(creep_grid, base):
            return base
    if len(bases) > 1:
        return bases[-1]
    return ctx.bot.enemy_start_locations[0]


def _place_tumor_toward(
    ctx: "BotContext", unit, target: Point2, *, queen: bool
) -> bool:
    """Cast or move toward the next highway tumor spot. Returns True if acted."""
    mediator = ctx.mediator
    ability = (
        AbilityId.BUILD_CREEPTUMOR_QUEEN
        if queen
        else AbilityId.BUILD_CREEPTUMOR_TUMOR
    )
    cast_ability = (
        AbilityId.BUILD_CREEPTUMOR_QUEEN if queen else AbilityId.BUILD_CREEPTUMOR
    )

    if queen:
        spreading = unit.is_using_ability(AbilityId.BUILD_CREEPTUMOR)
        grid = mediator.get_ground_grid
        if spreading and KeepUnitSafe(unit, grid).execute(
            ctx.bot, ctx.bot.config, mediator
        ):
            return True
        if spreading:
            return True
        ability_available = ability in unit.abilities
        if not ability_available:
            spot = mediator.get_next_tumor_on_path(
                grid=grid,
                from_pos=unit.position,
                to_pos=target,
                find_alternative=True,
            )
            if spot and cy_distance_to_squared(unit.position, spot) > 9.0:
                unit.move(spot)
                return True
            return True
    else:
        if not mediator.should_calculate_tumor_spread:
            return False
        if ability not in unit.abilities:
            return False
        grid = mediator.get_ground_grid

    sticky = _QUEEN_TUMOR_STICKY.get(unit.tag) if queen else None
    if sticky is not None and cy_has_creep(mediator.get_creep_grid, sticky):
        spot = sticky
    else:
        if queen:
            _QUEEN_TUMOR_STICKY.pop(unit.tag, None)
        spot = mediator.get_next_tumor_on_path(
            grid=grid,
            from_pos=unit.position,
            to_pos=target,
            find_alternative=True,
            min_separation=5.0 if queen else 3.0,
        )
        if spot is None:
            spot = mediator.find_nearby_creep_edge_position(
                position=unit.position,
                search_radius=12.0 if queen else 10.2,
                closest_valid=False,
                spread_dist=3.0 if queen else 1.0,
                unit_tag=unit.tag if queen else None,
            )
        if spot is None:
            return False
        if queen:
            _QUEEN_TUMOR_STICKY[unit.tag] = spot

    if queen and cy_distance_to_squared(unit.position, spot) > 25.0:
        target_order = unit.order_target
        already = (
            isinstance(target_order, Point2)
            and cy_distance_to_squared(target_order, spot) < 4.0
        )
        if not already:
            unit.move(spot)
        return True
    unit(cast_ability, spot)
    if queen:
        # Cast issued — drop sticky so the next plant can pick a new tile.
        _QUEEN_TUMOR_STICKY.pop(unit.tag, None)
    return True


def spread_creep() -> CombatRoutine:
    """Dedicate one Queen, beyond one per base, to spreading creep.

    After Macro Zerg's opening main-plateau claim finishes, the creep Queen
    plants along the own-base highway (main -> nat -> later hatches) via
    `get_next_tumor_on_path`, instead of ares `QueenSpreadCreep` which walks
    toward `get_enemy_nat` while map coverage is low.

    Skips Queens claimed by Macro Zerg opening tumors (`main_queen_tag` /
    `natural_queen_tag`): those claims drive placement themselves with a
    sticky single-command path so inject/highway cannot thrash them.
    """

    def routine(ctx: "BotContext") -> None:
        creep_queens = ctx.mediator.get_units_from_role(
            role=UnitRole.QUEEN_CREEP, unit_type=UnitTypeId.QUEEN
        )
        if not creep_queens:
            injectors = ctx.mediator.get_units_from_role(
                role=UnitRole.QUEEN_INJECT, unit_type=UnitTypeId.QUEEN
            )
            if len(injectors) <= ctx.base_count:
                return
            newest = max(injectors, key=lambda q: q.tag)
            ctx.mediator.assign_role(tag=newest.tag, role=UnitRole.QUEEN_CREEP)
            return

        # Opening tumor claims drive their Queen themselves (sticky single
        # command path). Do not also highway-drive them — that was the
        # inject/tumor thrash: two routines fighting over the same Queen.
        reserved: set[int] = set()
        if (
            ctx.state.main_queen_tag is not None
            and not ctx.state.main_queen_tumor_done
        ):
            reserved.add(ctx.state.main_queen_tag)
        if (
            ctx.state.natural_queen_tag is not None
            and not ctx.state.natural_queen_tumor_done
        ):
            reserved.add(ctx.state.natural_queen_tag)

        for queen in creep_queens:
            if queen.tag in reserved:
                continue
            target = _creep_highway_target(ctx, queen.position)
            _place_tumor_toward(ctx, queen, target, queen=True)
            return

    return routine


def spread_tumors() -> CombatRoutine:
    """Burrowed tumors extend the own-base creep highway.

    Each tumor paths its next plant toward the next base that still needs
    creep (then the far end of the chain). This replaces the old
    `TumorSpreadCreep(..., enemy_start)` registration - that behavior's
    `target` is unused and its edge search does not prefer inter-base links.
    """

    def routine(ctx: "BotContext") -> None:
        tumors = ctx.mediator.get_own_structures_dict[UnitTypeId.CREEPTUMORBURROWED]
        if not tumors:
            return

        for tumor in tumors:
            target = _creep_highway_target(ctx, tumor.position)
            _place_tumor_toward(ctx, tumor, target, queen=False)

    return routine
