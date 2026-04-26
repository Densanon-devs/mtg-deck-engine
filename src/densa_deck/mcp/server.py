"""FastMCP server entry point.

`build_server(read_only=False)` constructs a FastMCP server with every
free-tier tool registered as a `@mcp.tool()`. When read_only=False (the
default), it ALSO registers Pro-tier tools — but each Pro tool calls
`assert_pro(...)` at the top, so a free user invoking one gets a clean
ProRequiredError surfaced through MCP rather than silently failing.

`run_stdio_server(read_only=False)` is the one-line entry point the CLI
calls. It builds the server and runs it on stdio (the transport every
desktop AI client uses for local MCP servers).

The MCP package is an OPTIONAL dependency — `pip install densa-deck[mcp]`
installs `mcp[cli]`. If the user runs `densa-deck mcp serve` without that
extra installed, the import here fails with a clear hint pointing at the
extras install command.
"""

from __future__ import annotations

import sys

from densa_deck.app.api import AppApi
from densa_deck.mcp import tools as tools_mod
from densa_deck.mcp.license_gate import current_tier


class McpSdkMissingError(RuntimeError):
    """Raised when `build_server()` is called without the `mcp` SDK
    installed. Distinct from ImportError so the CLI entry point can
    catch it and print a clean install hint without exiting through
    the generic exception path. Tests can also catch it to skip cleanly."""


def _import_fastmcp():
    """Lazy-import FastMCP so the rest of the package stays importable
    without the `mcp` dep installed (matters for tests that don't need
    the protocol surface, and for the CLI's `mcp --help` path)."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:
        raise McpSdkMissingError(
            "The MCP server requires the 'mcp' SDK. Install with:\n"
            "    pip install 'densa-deck[mcp]'\n"
            "or:\n"
            "    pip install 'mcp[cli]'\n"
            f"Original import error: {e}"
        )
    return FastMCP


def build_server(read_only: bool = False, api: "AppApi | None" = None):
    """Build a FastMCP server with the tool surface registered.

    `api` is optional — pass an instance for tests; production builds a
    fresh one against the user's `~/.densa-deck/` data directory.
    """
    FastMCP = _import_fastmcp()

    mcp = FastMCP(
        "densa-deck",
        instructions=(
            "Densa Deck local MTG deck-analysis engine. Tools cover deck "
            "analysis, card search, Commander Spellbook combo detection, "
            "version history, and (Pro tier) goldfish simulation, matchup "
            "gauntlet, LLM analyst, and the coach REPL. Call get_tier "
            "first to find out whether Pro tools are available; call "
            "get_combo_status before any combo tool to make sure the "
            "local cache is populated."
        ),
    )

    if api is None:
        api = AppApi()

    # Free tools: always registered.
    free = tools_mod.make_free_tools(api)
    for name, fn in free.items():
        mcp.tool(name=name)(fn)

    # Pro tools: skipped entirely in read-only mode (so a less-trusted
    # agent can't even see them in the tool list). Otherwise registered
    # with the per-tool assert_pro() defense in depth — a free user
    # invoking one gets a clear ProRequiredError.
    if not read_only:
        pro = tools_mod.make_pro_tools(api)
        for name, fn in pro.items():
            mcp.tool(name=name)(fn)

    return mcp


def run_stdio_server(read_only: bool = False) -> None:
    """Build the server and run it on stdio. CLI entry point.

    Logs a one-line tier banner to stderr so the user can see "Free tier"
    vs "Pro tier" without it polluting the JSON-RPC channel on stdout.

    Owns the AppApi lifetime — closes it in a `finally` so SQLite
    connections (cards.db, versions.db, combos.db) and any background
    threads are cleaned up when the AI client closes the subprocess.
    """
    tier = current_tier()
    sys.stderr.write(
        f"Densa Deck MCP server ready ({tier.value} tier"
        f"{', read-only' if read_only else ''}).\n"
    )
    sys.stderr.flush()

    api = AppApi()
    try:
        server = build_server(read_only=read_only, api=api)
        server.run(transport="stdio")
    finally:
        try:
            api.close()
        except Exception:
            # Don't mask the real exit reason with a cleanup error.
            pass
