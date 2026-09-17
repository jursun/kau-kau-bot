"""Regression tier: every map x every race (21 total), Hard.

    python scripts/harness/regression_tier.py --build "2base Chargelot All-In"
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.harness.harness_common import REGRESSION, run_tier  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_tier(REGRESSION))
