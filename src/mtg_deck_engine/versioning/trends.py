"""Version history trend tracking and regression detection.

Analyzes score and metric trends across all saved versions of a deck
to detect improvements, regressions, and suggest adaptations.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mtg_deck_engine.versioning.storage import DeckSnapshot


@dataclass
class TrendPoint:
    """A single data point in a trend line."""

    version: int
    value: float
    saved_at: str = ""


@dataclass
class ScoreTrend:
    """Trend for a single score across versions."""

    name: str
    points: list[TrendPoint] = field(default_factory=list)
    direction: str = "stable"  # "improving", "declining", "stable", "volatile"
    current: float = 0.0
    best: float = 0.0
    worst: float = 0.0
    delta_first_to_last: float = 0.0
    delta_recent: float = 0.0  # Last 2 versions


@dataclass
class TrendReport:
    """Complete trend analysis across all versions of a deck."""

    deck_id: str = ""
    deck_name: str = ""
    total_versions: int = 0

    score_trends: dict[str, ScoreTrend] = field(default_factory=dict)
    metric_trends: dict[str, ScoreTrend] = field(default_factory=dict)

    # Summary
    improving_scores: list[str] = field(default_factory=list)
    declining_scores: list[str] = field(default_factory=list)
    stable_scores: list[str] = field(default_factory=list)

    # Suggestions
    suggestions: list[str] = field(default_factory=list)


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
}


def analyze_trends(snapshots: list[DeckSnapshot]) -> TrendReport:
    """Analyze score and metric trends across deck versions."""
    report = TrendReport(total_versions=len(snapshots))

    if not snapshots:
        return report

    report.deck_id = snapshots[0].deck_id

    # Build score trends
    all_score_keys: set[str] = set()
    for snap in snapshots:
        all_score_keys.update(snap.scores.keys())

    for key in sorted(all_score_keys):
        name = _SCORE_NAMES.get(key, key.replace("_", " ").title())
        trend = _build_trend(name, key, snapshots, is_score=True)
        report.score_trends[key] = trend

        if trend.direction == "improving":
            report.improving_scores.append(name)
        elif trend.direction == "declining":
            report.declining_scores.append(name)
        else:
            report.stable_scores.append(name)

    # Build metric trends
    all_metric_keys: set[str] = set()
    for snap in snapshots:
        all_metric_keys.update(snap.metrics.keys())

    for key in sorted(all_metric_keys):
        name = _METRIC_NAMES.get(key, key.replace("_", " ").title())
        trend = _build_trend(name, key, snapshots, is_score=False)
        report.metric_trends[key] = trend

    # Generate suggestions
    report.suggestions = _generate_suggestions(report)

    return report


def _build_trend(
    name: str,
    key: str,
    snapshots: list[DeckSnapshot],
    is_score: bool,
) -> ScoreTrend:
    """Build a trend line for one score/metric."""
    trend = ScoreTrend(name=name)

    for snap in snapshots:
        source = snap.scores if is_score else snap.metrics
        value = source.get(key, 0.0)
        trend.points.append(TrendPoint(
            version=snap.version_number,
            value=value,
            saved_at=snap.saved_at,
        ))

    if not trend.points:
        return trend

    values = [p.value for p in trend.points]
    trend.current = values[-1]
    trend.best = max(values)
    trend.worst = min(values)
    trend.delta_first_to_last = round(values[-1] - values[0], 2)

    if len(values) >= 2:
        trend.delta_recent = round(values[-1] - values[-2], 2)

    # Classify direction
    trend.direction = _classify_direction(values)

    return trend


def _classify_direction(values: list[float]) -> str:
    """Classify the direction of a trend."""
    if len(values) < 2:
        return "stable"

    # Look at the overall trajectory and recent movement
    total_delta = values[-1] - values[0]
    recent_delta = values[-1] - values[-2] if len(values) >= 2 else 0

    # Check for volatility
    if len(values) >= 3:
        changes = [values[i + 1] - values[i] for i in range(len(values) - 1)]
        sign_changes = sum(
            1 for i in range(len(changes) - 1)
            if (changes[i] > 0) != (changes[i + 1] > 0)
        )
        if sign_changes >= len(changes) * 0.6:
            return "volatile"

    threshold = 2.0

    if total_delta > threshold and recent_delta >= 0:
        return "improving"
    elif total_delta < -threshold and recent_delta <= 0:
        return "declining"
    elif abs(total_delta) > threshold:
        # Mixed — overall positive but recent decline, or vice versa
        return "volatile"
    else:
        return "stable"


def _generate_suggestions(report: TrendReport) -> list[str]:
    """Generate suggestions based on trends."""
    suggestions: list[str] = []

    # Declining scores need attention
    for name in report.declining_scores:
        suggestions.append(f"{name} is declining across versions — review recent cuts in this area.")

    # Volatile scores suggest unfocused changes
    for key, trend in report.score_trends.items():
        if trend.direction == "volatile":
            suggestions.append(
                f"{trend.name} is volatile — changes are alternating between better and worse. "
                f"Consider testing one direction more thoroughly."
            )

    # Score at historical worst
    for key, trend in report.score_trends.items():
        if trend.points and trend.current == trend.worst and trend.current < trend.best - 5:
            suggestions.append(
                f"{trend.name} is at its lowest point ({trend.current:.0f}). "
                f"Best was {trend.best:.0f} in an earlier version."
            )

    # All improving — positive feedback
    if report.improving_scores and not report.declining_scores:
        suggestions.append("All tracked scores are improving — the deck is getting tighter.")

    if not suggestions:
        suggestions.append("Scores are stable across versions. The deck may be at a local optimum.")

    return suggestions
