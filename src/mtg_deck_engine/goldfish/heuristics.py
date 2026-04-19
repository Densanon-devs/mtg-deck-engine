"""Spell casting and land play heuristics for goldfish simulation.

These are simplified decision-making rules that approximate reasonable
play without implementing full MTG rules. The goal is to measure deck
consistency, not to play optimally.

Priority system:
  T1-T2: Ramp > low-cost threats > card draw
  T3-T4: Engines/draw > threats > interaction (goldfish = no opponent)
  T5+:   Finishers > threats > draw > anything castable
"""

from __future__ import annotations

from mtg_deck_engine.goldfish.state import GameState
from mtg_deck_engine.models import CardTag, DeckEntry


def play_turn(state: GameState):
    """Execute a full main phase: play land, tap mana, cast spells."""
    _play_best_land(state)
    _cast_spells(state)


def _play_best_land(state: GameState):
    """Choose and play the best land from hand."""
    if state.land_played_this_turn:
        return

    lands = [e for e in state.hand if e.card and e.card.is_land]
    if not lands:
        return

    # Prefer untapped lands, then lands that produce needed colors
    lands.sort(key=lambda e: _land_score(e, state), reverse=True)
    state.play_land(lands[0])


def _land_score(entry: DeckEntry, state: GameState) -> float:
    """Score a land for play priority."""
    card = entry.card
    if card is None:
        return 0.0

    score = 10.0  # Base

    # Untapped is much better
    ot = card.oracle_text.lower()
    enters_tapped = "enters tapped" in ot or "enters the battlefield tapped" in ot
    if "you may pay" in ot:
        enters_tapped = False  # Shock lands — assume player pays
    if "search your library" in ot:
        enters_tapped = False  # Fetch lands

    if not enters_tapped:
        score += 20.0

    # Multi-color production is better
    produced = set(card.produced_mana)
    for face in card.faces:
        produced.update(face.produced_mana)
    color_count = len(produced & {"W", "U", "B", "R", "G"})
    score += color_count * 3.0

    # Utility lands are lower priority early
    if card.tags and CardTag.UTILITY_LAND in card.tags:
        score -= 5.0

    return score


def _cast_spells(state: GameState):
    """Cast as many spells as possible, prioritized by game phase."""
    # Determine available mana
    total_mana = state.available_mana
    if total_mana == 0:
        return

    # Tap all available mana sources
    state.tap_for_mana(total_mana)

    # Try to cast commander from command zone first (if affordable)
    _try_cast_commander(state)

    # Gather castable nonland cards from hand (split cards use front-face cost)
    castable = [
        e for e in state.hand
        if e.card and not e.card.is_land and e.card.display_cmc() <= state.mana_pool
    ]

    if not castable:
        return

    # Sort by priority
    castable.sort(key=lambda e: _spell_priority(e, state), reverse=True)

    # Cast in priority order
    for entry in castable:
        if entry.card is None:
            continue
        cost = int(entry.card.display_cmc())
        if cost <= state.mana_pool:
            state.spend_mana(cost)
            state.cast_spell(entry)


def _try_cast_commander(state: GameState):
    """Try to cast commander from command zone."""
    if not state.command_zone:
        return

    cmd = state.command_zone[0]
    if cmd.card is None:
        return

    cost = int(cmd.card.cmc) + state.commander_tax
    if cost <= state.mana_pool:
        state.spend_mana(cost)
        state.cast_spell(cmd, from_command_zone=True)


def _spell_priority(entry: DeckEntry, state: GameState) -> float:
    """Score a spell for casting priority based on current turn and game state."""
    card = entry.card
    if card is None:
        return 0.0

    score = 0.0
    turn = state.turn
    tags = card.tags or []

    # --- Early game (T1-T3): prioritize ramp and setup ---
    if turn <= 3:
        if CardTag.MANA_ROCK in tags or CardTag.MANA_DORK in tags or CardTag.RAMP in tags:
            score += 50.0  # Ramp is king early
        if CardTag.CARD_DRAW in tags:
            score += 30.0
        if CardTag.ENGINE in tags:
            score += 25.0
        if CardTag.THREAT in tags:
            score += 15.0

    # --- Mid game (T4-T6): threats, engines, draw ---
    elif turn <= 6:
        if CardTag.ENGINE in tags:
            score += 45.0
        if CardTag.CARD_DRAW in tags:
            score += 40.0
        if CardTag.THREAT in tags:
            score += 35.0
        if CardTag.FINISHER in tags:
            score += 50.0
        if CardTag.MANA_ROCK in tags or CardTag.RAMP in tags:
            score += 15.0  # Still ok but lower priority

    # --- Late game (T7+): finishers and haymakers ---
    else:
        if CardTag.FINISHER in tags:
            score += 60.0
        if CardTag.THREAT in tags:
            score += 40.0
        if CardTag.ENGINE in tags:
            score += 35.0
        if CardTag.CARD_DRAW in tags:
            score += 30.0

    # Interaction is low priority in goldfish (no opponent)
    if CardTag.TARGETED_REMOVAL in tags or CardTag.COUNTERSPELL in tags or CardTag.BOARD_WIPE in tags:
        score += 5.0  # Cast if nothing better, just for the body if any

    # Prefer spending all mana: bonus for cards that cost exactly remaining mana
    cost = int(card.display_cmc())
    if cost == state.mana_pool:
        score += 10.0  # Perfect curve-out bonus

    # Slight bonus for higher CMC (bigger impact)
    score += card.display_cmc() * 1.5

    # Creatures get bonus for attacking
    if card.is_creature and card.power and card.power.isdigit():
        score += int(card.power) * 2.0

    return score
