"""Scout-driven counter composition."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sc2.ids.unit_typeid import UnitTypeId as U

from bot.core.context import BotContext
from bot.core.state import RunState
from bot.intel import composition as comp

BASE = {
    U.ROACH: {"proportion": 0.30, "priority": 0},
    U.HYDRALISK: {"proportion": 0.25, "priority": 0},
    U.RAVAGER: {"proportion": 0.20, "priority": 0},
    U.INFESTOR: {"proportion": 0.10, "priority": 0},
    U.ZERGLING: {"proportion": 0.15, "priority": 1},
}


def _total(c) -> float:
    return sum(v["proportion"] for v in c.values())


def test_no_scouting_leaves_the_base_comp_unchanged() -> None:
    out = comp.counter_comp(BASE, {})

    assert {u: round(v["proportion"], 4) for u, v in out.items()} == {
        u: v["proportion"] for u, v in BASE.items()
    }


def test_immortals_cut_roach_and_ravager_and_boost_lings_hydras_infestors() -> None:
    out = comp.counter_comp(BASE, {U.IMMORTAL: 0.5, U.ZEALOT: 0.5})

    assert out[U.ROACH]["proportion"] < BASE[U.ROACH]["proportion"]
    assert out[U.RAVAGER]["proportion"] < BASE[U.RAVAGER]["proportion"]
    assert out[U.ZERGLING]["proportion"] > BASE[U.ZERGLING]["proportion"]
    assert out[U.HYDRALISK]["proportion"] > BASE[U.HYDRALISK]["proportion"]
    assert out[U.INFESTOR]["proportion"] > BASE[U.INFESTOR]["proportion"]
    assert _total(out) == pytest.approx(1.0)


def test_a_pure_immortal_army_pushes_infestors_hard() -> None:
    out = comp.counter_comp(BASE, {U.IMMORTAL: 1.0})

    assert out[U.INFESTOR]["proportion"] > 2 * BASE[U.INFESTOR]["proportion"]
    assert out[U.ROACH]["proportion"] < 0.5 * BASE[U.ROACH]["proportion"]


def test_mixed_armies_blend_their_counters() -> None:
    mostly_zealot = comp.counter_comp(BASE, {U.ZEALOT: 0.9, U.IMMORTAL: 0.1})
    mostly_immortal = comp.counter_comp(BASE, {U.ZEALOT: 0.1, U.IMMORTAL: 0.9})

    assert (
        mostly_immortal[U.INFESTOR]["proportion"]
        > mostly_zealot[U.INFESTOR]["proportion"]
    )
    assert (
        mostly_zealot[U.ROACH]["proportion"] > mostly_immortal[U.ROACH]["proportion"]
    )


def test_air_armies_drop_units_that_cannot_shoot_up() -> None:
    out = comp.counter_comp(BASE, {U.CARRIER: 1.0})

    assert out[U.HYDRALISK]["proportion"] > BASE[U.HYDRALISK]["proportion"]
    assert U.ZERGLING not in out or out[U.ZERGLING]["proportion"] < 0.1


def test_infestor_is_added_only_when_allowed() -> None:
    base = {k: v for k, v in BASE.items() if k != U.INFESTOR}

    without = comp.counter_comp(base, {U.IMMORTAL: 1.0})
    with_it = comp.counter_comp(base, {U.IMMORTAL: 1.0}, add_infestor=True)

    assert U.INFESTOR not in without
    assert U.INFESTOR in with_it
    assert _total(with_it) == pytest.approx(1.0)


def test_priorities_survive_and_the_input_is_not_mutated() -> None:
    snapshot = {u: dict(v) for u, v in BASE.items()}

    out = comp.counter_comp(BASE, {U.IMMORTAL: 1.0})

    assert BASE == snapshot
    assert out[U.ZERGLING]["priority"] == 1


def test_cap_unit_removes_it_at_the_cap_and_renormalizes() -> None:
    capped = comp.cap_unit(BASE, U.RAVAGER, 8, 8)

    assert U.RAVAGER not in capped
    assert _total(capped) == pytest.approx(1.0)
    assert comp.cap_unit(BASE, U.RAVAGER, 7, 8) is BASE


def _enemy(type_id) -> MagicMock:
    unit = MagicMock()
    unit.type_id = type_id
    return unit


def _ctx(enemies, now: float = 100.0) -> BotContext:
    bot = MagicMock()
    bot.time = now
    bot.calculate_supply_cost.side_effect = lambda t: {
        U.IMMORTAL: 4.0,
        U.ZEALOT: 2.0,
    }.get(t, 2.0)
    ctx = BotContext(bot=bot, build=MagicMock(), state=RunState())
    ctx.mediator.get_cached_enemy_army = list(enemies)
    return ctx


def test_supply_shares_follow_the_visible_army() -> None:
    ctx = _ctx([_enemy(U.IMMORTAL)] * 3 + [_enemy(U.ZEALOT)] * 6)

    shares = comp.enemy_supply_shares(ctx)

    assert shares[U.IMMORTAL] == pytest.approx(0.5)
    assert shares[U.ZEALOT] == pytest.approx(0.5)


def test_supply_shares_remember_what_was_seen_recently() -> None:
    ctx = _ctx([_enemy(U.IMMORTAL)] * 3)
    comp.enemy_supply_shares(ctx)

    ctx.mediator.get_cached_enemy_army = [_enemy(U.ZEALOT)] * 2  # immortals vanish
    ctx.bot.time = 100.0 + comp.MEMORY_S - 5.0
    shares = comp.enemy_supply_shares(ctx)
    assert U.IMMORTAL in shares  # still counted

    ctx.bot.time = 100.0 + comp.MEMORY_S + 5.0
    shares = comp.enemy_supply_shares(ctx)
    assert U.IMMORTAL not in shares


def test_no_enemy_seen_gives_no_shares() -> None:
    assert comp.enemy_supply_shares(_ctx([])) == {}
