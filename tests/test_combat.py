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
from ares.behaviors.combat.individual import KeepUnitSafe, MoveToSafeTarget
from ares.consts import UnitRole
from ares.managers.squad_manager import UnitSquad
from sc2.position import Point2

from bot.core.context import BotContext
from bot.core.state import RunState
from bot.routines import combat, targeting


def _unit(tag: int, position: Point2 = Point2((0.0, 0.0))) -> MagicMock:
    unit = MagicMock()
    unit.tag = tag
    unit.position = position
    return unit


def _ctx(wave1_min: int = 6, wave_growth: float = 1.25) -> BotContext:
    build = MagicMock()
    build.army.types = frozenset()
    build.combat.wave1_min = wave1_min
    build.combat.wave_growth = wave_growth
    build.combat.wave_gate = lambda _ctx: True
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
    """attack_squads calls module-level targeting.rally_point/attack_target."""
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


# ── Attack squads: no engagement ratio, no retreat ──────────────────────────


def test_squad_attacks_regardless_of_how_outnumbered_it_is() -> None:
    """There is no supply-ratio check and no retreat: a squad fights whatever
    is in `SQUAD_ENGAGE_RANGE` with `StutterGroupForward` even when badly
    outnumbered, and still heads for the real attack target, never rally."""
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
    the same destination (`targeting.attack_target`) the squad itself is
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
    assert target == targeting.attack_target(
        ctx, squad.squad_position
    ), "a streamed unit must head to the attack target, not the rally point"


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
