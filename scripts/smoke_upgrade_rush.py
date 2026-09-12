"""Smoke-test UpgradeRush vs Terran, Zerg, and Protoss on random maps.

Race / Debug / build are CLI overrides applied in memory (config.yml is not
written). Defaults: config.yml for race & Debug when omitted; this script's
build name when `--build` is omitted. Forcing a build turns Debug on and
UseData off so ares always picks that opening via `test_123`.

Usage (from repo root):

    poetry run python scripts/smoke_upgrade_rush.py
    poetry run python scripts/smoke_upgrade_rush.py --validate
    poetry run python scripts/smoke_upgrade_rush.py --race Zerg --build UpgradeRush
    poetry run python scripts/smoke_upgrade_rush.py --difficulty Easy --time-limit 480

Exit status is 0 when every game finishes without raising (Victory / Defeat /
Tie all count as a smoke pass). Non-zero if a game crashes.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.smoke_common import run_smoke  # noqa: E402

BUILD_NAME = "UpgradeRush"
DEFAULT_RACE = "Zerg"


if __name__ == "__main__":
    raise SystemExit(
        run_smoke(build_name=BUILD_NAME, default_race=DEFAULT_RACE)
    )
