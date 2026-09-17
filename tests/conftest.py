"""Puts `ares-sc2`'s vendored `ares`/`sc2` packages (and the repo root) on
`sys.path` before pytest collects anything under this directory.

`ares-sc2` is a git submodule, not an installed package - nothing makes it
importable except `run.py`'s own `sys.path.insert`/`sys.path.append` calls
at module load time. Without this file, whichever test file happened to
transitively `import run` (until now, only `test_harness_common.py`, via
`scripts.harness.harness_common`) had to be collected *first* in the same
pytest process for every other file's `ares`/`bot` imports to succeed -
confirmed live: `pytest tests/test_combat.py` alone, or a plain `pytest
tests/` with no file-order override, both fail with `ModuleNotFoundError:
No module named 'ares'`.

pytest guarantees a directory's `conftest.py` loads before any test file
under it collects, so importing `run` here - the same module every real
entry point (`run.py`, `scripts/harness/*`) already goes through for this
exact setup - fixes every test file unconditionally, regardless of run
order, `-k` filtering, or which subdirectory it lives in.
"""

import run  # noqa: F401
