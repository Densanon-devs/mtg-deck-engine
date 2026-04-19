"""Static analysis engine: structural inspection, scoring, and recommendations."""

from __future__ import annotations

import re
from collections import Counter

from mtg_deck_engine.classification.tagger import classify_card
from mtg_deck_engine.models import (
    AnalysisResult,
    CardTag,
    Deck,
    Format,
    ValidationIssue,
    Zone,
)

# Recommended ranges by format
_COMMANDER_TARGETS = {
    "lands": (35, 38),
    "ramp": (10, 15),
    "draw": (8, 12),
    "removal": (8, 12),
    "wipes": (2, 4),
    "average_cmc": (2.5, 3.5),
}

_SIXTY_CARD_TARGETS = {
    "lands": (22, 26),
    "ramp": (0, 4),
    "draw": (4, 8),
    "removal": (6, 10),
    "wipes": (0, 3),
    "average_cmc": (2.0, 3.0),
}


def analyze_deck(deck: Deck) -> AnalysisResult:
    """Run full static analysis on a resolved deck."""
    result = AnalysisResult(
        deck_name=deck.name,
        format=deck.format.value if deck.format else None,
    )

    # Only analyze resolved cards
    active_entries = [
        e for e in deck.entries if e.zone not in (Zone.MAYBEBOARD,) and e.card is not None
    ]

    if not active_entries:
        result.issues.append(
            ValidationIssue(severity="error", message="No resolved cards to analyze")
        )
        return result

    # Classify all cards (copy tags to avoid mutating shared Card objects)
    for entry in active_entries:
        if entry.card and not entry.card.tags:
            entry.card.tags = list(classify_card(entry.card))

    result.total_cards = sum(e.quantity for e in active_entries)

    # --- Mana curve ---
    mana_curve: Counter[int] = Counter()
    nonland_cmcs: list[float] = []
    for entry in active_entries:
        card = entry.card
        if card and not card.is_land:
            cmc = card.display_cmc()
            mv = int(cmc)
            capped = min(mv, 7)  # Group 7+ together
            mana_curve[capped] += entry.quantity
            nonland_cmcs.extend([cmc] * entry.quantity)
    result.mana_curve = dict(sorted(mana_curve.items()))
    result.average_cmc = round(sum(nonland_cmcs) / len(nonland_cmcs), 2) if nonland_cmcs else 0.0

    # --- Color distribution ---
    color_dist: Counter[str] = Counter()
    for entry in active_entries:
        if entry.card:
            for color in entry.card.color_identity:
                color_dist[color.value] += entry.quantity
    result.color_distribution = dict(color_dist)

    # --- Color sources (lands and mana producers) ---
    color_sources: Counter[str] = Counter()
    for entry in active_entries:
        card = entry.card
        if card:
            produced = set(card.produced_mana)
            for face in card.faces:
                produced.update(face.produced_mana)
            if card.is_land or CardTag.MANA_ROCK in card.tags or CardTag.MANA_DORK in card.tags:
                for c in produced & {"W", "U", "B", "R", "G"}:
                    color_sources[c] += entry.quantity
    result.color_sources = dict(color_sources)

    # --- Type distribution ---
    type_dist: Counter[str] = Counter()
    for entry in active_entries:
        card = entry.card
        if card:
            if card.is_land:
                type_dist["Land"] += entry.quantity
            if card.is_creature:
                type_dist["Creature"] += entry.quantity
            if card.is_instant:
                type_dist["Instant"] += entry.quantity
            if card.is_sorcery:
                type_dist["Sorcery"] += entry.quantity
            if card.is_artifact and not card.is_creature:
                type_dist["Artifact"] += entry.quantity
            if card.is_enchantment:
                type_dist["Enchantment"] += entry.quantity
            if card.is_planeswalker:
                type_dist["Planeswalker"] += entry.quantity
            if card.is_battle:
                type_dist["Battle"] += entry.quantity
    result.type_distribution = dict(type_dist)

    # --- Tag distribution ---
    tag_dist: Counter[str] = Counter()
    for entry in active_entries:
        if entry.card:
            for tag in entry.card.tags:
                tag_dist[tag.value] += entry.quantity
    result.tag_distribution = dict(tag_dist)

    # --- Key counts ---
    result.land_count = sum(
        e.quantity for e in active_entries if e.card and e.card.is_land
    )
    result.nonland_count = result.total_cards - result.land_count
    result.ramp_count = _count_tag(active_entries, CardTag.RAMP)
    result.interaction_count = (
        _count_tag(active_entries, CardTag.TARGETED_REMOVAL)
        + _count_tag(active_entries, CardTag.BOARD_WIPE)
        + _count_tag(active_entries, CardTag.COUNTERSPELL)
    )
    result.draw_engine_count = _count_tag(active_entries, CardTag.CARD_DRAW)
    result.threat_count = (
        _count_tag(active_entries, CardTag.THREAT) + _count_tag(active_entries, CardTag.FINISHER)
    )

    # --- Scoring ---
    targets = _get_targets(deck.format)
    result.scores = _compute_scores(result, targets)

    # --- Structural issues and recommendations ---
    result.issues.extend(_detect_issues(result, deck, targets))
    result.recommendations = _generate_recommendations(result, deck, targets)

    return result


def _count_tag(entries, tag: CardTag) -> int:
    return sum(e.quantity for e in entries if e.card and tag in e.card.tags)


def _get_targets(fmt: Format | None) -> dict:
    if fmt in (Format.COMMANDER, Format.BRAWL, Format.OATHBREAKER, Format.DUEL):
        return _COMMANDER_TARGETS
    return _SIXTY_CARD_TARGETS


def _compute_scores(result: AnalysisResult, targets: dict) -> dict[str, float]:
    """Compute category scores (0-100) based on target ranges."""
    scores: dict[str, float] = {}

    # Mana base score
    land_lo, land_hi = targets["lands"]
    if land_lo <= result.land_count <= land_hi:
        scores["mana_base"] = 90.0
    elif result.land_count < land_lo:
        scores["mana_base"] = max(0, 90 - (land_lo - result.land_count) * 8)
    else:
        scores["mana_base"] = max(0, 90 - (result.land_count - land_hi) * 5)

    # Ramp score
    ramp_lo, ramp_hi = targets["ramp"]
    if ramp_lo <= result.ramp_count <= ramp_hi:
        scores["ramp"] = 85.0
    elif result.ramp_count < ramp_lo:
        scores["ramp"] = max(0, 85 - (ramp_lo - result.ramp_count) * 10)
    else:
        scores["ramp"] = max(0, 85 - (result.ramp_count - ramp_hi) * 5)

    # Card draw score
    draw_lo, draw_hi = targets["draw"]
    if draw_lo <= result.draw_engine_count <= draw_hi:
        scores["card_advantage"] = 85.0
    elif result.draw_engine_count < draw_lo:
        scores["card_advantage"] = max(0, 85 - (draw_lo - result.draw_engine_count) * 10)
    else:
        scores["card_advantage"] = 85.0  # More draw is rarely bad

    # Interaction score
    removal_lo, removal_hi = targets["removal"]
    if removal_lo <= result.interaction_count <= removal_hi:
        scores["interaction"] = 85.0
    elif result.interaction_count < removal_lo:
        scores["interaction"] = max(0, 85 - (removal_lo - result.interaction_count) * 10)
    else:
        scores["interaction"] = max(0, 85 - (result.interaction_count - removal_hi) * 3)

    # Curve score
    cmc_lo, cmc_hi = targets["average_cmc"]
    if cmc_lo <= result.average_cmc <= cmc_hi:
        scores["curve"] = 90.0
    elif result.average_cmc < cmc_lo:
        scores["curve"] = max(0, 90 - (cmc_lo - result.average_cmc) * 20)
    else:
        scores["curve"] = max(0, 90 - (result.average_cmc - cmc_hi) * 15)

    # Threat density
    if result.threat_count >= 8:
        scores["threat_density"] = 85.0
    elif result.threat_count >= 5:
        scores["threat_density"] = 70.0
    else:
        scores["threat_density"] = max(0, 50 + result.threat_count * 5)

    return scores


def _detect_issues(
    result: AnalysisResult, deck: Deck, targets: dict
) -> list[ValidationIssue]:
    """Detect structural issues in the deck."""
    issues: list[ValidationIssue] = []

    land_lo, land_hi = targets["lands"]
    if result.land_count < land_lo:
        issues.append(
            ValidationIssue(
                severity="warning",
                message=f"Low land count ({result.land_count}). Recommended: {land_lo}-{land_hi}",
            )
        )
    elif result.land_count > land_hi + 2:
        issues.append(
            ValidationIssue(
                severity="warning",
                message=f"High land count ({result.land_count}). Recommended: {land_lo}-{land_hi}",
            )
        )

    ramp_lo, ramp_hi = targets["ramp"]
    if result.ramp_count < ramp_lo:
        issues.append(
            ValidationIssue(
                severity="warning",
                message=f"Low ramp count ({result.ramp_count}). Recommended: {ramp_lo}-{ramp_hi}",
            )
        )

    draw_lo, draw_hi = targets["draw"]
    if result.draw_engine_count < draw_lo:
        issues.append(
            ValidationIssue(
                severity="warning",
                message=f"Low card draw ({result.draw_engine_count}). Recommended: {draw_lo}-{draw_hi}",
            )
        )

    removal_lo, removal_hi = targets["removal"]
    if result.interaction_count < removal_lo:
        issues.append(
            ValidationIssue(
                severity="warning",
                message=f"Low interaction ({result.interaction_count}). Recommended: {removal_lo}-{removal_hi}",
            )
        )

    cmc_lo, cmc_hi = targets["average_cmc"]
    if result.average_cmc > cmc_hi + 0.5:
        issues.append(
            ValidationIssue(
                severity="warning",
                message=f"High average mana value ({result.average_cmc}). This may cause slow starts.",
            )
        )

    # Check for color source sufficiency
    if result.color_distribution:
        for color, need in result.color_distribution.items():
            source = result.color_sources.get(color, 0)
            # Rough heuristic: need at least source-count / total-cards ratio
            if need > 5 and source < 5:
                color_names = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        message=f"Low {color_names.get(color, color)} sources ({source}) for {need} cards needing that color",
                    )
                )

    # Wipe count for commander
    wipe_count = sum(
        e.quantity
        for e in deck.entries
        if e.card and CardTag.BOARD_WIPE in e.card.tags and e.zone != Zone.MAYBEBOARD
    )
    wipe_lo, wipe_hi = targets["wipes"]
    if wipe_count < wipe_lo:
        issues.append(
            ValidationIssue(
                severity="info",
                message=f"Low board wipe count ({wipe_count}). Consider {wipe_lo}-{wipe_hi} wipes for recovery.",
            )
        )

    return issues


def _generate_recommendations(
    result: AnalysisResult, deck: Deck, targets: dict
) -> list[str]:
    """Generate actionable suggestions based on analysis."""
    recs: list[str] = []

    land_lo, land_hi = targets["lands"]
    if result.land_count < land_lo:
        deficit = land_lo - result.land_count
        recs.append(f"Add {deficit} more land(s) to improve mana consistency.")
    elif result.land_count > land_hi + 2:
        excess = result.land_count - land_hi
        recs.append(f"Consider cutting {excess} land(s) — you may flood frequently.")

    ramp_lo, _ = targets["ramp"]
    if result.ramp_count < ramp_lo:
        recs.append(
            f"Add {ramp_lo - result.ramp_count} more ramp piece(s) to accelerate mana development."
        )

    draw_lo, _ = targets["draw"]
    if result.draw_engine_count < draw_lo:
        recs.append(
            f"Add {draw_lo - result.draw_engine_count} more card draw source(s) to avoid running out of gas."
        )

    removal_lo, _ = targets["removal"]
    if result.interaction_count < removal_lo:
        recs.append(
            f"Add {removal_lo - result.interaction_count} more removal/interaction piece(s) to handle opponent threats."
        )

    if result.average_cmc > 3.5:
        # Find high-CMC non-finisher cards as cut candidates
        high_cmc_entries = [
            e
            for e in deck.entries
            if e.card
            and e.card.display_cmc() >= 6
            and CardTag.FINISHER not in e.card.tags
            and e.zone == Zone.MAINBOARD
        ]
        if high_cmc_entries:
            names = ", ".join(e.card_name for e in high_cmc_entries[:3])
            recs.append(
                f"Reduce top-end: consider cutting high-cost cards like {names} to lower average mana value."
            )
        else:
            recs.append("Consider lowering your mana curve — high average MV may cause slow starts.")

    # Check threat density
    if result.threat_count < 5:
        recs.append("Add more threats or win conditions — the deck may struggle to close games.")

    # Color source recommendations
    for color, need in result.color_distribution.items():
        source = result.color_sources.get(color, 0)
        if need > 5 and source < 5:
            color_names = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}
            recs.append(
                f"Increase {color_names.get(color, color)} mana sources to improve casting consistency."
            )

    return recs
