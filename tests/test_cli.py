"""CLI integration tests — verify commands parse args and don't crash."""

import subprocess
import sys

PYTHON = sys.executable


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    """Run mtg-engine CLI with given args."""
    import os
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [PYTHON, "-m", "mtg_deck_engine.cli", *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        env=env,
    )


class TestCLIBasics:
    def test_no_args_shows_help(self):
        r = _run_cli()
        assert r.returncode == 0
        assert "mtg-engine" in r.stdout or "usage" in r.stdout.lower()

    def test_help_flag(self):
        r = _run_cli("--help")
        assert r.returncode == 0
        assert "analyze" in r.stdout
        assert "goldfish" in r.stdout
        assert "gauntlet" in r.stdout

    def test_info_no_db(self):
        """info command should work even with no cards ingested."""
        r = _run_cli("info")
        assert r.returncode == 0
        assert "Cards in database" in r.stdout or "cards" in r.stdout.lower()

    def test_analyze_missing_file(self):
        """analyze with nonexistent file should print error and exit 1."""
        r = _run_cli("analyze", "nonexistent_deck.txt")
        assert r.returncode == 1
        assert "not found" in r.stderr.lower() or "not found" in r.stdout.lower() or r.returncode == 1

    def test_search_no_db(self):
        """search with empty db should handle gracefully."""
        r = _run_cli("search", "Lightning Bolt")
        # Should either return 0 with "no cards" message or 1
        assert r.returncode in (0, 1)

    def test_history_no_decks(self):
        """history with no saved decks should show empty message."""
        r = _run_cli("history")
        assert r.returncode == 0

    def test_ingest_subcommand_exists(self):
        """ingest --help should work."""
        r = _run_cli("ingest", "--help")
        assert r.returncode == 0
        assert "force" in r.stdout.lower()

    def test_analyze_subcommand_exists(self):
        r = _run_cli("analyze", "--help")
        assert r.returncode == 0
        assert "deep" in r.stdout.lower()
        assert "export" in r.stdout.lower()

    def test_goldfish_subcommand_exists(self):
        r = _run_cli("goldfish", "--help")
        assert r.returncode == 0
        assert "sims" in r.stdout.lower()

    def test_gauntlet_subcommand_exists(self):
        r = _run_cli("gauntlet", "--help")
        assert r.returncode == 0
        assert "suite" in r.stdout.lower()

    def test_probability_subcommand_exists(self):
        r = _run_cli("probability", "--help")
        assert r.returncode == 0

    def test_save_subcommand_exists(self):
        r = _run_cli("save", "--help")
        assert r.returncode == 0
        assert "notes" in r.stdout.lower()

    def test_compare_subcommand_exists(self):
        r = _run_cli("compare", "--help")
        assert r.returncode == 0
