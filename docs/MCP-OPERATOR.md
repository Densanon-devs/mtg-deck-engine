# Densa Deck MCP server — operator's guide

The MCP (Model Context Protocol) server lets AI clients — Claude desktop,
ulcagent, Cursor, anything that speaks MCP — drive the Densa Deck engine
through tool calls instead of GUI clicks. Free-tier read-only tools are
always available; Pro-tier simulation, analyst, and coach tools unlock
when a Pro license is active.

This doc is for **operators** — the person deciding what gets exposed,
to which AI clients, with which controls. Players who just want to use
the desktop app don't need to read this.

---

## TL;DR — fastest path from install to working

```bash
# 1. Verify the server side works (no Claude desktop involved):
densa-deck mcp selftest
# Expected: "OK — 28 tools registered (pro tier)."

# 2. Get a paste-ready Claude desktop config block with YOUR install's
#    exe path already filled in:
densa-deck mcp config
# Expected: prints a {"mcpServers": {"densa-deck": {"command": "...", "args": [...]}}} block

# 3. Open Claude desktop's config file (path printed by mcp config),
#    paste the block, save, fully quit + relaunch Claude desktop.
```

The desktop UI also has a built-in **Settings → AI client integration (MCP)**
panel with "Show Claude desktop config" / "Verify connection" / "Copy to
clipboard" buttons. Same flow, but click-driven for non-CLI users.

- **Curate the exposed surface:** `densa-deck mcp serve --tools name1,name2`.
- **Hide every Pro tool from the AI client:** `--read-only`.
- **Turn it off entirely:** `MTG_ENGINE_MCP=disabled` env, or
  `{"mcp_enabled": false}` in `~/.densa-deck/config.json`.

### Why `mcp config` instead of writing the JSON yourself

The bundled installer drops `densa-deck.exe` at
`%LOCALAPPDATA%\Programs\Densa Deck\densa-deck.exe` and does **not** add
it to PATH. A generic config with `"command": "densa-deck"` will fail
on installer customers — Claude desktop reports "command not found." The
`mcp config` subcommand resolves the right path for your install
(absolute path for frozen binaries, bare `densa-deck` for `pip install`)
so the customer never has to think about it.

---

## How it works

`densa-deck mcp serve` starts a JSON-RPC server on stdio. The AI client
launches it as a subprocess, exchanges tool calls + results over the
pipes, and shuts it down when the conversation ends. No network port.
No daemon. The server lives exactly as long as the AI client keeps it
running.

License tier is read at startup from the same place the desktop app
reads it — `MTG_ENGINE_TIER` env var, then `~/.densa-deck/config.json`,
then the saved license file. A Pro activation in the desktop UI unlocks
the Pro tools on the AI client's next reconnect with no extra step.

The server prints a one-line tier banner to **stderr** (so the JSON-RPC
channel on **stdout** stays clean):

```
Densa Deck MCP server ready (pro tier).
Densa Deck MCP server ready (free tier, read-only).
Densa Deck MCP server ready (pro tier, tools=search_cards,analyze_deck).
```

---

## Controls

### 1. Operator kill switch — `MTG_ENGINE_MCP` / `mcp_enabled`

Use this when MCP must be **off** regardless of what an AI client tries.
The CLI checks this before any MCP code imports, so a paranoid install
never even loads the SDK.

| Mechanism | How to set | Effect | Priority |
|---|---|---|---|
| `MTG_ENGINE_MCP` env | `MTG_ENGINE_MCP=disabled` (or `false`/`0`/`no`/`off`, case-insensitive) | Refuses to start; exits 2 | Highest |
| Config file | `{"mcp_enabled": false}` in `~/.densa-deck/config.json` | Same | Falls through to default if env doesn't say "off" |
| Default | (nothing set) | Enabled | Lowest |

Behavior:

```bash
MTG_ENGINE_MCP=disabled densa-deck mcp serve
# MCP server is disabled by operator setting (MTG_ENGINE_MCP=disabled).
# Remove the env var or flip mcp_enabled back to true in
# ~/.densa-deck/config.json to re-enable.
# (exit 2)
```

**Foot-gun protection:**

- An unrecognized env value (`MTG_ENGINE_MCP=maybe`, `yes`, anything not
  in the disabled-list) is treated as "no opinion." Typos can't
  surprise-disable the server.
- A corrupt `config.json` is treated as "no opinion." One bad edit
  can't lock you out — you'll always have the env var as a recovery
  path.

### 2. `--tools NAMES` whitelist

Filter the exposed surface to a curated subset.

```bash
densa-deck mcp serve --tools "search_cards,analyze_deck,run_goldfish"
```

Operator typos surface as a clear error before the server starts:

```
Densa Deck MCP server ready (pro tier, tools=foo).
Unknown MCP tool name(s): foo. Available free+pro tools:
analyze_deck, assess_bracket_fit, build_rule0_worksheet, ...
(exit 1)
```

Composes with `--read-only`: the Pro tools are dropped from the
candidate set first, then the whitelist applies. Asking for a Pro
tool in read-only mode errors out — the tool isn't available to
whitelist.

### 3. `--read-only`

Drops every Pro tool from registration. The AI client can't even *see*
goldfish / gauntlet / coach / save / explain / compare / suggest. Same
effect as withholding a Pro license, but enforced at the wire layer
rather than relying on the per-tool `assert_pro` defense in depth.

```bash
densa-deck mcp serve --read-only
# 17 free tools registered, Pro tools never appear in tools/list
```

### 4. License tier — `MTG_ENGINE_TIER`

Same control the desktop app uses. Reads `pro` or `free` (case
insensitive). Useful for testing — leave it unset in production and let
the saved license file do its job.

```bash
MTG_ENGINE_TIER=pro densa-deck mcp serve   # forces Pro for testing
MTG_ENGINE_TIER=free densa-deck mcp serve  # simulates free user
```

### Subcommands

`densa-deck mcp` has three subcommands. Customers should generally start
with `selftest`, then `config`. `serve` is what AI clients call.

| Subcommand | Use case | Exits |
|---|---|---|
| `mcp selftest` | "Does the server side work?" Builds the server in-process, lists tools, exits cleanly. Run this BEFORE filing a bug or debugging Claude desktop. | 0 ok / 1 SDK or build issue / 2 disabled |
| `mcp config` | "Give me the right Claude desktop config block." Emits a paste-ready JSON config with the install's exe path resolved. Supports `--read-only` and `--tools NAMES` to bake those into the emitted args. | 0 |
| `mcp serve` | What an AI client launches as a subprocess. Speaks JSON-RPC on stdio. Supports `--read-only` and `--tools NAMES`. | 0 ok / 1 SDK missing or bad `--tools` / 2 disabled |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Server ran cleanly until the AI client closed the subprocess (or `mcp config` finished printing) |
| `1` | `mcp` SDK not installed, OR an unknown name in `--tools`, OR selftest failed |
| `2` | Operator kill switch active (env var or config) |

Any other non-zero exit indicates a real crash — capture stderr and
file a bug.

---

## Tool inventory

### Free tier (always available, 17 tools)

| Tool | What it does |
|---|---|
| `get_tier` | Report whether Pro features are available. AI should call this first. |
| `get_current_version` | Report the running Densa Deck version. |
| `search_cards` | Structured search (name / colors / CMC / type / format / rarity / price). |
| `get_card` | Fetch one card by exact name. |
| `resolve_suggestions` | Fuzzy typo-fix for unresolved card names. |
| `analyze_deck` | Full static analysis: archetype, power, mana curve, ramp/draw/interaction, castability, recommendations. |
| `assess_bracket_fit` | Score the deck against a Commander bracket (1-precon … 5-cEDH) with a punch-list. |
| `list_saved_decks` | List the user's saved decks (deck_id + version count). |
| `get_deck_latest` | Latest version of a saved deck (snapshot + reconstructed text). |
| `get_deck_history` | All versions of a saved deck, newest-first. |
| `diff_deck_versions` | Cards added/removed, score deltas, combo gained/lost. |
| `import_deck_from_url` | Fetch a Moxfield/Archidekt URL into pasteable text. |
| `export_deck_format` | Export to MTGA / MTGO / Moxfield text. |
| `build_rule0_worksheet` | Pre-game disclosure sheet (archetype, power, bracket, win conditions, combo lines). |
| `get_combo_status` | Local Spellbook combo cache status (count, last refresh). |
| `detect_combos_for_deck` | Full combo lines present in the deck. |
| `detect_near_miss_combos_for_deck` | Combos N cards away from completing. |

### Pro tier (license-gated, 11 tools)

| Tool | What it does |
|---|---|
| `run_goldfish` | Goldfish simulation (combo-aware). Caps at 5000 sims. |
| `run_gauntlet` | 11-archetype matchup gauntlet. Caps at 500 sims. |
| `duel_decks` | Saved-deck-vs-saved-deck simulation. Caps at 500 sims. |
| `suggest_deckbuild_additions` | Role-gap + combo-completer ranked add suggestions. |
| `explain_card_in_deck` | LLM-narrated "why is this card flagged" for a single card. |
| `compare_decks_analyst` | LLM-narrated comparison of two saved decks. |
| `save_deck_version` | Save a new version of a deck (returns combos broken). |
| `coach_start` | Open an interactive coach session against a deck. |
| `coach_ask` | Send a turn to a coach session. |
| `coach_get_history` | Full turn history for a coach session. |
| `coach_close` | Close a coach session. |

### Tools deliberately NOT exposed via MCP

These are reachable only through the desktop UI or CLI — never the AI
agent surface, by design:

- `delete_deck` — destructive, too easy for a confused agent to drop a
  deck on a stray prompt.
- `set_user_preferences`, `activate_license` — privileged operations
  the user should drive directly.
- `open_external` — browser hijack via prompt injection is a real
  attack class; not worth the convenience.
- `ingest_start`, `analyst_pull_start`, `combo_refresh_start` and their
  progress polls — threaded background ops the user kicks off from the
  Settings tab.
- Builder draft management, first-run state, "what changed" diffs —
  UI-state plumbing the AI doesn't need.

---

## Recipes

### A. Default — paying customer, full surface

```jsonc
{
  "mcpServers": {
    "densa-deck": {
      "command": "densa-deck",
      "args": ["mcp", "serve"]
    }
  }
}
```

License flows through automatically. AI sees 28 tools when Pro is
active, 17 when not.

### B. Privacy-paranoid — analyze decks without exposing saved-deck history

```jsonc
{
  "mcpServers": {
    "densa-deck": {
      "command": "densa-deck",
      "args": [
        "mcp", "serve",
        "--read-only",
        "--tools", "search_cards,get_card,analyze_deck,assess_bracket_fit,detect_combos_for_deck,detect_near_miss_combos_for_deck,build_rule0_worksheet,export_deck_format"
      ]
    }
  }
}
```

The AI can analyze decks the user pastes in but can't list, read, or
diff anything from `~/.densa-deck/versions.db`. The combo cache and
card DB are read-only.

### C. Smaller-context AI client — stay under the regression cliff

Many smaller models (anything in the 7B–14B range) regress when their
tool registry crosses ~10 tools. Curate to the high-leverage subset:

```jsonc
{
  "mcpServers": {
    "densa-deck": {
      "command": "densa-deck",
      "args": [
        "mcp", "serve",
        "--tools", "search_cards,analyze_deck,run_goldfish,assess_bracket_fit,detect_combos_for_deck,suggest_deckbuild_additions,explain_card_in_deck"
      ]
    }
  }
}
```

Seven tools cover the deckbuilding loop end-to-end (search → assemble →
analyze → goldfish → bracket-check → near-miss combos → swap
suggestions → why-this-card) without overflowing the context budget.

### D. Corporate / shared-machine — fully off

```bash
# Per-machine via config, persistent across all users / shells:
echo '{"mcp_enabled": false}' > ~/.densa-deck/config.json

# Or per-shell session:
export MTG_ENGINE_MCP=disabled
```

Either gate refuses `densa-deck mcp serve` cleanly with exit 2. No MCP
code loads. The CLI / desktop UI continue to work normally — the gate
is MCP-specific.

### E. Coach-only — Pro tier, conversation interface only

```jsonc
{
  "mcpServers": {
    "densa-deck": {
      "command": "densa-deck",
      "args": [
        "mcp", "serve",
        "--tools", "list_saved_decks,get_deck_latest,coach_start,coach_ask,coach_close"
      ]
    }
  }
}
```

The AI can pick a saved deck and run a coach session against it.
Nothing else.

---

## Security model

**Local-only, in-process, stdio-only.**

- The server runs as a subprocess of the AI client, on **stdio**. There
  is no network listener. There is no localhost port. The protocol is
  JSON-RPC over the subprocess's stdin/stdout pipes.
- The user's saved decks, license key, card DB, and Spellbook combo
  cache stay on local disk in `~/.densa-deck/`. The server never sends
  any of that to a remote service.
- `import_deck_from_url` is the only tool that makes outbound network
  calls — and only when the user explicitly asks the AI to import a
  Moxfield/Archidekt URL. Don't whitelist it if outbound HTTP from the
  Densa Deck process is unacceptable in your environment.
- Tools that *could* run for a long time (`run_goldfish`,
  `run_gauntlet`, `duel_decks`) cap their sim counts (5000 / 500 / 500
  respectively) so a runaway agent loop can't tie up the CPU
  indefinitely.
- License gating is offline. The Pro tools call `assert_pro` on every
  invocation against the locally-cached tier; an air-gapped Pro user
  has the full surface.

**What the server CANNOT prevent**: an AI client that connects to it is
trusted to call the tools responsibly. The server has no audit trail of
which prompt triggered which call. If you need that, run the AI client
in a sandbox that proxies + logs the MCP traffic — out of scope for
the server itself.

---

## Troubleshooting

### Step 0: run selftest first

Almost every "Claude desktop doesn't see Densa Deck tools" report is
fixable with one command:

```bash
densa-deck mcp selftest
```

- **If selftest is OK** → the server side is fine; the problem is in
  the Claude desktop config (wrong path, wrong file, or Claude desktop
  not fully restarted). Re-run `densa-deck mcp config` and re-paste.
- **If selftest fails** → the failure_kind tells you which side to fix
  (sdk_missing, disabled, build_failed, list_failed).

### "MCP server is disabled by operator setting"

The kill switch is active. The reason string in the message names which
control flipped:

- `MTG_ENGINE_MCP=disabled` → unset the env var (`unset MTG_ENGINE_MCP`
  on Linux/macOS, `Remove-Item Env:\MTG_ENGINE_MCP` in PowerShell, or
  remove it from the AI client's MCP config block).
- `~/.densa-deck/config.json: mcp_enabled=false` → edit the JSON and
  set `"mcp_enabled": true` (or remove the key entirely).

### "The MCP server requires the 'mcp' SDK"

The `mcp` Python package isn't installed. Install:

```bash
pip install 'densa-deck[mcp]'
# or directly:
pip install 'mcp[cli]'
```

If you're running the bundled desktop binary, the SDK should already
be inside — file a bug if it isn't.

### "Unknown MCP tool name(s): X"

Typo in `--tools`. The error message lists every available name; copy
from there.

### Claude desktop says "command not found: densa-deck"

The Inno installer doesn't add Densa Deck to PATH (per-user install,
no admin elevation). Use `densa-deck mcp config` from a terminal to
get a config block with the absolute path resolved, OR open Settings
→ AI client integration → "Show Claude desktop config" in the desktop
UI and copy from there. Replace the existing config block with the
one produced by either method.

### AI client says "tool not found" mid-conversation

Possibilities:

1. The AI is hallucinating a tool name. Check the `tools/list` output
   the client received at startup — that's the authoritative surface.
2. `--read-only` is on and the AI tried to call a Pro tool. By design.
3. `--tools` whitelist excludes the tool. Check the banner line.

### AI tries to call a Pro tool on a free license

Expected. The tool returns an error like `ProRequired: 'goldfish_simulation'
requires Densa Deck Pro.` The AI client should explain this to the user;
some clients silently retry, in which case look for the error in the
client's logs.

### Pro tier not detected after activation

Make sure the desktop app saved the license — check
`~/.densa-deck/license.json`. Restart the AI client (so it spawns a
fresh subprocess that reads the new tier). The server detects tier at
startup, not per-call, so a license activated mid-session won't
take effect until the next subprocess.

---

## Reference: file paths

| Path | Purpose |
|---|---|
| `~/.densa-deck/config.json` | User config — `mcp_enabled`, `tier`, `auto_check_card_db`, etc. |
| `~/.densa-deck/license.json` | Saved Pro license |
| `~/.densa-deck/cards.db` | Scryfall bulk card data |
| `~/.densa-deck/versions.db` | Saved deck snapshots + version history |
| `~/.densa-deck/combos.db` | Commander Spellbook combo cache |

The server only ever reads these. The Pro tools that write
(`save_deck_version`, `coach_*`) hit `versions.db` and an in-memory
session store; they don't touch `config.json` or `license.json`.
