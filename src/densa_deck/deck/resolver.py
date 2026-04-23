"""Resolve deck entries against the card database."""

from __future__ import annotations

import time

from rich.console import Console

from densa_deck.data.database import CardDatabase
from densa_deck.models import Deck, DeckEntry, Format

console = Console()

# Format inference heuristics
_FORMAT_HINTS = {
    Format.COMMANDER: lambda d: bool(d.commanders) or d.total_mainboard >= 95,
    Format.STANDARD: lambda d: 55 <= d.total_mainboard <= 80 and not d.commanders,
}


def resolve_deck(
    entries: list[DeckEntry],
    db: CardDatabase,
    name: str = "Untitled Deck",
    format: Format | None = None,
    online_fallback: bool = True,
) -> Deck:
    """Resolve card names against the database and build a Deck object.

    Three-pass resolution:
      1. Direct `cards.name` match (covers the 99% happy path).
      2. Cached `card_aliases` lookup — flavor_name -> oracle_name mappings
         we've populated on prior imports.
      3. Online Scryfall fuzzy fallback for anything still missing. This
         catches Universes Within flavor names like "Dracula, Blood Immortal"
         (-> "Falkenrath Forebear") from Crimson Vow's Dracula subseries,
         Warhammer 40K reprints, Doctor Who reprints, etc. Result is
         cached in card_aliases so the same deck re-imports instantly
         offline next time.

    `online_fallback=False` skips step 3 — useful for tests and for users
    who explicitly want offline-only resolution.
    """
    # Collect unique card names
    unique_names = list({e.card_name for e in entries})

    # Pass 1: batch canonical-name lookup
    resolved = db.lookup_many(unique_names)

    unresolved: list[str] = []
    for entry in entries:
        card = resolved.get(entry.card_name)
        if card:
            entry.card = card
        else:
            unresolved.append(entry.card_name)

    # Pass 2: check the local alias cache (populated by prior online
    # fallbacks, so a second import with the same flavor-named cards is
    # fully offline).
    if unresolved:
        still_unresolved: list[str] = []
        for unr_name in dict.fromkeys(unresolved):  # preserve order, dedupe
            alias_card = db.lookup_alias(unr_name)
            if alias_card is None:
                still_unresolved.append(unr_name)
                continue
            for e in entries:
                if e.card_name == unr_name and e.card is None:
                    e.card = alias_card
        unresolved = [n for n in unresolved if any(
            e.card is None and e.card_name == n for e in entries
        )]
        unresolved = list(dict.fromkeys(unresolved))

    # Pass 3: online Scryfall fallback (if allowed + anything still missing)
    if unresolved and online_fallback:
        fetched = _fetch_oracle_names_via_scryfall(unresolved)
        for flavor_name, oracle_name in fetched.items():
            db.add_alias(flavor_name, oracle_name)
            card = db.lookup_by_name(oracle_name)
            if card is None:
                continue
            for e in entries:
                if e.card_name == flavor_name and e.card is None:
                    e.card = card
        unresolved = [n for n in unresolved if n not in fetched]

    if unresolved:
        console.print(f"[yellow]Could not resolve {len(unresolved)} card(s):[/yellow]")
        for name_u in unresolved[:10]:
            console.print(f"  [dim]- {name_u}[/dim]")
        if len(unresolved) > 10:
            console.print(f"  [dim]... and {len(unresolved) - 10} more[/dim]")

    deck = Deck(name=name, format=format, entries=entries)

    # Try to infer format if not specified
    if deck.format is None:
        deck.format = _infer_format(deck)
        if deck.format:
            console.print(f"[cyan]Inferred format: {deck.format.value}[/cyan]")

    return deck


def _fetch_oracle_names_via_scryfall(names: list[str]) -> dict[str, str]:
    """Hit Scryfall's fuzzy `/cards/named` endpoint for each unresolved
    name and return the {input_name -> oracle_name} mapping for the ones
    that resolved.

    Rate-limited at 10 req/s per Scryfall's guidance. Swallows network
    and HTTP errors per-name so one offline / 404 card doesn't abort
    the whole batch; unresolved cards just stay unresolved.
    """
    import httpx
    out: dict[str, str] = {}
    try:
        client = httpx.Client(
            timeout=10,
            headers={
                "User-Agent": "DensaDeck/0.1 (+https://toolkit.densanon.com/densa-deck.html)",
                "Accept": "application/json",
            },
        )
    except Exception:
        return out
    try:
        for i, name in enumerate(names):
            if i > 0:
                time.sleep(0.1)  # 10 req/s ceiling, Scryfall's published limit
            try:
                r = client.get("https://api.scryfall.com/cards/named", params={"fuzzy": name})
                if r.status_code != 200:
                    continue
                oracle_name = r.json().get("name", "")
                if oracle_name:
                    out[name] = oracle_name
            except Exception:
                continue
    finally:
        client.close()
    return out


def _infer_format(deck: Deck) -> Format | None:
    """Try to guess the format from deck structure."""
    if deck.commanders:
        return Format.COMMANDER

    total = deck.total_mainboard
    if total >= 95:
        return Format.COMMANDER

    # Default to no format rather than guessing wrong
    return None
