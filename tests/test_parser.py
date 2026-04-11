"""Tests for deck parsing."""

from mtg_deck_engine.deck.parser import parse_auto, parse_csv, parse_decklist
from mtg_deck_engine.models import Zone


def test_basic_quantity_name():
    text = "4 Lightning Bolt\n2 Mountain\n"
    entries = parse_decklist(text)
    assert len(entries) == 2
    assert entries[0].card_name == "Lightning Bolt"
    assert entries[0].quantity == 4
    assert entries[1].card_name == "Mountain"
    assert entries[1].quantity == 2


def test_quantity_x_marker():
    text = "4x Lightning Bolt\n2X Goblin Guide\n"
    entries = parse_decklist(text)
    assert entries[0].card_name == "Lightning Bolt"
    assert entries[0].quantity == 4
    assert entries[1].card_name == "Goblin Guide"
    assert entries[1].quantity == 2


def test_name_only_defaults_to_1():
    text = "Sol Ring\nCommand Tower\n"
    entries = parse_decklist(text)
    assert len(entries) == 2
    assert all(e.quantity == 1 for e in entries)


def test_section_headers():
    text = """Commander
1 Atraxa, Praetors' Voice

Mainboard
1 Sol Ring
1 Command Tower

Sideboard
1 Swords to Plowshares
"""
    entries = parse_decklist(text)
    cmdr = [e for e in entries if e.zone == Zone.COMMANDER]
    main = [e for e in entries if e.zone == Zone.MAINBOARD]
    sb = [e for e in entries if e.zone == Zone.SIDEBOARD]
    assert len(cmdr) == 1
    assert cmdr[0].card_name == "Atraxa, Praetors' Voice"
    assert len(main) == 2
    assert len(sb) == 1


def test_blank_line_sideboard_heuristic():
    text = """4 Lightning Bolt
4 Mountain

2 Smash to Smithereens
"""
    entries = parse_decklist(text)
    main = [e for e in entries if e.zone == Zone.MAINBOARD]
    sb = [e for e in entries if e.zone == Zone.SIDEBOARD]
    assert len(main) == 2
    assert len(sb) == 1


def test_set_code_stripped():
    text = "4 Lightning Bolt (M21) 199\n"
    entries = parse_decklist(text)
    assert entries[0].card_name == "Lightning Bolt"
    assert entries[0].quantity == 4


def test_custom_tags():
    text = "1 Sol Ring #ramp #mana\n"
    entries = parse_decklist(text)
    assert entries[0].card_name == "Sol Ring"
    assert "ramp" in entries[0].custom_tags
    assert "mana" in entries[0].custom_tags


def test_comments_skipped():
    text = "// This is a comment\n# Another comment\n4 Lightning Bolt\n"
    entries = parse_decklist(text)
    assert len(entries) == 1
    assert entries[0].card_name == "Lightning Bolt"


def test_csv_format():
    text = 'quantity,name,zone\n4,"Lightning Bolt",mainboard\n2,"Mountain",mainboard\n'
    entries = parse_csv(text)
    assert len(entries) == 2
    assert entries[0].card_name == "Lightning Bolt"
    assert entries[0].quantity == 4


def test_csv_comma_in_name():
    text = '4,"Jace, the Mind Sculptor",mainboard\n2,"Mountain",sideboard\n'
    entries = parse_csv(text)
    assert len(entries) == 2
    assert entries[0].card_name == "Jace, the Mind Sculptor"
    assert entries[0].quantity == 4
    assert entries[1].card_name == "Mountain"
    assert entries[1].zone == Zone.SIDEBOARD


def test_csv_no_quotes():
    text = "4,Lightning Bolt,mainboard\n2,Mountain,mainboard\n"
    entries = parse_csv(text)
    assert len(entries) == 2
    assert entries[0].card_name == "Lightning Bolt"


def test_auto_detect_text():
    text = "4 Lightning Bolt\n4 Mountain\n"
    entries = parse_auto(text)
    assert len(entries) == 2


def test_auto_detect_csv():
    text = '4,"Lightning Bolt",mainboard\n4,"Mountain",mainboard\n'
    entries = parse_auto(text)
    assert len(entries) == 2
