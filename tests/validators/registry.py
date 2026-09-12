"""Validator discovery - mirrors `bot.core.registry`'s pattern: every module
under `tests/validators/` that defines a `BaseValidator` subclass registers
itself via `__init_subclass__` on import, so a new build's validator is one
new file, no registry edit, and a duplicate `BUILD_NAME` raises at import.
"""

from __future__ import annotations

import importlib
import pkgutil
from functools import lru_cache

import tests.validators as _validators_package
from bot.core.registry import all_builds
from tests.validators.base_validator import BaseValidator


@lru_cache(maxsize=1)
def _import_all_validators() -> None:
    """Import every module in this package once so each `BaseValidator`
    subclass has registered itself."""
    for module_info in pkgutil.iter_modules(_validators_package.__path__):
        if module_info.name.startswith("_") or module_info.name == "registry":
            continue
        importlib.import_module(f"tests.validators.{module_info.name}")


def validator_for_build(build_name: str) -> type[BaseValidator]:
    """The validator class registered for `build_name`, or `BaseValidator`
    itself as a fallback for a build with no dedicated validator yet."""
    _import_all_validators()
    return BaseValidator._registry.get(build_name, BaseValidator)


def known_validator_build_names() -> frozenset[str]:
    """Every build name with its own dedicated validator class."""
    _import_all_validators()
    return frozenset(BaseValidator._registry)


def unregistered_build_names() -> frozenset[str]:
    """Real builds with no validator of their own yet (fall back to
    `BaseValidator`'s generic report)."""
    return frozenset(all_builds()) - known_validator_build_names()


def unknown_validator_build_names() -> frozenset[str]:
    """Validator `BUILD_NAME`s that don't match any real build - catches a
    typo'd name as a testable fact instead of a silent mid-game fallback."""
    return known_validator_build_names() - frozenset(all_builds())
