"""Prompt templates for the analyst.

Each template is a pure function that returns a prompt string. Two template
categories:

1. Prose-only prompts (executive summary, power narration). Output is
   pure text — no card emission required, no hallucination surface.

2. Tag-constrained prompts (cuts, adds). Output must reference only the
   tags provided in the candidate table. Verifiers enforce this.

Templates embed a few-shot example so small models know the target format.
"""

from __future__ import annotations

from mtg_deck_engine.analyst.add_candidates import AddCandidate, render_add_table
from mtg_deck_engine.analyst.candidates import CutCandidate, render_cut_table


# =============================================================================
# Executive summary — prose only, no card emission
# =============================================================================

_SUMMARY_FEWSHOT = """\
[EXAMPLE]
Input:
  Deck: Prossh, Skyraider of Kher (BRG, Commander)
  Archetype: Aristocrats
  Power: 6.8/10 (focused)
  Land count: 36 (in range)
  Ramp: 12 (in range)
  Draw: 6 (low — target 8-12)
  Interaction: 5 (low — target 8-12)
  Avg mana value: 2.9
  Reasons up: heavy ramp package; multiple sac outlets; cheap tokens
  Reasons down: low interaction may lose to combo

Output:
  This is a focused Jund aristocrats build sitting around power 6-7 — fast
  enough to matter, disciplined enough that it should close games by turn
  8 or 9 without needing to assemble a combo. The ramp shell is well
  packed and the curve lands in a comfortable 2-3 range, which tracks with
  the sac-outlet plan of grinding value over multiple turns.

  The shape of the deck's weakness is interaction rather than threat
  density. Five pieces of removal plus no board wipes means a resolved
  Dockside or Thassa's Oracle from across the table probably ends the
  game before you can respond. Consider trimming 2-3 redundant mana dorks
  for instant-speed answers, and adding one sweeper — this keeps the
  curve honest while raising the interaction floor.
[/EXAMPLE]
"""


def executive_summary_prompt(
    deck_name: str,
    archetype: str,
    power_overall: float,
    power_tier: str,
    power_reasons_up: list[str],
    power_reasons_down: list[str],
    land_count: int,
    ramp_count: int,
    draw_count: int,
    interaction_count: int,
    avg_mana_value: float,
    color_identity: list[str],
    format_name: str,
    recommendations: list[str],
    playgroup_power: float | None = None,
    version_diff: dict | None = None,  # {"added": {...}, "removed": {...}, "score_deltas": {...}}
) -> str:
    """Executive summary prompt — prose only, no card references required.

    The LLM narrates the structured numbers. It has no reason or opportunity
    to emit card names, so hallucination surface is zero.

    If `playgroup_power` is given, a line is added to the input block so the
    narration can contextualize the deck's fit relative to the table target.
    """
    up = "; ".join(power_reasons_up[:4]) or "none surfaced"
    down = "; ".join(power_reasons_down[:4]) or "none surfaced"
    recs = "\n  - " + "\n  - ".join(recommendations[:6]) if recommendations else " (none)"
    colors = "".join(color_identity) or "colorless"

    # Version diff block — optional context about the last saved revision.
    # We keep the summary to a few lines so small models aren't overwhelmed:
    # counts of adds/removes and the biggest score delta. Specific card names
    # are mentioned here (inside [INPUT]) because they're drawn from the
    # user's own deck history — same safety class as the card list.
    version_diff_line = ""
    if version_diff:
        added = version_diff.get("added") or {}
        removed = version_diff.get("removed") or {}
        score_deltas = version_diff.get("score_deltas") or {}
        # Pick the top score change as the narrative hook
        top_delta = ""
        if score_deltas:
            best = max(score_deltas.items(), key=lambda kv: abs(kv[1]))
            sign = "+" if best[1] >= 0 else ""
            top_delta = f"; {best[0]} {sign}{best[1]:.1f}"
        added_sample = ", ".join(list(added.keys())[:3]) or "none"
        removed_sample = ", ".join(list(removed.keys())[:3]) or "none"
        version_diff_line = (
            f"\nSince last save: +{len(added)} adds ({added_sample}), "
            f"-{len(removed)} cuts ({removed_sample}){top_delta}"
        )

    playgroup_line = ""
    playgroup_instruction = ""
    if playgroup_power is not None:
        gap = power_overall - playgroup_power
        if gap >= 1.0:
            fit_phrase = "OVER-PITCHES (this deck is stronger than the table)"
        elif gap <= -1.0:
            fit_phrase = "UNDER-DELIVERS (the table will out-pace this deck)"
        else:
            fit_phrase = "FITS the playgroup"
        playgroup_line = f"\nPlaygroup target: {playgroup_power:.1f}/10 — this deck {fit_phrase}"
        playgroup_instruction = (
            " The second paragraph should explicitly address whether this "
            "deck fits the playgroup target and what to adjust if it doesn't."
        )

    return f"""You are a Magic: The Gathering deck analyst. Write a 2-paragraph
executive summary of the deck described below. Narrate the structured data
below in natural prose — do NOT introduce specific card names the data does
not reference. Keep paragraphs tight. Tone: direct, helpful, no hype.{playgroup_instruction}

{_SUMMARY_FEWSHOT}

[INPUT]
Deck: {deck_name} ({colors}, {format_name})
Archetype: {archetype}
Power: {power_overall:.1f}/10 ({power_tier}){playgroup_line}{version_diff_line}
Land count: {land_count}
Ramp: {ramp_count}
Draw: {draw_count}
Interaction: {interaction_count}
Avg mana value: {avg_mana_value:.2f}
Reasons up: {up}
Reasons down: {down}
Rule-engine recommendations:{recs}

[OUTPUT]
"""


# =============================================================================
# Cut suggestions — tag-constrained emission
# =============================================================================

_CUTS_FEWSHOT = """\
[EXAMPLE]
Candidates:
  [c01] Rampant Growth — CMC 2 — tags: ramp — signals: redundant_ramp
  [c02] Pelakka Wurm — CMC 7 — tags: threat — signals: high_cmc_non_finisher
  [c03] Cultivate — CMC 3 — tags: ramp — signals: redundant_ramp
  [c04] Warstorm Surge — CMC 6 — tags: none — signals: high_cmc_non_finisher/no_functional_tag
  [c05] Elvish Mystic — CMC 1 — tags: mana_dork — signals: redundant_ramp

Request: pick the 3 strongest cuts for a focused midrange deck.

Output:
  [c04]: high-cost filler with no functional tag — pure curve ballast.
  [c02]: 7-mana vanilla threat, not a finisher, redundant with better closers.
  [c01]: your 13-piece ramp suite doesn't need another 2-mana land-searcher.
[/EXAMPLE]
"""


def cut_suggestions_prompt(
    candidates: list[CutCandidate],
    deck_name: str,
    archetype: str,
    power_tier: str,
    count: int = 5,
) -> str:
    """Cut suggestions prompt with tag scaffolding.

    The LLM must emit picks in the form `[cNN]: reason` — one pick per line.
    Verifiers reject any output that contains free-form card names OR tags
    not in the candidate table.
    """
    table = render_cut_table(candidates)
    return f"""You are a Magic: The Gathering deck analyst suggesting cuts.
Output format is strict: your entire response is exactly {count} lines, each
formatted as `[tag]: one-sentence reason`. No preamble. No closing remarks.
No commentary between lines. Reference cards only by their bracket tag
(like [c01]) — never type a card's name in your response.

Prefer cards with stronger signals (higher score, redundancy, or no
functional tag). Pick at most {count} cuts.

{_CUTS_FEWSHOT}

[INPUT]
Deck: {deck_name}
Archetype: {archetype}
Power tier: {power_tier}

Candidates:
{table}

Output exactly {count} lines, each like `[tag]: reason`. No other text.

[OUTPUT]
"""


# =============================================================================
# Add suggestions — tag-constrained emission
# =============================================================================

_ADDS_FEWSHOT = """\
[EXAMPLE]
Role needed: card_draw (current: 6, target: 8-12)
Deck colors: BUG (Sultai)
Candidates (all in-color, all format-legal, not in deck):
  [a01] Brainstorm — {U} (CMC 1) — Draw three cards, then put two back on top of your library.
  [a02] Mystic Remora — {U} (CMC 1) — Cumulative upkeep {1}. When an opponent casts a noncreature spell, you may draw a card unless they pay {4}.
  [a03] Night's Whisper — {1}{B} (CMC 2) — You draw two cards and lose 2 life.
  [a04] Phyrexian Arena — {1}{B}{B} (CMC 3) — At the beginning of your upkeep, draw a card and lose 1 life.
  [a05] Rhystic Study — {2}{U} (CMC 3) — Opponents draw the deck unless they pay {1}.

Request: pick 3 to address the card-draw gap.

Output:
  [a05]: taxes opponents while filling the card-draw engine — best rate at the cost.
  [a04]: steady, uncounterable card-advantage engine that plugs the gap without slowing the deck.
  [a01]: one-mana selection to smooth early turns, works with the deck's tutors.
[/EXAMPLE]
"""


def add_suggestions_prompt(
    role_name: str,
    role_target_low: int,
    role_target_high: int,
    current_count: int,
    candidates: list[AddCandidate],
    deck_name: str,
    archetype: str,
    color_identity: list[str],
    count: int = 3,
) -> str:
    """Add suggestions prompt. Candidates are pre-validated for color / legal / role.

    Verifiers enforce:
      - Output references only `[aNN]` tags present in the table
      - No free-form card names
    Plus a belt-and-suspenders re-check of color identity and format legality
    on resolved picks — even though the candidate query already enforced both.
    """
    table = render_add_table(candidates)
    colors = "".join(color_identity) or "colorless"
    return f"""You are a Magic: The Gathering deck analyst suggesting card
additions. The candidate cards below have already been validated: each is
in the deck's color identity, legal in the target format, and not already
in the deck. You MUST reference cards ONLY by bracket tag like [a01]. Do
NOT type a card's name anywhere in your response.

{_ADDS_FEWSHOT}

[INPUT]
Deck: {deck_name}
Archetype: {archetype}
Colors: {colors}
Role gap: {role_name} (current {current_count}, target {role_target_low}-{role_target_high})

Candidates (all in-color, all legal, not in deck):
{table}

Request: pick {count} cards that best close the {role_name} gap for this
archetype. One pick per line. Each line: `[tag]: one-sentence reason`.

[OUTPUT]
"""

