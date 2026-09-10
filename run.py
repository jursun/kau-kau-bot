"""KauKauBot entry point.

  python run.py                 # local game vs the built-in computer
  python run.py --validate      # local game + rush milestone report at game end
  python run.py --LadderServer  # invoked by AI Arena's LadderManager

Local settings live under `LocalGame:` in config.yml. Bot name / race come
from `MyBotName` / `MyBotRace` in the same file, which is also what
`scripts/create_ladder_zip.py` reads.
"""

import argparse
import platform
import random
import sys
from os import path
from pathlib import Path

# ares-sc2 is vendored as a git submodule; it must be importable before
# anything reaches for `sc2` or `ares`.
sys.path.append("ares-sc2/src/ares")
sys.path.append("ares-sc2/src")
sys.path.append("ares-sc2")

import yaml  # noqa: E402
from loguru import logger  # noqa: E402
from sc2 import maps  # noqa: E402
from sc2.maps import Map  # noqa: E402
from sc2.data import Difficulty, Race  # noqa: E402
from sc2.main import run_game  # noqa: E402
from sc2.player import Bot, Computer  # noqa: E402

from bot.main import KauKauBot  # noqa: E402
from ladder import run_ladder_game  # noqa: E402

CONFIG_FILE: str = "config.yml"
MAP_FILE_EXT: str = "SC2Map"
MY_BOT_NAME: str = "MyBotName"
MY_BOT_RACE: str = "MyBotRace"
LOCAL_GAME: str = "LocalGame"

FALLBACK_MAPS: list[str] = [
    "PersephoneAIE_v4",
    "PylonAIE_v4",
    "TorchesAIE_v4",
]


def _default_maps_path() -> str:
    plt = platform.system()
    if plt == "Windows":
        return "C:\\Program Files (x86)\\StarCraft II\\Maps"
    if plt == "Darwin":
        return "/Applications/StarCraft II/Maps"
    if plt == "Linux":
        return path.expanduser(
            "~/Games/battlenet/drive_c/Program Files (x86)/StarCraft II/Maps"
        )
    logger.error(f"{plt} not supported")
    sys.exit(1)


def load_config() -> dict:
    user_config_path: str = path.join(path.abspath("."), CONFIG_FILE)
    if not path.isfile(user_config_path):
        logger.warning(f"No {CONFIG_FILE} found, falling back to defaults.")
        return {}
    with open(user_config_path) as config_file:
        return yaml.safe_load(config_file) or {}


def build_bot_ai(validate: bool):
    """Return a bot instance, optionally with the rush validator mixed in."""
    if not validate:
        return KauKauBot()

    from tests.upgrade_rush_validator import UpgradeRushValidator

    class ValidatedKauKauBot(UpgradeRushValidator, KauKauBot):
        """KauKauBot with the milestone validator layered on top.

        UpgradeRushValidator deliberately does not call `super().on_step`, so
        each hook is dispatched explicitly here — KauKauBot's own hooks are
        the ones that call into ares.
        """

        async def on_start(self) -> None:
            await UpgradeRushValidator.on_start(self)
            await KauKauBot.on_start(self)

        async def on_step(self, iteration: int) -> None:
            await UpgradeRushValidator.on_step(self, iteration)
            await KauKauBot.on_step(self, iteration)

        async def on_end(self, game_result) -> None:
            await UpgradeRushValidator.on_end(self, game_result)
            await KauKauBot.on_end(self, game_result)

    logger.info("Rush validation ENABLED - report prints at game end.")
    return ValidatedKauKauBot()


def resolve_map_list(local_cfg: dict) -> list[str]:
    pool = local_cfg.get("MapPool") or []
    if pool:
        return list(pool)

    maps_path: str = local_cfg.get("MapPath") or _default_maps_path()
    found = [p.stem for p in Path(maps_path).rglob(f"*.{MAP_FILE_EXT}") if p.is_file()]
    if found:
        return found

    logger.error(f"No maps found under {maps_path}; check `LocalGame.MapPath`.")
    return FALLBACK_MAPS


def resolve_map(map_name: str, maps_path: str | None):
    """Resolve a map by name.

    `sc2.maps.get()` in the python-sc2 fork ares uses only searches `Paths.MAPS`
    and takes no directory argument, so an explicit `LocalGame.MapPath` is
    handled here by building the `Map` directly.
    """
    if maps_path and path.isdir(maps_path):
        match = next(
            (
                p
                for p in Path(maps_path).rglob(f"{map_name}.{MAP_FILE_EXT}")
                if p.is_file()
            ),
            None,
        )
        if match is not None:
            return Map(match)
        logger.warning(
            f"{map_name}.{MAP_FILE_EXT} not found under {maps_path}; "
            "falling back to the default SC2 maps folder."
        )
    return maps.get(map_name)


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--map", type=str, default=None)
    parser.add_argument("--opponent-race", type=str, default=None)
    parser.add_argument("--difficulty", type=str, default=None)
    parser.add_argument("--realtime", action="store_true", default=None)
    args, _unknown = parser.parse_known_args()

    config: dict = load_config()
    local_cfg: dict = config.get(LOCAL_GAME) or {}

    bot_name: str = config.get(MY_BOT_NAME, "KauKauBot")
    race: Race = Race[config.get(MY_BOT_RACE, "Zerg").title()]

    bot = Bot(race, build_bot_ai(args.validate), bot_name)

    if "--LadderServer" in sys.argv:
        logger.info("Starting ladder game...")
        result, opponent_id = run_ladder_game(bot)
        logger.info(f"{result} against opponent {opponent_id}")
        return

    map_list = resolve_map_list(local_cfg)
    map_name: str = args.map or random.choice(map_list)

    opponent_race_name = args.opponent_race or local_cfg.get("OpponentRace", "Terran")
    difficulty_name = args.difficulty or local_cfg.get("OpponentDifficulty", "VeryHard")
    realtime: bool = (
        args.realtime if args.realtime is not None else bool(local_cfg.get("Realtime"))
    )

    try:
        opponent_race = Race[opponent_race_name.title()]
    except KeyError:
        logger.warning(f"Unknown opponent race {opponent_race_name!r}, using Terran.")
        opponent_race = Race.Terran
    try:
        difficulty = Difficulty[difficulty_name]
    except KeyError:
        logger.warning(f"Unknown difficulty {difficulty_name!r}, using VeryHard.")
        difficulty = Difficulty.VeryHard

    map_obj = resolve_map(map_name, local_cfg.get("MapPath"))

    logger.info(
        f"===== {bot_name} ({race.name}) vs {opponent_race.name} "
        f"{difficulty.name} on {map_name} ====="
    )
    run_game(
        map_obj,
        [bot, Computer(opponent_race, difficulty)],
        realtime=realtime,
    )


if __name__ == "__main__":
    main()
