"""Apply kau-kau-bot's ares-sc2 patches after a clean submodule checkout.

Pin is upstream v3.13.1 (87308658). Patches live in patches/ares-sc2/ and cover
nat-wall FirstPylon preference, pylon+Prism warp power by spawn distance,
spreading multi-Gate warp placements around the power source, counting
pending (still-morphing) workers against ConstantWorkerProductionTill, and
preferring the main hatchery for TechUp Lair/Hive morphs.

Usage (from repo root, after `git submodule update --init --recursive`):

    python scripts/apply_ares_patches.py

Idempotent: exits 0 if patches are already applied. Re-run after bumping ares
(may need refreshed patches if upstream files moved).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ARES_DIR = REPO_ROOT / "ares-sc2"
PATCH_DIR = REPO_ROOT / "patches" / "ares-sc2"
# Official pin recorded in the parent repo (v3.13.1).
EXPECTED_PIN = "87308658b0dfe1e59486c2b157ef552e9af0c7fd"


def _git() -> str:
    return "git.exe" if sys.platform == "win32" else "git"


def _run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ARES_DIR,
        check=check,
        text=True,
        capture_output=True,
    )


def _die(msg: str) -> None:
    print(f"apply_ares_patches: {msg}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    git = _git()
    if not (ARES_DIR / "src" / "ares").is_dir():
        _die(f"ares-sc2 missing at {ARES_DIR}; run git submodule update --init")

    if not PATCH_DIR.is_dir():
        _die(f"patch dir missing: {PATCH_DIR}")

    patches = sorted(PATCH_DIR.glob("*.patch"))
    if not patches:
        _die(f"no *.patch files in {PATCH_DIR}")

    try:
        head = _run([git, "rev-parse", "HEAD"]).stdout.strip()
    except subprocess.CalledProcessError as exc:
        _die(f"cannot read ares-sc2 HEAD: {exc.stderr or exc}")

    if head != EXPECTED_PIN:
        _die(
            f"ares-sc2 HEAD is {head[:12]}, expected pin {EXPECTED_PIN[:12]}. "
            "Reset the submodule first: git submodule update --init"
        )

    # Already applied? Reverse-check of the last patch succeeds.
    last = patches[-1]
    reverse_check = _run(
        [git, "apply", "--reverse", "--check", str(last)],
        check=False,
    )
    if reverse_check.returncode == 0:
        print("apply_ares_patches: already applied")
        return

    for patch in patches:
        check = _run([git, "apply", "--check", str(patch)], check=False)
        if check.returncode != 0:
            _die(
                f"cannot apply {patch.name}:\n"
                f"{check.stderr or check.stdout or '(no output)'}"
            )
        applied = _run([git, "apply", str(patch)], check=False)
        if applied.returncode != 0:
            _die(
                f"failed applying {patch.name}:\n"
                f"{applied.stderr or applied.stdout or '(no output)'}"
            )
        print(f"apply_ares_patches: applied {patch.name}")

    print("apply_ares_patches: done")


if __name__ == "__main__":
    main()
