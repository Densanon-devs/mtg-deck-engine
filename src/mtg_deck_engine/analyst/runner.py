"""Public analyst API.

`AnalystRunner` is the orchestrator: it takes analysis outputs (AnalysisResult,
PowerBreakdown, AdvancedReport, etc.) plus a backend, and produces an
`AnalystResult` with prose summary + tag-picked cut suggestions. Later phases
add add-suggestions and a coach REPL on top of the same machinery.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mtg_deck_engine.analyst.add_candidates import AddCandidate, find_add_candidates
from mtg_deck_engine.analyst.backends import LLMBackend
from mtg_deck_engine.analyst.candidates import (
    CutCandidate,
    rank_cut_candidates,
)
from mtg_deck_engine.analyst.pipeline import GenerateResult, generate_with_verify
from mtg_deck_engine.analyst.prompts import (
    add_suggestions_prompt,
    cut_suggestions_prompt,
    executive_summary_prompt,
)
from mtg_deck_engine.analyst.verifiers import (
    TagPick,
    parse_tag_picks,
    verify_add_picks_constraints,
    verify_no_free_form_card_names,
    verify_prose_output,
    verify_tags_in_table,
)
from mtg_deck_engine.models import AnalysisResult, CardTag, Deck, Format


@dataclass
class CutSuggestion:
    """One suggested cut with a human-readable reason."""

    card_name: str      # Resolved from the candidate table, not from LLM output
    tag: str
    reason: str
    signals: list[str]  # Rule-engine reasons that made this a candidate


@dataclass
class AddSuggestion:
    """One suggested add with role context and a human-readable reason."""

    card_name: str
    role: str       # e.g. "card_draw"
    tag: str
    reason: str
    mana_cost: str
    cmc: float


@dataclass
class SwapSuggestion:
    """A paired cut+add — replace one card with another at the same role.

    Safer than raw cuts-then-adds because the paired framing preserves the
    deck's role totals (swap a ramp piece for a better ramp piece, don't
    leave a ramp hole). `rationale` is an LLM-generated sentence explaining
    why the swap is an upgrade; falls back to a structured sentence when
    the LLM's output fails verification.
    """

    cut_card: str
    add_card: str
    role: str
    rationale: str


@dataclass
class AnalystResult:
    """Outputs of one analyst pass."""

    summary: str = ""
    summary_confidence: float = 0.0
    summary_verified: bool = False

    cuts: list[CutSuggestion] = field(default_factory=list)
    cuts_confidence: float = 0.0
    cuts_verified: bool = False

    # Per-role adds keyed by role name ("ramp", "card_draw", "targeted_removal", ...)
    adds: dict[str, list[AddSuggestion]] = field(default_factory=dict)
    adds_confidence: dict[str, float] = field(default_factory=dict)
    adds_verified: dict[str, bool] = field(default_factory=dict)

    # Paired cut-then-add "swap" suggestions
    swaps: list[SwapSuggestion] = field(default_factory=list)

    # Raw generator artifacts for debugging / future gauntlet scoring
    raw_summary: GenerateResult | None = None
    raw_cuts: GenerateResult | None = None
    raw_adds: dict[str, GenerateResult] = field(default_factory=dict)


class AnalystRunner:
    """Orchestrates analyst generation passes.

    Usage:
        runner = AnalystRunner(backend=MockBackend(scripts=[...]))
        result = runner.run(deck, analysis_result, power, advanced, archetype)
    """

    def __init__(self, backend: LLMBackend, max_retries: int = 2):
        self.backend = backend
        self.max_retries = max_retries

    def run(
        self,
        deck: Deck,
        analysis: AnalysisResult,
        power,
        advanced,
        archetype: str,
        format_name: str = "commander",
        cut_count: int = 5,
        want_summary: bool = True,
        want_cuts: bool = True,
        want_adds: bool = False,
        db=None,  # CardDatabase for add candidate queries
        add_roles: list[CardTag] | None = None,
        adds_per_role: int = 3,
        playgroup_power: float | None = None,
        version_diff: dict | None = None,
    ) -> AnalystResult:
        """Run executive summary + cut suggestions + optional add suggestions.

        playgroup_power (optional, 1-10): the user's playgroup target. When set,
        the exec-summary prompt frames the deck's power relative to the table.

        version_diff (optional): result of diffing current deck against the
        previous saved snapshot — dict with keys "added" (name→qty),
        "removed" (name→qty), and "score_deltas" (score_name→delta). When
        provided, the summary prompt narrates what changed since last save.
        """
        result = AnalystResult()

        if want_summary:
            result = self._run_summary(
                deck, analysis, power, archetype, format_name, result,
                playgroup_power=playgroup_power,
                version_diff=version_diff,
            )

        if want_cuts:
            result = self._run_cuts(deck, archetype, power, cut_count, result)

        if want_adds and db is not None:
            # Auto-detect role gaps from analysis vs commander targets
            gaps = add_roles if add_roles is not None else _detect_role_gaps(analysis)
            for role in gaps:
                result = self._run_adds(
                    deck=deck, analysis=analysis, db=db, role=role,
                    archetype=archetype, format_name=format_name,
                    count=adds_per_role, result=result,
                )

        return result

    # ------------------------------------------------------------------ internals

    def _run_summary(
        self,
        deck: Deck,
        analysis: AnalysisResult,
        power,
        archetype: str,
        format_name: str,
        result: AnalystResult,
        playgroup_power: float | None = None,
        version_diff: dict | None = None,
    ) -> AnalystResult:
        color_identity = sorted({
            c.value
            for e in deck.entries
            if e.card
            for c in e.card.color_identity
        })

        prompt = executive_summary_prompt(
            deck_name=deck.name,
            archetype=archetype,
            power_overall=getattr(power, "overall", 0.0),
            power_tier=getattr(power, "tier", ""),
            power_reasons_up=list(getattr(power, "reasons_up", [])),
            power_reasons_down=list(getattr(power, "reasons_down", [])),
            land_count=analysis.land_count,
            ramp_count=analysis.ramp_count,
            draw_count=analysis.draw_engine_count,
            interaction_count=analysis.interaction_count,
            avg_mana_value=analysis.average_cmc,
            color_identity=color_identity,
            format_name=format_name,
            recommendations=list(analysis.recommendations),
            playgroup_power=playgroup_power,
            version_diff=version_diff,
        )
        gen = generate_with_verify(
            self.backend,
            prompt,
            verify=verify_prose_output,
            max_retries=self.max_retries,
            max_tokens=512,
        )
        result.summary = gen.output.strip()
        result.summary_confidence = gen.confidence
        result.summary_verified = gen.verified
        result.raw_summary = gen
        return result

    def _run_cuts(
        self,
        deck: Deck,
        archetype: str,
        power,
        cut_count: int,
        result: AnalystResult,
    ) -> AnalystResult:
        candidates = rank_cut_candidates(deck, limit=12)
        if not candidates:
            return result

        by_tag: dict[str, CutCandidate] = {c.tag: c for c in candidates}
        valid_tags = set(by_tag.keys())
        candidate_names = {c.entry.card.name for c in candidates if c.entry.card}
        deck_names = {e.card.name for e in deck.entries if e.card}

        prompt = cut_suggestions_prompt(
            candidates=candidates,
            deck_name=deck.name,
            archetype=archetype,
            power_tier=getattr(power, "tier", ""),
            count=cut_count,
        )

        def verify(output: str) -> None:
            # Cut safety comes from the tag→candidate resolution, not from
            # prose sanitation. The verifier's ONE job here is: every emitted
            # tag must be in the candidate table. Prose around the tag lines
            # (model preamble / "also consider X" phrasing) is noise that
            # gets stripped by the final resolution — it never becomes a
            # CutSuggestion. The old free-form name check was pedantic format
            # enforcement and blocked valid outputs without adding safety,
            # because a name outside a tag is only "detectable" when it's
            # already on the deck/candidate safe list (i.e. not hallucination).
            picks = parse_tag_picks(output)
            verify_tags_in_table(picks, valid_tags)

        gen = generate_with_verify(
            self.backend,
            prompt,
            verify=verify,
            max_retries=self.max_retries,
            max_tokens=512,
        )
        result.raw_cuts = gen
        # Keep the deck_names + candidate_names locals alive for the sake
        # of readers — they're still useful context for future tightening.
        _ = deck_names, candidate_names
        result.cuts_confidence = gen.confidence
        result.cuts_verified = gen.verified

        if gen.verified:
            picks = parse_tag_picks(gen.output)
            cuts: list[CutSuggestion] = []
            for p in picks:
                cand = by_tag.get(p.tag)
                if cand is None or cand.entry.card is None:
                    continue
                cuts.append(CutSuggestion(
                    card_name=cand.entry.card.name,
                    tag=p.tag,
                    reason=p.reason,
                    signals=cand.reasons,
                ))
            result.cuts = cuts[:cut_count]
        return result

    def run_swaps(
        self,
        deck: Deck,
        analysis: AnalysisResult,
        power,
        advanced,
        archetype: str,
        db,
        format_name: str = "commander",
        swap_count: int = 3,
    ) -> list[SwapSuggestion]:
        """Generate swap suggestions (cut X, add Y at the same role).

        Algorithm:
          1. Rank cut candidates with the usual ranker.
          2. For each of the top N cuts, check if it has a functional role
             tag (ramp / draw / removal / board_wipe). If it does, query
             ADD candidates for the same role in the deck's color identity,
             and pair the top-ranked add as the replacement.
          3. Cuts with no functional role (pure filler, threats) get
             paired with whatever role the analysis says is MOST lacking —
             so removing a vanilla threat opens up space for the biggest gap.

        Returns an empty list if the db isn't available or no swaps can be
        paired. Stateless — the AnalystResult is NOT mutated here; callers
        should assign the return into result.swaps themselves.
        """
        from mtg_deck_engine.analyst.add_candidates import find_add_candidates
        from mtg_deck_engine.analyst.candidates import rank_cut_candidates

        try:
            fmt_enum = Format(format_name)
        except ValueError:
            fmt_enum = Format.COMMANDER

        # Deck color identity + names-in-deck (for add exclusion)
        deck_colors: set[str] = set()
        deck_names: set[str] = set()
        for e in deck.entries:
            if e.card:
                deck_names.add(e.card.name)
                for c in e.card.color_identity:
                    deck_colors.add(c.value)

        # Role priorities for no-tag cuts: gaps first (biggest deficit), then
        # a fallback walk so we don't fail just because one role has no
        # in-color add candidates. For BALANCED decks (no gaps), we do NOT
        # append all roles — making up reasons to swap a vanilla card for a
        # ramp piece when ramp is already at target is worse than emitting
        # fewer swaps. Fallback only fires when there's a real gap.
        gaps = _detect_role_gaps(analysis)
        role_axes = [CardTag.RAMP, CardTag.CARD_DRAW, CardTag.TARGETED_REMOVAL, CardTag.BOARD_WIPE]

        cut_cands = rank_cut_candidates(deck, limit=max(swap_count * 3, 8))
        swaps: list[SwapSuggestion] = []
        used_adds: set[str] = set()

        for cut in cut_cands:
            if len(swaps) >= swap_count:
                break
            card = cut.entry.card
            if card is None:
                continue

            # Which roles to try? If the cut has a functional tag, prefer
            # upgrading at the same role. Otherwise, only consider GAP
            # roles — we won't manufacture a reason to add ramp/draw/etc
            # to a balanced deck just because a cut candidate surfaced.
            cut_tags = set(card.tags or [])
            preferred: list[CardTag] = [r for r in role_axes if r in cut_tags]
            try_order: list[CardTag] = []
            # Preferred first, then gaps (dedup'd). If the cut is tagged and
            # no gaps exist, we still try the same-role upgrade — that's
            # a legitimate recommendation (e.g. swap a 6-cost ramp for a
            # 3-cost ramp at the same count).
            for r in preferred + list(gaps):
                if r not in try_order:
                    try_order.append(r)
            if not try_order:
                # Cut has no tag AND the deck has no gaps — skip. No
                # defensible pairing exists for this cut.
                continue

            pick = None
            chosen_role: CardTag | None = None
            for r in try_order:
                # limit=10 gives role/color filters room to winnow without
                # returning empty for narrow color identities. The main
                # analyst flow uses 20; swaps care about latency more so
                # we pick the midpoint.
                add_cands = find_add_candidates(
                    db=db, role=r,
                    deck_color_identity=deck_colors,
                    format_=fmt_enum,
                    exclude_names=deck_names | used_adds,
                    limit=10,
                )
                if add_cands:
                    pick = add_cands[0]
                    chosen_role = r
                    break
            if pick is None or chosen_role is None:
                continue

            used_adds.add(pick.card.name)
            # Rationale wording: "same-role upgrade" if the cut was tagged
            # with the chosen role; "fill a gap" only if the chosen role is
            # actually in gaps; otherwise generic "upgrade" phrasing.
            if chosen_role in preferred:
                rationale = (
                    f"Upgrade at the same role ({chosen_role.value}): "
                    f"{pick.card.mana_cost or '{0}'} vs. the cut's {card.mana_cost or '{0}'}."
                )
            elif chosen_role in gaps:
                rationale = (
                    f"Reassign the slot toward {chosen_role.value}, which "
                    f"the rule engine flagged as under-provisioned."
                )
            else:  # defensive — shouldn't normally reach here given try_order
                rationale = (
                    f"Swap into {chosen_role.value} as an upgrade over the "
                    f"current slot."
                )
            swaps.append(SwapSuggestion(
                cut_card=card.name,
                add_card=pick.card.name,
                role=chosen_role.value,
                rationale=rationale,
            ))

        return swaps

    def _run_adds(
        self,
        deck: Deck,
        analysis: AnalysisResult,
        db,
        role: CardTag,
        archetype: str,
        format_name: str,
        count: int,
        result: AnalystResult,
    ) -> AnalystResult:
        """Run add-suggestion pass for one role gap."""
        # Determine deck colors + excluded names
        deck_colors: set[str] = set()
        exclude_names: set[str] = set()
        for e in deck.entries:
            if e.card:
                exclude_names.add(e.card.name)
                for c in e.card.color_identity:
                    deck_colors.add(c.value)

        # Query candidates
        try:
            fmt_enum = Format(format_name)
        except ValueError:
            fmt_enum = Format.COMMANDER
        candidates = find_add_candidates(
            db=db, role=role,
            deck_color_identity=deck_colors,
            format_=fmt_enum,
            exclude_names=exclude_names,
            limit=20,
        )
        if not candidates:
            return result

        # Target range per role (matches analysis/static.py _COMMANDER_TARGETS)
        role_targets = _role_targets_for_format(fmt_enum, role)
        current_count = _role_current_count(analysis, role)

        by_tag: dict[str, AddCandidate] = {c.tag: c for c in candidates}
        valid_tags = set(by_tag.keys())
        candidate_names = {c.card.name for c in candidates}

        prompt = add_suggestions_prompt(
            role_name=role.value,
            role_target_low=role_targets[0],
            role_target_high=role_targets[1],
            current_count=current_count,
            candidates=candidates,
            deck_name=deck.name,
            archetype=archetype,
            color_identity=sorted(deck_colors),
            count=count,
        )

        def verify(output: str) -> None:
            # Same philosophy as cuts: safety is the tag-resolution + the
            # constraint re-check. Free-form prose is noise that never
            # becomes an AddSuggestion because we only consult by_tag.
            picks = parse_tag_picks(output)
            verify_tags_in_table(picks, valid_tags)
            verify_add_picks_constraints(
                picks, by_tag, deck_colors, fmt_enum.value,
            )

        gen = generate_with_verify(
            self.backend, prompt, verify=verify,
            max_retries=self.max_retries, max_tokens=512,
        )
        result.raw_adds[role.value] = gen
        result.adds_confidence[role.value] = gen.confidence
        result.adds_verified[role.value] = gen.verified

        if gen.verified:
            picks = parse_tag_picks(gen.output)
            adds: list[AddSuggestion] = []
            for p in picks:
                cand = by_tag.get(p.tag)
                if cand is None:
                    continue
                adds.append(AddSuggestion(
                    card_name=cand.card.name,
                    role=role.value,
                    tag=p.tag,
                    reason=p.reason,
                    mana_cost=cand.card.mana_cost,
                    cmc=cand.card.display_cmc(),
                ))
            result.adds[role.value] = adds[:count]
        return result


# =============================================================================
# Helpers
# =============================================================================


def _detect_role_gaps(analysis: AnalysisResult) -> list[CardTag]:
    """Return roles where `current < target_low` for the deck's format.

    Commander-focused for now — the _COMMANDER_TARGETS values mirror analysis/static.py.
    """
    gaps: list[CardTag] = []
    # (role, target_low)
    commander_checks = [
        (CardTag.RAMP, 10, analysis.ramp_count),
        (CardTag.CARD_DRAW, 8, analysis.draw_engine_count),
        (CardTag.TARGETED_REMOVAL, 8, analysis.interaction_count),
    ]
    for role, target_low, current in commander_checks:
        if current < target_low:
            gaps.append(role)
    return gaps


def _role_targets_for_format(fmt: Format, role: CardTag) -> tuple[int, int]:
    """Return (low, high) target range for a role. Mirrors analysis/static.py targets."""
    if fmt in (Format.COMMANDER, Format.BRAWL, Format.OATHBREAKER, Format.DUEL):
        return {
            CardTag.RAMP: (10, 15),
            CardTag.CARD_DRAW: (8, 12),
            CardTag.TARGETED_REMOVAL: (8, 12),
            CardTag.BOARD_WIPE: (2, 4),
        }.get(role, (0, 0))
    return {
        CardTag.RAMP: (0, 4),
        CardTag.CARD_DRAW: (4, 8),
        CardTag.TARGETED_REMOVAL: (6, 10),
        CardTag.BOARD_WIPE: (0, 3),
    }.get(role, (0, 0))


def _role_current_count(analysis: AnalysisResult, role: CardTag) -> int:
    return {
        CardTag.RAMP: analysis.ramp_count,
        CardTag.CARD_DRAW: analysis.draw_engine_count,
        CardTag.TARGETED_REMOVAL: analysis.interaction_count,
    }.get(role, 0)
