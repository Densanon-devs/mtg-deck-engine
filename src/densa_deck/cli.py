"""CLI entry point for densa-deck."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Use safe console that handles piped output and non-UTF-8 terminals
import io as _io, sys as _sys

# Force stdout/stderr to UTF-8 on Windows to avoid cp1252 encoding errors
# with Rich's box-drawing characters when running the bundled binary
if _sys.platform == "win32":
    try:
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

_force_terminal = _sys.stdout.isatty() if hasattr(_sys.stdout, "isatty") else False

# Core imports needed by most commands
from densa_deck.data.database import CardDatabase
from densa_deck.legal import ATTRIBUTION, DISCLAIMER
from densa_deck.models import Format
from densa_deck.tiers import COMMAND_FEATURES, _PRO_UPGRADE_MSG, get_user_tier, require_pro

# Heavy imports are lazy-loaded inside command functions to speed up
# simple commands like `info` and `search`. Each cmd_* function imports
# only what it needs.

console = Console(force_terminal=_force_terminal)

COLOR_SYMBOLS = {"W": "☀", "U": "💧", "B": "💀", "R": "🔥", "G": "🌿"}
COLOR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}


def main():
    parser = argparse.ArgumentParser(
        prog="densa-deck",
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
    analyze_parser.add_argument(
        "--with-llm", action="store_true",
        help="Add LLM-generated executive summary + cut suggestions to the report (Pro)",
    )
    analyze_parser.add_argument(
        "--llm-seed", type=int, default=42,
        help=(
            "RNG seed for the LLM backend. Same seed + same prompt = same "
            "output (deterministic). Default: 42. Pass different integers "
            "to get different angles on the same deck."
        ),
    )
    analyze_parser.add_argument(
        "--playgroup-power", type=_playgroup_power_type, default=None,
        help="Target power level of your playgroup (1-10) — analyst narrates how the deck fits",
    )
    analyze_parser.add_argument(
        "--vs-previous", action="store_true",
        help="Narrate what changed vs. the last saved version of this deck (requires prior `save`)",
    )
    analyze_parser.add_argument(
        "--deck-id", type=str, default=None,
        help="Deck ID used during `save` (for --vs-previous lookup). Defaults to deck name, then falls back to a name-match over saved decks.",
    )
    analyze_parser.add_argument(
        "--swaps", type=int, default=0, metavar="N",
        help="Generate N paired cut+add swap suggestions (Pro; requires --with-llm)",
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

    # calc command
    calc_parser = subparsers.add_parser("calc", help="Quick hypergeometric probability calculator")
    calc_parser.add_argument("--deck", type=int, required=True, help="Deck size")
    calc_parser.add_argument("--copies", type=int, required=True, help="Copies in deck")
    calc_parser.add_argument("--turns", type=int, default=7, help="Calculate through turn N (default: 7)")
    calc_parser.add_argument("--draw", action="store_true", help="On the draw (default: on the play)")

    # diff command
    diff_parser = subparsers.add_parser("diff", help="Compare two different decks side by side")
    diff_parser.add_argument("file_a", type=str, help="First decklist file")
    diff_parser.add_argument("file_b", type=str, help="Second decklist file")
    diff_parser.add_argument("--format", type=str, default=None, choices=[f.value for f in Format])
    diff_parser.add_argument("--db", type=str, help="Custom database path")

    # practice command
    practice_parser = subparsers.add_parser("practice", help="Interactive mulligan practice")
    practice_parser.add_argument("file", type=str, help="Path to decklist file")
    practice_parser.add_argument("--format", type=str, default=None, choices=[f.value for f in Format])
    practice_parser.add_argument("--db", type=str, help="Custom database path")
    practice_parser.add_argument("--rounds", type=int, default=10, help="Number of practice rounds")

    # license command
    lic_parser = subparsers.add_parser("license", help="Manage Pro license key")
    lic_subs = lic_parser.add_subparsers(dest="license_action")
    lic_activate = lic_subs.add_parser("activate", help="Activate a license key")
    lic_activate.add_argument("key", type=str, help="License key from purchase")
    lic_subs.add_parser("show", help="Show current license info")
    lic_subs.add_parser("remove", help="Remove the saved license")

    # info command
    info_parser = subparsers.add_parser("info", help="Show database info")
    info_parser.add_argument("--db", type=str, help="Custom database path")

    # coach command (Pro) — interactive REPL with deck context pre-loaded
    coach_parser = subparsers.add_parser(
        "coach", help="Interactive deck coach (Pro) — asks questions about your deck")
    coach_parser.add_argument("file", type=str, help="Path to decklist file")
    coach_parser.add_argument("--name", type=str, default=None)
    coach_parser.add_argument(
        "--format", type=str, default=None, choices=[f.value for f in Format])
    coach_parser.add_argument("--db", type=str, help="Custom database path")
    coach_parser.add_argument(
        "--llm-seed", type=int, default=42,
        help="RNG seed for the coach LLM. Default: 42 (deterministic).",
    )

    # app command — launch the desktop GUI (Stage A)
    app_parser = subparsers.add_parser(
        "app", help="Launch the desktop app (GUI over this engine)")
    app_parser.add_argument(
        "--debug", action="store_true",
        help="Enable pywebview devtools for frontend debugging",
    )
    # Hidden arg — when the OS launches us via the densa-deck:// URI scheme
    # it passes the URL as the first positional arg. We parse + auto-activate.
    app_parser.add_argument(
        "activation_url", nargs="?", default=None,
        help="(internal) densa-deck:// URI from OS deep-link handler",
    )

    # register-protocol command — register the densa-deck:// URI scheme so
    # the Stripe success page can deep-link an activation into the app.
    reg_parser = subparsers.add_parser(
        "register-protocol",
        help="Register the densa-deck:// URI scheme (Windows)",
    )
    reg_parser.add_argument(
        "--unregister", action="store_true",
        help="Remove the registration instead of adding it",
    )

    # mcp command — expose the engine to AI clients over the Model Context
    # Protocol. `densa-deck mcp serve` runs a stdio server that Claude
    # desktop / ulcagent / Cursor / etc. can mount as a subprocess.
    mcp_parser = subparsers.add_parser(
        "mcp", help="Run an MCP (Model Context Protocol) server (Pro features license-gated)")
    mcp_subs = mcp_parser.add_subparsers(dest="mcp_action")
    mcp_serve = mcp_subs.add_parser(
        "serve", help="Run the MCP server on stdio for AI clients to mount")
    mcp_serve.add_argument(
        "--read-only", action="store_true",
        help="Skip registering Pro tools entirely — agent can't see goldfish/gauntlet/coach",
    )

    # analyst command (Pro) — manage the local GGUF model for the LLM analyst layer
    analyst_parser = subparsers.add_parser(
        "analyst", help="Manage the local LLM analyst model (Pro)")
    analyst_subs = analyst_parser.add_subparsers(dest="analyst_action")
    analyst_pull = analyst_subs.add_parser("pull", help="Download an analyst model")
    analyst_pull.add_argument(
        "model", nargs="?", default="qwen2.5-3b",
        choices=["qwen2.5-0.5b", "qwen2.5-3b"],
        help="Which model to download (default: qwen2.5-3b, ~1.8 GB)",
    )
    analyst_subs.add_parser("show", help="Show the configured model path + availability")

    # combos command — Commander Spellbook integration (free tier)
    combos_parser = subparsers.add_parser(
        "combos",
        help="Detect combos in a deck via Commander Spellbook",
    )
    combos_subs = combos_parser.add_subparsers(dest="combos_action")
    combos_refresh = combos_subs.add_parser(
        "refresh", help="Fetch the latest combo dataset from Commander Spellbook")
    combos_refresh.add_argument(
        "--db", type=Path, help="Override combo DB path (default: ~/.densa-deck/combos.db)")
    combos_status = combos_subs.add_parser(
        "status", help="Show local combo cache status (count + last refresh)")
    combos_status.add_argument("--db", type=Path)
    combos_detect = combos_subs.add_parser(
        "detect", help="Detect combos in a deck file or stdin")
    combos_detect.add_argument(
        "deck", nargs="?", help="Deck file path (or '-' / omitted for stdin)")
    combos_detect.add_argument(
        "--format", default="commander",
        help="Format for color-identity / legality (default: commander)")
    combos_detect.add_argument("--db", type=Path, help="Override combo DB path")
    combos_detect.add_argument(
        "--limit", type=int, default=25,
        help="Max combos to print (default: 25)")

    combos_near = combos_subs.add_parser(
        "near-miss", help="List combos this deck is N cards away from completing")
    combos_near.add_argument(
        "deck", nargs="?", help="Deck file (or '-' / omitted for stdin)")
    combos_near.add_argument(
        "--format", default="commander",
        help="Format for color-identity (default: commander)")
    combos_near.add_argument(
        "--max-missing", type=int, default=1,
        help="Max missing cards to count as 'near miss' (default: 1)")
    combos_near.add_argument("--db", type=Path)
    combos_near.add_argument("--limit", type=int, default=25)

    combos_density = combos_subs.add_parser(
        "density",
        help="One-shot summary of a deck's combo density "
             "(detected + near-miss + bracket implications)")
    combos_density.add_argument(
        "deck", nargs="?", help="Deck file (or '-' / omitted for stdin)")
    combos_density.add_argument(
        "--format", default="commander", help="Format profile (default: commander)")
    combos_density.add_argument("--db", type=Path)

    # Debug helper: given a deck + a Spellbook combo id (the URL slug like
    # "1234-5678-..."), report which pieces the deck has and which are
    # missing. Useful for diagnosing why a combo isn't showing in detect /
    # near-miss output.
    combos_verify = combos_subs.add_parser(
        "verify",
        help="Check whether a deck contains all pieces of a named combo")
    combos_verify.add_argument(
        "deck", help="Deck file (or '-' for stdin)")
    combos_verify.add_argument(
        "combo_id", help="Commander Spellbook combo id (URL slug)")
    combos_verify.add_argument(
        "--format", default="commander", help="Format profile (default: commander)")
    combos_verify.add_argument("--db", type=Path)

    # bracket command — Commander bracket-fit assessment (free tier)
    bracket_parser = subparsers.add_parser(
        "bracket", help="Assess how a deck fits a Commander bracket (1-precon ... 5-cedh)")
    bracket_parser.add_argument(
        "deck", nargs="?", help="Deck file (or '-' / omitted for stdin)")
    bracket_parser.add_argument(
        "--target", required=True,
        choices=["1-precon", "2-upgraded", "3-optimized", "4-high-power", "5-cedh"],
        help="Target bracket label")
    bracket_parser.add_argument(
        "--format", default="commander",
        help="Format profile (default: commander)")

    # export command — MTGO / MTGA / Moxfield export (free tier)
    export_parser = subparsers.add_parser(
        "export", help="Export a deck to MTGA / MTGO (.dek) / Moxfield format")
    export_parser.add_argument("deck", help="Deck file to export")
    export_parser.add_argument(
        "--target", default="mtga",
        choices=["mtga", "mtgo", "moxfield"],
        help="Export format (default: mtga)")
    export_parser.add_argument(
        "--format", default="commander",
        help="Source format profile (default: commander)")
    export_parser.add_argument(
        "--out", type=Path,
        help="Write to file instead of stdout")

    # rule0 command — pre-game discussion worksheet (free tier)
    rule0_parser = subparsers.add_parser(
        "rule0",
        help="Build a Rule 0 pre-game worksheet for a deck")
    rule0_parser.add_argument(
        "deck", nargs="?", help="Deck file path (or '-' / omitted for stdin)")
    rule0_parser.add_argument(
        "--format", default="commander",
        help="Format profile (default: commander)")
    rule0_parser.add_argument(
        "--no-combos", action="store_true",
        help="Skip combo detection even if the cache is populated")

    # explain command (Pro) — narrate why one card was flagged
    explain_parser = subparsers.add_parser(
        "explain", help="Explain why a single card was flagged in a deck (Pro)")
    explain_parser.add_argument("deck", help="Deck file path")
    explain_parser.add_argument("card", help="Card name to explain")
    explain_parser.add_argument(
        "--format", default="commander",
        help="Format profile (default: commander)")

    # compare-decks command (Pro) — analyst narration of A vs B
    compare_decks_parser = subparsers.add_parser(
        "compare-decks",
        help="Compare two saved decks via the analyst (Pro)")
    compare_decks_parser.add_argument(
        "deck_a_id", help="First saved deck id")
    compare_decks_parser.add_argument(
        "deck_b_id", help="Second saved deck id")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    # Check tier for pro-gated commands
    command = args.command
    feature = COMMAND_FEATURES.get(command, command)
    if require_pro(feature):
        console.print(_PRO_UPGRADE_MSG)
        sys.exit(0)

    if command == "ingest":
        cmd_ingest(args)
    elif command == "analyze":
        cmd_analyze(args)
    elif command == "probability":
        cmd_probability(args)
    elif command == "goldfish":
        cmd_goldfish(args)
    elif command == "gauntlet":
        cmd_gauntlet(args)
    elif command == "save":
        cmd_save(args)
    elif command == "compare":
        cmd_compare(args)
    elif command == "history":
        cmd_history(args)
    elif command == "calc":
        cmd_calc(args)
    elif command == "diff":
        cmd_diff(args)
    elif command == "practice":
        cmd_practice(args)
    elif command == "search":
        cmd_search(args)
    elif command == "info":
        cmd_info(args)
    elif command == "license":
        cmd_license(args)
    elif command == "analyst":
        cmd_analyst(args)
    elif command == "coach":
        cmd_coach(args)
    elif command == "app":
        cmd_app(args)
    elif command == "register-protocol":
        cmd_register_protocol(args)
    elif command == "mcp":
        cmd_mcp(args)
    elif command == "combos":
        cmd_combos(args)
    elif command == "rule0":
        cmd_rule0(args)
    elif command == "explain":
        cmd_explain(args)
    elif command == "compare-decks":
        cmd_compare_decks(args)
    elif command == "bracket":
        cmd_bracket(args)
    elif command == "export":
        cmd_export(args)


def _get_db(args) -> CardDatabase:
    if hasattr(args, "db") and args.db:
        return CardDatabase(Path(args.db))
    return CardDatabase()


def cmd_ingest(args):
    from densa_deck.data.scryfall import ingest

    db = _get_db(args)
    try:
        asyncio.run(ingest(db=db, force=args.force))
    finally:
        db.close()


def _playgroup_power_type(value: str) -> float:
    """Validate the --playgroup-power arg to the 1.0-10.0 Commander power scale.

    Users hitting values outside [1, 10] usually meant to type something on
    that scale; rejecting early produces a clear error instead of weird
    OVER/UNDER-PITCHES narration further down.
    """
    import argparse
    try:
        f = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"playgroup-power must be a number, got {value!r}")
    if not (1.0 <= f <= 10.0):
        raise argparse.ArgumentTypeError(
            f"playgroup-power must be in 1.0-10.0 (Commander power scale); got {f}"
        )
    return f


def cmd_analyze(args):
    from densa_deck.analysis.advanced import run_advanced_analysis
    from densa_deck.analysis.static import analyze_deck
    from densa_deck.deck.parser import parse_auto
    from densa_deck.deck.resolver import resolve_deck
    from densa_deck.deck.validator import validate_deck
    from densa_deck.export.exporter import export_html, export_json, export_markdown
    from densa_deck.formats.profiles import detect_archetype, format_recommendations
    from densa_deck.models import AnalysisResult

    db = _get_db(args)
    try:
        # Check database has cards
        if db.card_count() == 0:
            console.print(
                "[red]No cards in database. Run 'densa-deck ingest' first.[/red]"
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

        # Power level
        from densa_deck.analysis.power_level import estimate_power_level
        power = estimate_power_level(deck)

        # Castability
        from densa_deck.analysis.castability import analyze_castability
        castability = analyze_castability(deck, result.color_sources)

        # Staples
        from densa_deck.analysis.staples import check_staples
        staples = check_staples(deck)

        # Archetype, power, advanced summary
        summary_parts = []
        if archetype.value != "unknown":
            summary_parts.append(f"[bold]Archetype:[/bold] {archetype.value.replace('_', ' ').title()}")
        summary_parts.append(f"[bold]Power Level:[/bold] {power.overall}/10 ({power.tier})")
        if adv.mana_base_grade:
            summary_parts.append(f"[bold]Mana Base:[/bold] {adv.mana_base_grade}")
        if adv.synergies:
            summary_parts.append(f"[bold]Synergies:[/bold] {len(adv.synergies)}")
        if staples.missing:
            essential = [s for s in staples.missing if s.priority == "essential"]
            if essential:
                summary_parts.append(f"[bold red]Missing Staples:[/bold red] {len(essential)} essential")

        console.print("  " + "  |  ".join(summary_parts))

        is_pro = not require_pro("advanced_scoring")

        # Power breakdown (pro: full breakdown, free: just the number)
        if is_pro:
            console.print(
                f"  [dim]Speed {power.speed:.0f} | Interaction {power.interaction:.0f} | "
                f"Combo {power.combo_potential:.0f} | Mana {power.mana_efficiency:.0f} | "
                f"WinCons {power.win_condition_quality:.0f} | Quality {power.card_quality:.0f}[/dim]"
            )
            if power.reasons_up:
                for r in power.reasons_up[:3]:
                    console.print(f"    [green]+[/green] [dim]{r}[/dim]")
            if power.reasons_down:
                for r in power.reasons_down[:3]:
                    console.print(f"    [red]-[/red] [dim]{r}[/dim]")
        else:
            console.print("  [dim]Upgrade to Pro for full power level breakdown[/dim]")

        # Castability warnings (pro: all cards, free: top 2)
        if castability.unreliable_cards:
            limit = 5 if is_pro else 2
            console.print(f"\n  [yellow]Casting concerns ({len(castability.unreliable_cards)} cards):[/yellow]")
            for cc in castability.unreliable_cards[:limit]:
                console.print(
                    f"    [dim]{cc.name} ({cc.mana_cost}): "
                    f"{cc.on_curve_probability * 100:.0f}% on curve[/dim]"
                )
            if not is_pro and len(castability.unreliable_cards) > limit:
                console.print(f"    [dim]... +{len(castability.unreliable_cards) - limit} more (Pro)[/dim]")

        # Missing staples (pro: all, free: essentials only)
        if staples.missing:
            essentials = [s for s in staples.missing if s.priority == "essential"]
            if essentials:
                console.print(f"\n  [red]Missing essential staples:[/red]")
                for s in essentials:
                    console.print(f"    [dim]- {s.name}: {s.reason}[/dim]")
            if is_pro:
                recommended = [s for s in staples.missing if s.priority == "recommended"]
                if recommended:
                    console.print(f"  [yellow]Consider adding:[/yellow]")
                    for s in recommended[:5]:
                        console.print(f"    [dim]- {s.name}: {s.reason}[/dim]")

        console.print()

        # Probability layer (--deep) [PRO]
        if hasattr(args, "deep") and args.deep:
            if require_pro("deep_analysis"):
                console.print("[yellow]--deep requires Pro tier.[/yellow] [dim]Set MTG_ENGINE_TIER=pro to unlock.[/dim]")
            else:
                _run_and_render_probability(deck, args.sims)

        # Analyst (LLM layer) [PRO]
        analyst_output = None
        if hasattr(args, "with_llm") and args.with_llm:
            if require_pro("analyst"):
                console.print("[yellow]--with-llm requires Pro tier.[/yellow] [dim]Set MTG_ENGINE_TIER=pro to unlock.[/dim]")
            else:
                version_diff = None
                if getattr(args, "vs_previous", False):
                    version_diff = _load_prev_version_diff(
                        deck, result,
                        deck_id_override=getattr(args, "deck_id", None),
                    )
                combo_lines, protected_card_names = _collect_combo_context(deck)
                analyst_output = _run_analyst(
                    deck, result, power, adv, archetype.value,
                    seed=getattr(args, "llm_seed", 0),
                    playgroup_power=getattr(args, "playgroup_power", None),
                    version_diff=version_diff,
                    combo_lines=combo_lines,
                    protected_card_names=protected_card_names,
                )
                # Swaps are computed from the same analyst runner using the
                # rule-engine ranker + candidate DB — no new LLM call.
                swap_count = getattr(args, "swaps", 0) or 0
                if swap_count > 0:
                    from densa_deck.analyst import AnalystRunner
                    swap_runner = AnalystRunner(backend=_default_mock_analyst_backend())
                    analyst_output.swaps = swap_runner.run_swaps(
                        deck=deck, analysis=result, power=power, advanced=adv,
                        archetype=archetype.value, db=db,
                        format_name=(deck.format.value if deck.format else "commander"),
                        swap_count=swap_count,
                        protected_card_names=protected_card_names,
                    )
                _render_analyst(analyst_output)

        # Export [PRO]
        if hasattr(args, "export") and args.export:
            if require_pro("export_reports"):
                console.print("[yellow]--export requires Pro tier.[/yellow] [dim]Set MTG_ENGINE_TIER=pro to unlock.[/dim]")
            else:
                export_path = Path(args.export)
                adv_dict = {
                    "mana_base_grade": adv.mana_base_grade,
                    "mana_base_notes": adv.mana_base_notes,
                    "synergies": [{"card_a": s.card_a, "card_b": s.card_b, "reason": s.reason} for s in adv.synergies],
                    "advanced_recommendations": adv.advanced_recommendations,
                }
                if analyst_output is not None:
                    adv_dict["analyst_summary"] = analyst_output.summary
                    adv_dict["analyst_cuts"] = [
                        {
                            "card": c.card_name,
                            "reason": c.reason,
                            "signals": c.signals,
                        }
                        for c in analyst_output.cuts
                    ]
                export_kwargs = {
                    "power": power,
                    "castability": castability,
                    "staples": staples,
                }
                if export_path.suffix == ".json":
                    export_json(result, adv_dict, archetype.value, export_path, **export_kwargs)
                elif export_path.suffix == ".html":
                    export_html(result, adv_dict, archetype.value, export_path, **export_kwargs)
                else:
                    export_markdown(result, adv_dict, archetype.value, export_path, **export_kwargs)
                console.print(f"[green]Report exported to {export_path}[/green]")

    finally:
        db.close()


def cmd_search(args):
    db = _get_db(args)
    try:
        if db.card_count() == 0:
            console.print("[red]No cards in database. Run 'densa-deck ingest' first.[/red]")
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

        # Show tier and license info
        tier = get_user_tier()
        tier_label = "[bold green]Pro[/bold green]" if tier.value == "pro" else "[dim]Free[/dim]"

        console.print(Panel(
            f"[bold]Tier:[/bold] {tier_label}\n"
            f"[bold]Cards in database:[/bold] {count}\n"
            f"[bold]Database path:[/bold] {db.db_path}\n"
            f"[bold]Last ingest:[/bold] {last_ingest or 'Never'}",
            title="Densa Deck — Status",
        ))
    finally:
        db.close()


def cmd_license(args):
    """Manage Pro license activation."""
    from densa_deck.licensing import LICENSE_PATH, load_saved_license, remove_license, save_license

    action = getattr(args, "license_action", None)

    if action == "activate":
        result = save_license(args.key)
        if result.valid:
            label = "Master Key" if result.is_master else "Pro License"
            console.print(Panel(
                f"[bold green]{label} activated![/bold green]\n\n"
                f"[bold]Key:[/bold] {result.key}\n"
                f"[bold]Activated:[/bold] {result.activated_at[:19] if result.activated_at else 'now'}\n\n"
                f"[dim]Saved to {LICENSE_PATH}[/dim]",
                title="Densa Deck Pro",
                border_style="green",
            ))
        else:
            console.print(Panel(
                f"[bold red]License activation failed[/bold red]\n\n"
                f"[red]{result.error or 'Invalid key'}[/red]\n\n"
                f"[dim]If you purchased and believe this is an error, contact admin@densanon.com[/dim]",
                title="License Error",
                border_style="red",
            ))
            sys.exit(1)

    elif action == "show":
        license = load_saved_license()
        if license is None:
            console.print(Panel(
                "[dim]No license installed.[/dim]\n\n"
                "Purchase Pro at [bold]toolkit.densanon.com/densa-deck.html[/bold]\n"
                "Then run [bold]densa-deck license activate KEY[/bold]",
                title="License Status",
            ))
        elif license.valid:
            label = "Master Key" if license.is_master else "Pro License"
            console.print(Panel(
                f"[bold]Status:[/bold] [bold green]ACTIVE[/bold green]\n"
                f"[bold]Type:[/bold] {label}\n"
                f"[bold]Key:[/bold] {license.key}\n"
                f"[bold]Activated:[/bold] {license.activated_at[:19] if license.activated_at else 'unknown'}",
                title="Densa Deck Pro License",
                border_style="green",
            ))
        else:
            console.print(Panel(
                f"[red]License invalid: {license.error}[/red]\n\n"
                f"Run [bold]densa-deck license remove[/bold] then re-activate.",
                title="License Error",
                border_style="red",
            ))

    elif action == "remove":
        if remove_license():
            console.print("[yellow]License removed.[/yellow] You are now on the free tier.")
        else:
            console.print("[dim]No license to remove.[/dim]")

    else:
        console.print("Usage: densa-deck license [activate KEY | show | remove]")
        sys.exit(1)


def cmd_probability(args):
    """Dedicated probability analysis command."""
    from densa_deck.deck.parser import parse_auto
    from densa_deck.deck.resolver import resolve_deck

    db = _get_db(args)
    try:
        if db.card_count() == 0:
            console.print("[red]No cards in database. Run 'densa-deck ingest' first.[/red]")
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
                title="[bold cyan]Densa Deck — Probability Analysis[/bold cyan]",
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
    from densa_deck.deck.parser import parse_auto
    from densa_deck.deck.resolver import resolve_deck
    from densa_deck.goldfish.runner import run_goldfish_batch

    db = _get_db(args)
    try:
        if db.card_count() == 0:
            console.print("[red]No cards in database. Run 'densa-deck ingest' first.[/red]")
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
                title="[bold cyan]Densa Deck — Goldfish Simulation[/bold cyan]",
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
    from densa_deck.benchmarks.suites import get_suite, list_suites
    from densa_deck.deck.parser import parse_auto
    from densa_deck.deck.resolver import resolve_deck
    from densa_deck.matchup.gauntlet import run_gauntlet

    db = _get_db(args)
    try:
        if db.card_count() == 0:
            console.print("[red]No cards in database. Run 'densa-deck ingest' first.[/red]")
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
                title="[bold cyan]Densa Deck — Meta Gauntlet[/bold cyan]",
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
    from densa_deck.analysis.static import analyze_deck
    from densa_deck.deck.parser import parse_auto
    from densa_deck.deck.resolver import resolve_deck
    from densa_deck.versioning.storage import VersionStore

    db = _get_db(args)
    try:
        if db.card_count() == 0:
            console.print("[red]No cards in database. Run 'densa-deck ingest' first.[/red]")
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
    from densa_deck.versioning.impact import analyze_impact
    from densa_deck.versioning.storage import VersionStore, diff_versions

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
    from densa_deck.versioning.storage import VersionStore
    from densa_deck.versioning.trends import analyze_trends

    store = VersionStore()
    try:
        if args.deck_id is None:
            # List all decks
            decks = store.list_decks()
            if not decks:
                console.print("[yellow]No saved decks found. Use 'densa-deck save' to save a deck.[/yellow]")
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


def cmd_calc(args):
    """Standalone hypergeometric calculator."""
    from densa_deck.probability.hypergeometric import cards_seen_by_turn, prob_card_by_turn

    N = args.deck
    K = args.copies
    on_play = not args.draw

    if N <= 0:
        console.print("[red]Deck size must be positive.[/red]")
        sys.exit(1)
    if K < 0 or K > N:
        console.print(f"[red]Copies must be between 0 and deck size ({N}).[/red]")
        sys.exit(1)
    if args.turns <= 0:
        console.print("[red]Turns must be positive.[/red]")
        sys.exit(1)

    console.print(Panel(
        f"[bold]Deck Size:[/bold] {N}  |  [bold]Copies:[/bold] {K}  |  "
        f"[bold]On the {'draw' if args.draw else 'play'}[/bold]",
        title="[bold cyan]Hypergeometric Calculator[/bold cyan]",
        border_style="cyan",
    ))

    table = Table(show_header=True, header_style="bold")
    table.add_column("Turn", justify="center", width=6)
    table.add_column("Cards Seen", justify="right", width=10)
    table.add_column("P(at least 1)", justify="right", width=13)
    table.add_column("", min_width=20)

    for turn in range(1, args.turns + 1):
        seen = min(cards_seen_by_turn(turn, on_play), N)
        p = prob_card_by_turn(K, N, turn, on_play)
        pct = f"{p * 100:.1f}%"
        bar_len = int(p * 20)
        bar = "█" * bar_len
        color = "green" if p >= 0.75 else "yellow" if p >= 0.5 else "dim"
        table.add_row(str(turn), str(seen), f"[{color}]{pct}[/{color}]", f"[{color}]{bar}[/{color}]")

    console.print(table)


def cmd_diff(args):
    """Compare two different decks."""
    from densa_deck.analysis.deck_diff import compare_decks
    from densa_deck.deck.parser import parse_auto
    from densa_deck.deck.resolver import resolve_deck

    db = _get_db(args)
    try:
        if db.card_count() == 0:
            console.print("[red]No cards in database. Run 'densa-deck ingest' first.[/red]")
            sys.exit(1)

        for f in (args.file_a, args.file_b):
            if not Path(f).exists():
                console.print(f"[red]File not found: {f}[/red]")
                sys.exit(1)

        fmt = Format(args.format) if args.format else None

        text_a = Path(args.file_a).read_text(encoding="utf-8")
        text_b = Path(args.file_b).read_text(encoding="utf-8")
        deck_a = resolve_deck(parse_auto(text_a), db, name=Path(args.file_a).stem, format=fmt)
        deck_b = resolve_deck(parse_auto(text_b), db, name=Path(args.file_b).stem, format=fmt)

        comp = compare_decks(deck_a, deck_b)

        console.print(Panel(
            f"[bold]{comp.name_a}[/bold] vs [bold]{comp.name_b}[/bold]  |  "
            f"{comp.overlap_percentage:.0f}% card overlap",
            title="[bold cyan]Deck Comparison[/bold cyan]",
            border_style="cyan",
        ))

        # Score comparison
        if comp.score_deltas:
            table = Table(title="Score Comparison", show_header=True, header_style="bold")
            table.add_column("Category", width=18)
            table.add_column(comp.name_a, justify="right", width=8)
            table.add_column(comp.name_b, justify="right", width=8)
            table.add_column("Delta", justify="right", width=8)

            for key, delta in comp.score_deltas.items():
                sa = comp.result_a.scores.get(key, 0) if comp.result_a else 0
                sb = comp.result_b.scores.get(key, 0) if comp.result_b else 0
                name = key.replace("_", " ").title()
                color = "green" if delta > 3 else "red" if delta < -3 else "dim"
                table.add_row(name, f"{sa:.0f}", f"{sb:.0f}", f"[{color}]{delta:+.0f}[/{color}]")
            console.print(table)

        # Role comparison
        if comp.role_comparison:
            table = Table(title="Role Distribution", show_header=True, header_style="bold")
            table.add_column("Role", width=18)
            table.add_column(comp.name_a, justify="right", width=8)
            table.add_column(comp.name_b, justify="right", width=8)
            for role, (ca, cb) in comp.role_comparison.items():
                table.add_row(role.replace("_", " ").title(), str(ca), str(cb))
            console.print(table)

        # Advantages
        if comp.a_advantages:
            console.print(Panel(
                "\n".join(f"  + {a}" for a in comp.a_advantages),
                title=f"[green]{comp.name_a} Advantages[/green]", border_style="green",
            ))
        if comp.b_advantages:
            console.print(Panel(
                "\n".join(f"  + {a}" for a in comp.b_advantages),
                title=f"[green]{comp.name_b} Advantages[/green]", border_style="green",
            ))

        console.print(f"\n[dim]{DISCLAIMER}[/dim]\n")
    finally:
        db.close()


def cmd_practice(args):
    """Interactive mulligan practice mode."""
    import random
    from densa_deck.deck.parser import parse_auto
    from densa_deck.deck.resolver import resolve_deck
    from densa_deck.probability.opening_hand import evaluate_hand

    db = _get_db(args)
    try:
        if db.card_count() == 0:
            console.print("[red]No cards in database. Run 'densa-deck ingest' first.[/red]")
            sys.exit(1)

        file_path = Path(args.file)
        if not file_path.exists():
            console.print(f"[red]File not found: {file_path}[/red]")
            sys.exit(1)

        text = file_path.read_text(encoding="utf-8")
        fmt = Format(args.format) if args.format else None
        deck = resolve_deck(parse_auto(text), db, name=file_path.stem, format=fmt)

        # Build pool
        from densa_deck.models import Zone
        pool = []
        for entry in deck.entries:
            if entry.zone in (Zone.MAYBEBOARD, Zone.SIDEBOARD):
                continue
            for _ in range(entry.quantity):
                pool.append(entry)

        if len(pool) < 7:
            console.print("[red]Deck too small for practice.[/red]")
            return

        console.print(Panel(
            f"[bold]{deck.name}[/bold] — Mulligan Practice ({args.rounds} rounds)\n"
            f"For each hand, type [bold]k[/bold] to keep or [bold]m[/bold] to mulligan.",
            title="[bold blue]Mulligan Practice[/bold blue]",
            border_style="blue",
        ))

        correct = 0
        total = 0

        for round_num in range(1, args.rounds + 1):
            shuffled = pool.copy()
            random.shuffle(shuffled)
            hand = shuffled[:7]

            console.print(f"\n[bold]--- Round {round_num}/{args.rounds} ---[/bold]")
            table = Table(show_header=False)
            table.add_column("Card", width=30)
            table.add_column("Type", width=20)
            table.add_column("CMC", justify="right", width=4)

            lands = 0
            for entry in hand:
                card = entry.card
                if card:
                    if card.is_land:
                        lands += 1
                    table.add_row(
                        card.name,
                        card.type_line[:20] if card.type_line else "",
                        str(int(card.cmc)) if not card.is_land else "",
                    )
            console.print(table)
            console.print(f"  [dim]({lands} lands, {7 - lands} spells)[/dim]")

            # Engine evaluation
            ev = evaluate_hand(hand, deck)
            engine_keep = ev.keepable

            # Get user input
            try:
                answer = input("  Keep (k) or Mulligan (m)? ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Practice ended.[/dim]")
                break

            user_keep = answer.startswith("k")
            total += 1

            if user_keep == engine_keep:
                correct += 1
                console.print(f"  [green]Correct![/green] Engine score: {ev.score:.0f}/100 ({ev.archetype.value})")
            else:
                engine_decision = "KEEP" if engine_keep else "MULL"
                console.print(
                    f"  [red]Engine says {engine_decision}[/red] — "
                    f"score: {ev.score:.0f}/100 ({ev.archetype.value})"
                )

        if total > 0:
            pct = correct / total * 100
            console.print(f"\n[bold]Results: {correct}/{total} ({pct:.0f}%) agreement with engine[/bold]")

    finally:
        db.close()


def cmd_app(args):
    """Launch the desktop GUI. Optional dependency (`pip install .[desktop]`).

    If invoked with a `densa-deck://activate?key=...` URL as the positional
    arg (via the OS's URI scheme handler), auto-activates that key before
    showing the window. Any errors at activation surface as a toast inside
    the app, not a CLI crash.
    """
    try:
        from densa_deck.app.main import run as app_run
    except ImportError as e:
        console.print(f"[red]Failed to import app module: {e}[/red]")
        console.print("[dim]Install with: pip install 'densa-deck[desktop]'[/dim]")
        sys.exit(1)

    activation_url = getattr(args, "activation_url", None)
    if activation_url:
        _handle_activation_url(activation_url)

    app_run(debug=getattr(args, "debug", False))


def _handle_activation_url(url: str):
    """Parse a densa-deck:// URL and activate a license if one is present."""
    from urllib.parse import parse_qs, urlparse
    try:
        parsed = urlparse(url)
        # Valid URLs look like densa-deck://activate?key=MTG-XXXX-XXXX-XXXX.
        # Anything else (wrong scheme, missing key) is a no-op — we prefer
        # a quiet failure so a bogus deep-link can't block the app launch.
        if parsed.scheme != "densa-deck":
            return
        action = parsed.netloc or parsed.path.lstrip("/").split("/")[0]
        if action != "activate":
            return
        params = parse_qs(parsed.query or "")
        key = (params.get("key") or [None])[0]
        if not key:
            return
        from densa_deck.licensing import save_license
        result = save_license(key)
        if result.valid:
            console.print(f"[green]Activated Pro license from deep link.[/green]")
        else:
            console.print(f"[yellow]Deep-link activation failed — invalid key.[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Deep-link parse failed ({e}); launching normally.[/yellow]")


def cmd_mcp(args):
    """Run the Densa Deck MCP server on stdio.

    `densa-deck mcp serve` is the entry point — AI clients (Claude desktop,
    ulcagent, Cursor) launch this as a subprocess and talk JSON-RPC over
    the pipes. Free-tier tools (analyze, search, combos, version history)
    are always exposed; Pro tools (goldfish, gauntlet, analyst, coach) are
    license-gated via tiers.get_user_tier() — same gate the desktop UI
    uses, so a Pro license unlocks all three surfaces from one activation.

    `--read-only` skips registering Pro tools entirely (defense-in-depth
    for users who want to expose the server to a less-trusted agent).
    """
    action = getattr(args, "mcp_action", None)
    if action != "serve":
        console.print(
            "[yellow]Usage: densa-deck mcp serve [--read-only][/yellow]\n"
            "[dim]Add this server to your AI client's MCP config to drive "
            "the engine via tool calls.[/dim]"
        )
        return
    # Lazy import — keeps the rest of the CLI importable even when the
    # optional `mcp` SDK isn't installed.
    from densa_deck.mcp.server import McpSdkMissingError, run_stdio_server
    try:
        run_stdio_server(read_only=getattr(args, "read_only", False))
    except McpSdkMissingError as e:
        # SDK missing is a setup gap, not a runtime crash — show the
        # install hint cleanly and exit nonzero so scripts can detect it.
        console.print(f"[yellow]{e}[/yellow]")
        sys.exit(1)


def cmd_register_protocol(args):
    """Register (or unregister) the densa-deck:// URI scheme on Windows.

    Writes to HKCU\\Software\\Classes\\densa-deck so it's per-user and doesn't
    require admin. On non-Windows, prints a platform note — Linux/Mac desktop
    file registration can be added later.
    """
    if sys.platform != "win32":
        console.print(
            "[yellow]Protocol registration is Windows-only for now.[/yellow]\n"
            "[dim]On Linux, create a .desktop file with MimeType=x-scheme-handler/densa-deck. "
            "On Mac, use LSSetDefaultHandlerForURLScheme.[/dim]"
        )
        return

    try:
        import winreg
    except ImportError:
        console.print("[red]winreg not available — can't register the protocol.[/red]")
        return

    exe = sys.executable
    # Invoke through `python -m densa_deck app "<url>"` so we don't need
    # to know the installed shortcut path. In the PyInstaller bundle the
    # packaged exe will substitute this during installer-time registration.
    module_cmd = f'"{exe}" -m densa_deck app "%1"'

    base_path = r"Software\Classes\densa-deck"
    shell_cmd_path = base_path + r"\shell\open\command"

    if args.unregister:
        try:
            # winreg has no recursive delete; walk the tree.
            for sub in (r"\shell\open\command", r"\shell\open", r"\shell", ""):
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, base_path + sub)
                except FileNotFoundError:
                    pass
            console.print("[green]Protocol unregistered.[/green]")
        except OSError as e:
            console.print(f"[red]Unregister failed: {e}[/red]")
        return

    try:
        root = winreg.CreateKey(winreg.HKEY_CURRENT_USER, base_path)
        winreg.SetValueEx(root, "", 0, winreg.REG_SZ, "URL:Densa Deck Protocol")
        winreg.SetValueEx(root, "URL Protocol", 0, winreg.REG_SZ, "")
        winreg.CloseKey(root)

        cmd_key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, shell_cmd_path)
        winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, module_cmd)
        winreg.CloseKey(cmd_key)

        console.print(
            f"[green]Registered densa-deck:// -> {module_cmd}[/green]\n"
            "[dim]Test with: start densa-deck://activate?key=MTG-TEST-TEST-TEST[/dim]"
        )
    except OSError as e:
        console.print(f"[red]Registration failed: {e}[/red]")


def cmd_coach(args):
    """Interactive deck coach REPL.

    Loads the deck, runs analysis + power-level + archetype detection, builds
    a CoachSession seeded with a PIE-style knowledge sheet, then loops:
    user types a question, the LLM answers using only the sheet's facts and
    the allowlist of deck cards. Exit with /quit or EOF.

    Pro-gated. Requires the analyst model to be installed (`densa-deck analyst
    pull qwen2.5-3b`) and MTG_ANALYST_BACKEND=llama_cpp in the env, otherwise
    falls back to the placeholder mock backend (useful for smoke-testing but
    not for real coaching).
    """
    from pathlib import Path

    from densa_deck.analysis.power_level import estimate_power_level
    from densa_deck.analysis.static import analyze_deck
    from densa_deck.analyst.coach import CoachSession, build_deck_sheet, coach_step
    from densa_deck.deck.parser import parse_auto
    from densa_deck.deck.resolver import resolve_deck
    from densa_deck.formats.profiles import detect_archetype

    file_path = Path(args.file)
    if not file_path.exists():
        console.print(f"[red]File not found: {file_path}[/red]")
        sys.exit(1)

    db = _get_db(args)
    try:
        if db.card_count() == 0:
            console.print("[red]No cards in database. Run 'densa-deck ingest' first.[/red]")
            sys.exit(1)

        text = file_path.read_text(encoding="utf-8")
        deck_name = args.name or file_path.stem
        entries = parse_auto(text)
        fmt = Format(args.format) if args.format else None
        deck = resolve_deck(entries, db, name=deck_name, format=fmt)

        # Compute the deck sheet inputs
        result = analyze_deck(deck)
        power = estimate_power_level(deck)
        archetype = detect_archetype(deck)

        color_identity = sorted({
            c.value for e in deck.entries if e.card for c in e.card.color_identity
        })
        deck_cards = [e.card.name for e in deck.entries if e.card]
        if not deck_cards:
            console.print(
                "[red]No valid cards resolved from the decklist.[/red] "
                "[dim]Check that the file has real card names and the Scryfall "
                "database is ingested (`densa-deck ingest`).[/dim]"
            )
            sys.exit(1)

        sheet = build_deck_sheet(
            deck_name=deck.name,
            archetype=archetype.value if hasattr(archetype, "value") else str(archetype),
            color_identity=color_identity,
            power_overall=power.overall,
            power_tier=power.tier,
            land_count=result.land_count,
            ramp_count=result.ramp_count,
            draw_count=result.draw_engine_count,
            interaction_count=result.interaction_count,
            avg_mana_value=result.average_cmc,
            deck_cards=deck_cards,
            reasons_up=list(power.reasons_up),
            reasons_down=list(power.reasons_down),
        )

        session = CoachSession(deck_sheet=sheet, allowed_cards=set(deck_cards))

        # Select a backend — mirror the analyst path
        backend = _pick_analyst_backend(seed=getattr(args, "llm_seed", 0))

        console.print(Panel(
            f"[bold]Coach session loaded for {deck.name}[/bold]\n"
            f"Power {power.overall:.1f}/10 ({power.tier}) — archetype: {archetype}\n\n"
            f"[dim]Ask questions about your deck. Type /quit or Ctrl-D to exit.[/dim]",
            border_style="cyan",
        ))

        while True:
            try:
                question = input("\nyou> ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]session ended[/dim]")
                break
            if not question:
                continue
            if question.lower() in ("/quit", "/exit", "/q"):
                console.print("[dim]session ended[/dim]")
                break

            turn = coach_step(session, backend, question, max_retries=1)
            if turn.verified:
                console.print(f"[cyan]coach[/cyan]> {turn.assistant_response}")
            else:
                console.print(
                    "[yellow]coach[/yellow]> "
                    "I couldn't generate a confident answer. Try rephrasing or "
                    "asking about a specific axis (ramp, draw, interaction, curve)."
                )
    finally:
        db.close()


def _pick_analyst_backend(seed: int = 42):
    """Shared backend selector used by `analyze --with-llm` and `coach`.

    Reads MTG_ANALYST_BACKEND; falls back to mock when llama-cpp can't load.
    Extracted so the coach and analyze paths stay in sync.
    """
    import os
    backend_name = os.environ.get("MTG_ANALYST_BACKEND", "mock").lower().strip()
    if backend_name in ("llama", "llama_cpp", "llamacpp"):
        try:
            from densa_deck.analyst.backends.llama_cpp import LlamaCppBackend
            backend = LlamaCppBackend(seed=seed)
            if not backend.is_available():
                console.print(
                    f"[yellow]Analyst model not found at {backend.model_path}; "
                    "falling back to mock. Run `densa-deck analyst pull` to install one.[/yellow]"
                )
                return _default_mock_analyst_backend()
            return backend
        except Exception as e:
            console.print(f"[yellow]llama-cpp backend unavailable ({e}); falling back to mock.[/yellow]")
            return _default_mock_analyst_backend()
    return _default_mock_analyst_backend()


def cmd_analyst(args):
    """Manage the local GGUF model used by the LLM analyst layer.

    Subcommands:
      - pull [qwen2.5-0.5b | qwen2.5-3b]: download and install the model
      - show: print the model path + whether it's currently available
    """
    from densa_deck.analyst.backends.llama_cpp import (
        DEFAULT_MODEL_PATH, LlamaCppBackend,
    )

    action = getattr(args, "analyst_action", None)

    if action == "show" or action is None:
        backend = LlamaCppBackend()
        status = "[green]ready[/green]" if backend.is_available() else "[yellow]not installed[/yellow]"
        # Also report llama_cpp importability so users (and the frozen-exe
        # smoke test) can tell "file missing" from "library failed to load".
        try:
            import llama_cpp  # noqa: F401
            lib_status = "[green]importable[/green]"
        except Exception as lib_err:
            lib_status = f"[red]import failed: {lib_err}[/red]"
        console.print(Panel(
            f"[bold]Path:[/bold] {backend.model_path}\n"
            f"[bold]File status:[/bold] {status}\n"
            f"[bold]llama-cpp-python:[/bold] {lib_status}\n\n"
            f"[dim]Run `densa-deck analyst pull` to install the default model.[/dim]",
            title="MTG Analyst Model",
            border_style="cyan",
        ))
        return

    if action == "pull":
        model_key = getattr(args, "model", "qwen2.5-3b")
        _pull_analyst_model(model_key)
        return

    console.print("[yellow]Unknown analyst action. Use `pull` or `show`.[/yellow]")


# Hugging Face download URLs for the default GGUF models. Using bartowski's
# GGUF repositories which serve a stable Q4_K_M quant at a consistent URL.
_ANALYST_MODELS = {
    "qwen2.5-0.5b": {
        "url": "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "size_mb": 400,
        "filename": "qwen2.5-0.5b-instruct-q4_k_m.gguf",
    },
    "qwen2.5-3b": {
        "url": "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf",
        "size_mb": 1800,
        "filename": "qwen2.5-3b-instruct-q4_k_m.gguf",
    },
}


def _pull_analyst_model(model_key: str):
    """Download a model to ~/.densa-deck/models/ and symlink as analyst.gguf."""
    from densa_deck.analyst.backends.llama_cpp import DEFAULT_MODEL_PATH
    import shutil
    import urllib.request

    spec = _ANALYST_MODELS.get(model_key)
    if spec is None:
        console.print(f"[red]Unknown model: {model_key}[/red]")
        return

    dest_dir = DEFAULT_MODEL_PATH.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / spec["filename"]

    if dest_file.exists():
        console.print(f"[dim]Already downloaded: {dest_file}[/dim]")
    else:
        console.print(
            f"[cyan]Downloading {model_key} (~{spec['size_mb']} MB) to {dest_file}...[/cyan]"
        )
        try:
            # urlretrieve is simple; for nicer UX we could wire Rich Progress here.
            urllib.request.urlretrieve(spec["url"], dest_file)
        except Exception as e:
            console.print(f"[red]Download failed: {e}[/red]")
            if dest_file.exists():
                dest_file.unlink()
            return

    # Point the default path at this download so LlamaCppBackend picks it up
    # without the user setting MTG_ANALYST_MODEL.
    if DEFAULT_MODEL_PATH != dest_file:
        if DEFAULT_MODEL_PATH.exists() or DEFAULT_MODEL_PATH.is_symlink():
            DEFAULT_MODEL_PATH.unlink()
        try:
            # Prefer a symlink on systems that allow it; fall back to a copy.
            DEFAULT_MODEL_PATH.symlink_to(dest_file)
        except (OSError, NotImplementedError):
            shutil.copy2(dest_file, DEFAULT_MODEL_PATH)

    console.print(f"[green]Analyst model installed at {DEFAULT_MODEL_PATH}[/green]")
    console.print(
        "[dim]Set MTG_ANALYST_BACKEND=llama_cpp to use it. "
        "`densa-deck analyze <deck> --with-llm` now emits LLM-backed summary + cuts.[/dim]"
    )


def _load_prev_version_diff(deck, result, deck_id_override: str | None = None) -> dict | None:
    """Build a version-diff dict for the analyst prompt from the versions DB.

    Lookup strategy (first hit wins):
      1. If `deck_id_override` is given (from --deck-id), use it directly.
         This matches the `save` subcommand's key exactly.
      2. Otherwise, try `deck.name` as the deck_id — works when save was
         invoked with `deck_id == deck_name`.
      3. As a fallback, scan `store.list_decks()` for any saved deck whose
         `name` field matches `deck.name` (case-insensitive). Handles the
         common case where save used a different deck_id string but the
         deck name is unique across the user's saved decks.

    Returns None (with a warning) if nothing matches — the analyst then
    renders without the version block.
    """
    try:
        from densa_deck.versioning.storage import DeckSnapshot, VersionStore, diff_versions
    except Exception:
        return None
    store = VersionStore()

    prev = None
    if deck_id_override:
        prev = store.get_latest(deck_id_override)
    if prev is None:
        prev = store.get_latest(deck.name)
    if prev is None:
        # Fallback: name-match across saved decks. save uses deck_id as the
        # primary key but stores deck.name separately, so we can still find
        # the deck even if the user used a different deck_id.
        try:
            candidates = [
                d for d in store.list_decks()
                if (d.get("name") or "").lower() == deck.name.lower()
            ]
        except Exception:
            candidates = []
        if len(candidates) == 1:
            prev = store.get_latest(candidates[0]["deck_id"])
        elif len(candidates) > 1:
            ids = ", ".join(c["deck_id"] for c in candidates[:3])
            console.print(
                f"[yellow]--vs-previous: multiple saved decks match name "
                f"'{deck.name}' ({ids}). Pass --deck-id to disambiguate.[/yellow]"
            )
            return None

    if prev is None:
        console.print(
            "[yellow]--vs-previous: no prior saved version for this deck; skipping.[/yellow] "
            "[dim]Run `densa-deck save <deck> <deck_id>` first, or pass --deck-id.[/dim]"
        )
        return None

    # Build a transient snapshot for the current deck state so diff_versions
    # can produce adds/removes/score-delta. We don't persist it; this is just
    # for the diff math.
    decklist: dict[str, int] = {}
    zones: dict[str, list[str]] = {}
    for entry in deck.entries:
        name = entry.card_name
        decklist[name] = decklist.get(name, 0) + entry.quantity
        zone_key = entry.zone.value if hasattr(entry.zone, "value") else str(entry.zone)
        zones.setdefault(zone_key, []).append(name)
    current = DeckSnapshot(
        deck_id=deck.name, version_number=0,
        decklist=decklist, zones=zones,
        scores=dict(result.scores or {}),
    )
    diff = diff_versions(prev, current)
    return {
        "added": dict(diff.added),
        "removed": dict(diff.removed),
        "score_deltas": dict(diff.score_deltas),
    }


def _collect_combo_context(deck):
    """Detect combos in `deck` and return (combo_lines, protected_card_names).

    Reads the local Commander Spellbook cache. Returns ([], None) if the
    cache is empty or detection fails — combo awareness is opt-in and never
    blocks an analyze run. `protected_card_names` is None (not an empty
    set) when there are no combos, so the analyst's cut ranker keeps its
    default no-protection branch instead of receiving an empty filter.
    """
    try:
        from densa_deck.combos import ComboStore, detect_combos
    except Exception:
        return [], None
    try:
        store = ComboStore()
        if store.combo_count() <= 0:
            return [], None
        deck_card_names = [e.card.name for e in deck.entries if e.card]
        deck_color_identity = sorted({
            c.value for e in deck.entries if e.card for c in e.card.color_identity
        })
        matches = detect_combos(
            store=store,
            deck_card_names=deck_card_names,
            deck_color_identity=deck_color_identity,
            limit=8,
        )
        if not matches:
            return [], None
        combo_lines = [m.combo.short_label() for m in matches]
        # `rank_cut_candidates` compares with `card.name.lower() in protected`,
        # so the set must hold lowercase names.
        protected: set[str] = set()
        for m in matches:
            for name in m.in_deck_cards:
                protected.add(name.lower())
        return combo_lines, protected
    except Exception:
        return [], None


def _run_analyst(
    deck, result, power, adv, archetype_value: str,
    seed: int = 42,
    playgroup_power: float | None = None,
    version_diff: dict | None = None,
    combo_lines: list[str] | None = None,
    protected_card_names: set[str] | None = None,
):
    """Run the LLM analyst layer. Picks backend from MTG_ANALYST_BACKEND env var.

    Supported values:
      - "mock" (default): deterministic placeholder output. Used when no
        GGUF model is present, for CI, and for gauntlet runs.
      - "llama" / "llama_cpp": in-process llama-cpp-python with a local GGUF
        at ~/.densa-deck/models/analyst.gguf (override via MTG_ANALYST_MODEL).

    Args:
      seed: Forwarded to LlamaCppBackend for reproducible generation. Same seed
        + same prompt = same output. Useful for "show me the same advice again"
        vs. bumping the seed to get a fresh take.
      playgroup_power: Optional 1-10 power target for the user's playgroup.
        Threads into the exec-summary prompt so the narration can frame the
        deck's power relative to the table (e.g. "sits at 7, your table
        targets 6 — this deck will over-pitch").
    """
    import os
    backend_name = os.environ.get("MTG_ANALYST_BACKEND", "mock").lower().strip()
    if backend_name in ("llama", "llama_cpp", "llamacpp"):
        try:
            from densa_deck.analyst.backends.llama_cpp import LlamaCppBackend
            backend = LlamaCppBackend(seed=seed)
            if not backend.is_available():
                console.print(
                    f"[yellow]Analyst model not found at {backend.model_path}; "
                    "falling back to mock. Place a GGUF file at that path or "
                    "set MTG_ANALYST_MODEL.[/yellow]"
                )
                backend = _default_mock_analyst_backend()
        except Exception as e:
            console.print(f"[yellow]llama-cpp backend unavailable ({e}); falling back to mock.[/yellow]")
            backend = _default_mock_analyst_backend()
    else:
        backend = _default_mock_analyst_backend()

    from densa_deck.analyst import AnalystRunner
    runner = AnalystRunner(backend=backend)
    return runner.run(
        deck=deck, analysis=result, power=power, advanced=adv,
        archetype=archetype_value, format_name=(deck.format.value if deck.format else "commander"),
        playgroup_power=playgroup_power,
        version_diff=version_diff,
        combo_lines=combo_lines,
        protected_card_names=protected_card_names,
    )


def _default_mock_analyst_backend():
    """Mock backend emits structured placeholder text until PIE is wired.

    Summary is deterministic prose; cuts reference c01/c02 — which are always
    the highest-ranked candidates, so the verifier will pass (both tags are
    in whatever candidate table the runner produced).
    """
    from densa_deck.analyst import MockBackend
    return MockBackend(scripts=[
        (
            "[INPUT]",
            (
                "This deck's shape tracks its numbers — the land count, ramp "
                "package, and curve are each within the commander target range, "
                "so the engine is there. The rule-engine recommendations below "
                "call out where the shape slips.\n\n"
                "The biggest lever is the gap between the deck's threat density "
                "and its interaction density. Bring interaction closer to the "
                "target range and the deck should convert its stronger starts "
                "into closed games more reliably."
            ),
        ),
        (
            "suggesting cuts",
            "[c01]: flagged by the rule engine on redundancy and curve.\n"
            "[c02]: weakest slot among the surfaced candidates.",
        ),
    ], default="")


def _render_analyst(analyst_output):
    """Render analyst output in the CLI.

    Distinguishes three states for the cuts section so the user knows when
    something genuinely went wrong vs. when the model simply didn't find
    anything with enough confidence:
      1. Cuts present -> normal happy path.
      2. Verified + empty -> the ranker surfaced no candidates worth showing
         (rare; only happens when the deck has no structural cut candidates).
      3. Unverified -> the model tried, retries exhausted; show the low
         confidence explicitly so the user isn't staring at a blank block.
    """
    from rich.panel import Panel

    # Summary section
    if analyst_output.summary_verified:
        console.print(Panel(
            analyst_output.summary,
            title=f"Analyst Summary (confidence {analyst_output.summary_confidence:.0%})",
            border_style="cyan",
        ))
    else:
        console.print(
            "[yellow]Analyst summary not confidently generated after retries — "
            "skipping summary. The structured numeric analysis above is authoritative.[/yellow]"
        )

    # Cuts section
    if analyst_output.cuts:
        console.print(
            f"\n[bold cyan]Analyst cut suggestions[/bold cyan] "
            f"(confidence {analyst_output.cuts_confidence:.0%})"
        )
        for cut in analyst_output.cuts:
            console.print(f"  [dim]- {cut.card_name}:[/dim] {cut.reason}")
    elif analyst_output.raw_cuts and not analyst_output.cuts_verified:
        console.print(
            "\n[yellow]No high-confidence cut suggestions at this time.[/yellow] "
            "[dim]The model tried but its output didn't pass verification "
            "after 2 retries. Run with a larger model (MTG_ANALYST_MODEL) "
            "or review the rule-engine recommendations above.[/dim]"
        )
    elif analyst_output.raw_cuts is None:
        # Cuts weren't requested this run — say nothing.
        pass

    # Swap suggestions (paired cut + add at the same role)
    if analyst_output.swaps:
        console.print("\n[bold cyan]Analyst swap suggestions[/bold cyan]")
        for s in analyst_output.swaps:
            console.print(
                f"  [dim]- cut[/dim] [red]{s.cut_card}[/red] "
                f"[dim]-> add[/dim] [green]{s.add_card}[/green] "
                f"[dim]({s.role})[/dim]"
            )
            console.print(f"    [dim]{s.rationale}[/dim]")


def _run_and_render_probability(deck, sims: int = 10000, card_names: list[str] | None = None):
    """Run all probability analyses and render results."""
    from densa_deck.probability.key_cards import analyze_card_access, analyze_role_access
    from densa_deck.probability.mana_development import analyze_mana_development
    from densa_deck.probability.opening_hand import simulate_opening_hands

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
            title="[bold cyan]Densa Deck — Static Analysis[/bold cyan]",
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


# =============================================================================
# Phase 6 + Combos (v0.2.x integration commands)
# =============================================================================


def _read_deck_text(arg) -> str:
    """Pull a decklist from a file path or stdin (`-` / no arg)."""
    if arg is None or arg == "-":
        return sys.stdin.read()
    return Path(arg).read_text(encoding="utf-8")


def cmd_combos(args):
    """Subcommands: refresh / status / detect."""
    from densa_deck.combos import ComboStore, refresh_combo_snapshot, detect_combos

    action = getattr(args, "combos_action", None)
    db_path = getattr(args, "db", None)
    store = ComboStore(db_path=db_path) if db_path else ComboStore()

    if action == "status":
        count = store.combo_count()
        last = store.get_metadata("last_refresh_at") or "(never)"
        console.print(f"[cyan]Combo cache:[/cyan] {count} combos, last refresh {last}")
        if count == 0:
            console.print("[yellow]Run `densa-deck combos refresh` to populate.[/yellow]")
        return

    if action == "refresh":
        console.print("[cyan]Fetching Commander Spellbook combo dataset...[/cyan]")
        with console.status("[bold green]walking /variants/...") as status:
            def _on_page(pages: int, seen: int):
                status.update(f"[bold green]page {pages} — {seen} combos")
            written = refresh_combo_snapshot(store=store, progress_cb=_on_page)
        console.print(f"[green]Done — {written} combos cached.[/green]")
        return

    if action == "density":
        if store.combo_count() == 0:
            console.print(
                "[yellow]Combo cache empty. Run `densa-deck combos refresh` first.[/yellow]"
            )
            return
        text = _read_deck_text(args.deck)
        if not text.strip():
            console.print("[red]No deck text provided.[/red]")
            return
        from densa_deck.analysis.brackets import _bracket_for_power
        from densa_deck.analysis.power_level import estimate_power_level
        from densa_deck.combos import detect_combos, detect_near_miss_combos
        from densa_deck.deck.parser import parse_decklist
        from densa_deck.deck.resolver import resolve_deck
        from densa_deck.models import Format

        db = _get_db(args)
        if db.card_count() == 0:
            console.print("[yellow]Card database empty — run `densa-deck ingest` first.[/yellow]")
            return
        try:
            fmt = Format(args.format)
        except ValueError:
            fmt = Format.COMMANDER
        entries = parse_decklist(text)
        deck = resolve_deck(entries, db, format=fmt)
        deck_card_names = [e.card.name for e in deck.entries if e.card]
        deck_color_identity = sorted({
            c.value for e in deck.entries if e.card for c in e.card.color_identity
        })

        matches = detect_combos(
            store=store, deck_card_names=deck_card_names,
            deck_color_identity=deck_color_identity, limit=50,
        )
        near = detect_near_miss_combos(
            store=store, deck_card_names=deck_card_names,
            deck_color_identity=deck_color_identity, max_missing=1, limit=25,
        )
        # Power + bracket implication — same baseline detect_deck_brackets
        # uses, but with the combo count fed in.
        power = estimate_power_level(deck, detected_combo_count=len(matches))
        bracket_label, _ = _bracket_for_power(power.overall), None
        # _bracket_for_power returns (label, name). Above tuple unpacks
        # to "label" only; tweak to actually capture both.
        bracket_label, bracket_name = _bracket_for_power(power.overall)

        console.print(f"[bold]Combo density summary — {deck.name or '(unnamed)'}[/bold]")
        console.print(f"  Detected combo lines: [bold]{len(matches)}[/bold]")
        console.print(f"  1-card-away near-misses: [bold]{len(near)}[/bold]")
        console.print(
            f"  Power w/ combos: [bold]{power.overall:.1f}/10[/bold] ({power.tier})")
        console.print(
            f"  Reads as bracket: [bold]{bracket_label}[/bold] ({bracket_name})")
        if matches:
            console.print()
            console.print("[bold]Top detected combos:[/bold]")
            for m in matches[:5]:
                console.print(f"  - {m.combo.short_label()}")
        if near:
            console.print()
            console.print("[bold]1-card-away combos (high-leverage adds):[/bold]")
            for n in near[:5]:
                missing = ", ".join(n.missing_cards)
                console.print(f"  - {n.combo.short_label()}  [dim](add: {missing})[/dim]")
        if not matches and not near:
            console.print()
            console.print("[dim]No combos detected and nothing within 1 card. The deck doesn't appear combo-shaped.[/dim]")
        return

    if action == "near-miss":
        if store.combo_count() == 0:
            console.print(
                "[yellow]Combo cache empty. Run `densa-deck combos refresh` first.[/yellow]"
            )
            return
        text = _read_deck_text(args.deck)
        if not text.strip():
            console.print("[red]No deck text provided.[/red]")
            return
        from densa_deck.combos import detect_near_miss_combos
        from densa_deck.deck.parser import parse_decklist
        from densa_deck.deck.resolver import resolve_deck
        from densa_deck.models import Format
        db = _get_db(args)
        if db.card_count() == 0:
            console.print("[yellow]Card database empty — run `densa-deck ingest` first.[/yellow]")
            return
        try:
            fmt = Format(args.format)
        except ValueError:
            fmt = Format.COMMANDER
        entries = parse_decklist(text)
        deck = resolve_deck(entries, db, format=fmt)
        deck_card_names = [e.card.name for e in deck.entries if e.card]
        deck_color_identity = sorted({
            c.value for e in deck.entries if e.card for c in e.card.color_identity
        })
        near = detect_near_miss_combos(
            store=store,
            deck_card_names=deck_card_names,
            deck_color_identity=deck_color_identity,
            max_missing=args.max_missing,
            limit=args.limit,
        )
        if not near:
            console.print(f"[green]No combos within {args.max_missing} card(s) of completion.[/green]")
            return
        console.print(
            f"[bold green]{len(near)} combo line(s) within {args.max_missing} card(s):[/bold green]",
        )
        for i, n in enumerate(near, start=1):
            missing_str = ", ".join(n.missing_cards)
            console.print(f"  {i:>2}. {n.combo.short_label()}")
            console.print(f"      [dim]missing: {missing_str}[/dim]")
            console.print(f"      [dim]{n.combo.spellbook_url}[/dim]")
        return

    if action == "verify":
        if store.combo_count() == 0:
            console.print(
                "[yellow]Combo cache empty. Run `densa-deck combos refresh` first.[/yellow]"
            )
            return
        combo = store.get_combo(args.combo_id)
        if combo is None:
            console.print(
                f"[red]No combo found for id '{args.combo_id}'.[/red] "
                "[dim]Use `densa-deck combos detect` to surface ids the cache knows.[/dim]"
            )
            return
        text = _read_deck_text(args.deck)
        if not text.strip():
            console.print("[red]No deck text provided.[/red]")
            return
        from densa_deck.deck.parser import parse_decklist
        from densa_deck.deck.resolver import resolve_deck
        from densa_deck.models import Format
        db = _get_db(args)
        if db.card_count() == 0:
            console.print("[yellow]Card database empty — run `densa-deck ingest` first.[/yellow]")
            return
        try:
            fmt = Format(args.format)
        except ValueError:
            fmt = Format.COMMANDER
        entries = parse_decklist(text)
        deck = resolve_deck(entries, db, format=fmt)
        deck_color_identity = sorted({
            c.value for e in deck.entries if e.card for c in e.card.color_identity
        })
        # Case-insensitive match — mirrors detect_combos in
        # `densa_deck.combos.matcher` so verify and detect agree on a
        # given combo's "is it in the deck" verdict.
        deck_lower = {e.card.name.lower() for e in deck.entries if e.card}

        missing = [c for c in combo.cards if c.lower() not in deck_lower]
        # Color-identity check mirrors detect_combos so the verdict matches
        # what `combos detect` would say if this combo's cards were all in.
        combo_ci = set(combo.color_identity or "")
        deck_ci = set(deck_color_identity)
        ci_ok = combo_ci.issubset(deck_ci)

        console.print(f"[bold]Combo:[/bold] {combo.short_label()}")
        console.print(f"[dim]{combo.spellbook_url}[/dim]")
        console.print()
        for c in combo.cards:
            mark = "[green]✓[/green]" if c.lower() in deck_lower else "[red]✗[/red]"
            console.print(f"  {mark} {c}")
        if combo.templates:
            console.print()
            console.print("[bold]Templates (not deck-checked):[/bold]")
            for t in combo.templates:
                console.print(f"  • {t}")

        console.print()
        if not missing and ci_ok:
            console.print(
                f"[green]Deck contains all {len(combo.cards)} pieces "
                "and color identity fits.[/green]"
            )
        elif not missing and not ci_ok:
            extra = "".join(sorted(combo_ci - deck_ci))
            console.print(
                f"[yellow]All pieces present, but combo needs colors {extra} "
                f"outside the deck's identity ({''.join(sorted(deck_ci)) or 'C'}).[/yellow]"
            )
        else:
            console.print(
                f"[yellow]Missing {len(missing)} of {len(combo.cards)} piece"
                f"{'' if len(missing) == 1 else 's'}: {', '.join(missing)}.[/yellow]"
            )
        return

    if action == "detect":
        if store.combo_count() == 0:
            console.print(
                "[yellow]Combo cache empty. Run `densa-deck combos refresh` first.[/yellow]"
            )
            return
        # Resolve the deck so we have card objects and color identity.
        text = _read_deck_text(args.deck)
        if not text.strip():
            console.print("[red]No deck text provided.[/red]")
            return
        from densa_deck.deck.parser import parse_decklist
        from densa_deck.deck.resolver import resolve_deck
        from densa_deck.models import Format
        db = _get_db(args)
        if db.card_count() == 0:
            console.print("[yellow]Card database empty — run `densa-deck ingest` first.[/yellow]")
            return
        try:
            fmt = Format(args.format)
        except ValueError:
            fmt = Format.COMMANDER
        entries = parse_decklist(text)
        deck = resolve_deck(entries, db, format=fmt)
        deck_card_names = [e.card.name for e in deck.entries if e.card]
        deck_color_identity = sorted({
            c.value for e in deck.entries if e.card for c in e.card.color_identity
        })
        matches = detect_combos(
            store=store,
            deck_card_names=deck_card_names,
            deck_color_identity=deck_color_identity,
            limit=args.limit,
        )
        if not matches:
            console.print("[green]No combos detected.[/green]")
            return
        console.print(f"[bold green]{len(matches)} combo line{'' if len(matches) == 1 else 's'} found:[/bold green]")
        for i, m in enumerate(matches, start=1):
            label = m.combo.short_label()
            extra = ""
            if m.unsatisfied_templates:
                extra = f"  [dim](note: {m.unsatisfied_templates} template prerequisite{'s' if m.unsatisfied_templates != 1 else ''})[/dim]"
            console.print(f"  {i:>2}. {label}{extra}")
            console.print(f"      [dim]{m.combo.spellbook_url}[/dim]")
        return

    console.print("[yellow]Usage:[/yellow] densa-deck combos {refresh|status|detect <deck>}")


def cmd_rule0(args):
    """Build + print a Rule 0 pre-game worksheet for a deck."""
    from densa_deck.analysis.power_level import estimate_power_level
    from densa_deck.analysis.static import analyze_deck as run_static_analysis
    from densa_deck.analyst.phase6 import build_rule0_worksheet, render_rule0_text
    from densa_deck.combos import ComboStore, detect_combos
    from densa_deck.deck.parser import parse_decklist
    from densa_deck.deck.resolver import resolve_deck
    from densa_deck.formats.profiles import detect_archetype
    from densa_deck.models import Format

    text = _read_deck_text(args.deck)
    if not text.strip():
        console.print("[red]No deck text provided.[/red]")
        return
    db = _get_db(args)
    if db.card_count() == 0:
        console.print("[yellow]Card database empty — run `densa-deck ingest` first.[/yellow]")
        return
    try:
        fmt = Format(args.format)
    except ValueError:
        fmt = Format.COMMANDER
    entries = parse_decklist(text)
    deck = resolve_deck(entries, db, format=fmt)
    analysis = run_static_analysis(deck)
    power = estimate_power_level(deck)
    archetype = detect_archetype(deck)
    color_identity = sorted({
        c.value for e in deck.entries if e.card for c in e.card.color_identity
    })

    combo_lines: list[str] = []
    if not args.no_combos:
        store = ComboStore()
        if store.combo_count() > 0:
            deck_card_names = [e.card.name for e in deck.entries if e.card]
            matches = detect_combos(
                store=store,
                deck_card_names=deck_card_names,
                deck_color_identity=color_identity,
                limit=5,
            )
            combo_lines = [m.combo.short_label() for m in matches]

    notable_cards = [e.card.name for e in deck.entries
                     if e.card and "Legendary" in (e.card.type_line or "")][:6]
    ws = build_rule0_worksheet(
        deck_name=deck.name,
        archetype=archetype.value if hasattr(archetype, "value") else str(archetype),
        color_identity=color_identity,
        power=power,
        analysis=analysis,
        goldfish_report=None,
        combo_lines=combo_lines,
        notable_cards=notable_cards,
    )
    console.print(render_rule0_text(ws))


def cmd_explain(args):
    """Explain why one named card is flagged in the given deck."""
    from densa_deck.analysis.castability import analyze_castability
    from densa_deck.analysis.static import analyze_deck as run_static_analysis
    from densa_deck.analyst.candidates import rank_cut_candidates
    from densa_deck.analyst.phase6 import explain_card
    from densa_deck.deck.parser import parse_decklist
    from densa_deck.deck.resolver import resolve_deck
    from densa_deck.models import Format

    text = Path(args.deck).read_text(encoding="utf-8")
    db = _get_db(args)
    if db.card_count() == 0:
        console.print("[yellow]Card database empty — run `densa-deck ingest` first.[/yellow]")
        return
    try:
        fmt = Format(args.format)
    except ValueError:
        fmt = Format.COMMANDER
    entries = parse_decklist(text)
    deck = resolve_deck(entries, db, format=fmt)
    target = next(
        (e for e in deck.entries if e.card and e.card.name.lower() == args.card.lower()),
        None,
    )
    if target is None or target.card is None:
        console.print(f"[red]Card '{args.card}' not in deck.[/red]")
        return

    result = run_static_analysis(deck)
    castability = analyze_castability(deck, result.color_sources)
    flags: list[str] = []
    on_curve = None
    bottleneck = None
    for c in castability.unreliable_cards:
        if c.name.lower() == args.card.lower():
            on_curve = float(c.on_curve_probability)
            bottleneck = c.bottleneck_color or None
            flags.append(f"unreliable on curve (P={on_curve:.2f})")
            if bottleneck:
                flags.append(f"bottleneck color: {bottleneck}")
            break
    for cand in rank_cut_candidates(deck, limit=20):
        if cand.entry.card and cand.entry.card.name.lower() == args.card.lower():
            flags.extend(cand.reasons)
            break

    deck_colors = sorted({
        c.value for e in deck.entries if e.card for c in e.card.color_identity
    })
    backend = _resolve_analyst_backend()
    r = explain_card(
        backend=backend,
        card_name=target.card.name,
        mana_cost=target.card.mana_cost or "",
        cmc=float(target.card.cmc or 0.0),
        deck_name=deck.name,
        deck_colors=deck_colors,
        color_sources=dict(result.color_sources),
        on_curve_prob=on_curve,
        bottleneck_color=bottleneck,
        flags=flags,
        role_tags=[t.value for t in (target.card.tags or [])],
    )
    console.print(f"[bold cyan]{r.card_name}[/bold cyan]")
    if r.summary:
        console.print(r.summary)
    if r.flags:
        console.print(f"[dim]flags: {', '.join(r.flags)}[/dim]")


def cmd_compare_decks(args):
    """Compare two SAVED decks via the analyst — narrates the diff prose."""
    from densa_deck.analysis.power_level import estimate_power_level
    from densa_deck.analysis.static import analyze_deck as run_static_analysis
    from densa_deck.analyst.phase6 import compare_decks
    from densa_deck.deck.parser import parse_decklist
    from densa_deck.deck.resolver import resolve_deck
    from densa_deck.formats.profiles import detect_archetype
    from densa_deck.models import Format
    from densa_deck.versioning.storage import VersionStore, diff_versions

    store = VersionStore()
    snap_a = store.get_latest(args.deck_a_id)
    snap_b = store.get_latest(args.deck_b_id)
    if snap_a is None:
        console.print(f"[red]No saved versions for deck '{args.deck_a_id}'.[/red]")
        return
    if snap_b is None:
        console.print(f"[red]No saved versions for deck '{args.deck_b_id}'.[/red]")
        return

    db = _get_db(args)
    if db.card_count() == 0:
        console.print("[yellow]Card database empty — run `densa-deck ingest` first.[/yellow]")
        return

    def _resolve(snap):
        # Reconstruct a deck from a snapshot's stored zones.
        from densa_deck.app.api import _snapshot_to_text
        entries = parse_decklist(_snapshot_to_text(snap))
        try:
            fmt = Format(snap.format) if snap.format else Format.COMMANDER
        except ValueError:
            fmt = Format.COMMANDER
        return resolve_deck(entries, db, name=snap.name or snap.deck_id, format=fmt)

    deck_a = _resolve(snap_a)
    deck_b = _resolve(snap_b)
    ar = run_static_analysis(deck_a)
    br = run_static_analysis(deck_b)
    pa = estimate_power_level(deck_a)
    pb = estimate_power_level(deck_b)
    archetype_a = detect_archetype(deck_a)
    archetype_b = detect_archetype(deck_b)

    d = diff_versions(snap_a, snap_b)
    score_deltas = {
        k: float(br.scores.get(k, 0.0) - ar.scores.get(k, 0.0))
        for k in (ar.scores.keys() | br.scores.keys())
    }
    role_deltas = {
        "lands":       int(br.land_count - ar.land_count),
        "ramp":        int(br.ramp_count - ar.ramp_count),
        "draw":        int(br.draw_engine_count - ar.draw_engine_count),
        "interaction": int(br.interaction_count - ar.interaction_count),
        "threats":     int(br.threat_count - ar.threat_count),
    }

    backend = _resolve_analyst_backend()
    r = compare_decks(
        backend=backend,
        deck_a_name=deck_a.name, deck_b_name=deck_b.name,
        deck_a_archetype=archetype_a.value if hasattr(archetype_a, "value") else str(archetype_a),
        deck_b_archetype=archetype_b.value if hasattr(archetype_b, "value") else str(archetype_b),
        deck_a_power=float(pa.overall),
        deck_b_power=float(pb.overall),
        added_cards=list(d.added.keys()),
        removed_cards=list(d.removed.keys()),
        score_deltas=score_deltas,
        role_deltas=role_deltas,
    )
    console.print(f"[bold cyan]{deck_a.name}[/bold cyan]  vs  [bold cyan]{deck_b.name}[/bold cyan]")
    console.print(f"[dim]power gap (B - A): {r.power_gap:+.1f}[/dim]")
    if r.summary:
        console.print(r.summary)


def cmd_bracket(args):
    """Assess how a deck fits a Commander bracket."""
    from densa_deck.analysis.brackets import bracket_fit
    from densa_deck.analysis.power_level import estimate_power_level
    from densa_deck.analysis.static import analyze_deck as run_static_analysis
    from densa_deck.combos import ComboStore, detect_combos
    from densa_deck.deck.parser import parse_decklist
    from densa_deck.deck.resolver import resolve_deck
    from densa_deck.models import Format

    text = _read_deck_text(args.deck)
    if not text.strip():
        console.print("[red]No deck text provided.[/red]")
        return
    db = _get_db(args)
    if db.card_count() == 0:
        console.print("[yellow]Card database empty — run `densa-deck ingest` first.[/yellow]")
        return
    try:
        fmt = Format(args.format)
    except ValueError:
        fmt = Format.COMMANDER
    entries = parse_decklist(text)
    deck = resolve_deck(entries, db, format=fmt)
    analysis = run_static_analysis(deck)
    power = estimate_power_level(deck)

    # Combo count from the local cache, if populated. Affects the "max
    # combos" constraint per bracket.
    combo_count = 0
    cstore = ComboStore()
    if cstore.combo_count() > 0:
        deck_card_names = [e.card.name for e in deck.entries if e.card]
        deck_color_identity = sorted({
            c.value for e in deck.entries if e.card for c in e.card.color_identity
        })
        combo_count = len(detect_combos(
            store=cstore, deck_card_names=deck_card_names,
            deck_color_identity=deck_color_identity, limit=50,
        ))

    fit = bracket_fit(
        deck=deck, target_label=args.target,
        power_overall=float(power.overall),
        interaction_count=int(analysis.interaction_count),
        ramp_count=int(analysis.ramp_count),
        detected_combo_count=combo_count,
    )
    color_for_verdict = {
        "fits": "green",
        "over-pitches": "red",
        "under-delivers": "yellow",
    }.get(fit.verdict, "white")
    console.print(
        f"[bold {color_for_verdict}]{fit.verdict.upper()}[/bold {color_for_verdict}] — "
        f"detected [bold]{fit.detected_label}[/bold], target [bold]{fit.target_label}[/bold]"
    )
    console.print(fit.headline)
    if fit.over_signals:
        console.print()
        console.print("[bold]Over the cap:[/bold]")
        for s in fit.over_signals:
            console.print(f"  - {s}")
    if fit.under_signals:
        console.print()
        console.print("[bold]Under the floor:[/bold]")
        for s in fit.under_signals:
            console.print(f"  - {s}")
    if fit.recommendations:
        console.print()
        console.print("[bold]Punch list:[/bold]")
        for r in fit.recommendations:
            console.print(f"  - {r}")


def cmd_export(args):
    """Export a deck to MTGA / MTGO / Moxfield format."""
    from densa_deck.app.api import (
        _export_mtga, _export_mtgo, _export_moxfield_text,
    )
    from densa_deck.deck.parser import parse_decklist
    from densa_deck.deck.resolver import resolve_deck
    from densa_deck.models import Format

    text = Path(args.deck).read_text(encoding="utf-8")
    db = _get_db(args)
    if db.card_count() == 0:
        console.print("[yellow]Card database empty — run `densa-deck ingest` first.[/yellow]")
        return
    try:
        fmt = Format(args.format)
    except ValueError:
        fmt = Format.COMMANDER
    entries = parse_decklist(text)
    deck = resolve_deck(entries, db, format=fmt)

    target = args.target.lower()
    if target == "mtga":
        content, fname = _export_mtga(deck)
    elif target == "mtgo":
        content, fname = _export_mtgo(deck)
    else:
        content, fname = _export_moxfield_text(deck)

    if args.out:
        out_path = Path(args.out)
        out_path.write_text(content, encoding="utf-8")
        console.print(f"[green]Wrote {len(content)} chars to {out_path}[/green]")
    else:
        # Print plain (no Rich markup) so the output is paste-ready.
        sys.stdout.write(content)
        if not content.endswith("\n"):
            sys.stdout.write("\n")


def _resolve_analyst_backend():
    """Pick LlamaCpp if importable + model present, else MockBackend.

    Mirrors the desktop app's _get_coach_backend logic so CLI explain /
    compare-decks pick up the user's downloaded GGUF without an env var.
    """
    import os
    if os.environ.get("MTG_ANALYST_BACKEND", "").lower() == "mock":
        from densa_deck.analyst import MockBackend
        return MockBackend(default="(Mock backend — set MTG_ANALYST_BACKEND= to use the real model.)")
    try:
        from densa_deck.analyst.backends.llama_cpp import LlamaCppBackend
        backend = LlamaCppBackend()
        if backend.is_available():
            return backend
    except Exception:
        pass
    from densa_deck.analyst import MockBackend
    return MockBackend(default="(Analyst model not installed — install via `densa-deck analyst pull`.)")


if __name__ == "__main__":
    main()
