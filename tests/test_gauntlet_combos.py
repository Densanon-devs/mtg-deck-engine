"""Tests for combo-aware gauntlet + matchup simulator.

Confirms simulate_matchup + run_gauntlet thread the optional `combos`
argument, that combo wins are credited only when the combo assembles
before the opponent closes, and that the report aggregates correctly.
"""

from __future__ import annotations

import pytest

from densa_deck.combos.models import Combo
from densa_deck.matchup.archetypes import ArchetypeProfile, get_default_gauntlet
from densa_deck.matchup.gauntlet import run_gauntlet
from densa_deck.matchup.simulator import MatchupResult, simulate_matchup
from densa_deck.models import (
    Card,
    CardLayout,
    Color,
    Deck,
    DeckEntry,
    Format,
    Legality,
    Zone,
)


def _mk(name, **kw):
    return Card(
        scryfall_id=f"sid-{name}", oracle_id=f"oid-{name}",
        name=name, layout=CardLayout.NORMAL,
        cmc=kw.get("cmc", 0), mana_cost=kw.get("mc", ""),
        type_line=kw.get("tl", "Artifact"),
        colors=kw.get("cols", []), color_identity=kw.get("ci", []),
        is_land=kw.get("is_land", False),
        is_creature=kw.get("is_creature", False),
        is_artifact=kw.get("is_artifact", False),
        is_instant=kw.get("is_instant", False),
        legalities={"commander": Legality.LEGAL},
    )


def _build_combo_deck(combo_cards: list[str], copies_each: int = 12) -> Deck:
    """60-card-style deck stuffed with combo pieces so over short matchups
    the combo reliably assembles. Same shape as the goldfish test fixture."""
    entries: list[DeckEntry] = []
    for n in combo_cards:
        c = _mk(n, mc="{2}", cmc=2, is_artifact=True)
        entries.append(DeckEntry(card_name=n, quantity=copies_each,
                                 zone=Zone.MAINBOARD, card=c))
    forest = _mk("Forest", tl="Basic Land — Forest", ci=[Color.GREEN], is_land=True)
    entries.append(DeckEntry(card_name="Forest", quantity=24,
                             zone=Zone.MAINBOARD, card=forest))
    target_total = 60 - 24 - copies_each * len(combo_cards)
    for i in range(max(0, target_total)):
        c = _mk(f"Filler-{i}", mc="{2}", cmc=2, tl="Creature", is_creature=True)
        entries.append(DeckEntry(card_name=f"Filler-{i}", quantity=1,
                                 zone=Zone.MAINBOARD, card=c))
    return Deck(name="Combo Deck", format=Format.MODERN, entries=entries)


def _slow_archetype() -> ArchetypeProfile:
    """A non-pressuring archetype used to give combos time to fire.

    pressure_start_turn=20 means the opponent never deals damage during
    a 12-turn match, so the combo win condition is the only path.
    """
    from densa_deck.matchup.archetypes import ArchetypeName
    return ArchetypeProfile(
        name=ArchetypeName.MIDRANGE,
        display_name="No-Pressure-Test",
        description="testing combo wins",
        meta_weight=1.0,
        damage_per_turn=0,
        pressure_start_turn=20,
        max_pressure_turn=21,
        wipe_chance=0.0,
        targeted_removal_chance=0.0,
        counterspell_chance=0.0,
        hand_disruption_chance=0.0,
        mana_tax=0,
    )


# ---------------------------------------------------------------- matchup


class TestComboMatchup:
    def test_no_combos_means_zero_combo_fields(self):
        """Backward compat: omitting combos leaves all combo fields at defaults."""
        deck = _build_combo_deck(["X", "Y"])
        result = simulate_matchup(
            deck, _slow_archetype(), simulations=10, seed=1,
        )
        assert result.combos_evaluated == 0
        assert result.wins_by_combo == 0
        assert result.combo_win_rate == 0.0
        assert result.avg_combo_win_turn == 0.0
        assert result.top_combo_lines == []

    def test_combo_assembles_against_slow_opponent(self):
        """Against a no-pressure archetype, a deck loaded with combo
        pieces should win nearly every game via combo."""
        deck = _build_combo_deck(["A", "B"])
        combo = Combo(combo_id="cAB", cards=["A", "B"], popularity=1)
        result = simulate_matchup(
            deck, _slow_archetype(), simulations=30, seed=42, combos=[combo],
        )
        assert result.combos_evaluated == 1
        # All wins should be via combo since the opponent never closes
        assert result.wins_by_combo > 0
        assert result.wins_by_damage == 0  # this deck has only 2-cost filler creatures
        # Win rate is dominated by combo; avg combo turn should be > 0
        assert result.combo_win_rate > 0.0
        assert result.avg_combo_win_turn > 0.0
        # top_combo_lines should reference our combo
        assert any(cid == "cAB" for cid, _, _, _ in result.top_combo_lines)

    def test_unmatched_combo_skipped(self):
        """If the deck doesn't run all combo pieces, the combo is filtered
        out and combos_evaluated stays 0."""
        deck = _build_combo_deck(["A"])  # only A, missing B
        combo = Combo(combo_id="cAB", cards=["A", "Missing-B"], popularity=1)
        result = simulate_matchup(
            deck, _slow_archetype(), simulations=10, seed=1, combos=[combo],
        )
        assert result.combos_evaluated == 0


# ---------------------------------------------------------------- gauntlet


class TestComboGauntlet:
    def test_run_gauntlet_with_combos_aggregates_overall(self):
        """Running a small gauntlet (just our slow archetype) with a
        combo deck should produce non-zero combo aggregates at the
        gauntlet level."""
        deck = _build_combo_deck(["A", "B"])
        combo = Combo(combo_id="cAB", cards=["A", "B"], popularity=1)
        # Use a tiny gauntlet of just our slow archetype so the combo
        # wins are unambiguous.
        report = run_gauntlet(
            deck, archetypes=[_slow_archetype()],
            simulations=20, seed=1, combos=[combo],
        )
        assert report.combos_evaluated == 1
        assert report.combo_win_rate_overall > 0
        assert report.avg_combo_win_turn_overall > 0
        # top_combo_lines_overall reflects total fires
        assert any(cid == "cAB" for cid, _, _, _ in report.top_combo_lines_overall)

    def test_run_gauntlet_without_combos_stays_compatible(self):
        """Backward compat: omitting combos leaves combo aggregates at zero."""
        deck = _build_combo_deck(["A", "B"])
        report = run_gauntlet(
            deck, archetypes=[_slow_archetype()],
            simulations=10, seed=1,
        )
        assert report.combos_evaluated == 0
        assert report.combo_win_rate_overall == 0.0
        assert report.avg_combo_win_turn_overall == 0.0
        assert report.top_combo_lines_overall == []

    def test_per_matchup_combo_metrics_filled(self):
        """The MatchupResult inside the gauntlet should also carry the
        per-matchup combo metrics (wins_by_combo / combo_win_rate /
        avg_combo_win_turn)."""
        deck = _build_combo_deck(["A", "B"])
        combo = Combo(combo_id="cAB", cards=["A", "B"], popularity=1)
        report = run_gauntlet(
            deck, archetypes=[_slow_archetype()],
            simulations=20, seed=1, combos=[combo],
        )
        m = report.matchups[0]
        assert m.combos_evaluated == 1
        assert m.wins_by_combo > 0
        assert m.combo_win_rate > 0
        assert m.avg_combo_win_turn > 0
