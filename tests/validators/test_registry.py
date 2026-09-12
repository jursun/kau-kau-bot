"""Tests for `tests.validators.registry` - the piece that resolves a live
game's `ctx.build.name` to the right per-build validator class.
"""

from __future__ import annotations

from tests.validators.base_validator import BaseValidator
from tests.validators.four_rax_proxy_validator import FourRaxProxyValidator
from tests.validators.registry import (
    unknown_validator_build_names,
    unregistered_build_names,
    validator_for_build,
)
from tests.validators.speedling_all_in_validator import SpeedlingAllInValidator
from tests.validators.two_base_chargelot_all_in_validator import (
    TwoBaseChargelotAllInValidator,
)
from tests.validators.upgrade_rush_validator import UpgradeRushValidator


def test_validator_for_build_resolves_every_registered_build() -> None:
    assert validator_for_build("Four Rax Proxy") is FourRaxProxyValidator
    assert validator_for_build("UpgradeRush") is UpgradeRushValidator
    assert validator_for_build("Speedling All-In") is SpeedlingAllInValidator
    assert (
        validator_for_build("2base Chargelot All-In")
        is TwoBaseChargelotAllInValidator
    )


def test_validator_for_build_falls_back_to_base_validator_for_an_unknown_name() -> None:
    """A build with no dedicated validator file yet still gets a report -
    the safe, minimal one - rather than crashing `--validate` mode. Same
    "don't let a name mismatch end a game" reasoning as
    `bot.core.registry.default_build`."""
    assert validator_for_build("Some Future Build") is BaseValidator


def test_every_validator_build_name_matches_a_real_build() -> None:
    """Catches a typo'd `BUILD_NAME` (registered under a name no
    `BuildDefinition` actually uses) at test time rather than as a silent
    always-falls-back-to-BaseValidator bug discovered mid-game."""
    assert unknown_validator_build_names() == frozenset()


def test_every_currently_registered_build_has_its_own_validator() -> None:
    """Not a hard requirement (`validator_for_build` falls back gracefully),
    but true today - a build without one is worth a deliberate decision, not
    an oversight this test would otherwise miss."""
    assert unregistered_build_names() == frozenset()


def test_duplicate_build_name_across_two_validator_classes_is_rejected() -> None:
    class _First(BaseValidator):
        BUILD_NAME = "Test Duplicate Build"

    try:

        class _Second(BaseValidator):
            BUILD_NAME = "Test Duplicate Build"

    except ValueError as error:
        assert "Test Duplicate Build" in str(error)
    else:
        raise AssertionError("expected a ValueError for the duplicate BUILD_NAME")
    finally:
        # Don't leak this test's fake entry into the shared registry other
        # tests (and `registry.py`'s own scan) read from.
        BaseValidator._registry.pop("Test Duplicate Build", None)
