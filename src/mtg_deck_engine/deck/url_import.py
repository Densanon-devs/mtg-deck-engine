"""Import decklists from Moxfield and Archidekt URLs.

Fetches public decklists via their APIs and converts to our format.
"""

from __future__ import annotations

import re

import httpx

from mtg_deck_engine.models import DeckEntry, Zone

# URL patterns
_MOXFIELD_PATTERN = re.compile(r"moxfield\.com/decks/([a-zA-Z0-9_-]+)")
_ARCHIDEKT_PATTERN = re.compile(r"archidekt\.com/(?:decks|api/decks)/(\d+)")

MOXFIELD_API = "https://api2.moxfield.com/v3/decks/all"
ARCHIDEKT_API = "https://archidekt.com/api/decks"


def detect_url(text: str) -> tuple[str, str] | None:
    """Detect if input is a deck URL. Returns (service, deck_id) or None."""
    m = _MOXFIELD_PATTERN.search(text)
    if m:
        return ("moxfield", m.group(1))

    m = _ARCHIDEKT_PATTERN.search(text)
    if m:
        return ("archidekt", m.group(1))

    return None


async def fetch_from_url(url: str) -> list[DeckEntry]:
    """Fetch a decklist from a Moxfield or Archidekt URL."""
    detected = detect_url(url)
    if detected is None:
        raise ValueError(f"Unsupported deck URL: {url}")

    service, deck_id = detected

    if service == "moxfield":
        return await _fetch_moxfield(deck_id)
    elif service == "archidekt":
        return await _fetch_archidekt(deck_id)
    else:
        raise ValueError(f"Unknown service: {service}")


async def _fetch_moxfield(deck_id: str) -> list[DeckEntry]:
    """Fetch from Moxfield public API."""
    url = f"{MOXFIELD_API}/{deck_id}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers={
            "Accept": "application/json",
            "User-Agent": "MTGDeckEngine/1.0",
        })
        resp.raise_for_status()
        data = resp.json()

    entries: list[DeckEntry] = []

    # Moxfield structure: boards -> mainboard/sideboard/commanders/companions
    boards = data.get("boards", {})

    zone_map = {
        "mainboard": Zone.MAINBOARD,
        "sideboard": Zone.SIDEBOARD,
        "commanders": Zone.COMMANDER,
        "companions": Zone.COMPANION,
    }

    for board_name, zone in zone_map.items():
        board = boards.get(board_name, {})
        cards = board.get("cards", {})
        for card_id, card_data in cards.items():
            name = card_data.get("card", {}).get("name", "")
            qty = card_data.get("quantity", 1)
            if name:
                entries.append(DeckEntry(card_name=name, quantity=qty, zone=zone))

    return entries


async def _fetch_archidekt(deck_id: str) -> list[DeckEntry]:
    """Fetch from Archidekt public API."""
    url = f"{ARCHIDEKT_API}/{deck_id}/"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers={"Accept": "application/json"})
        resp.raise_for_status()
        data = resp.json()

    entries: list[DeckEntry] = []

    for card_entry in data.get("cards", []):
        name = card_entry.get("card", {}).get("oracleCard", {}).get("name", "")
        qty = card_entry.get("quantity", 1)
        categories = card_entry.get("categories", [])

        zone = Zone.MAINBOARD
        if "Commander" in categories:
            zone = Zone.COMMANDER
        elif "Sideboard" in categories:
            zone = Zone.SIDEBOARD
        elif "Companion" in categories:
            zone = Zone.COMPANION
        elif "Maybeboard" in categories:
            zone = Zone.MAYBEBOARD

        if name:
            entries.append(DeckEntry(card_name=name, quantity=qty, zone=zone))

    return entries
