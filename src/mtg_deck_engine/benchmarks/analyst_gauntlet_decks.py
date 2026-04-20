"""30 hand-curated gauntlet cases.

Each case is a realistic deck shape with named real MTG cards. The card
library below is a shared pool — each spec just references names, and the
builder materialises a Deck with tags/costs/colors set. Gold cut sets are
the specific cards that case expects to see in the analyst's suggestions.

Deck categories:
  1. Ramp over-cap (6)  — should surface redundant ramp + vanilla filler
  2. Low interaction (5) — should trigger add-removal role gap
  3. Low draw (4)        — should trigger add-card_draw role gap
  4. Low ramp (4)        — should trigger add-ramp role gap
  5. High-CMC bloat (5)  — should surface high-cost non-finisher cuts
  6. Balanced (3)        — minimal warnings, few cuts
  7. Over-removal (3)    — should surface removal-trim cuts

Flavor note: we use real card names for readability of gauntlet output but
set minimal properties — oracle_text is truncated to what the classifier
needs to match tags. Gold cuts are written with knowledge that the ranker
will surface (high-CMC non-finisher) + (redundant role) + (no-tag) combos.
"""

from __future__ import annotations

from typing import Callable

from mtg_deck_engine.analysis.power_level import PowerBreakdown
from mtg_deck_engine.models import (
    AnalysisResult,
    Card,
    CardLayout,
    CardTag,
    Color,
    Deck,
    DeckEntry,
    Format,
    Zone,
)


# =============================================================================
# Shared card library
# =============================================================================
# Each entry: {name: (cmc, [tags], [color_identity_letters], is_creature, is_artifact,
#                      is_instant, oracle_text_stub)}

_R = CardTag.RAMP
_MR = CardTag.MANA_ROCK
_MD = CardTag.MANA_DORK
_D = CardTag.CARD_DRAW
_T = CardTag.TARGETED_REMOVAL
_BW = CardTag.BOARD_WIPE
_F = CardTag.FINISHER
_TH = CardTag.THREAT
_CS = CardTag.COUNTERSPELL
_TK = CardTag.TOKEN_MAKER
_RM = CardTag.RECURSION

# (cmc, tags, color_identity, is_creature, is_artifact, is_instant, oracle_stub)
_CARDS: dict[str, tuple] = {
    # --- Ramp -------------------------------------------------------------
    "Sol Ring":            (1, [_R, _MR], [],      False, True,  False, "{T}: Add {C}{C}."),
    "Arcane Signet":       (2, [_R, _MR], [],      False, True,  False, "{T}: Add one mana of any color in your commander's identity."),
    "Commander's Sphere":  (3, [_R, _MR], [],      False, True,  False, "{T}: Add one mana of any color in your commander's identity."),
    "Mind Stone":          (2, [_R, _MR], [],      False, True,  False, "{T}: Add {C}."),
    "Thought Vessel":      (2, [_R, _MR], [],      False, True,  False, "{T}: Add {C}."),
    "Fellwar Stone":       (2, [_R, _MR], [],      False, True,  False, "{T}: Add one mana of any color."),
    "Prismatic Lens":      (2, [_R, _MR], [],      False, True,  False, "{T}: Add one mana of any color."),
    "Coldsteel Heart":     (2, [_R, _MR], [],      False, True,  False, "{T}: Add one mana."),
    "Cultivate":           (3, [_R],      ["G"],   False, False, False, "Search your library for a basic land card and put a land onto the battlefield."),
    "Kodama's Reach":      (3, [_R],      ["G"],   False, False, False, "Search your library for a basic land card and put a land onto the battlefield."),
    "Farseek":             (2, [_R],      ["G"],   False, False, False, "Search your library for a plains, island, swamp, or mountain and put a land onto the battlefield."),
    "Rampant Growth":      (2, [_R],      ["G"],   False, False, False, "Search your library for a basic land and put a land onto the battlefield."),
    "Three Visits":        (2, [_R],      ["G"],   False, False, False, "Search your library for a forest card and put a land onto the battlefield."),
    "Nature's Lore":       (2, [_R],      ["G"],   False, False, False, "Search your library for a forest card and put a land onto the battlefield."),
    "Skyshroud Claim":     (4, [_R],      ["G"],   False, False, False, "Search your library for two forest cards and put a land onto the battlefield."),
    "Explosive Vegetation":(4, [_R],      ["G"],   False, False, False, "Search your library for two basic land cards and put a land onto the battlefield."),
    "Circuitous Route":    (4, [_R],      ["G"],   False, False, False, "Search your library and put a land onto the battlefield."),
    "Search for Tomorrow": (3, [_R],      ["G"],   False, False, False, "Search your library for a basic land and put a land onto the battlefield."),
    "Harrow":              (3, [_R],      ["G"],   False, False, True,  "Search your library for two basic land cards and put a land onto the battlefield."),
    "Llanowar Elves":      (1, [_MD, _R], ["G"],   True,  False, False, "{T}: Add {G}."),
    "Fyndhorn Elves":      (1, [_MD, _R], ["G"],   True,  False, False, "{T}: Add {G}."),
    "Elvish Mystic":       (1, [_MD, _R], ["G"],   True,  False, False, "{T}: Add {G}."),
    "Birds of Paradise":   (1, [_MD, _R], ["G"],   True,  False, False, "{T}: Add one mana of any color."),

    # --- Card draw --------------------------------------------------------
    "Rhystic Study":       (3, [_D],      ["U"],   False, False, False, "Whenever an opponent casts a spell, draw a card unless they pay {1}."),
    "Mystic Remora":       (1, [_D],      ["U"],   False, False, False, "Whenever an opponent casts a noncreature spell, you may draw a card."),
    "Phyrexian Arena":     (3, [_D],      ["B"],   False, False, False, "At the beginning of your upkeep, draw a card."),
    "Sylvan Library":      (2, [_D],      ["G"],   False, False, False, "At the beginning of your draw step, draw a card."),
    "Harmonize":           (4, [_D],      ["G"],   False, False, False, "Draw a card three times."),
    "Night's Whisper":     (2, [_D],      ["B"],   False, False, False, "You draw a card twice and lose 2 life."),
    "Brainstorm":          (1, [_D],      ["U"],   False, False, True,  "Draw a card three times, then put two cards back."),
    "Ponder":              (1, [_D],      ["U"],   False, False, False, "Look at the top cards, draw a card."),
    "Fact or Fiction":     (4, [_D],      ["U"],   False, False, True,  "Reveal the top five cards, opponent separates into two piles, draw a card for each."),
    "Read the Bones":      (3, [_D],      ["B"],   False, False, False, "Scry 2, then draw a card twice."),
    "Bident of Thassa":    (4, [_D],      ["U"],   False, False, False, "Whenever a creature attacks, you may draw a card."),
    "Beast Whisperer":     (4, [_D],      ["G"],   True,  False, False, "Whenever you cast a creature spell, draw a card."),

    # --- Removal (targeted) ----------------------------------------------
    "Swords to Plowshares": (1, [_T],     ["W"],   False, False, True,  "Exile target creature."),
    "Path to Exile":        (1, [_T],     ["W"],   False, False, True,  "Exile target creature."),
    "Generous Gift":        (3, [_T],     ["W"],   False, False, True,  "Destroy target permanent."),
    "Beast Within":         (3, [_T],     ["G"],   False, False, True,  "Destroy target permanent."),
    "Chaos Warp":           (3, [_T],     ["R"],   False, False, True,  "Target permanent's owner shuffles it into their library."),
    "Anguished Unmaking":   (3, [_T],     ["W", "B"], False, False, True, "Exile target nonland permanent."),
    "Assassin's Trophy":    (2, [_T],     ["B", "G"], False, False, True, "Destroy target permanent. Its controller may search for a basic land."),
    "Nature's Claim":       (1, [_T],     ["G"],   False, False, True,  "Destroy target artifact or enchantment."),
    "Feed the Swarm":       (2, [_T],     ["B"],   False, False, False, "Destroy target creature or enchantment an opponent controls."),
    "Terminate":            (2, [_T],     ["B", "R"], False, False, True, "Destroy target creature."),
    "Doom Blade":           (2, [_T],     ["B"],   False, False, True,  "Destroy target nonblack creature."),
    "Putrefy":              (3, [_T],     ["B", "G"], False, False, True, "Destroy target artifact or creature."),
    "Go for the Throat":    (2, [_T],     ["B"],   False, False, True,  "Destroy target nonartifact creature."),
    "Vindicate":            (3, [_T],     ["W", "B"], False, False, False, "Destroy target permanent."),

    # --- Board wipes ------------------------------------------------------
    "Wrath of God":        (4, [_BW],     ["W"],   False, False, False, "Destroy all creatures. They can't be regenerated."),
    "Damnation":           (4, [_BW],     ["B"],   False, False, False, "Destroy all creatures. They can't be regenerated."),
    "Blasphemous Act":     (9, [_BW],     ["R"],   False, False, False, "Blasphemous Act deals 13 damage to each creature."),
    "Toxic Deluge":        (3, [_BW],     ["B"],   False, False, False, "Pay X life. All creatures get -X/-X."),
    "Cyclonic Rift":       (2, [_BW],     ["U"],   False, False, True,  "Return target nonland permanent an opponent controls to its owner's hand. Overload for {6}{U}."),

    # --- Counterspells ----------------------------------------------------
    "Counterspell":        (2, [_T, _CS], ["U"],   False, False, True,  "Counter target spell."),
    "Swan Song":           (1, [_T, _CS], ["U"],   False, False, True,  "Counter target enchantment, instant, or sorcery."),
    "Negate":              (2, [_T, _CS], ["U"],   False, False, True,  "Counter target noncreature spell."),
    "Mana Drain":          (2, [_T, _CS], ["U"],   False, False, True,  "Counter target spell."),

    # --- Finishers --------------------------------------------------------
    "Craterhoof Behemoth": (8, [_F, _TH], ["G"],   True,  False, False, "When Craterhoof Behemoth enters, creatures you control gain haste and get +X/+X."),
    "Avenger of Zendikar": (7, [_F, _TH], ["G"],   True,  False, False, "When Avenger enters, create a plant token for each land you control."),
    "Terastodon":          (8, [_F, _TH], ["G"],   True,  False, False, "When Terastodon enters, you may destroy up to three noncreature permanents."),
    "Elesh Norn, Grand Cenobite": (7, [_F, _TH], ["W"], True, False, False, "Creatures you control get +2/+2. Other creatures get -2/-2."),
    "Sheoldred, Whispering One":  (7, [_F, _TH], ["B"], True, False, False, "Upkeep: opponents sacrifice, return creature from graveyard."),
    "Consecrated Sphinx": (6, [_F, _TH, _D], ["U"], True, False, False, "Whenever an opponent draws, you may draw two cards."),
    "Massacre Wurm":       (6, [_F, _TH], ["B"],   True,  False, False, "When Wurm enters, creatures opponents control get -2/-2."),
    "Kalonian Hydra":      (5, [_F, _TH], ["G"],   True,  False, False, "Double +1/+1 counters on creatures when attacks."),

    # --- Threats ----------------------------------------------------------
    "Llanowar Visionary":  (3, [_TH, _R, _D], ["G"], True, False, False, "When enters, {T}: Add {G}. Draw a card."),
    "Eternal Witness":     (3, [_TH, _RM], ["G"],   True, False, False, "When Eternal Witness enters, return target card from graveyard to hand."),
    "Solemn Simulacrum":   (4, [_TH, _R, _D], [],   True, True,  False, "When enters, search for a basic land; when dies, draw a card."),
    "Reclamation Sage":    (3, [_T, _TH],    ["G"], True, False, False, "When enters, destroy target artifact or enchantment."),
    "Oracle of Mul Daya":  (4, [_TH, _R],    ["G"], True, False, False, "You may play additional land; play lands from top of library."),

    # --- Vanilla / Filler (no functional tag) ----------------------------
    # These are the prime cut targets — high-cost creatures with no role.
    "Pelakka Wurm":        (7, [],        ["G"],   True,  False, False, "Trample; when Pelakka Wurm dies, gain life and draw a card."),
    "Yavimaya Wurm":       (6, [],        ["G"],   True,  False, False, "Trample."),
    "Garruk's Packleader": (5, [],        ["G"],   True,  False, False, "When a creature you control dies, draw a card."),
    "Siege Wurm":          (7, [],        ["G"],   True,  False, False, "Convoke. Trample."),
    "Gurmag Swiftwing":    (5, [],        ["B"],   True,  False, False, "Flying, haste, deathtouch."),
    "Pristine Angel":      (6, [],        ["W"],   True,  False, False, "Flying; untap, gain protection from chosen color."),
    "Warstorm Surge":      (6, [],        ["R"],   False, False, False, "Whenever a creature enters, deals damage equal to power."),
    "Jade Mage":           (2, [],        ["G"],   True,  False, False, "{2}{G}: Create a 1/1 green saproling."),
    "Hungering Yeti":      (5, [],        ["R"],   True,  False, False, "Dash. When enters, chooses a creature."),
    "Frost Lynx":          (3, [],        ["U"],   True,  False, False, "When enters, tap target creature."),
    "Kessig Wolf":         (4, [],        ["R", "G"], True, False, False, "Werewolf vanilla."),

    # --- Small creatures / generic fill (no tag, low CMC) ----------------
    "Forest Bear":         (3, [],        ["G"],   True,  False, False, "3/3 vanilla bear."),
    "Grizzly Bears":       (2, [],        ["G"],   True,  False, False, "2/2 vanilla bear."),
    "Runeclaw Bear":       (2, [],        ["G"],   True,  False, False, "2/2 vanilla."),
    "Dryad Militant":      (1, [],        ["G", "W"], True, False, False, "Instant/sorcery hate."),
}


def _mk_card(name: str) -> Card:
    """Build a Card from the shared library."""
    if name not in _CARDS:
        raise KeyError(f"{name!r} not in card library")
    cmc, tags, ci_letters, is_creature, is_artifact, is_instant, oracle = _CARDS[name]
    color_to_enum = {"W": Color.WHITE, "U": Color.BLUE, "B": Color.BLACK, "R": Color.RED, "G": Color.GREEN}
    ci = [color_to_enum[c] for c in ci_letters]
    # Build a minimal mana cost string from ci so _is_card_draw etc. can read it
    cost = "{" + str(int(cmc)) + "}"
    for c in ci_letters:
        cost += "{" + c + "}"
    tl = "Creature" if is_creature else ("Artifact" if is_artifact else ("Instant" if is_instant else "Sorcery"))
    return Card(
        scryfall_id=f"sid-{name}", oracle_id=f"oid-{name}", name=name,
        layout=CardLayout.NORMAL, cmc=float(cmc), mana_cost=cost,
        type_line=tl, oracle_text=oracle, color_identity=ci,
        tags=list(tags),
        is_creature=is_creature, is_artifact=is_artifact, is_instant=is_instant,
    )


def _entry(name: str) -> DeckEntry:
    return DeckEntry(card_name=name, quantity=1, zone=Zone.MAINBOARD, card=_mk_card(name))


def _basic_lands(n: int, kind: str = "Forest") -> list[DeckEntry]:
    ci = {"Plains": Color.WHITE, "Island": Color.BLUE, "Swamp": Color.BLACK,
          "Mountain": Color.RED, "Forest": Color.GREEN}.get(kind)
    out = []
    for i in range(n):
        nm = f"{kind}{i}"
        card = Card(
            scryfall_id=f"l-{nm}", oracle_id=f"lo-{nm}", name=nm,
            layout=CardLayout.NORMAL, is_land=True,
            type_line=f"Basic Land — {kind}",
            color_identity=[ci] if ci else [],
        )
        out.append(DeckEntry(card_name=nm, quantity=1, zone=Zone.MAINBOARD, card=card))
    return out


def _mixed_lands(n: int, colors: list[Color]) -> list[DeckEntry]:
    """A split of basic lands across `colors` to pad total land count."""
    if not colors:
        colors = [Color.GREEN]
    kind_by_color = {Color.WHITE: "Plains", Color.BLUE: "Island", Color.BLACK: "Swamp",
                     Color.RED: "Mountain", Color.GREEN: "Forest"}
    out: list[DeckEntry] = []
    for i in range(n):
        color = colors[i % len(colors)]
        out.extend(_basic_lands(1, kind_by_color[color]))
        out[-1].card_name = f"{kind_by_color[color]}{i}"
        out[-1].card.name = f"{kind_by_color[color]}{i}"
        out[-1].card.scryfall_id = f"l-{kind_by_color[color]}-{i}"
    return out


def _pad_to_100(entries: list[DeckEntry], pad_card_names: list[str]) -> list[DeckEntry]:
    """Top up with unique synthetic `Filler_NN` cards until quantity=100.

    The old version cycled through a short list of real card names (e.g.
    Forest Bear, Grizzly Bears) which meant the same card name appeared 5+
    times in a deck — breaking Commander singleton semantics AND polluting
    the cut ranker's top slots with duplicates. Now every pad slot gets a
    unique synthetic name so the ranker sees distinct candidates and
    gold-set cards actually make it into the top-N.

    The `pad_card_names` argument is retained for backwards compat but
    unused — the pad cards are generic low-CMC vanilla creatures that
    mimic "the rest of the deck that isn't the target of analysis".
    """
    _ = pad_card_names  # kept for backwards compat; no longer used
    next_id = 1
    while sum(e.quantity for e in entries) < 100:
        name = f"FillerCreature{next_id:02d}"
        card = Card(
            scryfall_id=f"fc-{next_id}", oracle_id=f"fc-{next_id}", name=name,
            layout=CardLayout.NORMAL, cmc=3.0, mana_cost="{3}",
            type_line="Creature — Beast",
            oracle_text="",  # No tags via classifier; verifier sees `no_functional_tag`
            color_identity=[],
            tags=[],  # empty -> surfaces as cut candidate but late in the list
            is_creature=True,
        )
        entries.append(DeckEntry(card_name=name, quantity=1, zone=Zone.MAINBOARD, card=card))
        next_id += 1
    return entries[:100]


def _mk_analysis(
    deck_name: str,
    land_count: int, ramp: int, draw: int, interaction: int, avg_cmc: float,
    recommendations: list[str] | None = None,
) -> AnalysisResult:
    return AnalysisResult(
        deck_name=deck_name, format="commander", total_cards=100,
        land_count=land_count, ramp_count=ramp, draw_engine_count=draw,
        interaction_count=interaction, average_cmc=avg_cmc,
        recommendations=recommendations or [],
    )


def _mk_power(overall: float, tier: str, up: list[str], down: list[str]) -> PowerBreakdown:
    pb = PowerBreakdown()
    pb.overall = overall
    pb.tier = tier
    pb.reasons_up = up
    pb.reasons_down = down
    return pb


# =============================================================================
# Case definitions — 30 decks
# =============================================================================


def all_cases():
    """Return all 30 gauntlet cases as (case_id, description, build_deck, build_analysis, gold_cuts, gold_add_roles)."""
    cases = []

    # ----- 1-6: RAMP OVER-CAP ---------------------------------------------

    def _c01():
        ramp = ["Cultivate", "Kodama's Reach", "Farseek", "Rampant Growth", "Three Visits",
                "Nature's Lore", "Skyshroud Claim", "Explosive Vegetation", "Harrow",
                "Search for Tomorrow", "Circuitous Route", "Sol Ring", "Arcane Signet",
                "Commander's Sphere", "Mind Stone", "Thought Vessel", "Llanowar Elves"]
        filler = ["Pelakka Wurm", "Yavimaya Wurm", "Siege Wurm", "Warstorm Surge"]
        draw = ["Sylvan Library", "Harmonize", "Beast Whisperer", "Bident of Thassa",
                "Rhystic Study", "Ponder", "Brainstorm", "Night's Whisper"]
        removal = ["Beast Within", "Nature's Claim", "Assassin's Trophy", "Reclamation Sage",
                   "Generous Gift", "Chaos Warp", "Terminate", "Doom Blade"]
        finishers = ["Craterhoof Behemoth", "Avenger of Zendikar", "Terastodon"]
        entries = [_entry(n) for n in ramp + filler + draw + removal + finishers]
        entries += _mixed_lands(36, [Color.GREEN])
        _pad_to_100(entries, ["Grizzly Bears", "Forest Bear", "Runeclaw Bear"])
        return Deck(name="Mono-G Ramp Heavy", format=Format.COMMANDER, entries=entries)
    cases.append(("ramp_heavy_01", "Mono-G ramp with 17 ramp pieces + 4 vanilla fillers",
                  _c01,
                  lambda: (_mk_analysis("Mono-G Ramp Heavy", 36, 17, 8, 8, 3.2, ["Trim ramp"]),
                           _mk_power(6.5, "focused", ["Heavy ramp"], ["Top-heavy curve"]), "ramp"),
                  {"Pelakka Wurm", "Yavimaya Wurm", "Siege Wurm", "Warstorm Surge"},
                  set()))

    def _c02():
        ramp = ["Cultivate", "Kodama's Reach", "Farseek", "Rampant Growth", "Three Visits",
                "Nature's Lore", "Skyshroud Claim", "Harrow", "Sol Ring", "Arcane Signet",
                "Commander's Sphere", "Mind Stone", "Thought Vessel", "Fellwar Stone",
                "Llanowar Elves", "Elvish Mystic", "Fyndhorn Elves", "Coldsteel Heart"]
        filler = ["Pelakka Wurm", "Yavimaya Wurm", "Gurmag Swiftwing"]
        draw = ["Phyrexian Arena", "Sylvan Library", "Night's Whisper", "Read the Bones",
                "Harmonize", "Beast Whisperer"]
        removal = ["Beast Within", "Assassin's Trophy", "Putrefy", "Doom Blade", "Terminate",
                   "Go for the Throat", "Feed the Swarm", "Anguished Unmaking"]
        finishers = ["Avenger of Zendikar", "Sheoldred, Whispering One"]
        wipes = ["Toxic Deluge"]
        entries = [_entry(n) for n in ramp + filler + draw + removal + finishers + wipes]
        entries += _mixed_lands(35, [Color.BLACK, Color.GREEN])
        _pad_to_100(entries, ["Forest Bear", "Grizzly Bears"])
        return Deck(name="Golgari Ramp", format=Format.COMMANDER, entries=entries)
    cases.append(("ramp_heavy_02", "BG Golgari 18 ramp + 3 vanilla filler",
                  _c02,
                  lambda: (_mk_analysis("Golgari Ramp", 35, 18, 6, 8, 3.0, ["Trim ramp"]),
                           _mk_power(6.8, "focused", ["Deep ramp"], ["Low draw"]), "midrange"),
                  {"Pelakka Wurm", "Yavimaya Wurm", "Gurmag Swiftwing"},
                  {CardTag.CARD_DRAW}))

    def _c03():
        ramp = ["Sol Ring", "Arcane Signet", "Commander's Sphere", "Mind Stone",
                "Thought Vessel", "Fellwar Stone", "Prismatic Lens", "Coldsteel Heart",
                "Cultivate", "Kodama's Reach", "Farseek", "Rampant Growth",
                "Skyshroud Claim", "Llanowar Elves", "Birds of Paradise", "Oracle of Mul Daya"]
        filler = ["Siege Wurm", "Pristine Angel", "Warstorm Surge", "Hungering Yeti", "Pelakka Wurm"]
        draw = ["Rhystic Study", "Mystic Remora", "Ponder", "Brainstorm"]
        removal = ["Path to Exile", "Swords to Plowshares", "Generous Gift", "Beast Within",
                   "Counterspell", "Negate", "Cyclonic Rift"]
        finishers = ["Elesh Norn, Grand Cenobite", "Consecrated Sphinx"]
        entries = [_entry(n) for n in ramp + filler + draw + removal + finishers]
        entries += _mixed_lands(36, [Color.WHITE, Color.BLUE, Color.GREEN])
        _pad_to_100(entries, ["Grizzly Bears"])
        return Deck(name="Bant Ramp Bloat", format=Format.COMMANDER, entries=entries)
    cases.append(("ramp_heavy_03", "Bant 16 ramp + 5 high-CMC vanilla",
                  _c03,
                  lambda: (_mk_analysis("Bant Ramp Bloat", 36, 16, 4, 9, 3.4, ["Trim ramp", "Curve is top-heavy"]),
                           _mk_power(6.7, "focused", ["Strong ramp"], ["Top-heavy curve"]), "ramp"),
                  {"Siege Wurm", "Pristine Angel", "Warstorm Surge", "Hungering Yeti", "Pelakka Wurm"},
                  {CardTag.CARD_DRAW}))

    def _c04():
        ramp = ["Sol Ring", "Arcane Signet", "Commander's Sphere", "Mind Stone", "Thought Vessel",
                "Fellwar Stone", "Prismatic Lens", "Coldsteel Heart", "Cultivate", "Kodama's Reach",
                "Farseek", "Rampant Growth", "Skyshroud Claim", "Circuitous Route",
                "Harrow", "Explosive Vegetation", "Llanowar Elves", "Fyndhorn Elves",
                "Elvish Mystic", "Birds of Paradise"]
        filler = ["Pelakka Wurm"]
        draw = ["Rhystic Study", "Phyrexian Arena", "Sylvan Library", "Harmonize", "Ponder"]
        removal = ["Swords to Plowshares", "Beast Within", "Chaos Warp", "Anguished Unmaking",
                   "Terminate", "Counterspell", "Cyclonic Rift"]
        finishers = ["Craterhoof Behemoth"]
        entries = [_entry(n) for n in ramp + filler + draw + removal + finishers]
        entries += _mixed_lands(36, list(Color))
        _pad_to_100(entries, ["Grizzly Bears", "Forest Bear"])
        return Deck(name="5-Color Ramp Overkill", format=Format.COMMANDER, entries=entries)
    cases.append(("ramp_heavy_04", "5-color 20 ramp (deep over-cap)",
                  _c04,
                  lambda: (_mk_analysis("5-Color Ramp Overkill", 36, 20, 5, 7, 2.8, ["Trim ramp significantly"]),
                           _mk_power(6.3, "focused", ["Massive ramp"], ["Draw is thin"]), "ramp"),
                  {"Pelakka Wurm"},
                  {CardTag.CARD_DRAW, CardTag.TARGETED_REMOVAL}))

    def _c05():
        ramp = ["Sol Ring", "Arcane Signet", "Commander's Sphere", "Mind Stone", "Thought Vessel",
                "Cultivate", "Kodama's Reach", "Farseek", "Rampant Growth", "Skyshroud Claim",
                "Llanowar Elves", "Elvish Mystic", "Birds of Paradise", "Oracle of Mul Daya"]
        filler = ["Kessig Wolf", "Hungering Yeti", "Pelakka Wurm", "Jade Mage"]
        draw = ["Harmonize", "Beast Whisperer", "Sylvan Library"]
        removal = ["Beast Within", "Chaos Warp", "Nature's Claim", "Reclamation Sage", "Terminate"]
        finishers = ["Craterhoof Behemoth"]
        entries = [_entry(n) for n in ramp + filler + draw + removal + finishers]
        entries += _mixed_lands(36, [Color.RED, Color.GREEN])
        _pad_to_100(entries, ["Grizzly Bears", "Runeclaw Bear"])
        return Deck(name="Gruul Ramp", format=Format.COMMANDER, entries=entries)
    cases.append(("ramp_heavy_05", "RG Gruul 14 ramp + 4 low-signal creatures",
                  _c05,
                  lambda: (_mk_analysis("Gruul Ramp", 36, 14, 3, 5, 3.3, ["Add draw", "Add interaction"]),
                           _mk_power(5.5, "casual", ["Solid ramp"], ["Very low draw", "Thin interaction"]), "ramp"),
                  {"Kessig Wolf", "Hungering Yeti", "Pelakka Wurm", "Jade Mage"},
                  {CardTag.CARD_DRAW, CardTag.TARGETED_REMOVAL}))

    def _c06():
        ramp = (["Cultivate", "Kodama's Reach", "Farseek", "Rampant Growth", "Three Visits",
                 "Nature's Lore", "Skyshroud Claim", "Explosive Vegetation", "Harrow",
                 "Sol Ring", "Arcane Signet", "Commander's Sphere", "Mind Stone", "Thought Vessel",
                 "Llanowar Elves", "Elvish Mystic", "Fyndhorn Elves", "Birds of Paradise"])
        filler = ["Pelakka Wurm", "Yavimaya Wurm", "Siege Wurm", "Garruk's Packleader",
                  "Jade Mage", "Forest Bear"]
        draw = ["Sylvan Library", "Harmonize"]
        removal = ["Beast Within", "Nature's Claim", "Reclamation Sage"]
        finishers = ["Craterhoof Behemoth"]
        entries = [_entry(n) for n in ramp + filler + draw + removal + finishers]
        entries += _mixed_lands(36, [Color.GREEN])
        _pad_to_100(entries, ["Grizzly Bears"])
        return Deck(name="Mono-G Fatties", format=Format.COMMANDER, entries=entries)
    cases.append(("ramp_heavy_06", "Mono-G 18 ramp + 6 vanilla fillers (severe over-cap)",
                  _c06,
                  lambda: (_mk_analysis("Mono-G Fatties", 36, 18, 2, 3, 3.6, ["Trim ramp", "Add draw", "Add interaction"]),
                           _mk_power(5.0, "casual", ["Deep ramp"], ["Almost no draw", "Almost no interaction"]), "ramp"),
                  {"Pelakka Wurm", "Yavimaya Wurm", "Siege Wurm", "Garruk's Packleader"},
                  {CardTag.CARD_DRAW, CardTag.TARGETED_REMOVAL}))

    # ----- 7-11: LOW INTERACTION ------------------------------------------

    def _c07():
        ramp = ["Sol Ring", "Arcane Signet", "Mind Stone", "Cultivate", "Kodama's Reach",
                "Farseek", "Rampant Growth", "Llanowar Elves", "Birds of Paradise"]
        draw = ["Rhystic Study", "Sylvan Library", "Ponder", "Brainstorm", "Fact or Fiction",
                "Harmonize", "Beast Whisperer", "Bident of Thassa", "Phyrexian Arena"]
        removal = ["Counterspell", "Beast Within"]
        finishers = ["Craterhoof Behemoth", "Consecrated Sphinx"]
        creatures = ["Eternal Witness", "Oracle of Mul Daya", "Reclamation Sage"]
        entries = [_entry(n) for n in ramp + draw + removal + finishers + creatures]
        entries += _mixed_lands(38, [Color.BLUE, Color.GREEN])
        _pad_to_100(entries, ["Forest Bear", "Grizzly Bears"])
        return Deck(name="Simic Value", format=Format.COMMANDER, entries=entries)
    cases.append(("low_interact_01", "Simic midrange, 2 removal (target 8-12)",
                  _c07,
                  lambda: (_mk_analysis("Simic Value", 38, 9, 10, 2, 3.1, ["Add removal"]),
                           _mk_power(6.0, "focused", ["Deep draw"], ["Almost no removal"]), "midrange"),
                  set(),
                  {CardTag.TARGETED_REMOVAL}))

    def _c08():
        ramp = ["Sol Ring", "Arcane Signet", "Mind Stone", "Thought Vessel", "Fellwar Stone",
                "Commander's Sphere"]
        draw = ["Rhystic Study", "Mystic Remora", "Ponder", "Brainstorm", "Fact or Fiction",
                "Bident of Thassa", "Read the Bones"]
        counters = ["Counterspell", "Swan Song", "Negate", "Mana Drain"]
        wipes = ["Cyclonic Rift"]
        finishers = ["Consecrated Sphinx"]
        entries = [_entry(n) for n in ramp + draw + counters + wipes + finishers]
        entries += _mixed_lands(36, [Color.BLUE])
        _pad_to_100(entries, ["Frost Lynx"])
        return Deck(name="Mono-U Control", format=Format.COMMANDER, entries=entries)
    cases.append(("low_interact_02", "Mono-U control with counters but 0 targeted removal",
                  _c08,
                  lambda: (_mk_analysis("Mono-U Control", 36, 6, 7, 4, 2.9, ["Add targeted removal"]),
                           _mk_power(6.2, "focused", ["Counters"], ["No targeted removal"]), "control"),
                  set(),
                  {CardTag.TARGETED_REMOVAL}))

    def _c09():
        ramp = ["Sol Ring", "Arcane Signet", "Cultivate", "Rampant Growth", "Farseek",
                "Llanowar Elves", "Elvish Mystic"]
        draw = ["Sylvan Library", "Harmonize", "Beast Whisperer"]
        removal = ["Beast Within"]
        finishers = ["Craterhoof Behemoth", "Avenger of Zendikar"]
        creatures = ["Reclamation Sage", "Eternal Witness"]
        filler = ["Kessig Wolf", "Hungering Yeti", "Pelakka Wurm"]
        entries = [_entry(n) for n in ramp + draw + removal + finishers + creatures + filler]
        entries += _mixed_lands(36, [Color.RED, Color.GREEN])
        _pad_to_100(entries, ["Grizzly Bears", "Runeclaw Bear"])
        return Deck(name="Gruul Big Mana", format=Format.COMMANDER, entries=entries)
    cases.append(("low_interact_03", "Gruul 2 removal only",
                  _c09,
                  lambda: (_mk_analysis("Gruul Big Mana", 36, 7, 3, 2, 3.5, ["Add removal"]),
                           _mk_power(5.3, "casual", [], ["Low removal", "Low draw"]), "ramp"),
                  {"Pelakka Wurm", "Hungering Yeti", "Kessig Wolf"},
                  {CardTag.TARGETED_REMOVAL, CardTag.CARD_DRAW}))

    def _c10():
        ramp = ["Sol Ring", "Arcane Signet", "Commander's Sphere", "Mind Stone", "Thought Vessel",
                "Fellwar Stone", "Prismatic Lens", "Cultivate", "Farseek"]
        draw = ["Rhystic Study", "Phyrexian Arena", "Sylvan Library", "Harmonize", "Fact or Fiction"]
        removal = ["Swords to Plowshares", "Beast Within", "Counterspell", "Terminate", "Putrefy"]
        finishers = ["Elesh Norn, Grand Cenobite"]
        creatures = ["Eternal Witness"]
        entries = [_entry(n) for n in ramp + draw + removal + finishers + creatures]
        entries += _mixed_lands(36, [Color.WHITE, Color.BLUE, Color.BLACK, Color.GREEN])
        _pad_to_100(entries, ["Forest Bear"])
        return Deck(name="Four-Color Goodstuff", format=Format.COMMANDER, entries=entries)
    cases.append(("low_interact_04", "4-color 5 removal (under target)",
                  _c10,
                  lambda: (_mk_analysis("Four-Color Goodstuff", 36, 9, 5, 5, 3.0, ["Add removal"]),
                           _mk_power(6.4, "focused", ["Broad tools"], ["Mid-tier removal"]), "midrange"),
                  set(),
                  {CardTag.TARGETED_REMOVAL}))

    def _c11():
        ramp = ["Sol Ring", "Arcane Signet", "Mind Stone", "Commander's Sphere", "Cultivate",
                "Farseek", "Rampant Growth", "Llanowar Elves"]
        draw = ["Phyrexian Arena", "Sylvan Library", "Read the Bones", "Night's Whisper", "Harmonize"]
        removal = ["Beast Within", "Doom Blade", "Terminate", "Putrefy"]
        finishers = ["Sheoldred, Whispering One"]
        creatures = ["Eternal Witness", "Reclamation Sage"]
        entries = [_entry(n) for n in ramp + draw + removal + finishers + creatures]
        entries += _mixed_lands(36, [Color.BLACK, Color.GREEN])
        _pad_to_100(entries, ["Grizzly Bears", "Forest Bear"])
        return Deck(name="Golgari Midrange", format=Format.COMMANDER, entries=entries)
    cases.append(("low_interact_05", "BG Golgari 4 removal",
                  _c11,
                  lambda: (_mk_analysis("Golgari Midrange", 36, 8, 5, 4, 3.0, ["Add removal"]),
                           _mk_power(5.8, "casual", ["Stable value"], ["Low removal"]), "midrange"),
                  set(),
                  {CardTag.TARGETED_REMOVAL}))

    # ----- 12-15: LOW DRAW -----------------------------------------------

    def _c12():
        ramp = ["Sol Ring", "Arcane Signet", "Mind Stone", "Fellwar Stone", "Prismatic Lens"]
        draw = ["Read the Bones", "Night's Whisper"]
        removal = ["Swords to Plowshares", "Path to Exile", "Generous Gift", "Anguished Unmaking",
                   "Terminate", "Doom Blade", "Vindicate", "Go for the Throat"]
        finishers = ["Elesh Norn, Grand Cenobite", "Craterhoof Behemoth"]
        creatures = ["Solemn Simulacrum", "Reclamation Sage"]
        filler = ["Pristine Angel"]
        entries = [_entry(n) for n in ramp + draw + removal + finishers + creatures + filler]
        entries += _mixed_lands(36, [Color.RED])
        _pad_to_100(entries, ["Runeclaw Bear", "Grizzly Bears"])
        return Deck(name="Mono-R Aggro-ish", format=Format.COMMANDER, entries=entries)
    cases.append(("low_draw_01", "Mono-R 2 draw pieces",
                  _c12,
                  lambda: (_mk_analysis("Mono-R Aggro-ish", 36, 5, 2, 8, 3.0, ["Add draw"]),
                           _mk_power(5.5, "casual", [], ["Very low draw"]), "aggro"),
                  set(),
                  {CardTag.CARD_DRAW}))

    def _c13():
        ramp = ["Sol Ring", "Arcane Signet", "Commander's Sphere", "Llanowar Elves",
                "Cultivate", "Rampant Growth", "Farseek", "Nature's Lore", "Birds of Paradise",
                "Thought Vessel"]
        draw = ["Sylvan Library", "Harmonize", "Beast Whisperer", "Bident of Thassa"]
        removal = ["Swords to Plowshares", "Beast Within", "Generous Gift", "Path to Exile",
                   "Nature's Claim", "Chaos Warp", "Reclamation Sage"]
        wipes = ["Wrath of God"]
        finishers = ["Craterhoof Behemoth", "Elesh Norn, Grand Cenobite"]
        creatures = ["Eternal Witness", "Reclamation Sage"]
        entries = [_entry(n) for n in ramp + draw + removal + wipes + finishers + creatures]
        entries += _mixed_lands(36, [Color.WHITE, Color.GREEN])
        _pad_to_100(entries, ["Forest Bear", "Grizzly Bears"])
        return Deck(name="Selesnya Creatures", format=Format.COMMANDER, entries=entries)
    cases.append(("low_draw_02", "GW Selesnya 4 draw (under target)",
                  _c13,
                  lambda: (_mk_analysis("Selesnya Creatures", 36, 10, 4, 8, 3.1, ["Add draw"]),
                           _mk_power(6.0, "focused", [], ["Low draw"]), "midrange"),
                  set(),
                  {CardTag.CARD_DRAW}))

    def _c14():
        ramp = ["Sol Ring", "Arcane Signet", "Mind Stone", "Thought Vessel", "Fellwar Stone",
                "Commander's Sphere", "Prismatic Lens"]
        draw = ["Read the Bones", "Night's Whisper", "Ponder"]
        removal = ["Swords to Plowshares", "Path to Exile", "Anguished Unmaking", "Vindicate",
                   "Counterspell"]
        finishers = ["Elesh Norn, Grand Cenobite"]
        creatures = ["Solemn Simulacrum"]
        entries = [_entry(n) for n in ramp + draw + removal + finishers + creatures]
        entries += _mixed_lands(36, [Color.WHITE, Color.RED])
        _pad_to_100(entries, ["Pristine Angel", "Frost Lynx"])
        return Deck(name="Boros Aggro", format=Format.COMMANDER, entries=entries)
    cases.append(("low_draw_03", "RW Boros 3 draw",
                  _c14,
                  lambda: (_mk_analysis("Boros Aggro", 36, 7, 3, 5, 2.8, ["Add draw"]),
                           _mk_power(5.3, "casual", [], ["Very low draw"]), "aggro"),
                  set(),
                  {CardTag.CARD_DRAW}))

    def _c15():
        ramp = ["Sol Ring", "Arcane Signet", "Mind Stone", "Commander's Sphere",
                "Cultivate", "Farseek", "Rampant Growth", "Birds of Paradise",
                "Llanowar Elves", "Fyndhorn Elves", "Thought Vessel"]
        draw = ["Harmonize", "Sylvan Library", "Beast Whisperer", "Mystic Remora", "Brainstorm"]
        removal = ["Swords to Plowshares", "Counterspell", "Beast Within", "Generous Gift"]
        wipes = ["Wrath of God", "Cyclonic Rift"]
        finishers = ["Craterhoof Behemoth", "Elesh Norn, Grand Cenobite", "Consecrated Sphinx"]
        creatures = ["Eternal Witness"]
        entries = [_entry(n) for n in ramp + draw + removal + wipes + finishers + creatures]
        entries += _mixed_lands(36, [Color.WHITE, Color.BLUE, Color.GREEN])
        _pad_to_100(entries, ["Forest Bear"])
        return Deck(name="Bant Midrange", format=Format.COMMANDER, entries=entries)
    cases.append(("low_draw_04", "Bant 5 draw (under target)",
                  _c15,
                  lambda: (_mk_analysis("Bant Midrange", 36, 11, 5, 6, 3.0, ["Add draw"]),
                           _mk_power(6.4, "focused", ["Balanced shape"], ["Low draw"]), "midrange"),
                  set(),
                  {CardTag.CARD_DRAW}))

    # ----- 16-19: LOW RAMP ------------------------------------------------

    def _c16():
        ramp = ["Sol Ring", "Arcane Signet", "Mind Stone", "Thought Vessel"]
        draw = ["Rhystic Study", "Mystic Remora", "Ponder", "Brainstorm", "Fact or Fiction",
                "Bident of Thassa", "Read the Bones"]
        counters = ["Counterspell", "Swan Song", "Negate", "Mana Drain"]
        removal = ["Cyclonic Rift"]
        finishers = ["Consecrated Sphinx"]
        entries = [_entry(n) for n in ramp + draw + counters + removal + finishers]
        entries += _mixed_lands(36, [Color.BLUE])
        _pad_to_100(entries, ["Frost Lynx"])
        return Deck(name="Mono-U Control Thin Ramp", format=Format.COMMANDER, entries=entries)
    cases.append(("low_ramp_01", "Mono-U control only 4 ramp",
                  _c16,
                  lambda: (_mk_analysis("Mono-U Control Thin Ramp", 36, 4, 7, 5, 3.0, ["Add ramp"]),
                           _mk_power(5.8, "casual", [], ["Slow mana development"]), "control"),
                  set(),
                  {CardTag.RAMP}))

    def _c17():
        ramp = ["Sol Ring", "Arcane Signet", "Commander's Sphere", "Mind Stone", "Fellwar Stone",
                "Thought Vessel"]
        draw = ["Phyrexian Arena", "Read the Bones", "Night's Whisper"]
        removal = ["Swords to Plowshares", "Path to Exile", "Anguished Unmaking",
                   "Terminate", "Go for the Throat", "Chaos Warp", "Vindicate"]
        finishers = ["Elesh Norn, Grand Cenobite"]
        creatures = ["Solemn Simulacrum"]
        entries = [_entry(n) for n in ramp + draw + removal + finishers + creatures]
        entries += _mixed_lands(36, [Color.WHITE, Color.BLACK, Color.RED])
        _pad_to_100(entries, ["Pristine Angel", "Frost Lynx"])
        return Deck(name="Mardu Midrange", format=Format.COMMANDER, entries=entries)
    cases.append(("low_ramp_02", "Mardu 6 ramp",
                  _c17,
                  lambda: (_mk_analysis("Mardu Midrange", 36, 6, 3, 7, 3.1, ["Add ramp", "Add draw"]),
                           _mk_power(5.6, "casual", [], ["Thin ramp"]), "midrange"),
                  set(),
                  {CardTag.RAMP, CardTag.CARD_DRAW}))

    def _c18():
        ramp = ["Sol Ring", "Arcane Signet", "Mind Stone", "Fellwar Stone", "Prismatic Lens"]
        draw = ["Read the Bones", "Night's Whisper", "Brainstorm", "Ponder"]
        removal = ["Swords to Plowshares", "Path to Exile", "Anguished Unmaking",
                   "Counterspell", "Vindicate", "Negate"]
        finishers = ["Elesh Norn, Grand Cenobite"]
        entries = [_entry(n) for n in ramp + draw + removal + finishers]
        entries += _mixed_lands(36, [Color.WHITE, Color.RED])
        _pad_to_100(entries, ["Pristine Angel", "Frost Lynx", "Runeclaw Bear"])
        return Deck(name="Boros Control", format=Format.COMMANDER, entries=entries)
    cases.append(("low_ramp_03", "Boros 5 ramp",
                  _c18,
                  lambda: (_mk_analysis("Boros Control", 36, 5, 4, 6, 2.9, ["Add ramp"]),
                           _mk_power(5.4, "casual", [], ["Thin ramp"]), "control"),
                  set(),
                  {CardTag.RAMP}))

    def _c19():
        ramp = ["Sol Ring", "Arcane Signet", "Mind Stone", "Commander's Sphere"]
        draw = ["Rhystic Study", "Phyrexian Arena", "Read the Bones", "Fact or Fiction",
                "Mystic Remora"]
        removal = ["Swords to Plowshares", "Path to Exile", "Counterspell", "Terminate",
                   "Anguished Unmaking", "Mana Drain", "Negate", "Cyclonic Rift"]
        wipes = ["Wrath of God", "Damnation"]
        finishers = ["Elesh Norn, Grand Cenobite"]
        entries = [_entry(n) for n in ramp + draw + removal + wipes + finishers]
        entries += _mixed_lands(36, [Color.WHITE, Color.BLUE, Color.BLACK])
        _pad_to_100(entries, ["Solemn Simulacrum", "Pristine Angel"])
        return Deck(name="Esper Control", format=Format.COMMANDER, entries=entries)
    cases.append(("low_ramp_04", "Esper 4 ramp",
                  _c19,
                  lambda: (_mk_analysis("Esper Control", 36, 4, 6, 10, 2.8, ["Add ramp"]),
                           _mk_power(6.5, "focused", ["Strong interaction"], ["Thin ramp"]), "control"),
                  set(),
                  {CardTag.RAMP}))

    # ----- 20-24: HIGH-CMC BLOAT ------------------------------------------

    def _c20():
        ramp = ["Sol Ring", "Arcane Signet", "Cultivate", "Kodama's Reach", "Farseek",
                "Skyshroud Claim", "Explosive Vegetation", "Llanowar Elves", "Birds of Paradise",
                "Commander's Sphere", "Mind Stone"]
        draw = ["Sylvan Library", "Harmonize", "Bident of Thassa"]
        removal = ["Beast Within", "Nature's Claim", "Reclamation Sage", "Generous Gift"]
        filler = ["Pelakka Wurm", "Yavimaya Wurm", "Siege Wurm", "Warstorm Surge",
                  "Garruk's Packleader", "Jade Mage"]
        finishers = ["Craterhoof Behemoth", "Avenger of Zendikar"]
        entries = [_entry(n) for n in ramp + draw + removal + filler + finishers]
        entries += _mixed_lands(36, [Color.GREEN, Color.WHITE])
        _pad_to_100(entries, ["Forest Bear"])
        return Deck(name="Selesnya Fatties", format=Format.COMMANDER, entries=entries)
    cases.append(("bloat_01", "6 high-CMC non-finisher fillers",
                  _c20,
                  lambda: (_mk_analysis("Selesnya Fatties", 36, 11, 3, 4, 3.7, ["Trim high-CMC"]),
                           _mk_power(5.5, "casual", [], ["Top-heavy curve"]), "midrange"),
                  {"Pelakka Wurm", "Yavimaya Wurm", "Siege Wurm", "Warstorm Surge",
                   "Garruk's Packleader"},
                  {CardTag.CARD_DRAW}))

    def _c21():
        ramp = ["Sol Ring", "Arcane Signet", "Cultivate", "Kodama's Reach", "Farseek",
                "Rampant Growth", "Nature's Lore", "Skyshroud Claim", "Llanowar Elves",
                "Mind Stone", "Fellwar Stone"]
        draw = ["Phyrexian Arena", "Sylvan Library", "Night's Whisper", "Read the Bones"]
        removal = ["Beast Within", "Terminate", "Putrefy", "Go for the Throat"]
        filler = ["Pelakka Wurm", "Yavimaya Wurm", "Siege Wurm", "Gurmag Swiftwing",
                  "Hungering Yeti"]
        finishers = ["Craterhoof Behemoth", "Sheoldred, Whispering One"]
        entries = [_entry(n) for n in ramp + draw + removal + filler + finishers]
        entries += _mixed_lands(36, [Color.BLACK, Color.GREEN, Color.RED])
        _pad_to_100(entries, ["Grizzly Bears", "Forest Bear"])
        return Deck(name="Jund Ramp Bloat", format=Format.COMMANDER, entries=entries)
    cases.append(("bloat_02", "Jund ramp into 5 vanilla 7-drops",
                  _c21,
                  lambda: (_mk_analysis("Jund Ramp Bloat", 36, 11, 4, 4, 3.9, ["Trim high-CMC", "Add draw"]),
                           _mk_power(5.7, "casual", ["Decent ramp"], ["Top-heavy"]), "ramp"),
                  {"Pelakka Wurm", "Yavimaya Wurm", "Siege Wurm", "Gurmag Swiftwing",
                   "Hungering Yeti"},
                  {CardTag.CARD_DRAW, CardTag.TARGETED_REMOVAL}))

    def _c22():
        ramp = ["Sol Ring", "Arcane Signet", "Llanowar Elves", "Birds of Paradise",
                "Cultivate", "Farseek", "Rampant Growth", "Elvish Mystic"]
        draw = ["Sylvan Library", "Harmonize"]
        removal = ["Swords to Plowshares", "Beast Within", "Chaos Warp", "Generous Gift"]
        filler = ["Pelakka Wurm", "Hungering Yeti", "Kessig Wolf", "Pristine Angel", "Jade Mage"]
        finishers = ["Craterhoof Behemoth"]
        entries = [_entry(n) for n in ramp + draw + removal + filler + finishers]
        entries += _mixed_lands(36, [Color.RED, Color.WHITE, Color.GREEN])
        _pad_to_100(entries, ["Grizzly Bears", "Forest Bear", "Runeclaw Bear"])
        return Deck(name="Naya Vanilla", format=Format.COMMANDER, entries=entries)
    cases.append(("bloat_03", "Naya with 5 vanilla creatures",
                  _c22,
                  lambda: (_mk_analysis("Naya Vanilla", 36, 8, 2, 4, 3.4, ["Trim vanilla creatures", "Add draw"]),
                           _mk_power(5.2, "casual", [], ["Very low draw", "Vanilla bloat"]), "midrange"),
                  {"Pelakka Wurm", "Hungering Yeti", "Kessig Wolf", "Pristine Angel", "Jade Mage"},
                  {CardTag.CARD_DRAW}))

    def _c23():
        ramp = ["Sol Ring", "Arcane Signet", "Commander's Sphere", "Mind Stone", "Fellwar Stone",
                "Prismatic Lens", "Thought Vessel", "Cultivate", "Farseek", "Llanowar Elves",
                "Birds of Paradise"]
        draw = ["Rhystic Study", "Phyrexian Arena", "Sylvan Library", "Harmonize"]
        removal = ["Swords to Plowshares", "Path to Exile", "Counterspell", "Anguished Unmaking",
                   "Cyclonic Rift"]
        filler = ["Pelakka Wurm", "Siege Wurm", "Warstorm Surge", "Pristine Angel",
                  "Hungering Yeti"]
        finishers = ["Elesh Norn, Grand Cenobite"]
        entries = [_entry(n) for n in ramp + draw + removal + filler + finishers]
        entries += _mixed_lands(36, list(Color))
        _pad_to_100(entries, ["Grizzly Bears", "Forest Bear"])
        return Deck(name="5-Color Superfriends Bloat", format=Format.COMMANDER, entries=entries)
    cases.append(("bloat_04", "5-color with 5 vanilla cards + moderate ramp",
                  _c23,
                  lambda: (_mk_analysis("5-Color Superfriends Bloat", 36, 11, 4, 5, 3.5, ["Trim bloat"]),
                           _mk_power(6.0, "focused", [], ["Top-heavy curve"]), "midrange"),
                  {"Pelakka Wurm", "Siege Wurm", "Warstorm Surge", "Pristine Angel", "Hungering Yeti"},
                  {CardTag.CARD_DRAW, CardTag.TARGETED_REMOVAL}))

    def _c24():
        ramp = ["Sol Ring", "Arcane Signet", "Mind Stone", "Thought Vessel", "Commander's Sphere",
                "Fellwar Stone", "Cultivate"]
        draw = ["Phyrexian Arena", "Read the Bones", "Ponder", "Fact or Fiction"]
        removal = ["Counterspell", "Swan Song", "Terminate", "Doom Blade", "Go for the Throat"]
        filler = ["Pelakka Wurm", "Siege Wurm", "Warstorm Surge", "Gurmag Swiftwing"]
        finishers = ["Consecrated Sphinx", "Sheoldred, Whispering One"]
        entries = [_entry(n) for n in ramp + draw + removal + filler + finishers]
        entries += _mixed_lands(36, [Color.BLUE, Color.BLACK])
        _pad_to_100(entries, ["Frost Lynx", "Forest Bear"])
        return Deck(name="Dimir Bloat Ramp", format=Format.COMMANDER, entries=entries)
    cases.append(("bloat_05", "Dimir ramp with 4 vanilla + 2 finishers",
                  _c24,
                  lambda: (_mk_analysis("Dimir Bloat Ramp", 36, 7, 4, 5, 3.3, ["Trim bloat", "Add draw"]),
                           _mk_power(5.9, "casual", [], ["Vanilla bloat"]), "midrange"),
                  {"Pelakka Wurm", "Siege Wurm", "Warstorm Surge", "Gurmag Swiftwing"},
                  {CardTag.CARD_DRAW, CardTag.TARGETED_REMOVAL, CardTag.RAMP}))

    # ----- 25-27: BALANCED ------------------------------------------------

    def _c25():
        ramp = ["Sol Ring", "Arcane Signet", "Commander's Sphere", "Mind Stone",
                "Thought Vessel", "Prismatic Lens", "Cultivate", "Farseek", "Birds of Paradise",
                "Llanowar Elves", "Solemn Simulacrum"]
        draw = ["Rhystic Study", "Phyrexian Arena", "Sylvan Library", "Fact or Fiction",
                "Harmonize", "Brainstorm", "Bident of Thassa", "Beast Whisperer"]
        removal = ["Swords to Plowshares", "Path to Exile", "Counterspell", "Anguished Unmaking",
                   "Beast Within", "Cyclonic Rift", "Terminate", "Vindicate", "Generous Gift",
                   "Negate"]
        finishers = ["Craterhoof Behemoth", "Elesh Norn, Grand Cenobite", "Consecrated Sphinx"]
        creatures = ["Eternal Witness", "Oracle of Mul Daya", "Reclamation Sage"]
        entries = [_entry(n) for n in ramp + draw + removal + finishers + creatures]
        entries += _mixed_lands(36, [Color.WHITE, Color.BLUE, Color.BLACK, Color.GREEN])
        _pad_to_100(entries, ["Forest Bear"])
        return Deck(name="Atraxa Balanced", format=Format.COMMANDER, entries=entries)
    cases.append(("balanced_01", "Atraxa: all targets in-range",
                  _c25,
                  lambda: (_mk_analysis("Atraxa Balanced", 36, 11, 8, 10, 3.1, []),
                           _mk_power(7.0, "optimized", ["Strong in every axis"], []), "midrange"),
                  set(), set()))

    def _c26():
        ramp = ["Sol Ring", "Arcane Signet", "Mind Stone", "Cultivate", "Rampant Growth",
                "Farseek", "Llanowar Elves", "Birds of Paradise", "Fyndhorn Elves",
                "Commander's Sphere"]
        draw = ["Phyrexian Arena", "Sylvan Library", "Harmonize", "Read the Bones",
                "Night's Whisper", "Beast Whisperer", "Bident of Thassa", "Fact or Fiction"]
        removal = ["Beast Within", "Generous Gift", "Nature's Claim", "Terminate",
                   "Assassin's Trophy", "Doom Blade", "Anguished Unmaking", "Chaos Warp",
                   "Feed the Swarm"]
        finishers = ["Craterhoof Behemoth", "Sheoldred, Whispering One"]
        creatures = ["Eternal Witness", "Solemn Simulacrum", "Reclamation Sage"]
        entries = [_entry(n) for n in ramp + draw + removal + finishers + creatures]
        entries += _mixed_lands(36, [Color.BLACK, Color.RED, Color.GREEN])
        _pad_to_100(entries, ["Forest Bear", "Grizzly Bears"])
        return Deck(name="Prossh Aristocrats", format=Format.COMMANDER, entries=entries)
    cases.append(("balanced_02", "Prossh aristocrats: balanced",
                  _c26,
                  lambda: (_mk_analysis("Prossh Aristocrats", 36, 10, 8, 9, 2.9, []),
                           _mk_power(6.8, "focused", ["Solid shape"], []), "aristocrats"),
                  set(), set()))

    def _c27():
        ramp = ["Sol Ring", "Arcane Signet", "Mind Stone", "Commander's Sphere",
                "Thought Vessel", "Fellwar Stone", "Cultivate", "Farseek", "Rampant Growth",
                "Llanowar Elves", "Elvish Mystic"]
        draw = ["Phyrexian Arena", "Sylvan Library", "Harmonize", "Read the Bones",
                "Beast Whisperer", "Night's Whisper", "Mystic Remora", "Bident of Thassa"]
        removal = ["Swords to Plowshares", "Path to Exile", "Anguished Unmaking", "Beast Within",
                   "Chaos Warp", "Terminate", "Doom Blade", "Generous Gift", "Vindicate",
                   "Feed the Swarm"]
        wipes = ["Wrath of God", "Blasphemous Act"]
        finishers = ["Elesh Norn, Grand Cenobite", "Craterhoof Behemoth"]
        creatures = ["Eternal Witness"]
        entries = [_entry(n) for n in ramp + draw + removal + wipes + finishers + creatures]
        entries += _mixed_lands(36, [Color.WHITE, Color.BLACK, Color.RED, Color.GREEN])
        _pad_to_100(entries, ["Forest Bear"])
        return Deck(name="Edgar Markov Vampires", format=Format.COMMANDER, entries=entries)
    cases.append(("balanced_03", "Edgar Markov: balanced aggressive build",
                  _c27,
                  lambda: (_mk_analysis("Edgar Markov Vampires", 36, 11, 8, 10, 2.8, []),
                           _mk_power(6.5, "focused", ["Balanced"], []), "aggro"),
                  set(), set()))

    # ----- 28-30: OVER-REMOVAL (trim candidates) -------------------------

    def _c28():
        ramp = ["Sol Ring", "Arcane Signet", "Mind Stone", "Thought Vessel", "Commander's Sphere",
                "Fellwar Stone"]
        draw = ["Rhystic Study", "Phyrexian Arena", "Read the Bones", "Fact or Fiction",
                "Mystic Remora", "Sylvan Library", "Ponder"]
        removal = ["Swords to Plowshares", "Path to Exile", "Counterspell", "Swan Song",
                   "Negate", "Mana Drain", "Anguished Unmaking", "Doom Blade", "Terminate",
                   "Go for the Throat", "Vindicate", "Cyclonic Rift", "Chaos Warp",
                   "Generous Gift", "Feed the Swarm"]
        wipes = ["Wrath of God", "Damnation"]
        finishers = ["Elesh Norn, Grand Cenobite", "Consecrated Sphinx"]
        entries = [_entry(n) for n in ramp + draw + removal + wipes + finishers]
        entries += _mixed_lands(36, [Color.WHITE, Color.BLUE, Color.BLACK])
        _pad_to_100(entries, ["Frost Lynx"])
        return Deck(name="Esper Removal Overkill", format=Format.COMMANDER, entries=entries)
    cases.append(("over_removal_01", "Esper control, 15 targeted removal (over cap)",
                  _c28,
                  lambda: (_mk_analysis("Esper Removal Overkill", 36, 6, 7, 15, 2.6, ["Trim removal"]),
                           _mk_power(6.3, "focused", ["Deep interaction"], ["Thin threats"]), "control"),
                  set(), set()))

    def _c29():
        ramp = ["Sol Ring", "Arcane Signet", "Mind Stone", "Commander's Sphere", "Fellwar Stone",
                "Prismatic Lens"]
        draw = ["Read the Bones", "Night's Whisper", "Ponder", "Brainstorm", "Fact or Fiction"]
        removal = ["Swords to Plowshares", "Path to Exile", "Generous Gift", "Vindicate",
                   "Anguished Unmaking", "Terminate", "Doom Blade", "Chaos Warp",
                   "Counterspell", "Mana Drain", "Negate", "Swan Song"]
        wipes = ["Wrath of God", "Blasphemous Act"]
        finishers = ["Elesh Norn, Grand Cenobite"]
        creatures = ["Solemn Simulacrum"]
        entries = [_entry(n) for n in ramp + draw + removal + wipes + finishers + creatures]
        entries += _mixed_lands(36, [Color.WHITE, Color.RED])
        _pad_to_100(entries, ["Pristine Angel", "Frost Lynx"])
        return Deck(name="Boros Angels Overkill", format=Format.COMMANDER, entries=entries)
    cases.append(("over_removal_02", "Boros angels, 14 removal (over cap)",
                  _c29,
                  lambda: (_mk_analysis("Boros Angels Overkill", 36, 6, 5, 14, 2.8, ["Trim removal"]),
                           _mk_power(6.0, "focused", ["Strong interaction"], []), "control"),
                  set(), set()))

    def _c30():
        ramp = ["Sol Ring", "Arcane Signet", "Mind Stone", "Fellwar Stone"]
        draw = ["Rhystic Study", "Phyrexian Arena", "Mystic Remora", "Ponder", "Brainstorm",
                "Fact or Fiction", "Read the Bones"]
        removal = ["Counterspell", "Swan Song", "Negate", "Mana Drain", "Doom Blade",
                   "Terminate", "Go for the Throat", "Feed the Swarm", "Anguished Unmaking",
                   "Cyclonic Rift", "Putrefy", "Vindicate"]
        wipes = ["Damnation", "Toxic Deluge"]
        finishers = ["Consecrated Sphinx", "Sheoldred, Whispering One"]
        entries = [_entry(n) for n in ramp + draw + removal + wipes + finishers]
        entries += _mixed_lands(36, [Color.BLUE, Color.BLACK])
        _pad_to_100(entries, ["Frost Lynx"])
        return Deck(name="Dimir Removal Wall", format=Format.COMMANDER, entries=entries)
    cases.append(("over_removal_03", "Dimir 12+ removal",
                  _c30,
                  lambda: (_mk_analysis("Dimir Removal Wall", 36, 4, 7, 12, 2.7, []),
                           _mk_power(6.2, "focused", ["Deep interaction"], ["Thin ramp"]), "control"),
                  set(),
                  {CardTag.RAMP}))

    return cases
