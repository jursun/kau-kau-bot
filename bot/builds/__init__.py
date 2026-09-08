"""Build plans. One is chosen at random on game start (or forced via config)."""

from __future__ import annotations

import random
from typing import Dict, List, Type, Union

from bot.builds.ling_rush import LingRush
from bot.builds.upgrade_rush import UpgradeRush

BuildPlan = Union[Type[LingRush], Type[UpgradeRush]]

BUILD_POOL: List[BuildPlan] = [LingRush, UpgradeRush]
BUILDS_BY_NAME: Dict[str, BuildPlan] = {b.NAME: b for b in BUILD_POOL}


def choose_build(rng: random.Random | None = None) -> BuildPlan:
    """Prefer config.FORCE_BUILD when present; otherwise random from BUILD_POOL."""
    try:
        from config import FORCE_BUILD  # type: ignore
    except Exception:
        FORCE_BUILD = None
    if FORCE_BUILD:
        key = str(FORCE_BUILD).strip().lower()
        if key in BUILDS_BY_NAME:
            return BUILDS_BY_NAME[key]
    r = rng or random
    return r.choice(BUILD_POOL)
