"""Benchmark gauntlet: run a deck against a field of archetypes.

Produces a meta positioning report showing win rates per archetype,
overall weighted win rate, and strengths/weaknesses analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.console import Console

from densa_deck.combos.models import Combo
from densa_deck.matchup.archetypes import (
    ArchetypeProfile,
    get_default_gauntlet,
)
from densa_deck.matchup.simulator import MatchupResult, simulate_matchup
from densa_deck.models import Deck

console = Console()


@dataclass
class GauntletReport:
    """Complete gauntlet results: deck vs the field."""

    deck_name: str = ""
    simulations_per_matchup: int = 0
    total_games: int = 0

    # Per-matchup results
    matchups: list[MatchupResult] = field(default_factory=list)

    # Aggregate scores
    overall_win_rate: float = 0.0
    weighted_win_rate: float = 0.0  # Weighted by meta share

    # Best and worst matchups
    best_matchup: str = ""
    best_win_rate: float = 0.0
    worst_matchup: str = ""
    worst_win_rate: float = 1.0

    # Category scores (0-100)
    speed_score: float = 0.0        # How fast we close games
    resilience_score: float = 0.0    # How well we handle disruption
    interaction_score: float = 0.0   # How well we handle opponent pressure
    consistency_score: float = 0.0   # Variance in performance

    # Combo aggregates (zero / empty when run_gauntlet was called without combos)
    combos_evaluated: int = 0
    combo_win_rate_overall: float = 0.0  # share of all gauntlet games won by combo
    avg_combo_win_turn_overall: float = 0.0
    # Across-archetype top combo lines (id, short_label, count_total, rate)
    top_combo_lines_overall: list[tuple[str, str, int, float]] = field(default_factory=list)


def run_gauntlet(
    deck: Deck,
    archetypes: list[ArchetypeProfile] | None = None,
    simulations: int = 500,
    max_turns: int = 12,
    seed: int | None = None,
    combos: list[Combo] | None = None,
) -> GauntletReport:
    """Run a deck against a gauntlet of archetypes.

    When `combos` is non-empty, each archetype matchup also tracks
    combo-as-win-condition: turns where the deck assembles a combo
    line before the opponent closes count as wins via combo. Each
    MatchupResult exposes combo_win_rate / wins_by_combo /
    avg_combo_win_turn, and the GauntletReport aggregates the gauntlet-
    wide combo win rate + top firing combos.
    """
    if archetypes is None:
        archetypes = get_default_gauntlet()

    report = GauntletReport(
        deck_name=deck.name,
        simulations_per_matchup=simulations,
    )

    console.print(f"[dim]Running gauntlet: {len(archetypes)} archetypes x {simulations} games each...[/dim]")

    matchup_seed = seed
    for arch in archetypes:
        console.print(f"  [dim]vs {arch.display_name}...[/dim]")
        result = simulate_matchup(
            deck, arch,
            simulations=simulations,
            max_turns=max_turns,
            seed=matchup_seed,
            combos=combos,
        )
        report.matchups.append(result)
        report.total_games += simulations
        if matchup_seed is not None:
            matchup_seed += 1000  # Vary seed per matchup for independence

    # Compute aggregates
    _compute_aggregates(report, archetypes)
    # Combo aggregates — separate pass so the existing _compute_aggregates
    # stays focused on the historical metrics.
    _compute_combo_aggregates(report)

    return report


def _compute_combo_aggregates(report: GauntletReport) -> None:
    """Sum combo-wins across all matchups + collect top combo lines."""
    if not report.matchups:
        return
    # combos_evaluated should be the same across all matchups (we pass
    # the same combos list); take the first non-zero value to surface.
    for m in report.matchups:
        if m.combos_evaluated:
            report.combos_evaluated = m.combos_evaluated
            break
    total_combo_wins = sum(m.wins_by_combo for m in report.matchups)
    if not total_combo_wins or report.total_games <= 0:
        return
    report.combo_win_rate_overall = round(total_combo_wins / report.total_games, 4)

    # Average combo turn weighted by combo-win count per matchup.
    weighted_turn_sum = 0.0
    total = 0
    for m in report.matchups:
        if m.wins_by_combo and m.avg_combo_win_turn:
            weighted_turn_sum += m.avg_combo_win_turn * m.wins_by_combo
            total += m.wins_by_combo
    if total:
        report.avg_combo_win_turn_overall = round(weighted_turn_sum / total, 2)

    # Collapse top_combo_lines across matchups.
    from collections import Counter as _C
    label_for: dict[str, str] = {}
    counter: _C[str] = _C()
    for m in report.matchups:
        for cid, label, count, _rate in m.top_combo_lines:
            label_for.setdefault(cid, label)
            counter[cid] += count
    report.top_combo_lines_overall = [
        (cid, label_for.get(cid, cid), n, round(n / report.total_games, 4))
        for cid, n in counter.most_common(5)
    ]


def _compute_aggregates(report: GauntletReport, archetypes: list[ArchetypeProfile]):
    """Calculate overall scores from individual matchup results."""
    if not report.matchups:
        return

    # Simple win rate (unweighted)
    total_wins = sum(m.wins for m in report.matchups)
    total_games = sum(m.simulations for m in report.matchups)
    report.overall_win_rate = total_wins / total_games if total_games > 0 else 0.0

    # Weighted win rate (by meta share)
    weighted_wins = 0.0
    total_weight = 0.0
    arch_map = {a.display_name: a for a in archetypes}

    for m in report.matchups:
        arch = arch_map.get(m.archetype_name)
        weight = arch.meta_weight if arch else 1.0
        weighted_wins += m.win_rate * weight
        total_weight += weight

    report.weighted_win_rate = weighted_wins / total_weight if total_weight > 0 else 0.0

    # Best and worst
    best = max(report.matchups, key=lambda m: m.win_rate)
    worst = min(report.matchups, key=lambda m: m.win_rate)
    report.best_matchup = best.archetype_name
    report.best_win_rate = best.win_rate
    report.worst_matchup = worst.archetype_name
    report.worst_win_rate = worst.win_rate

    # Category scores

    # Speed: estimate from average turns across matchups
    avg_turns_all = sum(m.avg_turns for m in report.matchups) / len(report.matchups)
    report.speed_score = round(max(0, min(100, (12 - avg_turns_all) / 8 * 100)), 1)

    # Build archetype name lookup for category scoring
    arch_name_map = {a.display_name: a.name for a in archetypes}

    # Resilience: win rate against high-interaction archetypes
    _HIGH_INTERACTION = {"control", "stax", "spellslinger"}
    high_interaction = [m for m in report.matchups if arch_name_map.get(m.archetype_name, "").value in _HIGH_INTERACTION]
    if high_interaction:
        resilience_wr = sum(m.win_rate for m in high_interaction) / len(high_interaction)
        report.resilience_score = round(resilience_wr * 100, 1)
    else:
        report.resilience_score = report.overall_win_rate * 100

    # Interaction: how well we handle aggro/fast decks
    _FAST_ARCHETYPES = {"aggro", "voltron", "turbo"}
    fast_matchups = [m for m in report.matchups if arch_name_map.get(m.archetype_name, "").value in _FAST_ARCHETYPES]
    if fast_matchups:
        interaction_wr = sum(m.win_rate for m in fast_matchups) / len(fast_matchups)
        report.interaction_score = round(interaction_wr * 100, 1)
    else:
        report.interaction_score = report.overall_win_rate * 100

    # Consistency: inverse of win rate variance
    if len(report.matchups) > 1:
        win_rates = [m.win_rate for m in report.matchups]
        avg_wr = sum(win_rates) / len(win_rates)
        variance = sum((wr - avg_wr) ** 2 for wr in win_rates) / len(win_rates)
        # Lower variance = more consistent
        report.consistency_score = round(max(0, min(100, (1 - variance * 4) * 100)), 1)
    else:
        report.consistency_score = 50.0
