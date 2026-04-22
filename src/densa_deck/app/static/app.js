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
    "analyze-btn", "save-btn", "goldfish-btn", "gauntlet-btn",
    "analyze-status", "analysis-result", "goldfish-result", "gauntlet-result",
    "refresh-decks-btn", "deck-list",
    "deck-editor-empty", "deck-editor-open",
    "editor-deck-name", "editor-version-number", "editor-saved-at", "editor-card-count",
    "editor-textarea", "editor-notes-input",
    "editor-save-version-btn", "editor-analyze-btn", "editor-history-btn", "editor-delete-btn",
    "editor-history", "history-body", "diff-panel",
    "tier-status", "license-key-input", "license-activate-btn", "license-status",
    "system-status", "toast",
    // Setup panel
    "ingest-btn", "ingest-status", "ingest-progress-wrap",
    "ingest-progress-fill", "ingest-progress-msg",
    "analyst-model-select", "analyst-pull-btn", "analyst-pull-status",
    "analyst-pull-progress-wrap", "analyst-pull-progress-fill", "analyst-pull-progress-msg",
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

function toast(message, kind = "info") {
  const t = els.toast;
  t.textContent = message;
  t.className = "toast " + kind;
  t.classList.remove("hidden");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => t.classList.add("hidden"), 4000);
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

  // Global keyboard shortcuts: Ctrl+1..4 switches tabs
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey) {
      const views = ["analyze", "decks", "coach", "settings"];
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
  els.editor_analyze_btn.addEventListener("click", () => {
    switchView("analyze");
    els.decklist_input.value = els.editor_textarea.value;
    els.deck_name_input.value = state.currentSnapshot ? state.currentSnapshot.deck_id : "";
  });
  els.editor_history_btn.addEventListener("click", toggleHistory);
  els.editor_delete_btn.addEventListener("click", deleteCurrentDeck);

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
      <div class="stat-card"><div class="label">Power level</div><div class="value">${r.power.overall.toFixed(1)}/10</div><div class="sub">${escape(r.power.tier)}</div></div>
      <div class="stat-card"><div class="label">Archetype</div><div class="value" style="font-size:1.1rem">${escape(r.archetype)}</div></div>
      <div class="stat-card"><div class="label">Mana base</div><div class="value">${escape(r.advanced.mana_base_grade || "-")}</div></div>
    </div>

    <div class="panel-row">
      <div class="panel result-section">
        <h3>Mana curve</h3>
        ${curveBars}
      </div>
      <div class="panel result-section">
        <h3>Category scores</h3>
        ${scoreBars(r.scores) || '<span class="status-text">(no scores)</span>'}
      </div>
    </div>

    ${r.power.reasons_up.length || r.power.reasons_down.length ? `
      <div class="panel result-section">
        <h3>Power-level signals</h3>
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
        <h3>Castability warnings</h3>
        <table class="castability-table">
          <thead><tr><th>Card</th><th>Cost</th><th>On-curve %</th><th>Bottleneck</th></tr></thead>
          <tbody>
            ${r.castability.unreliable_cards.map(c =>
              `<tr>
                <td>${escape(c.name)}</td>
                <td>${escape(c.mana_cost)}</td>
                <td>${(c.on_curve_probability * 100).toFixed(0)}%</td>
                <td>${escape(c.bottleneck_color || "-")}</td>
              </tr>`
            ).join("")}
          </tbody>
        </table>
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
  `;

  // Fire the fuzzy-match lookup async so it doesn't block the main render.
  // Shows "(checking...)" until results arrive, then replaces with chips
  // the user can click to fix their decklist.
  if (r.unresolved_cards.length) {
    fillUnresolvedSuggestions(r.unresolved_cards.slice(0, 20));
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
      li.innerHTML = `
        <span class="deck-name">${escape(d.name)}</span>
        <span class="deck-meta">${d.versions} version${d.versions === 1 ? "" : "s"} • ${escape((d.updated_at || "").slice(0, 10))}</span>
      `;
      li.addEventListener("click", () => openDeck(d.deck_id));
      els.deck_list.appendChild(li);
    });
  } catch (e) {
    toast("Failed to load decks: " + e.message, "error");
  }
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
    els.diff_panel.innerHTML = `
      <h4>Diff v${vA} → v${vB}</h4>
      <div class="diff-col"><h4>Added (${d.total_added})</h4><ul>${addedList}</ul></div>
      <div class="diff-col"><h4>Removed (${d.total_removed})</h4><ul>${removedList}</ul></div>
      <div class="diff-col"><h4>Score deltas</h4><ul>${scoreList}</ul></div>
    `;
    els.diff_panel.classList.remove("hidden");
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
      toast("License activated — restart the app if features don't update.", "success");
      state.tier = await callApi("get_tier");
      renderTier(state.tier);
      refreshSettings();
    } else {
      els.license_status.textContent = "Invalid key — check that you copied it exactly.";
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

  els.goldfish_result.innerHTML = `
    <div class="panel">
      <h2>Goldfish simulation — ${r.simulations} games</h2>
      <div class="sim-summary-grid">
        <div class="stat-card"><div class="label">Avg kill turn</div><div class="value">${r.average_kill_turn.toFixed(1)}</div></div>
        <div class="stat-card"><div class="label">Kill rate</div><div class="value">${(r.kill_rate * 100).toFixed(0)}%</div><div class="sub">games dealing 40+ damage</div></div>
        <div class="stat-card"><div class="label">Avg mulligans</div><div class="value">${r.average_mulligans.toFixed(2)}</div></div>
        <div class="stat-card"><div class="label">Commander cast rate</div><div class="value">${(r.commander_cast_rate * 100).toFixed(0)}%</div><div class="sub">turn ${r.average_commander_turn.toFixed(1)} avg</div></div>
      </div>

      ${turns.length ? `
        <div class="result-section">
          <h3>Kill-turn distribution</h3>
          <div class="kill-turn-dist">${rows}</div>
        </div>` : ""}

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

  const rows = (r.matchups || [])
    .slice()
    .sort((a, b) => b.win_rate - a.win_rate)
    .map(m => `<tr>
      <td>${escape(m.archetype)}</td>
      <td>${m.wins}/${m.simulations}</td>
      <td class="${rateClass(m.win_rate)}">${(m.win_rate * 100).toFixed(0)}%</td>
      <td>${m.avg_turns.toFixed(1)}</td>
    </tr>`).join("");

  els.gauntlet_result.innerHTML = `
    <div class="panel">
      <h2>Matchup gauntlet — ${r.total_games} games across ${r.matchups.length} archetypes</h2>
      <div class="sim-summary-grid">
        <div class="stat-card"><div class="label">Overall win rate</div><div class="value">${(r.overall_win_rate * 100).toFixed(0)}%</div></div>
        <div class="stat-card"><div class="label">Meta-weighted</div><div class="value">${(r.weighted_win_rate * 100).toFixed(0)}%</div><div class="sub">weighted by meta share</div></div>
        <div class="stat-card"><div class="label">Best matchup</div><div class="value" style="font-size:1rem">${escape(r.best_matchup)}</div><div class="sub">${(r.best_win_rate * 100).toFixed(0)}%</div></div>
        <div class="stat-card"><div class="label">Worst matchup</div><div class="value" style="font-size:1rem">${escape(r.worst_matchup)}</div><div class="sub">${(r.worst_win_rate * 100).toFixed(0)}%</div></div>
      </div>

      <div class="panel-row" style="gap:20px">
        <div style="flex:2">
          <h3>Per-archetype results</h3>
          <table class="gauntlet-table">
            <thead><tr><th>Archetype</th><th>Wins/Sims</th><th>Win %</th><th>Avg turns</th></tr></thead>
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
    </div>
  `;
  els.gauntlet_result.scrollIntoView({ behavior: "smooth", block: "start" });
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
    pollProgress("analyst_pull", () => {
      els.analyst_pull_btn.disabled = false;
      els.analyst_pull_status.textContent = "";
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
    toast("URL import failed: " + e.message, "error");
  } finally {
    els.url_import_btn.disabled = false;
    // Clear the status message after a few seconds so it doesn't linger
    setTimeout(() => { els.url_import_status.textContent = ""; }, 5000);
  }
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
window.addEventListener("DOMContentLoaded", bootstrap);
