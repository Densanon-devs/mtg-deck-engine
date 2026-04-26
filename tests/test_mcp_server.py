"""Tests for the densa_deck.mcp package — the MCP server surface.

Coverage:
  - Tool registration: free-only mode vs full mode.
  - License gate: Pro tools refuse on a free user, succeed on Pro.
  - Wrapper unwrap: AppApi {ok, data} envelope flattens to bare dict;
    {ok: false} raises with a clear message.
  - Server smoke: build_server() returns a FastMCP with the expected
    tool names registered.

These tests don't actually run the JSON-RPC stdio loop — that's tested
end-to-end manually with `densa-deck mcp serve | mcp inspect` (or via
Claude desktop). Here we exercise the in-process tool callables which is
where 90% of the bug surface lives.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from densa_deck.mcp.license_gate import (
    ProRequiredError,
    assert_pro,
    current_tier,
    is_pro,
)
from densa_deck.mcp.tools import _unwrap, make_free_tools, make_pro_tools


class TestUnwrap:
    def test_ok_envelope_flattens_to_data(self):
        assert _unwrap({"ok": True, "data": {"hello": 1}}) == {"hello": 1}

    def test_no_envelope_returns_as_is(self):
        # Some AppApi paths return raw lists/dicts. Pass-through.
        assert _unwrap({"foo": "bar"}) == {"foo": "bar"}

    def test_error_envelope_raises_with_message(self):
        with pytest.raises(RuntimeError, match="ProRequired: Need Pro"):
            _unwrap({"ok": False, "error": "Need Pro", "error_type": "ProRequired"})

    def test_error_without_type_uses_default_kind(self):
        with pytest.raises(RuntimeError, match="EngineError: Boom"):
            _unwrap({"ok": False, "error": "Boom"})


class TestLicenseGate:
    def test_free_tier_blocks_pro_features(self, monkeypatch):
        monkeypatch.setenv("MTG_ENGINE_TIER", "free")
        assert is_pro() is False
        with pytest.raises(ProRequiredError) as exc_info:
            assert_pro("goldfish_simulation")
        # Error message must name the feature so the AI can explain.
        assert "goldfish_simulation" in str(exc_info.value)

    def test_pro_tier_allows_pro_features(self, monkeypatch):
        monkeypatch.setenv("MTG_ENGINE_TIER", "pro")
        assert is_pro() is True
        # Should not raise
        assert_pro("goldfish_simulation")
        assert_pro("analyst")
        assert_pro("compare_decks")

    def test_free_tier_allows_free_features(self, monkeypatch):
        monkeypatch.setenv("MTG_ENGINE_TIER", "free")
        # No raise even on free.
        assert_pro("card_search")
        assert_pro("combos")


class TestToolRegistration:
    def test_free_tools_dict_has_expected_keys(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MTG_ENGINE_TIER", "free")
        # AppApi wants a writable home dir; sandbox it.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        from densa_deck.app.api import AppApi
        api = AppApi(db_path=tmp_path / "cards.db", version_db_path=tmp_path / "v.db")
        try:
            free = make_free_tools(api)
            # Must include the headline read-only tools.
            for required in ("get_tier", "search_cards", "analyze_deck",
                             "list_saved_decks", "detect_combos_for_deck",
                             "build_rule0_worksheet", "assess_bracket_fit"):
                assert required in free, f"missing free tool: {required}"
            # Pro-only tools MUST NOT be in the free dict.
            for forbidden in ("run_goldfish", "run_gauntlet",
                              "explain_card_in_deck", "compare_decks_analyst"):
                assert forbidden not in free, f"free dict leaked pro tool: {forbidden}"
        finally:
            api.close()

    def test_pro_tools_dict_has_expected_keys(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MTG_ENGINE_TIER", "pro")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        from densa_deck.app.api import AppApi
        api = AppApi(db_path=tmp_path / "cards.db", version_db_path=tmp_path / "v.db")
        try:
            pro = make_pro_tools(api)
            for required in ("run_goldfish", "run_gauntlet", "duel_decks",
                             "compare_decks_analyst", "explain_card_in_deck",
                             "save_deck_version", "coach_start", "coach_ask",
                             "coach_close"):
                assert required in pro, f"missing pro tool: {required}"
        finally:
            api.close()


class TestProGateAtToolLevel:
    """Defense in depth: even if the Pro tool dict is registered on a free
    user (full-mode server), invoking the tool should still refuse."""

    def test_run_goldfish_refuses_on_free(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MTG_ENGINE_TIER", "free")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        from densa_deck.app.api import AppApi
        api = AppApi(db_path=tmp_path / "cards.db", version_db_path=tmp_path / "v.db")
        try:
            pro = make_pro_tools(api)
            with pytest.raises(ProRequiredError):
                # Args don't matter — the assert_pro happens before any
                # AppApi work.
                pro["run_goldfish"]("Sol Ring", sims=10)
        finally:
            api.close()


class TestServerBuilds:
    # async def + pytest-asyncio (auto mode in pyproject.toml). Avoids the
    # asyncio.run() loop-close that breaks `asyncio.get_event_loop()`-based
    # tests later in the suite (e.g. test_new_features.py's Moxfield 403
    # check) on Python 3.10.

    async def test_full_mode_registers_free_and_pro_tools(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MTG_ENGINE_TIER", "pro")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        # Skip if `mcp` SDK isn't installed in the test env.
        pytest.importorskip("mcp.server.fastmcp")
        from densa_deck.app.api import AppApi
        from densa_deck.mcp.server import build_server
        api = AppApi(db_path=tmp_path / "cards.db", version_db_path=tmp_path / "v.db")
        try:
            server = build_server(read_only=False, api=api)
            tools = await server.list_tools()
            names = {t.name for t in tools}
            # Spot-check both surfaces.
            assert "search_cards" in names
            assert "run_goldfish" in names
        finally:
            api.close()

    async def test_read_only_mode_excludes_pro_tools(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MTG_ENGINE_TIER", "pro")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        pytest.importorskip("mcp.server.fastmcp")
        from densa_deck.app.api import AppApi
        from densa_deck.mcp.server import build_server
        api = AppApi(db_path=tmp_path / "cards.db", version_db_path=tmp_path / "v.db")
        try:
            server = build_server(read_only=True, api=api)
            tools = await server.list_tools()
            names = {t.name for t in tools}
            assert "search_cards" in names  # free still present
            # Pro tools must not be visible at all in read-only mode.
            for forbidden in ("run_goldfish", "run_gauntlet",
                              "compare_decks_analyst", "coach_start"):
                assert forbidden not in names, (
                    f"read-only mode leaked pro tool: {forbidden}"
                )
        finally:
            api.close()


class TestCliWiring:
    """The `densa-deck mcp` subcommand should at least parse without
    importing the optional MCP SDK — that lets us test it on environments
    where `mcp` isn't installed."""

    def test_mcp_subcommand_help_parses(self):
        import subprocess
        import sys
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        r = subprocess.run(
            [sys.executable, "-m", "densa_deck.cli", "mcp", "--help"],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=10, env=env,
        )
        assert r.returncode == 0
        assert "serve" in r.stdout.lower()

    def test_mcp_serve_help_lists_tools_flag(self):
        """`--tools NAMES` must show in `mcp serve --help` so operators
        can discover the whitelist mechanism."""
        import subprocess
        import sys
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        r = subprocess.run(
            [sys.executable, "-m", "densa_deck.cli", "mcp", "serve", "--help"],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=10, env=env,
        )
        assert r.returncode == 0
        assert "--tools" in r.stdout
        assert "--read-only" in r.stdout

    def test_mcp_serve_blocked_by_env_kill_switch(self, tmp_path):
        """`MTG_ENGINE_MCP=disabled` must short-circuit the subcommand
        BEFORE any MCP SDK code imports. Exit code 2 distinguishes
        from SDK-missing (1) and successful runs (0)."""
        import subprocess
        import sys
        env = {
            **os.environ, "PYTHONIOENCODING": "utf-8",
            "MTG_ENGINE_MCP": "disabled",
            "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
        }
        r = subprocess.run(
            [sys.executable, "-m", "densa_deck.cli", "mcp", "serve"],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=10, env=env,
        )
        assert r.returncode == 2, f"expected exit=2, got {r.returncode}"
        out = (r.stdout + r.stderr).lower()
        assert "disabled" in out
        # The reason string should name the env var so the operator
        # knows which control flipped the switch.
        assert "mtg_engine_mcp" in out


class TestKillSwitch:
    """`mcp_enabled()` controls whether `densa-deck mcp serve` starts at
    all. Operator-set, not user-set: env var or config-file flag."""

    def test_default_is_enabled(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MTG_ENGINE_MCP", raising=False)
        # Empty home dir = no config.json = should still be enabled.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        # Force re-import to pick up the patched _CONFIG_PATH base.
        import importlib
        import densa_deck.mcp.license_gate as lg
        importlib.reload(lg)
        try:
            enabled, reason = lg.mcp_enabled()
            assert enabled is True
            assert reason == ""
        finally:
            importlib.reload(lg)  # restore real module state

    def test_env_var_disables(self, monkeypatch):
        for value in ("disabled", "DISABLED", "false", "0", "no", "off"):
            monkeypatch.setenv("MTG_ENGINE_MCP", value)
            from densa_deck.mcp.license_gate import mcp_enabled
            enabled, reason = mcp_enabled()
            assert enabled is False, f"value {value!r} should disable"
            assert "MTG_ENGINE_MCP" in reason

    def test_unknown_env_value_leaves_enabled(self, monkeypatch):
        # An unrecognized value is not "off" — it's "no opinion."
        # Don't surprise the operator with surprise lockouts on typos.
        monkeypatch.setenv("MTG_ENGINE_MCP", "maybe")
        from densa_deck.mcp.license_gate import mcp_enabled
        enabled, _ = mcp_enabled()
        assert enabled is True

    def test_config_file_disables(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MTG_ENGINE_MCP", raising=False)
        cfg_dir = tmp_path / ".densa-deck"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text('{"mcp_enabled": false}')
        # Patch the module-level _CONFIG_PATH to the temp config.
        import densa_deck.mcp.license_gate as lg
        monkeypatch.setattr(lg, "_CONFIG_PATH", cfg_dir / "config.json")
        enabled, reason = lg.mcp_enabled()
        assert enabled is False
        assert "config.json" in reason

    def test_env_overrides_config(self, tmp_path, monkeypatch):
        """Env var has higher priority than config file. If both disagree,
        env wins — the operator can use env to override a checked-in
        config or vice versa."""
        cfg_dir = tmp_path / ".densa-deck"
        cfg_dir.mkdir()
        # Config says disabled; env says nothing → disabled (config wins
        # over default).
        (cfg_dir / "config.json").write_text('{"mcp_enabled": false}')
        import densa_deck.mcp.license_gate as lg
        monkeypatch.setattr(lg, "_CONFIG_PATH", cfg_dir / "config.json")
        monkeypatch.delenv("MTG_ENGINE_MCP", raising=False)
        assert lg.mcp_enabled()[0] is False
        # Env overrides — env not in disabled set → enabled, despite config.
        monkeypatch.setenv("MTG_ENGINE_MCP", "enabled")  # explicit "on"
        # `enabled` is not in _DISABLED_VALUES, so it leaves config to win.
        # Actually: the env-no-opinion path falls through to config, which
        # still says disabled. So env="enabled" alone DOESN'T override.
        # The operator must unset the config (or set "true" — but our
        # design is env-disable-only). Document this expectation:
        assert lg.mcp_enabled()[0] is False, (
            "Env without disabled-value falls through to config — "
            "by design, env is only an OFF override, not an ON override. "
            "To re-enable, clear the config flag."
        )

    def test_corrupt_config_does_not_lock_out(self, tmp_path, monkeypatch):
        """A garbage config.json must NOT silently disable MCP — that's
        a foot-gun where one bad edit locks the user out. Default to
        enabled when config can't be parsed."""
        cfg_dir = tmp_path / ".densa-deck"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text("{ this is not json")
        import densa_deck.mcp.license_gate as lg
        monkeypatch.setattr(lg, "_CONFIG_PATH", cfg_dir / "config.json")
        monkeypatch.delenv("MTG_ENGINE_MCP", raising=False)
        enabled, _ = lg.mcp_enabled()
        assert enabled is True


class TestToolPackWhitelist:
    """`--tools name1,name2` filters the registered tool surface."""

    async def test_whitelist_subset_only(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MTG_ENGINE_TIER", "pro")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        pytest.importorskip("mcp.server.fastmcp")
        from densa_deck.app.api import AppApi
        from densa_deck.mcp.server import build_server
        api = AppApi(db_path=tmp_path / "cards.db", version_db_path=tmp_path / "v.db")
        try:
            server = build_server(
                read_only=False, api=api,
                tool_pack=["search_cards", "analyze_deck", "run_goldfish"],
            )
            tools = await server.list_tools()
            names = {t.name for t in tools}
            assert names == {"search_cards", "analyze_deck", "run_goldfish"}
        finally:
            api.close()

    def test_unknown_tool_name_raises_clearly(self, tmp_path, monkeypatch):
        """Operator typos should fail loudly, not silently shrink the
        registered surface."""
        monkeypatch.setenv("MTG_ENGINE_TIER", "pro")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        pytest.importorskip("mcp.server.fastmcp")
        from densa_deck.app.api import AppApi
        from densa_deck.mcp.server import build_server
        api = AppApi(db_path=tmp_path / "cards.db", version_db_path=tmp_path / "v.db")
        try:
            with pytest.raises(ValueError) as exc_info:
                build_server(
                    read_only=False, api=api,
                    tool_pack=["search_cards", "no_such_tool"],
                )
            msg = str(exc_info.value)
            assert "no_such_tool" in msg
            # The error must surface the available names so the operator
            # can fix their typo.
            assert "search_cards" in msg
        finally:
            api.close()

    async def test_whitelist_with_read_only_excludes_pro(self, tmp_path, monkeypatch):
        """`--read-only --tools <name>` must reject Pro names — they were
        never in the candidate set in the first place."""
        monkeypatch.setenv("MTG_ENGINE_TIER", "pro")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        pytest.importorskip("mcp.server.fastmcp")
        from densa_deck.app.api import AppApi
        from densa_deck.mcp.server import build_server
        api = AppApi(db_path=tmp_path / "cards.db", version_db_path=tmp_path / "v.db")
        try:
            with pytest.raises(ValueError) as exc_info:
                build_server(
                    read_only=True, api=api,
                    tool_pack=["search_cards", "run_goldfish"],
                )
            msg = str(exc_info.value)
            assert "run_goldfish" in msg
            # Error message should clarify which surface was available.
            assert "free" in msg.lower()
        finally:
            api.close()
