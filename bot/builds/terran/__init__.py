"""Terran builds.

Drop a module here that defines a module-level
`BUILD = BuildDefinition(..., race=Race.Terran)` and the registry picks it up
automatically; also add a matching opening to `terran_builds.yml` (ares
requires that exact filename) or `tests/test_builds.py` will fail.

Running any of these needs `MyBotRace: Terran` in config.yml — a bot is one
race, so Terran and Zerg builds cannot both be live at once.
"""
