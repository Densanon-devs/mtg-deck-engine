"""Tests for goldfish simulation engine."""

import random

from mtg_deck_engine.goldfish.heuristics import play_turn
from mtg_deck_engine.goldfish.mulligan import mulligan_phase
from mtg_deck_engine.goldfish.objectives import (
    Objective,
    ObjectiveType,
    check_objectives,
    commander_on_curve,
    damage_by_turn,
    default_objectives,
    ramp_by_turn,
)
from mtg_deck_engine.goldfish.runner import run_goldfish_batch
from mtg_deck_engine.goldfish.state import GameState, Permanent
from mtg_deck_engine.models import Card, CardLayout, CardTag, Deck, DeckEntry, Format, Zone


def _make_card(name, is_land=False, cmc=0.0, tags=None, power=None, toughness=None, **kw):
    return Card(
        scryfall_id=f"id-{name}",
        oracle_id=f"oracle-{name}",
        name=name,
        layout=CardLayout.NORMAL,
        cmc=cmc,
        is_land=is_land,
        is_creature=power is not None,
        power=power,
        toughness=toughness,
        tags=tags or [],
        **kw,
    )


def _make_entry(name, qty=1, zone=Zone.MAINBOARD, **card_kw):
    card = _make_card(name, **card_kw)
    return DeckEntry(card_name=name, quantity=qty, zone=zone, card=card)


def _make_test_deck(land_count=36, ramp_count=8, creature_count=20, spell_count=35):
    """Build a 100-card Commander deck for testing."""
    entries = [
        _make_entry("Test Commander", zone=Zone.COMMANDER, cmc=4, power="4", toughness="4",
                     tags=[CardTag.FINISHER]),
    ]
    for i in range(land_count):
        entries.append(_make_entry(f"Land{i}", is_land=True))
    for i in range(ramp_count):
        entries.append(_make_entry(f"Rock{i}", cmc=2, tags=[CardTag.MANA_ROCK, CardTag.RAMP],
                                   is_artifact=True))
    for i in range(creature_count):
        p = str((i % 4) + 1)
        entries.append(_make_entry(f"Creature{i}", cmc=float(int(p) + 1), power=p, toughness=p,
                                   tags=[CardTag.THREAT]))
    filler = 99 - land_count - ramp_count - creature_count
    for i in range(filler):
        entries.append(_make_entry(f"Spell{i}", cmc=3, tags=[CardTag.CARD_DRAW]))
    return Deck(name="Test Goldfish", format=Format.COMMANDER, entries=entries)


# --- GameState tests ---


class TestGameState:
    def test_setup_library(self):
        deck = _make_test_deck()
        state = GameState()
        state.setup_library(deck.entries)
        assert len(state.library) == 99  # 99 mainboard, 1 commander in command zone
        assert len(state.command_zone) == 1

    def test_draw_opening_hand(self):
        deck = _make_test_deck()
        state = GameState()
        state.setup_library(deck.entries)
        hand = state.draw_opening_hand()
        assert len(hand) == 7
        assert len(state.hand) == 7
        assert len(state.library) == 92

    def test_play_land(self):
        land = _make_entry("TestLand", is_land=True)
        state = GameState()
        state.hand.append(land)
        state.play_land(land)
        assert len(state.hand) == 0
        assert len(state.battlefield) == 1
        assert state.land_played_this_turn is True

    def test_tap_for_mana(self):
        land = _make_entry("TestLand", is_land=True)
        state = GameState()
        perm = Permanent(entry=land, tapped=False, summoning_sick=False)
        state.battlefield.append(perm)
        produced = state.tap_for_mana(1)
        assert produced == 1
        assert state.mana_pool == 1
        assert perm.tapped is True

    def test_cast_spell(self):
        spell = _make_entry("TestSpell", cmc=2, tags=[CardTag.CARD_DRAW])
        state = GameState()
        state.hand.append(spell)
        state.mana_pool = 3
        state.spend_mana(2)
        state.cast_spell(spell)
        assert len(state.hand) == 0
        assert state.mana_pool == 1

    def test_attack_with_all(self):
        creature = _make_entry("Attacker", power="3", toughness="3", cmc=3)
        state = GameState()
        state.opponent_life = 20
        perm = Permanent(entry=creature, tapped=False, summoning_sick=False)
        state.battlefield.append(perm)
        dmg = state.attack_with_all()
        assert dmg == 3
        assert state.opponent_life == 17
        assert state.total_damage_dealt == 3

    def test_turn_sequence(self):
        deck = _make_test_deck()
        state = GameState()
        state.setup_library(deck.entries)
        state.draw_opening_hand()
        state.begin_turn()
        assert state.turn == 1
        assert len(state.hand) == 7  # On play, no draw T1
        state.end_turn()
        assert len(state.turn_history) == 1


# --- Heuristics tests ---


class TestHeuristics:
    def test_play_turn_plays_land(self):
        random.seed(42)
        deck = _make_test_deck()
        state = GameState()
        state.setup_library(deck.entries)
        state.draw_opening_hand()
        state.begin_turn()
        play_turn(state)
        # Should have played a land if one was in hand
        lands_in_hand_before = sum(1 for e in state.hand if e.card and e.card.is_land)
        # At least the land-played flag should be set if there were lands
        assert state.land_played_this_turn or lands_in_hand_before == 0

    def test_play_turn_casts_spells(self):
        random.seed(100)
        deck = _make_test_deck()
        state = GameState()
        state.setup_library(deck.entries)
        state.draw_opening_hand()
        # Play a few turns to get mana
        for _ in range(3):
            state.begin_turn()
            play_turn(state)
            state.end_turn()
        # By T3, should have cast something
        total_cast = sum(m.cards_cast for m in state.turn_history)
        assert total_cast >= 1


# --- Mulligan tests ---


class TestMulligan:
    def test_mulligan_returns_count(self):
        random.seed(42)
        deck = _make_test_deck()
        state = GameState()
        state.setup_library(deck.entries)
        mulls = mulligan_phase(state, deck)
        assert 0 <= mulls <= 3
        assert len(state.hand) == 7 - mulls

    def test_mulligan_deterministic(self):
        deck = _make_test_deck()
        random.seed(99)
        s1 = GameState()
        s1.setup_library(deck.entries)
        m1 = mulligan_phase(s1, deck)

        random.seed(99)
        s2 = GameState()
        s2.setup_library(deck.entries)
        m2 = mulligan_phase(s2, deck)

        assert m1 == m2
        assert len(s1.hand) == len(s2.hand)


# --- Objectives tests ---


class TestObjectives:
    def test_commander_objective(self):
        state = GameState()
        state.turn = 4
        state.commander_cast_turn = 4
        state.turn_history = []
        obj = commander_on_curve(4)
        check_objectives(state, [obj])
        assert obj.met is True

    def test_damage_objective(self):
        state = GameState()
        state.turn = 6
        state.total_damage_dealt = 22
        state.turn_history = []
        obj = damage_by_turn(20, 6)
        check_objectives(state, [obj])
        assert obj.met is True

    def test_objective_missed(self):
        state = GameState()
        state.turn = 5
        state.total_damage_dealt = 5
        state.turn_history = []
        obj = damage_by_turn(20, 4)  # Needed 20 by T4, already past
        check_objectives(state, [obj])
        assert obj.met is False

    def test_default_objectives(self):
        deck = _make_test_deck()
        objs = default_objectives(deck)
        assert len(objs) >= 5  # Should have several default objectives
        names = [o.name for o in objs]
        assert any("Commander" in n for n in names)
        assert any("Ramp" in n for n in names)


# --- Batch runner tests ---


class TestBatchRunner:
    def test_basic_run(self):
        random.seed(42)
        deck = _make_test_deck()
        report = run_goldfish_batch(deck, simulations=100, max_turns=8, seed=42)
        assert report.simulations == 100
        assert report.average_mulligans >= 0
        assert report.average_spells_cast > 0
        assert len(report.average_damage_by_turn) > 0
        assert len(report.average_lands_by_turn) > 0

    def test_deterministic(self):
        deck = _make_test_deck()
        r1 = run_goldfish_batch(deck, simulations=50, max_turns=6, seed=77)
        r2 = run_goldfish_batch(deck, simulations=50, max_turns=6, seed=77)
        assert r1.average_damage_by_turn == r2.average_damage_by_turn
        assert r1.average_mulligans == r2.average_mulligans

    def test_damage_increases_over_turns(self):
        deck = _make_test_deck(creature_count=30)
        report = run_goldfish_batch(deck, simulations=200, max_turns=8, seed=55)
        prev = 0
        for turn in range(1, 9):
            dmg = report.average_damage_by_turn.get(turn, 0)
            assert dmg >= prev, f"Damage should increase: T{turn}={dmg} < T{turn-1}={prev}"
            prev = dmg

    def test_objectives_tracked(self):
        deck = _make_test_deck()
        report = run_goldfish_batch(deck, simulations=100, max_turns=10, seed=42)
        assert len(report.objective_pass_rates) > 0
        # At least some objectives should pass in some games
        total_pass = sum(report.objective_pass_rates.values())
        assert total_pass > 0

    def test_most_cast_spells(self):
        deck = _make_test_deck()
        report = run_goldfish_batch(deck, simulations=100, max_turns=8, seed=42)
        assert len(report.most_cast_spells) > 0
        # Each entry is (name, count)
        for name, count in report.most_cast_spells:
            assert isinstance(name, str)
            assert count > 0

    def test_commander_cast_rate(self):
        deck = _make_test_deck(land_count=38, ramp_count=12)
        report = run_goldfish_batch(deck, simulations=200, max_turns=10, seed=33)
        # With 38 lands + 12 ramp and a 4-CMC commander, should cast it often
        assert report.commander_cast_rate > 0.3
