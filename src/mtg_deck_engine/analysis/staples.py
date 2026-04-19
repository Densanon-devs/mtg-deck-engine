"""Format staples checker — flag commonly-played cards that are absent.

Maintains curated lists of staples per format and color identity.
Checks the deck against applicable staples and suggests missing ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mtg_deck_engine.models import Color, Deck, Format, Zone


@dataclass
class MissingStaple:
    """A commonly-played card that's absent from the deck."""

    name: str
    reason: str
    priority: str = "suggested"  # "essential", "recommended", "suggested"


@dataclass
class StaplesReport:
    """Staples check results."""

    format: str = ""
    color_identity: list[str] = field(default_factory=list)
    missing: list[MissingStaple] = field(default_factory=list)
    present_staples: list[str] = field(default_factory=list)
    staple_coverage: float = 0.0  # % of applicable staples present


# Colorless staples applicable to most Commander decks.
# Priorities mirror the free browser staples checker at
# densanon-toolkit/categories/mtg-tools/deck-staples-checker/index.html so users comparing
# tools don't see a staple marked one priority in the browser and another in the desktop app.
_COMMANDER_COLORLESS = [
    ("Sol Ring", "Best mana rock in the format", "essential"),
    ("Arcane Signet", "Efficient color-fixing rock", "essential"),
    ("Command Tower", "Free color-fixing land", "essential"),
    ("Lightning Greaves", "Haste + protection for commander", "recommended"),
    ("Swiftfoot Boots", "Hexproof + haste for commander", "suggested"),
    ("Thought Vessel", "Mana rock + no max hand size", "suggested"),
    ("Solemn Simulacrum", "Body + ramp + cantrip on death", "suggested"),
    ("Skullclamp", "Two-mana draw engine for token decks", "suggested"),
    ("Chromatic Lantern", "Any land taps for any color", "suggested"),
]

# Color-specific Commander staples
_COMMANDER_BY_COLOR: dict[str, list[tuple[str, str, str]]] = {
    "W": [
        ("Swords to Plowshares", "Best single-target removal in white", "essential"),
        ("Path to Exile", "Efficient creature exile", "recommended"),
        ("Generous Gift", "Flexible permanent removal", "recommended"),
        ("Smothering Tithe", "Explosive mana generation", "recommended"),
        ("Teferi's Protection", "Phase out your board against wipes", "recommended"),
        ("Farewell", "Definitive, selective board reset", "suggested"),
    ],
    "U": [
        ("Counterspell", "Clean two-mana counter", "essential"),
        ("Rhystic Study", "Consistent card draw engine", "essential"),
        ("Cyclonic Rift", "Best board wipe in blue", "recommended"),
        ("Swan Song", "Efficient one-mana counter", "recommended"),
        ("Mystic Remora", "One-mana card draw engine", "recommended"),
        ("Brainstorm", "Cheap card selection", "suggested"),
        ("Ponder", "Top-deck sculpting cantrip", "suggested"),
    ],
    "B": [
        ("Demonic Tutor", "Best tutor in the format", "recommended"),
        ("Toxic Deluge", "Efficient board wipe that bypasses indestructible", "recommended"),
        ("Feed the Swarm", "Black enchantment removal", "suggested"),
        ("Vampiric Tutor", "Top-of-library tutor", "recommended"),
        ("Phyrexian Arena", "Steady card advantage", "suggested"),
        ("Animate Dead", "Cheap reanimation staple", "suggested"),
        ("Reanimate", "One-mana graveyard revival", "suggested"),
    ],
    "R": [
        ("Chaos Warp", "Red's best permanent removal", "recommended"),
        ("Blasphemous Act", "Efficient creature wipe", "recommended"),
        ("Dockside Extortionist", "Explosive treasure generator", "recommended"),
        ("Jeska's Will", "Burst mana + card draw", "suggested"),
        ("Vandalblast", "One-sided artifact wipe", "suggested"),
    ],
    "G": [
        ("Beast Within", "Flexible permanent removal", "essential"),
        ("Nature's Claim", "Efficient artifact/enchantment removal", "recommended"),
        ("Kodama's Reach", "Land ramp + fixing", "recommended"),
        ("Cultivate", "Land ramp + fixing", "recommended"),
        ("Heroic Intervention", "Protection against wipes and removal", "recommended"),
        ("Sylvan Library", "Card selection + draw engine", "suggested"),
        ("Farseek", "Two-mana dual-fetching ramp", "suggested"),
    ],
}

# 60-card format staples (less prescriptive — meta-dependent)
_MODERN_STAPLES = [
    ("Lightning Bolt", "Red's premium removal/burn", "suggested"),
    ("Thoughtseize", "Best hand disruption in black", "suggested"),
    ("Fatal Push", "Efficient creature removal in black", "suggested"),
]


def check_staples(deck: Deck) -> StaplesReport:
    """Check a deck for missing format staples."""
    report = StaplesReport()

    if deck.format is None:
        return report

    report.format = deck.format.value

    # Gather color identity
    identity: set[str] = set()
    for entry in deck.entries:
        if entry.card:
            for c in entry.card.color_identity:
                identity.add(c.value)
    # Also check commander
    for cmd in deck.commanders:
        if cmd.card:
            for c in cmd.card.color_identity:
                identity.add(c.value)
    report.color_identity = sorted(identity)

    # Get all card names in deck (lowercase for comparison)
    deck_cards = set()
    for entry in deck.entries:
        if entry.zone != Zone.MAYBEBOARD:
            deck_cards.add(entry.card_name.lower())

    # Build applicable staples list
    applicable: list[tuple[str, str, str]] = []

    if deck.format in (Format.COMMANDER, Format.BRAWL, Format.OATHBREAKER):
        applicable.extend(_COMMANDER_COLORLESS)
        for color in identity:
            applicable.extend(_COMMANDER_BY_COLOR.get(color, []))
    elif deck.format == Format.MODERN:
        applicable.extend(_MODERN_STAPLES)

    # Check each staple
    for name, reason, priority in applicable:
        if name.lower() in deck_cards:
            report.present_staples.append(name)
        else:
            report.missing.append(MissingStaple(name=name, reason=reason, priority=priority))

    total_applicable = len(applicable)
    if total_applicable > 0:
        report.staple_coverage = round(len(report.present_staples) / total_applicable, 2)

    # Sort missing by priority
    priority_order = {"essential": 0, "recommended": 1, "suggested": 2}
    report.missing.sort(key=lambda s: priority_order.get(s.priority, 3))

    return report
