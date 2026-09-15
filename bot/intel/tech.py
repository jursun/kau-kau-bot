"""Lightweight seen-tech tracking from the filtered army feed."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bot.core.context import BotContext
from bot.intel.army import enemy_army_type_ids


@dataclass
class SeenTech:
    """Type ids observed on the WORKER-filtered enemy army."""

    type_ids: set[Any] = field(default_factory=set)

    def observe(self, type_ids: Any) -> None:
        self.type_ids.update(type_ids)

    def observe_ctx(self, ctx: BotContext) -> None:
        self.observe(enemy_army_type_ids(ctx))

    def seen(self, type_id: Any) -> bool:
        return type_id in self.type_ids


def observe_seen_tech(seen: SeenTech, ctx: BotContext) -> SeenTech:
    """Update `seen` from this frame's filtered army; return it."""
    seen.observe_ctx(ctx)
    return seen
