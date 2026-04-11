"""Batch goldfish simulation runner.

Runs N goldfish games, aggregates results into a comprehensive report
covering damage curves, mana development, objective pass rates, and
per-turn metrics.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field

from rich.console import Console

from mtg_deck_engine.classification.tagger import classify_card
from mtg_deck_engine.goldfish.heuristics import play_turn
from mtg_deck_engine.goldfish.mulligan import mulligan_phase
from mtg_deck_engine.goldfish.objectives import (
    Objective,
    check_objectives,
    default_objectives,
)
from mtg_deck_engine.goldfish.state import GameState, TurnMetrics
from mtg_deck_engine.models import Deck

console = Console()

MAX_TURNS = 10


@dataclass
class GameResult:
    """Result of a single goldfish game."""

    mulligans_taken: int = 0
    turns_played: int = 0
    total_damage: int = 0
    kill_turn: int | None = None  # Turn opponent reached 0
    commander_cast_turn: int | None = None
    total_spells_cast: int = 0
    total_lands_played: int = 0
    total_mana_spent: int = 0
    turn_metrics: list[TurnMetrics] = field(default_factory=list)
    objectives_met: dict[str, bool] = field(default_factory=dict)
    objectives_met_turn: dict[str, int | None] = field(default_factory=dict)


@dataclass
class GoldfishReport:
    """Aggregated results from a batch of goldfish games."""

    simulations: int = 0
    max_turns: int = MAX_TURNS

    # Mulligan stats
    average_mulligans: float = 0.0
    mulligan_distribution: dict[int, float] = field(default_factory=dict)

    # Damage stats
    average_damage_by_turn: dict[int, float] = field(default_factory=dict)
    average_kill_turn: float = 0.0
    kill_rate: float = 0.0  # % of games that dealt 40+ damage
    kill_turn_distribution: dict[int, float] = field(default_factory=dict)

    # Board development
    average_creatures_by_turn: dict[int, float] = field(default_factory=dict)
    average_lands_by_turn: dict[int, float] = field(default_factory=dict)
    average_mana_spent_by_turn: dict[int, float] = field(default_factory=dict)
    average_cards_cast_by_turn: dict[int, float] = field(default_factory=dict)

    # Commander stats
    commander_cast_rate: float = 0.0
    average_commander_turn: float = 0.0

    # Spells
    average_spells_cast: float = 0.0
    most_cast_spells: list[tuple[str, int]] = field(default_factory=list)

    # Objectives
    objective_pass_rates: dict[str, float] = field(default_factory=dict)

    # Per-game results (for advanced analysis)
    game_results: list[GameResult] = field(default_factory=list)


def run_goldfish_batch(
    deck: Deck,
    simulations: int = 1000,
    max_turns: int = MAX_TURNS,
    objectives: list[Objective] | None = None,
    seed: int | None = None,
    store_games: bool = False,
) -> GoldfishReport:
    """Run a batch of goldfish simulations and aggregate results."""
    if seed is not None:
        random.seed(seed)

    # Ensure cards are classified
    for entry in deck.entries:
        if entry.card and not entry.card.tags:
            entry.card.tags = classify_card(entry.card)

    # Generate default objectives if none provided
    if objectives is None:
        objectives = default_objectives(deck)

    results: list[GameResult] = []
    spell_counter: Counter[str] = Counter()

    for _ in range(simulations):
        # Reset objectives for this game
        game_objectives = [
            Objective(
                name=o.name,
                type=o.type,
                target_turn=o.target_turn,
                target_value=o.target_value,
            )
            for o in objectives
        ]

        result = _run_single_game(deck, max_turns, game_objectives)
        results.append(result)

        # Track spell frequency
        for tm in result.turn_metrics:
            for spell in tm.spells_cast:
                spell_counter[spell] += 1

    # Aggregate
    report = _aggregate_results(results, simulations, max_turns, objectives, spell_counter)
    if store_games:
        report.game_results = results

    return report


def _run_single_game(
    deck: Deck,
    max_turns: int,
    objectives: list[Objective],
) -> GameResult:
    """Run a single goldfish game."""
    state = GameState()
    is_commander = deck.format and deck.format.value in ("commander", "brawl", "oathbreaker", "duel")
    state.life = 40 if is_commander else 20
    state.opponent_life = 40 if is_commander else 20

    # Setup library
    state.setup_library(deck.entries)

    # Mulligan phase
    mulls = mulligan_phase(state, deck)

    # Play turns
    for _ in range(max_turns):
        state.begin_turn()
        play_turn(state)
        metrics = state.end_turn()

        # Check objectives
        check_objectives(state, objectives)

        if state.game_over:
            break

    # Build result
    result = GameResult(
        mulligans_taken=mulls,
        turns_played=state.turn,
        total_damage=state.total_damage_dealt,
        kill_turn=state.turn if state.opponent_life <= 0 else None,
        commander_cast_turn=state.commander_cast_turn,
        total_spells_cast=sum(m.cards_cast for m in state.turn_history),
        total_lands_played=sum(1 for m in state.turn_history if m.land_played),
        total_mana_spent=sum(m.mana_spent for m in state.turn_history),
        turn_metrics=list(state.turn_history),
        objectives_met={o.name: o.met for o in objectives},
        objectives_met_turn={o.name: o.met_on_turn for o in objectives},
    )

    return result


def _aggregate_results(
    results: list[GameResult],
    simulations: int,
    max_turns: int,
    objectives: list[Objective],
    spell_counter: Counter,
) -> GoldfishReport:
    """Aggregate individual game results into a report."""
    report = GoldfishReport(simulations=simulations, max_turns=max_turns)

    if not results:
        return report

    # Mulligan stats
    mull_counts = [r.mulligans_taken for r in results]
    report.average_mulligans = sum(mull_counts) / len(mull_counts)
    mull_dist: Counter[int] = Counter(mull_counts)
    report.mulligan_distribution = {k: v / simulations for k, v in sorted(mull_dist.items())}

    # Per-turn aggregation
    for turn in range(1, max_turns + 1):
        damages = []
        creatures = []
        lands = []
        mana_spent = []
        cards_cast = []

        for r in results:
            if turn <= len(r.turn_metrics):
                tm = r.turn_metrics[turn - 1]
                damages.append(tm.cumulative_damage)
                creatures.append(tm.creatures_in_play)
                lands.append(tm.lands_in_play)
                mana_spent.append(tm.mana_spent)
                cards_cast.append(tm.cards_cast)
            else:
                # Game ended before this turn
                if r.turn_metrics:
                    last = r.turn_metrics[-1]
                    damages.append(last.cumulative_damage)
                    creatures.append(last.creatures_in_play)
                    lands.append(last.lands_in_play)

        if damages:
            report.average_damage_by_turn[turn] = round(sum(damages) / len(damages), 1)
        if creatures:
            report.average_creatures_by_turn[turn] = round(sum(creatures) / len(creatures), 1)
        if lands:
            report.average_lands_by_turn[turn] = round(sum(lands) / len(lands), 1)
        if mana_spent:
            report.average_mana_spent_by_turn[turn] = round(sum(mana_spent) / len(mana_spent), 1)
        if cards_cast:
            report.average_cards_cast_by_turn[turn] = round(sum(cards_cast) / len(cards_cast), 1)

    # Kill stats
    kill_turns = [r.kill_turn for r in results if r.kill_turn is not None]
    report.kill_rate = len(kill_turns) / simulations
    if kill_turns:
        report.average_kill_turn = round(sum(kill_turns) / len(kill_turns), 1)
        kill_dist: Counter[int] = Counter(kill_turns)
        report.kill_turn_distribution = {k: v / simulations for k, v in sorted(kill_dist.items())}

    # Commander stats
    cmd_turns = [r.commander_cast_turn for r in results if r.commander_cast_turn is not None]
    report.commander_cast_rate = len(cmd_turns) / simulations
    if cmd_turns:
        report.average_commander_turn = round(sum(cmd_turns) / len(cmd_turns), 1)

    # Spell stats
    all_spells = [r.total_spells_cast for r in results]
    report.average_spells_cast = round(sum(all_spells) / len(all_spells), 1)
    report.most_cast_spells = spell_counter.most_common(10)

    # Objective pass rates
    for obj in objectives:
        passed = sum(1 for r in results if r.objectives_met.get(obj.name, False))
        report.objective_pass_rates[obj.name] = round(passed / simulations, 4)

    return report
