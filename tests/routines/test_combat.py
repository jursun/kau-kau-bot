"""Regression tests for wave shaping and the muster-before-attack behavior.

`release_waves` and `attack_squads` are the two hardest-to-eyeball routines in
`bot/routines/combat.py`: sizing math and squad/rally-point interaction are
each easy to get subtly wrong without a game to watch. These exercise the
real `UnitSquad`, `CombatManeuver`/`AMoveGroup` and `cy_distance_to_squared`
from ares/cython_extensions; only the AresBot/ManagerMediator boundary is
mocked.

Runs under pytest, or standalone with no test dependency:

    python -m tests.routines.test_combat
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from ares.behaviors.combat import CombatManeuver
from ares.behaviors.combat.group import AMoveGroup, KeepGroupSafe, StutterGroupForward
from ares.behaviors.combat.individual import (
    AMove,
    AttackTarget,
    KeepUnitSafe,
    MoveToSafeTarget,
    ShootTargetInRange,
    UseAbility,
)
from ares.consts import UnitRole
from ares.managers.squad_manager import UnitSquad
from cython_extensions import cy_distance_to
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2

from bot.core.context import BotContext
from bot.core.state import RunState
from bot.routines import combat, targeting


def _unit(tag: int, position: Point2 = Point2((0.0, 0.0))) -> MagicMock:
    unit = MagicMock()
    unit.tag = tag
    unit.position = position
    unit.is_structure = False
    return unit


def _ctx(wave1_min: int = 6, wave_growth: float = 1.25) -> BotContext:
    build = MagicMock()
    build.army.types = frozenset()
    build.combat.wave1_min = wave1_min
    build.combat.wave_growth = wave_growth
    build.combat.wave_gate = lambda _ctx: True
    build.combat.attack_objective = None
    ctx = BotContext(bot=MagicMock(), build=build, state=RunState())
    # Comfortably under `MAX_SUPPLY` and nothing pending by default, so
    # `_maxed_and_ready` is False unless a test deliberately raises these -
    # `already_pending` returning 0 for any argument means "nothing training".
    ctx.bot.supply_used = 100.0
    ctx.bot.time = 0.0
    ctx.bot.already_pending.return_value = 0
    ctx.bot.calculate_supply_cost.return_value = 1.0
    # Influence retreat reads these every attack_squads frame.
    ctx.mediator.get_cached_enemy_army = []
    ctx.mediator.get_ground_grid = object()
    return ctx


def test_release_waves_grows_by_25_percent() -> None:
    ctx = _ctx(wave1_min=6, wave_growth=1.25)
    routine = combat.release_waves()

    wave1 = [_unit(i) for i in range(6)]
    ctx.mediator.get_units_from_role.return_value = wave1
    routine(ctx)
    assert ctx.state.wave_number == 1
    assert ctx.state.next_wave_size == 8, ctx.state.next_wave_size  # ceil(6*1.25)

    wave2 = [_unit(100 + i) for i in range(8)]
    ctx.mediator.get_units_from_role.return_value = wave2
    routine(ctx)
    assert ctx.state.wave_number == 2
    assert ctx.state.next_wave_size == 10, ctx.state.next_wave_size  # ceil(8*1.25)


def test_release_waves_marks_promoted_units_as_mustering() -> None:
    ctx = _ctx()
    defenders = [_unit(i) for i in range(6)]
    ctx.mediator.get_units_from_role.return_value = defenders

    combat.release_waves()(ctx)

    assert ctx.state.mustering_tags == {u.tag for u in defenders}


def _amove_target(ctx: BotContext, squad: UnitSquad) -> Point2:
    """Run attack_squads for one squad and return the advance target.

    With no enemy army nearby, attack_squads scatters per-unit AMove
    (structure cleanup) instead of one AMoveGroup — either shape is fine
    for destination checks as long as every unit heads to the same patched
    attack point.
    """
    ctx.mediator.get_units_in_range.return_value = [[]]  # no close enemies
    ctx.mediator.get_squads.return_value = [squad]

    combat.attack_squads()(ctx)

    registered = [
        c.args[0] for c in ctx.bot.register_behavior.call_args_list
    ]
    group = [
        b
        for plan in registered
        for b in plan.micros
        if isinstance(b, AMoveGroup)
    ]
    if group:
        assert len(group) == 1, "expected exactly one AMoveGroup"
        return group[0].target
    singles = [
        b for plan in registered for b in plan.micros if isinstance(b, AMove)
    ]
    assert singles, "expected AMoveGroup or per-unit AMove"
    targets = {b.target for b in singles}
    assert len(targets) == 1, "scatter should share the patched attack point"
    return singles[0].target


def _squad(units: list[MagicMock]) -> UnitSquad:
    return UnitSquad(
        main_squad=True,
        squad_id="s1",
        squad_position=Point2(
            (
                sum(u.position.x for u in units) / len(units),
                sum(u.position.y for u in units) / len(units),
            )
        ),
        squad_units=units,
        tags={u.tag for u in units},
    )


def _patch_targeting(rally: Point2, attack: Point2):
    """attack_squads calls targeting.rally_point / squad_destination
    (which falls through to attack_target when attack_objective is None)."""
    original = (targeting.rally_point, targeting.attack_target)
    targeting.rally_point = lambda _ctx: rally
    targeting.attack_target = lambda _ctx, _pos: attack
    return original


def _restore_targeting(original) -> None:
    targeting.rally_point, targeting.attack_target = original


def test_mustering_squad_moves_to_rally_not_the_attack_target() -> None:
    rally = Point2((50.0, 50.0))
    attack = Point2((999.0, 999.0))
    original = _patch_targeting(rally, attack)
    try:
        ctx = _ctx()
        far_units = [_unit(1, Point2((10.0, 10.0))), _unit(2, Point2((12.0, 10.0)))]
        ctx.state.mustering_tags = {1, 2}
        ctx.mediator.get_units_from_role.return_value = far_units

        target = _amove_target(ctx, _squad(far_units))

        assert (target.x, target.y) == (rally.x, rally.y)
        assert ctx.state.mustering_tags == {1, 2}, "should not release while still far"
    finally:
        _restore_targeting(original)


def test_squad_gathered_at_rally_is_released_to_attack() -> None:
    rally = Point2((50.0, 50.0))
    attack = Point2((999.0, 999.0))
    original = _patch_targeting(rally, attack)
    try:
        ctx = _ctx()
        close_units = [_unit(3, Point2((49.0, 50.0))), _unit(4, Point2((51.0, 50.0)))]
        ctx.state.mustering_tags = {3, 4}
        ctx.mediator.get_units_from_role.return_value = close_units

        target = _amove_target(ctx, _squad(close_units))

        assert (target.x, target.y) == (attack.x, attack.y)
        assert ctx.state.mustering_tags == set(), "should release once gathered"
    finally:
        _restore_targeting(original)


def test_already_released_squad_ignores_rally_point() -> None:
    rally = Point2((50.0, 50.0))
    attack = Point2((999.0, 999.0))
    original = _patch_targeting(rally, attack)
    try:
        ctx = _ctx()
        units = [_unit(5, Point2((10.0, 10.0)))]  # far from rally, never mustering
        ctx.mediator.get_units_from_role.return_value = units

        target = _amove_target(ctx, _squad(units))

        assert (target.x, target.y) == (attack.x, attack.y)
    finally:
        _restore_targeting(original)


# ── _enemies_near: enemy units outrank enemy structures ─────────────────────


def test_enemies_near_prefers_units_over_structures() -> None:
    ctx = _ctx()
    structure = _unit(90)
    structure.is_structure = True
    unit = _unit(91)
    ctx.mediator.get_units_in_range.return_value = [[structure, unit]]

    result = combat._enemies_near(ctx, Point2((0.0, 0.0)), 10.0)

    assert result == [unit], "a unit in range should crowd out a structure"


def test_enemies_near_falls_back_to_a_structure_with_nothing_else_around() -> None:
    ctx = _ctx()
    structure = _unit(90)
    structure.is_structure = True
    ctx.mediator.get_units_in_range.return_value = [[structure]]

    result = combat._enemies_near(ctx, Point2((0.0, 0.0)), 10.0)

    assert result == [structure], "a structure is still a valid target alone"


def test_enemies_near_ignores_eggs_and_larva() -> None:
    """Zerg's production units are never worth attacking - not even as a
    last resort with nothing else in range, unlike a structure."""
    ctx = _ctx()
    egg = _unit(90)
    egg.type_id = UnitTypeId.EGG
    larva = _unit(91)
    larva.type_id = UnitTypeId.LARVA
    ctx.mediator.get_units_in_range.return_value = [[egg, larva]]

    result = combat._enemies_near(ctx, Point2((0.0, 0.0)), 10.0)

    assert result == [], "no worthwhile target - should not fall back to Egg/Larva"


def test_enemies_near_still_prefers_a_real_unit_over_an_egg() -> None:
    ctx = _ctx()
    egg = _unit(90)
    egg.type_id = UnitTypeId.EGG
    zergling = _unit(91)
    ctx.mediator.get_units_in_range.return_value = [[egg, zergling]]

    result = combat._enemies_near(ctx, Point2((0.0, 0.0)), 10.0)

    assert result == [zergling]


# ── Attack squads: stutter when ahead, kite when not ────────────────────────


def test_squad_stutters_when_outnumbered_without_min_engage_range() -> None:
    """Builds that leave `min_engage_range` unset (Zerg openings) keep
    stuttering even when badly outnumbered - kiting is opt-in."""
    rally = Point2((50.0, 50.0))
    attack = Point2((999.0, 999.0))
    original = _patch_targeting(rally, attack)
    try:
        ctx = _ctx()
        units = [_unit(1, Point2((10.0, 10.0)))]  # 1 unit
        enemies = [
            _unit(90, Point2((11.0, 10.0))),
            _unit(91, Point2((11.0, 11.0))),
            _unit(92, Point2((11.0, 12.0))),
            _unit(93, Point2((11.0, 13.0))),
        ]  # badly outnumbered
        ctx.mediator.get_cached_enemy_army = enemies
        ctx.mediator.get_units_in_range.return_value = [enemies]
        ctx.mediator.get_squads.return_value = [_squad(units)]

        combat.attack_squads()(ctx)

        registered = ctx.bot.register_behavior.call_args.args[0]
        assert isinstance(registered.micros[0], KeepGroupSafe)
        amoves = [b for b in registered.micros if isinstance(b, AMoveGroup)]
        assert amoves[0].target == attack

        stutters = [b for b in registered.micros if isinstance(b, StutterGroupForward)]
        assert len(stutters) == 1, "should stutter-forward and trade, not retreat"
        assert stutters[0].enemies == enemies
    finally:
        _restore_targeting(original)


# ── min_engage_range kiting: _kite_maneuver and attack_squads' dispatch ─────


def _patch_in_range(mapping: dict):
    """`combat.cy_in_attack_range` needs real weapon-range game data - not
    available on a MagicMock unit - so it's monkeypatched here the same way
    `_patch_targeting` swaps out the module-level targeting functions.
    `mapping` is unit tag -> the enemies that unit should read as in range.
    """
    original = combat.cy_in_attack_range
    combat.cy_in_attack_range = lambda unit, enemies, *a, **k: mapping.get(unit.tag, [])
    return original


def _restore_in_range(original) -> None:
    combat.cy_in_attack_range = original


def test_kite_maneuver_retreats_from_an_enemy_inside_min_engage_range() -> None:
    marine = _unit(1, Point2((100.0, 100.0)))
    close_enemy = _unit(90, Point2((101.0, 100.0)))  # 1 tile away
    original = _patch_in_range({1: [close_enemy]})
    try:
        maneuver = combat._kite_maneuver(
            marine, [close_enemy], 3.0, Point2((999.0, 999.0))
        )
    finally:
        _restore_in_range(original)

    moves = [m for m in maneuver.micros if isinstance(m, combat._Move)]
    assert len(moves) == 1, "should back away, not shoot or advance"
    # Directly away from the enemy, ending exactly min_engage_range from it.
    assert round(cy_distance_to(moves[0].target, close_enemy.position), 3) == 3.0


def test_kite_maneuver_hysteresis_keeps_peeling_between_engage_and_resume() -> None:
    """Once peeling, stay peeled until enemies clear resume_range - stops
    Move↔Shoot thrash at the min_engage boundary."""
    marine = _unit(1, Point2((100.0, 100.0)))
    # 3.5 tiles: outside min_engage (3) but inside default resume (4).
    mid_enemy = _unit(90, Point2((103.5, 100.0)))
    peeling = {1}
    original = _patch_in_range({1: [mid_enemy]})
    try:
        maneuver = combat._kite_maneuver(
            marine,
            [mid_enemy],
            3.0,
            Point2((999.0, 999.0)),
            peeling=peeling,
        )
    finally:
        _restore_in_range(original)

    moves = [m for m in maneuver.micros if isinstance(m, combat._Move)]
    assert len(moves) == 1, "still peeling: must keep backing off"
    assert 1 in peeling
    # Past the unit, not pinned at min_engage (which would walk forward).
    assert cy_distance_to(moves[0].target, mid_enemy.position) > 3.5


def test_kite_maneuver_hysteresis_stops_peeling_past_resume_range() -> None:
    marine = _unit(1, Point2((100.0, 100.0)))
    far_enemy = _unit(90, Point2((104.5, 100.0)))  # past default resume 4.0
    peeling = {1}
    original = _patch_in_range({1: [far_enemy]})
    try:
        maneuver = combat._kite_maneuver(
            marine,
            [far_enemy],
            3.0,
            Point2((999.0, 999.0)),
            peeling=peeling,
        )
    finally:
        _restore_in_range(original)

    shoots = [m for m in maneuver.micros if isinstance(m, ShootTargetInRange)]
    assert len(shoots) == 1
    assert 1 not in peeling


def test_kite_maneuver_shoots_when_in_range_but_not_crowding() -> None:
    marine = _unit(1, Point2((100.0, 100.0)))
    far_enemy = _unit(90, Point2((104.0, 100.0)))  # in range, outside min_engage_range
    original = _patch_in_range({1: [far_enemy]})
    try:
        maneuver = combat._kite_maneuver(
            marine, [far_enemy], 3.0, Point2((999.0, 999.0))
        )
    finally:
        _restore_in_range(original)

    shoots = [m for m in maneuver.micros if isinstance(m, ShootTargetInRange)]
    assert len(shoots) == 1, "in range and clear of the min-range buffer: shoot"
    assert shoots[0].targets == [far_enemy]


def test_kite_maneuver_advances_when_nothing_is_in_range() -> None:
    marine = _unit(1, Point2((100.0, 100.0)))
    distant_enemy = _unit(90, Point2((200.0, 200.0)))
    original = _patch_in_range({1: []})
    try:
        maneuver = combat._kite_maneuver(
            marine, [distant_enemy], 3.0, Point2((999.0, 999.0))
        )
    finally:
        _restore_in_range(original)

    amoves = [m for m in maneuver.micros if isinstance(m, AMove)]
    assert len(amoves) == 1
    assert amoves[0].target == Point2((999.0, 999.0))


def test_attack_squads_kites_when_outnumbered_and_min_engage_range_is_set() -> None:
    """With `min_engage_range` set, equal-or-larger enemy force switches off
    group stutter and registers one `_kite_maneuver` per unit."""
    rally = Point2((50.0, 50.0))
    attack = Point2((999.0, 999.0))
    original_targeting = _patch_targeting(rally, attack)
    original_in_range = _patch_in_range({})  # nothing in range for any unit
    try:
        ctx = _ctx()
        units = [_unit(1, Point2((10.0, 10.0))), _unit(2, Point2((12.0, 10.0)))]
        # Three enemies > two of ours (each unit costs 1 supply in the fake).
        enemies = [
            _unit(90, Point2((11.0, 10.0))),
            _unit(91, Point2((11.0, 11.0))),
            _unit(92, Point2((11.0, 12.0))),
        ]
        ctx.mediator.get_cached_enemy_army = enemies
        ctx.mediator.get_units_in_range.return_value = [enemies]
        ctx.mediator.get_squads.return_value = [_squad(units)]

        combat.attack_squads(min_engage_range=3.0)(ctx)

        assert ctx.bot.register_behavior.call_count == 2, "one maneuver per unit"
        registered = [c.args[0] for c in ctx.bot.register_behavior.call_args_list]
        all_micros = [m for maneuver in registered for m in maneuver.micros]
        assert all(isinstance(m.micros[0], KeepUnitSafe) for m in registered)
        assert not any(isinstance(m, StutterGroupForward) for m in all_micros)
        assert not any(isinstance(m, AMoveGroup) for m in all_micros)
        amoves = [m for m in all_micros if isinstance(m, AMove)]
        assert len(amoves) == 2
        assert all(m.target == attack for m in amoves)
    finally:
        _restore_targeting(original_targeting)
        _restore_in_range(original_in_range)


# ── Attack squads: never_retreat (Roach/Zergling commit-and-grind) ─────────


def test_never_retreat_skips_keep_group_safe_entirely() -> None:
    """Regression test for the exact user report: "the majority of our
    Roaches and Zerglings never attacked... maintained their distance and
    never engaged". `KeepGroupSafe` only needs one unit mid-cooldown on
    enemy-influenced ground to short-circuit the whole squad's advance -
    `never_retreat=True` must not register it at all."""
    rally = Point2((50.0, 50.0))
    attack = Point2((999.0, 999.0))
    original = _patch_targeting(rally, attack)
    try:
        ctx = _ctx()
        units = [_unit(1, Point2((10.0, 10.0))), _unit(2, Point2((12.0, 10.0)))]
        enemies = [_unit(90, Point2((11.0, 10.0)))]
        ctx.mediator.get_cached_enemy_army = enemies
        ctx.mediator.get_units_in_range.return_value = [enemies]
        ctx.mediator.get_squads.return_value = [_squad(units)]

        combat.attack_squads(never_retreat=True)(ctx)

        registered = ctx.bot.register_behavior.call_args.args[0]
        assert not any(isinstance(m, KeepGroupSafe) for m in registered.micros)
        stutters = [
            m for m in registered.micros if isinstance(m, StutterGroupForward)
        ]
        assert len(stutters) == 1, "should stutter-forward toward the enemy"
        assert stutters[0].enemies == enemies
        assert any(isinstance(m, AMoveGroup) for m in registered.micros)
    finally:
        _restore_targeting(original)


def test_never_retreat_still_commits_when_badly_outnumbered() -> None:
    """`never_retreat=True` overrides `min_engage_range` kiting too - a
    build that wants to never peel off must not fall back to per-unit kite
    just because the enemy force is larger."""
    rally = Point2((50.0, 50.0))
    attack = Point2((999.0, 999.0))
    original = _patch_targeting(rally, attack)
    try:
        ctx = _ctx()
        units = [_unit(1, Point2((10.0, 10.0)))]  # 1 unit
        enemies = [
            _unit(90, Point2((11.0, 10.0))),
            _unit(91, Point2((11.0, 11.0))),
            _unit(92, Point2((11.0, 12.0))),
        ]  # badly outnumbered
        ctx.mediator.get_cached_enemy_army = enemies
        ctx.mediator.get_units_in_range.return_value = [enemies]
        ctx.mediator.get_squads.return_value = [_squad(units)]

        combat.attack_squads(min_engage_range=3.0, never_retreat=True)(ctx)

        assert ctx.bot.register_behavior.call_count == 1, "one group maneuver, not per-unit kite"
        registered = ctx.bot.register_behavior.call_args.args[0]
        assert not any(isinstance(m, KeepUnitSafe) for m in registered.micros)
        assert not any(isinstance(m, KeepGroupSafe) for m in registered.micros)
        assert any(isinstance(m, StutterGroupForward) for m in registered.micros)
    finally:
        _restore_targeting(original)


def test_never_retreat_falls_back_to_amove_with_no_close_enemy() -> None:
    """No nearby army: advance without KeepGroupSafe / stutter — per-unit
    AMove to the destination (no balling for structure hunt)."""
    rally = Point2((50.0, 50.0))
    attack = Point2((999.0, 999.0))
    original = _patch_targeting(rally, attack)
    try:
        ctx = _ctx()
        units = [_unit(1, Point2((10.0, 10.0)))]
        ctx.mediator.get_cached_enemy_army = []
        ctx.mediator.get_units_in_range.return_value = [[]]
        ctx.mediator.get_squads.return_value = [_squad(units)]

        combat.attack_squads(never_retreat=True)(ctx)

        plans = [c.args[0] for c in ctx.bot.register_behavior.call_args_list]
        micros = [m for plan in plans for m in plan.micros]
        assert not any(isinstance(m, KeepGroupSafe) for m in micros)
        assert not any(isinstance(m, StutterGroupForward) for m in micros)
        amoves = [m for m in micros if isinstance(m, AMove)]
        assert len(amoves) == 1
        assert amoves[0].target == attack
    finally:
        _restore_targeting(original)


# ── kite_types: Roach kites, Zergling still commits (never_retreat) ────────


def test_kite_types_splits_roach_into_per_unit_kiting() -> None:
    """Regression test for the exact request: Roach (range 4) should
    maintain 3-4 distance while attacking when outnumbered, same idiom as
    Marine/Stalker, while Zergling (still in the same squad, still melee)
    keeps the never_retreat commit behavior it needed already."""
    rally = Point2((50.0, 50.0))
    attack = Point2((999.0, 999.0))
    original_targeting = _patch_targeting(rally, attack)
    original_in_range = _patch_in_range({})  # nothing in range for any unit
    try:
        ctx = _ctx()
        roach = _unit(1, Point2((10.0, 10.0)))
        roach.type_id = UnitTypeId.ROACH
        zergling = _unit(2, Point2((11.0, 10.0)))
        zergling.type_id = UnitTypeId.ZERGLING
        # Three enemies → our 2-supply squad is outnumbered → Roach kites.
        enemies = [_unit(90 + i, Point2((12.0 + i, 10.0))) for i in range(3)]
        ctx.mediator.get_cached_enemy_army = enemies
        ctx.mediator.get_units_in_range.return_value = [enemies]
        ctx.mediator.get_squads.return_value = [_squad([roach, zergling])]

        combat.attack_squads(
            never_retreat=True,
            kite_types=frozenset({UnitTypeId.ROACH}),
            min_engage_range=3.0,
        )(ctx)

        # Roach: its own, individually-registered _kite_maneuver.
        roach_calls = [
            c
            for c in ctx.bot.register_behavior.call_args_list
            if any(getattr(m, "unit", None) is roach for m in c.args[0].micros)
        ]
        assert len(roach_calls) == 1
        assert not any(
            isinstance(m, KeepUnitSafe) for m in roach_calls[0].args[0].micros
        ), "kite_types must not receive the influence grid"

        # Zergling: the group-level never_retreat maneuver, Roach excluded.
        group_calls = [
            c
            for c in ctx.bot.register_behavior.call_args_list
            if any(isinstance(m, StutterGroupForward) for m in c.args[0].micros)
            or any(isinstance(m, AMoveGroup) for m in c.args[0].micros)
        ]
        assert len(group_calls) == 1
        amove = next(
            m for m in group_calls[0].args[0].micros if isinstance(m, AMoveGroup)
        )
        assert amove.group == [zergling]
        assert not any(
            isinstance(m, KeepGroupSafe) for m in group_calls[0].args[0].micros
        )
    finally:
        _restore_targeting(original_targeting)
        _restore_in_range(original_in_range)


def test_kite_types_stutters_when_our_force_is_larger() -> None:
    """Like Marines with min_engage_range: advantage → StutterGroupForward,
    not unconditional kite."""
    rally = Point2((50.0, 50.0))
    attack = Point2((999.0, 999.0))
    original_targeting = _patch_targeting(rally, attack)
    original_in_range = _patch_in_range({})
    try:
        ctx = _ctx()
        roaches = [_unit(i, Point2((10.0 + i, 10.0))) for i in range(3)]
        for r in roaches:
            r.type_id = UnitTypeId.ROACH
        weak_enemy = _unit(90, Point2((12.0, 10.0)))
        ctx.mediator.get_cached_enemy_army = [weak_enemy]
        ctx.mediator.get_units_in_range.return_value = [[weak_enemy]]
        ctx.mediator.get_squads.return_value = [_squad(roaches)]

        combat.attack_squads(
            never_retreat=True,
            kite_types=frozenset({UnitTypeId.ROACH}),
            min_engage_range=3.0,
        )(ctx)

        registered = ctx.bot.register_behavior.call_args.args[0]
        stutters = [b for b in registered.micros if isinstance(b, StutterGroupForward)]
        assert len(stutters) == 1
        assert stutters[0].group == roaches
        assert not any(getattr(m, "unit", None) in roaches for m in registered.micros)
    finally:
        _restore_targeting(original_targeting)
        _restore_in_range(original_in_range)


def test_kite_types_stutters_through_enemy_ramp_choke() -> None:
    """Even when outnumbered, stutter through the main-ramp choke instead of
    peeling in place (Marine push idiom)."""
    rally = Point2((50.0, 50.0))
    attack = Point2((999.0, 999.0))
    ramp = Point2((100.0, 100.0))
    original_targeting = _patch_targeting(rally, attack)
    original_in_range = _patch_in_range({})
    try:
        ctx = _ctx()
        ramp_mock = MagicMock()
        ramp_mock.bottom_center = ramp
        ctx.mediator.get_enemy_ramp = ramp_mock
        roach = _unit(1, Point2((101.0, 100.0)))  # on the choke
        roach.type_id = UnitTypeId.ROACH
        enemies = [_unit(90 + i, Point2((102.0 + i, 100.0))) for i in range(3)]
        ctx.mediator.get_cached_enemy_army = enemies
        ctx.mediator.get_units_in_range.return_value = [enemies]
        ctx.mediator.get_squads.return_value = [_squad([roach])]

        combat.attack_squads(
            never_retreat=True,
            kite_types=frozenset({UnitTypeId.ROACH}),
            min_engage_range=3.0,
        )(ctx)

        registered = ctx.bot.register_behavior.call_args.args[0]
        stutters = [b for b in registered.micros if isinstance(b, StutterGroupForward)]
        assert len(stutters) == 1
        assert not any(getattr(m, "unit", None) is roach for m in registered.micros)
    finally:
        _restore_targeting(original_targeting)
        _restore_in_range(original_in_range)


def test_kite_types_skips_roach_about_to_regen_burrow() -> None:
    """Hurt Roaches dig in via regen_burrow — do not kite/AMove them first."""
    rally = Point2((50.0, 50.0))
    attack = Point2((999.0, 999.0))
    original_targeting = _patch_targeting(rally, attack)
    original_in_range = _patch_in_range({})
    try:
        ctx = _ctx()
        ctx.bot.state.upgrades = {UpgradeId.BURROW}
        hurt = _unit(1, Point2((10.0, 10.0)))
        hurt.type_id = UnitTypeId.ROACH
        hurt.health_percentage = 0.2
        healthy = _unit(2, Point2((11.0, 10.0)))
        healthy.type_id = UnitTypeId.ROACH
        healthy.health_percentage = 1.0
        enemy = _unit(90, Point2((12.0, 10.0)))
        ctx.mediator.get_cached_enemy_army = [enemy]
        ctx.mediator.get_units_in_range.return_value = [[enemy]]
        ctx.mediator.get_squads.return_value = [_squad([hurt, healthy])]

        combat.attack_squads(
            never_retreat=True,
            kite_types=frozenset({UnitTypeId.ROACH}),
            min_engage_range=3.0,
        )(ctx)

        units_ordered = [
            getattr(m, "unit", None)
            for c in ctx.bot.register_behavior.call_args_list
            for m in c.args[0].micros
        ]
        assert hurt not in units_ordered
        assert healthy in units_ordered
    finally:
        _restore_targeting(original_targeting)
        _restore_in_range(original_in_range)


def test_kite_types_skipped_while_still_mustering() -> None:
    """A squad still forming up should move as one group toward the rally
    point, not start micro-managing individual kite distance."""
    rally = Point2((50.0, 50.0))
    attack = Point2((999.0, 999.0))
    original_targeting = _patch_targeting(rally, attack)
    try:
        ctx = _ctx()
        far_from_rally = Point2((rally.x + 50.0, rally.y + 50.0))  # outside MUSTER_RADIUS
        roach = _unit(1, far_from_rally)
        roach.type_id = UnitTypeId.ROACH
        ctx.state.mustering_tags = {roach.tag}
        ctx.mediator.get_units_from_role.return_value = [roach]  # keep it "alive"
        ctx.mediator.get_units_in_range.return_value = [[]]
        ctx.mediator.get_squads.return_value = [_squad([roach])]

        combat.attack_squads(
            never_retreat=True,
            kite_types=frozenset({UnitTypeId.ROACH}),
            min_engage_range=3.0,
        )(ctx)

        registered = ctx.bot.register_behavior.call_args.args[0]
        assert not any(getattr(m, "unit", None) is roach for m in registered.micros)
        assert any(isinstance(m, AMoveGroup) for m in registered.micros)
    finally:
        _restore_targeting(original_targeting)


def test_attack_squads_scatters_when_only_structures_nearby() -> None:
    """No enemy army → each unit AMoves to its nearest structure instead
    of one AMoveGroup collapsing onto a single building."""
    rally = Point2((50.0, 50.0))
    attack = Point2((999.0, 999.0))
    original = _patch_targeting(rally, attack)
    try:
        ctx = _ctx()
        roach_a = _unit(1, Point2((10.0, 10.0)))
        roach_a.type_id = UnitTypeId.ROACH
        roach_b = _unit(2, Point2((30.0, 10.0)))
        roach_b.type_id = UnitTypeId.ROACH
        pylon_near_a = _unit(90, Point2((12.0, 10.0)))
        pylon_near_a.is_structure = True
        pylon_near_b = _unit(91, Point2((32.0, 10.0)))
        pylon_near_b.is_structure = True
        ctx.mediator.get_cached_enemy_army = []
        ctx.mediator.get_units_in_range.return_value = [
            [pylon_near_a, pylon_near_b]
        ]
        ctx.mediator.get_squads.return_value = [_squad([roach_a, roach_b])]

        combat.attack_squads(
            never_retreat=True,
            kite_types=frozenset({UnitTypeId.ROACH}),
            min_engage_range=3.0,
        )(ctx)

        amoves = [
            m
            for c in ctx.bot.register_behavior.call_args_list
            for m in c.args[0].micros
            if isinstance(m, AMove)
        ]
        assert len(amoves) == 2
        by_unit = {m.unit: m.target for m in amoves}
        assert by_unit[roach_a] == pylon_near_a.position
        assert by_unit[roach_b] == pylon_near_b.position
        assert not any(
            isinstance(m, AMoveGroup)
            for c in ctx.bot.register_behavior.call_args_list
            for m in c.args[0].micros
        )
    finally:
        _restore_targeting(original)


def test_attack_squads_stutters_when_ahead_even_with_min_engage_range() -> None:
    """Having a kite range configured must not kite a fight we are winning -
    stutter-step is the aggressive path when our supply is larger."""
    rally = Point2((50.0, 50.0))
    attack = Point2((999.0, 999.0))
    original = _patch_targeting(rally, attack)
    try:
        ctx = _ctx()
        units = [
            _unit(1, Point2((10.0, 10.0))),
            _unit(2, Point2((12.0, 10.0))),
            _unit(3, Point2((11.0, 12.0))),
        ]
        enemies = [_unit(90, Point2((11.0, 10.0)))]
        ctx.mediator.get_cached_enemy_army = enemies
        ctx.mediator.get_units_in_range.return_value = [enemies]
        ctx.mediator.get_squads.return_value = [_squad(units)]

        combat.attack_squads(min_engage_range=3.0)(ctx)

        registered = ctx.bot.register_behavior.call_args.args[0]
        assert isinstance(registered.micros[0], KeepGroupSafe)
        stutters = [b for b in registered.micros if isinstance(b, StutterGroupForward)]
        assert len(stutters) == 1
        assert stutters[0].enemies == enemies
        assert ctx.bot.register_behavior.call_count == 1, "one group maneuver, not per-unit"
    finally:
        _restore_targeting(original)


def test_structures_do_not_count_as_enemy_force_for_kite_vs_stutter() -> None:
    """A lone Hatchery is not an army — do not kite off its supply; scatter
    each unit onto the building instead of one AMoveGroup."""
    rally = Point2((50.0, 50.0))
    attack = Point2((999.0, 999.0))
    original = _patch_targeting(rally, attack)
    try:
        ctx = _ctx()
        units = [_unit(1, Point2((10.0, 10.0)))]
        hatch = _unit(90, Point2((11.0, 10.0)))
        hatch.is_structure = True
        hatch.type_id = UnitTypeId.HATCHERY
        ctx.bot.calculate_supply_cost.side_effect = (
            lambda t: 10.0 if t == UnitTypeId.HATCHERY else 1.0
        )
        ctx.mediator.get_cached_enemy_army = []
        ctx.mediator.get_units_in_range.return_value = [[hatch]]
        ctx.mediator.get_squads.return_value = [_squad(units)]

        combat.attack_squads(min_engage_range=3.0)(ctx)

        plans = [c.args[0] for c in ctx.bot.register_behavior.call_args_list]
        micros = [m for plan in plans for m in plan.micros]
        assert not any(isinstance(m, KeepUnitSafe) for m in micros), "no kite"
        amoves = [m for m in micros if isinstance(m, AMove)]
        assert len(amoves) == 1
        assert amoves[0].target == hatch.position
    finally:
        _restore_targeting(original)


def test_maxed_and_fully_trained_bypasses_the_wave_gate_and_size() -> None:
    """The 200-supply fallback should release defenders even if the wave
    gate would otherwise refuse and even below the usual size threshold."""
    ctx = _ctx(wave1_min=20)
    ctx.build.combat.wave_gate = lambda _ctx: False  # would normally block
    ctx.bot.supply_used = 200.0
    ctx.build.army.types = frozenset({"ZERGLING"})
    ctx.bot.already_pending.return_value = 0
    defenders = [_unit(i) for i in range(3)]  # well under wave1_min=20
    ctx.mediator.get_units_from_role.return_value = defenders

    combat.release_waves()(ctx)

    assert ctx.state.wave_number == 1
    assert ctx.state.mustering_tags == {u.tag for u in defenders}


def test_not_yet_maxed_still_respects_the_wave_gate() -> None:
    """Sanity check: supply alone isn't enough - still training something
    means the fallback must not fire yet."""
    ctx = _ctx(wave1_min=20)
    ctx.build.combat.wave_gate = lambda _ctx: False
    ctx.bot.supply_used = 200.0
    ctx.build.army.types = frozenset({"ZERGLING"})
    ctx.bot.already_pending.return_value = 1  # still an egg incubating
    defenders = [_unit(i) for i in range(3)]
    ctx.mediator.get_units_from_role.return_value = defenders

    combat.release_waves()(ctx)

    assert ctx.state.wave_number == 0


def test_escort_overseers_targets_the_biggest_squads_destination() -> None:
    """Regression test: the first version AMoved straight at the squad's
    live `squad_position`, which recedes as the squad advances - a slower
    Overseer chasing that target never catches up. It should instead target
    the same destination (`targeting.squad_destination`) the squad itself is
    walking toward, computed from the squad's position, and approach it via
    ares' danger-aware `MoveToSafeTarget`/`KeepUnitSafe` rather than a bare
    `AMove`."""
    ctx = _ctx()
    overseer = _unit(9, Point2((0.0, 0.0)))
    ctx.bot.units.return_value = [overseer]
    ctx.mediator.get_air_grid = "air-grid"

    small = _squad([_unit(1, Point2((10.0, 10.0)))])
    big = _squad([_unit(2, Point2((70.0, 70.0))), _unit(3, Point2((72.0, 70.0)))])
    ctx.mediator.get_squads.return_value = [small, big]

    destination = Point2((999.0, 999.0))
    from_positions: list[Point2] = []

    def _attack_target(_ctx: BotContext, from_pos: Point2) -> Point2:
        from_positions.append(from_pos)
        return destination

    original = (targeting.rally_point, targeting.attack_target)
    targeting.attack_target = _attack_target
    try:
        combat.escort_overseers()(ctx)
    finally:
        _restore_targeting(original)

    assert from_positions == [big.squad_position]

    registered = ctx.bot.register_behavior.call_args.args[0]
    moves = [b for b in registered.micros if isinstance(b, MoveToSafeTarget)]
    assert len(moves) == 1
    assert moves[0].unit is overseer
    assert moves[0].target == destination
    assert moves[0].grid == "air-grid"

    keep_safe = [b for b in registered.micros if isinstance(b, KeepUnitSafe)]
    assert len(keep_safe) == 1
    assert keep_safe[0].unit is overseer
    assert keep_safe[0].grid == "air-grid"

    # KeepUnitSafe must run first so an already-endangered Overseer retreats
    # instead of being sent toward the target (CombatManeuver.execute is
    # `any(...)` over micros - order decides which one wins).
    assert isinstance(registered.micros[0], KeepUnitSafe)


def test_escort_overseers_does_nothing_without_an_overseer() -> None:
    ctx = _ctx()
    ctx.bot.units.return_value = []

    combat.escort_overseers()(ctx)

    ctx.bot.register_behavior.assert_not_called()


def test_escort_overseers_does_nothing_without_an_attacking_squad() -> None:
    ctx = _ctx()
    ctx.bot.units.return_value = [_unit(9)]
    ctx.mediator.get_squads.return_value = []

    combat.escort_overseers()(ctx)

    ctx.bot.register_behavior.assert_not_called()


# --- release_first_wave_then_stream ---------------------------------------


def test_stream_waits_for_wave1_min_before_releasing() -> None:
    ctx = _ctx(wave1_min=5)
    routine = combat.release_first_wave_then_stream()

    ctx.mediator.get_units_from_role.return_value = [_unit(i) for i in range(4)]
    routine(ctx)

    assert ctx.state.wave_number == 0
    ctx.mediator.batch_assign_role.assert_not_called()


def test_stream_respects_wave_gate_for_the_first_wave() -> None:
    ctx = _ctx(wave1_min=5)
    ctx.build.combat.wave_gate = lambda _ctx: False
    routine = combat.release_first_wave_then_stream()

    ctx.mediator.get_units_from_role.return_value = [_unit(i) for i in range(5)]
    routine(ctx)

    assert ctx.state.wave_number == 0
    ctx.mediator.batch_assign_role.assert_not_called()


def test_stream_releases_and_musters_the_first_wave() -> None:
    ctx = _ctx(wave1_min=5)
    routine = combat.release_first_wave_then_stream()
    first_wave = [_unit(i) for i in range(5)]
    ctx.mediator.get_units_from_role.return_value = first_wave

    routine(ctx)

    assert ctx.state.wave_number == 1
    assert ctx.state.mustering_tags == {u.tag for u in first_wave}
    ctx.mediator.batch_assign_role.assert_called_once_with(
        tags={u.tag for u in first_wave}, role=UnitRole.ATTACKING
    )


def test_stream_does_not_wait_for_a_second_wave_size() -> None:
    """Once wave 1 is out, a SINGLE new defender streams immediately - no
    size floor, unlike `release_waves()`'s growth-based next threshold."""
    ctx = _ctx(wave1_min=5)
    routine = combat.release_first_wave_then_stream()
    routine(_release_first_wave(ctx))

    lone_reinforcement = [_unit(999)]
    ctx.mediator.get_units_from_role.return_value = lone_reinforcement
    ctx.mediator.batch_assign_role.reset_mock()

    routine(ctx)

    ctx.mediator.batch_assign_role.assert_called_once_with(
        tags={999}, role=UnitRole.ATTACKING
    )


def test_stream_does_not_add_streamed_units_to_mustering_tags() -> None:
    """The mechanism that makes streaming mean anything: a unit never added
    to `mustering_tags` reads as already-formed to `attack_squads()`, so it
    heads straight to the attack target instead of waiting at the rally."""
    ctx = _ctx(wave1_min=5)
    routine = combat.release_first_wave_then_stream()
    routine(_release_first_wave(ctx))
    before = set(ctx.state.mustering_tags)

    ctx.mediator.get_units_from_role.return_value = [_unit(999)]
    routine(ctx)

    assert ctx.state.mustering_tags == before, "streamed unit must not muster"


def test_stream_does_nothing_with_no_defenders() -> None:
    ctx = _ctx(wave1_min=5)
    ctx.mediator.get_units_from_role.return_value = []

    combat.release_first_wave_then_stream()(ctx)

    ctx.mediator.batch_assign_role.assert_not_called()


def _release_first_wave(ctx: BotContext) -> BotContext:
    """Run wave 1 through `release_first_wave_then_stream` and return ctx."""
    routine = combat.release_first_wave_then_stream()
    ctx.mediator.get_units_from_role.return_value = [
        _unit(i) for i in range(ctx.build.combat.wave1_min)
    ]
    routine(ctx)
    ctx.mediator.batch_assign_role.reset_mock()
    return ctx


def test_streamed_unit_is_not_held_at_rally_by_attack_squads() -> None:
    """End-to-end across both routines: a unit released after wave 1 must
    not sit at the rally point waiting - it should attack immediately."""
    ctx = _ctx(wave1_min=5)
    _release_first_wave(ctx)

    stream_routine = combat.release_first_wave_then_stream()
    ctx.mediator.get_units_from_role.return_value = [_unit(999)]
    stream_routine(ctx)

    squad = _squad([_unit(999)])
    target = _amove_target(ctx, squad)
    assert target == targeting.squad_destination(
        ctx, squad.squad_position
    ), "a streamed unit must head to the attack target, not the rally point"


def test_attack_squads_honors_build_attack_objective() -> None:
    """A build that pins `Combat.attack_objective` must send squads there
    instead of through the default nearest-enemy `attack_target`."""
    objective = Point2((123.0, 456.0))
    ctx = _ctx()
    ctx.build.combat.attack_objective = lambda _ctx: objective
    units = [_unit(1, Point2((0.0, 0.0))), _unit(2, Point2((1.0, 0.0)))]
    ctx.mediator.get_units_from_role.return_value = units

    target = _amove_target(ctx, _squad(units))

    assert target == objective


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
        except Exception as error:  # noqa: BLE001 - report, don't stop
            failures += 1
            print(f"  FAIL  {test.__name__}: {error}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed.")
    return 1 if failures else 0




def test_attack_squads_influence_retreat_runs_before_amove() -> None:
    """With enemy army nearby, KeepGroupSafe is first so unsafe ground
    influence wins over AMove. (No-army frames scatter instead — see
    `test_attack_squads_scatters_when_only_structures_nearby`.)"""
    rally = Point2((50.0, 50.0))
    attack = Point2((999.0, 999.0))
    original = _patch_targeting(rally, attack)
    try:
        ctx = _ctx()
        units = [_unit(1, Point2((10.0, 10.0))), _unit(2, Point2((12.0, 10.0)))]
        enemy = _unit(90, Point2((11.0, 10.0)))
        ctx.mediator.get_cached_enemy_army = [enemy]
        ctx.mediator.get_units_in_range.return_value = [[enemy]]
        ctx.mediator.get_squads.return_value = [_squad(units)]

        combat.attack_squads()(ctx)

        registered = ctx.bot.register_behavior.call_args.args[0]
        assert isinstance(registered.micros[0], KeepGroupSafe)
        assert any(isinstance(b, AMoveGroup) for b in registered.micros)
    finally:
        _restore_targeting(original)


def test_attack_squads_ignores_workers_in_intel_army_for_force() -> None:
    """Workers in the cached army must not flip stutter into kite."""
    rally = Point2((50.0, 50.0))
    attack = Point2((999.0, 999.0))
    original = _patch_targeting(rally, attack)
    try:
        ctx = _ctx()
        units = [
            _unit(1, Point2((10.0, 10.0))),
            _unit(2, Point2((12.0, 10.0))),
            _unit(3, Point2((11.0, 12.0))),
        ]
        marine = _unit(90, Point2((11.0, 10.0)))
        marine.type_id = UnitTypeId.MARINE
        probes = [
            _unit(91, Point2((11.0, 11.0))),
            _unit(92, Point2((11.0, 12.0))),
            _unit(93, Point2((11.0, 13.0))),
            _unit(94, Point2((11.0, 14.0))),
        ]
        for p in probes:
            p.type_id = UnitTypeId.PROBE
        # Cached army includes workers; intel feed strips them → one Marine.
        ctx.mediator.get_cached_enemy_army = [marine, *probes]
        ctx.mediator.get_units_in_range.return_value = [[marine, *probes]]
        ctx.mediator.get_squads.return_value = [_squad(units)]

        combat.attack_squads(min_engage_range=3.0)(ctx)

        registered = ctx.bot.register_behavior.call_args.args[0]
        assert isinstance(registered.micros[0], KeepGroupSafe)
        assert any(isinstance(b, StutterGroupForward) for b in registered.micros)
        assert ctx.bot.register_behavior.call_count == 1
    finally:
        _restore_targeting(original)




def test_early_aggression_widens_defender_engage() -> None:
    """Under early_aggression, defenders collapse farther from the hold."""
    ctx = _ctx()
    ctx.state.early_aggression = True
    hold = Point2((40.0, 40.0))
    unit = _unit(1, Point2((40.0, 40.0)))
    unit.type_id = UnitTypeId.ROACH
    unit.orders = []
    unit.order_target = None
    unit.health_percentage = 1.0
    enemy = _unit(90, Point2((55.0, 40.0)))  # 15 dist: outside 12, inside 18
    enemy.type_id = UnitTypeId.MARINE
    ctx.mediator.get_units_from_role.return_value = [unit]
    ctx.mediator.get_main_ground_threats_near_townhall = []
    ctx.mediator.get_units_in_range.return_value = [[enemy]]
    ctx.bot.units = MagicMock(return_value=[])
    original = targeting.hold_positions
    targeting.hold_positions = lambda _ctx: [hold]
    try:
        combat.defend_home()(ctx)
    finally:
        targeting.hold_positions = original
    assert ctx.bot.register_behavior.called


def test_release_first_wave_holds_leave_under_early_aggression() -> None:
    ctx = _ctx()
    ctx.state.early_aggression = True
    ctx.state.wave_number = 0
    ling = _unit(1)
    ling.type_id = UnitTypeId.ZERGLING
    ctx.mediator.get_units_from_role.return_value = [ling]
    combat.release_first_wave_then_stream()(ctx)
    ctx.mediator.batch_assign_role.assert_not_called()


def test_reinforce_home_vs_early_aggression_peels_lings() -> None:
    ctx = _ctx()
    ctx.state.early_aggression = True
    home = _unit(1)
    home.type_id = UnitTypeId.ZERGLING
    spare = _unit(2)
    spare.type_id = UnitTypeId.ZERGLING

    def _from_role(role=None, unit_type=None, **kwargs):
        if role == combat.ZERGLING_DEFENDER_ROLE:
            return [home]
        return []

    ctx.mediator.get_units_from_role.side_effect = _from_role

    def _units_in_role(role):
        if role == UnitRole.DEFENDING:
            return [spare]
        return []

    ctx.units_in_role = MagicMock(side_effect=_units_in_role)
    combat.reinforce_home_vs_early_aggression()(ctx)
    ctx.mediator.assign_role.assert_called()
    assert ctx.mediator.assign_role.call_args.kwargs["tag"] == 2


def test_defend_home_assigns_sticky_hold_slots() -> None:
    """Reordering units or hold points must not bounce defenders between holds."""
    ctx = _ctx()
    rally = Point2((40.0, 40.0))
    minerals = Point2((20.0, 20.0))
    a = _unit(1, Point2((39.0, 39.0)))
    b = _unit(2, Point2((21.0, 21.0)))
    a.type_id = UnitTypeId.ZEALOT
    b.type_id = UnitTypeId.STALKER
    a.orders = []
    b.orders = []
    a.order_target = None
    b.order_target = None
    ctx.mediator.get_units_from_role.return_value = [a, b]
    ctx.mediator.get_main_ground_threats_near_townhall = []
    ctx.mediator.get_units_in_range.return_value = [[]]
    original = targeting.hold_positions
    targeting.hold_positions = lambda _ctx: [rally, minerals]
    try:
        combat.defend_home()(ctx)
        first = dict(ctx.state.defender_hold)
        assert set(first) == {1, 2}
        assert first[1] != first[2]

        # Reverse unit list and hold list — sticky Point2 must stay put.
        targeting.hold_positions = lambda _ctx: [minerals, rally]
        ctx.mediator.get_units_from_role.return_value = [b, a]
        ctx.bot.register_behavior.reset_mock()
        combat.defend_home()(ctx)
        assert ctx.state.defender_hold == first
    finally:
        targeting.hold_positions = original


def test_sticky_hold_does_not_relog_when_hold_point_jitters() -> None:
    """Behind-mineral holds can drift several tiles; that must snap quietly,
    not reassign + spam `ZERGLING_DEFEND hold` every frame."""
    ctx = _ctx()
    ctx.log = MagicMock()
    hold_a = Point2((40.0, 40.0))
    hold_b = Point2((80.0, 80.0))
    unit = _unit(1, Point2((40.0, 40.0)))
    unit.type_id = UnitTypeId.ZERGLING
    hold_map: dict[int, Point2] = {}

    first = combat._sticky_hold_point(
        ctx, unit, [hold_a, hold_b], hold_map, log_label="ZERGLING_DEFEND"
    )
    assert first == hold_a
    assert ctx.log.call_count == 1

    # Same logical hold, drifted ~5 tiles (old 2.5 radius would reassign).
    jittered = Point2((45.0, 40.0))
    ctx.log.reset_mock()
    second = combat._sticky_hold_point(
        ctx, unit, [jittered, hold_b], hold_map, log_label="ZERGLING_DEFEND"
    )
    assert second == jittered
    assert hold_map[1] == jittered
    ctx.log.assert_not_called()


def test_defender_maneuver_zerglings_attack_scout_worker_in_base() -> None:
    ctx = _ctx()
    hold = Point2((40.0, 40.0))
    ling = _unit(1, Point2((40.0, 40.0)))
    ling.type_id = UnitTypeId.ZERGLING
    ling.orders = []
    ling.order_target = None
    scout = _unit(90, Point2((12.0, 12.0)))
    scout.type_id = UnitTypeId.PROBE
    ctx.bot.start_location = Point2((10.0, 10.0))
    ctx.mediator.get_own_nat = Point2((30.0, 30.0))
    th = MagicMock()
    th.position = Point2((10.0, 10.0))
    ctx.bot.townhalls = [th]
    ctx.bot.enemy_units = [scout]
    ctx.mediator.get_units_in_range.return_value = [[]]  # no army nearby

    maneuver = combat._defender_maneuver(
        ctx, ling, [], hold, chase_workers=True
    )

    assert maneuver is not None
    attacks = [m for m in maneuver.micros if isinstance(m, AttackTarget)]
    assert len(attacks) == 1
    assert attacks[0].target is scout


def test_defender_maneuver_zerglings_do_not_chase_scout_off_map() -> None:
    ctx = _ctx()
    hold = Point2((40.0, 40.0))
    ling = _unit(1, Point2((40.0, 40.0)))
    ling.type_id = UnitTypeId.ZERGLING
    ling.orders = []
    ling.order_target = None
    # Far past any base leash — fleeing scout.
    scout = _unit(90, Point2((100.0, 100.0)))
    scout.type_id = UnitTypeId.PROBE
    ctx.bot.start_location = Point2((10.0, 10.0))
    ctx.mediator.get_own_nat = Point2((30.0, 30.0))
    th = MagicMock()
    th.position = Point2((10.0, 10.0))
    ctx.bot.townhalls = [th]
    ctx.bot.enemy_units = [scout]
    ctx.mediator.get_units_in_range.return_value = [[]]

    maneuver = combat._defender_maneuver(
        ctx, ling, [], hold, chase_workers=True
    )

    assert maneuver is None, "settled at hold; scout left the leash"


def test_defender_maneuver_settled_hysteresis_skips_renudge() -> None:
    """Idle just outside ARRIVE but inside SETTLED must not re-issue move."""
    ctx = _ctx()
    hold = Point2((40.0, 40.0))
    # 5 tiles out — past ARRIVE (4) but inside SETTLED (7).
    ling = _unit(1, Point2((45.0, 40.0)))
    ling.type_id = UnitTypeId.ZERGLING
    ling.orders = []
    ling.order_target = None
    ctx.mediator.get_units_in_range.return_value = [[]]
    ctx.bot.enemy_units = []

    assert combat._defender_maneuver(ctx, ling, [], hold) is None


def test_defender_maneuver_without_chase_workers_ignores_scout() -> None:
    ctx = _ctx()
    hold = Point2((40.0, 40.0))
    zealot = _unit(1, Point2((40.0, 40.0)))
    zealot.type_id = UnitTypeId.ZEALOT
    zealot.orders = []
    zealot.order_target = None
    scout = _unit(90, Point2((12.0, 12.0)))
    scout.type_id = UnitTypeId.PROBE
    ctx.bot.start_location = Point2((10.0, 10.0))
    ctx.mediator.get_own_nat = Point2((30.0, 30.0))
    th = MagicMock()
    th.position = Point2((10.0, 10.0))
    ctx.bot.townhalls = [th]
    ctx.bot.enemy_units = [scout]
    ctx.mediator.get_units_in_range.return_value = [[scout]]

    maneuver = combat._defender_maneuver(
        ctx, zealot, [], hold, chase_workers=False
    )

    assert maneuver is None


def test_defend_home_does_not_reissue_when_settled_at_hold() -> None:
    ctx = _ctx()
    hold = Point2((40.0, 40.0))
    unit = _unit(7, Point2((40.5, 40.2)))  # inside ARRIVE
    unit.type_id = UnitTypeId.ZEALOT
    unit.orders = []
    unit.order_target = None
    ctx.mediator.get_units_from_role.return_value = [unit]
    ctx.mediator.get_main_ground_threats_near_townhall = []
    ctx.mediator.get_units_in_range.return_value = [[]]
    original = targeting.hold_positions
    targeting.hold_positions = lambda _ctx: [hold]
    try:
        combat.defend_home()(ctx)
        assert ctx.bot.register_behavior.call_count == 0
    finally:
        targeting.hold_positions = original


def test_defend_home_skips_move_when_already_pathing_to_hold() -> None:
    ctx = _ctx()
    hold = Point2((40.0, 40.0))
    unit = _unit(8, Point2((30.0, 30.0)))  # still walking in
    unit.type_id = UnitTypeId.STALKER
    unit.orders = [object()]
    unit.order_target = Point2((40.0, 40.0))
    ctx.mediator.get_units_from_role.return_value = [unit]
    ctx.mediator.get_main_ground_threats_near_townhall = []
    ctx.mediator.get_units_in_range.return_value = [[]]
    original = targeting.hold_positions
    targeting.hold_positions = lambda _ctx: [hold]
    try:
        combat.defend_home()(ctx)
        assert ctx.bot.register_behavior.call_count == 0
    finally:
        targeting.hold_positions = original


def test_muster_commit_holds_when_not_formed_up() -> None:
    commit, prism_wait_expired = combat.muster_commit_decision(
        form_ready=False, prism_ready=False, waiting_since=None, now=100.0
    )
    assert (commit, prism_wait_expired) == (False, False)


def test_muster_commit_fires_immediately_once_prism_is_ready() -> None:
    commit, prism_wait_expired = combat.muster_commit_decision(
        form_ready=True, prism_ready=True, waiting_since=None, now=100.0
    )
    assert (commit, prism_wait_expired) == (True, False)


def test_muster_commit_holds_while_prism_wait_clock_has_not_started() -> None:
    # Mirrors the first frame form-up is ready but not yet latched by
    # `note_muster_waiting_prism`.
    commit, prism_wait_expired = combat.muster_commit_decision(
        form_ready=True, prism_ready=False, waiting_since=None, now=100.0
    )
    assert (commit, prism_wait_expired) == (False, False)


def test_muster_commit_holds_before_prism_timeout_elapses() -> None:
    commit, prism_wait_expired = combat.muster_commit_decision(
        form_ready=True,
        prism_ready=False,
        waiting_since=100.0,
        now=100.0 + combat.CHARGELOT_MUSTER_PRISM_TIMEOUT - 1.0,
    )
    assert (commit, prism_wait_expired) == (False, False)


def test_muster_commit_fires_once_prism_timeout_elapses() -> None:
    commit, prism_wait_expired = combat.muster_commit_decision(
        form_ready=True,
        prism_ready=False,
        waiting_since=100.0,
        now=100.0 + combat.CHARGELOT_MUSTER_PRISM_TIMEOUT,
    )
    assert (commit, prism_wait_expired) == (True, True), "boundary (>=) should fire"


def test_chargelot_kiting_is_false_before_any_commit() -> None:
    assert combat.chargelot_kiting(committed_at=None, now=100.0) is False


def test_chargelot_kiting_is_true_right_after_commit() -> None:
    assert combat.chargelot_kiting(committed_at=100.0, now=100.0) is True


def test_chargelot_kiting_is_true_within_the_window() -> None:
    assert (
        combat.chargelot_kiting(
            committed_at=100.0,
            now=100.0 + combat.CHARGELOT_KITE_WINDOW_S - 1.0,
        )
        is True
    )


def test_chargelot_kiting_ends_once_the_window_elapses() -> None:
    assert (
        combat.chargelot_kiting(
            committed_at=100.0, now=100.0 + combat.CHARGELOT_KITE_WINDOW_S
        )
        is False
    ), "boundary (>=) should end kiting"


def test_stalker_target_score_prefers_medivac_over_everything() -> None:
    medivac = combat.stalker_target_score(
        is_medivac=True, is_repairing=False, is_worker=False, vital=500.0
    )
    low_hp_zealot = combat.stalker_target_score(
        is_medivac=False, is_repairing=False, is_worker=False, vital=1.0
    )
    assert medivac < low_hp_zealot


def test_stalker_target_score_prefers_repairing_worker_over_plain_worker() -> None:
    repairing = combat.stalker_target_score(
        is_medivac=False, is_repairing=True, is_worker=True, vital=45.0
    )
    plain_worker = combat.stalker_target_score(
        is_medivac=False, is_repairing=False, is_worker=True, vital=1.0
    )
    assert repairing < plain_worker


def test_stalker_target_score_prefers_any_worker_over_army_unit() -> None:
    worker = combat.stalker_target_score(
        is_medivac=False, is_repairing=False, is_worker=True, vital=45.0
    )
    army_unit = combat.stalker_target_score(
        is_medivac=False, is_repairing=False, is_worker=False, vital=1.0
    )
    assert worker < army_unit


def test_stalker_target_score_falls_back_to_lowest_vital() -> None:
    lower_hp = combat.stalker_target_score(
        is_medivac=False, is_repairing=False, is_worker=False, vital=10.0
    )
    higher_hp = combat.stalker_target_score(
        is_medivac=False, is_repairing=False, is_worker=False, vital=50.0
    )
    assert lower_hp < higher_hp


def test_stalker_pick_target_uses_priority_order_end_to_end() -> None:
    """`_stalker_pick_target` wires `stalker_target_score` to real units."""
    stalker = _unit(1)
    marine = _unit(2)
    marine.type_id = UnitTypeId.MARINE
    marine.orders = []
    marine.health = 5.0
    marine.shield = 0.0
    medivac = _unit(3)
    medivac.type_id = UnitTypeId.MEDIVAC
    medivac.orders = []
    medivac.health = 150.0
    medivac.shield = 0.0
    original = combat.cy_in_attack_range
    combat.cy_in_attack_range = lambda _stalker, enemies: enemies
    try:
        picked = combat._stalker_pick_target(stalker, [marine, medivac])
    finally:
        combat.cy_in_attack_range = original
    assert picked is medivac, "Medivac must outrank a low-HP Marine"



def test_zergling_target_score_prefers_immortal_over_stalker() -> None:
    immortal = combat.zergling_target_score(
        type_id=UnitTypeId.IMMORTAL, vital=100.0
    )
    stalker = combat.zergling_target_score(
        type_id=UnitTypeId.STALKER, vital=50.0
    )
    assert immortal < stalker


def test_pick_zergling_focus_chases_immortal_outside_melee_range() -> None:
    """Immortals just outside weapon range must still beat in-range Stalkers."""
    ling = _unit(1, Point2((100.0, 100.0)))
    stalker = _unit(90, Point2((101.0, 100.0)))
    stalker.type_id = UnitTypeId.STALKER
    stalker.health = 80.0
    stalker.shield = 80.0
    immortal = _unit(91, Point2((105.0, 100.0)))
    immortal.type_id = UnitTypeId.IMMORTAL
    immortal.health = 200.0
    immortal.shield = 100.0
    original = combat.cy_in_attack_range
    combat.cy_in_attack_range = lambda unit, enemies, *a, **k: [stalker]
    try:
        picked = combat.pick_zergling_focus_target(ling, [stalker, immortal])
    finally:
        combat.cy_in_attack_range = original
    assert picked is immortal


def test_zergling_target_score_prefers_immortal_over_marine() -> None:
    immortal = combat.zergling_target_score(
        type_id=UnitTypeId.IMMORTAL, vital=200.0
    )
    marine = combat.zergling_target_score(
        type_id=UnitTypeId.MARINE, vital=10.0
    )
    assert immortal < marine


def test_zergling_target_score_avoids_colossus_when_alternatives_exist() -> None:
    colossus = combat.zergling_target_score(
        type_id=UnitTypeId.COLOSSUS, vital=10.0
    )
    stalker = combat.zergling_target_score(
        type_id=UnitTypeId.STALKER, vital=200.0
    )
    assert stalker < colossus


def test_roach_target_score_prefers_colossus_over_stalker() -> None:
    colossus = combat.roach_target_score(
        type_id=UnitTypeId.COLOSSUS, vital=200.0, lings_present=False
    )
    stalker = combat.roach_target_score(
        type_id=UnitTypeId.STALKER, vital=10.0, lings_present=False
    )
    assert colossus < stalker


def test_roach_focus_types_are_ground_only() -> None:
    air = {
        UnitTypeId.CARRIER,
        UnitTypeId.TEMPEST,
        UnitTypeId.LIBERATORAG,
        UnitTypeId.VOIDRAY,
        UnitTypeId.BANSHEE,
        UnitTypeId.MUTALISK,
    }
    assert not (combat.ROACH_FOCUS_TYPES & air)
    for ground_hvt in (
        UnitTypeId.GHOST,
        UnitTypeId.ARCHON,
        UnitTypeId.DARKTEMPLAR,
        UnitTypeId.LURKERMPBURROWED,
        UnitTypeId.INFESTOR,
    ):
        assert ground_hvt in combat.ROACH_FOCUS_TYPES


def test_roach_target_score_prefers_ghost_over_marine() -> None:
    ghost = combat.roach_target_score(
        type_id=UnitTypeId.GHOST, vital=200.0, lings_present=False
    )
    marine = combat.roach_target_score(
        type_id=UnitTypeId.MARINE, vital=10.0, lings_present=False
    )
    assert ghost < marine


def test_roach_target_score_deprioritizes_tank_when_lings_present() -> None:
    tank_with_lings = combat.roach_target_score(
        type_id=UnitTypeId.SIEGETANKSIEGED, vital=10.0, lings_present=True
    )
    marine_with_lings = combat.roach_target_score(
        type_id=UnitTypeId.MARINE, vital=200.0, lings_present=True
    )
    assert marine_with_lings < tank_with_lings
    tank_alone = combat.roach_target_score(
        type_id=UnitTypeId.SIEGETANKSIEGED, vital=10.0, lings_present=False
    )
    assert tank_alone < tank_with_lings


def test_pick_roach_hvt_target_chases_colossus_outside_range() -> None:
    roach = _unit(1, Point2((10.0, 10.0)))
    roach.type_id = UnitTypeId.ROACH
    colossus = _unit(90, Point2((20.0, 10.0)))
    colossus.type_id = UnitTypeId.COLOSSUS
    colossus.health = 200.0
    colossus.shield = 0.0
    stalker = _unit(91, Point2((12.0, 10.0)))
    stalker.type_id = UnitTypeId.STALKER
    stalker.health = 10.0
    stalker.shield = 0.0
    # Only Stalker in weapon range — still chase the Colossus.
    original = _patch_in_range({1: [stalker]})
    try:
        picked = combat.pick_roach_hvt_target(roach, [colossus, stalker])
        assert picked is colossus
    finally:
        _restore_in_range(original)


def test_pick_zergling_focus_target_picks_marauder_over_hellion() -> None:
    ling = _unit(1, Point2((10.0, 10.0)))
    ling.type_id = UnitTypeId.ZERGLING
    marauder = _unit(90, Point2((11.0, 10.0)))
    marauder.type_id = UnitTypeId.MARAUDER
    marauder.health = 100.0
    marauder.shield = 0.0
    hellion = _unit(91, Point2((10.5, 10.0)))
    hellion.type_id = UnitTypeId.HELLION
    hellion.health = 10.0
    hellion.shield = 0.0
    original = _patch_in_range({1: [marauder, hellion]})
    try:
        picked = combat.pick_zergling_focus_target(ling, [marauder, hellion])
        assert picked is marauder
    finally:
        _restore_in_range(original)


def test_pick_roach_kite_target_skips_immortal_when_lings_present() -> None:
    roach = _unit(1, Point2((10.0, 10.0)))
    roach.type_id = UnitTypeId.ROACH
    immortal = _unit(90, Point2((12.0, 10.0)))
    immortal.type_id = UnitTypeId.IMMORTAL
    immortal.health = 10.0
    immortal.shield = 0.0
    stalker = _unit(91, Point2((13.0, 10.0)))
    stalker.type_id = UnitTypeId.STALKER
    stalker.health = 200.0
    stalker.shield = 0.0
    original = _patch_in_range({1: [immortal, stalker]})
    try:
        picked = combat.pick_roach_kite_target(
            roach, [immortal, stalker], lings_present=True
        )
        assert picked is stalker
    finally:
        _restore_in_range(original)



def test_stalker_retreat_point_backs_away_from_single_crowder() -> None:
    stalker = _unit(1, Point2((100.0, 100.0)))
    crowder = _unit(90, Point2((101.0, 100.0)))  # 1 unit away - melee range

    retreat_to = combat._stalker_retreat_point(stalker, [crowder], 4.0)

    assert round(cy_distance_to(retreat_to, crowder.position), 3) == 4.0
    assert retreat_to.x < crowder.position.x, "retreats toward the stalker's side"


def test_stalker_retreat_point_backs_away_from_crowd_center() -> None:
    stalker = _unit(1, Point2((100.0, 100.0)))
    crowder_a = _unit(90, Point2((101.0, 99.0)))
    crowder_b = _unit(91, Point2((101.0, 101.0)))

    retreat_to = combat._stalker_retreat_point(stalker, [crowder_a, crowder_b], 4.0)

    center = Point2((101.0, 100.0))
    assert round(cy_distance_to(retreat_to, center), 3) == 4.0


def test_attacker_needs_work_when_idle_or_holding() -> None:
    idle = _unit(1)
    idle.is_idle = True
    idle.orders = []
    assert combat._attacker_needs_work(idle) is True

    holding = _unit(2)
    holding.is_idle = False
    order = MagicMock()
    order.ability = MagicMock()
    order.ability.id = AbilityId.HOLDPOSITION
    holding.orders = [order]
    assert combat._attacker_needs_work(holding) is True

    busy = _unit(3)
    busy.is_idle = False
    move = MagicMock()
    move.ability = MagicMock()
    move.ability.id = AbilityId.ATTACK
    busy.orders = [move]
    assert combat._attacker_needs_work(busy) is False


def test_nudge_idle_army_reissues_attack_on_interval() -> None:
    ctx = _ctx()
    ctx.bot.time = 400.0
    ctx.state.chargelot_muster_committed_at = 300.0  # kite window long over
    idle = _unit(1, Point2((50.0, 50.0)))
    idle.is_idle = True
    idle.orders = []
    ctx.units_in_role = MagicMock(return_value=[idle])
    ctx.mediator.get_units_from_role.return_value = []
    dest = Point2((200.0, 200.0))

    with patch.object(combat.targeting, "squad_destination", return_value=dest):
        combat.nudge_idle_army(interval_s=3.0)(ctx)
    idle.attack.assert_called_once_with(dest)
    assert ctx.state.army_idle_check_at == 400.0

    idle.attack.reset_mock()
    ctx.bot.time = 401.0
    with patch.object(combat.targeting, "squad_destination", return_value=dest):
        combat.nudge_idle_army(interval_s=3.0)(ctx)
    idle.attack.assert_not_called()

    ctx.bot.time = 404.0
    with patch.object(combat.targeting, "squad_destination", return_value=dest):
        combat.nudge_idle_army(interval_s=3.0)(ctx)
    idle.attack.assert_called_once_with(dest)


def test_nudge_idle_army_skips_mustering_and_drop_load() -> None:
    ctx = _ctx()
    ctx.bot.time = 400.0
    mustering = _unit(1, Point2((10.0, 10.0)))
    mustering.is_idle = True
    mustering.orders = []
    ctx.state.mustering_tags = {1}
    ctx.units_in_role = MagicMock(return_value=[mustering])
    drop = _unit(2)
    ctx.mediator.get_units_from_role.return_value = [drop]

    combat.nudge_idle_army(interval_s=0.0)(ctx)

    mustering.attack.assert_not_called()




def test_defend_home_skips_roach_about_to_regen_burrow() -> None:
    """Hurt DEFENDING Roaches dig via regen - defend must not Move them."""
    ctx = _ctx()
    hold = Point2((40.0, 40.0))
    roach = _unit(7, Point2((10.0, 10.0)))
    roach.type_id = UnitTypeId.ROACH
    roach.health_percentage = 0.2
    roach.orders = []
    roach.order_target = None
    ctx.bot.state.upgrades = {UpgradeId.BURROW}
    ctx.mediator.get_units_from_role.return_value = [roach]
    ctx.mediator.get_main_ground_threats_near_townhall = []
    ctx.mediator.get_units_in_range.return_value = [[]]
    ctx.bot.units = MagicMock(return_value=[])
    original = targeting.hold_positions
    targeting.hold_positions = lambda _ctx: [hold]
    try:
        combat.defend_home()(ctx)
    finally:
        targeting.hold_positions = original
    ctx.bot.register_behavior.assert_not_called()
    assert 7 not in ctx.state.defender_hold


def test_regen_burrow_roaches_skips_burrow_when_already_ordered() -> None:
    ctx = _ctx()
    ctx.bot.state.upgrades = {UpgradeId.BURROW}
    hurt = _unit(1)
    hurt.type_id = UnitTypeId.ROACH
    hurt.health_percentage = 0.2
    order = MagicMock()
    order.ability = MagicMock()
    order.ability.id = AbilityId.BURROWDOWN_ROACH
    hurt.orders = [order]

    def _units(unit_type):
        if unit_type == UnitTypeId.ROACH:
            return [hurt]
        if unit_type == UnitTypeId.ROACHBURROWED:
            return []
        return []

    ctx.bot.units = MagicMock(side_effect=_units)
    combat.regen_burrow_roaches()(ctx)
    ctx.bot.register_behavior.assert_not_called()


def test_regen_burrow_roaches_skips_claws_move_when_already_moving() -> None:
    from bot.core import context as context_mod

    ctx = _ctx()
    ctx.bot.state.upgrades = {UpgradeId.BURROW, UpgradeId.TUNNELINGCLAWS}
    home = Point2((5.0, 5.0))
    ctx.mediator.get_units_in_range.return_value = [[_unit(90, Point2((52.0, 50.0)))]]
    ctx.mediator.is_position_safe.return_value = False
    burrowed = _unit(1, Point2((50.0, 50.0)))
    burrowed.health_percentage = 0.4
    burrowed.is_moving = True
    order = MagicMock()
    order.ability = MagicMock()
    order.ability.id = AbilityId.MOVE
    burrowed.orders = [order]
    burrowed.order_target = Point2((6.0, 6.0))

    def _units(unit_type):
        if unit_type == UnitTypeId.ROACH:
            return []
        if unit_type == UnitTypeId.ROACHBURROWED:
            return [burrowed]
        return []

    ctx.bot.units = MagicMock(side_effect=_units)
    original = context_mod.BotContext.production_location
    context_mod.BotContext.production_location = property(lambda self: home)
    try:
        combat.regen_burrow_roaches()(ctx)
    finally:
        context_mod.BotContext.production_location = original
    ctx.bot.register_behavior.assert_not_called()


def test_regen_burrow_roaches_burrows_when_not_full_hp() -> None:
    ctx = _ctx()
    ctx.bot.state.upgrades = {UpgradeId.BURROW}
    hurt = _unit(1)
    hurt.type_id = UnitTypeId.ROACH
    hurt.health_percentage = 0.99
    healthy = _unit(2)
    healthy.type_id = UnitTypeId.ROACH
    healthy.health_percentage = 1.0

    def _units(unit_type):
        if unit_type == UnitTypeId.ROACH:
            return [hurt, healthy]
        if unit_type == UnitTypeId.ROACHBURROWED:
            return []
        return []

    ctx.bot.units = MagicMock(side_effect=_units)

    combat.regen_burrow_roaches()(ctx)

    registered = [c.args[0] for c in ctx.bot.register_behavior.call_args_list]
    assert len(registered) == 1
    assert isinstance(registered[0], UseAbility)
    assert registered[0].ability == AbilityId.BURROWDOWN_ROACH
    assert registered[0].unit is hurt


def test_regen_burrow_roaches_unburrows_at_full_health() -> None:
    ctx = _ctx()
    ctx.bot.state.upgrades = {UpgradeId.BURROW}
    ready = _unit(1)
    ready.health_percentage = 1.0
    healing = _unit(2)
    healing.health_percentage = 0.99

    def _units(unit_type):
        if unit_type == UnitTypeId.ROACH:
            return []
        if unit_type == UnitTypeId.ROACHBURROWED:
            return [ready, healing]
        return []

    ctx.bot.units = MagicMock(side_effect=_units)

    combat.regen_burrow_roaches()(ctx)

    registered = [c.args[0] for c in ctx.bot.register_behavior.call_args_list]
    assert len(registered) == 1
    assert isinstance(registered[0], UseAbility)
    assert registered[0].ability == AbilityId.BURROWUP_ROACH
    assert registered[0].unit is ready


def test_regen_burrow_roaches_unburrows_even_when_army_nearby() -> None:
    """At full HP, surface to fight — do not wait for a clear bubble."""
    ctx = _ctx()
    ctx.bot.state.upgrades = {UpgradeId.BURROW}
    ready = _unit(1, Point2((50.0, 50.0)))
    ready.health_percentage = 1.0
    enemy = _unit(90, Point2((52.0, 50.0)))
    ctx.mediator.get_units_in_range.return_value = [[enemy]]

    def _units(unit_type):
        if unit_type == UnitTypeId.ROACH:
            return []
        if unit_type == UnitTypeId.ROACHBURROWED:
            return [ready]
        return []

    ctx.bot.units = MagicMock(side_effect=_units)

    combat.regen_burrow_roaches()(ctx)

    registered = [c.args[0] for c in ctx.bot.register_behavior.call_args_list]
    assert len(registered) == 1
    assert isinstance(registered[0], UseAbility)
    assert registered[0].ability == AbilityId.BURROWUP_ROACH


def test_regen_burrow_roaches_unburrows_near_structures_only() -> None:
    """Structures must not pin ready Roaches underground."""
    ctx = _ctx()
    ctx.bot.state.upgrades = {UpgradeId.BURROW}
    ready = _unit(1, Point2((50.0, 50.0)))
    ready.health_percentage = 1.0
    pylon = _unit(90, Point2((52.0, 50.0)))
    pylon.is_structure = True
    ctx.mediator.get_units_in_range.return_value = [[pylon]]

    def _units(unit_type):
        if unit_type == UnitTypeId.ROACH:
            return []
        if unit_type == UnitTypeId.ROACHBURROWED:
            return [ready]
        return []

    ctx.bot.units = MagicMock(side_effect=_units)

    combat.regen_burrow_roaches()(ctx)

    registered = [c.args[0] for c in ctx.bot.register_behavior.call_args_list]
    assert len(registered) == 1
    assert isinstance(registered[0], UseAbility)
    assert registered[0].ability == AbilityId.BURROWUP_ROACH


def test_regen_burrow_roaches_unburrows_despite_unsafe_influence() -> None:
    """Stale influence must not keep ready Roaches down."""
    ctx = _ctx()
    ctx.bot.state.upgrades = {UpgradeId.BURROW, UpgradeId.TUNNELINGCLAWS}
    ctx.bot.start_location = Point2((10.0, 10.0))
    ctx.mediator.is_position_safe.return_value = False
    ctx.mediator.get_units_in_range.return_value = [[]]
    ready = _unit(1, Point2((50.0, 50.0)))
    ready.health_percentage = 1.0

    def _units(unit_type):
        if unit_type == UnitTypeId.ROACH:
            return []
        if unit_type == UnitTypeId.ROACHBURROWED:
            return [ready]
        return []

    ctx.bot.units = MagicMock(side_effect=_units)

    combat.regen_burrow_roaches()(ctx)

    registered = [c.args[0] for c in ctx.bot.register_behavior.call_args_list]
    assert len(registered) == 1
    assert isinstance(registered[0], UseAbility)
    assert registered[0].ability == AbilityId.BURROWUP_ROACH


def test_regen_burrow_roaches_stays_burrowed_below_full_when_surrounded() -> None:
    """Below full HP still sit/peel — do not surface into the fight early."""
    ctx = _ctx()
    ctx.bot.state.upgrades = {UpgradeId.BURROW}
    healing = _unit(1, Point2((50.0, 50.0)))
    healing.health_percentage = 0.4
    enemy = _unit(90, Point2((52.0, 50.0)))
    ctx.mediator.get_units_in_range.return_value = [[enemy]]

    def _units(unit_type):
        if unit_type == UnitTypeId.ROACH:
            return []
        if unit_type == UnitTypeId.ROACHBURROWED:
            return [healing]
        return []

    ctx.bot.units = MagicMock(side_effect=_units)

    combat.regen_burrow_roaches()(ctx)

    ctx.bot.register_behavior.assert_not_called()


def test_regen_burrow_roaches_retreats_while_healing_with_claws() -> None:
    """Sticky PathUnitToTarget home — no KeepUnitSafe (live burrow thrash)."""
    from ares.behaviors.combat.individual import PathUnitToTarget

    ctx = _ctx()
    ctx.bot.state.upgrades = {UpgradeId.BURROW, UpgradeId.TUNNELINGCLAWS}
    ctx.bot.start_location = Point2((10.0, 10.0))
    ctx.mediator.is_position_safe.return_value = True
    healing = _unit(1, Point2((50.0, 50.0)))
    healing.health_percentage = 0.4
    healing.orders = []
    healing.is_moving = False
    enemy = _unit(90, Point2((52.0, 50.0)))
    ctx.mediator.get_units_in_range.return_value = [[enemy]]

    def _units(unit_type):
        if unit_type == UnitTypeId.ROACH:
            return []
        if unit_type == UnitTypeId.ROACHBURROWED:
            return [healing]
        return []

    ctx.bot.units = MagicMock(side_effect=_units)

    combat.regen_burrow_roaches()(ctx)

    registered = ctx.bot.register_behavior.call_args.args[0]
    assert isinstance(registered, PathUnitToTarget)
    assert registered.unit is healing
    assert UseAbility not in [type(registered)]


def test_regen_burrow_roaches_paths_home_when_clear_with_claws() -> None:
    """Damaged burrowed Roaches still tunnel home even with no army nearby."""
    from ares.behaviors.combat.individual import PathUnitToTarget

    ctx = _ctx()
    ctx.bot.state.upgrades = {UpgradeId.BURROW, UpgradeId.TUNNELINGCLAWS}
    ctx.bot.start_location = Point2((10.0, 10.0))
    ctx.mediator.is_position_safe.return_value = True
    ctx.mediator.get_units_in_range.return_value = [[]]
    healing = _unit(1, Point2((50.0, 50.0)))
    healing.health_percentage = 0.4
    healing.orders = []
    healing.is_moving = False

    def _units(unit_type):
        if unit_type == UnitTypeId.ROACH:
            return []
        if unit_type == UnitTypeId.ROACHBURROWED:
            return [healing]
        return []

    ctx.bot.units = MagicMock(side_effect=_units)

    combat.regen_burrow_roaches()(ctx)

    registered = ctx.bot.register_behavior.call_args.args[0]
    assert isinstance(registered, PathUnitToTarget)
    assert registered.unit is healing


def test_regen_burrow_roaches_skips_repath_when_already_tunneling() -> None:
    """Active burrowed MOVE must not be canceled every frame."""
    from bot.core import context as context_mod

    ctx = _ctx()
    ctx.bot.state.upgrades = {UpgradeId.BURROW, UpgradeId.TUNNELINGCLAWS}
    home = Point2((5.0, 5.0))
    burrowed = _unit(1, Point2((50.0, 50.0)))
    burrowed.health_percentage = 0.4
    burrowed.is_moving = True
    order = MagicMock()
    order.ability = MagicMock()
    order.ability.id = AbilityId.MOVE
    burrowed.orders = [order]
    burrowed.order_target = Point2((20.0, 20.0))

    def _units(unit_type):
        if unit_type == UnitTypeId.ROACH:
            return []
        if unit_type == UnitTypeId.ROACHBURROWED:
            return [burrowed]
        return []

    ctx.bot.units = MagicMock(side_effect=_units)
    original = context_mod.BotContext.production_location
    context_mod.BotContext.production_location = property(lambda self: home)
    try:
        combat.regen_burrow_roaches()(ctx)
    finally:
        context_mod.BotContext.production_location = original
    ctx.bot.register_behavior.assert_not_called()


def test_regen_burrow_roaches_stays_put_without_claws_while_healing() -> None:
    ctx = _ctx()
    ctx.bot.state.upgrades = {UpgradeId.BURROW}
    healing = _unit(1)
    healing.health_percentage = 0.4

    def _units(unit_type):
        if unit_type == UnitTypeId.ROACH:
            return []
        if unit_type == UnitTypeId.ROACHBURROWED:
            return [healing]
        return []

    ctx.bot.units = MagicMock(side_effect=_units)

    combat.regen_burrow_roaches()(ctx)

    ctx.bot.register_behavior.assert_not_called()


def test_regen_burrow_roaches_noop_without_burrow_upgrade() -> None:
    ctx = _ctx()
    ctx.bot.state.upgrades = set()
    hurt = _unit(1)
    hurt.health_percentage = 0.1
    ctx.bot.units = MagicMock(return_value=[hurt])

    combat.regen_burrow_roaches()(ctx)

    ctx.bot.register_behavior.assert_not_called()


def test_release_home_zerglings_after_early_promotes_to_defending() -> None:
    from bot.consts import ZERGLING_DEFENDER_ROLE

    ctx = _ctx()
    ctx.bot.time = 301.0
    ctx.state.early_aggression = False
    ling = _unit(9, Point2((20.0, 20.0)))
    ling.type_id = UnitTypeId.ZERGLING
    ctx.mediator.get_units_from_role.return_value = [ling]
    ctx.state.zergling_defender_hold[9] = Point2((15.0, 15.0))

    combat.release_home_zerglings_after_early()(ctx)

    ctx.mediator.assign_role.assert_called_once_with(
        tag=9, role=UnitRole.DEFENDING
    )
    assert 9 not in ctx.state.zergling_defender_hold


def test_release_home_zerglings_after_early_noop_during_window() -> None:
    ctx = _ctx()
    ctx.bot.time = 60.0
    ctx.state.early_aggression = False
    ling = _unit(9, Point2((20.0, 20.0)))
    ctx.mediator.get_units_from_role.return_value = [ling]

    combat.release_home_zerglings_after_early()(ctx)

    ctx.mediator.assign_role.assert_not_called()


if __name__ == "__main__":
    sys.exit(main())
