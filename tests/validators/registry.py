"""Validator discovery — mirrors `bot.core.registry`'s pattern deliberately:
every module under `tests/validators/` that defines a `BaseValidator`
subclass with its own `BUILD_NAME` registers itself via
`BaseValidator.__init_subclass__` the moment this module is imported, so
adding a build's validator means adding one file here too - no separate
registry edit, and no way for two files to fight over the same build name
(`__init_subclass__` raises immediately, at import time, on a collision -
the validator equivalent of `bot.core.registry.all_builds()` raising on a
duplicate `BUILD.name`).
"""

from __future__ import annotations

import importlib
import pkgutil
from functools import lru_cache
from typing import FrozenSet, Type

import tests.validators as _validators_package
from bot.core.registry import all_builds
from tests.validators.base_validator import BaseValidator


@lru_cache(maxsize=1)
def _import_all_validators() -> None:
    """Import every module in this package once, so every `BaseValidator`
    subclass's `__init_subclass__` has already run and registered it."""
    for module_info in pkgutil.iter_modules(_validators_package.__path__):
        if module_info.name.startswith("_") or module_info.name == "registry":
            continue
        importlib.import_module(f"tests.validators.{module_info.name}")


def validator_for_build(build_name: str) -> Type[BaseValidator]:
    """The validator class registered for `build_name`, or `BaseValidator`
    itself (Stage 1 + Stage 4 only, generically titled) as a last-resort
    fallback for a build with no dedicated validator file yet - the same
    "a name mismatch shouldn't end a game" reasoning as
    `bot.core.registry.default_build`.
    """
    _import_all_validators()
    return BaseValidator._registry.get(build_name, BaseValidator)


def known_validator_build_names() -> FrozenSet[str]:
    """Every build name with its own dedicated validator class."""
    _import_all_validators()
    return frozenset(BaseValidator._registry)


def unregistered_build_names() -> FrozenSet[str]:
    """Every real build (from `bot.core.registry.all_builds`) that has no
    validator of its own yet, and so falls back to `BaseValidator`'s
    generic Stage 1 + Stage 4 report."""
    return frozenset(all_builds()) - known_validator_build_names()


def unknown_validator_build_names() -> FrozenSet[str]:
    """Every validator `BUILD_NAME` that doesn't match any real build -
    catches a typo'd `BUILD_NAME` (registered under a name no
    `BuildDefinition` actually uses) as a plain, testable fact rather than a
    silent always-falls-back-to-BaseValidator bug discovered mid-game."""
    return known_validator_build_names() - frozenset(all_builds())
