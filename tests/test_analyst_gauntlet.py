"""Tests for the analyst gauntlet scoring machinery itself.

The gauntlet is the accountability layer for the LLM analyst — if it passes
hard-pass 100%, we have mathematical certainty that no hallucinated card name
made it into a suggestion. These tests confirm the scoring logic actually
enforces that, by constructing an adversarial mock backend and asserting the
gauntlet catches its cheating.
"""

import pytest

from mtg_deck_engine.analyst import AnalystRunner, MockBackend
from mtg_deck_engine.benchmarks.analyst_gauntlet import (
    default_cases,
    mini_cases,
    print_report,
    run_gauntlet,
    score_case,
)


def _scripted_runner(n_cases: int = 2):
    """Script the mock to pick c01/c02 for each cuts query, plus a valid prose summary."""
    scripts = [
        ("[INPUT]", "A " * 80 + " focused midrange."),
        ("suggesting cuts", "[c01]: redundant\n[c02]: filler"),
    ] * (n_cases + 1)
    return AnalystRunner(backend=MockBackend(scripts=scripts))


class TestGauntletHappyPath:
    def test_mock_hits_100_percent_hard_pass(self):
        runner = _scripted_runner()
        res = run_gauntlet(runner, cases=mini_cases(), verbose=False)
        assert res.total_cases == 2
        assert res.summary_hard_pass == 2
        assert res.cuts_hard_pass == 2
        assert res.hard_pass_rate == 1.0

    def test_report_does_not_crash(self, capsys):
        runner = _scripted_runner()
        res = run_gauntlet(runner, cases=mini_cases(), verbose=False)
        print_report(res)
        out = capsys.readouterr().out
        assert "HARD PASS" in out
        assert "SOFT METRICS" in out


class TestGauntletCatchesHallucination:
    def test_hallucinated_cuts_fail_hard_pass(self):
        scripts = [
            ("[INPUT]", "A " * 80 + " prose summary."),
            ("suggesting cuts", "[zzz99]: fake tag."),
            ("suggesting cuts", "[zzz99]: fake tag."),
            ("suggesting cuts", "[zzz99]: fake tag."),
        ] * 5
        runner = AnalystRunner(backend=MockBackend(scripts=scripts))
        res = run_gauntlet(runner, cases=mini_cases(), verbose=False)
        assert res.summary_hard_pass == 2
        assert all(not c["cuts_verified"] for c in res.per_case)


class TestScoreCase:
    def test_invalid_cut_name_fails_hard_ok(self):
        from mtg_deck_engine.analyst.runner import AnalystResult, CutSuggestion
        case = mini_cases()[0]
        forged = AnalystResult()
        forged.cuts_verified = True
        forged.cuts = [
            CutSuggestion(card_name="Lightning Boltstrike", tag="c99", reason="fake", signals=[]),
        ]
        record = score_case(case, forged)
        assert record["cuts_hard_ok"] is False


class TestDefaultCases30:
    """Sanity check the full 30-case suite — just that it builds cleanly."""

    def test_all_30_cases_build(self):
        cases = default_cases()
        assert len(cases) == 30
        for c in cases:
            deck = c.build_deck()
            assert sum(e.quantity for e in deck.entries) == 100
            analysis, power, archetype = c.build_analysis()
            assert analysis.total_cards == 100
