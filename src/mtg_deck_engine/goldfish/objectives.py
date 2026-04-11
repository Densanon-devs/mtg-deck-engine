"""Objective test system for goldfish simulation.

Objectives are testable conditions evaluated after each turn:
- "Cast commander by turn 4"
- "Deal 20 damage by turn 7"
- "Have 5+ creatures by turn 5"
- "Cast a ramp spell by turn 2"

Each objective has a target turn and a condition checker.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from mtg_deck_engine.goldfish.state import GameState
from mtg_deck_engine.models import CardTag


class ObjectiveType(str, Enum):
    COMMANDER_BY_TURN = "commander_by_turn"
    DAMAGE_BY_TURN = "damage_by_turn"
    CREATURES_BY_TURN = "creatures_by_turn"
    LANDS_BY_TURN = "lands_by_turn"
    CAST_ROLE_BY_TURN = "cast_role_by_turn"
    MANA_BY_TURN = "mana_by_turn"
    SPELLS_CAST_BY_TURN = "spells_cast_by_turn"
    CARD_CAST_BY_TURN = "card_cast_by_turn"


@dataclass
class Objective:
    """A testable game objective."""

    name: str
    type: ObjectiveType
    target_turn: int
    target_value: int | str = 0
    met: bool = False
    met_on_turn: int | None = None


def check_objectives(state: GameState, objectives: list[Objective]):
    """Check all objectives against current game state."""
    for obj in objectives:
        if obj.met:
            continue  # Already achieved
        if state.turn > obj.target_turn:
            continue  # Missed the window

        if _check_objective(state, obj):
            obj.met = True
            obj.met_on_turn = state.turn


def _check_objective(state: GameState, obj: Objective) -> bool:
    """Evaluate a single objective against game state."""
    target = obj.target_value

    if obj.type == ObjectiveType.COMMANDER_BY_TURN:
        return state.commander_cast_turn is not None and state.commander_cast_turn <= obj.target_turn

    elif obj.type == ObjectiveType.DAMAGE_BY_TURN:
        return state.total_damage_dealt >= int(target)

    elif obj.type == ObjectiveType.CREATURES_BY_TURN:
        return len(state.creatures_in_play) >= int(target)

    elif obj.type == ObjectiveType.LANDS_BY_TURN:
        return len(state.lands_in_play) >= int(target)

    elif obj.type == ObjectiveType.MANA_BY_TURN:
        # Check if available mana (untapped sources) meets target
        total_sources = len(state.mana_sources)
        return total_sources >= int(target)

    elif obj.type == ObjectiveType.SPELLS_CAST_BY_TURN:
        total_cast = sum(m.cards_cast for m in state.turn_history)
        return total_cast >= int(target)

    elif obj.type == ObjectiveType.CAST_ROLE_BY_TURN:
        # target_value is a CardTag value string
        tag_str = str(target)
        for metrics in state.turn_history:
            for spell_name in metrics.spells_cast:
                # Look up the card in battlefield or graveyard
                for perm in state.battlefield:
                    if perm.name == spell_name and perm.card and perm.card.tags:
                        if any(t.value == tag_str for t in perm.card.tags):
                            return True
                for entry in state.graveyard:
                    if entry.card_name == spell_name and entry.card and entry.card.tags:
                        if any(t.value == tag_str for t in entry.card.tags):
                            return True
        return False

    elif obj.type == ObjectiveType.CARD_CAST_BY_TURN:
        card_name = str(target).lower()
        for metrics in state.turn_history:
            if any(s.lower() == card_name for s in metrics.spells_cast):
                return True
        return False

    return False


# --- Preset objective builders ---


def commander_on_curve(cmc: int) -> Objective:
    """Objective: cast commander by its CMC turn."""
    return Objective(
        name=f"Commander cast by turn {cmc}",
        type=ObjectiveType.COMMANDER_BY_TURN,
        target_turn=cmc,
    )


def damage_by_turn(damage: int, turn: int) -> Objective:
    return Objective(
        name=f"{damage} damage by turn {turn}",
        type=ObjectiveType.DAMAGE_BY_TURN,
        target_turn=turn,
        target_value=damage,
    )


def creatures_by_turn(count: int, turn: int) -> Objective:
    return Objective(
        name=f"{count}+ creatures by turn {turn}",
        type=ObjectiveType.CREATURES_BY_TURN,
        target_turn=turn,
        target_value=count,
    )


def ramp_by_turn(turn: int) -> Objective:
    return Objective(
        name=f"Ramp spell by turn {turn}",
        type=ObjectiveType.CAST_ROLE_BY_TURN,
        target_turn=turn,
        target_value=CardTag.RAMP.value,
    )


def card_draw_by_turn(turn: int) -> Objective:
    return Objective(
        name=f"Card draw by turn {turn}",
        type=ObjectiveType.CAST_ROLE_BY_TURN,
        target_turn=turn,
        target_value=CardTag.CARD_DRAW.value,
    )


def lands_by_turn(count: int, turn: int) -> Objective:
    return Objective(
        name=f"{count} lands by turn {turn}",
        type=ObjectiveType.LANDS_BY_TURN,
        target_turn=turn,
        target_value=count,
    )


def default_objectives(deck) -> list[Objective]:
    """Generate a set of default objectives based on deck characteristics."""
    objs: list[Objective] = []

    # Commander on curve
    if deck.commanders:
        cmd = deck.commanders[0]
        if cmd.card:
            cmc = max(1, int(cmd.card.cmc))
            objs.append(commander_on_curve(cmc))

    # Standard milestones
    objs.append(ramp_by_turn(2))
    objs.append(lands_by_turn(3, 3))
    objs.append(creatures_by_turn(3, 5))
    objs.append(damage_by_turn(10, 6))
    objs.append(damage_by_turn(20, 8))
    objs.append(card_draw_by_turn(4))

    return objs
