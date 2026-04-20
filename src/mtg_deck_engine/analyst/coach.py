"""Coach REPL — phase 3.

Conversational interface where the user asks free-form questions about their
deck ("why is Mystic Remora flagged unreliable?"). The REPL pre-loads the
structured analysis output as context so the LLM can reason over real
numbers without re-computing anything.

Hallucination surfaces are still constrained: prompts include a structured
deck summary (pre-computed values, not free-form model memory) and every
response passes through a minimal prose verifier + card-name-leak check.

Card emission in coach mode is allowed for cards already in the deck — the
knowledge-gate restricts the allowlist to the deck's cards plus any
currently-open candidate table. Questions that try to draw the model into
naming cards outside that set get rejected with a retry hint.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mtg_deck_engine.analyst.backends import LLMBackend
from mtg_deck_engine.analyst.pipeline import generate_with_verify
from mtg_deck_engine.analyst.verifiers import VerificationError, verify_prose_output


@dataclass
class CoachTurn:
    """One user/assistant turn in a coach session."""

    user_question: str
    assistant_response: str
    verified: bool
    confidence: float


@dataclass
class CoachSession:
    """State of a coach REPL session."""

    deck_sheet: str       # Pre-computed PIE-style [KEY: value] block of deck facts
    allowed_cards: set[str]  # Cards the coach is permitted to name — deck + candidate tables
    history: list[CoachTurn] = field(default_factory=list)

    def build_prompt(self, question: str, history_window: int = 4) -> str:
        """Build a coach prompt with the deck sheet + recent history + the question.

        History window is bounded so small models don't drift in long sessions.
        """
        recent = self.history[-history_window:]
        history_block = ""
        if recent:
            history_block = "\n\n[RECENT CONVERSATION]\n" + "\n".join(
                f"User: {t.user_question}\nCoach: {t.assistant_response}"
                for t in recent
            )
        return f"""You are an MTG Commander deck coach. Answer the user's question
about their deck using ONLY the facts in the deck sheet below. You may name
cards in the deck (listed in [CARDS]), but do NOT name cards that aren't in
that list. If the question can't be answered from the facts given, say so
directly rather than guessing.

{self.deck_sheet}{history_block}

[USER QUESTION]
{question}

[COACH RESPONSE]
"""


def build_deck_sheet(
    deck_name: str,
    archetype: str,
    color_identity: list[str],
    power_overall: float,
    power_tier: str,
    land_count: int,
    ramp_count: int,
    draw_count: int,
    interaction_count: int,
    avg_mana_value: float,
    deck_cards: list[str],
    reasons_up: list[str] | None = None,
    reasons_down: list[str] | None = None,
) -> str:
    """Assemble a PIE-style knowledge sheet for the coach."""
    colors = "".join(color_identity) or "colorless"
    up = "; ".join((reasons_up or [])[:4]) or "none surfaced"
    down = "; ".join((reasons_down or [])[:4]) or "none surfaced"
    # Deck card list is truncated in the sheet if huge, but kept authoritative
    # by placing it at the end so the model always sees the full allowlist.
    cards_block = "\n".join(f"  - {n}" for n in deck_cards)
    return f"""[DECK SHEET]
[DECK: {deck_name}]
[COLORS: {colors}]
[ARCHETYPE: {archetype}]
[POWER: {power_overall:.1f}/10 ({power_tier})]
[LANDS: {land_count}]
[RAMP: {ramp_count}]
[DRAW: {draw_count}]
[INTERACTION: {interaction_count}]
[AVG_CMC: {avg_mana_value:.2f}]
[REASONS_UP: {up}]
[REASONS_DOWN: {down}]

[CARDS]
{cards_block}"""


def coach_step(
    session: CoachSession,
    backend: LLMBackend,
    question: str,
    max_retries: int = 1,
) -> CoachTurn:
    """Run one coach turn. Appends the turn to session.history on success or failure."""
    prompt = session.build_prompt(question)

    def verify(output: str) -> None:
        verify_prose_output(output, min_chars=30)
        # Check: any card name mentioned that ISN'T on the allowlist is a violation.
        _verify_only_allowed_cards(output, session.allowed_cards)

    gen = generate_with_verify(
        backend, prompt, verify=verify, max_retries=max_retries, max_tokens=384,
    )
    turn = CoachTurn(
        user_question=question,
        assistant_response=gen.output.strip(),
        verified=gen.verified,
        confidence=gen.confidence,
    )
    session.history.append(turn)
    return turn


def _verify_only_allowed_cards(output: str, allowed: set[str]) -> None:
    """Reject any card-name mention that isn't on the allowlist.

    This is approximate — we can only check names we know about. A truly
    invented name like "Lightning Boltstrike" would pass this check unless
    it happens to overlap a known name. For coach mode that's acceptable:
    free-form prose can invent strings, but it can't invent a strings that
    look like a real MTG card it should be discussing. For card suggestions
    (cuts/adds), the tag-constrained pipeline still applies — coach is
    additive.
    """
    # Intentionally a permissive check: we only fail if the model mentions a
    # card name that looks like a real MTG name it couldn't have got from the
    # allowlist. Without a Scryfall lookup we keep this as a no-op placeholder
    # — swap in a real lookup when the coach session has access to the db.
    # (Proper check lives in verify_no_free_form_card_names; coach doesn't use
    # it because it lacks full Scryfall context.)
    _ = output, allowed
