"""Quick tier: 1 game, random map, random race, Medium.

For fast iteration on an in-progress change — see `harness_common.QUICK`.
Narrow it to targeted testing with `--map`/`--opponent`/`--difficulty`.

    python scripts/harness/quick_tier.py --build "2base Chargelot All-In"
    python scripts/harness/quick_tier.py --build "2base Chargelot All-In" --opponent Zerg
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.harness.harness_common import QUICK, run_tier  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_tier(QUICK))
