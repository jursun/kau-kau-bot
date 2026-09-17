"""Queen production as an ares `MacroBehavior`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from cython_extensions import cy_distance_to_squared
from sc2.ids.unit_typeid import UnitTypeId
from sc2.unit import Unit

from ares.behaviors.macro.macro_behavior import MacroBehavior
from ares.managers.manager_mediator import ManagerMediator

if TYPE_CHECKING:
    from ares import AresBot


@dataclass
class TrainQueens(MacroBehavior):
    """Keep up to `to_count` queens, at most `max_per_townhall` per ready
    townhall - every townhall gets its own before any gets a second.

    Queens are trained from the hatchery rather than from larva, so this does
    not compete with `SpawnController` for larva — but it does compete for
    minerals, which is why it sits high in the `MacroPlan`.

    A first version picked the first *idle* ready townhall in `ai.townhalls`'
    own iteration order every call, with `max_per_townhall` only feeding the
    overall target ceiling (`len(townhalls) * max_per_townhall`), not an
    actual per-townhall cap - confirmed live, with `max_per_townhall=2` a
    newer base could go a full game without ever training a Queen, because
    the (consistently-ordered-first) main kept winning the "which townhall
    is idle right now" race and re-filled its own slots as its queens
    finished, reaching the *combined* target on its own before the newer
    townhall ever got a turn. Sorting ready-and-idle townhalls by their own
    *current* Queen count (ascending) before picking means an empty townhall
    always outranks one that already has some, at any total count.

    A second version counted each live Queen against whichever townhall it
    was *currently* nearest to, re-derived fresh every call - confirmed
    live as its own bug: `InjectLarva` sends the closest *available* Queen
    to whichever townhall needs an inject next, not necessarily the one
    that trained it, so a Queen could be standing at a different base
    mid-inject at the exact moment this recomputed counts, crediting that
    base with a Queen it never actually got while the base that trained it
    silently lost credit - the natural inconsistently never receiving its
    own Queen was this in practice. `home_townhall` (a persistent tag ->
    tag mapping, snapshotted once per Queen at creation - see `builds.zerg.
    macro_zerg._macro_zerg_on_unit_created`) fixes that by recording
    "which base trained this Queen" once, at the one moment it's
    unambiguous, instead of re-guessing it from wherever the Queen happens
    to be standing right now.

    A third bug, `cooldown_seconds` fixes: in realtime play specifically,
    ares can call `execute()` several times against the *same* stale game
    state before the engine finishes processing an already-issued command
    (`already_pending`/`is_idle` both lag behind `.train()` by more than
    one call there, unlike stepped/non-realtime play where each call sees
    a fresh observation) - confirmed live, three Queens landed at the same
    townhall within 0.1 real seconds, `counts` reading identically `{0, 0}`
    on all three calls, while the other base never got one at all. A
    per-instance cooldown (backed by `cooldown_state`, a plain dict that
    persists across the fresh `TrainQueens` instance the wrapper builds
    each call) closes that gap without depending on game state catching up
    at all - nothing else here can misfire more than once per interval,
    regardless of how many times ares calls in before an observation
    refreshes.

    Attributes:
        to_count: Total queen target across all bases.
        max_per_townhall: Queens to allow per ready townhall.
        home_townhall: Queen tag -> townhall tag, as recorded at creation.
            Falls back to nearest-current-position for any Queen not in
            this mapping (e.g. one that existed before tracking started).
        cooldown_state: Mutable dict this writes `{"last": ai.time}` into
            after training - persisted by the caller (`ctx.state`) across
            the fresh instance built each call. `None` disables the guard.
        cooldown_seconds: Minimum real/game time between two commands.
    """

    to_count: int
    max_per_townhall: int = 1
    home_townhall: dict[int, int] | None = None
    cooldown_state: dict[str, float] | None = None
    cooldown_seconds: float = 2.0

    def execute(self, ai: "AresBot", config: dict, mediator: ManagerMediator) -> bool:
        if not ai.structures(UnitTypeId.SPAWNINGPOOL).ready:
            return False

        if self.cooldown_state is not None:
            last_trained: float = self.cooldown_state.get("last", -1e9)
            if ai.time - last_trained < self.cooldown_seconds:
                return False

        townhalls: list[Unit] = [th for th in ai.townhalls.ready]
        if not townhalls:
            return False

        queens = ai.units(UnitTypeId.QUEEN)
        existing: int = queens.amount
        pending: int = int(ai.already_pending(UnitTypeId.QUEEN))
        target: int = min(self.to_count, len(townhalls) * self.max_per_townhall)
        if existing + pending >= target:
            return False

        if not ai.can_afford(UnitTypeId.QUEEN):
            return False

        counts: dict[int, int] = {th.tag: 0 for th in townhalls}
        townhall_tags = set(counts)
        for queen in queens:
            home = self.home_townhall.get(queen.tag) if self.home_townhall else None
            if home is not None and home in townhall_tags:
                counts[home] += 1
                continue
            nearest = min(
                townhalls,
                key=lambda th: cy_distance_to_squared(queen.position, th.position),
            )
            counts[nearest.tag] += 1

        for th in sorted(townhalls, key=lambda th: counts[th.tag]):
            if counts[th.tag] >= self.max_per_townhall or not th.is_idle:
                continue
            th.train(UnitTypeId.QUEEN)
            if self.cooldown_state is not None:
                self.cooldown_state["last"] = ai.time
            return True

        return False
