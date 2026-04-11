"""Tests for new features: power level, castability, staples, deck diff, URL import, calc CLI."""

import subprocess
import sys

from mtg_deck_engine.analysis.castability import analyze_castability
from mtg_deck_engine.analysis.deck_diff import compare_decks
from mtg_deck_engine.analysis.power_level import PowerBreakdown, estimate_power_level
from mtg_deck_engine.analysis.staples import check_staples
from mtg_deck_engine.deck.url_import import detect_url
from mtg_deck_engine.models import Card, CardLayout, CardTag, Color, Deck, DeckEntry, Format, Zone

PYTHON = sys.executable


def _make_card(name, is_land=False, cmc=0.0, tags=None, mana_cost="", **kw):
    return Card(
        scryfall_id=f"id-{name}", oracle_id=f"oracle-{name}", name=name,
        layout=CardLayout.NORMAL, cmc=cmc, is_land=is_land, mana_cost=mana_cost,
        tags=tags or [], **kw,
    )


def _make_entry(name, qty=1, zone=Zone.MAINBOARD, **kw):
    card = _make_card(name, **kw)
    return DeckEntry(card_name=name, quantity=qty, zone=zone, card=card)


def _make_deck(land_count=36, ramp_count=10, creature_count=15, draw_count=8,
               removal_count=8, tutor_count=3, format=Format.COMMANDER):
    entries = [
        _make_entry("Commander", zone=Zone.COMMANDER, cmc=4, tags=[CardTag.FINISHER],
                     mana_cost="{2}{G}{W}", color_identity=[Color.GREEN, Color.WHITE]),
    ]
    for i in range(land_count):
        entries.append(_make_entry(f"Land{i}", is_land=True))
    for i in range(ramp_count):
        entries.append(_make_entry(f"Rock{i}", cmc=2, tags=[CardTag.MANA_ROCK, CardTag.RAMP],
                                   is_artifact=True, mana_cost="{2}"))
    for i in range(creature_count):
        entries.append(_make_entry(f"Creature{i}", cmc=3, tags=[CardTag.THREAT],
                                   power="3", toughness="3", is_creature=True, mana_cost="{1}{G}{G}"))
    for i in range(draw_count):
        entries.append(_make_entry(f"Draw{i}", cmc=3, tags=[CardTag.CARD_DRAW], mana_cost="{2}{U}"))
    for i in range(removal_count):
        entries.append(_make_entry(f"Removal{i}", cmc=2, tags=[CardTag.TARGETED_REMOVAL],
                                   is_instant=True, mana_cost="{1}{W}"))
    for i in range(tutor_count):
        entries.append(_make_entry(f"Tutor{i}", cmc=2, tags=[CardTag.TUTOR], mana_cost="{1}{B}"))
    filler = 99 - land_count - ramp_count - creature_count - draw_count - removal_count - tutor_count
    for i in range(max(0, filler)):
        entries.append(_make_entry(f"Filler{i}", cmc=3, mana_cost="{2}{G}"))
    return Deck(name="Test Deck", format=format, entries=entries)


# --- Power level ---

class TestPowerLevel:
    def test_basic_estimation(self):
        deck = _make_deck()
        power = estimate_power_level(deck)
        assert 1.0 <= power.overall <= 10.0
        assert power.tier != ""

    def test_high_power_signals(self):
        """Lots of tutors + low curve + counters = higher power."""
        deck = _make_deck(tutor_count=6, removal_count=10, ramp_count=14)
        power = estimate_power_level(deck)
        assert power.overall >= 4.0

    def test_low_power_signals(self):
        """High curve, no tutors, no interaction = low power."""
        deck = _make_deck(tutor_count=0, removal_count=0, ramp_count=2, draw_count=2)
        power = estimate_power_level(deck)
        assert power.overall <= 6.0

    def test_breakdown_fields(self):
        deck = _make_deck()
        power = estimate_power_level(deck)
        assert 0 <= power.speed <= 10
        assert 0 <= power.interaction <= 10
        assert 0 <= power.combo_potential <= 10
        assert 0 <= power.mana_efficiency <= 10
        assert 0 <= power.win_condition_quality <= 10
        assert 0 <= power.card_quality <= 10

    def test_tier_labels(self):
        deck = _make_deck()
        power = estimate_power_level(deck)
        assert power.tier in ("jank", "casual", "focused", "optimized", "competitive", "cEDH")

    def test_empty_deck(self):
        deck = Deck(name="Empty", format=Format.COMMANDER, entries=[])
        power = estimate_power_level(deck)
        assert power.overall == 1.0
        assert power.tier == "jank"


# --- Castability ---

class TestCastability:
    def test_analyze_demanding_cards(self):
        deck = _make_deck()
        sources = {"G": 15, "W": 10, "U": 5, "B": 3}
        report = analyze_castability(deck, sources)
        assert len(report.cards) > 0

    def test_unreliable_detection(self):
        """Cards needing colors with few sources should be flagged."""
        deck = _make_deck()
        sources = {"G": 2, "W": 2, "U": 1, "B": 1}  # Very few sources
        report = analyze_castability(deck, sources)
        assert len(report.unreliable_cards) > 0

    def test_reliable_with_good_base(self):
        """Well-supported colors should improve castability."""
        deck = _make_deck()
        sources_bad = {"G": 5, "W": 5, "U": 3, "B": 2}
        sources_good = {"G": 30, "W": 20, "U": 15, "B": 10}
        report_bad = analyze_castability(deck, sources_bad)
        report_good = analyze_castability(deck, sources_good)
        # More sources = fewer unreliable cards
        assert len(report_good.unreliable_cards) <= len(report_bad.unreliable_cards)

    def test_no_sources_returns_empty(self):
        deck = _make_deck()
        report = analyze_castability(deck, None)
        assert len(report.cards) == 0


# --- Staples ---

class TestStaples:
    def test_missing_staples_detected(self):
        deck = _make_deck()
        report = check_staples(deck)
        assert len(report.missing) > 0
        names = [s.name for s in report.missing]
        assert "Sol Ring" in names  # Not in our test deck

    def test_present_staples_tracked(self):
        entries = _make_deck().entries + [
            _make_entry("Sol Ring", cmc=1, tags=[CardTag.MANA_ROCK]),
        ]
        deck = Deck(name="With Sol Ring", format=Format.COMMANDER, entries=entries)
        report = check_staples(deck)
        assert "Sol Ring" in report.present_staples

    def test_coverage_score(self):
        deck = _make_deck()
        report = check_staples(deck)
        assert 0.0 <= report.staple_coverage <= 1.0

    def test_no_format(self):
        deck = Deck(name="No Format", format=None, entries=[])
        report = check_staples(deck)
        assert len(report.missing) == 0


# --- Deck diff ---

class TestDeckDiff:
    def test_compare_different_decks(self):
        deck_a = _make_deck(land_count=36, ramp_count=10)
        deck_b = _make_deck(land_count=38, ramp_count=14)
        deck_b.name = "Deck B"
        comp = compare_decks(deck_a, deck_b)
        assert comp.name_a == "Test Deck"
        assert comp.name_b == "Deck B"
        assert comp.result_a is not None
        assert comp.result_b is not None

    def test_overlap_calculation(self):
        deck_a = _make_deck()
        deck_b = _make_deck()
        deck_b.name = "Clone"
        comp = compare_decks(deck_a, deck_b)
        assert comp.overlap_percentage > 90.0  # Same deck = ~100% overlap

    def test_advantages_detected(self):
        # Deck A: more interaction, Deck B: more ramp
        deck_a = _make_deck(removal_count=15, ramp_count=3)
        deck_b = _make_deck(removal_count=3, ramp_count=15)
        deck_b.name = "Rampy"
        comp = compare_decks(deck_a, deck_b)
        # At least one side should have an advantage
        assert len(comp.a_advantages) > 0 or len(comp.b_advantages) > 0


# --- URL detection ---

class TestURLImport:
    def test_detect_moxfield(self):
        result = detect_url("https://www.moxfield.com/decks/abc123")
        assert result == ("moxfield", "abc123")

    def test_detect_archidekt(self):
        result = detect_url("https://archidekt.com/decks/12345")
        assert result == ("archidekt", "12345")

    def test_detect_invalid(self):
        assert detect_url("https://google.com") is None
        assert detect_url("not a url") is None

    def test_detect_moxfield_with_path(self):
        result = detect_url("https://www.moxfield.com/decks/my-deck_v2")
        assert result is not None
        assert result[0] == "moxfield"


# --- Calc CLI ---

class TestCalcCLI:
    def test_calc_basic(self):
        env = {**__import__("os").environ, "PYTHONIOENCODING": "utf-8"}
        r = subprocess.run(
            [PYTHON, "-m", "mtg_deck_engine.cli", "calc", "--deck", "99", "--copies", "1", "--turns", "5"],
            capture_output=True, timeout=10, env=env, encoding="utf-8", errors="replace",
        )
        assert r.returncode == 0
        assert "1" in r.stdout

    def test_calc_help(self):
        r = subprocess.run(
            [PYTHON, "-m", "mtg_deck_engine.cli", "calc", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 0
        assert "deck" in r.stdout.lower()
        assert "copies" in r.stdout.lower()


# --- Diff CLI ---

class TestDiffCLI:
    def test_diff_help(self):
        r = subprocess.run(
            [PYTHON, "-m", "mtg_deck_engine.cli", "diff", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 0
        assert "file_a" in r.stdout or "FILE_A" in r.stdout

    def test_diff_missing_files(self):
        r = subprocess.run(
            [PYTHON, "-m", "mtg_deck_engine.cli", "diff", "fake_a.txt", "fake_b.txt"],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 1


# --- Practice CLI ---

class TestPracticeCLI:
    def test_practice_help(self):
        r = subprocess.run(
            [PYTHON, "-m", "mtg_deck_engine.cli", "practice", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 0
        assert "rounds" in r.stdout.lower()
