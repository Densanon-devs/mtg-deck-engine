"""Resolve deck entries against the card database."""

from __future__ import annotations

from rich.console import Console

from mtg_deck_engine.data.database import CardDatabase
from mtg_deck_engine.models import Deck, DeckEntry, Format

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
) -> Deck:
    """Resolve card names against the database and build a Deck object."""
    # Collect unique card names
    unique_names = list({e.card_name for e in entries})

    # Batch lookup
    resolved = db.lookup_many(unique_names)

    unresolved: list[str] = []
    for entry in entries:
        card = resolved.get(entry.card_name)
        if card:
            entry.card = card
        else:
            unresolved.append(entry.card_name)

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


def _infer_format(deck: Deck) -> Format | None:
    """Try to guess the format from deck structure."""
    if deck.commanders:
        return Format.COMMANDER

    total = deck.total_mainboard
    if total >= 95:
        return Format.COMMANDER

    # Default to no format rather than guessing wrong
    return None
