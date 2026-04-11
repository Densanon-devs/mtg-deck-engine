"""Deck import and parsing from multiple formats."""

from __future__ import annotations

import re

from mtg_deck_engine.models import DeckEntry, Zone


# Regex patterns for decklist parsing
# Matches: "1 Lightning Bolt", "1x Lightning Bolt", "Lightning Bolt"
_QTY_NAME = re.compile(r"^\s*(\d+)\s*[xX]?\s+(.+?)\s*$")
_NAME_ONLY = re.compile(r"^\s*([A-Z].+?)\s*$")

# Section headers
_SECTION_PATTERNS = {
    Zone.COMMANDER: re.compile(r"^(commander|cmdr)\s*:?\s*$", re.IGNORECASE),
    Zone.COMPANION: re.compile(r"^companion\s*:?\s*$", re.IGNORECASE),
    Zone.SIDEBOARD: re.compile(r"^(sideboard|sb|side)\s*:?\s*$", re.IGNORECASE),
    Zone.MAYBEBOARD: re.compile(r"^(maybeboard|maybe|considering)\s*:?\s*$", re.IGNORECASE),
    Zone.MAINBOARD: re.compile(r"^(mainboard|main|deck|mainlist)\s*:?\s*$", re.IGNORECASE),
}


def parse_decklist(text: str) -> list[DeckEntry]:
    """Parse a plain-text decklist into DeckEntry objects.

    Supports formats:
    - Plain text: "4 Lightning Bolt"
    - With quantity marker: "4x Lightning Bolt"
    - Moxfield/Archidekt exports with section headers
    - Card names alone (quantity defaults to 1)
    - Set codes in parens: "4 Lightning Bolt (M21) 199" (ignored)
    - Category tags: "1 Sol Ring #ramp #mana"
    """
    entries: list[DeckEntry] = []
    current_zone = Zone.MAINBOARD
    seen_blank = False

    for raw_line in text.strip().splitlines():
        line = raw_line.strip()

        # Skip empty lines (but track for sideboard heuristic)
        if not line:
            seen_blank = True
            continue

        # Skip comment lines
        if line.startswith("//") or line.startswith("#"):
            continue

        # Check for section headers
        matched_section = False
        for zone, pattern in _SECTION_PATTERNS.items():
            if pattern.match(line):
                current_zone = zone
                matched_section = True
                break
        if matched_section:
            continue

        # Moxfield-style "SIDEBOARD:" prefix inline
        zone_override = current_zone
        for zone, pattern in _SECTION_PATTERNS.items():
            prefix_match = re.match(rf"^{pattern.pattern.strip('^$')}\s*", line, re.IGNORECASE)
            if prefix_match:
                zone_override = zone
                line = line[prefix_match.end() :].strip()
                break

        # Extract custom tags (e.g. #ramp #draw)
        custom_tags: list[str] = []
        tag_matches = re.findall(r"#(\w+)", line)
        if tag_matches:
            custom_tags = tag_matches
            line = re.sub(r"\s*#\w+", "", line).strip()

        # Strip set code and collector number: "(M21) 199", "(NEO)", etc.
        line = re.sub(r"\s*\([A-Z0-9]+\)\s*\d*\s*$", "", line).strip()
        # Strip trailing star for foil indicators
        line = re.sub(r"\s*\*F\*\s*$", "", line).strip()

        # Try quantity + name
        m = _QTY_NAME.match(line)
        if m:
            qty = int(m.group(1))
            name = m.group(2).strip()
        else:
            # Try name only
            m = _NAME_ONLY.match(line)
            if m:
                qty = 1
                name = m.group(1).strip()
            else:
                continue  # Unparseable line

        if not name:
            continue

        # Heuristic: in formats without section headers, a blank line
        # before remaining entries often means sideboard
        if seen_blank and current_zone == Zone.MAINBOARD and zone_override == Zone.MAINBOARD:
            # Only apply blank-line sideboard heuristic if no explicit sections used
            if not any(
                any(p.search(l) for p in _SECTION_PATTERNS.values())
                for l in text.strip().splitlines()
                if l.strip()
            ):
                zone_override = Zone.SIDEBOARD

        entries.append(
            DeckEntry(
                card_name=name,
                quantity=qty,
                zone=zone_override,
                custom_tags=custom_tags,
            )
        )

    return entries


def parse_csv(text: str) -> list[DeckEntry]:
    """Parse CSV-format decklist (quantity,name,zone)."""
    entries: list[DeckEntry] = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("quantity") or line.startswith("Quantity"):
            continue
        parts = [p.strip().strip('"') for p in line.split(",")]
        if len(parts) >= 2:
            try:
                qty = int(parts[0])
            except ValueError:
                continue
            name = parts[1]
            zone = Zone.MAINBOARD
            if len(parts) >= 3:
                zone_str = parts[2].lower()
                for z in Zone:
                    if z.value == zone_str:
                        zone = z
                        break
            entries.append(DeckEntry(card_name=name, quantity=qty, zone=zone))
    return entries


def detect_format(text: str) -> str:
    """Detect if input is plain text or CSV."""
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    csv_score = sum(1 for l in lines[:10] if "," in l)
    if csv_score > len(lines[:10]) * 0.5:
        return "csv"
    return "text"


def parse_auto(text: str) -> list[DeckEntry]:
    """Auto-detect format and parse."""
    fmt = detect_format(text)
    if fmt == "csv":
        return parse_csv(text)
    return parse_decklist(text)
