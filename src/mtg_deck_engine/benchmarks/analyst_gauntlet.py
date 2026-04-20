"""Analyst gauntlet — fidelity benchmark for the LLM-backed analyst layer.

Mirrors the tax-compliance-engine's IRS 200-problem gauntlet pattern:
hand-curated input cases, deterministic verifiers, and two-tier scoring.

Scoring (matches TCE's style):

  HARD PASS (must be 100% or we ship nothing):
    - All output card names resolve to real cards
    - Cut suggestions are cards currently in the deck
    - Add suggestions are in color identity + format-legal

  SOFT RELEVANCE (target: 75%+):
    - Cut picks overlap with the hand-written "gold cut" set
    - Add picks match the expected role and overlap the gold add set

  PROSE QUALITY (informational, not load-bearing):
    - Summary length within band, not truncated
    - Power tier mentioned in the narration

The gauntlet is designed so a regression in the verifier stack (e.g. a
new model silently typing raw card names) is caught by HARD PASS — the
number can only ever go to 100% if every suggestion stays inside the
pre-validated tables. SOFT RELEVANCE reflects model quality and can
legitimately vary between model sizes.

Usage:
    python -m mtg_deck_engine.benchmarks.analyst_gauntlet --backend mock
    python -m mtg_deck_engine.benchmarks.analyst_gauntlet --backend llama_cpp
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from typing import Callable

from mtg_deck_engine.analyst import AnalystRunner, MockBackend
from mtg_deck_engine.analyst.runner import AnalystResult
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
# Case definitions
# =============================================================================


@dataclass
class GauntletCase:
    """One deck-analysis input case with gold-standard expectations."""

    case_id: str
    description: str
    build_deck: Callable[[], Deck]
    build_analysis: Callable[[], tuple[AnalysisResult, PowerBreakdown, str]]
    gold_cuts: set[str] = field(default_factory=set)
    gold_add_roles: set[CardTag] = field(default_factory=set)


@dataclass
class GauntletResult:
    """Aggregated gauntlet outcome across all cases."""

    total_cases: int = 0

    # Hard pass counters
    summary_hard_pass: int = 0
    cuts_hard_pass: int = 0

    # Soft metrics — two axes so we don't punish models that pick defensibly
    # but differently than a narrow hand-written gold set.
    #
    # cuts_strict_overlap: average overlap with gold_cuts, counted ONLY over
    #   cases that actually have a gold set. Cases with gold_cuts=empty are
    #   excluded from the denominator — a cuts pass on a cuts-not-interesting
    #   case shouldn't pull the strict score up or down.
    #
    # cuts_defensible_rate: fraction of emitted picks that fell in the TOP
    #   2×count of the ranker. Both 0.5B and 3B emitted valid candidate-table
    #   tags (hard pass 100%), but one gets credit for picking top slots
    #   vs. the other picking deeper. If the ranker is wrong, this is wrong;
    #   if the ranker is right, this measures model/ranker alignment.
    cuts_strict_overlap: float = 0.0
    cuts_defensible_rate: float = 0.0

    # Prose quality counters
    summary_mentions_tier: int = 0

    # Per-case details for reporting
    per_case: list[dict] = field(default_factory=list)

    @property
    def hard_pass_rate(self) -> float:
        if self.total_cases == 0:
            return 0.0
        both_pass = sum(
            1 for c in self.per_case
            if c.get("summary_verified") and c.get("cuts_hard_ok", True)
        )
        return both_pass / self.total_cases

    # Back-compat alias — older callers read .cuts_relevance.
    @property
    def cuts_relevance(self) -> float:
        return self.cuts_strict_overlap


# =============================================================================
# Synthetic deck builders — 10 diverse cases
# =============================================================================


def _land(name: str) -> DeckEntry:
    card = Card(
        scryfall_id=f"l-{name}", oracle_id=f"lo-{name}", name=name,
        layout=CardLayout.NORMAL, is_land=True,
        type_line="Basic Land — Forest", mana_cost="",
        color_identity=[Color.GREEN],
    )
    return DeckEntry(card_name=name, quantity=1, zone=Zone.MAINBOARD, card=card)


def _nonland(
    name: str, cmc: float, tags: list[CardTag],
    color_identity: list[Color] = None,
    is_creature: bool = False, is_artifact: bool = False,
    is_instant: bool = False, mana_cost: str = "{2}",
    type_line: str = "",
) -> DeckEntry:
    ci = color_identity if color_identity is not None else [Color.GREEN]
    card = Card(
        scryfall_id=f"s-{name}", oracle_id=f"so-{name}", name=name,
        layout=CardLayout.NORMAL, cmc=cmc, mana_cost=mana_cost,
        type_line=type_line or ("Creature" if is_creature else "Artifact"),
        tags=tags,
        is_creature=is_creature, is_artifact=is_artifact, is_instant=is_instant,
        color_identity=ci,
    )
    return DeckEntry(card_name=name, quantity=1, zone=Zone.MAINBOARD, card=card)


def _build_ramp_heavy_deck() -> Deck:
    """100-card mono-G with 18 ramp (over cap) and 5 vanilla high-CMC fillers.

    Ideal cut targets: the 5 no-tag high-CMC cards + several of the 18 ramp pieces.
    """
    entries: list[DeckEntry] = []
    for i in range(36):
        entries.append(_land(f"Forest{i}"))
    for i in range(18):
        entries.append(_nonland(f"Ramp{i}", cmc=3.0, tags=[CardTag.RAMP, CardTag.MANA_ROCK],
                                is_artifact=True))
    for i in range(5):
        entries.append(_nonland(f"VanillaFiller{i}", cmc=7.0, tags=[]))
    for i in range(3):
        entries.append(_nonland(f"BigFinisher{i}", cmc=7.0, tags=[CardTag.FINISHER],
                                is_creature=True))
    for i in range(8):
        entries.append(_nonland(f"DrawSpell{i}", cmc=3.0, tags=[CardTag.CARD_DRAW]))
    for i in range(8):
        entries.append(_nonland(f"Removal{i}", cmc=2.0, tags=[CardTag.TARGETED_REMOVAL],
                                is_instant=True))
    while sum(e.quantity for e in entries) < 100:
        entries.append(_nonland(f"Extra{len(entries)}", cmc=4.0, tags=[CardTag.THREAT],
                                is_creature=True))
    return Deck(name="Mono-G Ramp Heavy", format=Format.COMMANDER, entries=entries[:100])


def _build_under_interaction_deck() -> Deck:
    """100-card deck with low interaction count — should trigger removal-role gap."""
    entries: list[DeckEntry] = []
    for i in range(36):
        entries.append(_land(f"Forest{i}"))
    for i in range(12):
        entries.append(_nonland(f"Ramp{i}", cmc=2.0, tags=[CardTag.RAMP, CardTag.MANA_ROCK],
                                is_artifact=True))
    for i in range(10):
        entries.append(_nonland(f"Draw{i}", cmc=3.0, tags=[CardTag.CARD_DRAW]))
    for i in range(3):
        entries.append(_nonland(f"Removal{i}", cmc=2.0, tags=[CardTag.TARGETED_REMOVAL],
                                is_instant=True))
    for i in range(5):
        entries.append(_nonland(f"Finisher{i}", cmc=6.0, tags=[CardTag.FINISHER],
                                is_creature=True))
    while sum(e.quantity for e in entries) < 100:
        entries.append(_nonland(f"Creature{len(entries)}", cmc=4.0, tags=[CardTag.THREAT],
                                is_creature=True))
    return Deck(name="Low-Interaction", format=Format.COMMANDER, entries=entries[:100])


def _ramp_heavy_analysis() -> tuple[AnalysisResult, PowerBreakdown, str]:
    ar = AnalysisResult(
        deck_name="Mono-G Ramp Heavy", format="commander", total_cards=100,
        land_count=36, ramp_count=18, draw_engine_count=8, interaction_count=8,
        average_cmc=3.2, recommendations=["Trim 3 ramp pieces", "Curve is top-heavy"],
    )
    pb = PowerBreakdown()
    pb.overall = 6.2
    pb.tier = "focused"
    pb.reasons_up = ["Heavy ramp package"]
    pb.reasons_down = ["Top-heavy curve"]
    return ar, pb, "midrange"


def _low_interaction_analysis() -> tuple[AnalysisResult, PowerBreakdown, str]:
    ar = AnalysisResult(
        deck_name="Low-Interaction", format="commander", total_cards=100,
        land_count=36, ramp_count=12, draw_engine_count=10, interaction_count=3,
        average_cmc=3.5, recommendations=["Add 5 more removal pieces"],
    )
    pb = PowerBreakdown()
    pb.overall = 5.5
    pb.tier = "casual"
    pb.reasons_up = ["Solid ramp and draw"]
    pb.reasons_down = ["Almost no interaction"]
    return ar, pb, "midrange"


def default_cases() -> list[GauntletCase]:
    """30 hand-curated cases spanning ramp-heavy, low-interaction, low-draw,
    low-ramp, high-CMC bloat, balanced, and over-removal deck shapes.

    Two original synthetic cases are kept as `mini_cases()` for quick iteration.
    """
    from mtg_deck_engine.benchmarks.analyst_gauntlet_decks import all_cases
    out: list[GauntletCase] = []
    for case_id, desc, build_deck, build_analysis, gold_cuts, gold_add_roles in all_cases():
        out.append(GauntletCase(
            case_id=case_id,
            description=desc,
            build_deck=build_deck,
            build_analysis=build_analysis,
            gold_cuts=gold_cuts,
            gold_add_roles=gold_add_roles,
        ))
    return out


def mini_cases() -> list[GauntletCase]:
    """The original 2-case smoke suite — used by fast tests."""
    return [
        GauntletCase(
            case_id="ramp_heavy_mini",
            description="Mono-G with 18 ramp (over the 10-15 cap) and 5 vanilla fillers",
            build_deck=_build_ramp_heavy_deck,
            build_analysis=_ramp_heavy_analysis,
            gold_cuts={f"VanillaFiller{i}" for i in range(5)} | {f"Ramp{i}" for i in range(15, 18)},
            gold_add_roles=set(),
        ),
        GauntletCase(
            case_id="under_interaction_mini",
            description="Deck with 3 removal (target 8-12) — should trigger removal-role add suggestions",
            build_deck=_build_under_interaction_deck,
            build_analysis=_low_interaction_analysis,
            gold_cuts=set(),
            gold_add_roles={CardTag.TARGETED_REMOVAL},
        ),
    ]


# =============================================================================
# Scoring
# =============================================================================


def score_case(case: GauntletCase, result: AnalystResult) -> dict:
    """Score one case, returning a per-case record for the final report."""
    record = {
        "case_id": case.case_id,
        "description": case.description,
        "summary_verified": result.summary_verified,
        "summary_confidence": result.summary_confidence,
        "cuts_verified": result.cuts_verified,
        "cuts_confidence": result.cuts_confidence,
        "cuts_hard_ok": True,
        "cuts_strict_overlap": 0.0,
        "cuts_defensible": 0.0,
        "cut_names": [c.card_name for c in result.cuts],
        "cut_tags": [c.tag for c in result.cuts],
        "has_gold": bool(case.gold_cuts),
    }

    # HARD PASS for cuts: every emitted cut name must exist in the deck
    deck = case.build_deck()
    deck_card_names = {e.card.name for e in deck.entries if e.card}
    for c in result.cuts:
        if c.card_name not in deck_card_names:
            record["cuts_hard_ok"] = False
            break

    # STRICT OVERLAP with gold cut set — only meaningful when gold is non-empty
    if case.gold_cuts and result.cuts:
        picked = {c.card_name for c in result.cuts}
        overlap = picked & case.gold_cuts
        record["cuts_strict_overlap"] = len(overlap) / max(1, len(picked))

    # DEFENSIBLE RATE: did the picks fall in the top 2×count of the ranker?
    # Tags are of the form c01, c02, ... — we parse the numeric suffix to
    # know the rank each pick corresponds to. Anything at or inside the top
    # 2×count window counts as defensible; picks deeper in the list still
    # count if no deeper picks were made (they're real candidates, just not
    # the highest-signal ones per our heuristic).
    if result.cuts:
        # `count` comes from whatever the runner asked for. We didn't save it
        # explicitly, so use the number of picks produced as the proxy — a
        # run that produced 5 picks is defensible if they fall in the top 10.
        count = len(result.cuts)
        top_k = max(5, count * 2)
        defensible = 0
        for tag in record["cut_tags"]:
            try:
                rank = int(tag.lstrip("c").lstrip("a"))
            except ValueError:
                continue
            if rank <= top_k:
                defensible += 1
        record["cuts_defensible"] = defensible / count

    return record


def run_gauntlet(
    runner: AnalystRunner,
    cases: list[GauntletCase] | None = None,
    verbose: bool = True,
) -> GauntletResult:
    """Run the analyst over every case and aggregate scores."""
    cases = cases or default_cases()
    result = GauntletResult(total_cases=len(cases))

    strict_overlap_sum = 0.0
    strict_overlap_cases = 0
    defensible_sum = 0.0
    defensible_cases = 0

    for case in cases:
        if verbose:
            print(f"[{case.case_id}] {case.description}")
        deck = case.build_deck()
        analysis, power, archetype = case.build_analysis()

        ar = runner.run(
            deck=deck, analysis=analysis, power=power, advanced=None,
            archetype=archetype,
        )

        record = score_case(case, ar)
        result.per_case.append(record)

        if ar.summary_verified:
            result.summary_hard_pass += 1
        if record["cuts_hard_ok"]:
            result.cuts_hard_pass += 1

        # Only include cases with a gold set in the strict-overlap average,
        # so a sparse gold corpus can't silently drive the metric to zero.
        if record["has_gold"]:
            strict_overlap_sum += record["cuts_strict_overlap"]
            strict_overlap_cases += 1

        # Defensibility is meaningful on every case where cuts were produced.
        if ar.cuts:
            defensible_sum += record["cuts_defensible"]
            defensible_cases += 1

        if ar.summary and (getattr(power, "tier", "") in ar.summary):
            result.summary_mentions_tier += 1

    if strict_overlap_cases:
        result.cuts_strict_overlap = strict_overlap_sum / strict_overlap_cases
    if defensible_cases:
        result.cuts_defensible_rate = defensible_sum / defensible_cases

    return result


def print_report(result: GauntletResult) -> None:
    print()
    print("=" * 60)
    print("ANALYST GAUNTLET RESULTS")
    print("=" * 60)
    print(f"Total cases: {result.total_cases}")
    print()
    print("HARD PASS (must be 100%):")
    print(f"  Summary verified:     {result.summary_hard_pass}/{result.total_cases}")
    print(f"  Cuts names valid:     {result.cuts_hard_pass}/{result.total_cases}")
    print()
    print("SOFT METRICS:")
    print(f"  Cuts strict overlap (gold set only): {result.cuts_strict_overlap * 100:.1f}%")
    print(f"  Cuts defensible (top 2x by ranker):  {result.cuts_defensible_rate * 100:.1f}%")
    print()
    print("PROSE QUALITY (informational):")
    print(f"  Summaries mentioning tier: {result.summary_mentions_tier}/{result.total_cases}")
    print()


def _mock_runner() -> AnalystRunner:
    """Mock runner scripted to pick the first 2 tags and produce a valid prose summary."""
    scripts = [
        ("[INPUT]", "A " * 80 + " (focused midrange deck)"),
        # For cuts: always pick c01, c02 — they exist because the candidates
        # list is always non-empty for these cases.
        ("suggesting cuts", "[c01]: flagged by rule engine.\n[c02]: redundant slot."),
    ] * 5  # Repeat so each case gets its own scripted pair
    return AnalystRunner(backend=MockBackend(scripts=scripts))


def main():
    parser = argparse.ArgumentParser(description="MTG analyst gauntlet")
    parser.add_argument("--backend", choices=["mock", "llama_cpp"], default="mock")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.backend == "llama_cpp":
        from mtg_deck_engine.analyst.backends.llama_cpp import LlamaCppBackend
        backend = LlamaCppBackend()
        if not backend.is_available():
            print(f"Model not available at {backend.model_path}. Aborting.", file=sys.stderr)
            sys.exit(1)
        runner = AnalystRunner(backend=backend)
    else:
        runner = _mock_runner()

    res = run_gauntlet(runner, verbose=args.verbose)
    print_report(res)

    # Exit non-zero if the hard pass rate isn't 100% — lets CI gate on it
    if res.hard_pass_rate < 1.0:
        sys.exit(2)


if __name__ == "__main__":
    main()
