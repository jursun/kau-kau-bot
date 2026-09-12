"""Regression tests for wave shaping and the muster-before-attack behavior.

`release_waves` and `attack_squads` are the two hardest-to-eyeball routines in
`bot/routines/combat.py`: sizing math and squad/rally-point interaction are
each easy to get subtly wrong without a game to watch. These exercise the
real `UnitSquad`, `CombatManeuver`/`AMoveGroup` and `cy_distance_to_squared`
from ares/cython_extensions; only the AresBot/ManagerMediator boundary is
mocked.

Runs under pytest, or standalone with no test dependency:

    python -m tests.test_combat
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from ares.behaviors.combat.group import AMoveGroup, StutterGroupForward
from ares.behaviors.combat.individual import (
    AMove,
    KeepUnitSafe,
    MoveToSafeTarget,
    ShootTargetInRange,
)
from ares.consts import UnitRole
from ares.managers.squad_manager import UnitSquad
from cython_extensions import cy_distance_to
from sc2.ids.unit_typeid import UnitTypeId
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
    ctx.bot.already_pending.return_value = 0
    ctx.bot.calculate_supply_cost.return_value = 1.0
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
    """Run attack_squads for one squad and return the AMoveGroup target."""
    ctx.mediator.get_units_in_range.return_value = [[]]  # no close enemies
    ctx.mediator.get_squads.return_value = [squad]

    combat.attack_squads()(ctx)

    registered = ctx.bot.register_behavior.call_args.args[0]
    amoves = [b for b in registered.micros if isinstance(b, AMoveGroup)]
    assert len(amoves) == 1, "expected exactly one AMoveGroup"
    return amoves[0].target


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
        enemies = [_unit(90), _unit(91), _unit(92), _unit(93)]  # badly outnumbered
        ctx.mediator.get_units_in_range.return_value = [enemies]
        ctx.mediator.get_squads.return_value = [_squad(units)]

        combat.attack_squads()(ctx)

        registered = ctx.bot.register_behavior.call_args.args[0]
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
        ctx.mediator.get_units_in_range.return_value = [enemies]
        ctx.mediator.get_squads.return_value = [_squad(units)]

        combat.attack_squads(min_engage_range=3.0)(ctx)

        assert ctx.bot.register_behavior.call_count == 2, "one maneuver per unit"
        registered = [c.args[0] for c in ctx.bot.register_behavior.call_args_list]
        all_micros = [m for maneuver in registered for m in maneuver.micros]
        assert not any(isinstance(m, StutterGroupForward) for m in all_micros)
        assert not any(isinstance(m, AMoveGroup) for m in all_micros)
        amoves = [m for m in all_micros if isinstance(m, AMove)]
        assert len(amoves) == 2
        assert all(m.target == attack for m in amoves)
    finally:
        _restore_targeting(original_targeting)
        _restore_in_range(original_in_range)


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
        ctx.mediator.get_units_in_range.return_value = [enemies]
        ctx.mediator.get_squads.return_value = [_squad(units)]

        combat.attack_squads(min_engage_range=3.0)(ctx)

        registered = ctx.bot.register_behavior.call_args.args[0]
        stutters = [b for b in registered.micros if isinstance(b, StutterGroupForward)]
        assert len(stutters) == 1
        assert stutters[0].enemies == enemies
        assert ctx.bot.register_behavior.call_count == 1, "one group maneuver, not per-unit"
    finally:
        _restore_targeting(original)


def test_structures_do_not_count_as_enemy_force_for_kite_vs_stutter() -> None:
    """A lone Hatchery in range is not an army - Marines should stutter into
    it, not kite off its supply cost."""
    rally = Point2((50.0, 50.0))
    attack = Point2((999.0, 999.0))
    original = _patch_targeting(rally, attack)
    try:
        ctx = _ctx()
        units = [_unit(1, Point2((10.0, 10.0)))]
        hatch = _unit(90, Point2((11.0, 10.0)))
        hatch.is_structure = True
        hatch.type_id = UnitTypeId.HATCHERY
        # Expensive if counted - would flip the comparison wrongly.
        ctx.bot.calculate_supply_cost.side_effect = (
            lambda t: 10.0 if t == UnitTypeId.HATCHERY else 1.0
        )
        ctx.mediator.get_units_in_range.return_value = [[hatch]]
        ctx.mediator.get_squads.return_value = [_squad(units)]

        combat.attack_squads(min_engage_range=3.0)(ctx)

        registered = ctx.bot.register_behavior.call_args.args[0]
        assert any(isinstance(b, StutterGroupForward) for b in registered.micros)
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


if __name__ == "__main__":
    sys.exit(main())
