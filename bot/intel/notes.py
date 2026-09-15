"""OpponentId-keyed notes stub.

Ladder sets `bot.opponent_id` from `--OpponentId` (see `ladder.py`).
Ares `./data/<opponent_id>-<race>.json` is opening win/loss history only —
do not dump metrics there. Keep notes in-memory for now.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bot.core.context import BotContext


def get_opponent_id(ctx: BotContext) -> str | None:
    """Ladder OpponentId, or None for local / unset."""
    return getattr(ctx.bot, "opponent_id", None)


@dataclass
class OpponentNotes:
    """In-memory notes keyed by opponent id (empty string if unknown)."""

    _notes: dict[str, list[str]] = field(default_factory=dict)

    @staticmethod
    def _key(opponent_id: str | None) -> str:
        return opponent_id or ""

    def add(self, opponent_id: str | None, note: str) -> None:
        self._notes.setdefault(self._key(opponent_id), []).append(note)

    def get(self, opponent_id: str | None) -> list[str]:
        return list(self._notes.get(self._key(opponent_id), []))

    def add_for_ctx(self, ctx: BotContext, note: str) -> None:
        self.add(get_opponent_id(ctx), note)

    def get_for_ctx(self, ctx: BotContext) -> list[str]:
        return self.get(get_opponent_id(ctx))

    def clear(self, opponent_id: str | None = None) -> None:
        if opponent_id is None:
            self._notes.clear()
        else:
            self._notes.pop(self._key(opponent_id), None)
