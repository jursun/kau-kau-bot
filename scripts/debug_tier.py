"""Debug tier: 1 game vs Protoss, random map, Easy.

For fast iteration on an in-progress change — see `harness_common.DEBUG`.

    python scripts/debug_tier.py --build "2base Chargelot All-In"
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.harness_common import DEBUG, run_tier  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_tier(DEBUG))
