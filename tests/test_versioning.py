"""Tests for deck versioning, diffing, impact analysis, and trends."""

import tempfile
from pathlib import Path

from mtg_deck_engine.versioning.impact import ImpactReport, analyze_impact
from mtg_deck_engine.versioning.storage import DeckDiff, DeckSnapshot, VersionStore, diff_versions
from mtg_deck_engine.versioning.trends import TrendReport, analyze_trends


def _tmp_store() -> VersionStore:
    """Create a version store in a temp directory."""
    tmp = tempfile.mkdtemp()
    return VersionStore(db_path=Path(tmp) / "test_versions.db")


# --- Storage tests ---


class TestVersionStore:
    def test_save_and_load(self):
        store = _tmp_store()
        snap = store.save_version(
            deck_id="test-deck",
            name="Test Deck",
            format="commander",
            decklist={"Sol Ring": 1, "Command Tower": 1, "Forest": 30},
            zones={"mainboard": ["Sol Ring", "Command Tower", "Forest"], "commander": ["Atraxa"]},
            scores={"mana_base": 85.0, "ramp": 70.0},
            metrics={"land_count": 31.0, "average_cmc": 3.2},
            notes="Initial build",
        )
        assert snap.version_number == 1
        assert snap.deck_id == "test-deck"

        loaded = store.get_version("test-deck", 1)
        assert loaded is not None
        assert loaded.decklist["Sol Ring"] == 1
        assert loaded.scores["mana_base"] == 85.0
        assert loaded.notes == "Initial build"
        store.close()

    def test_multiple_versions(self):
        store = _tmp_store()
        store.save_version("d1", "Deck 1", "commander",
                           {"Sol Ring": 1, "Forest": 35}, {}, {"curve": 80.0}, {})
        store.save_version("d1", "Deck 1", "commander",
                           {"Sol Ring": 1, "Forest": 36, "Rampant Growth": 1}, {},
                           {"curve": 82.0}, {})
        store.save_version("d1", "Deck 1", "commander",
                           {"Sol Ring": 1, "Forest": 37, "Rampant Growth": 1, "Cultivate": 1}, {},
                           {"curve": 85.0}, {})

        versions = store.get_all_versions("d1")
        assert len(versions) == 3
        assert versions[0].version_number == 1
        assert versions[2].version_number == 3
        store.close()

    def test_get_latest(self):
        store = _tmp_store()
        store.save_version("d1", "Deck", None, {"A": 1}, {})
        store.save_version("d1", "Deck", None, {"A": 1, "B": 1}, {})
        latest = store.get_latest("d1")
        assert latest is not None
        assert latest.version_number == 2
        assert "B" in latest.decklist
        store.close()

    def test_list_decks(self):
        store = _tmp_store()
        store.save_version("deck-a", "Alpha", "commander", {"A": 1}, {})
        store.save_version("deck-b", "Beta", "modern", {"B": 4}, {})
        decks = store.list_decks()
        assert len(decks) == 2
        ids = [d["deck_id"] for d in decks]
        assert "deck-a" in ids
        assert "deck-b" in ids
        store.close()

    def test_delete_deck(self):
        store = _tmp_store()
        store.save_version("d1", "Deck", None, {"A": 1}, {})
        store.save_version("d1", "Deck", None, {"A": 2}, {})
        store.delete_deck("d1")
        assert store.get_all_versions("d1") == []
        assert len(store.list_decks()) == 0
        store.close()

    def test_missing_version(self):
        store = _tmp_store()
        assert store.get_version("nonexistent", 1) is None
        assert store.get_latest("nonexistent") is None
        store.close()


# --- Diff tests ---


class TestDiffVersions:
    def test_added_cards(self):
        a = DeckSnapshot(deck_id="d", version_number=1, decklist={"Sol Ring": 1, "Forest": 30})
        b = DeckSnapshot(deck_id="d", version_number=2, decklist={"Sol Ring": 1, "Forest": 30, "Cultivate": 1})
        diff = diff_versions(a, b)
        assert diff.added == {"Cultivate": 1}
        assert diff.total_added == 1
        assert diff.total_removed == 0

    def test_removed_cards(self):
        a = DeckSnapshot(deck_id="d", version_number=1, decklist={"Sol Ring": 1, "Forest": 30, "Bad Card": 1})
        b = DeckSnapshot(deck_id="d", version_number=2, decklist={"Sol Ring": 1, "Forest": 30})
        diff = diff_versions(a, b)
        assert diff.removed == {"Bad Card": 1}
        assert diff.total_removed == 1

    def test_changed_quantity(self):
        a = DeckSnapshot(deck_id="d", version_number=1, decklist={"Lightning Bolt": 3, "Mountain": 20})
        b = DeckSnapshot(deck_id="d", version_number=2, decklist={"Lightning Bolt": 4, "Mountain": 20})
        diff = diff_versions(a, b)
        assert diff.changed_qty == {"Lightning Bolt": (3, 4)}
        assert diff.total_added == 1

    def test_no_changes(self):
        a = DeckSnapshot(deck_id="d", version_number=1, decklist={"A": 1, "B": 2})
        b = DeckSnapshot(deck_id="d", version_number=2, decklist={"A": 1, "B": 2})
        diff = diff_versions(a, b)
        assert len(diff.added) == 0
        assert len(diff.removed) == 0
        assert len(diff.changed_qty) == 0

    def test_score_deltas(self):
        a = DeckSnapshot(deck_id="d", version_number=1, decklist={}, scores={"curve": 70.0, "ramp": 60.0})
        b = DeckSnapshot(deck_id="d", version_number=2, decklist={}, scores={"curve": 80.0, "ramp": 55.0})
        diff = diff_versions(a, b)
        assert diff.score_deltas["curve"] == 10.0
        assert diff.score_deltas["ramp"] == -5.0


# --- Impact analysis tests ---


class TestImpactAnalysis:
    def test_improved(self):
        a = DeckSnapshot(deck_id="d", version_number=1, decklist={},
                         scores={"mana_base": 60, "curve": 65, "ramp": 50},
                         metrics={"land_count": 30})
        b = DeckSnapshot(deck_id="d", version_number=2, decklist={},
                         scores={"mana_base": 80, "curve": 82, "ramp": 75},
                         metrics={"land_count": 36})
        diff = diff_versions(a, b)
        impact = analyze_impact(a, b, diff)
        assert impact.overall_verdict == "improved"
        assert len(impact.improvements) > 0

    def test_regressed(self):
        a = DeckSnapshot(deck_id="d", version_number=1, decklist={},
                         scores={"mana_base": 85, "curve": 90, "ramp": 80},
                         metrics={})
        b = DeckSnapshot(deck_id="d", version_number=2, decklist={},
                         scores={"mana_base": 60, "curve": 55, "ramp": 40},
                         metrics={})
        diff = diff_versions(a, b)
        impact = analyze_impact(a, b, diff)
        assert impact.overall_verdict == "regressed"
        assert len(impact.regressions) > 0

    def test_neutral(self):
        a = DeckSnapshot(deck_id="d", version_number=1, decklist={},
                         scores={"curve": 80}, metrics={})
        b = DeckSnapshot(deck_id="d", version_number=2, decklist={},
                         scores={"curve": 81}, metrics={})
        diff = diff_versions(a, b)
        impact = analyze_impact(a, b, diff)
        assert impact.overall_verdict == "neutral"


# --- Trend tests ---


class TestTrends:
    def test_improving_trend(self):
        snapshots = [
            DeckSnapshot(deck_id="d", version_number=i, scores={"curve": float(60 + i * 5)}, metrics={})
            for i in range(1, 6)
        ]
        report = analyze_trends(snapshots)
        assert "curve" in report.score_trends
        trend = report.score_trends["curve"]
        assert trend.direction == "improving"
        assert trend.delta_first_to_last > 0

    def test_declining_trend(self):
        snapshots = [
            DeckSnapshot(deck_id="d", version_number=i, scores={"ramp": float(80 - i * 5)}, metrics={})
            for i in range(1, 6)
        ]
        report = analyze_trends(snapshots)
        trend = report.score_trends["ramp"]
        assert trend.direction == "declining"

    def test_stable_trend(self):
        snapshots = [
            DeckSnapshot(deck_id="d", version_number=i, scores={"curve": 75.0}, metrics={})
            for i in range(1, 6)
        ]
        report = analyze_trends(snapshots)
        trend = report.score_trends["curve"]
        assert trend.direction == "stable"

    def test_suggestions_generated(self):
        snapshots = [
            DeckSnapshot(deck_id="d", version_number=1, scores={"curve": 80.0, "ramp": 70.0}, metrics={}),
            DeckSnapshot(deck_id="d", version_number=2, scores={"curve": 75.0, "ramp": 65.0}, metrics={}),
            DeckSnapshot(deck_id="d", version_number=3, scores={"curve": 70.0, "ramp": 60.0}, metrics={}),
        ]
        report = analyze_trends(snapshots)
        assert len(report.suggestions) > 0

    def test_empty_versions(self):
        report = analyze_trends([])
        assert report.total_versions == 0
        assert len(report.score_trends) == 0

    def test_single_version(self):
        report = analyze_trends([
            DeckSnapshot(deck_id="d", version_number=1, scores={"curve": 80.0}, metrics={})
        ])
        assert report.total_versions == 1
        trend = report.score_trends["curve"]
        assert trend.direction == "stable"
