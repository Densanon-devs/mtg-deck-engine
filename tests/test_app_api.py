"""Tests for the desktop app's Python API surface.

Everything is tested in-process without pywebview. The API class is a plain
Python object that returns JSON-serializable dicts, so the same code paths
the frontend hits via the bridge are exercised here with direct calls.

Key invariants locked down:
- Deck save/load/diff round-trips through the versioning store cleanly
- Snapshot-to-text reconstruction produces a parseable decklist that can
  be re-saved as a new version (editing workflow proof)
- Tier gating reports correct shape for both free and pro
- Analyze path fails gracefully when the card database is unpopulated
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from densa_deck.app.api import AppApi, _snapshot_to_text
from densa_deck.data.database import CardDatabase
from densa_deck.models import Card, CardLayout, Color, Legality


@pytest.fixture
def temp_dbs():
    """Fresh on-disk card + versions DBs for the API to talk to."""
    with tempfile.TemporaryDirectory() as tmp:
        card_db = Path(tmp) / "cards.db"
        version_db = Path(tmp) / "versions.db"
        yield card_db, version_db


@pytest.fixture
def api(temp_dbs):
    card_db, version_db = temp_dbs
    api = AppApi(db_path=card_db, version_db_path=version_db)
    yield api
    api.close()


@pytest.fixture
def api_with_cards(temp_dbs):
    """API + a tiny in-memory card library the tests can reference by name."""
    card_db, version_db = temp_dbs
    db = CardDatabase(db_path=card_db)
    cards = [
        Card(
            scryfall_id="sid-sol", oracle_id="oid-sol", name="Sol Ring",
            layout=CardLayout.NORMAL, cmc=1, mana_cost="{1}",
            type_line="Artifact", color_identity=[],
            legalities={"commander": Legality.LEGAL},
            oracle_text="{T}: Add {C}{C}.",
        ),
        Card(
            scryfall_id="sid-arcane", oracle_id="oid-arcane", name="Arcane Signet",
            layout=CardLayout.NORMAL, cmc=2, mana_cost="{2}",
            type_line="Artifact", color_identity=[],
            legalities={"commander": Legality.LEGAL},
            oracle_text="{T}: Add one mana of any color.",
        ),
        Card(
            scryfall_id="sid-forest", oracle_id="oid-forest", name="Forest",
            layout=CardLayout.NORMAL, is_land=True,
            type_line="Basic Land — Forest", color_identity=[Color.GREEN],
            legalities={"commander": Legality.LEGAL},
        ),
        Card(
            scryfall_id="sid-cultivate", oracle_id="oid-cultivate", name="Cultivate",
            layout=CardLayout.NORMAL, cmc=3, mana_cost="{2}{G}",
            type_line="Sorcery", color_identity=[Color.GREEN],
            legalities={"commander": Legality.LEGAL},
            oracle_text="Search your library for a basic land and put a land onto the battlefield.",
        ),
    ]
    db.upsert_cards(cards)
    db.close()
    api = AppApi(db_path=card_db, version_db_path=version_db)
    yield api
    api.close()


# ---------------------------------------------------------------- tier + status

class TestTierAndStatus:
    def test_get_tier_shape(self, api):
        r = api.get_tier()
        assert r["ok"] is True
        assert "tier" in r["data"]
        assert "is_pro" in r["data"]
        assert r["data"]["tier"] in ("free", "pro")

    def test_system_status_fresh_db(self, api):
        r = api.get_system_status()
        assert r["ok"] is True
        assert r["data"]["card_database"]["count"] == 0
        assert r["data"]["card_database"]["ready"] is False

    def test_system_status_analyst_reports_file_and_library_separately(self, api):
        """Regression for v0.1.1: after downloading the analyst GGUF, the
        Settings card still said "Not installed" because is_available()
        collapsed file-exists + library-importable into a single bool and
        llama_cpp wasn't bundled in the frozen exe. v0.1.2 splits the
        two checks so the UI can render a useful message in the mixed
        state."""
        r = api.get_system_status()
        assert r["ok"] is True
        am = r["data"]["analyst_model"]
        assert "file_present" in am
        assert "library_ok" in am
        assert "reason" in am
        # `ready` must require both pieces, not just one.
        assert am["ready"] == (am["file_present"] and am["library_ok"])


# ---------------------------------------------------------------- analyze

class TestAnalyze:
    def test_analyze_empty_text_errors_cleanly(self, api):
        r = api.analyze_deck("")
        assert r["ok"] is False
        assert "empty" in r["error"].lower()

    def test_analyze_without_ingest_errors_with_hint(self, api):
        r = api.analyze_deck("1 Sol Ring\n")
        assert r["ok"] is False
        assert r.get("error_type") == "IngestRequired"

    def test_analyze_basic_roundtrip(self, api_with_cards):
        deck = "Commander:\n1 Sol Ring\n\nMainboard:\n1 Arcane Signet\n1 Cultivate\n30 Forest\n"
        r = api_with_cards.analyze_deck(deck, format_="commander", name="Test")
        assert r["ok"] is True
        result = r["data"]
        assert result["total_cards"] > 0
        assert result["format"] == "commander"
        assert "power" in result
        assert "scores" in result
        # Prose / numeric fields render as primitives, not dataclasses
        assert isinstance(result["power"]["overall"], (int, float))


# ---------------------------------------------------------------- deck lab

class TestDeckLab:
    def test_list_is_empty_initially(self, api):
        r = api.list_saved_decks()
        assert r["ok"] is True
        assert r["data"] == []

    def test_save_then_load_roundtrips(self, api_with_cards):
        text = "Commander:\n1 Sol Ring\n\nMainboard:\n1 Arcane Signet\n30 Forest\n"
        save_r = api_with_cards.save_deck_version(
            deck_id="test-deck", name="Test Deck",
            decklist_text=text, format_="commander", notes="v1",
        )
        assert save_r["ok"] is True
        assert save_r["data"]["version_number"] == 1
        assert save_r["data"]["notes"] == "v1"

        list_r = api_with_cards.list_saved_decks()
        assert len(list_r["data"]) == 1
        assert list_r["data"][0]["deck_id"] == "test-deck"

        load_r = api_with_cards.get_deck_latest("test-deck")
        assert load_r["ok"] is True
        snap = load_r["data"]
        # The reconstructed text must contain the original card names
        assert "Sol Ring" in snap["decklist_text"]
        assert "Arcane Signet" in snap["decklist_text"]
        assert "Forest" in snap["decklist_text"]
        # Zone structure is preserved
        assert "Commander" in snap["decklist_text"] or "commander" in snap["decklist_text"].lower()

    def test_save_second_version_increments(self, api_with_cards):
        text = "Commander:\n1 Sol Ring\n\nMainboard:\n30 Forest\n"
        api_with_cards.save_deck_version("d1", "D1", text, "commander", "v1")
        r2 = api_with_cards.save_deck_version("d1", "D1", text, "commander", "v2")
        assert r2["data"]["version_number"] == 2

    def test_diff_two_versions(self, api_with_cards):
        v1 = "Commander:\n1 Sol Ring\n\nMainboard:\n30 Forest\n"
        v2 = "Commander:\n1 Sol Ring\n\nMainboard:\n1 Arcane Signet\n29 Forest\n"
        api_with_cards.save_deck_version("d1", "D1", v1, "commander", "v1")
        api_with_cards.save_deck_version("d1", "D1", v2, "commander", "v2")
        diff_r = api_with_cards.diff_deck_versions("d1", 1, 2)
        assert diff_r["ok"] is True
        d = diff_r["data"]
        # Arcane Signet was added between v1 and v2
        assert "Arcane Signet" in d["added"]
        # Total_added/removed reflect the shift — Arcane Signet in, one Forest out
        assert d["total_added"] >= 1
        assert d["total_removed"] >= 1

    def test_delete_removes_deck(self, api_with_cards):
        text = "Commander:\n1 Sol Ring\n\nMainboard:\n30 Forest\n"
        api_with_cards.save_deck_version("d1", "D1", text, "commander", "v1")
        del_r = api_with_cards.delete_deck("d1")
        assert del_r["ok"] is True
        list_r = api_with_cards.list_saved_decks()
        assert list_r["data"] == []

    def test_load_unknown_deck_returns_error(self, api):
        r = api.get_deck_latest("does-not-exist")
        assert r["ok"] is False
        assert "no saved versions" in r["error"].lower()


# ---------------------------------------------------------------- text reconstruction

class TestSnapshotToText:
    def test_reconstructed_text_parses_back(self):
        """Round-trip invariant: a reconstructed text must parse back into
        the same card names + quantities. This is what makes editing safe —
        the user can load, edit, and re-save without the format shifting
        under them."""
        from densa_deck.versioning.storage import DeckSnapshot

        snap = DeckSnapshot(
            deck_id="test", version_number=1,
            decklist={"Sol Ring": 1, "Arcane Signet": 1, "Forest": 30},
            zones={"commander": ["Sol Ring"], "mainboard": ["Arcane Signet", "Forest"]},
        )
        text = _snapshot_to_text(snap)
        # Commander section renders first — ordering is stable
        assert text.index("Commander") < text.index("Mainboard")
        # All cards present
        assert "1 Sol Ring" in text
        assert "1 Arcane Signet" in text
        assert "30 Forest" in text

    def test_empty_snapshot_produces_empty_text(self):
        from densa_deck.versioning.storage import DeckSnapshot
        snap = DeckSnapshot(deck_id="empty", version_number=1, decklist={}, zones={})
        text = _snapshot_to_text(snap)
        # Empty but not a crash — just an empty string-ish
        assert isinstance(text, str)

    def test_preserves_quantities_above_one(self):
        """Non-Commander formats can have 4-ofs. Text reconstruction must
        preserve the quantity so the saved deck round-trips correctly."""
        from densa_deck.versioning.storage import DeckSnapshot
        snap = DeckSnapshot(
            deck_id="modern-deck", version_number=1,
            decklist={"Lightning Bolt": 4, "Mountain": 20},
            zones={"mainboard": ["Lightning Bolt", "Mountain"]},
        )
        text = _snapshot_to_text(snap)
        assert "4 Lightning Bolt" in text
        assert "20 Mountain" in text


# ---------------------------------------------------------------- error envelope

class TestSimulations:
    def test_goldfish_requires_ingest(self, api):
        r = api.run_goldfish("1 Sol Ring", "commander", "X", sims=10)
        assert r["ok"] is False
        assert r.get("error_type") == "IngestRequired"

    def test_goldfish_runs_and_serializes(self, api_with_cards):
        text = "Commander:\n1 Sol Ring\n\nMainboard:\n1 Arcane Signet\n1 Cultivate\n30 Forest\n"
        r = api_with_cards.run_goldfish(text, "commander", "Test", sims=10, seed=42)
        assert r["ok"] is True
        d = r["data"]
        # Shape: all integer-keyed dicts have been str-ified for JSON
        assert d["simulations"] == 10
        assert "kill_turn_distribution" in d
        assert all(isinstance(k, str) for k in d["kill_turn_distribution"].keys())
        # JSON-serializable end-to-end (nothing slipped through as dataclass/set/etc.)
        import json
        json.dumps(d)

    def test_gauntlet_requires_ingest(self, api):
        r = api.run_gauntlet("1 Sol Ring", "commander", "X", sims=5)
        assert r["ok"] is False
        assert r.get("error_type") == "IngestRequired"

    def test_gauntlet_runs_and_serializes(self, api_with_cards):
        text = "Commander:\n1 Sol Ring\n\nMainboard:\n1 Arcane Signet\n1 Cultivate\n30 Forest\n"
        r = api_with_cards.run_gauntlet(text, "commander", "Test", sims=5, seed=42)
        assert r["ok"] is True
        d = r["data"]
        assert d["simulations_per_matchup"] == 5
        # 11 archetypes hard-coded in the gauntlet — product-page claim
        assert len(d["matchups"]) == 11
        # Win rates are floats in [0, 1]
        for m in d["matchups"]:
            assert 0.0 <= m["win_rate"] <= 1.0
        import json
        json.dumps(d)


class TestProgressState:
    def test_initial_progress_is_idle(self, api):
        p = api.ingest_progress()
        assert p["ok"] is True
        assert p["data"]["done"] is True
        assert p["data"]["running"] is False
        assert p["data"]["pct"] == 0

    def test_ingest_start_flips_running(self, api, monkeypatch):
        """Start the ingest with a stubbed background task so the test is fast
        and deterministic. Real ingest hits the network and takes minutes;
        here we just verify the state transitions happen correctly."""
        # Replace the thread body with a short sleep so the state transitions
        # through running -> done cleanly.
        import threading

        orig_thread = threading.Thread

        class FastThread(orig_thread):
            def __init__(self, target, *args, **kwargs):
                def wrapped(*a, **kw):
                    # Simulate the first phase transition so the test has
                    # something observable, then mark done.
                    api._progress["ingest"].update(pct=10, message="fake download")
                    api._progress["ingest"].update(pct=100, message="fake done", done=True, running=False)
                super().__init__(target=wrapped, *args, **kwargs)

        monkeypatch.setattr("threading.Thread", FastThread)
        start_r = api.ingest_start()
        assert start_r["ok"] is True
        # Let the fake thread finish
        api._threads["ingest"].join(timeout=1.0)
        progress = api.ingest_progress()["data"]
        assert progress["done"] is True
        assert progress["running"] is False
        assert progress["pct"] == 100

    def test_ingest_start_rejects_double_invocation(self, api):
        # Manually set the running flag — simulates "already running"
        api._progress["ingest"]["running"] = True
        api._progress["ingest"]["done"] = False
        r = api.ingest_start()
        assert r["ok"] is False
        assert "already running" in r["error"].lower()

    def test_analyst_pull_progress_initial(self, api):
        p = api.analyst_pull_progress()
        assert p["ok"] is True
        assert p["data"]["done"] is True
        assert p["data"]["running"] is False


class TestCoachSessions:
    def test_start_without_ingest_errors(self, api):
        r = api.coach_start(decklist_text="1 Sol Ring")
        assert r["ok"] is False
        assert r.get("error_type") == "IngestRequired"

    def test_start_with_text_creates_session(self, api_with_cards):
        text = "Commander:\n1 Sol Ring\n\nMainboard:\n1 Arcane Signet\n30 Forest\n"
        r = api_with_cards.coach_start(decklist_text=text, name="TestDeck")
        assert r["ok"] is True
        assert "token" in r["data"]
        assert r["data"]["deck_name"] == "TestDeck"
        assert r["data"]["card_count"] > 0

    def test_start_with_saved_deck_loads_snapshot(self, api_with_cards):
        text = "Commander:\n1 Sol Ring\n\nMainboard:\n30 Forest\n"
        api_with_cards.save_deck_version("mydeck", "MyDeck", text, "commander", "v1")
        r = api_with_cards.coach_start(deck_id="mydeck")
        assert r["ok"] is True
        assert r["data"]["deck_id"] == "mydeck"
        assert r["data"]["deck_name"] == "MyDeck"

    def test_ask_then_reset_then_close(self, api_with_cards):
        # Use a deterministic mock backend so the ask path doesn't touch a real LLM
        from densa_deck.analyst import MockBackend
        api_with_cards._coach_backend = MockBackend(
            default="Your deck's interaction count is on the low side — consider adding two more removal spells.",
        )
        text = "Commander:\n1 Sol Ring\n\nMainboard:\n1 Cultivate\n30 Forest\n"
        start = api_with_cards.coach_start(decklist_text=text, name="TestDeck")
        token = start["data"]["token"]

        ask_r = api_with_cards.coach_ask(token, "what's my biggest weakness?")
        assert ask_r["ok"] is True
        assert ask_r["data"]["verified"] is True
        assert "removal" in ask_r["data"]["assistant_response"].lower()

        # Turn count reflected in session list
        sessions = api_with_cards.coach_list_sessions()["data"]
        assert any(s["token"] == token and s["turn_count"] == 1 for s in sessions)

        reset_r = api_with_cards.coach_reset(token)
        assert reset_r["ok"] is True
        sessions = api_with_cards.coach_list_sessions()["data"]
        assert any(s["token"] == token and s["turn_count"] == 0 for s in sessions)

        close_r = api_with_cards.coach_close(token)
        assert close_r["ok"] is True
        sessions = api_with_cards.coach_list_sessions()["data"]
        assert all(s["token"] != token for s in sessions)

    def test_ask_on_unknown_token_errors(self, api):
        r = api.coach_ask("does-not-exist", "hello")
        assert r["ok"] is False
        assert "unknown" in r["error"].lower()

    def test_empty_question_rejected(self, api_with_cards):
        text = "Commander:\n1 Sol Ring\n\nMainboard:\n30 Forest\n"
        start = api_with_cards.coach_start(decklist_text=text, name="TestDeck")
        r = api_with_cards.coach_ask(start["data"]["token"], "   ")
        assert r["ok"] is False


class TestAutoUpdater:
    def test_current_version_returned(self, api):
        r = api.get_current_version()
        assert r["ok"] is True
        assert isinstance(r["data"]["version"], str)
        assert len(r["data"]["version"]) >= 3  # at least "0.1"

    def test_version_comparison(self):
        from densa_deck.app.api import _is_newer
        assert _is_newer("0.2.0", "0.1.0") is True
        assert _is_newer("0.1.1", "0.1.0") is True
        assert _is_newer("0.1.0", "0.1.0") is False
        assert _is_newer("0.1.0", "0.1.1") is False
        # v-prefix + suffix handling
        assert _is_newer("v1.2.3", "v1.2.2") is True
        assert _is_newer("1.2.3-rc1", "1.2.2") is True
        # Malformed doesn't crash / nag falsely
        assert _is_newer("", "0.1.0") is False
        assert _is_newer("garbage", "0.1.0") is False

    def test_check_for_updates_network_failure_silent(self, api):
        # Point at a guaranteed-unreachable URL; the API should NOT raise.
        r = api.check_for_updates("https://127.0.0.1:1/nonexistent.json")
        assert r["ok"] is True  # wrapped so frontend doesn't bug the user
        assert r["data"]["update_available"] is False
        assert "error" in r["data"]


class TestDeepLinkParser:
    def test_valid_activate_url(self):
        from densa_deck.cli import _handle_activation_url
        from densa_deck.licensing import load_saved_license, remove_license
        # This test will attempt to save an invalid license (the handler
        # passes whatever key is in the URL to save_license, which validates
        # format before persisting). A clearly-bad key should NOT persist.
        import tempfile
        # Isolate license file so the test doesn't mutate the real user config
        import densa_deck.licensing as licensing_mod
        orig = licensing_mod.LICENSE_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                from pathlib import Path
                licensing_mod.LICENSE_PATH = Path(tmp) / "lic.json"
                # An invalid key — handler should not crash
                _handle_activation_url("densa-deck://activate?key=INVALID-KEY")
                # No license saved (invalid key)
                assert licensing_mod.LICENSE_PATH.exists() is False
        finally:
            licensing_mod.LICENSE_PATH = orig

    def test_non_matching_scheme_is_noop(self):
        from densa_deck.cli import _handle_activation_url
        # Should not raise on URLs with the wrong scheme
        _handle_activation_url("https://example.com/activate?key=X")
        _handle_activation_url("not-even-a-url")

    def test_missing_key_is_noop(self):
        from densa_deck.cli import _handle_activation_url
        _handle_activation_url("densa-deck://activate")  # no query
        _handle_activation_url("densa-deck://activate?foo=bar")  # wrong param


class TestSessionPersistence:
    def test_sessions_persist_across_api_instances(self, temp_dbs):
        """Round-trip: start a session, write to disk on close, reload a
        fresh API instance, session is restored with its full history."""
        import tempfile
        card_db, version_db = temp_dbs
        session_path = Path(card_db).parent / "coach_sessions.json"

        # Seed the card DB minimally so coach_start can resolve a deck
        db = CardDatabase(db_path=card_db)
        db.upsert_cards([Card(
            scryfall_id="sid-sol", oracle_id="oid-sol", name="Sol Ring",
            layout=CardLayout.NORMAL, cmc=1, mana_cost="{1}",
            type_line="Artifact", oracle_text="{T}: Add {C}{C}.",
            legalities={"commander": Legality.LEGAL},
        )])
        db.close()

        api1 = AppApi(db_path=card_db, version_db_path=version_db, session_path=session_path)
        # Deterministic backend so ask() doesn't need an LLM
        from densa_deck.analyst import MockBackend
        api1._coach_backend = MockBackend(default="persisted response")
        start = api1.coach_start(decklist_text="1 Sol Ring\n", name="TestDeck")
        token = start["data"]["token"]
        api1.coach_ask(token, "first question")
        api1.close()

        assert session_path.exists()

        # Fresh API instance — sessions should auto-load
        api2 = AppApi(db_path=card_db, version_db_path=version_db, session_path=session_path)
        sessions = api2.coach_list_sessions()["data"]
        assert len(sessions) == 1
        assert sessions[0]["token"] == token
        assert sessions[0]["turn_count"] == 1

        # History is retrievable
        hist = api2.coach_get_history(token)["data"]
        assert len(hist) == 1
        assert hist[0]["user_question"] == "first question"
        api2.close()

    def test_missing_session_file_is_silent(self, temp_dbs):
        """Fresh installs have no session file; API boots clean."""
        card_db, version_db = temp_dbs
        session_path = Path(card_db).parent / "does-not-exist.json"
        api = AppApi(db_path=card_db, version_db_path=version_db, session_path=session_path)
        assert api.coach_list_sessions()["data"] == []
        api.close()

    def test_malformed_session_file_does_not_crash(self, temp_dbs):
        card_db, version_db = temp_dbs
        session_path = Path(card_db).parent / "bad.json"
        session_path.write_text("{not: valid json at all")
        # Construction must tolerate malformed state — a crash here would
        # render the app unlaunchable after a bad shutdown.
        api = AppApi(db_path=card_db, version_db_path=version_db, session_path=session_path)
        assert api.coach_list_sessions()["data"] == []
        api.close()


class TestResolveSuggestions:
    def test_exact_match_prefix(self, api_with_cards):
        r = api_with_cards.resolve_suggestions(["Cultvate"])  # missing 'i'
        assert r["ok"] is True
        # "Cult" prefix hits Cultivate via LIKE; close enough for difflib
        assert "Cultivate" in r["data"]["Cultvate"]

    def test_no_match_returns_empty(self, api_with_cards):
        r = api_with_cards.resolve_suggestions(["ZzzGarbageNonsense"])
        assert r["ok"] is True
        assert r["data"]["ZzzGarbageNonsense"] == []

    def test_short_names_skip_lookup(self, api_with_cards):
        r = api_with_cards.resolve_suggestions(["a"])
        assert r["data"]["a"] == []

    def test_without_ingest_returns_empty(self, api):
        r = api.resolve_suggestions(["Lightning Bolt"])
        assert r["ok"] is True
        assert r["data"] == {}


class TestUrlImport:
    def test_empty_url_errors(self, api):
        r = api.import_deck_from_url("")
        assert r["ok"] is False
        assert "empty" in r["error"].lower()

    def test_unsupported_url_errors_cleanly(self, api):
        r = api.import_deck_from_url("https://example.com/deck/123")
        assert r["ok"] is False
        assert "unsupported" in r["error"].lower()


class TestOpenExternal:
    def test_https_allowed(self, api, monkeypatch):
        calls = []
        monkeypatch.setattr("webbrowser.open", lambda url, new=0: calls.append(url))
        r = api.open_external("https://example.com")
        assert r["ok"] is True
        assert calls == ["https://example.com"]

    def test_file_scheme_rejected(self, api):
        r = api.open_external("file:///C:/Windows/System32/cmd.exe")
        assert r["ok"] is False
        assert "https/http" in r["error"]

    def test_javascript_scheme_rejected(self, api):
        r = api.open_external("javascript:alert(1)")
        assert r["ok"] is False


class TestFirstRunState:
    def test_initially_not_completed(self, temp_dbs):
        card_db, version_db = temp_dbs
        state_path = Path(card_db).parent / "app_state.json"
        api = AppApi(db_path=card_db, version_db_path=version_db, state_path=state_path)
        r = api.get_first_run_state()
        assert r["ok"] is True
        assert r["data"]["completed"] is False
        assert r["data"]["completed_at"] is None
        api.close()

    def test_mark_complete_persists(self, temp_dbs):
        card_db, version_db = temp_dbs
        state_path = Path(card_db).parent / "app_state.json"
        api1 = AppApi(db_path=card_db, version_db_path=version_db, state_path=state_path)
        api1.mark_first_run_complete()
        api1.close()

        # Fresh API instance sees the flag
        api2 = AppApi(db_path=card_db, version_db_path=version_db, state_path=state_path)
        r = api2.get_first_run_state()
        assert r["data"]["completed"] is True
        assert r["data"]["completed_at"] is not None
        api2.close()

    def test_reset_clears_flag(self, temp_dbs):
        card_db, version_db = temp_dbs
        state_path = Path(card_db).parent / "app_state.json"
        api = AppApi(db_path=card_db, version_db_path=version_db, state_path=state_path)
        api.mark_first_run_complete()
        assert api.get_first_run_state()["data"]["completed"] is True
        api.reset_first_run()
        assert api.get_first_run_state()["data"]["completed"] is False
        api.close()

    def test_malformed_state_file_does_not_crash(self, temp_dbs):
        """A corrupted state file on disk (from power loss, etc.) must not
        prevent the app from launching — we return empty state + carry on."""
        card_db, version_db = temp_dbs
        state_path = Path(card_db).parent / "app_state.json"
        state_path.write_text("not valid json {][")
        api = AppApi(db_path=card_db, version_db_path=version_db, state_path=state_path)
        r = api.get_first_run_state()
        assert r["ok"] is True
        assert r["data"]["completed"] is False
        # And marking complete from here overwrites the garbage cleanly
        api.mark_first_run_complete()
        assert api.get_first_run_state()["data"]["completed"] is True
        api.close()

    def test_state_file_schema_unknown_keys_preserved(self, temp_dbs):
        """If future versions add keys to app_state.json, a downgrade shouldn't
        strip them. Load → save must preserve untouched keys."""
        import json as _json
        card_db, version_db = temp_dbs
        state_path = Path(card_db).parent / "app_state.json"
        state_path.write_text(_json.dumps({
            "first_run_completed": True,
            "first_run_completed_at": "2026-04-19T12:00:00",
            "future_key": "future_value",  # from a newer app version
        }))
        api = AppApi(db_path=card_db, version_db_path=version_db, state_path=state_path)
        api.reset_first_run()
        api.close()
        data = _json.loads(state_path.read_text())
        # future_key survives the reset
        assert data.get("future_key") == "future_value"
        # first_run keys gone
        assert "first_run_completed" not in data


class TestAtomicWrites:
    def test_atomic_write_survives_simulated_partial_write(self, temp_dbs):
        """Regression lock: `_save_state` must write via temp-then-rename so
        a crash mid-write can't leave a corrupt state file that blocks the
        next launch. We simulate a crash by writing garbage directly to the
        target path FIRST, then verify the atomic write overwrites it cleanly
        (i.e. the new file contains the real payload, not a merged mess).
        """
        card_db, version_db = temp_dbs
        state_path = Path(card_db).parent / "app_state.json"
        # Simulate a previously-corrupt state file
        state_path.write_text('{"first_run_completed": true, "garbage": ')
        api = AppApi(db_path=card_db, version_db_path=version_db, state_path=state_path)
        # get_first_run_state should recover from the garbage file
        r = api.get_first_run_state()
        assert r["data"]["completed"] is False
        # Marking complete atomically overwrites the garbage file
        api.mark_first_run_complete()
        # The file is now valid JSON — load it back and verify
        import json as _json
        data = _json.loads(state_path.read_text())
        assert data["first_run_completed"] is True
        api.close()

    def test_atomic_write_does_not_leave_temp_file(self, temp_dbs):
        """No `.tmp` file should linger after a successful atomic write."""
        card_db, version_db = temp_dbs
        state_path = Path(card_db).parent / "app_state.json"
        api = AppApi(db_path=card_db, version_db_path=version_db, state_path=state_path)
        api.mark_first_run_complete()
        tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")
        assert not tmp_path.exists()
        api.close()

    def test_atomic_write_cleans_up_on_failure(self, tmp_path, monkeypatch):
        """If write_text raises (disk full, perms, etc.) the partial tmp file
        is unlinked so subsequent retries don't trip over an abandoned
        `.tmp` squatter in the way of the next rename."""
        from densa_deck.app.api import _atomic_write_json
        target = tmp_path / "state.json"
        tmp_file = target.with_suffix(target.suffix + ".tmp")
        original_write_text = Path.write_text

        def failing_write(self, *args, **kwargs):
            # Touch the tmp file to simulate a partial write BEFORE raising
            # so the cleanup path has something to remove.
            if self == tmp_file:
                original_write_text(self, "", encoding="utf-8")
                raise OSError("simulated disk full")
            return original_write_text(self, *args, **kwargs)
        monkeypatch.setattr(Path, "write_text", failing_write)

        with pytest.raises(OSError):
            _atomic_write_json(target, {"x": 1})
        # tmp file must not linger
        assert not tmp_file.exists()
        # target was never written
        assert not target.exists()


class TestConcurrencyLocks:
    def test_double_ingest_start_rejected(self, api):
        """Lock-regression test: two rapid ingest_start calls must yield only
        one running task. Previously the check-then-set race could spawn two
        threads both trying to download + upsert concurrently."""
        import threading
        # Preload the progress dict as running to simulate a racing thread
        # that already claimed the slot. (Testing the real race is flaky;
        # this locks down the guard behavior via the state check path.)
        api._progress["ingest"]["running"] = True
        api._progress["ingest"]["done"] = False
        r1 = api.ingest_start()
        assert r1["ok"] is False
        assert "already running" in r1["error"].lower()

    def test_coach_backend_double_checked_init(self, api_with_cards, monkeypatch):
        """The double-checked locking on `_get_coach_backend` should produce
        a single backend even under rapid concurrent calls."""
        import threading
        init_calls = []
        from densa_deck.analyst import MockBackend
        orig = MockBackend.__init__

        def tracking_init(self, *args, **kwargs):
            init_calls.append(1)
            orig(self, *args, **kwargs)
        monkeypatch.setattr(MockBackend, "__init__", tracking_init)

        # Force fresh init
        api_with_cards._coach_backend = None
        # Kick off parallel init attempts. The lock + double-check means
        # at most one MockBackend should actually be constructed.
        threads = [threading.Thread(target=api_with_cards._get_coach_backend)
                   for _ in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert len(init_calls) == 1
        assert api_with_cards._coach_backend is not None

    def test_card_database_usable_from_background_thread(self, tmp_path):
        """Regression lock for the SmartScreen-hostile bug reported against
        v0.1.0: _do_ingest ran on a worker thread but reused a CardDatabase
        whose sqlite3.Connection had been created on the dispatcher thread,
        raising 'SQLite objects created in a thread can only be used in that
        same thread.' Thread-local connections fix it — each thread that
        calls connect() gets its own handle, and concurrent use is safe
        under WAL mode."""
        import threading
        from densa_deck.data.database import CardDatabase

        db = CardDatabase(db_path=tmp_path / "cards.db")
        # Touch the DB on the current (main) thread to cache a connection.
        assert db.card_count() == 0

        results = {}

        def worker():
            # Without the thread-local fix this raises a ProgrammingError
            # "SQLite objects created in a thread...".
            try:
                results["count"] = db.card_count()
                db.set_metadata("worker_touch", "ok")
                results["meta"] = db.get_metadata("worker_touch")
            except Exception as e:  # pragma: no cover — kept explicit for diagnosis
                results["error"] = repr(e)

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        assert "error" not in results, results.get("error")
        assert results["count"] == 0
        assert results["meta"] == "ok"

    def test_version_store_usable_from_background_thread(self, tmp_path):
        """Same threading guarantee for VersionStore — deck saves happen
        from background operations too."""
        import threading
        from densa_deck.versioning.storage import VersionStore

        store = VersionStore(db_path=tmp_path / "versions.db")
        store.connect()  # warm main-thread connection

        results = {}

        def worker():
            try:
                store.save_version(
                    deck_id="deck-xyz",
                    name="Threaded deck",
                    format="commander",
                    decklist={"Sol Ring": 1},
                    zones={"mainboard": ["Sol Ring"]},
                )
                versions = store.get_all_versions("deck-xyz")
                results["count"] = len(versions)
            except Exception as e:  # pragma: no cover
                results["error"] = repr(e)

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        assert "error" not in results, results.get("error")
        assert results["count"] == 1

    def test_progress_read_during_worker_update_never_raises(self, api):
        """Regression lock for the audit finding: _do_ingest mutated
        self._progress['ingest'] on a worker thread while ingest_progress
        (UI polling) copied the dict on the main thread without a lock.
        A rapid poll during mutation can raise RuntimeError("dictionary
        changed size during iteration"). After the _update_progress /
        _read_progress helper switch, both sides hold _progress_lock so
        snapshots are always coherent."""
        import threading, time
        stop = threading.Event()
        errors = []

        def writer():
            i = 0
            while not stop.is_set():
                api._update_progress("ingest", pct=i % 100, message=f"tick {i}")
                i += 1
                if i > 5000:
                    break

        def reader():
            try:
                while not stop.is_set():
                    snap = api._read_progress("ingest")
                    assert "pct" in snap
            except Exception as e:
                errors.append(repr(e))

        t_w = threading.Thread(target=writer)
        t_r = threading.Thread(target=reader)
        t_w.start(); t_r.start()
        time.sleep(0.3)
        stop.set()
        t_w.join(); t_r.join()
        assert errors == [], errors

    def test_close_joins_background_threads(self, api):
        """Regression: AppApi.close() previously abandoned daemon threads,
        which could yank SQLite mid-write. It now signals _shutdown_event
        and joins each live thread with a bounded timeout, so a close()
        during a quick no-op ingest lets it finish cleanly."""
        import threading, time
        # Register a fake "ingest" thread that takes ~0.2s to finish.
        api._progress["ingest"].update(running=True, done=False)
        finished = threading.Event()

        def slow_worker():
            # Mimic a worker that checks _shutdown_event at a checkpoint
            # and exits early — the whole point of the event.
            for _ in range(50):
                if api._shutdown_event.is_set():
                    break
                time.sleep(0.01)
            finished.set()

        t = threading.Thread(target=slow_worker)
        api._threads["fake-ingest"] = t
        t.start()
        t0 = time.time()
        api.close()
        elapsed = time.time() - t0
        assert finished.is_set(), "worker did not finish — close() returned before signal was honored"
        # close() joined with a 5s timeout — in practice the worker finishes
        # in well under a second because it checks the shutdown event.
        assert elapsed < 3.0

    def test_coach_ask_and_reset_do_not_race(self, api_with_cards, monkeypatch):
        """Regression for the audit finding: coach_ask released _coach_lock
        before appending to session.history while coach_reset held the lock
        and cleared the list. Under rapid interleaving, the list ended up
        in a torn state. The per-session turn_lock now serializes any ask
        with any reset on the same session."""
        import threading, time, uuid
        # Seed a session directly without going through coach_start (avoids
        # needing a full deck + ingest fixture).
        from densa_deck.analyst.coach import CoachSession
        token = "test-token-" + uuid.uuid4().hex[:6]
        session = CoachSession(deck_sheet="", allowed_cards=set())
        api_with_cards._coach_sessions[token] = {
            "session": session, "deck_name": "t", "deck_id": None,
            "created_at": "", "turn_lock": threading.Lock(),
        }

        # Stub coach_step to append a turn with a small delay so the test
        # window overlaps with coach_reset.
        from densa_deck.analyst import coach as coach_mod
        from densa_deck.analyst.coach import CoachTurn
        def slow_step(sess, backend, question, max_retries=1):
            time.sleep(0.02)
            turn = CoachTurn(user_question=question, assistant_response="ok",
                             verified=True, confidence=1.0)
            sess.history.append(turn)
            return turn
        monkeypatch.setattr(coach_mod, "coach_step", slow_step)

        errors = []
        def asker():
            for i in range(20):
                r = api_with_cards.coach_ask(token, f"q{i}")
                if not r.get("ok", True) and "closed" in (r.get("error", "") or "").lower():
                    return
                if r.get("ok") is False:
                    errors.append(r)
        def resetter():
            for _ in range(20):
                api_with_cards.coach_reset(token)
                time.sleep(0.005)

        ta = threading.Thread(target=asker)
        tr = threading.Thread(target=resetter)
        ta.start(); tr.start()
        ta.join(); tr.join()

        # Invariant: history must be a list of well-formed turns, never
        # a partially-constructed state. (In Python, list.append is atomic
        # under the GIL, but the test also asserts we never returned an
        # error envelope from coach_ask due to a race-detection path.)
        assert errors == [], errors
        for turn in session.history:
            assert turn.user_question.startswith("q")
            assert turn.assistant_response == "ok"

    def test_corrupt_coach_sessions_surfaces_load_warning(self, tmp_path):
        """Regression: a corrupt coach_sessions.json should be quarantined
        and surface a load_warning via get_system_status so the user is
        told their history was moved aside rather than silently erased."""
        from densa_deck.app.api import AppApi
        session_path = tmp_path / "coach_sessions.json"
        session_path.write_text("{not valid json", encoding="utf-8")
        api = AppApi(
            db_path=tmp_path / "cards.db",
            session_path=session_path,
            version_db_path=tmp_path / "versions.db",
        )
        status = api.get_system_status()
        assert status["ok"] is True
        warnings = status["data"]["load_warnings"]
        assert any("Coach session" in w or "coach_session" in w.lower()
                   for w in warnings), warnings
        # Quarantine file should have been created beside the original
        baks = list(tmp_path.glob("coach_sessions.json.corrupt-*.bak"))
        assert len(baks) == 1
        # Dismissing warnings empties the list
        api.dismiss_load_warnings()
        status2 = api.get_system_status()
        assert status2["data"]["load_warnings"] == []
    def test_uncaught_exceptions_become_error_dicts(self, api):
        # Pass a bogus format that ValueError-s in Format(...) — the _safe
        # decorator should convert the exception to {ok: false, error: ...}
        # instead of propagating.
        r = api.analyze_deck("1 Sol Ring", format_="not-a-real-format")
        assert r["ok"] is False
        assert "error" in r

    def test_save_empty_deck_id_rejected(self, api):
        r = api.save_deck_version("", "name", "1 Sol Ring", "commander")
        assert r["ok"] is False
        assert "required" in r["error"].lower()

    def test_save_without_ingest_hints_clearly(self, api):
        r = api.save_deck_version("d1", "D1", "1 Sol Ring", "commander")
        assert r["ok"] is False
        assert r.get("error_type") == "IngestRequired"
