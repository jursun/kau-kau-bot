"""Shared engine for the local test harness.

Four fixed tiers, run via their own thin wrapper scripts:

    Quick      1 game, random map/race, Medium  — python scripts/quick_tier.py
    Smoke      1 map per race (3 games), Easy   — python scripts/smoke_tier.py
    Sanity     1 race per map (7 games), Medium — python scripts/sanity_tier.py
    Regression every map x every race (21), Hard — python scripts/regression_tier.py

Each takes the same parameters:

    --build NAME       Opening to force, e.g. "2base Chargelot All-In".
                        The opponent race is resolved automatically from
                        this build's own BuildDefinition — no separate
                        --race flag to keep in sync.
    --metrics ATTR      Bot attribute to snapshot as this build's custom
                        metrics after each game (e.g.
                        "chargelot_regression_metrics" — see
                        bot/main.py's on_end). Omit to skip; only the
                        crash/result columns are written.
    --leave SECONDS     Leave at this game time (game_time_limit). Defaults
                        to 420 (7:00), matching bot/main.py's
                        LOCAL_GAME_TIME_LIMIT_SECONDS team-policy default —
                        passing something larger is a deliberate call, not
                        a silent extension of that policy.
    --map NAME          Force a specific map instead of a tier's random
                        pick(s) — targeted testing (e.g. Quick against one
                        known-tricky map).
    --opponent RACE     Force a specific opponent race (Terran/Zerg/Protoss)
                        instead of a tier's random/all pick — targeted
                        testing (e.g. Quick against one known-tricky race).

Always stepped (Realtime: False) with the FastWindow corner client
(FastWindow: True) for the quickest possible execution — there is no
`--realtime` escape hatch here; watch a game via `run.py` directly instead.

Reuses `scripts/smoke_common.py` for opening-forcing (the Debug/test_123
cycle dance) and `run.py` for actual game execution, so none of that
plumbing is duplicated here.

Results land under reports/test_harness/<tier>/<build-slug>/<timestamp>/
as results.csv, results.json, and summary.txt. Exit 0 iff no game crashed
— a loss is a pass, same convention as the existing smoke/regression
scripts (see their docstrings).
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loguru import logger  # noqa: E402
from sc2.data import Difficulty, Race, Result  # noqa: E402
from sc2.player import Bot, Computer  # noqa: E402

from run import (  # noqa: E402
    load_config,
    local_game_cfg_for_testing,
    resolve_map,
    resolve_map_list,
    run_local_game,
)
from scripts.smoke_common import (  # noqa: E402
    SmokeSettings,
    build_smoke_bot,
    resolve_smoke_settings,
)

DEFAULT_LEAVE_SECONDS: int = 7 * 60
"""bot/main.py's LOCAL_GAME_TIME_LIMIT_SECONDS (7:00 team policy)."""

ALL_RACES: tuple[Race, ...] = (Race.Terran, Race.Zerg, Race.Protoss)

MapStrategy = Literal["random", "all"]
OpponentStrategy = Literal["all", "random"]


@dataclass(frozen=True)
class TierSpec:
    name: str
    map_strategy: MapStrategy
    opponent_strategy: OpponentStrategy
    difficulty: Difficulty
    expected_games: int
    """What this tier *should* total against the current 7-map
    LocalGame.MapPool — informational. The real count is always derived
    from the live pool + race list, so a map-pool change scales the run
    instead of silently going stale; a mismatch is logged, not fatal."""
    races: tuple[Race, ...] | None = None
    """Opponent race pool override; `None` means the usual `ALL_RACES`.
    Lets a tier pin its random opponent pick to fewer races than
    `ALL_RACES` — see `run_tier`'s `--opponent` override for the CLI side
    of the same knob."""


QUICK = TierSpec("quick", "random", "random", Difficulty.Medium, 1)
"""One quick game (random map, random race, Medium) - for iterating on a
change before spending the time on a full smoke/sanity/regression run.
`--map`/`--opponent`/`--difficulty` narrow it to targeted testing (e.g. one
known-tricky map or race) without losing the fast, 1-game default. See
`quick_tier.py`."""
SMOKE = TierSpec("smoke", "random", "all", Difficulty.Easy, len(ALL_RACES))
SANITY = TierSpec("sanity", "all", "random", Difficulty.Medium, 7)
REGRESSION = TierSpec("regression", "all", "all", Difficulty.Hard, 7 * len(ALL_RACES))


def _pick_distinct(pool: list[str], count: int, rng: random.Random) -> list[str]:
    """One map per slot; prefer unique maps when the pool is large enough."""
    if len(pool) >= count:
        return rng.sample(pool, count)
    return [rng.choice(pool) for _ in range(count)]


def build_game_plan(
    tier: TierSpec,
    maps: list[str],
    races: tuple[Race, ...],
    rng: random.Random,
) -> list[tuple[str, Race]]:
    """(map, opponent) pairs for one tier run. Pure — no I/O, easy to test."""
    if not maps:
        raise ValueError("no maps available")
    if not races:
        raise ValueError("no opponent races given")

    if tier.map_strategy == "random" and tier.opponent_strategy == "all":
        picks = _pick_distinct(maps, len(races), rng)
        return list(zip(picks, races, strict=True))

    if tier.map_strategy == "random" and tier.opponent_strategy == "random":
        picks = _pick_distinct(maps, tier.expected_games, rng)
        return [(m, rng.choice(races)) for m in picks]

    if tier.map_strategy == "all" and tier.opponent_strategy == "random":
        return [(m, rng.choice(races)) for m in maps]

    if tier.map_strategy == "all" and tier.opponent_strategy == "all":
        return [(m, r) for m in maps for r in races]

    raise ValueError(
        f"unsupported strategy combo: map={tier.map_strategy!r} "
        f"opponent={tier.opponent_strategy!r}"
    )


def resolve_build_race(build_name: str) -> Race:
    """Which race a build belongs to, via the auto-discovered registry.

    Lets `--build` stand alone — no separate `--race` flag that can drift
    out of sync with the build it's supposedly naming.
    """
    from bot.core.registry import all_builds

    builds = all_builds()
    if build_name in builds:
        return builds[build_name].race

    lowered = build_name.lower()
    for name, build in builds.items():
        if name.lower() == lowered:
            return build.race

    available = sorted(builds)
    raise SystemExit(f"Unknown build {build_name!r}. Known builds: {available}")


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text).strip("_").lower()


@dataclass
class GameRow:
    tier: str
    opponent: str
    map_name: str
    result: str
    error: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    elapsed_wall_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None


def _play_one(
    *,
    tier: TierSpec,
    settings: SmokeSettings,
    opponent: Race,
    map_name: str,
    maps_path: str | None,
    validate: bool,
    metrics_attr: str | None,
    leave: int | None,
    local_cfg: dict | None,
) -> GameRow:
    logger.info(
        f"===== {tier.name.upper()} {settings.build or '(no build forced)'} "
        f"vs {opponent.name} {tier.difficulty.name} on {map_name} ====="
    )
    bot = build_smoke_bot(validate, settings)
    metrics_box: dict[str, Any] = {}

    if metrics_attr:
        original_on_end = bot.on_end

        async def on_end_capture(game_result: Result) -> None:
            await original_on_end(game_result)
            snap = getattr(bot, metrics_attr, None)
            if isinstance(snap, dict):
                metrics_box.update(snap)

        bot.on_end = on_end_capture  # type: ignore[method-assign]

    t0 = time.perf_counter()
    try:
        result = run_local_game(
            resolve_map(map_name, maps_path),
            [
                Bot(settings.race, bot, settings.bot_name),
                Computer(opponent, tier.difficulty),
            ],
            realtime=False,
            game_time_limit=leave,
            local_cfg=local_cfg,
        )
        elapsed = time.perf_counter() - t0
        return GameRow(
            tier=tier.name,
            opponent=opponent.name,
            map_name=map_name,
            result=result.name if result is not None else "UNKNOWN",
            metrics=dict(metrics_box),
            elapsed_wall_s=round(elapsed, 1),
        )
    except Exception as error:  # noqa: BLE001 - keep going, report at the end
        elapsed = time.perf_counter() - t0
        logger.error(
            f"{tier.name.upper()} crashed vs {opponent.name} on {map_name}: {error}"
        )
        return GameRow(
            tier=tier.name,
            opponent=opponent.name,
            map_name=map_name,
            result="CRASH",
            error=f"{type(error).__name__}: {error}",
            metrics=dict(metrics_box),
            elapsed_wall_s=round(elapsed, 1),
        )


BASE_FIELDS: tuple[str, ...] = ("opponent", "map", "result", "error", "elapsed_wall_s")


def _row_dict(row: GameRow) -> dict[str, Any]:
    out: dict[str, Any] = {
        "opponent": row.opponent,
        "map": row.map_name,
        "result": row.result,
        "error": row.error or "",
        "elapsed_wall_s": row.elapsed_wall_s,
    }
    out.update(row.metrics)
    return out


def discover_fields(rows: list[GameRow]) -> list[str]:
    """Base columns plus the union of every metrics key seen, in first-seen
    order — works for any build's metrics shape, not just Chargelot's."""
    metric_keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row.metrics:
            if key not in seen:
                seen.add(key)
                metric_keys.append(key)
    return [*BASE_FIELDS, *metric_keys]


def write_reports(
    out_dir: Path, tier: TierSpec, build_name: str, rows: list[GameRow]
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fields = discover_fields(rows)
    dicts = [_row_dict(r) for r in rows]

    csv_path = out_dir / "results.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for d in dicts:
            writer.writerow({k: d.get(k, "") for k in fields})

    json_path = out_dir / "results.json"
    json_path.write_text(json.dumps(dicts, indent=2), encoding="utf-8")

    crashes = sum(1 for r in rows if not r.ok)
    lines = [
        f"{tier.name.upper()} - build={build_name!r} difficulty={tier.difficulty.name}",
        f"Games: {len(rows)}  crashes: {crashes}",
        "Pass = no crashes (losses are fine).",
        "",
        "Per game:",
    ]
    for r in rows:
        status = f"CRASH ({r.error})" if r.error else r.result
        extra = " ".join(f"{k}={v}" for k, v in r.metrics.items())
        lines.append(
            f"  vs {r.opponent:<8} on {r.map_name:<22} {status}"
            + (f"  {extra}" if extra else "")
        )

    summary_path = out_dir / "summary.txt"
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    logger.info(f"Wrote {csv_path}")
    logger.info(f"Wrote {summary_path}")
    for line in lines:
        logger.info(line)


def add_tier_args(parser: argparse.ArgumentParser, tier: TierSpec) -> None:
    parser.add_argument(
        "--build",
        required=True,
        metavar="NAME",
        help='Opening to force, e.g. "2base Chargelot All-In".',
    )
    parser.add_argument(
        "--metrics",
        default=None,
        metavar="ATTR",
        help=(
            "Bot attribute to snapshot as this build's custom metrics after "
            'each game (e.g. "chargelot_regression_metrics"). Omit to skip.'
        ),
    )
    parser.add_argument(
        "--leave",
        type=int,
        default=DEFAULT_LEAVE_SECONDS,
        metavar="SECONDS",
        help=(
            f"Leave at this game time (default {DEFAULT_LEAVE_SECONDS}s / "
            "7:00 - team policy; pass a larger value deliberately to test "
            "past it)."
        ),
    )
    parser.add_argument(
        "--difficulty",
        default=None,
        help=f"Override this tier's default ({tier.difficulty.name}).",
    )
    parser.add_argument(
        "--map",
        default=None,
        metavar="NAME",
        help=(
            "Force a specific map instead of this tier's random pick(s) — "
            "targeted testing."
        ),
    )
    parser.add_argument(
        "--opponent",
        default=None,
        metavar="RACE",
        choices=[r.name for r in ALL_RACES],
        help=(
            "Force a specific opponent race instead of this tier's "
            "random/all pick — targeted testing."
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Attach the build's rush validator (report at each game end).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="RNG seed for map/opponent picks.",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Output directory (default reports/test_harness/<tier>/<build>/<ts>).",
    )


def run_tier(tier: TierSpec, argv: list[str] | None = None) -> int:
    """Entry point shared by the three tier wrapper scripts."""
    parser = argparse.ArgumentParser(
        description=(
            f"{tier.name.capitalize()} test: map={tier.map_strategy}, "
            f"opponent={tier.opponent_strategy}, difficulty={tier.difficulty.name}, "
            f"~{tier.expected_games} games."
        )
    )
    add_tier_args(parser, tier)
    args = parser.parse_args(argv)

    if args.difficulty:
        try:
            tier = replace(tier, difficulty=Difficulty[args.difficulty])
        except KeyError:
            raise SystemExit(f"Unknown difficulty {args.difficulty!r}") from None

    rng = random.Random(args.seed)
    race = resolve_build_race(args.build)

    config = load_config()
    settings = resolve_smoke_settings(
        config,
        race_arg=race.name,
        debug_arg=None,
        build_arg=args.build,
        default_race=race.name,
        default_build=args.build,
    )
    local_cfg = local_game_cfg_for_testing(config)
    maps_path = local_cfg.get("MapPath")
    maps = [args.map] if args.map else resolve_map_list(local_cfg)
    races = (Race[args.opponent],) if args.opponent else (tier.races or ALL_RACES)

    plan = build_game_plan(tier, maps, races, rng)
    if len(plan) != tier.expected_games:
        logger.warning(
            f"{tier.name}: computed {len(plan)} games from a "
            f"{len(maps)}-map pool (spec says {tier.expected_games}) — "
            "the map pool has changed size since the spec was written."
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) if args.out else (
        ROOT / "reports" / "test_harness" / tier.name / _slug(args.build) / stamp
    )

    rows: list[GameRow] = []
    for n, (map_name, opponent) in enumerate(plan, start=1):
        logger.info(f"--- {tier.name} game {n}/{len(plan)} ---")
        rows.append(
            _play_one(
                tier=tier,
                settings=settings,
                opponent=opponent,
                map_name=map_name,
                maps_path=maps_path,
                validate=args.validate,
                metrics_attr=args.metrics,
                leave=args.leave,
                local_cfg=local_cfg,
            )
        )

    write_reports(out_dir, tier, args.build, rows)
    return 1 if any(not r.ok for r in rows) else 0
