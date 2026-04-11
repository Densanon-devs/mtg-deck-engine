"""Advanced heuristics: synergy detection, pip analysis, mana base grading, win-con concentration.

These go deeper than the static analysis by examining card relationships,
color pip demands, and deck focus metrics.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from mtg_deck_engine.models import CardTag, Color, Deck, DeckEntry, Zone


@dataclass
class PipAnalysis:
    """Color pip intensity analysis — how demanding is each color?"""

    total_pips: int = 0
    pips_by_color: dict[str, int] = field(default_factory=dict)
    pip_density: dict[str, float] = field(default_factory=dict)  # pips / nonland cards
    heaviest_color: str = ""
    multi_pip_cards: list[str] = field(default_factory=list)  # Cards with 2+ pips of one color
    sources_per_pip: dict[str, float] = field(default_factory=dict)  # source count / pip count


@dataclass
class SynergyPair:
    """A detected synergy between two cards."""

    card_a: str
    card_b: str
    reason: str
    strength: float = 0.0  # 0-1


@dataclass
class WinConAnalysis:
    """Win condition concentration scoring."""

    total_win_cons: int = 0
    win_con_cards: list[str] = field(default_factory=list)
    concentration: float = 0.0  # What % of wins depend on top win-con
    diversity_score: float = 0.0  # Higher = more diverse win paths


@dataclass
class AdvancedReport:
    """Combined advanced heuristics report."""

    pip_analysis: PipAnalysis = field(default_factory=PipAnalysis)
    synergies: list[SynergyPair] = field(default_factory=list)
    win_con_analysis: WinConAnalysis = field(default_factory=WinConAnalysis)
    mana_base_grade: str = ""
    mana_base_notes: list[str] = field(default_factory=list)
    advanced_recommendations: list[str] = field(default_factory=list)


# =============================================================================
# Pip analysis
# =============================================================================

_PIP_PATTERN = re.compile(r"\{([WUBRG])\}")


def analyze_pips(deck: Deck, color_sources: dict[str, int] | None = None) -> PipAnalysis:
    """Analyze color pip intensity across the deck."""
    pa = PipAnalysis()
    active = [e for e in deck.entries if e.zone not in (Zone.MAYBEBOARD, Zone.SIDEBOARD) and e.card]
    nonland_count = sum(e.quantity for e in active if e.card and not e.card.is_land)

    pip_counter: Counter[str] = Counter()
    multi_pip_cards: list[str] = []

    for entry in active:
        card = entry.card
        if card is None or card.is_land:
            continue
        mana_cost = card.mana_cost
        if not mana_cost:
            # Check faces for DFCs
            if card.faces:
                mana_cost = card.faces[0].mana_cost

        pips = _PIP_PATTERN.findall(mana_cost)
        card_pip_count: Counter[str] = Counter(pips)

        for color, count in card_pip_count.items():
            pip_counter[color] += count * entry.quantity
            if count >= 2:
                multi_pip_cards.append(f"{card.name} ({count}{color})")

    pa.total_pips = sum(pip_counter.values())
    pa.pips_by_color = dict(pip_counter)
    pa.multi_pip_cards = multi_pip_cards

    if nonland_count > 0:
        pa.pip_density = {c: round(n / nonland_count, 2) for c, n in pip_counter.items()}

    if pip_counter:
        pa.heaviest_color = pip_counter.most_common(1)[0][0]

    # Sources per pip
    if color_sources:
        for color, pips in pip_counter.items():
            sources = color_sources.get(color, 0)
            pa.sources_per_pip[color] = round(sources / max(1, pips), 2)

    return pa


# =============================================================================
# Synergy detection
# =============================================================================

# Known synergy patterns: tag combos that work together
_SYNERGY_RULES: list[tuple[str, str, str, float]] = [
    ("sacrifice_outlet", "aristocrat_payoff", "Sacrifice outlet feeds death triggers", 0.9),
    ("sacrifice_outlet", "recursion", "Sacrifice + recursion creates a loop", 0.8),
    ("sacrifice_outlet", "token_maker", "Tokens provide sacrifice fodder", 0.7),
    ("token_maker", "aristocrat_payoff", "Tokens die to trigger payoffs", 0.8),
    ("engine", "card_draw", "Engine + draw creates value chains", 0.6),
    ("ramp", "finisher", "Ramp accelerates into finishers", 0.5),
    ("cost_reducer", "engine", "Cost reduction amplifies engine output", 0.7),
    ("equipment", "threat", "Equipment boosts threat damage", 0.5),
    ("counterspell", "engine", "Protection for key engines", 0.6),
    ("tutor", "finisher", "Tutors find win conditions", 0.7),
    ("lifegain", "aristocrat_payoff", "Lifegain triggers alongside drains", 0.6),
    ("graveyard_hate", "recursion", "Asymmetric graveyard control", 0.5),
]


def detect_synergies(deck: Deck, max_results: int = 15) -> list[SynergyPair]:
    """Detect synergy pairs in the deck based on tag combinations."""
    active = [e for e in deck.entries if e.zone not in (Zone.MAYBEBOARD, Zone.SIDEBOARD) and e.card]

    # Index cards by tag
    tag_to_cards: dict[str, list[str]] = {}
    for entry in active:
        if entry.card and entry.card.tags:
            for tag in entry.card.tags:
                tag_to_cards.setdefault(tag.value, []).append(entry.card.name)

    synergies: list[SynergyPair] = []
    seen = set()

    for tag_a, tag_b, reason, strength in _SYNERGY_RULES:
        cards_a = tag_to_cards.get(tag_a, [])
        cards_b = tag_to_cards.get(tag_b, [])

        if not cards_a or not cards_b:
            continue

        # Pick representative pairs (not all combinations)
        for ca in cards_a[:3]:
            for cb in cards_b[:3]:
                if ca == cb:
                    continue
                pair_key = tuple(sorted([ca, cb]))
                if pair_key in seen:
                    continue
                seen.add(pair_key)
                synergies.append(SynergyPair(
                    card_a=ca, card_b=cb, reason=reason, strength=strength,
                ))

    # Sort by strength and limit
    synergies.sort(key=lambda s: s.strength, reverse=True)
    return synergies[:max_results]


# =============================================================================
# Win condition analysis
# =============================================================================


def analyze_win_conditions(deck: Deck) -> WinConAnalysis:
    """Analyze win condition concentration and diversity."""
    wca = WinConAnalysis()
    active = [e for e in deck.entries if e.zone not in (Zone.MAYBEBOARD, Zone.SIDEBOARD) and e.card]

    win_cons: list[str] = []
    for entry in active:
        if entry.card and entry.card.tags:
            if CardTag.FINISHER in entry.card.tags:
                win_cons.extend([entry.card.name] * entry.quantity)

    # Also count commanders as win cons
    for entry in deck.commanders:
        if entry.card:
            win_cons.append(entry.card.name)

    wca.total_win_cons = len(win_cons)
    wca.win_con_cards = list(set(win_cons))

    if wca.total_win_cons == 0:
        wca.concentration = 1.0  # No win cons is maximum concentration (bad)
        wca.diversity_score = 0.0
        return wca

    # Concentration: how much do we rely on a single card?
    name_counts = Counter(win_cons)
    most_common_count = name_counts.most_common(1)[0][1]
    wca.concentration = round(most_common_count / len(win_cons), 2)

    # Diversity: more unique win cons = more diverse
    unique_count = len(set(win_cons))
    wca.diversity_score = round(min(1.0, unique_count / 5), 2)  # 5+ unique = max diversity

    return wca


# =============================================================================
# Mana base grading
# =============================================================================


def grade_mana_base(
    deck: Deck,
    pip_analysis: PipAnalysis,
    color_sources: dict[str, int],
) -> tuple[str, list[str]]:
    """Grade the mana base from A+ to F based on pip demands vs sources."""
    notes: list[str] = []
    total_score = 0.0
    checks = 0

    for color, pips in pip_analysis.pips_by_color.items():
        if pips == 0:
            continue
        sources = color_sources.get(color, 0)
        ratio = sources / max(1, pips)

        color_names = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}
        cname = color_names.get(color, color)

        if ratio >= 1.5:
            total_score += 95
            notes.append(f"{cname}: excellent ({sources} sources / {pips} pips)")
        elif ratio >= 1.0:
            total_score += 80
            notes.append(f"{cname}: good ({sources} sources / {pips} pips)")
        elif ratio >= 0.7:
            total_score += 60
            notes.append(f"{cname}: fair ({sources} sources / {pips} pips) — may miss on color")
        elif ratio >= 0.4:
            total_score += 35
            notes.append(f"{cname}: weak ({sources} sources / {pips} pips) — frequent color issues")
        else:
            total_score += 10
            notes.append(f"{cname}: critical ({sources} sources / {pips} pips) — severe deficit")
        checks += 1

    # Multi-pip penalty
    if pip_analysis.multi_pip_cards:
        count = len(pip_analysis.multi_pip_cards)
        if count > 5:
            notes.append(f"{count} cards with 2+ pips of one color — high color strain")
            total_score -= 5

    avg_score = total_score / max(1, checks)

    if avg_score >= 90:
        grade = "A+"
    elif avg_score >= 80:
        grade = "A"
    elif avg_score >= 70:
        grade = "B+"
    elif avg_score >= 60:
        grade = "B"
    elif avg_score >= 50:
        grade = "C+"
    elif avg_score >= 40:
        grade = "C"
    elif avg_score >= 30:
        grade = "D"
    else:
        grade = "F"

    return grade, notes


# =============================================================================
# Combined advanced analysis
# =============================================================================


def run_advanced_analysis(deck: Deck, color_sources: dict[str, int] | None = None) -> AdvancedReport:
    """Run all advanced heuristics and return a combined report."""
    report = AdvancedReport()
    sources = color_sources or {}

    report.pip_analysis = analyze_pips(deck, sources)
    report.synergies = detect_synergies(deck)
    report.win_con_analysis = analyze_win_conditions(deck)

    if sources:
        grade, notes = grade_mana_base(deck, report.pip_analysis, sources)
        report.mana_base_grade = grade
        report.mana_base_notes = notes

    # Advanced recommendations
    recs = []
    wca = report.win_con_analysis
    if wca.total_win_cons == 0:
        recs.append("No finishers detected — the deck may struggle to close games.")
    elif wca.concentration > 0.5:
        recs.append(
            f"Win conditions are concentrated — {wca.concentration * 100:.0f}% of lines "
            f"depend on one card. Diversify to reduce fragility."
        )
    elif wca.diversity_score < 0.4:
        recs.append("Few unique win conditions — consider adding alternative win paths.")

    pa = report.pip_analysis
    for color, ratio in pa.sources_per_pip.items():
        if ratio < 0.7:
            color_names = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}
            recs.append(
                f"{color_names.get(color, color)} mana sources are strained "
                f"({ratio:.1f} sources per pip) — add {color_names.get(color, color)} sources "
                f"or reduce {color_names.get(color, color)} pip demands."
            )

    if not report.synergies:
        recs.append("Few card synergies detected — consider adding cards that work together.")

    report.advanced_recommendations = recs
    return report
