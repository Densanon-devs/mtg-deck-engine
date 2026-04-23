"""Regression coverage for the three-pass deck resolver: canonical name
lookup → cached alias lookup → online Scryfall fuzzy fallback.

The online fallback is what lets a Moxfield "Export → Text" paste of a
deck containing Universes Within reprints (e.g. Innistrad: Crimson Vow's
"Dracula, Blood Immortal" → "Falkenrath Forebear") resolve without
forcing the user to hand-edit the decklist.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from densa_deck.data.database import CardDatabase
from densa_deck.deck.resolver import resolve_deck
from densa_deck.models import (
    Card, CardLayout, DeckEntry, Legality, Zone,
)


@pytest.fixture
def db_with_falkenrath(tmp_path: Path) -> CardDatabase:
    db = CardDatabase(db_path=tmp_path / "cards.db")
    db.upsert_cards([
        Card(
            scryfall_id="sid-ff", oracle_id="oid-ff", name="Falkenrath Forebear",
            layout=CardLayout.NORMAL, cmc=3, mana_cost="{2}{B}",
            type_line="Creature - Vampire", color_identity=["B"],
            legalities={"commander": Legality.LEGAL},
        ),
    ])
    return db


def test_canonical_name_resolves_first_pass(db_with_falkenrath):
    entries = [DeckEntry(card_name="Falkenrath Forebear", quantity=1, zone=Zone.MAINBOARD)]
    deck = resolve_deck(entries, db_with_falkenrath, online_fallback=False)
    assert deck.entries[0].card is not None
    assert deck.entries[0].card.name == "Falkenrath Forebear"


def test_cached_alias_resolves_second_pass(db_with_falkenrath):
    """If we've previously cached an alias, that import should resolve
    with zero network calls. This is the steady-state offline experience
    after the very first time the user imports a Dracula-variant deck."""
    db_with_falkenrath.add_alias("Dracula, Blood Immortal", "Falkenrath Forebear")
    entries = [DeckEntry(card_name="Dracula, Blood Immortal", quantity=1, zone=Zone.MAINBOARD)]

    # Patch the Scryfall fetcher with a sentinel so the test fails loudly
    # if we accidentally hit the network despite the alias being cached.
    def panic(*a, **kw):
        raise AssertionError("online fallback ran despite cache hit")
    with patch("densa_deck.deck.resolver._fetch_oracle_names_via_scryfall", side_effect=panic):
        deck = resolve_deck(entries, db_with_falkenrath, online_fallback=True)

    assert deck.entries[0].card is not None
    assert deck.entries[0].card.name == "Falkenrath Forebear"


def test_online_scryfall_fallback_populates_cache(db_with_falkenrath, monkeypatch):
    """First-time resolution of a flavor name: the resolver calls
    Scryfall, caches the alias, and resolves the entry. A second
    resolve_deck call for the same name must not hit Scryfall again."""
    calls = {"n": 0}
    def fake_fetch(names):
        calls["n"] += 1
        # Simulate Scryfall's fuzzy endpoint resolving the flavor name
        return {"Dracula, Blood Immortal": "Falkenrath Forebear"}
    monkeypatch.setattr(
        "densa_deck.deck.resolver._fetch_oracle_names_via_scryfall", fake_fetch,
    )

    # First import — online fallback runs, alias gets cached
    entries = [DeckEntry(card_name="Dracula, Blood Immortal", quantity=1, zone=Zone.MAINBOARD)]
    deck = resolve_deck(entries, db_with_falkenrath)
    assert calls["n"] == 1
    assert deck.entries[0].card is not None
    assert deck.entries[0].card.name == "Falkenrath Forebear"
    # Alias must be persisted
    cached = db_with_falkenrath.lookup_alias("Dracula, Blood Immortal")
    assert cached is not None and cached.name == "Falkenrath Forebear"

    # Second import of the same flavor name — must hit cache, NOT Scryfall
    entries2 = [DeckEntry(card_name="Dracula, Blood Immortal", quantity=1, zone=Zone.MAINBOARD)]
    deck2 = resolve_deck(entries2, db_with_falkenrath)
    assert calls["n"] == 1  # unchanged — cache hit
    assert deck2.entries[0].card is not None


def test_online_fallback_disabled_leaves_card_unresolved(db_with_falkenrath):
    """`online_fallback=False` must not perform any network I/O, and
    unknown names must stay unresolved."""
    entries = [DeckEntry(card_name="Dracula, Blood Immortal", quantity=1, zone=Zone.MAINBOARD)]
    def panic(*a, **kw):
        raise AssertionError("online fallback ran with online_fallback=False")
    with patch("densa_deck.deck.resolver._fetch_oracle_names_via_scryfall", side_effect=panic):
        deck = resolve_deck(entries, db_with_falkenrath, online_fallback=False)
    assert deck.entries[0].card is None


def test_online_fallback_survives_network_error(db_with_falkenrath, monkeypatch):
    """If Scryfall is unreachable (offline, DNS failure, etc.) the fetcher
    returns {} and unresolved cards simply stay unresolved — the resolver
    must not crash."""
    monkeypatch.setattr(
        "densa_deck.deck.resolver._fetch_oracle_names_via_scryfall",
        lambda names: {},
    )
    entries = [DeckEntry(card_name="Nonexistent Card Name Xyz", quantity=1, zone=Zone.MAINBOARD)]
    deck = resolve_deck(entries, db_with_falkenrath)
    assert deck.entries[0].card is None
    assert len(deck.entries) == 1


def test_multiple_unresolved_names_share_one_fallback_batch(db_with_falkenrath, monkeypatch):
    """Several flavor-named cards in one import should all get handed
    to _fetch_oracle_names_via_scryfall in a single call — we don't
    want N round-trips from N callers."""
    batch_sizes: list[int] = []
    def fake_fetch(names):
        batch_sizes.append(len(names))
        return {n: "Falkenrath Forebear" for n in names}
    monkeypatch.setattr(
        "densa_deck.deck.resolver._fetch_oracle_names_via_scryfall", fake_fetch,
    )
    entries = [
        DeckEntry(card_name="Dracula, Blood Immortal", quantity=1, zone=Zone.MAINBOARD),
        DeckEntry(card_name="Dracula, Lord of Blood", quantity=1, zone=Zone.MAINBOARD),
        DeckEntry(card_name="Vlad, Son of the Dragon", quantity=1, zone=Zone.MAINBOARD),
    ]
    resolve_deck(entries, db_with_falkenrath)
    assert batch_sizes == [3], batch_sizes
