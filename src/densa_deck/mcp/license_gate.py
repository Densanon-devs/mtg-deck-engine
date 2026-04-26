"""License gate + global enable check for MCP-exposed tools.

Tier system: same as CLI / desktop UI — `tiers.get_user_tier()` reads
`MTG_ENGINE_TIER` env, then `~/.densa-deck/config.json`, then the saved
license file. Pro activation in the desktop app unlocks Pro MCP tools
on next reconnect.

Global on/off switch: `mcp_enabled()` returns False when the operator
has set `MTG_ENGINE_MCP=disabled` (env, highest priority) or
`{"mcp_enabled": false}` in `~/.densa-deck/config.json`. The CLI checks
this *before* it imports any MCP code, so an MCP-disabled install
short-circuits cleanly without touching the SDK.

Three integration points:

- `mcp_enabled()` — runtime kill switch. Operator-set, not user-set.
- `current_tier()` returns the tier at server start (used to log a clear
  banner so the user can see "Free tier — Pro tools will refuse" or "Pro
  tier — full surface enabled").
- `assert_pro(feature)` is called at the top of every Pro tool. Raises
  ProRequiredError on a free user; the FastMCP framework catches this and
  surfaces it as a tool error to the AI client, which can then explain
  the situation to the user instead of silently retrying.

Defense in depth: a `--read-only` flag on `densa-deck mcp serve` skips
registering all Pro tools entirely, so an AI agent can't even *see* them.
That's the right default for someone exposing the server to a less-trusted
agent — the model can't be tempted to call goldfish in a tight loop if
the tool isn't in its registry.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from densa_deck.tiers import Tier, check_access, get_user_tier

_CONFIG_PATH = Path.home() / ".densa-deck" / "config.json"

# Values the env var treats as "off". Anything else (including unset)
# leaves MCP enabled. Mirrors the convention the rest of the engine uses
# for boolean env flags, including the few existing places in tiers.py
# that parse env strings.
_DISABLED_VALUES = frozenset({"disabled", "false", "0", "no", "off"})


def mcp_enabled() -> tuple[bool, str]:
    """Return `(enabled, reason)` for the MCP server.

    Operator-set kill switch — used by the CLI to short-circuit
    `densa-deck mcp serve` before any MCP code imports. The reason
    string is surfaced in the user-facing refusal message so it's clear
    *which* control flipped the switch.

    Precedence (highest first):
      1. `MTG_ENGINE_MCP` env var. "disabled"/"false"/"0"/"no"/"off"
         (case-insensitive) → disabled. Anything else → no opinion.
      2. `~/.densa-deck/config.json`'s `{"mcp_enabled": false}`.
         Boolean only; anything else (missing, garbage) → no opinion.
      3. Default: enabled.
    """
    env = os.environ.get("MTG_ENGINE_MCP", "").lower().strip()
    if env in _DISABLED_VALUES:
        return False, f"MTG_ENGINE_MCP={env}"
    # Config file is optional — missing/corrupt = no opinion, fall through.
    if _CONFIG_PATH.exists():
        try:
            data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("mcp_enabled") is False:
                return False, f"~/.densa-deck/config.json: mcp_enabled=false"
        except (json.JSONDecodeError, OSError):
            # Don't fail closed on a corrupt config — that would lock the
            # user out for a stray edit. Treat as no opinion.
            pass
    return True, ""


class ProRequiredError(Exception):
    """Raised when a free-tier session calls a Pro-only MCP tool.

    FastMCP catches exceptions and surfaces them to the AI client as
    structured tool errors, so the model gets a clear "this requires Pro"
    message instead of an opaque traceback.
    """

    def __init__(self, feature: str):
        self.feature = feature
        super().__init__(
            f"'{feature}' requires Densa Deck Pro. "
            "Activate a license in the desktop app's Settings tab "
            "(or set MTG_ENGINE_TIER=pro for testing)."
        )


def current_tier() -> Tier:
    """Return the user's current tier. Used for the startup banner."""
    return get_user_tier()


def is_pro() -> bool:
    return current_tier() == Tier.PRO


def assert_pro(feature: str) -> None:
    """Raise ProRequiredError if the current tier doesn't satisfy `feature`.

    `feature` is a key from `tiers.FEATURE_TIERS` — same names the CLI
    uses, e.g. "goldfish_simulation", "compare_decks", "analyst".
    """
    if not check_access(feature):
        raise ProRequiredError(feature)
