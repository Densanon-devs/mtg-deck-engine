// Densa Deck — Build tab frontend.
//
// Three-column MTGA-style deckbuilder layered on top of the existing
// pywebview bridge. Shares callApi / toast / escape / $ from app.js
// (those are globals attached in bootstrap()) so this file doesn't
// duplicate utility code.
//
// Free users can build a draft; only Save is Pro-gated — the backend
// returns error_type="ProRequired" and we show a modal that preserves
// the draft. Autosave writes to ~/.densa-deck/drafts.json on a throttle
// so a mid-build crash can't lose the deck.

(function () {
  "use strict";

  // ---------------- state ----------------
  const builderState = {
    query: {
      name: "",
      colors: [],
      color_match: "identity",
      cmc_min: null,
      cmc_max: null,
      types: [],
      format_legal: "commander",
      rarity: "",
      max_price: null,
      set_code: "",
      limit: 60,
      offset: 0,
    },
    results: [],
    resultTotal: 0,
    deck: {
      name: "",
      format: "commander",
      mainboard: {}, // {cardName: {qty, cmc, type_line, colors, color_identity, is_land, is_creature, ...}}
      sideboard: {},
      commander: {}, // yes, dict — commander zone just has 1–2 entries but shape matches others
    },
    activeZone: "mainboard",
    dirty: false,
    saving: false,
    autosaveTimer: null,
    wired: false,
  };

  // Exposed for tests / debugging
  window.__builderState = builderState;

  // DOM shortcuts — resolved lazily so bootstrap() caches from app.js
  // run first. The Build view may not be rendered in early test harnesses.
  function e(id) { return document.getElementById(id); }

  // ---------------- initial wire + autosave ----------------

  function wireOnce() {
    if (builderState.wired) return;
    if (!e("view-build")) return; // not this build, skip

    // Search input — debounced on every keystroke
    const searchInput = e("build-search-input");
    if (searchInput) {
      searchInput.addEventListener("input", () => {
        builderState.query.name = searchInput.value;
        builderState.query.offset = 0;
        debouncedSearch();
      });
    }

    // Filter controls
    document.querySelectorAll(".filter-color").forEach(cb => {
      cb.addEventListener("change", () => {
        const picked = Array.from(document.querySelectorAll(".filter-color"))
          .filter(x => x.checked).map(x => x.value);
        builderState.query.colors = picked;
        builderState.query.offset = 0;
        debouncedSearch();
      });
    });
    document.querySelectorAll("input[name='color-match-mode']").forEach(r => {
      r.addEventListener("change", () => {
        builderState.query.color_match = r.value;
        builderState.query.offset = 0;
        debouncedSearch();
      });
    });
    const cmcMin = e("filter-cmc-min");
    const cmcMax = e("filter-cmc-max");
    if (cmcMin) cmcMin.addEventListener("input", () => {
      builderState.query.cmc_min = cmcMin.value === "" ? null : Number(cmcMin.value);
      builderState.query.offset = 0;
      debouncedSearch();
    });
    if (cmcMax) cmcMax.addEventListener("input", () => {
      builderState.query.cmc_max = cmcMax.value === "" ? null : Number(cmcMax.value);
      builderState.query.offset = 0;
      debouncedSearch();
    });
    const typeSel = e("filter-type");
    if (typeSel) typeSel.addEventListener("change", () => {
      builderState.query.types = Array.from(typeSel.selectedOptions).map(o => o.value);
      builderState.query.offset = 0;
      debouncedSearch();
    });
    const fmtSel = e("filter-format");
    if (fmtSel) fmtSel.addEventListener("change", () => {
      builderState.query.format_legal = fmtSel.value;
      builderState.query.offset = 0;
      debouncedSearch();
    });
    const raritySel = e("filter-rarity");
    if (raritySel) raritySel.addEventListener("change", () => {
      builderState.query.rarity = raritySel.value;
      builderState.query.offset = 0;
      debouncedSearch();
    });
    const maxPrice = e("filter-max-price");
    if (maxPrice) maxPrice.addEventListener("input", () => {
      builderState.query.max_price = maxPrice.value === "" ? null : Number(maxPrice.value);
      builderState.query.offset = 0;
      debouncedSearch();
    });

    const clearBtn = e("build-search-clear");
    if (clearBtn) clearBtn.addEventListener("click", clearFilters);

    const moreBtn = e("build-search-more-btn");
    if (moreBtn) moreBtn.addEventListener("click", loadMoreResults);

    // Deck controls
    const deckName = e("build-deck-name");
    if (deckName) deckName.addEventListener("input", () => {
      builderState.deck.name = deckName.value;
      scheduleAutosave();
    });
    const deckFmt = e("build-deck-format");
    if (deckFmt) deckFmt.addEventListener("change", () => {
      builderState.deck.format = deckFmt.value;
      // Also narrow the search's format filter to match — most users
      // expect the left column to self-scope when they pick a format.
      if (fmtSel) {
        fmtSel.value = deckFmt.value;
        builderState.query.format_legal = deckFmt.value;
        builderState.query.offset = 0;
        debouncedSearch();
      }
      scheduleAutosave();
      recomputeStats();
    });

    // Zone tabs
    document.querySelectorAll(".zone-tab").forEach(btn => {
      btn.addEventListener("click", () => setActiveZone(btn.dataset.zone));
    });

    // Save / suggest / export / clear
    const saveBtn = e("build-save-btn");
    if (saveBtn) saveBtn.addEventListener("click", saveDraftAsDeck);
    const suggestBtn = e("build-suggest-btn");
    if (suggestBtn) suggestBtn.addEventListener("click", openSuggestModal);
    const exportBtn = e("build-export-btn");
    if (exportBtn) exportBtn.addEventListener("click", openExportModal);
    const clearDeckBtn = e("build-clear-btn");
    if (clearDeckBtn) clearDeckBtn.addEventListener("click", clearDraft);

    // Suggest modal handlers
    const suggestClose = e("suggest-close-btn");
    const suggestDismiss = e("suggest-dismiss-btn");
    if (suggestClose) suggestClose.addEventListener("click", hideSuggestModal);
    if (suggestDismiss) suggestDismiss.addEventListener("click", hideSuggestModal);

    // Export modal handlers
    const exportClose = e("export-close-btn");
    const exportDismiss = e("export-dismiss-btn");
    const exportCopy = e("export-copy-btn");
    if (exportClose) exportClose.addEventListener("click", hideExportModal);
    if (exportDismiss) exportDismiss.addEventListener("click", hideExportModal);
    if (exportCopy) exportCopy.addEventListener("click", copyExportToClipboard);
    document.querySelectorAll(".export-tab").forEach(btn => {
      btn.addEventListener("click", () => switchExportTarget(btn.dataset.target));
    });

    // Pro gate modal
    const proCloseBtn = e("pro-gate-close-btn");
    const proDismissBtn = e("pro-gate-dismiss-btn");
    const proBuyBtn = e("pro-gate-buy-btn");
    if (proCloseBtn) proCloseBtn.addEventListener("click", hideProGate);
    if (proDismissBtn) proDismissBtn.addEventListener("click", hideProGate);
    if (proBuyBtn) proBuyBtn.addEventListener("click", () => {
      // Use the same open_external API used by product links so the
      // browser opens outside the webview.
      try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.open_external) {
          window.pywebview.api.open_external("https://toolkit.densanon.com/densa-deck.html");
        } else {
          window.open("https://toolkit.densanon.com/densa-deck.html", "_blank");
        }
      } catch (e) { /* non-fatal */ }
      hideProGate();
    });

    builderState.wired = true;
  }

  // ---------------- search ----------------

  let searchTimer = null;
  function debouncedSearch() {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => runSearch(false), 180);
  }

  async function runSearch(append) {
    const status = e("build-search-status");
    const moreBtn = e("build-search-more-btn");
    if (status) status.textContent = "Searching...";
    try {
      const q = Object.assign({}, builderState.query);
      const r = await callApi("search_cards", q);
      const cards = (r && r.cards) || [];
      builderState.resultTotal = (r && r.total) || 0;
      if (append) {
        builderState.results = builderState.results.concat(cards);
      } else {
        builderState.results = cards;
      }
      renderSearchResults();
      if (status) {
        status.textContent = builderState.resultTotal === 0
          ? "No matches"
          : `${builderState.results.length} / ${builderState.resultTotal}`;
      }
      if (moreBtn) {
        const hasMore = builderState.results.length < builderState.resultTotal;
        moreBtn.classList.toggle("hidden", !hasMore);
      }
    } catch (err) {
      if (status) status.textContent = "";
      if (typeof toast === "function") toast("Search failed: " + err.message, "error");
    }
  }

  async function loadMoreResults() {
    builderState.query.offset = builderState.results.length;
    await runSearch(true);
  }

  function renderSearchResults() {
    const host = e("build-search-results");
    if (!host) return;
    if (!builderState.results.length) {
      host.innerHTML = `<p class="panel-hint" style="grid-column:1/-1">No cards match the current filters. Try clearing one.</p>`;
      return;
    }
    host.innerHTML = "";
    for (const card of builderState.results) {
      host.appendChild(renderCardTile(card));
    }
  }

  function renderCardTile(card) {
    const tile = document.createElement("div");
    tile.className = "card-tile";
    tile.title = `${card.name} ${card.mana_cost || ""} — ${card.type_line || ""}`;

    const img = document.createElement("img");
    img.loading = "lazy";
    img.alt = card.name;
    img.src = card.image_url || "";
    img.onerror = () => {
      // Replace img with a text fallback when Scryfall is blocked or
      // offline. We keep the tile size stable so the grid doesn't reflow.
      img.remove();
      const fallback = document.createElement("div");
      fallback.className = "text-fallback";
      fallback.innerHTML = `
        <div>
          <div class="name">${escape(card.name)}</div>
          <div class="mana">${escape(card.mana_cost || "")}</div>
        </div>
        <div class="type">${escape(card.type_line || "")}</div>
      `;
      tile.insertBefore(fallback, tile.firstChild);
    };
    tile.appendChild(img);

    const add = document.createElement("button");
    add.className = "tile-add";
    add.type = "button";
    add.textContent = "+";
    add.title = "Add to deck";
    add.addEventListener("click", (ev) => {
      ev.stopPropagation();
      addToDeck(card);
    });
    tile.appendChild(add);

    tile.addEventListener("click", () => addToDeck(card));
    return tile;
  }

  function clearFilters() {
    builderState.query.name = "";
    builderState.query.colors = [];
    builderState.query.color_match = "identity";
    builderState.query.cmc_min = null;
    builderState.query.cmc_max = null;
    builderState.query.types = [];
    builderState.query.rarity = "";
    builderState.query.max_price = null;
    builderState.query.offset = 0;
    // Sync the DOM
    const si = e("build-search-input"); if (si) si.value = "";
    document.querySelectorAll(".filter-color").forEach(cb => { cb.checked = false; });
    const cmcMin = e("filter-cmc-min"); if (cmcMin) cmcMin.value = "";
    const cmcMax = e("filter-cmc-max"); if (cmcMax) cmcMax.value = "";
    const typeSel = e("filter-type"); if (typeSel) Array.from(typeSel.options).forEach(o => { o.selected = false; });
    const raritySel = e("filter-rarity"); if (raritySel) raritySel.value = "";
    const maxPrice = e("filter-max-price"); if (maxPrice) maxPrice.value = "";
    const idRadio = document.querySelector("input[name='color-match-mode'][value='identity']");
    if (idRadio) idRadio.checked = true;
    debouncedSearch();
  }

  // ---------------- deck mutations ----------------

  function addToDeck(card) {
    const zone = builderState.activeZone;
    const entries = builderState.deck[zone];
    const prev = entries[card.name];
    // Pull just the fields the deck rendering + stats need. Keeps the
    // in-memory deck object small (there's no reason to hold all 35k
    // of the full card shape per row).
    if (prev) {
      prev.qty += 1;
    } else {
      entries[card.name] = {
        qty: 1,
        cmc: card.cmc || 0,
        mana_cost: card.mana_cost || "",
        type_line: card.type_line || "",
        colors: card.colors || [],
        color_identity: card.color_identity || [],
        is_land: !!card.is_land,
        is_creature: !!card.is_creature,
        is_instant: !!card.is_instant,
        is_sorcery: !!card.is_sorcery,
        is_artifact: !!card.is_artifact,
        is_enchantment: !!card.is_enchantment,
        is_planeswalker: !!card.is_planeswalker,
        is_battle: !!card.is_battle,
        scryfall_id: card.scryfall_id,
      };
    }
    markDirty();
    renderDeck();
    recomputeStats();
  }

  function decrementCard(name) {
    const entries = builderState.deck[builderState.activeZone];
    const entry = entries[name];
    if (!entry) return;
    entry.qty -= 1;
    if (entry.qty <= 0) delete entries[name];
    markDirty();
    renderDeck();
    recomputeStats();
  }

  function removeCard(name) {
    const entries = builderState.deck[builderState.activeZone];
    if (entries[name]) delete entries[name];
    markDirty();
    renderDeck();
    recomputeStats();
  }

  function setActiveZone(zone) {
    builderState.activeZone = zone;
    document.querySelectorAll(".zone-tab").forEach(btn => {
      btn.classList.toggle("active", btn.dataset.zone === zone);
    });
    renderDeck();
  }

  function clearDraft() {
    // Soft reset — ask for confirmation only if the deck is not already
    // empty. window.confirm in some pywebview versions returns undefined
    // instead of a real bool; treat anything that's not strictly !== false
    // as "cancel" so a borked confirm() doesn't silently nuke the draft.
    const count = totalCardCount();
    if (count > 0) {
      let answered;
      try {
        answered = window.confirm(`Clear ${count} card${count === 1 ? "" : "s"} from the current draft?`);
      } catch (err) {
        answered = undefined;
      }
      if (answered !== true) return;
    }
    builderState.deck = { name: "", format: "commander", mainboard: {}, sideboard: {}, commander: {} };
    const nameInput = e("build-deck-name"); if (nameInput) nameInput.value = "";
    // Reset the load flag so a draft created later (e.g. restored from
    // backup, or written by a parallel session) is picked up next time
    // the user opens the Build tab. Without this, _draftLoaded sticks
    // at true and loadDraft becomes a no-op until app restart.
    builderState._draftLoaded = false;
    builderState.dirty = false;
    renderDeck();
    recomputeStats();
    // Drop the persisted draft so a next-launch restore doesn't bring it back.
    try { callApi("clear_builder_draft"); } catch (err) { /* non-fatal */ }
  }

  // ---------------- deck rendering ----------------

  const TYPE_ORDER = [
    ["Creatures", e => e.is_creature],
    ["Instants", e => e.is_instant],
    ["Sorceries", e => e.is_sorcery],
    ["Artifacts", e => e.is_artifact && !e.is_creature],
    ["Enchantments", e => e.is_enchantment && !e.is_creature],
    ["Planeswalkers", e => e.is_planeswalker],
    ["Battles", e => e.is_battle],
    ["Lands", e => e.is_land],
    ["Other", () => true],
  ];

  function renderDeck() {
    const host = e("build-deck-body");
    if (!host) return;
    const entries = builderState.deck[builderState.activeZone];
    const names = Object.keys(entries);
    // Count summary
    const totalCount = names.reduce((a, n) => a + entries[n].qty, 0);
    const countEl = e("build-deck-count");
    if (countEl) countEl.textContent = `${totalCount} card${totalCount === 1 ? "" : "s"} in ${builderState.activeZone}`;
    const titleEl = e("build-deck-title");
    if (titleEl) titleEl.textContent = builderState.deck.name || "Untitled deck";
    const validityEl = e("build-deck-validity");
    if (validityEl) validityEl.innerHTML = renderValidity();

    if (!names.length) {
      host.innerHTML = `<p class="panel-hint">No cards in ${builderState.activeZone}. Click a card on the left to add it.</p>`;
      return;
    }
    // Group by primary type
    const groups = [];
    const placed = new Set();
    for (const [groupLabel, pred] of TYPE_ORDER) {
      const groupNames = names.filter(n => !placed.has(n) && pred(entries[n]));
      groupNames.forEach(n => placed.add(n));
      if (!groupNames.length) continue;
      groupNames.sort((a, b) => {
        const da = entries[a], db = entries[b];
        if (da.cmc !== db.cmc) return da.cmc - db.cmc;
        return a.localeCompare(b);
      });
      groups.push({ label: groupLabel, names: groupNames });
    }

    host.innerHTML = groups.map(g => `
      <div class="deck-type-group">
        <div class="deck-type-group-header">${escape(g.label)} (${g.names.reduce((a, n) => a + entries[n].qty, 0)})</div>
        ${g.names.map(n => {
          const ent = entries[n];
          return `
            <div class="deck-row">
              <div class="qty-controls">
                <button class="qty-btn" data-act="dec" data-name="${escape(n)}" title="Remove one">−</button>
                <span class="qty-value">${ent.qty}</span>
                <button class="qty-btn" data-act="inc" data-name="${escape(n)}" title="Add one">+</button>
              </div>
              <span class="card-name">${escape(n)}</span>
              <span class="card-cmc" title="${escape(ent.mana_cost || "")}">${ent.cmc || 0}</span>
            </div>
          `;
        }).join("")}
      </div>
    `).join("");

    // Delegated click handler — the qty buttons carry data-act so we
    // don't need a per-button listener (faster on large decks).
    host.onclick = (ev) => {
      const btn = ev.target.closest(".qty-btn");
      if (!btn) return;
      const name = btn.dataset.name;
      if (btn.dataset.act === "inc") {
        // Re-use the same shape — we don't have the full card object
        // at this point, only the stored entry fields, so build a
        // minimal Card-shaped payload and route through addToDeck.
        const ent = entries[name];
        addToDeck(Object.assign({ name }, ent));
      } else if (btn.dataset.act === "dec") {
        decrementCard(name);
      }
    };
  }

  function renderValidity() {
    const total = totalCardCount();
    const fmt = builderState.deck.format || "commander";
    // Commander-specific: 100-card singleton, exactly one commander.
    if (fmt === "commander") {
      const main = Object.values(builderState.deck.mainboard).reduce((a, e) => a + e.qty, 0);
      const cmdr = Object.values(builderState.deck.commander).reduce((a, e) => a + e.qty, 0);
      const combined = main + cmdr;
      const issues = [];
      if (combined !== 100) issues.push(`${combined}/100`);
      if (cmdr !== 1 && cmdr !== 2) issues.push(`${cmdr} commanders (want 1)`);
      if (!issues.length) return `<span class="validity-ok">legal: 100 singleton</span>`;
      return `<span class="validity-bad">${issues.join(" &middot; ")}</span>`;
    }
    // Generic format: 60-card minimum mainboard.
    const main = Object.values(builderState.deck.mainboard).reduce((a, e) => a + e.qty, 0);
    if (main < 60) return `<span class="validity-bad">${main}/60 mainboard</span>`;
    return `<span class="validity-ok">${main} mainboard</span>`;
  }

  function totalCardCount() {
    return ["mainboard", "sideboard", "commander"].reduce((a, z) => {
      return a + Object.values(builderState.deck[z]).reduce((b, e) => b + e.qty, 0);
    }, 0);
  }

  // ---------------- stats ----------------

  let statsTimer = null;
  function recomputeStats() {
    if (statsTimer) clearTimeout(statsTimer);
    statsTimer = setTimeout(renderStats, 150);
  }

  function renderStats() {
    const host = e("build-stats-body");
    if (!host) return;
    const mb = builderState.deck.mainboard;
    const cmdr = builderState.deck.commander;
    const names = Object.keys(mb).concat(Object.keys(cmdr));
    if (!names.length) {
      host.innerHTML = `<p class="panel-hint">Add cards to see live stats.</p>`;
      return;
    }
    // Mana curve (mainboard non-land only)
    const curve = [0, 0, 0, 0, 0, 0, 0, 0];
    let landCount = 0, creatureCount = 0;
    let totalNonLandCmc = 0, totalNonLandCards = 0;
    const pipCounts = { W: 0, U: 0, B: 0, R: 0, G: 0 };
    for (const n of names) {
      const ent = mb[n] || cmdr[n];
      if (ent.is_land) { landCount += ent.qty; continue; }
      if (ent.is_creature) creatureCount += ent.qty;
      const bucket = Math.min(7, Math.max(0, Math.round(ent.cmc || 0)));
      curve[bucket] += ent.qty;
      totalNonLandCmc += (ent.cmc || 0) * ent.qty;
      totalNonLandCards += ent.qty;
      // Pip counting from mana_cost string. Counts each colored pip — a
      // hybrid pip like {W/U} contributes 1 to each of W and U. Phyrexian
      // {W/P} also counts as 1 W. Generic {2} / {X} pips don't count.
      // Use a tokenizer rather than per-color regex so {W/U} is matched
      // exactly once and contributes to both colors symmetrically.
      const mc = ent.mana_cost || "";
      const tokenRe = /\{([^}]+)\}/g;
      let m;
      while ((m = tokenRe.exec(mc)) !== null) {
        const tok = m[1].toUpperCase();
        for (const c of ["W", "U", "B", "R", "G"]) {
          // A token contributes to color c if c appears between non-letter
          // boundaries — covers {W}, {W/U}, {2/W}, {W/P}, {W/U/P}.
          if (new RegExp(`(^|[^A-Z])${c}([^A-Z]|$)`).test(tok)) {
            pipCounts[c] += ent.qty;
          }
        }
      }
    }
    const totalCurve = curve.reduce((a, b) => a + b, 0) || 1;
    const maxBucket = Math.max.apply(null, curve) || 1;
    const curveBars = curve.map((count, i) => {
      const label = i < 7 ? `${i}` : "7+";
      const pct = Math.round((count / maxBucket) * 100);
      return `<div class="bar" style="height:${pct}%" title="MV ${label}: ${count}"><span class="count">${count || ""}</span></div>`;
    }).join("");
    const curveLabels = Array.from({length: 8}, (_, i) => `<span>${i < 7 ? i : "7+"}</span>`).join("");

    const avgCmc = totalNonLandCards > 0 ? (totalNonLandCmc / totalNonLandCards).toFixed(2) : "0.00";
    const pipChips = Object.entries(pipCounts)
      .filter(([, n]) => n > 0)
      .map(([c, n]) => `<span class="pip-chip">{${c}} × ${n}</span>`)
      .join("") || `<span class="panel-hint">(none)</span>`;

    host.innerHTML = `
      <div class="stat-block">
        <h3>Mana curve (non-lands)</h3>
        <div class="mini-curve">${curveBars}</div>
        <div class="mini-curve label-row">${curveLabels}</div>
      </div>
      <div class="stat-block">
        <h3>Totals</h3>
        <div>Lands: <strong>${landCount}</strong></div>
        <div>Creatures: <strong>${creatureCount}</strong></div>
        <div>Avg mana value: <strong>${avgCmc}</strong></div>
        <div>Non-lands in curve: <strong>${totalCurve}</strong></div>
      </div>
      <div class="stat-block">
        <h3>Mana pips</h3>
        <div class="pip-row">${pipChips}</div>
      </div>
      <div class="stat-block" id="builder-combos-block">
        <h3>Combos</h3>
        <div id="builder-combos-body" class="panel-hint">Detecting…</div>
      </div>
    `;
    // Combo detection runs against the current draft and the cached
    // Commander Spellbook dataset. Debounced so rapid +/- clicks don't
    // fire 30 detection calls.
    scheduleComboDetect();
  }

  let comboTimer = null;
  function scheduleComboDetect() {
    if (comboTimer) clearTimeout(comboTimer);
    comboTimer = setTimeout(detectBuilderCombos, 600);
  }

  function updateBuilderComboBadge(count) {
    const badge = e("build-deck-combo-badge");
    if (!badge) return;
    if (!count) {
      badge.classList.add("hidden");
      return;
    }
    badge.textContent = `${count} combo${count === 1 ? "" : "s"}`;
    badge.classList.remove("hidden");
    // Scroll the combos block into view when the badge is clicked. Wired
    // each render so the live badge value is correct on click.
    badge.onclick = () => {
      const block = e("builder-combos-block");
      if (block) block.scrollIntoView({ behavior: "smooth", block: "nearest" });
    };
  }

  async function detectBuilderCombos() {
    const body = e("builder-combos-body");
    if (!body) return;
    const text = draftToDecklistText();
    // Empty draft — show a hint and skip the call
    if (totalCardCount() === 0) {
      body.innerHTML = `<span class="panel-hint">Add cards to scan for combos.</span>`;
      updateBuilderComboBadge(0);
      return;
    }
    try {
      const r = await callApi(
        "detect_combos_for_deck",
        text, builderState.deck.format,
        builderState.deck.name || "Untitled deck",
        10,  // small limit — Build tab is a sidebar, not a full panel
      );
      if (!r || r.match_count === 0) {
        body.innerHTML = `<span class="panel-hint">No combos detected.</span>`;
        updateBuilderComboBadge(0);
        return;
      }
      const top = (r.combos || []).slice(0, 5).map(c => `
        <li title="${escape(c.short_label)}">${escape(c.short_label)}</li>
      `).join("");
      const more = r.match_count > 5 ? `<div class="panel-hint">+${r.match_count - 5} more</div>` : "";
      body.innerHTML = `
        <div><strong>${r.match_count}</strong> combo${r.match_count === 1 ? "" : " lines"} detected</div>
        <ul style="margin: 6px 0 0 20px; padding: 0; font-size: 0.78rem; line-height: 1.4;">${top}</ul>
        ${more}
      `;
      updateBuilderComboBadge(r.match_count);
    } catch (err) {
      // ComboCacheEmpty / IngestRequired surface as errors — show
      // a helpful hint instead of an alarming red message.
      const msg = (err && err.message) || "";
      if (msg.toLowerCase().includes("combo data not loaded")) {
        body.innerHTML = `<span class="panel-hint">Combo cache empty — refresh on Settings tab.</span>`;
      } else if (msg.toLowerCase().includes("ingestrequired") || msg.toLowerCase().includes("not ingested")) {
        body.innerHTML = `<span class="panel-hint">Card DB not ingested — see Settings.</span>`;
      } else {
        body.innerHTML = `<span class="panel-hint">Combo detection unavailable.</span>`;
      }
      updateBuilderComboBadge(0);
    }
  }

  // ---------------- persistence ----------------

  function markDirty() {
    builderState.dirty = true;
    scheduleAutosave();
  }

  function scheduleAutosave() {
    if (builderState.autosaveTimer) clearTimeout(builderState.autosaveTimer);
    builderState.autosaveTimer = setTimeout(saveDraftSilently, 2000);
  }

  async function saveDraftSilently() {
    if (!builderState.dirty) return;
    try {
      await callApi("save_builder_draft", serializeDraft());
      builderState.dirty = false;
    } catch (err) {
      // Toasting on autosave-fail is noisy. Swallow and rely on the
      // explicit save button's error path to surface problems.
    }
  }

  function serializeDraft() {
    return {
      name: builderState.deck.name,
      format: builderState.deck.format,
      mainboard: Object.fromEntries(
        Object.entries(builderState.deck.mainboard).map(([n, v]) => [n, v]),
      ),
      sideboard: Object.fromEntries(
        Object.entries(builderState.deck.sideboard).map(([n, v]) => [n, v]),
      ),
      commander: Object.fromEntries(
        Object.entries(builderState.deck.commander).map(([n, v]) => [n, v]),
      ),
      saved_at: new Date().toISOString(),
    };
  }

  async function loadDraft() {
    try {
      const data = await callApi("load_builder_draft");
      if (!data || typeof data !== "object") return;
      builderState.deck.name = data.name || "";
      builderState.deck.format = data.format || "commander";
      builderState.deck.mainboard = data.mainboard || {};
      builderState.deck.sideboard = data.sideboard || {};
      builderState.deck.commander = data.commander || {};
      const nameInput = e("build-deck-name"); if (nameInput) nameInput.value = builderState.deck.name;
      const fmtInput = e("build-deck-format"); if (fmtInput) fmtInput.value = builderState.deck.format;
      renderDeck();
      recomputeStats();
    } catch (err) {
      // Non-fatal — missing/corrupt draft returns null, caller already
      // handles the null case upstream. Only real bridge failures land here.
    }
  }

  // ---------------- Save as deck (Pro-gated) ----------------

  async function saveDraftAsDeck() {
    if (builderState.saving) return;
    const name = (builderState.deck.name || "").trim();
    if (!name) {
      if (typeof toast === "function") toast("Name your deck first.", "error");
      const nameInput = e("build-deck-name"); if (nameInput) nameInput.focus();
      return;
    }
    if (totalCardCount() === 0) {
      if (typeof toast === "function") toast("Add at least one card first.", "error");
      return;
    }

    // Slugify the deck name for deck_id — same pattern as the analyze tab's
    // saveFromAnalyzeTab. Must be safe-ish for sqlite; strip non-alnum.
    const deckId = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "") || "deck";
    const decklistText = draftToDecklistText();

    builderState.saving = true;
    const saveBtn = e("build-save-btn");
    if (saveBtn) saveBtn.disabled = true;
    const statusEl = e("build-save-status");
    if (statusEl) statusEl.textContent = "Saving...";

    try {
      const r = await window.pywebview.api.save_builder_as_deck(
        deckId, name, builderState.deck.format, decklistText, "Created in Build tab",
      );
      if (r && r.ok === false) {
        if (r.error_type === "ProRequired") {
          showProGate();
          if (statusEl) statusEl.textContent = "";
          return;
        }
        throw new Error(r.error || "save failed");
      }
      if (statusEl) statusEl.textContent = "Saved.";
      if (typeof toast === "function") toast("Deck saved — available on the My Decks tab.", "success");
      // Drop the draft; the deck is now tracked via VersionStore.
      try { await callApi("clear_builder_draft"); } catch (e2) { /* non-fatal */ }
    } catch (err) {
      if (statusEl) statusEl.textContent = "";
      if (typeof toast === "function") toast("Save failed: " + err.message, "error");
    } finally {
      builderState.saving = false;
      if (saveBtn) saveBtn.disabled = false;
      setTimeout(() => { if (statusEl) statusEl.textContent = ""; }, 3500);
    }
  }

  function draftToDecklistText() {
    // Build in the same pasteable format the parser expects so the
    // backend's save path doesn't need a new code path — same parser,
    // resolver, and version-store write as a pasted decklist.
    const lines = [];
    const zones = [
      ["Commander", builderState.deck.commander],
      ["Mainboard", builderState.deck.mainboard],
      ["Sideboard", builderState.deck.sideboard],
    ];
    for (const [zoneLabel, entries] of zones) {
      const names = Object.keys(entries);
      if (!names.length) continue;
      lines.push(`${zoneLabel}:`);
      names.sort().forEach(n => {
        lines.push(`${entries[n].qty} ${n}`);
      });
      lines.push("");
    }
    return lines.join("\n").trim() + "\n";
  }

  // ---------------- Suggest adds (Pro AI deckbuild) ----------------

  async function openSuggestModal() {
    const m = e("suggest-modal");
    const list = e("suggest-list");
    const meta = e("suggest-meta");
    if (!m) return;
    m.classList.remove("hidden");
    m.setAttribute("aria-hidden", "false");
    list.innerHTML = "";
    meta.textContent = "Loading suggestions...";
    if (totalCardCount() === 0) {
      meta.textContent = "Add at least one card to the deck so we have something to ground suggestions in.";
      return;
    }
    try {
      const r = await window.pywebview.api.suggest_deckbuild_additions(
        draftToDecklistText(),
        builderState.deck.format,
        builderState.deck.name || "Untitled deck",
        8, null,
      );
      if (r && r.ok === false) {
        if (r.error_type === "ProRequired") {
          hideSuggestModal();
          showProGate();
          return;
        }
        meta.textContent = "Error: " + (r.error || "(unknown)");
        return;
      }
      const data = r && r.data ? r.data : r;
      const gaps = (data.gaps || []).join(", ") || "no gaps detected";
      meta.textContent = `${data.count} suggestions • role gaps: ${gaps}`;
      list.innerHTML = (data.suggestions || []).map((s, i) => `
        <div class="suggest-row" data-name="${escape(s.name)}" data-cmc="${s.cmc}" data-mc="${escape(s.mana_cost)}" data-tl="${escape(s.type_line)}">
          <div class="suggest-rank">#${i + 1}</div>
          <div class="suggest-card">
            <div class="suggest-name">${escape(s.name)} <span class="status-text">${escape(s.mana_cost || "")}</span></div>
            <div class="status-text">${escape(s.type_line || "")} &middot; ${escape(s.role || "")}</div>
            <div class="status-text" style="margin-top:2px">${escape(s.reason || "")}</div>
          </div>
          <button class="btn btn-primary btn-slim suggest-add-btn">+ Add</button>
        </div>
      `).join("") || `<p class="panel-hint">No suggestions surfaced. Try refreshing combo data on Settings or save the draft and run Analyze for richer signals.</p>`;
      // Wire each row's add button.
      list.querySelectorAll(".suggest-add-btn").forEach(btn => {
        btn.addEventListener("click", () => {
          const row = btn.closest(".suggest-row");
          if (!row) return;
          const name = row.dataset.name;
          if (!name) return;
          // Build a card-shaped object minimal enough for addToDeck —
          // type-line shapes the bucket assignment in renderDeck.
          const tl = row.dataset.tl || "";
          const card = {
            name,
            mana_cost: row.dataset.mc || "",
            cmc: parseFloat(row.dataset.cmc) || 0,
            type_line: tl,
            colors: [], color_identity: [],
            is_creature: /Creature/i.test(tl),
            is_instant: /Instant/i.test(tl),
            is_sorcery: /Sorcery/i.test(tl),
            is_artifact: /Artifact/i.test(tl),
            is_enchantment: /Enchantment/i.test(tl),
            is_planeswalker: /Planeswalker/i.test(tl),
            is_battle: /Battle/i.test(tl),
            is_land: /Land/i.test(tl),
          };
          addToDeck(card);
          btn.textContent = "Added";
          btn.disabled = true;
        });
      });
    } catch (err) {
      meta.textContent = "Failed: " + err.message;
    }
  }

  function hideSuggestModal() {
    const m = e("suggest-modal");
    if (!m) return;
    m.classList.add("hidden");
    m.setAttribute("aria-hidden", "true");
  }

  // ---------------- Export modal (MTGO / MTGA / Moxfield) ----------------

  let _exportTarget = "mtga";

  async function openExportModal() {
    if (totalCardCount() === 0) {
      if (typeof toast === "function") toast("Add at least one card before exporting.", "error");
      return;
    }
    const m = e("export-modal");
    if (!m) return;
    m.classList.remove("hidden");
    m.setAttribute("aria-hidden", "false");
    _exportTarget = "mtga";
    document.querySelectorAll(".export-tab").forEach(btn => {
      btn.classList.toggle("active", btn.dataset.target === _exportTarget);
    });
    await loadExportContent();
  }

  function hideExportModal() {
    const m = e("export-modal");
    if (!m) return;
    m.classList.add("hidden");
    m.setAttribute("aria-hidden", "true");
  }

  async function switchExportTarget(target) {
    _exportTarget = target;
    document.querySelectorAll(".export-tab").forEach(btn => {
      btn.classList.toggle("active", btn.dataset.target === _exportTarget);
    });
    await loadExportContent();
  }

  async function loadExportContent() {
    const ta = e("export-text");
    if (!ta) return;
    ta.textContent = "(loading…)";
    try {
      const r = await callApi(
        "export_deck_format",
        draftToDecklistText(), _exportTarget,
        builderState.deck.format,
        builderState.deck.name || "Untitled deck",
      );
      ta.textContent = r.content || "(empty)";
    } catch (err) {
      ta.textContent = "Error: " + err.message;
    }
  }

  function copyExportToClipboard() {
    const ta = e("export-text");
    const btn = e("export-copy-btn");
    if (!ta || !btn) return;
    const orig = btn.textContent;
    try {
      navigator.clipboard.writeText(ta.textContent).then(() => {
        btn.textContent = "Copied!";
        setTimeout(() => { btn.textContent = orig; }, 1500);
      }).catch(() => {
        // Fallback — select the pre's contents so the user can Ctrl+C.
        const r = document.createRange();
        r.selectNode(ta);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(r);
        btn.textContent = "Selected — Ctrl+C";
        setTimeout(() => { btn.textContent = orig; }, 2000);
      });
    } catch (err) { /* non-fatal */ }
  }

  function showProGate() {
    const m = e("pro-gate-modal");
    if (!m) return;
    m.classList.remove("hidden");
    m.setAttribute("aria-hidden", "false");
  }
  function hideProGate() {
    const m = e("pro-gate-modal");
    if (!m) return;
    m.classList.add("hidden");
    m.setAttribute("aria-hidden", "true");
  }

  // ---------------- Build view entry point ----------------

  async function onBuildViewActivated() {
    wireOnce();
    // Load any in-progress draft the first time the tab is opened.
    if (!builderState._draftLoaded) {
      builderState._draftLoaded = true;
      await loadDraft();
    }
    renderDeck();
    recomputeStats();
    // Fire an initial search if the results pane is empty so the user
    // sees something to click on without having to type first.
    if (!builderState.results.length) {
      // Pull 60 Commander-legal cards with the default filters.
      debouncedSearch();
    }
  }

  // Hook into the existing switchView() in app.js — it calls `refresh*`
  // functions for known views. We monkey-patch by wrapping.
  const origSwitchView = window.__tourSwitchView;
  window.__tourSwitchView = function (view) {
    if (origSwitchView) origSwitchView(view);
    if (view === "build") onBuildViewActivated();
  };

  // Also hook the click handler — if the user clicks the Build tab
  // button directly (not through tour.js), app.js calls switchView()
  // which doesn't exist on window. Fall back to a DOM listener.
  window.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll('.tab-btn[data-view="build"]').forEach(btn => {
      btn.addEventListener("click", () => {
        // Debounce the activation so it runs AFTER app.js's switchView
        // has had a chance to flip the view active class.
        setTimeout(onBuildViewActivated, 0);
      });
    });
  });

  // Autosave on page unload — best-effort. Modern browsers often cancel
  // async work during unload, but pywebview routes through window.close
  // which fires our Python-side close() that closes the SQLite handles
  // cleanly regardless.
  window.addEventListener("beforeunload", () => {
    if (builderState.dirty) {
      try { saveDraftSilently(); } catch (e2) {}
    }
  });

  // Expose a handful of internals for tests / future hooks.
  window.__builderAPI = {
    addToDeck,
    decrementCard,
    removeCard,
    setActiveZone,
    clearDraft,
    recomputeStats,
    draftToDecklistText,
    serializeDraft,
  };
})();
