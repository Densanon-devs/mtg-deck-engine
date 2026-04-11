"""Feature tier system — free vs pro gating.

Monetization is feature-gated, never data-gated. Raw card data and basic
analysis are always free. Premium features include deep simulation, extended
deck storage, coaching insights, and advanced matchup testing.
"""

from __future__ import annotations

import enum


class Tier(str, enum.Enum):
    FREE = "free"
    PRO = "pro"


# Maps feature keys to the minimum tier required
FEATURE_TIERS: dict[str, Tier] = {
    # Always free
    "card_search": Tier.FREE,
    "deck_import": Tier.FREE,
    "deck_validation": Tier.FREE,
    "static_analysis": Tier.FREE,
    "basic_mana_curve": Tier.FREE,
    "basic_recommendations": Tier.FREE,
    # Pro features
    "goldfish_simulation": Tier.PRO,
    "matchup_gauntlet": Tier.PRO,
    "deck_version_history": Tier.PRO,
    "unlimited_deck_storage": Tier.PRO,
    "advanced_scoring": Tier.PRO,
    "coaching_insights": Tier.PRO,
    "export_reports": Tier.PRO,
    "custom_benchmark_suites": Tier.PRO,
}


def check_access(feature: str, user_tier: Tier) -> bool:
    """Check whether a user's tier grants access to a feature."""
    required = FEATURE_TIERS.get(feature)
    if required is None:
        return True  # Unknown features default to open
    if user_tier == Tier.PRO:
        return True  # Pro gets everything
    return required == Tier.FREE
