"""Tests for deck validation."""

from mtg_deck_engine.deck.validator import validate_deck
from mtg_deck_engine.models import (
    Card,
    CardLayout,
    Color,
    Deck,
    DeckEntry,
    Format,
    Legality,
    Zone,
)


def _make_card(name: str, **kwargs) -> Card:
    defaults = {
        "scryfall_id": f"id-{name}",
        "oracle_id": f"oracle-{name}",
        "name": name,
        "layout": CardLayout.NORMAL,
        "legalities": {"commander": Legality.LEGAL, "modern": Legality.LEGAL},
    }
    defaults.update(kwargs)
    return Card(**defaults)


def test_singleton_violation():
    card = _make_card("Sol Ring")
    deck = Deck(
        format=Format.COMMANDER,
        entries=[
            DeckEntry(card_name="Sol Ring", quantity=2, zone=Zone.MAINBOARD, card=card),
            DeckEntry(
                card_name="Atraxa",
                quantity=1,
                zone=Zone.COMMANDER,
                card=_make_card(
                    "Atraxa",
                    type_line="Legendary Creature",
                    color_identity=[Color.WHITE, Color.BLUE, Color.BLACK, Color.GREEN],
                ),
            ),
        ]
        + [
            DeckEntry(
                card_name=f"Plains{i}",
                quantity=1,
                zone=Zone.MAINBOARD,
                card=_make_card(f"Plains{i}", type_line="Basic Land — Plains", is_land=True),
            )
            for i in range(97)
        ],
    )
    issues = validate_deck(deck)
    singleton_issues = [i for i in issues if "singleton" in i.message.lower()]
    assert len(singleton_issues) > 0


def test_banned_card():
    card = _make_card(
        "Banned Card",
        legalities={"modern": Legality.BANNED},
    )
    deck = Deck(
        format=Format.MODERN,
        entries=[
            DeckEntry(card_name="Banned Card", quantity=1, zone=Zone.MAINBOARD, card=card),
        ]
        + [
            DeckEntry(
                card_name=f"Plains{i}",
                quantity=1,
                zone=Zone.MAINBOARD,
                card=_make_card(f"Plains{i}"),
            )
            for i in range(59)
        ],
    )
    issues = validate_deck(deck)
    banned = [i for i in issues if "banned" in i.message.lower()]
    assert len(banned) == 1


def test_basic_lands_exempt_from_copies():
    plains = _make_card("Plains", type_line="Basic Land — Plains", is_land=True)
    deck = Deck(
        format=Format.MODERN,
        entries=[
            DeckEntry(card_name="Plains", quantity=20, zone=Zone.MAINBOARD, card=plains),
        ]
        + [
            DeckEntry(
                card_name=f"Card{i}",
                quantity=1,
                zone=Zone.MAINBOARD,
                card=_make_card(f"Card{i}"),
            )
            for i in range(40)
        ],
    )
    issues = validate_deck(deck)
    copy_issues = [i for i in issues if "copies" in i.message.lower() and "plains" in i.message.lower()]
    assert len(copy_issues) == 0


def test_color_identity_violation():
    commander = _make_card(
        "Mono Green Commander",
        type_line="Legendary Creature",
        color_identity=[Color.GREEN],
    )
    off_color = _make_card(
        "Blue Card",
        color_identity=[Color.BLUE],
    )
    deck = Deck(
        format=Format.COMMANDER,
        entries=[
            DeckEntry(card_name="Mono Green Commander", quantity=1, zone=Zone.COMMANDER, card=commander),
            DeckEntry(card_name="Blue Card", quantity=1, zone=Zone.MAINBOARD, card=off_color),
        ]
        + [
            DeckEntry(
                card_name=f"Forest{i}",
                quantity=1,
                zone=Zone.MAINBOARD,
                card=_make_card(f"Forest{i}", color_identity=[Color.GREEN]),
            )
            for i in range(98)
        ],
    )
    issues = validate_deck(deck)
    color_issues = [i for i in issues if "outside commander identity" in i.message.lower()]
    assert len(color_issues) == 1


def test_sideboard_size():
    deck = Deck(
        format=Format.MODERN,
        entries=[
            DeckEntry(
                card_name=f"Card{i}",
                quantity=1,
                zone=Zone.MAINBOARD,
                card=_make_card(f"Card{i}"),
            )
            for i in range(60)
        ]
        + [
            DeckEntry(
                card_name=f"SB{i}",
                quantity=1,
                zone=Zone.SIDEBOARD,
                card=_make_card(f"SB{i}"),
            )
            for i in range(16)
        ],
    )
    issues = validate_deck(deck)
    sb_issues = [i for i in issues if "sideboard" in i.message.lower()]
    assert len(sb_issues) == 1


def test_unresolved_card():
    deck = Deck(
        format=Format.MODERN,
        entries=[
            DeckEntry(card_name="Unknown Card", quantity=1, zone=Zone.MAINBOARD, card=None),
        ],
    )
    issues = validate_deck(deck)
    unresolved = [i for i in issues if "not found" in i.message.lower()]
    assert len(unresolved) == 1
