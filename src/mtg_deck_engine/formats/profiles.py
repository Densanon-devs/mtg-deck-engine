"""Format profiles: tuned analysis targets, archetype detection, and format-specific rules.

Each format defines ideal ranges for lands, ramp, draw, interaction, curve,
and provides archetype detection based on card tag density and commander identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from mtg_deck_engine.models import CardTag, Deck, DeckEntry, Format, Zone


class DeckArchetype(str, Enum):
    """Detected deck archetype based on card composition."""

    AGGRO = "aggro"
    MIDRANGE = "midrange"
    CONTROL = "control"
    COMBO = "combo"
    ARISTOCRATS = "aristocrats"
    TOKENS = "tokens"
    VOLTRON = "voltron"
    SPELLSLINGER = "spellslinger"
    STAX = "stax"
    REANIMATOR = "reanimator"
    RAMP = "ramp"
    MILL = "mill"
    BURN = "burn"
    TEMPO = "tempo"
    UNKNOWN = "unknown"


@dataclass
class FormatTargets:
    """Ideal ranges for a format."""

    lands: tuple[int, int] = (35, 38)
    ramp: tuple[int, int] = (10, 15)
    draw: tuple[int, int] = (8, 12)
    removal: tuple[int, int] = (8, 12)
    wipes: tuple[int, int] = (2, 4)
    average_cmc: tuple[float, float] = (2.5, 3.5)
    threats: tuple[int, int] = (8, 15)
    min_deck_size: int = 100
    max_copies: int = 1
    singleton: bool = True


@dataclass
class FormatProfile:
    """Complete format profile with targets, archetype hints, and recommendations."""

    format: Format
    display_name: str
    targets: FormatTargets
    archetype_hints: dict[DeckArchetype, list[str]] = field(default_factory=dict)
    specific_recommendations: list[str] = field(default_factory=list)


# =============================================================================
# Format profiles
# =============================================================================

COMMANDER = FormatProfile(
    format=Format.COMMANDER,
    display_name="Commander / EDH",
    targets=FormatTargets(
        lands=(35, 38), ramp=(10, 15), draw=(8, 12), removal=(8, 12),
        wipes=(2, 4), average_cmc=(2.5, 3.5), threats=(8, 15),
        min_deck_size=100, max_copies=1, singleton=True,
    ),
    archetype_hints={
        DeckArchetype.VOLTRON: ["equipment", "aura", "buff"],
        DeckArchetype.ARISTOCRATS: ["sacrifice_outlet", "aristocrat_payoff", "recursion"],
        DeckArchetype.TOKENS: ["token_maker"],
        DeckArchetype.SPELLSLINGER: ["cantrip", "counterspell"],
        DeckArchetype.STAX: ["stax"],
        DeckArchetype.REANIMATOR: ["recursion"],
        DeckArchetype.RAMP: ["ramp", "mana_rock", "mana_dork"],
    },
)

MODERN = FormatProfile(
    format=Format.MODERN,
    display_name="Modern",
    targets=FormatTargets(
        lands=(20, 24), ramp=(0, 4), draw=(4, 8), removal=(6, 10),
        wipes=(0, 3), average_cmc=(1.5, 2.5), threats=(8, 16),
        min_deck_size=60, max_copies=4, singleton=False,
    ),
    archetype_hints={
        DeckArchetype.AGGRO: ["threat", "finisher"],
        DeckArchetype.BURN: ["targeted_removal"],
        DeckArchetype.CONTROL: ["counterspell", "board_wipe", "card_draw"],
        DeckArchetype.TEMPO: ["counterspell", "cantrip", "threat"],
        DeckArchetype.COMBO: ["engine"],
    },
)

STANDARD = FormatProfile(
    format=Format.STANDARD,
    display_name="Standard",
    targets=FormatTargets(
        lands=(22, 26), ramp=(0, 4), draw=(4, 8), removal=(4, 8),
        wipes=(0, 3), average_cmc=(2.0, 3.0), threats=(8, 16),
        min_deck_size=60, max_copies=4, singleton=False,
    ),
    archetype_hints={
        DeckArchetype.AGGRO: ["threat"],
        DeckArchetype.MIDRANGE: ["threat", "targeted_removal", "card_draw"],
        DeckArchetype.CONTROL: ["counterspell", "board_wipe"],
    },
)

PIONEER = FormatProfile(
    format=Format.PIONEER,
    display_name="Pioneer",
    targets=FormatTargets(
        lands=(22, 26), ramp=(0, 4), draw=(4, 8), removal=(6, 10),
        wipes=(0, 3), average_cmc=(2.0, 3.0), threats=(8, 16),
        min_deck_size=60, max_copies=4, singleton=False,
    ),
    archetype_hints={
        DeckArchetype.AGGRO: ["threat"],
        DeckArchetype.MIDRANGE: ["threat", "targeted_removal"],
        DeckArchetype.CONTROL: ["counterspell", "board_wipe"],
        DeckArchetype.COMBO: ["engine"],
    },
)

LEGACY = FormatProfile(
    format=Format.LEGACY,
    display_name="Legacy",
    targets=FormatTargets(
        lands=(18, 22), ramp=(0, 4), draw=(4, 10), removal=(6, 12),
        wipes=(0, 2), average_cmc=(1.5, 2.5), threats=(6, 14),
        min_deck_size=60, max_copies=4, singleton=False,
    ),
    archetype_hints={
        DeckArchetype.TEMPO: ["counterspell", "cantrip", "threat"],
        DeckArchetype.COMBO: ["engine", "tutor"],
        DeckArchetype.CONTROL: ["counterspell", "board_wipe"],
    },
)

PAUPER = FormatProfile(
    format=Format.PAUPER,
    display_name="Pauper",
    targets=FormatTargets(
        lands=(20, 24), ramp=(0, 4), draw=(4, 8), removal=(6, 10),
        wipes=(0, 2), average_cmc=(1.5, 2.5), threats=(8, 16),
        min_deck_size=60, max_copies=4, singleton=False,
    ),
    archetype_hints={
        DeckArchetype.AGGRO: ["threat"],
        DeckArchetype.CONTROL: ["counterspell", "card_draw"],
        DeckArchetype.BURN: ["targeted_removal"],
    },
)

BRAWL = FormatProfile(
    format=Format.BRAWL,
    display_name="Brawl",
    targets=FormatTargets(
        lands=(22, 25), ramp=(6, 10), draw=(6, 10), removal=(5, 8),
        wipes=(1, 3), average_cmc=(2.5, 3.5), threats=(6, 12),
        min_deck_size=60, max_copies=1, singleton=True,
    ),
    archetype_hints=COMMANDER.archetype_hints,
)

FORMAT_PROFILES: dict[Format, FormatProfile] = {
    Format.COMMANDER: COMMANDER,
    Format.MODERN: MODERN,
    Format.STANDARD: STANDARD,
    Format.PIONEER: PIONEER,
    Format.LEGACY: LEGACY,
    Format.PAUPER: PAUPER,
    Format.BRAWL: BRAWL,
}


def get_format_profile(fmt: Format | None) -> FormatProfile | None:
    """Get the profile for a format, or None if unsupported."""
    if fmt is None:
        return None
    return FORMAT_PROFILES.get(fmt)


def detect_archetype(deck: Deck) -> DeckArchetype:
    """Detect the deck's archetype based on tag density."""
    profile = get_format_profile(deck.format)
    if profile is None:
        return DeckArchetype.UNKNOWN

    active = [e for e in deck.entries if e.zone not in (Zone.MAYBEBOARD, Zone.SIDEBOARD) and e.card]
    total = sum(e.quantity for e in active)
    if total == 0:
        return DeckArchetype.UNKNOWN

    # Count tag densities
    tag_counts: dict[str, int] = {}
    for entry in active:
        if entry.card and entry.card.tags:
            for tag in entry.card.tags:
                tag_counts[tag.value] = tag_counts.get(tag.value, 0) + entry.quantity

    # Score each archetype by how many hint tags are present
    best_arch = DeckArchetype.UNKNOWN
    best_score = 0.0

    for arch, hint_tags in profile.archetype_hints.items():
        score = 0.0
        for tag_val in hint_tags:
            count = tag_counts.get(tag_val, 0)
            # Normalize by deck size for fair comparison
            density = count / total
            score += density * 100
        if score > best_score:
            best_score = score
            best_arch = arch

    # Need a minimum threshold to call it
    if best_score < 3.0:
        # Fall back to generic detection by curve
        avg_cmc = sum(
            e.card.cmc * e.quantity for e in active if e.card and not e.card.is_land
        ) / max(1, sum(e.quantity for e in active if e.card and not e.card.is_land))

        creature_count = sum(e.quantity for e in active if e.card and e.card.is_creature)
        creature_density = creature_count / total

        if avg_cmc <= 2.0 and creature_density > 0.4:
            return DeckArchetype.AGGRO
        elif avg_cmc <= 3.0:
            return DeckArchetype.MIDRANGE
        elif avg_cmc > 3.5:
            return DeckArchetype.CONTROL
        return DeckArchetype.MIDRANGE

    return best_arch


def format_recommendations(deck: Deck, archetype: DeckArchetype) -> list[str]:
    """Generate format-specific recommendations based on detected archetype."""
    recs: list[str] = []
    profile = get_format_profile(deck.format)
    if profile is None:
        return recs

    active = [e for e in deck.entries if e.zone not in (Zone.MAYBEBOARD, Zone.SIDEBOARD) and e.card]
    total = sum(e.quantity for e in active)
    if total == 0:
        return recs

    tag_counts: dict[str, int] = {}
    for entry in active:
        if entry.card and entry.card.tags:
            for tag in entry.card.tags:
                tag_counts[tag.value] = tag_counts.get(tag.value, 0) + entry.quantity

    # Archetype-specific advice
    if archetype == DeckArchetype.AGGRO:
        if tag_counts.get("threat", 0) < profile.targets.threats[0]:
            recs.append("Aggro decks need more threats — prioritize efficient creatures.")
        avg_cmc = sum(
            e.card.cmc * e.quantity for e in active if e.card and not e.card.is_land
        ) / max(1, sum(e.quantity for e in active if e.card and not e.card.is_land))
        if avg_cmc > 2.5:
            recs.append("Aggro curve is too high — cut expensive spells for 1-2 drops.")

    elif archetype == DeckArchetype.CONTROL:
        interaction = tag_counts.get("targeted_removal", 0) + tag_counts.get("counterspell", 0) + tag_counts.get("board_wipe", 0)
        if interaction < profile.targets.removal[0]:
            recs.append("Control needs more interaction — add removal, counters, or wipes.")
        if tag_counts.get("card_draw", 0) < profile.targets.draw[0]:
            recs.append("Control relies on card advantage — add more draw engines.")

    elif archetype == DeckArchetype.VOLTRON:
        equip = tag_counts.get("equipment", 0) + tag_counts.get("aura", 0)
        if equip < 10:
            recs.append("Voltron wants 10+ equipment/auras to reliably suit up.")
        if tag_counts.get("protection", 0) < 5:
            recs.append("Voltron commander is a removal magnet — add more protection pieces.")

    elif archetype == DeckArchetype.ARISTOCRATS:
        sac = tag_counts.get("sacrifice_outlet", 0)
        payoff = tag_counts.get("aristocrat_payoff", 0)
        if sac < 5:
            recs.append("Aristocrats needs more sacrifice outlets (5+ recommended).")
        if payoff < 5:
            recs.append("Add more death triggers / aristocrat payoffs for consistent drain.")

    elif archetype == DeckArchetype.TOKENS:
        makers = tag_counts.get("token_maker", 0)
        if makers < 12:
            recs.append("Token strategies want 12+ token producers for consistency.")

    elif archetype == DeckArchetype.SPELLSLINGER:
        instants_sorceries = sum(
            e.quantity for e in active if e.card and (e.card.is_instant or e.card.is_sorcery)
        )
        if instants_sorceries < total * 0.4:
            recs.append("Spellslinger decks want 40%+ instants/sorceries to fuel payoffs.")

    elif archetype == DeckArchetype.COMBO:
        tutors = tag_counts.get("tutor", 0)
        draw = tag_counts.get("card_draw", 0)
        if tutors < 3 and draw < 8:
            recs.append("Combo decks need card selection — add tutors or draw to find pieces.")

    return recs
