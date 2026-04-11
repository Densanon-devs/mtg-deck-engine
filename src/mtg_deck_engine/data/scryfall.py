"""Scryfall bulk data ingestion pipeline."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

from mtg_deck_engine.data.database import CardDatabase
from mtg_deck_engine.models import Card, CardFace, CardLayout, Color, Legality

console = Console()

SCRYFALL_BULK_API = "https://api.scryfall.com/bulk-data"
BULK_TYPE = "oracle_cards"  # One entry per unique card (no reprints)


async def fetch_bulk_data_url() -> str:
    """Get the download URL for the oracle cards bulk file."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(SCRYFALL_BULK_API, headers={"Accept": "application/json"})
        resp.raise_for_status()
        data = resp.json()
        for item in data["data"]:
            if item["type"] == BULK_TYPE:
                return item["download_uri"]
    raise RuntimeError(f"Could not find bulk data type '{BULK_TYPE}' in Scryfall API response")


async def download_bulk_file(url: str, dest: Path) -> Path:
    """Stream-download the bulk JSON file with atomic write."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=300) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                ) as progress:
                    task = progress.add_task("Downloading Scryfall data...", total=total or None)
                    with open(tmp, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            f.write(chunk)
                            progress.update(task, advance=len(chunk))
        # Atomic rename on success
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return dest


def parse_scryfall_card(raw: dict) -> Card | None:
    """Convert a raw Scryfall JSON object into our Card model."""
    # Skip tokens, emblems, art series, etc.
    layout_str = raw.get("layout", "normal")
    try:
        layout = CardLayout(layout_str)
    except ValueError:
        return None

    if layout in (
        CardLayout.TOKEN,
        CardLayout.DOUBLE_FACED_TOKEN,
        CardLayout.EMBLEM,
        CardLayout.ART_SERIES,
        CardLayout.PLANAR,
        CardLayout.SCHEME,
        CardLayout.VANGUARD,
    ):
        return None

    # Parse faces
    faces: list[CardFace] = []
    if "card_faces" in raw:
        for face_raw in raw["card_faces"]:
            faces.append(
                CardFace(
                    name=face_raw.get("name", ""),
                    mana_cost=face_raw.get("mana_cost", ""),
                    cmc=raw.get("cmc", 0.0),
                    type_line=face_raw.get("type_line", ""),
                    oracle_text=face_raw.get("oracle_text", ""),
                    power=face_raw.get("power"),
                    toughness=face_raw.get("toughness"),
                    loyalty=face_raw.get("loyalty"),
                    colors=[Color(c) for c in face_raw.get("colors", [])],
                    color_indicator=[Color(c) for c in face_raw.get("color_indicator", [])],
                    produced_mana=face_raw.get("produced_mana", []),
                )
            )

    # Parse legalities
    legalities = {}
    for fmt, status in raw.get("legalities", {}).items():
        try:
            legalities[fmt] = Legality(status)
        except ValueError:
            pass

    type_line = raw.get("type_line", "")
    tl_lower = type_line.lower()

    return Card(
        scryfall_id=raw["id"],
        oracle_id=raw.get("oracle_id", raw["id"]),
        name=raw.get("name", "Unknown"),
        layout=layout,
        cmc=raw.get("cmc", 0.0),
        mana_cost=raw.get("mana_cost", ""),
        type_line=type_line,
        oracle_text=raw.get("oracle_text", ""),
        colors=[Color(c) for c in raw.get("colors", [])],
        color_identity=[Color(c) for c in raw.get("color_identity", [])],
        produced_mana=raw.get("produced_mana", []),
        keywords=raw.get("keywords", []),
        legalities=legalities,
        faces=faces,
        power=raw.get("power"),
        toughness=raw.get("toughness"),
        loyalty=raw.get("loyalty"),
        rarity=raw.get("rarity", ""),
        set_code=raw.get("set", ""),
        is_land="land" in tl_lower,
        is_creature="creature" in tl_lower,
        is_instant="instant" in tl_lower,
        is_sorcery="sorcery" in tl_lower,
        is_artifact="artifact" in tl_lower,
        is_enchantment="enchantment" in tl_lower,
        is_planeswalker="planeswalker" in tl_lower,
        is_battle="battle" in tl_lower,
    )


def load_bulk_file(path: Path) -> list[Card]:
    """Parse the downloaded JSON bulk file into Card objects."""
    cards: list[Card] = []
    console.print(f"[cyan]Parsing {path.name}...[/cyan]")
    with open(path, "r", encoding="utf-8") as f:
        raw_cards = json.load(f)
    total = len(raw_cards)
    skipped = 0
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
    ) as progress:
        task = progress.add_task("Parsing cards...", total=total)
        for raw in raw_cards:
            card = parse_scryfall_card(raw)
            if card:
                cards.append(card)
            else:
                skipped += 1
            progress.update(task, advance=1)
    console.print(f"[green]Parsed {len(cards)} cards[/green] ({skipped} skipped)")
    return cards


async def ingest(db: CardDatabase | None = None, force: bool = False):
    """Full ingestion pipeline: download bulk data, parse, store."""
    if db is None:
        db = CardDatabase()

    existing = db.card_count()
    if existing > 0 and not force:
        console.print(
            f"[yellow]Database already has {existing} cards. Use --force to re-download.[/yellow]"
        )
        return

    console.print("[bold cyan]Starting Scryfall data ingestion...[/bold cyan]")

    cache_dir = db.db_path.parent / "bulk"
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / "oracle_cards.json"

    try:
        # Get download URL
        url = await fetch_bulk_data_url()
        console.print(f"[dim]Bulk data URL: {url}[/dim]")

        # Download
        await download_bulk_file(url, dest)

        # Parse
        cards = load_bulk_file(dest)

        # Store
        console.print("[cyan]Storing cards in database...[/cyan]")
        db.upsert_cards(cards)
        db.set_metadata("last_ingest", str(len(cards)))
        console.print(f"[bold green]Done! {len(cards)} cards stored.[/bold green]")
    except httpx.HTTPError as e:
        console.print(f"[bold red]Network error during ingestion: {e}[/bold red]")
        console.print("[yellow]Check your internet connection or try again later.[/yellow]")
        raise SystemExit(1)
    except (json.JSONDecodeError, KeyError) as e:
        console.print(f"[bold red]Failed to parse card data: {e}[/bold red]")
        raise SystemExit(1)
    finally:
        # Always clean up bulk file
        dest.unlink(missing_ok=True)
