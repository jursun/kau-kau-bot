"""Smoke-test Four Rax Proxy vs Terran, Zerg, and Protoss on random maps.

Runs three local games in sequence — one against each computer race — each
on a different random map from `LocalGame.MapPool` (or whatever
`run.resolve_map_list` finds). Requires `MyBotRace: Terran` and
`Debug: True` in config.yml so ares' `test_123` cycle forces Four Rax Proxy.

Usage (from repo root):

    poetry run python scripts/smoke_four_rax_proxy.py
    poetry run python scripts/smoke_four_rax_proxy.py --validate
    poetry run python scripts/smoke_four_rax_proxy.py --difficulty Easy --time-limit 480

Exit status is 0 when every game finishes without raising (Victory / Defeat /
Tie all count as a smoke pass). Non-zero if a game crashes or the config
isn't set up to run this build.
"""

from __future__ import annotations

import argparse
import random
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from loguru import logger  # noqa: E402
from sc2.data import Difficulty, Race, Result  # noqa: E402
from sc2.main import run_game  # noqa: E402
from sc2.player import Bot, Computer  # noqa: E402

from run import (  # noqa: E402
    LOCAL_GAME,
    MY_BOT_NAME,
    MY_BOT_RACE,
    build_bot_ai,
    load_config,
    resolve_map,
    resolve_map_list,
)

OPPONENT_RACES: tuple[Race, ...] = (Race.Terran, Race.Zerg, Race.Protoss)
BUILD_NAME: str = "Four Rax Proxy"


@dataclass
class GameOutcome:
    opponent: Race
    map_name: str
    result: Result | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.result is not None


def _require_four_rax_setup(config: dict) -> None:
    race = (config.get(MY_BOT_RACE) or "").title()
    if race != "Terran":
        raise SystemExit(
            f"Smoke needs {MY_BOT_RACE}: Terran (got {race!r}) so "
            f"`terran_builds.yml` / {BUILD_NAME} actually load."
        )
    if not config.get("Debug"):
        raise SystemExit(
            "Smoke needs Debug: True in config.yml so ares uses the "
            f"`test_123` cycle that forces {BUILD_NAME}."
        )


def _pick_maps(pool: list[str], count: int) -> list[str]:
    """One map per game; prefer unique maps when the pool is large enough."""
    if not pool:
        raise SystemExit("No maps available — check LocalGame.MapPool / MapPath.")
    if len(pool) >= count:
        return random.sample(pool, count)
    return [random.choice(pool) for _ in range(count)]


def _play_one(
    *,
    bot_name: str,
    race: Race,
    opponent: Race,
    map_name: str,
    maps_path: str | None,
    difficulty: Difficulty,
    validate: bool,
    realtime: bool,
    time_limit: int | None,
) -> GameOutcome:
    logger.info(
        f"===== SMOKE {BUILD_NAME} vs {opponent.name} {difficulty.name} "
        f"on {map_name} ====="
    )
    try:
        result = run_game(
            resolve_map(map_name, maps_path),
            [
                Bot(race, build_bot_ai(validate), bot_name),
                Computer(opponent, difficulty),
            ],
            realtime=realtime,
            game_time_limit=time_limit,
        )
        return GameOutcome(opponent=opponent, map_name=map_name, result=result)
    except Exception as error:  # noqa: BLE001 - smoke must keep going
        logger.error(f"SMOKE crashed vs {opponent.name} on {map_name}: {error}")
        traceback.print_exc()
        return GameOutcome(
            opponent=opponent,
            map_name=map_name,
            result=None,
            error=f"{type(error).__name__}: {error}",
        )


def _print_summary(outcomes: list[GameOutcome]) -> None:
    logger.info("===== SMOKE SUMMARY =====")
    for outcome in outcomes:
        if outcome.error:
            status = f"CRASH ({outcome.error})"
        else:
            status = outcome.result.name if outcome.result is not None else "UNKNOWN"
        logger.info(f"  vs {outcome.opponent.name:<8} on {outcome.map_name}: {status}")
    crashes = sum(1 for o in outcomes if not o.ok)
    logger.info(
        f"Finished {len(outcomes)} games, {crashes} crash(es). "
        "Smoke pass = no crashes (losses are fine)."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=f"Smoke-test {BUILD_NAME} vs all three races on random maps."
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Attach the build's rush validator (report at each game end).",
    )
    parser.add_argument(
        "--difficulty",
        type=str,
        default=None,
        help="Computer difficulty (default: LocalGame.OpponentDifficulty).",
    )
    parser.add_argument(
        "--time-limit",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Artificial Tie after N game-seconds (omit to play to the end).",
    )
    parser.add_argument(
        "--realtime",
        action="store_true",
        default=None,
        help="Force realtime on (default comes from LocalGame.Realtime).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="RNG seed for map picks (and anything else random here).",
    )
    args = parser.parse_args(argv)

    if args.seed is not None:
        random.seed(args.seed)

    config = load_config()
    _require_four_rax_setup(config)
    local_cfg = config.get(LOCAL_GAME) or {}

    bot_name = config.get(MY_BOT_NAME, "KauKauBot")
    race = Race[config.get(MY_BOT_RACE, "Terran").title()]

    difficulty_name = args.difficulty or local_cfg.get("OpponentDifficulty", "VeryHard")
    try:
        difficulty = Difficulty[difficulty_name]
    except KeyError:
        raise SystemExit(f"Unknown difficulty {difficulty_name!r}") from None

    realtime = (
        bool(args.realtime)
        if args.realtime is not None
        else bool(local_cfg.get("Realtime"))
    )

    maps_path = local_cfg.get("MapPath")
    map_picks = _pick_maps(resolve_map_list(local_cfg), len(OPPONENT_RACES))

    outcomes: list[GameOutcome] = []
    for opponent, map_name in zip(OPPONENT_RACES, map_picks, strict=True):
        outcomes.append(
            _play_one(
                bot_name=bot_name,
                race=race,
                opponent=opponent,
                map_name=map_name,
                maps_path=maps_path,
                difficulty=difficulty,
                validate=args.validate,
                realtime=realtime,
                time_limit=args.time_limit,
            )
        )

    _print_summary(outcomes)
    return 1 if any(not o.ok for o in outcomes) else 0


if __name__ == "__main__":
    sys.exit(main())
