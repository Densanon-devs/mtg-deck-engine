"""Third-wave combo-aware tests:

- rank_cut_candidates respects protected_card_names (combo pieces never cut)
- export_markdown emits Combos / Near-miss sections when fed combo dicts
- AppApi.analyze_deck appends combo notes to recommendations when the
  cache is populated (auto-detected via the AppApi-bound combo store)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from densa_deck.analyst.candidates import rank_cut_candidates
from densa_deck.app.api import AppApi
from densa_deck.combos import Combo, ComboStore
from densa_deck.data.database import CardDatabase
from densa_deck.export.exporter import export_markdown, export_html
from densa_deck.models import (
    AnalysisResult,
    Card,
    CardLayout,
    CardTag,
    Color,
    Deck,
    DeckEntry,
    Format,
    Legality,
    Zone,
)


def _mk(name, *, cmc=2, tags=(), tl="Creature"):
    return Card(
        scryfall_id=f"sid-{name}", oracle_id=f"oid-{name}",
        name=name, layout=CardLayout.NORMAL,
        cmc=cmc, mana_cost="{2}", type_line=tl,
        is_creature=("Creature" in tl),
        is_artifact=("Artifact" in tl),
        is_land=("Land" in tl),
        tags=list(tags),
        legalities={"commander": Legality.LEGAL},
    )


# ---------------------------------------------------------------- cut protection


class TestProtectedCutCandidates:
    def test_combo_pieces_excluded_from_cuts(self):
        """A high-CMC combo piece would normally rank as cut candidate
        ('high_cmc_non_finisher'). With protection set, it must be
        excluded entirely."""
        # Build a deck with: a tagged-finisher protect target (high CMC,
        # would normally rank as a cut candidate via vanilla bloat rules),
        # plus a no-tag bloat card, plus a baseline ramp.
        cards = [
            DeckEntry(card_name="Hullbreaker Horror", quantity=1, zone=Zone.MAINBOARD,
                      card=_mk("Hullbreaker Horror", cmc=7, tl="Creature")),  # combo piece
            DeckEntry(card_name="Pelakka Wurm", quantity=1, zone=Zone.MAINBOARD,
                      card=_mk("Pelakka Wurm", cmc=7, tl="Creature")),  # plain bloat
            DeckEntry(card_name="Sol Ring", quantity=1, zone=Zone.MAINBOARD,
                      card=_mk("Sol Ring", cmc=1, tl="Artifact",
                              tags=[CardTag.RAMP, CardTag.MANA_ROCK])),
            DeckEntry(card_name="Forest", quantity=35, zone=Zone.MAINBOARD,
                      card=_mk("Forest", cmc=0, tl="Basic Land")),
        ]
        deck = Deck(name="Test", format=Format.COMMANDER, entries=cards)
        # Without protection: both Hullbreaker Horror and Pelakka Wurm
        # surface as cut candidates because they're high-CMC non-finisher.
        unprotected = rank_cut_candidates(deck)
        unprotected_names = [c.entry.card.name for c in unprotected]
        assert "Hullbreaker Horror" in unprotected_names
        assert "Pelakka Wurm" in unprotected_names

        # With protection: Hullbreaker Horror is excluded, Pelakka Wurm
        # still appears (it's not in the protected set).
        protected = rank_cut_candidates(
            deck, protected_card_names={"hullbreaker horror"},
        )
        protected_names = [c.entry.card.name for c in protected]
        assert "Hullbreaker Horror" not in protected_names
        assert "Pelakka Wurm" in protected_names

    def test_protection_set_empty_means_no_change(self):
        """Backward compat: protected_card_names=None or set() yields
        the same result as the old call."""
        cards = [
            DeckEntry(card_name="X", quantity=1, zone=Zone.MAINBOARD,
                      card=_mk("X", cmc=7, tl="Creature")),
            DeckEntry(card_name="Forest", quantity=35, zone=Zone.MAINBOARD,
                      card=_mk("Forest", cmc=0, tl="Basic Land")),
        ]
        deck = Deck(name="Test", format=Format.COMMANDER, entries=cards)
        a = rank_cut_candidates(deck)
        b = rank_cut_candidates(deck, protected_card_names=set())
        c = rank_cut_candidates(deck, protected_card_names=None)
        assert [x.entry.card.name for x in a] == [x.entry.card.name for x in b]
        assert [x.entry.card.name for x in a] == [x.entry.card.name for x in c]


# ---------------------------------------------------------------- export


class TestExportCombos:
    @pytest.fixture
    def base_result(self):
        return AnalysisResult(
            deck_name="Test Deck",
            format="commander",
            total_cards=100,
            mana_curve={1: 5, 2: 8, 3: 10, 4: 5, 5: 2, 6: 1, 7: 0},
            land_count=36, ramp_count=10, draw_count=8,
            interaction_count=8, threat_count=12,
            average_cmc=2.5,
        )

    def test_markdown_contains_combos_section(self, base_result):
        combos = [{
            "short_label": "Sol Ring + Hullbreaker Horror -> Infinite mana",
            "cards": ["Sol Ring", "Hullbreaker Horror"],
            "produces": ["Infinite mana"],
            "popularity": 300_000,
            "spellbook_url": "https://commanderspellbook.com/combo/42/",
            "bracket_tag": "E",
        }]
        out = export_markdown(base_result, combos=combos)
        assert "## Combos" in out
        assert "Sol Ring + Hullbreaker Horror" in out
        assert "https://commanderspellbook.com/combo/42/" in out
        assert "Commander Spellbook" in out

    def test_markdown_no_section_when_omitted(self, base_result):
        out = export_markdown(base_result)
        assert "## Combos" not in out

    def test_markdown_near_miss_section(self, base_result):
        near = [{
            "short_label": "X + Y -> Win",
            "cards": ["X", "Y"],
            "missing_cards": ["Y"],
            "popularity": 100,
            "spellbook_url": "https://commanderspellbook.com/combo/99/",
        }]
        out = export_markdown(base_result, near_combos=near)
        assert "1 Card Away" in out
        assert "X + Y -> Win" in out

    def test_html_passes_combos_through(self, base_result):
        combos = [{
            "short_label": "A + B -> Win",
            "cards": ["A", "B"],
            "produces": ["Win the game"],
            "popularity": 50,
            "spellbook_url": "https://commanderspellbook.com/combo/1/",
            "bracket_tag": "C",
        }]
        out = export_html(base_result, combos=combos)
        assert "Combos" in out
        # HTML escapes the > to &gt; — test for the escaped form
        assert "A + B -&gt; Win" in out


# ---------------------------------------------------------------- analyze_deck recs


class TestAnalyzeDeckComboRecs:
    """End-to-end: AppApi.analyze_deck appends combo notes when the cache
    is populated. Uses the same monkey-patch pattern as the existing
    detect_combos tests in test_app_api.py."""

    def _seed_card_db(self, card_db_path):
        db = CardDatabase(db_path=card_db_path)
        db.upsert_cards([
            Card(scryfall_id="s1", oracle_id="o1", name="Sol Ring",
                 layout=CardLayout.NORMAL, cmc=1, mana_cost="{1}",
                 type_line="Artifact", is_artifact=True,
                 legalities={"commander": Legality.LEGAL}),
            Card(scryfall_id="s2", oracle_id="o2", name="Hullbreaker Horror",
                 layout=CardLayout.NORMAL, cmc=7, mana_cost="{6}{U}",
                 type_line="Creature", colors=[Color.BLUE],
                 color_identity=[Color.BLUE], is_creature=True,
                 legalities={"commander": Legality.LEGAL}),
            Card(scryfall_id="s3", oracle_id="o3", name="Forest",
                 layout=CardLayout.NORMAL, type_line="Basic Land",
                 color_identity=[Color.GREEN], is_land=True,
                 legalities={"commander": Legality.LEGAL}),
        ])
        db.close()

    def test_combo_present_appends_recommendation(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            card_db = tmp_path / "cards.db"
            ver_db = tmp_path / "v.db"
            self._seed_card_db(card_db)

            # Seed the per-AppApi combo store with one matching combo.
            cstore = ComboStore(db_path=tmp_path / "combos.db")
            cstore.upsert_combos([Combo(
                combo_id="42",
                cards=["Sol Ring", "Hullbreaker Horror"],
                produces=["Infinite mana"],
                color_identity="U",
                popularity=300_000,
            )])
            cstore.close()

            api = AppApi(db_path=card_db, version_db_path=ver_db)
            api._combo_store = ComboStore(db_path=tmp_path / "combos.db")
            try:
                text = ("Commander:\n1 Hullbreaker Horror\n\n"
                        "Mainboard:\n1 Sol Ring\n30 Forest\n")
                r = api.analyze_deck(text, "commander", "Test")
                assert r["ok"] is True
                recs = r["data"]["recommendations"]
                assert any("combo line" in s.lower() for s in recs)
            finally:
                api.close()

    def test_no_combos_no_extra_recs(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            card_db = tmp_path / "cards.db"
            ver_db = tmp_path / "v.db"
            self._seed_card_db(card_db)

            # Empty combo cache — analyze_deck should still work and NOT
            # add any combo-related recommendations.
            api = AppApi(db_path=card_db, version_db_path=ver_db)
            api._combo_store = ComboStore(db_path=tmp_path / "combos.db")
            try:
                text = "Commander:\n1 Hullbreaker Horror\n\nMainboard:\n30 Forest\n"
                r = api.analyze_deck(text, "commander", "Test")
                assert r["ok"] is True
                recs = r["data"]["recommendations"]
                # No combo language in any rec since cache is empty.
                assert not any("combo line" in s.lower() for s in recs)
            finally:
                api.close()
