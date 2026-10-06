"""`ResearchChain` pins a research chain to one building, outside the queue."""

from __future__ import annotations

from unittest.mock import MagicMock

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from bot.behaviors.zerg.research_chain import ResearchChain

CHAIN = (
    UpgradeId.ZERGMELEEWEAPONSLEVEL1,
    UpgradeId.ZERGMELEEWEAPONSLEVEL2,
    UpgradeId.ZERGMELEEWEAPONSLEVEL3,
)
RESEARCH = {
    UpgradeId.ZERGMELEEWEAPONSLEVEL1: AbilityId.RESEARCH_ZERGMELEEWEAPONSLEVEL1,
    UpgradeId.ZERGMELEEWEAPONSLEVEL2: AbilityId.RESEARCH_ZERGMELEEWEAPONSLEVEL2,
    UpgradeId.ZERGMELEEWEAPONSLEVEL3: AbilityId.RESEARCH_ZERGMELEEWEAPONSLEVEL3,
}


def _ai(structure, done=(), afford=True) -> MagicMock:
    ai = MagicMock()
    ai.structures.find_by_tag.return_value = structure
    ai.pending_or_complete_upgrade.side_effect = lambda u: u in done
    ai.can_afford.return_value = afford
    return ai


def _evo(idle=True, ready=True, abilities=tuple(RESEARCH.values())) -> MagicMock:
    evo = MagicMock()
    evo.type_id = UnitTypeId.EVOLUTIONCHAMBER
    evo.is_ready = ready
    evo.is_idle = idle
    evo.abilities = list(abilities)
    return evo


def test_starts_the_first_level_on_its_building() -> None:
    evo = _evo()
    ai = _ai(evo)

    assert ResearchChain(7, CHAIN).execute(ai, {}, MagicMock()) is True

    evo.research.assert_called_once_with(UpgradeId.ZERGMELEEWEAPONSLEVEL1)
    ai.structures.find_by_tag.assert_called_with(7)


def test_moves_to_the_next_level_once_the_first_is_started() -> None:
    evo = _evo()
    ai = _ai(evo, done={UpgradeId.ZERGMELEEWEAPONSLEVEL1})

    assert ResearchChain(7, CHAIN).execute(ai, {}, MagicMock()) is True

    evo.research.assert_called_once_with(UpgradeId.ZERGMELEEWEAPONSLEVEL2)


def test_does_nothing_while_the_building_is_busy_or_missing() -> None:
    busy = _evo(idle=False)
    assert ResearchChain(7, CHAIN).execute(_ai(busy), {}, MagicMock()) is False
    busy.research.assert_not_called()

    assert ResearchChain(7, CHAIN).execute(_ai(None), {}, MagicMock()) is False
    assert ResearchChain(7, CHAIN).execute(_ai(_evo(ready=False)), {}, MagicMock()) is False


def test_waits_when_the_next_level_is_not_available_or_affordable() -> None:
    evo = _evo(abilities=())  # e.g. level 2 needs level 1 done / Lair
    assert ResearchChain(7, CHAIN).execute(_ai(evo), {}, MagicMock()) is False
    evo.research.assert_not_called()

    poor = _evo()
    assert ResearchChain(7, CHAIN).execute(_ai(poor, afford=False), {}, MagicMock()) is False
    poor.research.assert_not_called()


def test_finished_chain_does_nothing() -> None:
    evo = _evo()
    ai = _ai(evo, done=set(CHAIN))

    assert ResearchChain(7, CHAIN).execute(ai, {}, MagicMock()) is False
    evo.research.assert_not_called()


def test_a_chain_listing_an_upgrade_the_building_does_not_research_does_nothing() -> None:
    """Seismic Spines is a Lurker Den upgrade: on a Hydralisk Den it must not
    be attempted (and logs that it is not researched there)."""
    den = MagicMock()
    den.type_id = UnitTypeId.HYDRALISKDEN
    den.is_ready = True
    den.is_idle = True
    den.abilities = []
    ai = _ai(den)
    ai.pending_or_complete_upgrade.side_effect = lambda u: False

    assert (
        ResearchChain(7, (UpgradeId.LURKERRANGE,)).execute(ai, {}, MagicMock()) is False
    )
    den.research.assert_not_called()


def test_seismic_spines_and_adaptive_talons_research_at_the_lurker_den() -> None:
    den = MagicMock()
    den.type_id = UnitTypeId.LURKERDENMP
    den.is_ready = True
    den.is_idle = True
    den.abilities = [
        AbilityId.LURKERDENRESEARCH_RESEARCHLURKERRANGE,
        AbilityId.RESEARCH_ADAPTIVETALONS,
    ]
    ai = _ai(den)
    ai.pending_or_complete_upgrade.side_effect = lambda u: False

    chain = ResearchChain(7, (UpgradeId.LURKERRANGE, UpgradeId.DIGGINGCLAWS))
    assert chain.execute(ai, {}, MagicMock()) is True
    den.research.assert_called_once_with(UpgradeId.LURKERRANGE)
