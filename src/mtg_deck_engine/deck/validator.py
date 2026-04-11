"""Deck validation: legality, format rules, structural checks."""

from __future__ import annotations

from mtg_deck_engine.models import (
    Deck,
    DeckEntry,
    Format,
    Legality,
    ValidationIssue,
    Zone,
)

# Format-specific deck size rules
FORMAT_RULES: dict[Format, dict] = {
    Format.COMMANDER: {
        "min_deck": 100,
        "max_deck": 100,
        "max_copies": 1,
        "requires_commander": True,
        "has_sideboard": False,
        "singleton": True,
    },
    Format.BRAWL: {
        "min_deck": 60,
        "max_deck": 60,
        "max_copies": 1,
        "requires_commander": True,
        "has_sideboard": False,
        "singleton": True,
    },
    Format.OATHBREAKER: {
        "min_deck": 60,
        "max_deck": 60,
        "max_copies": 1,
        "requires_commander": True,
        "has_sideboard": False,
        "singleton": True,
    },
    Format.STANDARD: {
        "min_deck": 60,
        "max_deck": None,
        "max_copies": 4,
        "requires_commander": False,
        "has_sideboard": True,
        "max_sideboard": 15,
        "singleton": False,
    },
    Format.PIONEER: {
        "min_deck": 60,
        "max_deck": None,
        "max_copies": 4,
        "requires_commander": False,
        "has_sideboard": True,
        "max_sideboard": 15,
        "singleton": False,
    },
    Format.MODERN: {
        "min_deck": 60,
        "max_deck": None,
        "max_copies": 4,
        "requires_commander": False,
        "has_sideboard": True,
        "max_sideboard": 15,
        "singleton": False,
    },
    Format.LEGACY: {
        "min_deck": 60,
        "max_deck": None,
        "max_copies": 4,
        "requires_commander": False,
        "has_sideboard": True,
        "max_sideboard": 15,
        "singleton": False,
    },
    Format.VINTAGE: {
        "min_deck": 60,
        "max_deck": None,
        "max_copies": 4,
        "requires_commander": False,
        "has_sideboard": True,
        "max_sideboard": 15,
        "singleton": False,
    },
    Format.PAUPER: {
        "min_deck": 60,
        "max_deck": None,
        "max_copies": 4,
        "requires_commander": False,
        "has_sideboard": True,
        "max_sideboard": 15,
        "singleton": False,
    },
}

# Basic land names exempt from copy limits
BASIC_LANDS = {
    "Plains",
    "Island",
    "Swamp",
    "Mountain",
    "Forest",
    "Wastes",
    "Snow-Covered Plains",
    "Snow-Covered Island",
    "Snow-Covered Swamp",
    "Snow-Covered Mountain",
    "Snow-Covered Forest",
}

# Cards that can have any number of copies
UNLIMITED_COPIES = BASIC_LANDS | {
    "Rat Colony",
    "Relentless Rats",
    "Shadowborn Apostle",
    "Persistent Petitioners",
    "Dragon's Approach",
    "Slime Against Humanity",
    "Seven Dwarves",  # actually limited to 7
}


def validate_deck(deck: Deck) -> list[ValidationIssue]:
    """Run all validation checks on a deck. Returns list of issues found."""
    issues: list[ValidationIssue] = []

    if deck.format is None:
        issues.append(ValidationIssue(severity="warning", message="No format specified"))
        # Still do basic structural checks
        _check_unresolved(deck, issues)
        return issues

    rules = FORMAT_RULES.get(deck.format)
    if rules is None:
        issues.append(
            ValidationIssue(
                severity="info",
                message=f"No specific rules defined for {deck.format.value}, running basic checks",
            )
        )
        _check_unresolved(deck, issues)
        return issues

    _check_unresolved(deck, issues)
    _check_deck_size(deck, rules, issues)
    _check_copy_limits(deck, rules, issues)
    _check_legality(deck, issues)
    _check_color_identity(deck, issues)

    if rules.get("requires_commander"):
        _check_commander(deck, issues)

    if rules.get("has_sideboard"):
        _check_sideboard(deck, rules, issues)

    return issues


def _check_unresolved(deck: Deck, issues: list[ValidationIssue]):
    """Flag cards that couldn't be resolved against the database."""
    for entry in deck.entries:
        if entry.card is None and entry.zone != Zone.MAYBEBOARD:
            issues.append(
                ValidationIssue(
                    severity="error",
                    message=f"Card not found in database: '{entry.card_name}'",
                    card_name=entry.card_name,
                )
            )


def _check_deck_size(deck: Deck, rules: dict, issues: list[ValidationIssue]):
    """Check minimum/maximum deck size."""
    # For commander, total includes commander zone
    total = deck.total_cards
    if rules.get("requires_commander"):
        total += sum(e.quantity for e in deck.commanders)

    min_size = rules.get("min_deck")
    max_size = rules.get("max_deck")

    if min_size and total < min_size:
        issues.append(
            ValidationIssue(
                severity="error",
                message=f"Deck has {total} cards, minimum is {min_size}",
            )
        )
    if max_size and total > max_size:
        issues.append(
            ValidationIssue(
                severity="error",
                message=f"Deck has {total} cards, maximum is {max_size}",
            )
        )


def _check_copy_limits(deck: Deck, rules: dict, issues: list[ValidationIssue]):
    """Check per-card copy limits."""
    max_copies = rules.get("max_copies", 4)
    singleton = rules.get("singleton", False)

    # Count copies across mainboard + sideboard + commander
    card_counts: dict[str, int] = {}
    for entry in deck.entries:
        if entry.zone == Zone.MAYBEBOARD:
            continue
        key = entry.card_name.lower()
        card_counts[key] = card_counts.get(key, 0) + entry.quantity

    for name, count in card_counts.items():
        display = name.title()
        if display in UNLIMITED_COPIES or name in {n.lower() for n in UNLIMITED_COPIES}:
            continue
        # Check if the card says "A deck can have any number of cards named..."
        entry = next(
            (e for e in deck.entries if e.card_name.lower() == name and e.card), None
        )
        if entry and entry.card and "a deck can have any number" in entry.card.oracle_text.lower():
            continue

        if singleton and count > 1:
            issues.append(
                ValidationIssue(
                    severity="error",
                    message=f"Singleton format: '{entry.card_name if entry else display}' appears {count} times",
                    card_name=entry.card_name if entry else display,
                )
            )
        elif count > max_copies:
            issues.append(
                ValidationIssue(
                    severity="error",
                    message=f"'{entry.card_name if entry else display}' has {count} copies (max {max_copies})",
                    card_name=entry.card_name if entry else display,
                )
            )


def _check_legality(deck: Deck, issues: list[ValidationIssue]):
    """Check each card's legality in the deck's format."""
    if deck.format is None:
        return
    fmt_key = deck.format.value

    for entry in deck.entries:
        if entry.card is None or entry.zone == Zone.MAYBEBOARD:
            continue
        legality = entry.card.legalities.get(fmt_key)
        if legality == Legality.BANNED:
            issues.append(
                ValidationIssue(
                    severity="error",
                    message=f"'{entry.card_name}' is banned in {deck.format.value}",
                    card_name=entry.card_name,
                )
            )
        elif legality == Legality.NOT_LEGAL:
            issues.append(
                ValidationIssue(
                    severity="error",
                    message=f"'{entry.card_name}' is not legal in {deck.format.value}",
                    card_name=entry.card_name,
                )
            )
        elif legality == Legality.RESTRICTED and entry.quantity > 1:
            issues.append(
                ValidationIssue(
                    severity="error",
                    message=f"'{entry.card_name}' is restricted in {deck.format.value} (max 1 copy)",
                    card_name=entry.card_name,
                )
            )


def _check_color_identity(deck: Deck, issues: list[ValidationIssue]):
    """For commander formats, check that all cards match commander color identity."""
    if not deck.commanders:
        return

    # Build commander color identity
    commander_identity: set[str] = set()
    for cmd in deck.commanders:
        if cmd.card:
            commander_identity.update(c.value for c in cmd.card.color_identity)

    for entry in deck.entries:
        if entry.card is None or entry.zone in (Zone.COMMANDER, Zone.MAYBEBOARD):
            continue
        card_identity = {c.value for c in entry.card.color_identity}
        if not card_identity.issubset(commander_identity):
            extra = card_identity - commander_identity
            issues.append(
                ValidationIssue(
                    severity="error",
                    message=f"'{entry.card_name}' has colors {extra} outside commander identity {commander_identity}",
                    card_name=entry.card_name,
                )
            )


def _check_commander(deck: Deck, issues: list[ValidationIssue]):
    """Check commander-specific rules."""
    if not deck.commanders:
        issues.append(
            ValidationIssue(
                severity="warning",
                message="No commander designated",
            )
        )
        return

    for cmd in deck.commanders:
        if cmd.card is None:
            continue
        type_line = cmd.card.type_line.lower()
        oracle = cmd.card.oracle_text.lower()
        is_legendary = "legendary" in type_line
        is_creature = "creature" in type_line
        can_be_commander = "can be your commander" in oracle

        if not (is_legendary and is_creature) and not can_be_commander:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    message=f"'{cmd.card_name}' may not be a legal commander (not legendary creature)",
                    card_name=cmd.card_name,
                )
            )


def _check_sideboard(deck: Deck, rules: dict, issues: list[ValidationIssue]):
    """Check sideboard size limits."""
    max_sb = rules.get("max_sideboard", 15)
    sb_total = sum(e.quantity for e in deck.sideboard)
    if sb_total > max_sb:
        issues.append(
            ValidationIssue(
                severity="error",
                message=f"Sideboard has {sb_total} cards, maximum is {max_sb}",
            )
        )
