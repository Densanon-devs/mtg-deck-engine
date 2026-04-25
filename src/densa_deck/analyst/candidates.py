"""Deterministic candidate selection for the analyst.

All analyst outputs that reference cards must reference a card from a
pre-validated candidate table. This module produces those tables — cut
candidates come straight from the user's deck (zero hallucination risk);
add candidates (phase 2) come from Scryfall queries constrained on color
identity, format legality, and banlist.

Every candidate gets a short tag of the form `[c01]`, `[a01]` so the LLM
can reference it without ever emitting a raw card name.
"""

from __future__ import annotations

from dataclasses import dataclass

from densa_deck.models import CardTag, Deck, DeckEntry, Zone


@dataclass
class CutCandidate:
    """A card in the user's deck that's a plausible cut."""

    tag: str           # e.g. "c01"
    entry: DeckEntry
    score: float       # Higher = more cut-worthy
    reasons: list[str]  # Machine-readable: "high_cmc", "no_functional_tag", "redundant_ramp"


def rank_cut_candidates(
    deck: Deck,
    limit: int = 12,
    *,
    protected_card_names: set[str] | None = None,
) -> list[CutCandidate]:
    """Rank deck cards by how cut-worthy they look from structural signals alone.

    Signals (higher = more cut-worthy):
      - High display_cmc non-finisher non-ramp cards
      - Cards with zero functional tags (probably filler)
      - Redundant role: if the deck has 15 ramp and target is 10-15, the
        highest-CMC ramp cards are surfaced as trim candidates
      - Not lands, not commander

    `protected_card_names` (optional, lower-cased): cards that must NEVER
    be surfaced as cut candidates. Used to shield combo pieces from
    accidental cut suggestions — without this, the analyst would
    cheerfully recommend cutting Thassa's Oracle from a Thoracle deck
    because it's "high-CMC non-finisher".

    The LLM picks FROM this list. It cannot add cards not on the list because
    the prompt only shows these tags. Zero-hallucination surface for cuts.
    """
    protected = protected_card_names or set()
    active = [
        e for e in deck.entries
        if e.zone == Zone.MAINBOARD and e.card is not None and not e.card.is_land
    ]

    # Count role totals so we know which roles are over-provisioned for Commander
    role_totals: dict[CardTag, int] = {}
    for e in active:
        if e.card and e.card.tags:
            for t in e.card.tags:
                role_totals[t] = role_totals.get(t, 0) + e.quantity

    # Commander targets — matches analysis/static.py _COMMANDER_TARGETS
    is_commander = sum(e.quantity for e in active) + sum(
        e.quantity for e in deck.entries if e.card and e.card.is_land and e.zone == Zone.MAINBOARD
    ) >= 95
    if is_commander:
        role_caps = {CardTag.RAMP: 15, CardTag.CARD_DRAW: 12, CardTag.TARGETED_REMOVAL: 12}
    else:
        role_caps = {CardTag.RAMP: 4, CardTag.CARD_DRAW: 8, CardTag.TARGETED_REMOVAL: 10}

    candidates: list[CutCandidate] = []
    seen_names: set[str] = set()  # Dedup by card name — Commander is singleton,
                                   # but synthetic decks or sideboards can have dupes.
    for entry in active:
        card = entry.card
        if card is None or card.name in seen_names:
            continue
        # Combo-piece protection: never suggest cutting a card that
        # participates in a known combo line for this deck.
        if protected and card.name.lower() in protected:
            continue
        tags = set(card.tags or [])

        # Finishers and ramp are rarely cut in isolation — require strong redundancy
        is_finisher = CardTag.FINISHER in tags
        is_ramp = CardTag.RAMP in tags or CardTag.MANA_ROCK in tags or CardTag.MANA_DORK in tags

        score = 0.0
        reasons: list[str] = []

        cmc = card.display_cmc()

        # High-CMC non-finisher bias
        if cmc >= 6 and not is_finisher:
            score += 30.0
            reasons.append("high_cmc_non_finisher")
        elif cmc >= 5 and not is_finisher:
            score += 15.0
            reasons.append("high_cmc")

        # No functional tag at all — likely filler
        if not tags or tags == {CardTag.THREAT}:
            score += 20.0
            reasons.append("no_functional_tag")

        # Combo bonus: high-CMC AND no functional tag is the textbook "vanilla
        # bloat" pattern — boost it so these cards rank above low-CMC untagged
        # creatures (which accumulate the no_functional_tag signal but aren't
        # the prime cut target).
        if cmc >= 5 and (not tags or tags == {CardTag.THREAT}):
            score += 15.0
            reasons.append("vanilla_bloat")

        # Redundant role (only counts if this card's role is over the cap)
        for tag, cap in role_caps.items():
            if tag in tags and role_totals.get(tag, 0) > cap:
                # Highest-CMC member of the over-provisioned role gets trimmed first
                score += max(0.0, cmc) * 2.0
                reasons.append(f"redundant_{tag.value}")
                break

        # Don't surface finishers unless they're blatantly redundant
        if is_finisher and "redundant_finisher" not in reasons and score < 25:
            continue

        # Ramp at the low end of the curve is basically never a cut — skip
        if is_ramp and cmc <= 2:
            continue

        if score > 0:
            candidates.append(CutCandidate(
                tag="",  # Filled after sort
                entry=entry,
                score=round(score, 2),
                reasons=reasons,
            ))
            seen_names.add(card.name)

    candidates.sort(key=lambda c: c.score, reverse=True)
    candidates = candidates[:limit]
    for i, c in enumerate(candidates, start=1):
        c.tag = f"c{i:02d}"
    return candidates


def render_cut_table(candidates: list[CutCandidate]) -> str:
    """Render candidates as a compact table for inclusion in LLM prompts.

    Format each row so it's easy to skim and the LLM can reference by tag.
    """
    if not candidates:
        return "(no cut candidates surfaced by the rule engine)"
    lines = []
    for c in candidates:
        card = c.entry.card
        if card is None:
            continue
        cmc = int(card.display_cmc())
        tags_str = ", ".join(t.value for t in (card.tags or [])) or "none"
        lines.append(
            f"[{c.tag}] {card.name} — CMC {cmc} — tags: {tags_str} — "
            f"signals: {'/'.join(c.reasons)}"
        )
    return "\n".join(lines)
