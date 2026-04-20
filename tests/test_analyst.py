"""Tests for the analyst package — hallucination-proof LLM integration layer.

Covers:
  - MockBackend scripted responses + consumption
  - Tag-pick parsing (happy path + errors)
  - Tag-in-table verification
  - Free-form card name detection
  - Prose-output verification
  - Retry loop: pass first try, pass second try, fail all tries
  - End-to-end AnalystRunner with scripted backend
  - Zero-hallucination guarantee: even a malicious backend that tries to
    emit a fake card name (or a real card not in the candidate table)
    cannot cause a CutSuggestion with that name to be emitted.
"""

import pytest

from mtg_deck_engine.analyst import AnalystRunner, MockBackend
from mtg_deck_engine.analyst.candidates import rank_cut_candidates, render_cut_table
from mtg_deck_engine.analyst.pipeline import generate_with_verify
from mtg_deck_engine.analyst.prompts import (
    cut_suggestions_prompt,
    executive_summary_prompt,
)
from mtg_deck_engine.analyst.verifiers import (
    TagPick,
    VerificationError,
    parse_tag_picks,
    verify_no_free_form_card_names,
    verify_prose_output,
    verify_tags_in_table,
)
from mtg_deck_engine.analysis.power_level import PowerBreakdown
from mtg_deck_engine.models import (
    AnalysisResult,
    Card,
    CardLayout,
    CardTag,
    Color,
    Deck,
    DeckEntry,
    Format,
    Zone,
)


def _card(name, cmc=3.0, tags=None, is_land=False, is_creature=False,
          is_artifact=False, is_instant=False, mana_cost="{3}"):
    return Card(
        scryfall_id=f"id-{name}",
        oracle_id=f"oracle-{name}",
        name=name,
        layout=CardLayout.NORMAL,
        cmc=cmc,
        mana_cost=mana_cost,
        type_line="Creature" if is_creature else "Instant" if is_instant else "Artifact",
        tags=tags or [],
        is_land=is_land,
        is_creature=is_creature,
        is_artifact=is_artifact,
        is_instant=is_instant,
        color_identity=[Color.GREEN, Color.WHITE],
    )


def _entry(name, **kw):
    card = _card(name, **kw)
    return DeckEntry(card_name=name, quantity=1, zone=Zone.MAINBOARD, card=card)


def _make_cuttable_deck():
    """A deck with obvious cut candidates to exercise the ranker."""
    entries = [
        _entry("Commander", cmc=4, tags=[CardTag.FINISHER], is_creature=True),
    ]
    # Lands
    for i in range(36):
        entries.append(_entry(f"Land{i}", is_land=True))
    # 18 ramp pieces — well over the 15 cap, so redundancy score fires
    for i in range(18):
        entries.append(_entry(
            f"RampRock{i}", cmc=3, tags=[CardTag.MANA_ROCK, CardTag.RAMP],
            is_artifact=True, mana_cost="{3}"
        ))
    # High-cost filler with no tags — prime cut candidate
    for i in range(5):
        entries.append(_entry(f"Filler{i}", cmc=7, tags=[], mana_cost="{6}{G}"))
    # Actual finishers (never cut)
    for i in range(3):
        entries.append(_entry(
            f"Finisher{i}", cmc=6, tags=[CardTag.FINISHER, CardTag.THREAT],
            is_creature=True
        ))
    # Draw and removal at target
    for i in range(8):
        entries.append(_entry(f"Draw{i}", cmc=3, tags=[CardTag.CARD_DRAW]))
    for i in range(8):
        entries.append(_entry(f"Removal{i}", cmc=2, tags=[CardTag.TARGETED_REMOVAL], is_instant=True))
    # Pad to 100
    while sum(e.quantity for e in entries) < 100:
        entries.append(_entry(f"Extra{len(entries)}", cmc=4, tags=[CardTag.THREAT]))
    return Deck(name="Test Deck", format=Format.COMMANDER, entries=entries[:100])


def _basic_analysis_result():
    return AnalysisResult(
        deck_name="Test Deck",
        format="commander",
        total_cards=100,
        land_count=36,
        ramp_count=18,
        draw_engine_count=8,
        interaction_count=8,
        average_cmc=3.1,
        recommendations=["Trim 3 ramp pieces", "Add a sweeper"],
    )


def _basic_power() -> PowerBreakdown:
    pb = PowerBreakdown()
    pb.overall = 6.5
    pb.tier = "focused"
    pb.reasons_up = ["Heavy ramp package"]
    pb.reasons_down = ["Low interaction"]
    return pb


# -------------------------------------------------------------------- MockBackend

class TestMockBackend:
    def test_returns_default_when_no_script_matches(self):
        mock = MockBackend(default="fallback")
        assert mock.generate("anything") == "fallback"

    def test_returns_first_matching_script(self):
        mock = MockBackend(scripts=[
            ("summary", "SUMMARY"),
            ("cuts", "CUTS"),
        ])
        assert mock.generate("please write a summary for me") == "SUMMARY"
        assert mock.generate("please pick cuts") == "CUTS"

    def test_consumes_script_entries(self):
        """Consumed entries drop so retries can route to the next response."""
        mock = MockBackend(scripts=[
            ("bad", "invalid"),
            ("bad", "valid retry"),
        ])
        assert mock.generate("I want something bad") == "invalid"
        assert mock.generate("I want something bad") == "valid retry"

    def test_logs_calls(self):
        mock = MockBackend(default="")
        mock.generate("first")
        mock.generate("second")
        assert mock.call_log == ["first", "second"]


# -------------------------------------------------------------------- Tag parsing

class TestParseTagPicks:
    def test_parses_simple_lines(self):
        picks = parse_tag_picks("[c01]: cut this\n[c03]: and this")
        assert len(picks) == 2
        assert picks[0] == TagPick(tag="c01", reason="cut this")
        assert picks[1].tag == "c03"

    def test_ignores_prose_lines(self):
        """Model preambles a little — picks still extract cleanly."""
        output = "Sure, here are my picks:\n\n[c02]: high CMC filler\n[c05]: redundant"
        picks = parse_tag_picks(output)
        assert len(picks) == 2
        assert picks[0].tag == "c02"
        assert picks[1].tag == "c05"

    def test_raises_on_no_picks(self):
        with pytest.raises(VerificationError) as ei:
            parse_tag_picks("Sorry, I can't help.")
        assert "No tag-prefixed picks" in str(ei.value)
        assert "required format" in ei.value.hint

    def test_supports_three_digit_tags(self):
        picks = parse_tag_picks("[a123]: works\n[c9]: also works")
        assert len(picks) == 2


# -------------------------------------------------------------------- Tag table verification

class TestVerifyTagsInTable:
    def test_accepts_known_tags(self):
        picks = [TagPick("c01", "r"), TagPick("c02", "r")]
        verify_tags_in_table(picks, {"c01", "c02", "c03"})

    def test_rejects_unknown_tag(self):
        picks = [TagPick("c01", "r"), TagPick("c99", "r")]
        with pytest.raises(VerificationError) as ei:
            verify_tags_in_table(picks, {"c01", "c02"})
        assert "c99" in ei.value.hint
        # Hint lists valid tags so the retry can self-correct
        assert "c01" in ei.value.hint


# -------------------------------------------------------------------- Free-form name check

class TestVerifyNoFreeFormCardNames:
    def test_allows_prose_without_card_names(self):
        verify_no_free_form_card_names(
            "[c01]: high-cost ramp piece that's redundant",
            card_names={"Solemn Simulacrum", "Cultivate"},
        )

    def test_allows_name_inside_tag_line(self):
        """Tag lines are stripped before the free-form check."""
        verify_no_free_form_card_names(
            "[c01]: Solemn Simulacrum is redundant with Cultivate",
            card_names={"Solemn Simulacrum", "Cultivate"},
        )

    def test_rejects_free_form_name_outside_tag(self):
        output = (
            "You should cut Solemn Simulacrum because it's too slow.\n"
            "[c01]: high-cost ramp"
        )
        with pytest.raises(VerificationError) as ei:
            verify_no_free_form_card_names(
                output, card_names={"Solemn Simulacrum", "Cultivate"},
            )
        assert "Solemn Simulacrum" in ei.value.hint

    def test_skips_short_names_to_avoid_false_positives(self):
        """Short card names like 'Ice' or 'Cut' could hit via substring noise."""
        verify_no_free_form_card_names(
            "[c01]: cut this, it's a bad fit",
            card_names={"Ice"},  # len 3, below min_name_length
        )

    def test_multi_line_tag_lines_are_all_stripped(self):
        """Regression test: `^/$` without re.MULTILINE used to make sub() strip
        only the first tag line in a multi-line output, causing card names
        in later tag reasons to false-positive as free-form emissions."""
        output = (
            "[c01]: Pelakka Wurm is high-cost filler with no tag\n"
            "[c02]: Siege Wurm is a 7-mana vanilla creature\n"
            "[c03]: Yavimaya Wurm similarly doesn't pull its weight"
        )
        # All three cards are mentioned only INSIDE tag reasons — must pass
        verify_no_free_form_card_names(
            output,
            card_names={"Pelakka Wurm", "Siege Wurm", "Yavimaya Wurm"},
        )


# -------------------------------------------------------------------- Prose output

class TestVerifyProseOutput:
    def test_accepts_reasonable_prose(self):
        verify_prose_output("A" * 200)

    def test_rejects_too_short(self):
        with pytest.raises(VerificationError):
            verify_prose_output("Nope.")

    def test_rejects_template_leak(self):
        with pytest.raises(VerificationError) as ei:
            verify_prose_output("x" * 200 + "\n[OUTPUT]")
        assert "template markers" in ei.value.hint


# -------------------------------------------------------------------- Retry loop

class TestRetryLoop:
    def test_pass_first_try(self):
        mock = MockBackend(scripts=[
            ("summary", "A" * 150),
        ])
        res = generate_with_verify(
            mock, "write a summary", verify=verify_prose_output, max_retries=2,
        )
        assert res.verified is True
        assert res.confidence == 1.0
        assert res.attempts == 1
        assert len(mock.call_log) == 1

    def test_pass_second_try_with_feedback(self):
        """First output fails; second attempt succeeds. Retry prompt must include the hint."""
        mock = MockBackend(scripts=[
            ("summary", "short"),           # fails (too short)
            ("summary", "A" * 200),          # passes on retry
        ])
        res = generate_with_verify(
            mock, "write a summary", verify=verify_prose_output, max_retries=2,
        )
        assert res.verified is True
        assert res.confidence == pytest.approx(0.8)  # 1.0 - 0.2
        assert res.attempts == 2
        # Retry prompt includes the feedback hint
        assert "[FEEDBACK]" in mock.call_log[1]
        assert "[PREVIOUS ATTEMPT" in mock.call_log[1]

    def test_fail_all_tries(self):
        mock = MockBackend(scripts=[
            ("summary", "short"),
            ("summary", "still short"),
            ("summary", "nope"),
        ])
        res = generate_with_verify(
            mock, "write a summary", verify=verify_prose_output, max_retries=2,
        )
        assert res.verified is False
        assert res.confidence == 0.0
        assert res.attempts == 3  # 1 + 2 retries
        assert len(res.errors) == 3


# -------------------------------------------------------------------- End-to-end runner

class TestAnalystRunner:
    def test_cut_ranker_finds_candidates(self):
        deck = _make_cuttable_deck()
        cands = rank_cut_candidates(deck, limit=12)
        assert len(cands) > 0
        # Filler cards (no tags, CMC 7) should be near the top — they hit both
        # high_cmc_non_finisher AND no_functional_tag signals
        filler_names = [c.entry.card.name for c in cands[:5] if c.entry.card]
        assert any("Filler" in n for n in filler_names)

    def test_cut_ranker_never_surfaces_commander_or_lands(self):
        deck = _make_cuttable_deck()
        cands = rank_cut_candidates(deck, limit=50)
        names = [c.entry.card.name for c in cands if c.entry.card]
        assert not any(n.startswith("Land") for n in names)
        # Commander isn't in the mainboard but double-check
        assert "Commander" not in names

    def test_runner_happy_path(self):
        deck = _make_cuttable_deck()
        analysis = _basic_analysis_result()
        power = _basic_power()

        # Pre-compute candidates to know which tags are valid
        cands = rank_cut_candidates(deck, limit=12)
        # Pick the first two candidate tags — guaranteed valid
        tag_picks = f"[{cands[0].tag}]: signals match\n[{cands[1].tag}]: ok"

        mock = MockBackend(scripts=[
            ("executive summary", "A" * 250 + " " + "B" * 50),
            ("suggesting cuts", tag_picks),
        ])
        runner = AnalystRunner(backend=mock)
        result = runner.run(deck, analysis, power, advanced=None, archetype="midrange")

        assert result.summary_verified is True
        assert result.cuts_verified is True
        assert len(result.cuts) == 2
        # Cuts reference real cards from the deck — resolved via candidate table
        cut_names = {c.card_name for c in result.cuts}
        deck_names = {e.card.name for e in deck.entries if e.card}
        assert cut_names.issubset(deck_names)

    def test_runner_recovers_via_retry(self):
        """First cut attempt uses an invalid tag; second fixes it. End result is clean."""
        deck = _make_cuttable_deck()
        analysis = _basic_analysis_result()
        power = _basic_power()
        cands = rank_cut_candidates(deck, limit=12)

        bad_output = "[zzz999]: fake tag that doesn't exist"
        good_output = f"[{cands[0].tag}]: retry worked"

        mock = MockBackend(scripts=[
            ("executive summary", "A" * 250),
            ("suggesting cuts", bad_output),
            ("suggesting cuts", good_output),
        ])
        runner = AnalystRunner(backend=mock)
        result = runner.run(deck, analysis, power, advanced=None, archetype="midrange")

        assert result.cuts_verified is True
        assert len(result.cuts) == 1
        assert result.cuts_confidence == pytest.approx(0.8)

    def test_runner_prose_noise_does_not_leak_into_picks(self):
        """Even when the model's prose mentions extra card names outside a tag,
        the final cuts list is built ONLY from tag-resolved candidates.

        The free-form prose check used to reject these outputs entirely, but
        that was pedantic format enforcement — not a safety check. The real
        safety guarantee is: CutSuggestion.card_name always comes from the
        candidate table via tag lookup, never from prose. So even a noisy
        emission with extra deck-card mentions produces a clean picks list.
        """
        deck = _make_cuttable_deck()
        analysis = _basic_analysis_result()
        power = _basic_power()
        cands = rank_cut_candidates(deck, limit=12)

        noisy = (
            f"[{cands[0].tag}]: redundant\n"
            f"You should also consider adding Filler3 as a cut candidate."
        )
        mock = MockBackend(scripts=[
            ("executive summary", "A" * 250),
            ("suggesting cuts", noisy),
        ])
        runner = AnalystRunner(backend=mock)
        result = runner.run(deck, analysis, power, advanced=None, archetype="midrange")

        # Verifier passes (the tag is valid) — prose noise is ignored.
        assert result.cuts_verified is True
        # Only one pick resolved from the tag; the "Filler3" prose mention is
        # dropped because it never became a TagPick.
        assert len(result.cuts) == 1
        assert result.cuts[0].card_name == cands[0].entry.card.name
        # Critically: no CutSuggestion with name "Filler3" got smuggled in.
        assert all(c.card_name != "Filler3" for c in result.cuts)

    def test_runner_blocks_invalid_tags(self):
        """If the model emits tags that AREN'T in the candidate table, verification
        fails and the cuts list stays empty. This is the real hallucination
        guard — the tag constraint, not prose scrubbing."""
        deck = _make_cuttable_deck()
        analysis = _basic_analysis_result()
        power = _basic_power()

        bogus = "[zzz999]: fake tag\n[yyy888]: another fake tag"
        mock = MockBackend(scripts=[
            ("executive summary", "A" * 250),
            ("suggesting cuts", bogus),
            ("suggesting cuts", bogus),
            ("suggesting cuts", bogus),
        ])
        runner = AnalystRunner(backend=mock)
        result = runner.run(deck, analysis, power, advanced=None, archetype="midrange")

        assert result.cuts_verified is False
        assert result.cuts == []


# -------------------------------------------------------------------- Prompt rendering

class TestPlaygroupPower:
    def test_over_pitches_when_deck_higher_than_target(self):
        prompt = executive_summary_prompt(
            deck_name="x", archetype="a",
            power_overall=8.0, power_tier="optimized",
            power_reasons_up=[], power_reasons_down=[],
            land_count=36, ramp_count=12, draw_count=10,
            interaction_count=10, avg_mana_value=3.0,
            color_identity=["G"], format_name="commander", recommendations=[],
            playgroup_power=6.0,
        )
        assert "OVER-PITCHES" in prompt
        assert "Playgroup target: 6.0" in prompt

    def test_under_delivers_when_deck_lower_than_target(self):
        prompt = executive_summary_prompt(
            deck_name="x", archetype="a",
            power_overall=4.0, power_tier="casual",
            power_reasons_up=[], power_reasons_down=[],
            land_count=36, ramp_count=8, draw_count=6,
            interaction_count=4, avg_mana_value=3.0,
            color_identity=["G"], format_name="commander", recommendations=[],
            playgroup_power=7.0,
        )
        assert "UNDER-DELIVERS" in prompt

    def test_fits_when_gap_under_one_point(self):
        prompt = executive_summary_prompt(
            deck_name="x", archetype="a",
            power_overall=6.5, power_tier="focused",
            power_reasons_up=[], power_reasons_down=[],
            land_count=36, ramp_count=10, draw_count=8,
            interaction_count=9, avg_mana_value=3.0,
            color_identity=["G"], format_name="commander", recommendations=[],
            playgroup_power=7.0,
        )
        assert "FITS the playgroup" in prompt

    def test_no_playgroup_line_when_unset(self):
        prompt = executive_summary_prompt(
            deck_name="x", archetype="a",
            power_overall=6.5, power_tier="focused",
            power_reasons_up=[], power_reasons_down=[],
            land_count=36, ramp_count=10, draw_count=8,
            interaction_count=9, avg_mana_value=3.0,
            color_identity=["G"], format_name="commander", recommendations=[],
        )
        assert "Playgroup target" not in prompt
        assert "OVER-PITCHES" not in prompt


class TestPlaygroupPowerValidator:
    def test_valid_values_pass(self):
        from mtg_deck_engine.cli import _playgroup_power_type
        assert _playgroup_power_type("1.0") == 1.0
        assert _playgroup_power_type("5.5") == 5.5
        assert _playgroup_power_type("10.0") == 10.0

    def test_out_of_range_rejected(self):
        import argparse
        from mtg_deck_engine.cli import _playgroup_power_type
        with pytest.raises(argparse.ArgumentTypeError):
            _playgroup_power_type("0")
        with pytest.raises(argparse.ArgumentTypeError):
            _playgroup_power_type("11")
        with pytest.raises(argparse.ArgumentTypeError):
            _playgroup_power_type("-2")

    def test_non_numeric_rejected(self):
        import argparse
        from mtg_deck_engine.cli import _playgroup_power_type
        with pytest.raises(argparse.ArgumentTypeError):
            _playgroup_power_type("high")


class TestVersionDiffInPrompt:
    def test_version_diff_line_rendered(self):
        diff = {
            "added": {"Rhystic Study": 1, "Cyclonic Rift": 1},
            "removed": {"Pelakka Wurm": 1},
            "score_deltas": {"interaction": 5.0, "curve": -2.0},
        }
        prompt = executive_summary_prompt(
            deck_name="x", archetype="a",
            power_overall=6.5, power_tier="focused",
            power_reasons_up=[], power_reasons_down=[],
            land_count=36, ramp_count=10, draw_count=8,
            interaction_count=9, avg_mana_value=3.0,
            color_identity=["G"], format_name="commander", recommendations=[],
            version_diff=diff,
        )
        # Count summary + sample of card names + biggest score delta
        assert "Since last save" in prompt
        assert "+2 adds" in prompt
        assert "Rhystic Study" in prompt
        assert "-1 cuts" in prompt
        assert "Pelakka Wurm" in prompt
        # Largest absolute delta wins — interaction (5.0) beats curve (-2.0)
        assert "interaction +5.0" in prompt

    def test_no_version_line_when_unset(self):
        prompt = executive_summary_prompt(
            deck_name="x", archetype="a",
            power_overall=6.5, power_tier="focused",
            power_reasons_up=[], power_reasons_down=[],
            land_count=36, ramp_count=10, draw_count=8,
            interaction_count=9, avg_mana_value=3.0,
            color_identity=["G"], format_name="commander", recommendations=[],
        )
        assert "Since last save" not in prompt


class TestPrompts:
    def test_executive_summary_prompt_no_template_markers_leak(self):
        prompt = executive_summary_prompt(
            deck_name="Atraxa Superfriends",
            archetype="Superfriends",
            power_overall=7.5,
            power_tier="optimized",
            power_reasons_up=["Atraxa is busted"],
            power_reasons_down=["Glass cannon"],
            land_count=36, ramp_count=12, draw_count=10,
            interaction_count=11, avg_mana_value=3.2,
            color_identity=["W", "U", "B", "G"],
            format_name="commander",
            recommendations=["Add more removal"],
        )
        # Sanity: our scaffolding tags are present for the model to follow
        assert "[INPUT]" in prompt
        assert "[OUTPUT]" in prompt
        assert "Atraxa Superfriends" in prompt
        assert "7.5" in prompt

    def test_cuts_prompt_renders_tags(self):
        deck = _make_cuttable_deck()
        cands = rank_cut_candidates(deck, limit=12)
        prompt = cut_suggestions_prompt(
            candidates=cands, deck_name="Test", archetype="midrange",
            power_tier="focused", count=5,
        )
        # Every candidate tag should be in the prompt so the model can reference
        for c in cands:
            assert f"[{c.tag}]" in prompt
        # Explicit instruction to use tags, not names
        assert "bracket tag" in prompt
        # Tightened wording after the verbose-prose fixes — the instruction
        # is now "never type a card's name" instead of "Do NOT type".
        assert "never type a card" in prompt
