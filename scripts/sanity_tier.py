"""Sanity tier: 1 random-race game per map (7 total), Medium.

    python scripts/sanity_tier.py --build "2base Chargelot All-In"
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.harness_common import SANITY, run_tier  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_tier(SANITY))
