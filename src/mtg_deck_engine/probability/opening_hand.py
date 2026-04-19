"""Opening hand analysis: keepability scoring, mulligan simulation, opener archetypes.

Uses Monte Carlo simulation to draw thousands of opening hands and evaluate
each one for keepability based on land count, castable spells, and role balance.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum

from mtg_deck_engine.models import CardTag, Deck, DeckEntry, Format, Zone


class OpenerArchetype(str, Enum):
    """Classification of an opening hand's character."""

    MANA_RICH = "mana_rich"      # 5+ lands, low action
    ACTION_RICH = "action_rich"  # 0-1 lands, high action
    BALANCED = "balanced"        # 2-4 lands with castable spells
    RISKY = "risky"              # Keepable but shaky (1 land + low curve, or 5 land + payoff)
    DEAD = "dead"                # Unkeepable: 0 lands, 6+ lands, no castable spells


@dataclass
class HandEvaluation:
    """Evaluation result for a single opening hand."""

    cards: list[DeckEntry]
    land_count: int = 0
    nonland_count: int = 0
    ramp_count: int = 0
    interaction_count: int = 0
    castable_by_turn_2: int = 0
    castable_by_turn_3: int = 0
    archetype: OpenerArchetype = OpenerArchetype.DEAD
    keepable: bool = False
    score: float = 0.0


@dataclass
class OpeningHandReport:
    """Aggregated results from many simulated opening hands."""

    simulations: int = 0
    keep_rate: float = 0.0
    average_lands: float = 0.0
    average_score: float = 0.0
    archetype_distribution: dict[str, float] = field(default_factory=dict)
    land_count_distribution: dict[int, float] = field(default_factory=dict)
    mulligan_keep_rates: dict[int, float] = field(default_factory=dict)
    best_hands: list[HandEvaluation] = field(default_factory=list)
    worst_keepable_hands: list[HandEvaluation] = field(default_factory=list)


def evaluate_hand(hand: list[DeckEntry], deck: Deck) -> HandEvaluation:
    """Score a single 7-card opening hand."""
    ev = HandEvaluation(cards=hand)

    for entry in hand:
        card = entry.card
        if card is None:
            continue

        if card.is_land:
            ev.land_count += 1
        else:
            ev.nonland_count += 1

        if card.tags:
            if CardTag.RAMP in card.tags or CardTag.MANA_ROCK in card.tags or CardTag.MANA_DORK in card.tags:
                ev.ramp_count += 1
            if CardTag.TARGETED_REMOVAL in card.tags or CardTag.COUNTERSPELL in card.tags or CardTag.BOARD_WIPE in card.tags:
                ev.interaction_count += 1

        # Castability check (simplified: need lands >= CMC; split cards use front-face cost)
        if not card.is_land:
            cmc = card.display_cmc()
            if cmc <= 2:
                ev.castable_by_turn_2 += 1
            if cmc <= 3:
                ev.castable_by_turn_3 += 1

    # Classify archetype
    ev.archetype = _classify_opener(ev, deck)

    # Score the hand
    ev.score = _score_hand(ev, deck)
    ev.keepable = ev.score >= 40.0

    return ev


def simulate_opening_hands(
    deck: Deck,
    simulations: int = 10000,
    seed: int | None = None,
) -> OpeningHandReport:
    """Run Monte Carlo simulation of opening hands.

    Simulates drawing opening hands, evaluating keepability, and
    tracking mulligan decisions through the London mulligan system.
    """
    if seed is not None:
        random.seed(seed)

    report = OpeningHandReport(simulations=simulations)

    # Build the draw pool: expand entries by quantity
    pool = _build_pool(deck)
    if len(pool) < 7:
        return report

    archetype_counts: Counter[str] = Counter()
    land_counts: Counter[int] = Counter()
    scores: list[float] = []
    land_totals: list[int] = []
    keep_count = 0

    # Track mulligan keep rates (hand size 7, 6, 5, 4)
    mull_keeps: dict[int, int] = {7: 0, 6: 0, 5: 0, 4: 0}
    mull_total: dict[int, int] = {7: 0, 6: 0, 5: 0, 4: 0}

    best: list[HandEvaluation] = []
    worst_keepable: list[HandEvaluation] = []

    for _ in range(simulations):
        shuffled = pool.copy()
        random.shuffle(shuffled)

        # London mulligan: draw 7 each time, evaluate the effective hand size
        kept = False
        for mulls in range(4):  # 0, 1, 2, 3 mulligans
            hand_size = 7 - mulls
            hand = shuffled[:7]  # See 7, but evaluate keepability for effective hand_size
            # Simulate bottoming: evaluate only the best hand_size cards
            if mulls > 0:
                scored = sorted(hand, key=lambda e: _bottom_score(e), reverse=True)
                hand = scored[:hand_size]
            ev = evaluate_hand(hand, deck)

            mull_total[hand_size] = mull_total.get(hand_size, 0) + 1

            if ev.keepable or mulls == 3:  # Must keep at 4 cards
                mull_keeps[hand_size] = mull_keeps.get(hand_size, 0) + 1
                archetype_counts[ev.archetype.value] += 1
                land_counts[ev.land_count] += 1
                scores.append(ev.score)
                land_totals.append(ev.land_count)
                keep_count += 1
                kept = True

                # Track best/worst
                if len(best) < 5 or ev.score > min(h.score for h in best):
                    best.append(ev)
                    best.sort(key=lambda h: h.score, reverse=True)
                    best = best[:5]
                if ev.keepable and (len(worst_keepable) < 5 or ev.score < max(h.score for h in worst_keepable)):
                    worst_keepable.append(ev)
                    worst_keepable.sort(key=lambda h: h.score)
                    worst_keepable = worst_keepable[:5]

                break

            # Re-shuffle for next mulligan attempt
            random.shuffle(shuffled)

    report.keep_rate = keep_count / simulations if simulations > 0 else 0.0
    report.average_lands = sum(land_totals) / len(land_totals) if land_totals else 0.0
    report.average_score = sum(scores) / len(scores) if scores else 0.0

    total_hands = sum(archetype_counts.values()) or 1
    report.archetype_distribution = {k: v / total_hands for k, v in archetype_counts.items()}
    report.land_count_distribution = {k: v / total_hands for k, v in sorted(land_counts.items())}

    report.mulligan_keep_rates = {
        size: (mull_keeps.get(size, 0) / mull_total[size] if mull_total.get(size, 0) > 0 else 0.0)
        for size in [7, 6, 5, 4]
    }

    report.best_hands = best
    report.worst_keepable_hands = worst_keepable

    return report


def _build_pool(deck: Deck) -> list[DeckEntry]:
    """Expand deck entries into a flat list (1 entry per card copy)."""
    pool: list[DeckEntry] = []
    for entry in deck.entries:
        if entry.zone in (Zone.MAYBEBOARD, Zone.SIDEBOARD):
            continue
        for _ in range(entry.quantity):
            pool.append(entry)
    return pool


def _classify_opener(ev: HandEvaluation, deck: Deck) -> OpenerArchetype:
    """Classify the archetype of an opening hand."""
    lands = ev.land_count

    if lands == 0:
        return OpenerArchetype.DEAD
    if lands >= 6:
        return OpenerArchetype.DEAD
    if lands >= 5:
        # 5 lands is keepable only with strong payoff
        if ev.castable_by_turn_3 >= 1:
            return OpenerArchetype.MANA_RICH
        return OpenerArchetype.DEAD
    if lands == 1:
        # 1-landers need low curve and/or ramp
        if ev.castable_by_turn_2 >= 2 or ev.ramp_count >= 1:
            return OpenerArchetype.RISKY
        return OpenerArchetype.ACTION_RICH if ev.nonland_count >= 5 else OpenerArchetype.DEAD
    if 2 <= lands <= 4:
        if ev.castable_by_turn_3 >= 2:
            return OpenerArchetype.BALANCED
        if ev.castable_by_turn_2 >= 1:
            return OpenerArchetype.RISKY
        return OpenerArchetype.RISKY

    return OpenerArchetype.DEAD


def _score_hand(ev: HandEvaluation, deck: Deck) -> float:
    """Score a hand from 0-100 based on keepability factors."""
    score = 0.0
    is_commander = deck.format in (Format.COMMANDER, Format.BRAWL, Format.OATHBREAKER, Format.DUEL)

    # Land score (bell curve around 2-4 for 60-card, 3-4 for commander)
    ideal_low = 3 if is_commander else 2
    ideal_high = 4 if is_commander else 3

    if ideal_low <= ev.land_count <= ideal_high:
        score += 35.0
    elif ev.land_count == ideal_low - 1 or ev.land_count == ideal_high + 1:
        score += 20.0
    elif ev.land_count == 0 or ev.land_count >= 6:
        score += 0.0
    else:
        score += 10.0

    # Castable spells score
    score += min(ev.castable_by_turn_2 * 10.0, 25.0)
    score += min(ev.castable_by_turn_3 * 5.0, 15.0)

    # Ramp bonus (especially for commander)
    if is_commander:
        score += min(ev.ramp_count * 8.0, 16.0)
    else:
        score += min(ev.ramp_count * 4.0, 8.0)

    # Interaction bonus
    score += min(ev.interaction_count * 3.0, 9.0)

    # Penalty for no action
    if ev.nonland_count == 0:
        score = 0.0

    return min(score, 100.0)


def _bottom_score(entry: DeckEntry) -> float:
    """Score a card for keeping (higher = more keepable, lower = bottom first)."""
    card = entry.card
    if card is None:
        return -100.0
    if card.is_land:
        return 50.0
    score = max(0, 8 - card.display_cmc()) * 5.0
    if card.tags and (CardTag.RAMP in card.tags or CardTag.MANA_ROCK in card.tags):
        score += 25.0
    if card.tags and CardTag.CARD_DRAW in card.tags:
        score += 15.0
    return score
