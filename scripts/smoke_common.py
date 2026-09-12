"""Shared helpers for local smoke scripts.

CLI overrides for race / debug / build are applied to the **in-memory** ares
config on the bot instance (after `*_builds.yml` merge, before DataManager
picks an opening). `config.yml` on disk is never written.
"""

from __future__ import annotations

import argparse
import random
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
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
TEST_CYCLE_ID: str = "test_123"


@dataclass
class GameOutcome:
    opponent: Race
    map_name: str
    result: Result | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.result is not None


@dataclass(frozen=True)
class SmokeSettings:
    """Resolved race / debug / build for one smoke run."""

    race: Race
    debug: bool
    build: str | None
    bot_name: str
    config: dict


def pick_maps(pool: list[str], count: int) -> list[str]:
    """One map per game; prefer unique maps when the pool is large enough."""
    if not pool:
        raise SystemExit("No maps available — check LocalGame.MapPool / MapPath.")
    if len(pool) >= count:
        return random.sample(pool, count)
    return [random.choice(pool) for _ in range(count)]


def apply_config_overrides(
    config: dict,
    *,
    debug: bool | None = None,
    build: str | None = None,
    race_name: str | None = None,
) -> None:
    """Mutate an ares config dict so Debug / opening cycle match smoke intent.

    When `build` is set: enable Debug (unless explicitly disabled), put that
    opening first under `BuildChoices.test_123.Cycle`, and set `UseData`
    False so ares always takes cycle[0] instead of win/loss history.
    """
    if race_name:
        config[MY_BOT_RACE] = race_name

    if build:
        if debug is False:
            raise SystemExit(
                f"Cannot force build {build!r} with Debug disabled — "
                "ares only uses the test_123 cycle when Debug is True."
            )
        debug = True
        config["UseData"] = False
        choices = config.setdefault("BuildChoices", {})
        entry = choices.setdefault(TEST_CYCLE_ID, {})
        cycle = list(entry.get("Cycle") or [])
        if build not in cycle:
            # Build may land only after *_builds.yml merge; allow empty here.
            cycle.append(build)
        entry["Cycle"] = [build, *[b for b in cycle if b != build]]
        entry.setdefault("BotName", "LocalDebug")
        choices[TEST_CYCLE_ID] = entry
        config["BuildChoices"] = choices

    if debug is not None:
        config["Debug"] = bool(debug)


def resolve_smoke_settings(
    config: dict,
    *,
    race_arg: str | None,
    debug_arg: bool | None,
    build_arg: str | None,
    default_race: str,
    default_build: str | None,
) -> SmokeSettings:
    """CLI overrides win; omitted values fall back to config.yml, then script defaults.

    `--build` with an empty string means "do not force an opening" (use the
    merged builds.yml cycle / UseData as-is).
    """
    race_name = (race_arg or config.get(MY_BOT_RACE) or default_race).title()
    try:
        race = Race[race_name]
    except KeyError as exc:
        raise SystemExit(f"Unknown race {race_name!r}") from exc

    if build_arg is None:
        build = default_build or None
    else:
        build = build_arg.strip() or None

    if debug_arg is not None:
        debug: bool | None = debug_arg
    elif build:
        # Forcing an opening requires the Debug test_123 cycle.
        debug = True
    elif "Debug" in config:
        debug = bool(config.get("Debug"))
    else:
        debug = None

    config[MY_BOT_RACE] = race.name
    apply_config_overrides(
        config, debug=debug, build=build, race_name=race.name
    )

    if build and not config.get("Debug"):
        raise SystemExit(
            f"Smoke needs Debug enabled to force {build!r} via the "
            f"{TEST_CYCLE_ID} cycle (omit --no-debug)."
        )

    return SmokeSettings(
        race=race,
        debug=bool(config.get("Debug")),
        build=build,
        bot_name=config.get(MY_BOT_NAME, "KauKauBot"),
        config=config,
    )


def build_smoke_bot(validate: bool, settings: SmokeSettings) -> Any:
    """Bot instance with smoke overrides patched in before opening selection."""
    bot = build_bot_ai(validate)
    apply_config_overrides(
        bot.config,
        debug=settings.debug,
        build=settings.build,
        race_name=settings.race.name,
    )

    original_on_start = bot.on_start

    async def on_start_with_overrides() -> None:
        # *_builds.yml merges in on_before_start; re-apply before DataManager.
        apply_config_overrides(
            bot.config,
            debug=settings.debug,
            build=settings.build,
            race_name=settings.race.name,
        )
        if settings.build:
            logger.info(
                f"Smoke overrides: Debug={bot.config.get('Debug')} "
                f"UseData={bot.config.get('UseData')} "
                f"build={settings.build!r}"
            )
        await original_on_start()

    bot.on_start = on_start_with_overrides  # type: ignore[method-assign]
    return bot


def add_smoke_override_args(
    parser: argparse.ArgumentParser, *, default_build: str
) -> None:
    parser.add_argument(
        "--race",
        type=str,
        default=None,
        help=f"Bot race (default: config.yml {MY_BOT_RACE}).",
    )
    parser.add_argument(
        "--debug",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Force Debug on/off (default: on when forcing a build, else config).",
    )
    parser.add_argument(
        "--build",
        type=str,
        default=None,
        metavar="NAME",
        help=(
            f"Opening to force via {TEST_CYCLE_ID} cycle "
            f"(default: {default_build!r}; pass empty string to skip)."
        ),
    )


def add_common_smoke_args(parser: argparse.ArgumentParser) -> None:
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
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Force realtime on/off (default: LocalGame.Realtime).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="RNG seed for map picks (and anything else random here).",
    )


def play_one(
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
    settings: SmokeSettings,
    build_label: str,
) -> GameOutcome:
    logger.info(
        f"===== SMOKE {build_label} vs {opponent.name} {difficulty.name} "
        f"on {map_name} ====="
    )
    try:
        result = run_game(
            resolve_map(map_name, maps_path),
            [
                Bot(race, build_smoke_bot(validate, settings), bot_name),
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


def print_summary(outcomes: list[GameOutcome]) -> None:
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


def run_smoke(
    *,
    build_name: str,
    default_race: str,
    argv: list[str] | None = None,
) -> int:
    """Entry point shared by the per-build smoke scripts."""
    parser = argparse.ArgumentParser(
        description=f"Smoke-test {build_name} vs all three races on random maps."
    )
    add_smoke_override_args(parser, default_build=build_name)
    add_common_smoke_args(parser)
    args = parser.parse_args(argv)

    if args.seed is not None:
        random.seed(args.seed)

    config = load_config()
    settings = resolve_smoke_settings(
        config,
        race_arg=args.race,
        debug_arg=args.debug,
        build_arg=args.build,
        default_race=default_race,
        default_build=build_name,
    )
    if (
        settings.build
        and settings.race.name.lower() != default_race.lower()
    ):
        raise SystemExit(
            f"Race {settings.race.name} cannot load {settings.build!r} "
            f"(need {default_race}). Pass --race {default_race}, or change "
            f"MyBotRace in config.yml."
        )
    local_cfg = config.get(LOCAL_GAME) or {}

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
    map_picks = pick_maps(resolve_map_list(local_cfg), len(OPPONENT_RACES))

    label = settings.build or build_name
    outcomes: list[GameOutcome] = []
    for opponent, map_name in zip(OPPONENT_RACES, map_picks, strict=True):
        outcomes.append(
            play_one(
                bot_name=settings.bot_name,
                race=settings.race,
                opponent=opponent,
                map_name=map_name,
                maps_path=maps_path,
                difficulty=difficulty,
                validate=args.validate,
                realtime=realtime,
                time_limit=args.time_limit,
                settings=settings,
                build_label=label,
            )
        )

    print_summary(outcomes)
    return 1 if any(not o.ok for o in outcomes) else 0
