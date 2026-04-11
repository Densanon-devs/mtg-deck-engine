"""CLI entry point for mtg-deck-engine."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from mtg_deck_engine.analysis.static import analyze_deck
from mtg_deck_engine.data.database import CardDatabase
from mtg_deck_engine.data.scryfall import ingest
from mtg_deck_engine.deck.parser import parse_auto
from mtg_deck_engine.deck.resolver import resolve_deck
from mtg_deck_engine.deck.validator import validate_deck
from mtg_deck_engine.legal import ATTRIBUTION, DISCLAIMER
from mtg_deck_engine.models import AnalysisResult, Format

console = Console()

COLOR_SYMBOLS = {"W": "☀", "U": "💧", "B": "💀", "R": "🔥", "G": "🌿"}
COLOR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}


def main():
    parser = argparse.ArgumentParser(
        prog="mtg-engine",
        description="MTG Deck Testing Engine — analyze, test, and improve your decks",
        epilog=DISCLAIMER,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ingest command
    ingest_parser = subparsers.add_parser("ingest", help="Download and store Scryfall card data")
    ingest_parser.add_argument("--force", action="store_true", help="Force re-download")
    ingest_parser.add_argument("--db", type=str, help="Custom database path")

    # analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze a decklist")
    analyze_parser.add_argument("file", type=str, help="Path to decklist file")
    analyze_parser.add_argument("--name", type=str, default=None, help="Deck name")
    analyze_parser.add_argument(
        "--format",
        type=str,
        default=None,
        choices=[f.value for f in Format],
        help="Deck format",
    )
    analyze_parser.add_argument("--db", type=str, help="Custom database path")

    # search command
    search_parser = subparsers.add_parser("search", help="Search for cards")
    search_parser.add_argument("query", type=str, help="Search query")
    search_parser.add_argument("--db", type=str, help="Custom database path")

    # info command
    info_parser = subparsers.add_parser("info", help="Show database info")
    info_parser.add_argument("--db", type=str, help="Custom database path")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    if args.command == "ingest":
        cmd_ingest(args)
    elif args.command == "analyze":
        cmd_analyze(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "info":
        cmd_info(args)


def _get_db(args) -> CardDatabase:
    if hasattr(args, "db") and args.db:
        return CardDatabase(Path(args.db))
    return CardDatabase()


def cmd_ingest(args):
    db = _get_db(args)
    try:
        asyncio.run(ingest(db=db, force=args.force))
    finally:
        db.close()


def cmd_analyze(args):
    db = _get_db(args)
    try:
        # Check database has cards
        if db.card_count() == 0:
            console.print(
                "[red]No cards in database. Run 'mtg-engine ingest' first.[/red]"
            )
            sys.exit(1)

        # Read decklist
        file_path = Path(args.file)
        if not file_path.exists():
            console.print(f"[red]File not found: {file_path}[/red]")
            sys.exit(1)

        text = file_path.read_text(encoding="utf-8")
        deck_name = args.name or file_path.stem

        # Parse
        entries = parse_auto(text)
        if not entries:
            console.print("[red]No cards found in decklist.[/red]")
            sys.exit(1)
        console.print(f"[cyan]Parsed {len(entries)} entries from {file_path.name}[/cyan]")

        # Resolve
        fmt = Format(args.format) if args.format else None
        deck = resolve_deck(entries, db, name=deck_name, format=fmt)

        # Validate
        issues = validate_deck(deck)

        # Analyze
        result = analyze_deck(deck)
        result.issues.extend(issues)

        # Display
        _render_dashboard(result, deck)

    finally:
        db.close()


def cmd_search(args):
    db = _get_db(args)
    try:
        if db.card_count() == 0:
            console.print("[red]No cards in database. Run 'mtg-engine ingest' first.[/red]")
            sys.exit(1)

        cards = db.search(args.query, limit=20)
        if not cards:
            console.print(f"[yellow]No cards found matching '{args.query}'[/yellow]")
            return

        table = Table(title=f"Search: {args.query}")
        table.add_column("Name", style="bold")
        table.add_column("Type")
        table.add_column("Cost")
        table.add_column("CMC", justify="right")

        for card in cards:
            table.add_row(card.name, card.type_line, card.mana_cost, str(card.cmc))

        console.print(table)
    finally:
        db.close()


def cmd_info(args):
    db = _get_db(args)
    try:
        count = db.card_count()
        last_ingest = db.get_metadata("last_ingest")
        console.print(Panel(
            f"[bold]Cards in database:[/bold] {count}\n"
            f"[bold]Database path:[/bold] {db.db_path}\n"
            f"[bold]Last ingest:[/bold] {last_ingest or 'Never'}",
            title="MTG Deck Engine — Database Info",
        ))
    finally:
        db.close()


# =============================================================================
# Dashboard rendering
# =============================================================================


def _render_dashboard(result: AnalysisResult, deck):
    """Render the full static analysis dashboard."""
    console.print()
    console.print(
        Panel(
            f"[bold]{result.deck_name}[/bold]"
            + (f"  |  Format: {result.format}" if result.format else "")
            + f"  |  {result.total_cards} cards",
            title="[bold cyan]MTG Deck Engine — Static Analysis[/bold cyan]",
            border_style="cyan",
        )
    )

    # Mana Curve
    _render_mana_curve(result)

    # Type and Tag distribution side by side
    _render_distributions(result)

    # Color sources
    _render_color_sources(result)

    # Scores
    _render_scores(result)

    # Issues
    if result.issues:
        _render_issues(result)

    # Recommendations
    if result.recommendations:
        _render_recommendations(result)

    # Legal footer
    console.print()
    console.print(f"[dim]{ATTRIBUTION}[/dim]")
    console.print(f"[dim]{DISCLAIMER}[/dim]")
    console.print()


def _render_mana_curve(result: AnalysisResult):
    """Render an ASCII mana curve chart."""
    if not result.mana_curve:
        return

    max_count = max(result.mana_curve.values()) if result.mana_curve else 1
    bar_max = 30  # Max bar width

    table = Table(title="Mana Curve", show_header=True, header_style="bold magenta")
    table.add_column("MV", justify="right", width=4)
    table.add_column("Count", justify="right", width=5)
    table.add_column("Distribution", min_width=35)

    for mv in range(8):
        count = result.mana_curve.get(mv, 0)
        bar_len = int((count / max_count) * bar_max) if max_count > 0 else 0
        bar = "█" * bar_len
        label = f"{mv}" if mv < 7 else "7+"
        table.add_row(label, str(count), f"[cyan]{bar}[/cyan]")

    console.print(table)
    console.print(f"  [dim]Average mana value: {result.average_cmc}[/dim]")
    console.print()


def _render_distributions(result: AnalysisResult):
    """Render type and tag distributions."""
    # Type distribution
    type_table = Table(title="Card Types", show_header=True, header_style="bold blue")
    type_table.add_column("Type", width=15)
    type_table.add_column("Count", justify="right", width=6)
    type_table.add_column("% of Deck", justify="right", width=8)

    total = result.total_cards or 1
    for type_name in ["Land", "Creature", "Instant", "Sorcery", "Artifact", "Enchantment", "Planeswalker", "Battle"]:
        count = result.type_distribution.get(type_name, 0)
        if count > 0:
            pct = f"{count / total * 100:.1f}%"
            type_table.add_row(type_name, str(count), pct)

    console.print(type_table)
    console.print()

    # Key role counts
    role_table = Table(title="Functional Roles", show_header=True, header_style="bold green")
    role_table.add_column("Role", width=20)
    role_table.add_column("Count", justify="right", width=6)

    key_tags = [
        ("Lands", result.land_count),
        ("Ramp", result.ramp_count),
        ("Card Draw", result.draw_engine_count),
        ("Removal", result.tag_distribution.get("targeted_removal", 0)),
        ("Board Wipes", result.tag_distribution.get("board_wipe", 0)),
        ("Counterspells", result.tag_distribution.get("counterspell", 0)),
        ("Threats", result.tag_distribution.get("threat", 0)),
        ("Finishers", result.tag_distribution.get("finisher", 0)),
        ("Engines", result.tag_distribution.get("engine", 0)),
        ("Tutors", result.tag_distribution.get("tutor", 0)),
        ("Recursion", result.tag_distribution.get("recursion", 0)),
        ("Protection", result.tag_distribution.get("protection", 0)),
    ]

    for name, count in key_tags:
        if count > 0:
            role_table.add_row(name, str(count))

    console.print(role_table)
    console.print()


def _render_color_sources(result: AnalysisResult):
    """Render color source analysis."""
    if not result.color_sources and not result.color_distribution:
        return

    table = Table(title="Color Analysis", show_header=True, header_style="bold yellow")
    table.add_column("Color", width=10)
    table.add_column("Cards", justify="right", width=8)
    table.add_column("Sources", justify="right", width=8)
    table.add_column("Status", width=12)

    for color_code in ["W", "U", "B", "R", "G"]:
        cards = result.color_distribution.get(color_code, 0)
        sources = result.color_sources.get(color_code, 0)
        if cards > 0 or sources > 0:
            name = COLOR_NAMES.get(color_code, color_code)
            if cards > 0 and sources >= cards * 0.6:
                status = "[green]Good[/green]"
            elif cards > 0 and sources >= cards * 0.4:
                status = "[yellow]Fair[/yellow]"
            elif cards > 0:
                status = "[red]Low[/red]"
            else:
                status = "[dim]—[/dim]"
            table.add_row(name, str(cards), str(sources), status)

    console.print(table)
    console.print()


def _render_scores(result: AnalysisResult):
    """Render category scores."""
    if not result.scores:
        return

    table = Table(title="Category Scores", show_header=True, header_style="bold")
    table.add_column("Category", width=20)
    table.add_column("Score", justify="right", width=8)
    table.add_column("Rating", width=12)

    score_names = {
        "mana_base": "Mana Base",
        "ramp": "Ramp",
        "card_advantage": "Card Advantage",
        "interaction": "Interaction",
        "curve": "Curve",
        "threat_density": "Threat Density",
    }

    for key, label in score_names.items():
        score = result.scores.get(key, 0)
        rating = _score_rating(score)
        table.add_row(label, f"{score:.0f}", rating)

    console.print(table)
    console.print()


def _score_rating(score: float) -> str:
    if score >= 85:
        return "[bold green]Excellent[/bold green]"
    elif score >= 70:
        return "[green]Good[/green]"
    elif score >= 55:
        return "[yellow]Fair[/yellow]"
    elif score >= 40:
        return "[red]Weak[/red]"
    else:
        return "[bold red]Critical[/bold red]"


def _render_issues(result: AnalysisResult):
    """Render validation issues and structural warnings."""
    table = Table(title="Issues", show_header=True, header_style="bold red")
    table.add_column("Severity", width=10)
    table.add_column("Issue")
    table.add_column("Card", width=25)

    for issue in result.issues:
        sev = issue.severity
        if sev == "error":
            sev_display = "[bold red]ERROR[/bold red]"
        elif sev == "warning":
            sev_display = "[yellow]WARN[/yellow]"
        else:
            sev_display = "[dim]INFO[/dim]"
        table.add_row(sev_display, issue.message, issue.card_name or "")

    console.print(table)
    console.print()


def _render_recommendations(result: AnalysisResult):
    """Render actionable recommendations."""
    console.print(Panel(
        "\n".join(f"  • {rec}" for rec in result.recommendations),
        title="[bold green]Recommendations[/bold green]",
        border_style="green",
    ))


if __name__ == "__main__":
    main()
