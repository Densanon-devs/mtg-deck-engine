"""Core data models for the MTG Deck Engine."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Format(str, enum.Enum):
    STANDARD = "standard"
    PIONEER = "pioneer"
    MODERN = "modern"
    LEGACY = "legacy"
    VINTAGE = "vintage"
    PAUPER = "pauper"
    COMMANDER = "commander"
    BRAWL = "brawl"
    HISTORIC = "historic"
    EXPLORER = "explorer"
    ALCHEMY = "alchemy"
    PENNY = "penny"
    OATHBREAKER = "oathbreaker"
    DUEL = "duel"
    PREMODERN = "premodern"


class Legality(str, enum.Enum):
    LEGAL = "legal"
    NOT_LEGAL = "not_legal"
    BANNED = "banned"
    RESTRICTED = "restricted"


class Color(str, enum.Enum):
    WHITE = "W"
    BLUE = "U"
    BLACK = "B"
    RED = "R"
    GREEN = "G"


class CardLayout(str, enum.Enum):
    NORMAL = "normal"
    SPLIT = "split"
    FLIP = "flip"
    TRANSFORM = "transform"
    MODAL_DFC = "modal_dfc"
    MELD = "meld"
    LEVELER = "leveler"
    CLASS = "class"
    CASE = "case"
    SAGA = "saga"
    ADVENTURE = "adventure"
    MUTATE = "mutate"
    PROTOTYPE = "prototype"
    BATTLE = "battle"
    PLANAR = "planar"
    SCHEME = "scheme"
    VANGUARD = "vanguard"
    TOKEN = "token"
    DOUBLE_FACED_TOKEN = "double_faced_token"
    EMBLEM = "emblem"
    AUGMENT = "augment"
    HOST = "host"
    ART_SERIES = "art_series"
    REVERSIBLE_CARD = "reversible_card"


class CardTag(str, enum.Enum):
    """Functional role tags for card classification."""

    LAND = "land"
    BASIC_LAND = "basic_land"
    FETCH_LAND = "fetch_land"
    DUAL_LAND = "dual_land"
    UTILITY_LAND = "utility_land"
    MDFC_LAND = "mdfc_land"
    RAMP = "ramp"
    MANA_ROCK = "mana_rock"
    MANA_DORK = "mana_dork"
    CARD_DRAW = "card_draw"
    TUTOR = "tutor"
    CANTRIP = "cantrip"
    TARGETED_REMOVAL = "targeted_removal"
    BOARD_WIPE = "board_wipe"
    COUNTERSPELL = "counterspell"
    PROTECTION = "protection"
    RECURSION = "recursion"
    GRAVEYARD_HATE = "graveyard_hate"
    ARTIFACT_ENCHANTMENT_REMOVAL = "artifact_enchantment_removal"
    TOKEN_MAKER = "token_maker"
    STAX = "stax"
    FINISHER = "finisher"
    THREAT = "threat"
    ENGINE = "engine"
    LIFEGAIN = "lifegain"
    SACRIFICE_OUTLET = "sacrifice_outlet"
    ARISTOCRAT_PAYOFF = "aristocrat_payoff"
    COST_REDUCER = "cost_reducer"
    COMBAT_TRICK = "combat_trick"
    EQUIPMENT = "equipment"
    AURA = "aura"


class CardFace(BaseModel):
    """A single face of a card (most cards have one face, DFCs have two)."""

    name: str
    mana_cost: str = ""
    cmc: float = 0.0
    type_line: str = ""
    oracle_text: str = ""
    power: Optional[str] = None
    toughness: Optional[str] = None
    loyalty: Optional[str] = None
    colors: list[Color] = Field(default_factory=list)
    color_indicator: list[Color] = Field(default_factory=list)
    produced_mana: list[str] = Field(default_factory=list)


class Card(BaseModel):
    """Canonical card object with normalized data from Scryfall."""

    scryfall_id: str
    oracle_id: str
    name: str
    layout: CardLayout
    cmc: float = 0.0
    mana_cost: str = ""
    type_line: str = ""
    oracle_text: str = ""
    colors: list[Color] = Field(default_factory=list)
    color_identity: list[Color] = Field(default_factory=list)
    produced_mana: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    legalities: dict[str, Legality] = Field(default_factory=dict)
    faces: list[CardFace] = Field(default_factory=list)
    power: Optional[str] = None
    toughness: Optional[str] = None
    loyalty: Optional[str] = None
    rarity: str = ""
    set_code: str = ""
    is_land: bool = False
    is_creature: bool = False
    is_instant: bool = False
    is_sorcery: bool = False
    is_artifact: bool = False
    is_enchantment: bool = False
    is_planeswalker: bool = False
    is_battle: bool = False
    tags: list[CardTag] = Field(default_factory=list)

    def has_type(self, t: str) -> bool:
        return t.lower() in self.type_line.lower()

    def display_cmc(self) -> float:
        """CMC used for curve bucketing and average-MV math.

        Scryfall reports `cmc` as the SUM of both halves for split-layout cards
        (e.g. Fire // Ice = 4), but the curve bucket players care about is the
        cost of a single half. Transform / adventure / modal-DFC cards already
        report the front face CMC at the top level, so only split needs fixing.
        """
        if self.layout == CardLayout.SPLIT and self.faces:
            return self.faces[0].cmc or 0.0
        return self.cmc or 0.0

    @property
    def image_url(self) -> str:
        """Scryfall hotlink URL. Never host card images locally."""
        from mtg_deck_engine.legal import scryfall_image_url

        return scryfall_image_url(self.scryfall_id)


class Zone(str, enum.Enum):
    MAINBOARD = "mainboard"
    SIDEBOARD = "sideboard"
    COMMANDER = "commander"
    COMPANION = "companion"
    MAYBEBOARD = "maybeboard"


class DeckEntry(BaseModel):
    """A single entry in a decklist (card + quantity + zone)."""

    card_name: str
    quantity: int = 1
    zone: Zone = Zone.MAINBOARD
    card: Optional[Card] = None
    custom_tags: list[str] = Field(default_factory=list)


class Deck(BaseModel):
    """A fully parsed and validated deck."""

    name: str = "Untitled Deck"
    format: Optional[Format] = None
    entries: list[DeckEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    version: int = 1
    notes: str = ""

    @property
    def mainboard(self) -> list[DeckEntry]:
        return [e for e in self.entries if e.zone == Zone.MAINBOARD]

    @property
    def sideboard(self) -> list[DeckEntry]:
        return [e for e in self.entries if e.zone == Zone.SIDEBOARD]

    @property
    def commanders(self) -> list[DeckEntry]:
        return [e for e in self.entries if e.zone == Zone.COMMANDER]

    @property
    def companion(self) -> list[DeckEntry]:
        return [e for e in self.entries if e.zone == Zone.COMPANION]

    @property
    def total_cards(self) -> int:
        return sum(
            e.quantity for e in self.entries if e.zone not in (Zone.MAYBEBOARD, Zone.SIDEBOARD)
        )

    @property
    def total_mainboard(self) -> int:
        return sum(e.quantity for e in self.mainboard)

    def cards_with_tag(self, tag: CardTag) -> list[DeckEntry]:
        return [e for e in self.entries if e.card and tag in e.card.tags]

    def resolved_cards(self) -> list[DeckEntry]:
        return [e for e in self.entries if e.card is not None]


class ValidationIssue(BaseModel):
    """A problem found during deck validation."""

    severity: str  # "error", "warning", "info"
    message: str
    card_name: Optional[str] = None


class AnalysisResult(BaseModel):
    """Result container for static analysis."""

    deck_name: str = ""
    format: Optional[str] = None
    total_cards: int = 0
    mana_curve: dict[int, int] = Field(default_factory=dict)
    color_distribution: dict[str, int] = Field(default_factory=dict)
    color_sources: dict[str, int] = Field(default_factory=dict)
    type_distribution: dict[str, int] = Field(default_factory=dict)
    tag_distribution: dict[str, int] = Field(default_factory=dict)
    land_count: int = 0
    nonland_count: int = 0
    average_cmc: float = 0.0
    interaction_count: int = 0
    draw_engine_count: int = 0
    ramp_count: int = 0
    threat_count: int = 0
    issues: list[ValidationIssue] = Field(default_factory=list)
    scores: dict[str, float] = Field(default_factory=dict)
    recommendations: list[str] = Field(default_factory=list)
