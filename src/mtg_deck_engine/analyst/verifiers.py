"""Verifiers that enforce hallucination-free output.

For tag-constrained prompts (cuts, adds), the LLM's output must:
  1. Parse cleanly into (tag, reason) pairs
  2. Reference only tags present in the candidate table
  3. Not contain any card name outside of a tag reference

Failures return a specific error hint that the retry loop can feed back
into the next attempt — PIE-style "errors as context" rather than discard.

For prose prompts (executive summary), the verifier is weaker — it just
checks that the output is non-empty and doesn't contain obvious template
leakage ("[OUTPUT]", "[EXAMPLE]").
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class VerificationError(Exception):
    """Raised when output fails verification. `hint` is fed back on retry."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint or message


@dataclass
class TagPick:
    tag: str
    reason: str


_PICK_LINE_RE = re.compile(r"^\s*\[([a-z][0-9]{1,3})\]\s*:\s*(.+?)\s*$")
# Multi-line variant used by `sub()` when stripping tag lines from output
# for the free-form card-name check. Without re.MULTILINE the anchors
# match string start/end only, so a multi-line emission never gets its
# tag lines stripped and any card name in a reason false-positives.
_PICK_LINE_MULTILINE = re.compile(r"^\s*\[([a-z][0-9]{1,3})\]\s*:.*$", re.MULTILINE)
_TEMPLATE_LEAK_RE = re.compile(r"\[(?:OUTPUT|INPUT|EXAMPLE|/EXAMPLE|FEWSHOT)\]", re.IGNORECASE)


def parse_tag_picks(output: str) -> list[TagPick]:
    """Extract [tag]: reason lines from LLM output.

    Ignores lines that don't match the pattern (lets the model preamble a little
    without failing the whole emission). Raises VerificationError if ZERO
    lines match — the model gave us nothing parseable.
    """
    picks: list[TagPick] = []
    for line in output.splitlines():
        m = _PICK_LINE_RE.match(line)
        if m:
            picks.append(TagPick(tag=m.group(1), reason=m.group(2)))
    if not picks:
        raise VerificationError(
            "No tag-prefixed picks found in output.",
            hint=(
                "Your last response had no picks in the required format. "
                "Each pick must be on its own line, formatted exactly as "
                "`[tag]: reason` — for example, `[c03]: high-cost redundant ramp`."
            ),
        )
    return picks


def verify_tags_in_table(picks: list[TagPick], valid_tags: set[str]) -> None:
    """Reject picks whose tag isn't in the candidate table."""
    unknown = [p.tag for p in picks if p.tag not in valid_tags]
    if unknown:
        raise VerificationError(
            f"Picks reference unknown tag(s): {unknown}",
            hint=(
                f"The tag(s) {unknown} are not in the candidate table. "
                f"Only pick from these tags: {sorted(valid_tags)}. "
                f"Do not invent tags that aren't listed."
            ),
        )


def verify_no_free_form_card_names(
    output: str,
    card_names: set[str],
    min_name_length: int = 5,
) -> None:
    """Reject any output that mentions a card name outside a tag reference.

    `card_names` is the full set of names present in the candidate table plus
    (if available) all deck card names. A free-form match is a case-insensitive
    substring hit on a card name of length >= min_name_length (to avoid noise
    from 2-3 letter words that happen to be card names like "Ice").
    """
    # Strip the tag-reference lines first — those are allowed to mention the card
    # indirectly via their tag. Free-form check runs over whatever remains.
    # Use the MULTILINE variant so every `[cNN]: ...` line in the output is
    # removed, not just the first one when the whole output happens to be a
    # single line.
    stripped = _PICK_LINE_MULTILINE.sub("", output)
    hits: list[str] = []
    lower_stripped = stripped.lower()
    for name in card_names:
        if len(name) < min_name_length:
            continue
        # Require a word-boundary-ish match to avoid "Fire" inside "Campfire"
        pattern = re.compile(r"\b" + re.escape(name.lower()) + r"\b")
        if pattern.search(lower_stripped):
            hits.append(name)
    if hits:
        raise VerificationError(
            f"Free-form card name(s) mentioned: {hits[:5]}",
            hint=(
                "Do not type card names directly. Reference cards only by their "
                "bracket tag like [c01]. Your last response included the card "
                f"name(s) {hits[:3]} outside a tag reference — rewrite using only tags."
            ),
        )


def verify_add_picks_constraints(
    picks: list[TagPick],
    candidates_by_tag: dict,  # dict[str, AddCandidate]
    deck_color_identity: set[str],
    format_key: str,
) -> None:
    """Belt-and-suspenders: re-verify every picked card against color + legality.

    The candidate query already enforced these; this re-check catches any
    regression in the query, drift in the classifier, or tag collisions. If
    ANY pick fails here, the whole batch is rejected — we don't partially
    emit. The retry hint names the specific violating tag.
    """
    from mtg_deck_engine.models import Legality

    for p in picks:
        cand = candidates_by_tag.get(p.tag)
        if cand is None:
            # parse_tag_picks already checked this via verify_tags_in_table,
            # but guarding again keeps this function independently safe.
            continue
        card = cand.card
        card_ci = {c.value for c in card.color_identity}
        if not card_ci.issubset(deck_color_identity):
            raise VerificationError(
                f"Pick [{p.tag}] card has color identity {card_ci} outside deck colors {deck_color_identity}.",
                hint=(
                    f"[{p.tag}] maps to a card whose color identity isn't a subset "
                    f"of the deck. Pick a different tag from the candidate list."
                ),
            )
        leg = card.legalities.get(format_key)
        if leg not in (Legality.LEGAL, Legality.RESTRICTED):
            raise VerificationError(
                f"Pick [{p.tag}] not legal in {format_key} (status: {leg}).",
                hint=(
                    f"[{p.tag}] maps to a card that isn't legal in {format_key}. "
                    f"Choose a different tag."
                ),
            )


def verify_prose_output(output: str, min_chars: int = 80) -> None:
    """Weak verifier for prose-only prompts.

    Checks: non-empty, above minimum length, no leaked template markers.
    """
    text = output.strip()
    if len(text) < min_chars:
        raise VerificationError(
            f"Prose output too short ({len(text)} chars, need >= {min_chars}).",
            hint="Write a complete 2-paragraph summary. Don't leave it truncated.",
        )
    if _TEMPLATE_LEAK_RE.search(text):
        raise VerificationError(
            "Output contains template markers (e.g. [OUTPUT]).",
            hint=(
                "Write only the summary text. Do not include `[OUTPUT]`, "
                "`[INPUT]`, `[EXAMPLE]` or similar template markers in your response."
            ),
        )
