"""LLM-backed analyst layer.

Turns structured analysis output into prose summaries and tag-constrained
card suggestions. Hallucination-resistant by construction: the LLM is never
allowed to emit a card name — it picks tags out of a pre-validated candidate
table, and every emission is post-hoc verified against the source table.

Phase 1: executive summary + cut suggestions (zero-hallucination surfaces —
cuts are picked from the user's own 100-card list, summaries emit only prose).

Phase 2: add suggestions sourced from a deterministic Scryfall query that
pre-filters for color identity, format legality, banlists, and already-present
cards before the LLM sees anything.

Phase 3: PIE pipeline backend, tool-call variant, coach REPL.

Phase 4: gauntlet benchmark in the style of the tax-compliance-engine IRS
200-problem suite.
"""

from mtg_deck_engine.analyst.runner import AnalystRunner, AnalystResult
from mtg_deck_engine.analyst.backends import LLMBackend, MockBackend

__all__ = ["AnalystRunner", "AnalystResult", "LLMBackend", "MockBackend"]
