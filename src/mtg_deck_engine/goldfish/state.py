"""Game state model for goldfish simulation.

Tracks all zones, mana, life, and per-turn metrics during a solo game.
This is deliberately simplified — no stack, no priority, no opponent actions.
The goal is to approximate how the deck functions, not to be rules-complete.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

from mtg_deck_engine.models import Card, CardTag, DeckEntry, Zone


class Phase(str, Enum):
    UNTAP = "untap"
    DRAW = "draw"
    MAIN = "main"
    END = "end"


@dataclass
class Permanent:
    """A card on the battlefield."""

    entry: DeckEntry
    tapped: bool = False
    summoning_sick: bool = True  # Can't tap for mana/attack until next turn
    counters: int = 0

    @property
    def card(self) -> Card | None:
        return self.entry.card

    @property
    def name(self) -> str:
        return self.entry.card_name

    def is_land(self) -> bool:
        return self.card is not None and self.card.is_land

    def is_creature(self) -> bool:
        return self.card is not None and self.card.is_creature

    def produces_mana(self) -> bool:
        if self.card is None:
            return False
        if self.card.is_land:
            return True
        if self.card.tags and (CardTag.MANA_ROCK in self.card.tags or CardTag.MANA_DORK in self.card.tags):
            return True
        return False

    def available_mana(self) -> int:
        """Mana this permanent can produce (simplified: 1 per source)."""
        if self.tapped or not self.produces_mana():
            return 0
        if self.is_creature() and self.summoning_sick:
            return 0  # Dorks can't tap with summoning sickness
        return 1


@dataclass
class TurnMetrics:
    """Metrics captured at the end of each turn."""

    turn: int = 0
    lands_in_play: int = 0
    mana_available: int = 0
    mana_spent: int = 0
    cards_in_hand: int = 0
    cards_cast: int = 0
    creatures_in_play: int = 0
    total_power: int = 0
    damage_dealt: int = 0
    cumulative_damage: int = 0
    land_played: bool = False
    spells_cast: list[str] = field(default_factory=list)


@dataclass
class GameState:
    """Complete state of a goldfish game."""

    # Zones
    library: list[DeckEntry] = field(default_factory=list)
    hand: list[DeckEntry] = field(default_factory=list)
    battlefield: list[Permanent] = field(default_factory=list)
    graveyard: list[DeckEntry] = field(default_factory=list)
    command_zone: list[DeckEntry] = field(default_factory=list)

    # Game state
    turn: int = 0
    phase: Phase = Phase.UNTAP
    life: int = 40
    opponent_life: int = 40
    land_played_this_turn: bool = False
    mana_pool: int = 0
    mana_spent_this_turn: int = 0
    cards_cast_this_turn: int = 0
    commander_tax: int = 0  # +2 for each previous cast from command zone
    on_play: bool = True
    mulligans_taken: int = 0

    # Per-turn history
    turn_history: list[TurnMetrics] = field(default_factory=list)
    spells_cast_this_turn: list[str] = field(default_factory=list)

    # Tracking
    total_damage_dealt: int = 0
    commander_cast_turn: int | None = None
    game_over: bool = False

    # --- Zone queries ---

    @property
    def lands_in_play(self) -> list[Permanent]:
        return [p for p in self.battlefield if p.is_land()]

    @property
    def creatures_in_play(self) -> list[Permanent]:
        return [p for p in self.battlefield if p.is_creature()]

    @property
    def mana_sources(self) -> list[Permanent]:
        return [p for p in self.battlefield if p.produces_mana()]

    @property
    def total_power(self) -> int:
        total = 0
        for p in self.creatures_in_play:
            if p.card and p.card.power and p.card.power.isdigit():
                total += int(p.card.power)
        return total

    @property
    def available_mana(self) -> int:
        return sum(p.available_mana() for p in self.battlefield)

    # --- Zone operations ---

    def draw(self, count: int = 1) -> list[DeckEntry]:
        """Draw cards from library to hand."""
        drawn = []
        for _ in range(count):
            if not self.library:
                break
            card = self.library.pop(0)
            self.hand.append(card)
            drawn.append(card)
        return drawn

    def play_land(self, entry: DeckEntry):
        """Play a land from hand to battlefield."""
        if entry in self.hand:
            self.hand.remove(entry)
        perm = Permanent(entry=entry, tapped=False, summoning_sick=False)
        # Check if land enters tapped (simplified heuristic)
        if entry.card and _enters_tapped(entry.card):
            perm.tapped = True
        self.battlefield.append(perm)
        self.land_played_this_turn = True

    def cast_spell(self, entry: DeckEntry, from_command_zone: bool = False):
        """Cast a nonland spell from hand (or command zone) to battlefield/graveyard."""
        if from_command_zone:
            if entry in self.command_zone:
                self.command_zone.remove(entry)
            self.commander_tax += 2
            if self.commander_cast_turn is None:
                self.commander_cast_turn = self.turn
        elif entry in self.hand:
            self.hand.remove(entry)

        card = entry.card
        if card is None:
            self.graveyard.append(entry)
            return

        # Permanents go to battlefield, instants/sorceries to graveyard
        if card.is_instant or card.is_sorcery:
            self.graveyard.append(entry)
        else:
            perm = Permanent(entry=entry, tapped=False, summoning_sick=True)
            self.battlefield.append(perm)

        self.spells_cast_this_turn.append(entry.card_name)
        self.cards_cast_this_turn += 1

    def tap_for_mana(self, count: int) -> int:
        """Tap mana sources to produce mana. Returns amount actually produced."""
        produced = 0
        for p in self.mana_sources:
            if produced >= count:
                break
            avail = p.available_mana()
            if avail > 0 and not p.tapped:
                p.tapped = True
                produced += avail
        self.mana_pool += produced
        return produced

    def spend_mana(self, amount: int) -> bool:
        """Spend mana from pool. Returns True if enough was available."""
        if self.mana_pool >= amount:
            self.mana_pool -= amount
            self.mana_spent_this_turn += amount
            return True
        return False

    def attack_with_all(self) -> int:
        """Attack with all non-sick creatures. Returns damage dealt."""
        damage = 0
        for p in self.creatures_in_play:
            if p.summoning_sick:
                continue
            if p.card and p.card.power and p.card.power.isdigit():
                damage += int(p.card.power)
            p.tapped = True
        self.opponent_life -= damage
        self.total_damage_dealt += damage
        return damage

    # --- Turn structure ---

    def begin_turn(self):
        """Start a new turn: untap, upkeep, draw."""
        self.turn += 1
        self.phase = Phase.UNTAP
        self.land_played_this_turn = False
        self.mana_pool = 0
        self.mana_spent_this_turn = 0
        self.cards_cast_this_turn = 0
        self.spells_cast_this_turn = []

        # Untap
        for p in self.battlefield:
            p.tapped = False
            p.summoning_sick = False  # Creatures that survived a full turn cycle

        # Draw (skip T1 on the play)
        self.phase = Phase.DRAW
        if not (self.turn == 1 and self.on_play):
            self.draw(1)

        self.phase = Phase.MAIN

    def end_turn(self) -> TurnMetrics:
        """End the turn: discard to hand size, record metrics."""
        self.phase = Phase.END

        # Discard to 7 (simplified)
        while len(self.hand) > 7:
            # Discard highest-CMC card
            worst = max(self.hand, key=lambda e: e.card.cmc if e.card else 0)
            self.hand.remove(worst)
            self.graveyard.append(worst)

        # Record metrics
        damage_this_turn = self.attack_with_all() if self.turn >= 2 else 0

        metrics = TurnMetrics(
            turn=self.turn,
            lands_in_play=len(self.lands_in_play),
            mana_available=self.available_mana + self.mana_spent_this_turn,
            mana_spent=self.mana_spent_this_turn,
            cards_in_hand=len(self.hand),
            cards_cast=self.cards_cast_this_turn,
            creatures_in_play=len(self.creatures_in_play),
            total_power=self.total_power,
            damage_dealt=damage_this_turn,
            cumulative_damage=self.total_damage_dealt,
            land_played=self.land_played_this_turn,
            spells_cast=list(self.spells_cast_this_turn),
        )
        self.turn_history.append(metrics)

        if self.opponent_life <= 0:
            self.game_over = True

        return metrics

    # --- Setup ---

    def setup_library(self, entries: list[DeckEntry], shuffle: bool = True):
        """Build and shuffle the library from deck entries."""
        pool: list[DeckEntry] = []
        for entry in entries:
            if entry.zone == Zone.COMMANDER:
                self.command_zone.append(entry)
                continue
            if entry.zone in (Zone.MAYBEBOARD, Zone.SIDEBOARD):
                continue
            for _ in range(entry.quantity):
                pool.append(entry)
        if shuffle:
            random.shuffle(pool)
        self.library = pool

    def draw_opening_hand(self, size: int = 7) -> list[DeckEntry]:
        """Draw an opening hand."""
        return self.draw(size)


def _enters_tapped(card: Card) -> bool:
    """Heuristic: does this land enter tapped?"""
    ot = card.oracle_text.lower()
    if not card.is_land:
        return False
    # Fetch lands don't enter tapped themselves
    if "search your library" in ot:
        return False
    # Shock lands: optional
    if "you may pay" in ot and "enters" in ot:
        return False  # Assume player pays life
    # Explicit ETB tapped
    if "enters tapped" in ot or "enters the battlefield tapped" in ot:
        return True
    # Triomes and similar
    if "cycling" in ot and card.produced_mana and len(set(card.produced_mana) & {"W", "U", "B", "R", "G"}) >= 3:
        return True
    return False
