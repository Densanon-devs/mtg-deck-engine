"""Import decklists from Moxfield and Archidekt URLs.

Fetches public decklists via their APIs and converts to our format.
"""

from __future__ import annotations

import asyncio
import re

import httpx

from densa_deck.models import DeckEntry, Zone

# URL patterns
_MOXFIELD_PATTERN = re.compile(r"moxfield\.com/decks/([a-zA-Z0-9_-]+)")
_ARCHIDEKT_PATTERN = re.compile(r"archidekt\.com/(?:decks|api/decks)/(\d+)")

MOXFIELD_API = "https://api2.moxfield.com/v3/decks/all"  # Moxfield v3 public endpoint
ARCHIDEKT_API = "https://archidekt.com/api/decks"

# Retry policy for deck host APIs. Moxfield in particular rate-limits bulk
# scripted imports (HTTP 429 with Retry-After); a single retry chain with
# exponential backoff is cheap and lets a queued import keep progressing
# rather than dying on the first throttle.
_MAX_RETRIES = 3
_INITIAL_BACKOFF_SECONDS = 1.0


async def _get_with_backoff(client: httpx.AsyncClient, url: str, headers: dict) -> httpx.Response:
    """GET `url`, retrying on 429 (and transient 5xx) with exponential backoff.

    Honors the server's Retry-After header when present, otherwise doubles the
    wait from _INITIAL_BACKOFF_SECONDS each attempt. Network / transport errors
    get the same treatment. The final attempt re-raises whatever httpx surfaced.
    """
    delay = _INITIAL_BACKOFF_SECONDS
    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code in (429, 502, 503, 504):
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
                if attempt == _MAX_RETRIES - 1:
                    resp.raise_for_status()
                await asyncio.sleep(wait)
                delay *= 2
                continue
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429 and attempt < _MAX_RETRIES - 1:
                retry_after = e.response.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
                await asyncio.sleep(wait)
                delay *= 2
                last_error = e
                continue
            raise
        except (httpx.TransportError, httpx.TimeoutException) as e:
            last_error = e
            if attempt == _MAX_RETRIES - 1:
                raise
            await asyncio.sleep(delay)
            delay *= 2
    # Unreachable under normal control flow — guard so callers never see None.
    if last_error:
        raise last_error
    raise RuntimeError(f"Failed to fetch {url} after {_MAX_RETRIES} attempts")


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
    """Fetch from Moxfield public API.

    As of April 2026, Moxfield sits behind Cloudflare bot detection that
    blocks direct API requests from non-browser clients regardless of the
    User-Agent we send — we translate a 403 to a user-actionable error
    that directs people to Moxfield's "Export → Text" feature and the
    paste-a-decklist box in the app, which always works.
    """
    url = f"{MOXFIELD_API}/{deck_id}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await _get_with_backoff(client, url, headers={
                "Accept": "application/json",
                "User-Agent": "DensaDeck/0.1 (+https://toolkit.densanon.com/densa-deck.html)",
            })
            data = resp.json()
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code == 403:
            raise RuntimeError(
                "Moxfield is blocking automated imports right now (their "
                "Cloudflare gate rejects non-browser requests). To load "
                "this deck into Densa Deck, open it on moxfield.com, click "
                "Export \u2192 Text, copy the list, and paste it into the "
                "decklist box above instead of using URL import. Archidekt "
                "URLs also work as a direct import."
            ) from e
        raise

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
        resp = await _get_with_backoff(client, url, headers={"Accept": "application/json"})
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
