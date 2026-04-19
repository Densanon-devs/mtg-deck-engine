"""Commander power level estimator (1-10 scale).

Combines multiple signals into a single power level rating:
- Speed (goldfish clock / curve efficiency)
- Interaction density and quality
- Combo potential (tutors + engines + low-CMC win conditions)
- Mana efficiency (ramp, curve, color fixing)
- Win condition quality (finisher density, diversity)
- Card quality proxy (average CMC, dead card density)

Each axis scores 0-10. The weighted average produces the final rating.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mtg_deck_engine.models import CardTag, Deck, Format, Zone


@dataclass
class PowerBreakdown:
    """Detailed breakdown of what drives the power level."""

    speed: float = 0.0
    interaction: float = 0.0
    combo_potential: float = 0.0
    mana_efficiency: float = 0.0
    win_condition_quality: float = 0.0
    card_quality: float = 0.0
    overall: float = 0.0
    tier: str = ""  # "jank", "casual", "focused", "optimized", "competitive", "cedh"
    reasons_up: list[str] = field(default_factory=list)
    reasons_down: list[str] = field(default_factory=list)


# Weights for each axis
_WEIGHTS = {
    "speed": 1.5,
    "interaction": 1.2,
    "combo_potential": 1.5,
    "mana_efficiency": 1.3,
    "win_condition_quality": 1.2,
    "card_quality": 1.0,
}


def estimate_power_level(deck: Deck) -> PowerBreakdown:
    """Estimate the power level of a deck on a 1-10 scale."""
    pb = PowerBreakdown()
    active = [e for e in deck.entries if e.zone not in (Zone.MAYBEBOARD, Zone.SIDEBOARD) and e.card]
    total = sum(e.quantity for e in active)

    if total == 0:
        pb.overall = 1.0
        pb.tier = "jank"
        return pb

    # Tag counts
    tags: dict[str, int] = {}
    for entry in active:
        if entry.card and entry.card.tags:
            for tag in entry.card.tags:
                tags[tag.value] = tags.get(tag.value, 0) + entry.quantity

    lands = sum(e.quantity for e in active if e.card and e.card.is_land)
    nonlands = [e for e in active if e.card and not e.card.is_land]
    nonland_count = sum(e.quantity for e in nonlands)

    avg_cmc = (
        sum(e.card.display_cmc() * e.quantity for e in nonlands if e.card)
        / max(1, nonland_count)
    )

    # --- Speed (0-10) ---
    # Lower curve = faster = higher score
    if avg_cmc <= 1.8:
        pb.speed = 9.0
        pb.reasons_up.append("Very low curve enables fast execution")
    elif avg_cmc <= 2.3:
        pb.speed = 7.5
    elif avg_cmc <= 2.8:
        pb.speed = 6.0
    elif avg_cmc <= 3.3:
        pb.speed = 4.5
    elif avg_cmc <= 3.8:
        pb.speed = 3.0
    else:
        pb.speed = 1.5
        pb.reasons_down.append("High average mana value slows the deck")

    # Ramp bonus
    ramp = tags.get("ramp", 0) + tags.get("mana_rock", 0) + tags.get("mana_dork", 0)
    if ramp >= 12:
        pb.speed = min(10, pb.speed + 1.5)
        pb.reasons_up.append(f"Heavy ramp package ({ramp} sources) accelerates the game plan")
    elif ramp >= 8:
        pb.speed = min(10, pb.speed + 0.5)

    # --- Interaction (0-10) ---
    removal = tags.get("targeted_removal", 0)
    counters = tags.get("counterspell", 0)
    wipes = tags.get("board_wipe", 0)
    total_interaction = removal + counters + wipes

    if counters >= 6:
        pb.interaction = 8.0
        pb.reasons_up.append(f"Heavy counterspell suite ({counters}) controls the stack")
    elif total_interaction >= 12:
        pb.interaction = 7.0
    elif total_interaction >= 8:
        pb.interaction = 5.5
    elif total_interaction >= 5:
        pb.interaction = 4.0
    elif total_interaction >= 2:
        pb.interaction = 2.5
    else:
        pb.interaction = 1.0
        pb.reasons_down.append("Almost no interaction — vulnerable to opponent strategies")

    # --- Combo potential (0-10) ---
    tutors = tags.get("tutor", 0)
    engines = tags.get("engine", 0)

    combo_score = 0.0
    if tutors >= 5:
        combo_score += 4.0
        pb.reasons_up.append(f"Tutor-heavy ({tutors}) enables consistent combo assembly")
    elif tutors >= 3:
        combo_score += 2.5
    elif tutors >= 1:
        combo_score += 1.0

    if engines >= 5:
        combo_score += 3.0
    elif engines >= 3:
        combo_score += 2.0
    elif engines >= 1:
        combo_score += 1.0

    # Low-CMC win conditions boost combo rating
    low_cmc_finishers = sum(
        e.quantity for e in active
        if e.card and e.card.tags and CardTag.FINISHER in e.card.tags and e.card.display_cmc() <= 4
    )
    if low_cmc_finishers >= 3:
        combo_score += 2.0
        pb.reasons_up.append("Multiple low-cost win conditions")
    elif low_cmc_finishers >= 1:
        combo_score += 1.0

    pb.combo_potential = min(10, combo_score)

    # --- Mana efficiency (0-10) ---
    is_commander = deck.format in (Format.COMMANDER, Format.BRAWL, Format.OATHBREAKER)
    ideal_lands = (35, 38) if is_commander else (22, 26)

    mana_score = 5.0  # Baseline
    if ideal_lands[0] <= lands <= ideal_lands[1]:
        mana_score += 2.0
    elif lands < ideal_lands[0] - 3 or lands > ideal_lands[1] + 3:
        mana_score -= 2.0
        pb.reasons_down.append(f"Land count ({lands}) is far from ideal range")

    if ramp >= 10 and is_commander:
        mana_score += 2.0
    elif ramp >= 6:
        mana_score += 1.0

    # Curve efficiency bonus
    if avg_cmc <= 2.5:
        mana_score += 1.0

    pb.mana_efficiency = max(0, min(10, mana_score))

    # --- Win condition quality (0-10) ---
    finishers = tags.get("finisher", 0)
    threats = tags.get("threat", 0)
    total_wincons = finishers + threats

    if total_wincons >= 15:
        pb.win_condition_quality = 7.0
    elif total_wincons >= 10:
        pb.win_condition_quality = 6.0
    elif total_wincons >= 6:
        pb.win_condition_quality = 4.5
    elif total_wincons >= 3:
        pb.win_condition_quality = 3.0
    else:
        pb.win_condition_quality = 1.5
        pb.reasons_down.append("Very few threats or finishers")

    # Diversity bonus
    unique_finishers = len(set(
        e.card.name for e in active
        if e.card and e.card.tags and CardTag.FINISHER in e.card.tags
    ))
    if unique_finishers >= 4:
        pb.win_condition_quality = min(10, pb.win_condition_quality + 2.0)

    # --- Card quality proxy (0-10) ---
    # Lower avg CMC, fewer dead cards, more cantrips/draw = higher quality
    draw = tags.get("card_draw", 0) + tags.get("cantrip", 0)
    card_q = 5.0

    if draw >= 10:
        card_q += 2.0
        pb.reasons_up.append(f"Strong card selection ({draw} draw/cantrip sources)")
    elif draw >= 6:
        card_q += 1.0

    if avg_cmc <= 2.5:
        card_q += 1.5
    elif avg_cmc >= 4.0:
        card_q -= 1.5

    # Protection for key pieces
    protection = tags.get("protection", 0)
    if protection >= 5:
        card_q += 1.0

    pb.card_quality = max(0, min(10, card_q))

    # --- Final rating ---
    weighted_sum = (
        pb.speed * _WEIGHTS["speed"]
        + pb.interaction * _WEIGHTS["interaction"]
        + pb.combo_potential * _WEIGHTS["combo_potential"]
        + pb.mana_efficiency * _WEIGHTS["mana_efficiency"]
        + pb.win_condition_quality * _WEIGHTS["win_condition_quality"]
        + pb.card_quality * _WEIGHTS["card_quality"]
    )
    total_weight = sum(_WEIGHTS.values())
    raw = weighted_sum / total_weight

    # Clamp to 1-10
    pb.overall = round(max(1.0, min(10.0, raw)), 1)

    # Tier label
    if pb.overall >= 9.0:
        pb.tier = "cEDH"
    elif pb.overall >= 7.5:
        pb.tier = "competitive"
    elif pb.overall >= 6.0:
        pb.tier = "optimized"
    elif pb.overall >= 4.5:
        pb.tier = "focused"
    elif pb.overall >= 3.0:
        pb.tier = "casual"
    else:
        pb.tier = "jank"

    return pb
