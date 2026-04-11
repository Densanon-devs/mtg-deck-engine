"""Deck-to-deck comparison: compare two different decks side by side.

Unlike version comparison (same deck over time), this compares two
entirely different decks to highlight differences in strategy, composition,
and predicted performance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mtg_deck_engine.analysis.static import analyze_deck
from mtg_deck_engine.classification.tagger import classify_card
from mtg_deck_engine.models import AnalysisResult, Deck, Zone


@dataclass
class DeckComparison:
    """Side-by-side comparison of two decks."""

    name_a: str = ""
    name_b: str = ""
    result_a: AnalysisResult | None = None
    result_b: AnalysisResult | None = None

    # Score deltas (B - A)
    score_deltas: dict[str, float] = field(default_factory=dict)

    # Metric deltas
    metric_deltas: dict[str, tuple[float, float]] = field(default_factory=dict)  # key -> (A, B)

    # Card overlap
    shared_cards: list[str] = field(default_factory=list)
    unique_to_a: list[str] = field(default_factory=list)
    unique_to_b: list[str] = field(default_factory=list)
    overlap_percentage: float = 0.0

    # Role comparison
    role_comparison: dict[str, tuple[int, int]] = field(default_factory=dict)  # role -> (A count, B count)

    # Strengths
    a_advantages: list[str] = field(default_factory=list)
    b_advantages: list[str] = field(default_factory=list)


def compare_decks(deck_a: Deck, deck_b: Deck) -> DeckComparison:
    """Compare two different decks."""
    comp = DeckComparison(name_a=deck_a.name, name_b=deck_b.name)

    # Classify cards
    for deck in (deck_a, deck_b):
        for entry in deck.entries:
            if entry.card and not entry.card.tags:
                entry.card.tags = list(classify_card(entry.card))

    # Analyze both
    result_a = analyze_deck(deck_a)
    result_b = analyze_deck(deck_b)
    comp.result_a = result_a
    comp.result_b = result_b

    # Score deltas
    all_scores = set(result_a.scores.keys()) | set(result_b.scores.keys())
    for key in all_scores:
        sa = result_a.scores.get(key, 0)
        sb = result_b.scores.get(key, 0)
        comp.score_deltas[key] = round(sb - sa, 1)

    # Metric deltas
    metrics = [
        ("lands", result_a.land_count, result_b.land_count),
        ("ramp", result_a.ramp_count, result_b.ramp_count),
        ("card_draw", result_a.draw_engine_count, result_b.draw_engine_count),
        ("interaction", result_a.interaction_count, result_b.interaction_count),
        ("threats", result_a.threat_count, result_b.threat_count),
        ("avg_cmc", result_a.average_cmc, result_b.average_cmc),
        ("total_cards", result_a.total_cards, result_b.total_cards),
    ]
    for name, va, vb in metrics:
        comp.metric_deltas[name] = (va, vb)

    # Card overlap
    cards_a = set()
    cards_b = set()
    for e in deck_a.entries:
        if e.zone != Zone.MAYBEBOARD:
            cards_a.add(e.card_name.lower())
    for e in deck_b.entries:
        if e.zone != Zone.MAYBEBOARD:
            cards_b.add(e.card_name.lower())

    shared = cards_a & cards_b
    comp.shared_cards = sorted(shared)
    comp.unique_to_a = sorted(cards_a - cards_b)
    comp.unique_to_b = sorted(cards_b - cards_a)
    total_unique = len(cards_a | cards_b)
    comp.overlap_percentage = round(len(shared) / max(1, total_unique) * 100, 1)

    # Role comparison
    roles = ["land", "ramp", "card_draw", "targeted_removal", "counterspell",
             "board_wipe", "threat", "finisher", "engine", "tutor"]
    for role in roles:
        count_a = result_a.tag_distribution.get(role, 0)
        count_b = result_b.tag_distribution.get(role, 0)
        if count_a > 0 or count_b > 0:
            comp.role_comparison[role] = (count_a, count_b)

    # Determine advantages
    for key, delta in comp.score_deltas.items():
        name = key.replace("_", " ").title()
        if delta >= 5:
            comp.b_advantages.append(f"Better {name} ({delta:+.0f})")
        elif delta <= -5:
            comp.a_advantages.append(f"Better {name} ({-delta:+.0f})")

    if result_a.average_cmc < result_b.average_cmc - 0.3:
        comp.a_advantages.append(f"Lower curve ({result_a.average_cmc:.1f} vs {result_b.average_cmc:.1f})")
    elif result_b.average_cmc < result_a.average_cmc - 0.3:
        comp.b_advantages.append(f"Lower curve ({result_b.average_cmc:.1f} vs {result_a.average_cmc:.1f})")

    return comp
