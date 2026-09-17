"""KauKauBot entry point.

  python run.py                 # local game vs the built-in computer (no time limit)
  python run.py --validate      # local game + rush milestone report at game end
  python run.py --leave 420     # optional artificial Tie at N game-seconds
  python run.py --LadderServer  # invoked by AI Arena's LadderManager

Harness scripts (`scripts/harness/*_tier.py`) also omit a limit unless
you pass `--leave N` (e.g. `--leave 420` for a 7:00 cut). Ladder never
passes a time limit.

Local settings live under `LocalGame:` in config.yml. Bot name / race come
from `MyBotName` / `MyBotRace` in the same file, which is also what
`scripts/create_ladder_zip.py` reads.
"""

import argparse
import platform
import random
import sys
from contextlib import contextmanager
from os import path
from pathlib import Path
from typing import Iterator

# Repo root must precede ares-sc2 on sys.path. Poetry's .pth adds ares-sc2
# before the project root, which makes `import tests` resolve to
# ares-sc2/tests instead of this repo's validators package.
_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

# ares-sc2 is vendored as a git submodule; it must be importable before
# anything reaches for `sc2` or `ares`.
sys.path.append(str(_ROOT / "ares-sc2" / "src" / "ares"))
sys.path.append(str(_ROOT / "ares-sc2" / "src"))
sys.path.append(str(_ROOT / "ares-sc2"))

import yaml  # noqa: E402
from ladder import run_ladder_game  # noqa: E402
from loguru import logger  # noqa: E402
from sc2 import maps  # noqa: E402
from sc2.data import Difficulty, Race  # noqa: E402
from sc2.main import run_game  # noqa: E402
from sc2.maps import Map  # noqa: E402
from sc2.player import Bot, Computer  # noqa: E402
from sc2.sc2process import SC2Process  # noqa: E402

from bot.main import KauKauBot  # noqa: E402

CONFIG_FILE: str = "config.yml"
MAP_FILE_EXT: str = "SC2Map"
MY_BOT_NAME: str = "MyBotName"
MY_BOT_RACE: str = "MyBotRace"
LOCAL_GAME: str = "LocalGame"

# Tiny corner client for --no-realtime. python-sc2's run_game() does not
# expose SC2Process resolution/placement, so we patch those for the launch.
FAST_WINDOW_SIZE: tuple[int, int] = (256, 256)
FAST_WINDOW_POS: tuple[int, int] = (0, 0)

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
    """Return a bot instance, optionally with a build-specific validator
    attached."""
    if not validate:
        return KauKauBot()

    from tests.validators.registry import validator_for_build

    class ValidatedKauKauBot(KauKauBot):
        """KauKauBot with a build-specific milestone validator attached.

        The validator can't be picked until `KauKauBot.on_start` sets
        `self.ctx.build.name` (ares picks the opening at runtime), so it's
        constructed fresh in `on_start` via `validator_for_build` and driven
        explicitly from the hooks below - composition, not inheritance.
        """

        validator = None
        validation_metrics: dict = {}
        """Populated at game end from `self.validator.metrics_snapshot()` -
        every check across every stage, flattened to a bare pass/fail bool
        per column. In principle lets `scripts/harness/harness_common.py`'s
        `--metrics validation_metrics` capture validator pass/fail across a
        whole tier run (many games) the same way `--metrics
        chargelot_regression_metrics` does for that build's own metrics.

        Not the recommended way to check a build's timing, and not actively
        used or maintained - a bare bool per check drops the actual
        started-at/deadline detail the printed report carries, so a "3s
        later than before" regression looks identical to "never happened".
        Use `scripts/harness/*_tier.py --validate` on its own and read the
        printed report at game end instead of wiring a `--metrics
        validation_metrics` consumer onto this."""

        async def on_start(self) -> None:
            await KauKauBot.on_start(self)
            validator_cls = validator_for_build(self.ctx.build.name)
            self.validator = validator_cls(self)

        async def on_step(self, iteration: int) -> None:
            self.validator.on_step(iteration)
            await KauKauBot.on_step(self, iteration)

        async def on_end(self, game_result) -> None:
            self.validator.on_end()
            self.validation_metrics = self.validator.metrics_snapshot()
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


def resolve_fast_window(
    local_cfg: dict | None, realtime: bool
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Corner-window size/pos for stepped games, or None to leave SC2 defaults.

    Realtime stays full-size so the game is watchable. Disable with
    `LocalGame.FastWindow: False`. Size/pos: WindowWidth/Height/X/Y.
    """
    if realtime:
        return None
    cfg = local_cfg or {}
    if not cfg.get("FastWindow", True):
        return None
    size = (
        int(cfg.get("WindowWidth", FAST_WINDOW_SIZE[0])),
        int(cfg.get("WindowHeight", FAST_WINDOW_SIZE[1])),
    )
    pos = (
        int(cfg.get("WindowX", FAST_WINDOW_POS[0])),
        int(cfg.get("WindowY", FAST_WINDOW_POS[1])),
    )
    return size, pos


def local_game_cfg_for_testing(config: dict | None = None) -> dict:
    """`LocalGame` settings for smoke/regression with FastWindow forced on."""
    cfg = dict((config or load_config()).get(LOCAL_GAME) or {})
    cfg["FastWindow"] = True
    return cfg


@contextmanager
def _patched_sc2_window(
    resolution: tuple[int, int], placement: tuple[int, int]
) -> Iterator[None]:
    orig_init = SC2Process.__init__

    def _init(self, *args, **kwargs):
        kwargs["resolution"] = resolution
        kwargs["placement"] = placement
        orig_init(self, *args, **kwargs)

    SC2Process.__init__ = _init  # type: ignore[method-assign]
    try:
        yield
    finally:
        SC2Process.__init__ = orig_init


def run_local_game(
    map_settings,
    players,
    *,
    realtime: bool,
    game_time_limit: int | None = None,
    local_cfg: dict | None = None,
    **run_game_kwargs,
):
    """`run_game` with a tiny corner window when not realtime."""
    window = resolve_fast_window(local_cfg, realtime)
    if window is None:
        return run_game(
            map_settings,
            players,
            realtime=realtime,
            game_time_limit=game_time_limit,
            **run_game_kwargs,
        )
    resolution, placement = window
    logger.info(
        f"SC2 fast window {resolution[0]}x{resolution[1]} at {placement} "
        "(non-realtime). Set LocalGame.FastWindow: False for a normal window."
    )
    with _patched_sc2_window(resolution, placement):
        return run_game(
            map_settings,
            players,
            realtime=realtime,
            game_time_limit=game_time_limit,
            **run_game_kwargs,
        )


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
    parser.add_argument(
        "--leave",
        type=int,
        default=None,
        metavar="SECONDS",
        help=(
            "Artificial Tie after N game-seconds. Omit for no time limit "
            "(same for harness scripts; pass --leave 420 for a 7:00 cut)."
        ),
    )
    args, _unknown = parser.parse_known_args()

    config: dict = load_config()
    local_cfg: dict = config.get(LOCAL_GAME) or {}

    bot_name: str = config.get(MY_BOT_NAME, "KauKauBot")
    race: Race = Race[config.get(MY_BOT_RACE, "Zerg").title()]

    is_ladder = "--LadderServer" in sys.argv
    bot = Bot(race, build_bot_ai(args.validate), bot_name)

    if is_ladder:
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
    if args.leave is not None:
        logger.info(
            f"Local game time limit ENABLED - end at {args.leave}s game time."
        )
    else:
        logger.info("Local game time limit OFF - play until the game ends.")
    run_local_game(
        map_obj,
        [bot, Computer(opponent_race, difficulty)],
        realtime=realtime,
        game_time_limit=args.leave,
        local_cfg=local_cfg,
    )


if __name__ == "__main__":
    main()
