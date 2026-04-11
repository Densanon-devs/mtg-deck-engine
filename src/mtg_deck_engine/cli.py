"""CLI entry point for mtg-deck-engine."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Core imports needed by most commands
from mtg_deck_engine.data.database import CardDatabase
from mtg_deck_engine.legal import ATTRIBUTION, DISCLAIMER
from mtg_deck_engine.models import Format

# Heavy imports are lazy-loaded inside command functions to speed up
# simple commands like `info` and `search`. Each cmd_* function imports
# only what it needs.

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
    analyze_parser.add_argument(
        "--deep", action="store_true",
        help="Include probability analysis (opening hands, mana odds, key card access)",
    )
    analyze_parser.add_argument(
        "--sims", type=int, default=10000,
        help="Number of Monte Carlo simulations for opening hand analysis (default: 10000)",
    )
    analyze_parser.add_argument(
        "--export", type=str, default=None,
        help="Export report to file (supports .json, .md, .html)",
    )

    # probability command
    prob_parser = subparsers.add_parser("probability", help="Run probability analysis on a decklist")
    prob_parser.add_argument("file", type=str, help="Path to decklist file")
    prob_parser.add_argument("--name", type=str, default=None, help="Deck name")
    prob_parser.add_argument(
        "--format", type=str, default=None, choices=[f.value for f in Format], help="Deck format",
    )
    prob_parser.add_argument("--db", type=str, help="Custom database path")
    prob_parser.add_argument("--sims", type=int, default=10000, help="Monte Carlo simulations")
    prob_parser.add_argument(
        "--card", action="append", dest="cards", help="Track specific card (repeatable)",
    )

    # goldfish command
    gf_parser = subparsers.add_parser("goldfish", help="Run goldfish (solo) simulation")
    gf_parser.add_argument("file", type=str, help="Path to decklist file")
    gf_parser.add_argument("--name", type=str, default=None, help="Deck name")
    gf_parser.add_argument(
        "--format", type=str, default=None, choices=[f.value for f in Format], help="Deck format",
    )
    gf_parser.add_argument("--db", type=str, help="Custom database path")
    gf_parser.add_argument("--sims", type=int, default=1000, help="Number of games to simulate")
    gf_parser.add_argument("--turns", type=int, default=10, help="Max turns per game (default: 10)")

    # gauntlet command
    gt_parser = subparsers.add_parser("gauntlet", help="Run matchup gauntlet against archetype field")
    gt_parser.add_argument("file", type=str, help="Path to decklist file")
    gt_parser.add_argument("--name", type=str, default=None, help="Deck name")
    gt_parser.add_argument(
        "--format", type=str, default=None, choices=[f.value for f in Format], help="Deck format",
    )
    gt_parser.add_argument("--db", type=str, help="Custom database path")
    gt_parser.add_argument("--sims", type=int, default=500, help="Games per matchup (default: 500)")
    gt_parser.add_argument("--turns", type=int, default=12, help="Max turns per game (default: 12)")
    gt_parser.add_argument(
        "--suite", type=str, default=None,
        help="Benchmark suite (casual-commander, cedh, modern-meta, standard-meta, aggro-gauntlet, control-gauntlet)",
    )

    # save command
    save_parser = subparsers.add_parser("save", help="Save a deck version snapshot")
    save_parser.add_argument("file", type=str, help="Path to decklist file")
    save_parser.add_argument("deck_id", type=str, help="Unique deck identifier (e.g. 'atraxa-superfriends')")
    save_parser.add_argument("--name", type=str, default=None, help="Deck name")
    save_parser.add_argument(
        "--format", type=str, default=None, choices=[f.value for f in Format], help="Deck format",
    )
    save_parser.add_argument("--notes", type=str, default="", help="Version notes")
    save_parser.add_argument("--db", type=str, help="Custom database path")

    # compare command
    cmp_parser = subparsers.add_parser("compare", help="Compare two deck versions")
    cmp_parser.add_argument("deck_id", type=str, help="Deck identifier")
    cmp_parser.add_argument("--v1", type=int, default=None, help="First version (default: previous)")
    cmp_parser.add_argument("--v2", type=int, default=None, help="Second version (default: latest)")

    # history command
    hist_parser = subparsers.add_parser("history", help="Show deck version history and trends")
    hist_parser.add_argument("deck_id", nargs="?", default=None, help="Deck identifier (omit to list all)")

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
    elif args.command == "probability":
        cmd_probability(args)
    elif args.command == "goldfish":
        cmd_goldfish(args)
    elif args.command == "gauntlet":
        cmd_gauntlet(args)
    elif args.command == "save":
        cmd_save(args)
    elif args.command == "compare":
        cmd_compare(args)
    elif args.command == "history":
        cmd_history(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "info":
        cmd_info(args)


def _get_db(args) -> CardDatabase:
    if hasattr(args, "db") and args.db:
        return CardDatabase(Path(args.db))
    return CardDatabase()


def cmd_ingest(args):
    from mtg_deck_engine.data.scryfall import ingest

    db = _get_db(args)
    try:
        asyncio.run(ingest(db=db, force=args.force))
    finally:
        db.close()


def cmd_analyze(args):
    from mtg_deck_engine.analysis.advanced import run_advanced_analysis
    from mtg_deck_engine.analysis.static import analyze_deck
    from mtg_deck_engine.deck.parser import parse_auto
    from mtg_deck_engine.deck.resolver import resolve_deck
    from mtg_deck_engine.deck.validator import validate_deck
    from mtg_deck_engine.export.exporter import export_html, export_json, export_markdown
    from mtg_deck_engine.formats.profiles import detect_archetype, format_recommendations
    from mtg_deck_engine.models import AnalysisResult

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

        # Archetype detection
        archetype = detect_archetype(deck)
        fmt_recs = format_recommendations(deck, archetype)
        result.recommendations.extend(fmt_recs)

        # Advanced heuristics
        adv = run_advanced_analysis(deck, result.color_sources)
        result.recommendations.extend(adv.advanced_recommendations)

        # Display
        _render_dashboard(result, deck)

        # Archetype and advanced
        if archetype.value != "unknown":
            console.print(f"  [bold]Detected Archetype:[/bold] {archetype.value.replace('_', ' ').title()}")
        if adv.mana_base_grade:
            console.print(f"  [bold]Mana Base Grade:[/bold] {adv.mana_base_grade}")
        if adv.synergies:
            console.print(f"  [bold]Synergies Found:[/bold] {len(adv.synergies)}")
        console.print()

        # Probability layer (--deep)
        if hasattr(args, "deep") and args.deep:
            _run_and_render_probability(deck, args.sims)

        # Export
        if hasattr(args, "export") and args.export:
            export_path = Path(args.export)
            adv_dict = {
                "mana_base_grade": adv.mana_base_grade,
                "mana_base_notes": adv.mana_base_notes,
                "synergies": [{"card_a": s.card_a, "card_b": s.card_b, "reason": s.reason} for s in adv.synergies],
                "advanced_recommendations": adv.advanced_recommendations,
            }
            if export_path.suffix == ".json":
                export_json(result, adv_dict, archetype.value, export_path)
            elif export_path.suffix == ".html":
                export_html(result, adv_dict, archetype.value, export_path)
            else:
                export_markdown(result, adv_dict, archetype.value, export_path)
            console.print(f"[green]Report exported to {export_path}[/green]")

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


def cmd_probability(args):
    """Dedicated probability analysis command."""
    from mtg_deck_engine.deck.parser import parse_auto
    from mtg_deck_engine.deck.resolver import resolve_deck

    db = _get_db(args)
    try:
        if db.card_count() == 0:
            console.print("[red]No cards in database. Run 'mtg-engine ingest' first.[/red]")
            sys.exit(1)

        file_path = Path(args.file)
        if not file_path.exists():
            console.print(f"[red]File not found: {file_path}[/red]")
            sys.exit(1)

        text = file_path.read_text(encoding="utf-8")
        deck_name = args.name or file_path.stem

        entries = parse_auto(text)
        if not entries:
            console.print("[red]No cards found in decklist.[/red]")
            sys.exit(1)

        fmt = Format(args.format) if args.format else None
        deck = resolve_deck(entries, db, name=deck_name, format=fmt)

        console.print(
            Panel(
                f"[bold]{deck_name}[/bold]"
                + (f"  |  Format: {deck.format.value}" if deck.format else "")
                + f"  |  {deck.total_cards} cards",
                title="[bold cyan]MTG Deck Engine — Probability Analysis[/bold cyan]",
                border_style="cyan",
            )
        )

        _run_and_render_probability(deck, args.sims, card_names=args.cards)

        console.print(f"\n[dim]{ATTRIBUTION}[/dim]")
        console.print(f"[dim]{DISCLAIMER}[/dim]\n")

    finally:
        db.close()


def cmd_goldfish(args):
    """Run goldfish simulation."""
    from mtg_deck_engine.deck.parser import parse_auto
    from mtg_deck_engine.deck.resolver import resolve_deck
    from mtg_deck_engine.goldfish.runner import run_goldfish_batch

    db = _get_db(args)
    try:
        if db.card_count() == 0:
            console.print("[red]No cards in database. Run 'mtg-engine ingest' first.[/red]")
            sys.exit(1)

        file_path = Path(args.file)
        if not file_path.exists():
            console.print(f"[red]File not found: {file_path}[/red]")
            sys.exit(1)

        text = file_path.read_text(encoding="utf-8")
        deck_name = args.name or file_path.stem

        entries = parse_auto(text)
        if not entries:
            console.print("[red]No cards found in decklist.[/red]")
            sys.exit(1)

        fmt = Format(args.format) if args.format else None
        deck = resolve_deck(entries, db, name=deck_name, format=fmt)

        console.print(
            Panel(
                f"[bold]{deck_name}[/bold]"
                + (f"  |  Format: {deck.format.value}" if deck.format else "")
                + f"  |  {deck.total_cards} cards",
                title="[bold cyan]MTG Deck Engine — Goldfish Simulation[/bold cyan]",
                border_style="cyan",
            )
        )

        console.print(f"[dim]Running {args.sims} goldfish games ({args.turns} turns each)...[/dim]")
        report = run_goldfish_batch(deck, simulations=args.sims, max_turns=args.turns)
        _render_goldfish_report(report)

        console.print(f"\n[dim]{ATTRIBUTION}[/dim]")
        console.print(f"[dim]{DISCLAIMER}[/dim]\n")

    finally:
        db.close()


def cmd_gauntlet(args):
    """Run matchup gauntlet against archetype field."""
    from mtg_deck_engine.benchmarks.suites import get_suite, list_suites
    from mtg_deck_engine.deck.parser import parse_auto
    from mtg_deck_engine.deck.resolver import resolve_deck
    from mtg_deck_engine.matchup.gauntlet import run_gauntlet

    db = _get_db(args)
    try:
        if db.card_count() == 0:
            console.print("[red]No cards in database. Run 'mtg-engine ingest' first.[/red]")
            sys.exit(1)

        file_path = Path(args.file)
        if not file_path.exists():
            console.print(f"[red]File not found: {file_path}[/red]")
            sys.exit(1)

        text = file_path.read_text(encoding="utf-8")
        deck_name = args.name or file_path.stem

        entries = parse_auto(text)
        if not entries:
            console.print("[red]No cards found in decklist.[/red]")
            sys.exit(1)

        fmt = Format(args.format) if args.format else None
        deck = resolve_deck(entries, db, name=deck_name, format=fmt)

        console.print(
            Panel(
                f"[bold]{deck_name}[/bold]"
                + (f"  |  Format: {deck.format.value}" if deck.format else "")
                + f"  |  {deck.total_cards} cards",
                title="[bold cyan]MTG Deck Engine — Meta Gauntlet[/bold cyan]",
                border_style="cyan",
            )
        )

        # Resolve suite
        archetypes = None
        if hasattr(args, "suite") and args.suite:
            suite = get_suite(args.suite)
            if suite:
                archetypes = suite.archetypes
                console.print(f"[cyan]Using suite: {suite.name} — {suite.description}[/cyan]")
            else:
                console.print(f"[yellow]Suite '{args.suite}' not found. Available: {', '.join(list_suites())}[/yellow]")
                console.print("[dim]Using default gauntlet.[/dim]")

        report = run_gauntlet(deck, archetypes=archetypes, simulations=args.sims, max_turns=args.turns)
        _render_gauntlet_report(report)

        console.print(f"\n[dim]{ATTRIBUTION}[/dim]")
        console.print(f"[dim]{DISCLAIMER}[/dim]\n")

    finally:
        db.close()


def cmd_save(args):
    """Save a deck version snapshot with analysis scores."""
    from mtg_deck_engine.analysis.static import analyze_deck
    from mtg_deck_engine.deck.parser import parse_auto
    from mtg_deck_engine.deck.resolver import resolve_deck
    from mtg_deck_engine.versioning.storage import VersionStore

    db = _get_db(args)
    try:
        if db.card_count() == 0:
            console.print("[red]No cards in database. Run 'mtg-engine ingest' first.[/red]")
            sys.exit(1)

        file_path = Path(args.file)
        if not file_path.exists():
            console.print(f"[red]File not found: {file_path}[/red]")
            sys.exit(1)

        text = file_path.read_text(encoding="utf-8")
        deck_name = args.name or file_path.stem
        fmt = Format(args.format) if args.format else None

        entries = parse_auto(text)
        if not entries:
            console.print("[red]No cards found in decklist.[/red]")
            sys.exit(1)

        deck = resolve_deck(entries, db, name=deck_name, format=fmt)

        # Run analysis to capture scores
        result = analyze_deck(deck)

        # Build decklist and zone maps
        decklist = {}
        zones: dict[str, list[str]] = {}
        for entry in deck.entries:
            decklist[entry.card_name] = decklist.get(entry.card_name, 0) + entry.quantity
            zone_name = entry.zone.value
            zones.setdefault(zone_name, []).append(entry.card_name)

        # Collect metrics
        metrics = {
            "land_count": float(result.land_count),
            "ramp_count": float(result.ramp_count),
            "draw_count": float(result.draw_engine_count),
            "interaction_count": float(result.interaction_count),
            "threat_count": float(result.threat_count),
            "average_cmc": result.average_cmc,
            "total_cards": float(result.total_cards),
        }

        # Save
        store = VersionStore()
        try:
            snap = store.save_version(
                deck_id=args.deck_id,
                name=deck_name,
                format=deck.format.value if deck.format else None,
                decklist=decklist,
                zones=zones,
                scores=result.scores,
                metrics=metrics,
                notes=args.notes,
            )
            console.print(
                f"[bold green]Saved {deck_name} v{snap.version_number}[/bold green] "
                f"(id: {args.deck_id}, {len(decklist)} unique cards)"
            )
            if args.notes:
                console.print(f"  [dim]Notes: {args.notes}[/dim]")
        finally:
            store.close()

    finally:
        db.close()


def cmd_compare(args):
    """Compare two versions of a deck."""
    from mtg_deck_engine.versioning.impact import analyze_impact
    from mtg_deck_engine.versioning.storage import VersionStore, diff_versions

    store = VersionStore()
    try:
        versions = store.get_all_versions(args.deck_id)
        if len(versions) < 2:
            console.print(f"[yellow]Need at least 2 saved versions to compare. Found {len(versions)}.[/yellow]")
            return

        v1_num = args.v1 if args.v1 is not None else versions[-2].version_number
        v2_num = args.v2 if args.v2 is not None else versions[-1].version_number

        snap_a = store.get_version(args.deck_id, v1_num)
        snap_b = store.get_version(args.deck_id, v2_num)

        if not snap_a or not snap_b:
            console.print(f"[red]Version not found. Available: {[v.version_number for v in versions]}[/red]")
            return

        diff = diff_versions(snap_a, snap_b)
        impact = analyze_impact(snap_a, snap_b, diff)

        _render_comparison(impact)

    finally:
        store.close()


def cmd_history(args):
    """Show deck version history and trends."""
    from mtg_deck_engine.versioning.storage import VersionStore
    from mtg_deck_engine.versioning.trends import analyze_trends

    store = VersionStore()
    try:
        if args.deck_id is None:
            # List all decks
            decks = store.list_decks()
            if not decks:
                console.print("[yellow]No saved decks found. Use 'mtg-engine save' to save a deck.[/yellow]")
                return

            table = Table(title="Saved Decks", show_header=True, header_style="bold")
            table.add_column("Deck ID", width=25)
            table.add_column("Name", width=25)
            table.add_column("Format", width=12)
            table.add_column("Versions", justify="right", width=9)
            table.add_column("Last Updated", width=20)

            for d in decks:
                table.add_row(
                    d["deck_id"], d["name"], d["format"] or "—",
                    str(d["versions"]), d["updated_at"][:16],
                )

            console.print(table)
            return

        # Show history for specific deck
        versions = store.get_all_versions(args.deck_id)
        if not versions:
            console.print(f"[yellow]No versions found for deck '{args.deck_id}'[/yellow]")
            return

        console.print(Panel(
            f"[bold]{args.deck_id}[/bold]  |  {len(versions)} version(s)",
            title="[bold cyan]Deck Version History[/bold cyan]",
            border_style="cyan",
        ))

        # Version list
        v_table = Table(title="Versions", show_header=True, header_style="bold")
        v_table.add_column("V#", justify="right", width=4)
        v_table.add_column("Saved At", width=18)
        v_table.add_column("Cards", justify="right", width=6)
        v_table.add_column("Notes", width=35)

        for v in versions:
            total_cards = sum(v.decklist.values())
            v_table.add_row(
                str(v.version_number),
                v.saved_at[:16],
                str(total_cards),
                v.notes or "—",
            )

        console.print(v_table)

        # Trend analysis
        if len(versions) >= 2:
            trend_report = analyze_trends(versions)
            _render_trends(trend_report)

    finally:
        store.close()


def _run_and_render_probability(deck, sims: int = 10000, card_names: list[str] | None = None):
    """Run all probability analyses and render results."""
    from mtg_deck_engine.probability.key_cards import analyze_card_access, analyze_role_access
    from mtg_deck_engine.probability.mana_development import analyze_mana_development
    from mtg_deck_engine.probability.opening_hand import simulate_opening_hands

    console.print()
    console.print("[bold magenta]--- Probability Analysis ---[/bold magenta]")

    # Mana development
    mana_report = analyze_mana_development(deck)
    _render_mana_development(mana_report)

    # Opening hands
    console.print("[dim]Running opening hand simulation...[/dim]")
    hand_report = simulate_opening_hands(deck, simulations=sims)
    _render_opening_hands(hand_report)

    # Key card access
    card_results = analyze_card_access(deck, card_names=card_names)
    if card_results:
        _render_card_access(card_results)

    # Role access
    role_results = analyze_role_access(deck)
    if role_results:
        _render_role_access(role_results)


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


# =============================================================================
# Probability rendering
# =============================================================================


def _render_mana_development(report: ManaDevelopmentReport):
    """Render mana development probability tables."""
    console.print()

    # Summary stats
    console.print(Panel(
        f"[bold]Lands:[/bold] {report.land_count}  |  "
        f"[bold]Ramp:[/bold] {report.ramp_count}  |  "
        f"[bold]Total Sources:[/bold] {report.mana_source_count}  |  "
        f"[bold]Deck Size:[/bold] {report.deck_size}",
        title="[bold yellow]Mana Development[/bold yellow]",
        border_style="yellow",
    ))

    # Key milestones
    table = Table(title="Mana Milestones", show_header=True, header_style="bold yellow")
    table.add_column("Milestone", width=30)
    table.add_column("Probability", justify="right", width=12)
    table.add_column("Rating", width=12)

    milestones = [
        ("2 lands by turn 2", report.two_lands_by_t2),
        ("3 lands by turn 3", report.three_lands_by_t3),
        ("4 mana by turn 4 (w/ ramp)", report.four_mana_by_t4),
        ("5 mana by turn 5 (w/ ramp)", report.five_mana_by_t5),
    ]

    if report.commander_on_curve > 0:
        milestones.append(
            (f"Commander on curve (CMC {report.commander_cmc:.0f})", report.commander_on_curve)
        )

    for name, prob in milestones:
        pct = f"{prob * 100:.1f}%"
        rating = _prob_rating(prob)
        table.add_row(name, pct, rating)

    # Failure rates
    table.add_row("", "", "")
    table.add_row(
        "Mana screw (<2 lands by T3)",
        f"{report.mana_screw_rate * 100:.1f}%",
        _inverse_prob_rating(report.mana_screw_rate),
    )
    table.add_row(
        "Mana flood (6+ lands in first 10)",
        f"{report.mana_flood_rate * 100:.1f}%",
        _inverse_prob_rating(report.mana_flood_rate),
    )

    console.print(table)

    # Turn-by-turn expected mana
    turn_table = Table(title="Expected Mana by Turn", show_header=True, header_style="bold")
    turn_table.add_column("Turn", justify="center", width=6)
    turn_table.add_column("Exp. Lands", justify="right", width=10)
    turn_table.add_column("Exp. Mana", justify="right", width=10)

    for turn in range(1, 8):
        exp_l = report.expected_lands_by_turn.get(turn, 0)
        exp_m = report.expected_mana_by_turn.get(turn, 0)
        turn_table.add_row(str(turn), f"{exp_l:.1f}", f"{exp_m:.1f}")

    console.print(turn_table)
    console.print()


def _render_opening_hands(report: OpeningHandReport):
    """Render opening hand simulation results."""
    if report.simulations == 0:
        return

    console.print(Panel(
        f"[bold]Simulations:[/bold] {report.simulations:,}  |  "
        f"[bold]Keep Rate:[/bold] {report.keep_rate * 100:.1f}%  |  "
        f"[bold]Avg Lands:[/bold] {report.average_lands:.1f}  |  "
        f"[bold]Avg Score:[/bold] {report.average_score:.0f}/100",
        title="[bold blue]Opening Hand Analysis[/bold blue]",
        border_style="blue",
    ))

    # Mulligan keep rates
    mull_table = Table(title="Mulligan Keep Rates", show_header=True, header_style="bold blue")
    mull_table.add_column("Hand Size", justify="center", width=10)
    mull_table.add_column("Keep Rate", justify="right", width=12)

    for size in [7, 6, 5, 4]:
        rate = report.mulligan_keep_rates.get(size, 0)
        mull_table.add_row(str(size), f"{rate * 100:.1f}%")

    console.print(mull_table)

    # Archetype distribution
    if report.archetype_distribution:
        arch_table = Table(title="Opener Archetypes", show_header=True, header_style="bold")
        arch_table.add_column("Archetype", width=15)
        arch_table.add_column("Frequency", justify="right", width=10)
        arch_table.add_column("", min_width=25)

        for arch, freq in sorted(report.archetype_distribution.items(), key=lambda x: -x[1]):
            bar_len = int(freq * 30)
            bar = "█" * bar_len
            arch_table.add_row(arch.replace("_", " ").title(), f"{freq * 100:.1f}%", f"[cyan]{bar}[/cyan]")

        console.print(arch_table)

    # Land count distribution
    if report.land_count_distribution:
        land_table = Table(title="Opening Hand Land Distribution", show_header=True, header_style="bold")
        land_table.add_column("Lands", justify="center", width=6)
        land_table.add_column("Frequency", justify="right", width=10)
        land_table.add_column("", min_width=25)

        for count, freq in sorted(report.land_count_distribution.items()):
            bar_len = int(freq * 40)
            bar = "█" * bar_len
            land_table.add_row(str(count), f"{freq * 100:.1f}%", f"[green]{bar}[/green]")

        console.print(land_table)

    console.print()


def _render_card_access(results: list):
    """Render key card access probabilities."""
    table = Table(title="Key Card Access (% chance by turn)", show_header=True, header_style="bold magenta")
    table.add_column("Card", width=28)
    table.add_column("Copies", justify="center", width=6)
    for t in range(1, 8):
        table.add_column(f"T{t}", justify="right", width=7)

    for result in results:
        row = [result.name, str(result.copies_in_deck)]
        for t in range(1, 8):
            p = result.by_turn.get(t, 0)
            pct = f"{p * 100:.0f}%"
            if p >= 0.8:
                row.append(f"[green]{pct}[/green]")
            elif p >= 0.5:
                row.append(f"[yellow]{pct}[/yellow]")
            else:
                row.append(f"[dim]{pct}[/dim]")
        table.add_row(*row)

    console.print(table)
    console.print()


def _render_role_access(results: list):
    """Render role access probabilities."""
    table = Table(title="Role Access (% chance of at least 1 by turn)", show_header=True, header_style="bold cyan")
    table.add_column("Role", width=20)
    table.add_column("In Deck", justify="center", width=7)
    for t in [1, 2, 3, 4, 5]:
        table.add_column(f"T{t}", justify="right", width=7)

    role_names = {
        "ramp": "Ramp",
        "card_draw": "Card Draw",
        "targeted_removal": "Removal",
        "board_wipe": "Board Wipe",
        "counterspell": "Counter",
        "mana_rock": "Mana Rock",
        "mana_dork": "Mana Dork",
        "tutor": "Tutor",
    }

    for result in results:
        label = role_names.get(result.role, result.role.replace("_", " ").title())
        row = [label, str(result.total_in_deck)]
        for t in [1, 2, 3, 4, 5]:
            p = result.by_turn.get(t, 0)
            pct = f"{p * 100:.0f}%"
            if p >= 0.8:
                row.append(f"[green]{pct}[/green]")
            elif p >= 0.5:
                row.append(f"[yellow]{pct}[/yellow]")
            else:
                row.append(f"[dim]{pct}[/dim]")
        table.add_row(*row)

    console.print(table)
    console.print()


def _prob_rating(prob: float) -> str:
    """Rate a probability (higher is better)."""
    if prob >= 0.90:
        return "[bold green]Excellent[/bold green]"
    elif prob >= 0.75:
        return "[green]Good[/green]"
    elif prob >= 0.60:
        return "[yellow]Fair[/yellow]"
    elif prob >= 0.40:
        return "[red]Weak[/red]"
    else:
        return "[bold red]Critical[/bold red]"


def _inverse_prob_rating(prob: float) -> str:
    """Rate a probability where lower is better (failure rates)."""
    if prob <= 0.05:
        return "[bold green]Excellent[/bold green]"
    elif prob <= 0.10:
        return "[green]Good[/green]"
    elif prob <= 0.20:
        return "[yellow]Fair[/yellow]"
    elif prob <= 0.35:
        return "[red]Weak[/red]"
    else:
        return "[bold red]Critical[/bold red]"


# =============================================================================
# Goldfish rendering
# =============================================================================


def _render_goldfish_report(report: GoldfishReport):
    """Render goldfish simulation results."""
    console.print()

    # Summary
    kill_info = f"Avg Kill Turn: {report.average_kill_turn}" if report.kill_rate > 0 else "No kills in sim window"
    cmd_info = f"Cmdr Cast Rate: {report.commander_cast_rate * 100:.0f}% (avg T{report.average_commander_turn})" if report.commander_cast_rate > 0 else ""

    summary = (
        f"[bold]Games:[/bold] {report.simulations:,}  |  "
        f"[bold]Turns:[/bold] {report.max_turns}  |  "
        f"[bold]Avg Mulligans:[/bold] {report.average_mulligans:.1f}  |  "
        f"[bold]Avg Spells/Game:[/bold] {report.average_spells_cast}"
    )
    if report.kill_rate > 0:
        summary += f"\n[bold]Kill Rate:[/bold] {report.kill_rate * 100:.1f}%  |  {kill_info}"
    if cmd_info:
        summary += f"\n{cmd_info}"

    console.print(Panel(summary, title="[bold red]Goldfish Results[/bold red]", border_style="red"))

    # Turn-by-turn progression
    prog_table = Table(title="Turn-by-Turn Progression (averages)", show_header=True, header_style="bold")
    prog_table.add_column("Turn", justify="center", width=5)
    prog_table.add_column("Lands", justify="right", width=6)
    prog_table.add_column("Creatures", justify="right", width=9)
    prog_table.add_column("Power", justify="right", width=6)
    prog_table.add_column("Mana Spent", justify="right", width=10)
    prog_table.add_column("Spells", justify="right", width=7)
    prog_table.add_column("Cum. Dmg", justify="right", width=8)

    for turn in range(1, report.max_turns + 1):
        lands = report.average_lands_by_turn.get(turn, 0)
        creatures = report.average_creatures_by_turn.get(turn, 0)
        mana = report.average_mana_spent_by_turn.get(turn, 0)
        casts = report.average_cards_cast_by_turn.get(turn, 0)
        damage = report.average_damage_by_turn.get(turn, 0)

        # Estimate avg power from damage delta
        prev_dmg = report.average_damage_by_turn.get(turn - 1, 0) if turn > 1 else 0
        turn_dmg = damage - prev_dmg

        prog_table.add_row(
            str(turn),
            f"{lands:.1f}",
            f"{creatures:.1f}",
            f"{turn_dmg:.0f}",
            f"{mana:.1f}",
            f"{casts:.1f}",
            f"{damage:.0f}",
        )

    console.print(prog_table)

    # Kill turn distribution
    if report.kill_turn_distribution:
        kill_table = Table(title="Kill Turn Distribution", show_header=True, header_style="bold red")
        kill_table.add_column("Turn", justify="center", width=6)
        kill_table.add_column("Rate", justify="right", width=8)
        kill_table.add_column("", min_width=25)

        for turn, rate in sorted(report.kill_turn_distribution.items()):
            bar_len = int(rate * 40)
            bar = "█" * bar_len
            kill_table.add_row(str(turn), f"{rate * 100:.1f}%", f"[red]{bar}[/red]")

        console.print(kill_table)

    # Objectives
    if report.objective_pass_rates:
        obj_table = Table(title="Objective Pass Rates", show_header=True, header_style="bold green")
        obj_table.add_column("Objective", width=30)
        obj_table.add_column("Pass Rate", justify="right", width=10)
        obj_table.add_column("Rating", width=12)

        for name, rate in report.objective_pass_rates.items():
            pct = f"{rate * 100:.1f}%"
            rating = _prob_rating(rate)
            obj_table.add_row(name, pct, rating)

        console.print(obj_table)

    # Most-cast spells
    if report.most_cast_spells:
        spell_table = Table(title="Most-Cast Spells", show_header=True, header_style="bold")
        spell_table.add_column("Card", width=28)
        spell_table.add_column("Times Cast", justify="right", width=10)
        spell_table.add_column("Per Game", justify="right", width=8)

        for name, count in report.most_cast_spells[:8]:
            per_game = count / report.simulations if report.simulations > 0 else 0
            spell_table.add_row(name, str(count), f"{per_game:.2f}")

        console.print(spell_table)

    console.print()


# =============================================================================
# Gauntlet rendering
# =============================================================================


def _render_gauntlet_report(report: GauntletReport):
    """Render matchup gauntlet results."""
    console.print()

    # Summary
    console.print(Panel(
        f"[bold]Total Games:[/bold] {report.total_games:,}  |  "
        f"[bold]Overall Win Rate:[/bold] {report.overall_win_rate * 100:.1f}%  |  "
        f"[bold]Weighted Win Rate:[/bold] {report.weighted_win_rate * 100:.1f}%\n"
        f"[bold]Best:[/bold] vs {report.best_matchup} ({report.best_win_rate * 100:.0f}%)  |  "
        f"[bold]Worst:[/bold] vs {report.worst_matchup} ({report.worst_win_rate * 100:.0f}%)",
        title="[bold magenta]Meta Positioning[/bold magenta]",
        border_style="magenta",
    ))

    # Matchup matrix
    table = Table(title="Matchup Results", show_header=True, header_style="bold")
    table.add_column("Archetype", width=18)
    table.add_column("Win Rate", justify="right", width=9)
    table.add_column("W-L", justify="center", width=9)
    table.add_column("Avg Turns", justify="right", width=9)
    table.add_column("Removed", justify="right", width=8)
    table.add_column("Countered", justify="right", width=9)
    table.add_column("Wiped", justify="right", width=7)
    table.add_column("", min_width=15)

    for m in sorted(report.matchups, key=lambda x: -x.win_rate):
        wr = m.win_rate
        pct = f"{wr * 100:.1f}%"
        bar_len = int(wr * 15)
        bar = "█" * bar_len

        if wr >= 0.60:
            color = "green"
        elif wr >= 0.45:
            color = "yellow"
        else:
            color = "red"

        table.add_row(
            m.archetype_name,
            f"[{color}]{pct}[/{color}]",
            f"{m.wins}-{m.losses}",
            f"{m.avg_turns}",
            f"{m.avg_permanents_removed:.1f}",
            f"{m.avg_spells_countered:.1f}",
            f"{m.avg_wipes_suffered:.1f}",
            f"[{color}]{bar}[/{color}]",
        )

    console.print(table)

    # Category scores
    score_table = Table(title="Meta Scores", show_header=True, header_style="bold cyan")
    score_table.add_column("Category", width=15)
    score_table.add_column("Score", justify="right", width=8)
    score_table.add_column("Rating", width=12)

    scores = [
        ("Speed", report.speed_score),
        ("Resilience", report.resilience_score),
        ("Interaction", report.interaction_score),
        ("Consistency", report.consistency_score),
    ]

    for name, score in scores:
        score_table.add_row(name, f"{score:.0f}", _score_rating(score))

    console.print(score_table)
    console.print()


# =============================================================================
# Version comparison rendering
# =============================================================================


def _render_comparison(impact: ImpactReport):
    """Render version comparison report."""
    console.print()
    diff = impact.diff

    # Verdict banner
    verdict_colors = {
        "improved": "bold green",
        "regressed": "bold red",
        "mixed": "bold yellow",
        "neutral": "dim",
    }
    vc = verdict_colors.get(impact.overall_verdict, "dim")
    console.print(Panel(
        f"[bold]v{impact.version_a} -> v{impact.version_b}[/bold]  |  "
        f"Verdict: [{vc}]{impact.overall_verdict.upper()}[/{vc}]",
        title="[bold cyan]Version Comparison[/bold cyan]",
        border_style="cyan",
    ))

    # Card changes
    if diff and (diff.added or diff.removed or diff.changed_qty):
        card_table = Table(title="Card Changes", show_header=True, header_style="bold")
        card_table.add_column("Change", width=8)
        card_table.add_column("Card", width=30)
        card_table.add_column("Qty", justify="right", width=8)

        for card, qty in sorted(diff.added.items()):
            card_table.add_row("[green]+ ADD[/green]", card, f"+{qty}")
        for card, qty in sorted(diff.removed.items()):
            card_table.add_row("[red]- CUT[/red]", card, f"-{qty}")
        for card, (old, new) in sorted(diff.changed_qty.items()):
            delta = new - old
            sign = "+" if delta > 0 else ""
            color = "green" if delta > 0 else "red"
            card_table.add_row(f"[{color}]~ CHG[/{color}]", card, f"{old}->{new} ({sign}{delta})")

        console.print(card_table)
        console.print(f"  [dim]Total: +{diff.total_added} / -{diff.total_removed}[/dim]")

    # Score deltas
    if impact.score_deltas:
        score_table = Table(title="Score Impact", show_header=True, header_style="bold")
        score_table.add_column("Category", width=18)
        score_table.add_column("Before", justify="right", width=8)
        score_table.add_column("After", justify="right", width=8)
        score_table.add_column("Delta", justify="right", width=8)
        score_table.add_column("", width=8)

        for key, delta in impact.score_deltas.items():
            name = key.replace("_", " ").title()
            old = impact.score_a.get(key, 0)
            new = impact.score_b.get(key, 0)
            if abs(delta) < 1:
                indicator = "[dim]—[/dim]"
            elif delta > 0:
                indicator = "[green]^[/green]"
            else:
                indicator = "[red]v[/red]"
            score_table.add_row(name, f"{old:.0f}", f"{new:.0f}", f"{delta:+.0f}", indicator)

        console.print(score_table)

    # Improvements and regressions
    if impact.improvements:
        console.print(Panel(
            "\n".join(f"  [green]+[/green] {s}" for s in impact.improvements),
            title="[green]Improvements[/green]",
            border_style="green",
        ))

    if impact.regressions:
        console.print(Panel(
            "\n".join(f"  [red]-[/red] {s}" for s in impact.regressions),
            title="[red]Regressions[/red]",
            border_style="red",
        ))

    console.print()


def _render_trends(report: TrendReport):
    """Render trend analysis."""
    if not report.score_trends:
        return

    console.print()
    table = Table(title="Score Trends", show_header=True, header_style="bold magenta")
    table.add_column("Score", width=18)
    table.add_column("Current", justify="right", width=8)
    table.add_column("Best", justify="right", width=6)
    table.add_column("Worst", justify="right", width=6)
    table.add_column("Overall", justify="right", width=8)
    table.add_column("Recent", justify="right", width=8)
    table.add_column("Direction", width=12)

    direction_styles = {
        "improving": "[green]Improving[/green]",
        "declining": "[red]Declining[/red]",
        "stable": "[dim]Stable[/dim]",
        "volatile": "[yellow]Volatile[/yellow]",
    }

    for key, trend in report.score_trends.items():
        overall = f"{trend.delta_first_to_last:+.0f}"
        recent = f"{trend.delta_recent:+.0f}" if trend.delta_recent != 0 else "—"
        direction = direction_styles.get(trend.direction, trend.direction)
        table.add_row(
            trend.name, f"{trend.current:.0f}", f"{trend.best:.0f}", f"{trend.worst:.0f}",
            overall, recent, direction,
        )

    console.print(table)

    # Suggestions
    if report.suggestions:
        console.print(Panel(
            "\n".join(f"  • {s}" for s in report.suggestions),
            title="[bold green]Suggestions[/bold green]",
            border_style="green",
        ))

    console.print()


if __name__ == "__main__":
    main()
