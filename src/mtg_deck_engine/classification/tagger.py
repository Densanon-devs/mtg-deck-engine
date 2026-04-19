"""Deterministic rules-based card classification engine.

Tags cards by functional role using type line, oracle text, keywords,
and mana cost analysis. Designed to be extended with learned scoring later.
"""

from __future__ import annotations

import re

from mtg_deck_engine.models import Card, CardTag


def classify_card(card: Card) -> list[CardTag]:
    """Apply all classification rules and return functional tags."""
    tags: list[CardTag] = []
    tl = card.type_line.lower()
    ot = card.oracle_text.lower()
    kw = {k.lower() for k in card.keywords}
    mc = card.mana_cost.lower()

    # --- Land classification ---
    if card.is_land:
        tags.append(CardTag.LAND)
        if "basic" in tl:
            tags.append(CardTag.BASIC_LAND)
        elif _is_fetch_land(card):
            tags.append(CardTag.FETCH_LAND)
        elif _is_dual_land(card):
            tags.append(CardTag.DUAL_LAND)
        elif _is_utility_land(card):
            tags.append(CardTag.UTILITY_LAND)

        # MDFC lands
        if card.faces and len(card.faces) >= 2:
            face_types = [f.type_line.lower() for f in card.faces]
            if any("land" in ft for ft in face_types) and any(
                "land" not in ft for ft in face_types
            ):
                tags.append(CardTag.MDFC_LAND)

    # --- Ramp ---
    if _is_ramp(card):
        tags.append(CardTag.RAMP)
    if _is_mana_rock(card):
        tags.append(CardTag.MANA_ROCK)
    if _is_mana_dork(card):
        tags.append(CardTag.MANA_DORK)

    # --- Card advantage ---
    if _is_card_draw(card):
        tags.append(CardTag.CARD_DRAW)
    if _is_tutor(card):
        tags.append(CardTag.TUTOR)
    if _is_cantrip(card):
        tags.append(CardTag.CANTRIP)

    # --- Interaction ---
    if _is_targeted_removal(card):
        tags.append(CardTag.TARGETED_REMOVAL)
    if _is_board_wipe(card):
        tags.append(CardTag.BOARD_WIPE)
    if _is_counterspell(card):
        tags.append(CardTag.COUNTERSPELL)
    if _is_artifact_enchantment_removal(card):
        tags.append(CardTag.ARTIFACT_ENCHANTMENT_REMOVAL)

    # --- Protection ---
    if _is_protection(card):
        tags.append(CardTag.PROTECTION)

    # --- Recursion / graveyard ---
    if _is_recursion(card):
        tags.append(CardTag.RECURSION)
    if _is_graveyard_hate(card):
        tags.append(CardTag.GRAVEYARD_HATE)

    # --- Token generation ---
    if _is_token_maker(card):
        tags.append(CardTag.TOKEN_MAKER)

    # --- Stax ---
    if _is_stax(card):
        tags.append(CardTag.STAX)

    # --- Threats and finishers ---
    if _is_finisher(card):
        tags.append(CardTag.FINISHER)
    elif _is_threat(card):
        tags.append(CardTag.THREAT)

    # --- Engine pieces ---
    if _is_engine(card):
        tags.append(CardTag.ENGINE)

    # --- Equipment and Auras ---
    if "equipment" in tl:
        tags.append(CardTag.EQUIPMENT)
    if "aura" in tl and "enchantment" in tl:
        tags.append(CardTag.AURA)

    # --- Sacrifice ---
    if _is_sacrifice_outlet(card):
        tags.append(CardTag.SACRIFICE_OUTLET)
    if _is_aristocrat_payoff(card):
        tags.append(CardTag.ARISTOCRAT_PAYOFF)

    # --- Cost reduction ---
    if _is_cost_reducer(card):
        tags.append(CardTag.COST_REDUCER)

    # --- Combat tricks ---
    if _is_combat_trick(card):
        tags.append(CardTag.COMBAT_TRICK)

    # --- Lifegain ---
    if _is_lifegain(card):
        tags.append(CardTag.LIFEGAIN)

    return list(set(tags))  # Deduplicate


# =============================================================================
# Classification rules
# =============================================================================


def _is_fetch_land(card: Card) -> bool:
    ot = card.oracle_text.lower()
    return (
        card.is_land
        and "search your library" in ot
        and ("land" in ot)
        and ("put" in ot or "onto the battlefield" in ot)
    )


def _is_dual_land(card: Card) -> bool:
    if not card.is_land:
        return False
    produced = set(card.produced_mana)
    # Check faces for produced mana too
    for face in card.faces:
        produced.update(face.produced_mana)
    color_count = len(produced & {"W", "U", "B", "R", "G"})
    return color_count >= 2


def _is_utility_land(card: Card) -> bool:
    if not card.is_land or "basic" in card.type_line.lower():
        return False
    ot = card.oracle_text.lower()
    # Has an activated ability beyond just tapping for mana
    non_mana_abilities = re.findall(r"\{[^}]*\}.*?:", ot)
    return bool(non_mana_abilities) or "sacrifice" in ot or "when" in ot


def _is_ramp(card: Card) -> bool:
    ot = card.oracle_text.lower()
    if card.is_land:
        return False
    ramp_phrases = [
        "search your library for a",
        "add {",
        "add one mana",
        "add two mana",
        "add three mana",
        "put a land",
        "additional land",
        "land onto the battlefield",
    ]
    return any(phrase in ot for phrase in ramp_phrases)


def _is_mana_rock(card: Card) -> bool:
    if not card.is_artifact or card.is_creature:
        return False
    ot = card.oracle_text.lower()
    return "{t}: add" in ot or "{t}: add {" in ot or "add one mana" in ot


def _is_mana_dork(card: Card) -> bool:
    if not card.is_creature:
        return False
    ot = card.oracle_text.lower()
    return "{t}: add" in ot or "{t}: add {" in ot


def _is_card_draw(card: Card) -> bool:
    ot = card.oracle_text.lower()
    if card.is_land:
        return False
    draw_phrases = [
        "draw two",
        "draw three",
        "draw four",
        "draw cards equal",
        "draws a card",
        "draw a card for each",
        "draw x cards",
        "whenever.*draw a card",
        "at the beginning.*draw",
    ]
    return any(re.search(p, ot) for p in draw_phrases)


def _is_tutor(card: Card) -> bool:
    ot = card.oracle_text.lower()
    if card.is_land:
        return False
    if "search your library" not in ot or "put" not in ot:
        return False
    # Exclude land-fetching ramp spells
    after_search = ot.split("search your library", 1)[1]
    return not re.search(r"for\s+a\s+(?:basic\s+)?land", after_search)


def _is_cantrip(card: Card) -> bool:
    ot = card.oracle_text.lower()
    if card.is_land or card.is_creature:
        return False
    return "draw a card" in ot and card.display_cmc() <= 2


def _is_targeted_removal(card: Card) -> bool:
    ot = card.oracle_text.lower()
    if card.is_land:
        return False
    removal_phrases = [
        "destroy target",
        "exile target",
        "target creature gets -",
        "deals.*damage to target",
        "return target.*to its owner's hand",
        "target player sacrifices",
    ]
    return any(re.search(p, ot) for p in removal_phrases)


def _is_board_wipe(card: Card) -> bool:
    ot = card.oracle_text.lower()
    if card.is_land:
        return False
    wipe_phrases = [
        "destroy all creatures",
        "destroy all nonland",
        "exile all",
        "all creatures get -",
        "deals.*damage to each creature",
        "each player sacrifices",
        "return all",
    ]
    return any(re.search(p, ot) for p in wipe_phrases)


def _is_counterspell(card: Card) -> bool:
    ot = card.oracle_text.lower()
    return "counter target" in ot and (card.is_instant or card.is_sorcery or "flash" in ot)


def _is_artifact_enchantment_removal(card: Card) -> bool:
    ot = card.oracle_text.lower()
    if card.is_land:
        return False
    return ("destroy target" in ot or "exile target" in ot) and (
        "artifact" in ot or "enchantment" in ot
    )


def _is_protection(card: Card) -> bool:
    ot = card.oracle_text.lower()
    kw = {k.lower() for k in card.keywords}
    protection_phrases = [
        "hexproof",
        "indestructible",
        "shroud",
        "protection from",
        "can't be the target",
        "can't be countered",
        "phase out",
        "gains hexproof",
        "gains indestructible",
    ]
    return any(p in ot or p in kw for p in protection_phrases)


def _is_recursion(card: Card) -> bool:
    ot = card.oracle_text.lower()
    if card.is_land:
        return False
    return (
        "return" in ot
        and "from your graveyard" in ot
        and ("to your hand" in ot or "to the battlefield" in ot)
    )


def _is_graveyard_hate(card: Card) -> bool:
    ot = card.oracle_text.lower()
    hate_phrases = [
        "exile.*graveyard",
        "exile all cards from.*graveyard",
        "cards can't leave graveyards",
        "if a card would be put into a graveyard.*exile",
    ]
    return any(re.search(p, ot) for p in hate_phrases)


def _is_token_maker(card: Card) -> bool:
    ot = card.oracle_text.lower()
    return "create" in ot and "token" in ot


def _is_stax(card: Card) -> bool:
    ot = card.oracle_text.lower()
    stax_phrases = [
        "can't cast",
        "can't activate",
        "costs.*more to cast",
        "enters tapped",
        "don't untap",
        "can't untap",
        "each player can.*only",
        "players can't",
        "nonbasic lands are",
    ]
    return any(re.search(p, ot) for p in stax_phrases)


def _is_threat(card: Card) -> bool:
    if not card.is_creature:
        return False
    try:
        power = int(card.power) if card.power and card.power.isdigit() else 0
    except (ValueError, TypeError):
        power = 0
    kw = {k.lower() for k in card.keywords}
    evasion = kw & {"flying", "trample", "menace", "unblockable", "double strike", "shadow"}
    return power >= 4 or (power >= 3 and len(evasion) > 0)


def _is_finisher(card: Card) -> bool:
    ot = card.oracle_text.lower()
    try:
        power = int(card.power) if card.power and card.power.isdigit() else 0
    except (ValueError, TypeError):
        power = 0
    win_phrases = ["you win the game", "loses the game", "extra turn", "infinite"]
    if any(p in ot for p in win_phrases):
        return True
    if power >= 7:
        return True
    if card.is_planeswalker:
        return True
    return False


def _is_engine(card: Card) -> bool:
    ot = card.oracle_text.lower()
    if card.is_land:
        return False
    engine_patterns = [
        r"whenever.*you.*draw",
        r"whenever.*enters.*the battlefield",
        r"whenever.*dies",
        r"whenever.*you cast",
        r"at the beginning of.*your",
        r"whenever.*you gain life",
        r"whenever.*a creature",
    ]
    trigger_count = sum(1 for p in engine_patterns if re.search(p, ot))
    return trigger_count >= 1 and ("draw" in ot or "create" in ot or "add" in ot or "put" in ot)


def _is_sacrifice_outlet(card: Card) -> bool:
    ot = card.oracle_text.lower()
    return "sacrifice a" in ot and (":" in ot or "as an additional cost" in ot)


def _is_aristocrat_payoff(card: Card) -> bool:
    ot = card.oracle_text.lower()
    if not ("whenever" in ot and "dies" in ot):
        return False
    return (
        "each opponent loses" in ot
        or "you gain" in ot
        or "draw a card" in ot
        or bool(re.search(r"deals.*damage", ot))
    )


def _is_cost_reducer(card: Card) -> bool:
    ot = card.oracle_text.lower()
    return "cost" in ot and ("less to cast" in ot or "{1} less" in ot or "{2} less" in ot)


def _is_combat_trick(card: Card) -> bool:
    if not card.is_instant:
        return False
    ot = card.oracle_text.lower()
    return "target creature gets" in ot and ("+" in ot or "indestructible" in ot)


def _is_lifegain(card: Card) -> bool:
    ot = card.oracle_text.lower()
    if card.is_land:
        return False
    return "you gain" in ot and "life" in ot


def classify_deck(cards: list[Card]) -> dict[str, list[Card]]:
    """Classify all cards in a deck and group by tag."""
    tag_groups: dict[str, list[Card]] = {}
    for card in cards:
        tags = classify_card(card)
        card.tags = tags
        for tag in tags:
            tag_groups.setdefault(tag.value, []).append(card)
    return tag_groups
