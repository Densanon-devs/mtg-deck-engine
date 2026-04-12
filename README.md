# MTG Deck Engine

Deck analysis, goldfish testing, matchup simulation, and player insight engine for Magic: The Gathering.

## What It Does

- **Card Database**: Pulls the full MTG card database from Scryfall
- **Deck Import**: Parses decklists from plain text, Moxfield, Archidekt, or CSV
- **Card Classification**: Auto-tags every card by functional role (ramp, removal, draw, threats, etc.)
- **Static Analysis**: Mana curve, color sources, role distribution, structural scoring, and actionable recommendations
- **Format Validation**: Legality checks, copy limits, color identity, commander rules

## Installation

**Option 1: Desktop binary (Pro)** — Download the standalone Windows binary from the releases page. No Python install needed. Activate with your license key:
```bash
mtg-engine license activate YOUR_KEY
```

**Option 2: From source**
```bash
pip install -e .
```

## Quick Start

```bash
# Install
pip install -e .

# Download card data (~50MB from Scryfall)
mtg-engine ingest

# Analyze a deck (static analysis)
mtg-engine analyze my_deck.txt --format commander

# Deep analysis (includes probability layer)
mtg-engine analyze my_deck.txt --format commander --deep

# Standalone probability analysis
mtg-engine probability my_deck.txt --format commander --card "Sol Ring"

# Goldfish simulation (solo play testing)
mtg-engine goldfish my_deck.txt --format commander --sims 1000 --turns 10

# Meta gauntlet (matchup testing vs archetype field)
mtg-engine gauntlet my_deck.txt --format commander --sims 500

# Save a deck version snapshot
mtg-engine save my_deck.txt my-deck-id --notes "Added more ramp"

# Compare two versions
mtg-engine compare my-deck-id

# View version history and trends
mtg-engine history my-deck-id

# Export report (JSON, Markdown, or HTML)
mtg-engine analyze my_deck.txt --format commander --export report.html

# Run gauntlet with a specific meta suite
mtg-engine gauntlet my_deck.txt --suite cedh

# Quick probability calculator (no deck file needed)
mtg-engine calc --deck 99 --copies 4 --turns 7

# Compare two different decks side by side
mtg-engine diff deck_a.txt deck_b.txt --format commander

# Interactive mulligan practice
mtg-engine practice my_deck.txt --format commander --rounds 10

# Search cards
mtg-engine search "Lightning Bolt"

# Database info
mtg-engine info
```

## Decklist Format

Plain text, one card per line:

```
Commander
1 Atraxa, Praetors' Voice

Mainboard
1 Sol Ring
1 Arcane Signet
1 Command Tower
35 Plains
```

Also supports `4x Lightning Bolt`, Moxfield/Archidekt exports, and CSV.

## Free vs Pro

All card data and basic analysis are **free forever**. Monetization is feature-gated, not data-gated.

| Feature | Free | Pro |
|---------|:----:|:---:|
| Card search & deck import | Y | Y |
| Static analysis & mana curve | Y | Y |
| Basic recommendations | Y | Y |
| Goldfish simulation | - | Y |
| Matchup gauntlet | - | Y |
| Deck version history | - | Y |
| Coaching insights | - | Y |
| Report export | - | Y |

## Roadmap

- [x] Phase 1: Card data, deck import, classification, static analysis
- [x] Phase 2: Opening hand / mana probability calculator
- [x] Phase 3: Goldfish simulation engine (Pro)
- [x] Phase 4: Matchup framework and benchmark gauntlet (Pro)
- [x] Phase 5: Version comparison and change tracking (Pro)
- [x] Phase 6: Advanced heuristics and format modules (Pro)

## Legal

This tool is not affiliated with or endorsed by Wizards of the Coast.
Magic: The Gathering and its logos are trademarks of Wizards of the Coast LLC.
Card data provided by [Scryfall](https://scryfall.com). Card images are hotlinked from Scryfall and are never hosted by this project.
