"""Tests for Phase 6: format profiles, advanced heuristics, export, benchmark suites."""

import json
import tempfile
from pathlib import Path

from mtg_deck_engine.analysis.advanced import (
    AdvancedReport,
    analyze_pips,
    analyze_win_conditions,
    detect_synergies,
    grade_mana_base,
    run_advanced_analysis,
)
from mtg_deck_engine.benchmarks.suites import (
    BUILTIN_SUITES,
    get_suite,
    list_suites,
    load_suite,
    save_suite,
)
from mtg_deck_engine.export.exporter import export_html, export_json, export_markdown
from mtg_deck_engine.formats.profiles import (
    DeckArchetype,
    detect_archetype,
    format_recommendations,
    get_format_profile,
)
from mtg_deck_engine.models import (
    AnalysisResult,
    Card,
    CardLayout,
    CardTag,
    Deck,
    DeckEntry,
    Format,
    ValidationIssue,
    Zone,
)


def _make_card(name, is_land=False, cmc=0.0, tags=None, mana_cost="", **kw):
    return Card(
        scryfall_id=f"id-{name}",
        oracle_id=f"oracle-{name}",
        name=name,
        layout=CardLayout.NORMAL,
        cmc=cmc,
        is_land=is_land,
        mana_cost=mana_cost,
        tags=tags or [],
        **kw,
    )


def _make_entry(name, qty=1, zone=Zone.MAINBOARD, **kw):
    card = _make_card(name, **kw)
    return DeckEntry(card_name=name, quantity=qty, zone=zone, card=card)


def _make_commander_deck():
    entries = [
        _make_entry("Commander", zone=Zone.COMMANDER, cmc=4, tags=[CardTag.FINISHER], mana_cost="{2}{G}{W}"),
    ]
    for i in range(36):
        entries.append(_make_entry(f"Land{i}", is_land=True))
    for i in range(8):
        entries.append(_make_entry(f"Rock{i}", cmc=2, tags=[CardTag.MANA_ROCK, CardTag.RAMP],
                                   is_artifact=True, mana_cost="{2}"))
    for i in range(5):
        entries.append(_make_entry(f"Sac{i}", cmc=2, tags=[CardTag.SACRIFICE_OUTLET], mana_cost="{1}{B}"))
    for i in range(5):
        entries.append(_make_entry(f"Payoff{i}", cmc=3, tags=[CardTag.ARISTOCRAT_PAYOFF], mana_cost="{2}{B}"))
    for i in range(5):
        entries.append(_make_entry(f"Token{i}", cmc=3, tags=[CardTag.TOKEN_MAKER], mana_cost="{2}{W}"))
    for i in range(5):
        entries.append(_make_entry(f"Removal{i}", cmc=2, tags=[CardTag.TARGETED_REMOVAL],
                                   is_instant=True, mana_cost="{1}{W}"))
    for i in range(3):
        entries.append(_make_entry(f"Draw{i}", cmc=3, tags=[CardTag.CARD_DRAW], mana_cost="{2}{U}"))
    for i in range(3):
        entries.append(_make_entry(f"Finisher{i}", cmc=6, tags=[CardTag.FINISHER],
                                   power="7", toughness="7", is_creature=True, mana_cost="{4}{G}{G}"))
    filler = 99 - 36 - 8 - 5 - 5 - 5 - 5 - 3 - 3
    for i in range(filler):
        entries.append(_make_entry(f"Filler{i}", cmc=3, tags=[CardTag.RECURSION], mana_cost="{2}{B}"))
    return Deck(name="Test P6", format=Format.COMMANDER, entries=entries)


# --- Format profiles ---

class TestFormatProfiles:
    def test_get_commander_profile(self):
        p = get_format_profile(Format.COMMANDER)
        assert p is not None
        assert p.targets.lands == (35, 38)
        assert p.targets.singleton is True

    def test_get_modern_profile(self):
        p = get_format_profile(Format.MODERN)
        assert p is not None
        assert p.targets.lands == (20, 24)
        assert p.targets.singleton is False

    def test_missing_format(self):
        assert get_format_profile(Format.ALCHEMY) is None

    def test_detect_archetype_aristocrats(self):
        deck = _make_commander_deck()
        arch = detect_archetype(deck)
        assert arch in (DeckArchetype.ARISTOCRATS, DeckArchetype.TOKENS, DeckArchetype.REANIMATOR)

    def test_detect_archetype_unknown_no_format(self):
        deck = Deck(name="Empty", format=None, entries=[])
        assert detect_archetype(deck) == DeckArchetype.UNKNOWN

    def test_format_recommendations(self):
        deck = _make_commander_deck()
        recs = format_recommendations(deck, DeckArchetype.ARISTOCRATS)
        # May or may not have recs depending on counts
        assert isinstance(recs, list)


# --- Advanced heuristics ---

class TestAdvancedHeuristics:
    def test_pip_analysis(self):
        deck = _make_commander_deck()
        pa = analyze_pips(deck)
        assert pa.total_pips > 0
        assert len(pa.pips_by_color) > 0

    def test_pip_analysis_heaviest_color(self):
        deck = _make_commander_deck()
        pa = analyze_pips(deck)
        assert pa.heaviest_color in ("W", "U", "B", "R", "G")

    def test_synergy_detection(self):
        deck = _make_commander_deck()
        synergies = detect_synergies(deck)
        assert len(synergies) > 0
        # Should find sac outlet + payoff synergy
        reasons = [s.reason for s in synergies]
        assert any("sacrifice" in r.lower() or "death" in r.lower() or "token" in r.lower() for r in reasons)

    def test_win_con_analysis(self):
        deck = _make_commander_deck()
        wca = analyze_win_conditions(deck)
        assert wca.total_win_cons >= 3  # Commander + finishers
        assert 0 <= wca.concentration <= 1.0
        assert 0 <= wca.diversity_score <= 1.0

    def test_mana_base_grade(self):
        deck = _make_commander_deck()
        pa = analyze_pips(deck)
        sources = {"W": 10, "U": 5, "B": 12, "G": 10}
        grade, notes = grade_mana_base(deck, pa, sources)
        assert grade in ("A+", "A", "B+", "B", "C+", "C", "D", "F")
        assert len(notes) > 0

    def test_combined_advanced(self):
        deck = _make_commander_deck()
        report = run_advanced_analysis(deck, {"W": 10, "B": 12, "G": 10, "U": 5})
        assert isinstance(report, AdvancedReport)
        assert report.mana_base_grade != ""
        assert len(report.synergies) > 0


# --- Export ---

class TestExport:
    def _sample_result(self):
        return AnalysisResult(
            deck_name="Export Test",
            format="commander",
            total_cards=100,
            mana_curve={0: 5, 1: 10, 2: 15, 3: 12, 4: 8, 5: 5, 6: 3, 7: 2},
            average_cmc=3.1,
            color_distribution={"W": 20, "B": 25, "G": 15},
            color_sources={"W": 12, "B": 14, "G": 10},
            type_distribution={"Creature": 25, "Land": 36, "Instant": 10},
            tag_distribution={"ramp": 10, "card_draw": 8},
            land_count=36,
            nonland_count=64,
            ramp_count=10,
            interaction_count=8,
            draw_engine_count=8,
            threat_count=12,
            scores={"mana_base": 85, "curve": 80, "ramp": 75},
            issues=[ValidationIssue(severity="warning", message="Low wipe count")],
            recommendations=["Add more board wipes"],
        )

    def test_export_json(self):
        result = self._sample_result()
        output = export_json(result)
        data = json.loads(output)
        assert data["deck_name"] == "Export Test"
        assert data["total_cards"] == 100
        assert "scores" in data

    def test_export_json_to_file(self):
        result = self._sample_result()
        tmp = Path(tempfile.mkdtemp()) / "report.json"
        export_json(result, path=tmp)
        assert tmp.exists()
        data = json.loads(tmp.read_text())
        assert data["deck_name"] == "Export Test"

    def test_export_markdown(self):
        result = self._sample_result()
        output = export_markdown(result, archetype="aristocrats")
        assert "# Export Test" in output
        assert "Detected Archetype" in output
        assert "Recommendations" in output

    def test_export_html(self):
        result = self._sample_result()
        output = export_html(result, archetype="control")
        assert "<!DOCTYPE html>" in output
        assert "Export Test" in output
        assert "<table>" in output

    def test_export_html_to_file(self):
        result = self._sample_result()
        tmp = Path(tempfile.mkdtemp()) / "report.html"
        export_html(result, path=tmp)
        assert tmp.exists()
        content = tmp.read_text(encoding="utf-8")
        assert "<html" in content


# --- Benchmark suites ---

class TestBenchmarkSuites:
    def test_builtin_suites_exist(self):
        names = list_suites()
        assert len(names) >= 5
        assert "casual-commander" in names
        assert "cedh" in names

    def test_get_suite(self):
        suite = get_suite("cedh")
        assert suite is not None
        assert len(suite.archetypes) >= 3
        assert suite.description != ""

    def test_get_suite_missing(self):
        assert get_suite("nonexistent") is None

    def test_save_and_load_suite(self):
        suite = get_suite("casual-commander")
        tmp = Path(tempfile.mkdtemp()) / "test_suite.json"
        save_suite(suite, tmp)
        assert tmp.exists()

        loaded = load_suite(tmp)
        assert loaded.name == suite.name
        assert len(loaded.archetypes) == len(suite.archetypes)

    def test_suite_weights_applied(self):
        suite = get_suite("cedh")
        turbo = next((a for a in suite.archetypes if a.name.value == "turbo"), None)
        assert turbo is not None
        assert turbo.meta_weight == 3.0  # cedh weights turbo at 3.0
