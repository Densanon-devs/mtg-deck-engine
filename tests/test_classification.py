"""Tests for card classification."""

from mtg_deck_engine.classification.tagger import classify_card
from mtg_deck_engine.models import Card, CardLayout, CardTag, Color


def _make_card(**kwargs) -> Card:
    """Helper to create a test card with minimal required fields."""
    defaults = {
        "scryfall_id": "test-id",
        "oracle_id": "test-oracle",
        "name": "Test Card",
        "layout": CardLayout.NORMAL,
    }
    defaults.update(kwargs)
    return Card(**defaults)


def test_basic_land():
    card = _make_card(
        name="Forest",
        type_line="Basic Land — Forest",
        is_land=True,
    )
    tags = classify_card(card)
    assert CardTag.LAND in tags
    assert CardTag.BASIC_LAND in tags


def test_fetch_land():
    card = _make_card(
        name="Flooded Strand",
        type_line="Land",
        oracle_text="Pay 1 life, Sacrifice Flooded Strand: Search your library for a Plains or Island card, put it onto the battlefield tapped, then shuffle.",
        is_land=True,
    )
    tags = classify_card(card)
    assert CardTag.LAND in tags
    assert CardTag.FETCH_LAND in tags


def test_mana_rock():
    card = _make_card(
        name="Sol Ring",
        type_line="Artifact",
        oracle_text="{T}: Add {C}{C}.",
        is_artifact=True,
        produced_mana=["C"],
    )
    tags = classify_card(card)
    assert CardTag.RAMP in tags
    assert CardTag.MANA_ROCK in tags


def test_mana_dork():
    card = _make_card(
        name="Llanowar Elves",
        type_line="Creature — Elf Druid",
        oracle_text="{T}: Add {G}.",
        is_creature=True,
        produced_mana=["G"],
    )
    tags = classify_card(card)
    assert CardTag.RAMP in tags
    assert CardTag.MANA_DORK in tags


def test_card_draw():
    card = _make_card(
        name="Harmonize",
        type_line="Sorcery",
        oracle_text="Draw three cards.",
        is_sorcery=True,
        cmc=4.0,
    )
    tags = classify_card(card)
    assert CardTag.CARD_DRAW in tags


def test_targeted_removal():
    card = _make_card(
        name="Swords to Plowshares",
        type_line="Instant",
        oracle_text="Exile target creature. Its controller gains life equal to its power.",
        is_instant=True,
    )
    tags = classify_card(card)
    assert CardTag.TARGETED_REMOVAL in tags


def test_board_wipe():
    card = _make_card(
        name="Wrath of God",
        type_line="Sorcery",
        oracle_text="Destroy all creatures. They can't be regenerated.",
        is_sorcery=True,
    )
    tags = classify_card(card)
    assert CardTag.BOARD_WIPE in tags


def test_counterspell():
    card = _make_card(
        name="Counterspell",
        type_line="Instant",
        oracle_text="Counter target spell.",
        is_instant=True,
    )
    tags = classify_card(card)
    assert CardTag.COUNTERSPELL in tags


def test_tutor():
    card = _make_card(
        name="Demonic Tutor",
        type_line="Sorcery",
        oracle_text="Search your library for a card, put that card into your hand, then shuffle.",
        is_sorcery=True,
    )
    tags = classify_card(card)
    assert CardTag.TUTOR in tags


def test_threat_high_power():
    card = _make_card(
        name="Gigantosaurus",
        type_line="Creature — Dinosaur",
        oracle_text="",
        is_creature=True,
        power="10",
        toughness="10",
        cmc=5.0,
    )
    tags = classify_card(card)
    assert CardTag.FINISHER in tags  # power >= 7


def test_token_maker():
    card = _make_card(
        name="Raise the Alarm",
        type_line="Instant",
        oracle_text="Create two 1/1 white Soldier creature tokens.",
        is_instant=True,
    )
    tags = classify_card(card)
    assert CardTag.TOKEN_MAKER in tags


def test_equipment():
    card = _make_card(
        name="Lightning Greaves",
        type_line="Artifact — Equipment",
        oracle_text="Equipped creature has haste and shroud.\nEquip {0}",
        is_artifact=True,
        keywords=["Equip"],
    )
    tags = classify_card(card)
    assert CardTag.EQUIPMENT in tags
    assert CardTag.PROTECTION in tags


def test_dual_land():
    card = _make_card(
        name="Breeding Pool",
        type_line="Land — Forest Island",
        oracle_text="As Breeding Pool enters, you may pay 2 life. If you don't, it enters tapped.\n{T}: Add {G} or {U}.",
        is_land=True,
        produced_mana=["G", "U"],
    )
    tags = classify_card(card)
    assert CardTag.LAND in tags
    assert CardTag.DUAL_LAND in tags
