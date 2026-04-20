"""Tests for the coach REPL session.

Coverage:
  - Deck sheet renders with all pre-computed facts
  - Prompt embeds sheet + recent history + user question
  - Coach step updates session history on success
  - Rejected output is still recorded so the user sees what went wrong
  - History window caps so the prompt doesn't grow unbounded
"""

import pytest

from mtg_deck_engine.analyst import MockBackend
from mtg_deck_engine.analyst.coach import (
    CoachSession,
    build_deck_sheet,
    coach_step,
)


def _sheet():
    return build_deck_sheet(
        deck_name="Atraxa Test",
        archetype="Superfriends",
        color_identity=["W", "U", "B", "G"],
        power_overall=7.5,
        power_tier="optimized",
        land_count=36,
        ramp_count=12,
        draw_count=10,
        interaction_count=11,
        avg_mana_value=3.2,
        deck_cards=["Atraxa, Praetors' Voice", "Sol Ring", "Arcane Signet"],
        reasons_up=["Atraxa is strong"],
        reasons_down=["Glass cannon"],
    )


class TestDeckSheet:
    def test_includes_all_keys(self):
        sheet = _sheet()
        for key in [
            "[DECK:", "[COLORS:", "[ARCHETYPE:", "[POWER:", "[LANDS:",
            "[RAMP:", "[DRAW:", "[INTERACTION:", "[AVG_CMC:",
            "[REASONS_UP:", "[REASONS_DOWN:", "[CARDS]",
        ]:
            assert key in sheet

    def test_cards_block_lists_every_card(self):
        sheet = _sheet()
        assert "Atraxa, Praetors' Voice" in sheet
        assert "Sol Ring" in sheet
        assert "Arcane Signet" in sheet


class TestCoachPrompt:
    def test_includes_sheet_and_question(self):
        sess = CoachSession(
            deck_sheet=_sheet(),
            allowed_cards={"Atraxa, Praetors' Voice", "Sol Ring"},
        )
        prompt = sess.build_prompt("why is my ramp count good?")
        assert "[DECK SHEET]" in prompt
        assert "[RAMP: 12]" in prompt
        assert "why is my ramp count good?" in prompt
        assert "[USER QUESTION]" in prompt
        assert "[COACH RESPONSE]" in prompt

    def test_history_window_caps_context(self):
        sess = CoachSession(deck_sheet=_sheet(), allowed_cards=set())
        # Preload 6 fake turns — only last 4 should appear in the prompt
        for i in range(6):
            sess.history.append(
                type("T", (), {
                    "user_question": f"Q{i}",
                    "assistant_response": f"A{i}",
                    "verified": True,
                    "confidence": 1.0,
                })()
            )
        prompt = sess.build_prompt("new question", history_window=4)
        # Q0, Q1 should be trimmed; Q2-Q5 remain
        assert "Q0" not in prompt
        assert "Q1" not in prompt
        assert "Q2" in prompt
        assert "Q5" in prompt


class TestCoachStep:
    def test_step_appends_to_history_on_success(self):
        sess = CoachSession(deck_sheet=_sheet(), allowed_cards={"Sol Ring"})
        mock = MockBackend(scripts=[
            ("[USER QUESTION]", "Your ramp sits at 12, comfortably inside the commander target range of 10-15."),
        ])
        turn = coach_step(sess, mock, "is my ramp count good?")
        assert turn.verified is True
        assert turn.confidence == 1.0
        assert len(sess.history) == 1
        assert sess.history[0].user_question == "is my ramp count good?"

    def test_step_records_failed_turn(self):
        """Too-short outputs fail verification across all retries. The turn is still
        recorded so the user sees the failure rather than a silent drop."""
        sess = CoachSession(deck_sheet=_sheet(), allowed_cards=set())
        mock = MockBackend(scripts=[
            ("[USER QUESTION]", "ok"),     # fails min_chars
            ("[USER QUESTION]", "nope"),   # retry also fails
        ])
        turn = coach_step(sess, mock, "why?", max_retries=1)
        assert turn.verified is False
        assert turn.confidence == 0.0
        assert len(sess.history) == 1
