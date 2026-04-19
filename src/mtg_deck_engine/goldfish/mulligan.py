"""London mulligan implementation for goldfish simulation.

London mulligan rules:
1. Draw 7 cards
2. Evaluate hand — keep or mulligan
3. If mulligan: shuffle hand back, draw 7 again, put N cards on bottom
4. Repeat up to 3 times (keep at 4 cards minimum)

Uses the opening hand evaluator from Phase 2 for keepability scoring.
"""

from __future__ import annotations

import random

from mtg_deck_engine.goldfish.state import GameState
from mtg_deck_engine.models import Deck
from mtg_deck_engine.probability.opening_hand import evaluate_hand


def mulligan_phase(state: GameState, deck: Deck, min_keep_score: float = 40.0) -> int:
    """Execute the mulligan phase. Returns number of mulligans taken."""
    mulligans = 0
    max_mulligans = 3

    for attempt in range(max_mulligans + 1):
        # Draw 7
        hand = state.draw(7)

        # Evaluate
        ev = evaluate_hand(hand, deck)

        if ev.keepable or attempt == max_mulligans:
            # Keep — but bottom N cards for mulligans taken
            if mulligans > 0:
                _bottom_cards(state, mulligans)
            state.mulligans_taken = mulligans
            return mulligans

        # Mulligan — put hand back and reshuffle
        mulligans += 1
        state.hand.clear()
        state.library = hand + state.library
        random.shuffle(state.library)

    return mulligans


def _bottom_cards(state: GameState, count: int):
    """Put the N worst cards from hand on the bottom of the library.

    Bottoming strategy:
    - Bottom highest-CMC spells first (can't cast them early anyway)
    - Never bottom the last land
    - Never bottom ramp if we have few lands
    """
    if count <= 0 or not state.hand:
        return

    hand = list(state.hand)
    lands_in_hand = sum(1 for e in hand if e.card and e.card.is_land)

    # Score each card: lower score = more likely to bottom
    scored = []
    for entry in hand:
        card = entry.card
        if card is None:
            scored.append((entry, -100.0))  # Bottom unknown cards first
            continue

        keep_score = 0.0

        # Lands are valuable
        if card.is_land:
            if lands_in_hand <= 2:
                keep_score += 100.0  # Never bottom if we're land-light
            else:
                keep_score += 30.0

        # Low-cost spells are more keepable
        keep_score += max(0, 8 - card.display_cmc()) * 5.0

        # Ramp is very keepable
        from mtg_deck_engine.models import CardTag
        if card.tags and (CardTag.RAMP in card.tags or CardTag.MANA_ROCK in card.tags):
            keep_score += 25.0

        # Card draw is keepable
        if card.tags and CardTag.CARD_DRAW in card.tags:
            keep_score += 15.0

        scored.append((entry, keep_score))

    # Sort by keep score ascending — bottom the lowest-scored cards
    scored.sort(key=lambda x: x[1])

    bottomed = 0
    for entry, _ in scored:
        if bottomed >= count:
            break
        state.hand.remove(entry)
        state.library.append(entry)
        bottomed += 1
