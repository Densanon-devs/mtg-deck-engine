// Densa Deck — desktop app frontend.
//
// Calls the Python backend via pywebview's `window.pywebview.api` bridge.
// Every backend method returns `{ok: true, data: ...}` or `{ok: false, error: "..."}`,
// so `callApi()` centralises the unwrap + error-toast logic.
//
// Single file, no build step. State is two top-level objects — `state` for
// mutable UI state and `els` for cached DOM lookups. Everything else is
// functions.

// ------------------------------ Setup + state ------------------------------

const state = {
  tier: null,
  currentDeckId: null,
  currentSnapshot: null,
  decks: [],
  history: [],
  // Coach tab state
  coachToken: null,
  coachMessages: [],
  coachSessions: [],
};

const els = {};

function $(id) { return document.getElementById(id); }

function cacheElements() {
  const ids = [
    "tier-badge", "setup-banner", "update-banner",
    // Card DB update banner + what-changed modal
    "card-db-update-banner", "card-db-update-banner-body", "card-db-update-now-btn",
    // Combo cache stale banner (>90d) — one-shot launch prompt
    "combo-stale-banner", "combo-stale-banner-body", "combo-stale-refresh-btn",
    "db-diff-modal", "db-diff-body", "db-diff-close-btn", "db-diff-dismiss-btn",
    // Settings: DB preferences + check now
    "pref-auto-check", "pref-auto-download", "check-db-update-btn",
    // Settings: Commander Spellbook combo refresh
    "combo-status", "combo-refresh-btn", "combo-refresh-status",
    "combo-refresh-progress-wrap", "combo-refresh-progress-fill", "combo-refresh-progress-msg",
    // URL import
    "url-import-input", "url-import-btn", "url-import-status",
    // Update + setup banner bodies
    "update-banner-body", "setup-banner-body",
    // About panel
    "about-version",
    // Coach tab
    "coach-deck-select", "coach-start-btn", "coach-sessions-list",
    "coach-empty", "coach-active", "coach-deck-name", "coach-deck-meta",
    "coach-reset-btn", "coach-close-btn",
    "coach-messages", "coach-form", "coach-input", "coach-send-btn",
    "decklist-input", "deck-name-input", "format-select",
    "analyze-btn", "save-btn", "goldfish-btn", "gauntlet-btn", "rule0-btn",
    "rule0-modal", "rule0-text", "rule0-copy-btn", "rule0-close-btn", "rule0-dismiss-btn",
    "bracket-target-select",
    "analyze-status", "analysis-result", "goldfish-result", "gauntlet-result",
    "refresh-decks-btn", "deck-list",
    "deck-editor-empty", "deck-editor-open",
    "editor-deck-name", "editor-version-number", "editor-saved-at", "editor-card-count",
    "editor-textarea", "editor-notes-input",
    "editor-save-version-btn", "editor-analyze-btn", "editor-history-btn", "editor-delete-btn",
    "editor-duel-btn", "editor-duel", "duel-opponent-select", "duel-sims-select",
    "duel-run-btn", "duel-result",
    "compare-decks-btn", "compare-decks-result",
    "editor-history", "history-body", "diff-panel",
    "tier-status", "license-key-input", "license-activate-btn", "license-status",
    "system-status", "toast",
    // Setup panel
    "ingest-btn", "ingest-status", "ingest-progress-wrap",
    "ingest-progress-fill", "ingest-progress-msg",
    "analyst-model-select", "analyst-pull-btn", "analyst-pull-status",
    "analyst-pull-progress-wrap", "analyst-pull-progress-fill", "analyst-pull-progress-msg",
    // MCP — AI client integration panel
    "mcp-status", "mcp-show-config-btn", "mcp-verify-btn", "mcp-status-text",
    "mcp-config-block", "mcp-config-text", "mcp-copy-config-btn", "mcp-copy-status",
  ];
  ids.forEach(id => { els[id.replace(/-/g, "_")] = $(id); });
}

// ------------------------------ API bridge ------------------------------

async function callApi(method, ...args) {
  // Wait for pywebview to finish injecting its API. On first page load the
  // bridge isn't always ready immediately — polling for up to ~1s is simpler
  // than wiring the pywebviewready event and catches the cold-start race.
  const start = Date.now();
  while (!(window.pywebview && window.pywebview.api && window.pywebview.api[method])) {
    if (Date.now() - start > 3000) {
      throw new Error(`API method '${method}' not available — pywebview bridge failed to load.`);
    }
    await new Promise(r => setTimeout(r, 30));
  }
  const result = await window.pywebview.api[method](...args);
  if (result && typeof result === "object" && "ok" in result) {
    if (!result.ok) throw new Error(result.error || "Unknown API error");
    return result.data !== undefined ? result.data : result;
  }
  return result;
}

function toast(message, kind = "info", durationMs = 4000) {
  const t = els.toast;
  t.textContent = message;
  t.className = "toast " + kind;
  t.classList.remove("hidden");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => t.classList.add("hidden"), durationMs);
}

// When a save snapshot reports `combos_broken`, surface it as a follow-up
// warning toast so the user knows their edit dropped a combo line. Stays on
// screen longer than a normal toast since the names matter.
function notifyCombosBroken(snap) {
  const broken = (snap && snap.combos_broken) || [];
  if (!broken.length) return;
  const labels = broken.slice(0, 3).map(c => c.short_label || c.name || "(combo)");
  const more = broken.length > 3 ? ` (+${broken.length - 3} more)` : "";
  const word = broken.length === 1 ? "combo" : "combos";
  setTimeout(() => {
    toast(`Heads up: this save broke ${broken.length} ${word} — ${labels.join("; ")}${more}`,
          "warn", 8000);
  }, 1200);
}

// ------------------------------ Tabs ------------------------------

function switchView(view) {
  document.querySelectorAll(".tab-btn").forEach(b =>
    b.classList.toggle("active", b.dataset.view === view));
  document.querySelectorAll(".view").forEach(v =>
    v.classList.toggle("active", v.id === `view-${view}`));
  if (view === "decks") refreshDeckList();
  if (view === "settings") refreshSettings();
  if (view === "coach") refreshCoachView();
}

// Exposed for tour.js — lets a tour step switch tabs before highlighting.
// Separate from the default-exported switchView so tour.js doesn't need to
// import the full module graph.
window.__tourSwitchView = switchView;

// ------------------------------ Bootstrap ------------------------------

async function bootstrap() {
  cacheElements();

  // Tab nav
  document.querySelectorAll(".tab-btn").forEach(b =>
    b.addEventListener("click", () => switchView(b.dataset.view)));

  // Analyze tab
  els.analyze_btn.addEventListener("click", () => runAnalyze(
    els.decklist_input.value,
    els.deck_name_input.value || "Unnamed Deck",
    els.format_select.value,
    els.analysis_result,
  ));
  els.save_btn.addEventListener("click", saveFromAnalyzeTab);
  els.goldfish_btn.addEventListener("click", runGoldfish);
  els.gauntlet_btn.addEventListener("click", runGauntlet);
  if (els.rule0_btn) els.rule0_btn.addEventListener("click", openRule0Worksheet);
  if (els.bracket_target_select) {
    els.bracket_target_select.addEventListener("change", () => {
      // Re-run only the bracket-fit panel — leave the rest of the
      // analysis output untouched so picking a target doesn't reset
      // the user's combo modal / explain panel state.
      fillBracketFitForCurrentDeck();
    });
  }
  if (els.rule0_close_btn) els.rule0_close_btn.addEventListener("click", hideRule0);
  if (els.rule0_dismiss_btn) els.rule0_dismiss_btn.addEventListener("click", hideRule0);
  if (els.rule0_copy_btn) {
    els.rule0_copy_btn.addEventListener("click", () => {
      const text = els.rule0_text?.textContent || "";
      if (!text) return;
      const orig = els.rule0_copy_btn.textContent;
      try {
        navigator.clipboard.writeText(text).then(() => {
          els.rule0_copy_btn.textContent = "Copied!";
          setTimeout(() => { els.rule0_copy_btn.textContent = orig; }, 1500);
        }).catch(() => {
          // pywebview's clipboard support varies — fall back to manual select.
          const r = document.createRange();
          r.selectNode(els.rule0_text);
          const sel = window.getSelection();
          sel.removeAllRanges();
          sel.addRange(r);
          els.rule0_copy_btn.textContent = "Selected — Ctrl+C";
          setTimeout(() => { els.rule0_copy_btn.textContent = orig; }, 2000);
        });
      } catch (e) { /* non-fatal */ }
    });
  }
  els.url_import_btn.addEventListener("click", importDeckFromUrl);
  // Ctrl/Cmd+Enter in the decklist textarea triggers Analyze
  els.decklist_input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      els.analyze_btn.click();
    }
  });

  // Dismissible banners — hide on close, remember for this session only
  document.querySelectorAll(".banner-close").forEach(btn => {
    btn.addEventListener("click", () => {
      const target = $(btn.dataset.target);
      if (target) target.classList.add("hidden");
    });
  });

  // External links via pywebview's open_url helper so they launch the user's
  // default browser rather than navigating inside the webview (which would
  // lose app state). Fallback to window.open for dev-browser testing.
  document.querySelectorAll(".external-link").forEach(a => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      const url = a.dataset.url;
      if (window.pywebview && window.pywebview.api && window.pywebview.api.open_external) {
        window.pywebview.api.open_external(url);
      } else {
        window.open(url, "_blank");
      }
    });
  });

  // Global keyboard shortcuts: Ctrl+1..5 switches tabs.
  // MUST stay in sync with the .app-tabs nav order in index.html — adding
  // a tab in HTML without bumping this list silently shifts every later
  // shortcut to the wrong view.
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey) {
      const views = ["analyze", "build", "decks", "coach", "settings"];
      const idx = parseInt(e.key, 10);
      if (idx >= 1 && idx <= views.length) {
        e.preventDefault();
        switchView(views[idx - 1]);
      }
    }
  });

  // My Decks tab
  els.refresh_decks_btn.addEventListener("click", refreshDeckList);
  els.editor_save_version_btn.addEventListener("click", saveEditorAsNewVersion);
  els.editor_analyze_btn.addEventListener("click", () => loadIntoAnalyzeTab());
  els.editor_history_btn.addEventListener("click", toggleHistory);
  els.editor_delete_btn.addEventListener("click", deleteCurrentDeck);
  els.editor_duel_btn.addEventListener("click", toggleDuelPanel);
  els.duel_run_btn.addEventListener("click", runDuel);
  if (els.compare_decks_btn) {
    els.compare_decks_btn.addEventListener("click", runAnalystCompare);
  }

  // Settings tab
  els.license_activate_btn.addEventListener("click", activateLicense);
  els.ingest_btn.addEventListener("click", startIngest);
  els.analyst_pull_btn.addEventListener("click", startAnalystPull);

  // Coach tab
  els.coach_start_btn.addEventListener("click", startCoachSession);
  els.coach_reset_btn.addEventListener("click", resetCoachSession);
  els.coach_close_btn.addEventListener("click", closeCoachSession);
  els.coach_form.addEventListener("submit", submitCoachQuestion);
  // Ctrl+Enter sends too — saves an awkward mouse trip mid-typing
  els.coach_input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      submitCoachQuestion(new Event("submit"));
    }
  });

  // Initial state
  try {
    const tier = await callApi("get_tier");
    state.tier = tier;
    renderTier(tier);
  } catch (e) {
    toast("Failed to load tier info: " + e.message, "error");
  }

  await checkSetupBanner();
  // Auto-update check fires in the background on launch. Fail-silent if
  // the network is down so offline users aren't harassed by error toasts.
  checkForUpdates();
  // Card DB update check also fires on launch — gated on the user's
  // auto_check_card_db preference. Silent when the pref is off; surfaces
  // a dismissible banner (or kicks off a silent re-ingest) when on.
  checkCardDbUpdates();

  // Wire the Card-DB update banner and Settings handlers for v0.1.7 prefs.
  if (els.card_db_update_now_btn) {
    els.card_db_update_now_btn.addEventListener("click", runCardDbUpdateNow);
  }
  if (els.check_db_update_btn) {
    els.check_db_update_btn.addEventListener("click", async () => {
      els.check_db_update_btn.disabled = true;
      try {
        const info = await callApi("check_card_db_update");
        if (info && info.available) {
          showCardDbUpdateBanner(info);
          toast("Update available.", "success");
        } else if (info && info.error) {
          toast("Couldn't check for updates: " + info.error, "error");
        } else {
          toast("You're already up to date.", "info");
        }
      } catch (e) {
        toast("Check failed: " + e.message, "error");
      } finally {
        els.check_db_update_btn.disabled = false;
      }
    });
  }
  if (els.pref_auto_check) {
    els.pref_auto_check.addEventListener("change", onPrefChange);
  }
  if (els.pref_auto_download) {
    els.pref_auto_download.addEventListener("change", onPrefChange);
  }
  if (els.combo_refresh_btn) {
    els.combo_refresh_btn.addEventListener("click", startComboRefresh);
  }
  // MCP — AI client integration panel
  if (els.mcp_show_config_btn) {
    els.mcp_show_config_btn.addEventListener("click", showMcpConfig);
  }
  if (els.mcp_verify_btn) {
    els.mcp_verify_btn.addEventListener("click", verifyMcp);
  }
  if (els.mcp_copy_config_btn) {
    els.mcp_copy_config_btn.addEventListener("click", copyMcpConfig);
  }

  // Delegated handler for "Why? (Pro)" buttons next to unreliable cards
  // — these are inside renderAnalysis output and re-rendered each Analyze
  // run, so a single delegated listener avoids per-render binding.
  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest(".explain-card-btn");
    if (!btn) return;
    ev.preventDefault();
    if (!state.tier?.is_pro) {
      toast("Card explanations are Pro — activate a license on Settings.", "error");
      return;
    }
    const cardName = btn.dataset.card;
    if (!cardName) return;
    const text = els.decklist_input.value.trim();
    if (!text) {
      toast("Re-run Analyze first so we have a deck to inspect.", "error");
      return;
    }
    btn.disabled = true;
    const orig = btn.textContent;
    btn.textContent = "Thinking…";
    const target = $("explain-card-result");
    if (target) {
      target.classList.remove("hidden");
      target.innerHTML = `<p class="panel-hint">Running the analyst on <strong>${escape(cardName)}</strong>…</p>`;
    }
    try {
      const r = await callApi("explain_card_in_deck", text, cardName,
                              els.format_select.value,
                              els.deck_name_input.value || "Unnamed Deck");
      if (target) {
        const verifiedBadge = r.verified
          ? `<span class="confidence">verified ${(r.confidence * 100).toFixed(0)}%</span>`
          : `<span class="confidence" style="color:#e8a33b">unverified</span>`;
        const flagsLine = (r.flags || []).length
          ? `<p class="panel-hint">flags: ${r.flags.map(escape).join("; ")}</p>`
          : "";
        target.innerHTML = `
          <div class="duel-verdict">
            <div class="duel-verdict-headline">${escape(r.card_name)} ${verifiedBadge}</div>
          </div>
          <p>${escape(r.summary).replace(/\n/g, "<br>")}</p>
          ${flagsLine}
        `;
      }
    } catch (e) {
      if (target) target.innerHTML = `<p class="panel-hint" style="color:#ff9999">Explain failed: ${escape(e.message)}</p>`;
    } finally {
      btn.disabled = false;
      btn.textContent = orig;
    }
  });
  if (els.db_diff_close_btn) {
    els.db_diff_close_btn.addEventListener("click", () => hideDbDiffModal());
  }
  if (els.db_diff_dismiss_btn) {
    els.db_diff_dismiss_btn.addEventListener("click", () => hideDbDiffModal());
  }
  // Moxfield workaround modal — close/dismiss + "open Moxfield deck"
  const mxClose = $("moxfield-help-close-btn");
  const mxDismiss = $("moxfield-help-dismiss-btn");
  const mxOpenLink = $("moxfield-help-open-link");
  if (mxClose) mxClose.addEventListener("click", hideMoxfieldWorkaround);
  if (mxDismiss) mxDismiss.addEventListener("click", hideMoxfieldWorkaround);
  if (mxOpenLink) {
    mxOpenLink.addEventListener("click", (ev) => {
      ev.preventDefault();
      const url = mxOpenLink.dataset.url || "";
      if (!url) return;
      try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.open_external) {
          window.pywebview.api.open_external(url);
        } else {
          window.open(url, "_blank");
        }
      } catch (e) { /* non-fatal */ }
    });
  }
  // After dismissing the modal, focus the decklist textarea so the user
  // can paste the exported list immediately. Done via the dismiss button
  // because closing via × may signal "not now."
  if (mxDismiss) {
    mxDismiss.addEventListener("click", () => {
      if (els.decklist_input) {
        try { els.decklist_input.focus(); } catch (e) { /* non-fatal */ }
      }
    });
  }
  // Force a coach-backend re-probe on every launch so an in-place
  // installer update (which dumps us into a fresh process but points at
  // the existing ~/.densa-deck/models/analyst.gguf) picks up the model
  // instead of keeping a stale Mock selection that won't go away until
  // the user clicks Download again. Silent on error — worst case the
  // Coach tab continues with whatever backend was selected on-demand.
  try { await callApi("refresh_coach_backend"); } catch (e) { /* non-fatal */ }

  // First-run tour: wire DOM handlers, then conditionally start the tour
  // if the user hasn't seen it yet. Restart is hooked from the About panel
  // in Settings.
  if (window.Tour) {
    window.Tour.wire();
    // Start the tour AFTER we've rendered the first view so elements exist
    setTimeout(() => window.Tour.maybeStartOnFirstLaunch(), 200);
  }
  const replayBtn = document.getElementById("replay-tour-btn");
  if (replayBtn && window.Tour) {
    replayBtn.addEventListener("click", () => window.Tour.restart());
  }

  // One-shot stale-combo prompt: if the cache is 90+ days old AND the user
  // hasn't already dismissed the banner for the current cohort, surface
  // it. Dismissals are keyed off `last_refresh_at` so the prompt re-fires
  // after the next refresh ages out.
  setTimeout(() => { maybePromptStaleCombos(); }, 400);
  if (els.combo_stale_refresh_btn) {
    els.combo_stale_refresh_btn.addEventListener("click", () => {
      els.combo_stale_banner.classList.add("hidden");
      switchView("settings");
      // Settings tab loads loadComboStatus on view, so the freshness card
      // updates without an extra refresh round-trip.
      startComboRefresh();
    });
  }
}

async function maybePromptStaleCombos() {
  if (!els.combo_stale_banner) return;
  let s;
  try {
    s = await callApi("get_combo_status");
  } catch (e) { return; }
  if (!s || !s.combo_count || !s.last_refresh_at) return;
  let daysAgo;
  try {
    const refreshDate = new Date(s.last_refresh_at);
    daysAgo = (Date.now() - refreshDate.getTime()) / (1000 * 60 * 60 * 24);
  } catch (e) { return; }
  if (daysAgo < 90) return;
  // localStorage key encodes the cohort so dismissing one stale window
  // doesn't suppress the prompt forever — once the user refreshes, the
  // key changes and a future >90d window prompts again.
  const dismissKey = `combo-stale-dismissed:${s.last_refresh_at}`;
  try {
    if (localStorage.getItem(dismissKey)) return;
  } catch (e) { /* localStorage may be disabled — fall through and prompt anyway */ }

  els.combo_stale_banner_body.innerHTML =
    `<strong>Combo data is ${Math.floor(daysAgo)} days old.</strong> ` +
    `Commander Spellbook adds new variants weekly — refresh to keep detection accurate.`;
  els.combo_stale_banner.classList.remove("hidden");
  // The shared .banner-close handler hides the banner; we layer onto it
  // here to also persist the dismissal for this cohort.
  const closeBtn = els.combo_stale_banner.querySelector(".banner-close");
  if (closeBtn && !closeBtn.dataset.staleHandlerWired) {
    closeBtn.dataset.staleHandlerWired = "1";
    closeBtn.addEventListener("click", () => {
      try { localStorage.setItem(dismissKey, "1"); } catch (e) { /* non-fatal */ }
    });
  }
}

async function checkForUpdates() {
  try {
    const r = await callApi("check_for_updates");
    if (r && r.update_available && r.latest) {
      const url = r.download_url || "https://toolkit.densanon.com/mtg-engine.html";
      els.update_banner_body.innerHTML = `
        <strong>Update available:</strong> v${escape(r.latest)} (you have v${escape(r.current)})
        &nbsp;&middot;&nbsp; <a href="#" id="update-link" class="external-link" data-url="${escape(url)}">Download</a>
      `;
      els.update_banner.classList.remove("hidden");
      $("update-link").addEventListener("click", (e) => {
        e.preventDefault();
        if (window.pywebview && window.pywebview.api && window.pywebview.api.open_external) {
          window.pywebview.api.open_external(url);
        } else {
          window.open(url, "_blank");
        }
      });
    }
  } catch (e) {
    // Silent failure — no update check, no error. User may be offline.
  }
}

// ------------------------------ Card DB update (v0.1.7) ------------------------------

// On-launch check for a newer Scryfall bulk. Silent when the user's
// auto-check preference is off, otherwise renders the update banner
// (or kicks off a silent re-ingest when auto-download is on).
async function checkCardDbUpdates() {
  try {
    const prefs = await callApi("get_user_preferences");
    if (!prefs || !prefs.auto_check_card_db) return;
    const info = await callApi("check_card_db_update");
    if (!info || !info.available) return;
    if (prefs.auto_download_card_db) {
      // Background ingest. Poll progress, pop the what-changed modal on done.
      showCardDbUpdateBanner(info, { autoMode: true });
      startCardDbUpdateIngest();
    } else {
      showCardDbUpdateBanner(info);
    }
  } catch (e) {
    // Network / bridge failure — no nag.
  }
}

function showCardDbUpdateBanner(info, opts) {
  const size = info.size_mb ? ` (~${info.size_mb} MB download)` : "";
  const when = info.remote_updated_at
    ? ` &middot; Scryfall build ${escape(info.remote_updated_at.slice(0, 10))}`
    : "";
  const body = (opts && opts.autoMode)
    ? `<strong>Updating card database in background</strong>${size}${when}`
    : `<strong>Card database update available</strong>${size}${when}`;
  els.card_db_update_banner_body.innerHTML = body;
  // Hide the "Update now" button when we're already updating automatically.
  if (els.card_db_update_now_btn) {
    els.card_db_update_now_btn.classList.toggle("hidden", !!(opts && opts.autoMode));
  }
  els.card_db_update_banner.classList.remove("hidden");
}

function hideCardDbUpdateBanner() {
  els.card_db_update_banner.classList.add("hidden");
}

async function runCardDbUpdateNow() {
  // Guard against double-fire when an auto-mode ingest is already in
  // flight — without this, two pollProgress("ingest") loops can be
  // attached, each firing the what-changed modal independently.
  try {
    const p = await callApi("ingest_progress");
    if (p && p.running) {
      toast("Card database update already in progress.", "info");
      return;
    }
  } catch (e) { /* fall through and try anyway */ }
  if (els.card_db_update_now_btn) els.card_db_update_now_btn.disabled = true;
  hideCardDbUpdateBanner();
  startCardDbUpdateIngest();
}

function startCardDbUpdateIngest() {
  // Reuses the existing ingest flow with force=true. Routes completion
  // through the same pollProgress helper but adds a "fetch diff + show
  // modal" step once done.
  els.ingest_progress_wrap.classList.remove("hidden");
  els.ingest_status.textContent = "Updating card database...";
  els.ingest_btn.disabled = true;
  callApi("ingest_start", true)
    .then(() => {
      pollProgress("ingest", async () => {
        els.ingest_btn.disabled = false;
        els.ingest_status.textContent = "";
        if (els.card_db_update_now_btn) els.card_db_update_now_btn.disabled = false;
        checkSetupBanner();
        refreshSettings();
        // Fetch the diff and pop the "what changed" modal. null means
        // the ingest was a first-run (no pre-snapshot) or was already
        // consumed — skip the modal in both cases.
        try {
          const diff = await callApi("get_last_ingest_diff");
          if (diff && (diff.counts && (diff.counts.added || diff.counts.updated || diff.counts.removed))) {
            showDbDiffModal(diff);
          }
        } catch (e) { /* non-fatal */ }
      });
    })
    .catch((e) => {
      els.ingest_btn.disabled = false;
      els.ingest_status.textContent = "";
      if (els.card_db_update_now_btn) els.card_db_update_now_btn.disabled = false;
      toast("Card DB update failed to start: " + e.message, "error");
    });
}

function showDbDiffModal(diff) {
  const section = (label, names, cssClass) => {
    const count = (diff.counts && diff.counts[label]) || names.length;
    if (count === 0) return "";
    const listItems = (names || []).map(n => `<li>${escape(n)}</li>`).join("");
    const truncatedNote = (count > names.length)
      ? `<p class="panel-hint subtle">Showing ${names.length} of ${count} — more truncated for render speed.</p>`
      : "";
    const title = label.charAt(0).toUpperCase() + label.slice(1);
    return `
      <details class="diff-section ${cssClass}" ${count > 0 ? "open" : ""}>
        <summary>${escape(title)} (${count})</summary>
        ${truncatedNote}
        <ul class="diff-list">${listItems}</ul>
      </details>
    `;
  };
  const totalChanged = (diff.counts && (
    (diff.counts.added || 0) + (diff.counts.updated || 0) + (diff.counts.removed || 0)
  )) || 0;
  const header = totalChanged === 0
    ? "<p>No oracle changes were detected in this update.</p>"
    : `<p>Scryfall's oracle set changed in this update. Summary below.</p>`;
  els.db_diff_body.innerHTML = `
    ${header}
    ${section("added", diff.added || [], "added")}
    ${section("updated", diff.updated || [], "updated")}
    ${section("removed", diff.removed || [], "removed")}
  `;
  els.db_diff_modal.classList.remove("hidden");
  els.db_diff_modal.setAttribute("aria-hidden", "false");
}

function hideDbDiffModal() {
  els.db_diff_modal.classList.add("hidden");
  els.db_diff_modal.setAttribute("aria-hidden", "true");
}

async function onPrefChange() {
  // Enforce the UI-side constraint: auto_download is disabled unless
  // auto_check is on. The server also enforces this — we mirror it here
  // so the checkbox feels right to the user before the POST round-trips.
  const autoCheck = !!els.pref_auto_check.checked;
  if (!autoCheck && els.pref_auto_download.checked) {
    els.pref_auto_download.checked = false;
  }
  els.pref_auto_download.disabled = !autoCheck;
  try {
    await callApi("set_user_preferences", {
      auto_check_card_db: autoCheck,
      auto_download_card_db: !!els.pref_auto_download.checked,
    });
  } catch (e) {
    toast("Saving preference failed: " + e.message, "error");
  }
}

async function loadComboStatus() {
  if (!els.combo_status) return;
  try {
    const s = await callApi("get_combo_status");
    if (!s) return;
    const count = s.combo_count || 0;
    if (count === 0) {
      els.combo_status.innerHTML = `<div class="card missing"><strong>Combo cache</strong><br>Empty — click <strong>Refresh combo data</strong> below to populate (~30k combos, 30-60s download).</div>`;
      return;
    }
    // Freshness check — Commander Spellbook adds variants weekly. Prompt
    // a refresh after 30 days; warn at 90+ days.
    let freshnessNote = "";
    let cardClass = "ready";
    if (s.last_refresh_at) {
      try {
        const refreshDate = new Date(s.last_refresh_at);
        const daysAgo = (Date.now() - refreshDate.getTime()) / (1000 * 60 * 60 * 24);
        if (daysAgo >= 90) {
          freshnessNote = ` <span style="color:#ff9999;font-weight:600">(${Math.floor(daysAgo)} days old — refresh recommended)</span>`;
          cardClass = "warning";
        } else if (daysAgo >= 30) {
          freshnessNote = ` <span style="color:#e8a33b">(${Math.floor(daysAgo)} days old)</span>`;
          cardClass = "warning";
        } else {
          freshnessNote = ` <span class="status-text">(${Math.max(1, Math.floor(daysAgo))} day${Math.floor(daysAgo) === 1 ? "" : "s"} ago)</span>`;
        }
      } catch (e) { /* non-fatal — display without freshness note */ }
    }
    const lastDate = s.last_refresh_at
      ? escape(s.last_refresh_at.slice(0, 10))
      : "never";
    els.combo_status.innerHTML = `<div class="card ${cardClass}"><strong>Combo cache</strong><br>${count.toLocaleString()} combos cached &middot; last refresh ${lastDate}${freshnessNote}</div>`;
  } catch (e) {
    // Non-fatal — combo cache check failure shouldn't block Settings.
  }
}

async function startComboRefresh() {
  if (!els.combo_refresh_btn) return;
  els.combo_refresh_btn.disabled = true;
  els.combo_refresh_status.textContent = "Starting...";
  els.combo_refresh_progress_wrap.classList.remove("hidden");
  try {
    await callApi("combo_refresh_start");
    pollProgress("combo_refresh", () => {
      els.combo_refresh_btn.disabled = false;
      els.combo_refresh_status.textContent = "";
      loadComboStatus();
    });
  } catch (e) {
    els.combo_refresh_btn.disabled = false;
    els.combo_refresh_status.textContent = "";
    toast("Combo refresh failed to start: " + e.message, "error");
  }
}

// ------------------------------ MCP (AI client integration) ------------------------------

async function loadMcpStatus() {
  if (!els.mcp_status) return;
  try {
    const s = await callApi("get_mcp_status");
    if (!s) return;
    if (!s.enabled) {
      els.mcp_status.innerHTML = `<div class="card warning"><strong>MCP server is disabled</strong><br>${escape(s.reason || "Operator setting blocked it.")}</div>`;
      // Hide the action buttons when disabled — running them won't help.
      els.mcp_show_config_btn.disabled = true;
      els.mcp_verify_btn.disabled = true;
      return;
    }
    if (!s.sdk_present) {
      els.mcp_status.innerHTML = `<div class="card warning"><strong>MCP SDK not bundled</strong><br>This install of Densa Deck doesn't include the MCP runtime. The bundled installer / portable ZIP includes it; pip-installs need <code>pip install 'densa-deck[mcp]'</code>.</div>`;
      els.mcp_show_config_btn.disabled = true;
      els.mcp_verify_btn.disabled = true;
      return;
    }
    const tierLabel = s.tier === "pro" ? "Pro tier — 28 tools available" : "Free tier — 17 tools (Pro tools unlock with a license)";
    els.mcp_status.innerHTML = `<div class="card ready"><strong>MCP server ready</strong> &middot; <span class="status-text">${escape(tierLabel)}</span></div>`;
    els.mcp_show_config_btn.disabled = false;
    els.mcp_verify_btn.disabled = false;
  } catch (e) {
    els.mcp_status.innerHTML = `<div class="card missing"><strong>MCP status unavailable</strong><br>${escape(e.message)}</div>`;
  }
}

async function showMcpConfig() {
  els.mcp_status_text.textContent = "";
  try {
    const r = await callApi("get_mcp_config_block");
    if (!r || !r.config_text) {
      toast("Couldn't generate config block.", "error");
      return;
    }
    els.mcp_config_text.value = r.config_text;
    els.mcp_config_block.classList.remove("hidden");
  } catch (e) {
    toast("Failed to generate config: " + e.message, "error");
  }
}

async function copyMcpConfig() {
  const text = els.mcp_config_text.value;
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    els.mcp_copy_status.textContent = "Copied to clipboard.";
    setTimeout(() => { els.mcp_copy_status.textContent = ""; }, 3000);
  } catch (e) {
    // Some environments deny clipboard access; fall back to selecting
    // the text so the user can hit Ctrl+C themselves.
    els.mcp_config_text.focus();
    els.mcp_config_text.select();
    els.mcp_copy_status.textContent = "Press Ctrl+C to copy.";
    setTimeout(() => { els.mcp_copy_status.textContent = ""; }, 5000);
  }
}

async function verifyMcp() {
  els.mcp_status_text.textContent = "Verifying...";
  els.mcp_verify_btn.disabled = true;
  try {
    const r = await callApi("selftest_mcp");
    if (r && r.success) {
      els.mcp_status_text.innerHTML =
        `<span style="color:var(--color-accent-green, #34d399);font-weight:600">&#10003;</span> ` +
        `MCP server starts cleanly &middot; ${r.tool_count} tools registered (${escape(r.tier)} tier).`;
    } else {
      const kind = (r && r.failure_kind) || "unknown";
      const msg = (r && r.failure_msg) || "Unknown failure.";
      els.mcp_status_text.innerHTML =
        `<span style="color:#ff9999;font-weight:600">&#10007;</span> ` +
        `${escape(kind)}: ${escape(msg)}`;
    }
  } catch (e) {
    els.mcp_status_text.innerHTML =
      `<span style="color:#ff9999;font-weight:600">&#10007;</span> Failed: ${escape(e.message)}`;
  } finally {
    els.mcp_verify_btn.disabled = false;
  }
}

async function loadUserPrefsIntoSettings() {
  // Used by refreshSettings so the checkboxes reflect the persisted
  // state every time the Settings tab opens.
  if (!els.pref_auto_check) return;
  try {
    const prefs = await callApi("get_user_preferences");
    els.pref_auto_check.checked = !!prefs.auto_check_card_db;
    els.pref_auto_download.checked = !!prefs.auto_download_card_db;
    els.pref_auto_download.disabled = !prefs.auto_check_card_db;
  } catch (e) {
    // Non-fatal — leave the checkboxes in whatever state they happened to be.
  }
}

function renderTier(tier) {
  els.tier_badge.textContent = tier.is_pro ? "Pro" : "Free";
  els.tier_badge.classList.toggle("pro", tier.is_pro);
  // Hide Pro-only buttons when free. Save, goldfish, gauntlet are all
  // Pro-gated; analyze stays free.
  els.save_btn.disabled = !tier.is_pro;
  els.goldfish_btn.disabled = !tier.is_pro;
  els.gauntlet_btn.disabled = !tier.is_pro;
  if (!tier.is_pro) {
    const hint = "Pro only — activate a license on the Settings tab";
    els.save_btn.title = hint;
    els.goldfish_btn.title = hint;
    els.gauntlet_btn.title = hint;
  }
}

async function checkSetupBanner() {
  try {
    const status = await callApi("get_system_status");
    if (!status.card_database.ready) {
      els.setup_banner_body.innerHTML =
        "Setup needed: the card database isn't installed yet. " +
        "Go to <strong>Settings</strong> → <strong>Install card database</strong> to pull ~250 MB of Scryfall data (one-time).";
      els.setup_banner.classList.remove("hidden");
    } else {
      els.setup_banner.classList.add("hidden");
    }
  } catch (e) {
    // Non-fatal — fall back silently
  }
}

// ------------------------------ Analyze view ------------------------------

async function runAnalyze(decklistText, name, format_, renderTarget) {
  els.analyze_status.textContent = "Analyzing...";
  try {
    const result = await callApi("analyze_deck", decklistText, format_, name);
    renderAnalysis(result, renderTarget);
    els.analyze_status.textContent = "";
  } catch (e) {
    els.analyze_status.textContent = "";
    toast("Analyze failed: " + e.message, "error");
  }
}

function renderAnalysis(r, target) {
  target.classList.remove("hidden");
  const scoreBars = (scores) => Object.entries(scores)
    .map(([k, v]) => {
      const label = k.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
      const pct = Math.max(0, Math.min(100, v));
      return `<div class="score-row">
        <span class="axis">${escape(label)}</span>
        <span class="bar"><span class="bar-fill" style="width:${pct}%"></span></span>
        <span class="value">${pct.toFixed(0)}</span>
      </div>`;
    }).join("");

  const curveBars = (() => {
    const maxCount = Math.max(...Object.values(r.mana_curve), 1);
    let out = "";
    for (let mv = 0; mv <= 7; mv++) {
      const count = r.mana_curve[mv] || 0;
      const pct = (count / maxCount) * 100;
      const label = mv < 7 ? mv : "7+";
      out += `<div class="curve-bar">
        <span class="mv-label">${label}</span>
        <span class="track"><span class="fill" style="width:${pct}%">${count || ""}</span></span>
      </div>`;
    }
    return out;
  })();

  target.innerHTML = `
    <div class="result-grid">
      <div class="stat-card"><div class="label">Total cards</div><div class="value">${r.total_cards}</div></div>
      <div class="stat-card"><div class="label">Lands</div><div class="value">${r.land_count}</div></div>
      <div class="stat-card"><div class="label">Avg mana value</div><div class="value">${r.average_cmc.toFixed(2)}</div></div>
      <div class="stat-card"><div class="label">Power level ${helpIcon("power_level", {title: "Power level (1-10)"})}</div><div class="value">${r.power.overall.toFixed(1)}/10</div><div class="sub">${escape(r.power.tier)}</div></div>
      <div class="stat-card"><div class="label">Archetype ${helpIcon("archetype", {title: "Archetype detection"})}</div><div class="value" style="font-size:1.1rem">${escape(r.archetype)}</div></div>
      <div class="stat-card"><div class="label">Mana base ${helpIcon("mana_base", {title: "Mana base grade"})}</div><div class="value">${escape(r.advanced.mana_base_grade || "-")}</div></div>
    </div>

    <div class="panel-row">
      <div class="panel result-section">
        <h3>Mana curve ${helpIcon("mana_curve", {title: "Mana curve"})}</h3>
        ${curveBars}
      </div>
      <div class="panel result-section">
        <h3>Category scores ${helpIcon("category_scores", {title: "Category scores"})}</h3>
        ${scoreBars(r.scores) || '<span class="status-text">(no scores)</span>'}
      </div>
    </div>

    ${r.power.reasons_up.length || r.power.reasons_down.length ? `
      <div class="panel result-section">
        <h3>Power-level signals ${helpIcon("power_signals", {title: "Power-level signals"})}</h3>
        <ul class="issue-list">
          ${r.power.reasons_up.map(x => `<li class="severity-info">+ ${escape(x)}</li>`).join("")}
          ${r.power.reasons_down.map(x => `<li class="severity-warning">- ${escape(x)}</li>`).join("")}
        </ul>
      </div>` : ""}

    ${r.issues.length ? `
      <div class="panel result-section">
        <h3>Issues</h3>
        <ul class="issue-list">
          ${r.issues.map(i =>
            `<li class="severity-${escape(i.severity)}">${escape(i.message)}${i.card ? " ("+escape(i.card)+")" : ""}</li>`
          ).join("")}
        </ul>
      </div>` : ""}

    ${r.recommendations.length ? `
      <div class="panel result-section">
        <h3>Recommendations</h3>
        <ul class="rec-list">
          ${r.recommendations.map(x => `<li class="severity-info">${escape(x)}</li>`).join("")}
        </ul>
      </div>` : ""}

    ${r.castability.unreliable_cards.length ? `
      <div class="panel result-section">
        <h3>Castability warnings ${helpIcon("castability", {title: "Castability"})}</h3>
        <table class="castability-table">
          <thead><tr><th>Card</th><th>Cost</th><th>On-curve %</th><th>Bottleneck</th><th></th></tr></thead>
          <tbody>
            ${r.castability.unreliable_cards.map(c =>
              `<tr>
                <td>${escape(c.name)}</td>
                <td>${escape(c.mana_cost)}</td>
                <td>${(c.on_curve_probability * 100).toFixed(0)}%</td>
                <td>${escape(c.bottleneck_color || "-")}</td>
                <td><button class="btn btn-outline btn-slim explain-card-btn" data-card="${escape(c.name)}" title="Run the analyst on this single card (Pro)">Why? (Pro)</button></td>
              </tr>`
            ).join("")}
          </tbody>
        </table>
        <div id="explain-card-result" class="explain-card-result hidden"></div>
      </div>` : ""}

    ${r.staples.missing.length ? `
      <div class="panel result-section">
        <h3>Missing staples (${Math.round((r.staples.staple_coverage || 0) * 100)}% coverage)</h3>
        <table class="staples-table">
          <thead><tr><th>Card</th><th>Priority</th><th>Reason</th></tr></thead>
          <tbody>
            ${r.staples.missing.map(s =>
              `<tr>
                <td>${escape(s.name)}</td>
                <td><span class="badge-${escape(s.priority)}">${escape(s.priority)}</span></td>
                <td>${escape(s.reason)}</td>
              </tr>`
            ).join("")}
          </tbody>
        </table>
      </div>` : ""}

    ${r.unresolved_cards.length ? `
      <div class="panel result-section">
        <h3>Unresolved cards (${r.unresolved_cards.length})</h3>
        <p class="panel-hint">These names couldn't be found in the card database — check for typos or missing sets. Click a suggestion to fix the decklist automatically.</p>
        <div id="unresolved-list">
          ${r.unresolved_cards.slice(0, 20).map(x =>
            `<div class="unresolved-row" data-bad="${escape(x)}">
              <span class="bad-name">${escape(x)}</span>
              <span class="suggest-arrow">&rarr;</span>
              <span class="suggestions">(checking...)</span>
            </div>`
          ).join("")}
        </div>
      </div>` : ""}

    <div id="combos-section" class="panel result-section hidden">
      <h3>Detected combos <span id="combos-count" class="status-text"></span></h3>
      <div id="combos-list"></div>
    </div>

    <div id="near-combos-section" class="panel result-section hidden">
      <h3>Combos you're 1 card away from <span id="near-combos-count" class="status-text"></span></h3>
      <p class="panel-hint">High-leverage adds: each missing card here completes a real combo line.</p>
      <div id="near-combos-list"></div>
    </div>

    <div id="bracket-fit-section" class="panel result-section hidden">
      <h3>Bracket fit <span id="bracket-fit-headline" class="status-text"></span></h3>
      <div id="bracket-fit-body"></div>
    </div>
  `;

  // Fire the fuzzy-match lookup async so it doesn't block the main render.
  // Shows "(checking...)" until results arrive, then replaces with chips
  // the user can click to fix their decklist.
  if (r.unresolved_cards.length) {
    fillUnresolvedSuggestions(r.unresolved_cards.slice(0, 20));
  }

  // Combo detection runs in the background — surfaces a "Detected combos"
  // section in the analysis output if the cache is populated AND any
  // combo lines match the deck. Stays hidden when the cache is empty so
  // the user isn't nagged before they've refreshed combo data.
  fillCombosForCurrentDeck();
  // Same for the "1 card away" surface — silent when nothing applies.
  fillNearMissCombosForCurrentDeck();
  // Bracket fit only fires when the user has picked a target; otherwise
  // hidden so the analysis result stays compact.
  fillBracketFitForCurrentDeck();
}

async function fillNearMissCombosForCurrentDeck() {
  const text = els.decklist_input.value.trim();
  if (!text) return;
  const section = $("near-combos-section");
  const list = $("near-combos-list");
  const count = $("near-combos-count");
  if (!section || !list || !count) return;
  try {
    const r = await callApi(
      "detect_near_miss_combos_for_deck",
      text, els.format_select.value,
      els.deck_name_input.value || "Unnamed Deck",
      1, 25,
    );
    if (!r || !r.match_count) return;
    count.textContent = `(${r.match_count})`;
    list.innerHTML = (r.near_combos || []).map(c => `
      <div class="combo-row">
        <div class="combo-label">${escape(c.short_label)}</div>
        <div class="combo-meta">
          <span class="status-text">missing: <strong>${escape(c.missing_cards.join(" + "))}</strong></span>
          ${c.popularity ? `<span class="status-text">${c.popularity.toLocaleString()} decks on Spellbook</span>` : ""}
          <a href="#" class="external-link combo-link" data-url="${escape(c.spellbook_url)}">Open on Spellbook &rarr;</a>
        </div>
      </div>
    `).join("");
    list.querySelectorAll(".combo-link").forEach(a => {
      a.addEventListener("click", (ev) => {
        ev.preventDefault();
        const url = a.dataset.url;
        try {
          if (window.pywebview?.api?.open_external) window.pywebview.api.open_external(url);
          else window.open(url, "_blank");
        } catch (e) { /* non-fatal */ }
      });
    });
    section.classList.remove("hidden");
  } catch (e) {
    // Silent on cache-empty / ingest-required — same as detect_combos path.
  }
}

async function fillBracketFitForCurrentDeck() {
  const target = els.bracket_target_select?.value || "";
  const section = $("bracket-fit-section");
  const headline = $("bracket-fit-headline");
  const body = $("bracket-fit-body");
  if (!section || !target) {
    if (section) section.classList.add("hidden");
    return;
  }
  const text = els.decklist_input.value.trim();
  if (!text) return;
  try {
    const r = await callApi(
      "assess_bracket_fit",
      text, target,
      els.format_select.value,
      els.deck_name_input.value || "Unnamed Deck",
    );
    const verdictColor = {
      "fits": "var(--color-accent-green, #34d399)",
      "over-pitches": "var(--color-warning, #ff6b6b)",
      "under-delivers": "#e8a33b",
    }[r.verdict] || "var(--color-text)";
    headline.innerHTML = `<span style="color:${verdictColor};font-weight:600">${escape(r.verdict)}</span> &middot; detected ${escape(r.detected_label)}, target ${escape(r.target_label)}`;
    const overList = (r.over_signals || []).map(s => `<li class="severity-warning">${escape(s)}</li>`).join("");
    const underList = (r.under_signals || []).map(s => `<li class="severity-warning">${escape(s)}</li>`).join("");
    const recList = (r.recommendations || []).map(s => `<li class="severity-info">${escape(s)}</li>`).join("");
    body.innerHTML = `
      <p>${escape(r.headline)}</p>
      ${overList ? `<h4>Where you're over the cap</h4><ul class="rec-list">${overList}</ul>` : ""}
      ${underList ? `<h4>Where you're under the floor</h4><ul class="rec-list">${underList}</ul>` : ""}
      ${recList ? `<h4>Punch list</h4><ul class="rec-list">${recList}</ul>` : ""}
    `;
    section.classList.remove("hidden");
  } catch (e) {
    section.classList.add("hidden");
  }
}

async function fillCombosForCurrentDeck() {
  const text = els.decklist_input.value.trim();
  if (!text) return;
  const section = $("combos-section");
  const list = $("combos-list");
  const count = $("combos-count");
  if (!section || !list || !count) return;
  try {
    const r = await callApi(
      "detect_combos_for_deck",
      text, els.format_select.value,
      els.deck_name_input.value || "Unnamed Deck",
      25,
    );
    if (!r || r.match_count === 0) return;
    count.textContent = `(${r.match_count})`;
    list.innerHTML = (r.combos || []).map(c => `
      <div class="combo-row">
        <div class="combo-label">${escape(c.short_label)}</div>
        <div class="combo-meta">
          ${c.bracket_tag ? `<span class="badge-${escape(c.bracket_tag.toLowerCase())}" style="padding:1px 6px;border-radius:8px;font-size:0.72rem">tier ${escape(c.bracket_tag)}</span>` : ""}
          ${c.popularity ? `<span class="status-text">${c.popularity.toLocaleString()} decks on Spellbook</span>` : ""}
          <a href="#" class="external-link combo-link" data-url="${escape(c.spellbook_url)}">Open on Spellbook &rarr;</a>
        </div>
        ${c.description ? `<div class="combo-desc">${escape(c.description.split("\n")[0]).slice(0, 240)}</div>` : ""}
      </div>
    `).join("");
    // Wire the combo external links — same pattern as the existing
    // .external-link delegation: they're added after bootstrap so we
    // bind them inline on insertion.
    list.querySelectorAll(".combo-link").forEach(a => {
      a.addEventListener("click", (ev) => {
        ev.preventDefault();
        const url = a.dataset.url;
        try {
          if (window.pywebview?.api?.open_external) {
            window.pywebview.api.open_external(url);
          } else {
            window.open(url, "_blank");
          }
        } catch (e) { /* non-fatal */ }
      });
    });
    section.classList.remove("hidden");
  } catch (e) {
    // ComboCacheEmpty / IngestRequired fall through here as Errors —
    // we deliberately do NOT toast because both are user-actionable
    // states (refresh combo data / ingest cards). The hidden section
    // stays hidden when the cache isn't ready.
  }
}

async function fillUnresolvedSuggestions(badNames) {
  try {
    const data = await callApi("resolve_suggestions", badNames);
    document.querySelectorAll("#unresolved-list .unresolved-row").forEach(row => {
      const bad = row.dataset.bad;
      const matches = data[bad] || [];
      const slot = row.querySelector(".suggestions");
      if (!matches.length) {
        slot.innerHTML = '<span class="status-text">(no close matches)</span>';
        return;
      }
      slot.innerHTML = matches.map(m =>
        `<span class="suggest-chip" data-replace="${escape(m)}">${escape(m)}</span>`
      ).join(" ");
      // Wire click-to-fix: swap the bad name in the decklist textarea with the pick
      slot.querySelectorAll(".suggest-chip").forEach(chip => {
        chip.addEventListener("click", () => {
          const replacement = chip.dataset.replace;
          // Regex-escape the bad name so special chars can't break the pattern.
          const escaped = bad.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
          // Unicode-safe word boundary: JS `\b` is ASCII-only, so it would
          // false-negative on card names like "Æther Vial" or "Lim-Dûl's Vault".
          // Negative-lookaround against letters/digits works the same way
          // but respects the Unicode letter class with the /u flag.
          const re = new RegExp(
            "(?<![\\p{L}\\p{N}])" + escaped + "(?![\\p{L}\\p{N}])",
            "gu",
          );
          const before = els.decklist_input.value;
          const after = before.replace(re, replacement);
          if (after === before) {
            toast(`Couldn't find "${bad}" as a whole word to replace — edit manually.`, "error");
            return;
          }
          els.decklist_input.value = after;
          toast(`Replaced "${bad}" with "${replacement}" — re-run Analyze.`, "success");
        });
      });
    });
  } catch (e) {
    // Non-fatal — leave the "(checking...)" placeholders as-is
  }
}

async function saveFromAnalyzeTab() {
  const text = els.decklist_input.value.trim();
  const name = els.deck_name_input.value.trim() || "Unnamed Deck";
  if (!text) { toast("Paste a decklist first.", "error"); return; }
  // Deck ID defaults to name with spaces -> dashes; user can rename via CLI if they care
  const deckId = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "deck";
  try {
    const snap = await callApi("save_deck_version",
      deckId, name, text, els.format_select.value, "initial save");
    toast(`Saved "${name}" as v${snap.version_number}.`, "success");
    notifyCombosBroken(snap);
  } catch (e) {
    toast("Save failed: " + e.message, "error");
  }
}

// ------------------------------ My Decks view ------------------------------

async function refreshDeckList() {
  try {
    const decks = await callApi("list_saved_decks");
    state.decks = decks;
    els.deck_list.innerHTML = "";
    if (!decks.length) {
      els.deck_list.innerHTML = '<li style="cursor:default"><span class="deck-meta">(no saved decks yet)</span></li>';
      return;
    }
    decks.forEach(d => {
      const li = document.createElement("li");
      const active = state.currentDeckId === d.deck_id ? "active" : "";
      li.className = active;
      // Each row carries a quick "Load in Analyze" action that skips the
      // Open-Deck-Editor step for users who just want to re-analyze a
      // saved list. Clicking anywhere ELSE on the row still opens the
      // editor like before.
      li.innerHTML = `
        <div class="deck-row-main">
          <span class="deck-name">${escape(d.name)}</span>
          <span class="deck-meta">${d.versions} version${d.versions === 1 ? "" : "s"} &middot; ${escape((d.updated_at || "").slice(0, 10))}</span>
        </div>
        <button class="deck-row-action" title="Load this deck into the Analyze tab" data-deck-id="${escape(d.deck_id)}">Load &rarr;</button>
      `;
      // Open in editor (existing behavior) when the main body is clicked.
      li.querySelector(".deck-row-main").addEventListener("click", () => openDeck(d.deck_id));
      // Sidebar shortcut: one-click load into Analyze tab without opening
      // the editor. Stops propagation so the row-click handler doesn't
      // also fire.
      li.querySelector(".deck-row-action").addEventListener("click", async (ev) => {
        ev.stopPropagation();
        await loadIntoAnalyzeTab(d.deck_id);
      });
      els.deck_list.appendChild(li);
    });
  } catch (e) {
    toast("Failed to load decks: " + e.message, "error");
  }
}

async function loadIntoAnalyzeTab(deckId) {
  // If no deckId, fall back to whatever is open in the editor — this
  // branch is what the "Load in Analyze tab" button inside the editor
  // uses. When called from the sidebar shortcut, deckId is provided
  // and we fetch fresh so the Analyze tab gets the latest saved version
  // even if the user has unsaved edits in the editor textarea.
  let snap;
  let text;
  if (deckId) {
    try {
      snap = await callApi("get_deck_latest", deckId);
      text = snap.decklist_text || "";
    } catch (e) {
      toast("Failed to load deck: " + e.message, "error");
      return;
    }
  } else {
    snap = state.currentSnapshot;
    text = els.editor_textarea.value;
    if (!snap || !text) {
      toast("Open a saved deck first.", "error");
      return;
    }
  }
  switchView("analyze");
  els.decklist_input.value = text;
  els.deck_name_input.value = (snap && (snap.name || snap.deck_id)) || "";
  // Set the format dropdown when the snapshot recorded one — otherwise
  // leave the user's current selection so we don't clobber their intent.
  if (snap && snap.format) {
    const fmt = String(snap.format).toLowerCase();
    const opt = Array.from(els.format_select.options).find(o => o.value.toLowerCase() === fmt);
    if (opt) els.format_select.value = opt.value;
  }
  // Visible confirmation that the load worked — otherwise the tab-switch
  // is the only feedback and users can wonder if anything happened.
  toast(`Loaded "${(snap && (snap.name || snap.deck_id)) || "deck"}" into Analyze tab.`, "success");
}

async function openDeck(deckId) {
  try {
    const snap = await callApi("get_deck_latest", deckId);
    state.currentDeckId = deckId;
    state.currentSnapshot = snap;
    els.deck_editor_empty.classList.add("hidden");
    els.deck_editor_open.classList.remove("hidden");
    els.editor_deck_name.textContent = deckId;
    els.editor_version_number.textContent = `v${snap.version_number}`;
    els.editor_saved_at.textContent = (snap.saved_at || "").slice(0, 19).replace("T", " ");
    const cardCount = Object.values(snap.decklist).reduce((a, b) => a + b, 0);
    els.editor_card_count.textContent = `${cardCount} cards`;
    els.editor_textarea.value = snap.decklist_text;
    els.editor_notes_input.value = "";
    els.editor_history.classList.add("hidden");
    await refreshDeckList(); // re-highlight the active deck
  } catch (e) {
    toast("Failed to load deck: " + e.message, "error");
  }
}

async function saveEditorAsNewVersion() {
  if (!state.currentDeckId) return;
  const text = els.editor_textarea.value.trim();
  const notes = els.editor_notes_input.value.trim();
  if (!text) { toast("Deck is empty.", "error"); return; }
  try {
    const snap = await callApi("save_deck_version",
      state.currentDeckId, state.currentSnapshot.deck_id,
      text, state.currentSnapshot.format || "commander", notes);
    toast(`Saved as v${snap.version_number}.`, "success");
    notifyCombosBroken(snap);
    await openDeck(state.currentDeckId);
  } catch (e) {
    toast("Save failed: " + e.message, "error");
  }
}

async function toggleHistory() {
  if (!state.currentDeckId) return;
  if (!els.editor_history.classList.contains("hidden")) {
    els.editor_history.classList.add("hidden");
    return;
  }
  try {
    const versions = await callApi("get_deck_history", state.currentDeckId);
    state.history = versions;
    els.history_body.innerHTML = "";
    versions.forEach((v, idx) => {
      const tr = document.createElement("tr");
      const notes = v.notes ? escape(v.notes) : "<span class='status-text'>(none)</span>";
      // Offer a "Diff vs previous" button for every version except the oldest
      const diffButton = idx < versions.length - 1
        ? `<button class="btn btn-outline" style="padding:4px 10px;font-size:0.82rem" data-a="${versions[idx + 1].version_number}" data-b="${v.version_number}">Diff vs v${versions[idx + 1].version_number}</button>`
        : "";
      tr.innerHTML = `
        <td>v${v.version_number}</td>
        <td>${(v.saved_at || "").slice(0, 19).replace("T", " ")}</td>
        <td>${v.card_count}</td>
        <td>${notes}</td>
        <td>${diffButton}</td>
      `;
      els.history_body.appendChild(tr);
    });
    els.history_body.querySelectorAll("button").forEach(btn =>
      btn.addEventListener("click", () => showDiff(+btn.dataset.a, +btn.dataset.b)));
    els.editor_history.classList.remove("hidden");
    els.diff_panel.classList.add("hidden");
  } catch (e) {
    toast("Failed to load history: " + e.message, "error");
  }
}

async function showDiff(vA, vB) {
  try {
    const d = await callApi("diff_deck_versions", state.currentDeckId, vA, vB);
    const addedList = Object.entries(d.added).map(([n, q]) =>
      `<li class="diff-add">+${q} ${escape(n)}</li>`).join("") || "<li class='status-text'>(none)</li>";
    const removedList = Object.entries(d.removed).map(([n, q]) =>
      `<li class="diff-remove">-${q} ${escape(n)}</li>`).join("") || "<li class='status-text'>(none)</li>";
    const scoreList = Object.entries(d.score_deltas).map(([n, v]) => {
      const sign = v >= 0 ? "+" : "";
      const cls = v >= 0 ? "diff-add" : "diff-remove";
      return `<li class="${cls}">${escape(n)} ${sign}${v.toFixed(1)}</li>`;
    }).join("") || "<li class='status-text'>(no changes)</li>";

    // Combo gained / lost — only render when at least one is non-empty.
    // Each row links to the upstream Spellbook page via open_external.
    const comboGained = (d.combo_gained || []);
    const comboLost = (d.combo_lost || []);
    const comboSection = (comboGained.length || comboLost.length) ? `
      <div class="diff-col" style="grid-column:1/-1">
        <h4>Combos gained / lost</h4>
        ${comboGained.length ? `
          <strong class="diff-add">Newly complete (${comboGained.length}):</strong>
          <ul>${comboGained.map(c =>
            `<li class="diff-add">${escape(c.short_label)} <a href="#" class="external-link" data-url="${escape(c.spellbook_url)}">[?]</a></li>`,
          ).join("")}</ul>` : ""}
        ${comboLost.length ? `
          <strong class="diff-remove">Now broken (${comboLost.length}):</strong>
          <ul>${comboLost.map(c =>
            `<li class="diff-remove">${escape(c.short_label)} <a href="#" class="external-link" data-url="${escape(c.spellbook_url)}">[?]</a></li>`,
          ).join("")}</ul>` : ""}
      </div>
    ` : "";

    els.diff_panel.innerHTML = `
      <h4>Diff v${vA} → v${vB}</h4>
      <div class="diff-col"><h4>Added (${d.total_added})</h4><ul>${addedList}</ul></div>
      <div class="diff-col"><h4>Removed (${d.total_removed})</h4><ul>${removedList}</ul></div>
      <div class="diff-col"><h4>Score deltas</h4><ul>${scoreList}</ul></div>
      ${comboSection}
    `;
    els.diff_panel.classList.remove("hidden");
    // Wire combo external links inside the diff panel — they're inserted
    // dynamically so the bootstrap-time .external-link delegation misses them.
    els.diff_panel.querySelectorAll(".external-link").forEach(a => {
      a.addEventListener("click", (ev) => {
        ev.preventDefault();
        const url = a.dataset.url;
        if (!url) return;
        try {
          if (window.pywebview?.api?.open_external) window.pywebview.api.open_external(url);
          else window.open(url, "_blank");
        } catch (err) { /* non-fatal */ }
      });
    });
  } catch (e) {
    toast("Diff failed: " + e.message, "error");
  }
}

async function deleteCurrentDeck() {
  if (!state.currentDeckId) return;
  const confirmed = confirm(`Delete "${state.currentSnapshot.deck_id}" and ALL its versions? This can't be undone.`);
  if (!confirmed) return;
  try {
    await callApi("delete_deck", state.currentDeckId);
    toast("Deck deleted.", "success");
    state.currentDeckId = null;
    state.currentSnapshot = null;
    els.deck_editor_open.classList.add("hidden");
    els.deck_editor_empty.classList.remove("hidden");
    await refreshDeckList();
  } catch (e) {
    toast("Delete failed: " + e.message, "error");
  }
}

function toggleDuelPanel() {
  if (!state.currentDeckId) return;
  if (!els.editor_duel.classList.contains("hidden")) {
    els.editor_duel.classList.add("hidden");
    return;
  }
  // Populate the opponent picker with every OTHER saved deck. Hide the
  // panel if there aren't any, and tell the user why.
  const sel = els.duel_opponent_select;
  sel.innerHTML = "";
  const opponents = (state.decks || []).filter(d => d.deck_id !== state.currentDeckId);
  if (!opponents.length) {
    sel.innerHTML = '<option value="">(save at least one more deck to duel)</option>';
    els.duel_run_btn.disabled = true;
  } else {
    opponents.forEach(d => {
      const opt = document.createElement("option");
      opt.value = d.deck_id;
      opt.textContent = `${d.name} (${d.versions} v${d.versions === 1 ? "" : "s"})`;
      sel.appendChild(opt);
    });
    els.duel_run_btn.disabled = false;
  }
  els.duel_result.classList.add("hidden");
  els.editor_duel.classList.remove("hidden");
  // Close the history panel if it's open — only one sub-tab at a time to
  // keep the editor column readable.
  els.editor_history.classList.add("hidden");
}

async function runAnalystCompare() {
  if (!state.currentDeckId) return;
  if (!state.tier?.is_pro) {
    toast("Analyst compare is Pro — activate a license on Settings.", "error");
    return;
  }
  const oppId = els.duel_opponent_select.value;
  if (!oppId) {
    toast("Pick an opponent deck first.", "error");
    return;
  }
  els.compare_decks_btn.disabled = true;
  const orig = els.compare_decks_btn.textContent;
  els.compare_decks_btn.textContent = "Comparing…";
  els.compare_decks_result.classList.remove("hidden");
  els.compare_decks_result.innerHTML = `<p class="panel-hint">Running the analyst — usually 5-15s on the local model…</p>`;
  try {
    const r = await callApi("compare_decks_analyst", state.currentDeckId, oppId);
    const sign = r.power_gap >= 0 ? "+" : "";
    const verifiedBadge = r.verified
      ? `<span class="confidence">verified ${(r.confidence * 100).toFixed(0)}%</span>`
      : `<span class="confidence" style="color:#e8a33b">unverified</span>`;
    const deltaRows = Object.entries(r.role_deltas || {})
      .map(([k, v]) => {
        const s = v >= 0 ? "+" : "";
        const color = v === 0 ? "var(--color-text-muted)"
          : v > 0 ? "var(--color-accent-green, #34d399)" : "var(--color-primary)";
        return `<tr><td>${escape(k)}</td><td style="text-align:right;color:${color};font-weight:600">${s}${v}</td></tr>`;
      })
      .join("");
    const addedSample = (r.added_cards || []).slice(0, 6).map(escape).join(", ") || "(none)";
    const removedSample = (r.removed_cards || []).slice(0, 6).map(escape).join(", ") || "(none)";
    // Combo gained / lost — only render the section when at least one
    // bucket is non-empty. Each row carries an external-link affordance
    // pointing at the upstream Spellbook page (handled below).
    const cmpGained = (r.combo_gained || []);
    const cmpLost = (r.combo_lost || []);
    const cmpComboBlock = (cmpGained.length || cmpLost.length) ? `
      <div style="margin-top:10px">
        <h4>Combos in B vs A</h4>
        ${cmpGained.length ? `
          <strong class="diff-add">Newly complete in B (${cmpGained.length}):</strong>
          <ul>${cmpGained.map(c =>
            `<li class="diff-add">${escape(c.short_label)} <a href="#" class="external-link" data-url="${escape(c.spellbook_url)}">[?]</a></li>`,
          ).join("")}</ul>` : ""}
        ${cmpLost.length ? `
          <strong class="diff-remove">Lost in B (${cmpLost.length}):</strong>
          <ul>${cmpLost.map(c =>
            `<li class="diff-remove">${escape(c.short_label)} <a href="#" class="external-link" data-url="${escape(c.spellbook_url)}">[?]</a></li>`,
          ).join("")}</ul>` : ""}
      </div>
    ` : "";
    els.compare_decks_result.innerHTML = `
      <div class="duel-verdict">
        <div class="duel-verdict-headline">Analyst comparison ${verifiedBadge}</div>
        <div class="duel-verdict-sub">Power gap (B - A): ${sign}${r.power_gap.toFixed(1)}</div>
      </div>
      <div class="panel" style="margin-top:8px">
        <p>${escape(r.summary).replace(/\n/g, "<br>")}</p>
        <div class="panel-row" style="gap: 16px; align-items: flex-start;">
          <div style="flex:1">
            <h4>Role deltas (B − A)</h4>
            <table class="gauntlet-table"><tbody>${deltaRows}</tbody></table>
          </div>
          <div style="flex:1">
            <h4>Added in B</h4>
            <p class="panel-hint">${addedSample}${(r.added_cards || []).length > 6 ? ` … +${r.added_cards.length - 6} more` : ""}</p>
            <h4 style="margin-top:10px">Removed in B</h4>
            <p class="panel-hint">${removedSample}${(r.removed_cards || []).length > 6 ? ` … +${r.removed_cards.length - 6} more` : ""}</p>
          </div>
        </div>
        ${cmpComboBlock}
      </div>
    `;
    els.compare_decks_result.querySelectorAll("a.external-link").forEach(a =>
      a.addEventListener("click", (e) => {
        e.preventDefault();
        const url = a.dataset.url;
        if (url) callApi("open_external", url).catch(() => {});
      }));
  } catch (e) {
    els.compare_decks_result.innerHTML = `<p class="panel-hint" style="color:#ff9999">Compare failed: ${escape(e.message)}</p>`;
  } finally {
    els.compare_decks_btn.disabled = false;
    els.compare_decks_btn.textContent = orig;
  }
}

async function runDuel() {
  if (!state.currentDeckId) return;
  const oppId = els.duel_opponent_select.value;
  if (!oppId) return;
  const sims = parseInt(els.duel_sims_select.value, 10) || 100;
  els.duel_run_btn.disabled = true;
  els.duel_run_btn.textContent = "Running…";
  try {
    const r = await callApi("duel_decks", state.currentDeckId, oppId, sims);
    renderDuelResult(r);
  } catch (e) {
    toast("Duel failed: " + e.message, "error");
  } finally {
    els.duel_run_btn.disabled = false;
    els.duel_run_btn.textContent = "Run duel";
  }
}

function renderDuelResult(r) {
  const a = r.a_vs_b, b = r.b_vs_a, v = r.verdict;
  const axisRow = (label, key) => {
    const d = v.axis_deltas[key];
    const sign = d > 0 ? "+" : "";
    const color = Math.abs(d) < 0.3 ? "var(--color-text-muted)"
      : d > 0 ? "var(--color-accent-green, #34d399)" : "var(--color-primary)";
    return `<tr><td>${label}</td><td style="text-align:right">${a.power[key].toFixed(1)}</td><td style="text-align:center;color:${color};font-weight:700">${sign}${d.toFixed(1)}</td><td style="text-align:right">${b.power[key].toFixed(1)}</td></tr>`;
  };
  const verdictClass = v.winner === "a" ? "duel-winner-a"
    : v.winner === "b" ? "duel-winner-b" : "duel-winner-even";
  els.duel_result.innerHTML = `
    <div class="duel-verdict ${verdictClass}">
      <div class="duel-verdict-headline">${escape(v.headline)}</div>
      <div class="duel-verdict-sub">${r.simulations} games per perspective &middot; two-sided run (each deck as hero once)</div>
    </div>
    <div class="duel-sides">
      <div class="duel-side">
        <h4>${escape(a.name)}</h4>
        <div class="duel-archetype">${escape(a.archetype)} &middot; power ${a.power.overall.toFixed(1)} (${escape(a.power.tier || "")})</div>
        <div class="duel-wr">${a.win_rate.toFixed(1)}% wins</div>
        <div class="duel-stat-grid">
          <div><span class="label">Wins / losses</span><span>${a.wins} / ${a.losses}</span></div>
          <div><span class="label">Avg turns to kill</span><span>${a.avg_turns.toFixed(2)}</span></div>
          <div><span class="label">Avg dmg dealt</span><span>${a.avg_damage_dealt.toFixed(1)}</span></div>
          <div><span class="label">Avg dmg taken</span><span>${a.avg_damage_taken.toFixed(1)}</span></div>
          <div><span class="label">Wins by damage</span><span>${a.wins_by_damage}</span></div>
          ${a.combos_evaluated ? `<div><span class="label">Wins by combo</span><span>${a.wins_by_combo} (${a.combo_win_rate.toFixed(1)}%)</span></div>` : ""}
          <div><span class="label">Losses by clock</span><span>${a.losses_by_clock}</span></div>
          ${a.combos_evaluated && a.avg_combo_win_turn ? `<div><span class="label">Avg combo turn</span><span>${a.avg_combo_win_turn.toFixed(1)}</span></div>` : ""}
        </div>
      </div>
      <div class="duel-vs">VS</div>
      <div class="duel-side">
        <h4>${escape(b.name)}</h4>
        <div class="duel-archetype">${escape(b.archetype)} &middot; power ${b.power.overall.toFixed(1)} (${escape(b.power.tier || "")})</div>
        <div class="duel-wr">${b.win_rate.toFixed(1)}% wins</div>
        <div class="duel-stat-grid">
          <div><span class="label">Wins / losses</span><span>${b.wins} / ${b.losses}</span></div>
          <div><span class="label">Avg turns to kill</span><span>${b.avg_turns.toFixed(2)}</span></div>
          <div><span class="label">Avg dmg dealt</span><span>${b.avg_damage_dealt.toFixed(1)}</span></div>
          <div><span class="label">Avg dmg taken</span><span>${b.avg_damage_taken.toFixed(1)}</span></div>
          <div><span class="label">Wins by damage</span><span>${b.wins_by_damage}</span></div>
          ${b.combos_evaluated ? `<div><span class="label">Wins by combo</span><span>${b.wins_by_combo} (${b.combo_win_rate.toFixed(1)}%)</span></div>` : ""}
          <div><span class="label">Losses by clock</span><span>${b.losses_by_clock}</span></div>
          ${b.combos_evaluated && b.avg_combo_win_turn ? `<div><span class="label">Avg combo turn</span><span>${b.avg_combo_win_turn.toFixed(1)}</span></div>` : ""}
        </div>
      </div>
    </div>
    <div class="duel-axis-table">
      <h4>Power sub-score deltas (positive = A is stronger) ${helpIcon("duel", {title: "Duel methodology"})}</h4>
      <table>
        <thead><tr><th>Axis</th><th style="text-align:right">${escape(a.name)}</th><th>Δ</th><th style="text-align:right">${escape(b.name)}</th></tr></thead>
        <tbody>
          ${axisRow("Speed", "speed")}
          ${axisRow("Interaction", "interaction")}
          ${axisRow("Combo potential", "combo_potential")}
          ${axisRow("Mana efficiency", "mana_efficiency")}
          ${axisRow("Win-condition quality", "win_condition_quality")}
          ${axisRow("Card quality", "card_quality")}
        </tbody>
      </table>
    </div>
  `;
  els.duel_result.classList.remove("hidden");
}

// ------------------------------ Settings view ------------------------------

async function refreshSettings() {
  try {
    const tier = await callApi("get_tier");
    els.tier_status.innerHTML = tier.is_pro
      ? `<strong>Pro</strong> — all features unlocked.`
      : `<strong>Free</strong> — analysis available; Save / Export / Analyst require Pro.`;

    // Fill version into the About panel — safe if the elt isn't found for any reason
    try {
      const v = await callApi("get_current_version");
      if (els.about_version && v.version) {
        els.about_version.textContent = `v${v.version}`;
      }
    } catch (e) { /* non-fatal */ }

    const status = await callApi("get_system_status");
    els.system_status.innerHTML = `
      <div class="card ${status.card_database.ready ? "ready" : "missing"}">
        <strong>Card database</strong><br>
        ${status.card_database.ready
          ? `<span>${status.card_database.count.toLocaleString()} cards ingested</span>`
          : "<span>Not ingested yet. Click <strong>Install card database</strong> below.</span>"}
      </div>
      <div class="card ${status.analyst_model.ready ? "ready" : (status.analyst_model.file_present ? "warning" : "missing")}">
        <strong>Analyst model</strong><br>
        ${status.analyst_model.ready
          ? "<span>Ready</span>"
          : `<span>${status.analyst_model.reason || "Not installed. Click <strong>Download analyst model</strong> below."}</span>`}
      </div>
    `;
    await loadUserPrefsIntoSettings();
    await loadComboStatus();
    await loadMcpStatus();
  } catch (e) {
    toast("Settings refresh failed: " + e.message, "error");
  }
}

async function activateLicense() {
  const key = els.license_key_input.value.trim();
  if (!key) { els.license_status.textContent = "Enter a license key first."; return; }
  els.license_status.textContent = "Activating...";
  try {
    const result = await callApi("activate_license", key);
    if (result.valid) {
      els.license_status.textContent = result.is_master ? "Master key activated." : "Pro activated.";
      toast("License activated — Pro features are now available.", "success");
      state.tier = await callApi("get_tier");
      renderTier(state.tier);
      refreshSettings();
    } else {
      // Surface the granular error from verify_license_key (wrong prefix /
      // wrong length / checksum mismatch / etc.) so the user can fix the
      // specific part of the key that looks off, rather than hunting for
      // typos blindly.
      els.license_status.textContent = result.error
        || "Invalid key — check that you copied it exactly.";
    }
  } catch (e) {
    els.license_status.textContent = "Activation failed: " + e.message;
  }
}

// ------------------------------ Goldfish + Gauntlet ------------------------------

async function runGoldfish() {
  const text = els.decklist_input.value.trim();
  const name = els.deck_name_input.value.trim() || "Unnamed Deck";
  if (!text) { toast("Paste a decklist first.", "error"); return; }
  els.goldfish_btn.disabled = true;
  els.analyze_status.textContent = "Running 1000 goldfish games (this takes 5-30s)...";
  try {
    const r = await callApi("run_goldfish", text, els.format_select.value, name, 1000);
    renderGoldfish(r);
    els.analyze_status.textContent = "";
  } catch (e) {
    toast("Goldfish failed: " + e.message, "error");
    els.analyze_status.textContent = "";
  } finally {
    els.goldfish_btn.disabled = !state.tier?.is_pro ? true : false;
  }
}

function renderGoldfish(r) {
  els.goldfish_result.classList.remove("hidden");
  // Kill-turn distribution as inline SVG-esque bars. `r.kill_turn_distribution`
  // has string keys because Python int-key dicts serialize to strings through
  // JSON; we parse back to sort numerically.
  const dist = r.kill_turn_distribution || {};
  const turns = Object.keys(dist).map(Number).sort((a, b) => a - b);
  const maxRate = Math.max(...Object.values(dist), 0.0001);
  const rows = turns.map(t => {
    const rate = dist[t];
    const pct = (rate / maxRate) * 100;
    return `<div class="kill-turn-row">
      <span class="turn-label">Turn ${t}</span>
      <span class="track"><span class="fill" style="width:${pct}%"></span></span>
      <span class="rate-label">${(rate * 100).toFixed(1)}%</span>
    </div>`;
  }).join("");

  const mostCast = (r.most_cast_spells || []).slice(0, 8).map(([name, count]) =>
    `<li>${escape(name)} <span class="status-text">cast in ${count} games</span></li>`
  ).join("");

  // Combo wins panel — only renders when combos were actually evaluated.
  // Builds two pieces: a "Combo wins" stat-card (sits next to kill stats)
  // and a per-turn distribution row underneath, mirroring the kill-turn
  // distribution UI.
  const combosEvaluated = r.combos_evaluated || 0;
  const comboWinRate = r.combo_win_rate || 0;
  const avgComboTurn = r.average_combo_win_turn || 0;
  const comboCard = combosEvaluated > 0 ? `
    <div class="stat-card"><div class="label">Combo win rate</div>
      <div class="value">${(comboWinRate * 100).toFixed(0)}%</div>
      <div class="sub">${avgComboTurn ? `turn ${avgComboTurn.toFixed(1)} avg` : "no combos assembled"}</div>
    </div>
  ` : "";

  let comboDistSection = "";
  if (combosEvaluated > 0 && Object.keys(r.combo_win_turn_distribution || {}).length) {
    const dist = r.combo_win_turn_distribution || {};
    const cTurns = Object.keys(dist).map(Number).sort((a, b) => a - b);
    const cMax = Math.max(...Object.values(dist), 0.0001);
    const cRows = cTurns.map(t => {
      const rate = dist[t];
      const pct = (rate / cMax) * 100;
      return `<div class="kill-turn-row">
        <span class="turn-label">Turn ${t}</span>
        <span class="track"><span class="fill combo-fill" style="width:${pct}%"></span></span>
        <span class="rate-label">${(rate * 100).toFixed(1)}%</span>
      </div>`;
    }).join("");
    const topLines = (r.top_combo_lines || []).slice(0, 5).map(([id, label, count, rate]) =>
      `<li><strong>${escape(label)}</strong> &middot; <span class="status-text">${count} game${count === 1 ? "" : "s"} (${(rate * 100).toFixed(1)}%)</span></li>`,
    ).join("");
    comboDistSection = `
      <div class="result-section">
        <h3>Combo win-turn distribution</h3>
        <p class="panel-hint">${combosEvaluated} combo line${combosEvaluated === 1 ? "" : "s"} tracked. A "combo win" means all pieces were in possession (battlefield + hand + graveyard) by the listed turn.</p>
        <div class="kill-turn-dist">${cRows}</div>
        ${topLines ? `<h4>Top firing combos</h4><ul class="rec-list">${topLines}</ul>` : ""}
      </div>
    `;
  } else if (combosEvaluated > 0) {
    comboDistSection = `
      <div class="result-section">
        <h3>Combos</h3>
        <p class="panel-hint">${combosEvaluated} combo line${combosEvaluated === 1 ? "" : "s"} tracked, but none assembled in ${r.simulations} games.</p>
      </div>
    `;
  }

  els.goldfish_result.innerHTML = `
    <div class="panel">
      <h2>Goldfish simulation — ${r.simulations} games</h2>
      <div class="sim-summary-grid">
        <div class="stat-card"><div class="label">Avg kill turn</div><div class="value">${r.average_kill_turn.toFixed(1)}</div></div>
        <div class="stat-card"><div class="label">Kill rate</div><div class="value">${(r.kill_rate * 100).toFixed(0)}%</div><div class="sub">games dealing 40+ damage</div></div>
        <div class="stat-card"><div class="label">Avg mulligans</div><div class="value">${r.average_mulligans.toFixed(2)}</div></div>
        <div class="stat-card"><div class="label">Commander cast rate</div><div class="value">${(r.commander_cast_rate * 100).toFixed(0)}%</div><div class="sub">turn ${r.average_commander_turn.toFixed(1)} avg</div></div>
        ${comboCard}
      </div>

      ${turns.length ? `
        <div class="result-section">
          <h3>Kill-turn distribution</h3>
          <div class="kill-turn-dist">${rows}</div>
        </div>` : ""}

      ${comboDistSection}

      ${mostCast ? `
        <div class="result-section">
          <h3>Most-cast spells</h3>
          <ul class="rec-list">${mostCast}</ul>
        </div>` : ""}

      ${Object.keys(r.objective_pass_rates || {}).length ? `
        <div class="result-section">
          <h3>Objective pass rates</h3>
          <ul class="rec-list">
            ${Object.entries(r.objective_pass_rates).map(([name, rate]) =>
              `<li class="severity-info">${escape(name)}: ${(rate * 100).toFixed(0)}%</li>`
            ).join("")}
          </ul>
        </div>` : ""}
    </div>
  `;
  // Scroll results into view so the user sees them on click
  els.goldfish_result.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function runGauntlet() {
  const text = els.decklist_input.value.trim();
  const name = els.deck_name_input.value.trim() || "Unnamed Deck";
  if (!text) { toast("Paste a decklist first.", "error"); return; }
  els.gauntlet_btn.disabled = true;
  els.analyze_status.textContent = "Running gauntlet (30-60s, tests vs 11 archetypes)...";
  try {
    const r = await callApi("run_gauntlet", text, els.format_select.value, name, 200);
    renderGauntlet(r);
    els.analyze_status.textContent = "";
  } catch (e) {
    toast("Gauntlet failed: " + e.message, "error");
    els.analyze_status.textContent = "";
  } finally {
    els.gauntlet_btn.disabled = !state.tier?.is_pro ? true : false;
  }
}

function renderGauntlet(r) {
  els.gauntlet_result.classList.remove("hidden");
  const rateClass = (wr) => wr >= 0.55 ? "rate-high" : wr >= 0.40 ? "rate-mid" : "rate-low";

  const combosOn = (r.combos_evaluated || 0) > 0;

  // The Combo % column shows the share of WINS (not all games) that
  // closed via combo for that archetype — so a 70% win-rate matchup
  // with 40% combo wins reads "combo closed 4/7 of the wins".
  const rows = (r.matchups || [])
    .slice()
    .sort((a, b) => b.win_rate - a.win_rate)
    .map(m => {
      const winShareCombo = m.wins > 0 ? (m.wins_by_combo / m.wins) : 0;
      const comboCol = combosOn
        ? `<td title="${m.wins_by_combo} of ${m.wins} wins closed via combo">${m.wins_by_combo ? `${(winShareCombo * 100).toFixed(0)}%` : "—"}</td>`
        : "";
      const comboTurnCol = combosOn
        ? `<td>${m.avg_combo_win_turn ? m.avg_combo_win_turn.toFixed(1) : "—"}</td>`
        : "";
      return `<tr>
        <td>${escape(m.archetype)}</td>
        <td>${m.wins}/${m.simulations}</td>
        <td class="${rateClass(m.win_rate)}">${(m.win_rate * 100).toFixed(0)}%</td>
        <td>${m.avg_turns.toFixed(1)}</td>
        ${comboCol}
        ${comboTurnCol}
      </tr>`;
    }).join("");

  const comboHeader = combosOn
    ? `<th title="Share of this archetype's wins that closed via combo assembly">Combo % of wins</th><th>Combo turn</th>`
    : "";

  // Overall combo card next to the win-rate cards. Hidden when no combos
  // evaluated so existing decks render the same as before.
  const comboOverallCard = combosOn ? `
    <div class="stat-card">
      <div class="label">Combo wins</div>
      <div class="value">${(r.combo_win_rate_overall * 100).toFixed(0)}%</div>
      <div class="sub">${r.avg_combo_win_turn_overall ? `turn ${r.avg_combo_win_turn_overall.toFixed(1)} avg` : "no combos closed"}</div>
    </div>
  ` : "";

  // Top firing combos block — shown beneath the table when any combo fired.
  const topLines = (r.top_combo_lines_overall || []).slice(0, 5).map(([id, label, count, rate]) =>
    `<li><strong>${escape(label)}</strong> &middot; <span class="status-text">${count} game${count === 1 ? "" : "s"} (${(rate * 100).toFixed(1)}%)</span></li>`,
  ).join("");
  const comboTopSection = combosOn && topLines ? `
    <div class="result-section">
      <h3>Top firing combos across the gauntlet</h3>
      <p class="panel-hint">${r.combos_evaluated} combo line${r.combos_evaluated === 1 ? "" : "s"} tracked. A win is attributed to combo when all pieces are assembled before the opponent's clock kills you.</p>
      <ul class="rec-list">${topLines}</ul>
    </div>
  ` : "";

  els.gauntlet_result.innerHTML = `
    <div class="panel">
      <h2>Matchup gauntlet — ${r.total_games} games across ${r.matchups.length} archetypes</h2>
      <div class="sim-summary-grid">
        <div class="stat-card"><div class="label">Overall win rate</div><div class="value">${(r.overall_win_rate * 100).toFixed(0)}%</div></div>
        <div class="stat-card"><div class="label">Meta-weighted</div><div class="value">${(r.weighted_win_rate * 100).toFixed(0)}%</div><div class="sub">weighted by meta share</div></div>
        <div class="stat-card"><div class="label">Best matchup</div><div class="value" style="font-size:1rem">${escape(r.best_matchup)}</div><div class="sub">${(r.best_win_rate * 100).toFixed(0)}%</div></div>
        <div class="stat-card"><div class="label">Worst matchup</div><div class="value" style="font-size:1rem">${escape(r.worst_matchup)}</div><div class="sub">${(r.worst_win_rate * 100).toFixed(0)}%</div></div>
        ${comboOverallCard}
      </div>

      <div class="panel-row" style="gap:20px">
        <div style="flex:2">
          <h3>Per-archetype results</h3>
          <table class="gauntlet-table">
            <thead><tr><th>Archetype</th><th>Wins/Sims</th><th>Win %</th><th>Avg turns</th>${comboHeader}</tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
        <div style="flex:1">
          <h3>Category scores</h3>
          <div class="score-row"><span class="axis">Speed</span><span class="bar"><span class="bar-fill" style="width:${r.speed_score}%"></span></span><span class="value">${r.speed_score.toFixed(0)}</span></div>
          <div class="score-row"><span class="axis">Resilience</span><span class="bar"><span class="bar-fill" style="width:${r.resilience_score}%"></span></span><span class="value">${r.resilience_score.toFixed(0)}</span></div>
          <div class="score-row"><span class="axis">Interaction</span><span class="bar"><span class="bar-fill" style="width:${r.interaction_score}%"></span></span><span class="value">${r.interaction_score.toFixed(0)}</span></div>
          <div class="score-row"><span class="axis">Consistency</span><span class="bar"><span class="bar-fill" style="width:${r.consistency_score}%"></span></span><span class="value">${r.consistency_score.toFixed(0)}</span></div>
        </div>
      </div>

      ${comboTopSection}
    </div>
  `;
  els.gauntlet_result.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ------------------------------ Rule 0 worksheet ------------------------------

async function openRule0Worksheet() {
  const text = els.decklist_input.value.trim();
  if (!text) { toast("Paste a decklist first.", "error"); return; }
  if (!els.rule0_modal) return;
  els.rule0_modal.classList.remove("hidden");
  els.rule0_modal.setAttribute("aria-hidden", "false");
  els.rule0_text.textContent = "(loading...)";
  try {
    const r = await callApi(
      "build_rule0_worksheet",
      text,
      els.format_select.value,
      els.deck_name_input.value || "Unnamed Deck",
      true,  // include_combos
    );
    els.rule0_text.textContent = r.rendered_text || "(no output)";
  } catch (e) {
    els.rule0_text.textContent = "Error: " + e.message;
  }
}

function hideRule0() {
  if (!els.rule0_modal) return;
  els.rule0_modal.classList.add("hidden");
  els.rule0_modal.setAttribute("aria-hidden", "true");
}

// ------------------------------ Setup (ingest + analyst pull) ------------------------------

async function startIngest() {
  els.ingest_btn.disabled = true;
  els.ingest_status.textContent = "Starting...";
  els.ingest_progress_wrap.classList.remove("hidden");
  try {
    await callApi("ingest_start", false);
    pollProgress("ingest", () => {
      els.ingest_btn.disabled = false;
      els.ingest_status.textContent = "";
      checkSetupBanner();
      refreshSettings();
    });
  } catch (e) {
    els.ingest_btn.disabled = false;
    els.ingest_status.textContent = "";
    toast("Ingest start failed: " + e.message, "error");
  }
}

async function startAnalystPull() {
  const model = els.analyst_model_select.value;
  els.analyst_pull_btn.disabled = true;
  els.analyst_pull_status.textContent = "Starting...";
  els.analyst_pull_progress_wrap.classList.remove("hidden");
  try {
    await callApi("analyst_pull_start", model);
    pollProgress("analyst_pull", async () => {
      els.analyst_pull_btn.disabled = false;
      els.analyst_pull_status.textContent = "";
      // Invalidate the cached coach backend so the next coach_start
      // picks up the newly-downloaded model instead of keeping the
      // Mock placeholder the app selected at launch when there was
      // no model on disk yet.
      try { await callApi("refresh_coach_backend"); } catch (e) { /* non-fatal */ }
      refreshSettings();
    });
  } catch (e) {
    els.analyst_pull_btn.disabled = false;
    els.analyst_pull_status.textContent = "";
    toast("Pull start failed: " + e.message, "error");
  }
}

/**
 * Poll a background operation's progress and update its bar until done.
 * `op` is "ingest" or "analyst_pull" — same naming as the API.
 */
function pollProgress(op, onComplete) {
  const apiMethod = op + "_progress";
  const fill = els[op + "_progress_fill"];
  const msg = els[op + "_progress_msg"];
  const tick = async () => {
    try {
      const p = await callApi(apiMethod);
      fill.style.width = `${p.pct || 0}%`;
      msg.textContent = p.message || "";
      if (p.error) {
        msg.textContent = "Error: " + p.error;
        msg.style.color = "#ff9999";
      }
      if (p.done) {
        if (onComplete) onComplete();
        return;
      }
    } catch (e) {
      msg.textContent = "Progress poll failed: " + e.message;
      return;
    }
    setTimeout(tick, 600);
  };
  tick();
}

// ------------------------------ URL import ------------------------------

async function importDeckFromUrl() {
  const url = els.url_import_input.value.trim();
  if (!url) { toast("Paste a Moxfield or Archidekt URL first.", "error"); return; }
  els.url_import_btn.disabled = true;
  els.url_import_status.textContent = "Fetching...";
  try {
    const r = await callApi("import_deck_from_url", url);
    // Replace the textarea contents + prefill the deck name if we can infer it
    els.decklist_input.value = r.decklist_text;
    if (!els.deck_name_input.value) {
      els.deck_name_input.value = `${r.service}-${r.deck_id}`;
    }
    els.url_import_status.textContent = `Loaded ${r.card_count} cards from ${r.service}.`;
    els.url_import_input.value = "";
  } catch (e) {
    els.url_import_status.textContent = "";
    // If this was a Moxfield URL, show the dedicated workaround modal
    // instead of a transient toast — the workaround is the supported
    // path (Export → Text) and we want to make it dead-simple to follow.
    const isMoxfield = /moxfield\.com\/decks\//i.test(url);
    if (isMoxfield) {
      showMoxfieldWorkaround(url);
    } else {
      toast("URL import failed: " + e.message, "error");
    }
  } finally {
    els.url_import_btn.disabled = false;
    // Clear the status message after a few seconds so it doesn't linger
    setTimeout(() => { els.url_import_status.textContent = ""; }, 5000);
  }
}

// Moxfield is behind Cloudflare and blocks automated imports — instead of
// a generic error toast, show a dedicated modal with a 3-step workaround
// that takes the user to the Export → Text feature on Moxfield. The
// research agent's recommendation: keep this UX path until Moxfield ships
// a documented public API.
function showMoxfieldWorkaround(originalUrl) {
  const m = $("moxfield-help-modal");
  if (!m) {
    // Modal not in DOM yet — fall back to a toast. Shouldn't happen if
    // the bundled index.html is current.
    toast(
      "Moxfield blocks direct imports. Use Export → Text on the deck page, then paste below.",
      "error",
    );
    return;
  }
  const linkEl = $("moxfield-help-open-link");
  if (linkEl) {
    linkEl.dataset.url = originalUrl;
    linkEl.textContent = originalUrl;
  }
  m.classList.remove("hidden");
  m.setAttribute("aria-hidden", "false");
}

function hideMoxfieldWorkaround() {
  const m = $("moxfield-help-modal");
  if (!m) return;
  m.classList.add("hidden");
  m.setAttribute("aria-hidden", "true");
}

// ------------------------------ Coach tab ------------------------------

async function refreshCoachView() {
  // Populate the "Start from saved deck" picker with current saved decks
  try {
    const decks = await callApi("list_saved_decks");
    els.coach_deck_select.innerHTML = '<option value="">(choose a deck...)</option>';
    decks.forEach(d => {
      const opt = document.createElement("option");
      opt.value = d.deck_id;
      opt.textContent = `${d.name} (v${d.versions})`;
      els.coach_deck_select.appendChild(opt);
    });

    const sessions = await callApi("coach_list_sessions");
    state.coachSessions = sessions;
    renderCoachSessionList();
    // If a session is already active, make sure the chat panel reflects it
    if (!state.coachToken) {
      els.coach_empty.classList.remove("hidden");
      els.coach_active.classList.add("hidden");
    }
  } catch (e) {
    toast("Coach view failed to refresh: " + e.message, "error");
  }
}

function renderCoachSessionList() {
  els.coach_sessions_list.innerHTML = "";
  if (!state.coachSessions.length) {
    els.coach_sessions_list.innerHTML = '<li style="cursor:default"><span class="deck-meta">(no active sessions)</span></li>';
    return;
  }
  state.coachSessions.forEach(s => {
    const li = document.createElement("li");
    const active = state.coachToken === s.token ? "active" : "";
    li.className = active;
    li.innerHTML = `
      <span class="deck-name">${escape(s.deck_name)}</span>
      <span class="deck-meta">${s.turn_count} turn${s.turn_count === 1 ? "" : "s"}</span>
    `;
    li.addEventListener("click", () => resumeCoachSession(s.token, s.deck_name));
    els.coach_sessions_list.appendChild(li);
  });
}

async function startCoachSession() {
  if (!state.tier?.is_pro) {
    toast("Coach requires Pro. Activate a license on Settings.", "error");
    return;
  }
  const deckId = els.coach_deck_select.value;
  if (!deckId) {
    toast("Pick a saved deck first.", "error");
    return;
  }
  els.coach_start_btn.disabled = true;
  try {
    const r = await callApi("coach_start", deckId);
    state.coachToken = r.token;
    state.coachMessages = [];
    openCoachSession(r);
    await refreshCoachView();
  } catch (e) {
    toast("Coach failed to start: " + e.message, "error");
  } finally {
    els.coach_start_btn.disabled = false;
  }
}

function openCoachSession(info) {
  els.coach_empty.classList.add("hidden");
  els.coach_active.classList.remove("hidden");
  els.coach_deck_name.textContent = info.deck_name;
  els.coach_deck_meta.textContent = `${info.power || ""} • ${info.archetype || ""}`;
  renderCoachMessages();
  els.coach_input.focus();
}

async function resumeCoachSession(token, deckName) {
  if (state.coachToken === token) return; // already viewing it
  state.coachToken = token;
  els.coach_empty.classList.add("hidden");
  els.coach_active.classList.remove("hidden");
  els.coach_deck_name.textContent = deckName;
  els.coach_deck_meta.textContent = "(loading history...)";
  state.coachMessages = [];
  // Disable the send button while history is loading — otherwise the user
  // could submit a new question before history loads, the response would
  // append to an empty message list, and the later history load would
  // overwrite + drop the just-sent exchange. Re-enabled after load.
  els.coach_send_btn.disabled = true;
  renderCoachMessages();
  renderCoachSessionList();
  try {
    const history = await callApi("coach_get_history", token);
    state.coachMessages = (history || []).flatMap(t => [
      { role: "user", text: t.user_question },
      {
        role: "assistant",
        text: t.assistant_response,
        verified: t.verified,
        confidence: t.confidence,
      },
    ]);
    els.coach_deck_meta.textContent = `${history.length} turn${history.length === 1 ? "" : "s"}`;
    renderCoachMessages();
  } catch (e) {
    els.coach_deck_meta.textContent = "(history unavailable)";
  } finally {
    els.coach_send_btn.disabled = false;
  }
}

async function submitCoachQuestion(e) {
  e.preventDefault();
  const question = els.coach_input.value.trim();
  if (!question || !state.coachToken) return;

  // Push the user message immediately so typing feels responsive
  state.coachMessages.push({ role: "user", text: question });
  els.coach_input.value = "";
  renderCoachMessages();
  els.coach_send_btn.disabled = true;

  // Placeholder assistant bubble that updates when the response arrives
  const pending = { role: "assistant", text: "...", pending: true };
  state.coachMessages.push(pending);
  renderCoachMessages();

  try {
    const turn = await callApi("coach_ask", state.coachToken, question);
    pending.text = turn.assistant_response || "(empty response)";
    pending.verified = turn.verified;
    pending.confidence = turn.confidence;
    pending.pending = false;
  } catch (err) {
    pending.text = "Error: " + err.message;
    pending.verified = false;
    pending.confidence = 0;
    pending.pending = false;
  }
  renderCoachMessages();
  els.coach_send_btn.disabled = false;
  // Refresh session list so the turn count updates
  try {
    const sessions = await callApi("coach_list_sessions");
    state.coachSessions = sessions;
    renderCoachSessionList();
  } catch (e) { /* non-fatal */ }
}

function renderCoachMessages() {
  els.coach_messages.innerHTML = "";
  state.coachMessages.forEach(m => {
    const div = document.createElement("div");
    div.className = "chat-message " + m.role + (m.verified === false ? " unverified" : "");
    div.textContent = m.text;
    if (m.role === "assistant" && !m.pending && typeof m.confidence === "number") {
      const conf = document.createElement("span");
      conf.className = "confidence";
      conf.textContent = `confidence: ${Math.round(m.confidence * 100)}%`;
      div.appendChild(conf);
    }
    els.coach_messages.appendChild(div);
  });
  // Scroll to newest message
  els.coach_messages.scrollTop = els.coach_messages.scrollHeight;
}

async function resetCoachSession() {
  if (!state.coachToken) return;
  try {
    await callApi("coach_reset", state.coachToken);
    state.coachMessages = [];
    renderCoachMessages();
    toast("History cleared.", "success");
  } catch (e) {
    toast("Reset failed: " + e.message, "error");
  }
}

async function closeCoachSession() {
  if (!state.coachToken) return;
  try {
    await callApi("coach_close", state.coachToken);
    state.coachToken = null;
    state.coachMessages = [];
    els.coach_active.classList.add("hidden");
    els.coach_empty.classList.remove("hidden");
    await refreshCoachView();
  } catch (e) {
    toast("Close failed: " + e.message, "error");
  }
}

// ------------------------------ Help / methodology tooltips ------------------------------

// Short plain-English explanations that appear in a popover when the
// user clicks a (?) icon next to a section heading or stat. The longer
// writeups with formulas live in static/methodology.html — the "Open
// methodology page" link below every tooltip goes there.
const HELP = {
  power_level: "1-10 rating averaging six weighted sub-scores (speed, interaction, combo potential, mana efficiency, win-condition quality, card quality). Tiers: jank < 3, casual 3-5, focused 5-7, optimized 7-8.5, competitive 8.5-9.5, cEDH 9.5+. Computed by densa_deck.analysis.power_level.estimate_power_level.",
  speed: "How quickly the deck can threaten lethal / combo. Derived from mana curve, ramp density, and low-CMC threat count. Higher = kills faster.",
  interaction: "How much the deck disrupts opponents. Counts targeted removal, counterspells, and board wipes against format-specific targets (Commander wants 8-12 interaction pieces).",
  combo_potential: "Tutor density + reliable wincon engines + low-CMC win conditions. High when the deck has multiple ways to assemble a win that bypasses combat.",
  mana_efficiency: "Curve vs land count + ramp, color fixing, and the amount of mana the deck produces by turn 5. Higher = more consistent at casting what you draw.",
  win_condition_quality: "Finisher density and diversity. Penalizes single-threat decks (easy to remove) and rewards redundancy across different win paths.",
  card_quality: "Proxy for how premium the card pool is. Uses average CMC and density of \"dead\" cards (high-cost creatures with no enter effects) as a coarse signal.",
  mana_curve: "Distribution of cards by mana value. Commander targets a curve peaking at 2-4; lower is faster, higher is top-heavy. Excludes lands.",
  archetype: "Detected by densa_deck.formats.profiles.detect_archetype using role counts + threat density + wincon signals. One of: aggro, midrange, control, combo, stax, aristocrats, spellslinger, tokens, voltron, group_hug, turbo.",
  mana_base: "Grade (A-F) reflecting color-source adequacy for each pip demand, untapped-land ratio, and fetch/fixing density. A means every colored card you draw is castable on-curve; F means frequent color screw.",
  castability: "For each card, probability it can be cast on its natural curve turn given this deck's land distribution. Low values flag cards whose color demands outstrip your mana base (e.g. triple-green on a two-color deck).",
  category_scores: "Per-category 0-100 scores (ramp, draw, removal, curve, etc.) showing how well this deck fits format-appropriate targets. Anchored to format presets — Commander wants 10-15 ramp, Modern wants 0-4.",
  power_signals: "Specific cards/patterns that pushed the power score up (ramp package, tutors, combos) or down (weak interaction, high curve, no wincon). Same text the AI coach uses to summarize the deck.",
  goldfish: "Solo simulation: the deck plays against no opponent for N games with a mulligan AI that keeps 7-card hands meeting format thresholds. Tracks avg kill turn, win rate vs the objective (deal 40 Commander / 20 Standard), mulligan rate, and key-card draw timing. Implemented in densa_deck.goldfish.runner.",
  gauntlet: "Matchup simulator: the deck plays 200 games (default) against each of 11 canonical archetype profiles (Aggro, Midrange, Control, Combo, Stax, etc.). Each archetype is a behavioral model — clock, interaction density, wipe chance — not a decklist. Result is a per-archetype win-rate table plus a weighted meta score. Implemented in densa_deck.matchup.gauntlet.",
  duel: "Head-to-head matchup between two of your saved decks. Runs the gauntlet engine twice (each deck as hero once) so win rates are reported from both perspectives. The opponent's behavioral profile is derived from their static analysis — archetype, power sub-scores, role counts — via densa_deck.matchup.deck_as_opponent.deck_to_profile. Same fidelity as the archetype gauntlet, not a card-by-card sim.",
  probability: "Hypergeometric distribution: P(drawing >= k copies of a card in N cards from a deck of D with K copies of that card). Used for \"chance I see my commander / ramp / combo by turn X\". Implemented in densa_deck.probability.hypergeometric.",
  recommendations: "Plain-language suggestions based on the numbers — \"you're 3 ramp pieces short of the 10-15 Commander target\", etc. Deterministic rules in densa_deck.analysis.static + .advanced. The AI coach (Pro) expands on these conversationally.",
};

function helpIcon(key, opts) {
  // Small inline (?) button that opens a popover with the HELP[key]
  // explanation. Clicking the button toggles the popover; clicking
  // elsewhere closes it. Multiple popovers can be open at once.
  const text = HELP[key];
  if (!text) return "";
  const title = (opts && opts.title) || "How this is calculated";
  return `<button class="help-icon" type="button" data-help-key="${escape(key)}" data-help-title="${escape(title)}" aria-label="${escape(title)}">?</button>`;
}

function installHelpPopoverHandlers() {
  // Delegated click handler — any .help-icon anywhere in the doc toggles
  // a popover next to it. Since we re-render chunks of DOM frequently
  // (analysis, gauntlet, duel), delegation beats per-render attach.
  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".help-icon");
    if (btn) {
      ev.preventDefault();
      ev.stopPropagation();
      const existing = btn.nextElementSibling;
      if (existing && existing.classList.contains("help-popover")) {
        existing.remove();
        return;
      }
      // Close any other open popovers before opening this one.
      document.querySelectorAll(".help-popover").forEach(p => p.remove());
      const key = btn.dataset.helpKey;
      const title = btn.dataset.helpTitle || "How this is calculated";
      const text = HELP[key] || "(no explanation available)";
      const pop = document.createElement("div");
      pop.className = "help-popover";
      pop.innerHTML = `
        <div class="help-popover-title">${escape(title)}</div>
        <div class="help-popover-body">${escape(text)}</div>
        <div class="help-popover-footer">
          <a href="#" class="help-open-methodology">Full methodology &rarr;</a>
        </div>
      `;
      btn.insertAdjacentElement("afterend", pop);
      pop.querySelector(".help-open-methodology").addEventListener("click", (e) => {
        e.preventDefault();
        // Open methodology page in-app. pywebview allows loading
        // static assets relative to the window URL.
        window.location.href = `methodology.html#${encodeURIComponent(key)}`;
      });
      return;
    }
    // Click outside: close any open popovers.
    if (!ev.target.closest(".help-popover")) {
      document.querySelectorAll(".help-popover").forEach(p => p.remove());
    }
  });
}

// ------------------------------ Utilities ------------------------------

function escape(s) {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// Fire when the DOM's parsed + the pywebview bridge has had a chance to init.
// pywebview injects `window.pywebview.api` after page load, so we give it
// a tick via DOMContentLoaded + the `pywebviewready` event if available.
window.addEventListener("DOMContentLoaded", () => {
  bootstrap();
  installHelpPopoverHandlers();
});
