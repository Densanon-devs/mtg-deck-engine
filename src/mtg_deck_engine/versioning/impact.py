"""Change impact analysis: compare two deck versions with full analytics.

Runs static analysis on both versions, computes deltas for every metric,
and generates a report explaining what improved, what regressed, and why.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mtg_deck_engine.versioning.storage import DeckDiff, DeckSnapshot


@dataclass
class ImpactReport:
    """Full change impact analysis between two deck versions."""

    deck_name: str = ""
    version_a: int = 0
    version_b: int = 0

    # Card changes
    diff: DeckDiff | None = None

    # Score comparisons
    score_a: dict[str, float] = field(default_factory=dict)
    score_b: dict[str, float] = field(default_factory=dict)
    score_deltas: dict[str, float] = field(default_factory=dict)

    # Metric comparisons
    metric_a: dict[str, float] = field(default_factory=dict)
    metric_b: dict[str, float] = field(default_factory=dict)
    metric_deltas: dict[str, float] = field(default_factory=dict)

    # Verdicts
    improvements: list[str] = field(default_factory=list)
    regressions: list[str] = field(default_factory=list)
    neutral: list[str] = field(default_factory=list)
    overall_verdict: str = ""  # "improved", "regressed", "mixed", "neutral"


# Score names for display
_SCORE_NAMES = {
    "mana_base": "Mana Base",
    "ramp": "Ramp",
    "card_advantage": "Card Advantage",
    "interaction": "Interaction",
    "curve": "Curve",
    "threat_density": "Threat Density",
}

_METRIC_NAMES = {
    "land_count": "Land Count",
    "ramp_count": "Ramp Count",
    "draw_count": "Card Draw Count",
    "interaction_count": "Interaction Count",
    "threat_count": "Threat Count",
    "average_cmc": "Average Mana Value",
    "total_cards": "Total Cards",
}

# Thresholds for classifying changes
_SCORE_THRESHOLD = 3.0     # Points change to count as meaningful
_METRIC_THRESHOLD = 0.5    # Absolute change to count as meaningful


def analyze_impact(
    snap_a: DeckSnapshot,
    snap_b: DeckSnapshot,
    diff: DeckDiff,
) -> ImpactReport:
    """Generate a change impact report between two versions."""
    report = ImpactReport(
        deck_name=snap_a.deck_id,
        version_a=snap_a.version_number,
        version_b=snap_b.version_number,
        diff=diff,
        score_a=snap_a.scores,
        score_b=snap_b.scores,
        score_deltas=diff.score_deltas,
        metric_a=snap_a.metrics,
        metric_b=snap_b.metrics,
        metric_deltas=diff.metric_deltas,
    )

    # Analyze score changes
    for key, delta in diff.score_deltas.items():
        name = _SCORE_NAMES.get(key, key.replace("_", " ").title())
        old = snap_a.scores.get(key, 0)
        new = snap_b.scores.get(key, 0)

        if abs(delta) < _SCORE_THRESHOLD:
            report.neutral.append(f"{name}: {old:.0f} -> {new:.0f} (no significant change)")
        elif delta > 0:
            report.improvements.append(f"{name}: {old:.0f} -> {new:.0f} (+{delta:.0f})")
        else:
            report.regressions.append(f"{name}: {old:.0f} -> {new:.0f} ({delta:.0f})")

    # Analyze metric changes
    for key, delta in diff.metric_deltas.items():
        name = _METRIC_NAMES.get(key, key.replace("_", " ").title())
        old = snap_a.metrics.get(key, 0)
        new = snap_b.metrics.get(key, 0)

        if abs(delta) < _METRIC_THRESHOLD:
            continue
        elif delta > 0:
            report.improvements.append(f"{name}: {old:.1f} -> {new:.1f} (+{delta:.1f})")
        else:
            report.regressions.append(f"{name}: {old:.1f} -> {new:.1f} ({delta:.1f})")

    # Overall verdict
    if len(report.improvements) > len(report.regressions) * 2:
        report.overall_verdict = "improved"
    elif len(report.regressions) > len(report.improvements) * 2:
        report.overall_verdict = "regressed"
    elif report.improvements and report.regressions:
        report.overall_verdict = "mixed"
    else:
        report.overall_verdict = "neutral"

    return report
