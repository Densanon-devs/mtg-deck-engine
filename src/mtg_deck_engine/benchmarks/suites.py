"""Benchmark suite system: named collections of archetype profiles for gauntlet testing.

Suites can be built-in presets or user-defined JSON files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from mtg_deck_engine.matchup.archetypes import (
    ARCHETYPES,
    ArchetypeName,
    ArchetypeProfile,
    get_archetype,
)


@dataclass
class BenchmarkSuite:
    """A named collection of archetypes with optional weight overrides."""

    name: str
    description: str = ""
    archetypes: list[ArchetypeProfile] = field(default_factory=list)


# =============================================================================
# Built-in suites
# =============================================================================

def _build_suite(name: str, desc: str, arch_names: list[str], weights: dict[str, float] | None = None) -> BenchmarkSuite:
    suite = BenchmarkSuite(name=name, description=desc)
    for aname in arch_names:
        arch = get_archetype(aname)
        if arch:
            if weights and aname in weights:
                # Create a copy with adjusted weight
                arch = ArchetypeProfile(**{**arch.__dict__, "meta_weight": weights[aname]})
            suite.archetypes.append(arch)
    return suite


CASUAL_COMMANDER = _build_suite(
    "casual-commander",
    "Casual Commander pod: midrange-heavy, some combo, minimal stax",
    ["midrange", "tokens", "aristocrats", "voltron", "spellslinger", "group_hug", "combo"],
    {"midrange": 3.0, "tokens": 2.0, "combo": 1.0, "group_hug": 1.0},
)

CEDH = _build_suite(
    "cedh",
    "Competitive EDH: fast combo, stax, heavy interaction",
    ["turbo", "stax", "combo", "control", "midrange"],
    {"turbo": 3.0, "stax": 2.0, "combo": 2.0, "control": 1.5},
)

MODERN_META = _build_suite(
    "modern-meta",
    "Modern competitive meta: aggro, midrange, control, combo",
    ["aggro", "midrange", "control", "combo", "burn", "tempo"],
    {"aggro": 2.0, "midrange": 2.5, "control": 1.5, "combo": 1.5},
)

STANDARD_META = _build_suite(
    "standard-meta",
    "Standard competitive meta: midrange-focused",
    ["aggro", "midrange", "control"],
    {"midrange": 3.0, "aggro": 2.0, "control": 2.0},
)

AGGRO_GAUNTLET = _build_suite(
    "aggro-gauntlet",
    "Pure aggro gauntlet: test if your deck can handle fast pressure",
    ["aggro", "tokens", "voltron", "burn"],
    {"aggro": 2.0, "tokens": 2.0, "voltron": 1.0},
)

CONTROL_GAUNTLET = _build_suite(
    "control-gauntlet",
    "Pure control gauntlet: test if your deck can push through interaction",
    ["control", "stax", "spellslinger"],
    {"control": 3.0, "stax": 2.0, "spellslinger": 1.5},
)

BUILTIN_SUITES: dict[str, BenchmarkSuite] = {
    s.name: s for s in [
        CASUAL_COMMANDER, CEDH, MODERN_META, STANDARD_META,
        AGGRO_GAUNTLET, CONTROL_GAUNTLET,
    ]
}


def get_suite(name: str) -> BenchmarkSuite | None:
    """Get a built-in suite by name."""
    return BUILTIN_SUITES.get(name)


def list_suites() -> list[str]:
    """List all available built-in suite names."""
    return list(BUILTIN_SUITES.keys())


# =============================================================================
# Custom suite I/O
# =============================================================================


def save_suite(suite: BenchmarkSuite, path: Path | str):
    """Save a benchmark suite to a JSON file."""
    data = {
        "name": suite.name,
        "description": suite.description,
        "archetypes": [
            {
                "name": a.name.value,
                "meta_weight": a.meta_weight,
            }
            for a in suite.archetypes
        ],
    }
    Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_suite(path: Path | str) -> BenchmarkSuite:
    """Load a benchmark suite from a JSON file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    suite = BenchmarkSuite(
        name=data.get("name", "custom"),
        description=data.get("description", ""),
    )
    for entry in data.get("archetypes", []):
        arch = get_archetype(entry["name"])
        if arch:
            weight = entry.get("meta_weight", arch.meta_weight)
            arch = ArchetypeProfile(**{**arch.__dict__, "meta_weight": weight})
            suite.archetypes.append(arch)
    return suite
