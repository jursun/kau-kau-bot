"""21-game Chargelot regression: Medium × 3 races × 7 maps, Tie at 7:00.

Collects scout/adept combat stats, Stalker-before-Robo, Prism, warpgate peak,
Prism-field warps, and max mineral/gas float after the natural Nexus.

Usage (repo root):

    python scripts/regression_2base_chargelot.py
    python scripts/regression_2base_chargelot.py --maps LeyLinesAIE_v3 --races Terran

Writes CSV + summary under reports/chargelot_regression/<timestamp>/.
Exit 0 when every game finishes (crashes fail the suite; W/L ignored).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    run_local_game,
)
from scripts.smoke_common import (  # noqa: E402
    build_smoke_bot,
    resolve_smoke_settings,
)

BUILD_NAME = "2base Chargelot All-In"
DEFAULT_RACE = "Protoss"
TIME_LIMIT = 7 * 60

# Ladder / placement map set (AIE versions used locally).
CHARGELOT_MAPS: tuple[str, ...] = (
    "IncorporealAIE_v4",
    "LeyLinesAIE_v3",
    "MagannathaAIE_v2",
    "PersephoneAIE_v4",
    "PylonAIE_v4",
    "TorchesAIE_v4",
    "UltraloveAIE_v2",
)

CSV_FIELDS: tuple[str, ...] = (
    "opponent",
    "map",
    "result",
    "error",
    "scout_damage_dealt",
    "scout_damage_taken",
    "scout_kills",
    "scout_probe_alive_at_200",
    "adept_damage_dealt",
    "adept_damage_taken",
    "adept_kills",
    "adept_produced",
    "adept_died",
    "adept_shade_aborts",
    "adept_alive_at_430",
    "charge_complete_time",
    "charge_by_545",
    "warpgate_complete_time",
    "warpgate_by_545",
    "stalkers_before_robo",
    "time_second_stalker",
    "time_robo_started",
    "stalkers_trained",
    "stalkers_2_by_400",
    "zealots_trained",
    "time_first_zealot",
    "zealot1_by_400",
    "time_8_zealots",
    "zealots_8_by_515",
    "prism_produced",
    "time_prism",
    "time_prism_phased",
    "time_prism_completed",
    "prism_by_515",
    "observer_produced",
    "time_observer",
    "observer_by_545",
    "muster_commit_time",
    "warpgate_peak",
    "prism_warps",
    "pylon_warps",
    "auto_supply_block_frames",
    "max_minerals_after_nat",
    "max_gas_after_nat",
    "nat_nexus_ready",
    "elapsed_wall_s",
)


@dataclass
class GameRow:
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
    settings,
    opponent: Race,
    map_name: str,
    maps_path: str | None,
    difficulty: Difficulty,
    local_cfg: dict | None = None,
) -> GameRow:
    logger.info(
        f"===== REGRESSION {BUILD_NAME} vs {opponent.name} "
        f"{difficulty.name} on {map_name} ====="
    )
    bot = build_smoke_bot(validate=True, settings=settings)
    metrics_box: dict[str, Any] = {}

    original_on_end = bot.on_end

    async def on_end_capture(game_result: Result) -> None:
        await original_on_end(game_result)
        snap = getattr(bot, "chargelot_regression_metrics", None)
        if isinstance(snap, dict):
            metrics_box.update(snap)

    bot.on_end = on_end_capture  # type: ignore[method-assign]

    t0 = time.perf_counter()
    try:
        result = run_local_game(
            resolve_map(map_name, maps_path),
            [
                Bot(settings.race, bot, settings.bot_name),
                Computer(opponent, difficulty),
            ],
            realtime=False,
            game_time_limit=TIME_LIMIT,
            local_cfg=local_cfg,
        )
        elapsed = time.perf_counter() - t0
        return GameRow(
            opponent=opponent.name,
            map_name=map_name,
            result=result.name if result is not None else "UNKNOWN",
            metrics=dict(metrics_box),
            elapsed_wall_s=round(elapsed, 1),
        )
    except Exception as error:  # noqa: BLE001
        elapsed = time.perf_counter() - t0
        logger.error(f"REGRESSION crashed vs {opponent.name} on {map_name}: {error}")
        return GameRow(
            opponent=opponent.name,
            map_name=map_name,
            result="CRASH",
            error=f"{type(error).__name__}: {error}",
            metrics=dict(metrics_box),
            elapsed_wall_s=round(elapsed, 1),
        )


def _row_dict(row: GameRow) -> dict[str, Any]:
    out: dict[str, Any] = {
        "opponent": row.opponent,
        "map": row.map_name,
        "result": row.result,
        "error": row.error or "",
        "elapsed_wall_s": row.elapsed_wall_s,
    }
    for key in CSV_FIELDS:
        if key in out:
            continue
        out[key] = row.metrics.get(key, "")
    return out


def _write_reports(out_dir: Path, rows: list[GameRow]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "results.csv"
    json_path = out_dir / "results.json"
    summary_path = out_dir / "summary.txt"

    dicts = [_row_dict(r) for r in rows]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CSV_FIELDS))
        writer.writeheader()
        for d in dicts:
            writer.writerow({k: d.get(k, "") for k in CSV_FIELDS})

    json_path.write_text(json.dumps(dicts, indent=2), encoding="utf-8")

    crashes = sum(1 for r in rows if not r.ok)
    s2_ok = sum(1 for r in rows if r.metrics.get("stalkers_before_robo") is True)
    s2_fail = sum(1 for r in rows if r.metrics.get("stalkers_before_robo") is False)
    s2_unk = sum(1 for r in rows if r.metrics.get("stalkers_before_robo") is None)
    prism_ok = sum(1 for r in rows if r.metrics.get("prism_produced") is True)
    gates = [r.metrics.get("warpgate_peak") for r in rows if r.ok]
    warps = [r.metrics.get("prism_warps") or 0 for r in rows if r.ok]
    floats_m = [r.metrics.get("max_minerals_after_nat") or 0 for r in rows if r.ok]
    floats_g = [r.metrics.get("max_gas_after_nat") or 0 for r in rows if r.ok]

    lines = [
        f"Games: {len(rows)}  crashes: {crashes}",
        f"Stalkers before Robo: pass={s2_ok} fail={s2_fail} unknown={s2_unk}",
        f"Prism produced: {prism_ok}/{len(rows)}",
    ]
    if gates:
        lines.append(
            f"Warpgate peak: min={min(gates)} max={max(gates)} "
            f"avg={sum(gates) / len(gates):.1f}"
        )
    else:
        lines.append("Warpgate peak: n/a")
    lines.append(
        f"Prism warps: min={min(warps) if warps else 0} "
        f"max={max(warps) if warps else 0} sum={sum(warps)}"
    )
    if floats_m:
        lines.append(
            f"Max float after nat: minerals avg={sum(floats_m) / len(floats_m):.0f}"
        )
        lines.append(
            f"Max float after nat: gas avg={sum(floats_g) / len(floats_g):.0f}"
        )
    lines.extend(["", "Per game:"])
    for r in rows:
        m = r.metrics
        lines.append(
            f"  {r.opponent:<8} {r.map_name:<22} {r.result:<8} "
            f"scout {m.get('scout_damage_dealt', 0)}/"
            f"{m.get('scout_damage_taken', 0)}/k{m.get('scout_kills', 0)} "
            f"adept {m.get('adept_damage_dealt', 0)}/"
            f"{m.get('adept_damage_taken', 0)}/k{m.get('adept_kills', 0)} "
            f"s2@{m.get('time_second_stalker')} "
            f"robo@{m.get('time_robo_started')} "
            f"before_robo={m.get('stalkers_before_robo')} "
            f"prism={m.get('prism_produced')}@{m.get('time_prism')} "
            f"phased@{m.get('time_prism_phased')} "
            f"gates={m.get('warpgate_peak')} "
            f"warps={m.get('prism_warps')}/pylon={m.get('pylon_warps')} "
            f"supply_blk={m.get('auto_supply_block_frames')} "
            f"float={m.get('max_minerals_after_nat')}/"
            f"{m.get('max_gas_after_nat')}"
            + (f" ERR={r.error}" if r.error else "")
        )
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(f"Wrote {csv_path}")
    logger.info(f"Wrote {summary_path}")
    for line in lines:
        logger.info(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--difficulty",
        default="Medium",
        help="Computer difficulty (default Medium).",
    )
    parser.add_argument(
        "--maps",
        nargs="*",
        default=None,
        help=f"Map names (default: all {len(CHARGELOT_MAPS)}).",
    )
    parser.add_argument(
        "--races",
        nargs="*",
        default=None,
        help="Opponent races (default: Terran Zerg Protoss).",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Output directory (default reports/chargelot_regression/<ts>).",
    )
    args = parser.parse_args(argv)

    try:
        difficulty = Difficulty[args.difficulty]
    except KeyError:
        raise SystemExit(f"Unknown difficulty {args.difficulty!r}") from None

    maps = tuple(args.maps) if args.maps else CHARGELOT_MAPS
    if args.races:
        races = tuple(Race[r.title()] for r in args.races)
    else:
        races = (Race.Terran, Race.Zerg, Race.Protoss)

    config = load_config()
    settings = resolve_smoke_settings(
        config,
        race_arg="Protoss",
        debug_arg=True,
        build_arg=BUILD_NAME,
        default_race=DEFAULT_RACE,
        default_build=BUILD_NAME,
    )
    local_cfg = local_game_cfg_for_testing(config)
    maps_path = local_cfg.get("MapPath")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) if args.out else (
        ROOT / "reports" / "chargelot_regression" / stamp
    )

    rows: list[GameRow] = []
    total = len(maps) * len(races)
    n = 0
    for map_name in maps:
        for opponent in races:
            n += 1
            logger.info(f"--- game {n}/{total} ---")
            rows.append(
                _play_one(
                    settings=settings,
                    opponent=opponent,
                    map_name=map_name,
                    maps_path=maps_path,
                    difficulty=difficulty,
                    local_cfg=local_cfg,
                )
            )

    _write_reports(out_dir, rows)
    crashes = sum(1 for r in rows if not r.ok)
    return 1 if crashes else 0


if __name__ == "__main__":
    raise SystemExit(main())
