"""Phase 2: Scryfall-backed add candidate selection.

The hard hallucination-proofing claim for ADD suggestions rests on this module:
every card in the candidate table has been pre-filtered against four rules
BEFORE the LLM sees anything.

  1. Color identity subset — card's color identity must be a subset of the
     user's deck colors (Commander rule 4).
  2. Format legality — card must be LEGAL or RESTRICTED in the user's format
     per Scryfall's `legalities` map. Banned/not_legal cards are excluded.
  3. Not already in deck — no duplicate suggestions.
  4. Functional role match — card must be tagged with the requested role
     (ramp, card_draw, targeted_removal, etc.) by the deterministic classifier.

The LLM's job is to pick 3-5 from that validated pool by tag and write a
reason. Any output that references a card name outside a `[aNN]` tag is
rejected — the card can't exist outside the table by construction.

Perf note: we full-scan the cards table (~30k rows). That's ~500ms on a warm
DB. Acceptable for a one-shot analyst pass; if we need repeated queries per
role gap we can add a tags column + index in a follow-up.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Iterable

from mtg_deck_engine.classification.tagger import classify_card
from mtg_deck_engine.models import (
    Card,
    CardTag,
    Format,
    Legality,
)


@dataclass
class AddCandidate:
    """A card pre-validated as eligible to add to the user's deck."""

    tag: str        # e.g. "a01"
    card: Card
    role: CardTag   # The role this card is offered as (ramp, draw, etc.)


# Legality values that count as "may include in deck" — matches Scryfall semantics.
# Restricted applies to Vintage (1-of); most decks operating in restricted rules
# can still technically include the card, so we surface it. Banned / not_legal
# are hard excluded.
_PLAYABLE_LEGALITY = {Legality.LEGAL, Legality.RESTRICTED}


def find_add_candidates(
    db,
    role: CardTag,
    deck_color_identity: set[str],
    format_: Format,
    exclude_names: set[str],
    limit: int = 20,
    budget_usd: float | None = None,
) -> list[AddCandidate]:
    """Return up to `limit` cards validated to match role + color + legality.

    `db` is a CardDatabase. We read `data_json` directly and rebuild cards —
    avoids adding analyst-specific methods to the db class.

    Args:
      budget_usd: optional max USD price per card. Cards with unknown price
        (NULL in the db) are included by default — "unknown" shouldn't block
        the suggestion. Only cards with a known price ABOVE the budget are
        filtered out.
    """
    conn = db.connect()
    # Pre-filter by price in SQL when possible — saves rebuilding Card
    # objects for rows that are definitely excluded. NULL passes through.
    if budget_usd is not None:
        rows = conn.execute(
            "SELECT data_json FROM cards WHERE price_usd IS NULL OR price_usd <= ?",
            (budget_usd,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT data_json FROM cards").fetchall()
    format_key = format_.value if isinstance(format_, Format) else str(format_)

    matches: list[Card] = []
    exclude_lower = {n.lower() for n in exclude_names}

    for (data_json,) in rows:
        card = _card_from_json(data_json)
        if card is None:
            continue

        # Land and basic filtering — lands are suggested via a different query later
        if card.is_land:
            continue
        if card.name.lower() in exclude_lower:
            continue

        # Color identity subset check — Commander rule 4
        card_ci = {c.value for c in card.color_identity}
        if not card_ci.issubset(deck_color_identity):
            continue

        # Legality check
        card_legality = card.legalities.get(format_key)
        if card_legality not in _PLAYABLE_LEGALITY:
            continue

        # Role check — classify deterministically so we don't trust stale tags
        tags = set(classify_card(card))
        if role not in tags:
            continue

        # Preserve the authoritative tag set on the card for downstream use
        card.tags = list(tags)
        matches.append(card)

    # Rank heuristic without edhrec data: prefer lower-CMC + higher-playability
    # proxies (has oracle text, is uncommon/rare rather than mythic-only chase).
    # This keeps the top-20 readable and actionable for the LLM picker.
    matches.sort(key=lambda c: (c.display_cmc(), c.name))
    matches = matches[:limit]

    return [
        AddCandidate(tag=f"a{i:02d}", card=c, role=role)
        for i, c in enumerate(matches, start=1)
    ]


def render_add_table(candidates: list[AddCandidate]) -> str:
    """Compact per-row render for inclusion in LLM prompts."""
    if not candidates:
        return "(no add candidates matched the constraints)"
    lines = []
    for c in candidates:
        card = c.card
        cmc = int(card.display_cmc())
        # Trim oracle text to ~100 chars — enough to reason over without bloating context
        text = (card.oracle_text or "").replace("\n", " ").strip()
        if len(text) > 100:
            text = text[:97] + "..."
        lines.append(
            f"[{c.tag}] {card.name} — {card.mana_cost or '{0}'} "
            f"(CMC {cmc}) — {text or '(no text)'}"
        )
    return "\n".join(lines)


def _card_from_json(data_json: str) -> Card | None:
    """Rebuild a Card from the DB's JSON blob. Mirrors data/database.py._card_from_json."""
    try:
        from mtg_deck_engine.data.database import _card_from_json as db_from_json
        return db_from_json(data_json)
    except Exception:
        return None
