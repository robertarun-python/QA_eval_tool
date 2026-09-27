// ==================== HR ====================

function setPageHeader(eyebrow, title, subtitle) {
  document.getElementById("page-eyebrow").textContent = eyebrow || "";
  document.getElementById("page-title").textContent = title;
  document.getElementById("page-subtitle").textContent = subtitle || "";
  // Every HR round/page switch and every candidate round switch routes
  // through here (see renderHRRoundNav/renderCandidateRoundNav) - the
  // single choke point for "the page changed." .app-main is one
  // persistent scrolling element whose content just gets swapped, not
  // recreated, so without this its scrollTop carries over from
  // whatever the previous page was scrolled to - a new page can open
  // already scrolled halfway down purely because the last one was.
  const main = document.querySelector(".app-main");
  if (main) main.scrollTop = 0;
}

// Icons for the rail's non-round items - the round ticks already have
// their number, but these three were text-only, so the tablet rail
// (labels hidden, see style.css's 68rem breakpoint) showed them blank.
const HR_RAIL_ICONS = {
  candidates: '<svg class="index-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
  settings: '<svg class="index-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg>',
  progressive: '<svg class="index-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 3h6"/><path d="M10 3v6L4.5 19a1.5 1.5 0 0 0 1.3 2h12.4a1.5 1.5 0 0 0 1.3-2L14 9V3"/></svg>',
};

function railIndexItem(page, label, icon) {
  const active = hrPage === page;
  return `<button class="index-item has-icon ${active ? "active" : ""}" ${active ? 'aria-current="page"' : ""} onclick="selectHRPage('${page}')" title="${label}">${icon}<span>${label}</span></button>`;
}

function renderHRRoundNav() {
  const nav = document.getElementById("hr-round-nav");
  // HR authors rounds in any order - no done/locked state here, just
  // which one's open right now (see the candidate nav below for the
  // sequence-with-real-state version of this same rail language).
  nav.innerHTML = `
    <div class="rail-section-label">Author scenarios</div>
    <nav class="tick-rail">
      ${[1, 2, 3, 4].map((n) => `
        <button class="tick ${hrPage === "rounds" && n === currentHRRound ? "active" : ""}" ${hrPage === "rounds" && n === currentHRRound ? 'aria-current="page"' : ""} onclick="selectHRRound(${n})" title="Round ${n} · ${ROUND_LABELS[n]}">
          <span class="tick-num">${n}</span>
          <span class="tick-label">${ROUND_LABELS[n]}</span>
        </button>
      `).join("")}
    </nav>
    <div class="rail-divider">
      <div class="rail-section-label">Reporting</div>
      ${railIndexItem("candidates", "Candidates", HR_RAIL_ICONS.candidates)}
      ${railIndexItem("settings", "Settings", HR_RAIL_ICONS.settings)}
    </div>
    <div class="rail-divider">
      <div class="rail-section-label">Experimental</div>
      ${railIndexItem("progressive", "Progressive Engineering", HR_RAIL_ICONS.progressive)}
    </div>
  `;

  document.getElementById("hr-page-rounds").classList.toggle("hidden", hrPage !== "rounds");
  document.getElementById("hr-page-candidates").classList.toggle("hidden", hrPage !== "candidates");
  document.getElementById("hr-page-settings").classList.toggle("hidden", hrPage !== "settings");
  document.getElementById("hr-page-progressive").classList.toggle("hidden", hrPage !== "progressive");

  if (hrPage === "rounds") {
    setPageHeader("HR Console", `Round ${currentHRRound} · ${ROUND_LABELS[currentHRRound]}`, "Author, review, and publish scenarios for this round.");
    document.getElementById("hr-round-context").textContent = `Define the scenario details and generate a reference solution for Round ${currentHRRound} (${ROUND_LABELS[currentHRRound]}).`;
    document.getElementById("hr-breadcrumb").innerHTML =
      `<span>Scenarios</span> <span aria-hidden="true">&rsaquo;</span> <span class="breadcrumb-current">Round ${currentHRRound} - ${ROUND_LABELS[currentHRRound]}</span>`;
  } else if (hrPage === "candidates") {
    setPageHeader("HR Console", "Candidates", "Every candidate's progress and results, across all rounds.");
  } else if (hrPage === "progressive") {
    setPageHeader("HR Console", "Progressive Engineering", "Experimental (POC) - author multi-stage problems, separate from Rounds 1-4.");
    loadProgressiveProblems();
  } else {
    setPageHeader("HR Console", "Settings", "Configure evaluation criteria and application behavior.");
  }
  // Every hrPage/currentHRRound change routes through here (selectHRRound,
  // selectHRPage, and the restore call in onLoggedIn) - persisting once
  // here instead of at each call site means a refresh always lands back
  // on whichever page/round was actually last showing.
  saveHRNavState();
}

function selectHRRound(n) {
  closeCandidateDetail({ silent: true });
  currentHRRound = n;
  hrPage = "rounds";
  renderHRRoundNav();
  showHRRoundPanels();
}

// Shows the panels that belong to currentHRRound and loads them. Also
// called straight after login (core.js onLoggedIn), where a refresh lands
// back on the round HR last had open - without it, a refresh on Round 2
// showed Round 1's scenario library (Create + Review) for Round 2
// scenarios instead of the Round 2 settings card.
function showHRRoundPanels() {
  const n = currentHRRound;
  // Defensive reset, same panels closeScenarioDetail restores - if a
  // scenario's detail was left open when HR switched rounds, its
  // hidden/full-width state shouldn't follow them to a round they
  // haven't opened anything in yet.
  closeScenarioDetail();

  // The automation round sits at slot 2 since the 2<->4 renumbering; the
  // identifier keeps its historical name (see scoring_service._SCORERS).
  const isRound2Automation = n === 2;
  // It gets its own single settings view instead of the author/review/
  // publish flow - see loadRound2AutomationSettings for why none of that maps onto
  // this round's actual shape (no fixed reference, no meaningfully
  // different "versions" to browse or compare).
  document.getElementById("create-scenario-row").classList.toggle("hidden", isRound2Automation);
  document.getElementById("screening-history-panel").classList.toggle("hidden", isRound2Automation);
  document.getElementById("round4-settings-panel").classList.toggle("hidden", !isRound2Automation);
  // #scenario-kpis sits outside create-scenario-row (so it reads as part
  // of the page, not nested inside the 2-column workspace) - it needs
  // its own hide, or it'd keep showing whichever round's counts were
  // last loaded instead of disappearing along with the rest of the
  // round 1-3 scenario-library UI.
  document.getElementById("scenario-kpis").classList.toggle("hidden", isRound2Automation);
  // Round 3's guardrail reference (see index.html) - static, no API call,
  // just shown/hidden alongside the rest of this round's panels.
  document.getElementById("round3-guardrails-panel").classList.toggle("hidden", n !== 3);

  if (isRound2Automation) {
    loadRound2AutomationSettings();
  } else {
    resetCreateScenarioForm();
    loadScenarios();
    loadHistory();
  }
}

// The "Create a scenario" fields are one static, always-mounted form
// (see index.html) shared by all three rounds - createScenario() reads
// whatever's currently in them, tagged with whichever round is selected
// at that moment (currentHRRound). Switching rounds used to leave
// whatever HR had typed sitting there untouched, so a time limit (or
// band, or half-written title/description) set while looking at one
// round would silently carry over and get used for a scenario created
// under a completely different round - not a shared value in the
// database, just a stale, easy-to-miss leftover in a shared input.
function resetCreateScenarioForm() {
  document.getElementById("s-time-limit").value = "30";
  document.getElementById("s-title").value = "";
  document.getElementById("s-desc").value = "";
  document.getElementById("s-desc").placeholder = ROUND_DESC_PLACEHOLDERS[currentHRRound] || "";
  updateCreateBtnState();
}

function selectHRPage(page) {
  // Leaving the candidate detail view for any HR page - including
  // Candidates itself, which lands back on the list.
  closeCandidateDetail({ silent: true });
  hrPage = page;
  renderHRRoundNav();
}

// The create form is always visible (left column, not a modal/drawer -
// see index.html's card-grid), so "New Scenario" has nothing to open;
// this just scrolls/focuses it, same convenience a jump link gives.
function focusCreateScenarioForm() {
  const title = document.getElementById("s-title");
  title.scrollIntoView({ behavior: "smooth", block: "center" });
  title.focus();
}

// HR Settings' time limit for a round, or null when each scenario uses its own.
function roundTimeLimitSetting(roundNumber) {
  return (appSettings && appSettings[`round${roundNumber}_time_limit_minutes`]) || null;
}
function roundTimeLimitNote(roundNumber) {
  return `Time limit: ${roundTimeLimitSetting(roundNumber)} minutes - set for every Round ${roundNumber} scenario in HR Settings. Candidates already mid-round keep the limit they started with.`;
}

// ---- Settings (runtime-editable pass criteria - see GET/PUT /hr/settings) ----

async function loadAppSettings() {
  appSettings = await api("/hr/settings");
  document.getElementById("set-round1").value = appSettings.round1_passing_score;
  document.getElementById("set-round2").value = appSettings.round2_passing_score;
  document.getElementById("set-round3").value = appSettings.round3_passing_score;
  document.getElementById("set-round4").value = appSettings.round4_passing_score;
  document.getElementById("set-final").value = appSettings.final_passing_score;
  document.getElementById("set-window").value = appSettings.reapplication_window_months;
  document.getElementById("set-assessment-window").value = appSettings.assessment_window_days;
  for (const n of [1, 2, 3, 4]) document.getElementById(`set-time-r${n}`).value = appSettings[`round${n}_time_limit_minutes`] ?? "";
  loadScenarios();
  loadAiHealth();  // the list's time column shows the round limit - loads in parallel at login
}

// Purely a form reset - no API call, no new backend capability. Fills
// the same fields saveAppSettings() already reads with the AppSettings
// model's own column defaults (see models.py), so HR still has to click
// "Save changes" to actually persist them, exactly like typing new
// values in by hand. Not calling PUT here on its own keeps this a
// reversible preview, not a silent, one-click factory reset.
function resetSettingsToDefaults() {
  document.getElementById("set-round1").value = 70;
  document.getElementById("set-round2").value = 70;
  document.getElementById("set-round3").value = 70;
  document.getElementById("set-round4").value = 70;
  document.getElementById("set-final").value = 280;
  document.getElementById("set-window").value = 6;
  document.getElementById("set-assessment-window").value = 1;
  for (const n of [1, 2, 3, 4]) document.getElementById(`set-time-r${n}`).value = "";  // default: each scenario's own
  document.getElementById("settings-status").textContent = "Defaults filled in - click \"Save changes\" to apply.";
}

// The recorded AI spend (kept across restarts) on the AI health card: totals,
// then per day, per round and the costliest candidates.
function aiBudgetHtml(s) {
  if (!s || s.monthly_limit_usd === undefined) return "";
  const used = s.monthly_limit_usd ? s.month_usd / s.monthly_limit_usd : 1;
  const note = used >= 1 ? `<span class="error-text">Limit reached - no new AI work starts until it is raised or the month ends.</span>`
    : used >= 0.8 ? `<span class="error-text">Over 80% of the limit used.</span>` : "";
  return `
    <p>This month's AI spend: <strong>$${Number(s.month_usd).toFixed(2)}</strong> of the <strong>$${Number(s.monthly_limit_usd).toFixed(2)}</strong> monthly limit. ${note}</p>
    <p><label>Monthly limit (US$) <input id="ai-budget-input" type="number" min="0" max="10000" step="1" value="${Number(s.monthly_limit_usd)}" style="width:6em"></label>
      <button type="button" class="btn-secondary" onclick="saveAiBudget()">Save limit</button> <span id="ai-budget-status" class="muted"></span></p>`;
}

async function saveAiBudget() {
  const status = document.getElementById("ai-budget-status");
  try {
    await api("/hr/ai-budget", { method: "PUT", body: JSON.stringify({ monthly_usd: Number(document.getElementById("ai-budget-input").value) }) });
    status.textContent = "Saved.";
    loadAiHealth();
  } catch (e) {
    status.textContent = e.message;
  }
}

function aiSpendHtml(s) {
  if (!s || !s.calls) return "";
  const usd = (v) => `$${Number(v || 0).toFixed(2)}`;
  const roundName = (k) => (k === "none" ? "Set-up / other" : `Round ${k}`);
  const days = s.by_day.map((d) => `<tr><td>${escapeHtml(d.day)}</td><td>${usd(d.usd)}</td><td>${d.calls}</td></tr>`).join("");
  const rounds = Object.entries(s.by_round).map(([k, v]) => `<tr><td>${escapeHtml(roundName(k))}</td><td>${usd(v)}</td></tr>`).join("");
  const people = s.top_candidates.map((c) => `<tr><td>${escapeHtml(c.email || `#${c.user_id}`)}</td><td>${usd(c.usd)}</td><td>${c.calls}</td></tr>`).join("");
  return `
    <p>Recorded AI spend (kept across restarts, UTC days): today <strong>${usd(s.today_usd)}</strong>,
      last 7 days <strong>${usd(s.last_7_days_usd)}</strong>, all time <strong>${usd(s.all_time_usd)}</strong> over ${s.calls} calls.
      ${s.unpriced_calls ? `<span class="muted">${s.unpriced_calls} call${s.unpriced_calls === 1 ? "" : "s"} had no price (failed, or a model without a listed price).</span>` : ""}</p>
    <details><summary>Spend by day, round and candidate</summary>
      <div class="table-scroll"><table><thead><tr><th>Day</th><th>Spend</th><th>Calls</th></tr></thead><tbody>${days}</tbody></table></div>
      <div class="table-scroll"><table><thead><tr><th>Round</th><th>Spend</th></tr></thead><tbody>${rounds}</tbody></table></div>
      ${people ? `<div class="table-scroll"><table><thead><tr><th>Candidate</th><th>Spend</th><th>Calls</th></tr></thead><tbody>${people}</tbody></table></div>` : ""}
    </details>`;
}

// HR Settings' AI health card - GET /hr/ai-health (metadata only).
async function loadAiHealth() {
  const box = document.getElementById("ai-health");
  if (!box) return;
  try {
    const h = await api("/hr/ai-health");
    const counts = Object.entries(h.by_outcome).map(([k, v]) =>
      `<span class="tag ${k === "ok" ? "tag-accent" : ""}">${escapeHtml(k)}: ${v}</span>`).join(" ");
    const rows = h.recent_problems.map((c) => `
      <tr><td class="muted">${escapeHtml(c.at)}</td><td>${escapeHtml(c.caller)}</td><td>${escapeHtml(c.outcome)}</td><td>${escapeHtml(c.detail || "")}</td></tr>`).join("");
    box.innerHTML = `
      <p>AI mode: <strong>${escapeHtml(h.mode)}</strong>. ${h.total} AI call${h.total === 1 ? "" : "s"} since the server started. ${counts}</p>
      ${h.total ? `<p class="muted">Estimated cost of these calls: about $${Number(h.estimated_cost_usd || 0).toFixed(2)}${h.saved_by_cache_usd ? ` - prompt caching saved about $${Number(h.saved_by_cache_usd).toFixed(2)}` : ""}.</p>` : ""}
      ${aiBudgetHtml(h.lasting)}
      ${aiSpendHtml(h.lasting)}
      ${rows ? `<div class="table-scroll"><table><thead><tr><th>When (UTC)</th><th>Step</th><th>Outcome</th><th>Detail</th></tr></thead><tbody>${rows}</tbody></table></div>`
             : `<p class="muted">No failed AI calls.</p>`}`;
  } catch (e) {
    box.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
  }
}

async function saveAppSettings() {
  const statusEl = document.getElementById("settings-status");
  const payload = {
    round1_passing_score: Number(document.getElementById("set-round1").value),
    round2_passing_score: Number(document.getElementById("set-round2").value),
    round3_passing_score: Number(document.getElementById("set-round3").value),
    round4_passing_score: Number(document.getElementById("set-round4").value),
    final_passing_score: Number(document.getElementById("set-final").value),
    reapplication_window_months: Number(document.getElementById("set-window").value),
    assessment_window_days: Number(document.getElementById("set-assessment-window").value),
  };
  // Blank = no round limit (each scenario's own time_limit_minutes).
  for (const n of [1, 2, 3, 4]) {
    const raw = document.getElementById(`set-time-r${n}`).value.trim();
    payload[`round${n}_time_limit_minutes`] = raw === "" ? null : Number(raw);
  }
  try {
    appSettings = await api("/hr/settings", { method: "PUT", body: JSON.stringify(payload) });
    statusEl.textContent = "Saved.";
    loadScenarios();  // time limits shown per scenario may have changed
    // Score-good/score-bad styling elsewhere (Candidates table, detail
    // panel) reads appSettings live on next render, but anything already
    // on screen right now was rendered against the old thresholds -
    // refresh it so it doesn't look stale.
    if (document.getElementById("candidates-table")) loadCandidates();
  } catch (e) {
    statusEl.textContent = e.message;
  }
}

// Disabled by default (see the `disabled` attribute in index.html) until
// there's actually something to generate from - keeps HR from firing an
// LLM call (and a blank draft scenario) off an empty title/description.
// The counters mirror ScenarioCreate's real max_length (300/20000 - see
// schemas.py) rather than inventing round numbers, so HR sees the actual
// server-side limit, not a placeholder one.
function updateCreateBtnState() {
  const title = document.getElementById("s-title").value.trim();
  const description = document.getElementById("s-desc").value.trim();
  document.getElementById("s-create-btn").disabled = !(title && description);
  document.getElementById("s-title-counter").textContent = `${document.getElementById("s-title").value.length}/300`;
  document.getElementById("s-desc-counter").textContent = `${document.getElementById("s-desc").value.length}/20000`;
}

async function createScenario() {
  const round_number = currentHRRound;
  const experience_band = DEFAULT_BAND;
  const time_limit_minutes = Number(document.getElementById("s-time-limit").value);
  const title = document.getElementById("s-title").value;
  const description = document.getElementById("s-desc").value;
  const statusEl = document.getElementById("hr-create-status");
  const createBtn = document.getElementById("s-create-btn");

  // Disabled for the duration of the generation call so a slow LLM
  // response can't be clicked twice into two draft scenarios. Button
  // text changes too - disabled styling alone is easy to miss.
  createBtn.disabled = true;
  const originalBtnText = createBtn.textContent;
  createBtn.textContent = "Generating...";
  statusEl.textContent = "Generating the reference test cases - this usually takes 1-2 minutes...";
  try {
    const scenario = await api("/hr/scenarios", {
      method: "POST",
      body: JSON.stringify({ round_number, experience_band, time_limit_minutes, title, description }),
    });
    statusEl.textContent = `Created draft #${scenario.id}: ${scenario.title}`;
    document.getElementById("s-title").value = "";
    document.getElementById("s-desc").value = "";
    loadScenarios();
    openScenarioDetail(scenario.id);
  } catch (e) {
    statusEl.textContent = e.message;
  } finally {
    createBtn.textContent = originalBtnText;
    // Not just "re-enable": on success the fields just got cleared, so
    // it should go back to disabled until HR types the next one; on
    // failure the fields still have content, so it should stay usable.
    updateCreateBtnState();
  }
}

// Set at the end of every loadScenarios() call - lets deleteScenarioFromList
// look up a scenario's title (for the confirm dialog) by id without
// embedding free-text HR-authored titles into an inline onclick="..."
// attribute, which escapeHtml doesn't make safe (it escapes for a text
// node, not for sitting inside a quoted HTML attribute - an apostrophe
// or quote in a title could break the attribute or inject markup).
let lastLoadedScenarios = [];
// scenario_id -> its /hr/history row (total_attempted, last_used_at, ...)
// - fetched alongside the scenario list purely for the "Used in
// Assessments" KPI and the table's "Last Used" column below; a second
// existing-endpoint call, not a new one.
let lastLoadedScenarioHistory = {};
let scenarioActiveTab = "all"; // "all" | "published" | "draft"
let scenarioPage = 1;
const SCENARIO_PAGE_SIZE = 6;

async function loadScenarios() {
  const [allScenarios, history] = await Promise.all([api("/hr/scenarios"), api("/hr/history")]);
  lastLoadedScenarios = allScenarios.filter((s) => s.round_number === currentHRRound);
  lastLoadedScenarioHistory = {};
  history.filter((h) => h.round_number === currentHRRound).forEach((h) => {
    lastLoadedScenarioHistory[h.scenario_id] = h;
  });
  scenarioActiveTab = "all";
  scenarioPage = 1;
  renderScenarioKpis();
  renderScenarioTable();
}

// Round 4 has no scenario library (one config per band, no draft/
// published/live library concept the way rounds 1-3 have - see
// loadRound2AutomationSettings) - these counts wouldn't mean anything there.
function renderScenarioKpis() {
  const box = document.getElementById("scenario-kpis");
  if (currentHRRound === 4) {
    box.innerHTML = "";
    return;
  }
  const total = lastLoadedScenarios.length;
  const published = lastLoadedScenarios.filter((s) => s.status === "published").length;
  const drafts = lastLoadedScenarios.filter((s) => s.status === "draft").length;
  const usedInAssessments = Object.values(lastLoadedScenarioHistory).reduce((sum, h) => sum + (h.total_attempted || 0), 0);
  const kpis = [
    { label: "Total Scenarios", value: total, caption: "Across all statuses", icon: "file", cls: "kpi-icon-accent" },
    { label: "Published", value: published, caption: "Reviewed and approved", icon: "check", cls: "kpi-icon-success" },
    { label: "Drafts", value: drafts, caption: "Awaiting review", icon: "file", cls: "kpi-icon-warning" },
    { label: "Used in Assessments", value: usedInAssessments, caption: "Total times attempted", icon: "users", cls: "kpi-icon-neutral" },
  ];
  box.innerHTML = kpis.map((k) => `
    <div class="kpi-card">
      <div class="kpi-icon ${k.cls}">${CANDIDATES_KPI_ICONS[k.icon]}</div>
      <div class="kpi-text">
        <div class="kpi-label">${k.label}</div>
        <div class="kpi-value">${k.value}</div>
        <div class="kpi-caption muted">${k.caption}</div>
      </div>
    </div>
  `).join("");
}

function renderScenarioTabs() {
  const box = document.getElementById("scenario-tabs");
  const total = lastLoadedScenarios.length;
  const published = lastLoadedScenarios.filter((s) => s.status === "published").length;
  const drafts = lastLoadedScenarios.filter((s) => s.status === "draft").length;
  const tabs = [
    { key: "all", label: `All (${total})` },
    { key: "published", label: `Published (${published})` },
    { key: "draft", label: `Drafts (${drafts})` },
  ];
  box.innerHTML = tabs.map((t) => `
    <button type="button" class="scenario-tab ${scenarioActiveTab === t.key ? "active" : ""}" onclick="selectScenarioTab('${t.key}')">${t.label}</button>
  `).join("");
}

function selectScenarioTab(tab) {
  scenarioActiveTab = tab;
  scenarioPage = 1;
  renderScenarioTable();
}

// Search (title substring) + status tab + pagination, all applied
// client-side to lastLoadedScenarios - same pattern as the Candidates
// dashboard's renderCandidatesTable, no new API calls per keystroke.
function renderScenarioTable() {
  renderScenarioTabs();
  const list = document.getElementById("scenario-list");
  const footer = document.getElementById("scenario-footer");

  if (lastLoadedScenarios.length === 0) {
    list.innerHTML = `<div class="empty-state">No Round ${currentHRRound} scenarios yet - create one to get started.</div>`;
    footer.innerHTML = "";
    return;
  }

  const query = (document.getElementById("scenario-search").value || "").trim().toLowerCase();
  let rows = lastLoadedScenarios;
  if (scenarioActiveTab !== "all") rows = rows.filter((s) => s.status === scenarioActiveTab);
  if (query) rows = rows.filter((s) => s.title.toLowerCase().includes(query));

  if (rows.length === 0) {
    list.innerHTML = `<div class="empty-state">No scenarios match your search/filter.</div><p id="scenario-list-status" class="muted"></p>`;
    footer.innerHTML = "";
    return;
  }

  const totalRows = rows.length;
  const pageCount = Math.max(1, Math.ceil(totalRows / SCENARIO_PAGE_SIZE));
  scenarioPage = Math.min(scenarioPage, pageCount);
  const start = (scenarioPage - 1) * SCENARIO_PAGE_SIZE;
  const pageRows = rows.slice(start, start + SCENARIO_PAGE_SIZE);

  list.innerHTML = `
    <div class="table-scroll">
      <table class="scenario-table-el">
        <thead><tr><th>#</th><th>Title</th><th>Status</th><th>Live</th><th>Time</th><th>Last Used</th><th>Actions</th></tr></thead>
        <tbody>${pageRows.map((s, i) => renderScenarioRow(s, start + i + 1)).join("")}</tbody>
      </table>
    </div>
    <p id="scenario-list-status" class="muted"></p>
  `;

  footer.innerHTML = `
    <span class="muted">Showing ${start + 1}-${Math.min(start + SCENARIO_PAGE_SIZE, totalRows)} of ${totalRows} scenario${totalRows === 1 ? "" : "s"}</span>
    <div class="candidates-pagination">
      <button class="btn-secondary btn-sm" ${scenarioPage <= 1 ? "disabled" : ""} onclick="changeScenarioPage(-1)">Previous</button>
      <span class="candidates-page-num">${scenarioPage}</span>
      <button class="btn-secondary btn-sm" ${scenarioPage >= pageCount ? "disabled" : ""} onclick="changeScenarioPage(1)">Next</button>
    </div>
  `;
}

function changeScenarioPage(delta) {
  scenarioPage += delta;
  renderScenarioTable();
}

// The "Live" column is a radio group per (round, band) - same control as
// the pre-redesign table, restored after HR flagged it missing. A radio
// group makes "only one can be live" visually self-evident (picking one
// un-picks the others) instead of relying on a buried menu action; only
// a published scenario is eligible, since a draft can't be made live.
function renderScenarioRow(s, rank) {
  const hist = lastLoadedScenarioHistory[s.id];
  const lastUsed = hist && hist.last_used_at ? formatDate(hist.last_used_at) : "-";
  const statusBadge = s.status === "published" ? `<span class="badge badge-published">Published</span>` : `<span class="badge badge-draft">Draft</span>`;
  const liveCell = s.status === "published"
    ? `<input type="radio" class="scenario-live-radio" name="live-r${s.round_number}-${s.experience_band}"
        ${s.is_live ? "checked" : ""} onchange="moveToScreening(${s.id})" title="Publish for screening" />`
    : `<button class="btn-secondary btn-sm" onclick="publishAndMakeLive(${s.id})" title="Publish this draft and make it the one candidates see">Publish &amp; make live</button>`;
  const deleteBtn = s.is_live
    ? `<button class="btn-ghost btn-sm" disabled title="Can't delete the live scenario - make a different one live first.">Delete</button>`
    : `<button class="btn-danger btn-sm" onclick="deleteScenarioFromList(${s.id})">Delete</button>`;
  return `
    <tr class="${s.is_live ? "scenario-row-live" : ""}">
      <td class="tabular">${rank}</td>
      <td class="scenario-title-cell">${escapeHtml(s.title)}${s.is_live ? ' <span class="badge badge-published">LIVE</span>' : ""}</td>
      <td>${statusBadge}</td>
      <td>${liveCell}</td>
      <td class="tabular" ${roundTimeLimitSetting(s.round_number) ? `title="Set for all Round ${s.round_number} scenarios in HR Settings"` : ""}>${s.round_time_limit_minutes || s.time_limit_minutes}${roundTimeLimitSetting(s.round_number) ? "*" : ""}</td>
      <td class="muted">${lastUsed}</td>
      <td>
        <div class="row-actions">
          <button class="btn-primary btn-sm" onclick="openScenarioDetail(${s.id})">Review</button>
          ${deleteBtn}
        </div>
      </td>
    </tr>
  `;
}

async function moveToScreening(id) {
  const scenario = await api(`/hr/scenarios/${id}`);
  const confirmed = confirm(
    `Publish "${scenario.title}" (Round ${scenario.round_number}) for screening? ` +
    `Candidates in that round will see this one immediately, replacing whichever scenario was live before.`
  );
  if (confirmed) {
    try {
      await api(`/hr/scenarios/${id}/move-to-screening`, { method: "POST" });
    } catch (e) {
      alert(e.message);
    }
  }
  loadScenarios(); // re-render either way: reflects the real is_live state, undoing the radio click if cancelled/failed
  loadHistory(); // LIVE badge there can change too
}

async function openScenarioDetail(id) {
  const box = document.getElementById("scenario-detail");
  let scenario;
  try {
    scenario = await api(`/hr/scenarios/${id}`);
  } catch (e) {
    box.classList.remove("hidden");
    box.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
    return;
  }
  box.classList.remove("hidden");
  // Reviewing one scenario isn't the moment to also be looking at
  // "Create a scenario" or the round's whole screening history - hide
  // both while the detail is open (restored by closeScenarioDetail
  // below, or by switching rounds - see selectHRRound) so the Scenarios
  // list isn't competing with two unrelated panels for attention.
  document.getElementById("create-scenario-panel").classList.add("hidden");
  document.getElementById("screening-history-panel").classList.add("hidden");
  document.getElementById("scenarios-list-panel").classList.add("scenarios-list-panel-full");
  // Everything below this point (through the box.innerHTML assignment
  // near the end of this function) used to be outside any try/catch -
  // the fetch above was covered, but a template-construction error
  // anywhere in the render itself (a malformed field on this particular
  // scenario, for instance) would throw silently: box.innerHTML would
  // simply never get set, leaving whatever was there before on screen
  // (blank, or the previous scenario's panel) with no visible sign
  // anything went wrong. Wrapping the whole render closes that blind
  // spot - a real error now shows up as text instead of nothing at all.
  try {

  // Round 1's reference is test cases (priority/type are meaningful);
  // round 2's is an ordered debugging sequence (they're not - see
  // llm_service.py's round 2 section) - the prompt never asks for them,
  // so those two columns just don't apply here.
  const showPriorityType = scenario.round_number === 1;
  const isCodingReference = scenario.round_number === 3;
  const refRows = isCodingReference
    ? ((scenario.reference_json && scenario.reference_json.test_cases) || []).map((tc, i) => `
        <tr>
          <td>${i + 1}</td>
          <td>${escapeHtml(tc.input)}</td>
          <td>${escapeHtml(tc.expected_output)}</td>
          <td>${escapeHtml(tc.description || "")}</td>
        </tr>
      `).join("")
    : (Array.isArray(scenario.reference_json) ? scenario.reference_json : []).map((r, i) => `
        <tr>
          <td>${i + 1}</td>
          <td>${escapeHtml(r.title)}</td>
          <td>${escapeHtml(r.preconditions || "")}</td>
          <td>${escapeHtml(r.steps)}</td>
          ${showPriorityType ? `<td>${escapeHtml(r.test_data || "")}</td>` : ""}
          <td>${escapeHtml(r.expected_result)}</td>
          ${showPriorityType ? `<td>${escapeHtml(r.priority)}</td><td>${escapeHtml(r.type)}</td>` : ""}
        </tr>
      `).join("");

  const isDraft = scenario.status === "draft";
  // Round 4 no longer routes through here at all (see loadRound2AutomationSettings/
  // renderRound2AutomationSettingsCard) - it has no fixed reference to author/
  // review/compare across versions the way round 1/2 do, so it gets its
  // own dedicated settings panel instead of a "Review" flow into this one.
  box.innerHTML = `
    <div class="row" style="align-items:center; justify-content:space-between">
      <h3 style="margin:0">#${scenario.id} - ${escapeHtml(scenario.title)} <span class="badge badge-${scenario.status}">${statusLabel(scenario.status)}</span>${scenario.is_live ? ' <span class="badge badge-published">LIVE</span>' : ""}</h3>
      <div class="row" style="margin:0">
        ${isDraft ? `<button class="btn-primary btn-sm" onclick="publishAndMakeLive(${scenario.id})">Publish and make live</button>` : ""}
        <button class="btn-ghost" onclick="closeScenarioDetail()">Close</button>
      </div>
    </div>
    ${scenario.is_live ? `<p class="muted">This is the one scenario Round ${scenario.round_number} candidates currently see.</p>` : ""}
    <p class="muted">Round ${scenario.round_number}</p>
    ${roundTimeLimitSetting(scenario.round_number) ? `
      <p class="muted">${roundTimeLimitNote(scenario.round_number)}</p>
    ` : isDraft ? `
      <div class="row" style="align-items:center">
        <div class="field-inline">
          <span class="muted">min limit</span>
          <input id="time-limit-edit" type="number" min="1" value="${scenario.time_limit_minutes}" />
        </div>
        <button onclick="saveTimeLimitEdit(${scenario.id})">Save</button>
      </div>
    ` : `
      <p class="muted">Time limit is locked at ${scenario.time_limit_minutes} minutes - it can't change once published, so candidates are always scored against the same duration. Publish a new scenario if you need a different one.</p>
    `}
    <h4>Question</h4>
    ${formatScenarioDescription(scenario.description)}
    <h4>Reference answer ${isDraft ? "(review before publishing)" : ""}</h4>
    <div class="table-scroll">
      <table>
        <thead><tr>${isCodingReference
          ? "<th>SI.No</th><th>Input</th><th>Expected output</th><th>Description</th>"
          : `<th>SI.No</th><th>Title</th><th>Preconditions</th><th>Steps</th>${showPriorityType ? "<th>Test data</th>" : ""}<th>Expected result</th>${showPriorityType ? "<th>Priority</th><th>Type</th>" : ""}`
        }</tr></thead>
        <tbody>${refRows || `<tr><td colspan="${isCodingReference ? 4 : showPriorityType ? 8 : 5}" class="muted">No reference generated yet.</td></tr>`}</tbody>
      </table>
    </div>
    ${scenario.round_number === 1 ? `<div id="reference-check" data-scenario-id="${scenario.id}"></div>` : ""}
    ${scenario.round_number === 1 ? `<div id="practice-app-panel" data-scenario-id="${scenario.id}"></div>` : ""}
    ${[1, 2].includes(scenario.round_number) ? `<div id="candidate-reference-preview" data-scenario-id="${scenario.id}"></div>` : ""}
    ${isCodingReference && scenario.reference_json ? `<p class="muted"><strong>Expected approach:</strong> ${escapeHtml(scenario.reference_json.expected_approach || "")}</p>` : ""}
    ${isCodingReference && scenario.reference_json && scenario.reference_json.required_constructs && scenario.reference_json.required_constructs.length
      ? `<p class="muted"><strong>Required concepts:</strong> ${escapeHtml(scenario.reference_json.required_constructs.join(", "))}</p>`
      : ""}
    ${isCodingReference ? `
      <h4>Reference program</h4>
      ${scenario.reference_json && scenario.reference_json.reference_solution
        ? `<p class="muted">A correct Python solution HR can read to confirm the test cases above are right - never shown to the candidate.</p>
           <div class="code-snippet code-with-lines">${codeWithLineNumbersHtml(scenario.reference_json.reference_solution)}</div>`
        : `<p class="muted">No reference program generated yet.</p>`}
    ` : ""}
    ${isDraft ? `
      <details>
        <summary>Edit reference as JSON</summary>
        <textarea id="ref-json-edit">${escapeHtml(JSON.stringify(scenario.reference_json || (isCodingReference ? {test_cases: [], expected_approach: "", required_constructs: [], reference_solution: ""} : []), null, 2))}</textarea>
        <div class="row">
          <button onclick="saveReferenceEdit(${scenario.id})">Save edits</button>
        </div>
      </details>
      <div class="row">
        <button id="regenerate-btn" onclick="regenerateReference(${scenario.id})">Regenerate reference</button>
      </div>
    ` : ""}
    ${isDraft ? `
      <div class="row">
        <button onclick="publishScenario(${scenario.id})">Publish</button>
        <button class="btn-danger" onclick="deleteScenario(${scenario.id})">Delete draft</button>
      </div>
    ` : [1, 3].includes(scenario.round_number) ? `
      <div class="row">
        <button class="btn-secondary" onclick="copyScenarioAsDraft(${scenario.id})" title="A published scenario can't be edited - make an editable copy, fix it, then publish the copy">Copy as new draft</button>
      </div>
    ` : ""}
    <p id="scenario-detail-status" class="muted"></p>
  `;
  if (scenario.round_number === 1) loadPracticeAppPanel(scenario.id);
  if (scenario.round_number === 1) loadReferenceCheck(scenario.id);
  if (scenario.round_number === 2) loadCandidateReferencePreview(scenario.id);
  // The panel sits below the scenario list, often off-screen - without this,
  // Review (or Create) looked like it did nothing.
  box.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    box.innerHTML = `<p class="muted">Couldn't render this scenario's detail view: ${escapeHtml(e.message)}. Check the browser console for more, and try a hard refresh (Ctrl+Shift+R) in case this page is running an old cached version.</p>`;
  }
}

// ---- Round 2 practice app for a Round 1 scenario (see services/practice_app) ----
// In Round 2 candidates automate their own Round 1 test cases against a small
// pretend version of this application. HR builds it here (paid AI, a few
// minutes), sees which test cases work in every language, and approves it.

let practiceAppPollTimer = null;

// HR sees the Round 2 reference panel exactly as candidates do (same drawing code):
// a Round 2 scenario's live one, or the one a Round 1's ready build brings once approved.
async function loadCandidateReferencePreview(id) {
  const box = document.getElementById("candidate-reference-preview");
  if (!box || Number(box.dataset.scenarioId) !== id) return;
  try {
    const d = await api(`/hr/scenarios/${id}/candidate-reference`);
    if (!d.reference_panel) { box.innerHTML = ""; return; }
    const note = d.source === "live" ? "This is what candidates see in Round 2 right now."
      : "This is what candidates will see in Round 2 once this build is approved - check it before approving.";
    box.innerHTML = `<details class="surface"><summary><strong>What candidates see in Round 2</strong> - reference panel</summary>
      <p class="muted">${note}</p>${round2AutomationPanelHtml(d.reference_panel)}</details>`;
  } catch (e) {
    box.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
  }
}

// Round 1 test cases that contradict each other (GET /hr/scenarios/{id}/reference-check, no AI call):
// shown above the Round 2 practice app so HR fixes the answer key before candidates are scored on it.
async function loadReferenceCheck(id) {
  const box = document.getElementById("reference-check");
  if (!box || Number(box.dataset.scenarioId) !== id) return;
  try {
    const d = await api(`/hr/scenarios/${id}/reference-check`);
    box.innerHTML = d.contradictions.length ? `<div class="surface" style="border-left:4px solid var(--warning, #d97706)">
      <p><strong>⚠️ Test cases that seem to contradict each other</strong></p>
      <ul>${d.contradictions.map((c) => `<li>${escapeHtml(c)}</li>`).join("")}</ul>
      <p class="muted">Candidates are scored against these test cases. Fix the wrong one - on a published scenario, use "Copy as new draft".</p></div>` : "";
  } catch (e) {
    box.innerHTML = "";
  }
}

async function loadPracticeAppPanel(id) {
  clearTimeout(practiceAppPollTimer);
  const box = document.getElementById("practice-app-panel");
  if (!box || Number(box.dataset.scenarioId) !== id) return; // HR opened a different scenario
  let data;
  try {
    data = await api(`/hr/scenarios/${id}/practice-app`);
  } catch (e) {
    box.innerHTML = `<h4>Round 2 practice app</h4><p class="muted">${escapeHtml(e.message)}</p>`;
    return;
  }
  box.innerHTML = renderPracticeAppPanel(id, data);
  if (data.status === "building") practiceAppPollTimer = setTimeout(() => loadPracticeAppPanel(id), 5000);
  else loadCandidateReferencePreview(id);
}

function practiceAppCoverageTable(rows) {
  if (!rows || !rows.length) return "";
  const icon = { works: "✅", fails: "❌", "not supported": "➖", "differs from its test case": "⚠️" };
  return `
    <details ${rows.some((r) => r.status !== "works") ? "open" : ""}>
      <summary>Every Round 1 test case (${rows.length})</summary>
      <div class="table-scroll"><table>
        <thead><tr><th></th><th>Test case</th><th>Details</th></tr></thead>
        <tbody>${rows.map((r) => `
          <tr><td>${icon[r.status] || ""}</td><td>${escapeHtml(r.title)}</td>
          <td class="muted">${r.status === "works" ? "Works in Python, JavaScript and Java" : escapeHtml((r.details || []).join("; "))}</td></tr>`).join("")}
        </tbody>
      </table></div>
    </details>`;
}

// A build HR may approve though not every test case could be verified (at
// least 90% were - see generator.APPROVE_AT): say which, plainly.
function practiceAppReadyLine(d, stats) {
  const unverified = d.unverified || [];
  if (!unverified.length) return `<p>✅ Ready - ${stats}.</p>`;
  return `<p>⚠️ Ready to approve - ${stats}. ${unverified.length === 1 ? "1 test case" : `${unverified.length} test cases`} could not be verified:</p>
    <ul>${unverified.map((r) => `<li><strong>${escapeHtml(r.title)}</strong>${(r.details || []).length ? ` <span class="muted">- ${escapeHtml(r.details[0])}</span>` : ""}</li>`).join("")}</ul>
    <p class="muted">The app may not behave as ${unverified.length === 1 ? "that test case expects" : "those test cases expect"}. If a candidate automates one and the app misbehaves, the scorer is told it's the app's gap, not theirs. Generating again may verify ${unverified.length === 1 ? "it" : "them"}.</p>`;
}

function renderPracticeAppPanel(id, d) {
  const intro = `<p class="muted">In Round 2, candidates automate the test cases they designed in Round 1, against a small pretend version of this application. Build it here: it is checked automatically against every reference test case in Python, JavaScript and Java before you can approve it.</p>`;
  const buildButton = (label) => d.cannot_start
    ? `<p class="muted">${escapeHtml(d.cannot_start)}</p>`
    : `<button onclick="buildPracticeApp(${id})">${label}</button>`;
  const approved = d.approved_round2_scenario_id
    ? `<p>✅ Approved - Round 2 scenario #${d.approved_round2_scenario_id} uses it, and goes live whenever this Round 1 scenario is live.</p>` : "";
  const stats = d.total ? `${d.working} of ${d.total} test cases work in every language` : "";
  const cost = d.ai_calls ? ` <span class="muted">(${d.ai_calls} AI calls, ${d.minutes} min)</span>` : "";
  let body;
  if (d.status === "building") {
    body = practiceAppProgressHtml(d) + `<p class="muted">You can leave this page - you'll get a notification when it's ready.</p>`;
  } else if (d.status === "ready") {
    body = `${practiceAppReadyLine(d, stats)}${cost ? `<p>${cost}</p>` : ""}${practiceAppCoverageTable(d.coverage)}
      ${approved || `<button class="btn-primary" onclick="approvePracticeApp(${id})">Approve for Round 2</button>`}
      <p>${buildButton(approved ? "Rebuild" : "Build again")}</p>`;
  } else if (d.status === "not_ready") {
    body = `<p class="error-text">❌ Not ready${d.approval_problem ? ` - ${escapeHtml(d.approval_problem)}` : ` - ${stats}`}. Building again often fixes it, or adjust the Round 1 reference test cases listed below.${cost}</p>
      ${practiceAppCoverageTable(d.coverage)}${approved}<p>${buildButton("Build again")}</p>`;
  } else if (d.status === "failed") {
    body = `<p class="error-text">The build stopped: ${escapeHtml(d.error || "unknown error")}</p>${approved}<p>${buildButton("Try again")}</p>`;
  } else {
    body = `${approved}<p>${buildButton("Build practice app")}</p>`;
  }
  return `<h4>Round 2 practice app</h4>${intro}${body}<p id="practice-app-status" class="muted"></p>`;
}

// Shown on the Round 2 card when no Round 1 scenario is live. (A live Round 1
// without its own practice app gets renderRound2NeedsPracticeApp instead.)
function practiceAppNoRound1Notice(pairedTitle) {
  return `<p class="error-text" role="alert">No Round 1 scenario is live. Make ${escapeHtml(pairedTitle)} live in Round 1, or another scenario with its own practice app.</p>`;
}

// What a practice-app build costs, shown before HR confirms one (paid AI calls).
const PRACTICE_APP_BUILD_COST = "It uses the paid AI: about $1.50-2 and 10 minutes for a full build, "
  + "or about $0.30-0.50 and a few minutes when the last build's Python app already works.";

async function buildPracticeAppFromRound2(round1Id) {
  if (!confirm(`Generate Round 2 for the live Round 1 scenario? ${PRACTICE_APP_BUILD_COST} Nothing changes for candidates until you approve it.`)) return;
  const buttons = [...document.querySelectorAll("#round4-settings-panel button")];
  buttons.forEach((b) => { b.disabled = true; });
  const status = document.getElementById("practice-app-r2-status");
  if (status) { status.className = "muted"; status.textContent = "Starting…"; }
  try {
    await api(`/hr/scenarios/${round1Id}/practice-app`, { method: "POST" });
  } catch (e) {
    buttons.forEach((b) => { b.disabled = false; });
    if (status) { status.className = "error-text"; status.textContent = e.message; }
    return;
  }
  watchPracticeAppBuild(round1Id);
  if (typeof loadScenarios === "function") await loadScenarios();
  loadRound2AutomationSettings();
}

async function approvePracticeAppFromRound2(round1Id) {
  if (!confirm("Approve this practice app? Round 2 switches to it now (unless candidates are mid-way through Round 2).")) return;
  try {
    await api(`/hr/scenarios/${round1Id}/practice-app/approve`, { method: "POST" });
  } catch (e) {
    const status = document.getElementById("practice-app-r2-status");
    if (status) status.textContent = e.message;
    return;
  }
  if (typeof loadScenarios === "function") await loadScenarios();
  loadRound2AutomationSettings();
}

// The build's steps, ticked off as it goes (step numbers come from
// generator.STEPS via the status endpoint; the Round 2 card, which reads the
// scenario list, falls back to the same names).
const PRACTICE_APP_STEPS = [
  "Designing the practice app", "Writing a checklist for each test case", "Building the Python version",
  "Checking the Python version", "Building the JavaScript and Java versions", "Checking all three languages",
];

function practiceAppProgressHtml(d) {
  const steps = d.steps || PRACTICE_APP_STEPS;
  const current = Number.isInteger(d.step) ? d.step : 0;
  const started = d.started_at ? new Date(d.started_at + "Z") : null;
  const seconds = started ? Math.max(0, Math.round((Date.now() - started) / 1000)) : null;
  const elapsed = seconds === null ? "" : `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
  return `<p>⏳ In progress${elapsed ? ` - running for ${elapsed}` : ""} (step ${current + 1} of ${steps.length})</p>
    <ol class="practice-steps">${steps.map((name, i) => `
      <li class="${i < current ? "done" : i === current ? "current" : ""}">${i < current ? "✅" : i === current ? "⏳" : "▫️"} ${escapeHtml(name)}${i === current && d.step_detail ? ` - ${escapeHtml(d.step_detail)}` : ""}</li>`).join("")}
    </ol>`;
}

// ---- "Your practice app is ready" notifications ----
// Builds HR started are remembered in this browser, checked every 10 seconds
// wherever HR is in the app, and announced with a pop-up (and a desktop
// notification if the browser allows it) as soon as they finish.

const PRACTICE_APP_WATCH_KEY = "practiceAppBuildsWatched";
let practiceAppWatchTimer = null;

function watchedPracticeAppBuilds() {
  try { return JSON.parse(localStorage.getItem(PRACTICE_APP_WATCH_KEY) || "[]"); } catch (e) { return []; }
}

function setWatchedPracticeAppBuilds(ids) {
  try { localStorage.setItem(PRACTICE_APP_WATCH_KEY, JSON.stringify(ids)); } catch (e) { /* private mode - watch just this page */ }
}

function watchPracticeAppBuild(id) {
  const ids = watchedPracticeAppBuilds();
  if (!ids.includes(id)) setWatchedPracticeAppBuilds([...ids, id]);
  if (typeof Notification !== "undefined" && Notification.permission === "default") {
    try { Notification.requestPermission(); } catch (e) { /* not available */ }
  }
  startPracticeAppWatch();
}

function startPracticeAppWatch() {
  if (practiceAppWatchTimer || !watchedPracticeAppBuilds().length) return;
  practiceAppWatchTimer = setInterval(checkWatchedPracticeAppBuilds, 10000);
}

async function checkWatchedPracticeAppBuilds() {
  const ids = watchedPracticeAppBuilds();
  if (!ids.length) {
    clearInterval(practiceAppWatchTimer);
    practiceAppWatchTimer = null;
    return;
  }
  const still = [];
  for (const id of ids) {
    let d;
    try {
      d = await api(`/hr/scenarios/${id}/practice-app`);
    } catch (e) {
      if (/log(ged)? in|401|403/i.test(e.message)) return; // signed out - try again later
      continue; // e.g. the scenario was deleted - stop watching it
    }
    if (d.status === "building") { still.push(id); continue; }
    announcePracticeAppResult(id, d);
  }
  setWatchedPracticeAppBuilds(still);
}

function announcePracticeAppResult(id, d) {
  const scenario = (typeof allScenarios !== "undefined" && Array.isArray(allScenarios) ? allScenarios : []).find((s) => s.id === id);
  const name = scenario ? scenario.title : `scenario #${id}`;
  const ready = d.status === "ready";
  const text = ready
    ? `Practice app for ${name} is ready - ${d.working} of ${d.total} test cases work in every language${(d.unverified || []).length ? ` (${d.unverified.length} could not be verified - see the card)` : ""}. Approve it to use it in Round 2.`
    : d.status === "not_ready"
      ? `Practice app for ${name} finished but isn't ready - ${d.working} of ${d.total} test cases work. Open it to see which.`
      : `Practice app for ${name} stopped: ${d.error || "unknown error"}.`;
  showToast(text, ready ? "success" : "error", { label: "Open", onClick: () => openPracticeAppFor(id) });
  if (typeof Notification !== "undefined" && Notification.permission === "granted" && document.hidden) {
    try { new Notification("QA Eval - Round 2 practice app", { body: text }); } catch (e) { /* not available */ }
  }
  // Refresh whichever practice-app view is open.
  const panel = document.getElementById("practice-app-panel");
  if (panel && Number(panel.dataset.scenarioId) === id) loadPracticeAppPanel(id);
  const round2Panel = document.getElementById("round4-settings-panel");
  if (round2Panel && round2Panel.offsetParent !== null) loadRound2AutomationSettings();
}

function openPracticeAppFor(id) {
  const detail = document.getElementById("scenario-detail");
  if (detail) {
    openScenarioDetail(id).then(() => document.getElementById("practice-app-panel")?.scrollIntoView({ behavior: "smooth" }));
  }
}

function showToast(message, kind = "info", action = null) {
  let area = document.getElementById("toast-area");
  if (!area) {
    area = document.createElement("div");
    area.id = "toast-area";
    area.className = "toast-area";
    area.setAttribute("role", "status");
    area.setAttribute("aria-live", "polite");
    document.body.appendChild(area);
  }
  const toast = document.createElement("div");
  toast.className = `toast toast-${kind}`;
  const text = document.createElement("div");
  text.textContent = message;
  toast.appendChild(text);
  const actions = document.createElement("div");
  actions.className = "toast-actions";
  if (action) {
    const go = document.createElement("button");
    go.className = "btn-primary btn-sm";
    go.textContent = action.label;
    go.onclick = () => { toast.remove(); action.onClick(); };
    actions.appendChild(go);
  }
  const close = document.createElement("button");
  close.className = "btn-ghost btn-sm";
  close.textContent = "Dismiss";
  close.onclick = () => toast.remove();
  actions.appendChild(close);
  toast.appendChild(actions);
  area.appendChild(toast);
}

// Pick up builds started before a page reload.
startPracticeAppWatch();

async function buildPracticeApp(id) {
  if (!confirm(`Build the Round 2 practice app for this scenario? ${PRACTICE_APP_BUILD_COST} Nothing changes for candidates until you approve it.`)) return;
  try {
    await api(`/hr/scenarios/${id}/practice-app`, { method: "POST" });
  } catch (e) {
    const status = document.getElementById("practice-app-status");
    if (status) status.textContent = e.message;
    return;
  }
  watchPracticeAppBuild(id);
  loadPracticeAppPanel(id);
}

async function approvePracticeApp(id) {
  if (!confirm("Approve this practice app for Round 2? Candidates doing this Round 1 scenario will automate their test cases against it.")) return;
  const status = document.getElementById("practice-app-status");
  try {
    const result = await api(`/hr/scenarios/${id}/practice-app/approve`, { method: "POST" });
    if (status) status.textContent = result.round2_is_live
      ? "Approved - it's live for Round 2 now."
      : "Approved - it will go live for Round 2 when this Round 1 scenario is live.";
  } catch (e) {
    if (status) status.textContent = e.message;
    return;
  }
  loadPracticeAppPanel(id);
  if (typeof loadScenarios === "function") loadScenarios();
}

// Undoes the panel-hiding openScenarioDetail does above - restores
// "Create a scenario" and "Screening history" once HR is done reviewing
// this one scenario.
function closeScenarioDetail() {
  document.getElementById("scenario-detail").classList.add("hidden");
  document.getElementById("create-scenario-panel").classList.remove("hidden");
  document.getElementById("screening-history-panel").classList.remove("hidden");
  document.getElementById("scenarios-list-panel").classList.remove("scenarios-list-panel-full");
}

// ---- Round 4 settings: replaces the round1/2-style Create-a-scenario/
// Scenarios-list/Screening-history flow entirely (see selectHRRound).
// Round 4 has no fixed reference to author, review, or compare across
// versions - each candidate automates their own round 1 answer, and the
// environment/screens are auto-generated, not HR-authored - so there's
// no "library of scenarios" to browse the way round 1/2 genuinely have,
// and never more than one meaningful configuration worth looking at.
//
// Used to render one card per experience band - band is a hidden feature
// now (see DEFAULT_BAND), so there's only ever the one card.

async function loadRound2AutomationSettings() {
  const box = document.getElementById("round4-settings-panel");
  let allScenarios;
  try {
    allScenarios = await api("/hr/scenarios");
  } catch (e) {
    box.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
    return;
  }
  // Nothing shows at all if round 4 hasn't been set up yet - not a
  // "create one" prompt (bootstrapping round 4, if ever needed, is a
  // direct API action, not a standing part of this page).
  const liveScenario = allScenarios.find((s) => s.round_number === 2 && s.experience_band === DEFAULT_BAND && s.is_live);
  const liveRound1 = allScenarios.find((s) => s.round_number === 1 && s.experience_band === DEFAULT_BAND && s.is_live);
  if (liveRound1 && !(liveScenario && round2BuiltFor(liveScenario, liveRound1))) {
    // Round 1 changed: the previous Round 2 content was built for another
    // scenario, so none of it is shown - only the way to build this one's.
    box.innerHTML = renderRound2NeedsPracticeApp(liveRound1);
  } else if (!liveScenario) {
    box.innerHTML = "";
    return;
  } else {
    box.innerHTML = renderRound2AutomationSettingsCard(liveScenario, liveRound1 ? liveRound1.title : null, liveRound1);
  }
  const waitingEl = liveRound1 && document.getElementById(`r2-waiting-${liveRound1.id}`);
  if (waitingEl) {
    // Candidates who finished this Round 1 can't start Round 2 until an app is approved.
    api(`/hr/scenarios/${liveRound1.id}/practice-app`).then((d) => {
      const n = d.waiting_candidates || 0;
      if (n) waitingEl.textContent = `${n} candidate${n === 1 ? " has" : "s have"} finished Round 1 on ${liveRound1.title} and ${n === 1 ? "is" : "are"} waiting for this Round 2 - they can't continue until you approve a practice app.`;
    }).catch(() => {});
  }
  clearTimeout(round2PracticeAppTimer);
  if (liveRound1 && ((liveRound1.config_json || {}).practice_app || {}).status === "building") {
    round2PracticeAppTimer = setTimeout(() => {
      if (document.getElementById("round4-settings-panel")?.offsetParent !== null) loadRound2AutomationSettings();
    }, 5000);
  }
}

let round2PracticeAppTimer = null;

// Is this Round 2 scenario's practice app the one built for this Round 1
// scenario? Seeded environments only record the title they were built for.
function round2BuiltFor(round2, round1) {
  const config = round2.config_json || {};
  if (config.paired_round1_scenario_id) return config.paired_round1_scenario_id === round1.id;
  return Boolean(config.paired_round1_title) && config.paired_round1_title === round1.title;
}

// The Round 2 card while the live Round 1 has no practice app live in
// Round 2: one button to build it, its progress while it builds (button
// disabled), then the results - every Round 1 test case - and Approve.
function renderRound2NeedsPracticeApp(round1) {
  const d = (round1.config_json || {}).practice_app || {};
  const title = escapeHtml(round1.title);
  const stats = d.total ? `${d.working} of ${d.total} test cases work in every language` : "";
  const button = (label, disabled = false) =>
    `<button id="r2-generate-btn" class="btn-primary" onclick="buildPracticeAppFromRound2(${round1.id})" ${disabled ? "disabled" : ""}>${label}</button>`;
  let badge;
  let body;
  if (d.status === "building") {
    badge = `<span class="badge badge-draft">IN PROGRESS</span>`;
    body = `${practiceAppProgressHtml(d)}${button("Generating…", true)}
      <p class="muted">This updates by itself - you can leave the page and you'll get a notification when it's done.</p>`;
  } else if (d.status === "ready") {
    badge = `<span class="badge badge-published">READY TO APPROVE</span>`;
    body = `${practiceAppReadyLine(d, stats)}${practiceAppCoverageTable(d.coverage)}
      <div class="row"><button class="btn-primary" onclick="approvePracticeAppFromRound2(${round1.id})">Approve - use it for Round 2</button>
      <button class="btn-secondary" onclick="buildPracticeAppFromRound2(${round1.id})">Generate again</button></div>`;
  } else if (d.status === "not_ready") {
    badge = `<span class="badge">NOT READY</span>`;
    body = `<p class="error-text">❌ Done, but it can't be approved${d.approval_problem ? ` - ${escapeHtml(d.approval_problem)}` : ` - ${stats}`}. Generating again often fixes it; otherwise adjust the Round 1 test cases marked below.</p>
      ${practiceAppCoverageTable(d.coverage)}${button("Generate again")}`;
  } else if (d.status === "failed") {
    badge = `<span class="badge">FAILED</span>`;
    body = `<p class="error-text">The last run stopped: ${escapeHtml(d.error || "unknown error")}</p>${button("Try again")}`;
  } else {
    badge = `<span class="badge">NOT GENERATED</span>`;
    body = `<p>Generate Round 2 for <strong>${title}</strong>: a practice app built from its Round 1 test cases, checked automatically against every one of them. Nothing changes for candidates until you approve it.</p>
      ${button("Generate Round 2")}`;
  }
  return `
    <div class="panel card" style="margin-bottom:1.5rem">
      <h3>Round 2 ${badge}</h3>
      <p class="muted">Round 1 is now <strong>${title}</strong>. Round 2's previous content was built for a different scenario, so it's hidden until Round 2 is generated for this one.</p>
      <p id="r2-waiting-${round1.id}" class="error-text" role="status"></p>
      ${body}
      <p id="practice-app-r2-status" class="muted"></p>
    </div>`;
}

function renderRound2AutomationSettingsCard(scenario, groundedInTitle, liveRound1 = null) {
  const paired = scenario.config_json || {};
  const isPaired = Boolean(paired.paired_round1_title || paired.paired_round1_scenario_id);
  return `
    <div class="panel card" style="margin-bottom:1.5rem">
      <h3>Round 2 <span class="badge badge-published">LIVE</span></h3>
      <p class="muted">This is what Round 2 candidates currently see.</p>

      <h4>Instructions</h4>
      <p class="muted">What candidates read when they open this round.</p>
      <input id="r4-title-${scenario.id}" value="${escapeAttr(scenario.title)}" />
      <textarea id="r4-desc-${scenario.id}">${escapeHtml(scenario.description)}</textarea>
      <div class="row">
        <button onclick="saveRound2AutomationInstructions(${scenario.id})">Save instructions</button>
      </div>

      <h4>Time limit</h4>
      ${roundTimeLimitSetting(scenario.round_number) ? `<p class="muted">${roundTimeLimitNote(scenario.round_number)}</p>` : `
      <div class="row" style="align-items:center">
        <div class="field-inline">
          <span class="muted">min limit</span>
          <input id="r4-time-limit-${scenario.id}" type="number" min="1" value="${scenario.time_limit_minutes}" />
        </div>
        <button onclick="saveRound2AutomationTimeLimit(${scenario.id})">Save</button>
      </div>`}

      ${isPaired ? `
        <h4>Practice app</h4>
        <p class="muted">Candidates automate their Round 1 test cases against a practice app built for <strong>${escapeHtml(paired.paired_round1_title || "")}</strong>.</p>
        ${liveRound1 ? "" : practiceAppNoRound1Notice(paired.paired_round1_title || "")}` : ""}

      ${isPaired ? "" : `      <h4>Grounded in</h4>
      <p class="muted">\${groundedInTitle
        ? \`This round's test environment &amp; reference screens are auto-generated from <strong>\${escapeHtml(groundedInTitle)}</strong> - the round 1 scenario currently live. They resync automatically whenever a different round 1 scenario goes live here.\`
        : \`No round 1 scenario is currently live - the environment/screens below fell back to this scenario's own description instead. They'll resync automatically once one is published.\`}</p>`}

      ${scenario.config_json && scenario.config_json.reference_panel ? `
      <details class="surface" id="candidate-reference-preview" open>
        <summary><strong>What candidates see in Round 2</strong> - reference panel</summary>
        <p class="muted">This is what candidates see in Round 2 right now - built from the practice app itself, never written by the AI.</p>
        ${round2AutomationPanelHtml(scenario.config_json.reference_panel)}
      </details>` : `
      <details>
        <summary>Preview: test environment &amp; reference screens (auto-generated, shown to candidates)</summary>
        ${scenario.environment_json && isPaired ? `
          <div class="hint-box env-panel">
            <dl class="env-fields">
              ${Object.entries(scenario.environment_json.fields || {}).map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join("")}
            </dl>
            ${scenario.environment_json.notes ? `<p class="muted">${escapeHtml(scenario.environment_json.notes)}</p>` : ""}
            <p class="muted">Read-only: this sheet matches the practice app's code exactly. To change a login or data, change the Round 1 test cases and generate Round 2 again.</p>
          </div>
        ` : scenario.environment_json ? `
          <div class="hint-box env-panel">
            <dl class="env-fields" id="r4-env-fields-${scenario.id}">
              ${Object.entries(scenario.environment_json.fields || {}).map(([k, v]) => `
                <dt>${escapeHtml(k)}</dt>
                <dd><input type="text" class="env-field-input" data-key="${escapeAttr(k)}" value="${escapeAttr(v)}" /></dd>
              `).join("")}
            </dl>
            <textarea id="r4-env-notes-${scenario.id}" rows="2" placeholder="Notes (optional)">${escapeHtml(scenario.environment_json.notes || "")}</textarea>
            <div class="row">
              <button onclick="saveRound2AutomationEnvironment(${scenario.id})">Save environment fields</button>
            </div>
            ${scenario.environment_hr_edited
              ? `<p class="muted">These fields were hand-set by HR - the automatic refresh that runs when a different round 1 scenario goes live won't overwrite them. Only "Regenerate" below replaces them.</p>`
              : ""}
          </div>
        ` : `<p class="muted">No test environment generated yet.</p>`}
        ${scenario.ui_mockup_json ? renderMockupScreens(scenario.ui_mockup_json, `hr-mockup-${scenario.id}`) : `<p class="muted">No reference screens generated yet.</p>`}
        <div class="row">
          ${scenario.config_json && scenario.config_json.paired_round1_title ? "" : `<button id="r4-regen-btn-${scenario.id}" onclick="regenerateRound2AutomationReference(${scenario.id})">Regenerate environment &amp; screens</button>`}
        </div>
      </details>
      `}

      <p id="r4-status-${scenario.id}" class="muted"></p>
    </div>
  `;
}

async function saveRound2AutomationInstructions(id) {
  const statusEl = document.getElementById(`r4-status-${id}`);
  const title = document.getElementById(`r4-title-${id}`).value.trim();
  const description = document.getElementById(`r4-desc-${id}`).value.trim();
  statusEl.className = "muted";
  if (!title || !description) {
    statusEl.className = "error-text";
    statusEl.textContent = "Title and instructions are both required.";
    return;
  }
  try {
    await api(`/hr/scenarios/${id}/round4-instructions`, { method: "PATCH", body: JSON.stringify({ title, description }) });
    statusEl.textContent = "Saved.";
  } catch (e) {
    statusEl.className = "error-text";
    statusEl.textContent = e.message;
  }
}

async function saveRound2AutomationTimeLimit(id) {
  const statusEl = document.getElementById(`r4-status-${id}`);
  const inputEl = document.getElementById(`r4-time-limit-${id}`);
  const value = Number(inputEl.value);
  statusEl.className = "muted";
  if (!Number.isInteger(value) || value < 1) {
    statusEl.className = "error-text";
    statusEl.textContent = "Time limit must be a whole number of minutes, at least 1.";
    return;
  }
  try {
    await api(`/hr/scenarios/${id}/time-limit`, { method: "PATCH", body: JSON.stringify({ time_limit_minutes: value }) });
    statusEl.textContent = "Saved.";
  } catch (e) {
    inputEl.value = inputEl.defaultValue;
    statusEl.className = "error-text";
    statusEl.textContent = e.message;
  }
}

async function saveRound2AutomationEnvironment(id) {
  const statusEl = document.getElementById(`r4-status-${id}`);
  const container = document.getElementById(`r4-env-fields-${id}`);
  const fields = {};
  container.querySelectorAll(".env-field-input").forEach((input) => {
    fields[input.dataset.key] = input.value.trim();
  });
  const notes = document.getElementById(`r4-env-notes-${id}`).value.trim();
  statusEl.className = "muted";
  try {
    await api(`/hr/scenarios/${id}/round4-environment`, {
      method: "PATCH",
      body: JSON.stringify({ fields, notes: notes || null }),
    });
    loadRound2AutomationSettings();
  } catch (e) {
    statusEl.className = "error-text";
    statusEl.textContent = e.message;
  }
}

async function regenerateRound2AutomationReference(id) {
  const statusEl = document.getElementById(`r4-status-${id}`);
  const btn = document.getElementById(`r4-regen-btn-${id}`);
  btn.disabled = true;
  btn.textContent = "Regenerating...";
  statusEl.className = "muted";
  statusEl.textContent = "Regenerating (a few seconds)...";
  try {
    await api(`/hr/scenarios/${id}/regenerate-reference`, { method: "POST" });
    loadRound2AutomationSettings();
  } catch (e) {
    statusEl.className = "error-text";
    statusEl.textContent = e.message;
    btn.disabled = false;
    btn.textContent = "Regenerate environment & screens";
  }
}

async function regenerateReference(id) {
  const statusEl = document.getElementById("scenario-detail-status");
  const btn = document.getElementById("regenerate-btn");
  btn.disabled = true;
  btn.textContent = "Regenerating...";
  statusEl.textContent = "Regenerating...";
  try {
    await api(`/hr/scenarios/${id}/regenerate-reference`, { method: "POST" });
    openScenarioDetail(id); // re-renders the whole panel, so btn is recreated enabled with its original text
  } catch (e) {
    statusEl.textContent = e.message;
    btn.disabled = false;
    btn.textContent = "Regenerate reference";
  }
}

async function saveReferenceEdit(id) {
  const statusEl = document.getElementById("scenario-detail-status");
  try {
    const reference_json = JSON.parse(document.getElementById("ref-json-edit").value);
    await api(`/hr/scenarios/${id}`, { method: "PATCH", body: JSON.stringify({ reference_json }) });
    // openScenarioDetail rebuilds this whole panel (including a fresh,
    // empty #scenario-detail-status) - the confirmation has to be set
    // AFTER it finishes, not before, or the rebuild wipes it unseen.
    await openScenarioDetail(id);
    document.getElementById("scenario-detail-status").textContent = "Saved.";
  } catch (e) {
    statusEl.textContent = e.message.includes("JSON") ? "Invalid JSON - check the syntax." : e.message;
  }
}

async function saveTimeLimitEdit(id) {
  // Only ever called while this scenario is a draft (see the isDraft
  // check around the "min limit" field above) - its own endpoint rather
  // than the general draft-only PATCH /hr/scenarios/{id} only because
  // Round 4 scenarios (which have no draft phase) also go through it -
  // see hr.py's update_scenario_time_limit.
  const statusEl = document.getElementById("scenario-detail-status");
  const inputEl = document.getElementById("time-limit-edit");
  const value = Number(inputEl.value);
  statusEl.className = "muted";
  if (!Number.isInteger(value) || value < 1) {
    statusEl.className = "error-text";
    statusEl.textContent = "Time limit must be a whole number of minutes, at least 1.";
    return;
  }
  try {
    await api(`/hr/scenarios/${id}/time-limit`, { method: "PATCH", body: JSON.stringify({ time_limit_minutes: value }) });
    await openScenarioDetail(id);
    document.getElementById("scenario-detail-status").textContent = "Saved.";
  } catch (e) {
    // Rejected (e.g. blocked while a candidate is mid-round) - snap the
    // input back to the real live value so the number HR typed doesn't
    // sit there looking like it was accepted when nothing was saved.
    inputEl.value = inputEl.defaultValue;
    statusEl.className = "error-text";
    statusEl.textContent = e.message;
  }
}

function statusLabel(status) {
  if (status === "published") return "published";
  if (status === "draft") return "draft";
  return status;
}

// Draft -> live in one step: publish (which makes it live by itself when
// nothing is live yet), then move it to screening if something else was.
async function publishAndMakeLive(id) {
  const statusEl = document.getElementById("scenario-detail-status");
  const scenario = await api(`/hr/scenarios/${id}`);
  const limit = scenario.round_time_limit_minutes || scenario.time_limit_minutes;
  if (!confirm(`Publish "${scenario.title}" and make it live? Round ${scenario.round_number} candidates will see it immediately (${limit}-minute limit), replacing whichever scenario is live now.`)) return;
  try {
    const published = await api(`/hr/scenarios/${id}/publish`, { method: "POST" });
    if (!published.is_live) await api(`/hr/scenarios/${id}/move-to-screening`, { method: "POST" });
  } catch (e) {
    if (statusEl) statusEl.textContent = e.message; else alert(e.message);
  }
  loadScenarios();
  loadHistory();
  if (document.getElementById("scenario-detail") && !document.getElementById("scenario-detail").classList.contains("hidden")) openScenarioDetail(id);
}

async function publishScenario(id) {
  const statusEl = document.getElementById("scenario-detail-status");
  const scenario = await api(`/hr/scenarios/${id}`);
  const scenarios = await api("/hr/scenarios");
  const currentlyLive = scenarios.find((s) =>
    s.is_live && s.round_number === scenario.round_number && s.experience_band === scenario.experience_band
  );
  const warning = currentlyLive
    ? `Publish this into the Round ${scenario.round_number} library alongside "${currentlyLive.title}", which stays live for candidates until you explicitly publish it for screening. Continue?`
    : `Publish this scenario? Since nothing is currently live for Round ${scenario.round_number}, it will also become the one candidates see immediately.`;
  if (!confirm(warning)) return;

  try {
    await api(`/hr/scenarios/${id}/publish`, { method: "POST" });
    loadScenarios();
    openScenarioDetail(id);
  } catch (e) {
    statusEl.textContent = e.message;
  }
}

// Published scenarios can't be edited: an editable draft copy (no AI call) is how HR corrects one.
async function copyScenarioAsDraft(id) {
  const statusEl = document.getElementById("scenario-detail-status");
  try {
    const draft = await api(`/hr/scenarios/${id}/copy-as-draft`, { method: "POST" });
    await loadScenarios();
    await openScenarioDetail(draft.id);
    const s = document.getElementById("scenario-detail-status");
    if (s) s.textContent = `This is an editable copy (#${draft.id}). Fix it, then Publish - the original stays as it was.`;
  } catch (e) {
    if (statusEl) statusEl.textContent = e.message;
  }
}

async function deleteScenario(id) {
  const statusEl = document.getElementById("scenario-detail-status");
  try {
    await api(`/hr/scenarios/${id}`, { method: "DELETE" });
    document.getElementById("scenario-detail").classList.add("hidden");
    loadScenarios();
  } catch (e) {
    statusEl.textContent = e.message;
  }
}

// Delete straight from the scenario list rows (see renderScenarioRow) -
// unlike deleteScenario above
// (only ever reachable from a draft's open detail view), this works on
// EITHER status: the backend (DELETE /hr/scenarios/{id}) now allows
// deleting a published scenario too, as long as it isn't the live one
// and nothing has ever submitted against it (see that endpoint's
// docstring for why those are the two safety gates). Looks the title up
// from lastLoadedScenarios rather than taking it as a parameter - see
// that variable's comment for why.
async function deleteScenarioFromList(id) {
  const scenario = lastLoadedScenarios.find((s) => s.id === id);
  const label = scenario ? `"${scenario.title}"` : "this scenario";
  if (!confirm(`Delete ${label}? This can't be undone.`)) return;
  const statusEl = document.getElementById("scenario-list-status");
  try {
    await api(`/hr/scenarios/${id}`, { method: "DELETE" });
    loadScenarios();
  } catch (e) {
    if (statusEl) {
      statusEl.className = "error-text";
      statusEl.textContent = e.message;
    }
  }
}

// Candidates dashboard state - the full list is fetched once per
// loadCandidates() call and kept here; search/filter/pagination
// (renderCandidatesTable) all re-slice this same array client-side
// rather than re-fetching, since /hr/candidates has no server-side
// search/filter/pagination params to call.
let lastLoadedCandidates = [];
let candidatesPage = 1;
const CANDIDATES_PAGE_SIZE = 10;

// Small stroke icons for the KPI cards below, matching the sun/moon
// theme-toggle icons already hand-written in index.html (same
// currentColor/stroke-width=2/round-linecap style) rather than pulling
// in an icon library for three shapes.
const CANDIDATES_KPI_ICONS = {
  users: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
  clock: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
  check: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
  file: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
};

async function loadCandidates() {
  const box = document.getElementById("candidates-table");
  const footer = document.getElementById("candidates-footer");
  // Skeleton only on the very first load - a refresh after an upload or
  // a settings save keeps the current table on screen instead of
  // flashing placeholders over data that's about to come back the same.
  if (!lastLoadedCandidates.length) {
    box.innerHTML = candidatesSkeletonHtml();
    footer.innerHTML = "";
  }
  try {
    lastLoadedCandidates = await api("/hr/candidates");
  } catch (e) {
    box.innerHTML = `
      <div class="banner banner-error cd-error" role="alert">
        <span class="banner-icon" aria-hidden="true">!</span>
        <div>
          <strong>Couldn't load candidates</strong>
          <p class="muted">${escapeHtml(e.message)}</p>
          <button type="button" class="btn-secondary btn-sm" onclick="loadCandidates()">Try again</button>
        </div>
      </div>
    `;
    footer.innerHTML = "";
    document.getElementById("candidates-count").textContent = "";
    document.getElementById("candidates-total-pill").textContent = "";
    return;
  }
  candidatesPage = 1;
  renderCandidatesKpis();
  renderCandidatesTable();
}

function candidatesSkeletonHtml() {
  const row = `
    <div class="cd-skeleton-row">
      <div class="skeleton-block cd-skeleton-avatar"></div>
      <div class="cd-skeleton-lines"><div class="skeleton-block"></div><div class="skeleton-block cd-skeleton-short"></div></div>
      <div class="skeleton-block cd-skeleton-wide"></div>
    </div>
  `;
  return `<div class="cd-skeleton" aria-busy="true"><span class="sr-only">Loading candidates...</span>${row.repeat(4)}</div>`;
}

// Opens the (collapsed-by-default) upload panel below the table and
// brings it into view - the toolbar's Upload button, so the action is
// discoverable from the top of the page without the panel itself
// taking up space above the table.
function openCandidateUpload() {
  const panel = document.getElementById("candidate-upload-panel");
  panel.open = true;
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
  document.getElementById("upload-file").focus({ preventScroll: true });
}

function clearCandidateFilters() {
  document.getElementById("candidates-search").value = "";
  document.getElementById("candidates-status-filter").value = "";
  candidatesPage = 1;
  renderCandidatesTable();
}

// Four counts derived entirely from each candidate's own rounds array -
// "completed" mirrors _build_candidate_summary's all_four_scored check,
// "not started" is the reverse (every round still not_started), and
// "in progress" is just whatever's neither.
function renderCandidatesKpis() {
  const box = document.getElementById("candidates-kpis");
  const total = lastLoadedCandidates.length;
  const completed = lastLoadedCandidates.filter((c) => c.rounds.every((r) => r.status === "scored")).length;
  const notStarted = lastLoadedCandidates.filter((c) => c.rounds.every((r) => r.status === "not_started")).length;
  const inProgress = total - completed - notStarted;
  const kpis = [
    { label: "Total candidates", value: total, caption: "All assessments", icon: "users", cls: "kpi-icon-accent" },
    { label: "In progress", value: inProgress, caption: "Taking an assessment", icon: "clock", cls: "kpi-icon-warning" },
    { label: "Completed", value: completed, caption: "Finished all rounds", icon: "check", cls: "kpi-icon-success" },
    { label: "Not started", value: notStarted, caption: "Invited, not started", icon: "clock", cls: "kpi-icon-neutral" },
  ];
  // Same four counts as always; each also shown as a share of the total
  // (100% on the total card itself) so all four cards share one shape.
  box.innerHTML = kpis.map((k) => {
    const share = total ? Math.round((k.value / total) * 100) : 0;
    return `
      <div class="kpi-card cd-kpi">
        <div class="cd-kpi-head">
          <span class="kpi-label">${k.label}</span>
          <span class="kpi-icon cd-kpi-icon ${k.cls}" aria-hidden="true">${CANDIDATES_KPI_ICONS[k.icon]}</span>
        </div>
        <div class="kpi-value">${k.value}</div>
        <div class="kpi-caption muted">${share}% · ${k.caption}</div>
        <div class="cd-kpi-share ${k.cls}" aria-hidden="true"><i style="width:${share}%"></i></div>
      </div>
    `;
  }).join("");
}

// Search (email substring) + status filter + pagination, all applied to
// the same in-memory lastLoadedCandidates - re-run on every keystroke/
// filter change/page click, never a network call.
function renderCandidatesTable() {
  const box = document.getElementById("candidates-table");
  const footer = document.getElementById("candidates-footer");
  const query = (document.getElementById("candidates-search").value || "").trim().toLowerCase();
  const statusFilter = document.getElementById("candidates-status-filter").value;

  let rows = lastLoadedCandidates;
  if (query) rows = rows.filter((c) => c.email.toLowerCase().includes(query));
  if (statusFilter) rows = rows.filter((c) => candidateStage(c) === statusFilter);

  const totalRows = rows.length;
  const filtered = Boolean(query || statusFilter);
  const all = lastLoadedCandidates.length;
  document.getElementById("candidates-count").textContent = filtered
    ? `${totalRows} of ${all} candidate${all === 1 ? "" : "s"} match`
    : `${all} candidate${all === 1 ? "" : "s"}`;
  // Visible only while filtering (the pill beside the title already
  // shows the total); still read out to screen readers either way.
  document.getElementById("candidates-count").classList.toggle("sr-only", !filtered);
  document.getElementById("candidates-total-pill").textContent = all;
  renderCandidateStatusTabs(query, statusFilter);

  if (totalRows === 0) {
    box.innerHTML = all === 0
      ? `<div class="empty-state cd-empty">
           <div class="empty-eyebrow">No candidates yet</div>
           <p>Upload a candidate file to generate logins - candidates appear here as soon as they're created.</p>
           <button type="button" class="btn-secondary btn-sm" onclick="openCandidateUpload()">Upload candidates</button>
         </div>`
      : `<div class="empty-state cd-empty">
           <div class="empty-eyebrow">No matches</div>
           <p>No candidates match your search or status filter.</p>
           <button type="button" class="btn-secondary btn-sm" onclick="clearCandidateFilters()">Clear filters</button>
         </div>`;
    footer.innerHTML = "";
    return;
  }

  const pageCount = Math.max(1, Math.ceil(totalRows / CANDIDATES_PAGE_SIZE));
  candidatesPage = Math.min(candidatesPage, pageCount);
  const start = (candidatesPage - 1) * CANDIDATES_PAGE_SIZE;
  const pageRows = rows.slice(start, start + CANDIDATES_PAGE_SIZE);

  box.innerHTML = `
    <div class="table-scroll">
      <table class="candidates-table-el">
        <thead>
          <tr>
            <th scope="col">Candidate</th>
            <th scope="col">Progress</th>
            <th scope="col">Round scores</th>
            <th scope="col">Overall</th>
            <th scope="col">Status</th>
            <th scope="col"><span class="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          ${pageRows.map((c) => renderCandidateRow(c)).join("")}
        </tbody>
      </table>
    </div>
  `;

  // First, last, and the current page's neighbours - with a gap marker
  // between runs - so a long list doesn't render dozens of page buttons.
  const pages = [];
  for (let p = 1; p <= pageCount; p++) {
    if (p === 1 || p === pageCount || Math.abs(p - candidatesPage) <= 1) pages.push(p);
    else if (pages[pages.length - 1] !== "gap") pages.push("gap");
  }
  footer.innerHTML = `
    <span class="muted">Showing ${start + 1}-${Math.min(start + CANDIDATES_PAGE_SIZE, totalRows)} of ${totalRows} candidate${totalRows === 1 ? "" : "s"}</span>
    ${pageCount > 1 ? `
      <nav class="candidates-pagination" aria-label="Candidates pages">
        <button class="btn-secondary btn-sm" ${candidatesPage <= 1 ? "disabled" : ""} onclick="changeCandidatesPage(-1)">Previous</button>
        ${pages.map((p) => p === "gap" ? `<span class="cd-page-gap" aria-hidden="true">&hellip;</span>` : `
          <button class="cd-page-btn${p === candidatesPage ? " active" : ""}" ${p === candidatesPage ? 'aria-current="page"' : ""} onclick="goToCandidatesPage(${p})" aria-label="Page ${p}">${p}</button>
        `).join("")}
        <button class="btn-secondary btn-sm" ${candidatesPage >= pageCount ? "disabled" : ""} onclick="changeCandidatesPage(1)">Next</button>
      </nav>
    ` : ""}
  `;
}

// Status filter as toggle buttons with counts. The <select> stays the
// source of truth: a click just sets its value and runs the exact same
// handler as its own onchange, and this re-renders from select.value
// every time the table does - so the two can never disagree. Counts
// honour the current search (the same email filter the table uses).
const CANDIDATE_STATUS_TABS = [
  { value: "", label: "All" },
  { value: "not_started", label: "Not started" },
  { value: "in_progress", label: "In progress" },
  { value: "selected", label: "Selected" },
  { value: "not_selected", label: "Not selected" },
];

function renderCandidateStatusTabs(query, statusFilter) {
  const searched = query ? lastLoadedCandidates.filter((c) => c.email.toLowerCase().includes(query)) : lastLoadedCandidates;
  document.getElementById("candidates-status-tabs").innerHTML = CANDIDATE_STATUS_TABS.map((t) => {
    const count = t.value ? searched.filter((c) => candidateStage(c) === t.value).length : searched.length;
    const active = t.value === statusFilter;
    return `
      <button type="button" class="tab cd-status-tab${active ? " active" : ""}" aria-pressed="${active}" onclick="selectCandidateStatusTab('${t.value}')">
        ${t.label}<span class="cd-status-tab-count">${count}</span>
      </button>
    `;
  }).join("");
}

function selectCandidateStatusTab(value) {
  const select = document.getElementById("candidates-status-filter");
  select.value = value;
  select.onchange();
  // Re-render replaced the button that had focus - put it back.
  const tabs = document.querySelectorAll("#candidates-status-tabs .cd-status-tab");
  const idx = CANDIDATE_STATUS_TABS.findIndex((t) => t.value === value);
  if (tabs[idx]) tabs[idx].focus();
}

function changeCandidatesPage(delta) {
  candidatesPage += delta;
  renderCandidatesTable();
}

function goToCandidatesPage(page) {
  candidatesPage = page;
  renderCandidatesTable();
}

// Cosmetic "CAND-00N" label from the candidate's own real database id
// (not a row number, which would shift under search/filter/pagination,
// and not invented data - just a formatted view of the existing id).
function renderCandidateRow(c) {
  const idLabel = `CAND-${String(c.id).padStart(3, "0")}`;
  // One neutral avatar for everyone - the old per-id colour cycle looked
  // like it meant something, and it didn't.
  return `
    <tr>
      <td data-label="Candidate">
        <div class="candidate-identity">
          <div class="candidate-avatar cd-avatar" aria-hidden="true">${escapeHtml(candidateInitials(c.email))}</div>
          <div class="candidate-identity-text">
            <div class="candidate-email">${escapeHtml(c.email)}</div>
            <div class="candidate-id muted">
              ${idLabel}${c.exam_date ? ` · Exam ${formatDate(c.exam_date)}` : ""}
              ${c.reapplied_within_window ? '<span class="cd-reapplied">Re-applied</span>' : ""}
            </div>
          </div>
        </div>
      </td>
      <td data-label="Progress">${candidateProgressCell(c)}</td>
      <td data-label="Round scores">${candidateRoundScoresCell(c)}</td>
      <td data-label="Overall">${aggregateCell(c)}</td>
      <td data-label="Status">${statusCell(c)}</td>
      <td class="cd-actions-cell">
        <div class="row-actions">
          <button type="button" class="cd-view-btn" onclick="openCandidateDetail(${c.id})" aria-label="View report for ${escapeAttr(c.email)}">
            View report <span aria-hidden="true">&rsaquo;</span>
          </button>
        </div>
      </td>
    </tr>
  `;
}

// Two letters from the email's local part (the only identity field the
// API has - no name) - "jordan.rivera@..." -> "JR", "candidate1@..." -> "CA".
function candidateInitials(email) {
  const local = (email || "").split("@")[0];
  const parts = local.split(/[._\-+\d]+/).filter(Boolean);
  const letters = parts.length > 1 ? parts[0][0] + parts[1][0] : (parts[0] || local).slice(0, 2);
  return (letters || "?").toUpperCase();
}

// Human labels for CandidateRoundSummary.status - the raw enum values
// ("scoring_failed", "not_started") used to be shown as-is.
const CANDIDATE_ROUND_STATUS_LABELS = {
  not_started: "Not started",
  in_progress: "In progress",
  submitted: "Scoring",
  scored: "Scored",
  scoring_failed: "Scoring failed",
};

// Four-step progress indicator + the existing one-line caption
// (candidateStatusCaption). A round counts as done once it's been
// submitted, whether or not scoring has finished yet.
function candidateProgressCell(c) {
  const done = c.rounds.filter((r) => ["submitted", "scored", "scoring_failed"].includes(r.status)).length;
  const steps = c.rounds.map((r) => {
    const cls = r.status === "scored" ? "is-done"
      : r.status === "scoring_failed" ? "is-failed"
      : r.status === "submitted" ? "is-submitted"
      : r.status === "in_progress" ? "is-current" : "";
    return `<span class="cd-step ${cls}" title="Round ${r.round_number} · ${ROUND_LABELS[r.round_number]}: ${CANDIDATE_ROUND_STATUS_LABELS[r.status] || r.status}"></span>`;
  }).join("");
  return `
    <div class="cd-progress">
      <div class="cd-progress-label"><strong>${done}</strong> of ${c.rounds.length} rounds</div>
      <div class="cd-steps" role="img" aria-label="${done} of ${c.rounds.length} rounds submitted">${steps}</div>
      <div class="status-caption muted">${candidateStatusCaption(c)}</div>
    </div>
  `;
}

// The four per-round chips, then any integrity flags underneath (tab
// switches / auto-close) - the same flags the old per-round columns
// showed, labelled with their round now that they share one cell.
function candidateRoundScoresCell(c) {
  // Integrity flags as one line of plain text (grouped by kind, rounds
  // listed) rather than boxed badges - every flag still visible; the
  // auto-close reason stays on hover per round, as before.
  const autoClosed = c.rounds.filter((r) => r.auto_closed_reason);
  const switched = c.rounds.filter((r) => r.tab_switch_count > 0);
  const flags = [];
  if (autoClosed.length) {
    flags.push(`<div class="cd-flag cd-flag-warning"><span class="cd-flag-icon" aria-hidden="true">&#9888;</span><span>Auto-closed: ${autoClosed.map((r) => `<span title="${escapeAttr(r.auto_closed_reason)}">R${r.round_number}</span>`).join(", ")}</span></div>`);
  }
  if (switched.length) {
    flags.push(`<div class="cd-flag cd-flag-error"><span class="cd-flag-icon" aria-hidden="true">&#9888;</span><span>Tab switch: ${switched.map((r) => `<span title="Left the test ${r.tab_switch_count} time${r.tab_switch_count === 1 ? "" : "s"} during this round">R${r.round_number} (${r.tab_switch_count}&times;)</span>`).join(", ")}</span></div>`);
  }
  return `
    <div class="cd-round-strip">${c.rounds.map((r) => roundStatusCell(r)).join("")}</div>
    ${flags.length ? `<div class="cd-flags">${flags.join("")}</div>` : ""}
  `;
}

// One "Export" entry point covering both existing exports (the CSV
// below, and the daily-cohort-summary PDF - see downloadDailySummary) -
// they used to be two separate, similarly-purposed controls sitting
// right next to each other on this same page. Plain absolute
// positioning is fine here (unlike the row-level overflow menu this
// page used to have) since the toolbar itself never sits inside a
// horizontally-scrolling container.
function toggleExportMenu(evt) {
  if (evt) evt.stopPropagation();
  document.getElementById("export-menu-dropdown").classList.toggle("hidden");
}
document.addEventListener("click", (evt) => {
  const menu = document.getElementById("export-menu-dropdown");
  if (menu && !menu.classList.contains("hidden") && !evt.target.closest(".export-menu")) {
    menu.classList.add("hidden");
  }
});

// Full loaded list, not just the current search/filter/page - "export"
// on a filtered dashboard still means "give me the data", same
// convention as most enterprise tables.
function exportCandidatesCsv() {
  const header = ["#", "Candidate ID", "Email", "Exam Date", "Round 1", "Round 2", "Round 3", "Round 4", "Aggregate", "Result"];
  const csvRows = lastLoadedCandidates.map((c, i) => [
    i + 1,
    `CAND-${String(c.id).padStart(3, "0")}`,
    c.email,
    c.exam_date ? formatDate(c.exam_date) : "",
    ...c.rounds.map((r) => (r.final_score != null ? r.final_score : r.status)),
    c.aggregate_score != null ? c.aggregate_score : "",
    c.result,
  ]);
  const csv = [header, ...csvRows]
    .map((row) => row.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(","))
    .join("\r\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "candidates.csv";
  a.click();
  URL.revokeObjectURL(url);
}

// "selected"/"not_selected"/"in_progress" - see hr.py's
// _build_candidate_summary for the actual determination (aggregate_score
// vs AppSettings.final_passing_score, only once every round is scored).
// Reuses the existing badge-published/badge-draft classes (green/amber)
// rather than inventing new ones; "not_selected" gets the neutral grey
// badge-neutral (not badge-fail's red) - a final, no-longer-in-flight
// outcome reads as a settled status here, not an alarm.
function resultBadge(result) {
  if (result === "selected") return `<span class="badge badge-published">Selected</span>`;
  if (result === "not_selected") return `<span class="badge badge-neutral">Not selected</span>`;
  if (result === "not_started") return `<span class="badge badge-neutral">Not started</span>`;
  return `<span class="badge cd-badge-info">In progress</span>`;
}

// The server's result is "in_progress" for every candidate without a final
// decision - including one who hasn't started. The page shows "not_started"
// separately so the badge, the filter tabs and the summary cards agree.
function candidateStage(c) {
  if (c.result === "in_progress" && c.rounds.every((r) => r.status === "not_started")) return "not_started";
  return c.result;
}

// One-line summary of what's actually happening for this candidate
// right now - derived entirely from the same per-round statuses already
// shown in the row's own Round 1-4 cells, never a separate computation.
function candidateStatusCaption(c) {
  if (c.result === "selected" || c.result === "not_selected") return "Assessment complete";
  const active = c.rounds.find((r) => r.status === "in_progress" || r.status === "submitted");
  if (active) return `Round ${active.round_number} ${active.status === "in_progress" ? "in progress" : "submitted, scoring"}`;
  return c.rounds.some((r) => r.status !== "not_started") ? "Awaiting next round" : "Not started yet";
}

// Just the result badge - the one-line "what's happening now" caption
// moved under the Progress column (see candidateProgressCell).
function statusCell(c) {
  return `<div class="status-cell">${resultBadge(candidateStage(c))}</div>`;
}

// Aggregate score, its % of the fixed 400-point scale (4 rounds x 100 -
// the same convention _build_candidate_summary/the PDF exports use),
// and a mini bar - reusing .readout-bar (see style.css section 10)
// rather than a new bar component.
function aggregateCell(c) {
  if (c.aggregate_score == null) {
    return `<div class="aggregate-cell"><span class="cd-overall-score muted">&ndash;</span><span class="status-caption muted">Not scored yet</span></div>`;
  }
  const passing = appSettings ? appSettings.final_passing_score : 280;
  const passed = c.aggregate_score >= passing;
  const pct = Math.round((c.aggregate_score / 400) * 100);
  const passPct = Math.min(100, Math.round((passing / 400) * 100));
  // Pass/fail is spelled out in the caption (with a mark), not left to
  // the number's colour alone; the bar's tick shows where the mark sits.
  return `
    <div class="aggregate-cell">
      <div class="cd-overall-top">
        <span class="cd-overall-score ${passed ? "score-good" : "score-bad"}">${c.aggregate_score}</span>
        <span class="cd-overall-max muted">/ 400</span>
        <span class="cd-overall-pct muted">&middot; ${pct}%</span>
      </div>
      <div class="readout-bar aggregate-bar${passed ? "" : " is-bad"}" style="--pct:${Math.min(100, pct)}%" aria-hidden="true">
        <i></i><b class="cd-pass-marker" style="left:${passPct}%"></b>
      </div>
      <span class="cd-pass-caption ${passed ? "is-pass" : "is-fail"}"><span aria-hidden="true">${passed ? "&#10003;" : "&#10005;"}</span> ${passed ? "Meets" : "Below"} pass mark ${passing}</span>
    </div>
  `;
}

// No longer called from the candidate table (band is a hidden feature -
// see DEFAULT_BAND) - kept working against the real PATCH endpoint in
// case per-candidate bands come back.
async function setCandidateBand(id, band) {
  if (!band) return;
  try {
    await api(`/hr/candidates/${id}/band`, { method: "PATCH", body: JSON.stringify({ experience_band: band }) });
    loadCandidates();
  } catch (e) {
    alert(e.message);
    loadCandidates(); // revert the select to whatever the server actually has
  }
}

async function uploadCandidates() {
  const input = document.getElementById("upload-file");
  const resultEl = document.getElementById("upload-result");
  if (!input.files.length) {
    resultEl.innerHTML = `<p class="muted">Choose a file first.</p>`;
    return;
  }
  resultEl.innerHTML = loadingHtml("Uploading...");
  const formData = new FormData();
  formData.append("file", input.files[0]);
  try {
    // Not api() on purpose - a multipart body must not have a
    // Content-Type header set manually (the browser sets its own with
    // the correct boundary), but jsonHeaders() always includes one. No
    // auth header needed either way now - the session cookie attaches to
    // this same-origin fetch automatically.
    const res = await fetch("/hr/candidates/upload", {
      method: "POST",
      body: formData,
    });
    const result = await res.json();
    if (!res.ok) throw new Error(apiErrorMessage(result, "Upload failed"));

    const rows = result.rows.map((r) => `
      <tr>
        <td>${r.row_number}</td>
        <td>${escapeHtml(r.email || "-")}</td>
        <td><span class="badge badge-${r.status === "error" ? "fail" : "pass"}">${r.status}</span></td>
        <td>${r.username ? escapeHtml(r.username) : ""}</td>
        <td>${r.password ? `<code>${escapeHtml(r.password)}</code>` : `<span class="muted">-</span>`}</td>
        <td class="muted">${r.error ? escapeHtml(r.error) : ""}</td>
      </tr>
    `).join("");
    const anyPasswords = result.rows.some((r) => r.password);
    resultEl.innerHTML = `
      <p>${result.created_count} created, ${result.reset_count} reset, ${result.error_count} error${result.error_count === 1 ? "" : "s"}.</p>
      ${anyPasswords ? `<p class="muted">Each new candidate got a random password, shown here once - copy these now, this table isn't saved anywhere.</p>` : ""}
      <div class="table-scroll">
        <table>
          <thead><tr><th>Row</th><th>Email</th><th>Status</th><th>Username</th><th>Password</th><th>Error</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
    input.value = "";
    loadCandidates();
  } catch (e) {
    resultEl.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
  }
}

// One cell of the per-row round strip (label, score + pass mark or a
// status word, mini bar with the round's pass-mark tick). The
// tab-switch/auto-closed flags are rendered by the caller
// (candidateRoundScoresCell), since the four cells share one column.
function roundStatusCell(r) {
  // Real completion timestamp (see models.Submission.submitted_at) on
  // hover - a title attribute rather than its own table column, so the
  // already-wide dashboard doesn't grow a column per round just for
  // this. Only ever set once a round has actually finished, so this
  // naturally never appears on a not_started/in_progress cell.
  const tip = `Round ${r.round_number} · ${ROUND_LABELS[r.round_number]}${r.submitted_at ? ` · Submitted ${formatDateTime(r.submitted_at)}` : ""}`;
  if (r.final_score != null) {
    const passing = passingScoreForRound(r.round_number);
    const passed = r.final_score >= passing;
    return `
      <div class="cd-round-chip ${passed ? "is-pass" : "is-fail"}" title="${escapeAttr(`${tip} · Pass mark ${passing}`)}">
        <span class="cd-round-label">R${r.round_number}</span>
        <span class="cd-round-value">
          <span class="cd-round-score ${passed ? "score-good" : "score-bad"}">${r.final_score}</span>
          <span class="cd-round-mark" aria-hidden="true">${passed ? "&#10003;" : "&#10005;"}</span>
        </span>
        <span class="sr-only">out of 100, ${passed ? "passed" : `below pass mark ${passing}`}</span>
        <div class="readout-bar round-bar${passed ? "" : " is-bad"}" style="--pct:${r.final_score}%" aria-hidden="true">
          <i></i><b class="cd-pass-marker" style="left:${Math.min(100, passing)}%"></b>
        </div>
      </div>
    `;
  }
  return `
    <div class="cd-round-chip is-${r.status}" title="${escapeAttr(tip)}">
      <span class="cd-round-label">R${r.round_number}</span>
      <span class="cd-round-status">${CANDIDATE_ROUND_STATUS_LABELS[r.status] || escapeHtml(r.status)}</span>
    </div>
  `;
}

// Shared by the live candidate-detail view and the "Past appearances"
// drill-down (viewAppearance) - same per-round rendering either way, fed
// either the current cycle's submissions or one specific past cycle's.
// opts.allRounds (live report only): a round with no submission yet still
// gets a card, so R1-R4 always read as a set.
function renderSubmissionsPanels(submissions, opts = {}) {
  if (submissions.length === 0 && !opts.allRounds) return `<p class="muted">No submissions in this cycle.</p>`;
  const byRound = {};
  submissions.forEach((s) => { byRound[s.round_number] = s; });
  const order = opts.allRounds ? [1, 2, 3, 4] : submissions.map((s) => s.round_number);
  return order.map((n) => (byRound[n] ? renderRoundCard(byRound[n], opts) : renderRoundPlaceholder(n))).join("");
}

function renderRoundPlaceholder(n) {
  return `
    <article class="cdd-rcard is-not_started" aria-labelledby="round-placeholder-${n}-title">
      <header class="cdd-rcard-head">
        <div class="cdd-rcard-heading">
          <span class="cd-round-label">R${n}</span>
          <h4 class="cdd-rcard-title" id="round-placeholder-${n}-title">${escapeHtml(ROUND_LABELS[n] || `Round ${n}`)}</h4>
        </div>
        <span class="cdd-status-pill is-not_started">${CANDIDATE_DETAIL_STATUS_LABELS.not_started}</span>
      </header>
      <p class="cdd-card-note muted">No submission for this round yet &middot; pass mark ${passingScoreForRound(n)}</p>
    </article>
  `;
}

// One round: header (round, status), what the candidate saw and did, the
// score and its audit trail, then the round's own evidence renderer. The
// renderer mapping below is deliberate (rounds 2 and 4 were renumbered -
// see selectHRRound) - R1 side-by-side, R2 -> renderRound4Report,
// R3 -> renderRound3Report, R4 -> renderRound2Report.
function renderRoundCard(s, opts = {}) {
  const n = s.round_number;
  const events = s.tab_switch_events_json || [];
  const flags = [];
  if (s.tab_switch_count > 0) {
    flags.push(`Left the test ${s.tab_switch_count} time${s.tab_switch_count === 1 ? "" : "s"}${events.length ? ` (${events.map(formatDateTime).join(", ")})` : ""}`);
  }
  if (s.auto_closed_reason) flags.push(`Auto-closed: ${s.auto_closed_reason}`);
  const times = [
    s.started_at ? `Started ${formatDateTime(s.started_at)}` : "",
    s.submitted_at ? `Submitted ${formatDateTime(s.submitted_at)}` : "",
  ].filter(Boolean).join(" &middot; ");
  return `
    <article class="cdd-rcard is-${escapeAttr(s.status)}" aria-labelledby="round-${s.id}-title">
      <header class="cdd-rcard-head">
        <div class="cdd-rcard-heading">
          <span class="cd-round-label">R${n}</span>
          <h4 class="cdd-rcard-title" id="round-${s.id}-title" tabindex="-1">${escapeHtml(ROUND_LABELS[n] || `Round ${n}`)}</h4>
        </div>
        <span class="cdd-status-pill is-${escapeAttr(s.status)}">${escapeHtml(CANDIDATE_DETAIL_STATUS_LABELS[s.status] || s.status)}</span>
      </header>
      ${s.scenario ? `<p class="cdd-rcard-scenario">${escapeHtml(s.scenario.title)}</p>` : ""}
      ${times ? `<p class="cdd-rcard-meta muted">${times}</p>` : ""}
      ${flags.length ? `<ul class="cdd-rcard-flags">${flags.map((f) => `<li><span aria-hidden="true">&#9888;</span> ${escapeHtml(f)}</li>`).join("")}</ul>` : ""}
      <section class="cdd-rcard-section">
        <h5 class="cdd-rcard-label">Result</h5>
        ${renderScoreBlock(s, opts)}
      </section>
      ${s.scenario ? `
        <details class="scenario-question" open>
          <summary>Question</summary>
          ${formatScenarioDescription(s.scenario.description)}
        </details>
      ` : ""}
      <section class="cdd-rcard-section cdd-evidence">
        <h5 class="cdd-rcard-label">Evidence</h5>
        ${n === 2 ? renderRound4Report(s)
          : n === 3 ? renderRound3Report(s)
          : n === 4 ? renderRound2Report(s)
          : renderSideBySide(s.content, s.scenario ? s.scenario.reference_json : null)}
      </section>
    </article>
  `;
}

// Three states, not two - see models.RoundStatus.scoring_failed and
// hr.py's retry-scoring/score-override endpoints (the human-in-the-loop
// escape hatch this app didn't have before).
function conceptCoverageLine(items) {
  // Round 1 only (see models.Score.concept_coverage_json) - empty for
  // rounds 2/3, and for any score predating this column.
  if (!items || items.length === 0) return "";
  const parts = items.map((c) => {
    const cls = c.covered === 0 ? "score-bad" : c.covered < c.total ? "" : "score-good";
    return `<span class="${cls}" title="${escapeAttr(c.notes || "")}">${escapeHtml(c.category)} ${c.covered}/${c.total}</span>`;
  });
  return `<p class="muted">Coverage by type: ${parts.join(" · ")}</p>`;
}

// Audit trail behind a score - collapsed, since it's for the rare "how
// was this scored?" question, not the everyday read.
function renderScoreAudit(score) {
  const rows = [
    ["Scoring model", score.scoring_model],
    ["Prompt file", score.scoring_prompt_file],
    ["Prompt hash", score.scoring_prompt_hash],
    ["Scored at", score.scored_at ? formatDateTime(score.scored_at) : null],
    ["Overridden at", score.overridden_at ? formatDateTime(score.overridden_at) : null],
  ].filter(([, v]) => v);
  if (!rows.length) return "";
  return `
    <details class="cdd-audit">
      <summary>Audit details</summary>
      <dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${escapeHtml(String(v))}</dd>`).join("")}</dl>
    </details>
  `;
}

// The override form's container carries the current score so the form
// can show it; the status line below it is where Retry/Save report back.
function scoreActionsHtml(s, toggleLabel) {
  return `
    <div class="cdd-rcard-actions">
      ${s.status === "scoring_failed" ? `<button id="retry-btn-${s.id}" onclick="retryScoring(${s.id})">Retry scoring</button>` : ""}
      <button type="button" class="btn-ghost" id="override-toggle-${s.id}" aria-expanded="false" aria-controls="override-form-${s.id}" onclick="toggleScoreOverrideForm(${s.id})">${toggleLabel}</button>
    </div>
    <div id="override-form-${s.id}" data-current-score="${s.score && s.score.final_score != null ? s.score.final_score : ""}"></div>
    <p id="score-status-${s.id}" class="muted cdd-action-status" role="status"></p>
  `;
}

// opts.readOnly (archived appearances): the same score, evidence and audit
// trail, but no Retry / Override controls at all.
function renderScoreBlock(s, opts = {}) {
  const passing = passingScoreForRound(s.round_number);
  const actions = (label) => (opts.readOnly ? "" : scoreActionsHtml(s, label));
  if (s.status === "scoring_failed") {
    return `
      <div class="panel-inset cd-score-failed">
        <p><strong class="score-bad">Scoring failed</strong></p>
        <p class="muted">${escapeHtml(s.scoring_error || "Unknown error.")}</p>
        ${actions("Score manually")}
      </div>
    `;
  }
  if (s.score) {
    const sc = s.score;
    const passed = sc.final_score >= passing;
    const misses = sc.misses_json || [];
    return `
      <div class="cdd-result">
        <div class="cdd-result-score">
          <span class="cd-overall-score ${passed ? "score-good" : "score-bad"}">${sc.final_score}</span><span class="cd-overall-max muted">/ 100</span>
          <span class="cd-pass-caption ${passed ? "is-pass" : "is-fail"}"><span aria-hidden="true">${passed ? "&#10003;" : "&#10005;"}</span> ${passed ? "Meets" : "Below"} pass mark ${passing}</span>
        </div>
        <div class="readout-bar cdd-round-bar${passed ? "" : " is-bad"}" style="--pct:${Math.min(100, sc.final_score)}%" aria-hidden="true">
          <i></i><b class="cd-pass-marker" style="left:${Math.min(100, passing)}%"></b>
        </div>
        ${sc.coverage_score != null ? `<p class="cdd-result-line">Coverage ${sc.coverage_score} / 100</p>` : ""}
      </div>
      ${sc.overridden_by_hr ? `
        <div class="cdd-override-note">
          <span class="badge cdd-override-badge">Overridden by HR${sc.original_final_score != null ? ` - LLM originally said ${sc.original_final_score}/100` : ""}</span>
          ${sc.override_note ? `<p><span class="cdd-mini-label">Reason</span> ${escapeHtml(sc.override_note)}</p>` : ""}
        </div>
      ` : ""}
      ${sc.feedback_text ? `
        <div class="cdd-result-block">
          <p class="cdd-mini-label">Evaluation</p>
          <p>${escapeHtml(sc.feedback_text)}</p>
        </div>
      ` : ""}
      <div class="cdd-result-block">
        <p class="cdd-mini-label">Missed</p>
        ${misses.length ? `<ul>${misses.map((m) => `<li>${escapeHtml(m)}</li>`).join("")}</ul>` : `<p class="muted">None noted</p>`}
      </div>
      ${conceptCoverageLine(sc.concept_coverage_json)}
      ${renderScoreAudit(sc)}
      ${actions("Override score")}
    `;
  }
  return `<p class="cdd-card-note muted">No score yet &middot; pass mark ${passing}</p>`;
}

function toggleScoreOverrideForm(submissionId) {
  if (archivedSubmissionIds.has(submissionId)) return;
  const el = document.getElementById(`override-form-${submissionId}`);
  const toggle = document.getElementById(`override-toggle-${submissionId}`);
  const statusEl = document.getElementById(`score-status-${submissionId}`);
  if (el.innerHTML) {
    el.innerHTML = "";
    if (toggle) { toggle.setAttribute("aria-expanded", "false"); toggle.focus(); }
    if (statusEl) { statusEl.textContent = ""; statusEl.classList.remove("is-error"); }
    return;
  }
  const current = el.dataset.currentScore;
  el.innerHTML = `
    <div class="cdd-override-form">
      ${current !== "" && current != null ? `<p class="cdd-card-note muted">Current score: ${escapeHtml(current)} / 100</p>` : ""}
      <div class="cdd-field">
        <label for="override-score-${submissionId}">Final score <span class="muted">(whole number, 0-100, required)</span></label>
        <input id="override-score-${submissionId}" type="number" min="0" max="100" step="1" inputmode="numeric" required aria-describedby="score-status-${submissionId}" />
      </div>
      <div class="cdd-field">
        <label for="override-feedback-${submissionId}">Feedback <span class="muted">(optional - leave blank to keep the current feedback)</span></label>
        <textarea id="override-feedback-${submissionId}"></textarea>
      </div>
      <div class="cdd-field">
        <label for="override-note-${submissionId}">Reason for the override <span class="muted">(required)</span></label>
        <textarea id="override-note-${submissionId}" required aria-describedby="score-status-${submissionId}"></textarea>
      </div>
      <div class="cdd-rcard-actions">
        <button id="override-save-${submissionId}" onclick="saveScoreOverride(${submissionId})">Save override</button>
        <button type="button" class="btn-ghost" onclick="toggleScoreOverrideForm(${submissionId})">Cancel</button>
      </div>
    </div>
  `;
  if (toggle) toggle.setAttribute("aria-expanded", "true");
  document.getElementById(`override-score-${submissionId}`).focus();
}

async function retryScoring(submissionId) {
  if (archivedSubmissionIds.has(submissionId)) return;
  const key = `retry-${submissionId}`;
  if (candidateDetailPending.has(key)) return;
  const statusEl = document.getElementById(`score-status-${submissionId}`);
  const btn = document.getElementById(`retry-btn-${submissionId}`);
  candidateDetailPending.add(key);
  if (btn) btn.disabled = true;
  statusEl.classList.remove("is-error");
  statusEl.textContent = "Retrying - this can take a few seconds...";
  try {
    await api(`/hr/submissions/${submissionId}/retry-scoring`, { method: "POST" });
    candidateDetailPending.delete(key);
    candidatesListStale = true;
    openCandidateDetail(currentCandidateDetailId, { refresh: true, focusRound: submissionId });
  } catch (e) {
    candidateDetailPending.delete(key);
    // The panel may have been re-rendered (or another candidate opened)
    // while this was in flight - only touch elements that still exist.
    if (btn && btn.isConnected) btn.disabled = false;
    if (statusEl.isConnected) { statusEl.textContent = e.message; statusEl.classList.add("is-error"); }
  }
}

async function saveScoreOverride(submissionId) {
  if (archivedSubmissionIds.has(submissionId)) return;
  const key = `override-${submissionId}`;
  if (candidateDetailPending.has(key)) return;
  const statusEl = document.getElementById(`score-status-${submissionId}`);
  const scoreRaw = document.getElementById(`override-score-${submissionId}`).value.trim();
  const feedback_text = document.getElementById(`override-feedback-${submissionId}`).value.trim() || null;
  const override_note = document.getElementById(`override-note-${submissionId}`).value.trim();
  // An empty field must not become Number("") === 0 - that would save a
  // real 0/100 the HR user never typed.
  const scoreInput = document.getElementById(`override-score-${submissionId}`);
  const noteInput = document.getElementById(`override-note-${submissionId}`);
  scoreInput.removeAttribute("aria-invalid");
  noteInput.removeAttribute("aria-invalid");
  statusEl.classList.remove("is-error");
  if (!/^\d+$/.test(scoreRaw) || Number(scoreRaw) > 100) {
    statusEl.textContent = "Enter a whole-number score from 0 to 100.";
    statusEl.classList.add("is-error");
    scoreInput.setAttribute("aria-invalid", "true");
    scoreInput.focus();
    return;
  }
  const final_score = Number(scoreRaw);
  if (!override_note) {
    statusEl.textContent = "Explain why this is being overridden before saving.";
    statusEl.classList.add("is-error");
    noteInput.setAttribute("aria-invalid", "true");
    noteInput.focus();
    return;
  }
  const btn = document.getElementById(`override-save-${submissionId}`);
  candidateDetailPending.add(key);
  if (btn) btn.disabled = true;
  statusEl.textContent = "Saving...";
  try {
    await api(`/hr/submissions/${submissionId}/score`, {
      method: "PATCH",
      body: JSON.stringify({ final_score, feedback_text, override_note }),
    });
    candidateDetailPending.delete(key);
    candidatesListStale = true;
    openCandidateDetail(currentCandidateDetailId, { refresh: true, focusRound: submissionId });
  } catch (e) {
    candidateDetailPending.delete(key);
    if (btn && btn.isConnected) btn.disabled = false;
    if (statusEl.isConnected) { statusEl.textContent = e.message; statusEl.classList.add("is-error"); }
  }
}

// Screen-reader announcement via the persistent #candidate-detail-status
// live region. Cleared first and set on the next tick so repeating the
// same message ("Report updated.") is still announced.
function announceCandidateDetail(message) {
  const el = document.getElementById("candidate-detail-status");
  if (!el) return;
  el.textContent = "";
  setTimeout(() => { el.textContent = message; }, 50);
}

// The detail view's header: identity card, breadcrumb and page title.
// Everything here comes from the candidates list HR just clicked in
// (lastLoadedCandidates) - no extra request, so it shows immediately
// while the report itself is still loading.
function renderCandidateDetailShell(id) {
  const c = lastLoadedCandidates.find((x) => x.id === id);
  const idLabel = `CAND-${String(id).padStart(3, "0")}`;
  const name = c ? c.email : idLabel;
  document.getElementById("candidate-detail-crumb").textContent = name;
  document.getElementById("candidate-detail-identity").innerHTML = `
    <div class="candidate-identity">
      <div class="candidate-avatar cd-avatar" aria-hidden="true">${escapeHtml(c ? candidateInitials(c.email) : "?")}</div>
      <div class="candidate-identity-text">
        <h2 class="cdd-identity-name" id="candidate-detail-heading" tabindex="-1">${escapeHtml(name)}</h2>
        <div class="candidate-id muted">
          ${idLabel}${c && c.exam_date ? ` · Exam ${formatDate(c.exam_date)}` : ""}
          ${c && c.reapplied_within_window ? '<span class="cd-reapplied">Re-applied</span>' : ""}
        </div>
      </div>
    </div>
    ${c ? `<div class="status-cell" id="candidate-detail-result">${resultBadge(candidateStage(c))}</div>` : ""}
  `;
  renderCandidateAssessmentSummary(c);
  setPageHeader("HR Console", "Candidate report", "Scores, evidence and actions for one candidate.");
}

// The dashboard's round-status wording, except "submitted" - in the
// report it reads as waiting on the score rather than as an activity.
const CANDIDATE_DETAIL_STATUS_LABELS = { ...CANDIDATE_ROUND_STATUS_LABELS, submitted: "Awaiting score" };

// Why the result is what it is - the rule itself lives server-side
// (hr.py's _build_candidate_summary): a verdict only once all four
// rounds are scored, aggregate vs the final pass mark.
function candidateResultExplanation(c) {
  const passing = appSettings ? appSettings.final_passing_score : 280;
  if (c.result === "selected") return `Overall score meets the final pass mark of ${passing}.`;
  if (c.result === "not_selected") return `Overall score is below the final pass mark of ${passing}.`;
  return "Final result is decided once all four rounds are scored.";
}

// Assessment summary (Overall / Progress / Result / Integrity) and the
// R1-R4 overview - all from the candidates-list row (CandidateSummaryOut),
// the same data and helpers the dashboard row uses, so the two can't
// disagree.
function renderCandidateAssessmentSummary(c) {
  const box = document.getElementById("candidate-detail-summary");
  if (!c) { box.innerHTML = ""; return; }

  const autoClosed = c.rounds.filter((r) => r.auto_closed_reason);
  const switched = c.rounds.filter((r) => r.tab_switch_count > 0);
  const flagLines = [
    ...switched.map((r) => `R${r.round_number} · Left the test ${r.tab_switch_count} time${r.tab_switch_count === 1 ? "" : "s"}`),
    ...autoClosed.map((r) => `R${r.round_number} · Auto-closed`),
  ];
  const flagCount = flagLines.length;
  const integrity = flagCount
    ? `<div class="cdd-card-value cdd-integrity-flagged"><span aria-hidden="true">&#9888;</span> ${flagCount} flag${flagCount === 1 ? "" : "s"}</div>
       <ul class="cdd-flag-list">${flagLines.map((l) => `<li>${escapeHtml(l)}</li>`).join("")}</ul>`
    : `<div class="cdd-card-value"><span class="cdd-ok-mark" aria-hidden="true">&#10003;</span> No flags</div>
       <p class="cdd-card-note muted">No tab switches or auto-closed rounds.</p>`;

  const rounds = c.rounds.map((r) => {
    const label = CANDIDATE_DETAIL_STATUS_LABELS[r.status] || r.status;
    const passing = passingScoreForRound(r.round_number);
    let body;
    if (r.final_score != null) {
      const passed = r.final_score >= passing;
      body = `
        <div class="cdd-round-score">
          <span class="cd-overall-score ${passed ? "score-good" : "score-bad"}">${r.final_score}</span><span class="cd-overall-max muted">/ 100</span>
        </div>
        <div class="readout-bar cdd-round-bar${passed ? "" : " is-bad"}" style="--pct:${Math.min(100, r.final_score)}%" aria-hidden="true">
          <i></i><b class="cd-pass-marker" style="left:${Math.min(100, passing)}%"></b>
        </div>
        <div class="cd-pass-caption ${passed ? "is-pass" : "is-fail"}"><span aria-hidden="true">${passed ? "&#10003;" : "&#10005;"}</span> ${passed ? "Meets" : "Below"} pass mark ${passing}</div>`;
    } else {
      body = `<div class="cdd-card-note muted">No score yet &middot; pass mark ${passing}</div>`;
    }
    return `
      <li class="cdd-round is-${escapeAttr(r.status)}">
        <div class="cdd-round-head">
          <span class="cd-round-label">R${r.round_number}</span>
          <span class="cdd-round-name">${escapeHtml(ROUND_LABELS[r.round_number] || `Round ${r.round_number}`)}</span>
        </div>
        <div class="cdd-round-status">${escapeHtml(label)}</div>
        ${body}
        ${r.submitted_at ? `<div class="cdd-card-note muted">Submitted ${formatDateTime(r.submitted_at)}</div>` : ""}
      </li>
    `;
  }).join("");

  box.innerHTML = `
    <section class="cdd-section" aria-labelledby="cdd-summary-title">
      <h3 class="cdd-section-title" id="cdd-summary-title">Assessment summary</h3>
      <div class="cdd-summary-cards">
        <div class="cdd-card">
          <h4 class="cdd-card-label">Overall</h4>
          ${aggregateCell(c)}
        </div>
        <div class="cdd-card">
          <h4 class="cdd-card-label">Progress</h4>
          ${candidateProgressCell(c)}
        </div>
        <div class="cdd-card">
          <h4 class="cdd-card-label">Result</h4>
          <div class="status-cell">${resultBadge(candidateStage(c))}</div>
          <p class="cdd-card-note muted">${escapeHtml(candidateResultExplanation(c))}</p>
        </div>
        <div class="cdd-card">
          <h4 class="cdd-card-label">Integrity</h4>
          ${integrity}
        </div>
      </div>
    </section>
    <section class="cdd-section" aria-labelledby="cdd-rounds-title">
      <h3 class="cdd-section-title" id="cdd-rounds-title">Rounds</h3>
      <ol class="cdd-rounds">${rounds}</ol>
    </section>
  `;
}

// After a retry/override the report re-renders in place; the summary and
// the identity badge come from the candidates list, so re-fetch that (the
// existing GET /hr/candidates) and repaint just those two.
async function refreshCandidateDetailSummary(id, seq) {
  let list;
  try {
    list = await api("/hr/candidates");
  } catch (e) {
    return; // the report itself is already up to date; the cards stay one step behind
  }
  if (seq !== candidateDetailSeq) return;
  lastLoadedCandidates = list;
  const c = list.find((x) => x.id === id);
  if (!c) return;
  const badge = document.getElementById("candidate-detail-result");
  if (badge) badge.innerHTML = resultBadge(candidateStage(c));
  renderCandidateAssessmentSummary(c);
}

// Whatever actually scrolls the page: .app-main on wider screens, the
// document itself below the mobile breakpoint (see style.css, where
// .app-main becomes overflow-y: visible).
function pageScroller() {
  const main = document.querySelector(".app-main");
  return main && /(auto|scroll)/.test(getComputedStyle(main).overflowY) ? main : document.scrollingElement;
}

// opts.refresh: re-render the already-open candidate after a retry/
// override - no loading placeholder and no scroll jump, just swap in the
// fresh data where HR already is.
async function openCandidateDetail(id, opts = {}) {
  const seq = ++candidateDetailSeq;
  const box = document.getElementById("candidate-detail");
  currentCandidateDetailId = id;
  if (!opts.refresh) {
    const view = document.getElementById("candidate-detail-view");
    const page = document.getElementById("hr-page-candidates");
    // Only when coming from the list - "Try again" re-opens from inside
    // the detail view and must keep the original return point.
    if (view.classList.contains("hidden")) {
      const c = lastLoadedCandidates.find((x) => x.id === id);
      candidateDetailReturn = { scrollTop: pageScroller().scrollTop, email: c ? c.email : null };
    }
    view.classList.remove("hidden");
    page.classList.add("is-detail-open");
    candidateAppearances = [];
    renderCandidateDetailShell(id);   // resets .app-main's scroll (setPageHeader)...
    // ...which is all it takes on wider screens. On mobile the document
    // scrolls instead, with the nav stacked above - bring the page title
    // into view so HR lands at the top of the report, not mid-way down.
    if (pageScroller() !== document.querySelector(".app-main")) {
      document.querySelector(".app-topbar").scrollIntoView({ block: "start" });
    }
    // The button HR activated is now hidden - move focus to the heading
    // so keyboard and screen-reader users land at the top of the report.
    document.getElementById("candidate-detail-heading").focus({ preventScroll: true });
    announceCandidateDetail("Loading candidate report.");
    box.innerHTML = `<div aria-busy="true">${loadingHtml("Loading report...")}</div>`;
  }

  let submissions;
  try {
    submissions = await api(`/hr/candidates/${id}/report`);
  } catch (e) {
    if (seq !== candidateDetailSeq) return;
    box.innerHTML = `
      <div class="banner banner-error" role="alert">
        <span class="banner-icon" aria-hidden="true">!</span>
        <div>
          <strong>Couldn't load this candidate's report</strong>
          <p class="muted">${escapeHtml(e.message)}</p>
          <button type="button" class="btn-secondary btn-sm" onclick="openCandidateDetail(${Number(id)})">Try again</button>
        </div>
      </div>
    `;
    return;
  }
  // HR opened another candidate (or this one again) while this request
  // was in flight - that newer call owns the panel now.
  if (seq !== candidateDetailSeq) return;

  candidateSummaryData = null;       // stale from whatever candidate was open before
  candidateDetailSubmissions = submissions; // so generateCandidateSummary can label each round's comment with its real title/score
  appearanceDetailSeq++;             // drop any past-appearance drill-down still loading for the previous render

  setCandidateDetailArchived(null);
  box.innerHTML = `
    ${candidateSummaryPanelHtml(id, false)}
    <h3 class="cdd-section-title cdd-report-section">Round details</h3>
    ${renderSubmissionsPanels(submissions, { allRounds: true })}
    ${pastAppearancesPanelHtml()}
  `;
  loadAppearances(id, seq);
  loadExistingCandidateSummary(id, seq);
  if (opts.refresh) refreshCandidateDetailSummary(id, seq);
  if (opts.focusRound) {
    const heading = document.getElementById(`round-${opts.focusRound}-title`);
    if (heading) heading.focus({ preventScroll: true });
  }
  if (opts.fromArchive) document.getElementById("candidate-detail-heading").focus({ preventScroll: true });
  // The load-failure banner above is role="alert" and announces itself.
  announceCandidateDetail(opts.fromArchive ? "Current report loaded." : opts.refresh ? "Report updated." : "Candidate report loaded.");
}

// Same two sections in the current and the archived report. The AI
// summary is saved per candidate and built from the CURRENT cycle only
// (hr.py's _gather_candidate_rounds) - on an archived appearance it's
// shown for reference with PDF only, since Generate/Regenerate/Delete
// would overwrite or remove the candidate's one saved summary.
function candidateSummaryPanelHtml(id, readOnly) {
  return `
    <section class="panel-inset cdd-ai-summary" aria-labelledby="cdd-ai-summary-title">
      <div class="cdd-panel-head">
        <h3 class="cdd-panel-title" id="cdd-ai-summary-title">AI summary</h3>
        <div id="candidate-summary-controls" class="cdd-rcard-actions">
          ${readOnly ? "" : `<button onclick="generateCandidateSummary(${Number(id)})">Generate Summary</button>`}
        </div>
      </div>
      <p class="cdd-card-note muted">${readOnly
        ? "Covers the candidate's current cycle, not this archived appearance. Read-only here - generate, regenerate and delete from the current report."
        : "A crisp, cross-round synthesis for feedback to the candidate or a briefing for the next round's interviewers."}</p>
      <div id="candidate-summary-body"></div>
    </section>
  `;
}

function pastAppearancesPanelHtml() {
  return `
    <section class="panel-inset cdd-appearances" aria-labelledby="cdd-appearances-title">
      <h3 class="cdd-panel-title" id="cdd-appearances-title">Past appearances</h3>
      <p class="cdd-card-note muted">Every upload cycle for this candidate - re-applying resets the current attempt but keeps the old one here, read-only.</p>
      <div id="appearances-list"></div>
    </section>
  `;
}

// Enter / leave archived mode: the header cards (Phase 2 summary) describe
// the current cycle, so they're hidden while an archived appearance is on
// screen, and the breadcrumb says which cycle this is.
function setCandidateDetailArchived(a) {
  candidateDetailArchived = a;
  if (!a) archivedSubmissionIds.clear();
  document.getElementById("candidate-detail-view").classList.toggle("is-archived", !!a);
  const heading = document.getElementById("candidate-detail-heading");
  const name = heading ? heading.textContent : "";
  document.getElementById("candidate-detail-crumb").textContent = a ? `${name} - archived appearance (exam ${formatDate(a.exam_date)})` : name;
}

function scrollCandidateDetailToTop() {
  const main = document.querySelector(".app-main");
  if (pageScroller() === main) main.scrollTop = 0;
  else document.querySelector(".app-topbar").scrollIntoView({ block: "start" });
}

// Back from an archived appearance to the candidate's current report,
// in the same detail view (identity, breadcrumb and Back stay as they are).
function showCurrentCandidateReport() {
  if (currentCandidateDetailId == null) return;
  setCandidateDetailArchived(null);
  scrollCandidateDetailToTop();
  document.getElementById("candidate-detail").innerHTML = `<div aria-busy="true">${loadingHtml("Loading report...")}</div>`;
  openCandidateDetail(currentCandidateDetailId, { refresh: true, fromArchive: true });
}

// Back to the candidates list. The list was only hidden, so its search,
// status filter and page are untouched; this restores the scroll
// position and puts focus back on the row's "View report" button.
// opts.silent: HR is navigating somewhere else (another HR page, a fresh
// login) - just tear the view down, the caller sets the page header.
async function closeCandidateDetail(opts = {}) {
  const view = document.getElementById("candidate-detail-view");
  if (view.classList.contains("hidden")) return;
  // Anything still in flight for the report being closed drops itself.
  candidateDetailSeq++;
  appearanceDetailSeq++;
  currentCandidateDetailId = null;
  candidateSummaryData = null;
  view.classList.add("hidden");
  document.getElementById("hr-page-candidates").classList.remove("is-detail-open");
  document.getElementById("candidate-detail").innerHTML = "";
  document.getElementById("candidate-detail-identity").innerHTML = "";
  document.getElementById("candidate-detail-summary").innerHTML = "";
  candidateDetailArchived = null;
  archivedSubmissionIds.clear();
  candidateAppearances = [];
  view.classList.remove("is-archived");
  const ret = candidateDetailReturn || { scrollTop: 0, email: null };
  candidateDetailReturn = null;
  if (opts.silent) {
    if (candidatesListStale) {
      candidatesListStale = false;
      api("/hr/candidates").then((data) => { lastLoadedCandidates = data; renderCandidatesKpis(); renderCandidatesTable(); }).catch(() => {});
    }
    return;
  }

  setPageHeader("HR Console", "Candidates", "Every candidate's progress and results, across all rounds.");
  // Matched by attribute value, not a CSS selector built from the email
  // (candidate-controlled via bulk upload).
  const focusRowButton = () => {
    const btn = ret.email && [...document.querySelectorAll("#candidates-table .cd-view-btn")]
      .find((b) => b.getAttribute("aria-label") === `View report for ${ret.email}`);
    (btn || document.getElementById("candidates-search")).focus({ preventScroll: true });
    pageScroller().scrollTop = ret.scrollTop;
  };
  focusRowButton();

  if (!candidatesListStale) return;
  // A score changed while the report was open - refresh the numbers
  // without resetting search/filter/page (unlike loadCandidates, which
  // starts over at page 1).
  candidatesListStale = false;
  try {
    lastLoadedCandidates = await api("/hr/candidates");
  } catch (e) {
    return; // the list on screen is still usable, just one refresh behind
  }
  if (!document.getElementById("candidate-detail-view").classList.contains("hidden")) return; // reopened meanwhile
  const hadFocus = document.activeElement && document.activeElement.closest("#candidates-table");
  renderCandidatesKpis();
  renderCandidatesTable();
  if (hadFocus) focusRowButton();
}

async function loadAppearances(candidateId, seq) {
  const listEl = document.getElementById("appearances-list");
  listEl.innerHTML = loadingHtml();
  let appearances;
  try {
    appearances = await api(`/hr/candidates/${candidateId}/appearances`);
  } catch (e) {
    if (seq !== candidateDetailSeq) return;
    listEl.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
    announceCandidateDetail(`Couldn't load past appearances: ${e.message}`);
    return;
  }
  if (seq !== candidateDetailSeq) return;
  candidateAppearances = appearances;
  if (appearances.length === 0) {
    listEl.innerHTML = `<p class="muted">No upload history - this candidate wasn't created via bulk upload.</p>`;
    return;
  }
  const current = appearances.find((a) => a.is_current);
  const viewing = candidateDetailArchived ? candidateDetailArchived.appearanceId : current && current.id;
  listEl.innerHTML = `
    <div class="table-scroll">
      <table class="cdd-appearances-table">
        <caption class="sr-only">Upload cycles for this candidate</caption>
        <thead>
          <tr><th scope="col">Exam date</th><th scope="col">Uploaded</th><th scope="col">Status</th><th scope="col">Overall</th><th scope="col"><span class="sr-only">Actions</span></th></tr>
        </thead>
        <tbody>
          ${appearances.map((a) => `
            <tr class="${a.id === viewing ? "is-viewing" : ""}" ${a.id === viewing ? 'aria-current="true"' : ""}>
              <th scope="row">${formatDate(a.exam_date)}</th>
              <td>${formatDateTime(a.created_at)}</td>
              <td>
                <span class="cdd-status-pill ${a.is_current ? "is-current" : "is-archived"}">${a.is_current ? "Current" : "Archived"}</span>
                ${a.reapplied_within_window ? '<span class="cd-reapplied">Re-applied</span>' : ""}
              </td>
              <td>${a.aggregate_score != null ? `<span class="cdd-appearance-score">${a.aggregate_score}</span><span class="muted"> / 400</span>` : `<span class="muted">Not scored</span>`}</td>
              <td class="cdd-appearance-action">
                ${a.id === viewing ? `<span class="muted">Viewing</span>`
                  : a.is_current ? `<button type="button" class="cd-view-btn" onclick="showCurrentCandidateReport()">View current report <span aria-hidden="true">&rsaquo;</span></button>`
                  : `<button type="button" class="cd-view-btn" onclick="viewAppearance(${Number(candidateId)}, ${Number(a.id)})" aria-label="View archived report, exam ${formatDate(a.exam_date)}">View report <span aria-hidden="true">&rsaquo;</span></button>`}
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

// An archived appearance opens in the same detail view, read-only: its
// rounds and evidence, the (current-cycle) AI summary for reference, and
// the appearances list to move between cycles. Archived vs current comes
// from the appearances API's own is_current flag.
async function viewAppearance(candidateId, appearanceId) {
  const meta = candidateAppearances.find((a) => a.id === appearanceId);
  if (!meta) return;
  if (meta.is_current) { showCurrentCandidateReport(); return; }
  const seq = ++candidateDetailSeq;
  appearanceDetailSeq++;
  const box = document.getElementById("candidate-detail");
  setCandidateDetailArchived({ appearanceId, exam_date: meta.exam_date, created_at: meta.created_at, aggregate_score: meta.aggregate_score });
  scrollCandidateDetailToTop();
  box.innerHTML = `<div aria-busy="true">${loadingHtml("Loading archived report...")}</div>`;
  announceCandidateDetail("Loading archived report.");
  let submissions;
  try {
    submissions = await api(`/hr/candidates/${candidateId}/appearances/${appearanceId}/report`);
  } catch (e) {
    if (seq !== candidateDetailSeq) return;
    box.innerHTML = `
      <div class="banner banner-error" role="alert">
        <span class="banner-icon" aria-hidden="true">!</span>
        <div>
          <strong>Couldn't load this archived report</strong>
          <p class="muted">${escapeHtml(e.message)}</p>
          <div class="cdd-rcard-actions">
            <button type="button" class="btn-secondary btn-sm" onclick="viewAppearance(${Number(candidateId)}, ${Number(appearanceId)})">Try again</button>
            <button type="button" class="btn-ghost btn-sm" onclick="showCurrentCandidateReport()">Back to current report</button>
          </div>
        </div>
      </div>
    `;
    return;
  }
  if (seq !== candidateDetailSeq) return;
  archivedSubmissionIds.clear();
  submissions.forEach((sub) => archivedSubmissionIds.add(sub.id));
  box.innerHTML = `
    <div class="cdd-readonly-banner">
      <h3 class="cdd-readonly-title" id="cdd-readonly-title" tabindex="-1">Read-only &mdash; archived appearance</h3>
      <p>Exam ${formatDate(meta.exam_date)} &middot; uploaded ${formatDateTime(meta.created_at)}${meta.aggregate_score != null ? ` &middot; overall ${meta.aggregate_score} / 400` : ""}. Scores, evidence and audit details can be viewed; Retry scoring and Override are not available for archived appearances.</p>
      <div class="cdd-rcard-actions"><button type="button" class="btn-secondary btn-sm" onclick="showCurrentCandidateReport()">Back to current report</button></div>
    </div>
    ${candidateSummaryPanelHtml(candidateId, true)}
    <h3 class="cdd-section-title cdd-report-section">Round details</h3>
    ${renderSubmissionsPanels(submissions, { readOnly: true })}
    ${pastAppearancesPanelHtml()}
  `;
  loadAppearances(candidateId, seq);
  loadExistingCandidateSummary(candidateId, seq, { readOnly: true });
  document.getElementById("cdd-readonly-title").focus({ preventScroll: true });
  announceCandidateDetail("Archived appearance loaded. Read-only.");
}

// Tries to load a summary saved from an earlier visit (see models.
// CandidateSummary) - no LLM call, so opening a candidate HR has already
// summarized before shows it and its Download/Regenerate/Delete controls
// immediately, rather than making HR click Generate again just to get
// back something that already exists. A 404 here is the normal "nothing
// generated yet" case, not an error - the static "Generate Summary"
// button already in the panel is left exactly as it is.
async function loadExistingCandidateSummary(id, seq, opts = {}) {
  let result;
  try {
    result = await api(`/hr/candidates/${id}/summary`);
  } catch (e) {
    // Current report: leave the initial "Generate Summary" button in place.
    if (opts.readOnly && seq === candidateDetailSeq) {
      document.getElementById("candidate-summary-body").innerHTML = `<p class="muted">No summary has been generated for this candidate yet.</p>`;
    }
    return;
  }
  if (seq !== candidateDetailSeq) return;
  renderCandidateSummary(id, result, opts);
}

// Disables every summary button (Generate/Regenerate/Download/Delete)
// while one of them is in flight - they all act on the same saved
// summary, so none should run alongside another.
function setCandidateSummaryControlsBusy(busy) {
  document.querySelectorAll("#candidate-summary-controls button").forEach((b) => { b.disabled = busy; });
}

async function generateCandidateSummary(id) {
  if (candidateDetailArchived) return;
  const key = `summary-${id}`;
  if (candidateDetailPending.has(key)) return;
  const seq = candidateDetailSeq;
  const body = document.getElementById("candidate-summary-body");
  candidateDetailPending.add(key);
  setCandidateSummaryControlsBusy(true);
  body.innerHTML = `<p class="muted">Generating (a few seconds)...</p>`;
  announceCandidateDetail("Generating summary.");
  let result;
  try {
    result = await api(`/hr/candidates/${id}/summary`, { method: "POST" });
  } catch (e) {
    candidateDetailPending.delete(key);
    if (seq !== candidateDetailSeq) return;
    body.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
    setCandidateSummaryControlsBusy(false);
    announceCandidateDetail(`Couldn't generate the summary: ${e.message}`);
    return;
  }
  candidateDetailPending.delete(key);
  // Saved server-side either way; only paint it if HR is still looking
  // at this candidate's panel.
  if (seq !== candidateDetailSeq) return;
  renderCandidateSummary(id, result);
  announceCandidateDetail("Summary generated.");
}

// Shared by both paths above - a freshly-generated summary and one
// loaded back from an earlier visit render identically, since both are
// now just "whatever's currently saved" (see models.CandidateSummary).
function renderCandidateSummary(id, result, opts = {}) {
  candidateSummaryData = result;

  const bulletList = (items, variant) => items.length
    ? `<ul class="summary-bullets ${variant}">${items.map((b) => `<li>${escapeHtml(b)}</li>`).join("")}</ul>`
    : `<p class="summary-bullets-empty">Nothing to note.</p>`;

  // Reuses the existing .badge/.badge-pass/.badge-fail chip vocabulary
  // (same one the candidates table and round-history views use for
  // status pills) instead of inventing new score styling - one visual
  // language for "pass" and "fail" across the whole app.
  const roundBlocks = result.round_comments.map((rc) => {
    const submission = candidateDetailSubmissions.find((s) => s.round_number === rc.round_number);
    const label = ROUND_LABELS[rc.round_number] || `Round ${rc.round_number}`;
    const passed = submission && submission.score && submission.score.final_score >= passingScoreForRound(rc.round_number);
    const scoreChip = submission && submission.score
      ? `<span class="badge badge-score ${passed ? "badge-pass" : "badge-fail"}"><span aria-hidden="true">${passed ? "&#10003;" : "&#10005;"}</span> ${submission.score.final_score}/100<span class="sr-only">, ${passed ? "meets" : "below"} pass mark</span></span>`
      : submission && submission.status === "scoring_failed"
        ? `<span class="badge badge-score badge-fail">Scoring failed</span>`
        : `<span class="badge badge-score">Not scored yet</span>`;
    const accentClass = !submission || !submission.score ? "" : passed ? "round-pass" : "round-fail";
    return `
      <div class="panel-inset summary-round ${accentClass}">
        <div class="summary-round-head">
          <h4><span class="summary-round-number">${String(rc.round_number).padStart(2, "0")}</span>${escapeHtml(label)}</h4>
          ${scoreChip}
        </div>
        <div class="summary-cols">
          <div>
            <div class="summary-col-label did-well">What went well</div>
            ${bulletList(rc.did_well, "did-well")}
          </div>
          <div>
            <div class="summary-col-label missed">What was missed</div>
            ${bulletList(rc.missed, "missed")}
          </div>
        </div>
      </div>
    `;
  }).join("");

  // Verdict + key observations lead the panel (executive summary first,
  // detail below) - the way an actual scorecard reads, not the order
  // the LLM happens to return fields in.
  document.getElementById("candidate-summary-body").innerHTML = `
    <div class="summary-verdict">
      <span class="summary-verdict-label">Overall verdict</span>
      <p class="summary-verdict-text">${escapeHtml(result.verdict)}</p>
    </div>
    <div class="panel-inset summary-observations-panel">
      <h4>Key Observations</h4>
      <ul class="summary-observations">${result.key_observations.map((o) => `<li>${escapeHtml(o)}</li>`).join("")}</ul>
    </div>
    ${roundBlocks}
    ${result.generated_at ? `<p class="muted">Generated ${formatDateTime(result.generated_at)}</p>` : ""}
  `;
  // id only, not the candidate's email (attacker-controlled via bulk
  // upload) - a value inside onclick="...'...'" isn't made safe by
  // escapeHtml, which only escapes for a text node, not for sitting
  // inside a quoted attribute (same gap noted on deleteScenarioFromList's
  // button - see lastLoadedScenarios above). downloadCandidateSummaryPdf/
  // deleteCandidateSummary read the email straight from
  // candidateSummaryData instead - already fetched, right above.
  document.getElementById("candidate-summary-controls").innerHTML = opts.readOnly
    ? `<button onclick="downloadCandidateSummaryPdf(${id})">Download as PDF</button>`
    : `
    <button onclick="generateCandidateSummary(${id})">Regenerate</button>
    <button onclick="downloadCandidateSummaryPdf(${id})">Download as PDF</button>
    <button class="btn-danger" onclick="deleteCandidateSummary(${id})">Delete</button>
  `;
}

async function downloadCandidateSummaryPdf(id) {
  const key = `summary-${id}`;
  if (candidateDetailPending.has(key) || !candidateSummaryData) return;
  // Captured now - candidateSummaryData is reset if HR opens another
  // candidate while the PDF is still being rendered.
  const candidateEmail = candidateSummaryData.candidate_email;
  candidateDetailPending.add(key);
  setCandidateSummaryControlsBusy(true);
  // Not api() on purpose - that helper always calls res.json(), which
  // would fail on this endpoint's binary PDF response.
  try {
    // fetch() itself can reject (network failure) before there's even a
    // response to check .ok on - same gap login() had, same fix. No body
    // to send anymore either - the server renders whatever's currently
    // saved (see models.CandidateSummary), not a client-supplied copy.
    const res = await fetch(`/hr/candidates/${id}/summary/pdf`, { method: "POST" });
    if (!res.ok) {
      alert("Couldn't generate the PDF - try again.");
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    // candidateSummaryData.candidate_email, not a value threaded through
    // an onclick="..." string (the candidate's own email, attacker-
    // controlled via bulk upload) - see the fix note on
    // renderCandidateSummary's controls above for why that mattered.
    a.download = `${candidateEmail.replace("@", "_at_").replace(/\./g, "_")}-summary.pdf`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    alert("Couldn't reach the server - check your connection and try again.");
  } finally {
    candidateDetailPending.delete(key);
    setCandidateSummaryControlsBusy(false);
  }
}

// One PDF per screening day - every candidate whose current appearance's
// exam_date matches, with marks/comment/result (see hr.py's
// daily_summary_pdf). Never generates anything - the comment column is
// whatever AI summary a candidate already has, "Not generated yet"
// otherwise - so this is safe to click at any time.
async function downloadDailySummary() {
  const dateInput = document.getElementById("daily-summary-date");
  const statusEl = document.getElementById("daily-summary-status");
  statusEl.className = "";
  statusEl.textContent = "";
  if (!dateInput.value) {
    statusEl.className = "error-text";
    statusEl.textContent = "Pick a date first.";
    return;
  }
  try {
    const res = await fetch(`/hr/reports/daily-summary?exam_date=${dateInput.value}`);
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      statusEl.className = "error-text";
      statusEl.textContent = (body && body.detail) || "Couldn't generate the summary - try again.";
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `cohort-${dateInput.value}.pdf`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    statusEl.className = "error-text";
    statusEl.textContent = "Couldn't reach the server - check your connection and try again.";
  }
}

async function deleteCandidateSummary(id) {
  if (candidateDetailArchived) return;
  const key = `summary-${id}`;
  if (candidateDetailPending.has(key)) return;
  if (!confirm("Delete this candidate's saved summary? You can generate a new one anytime, but this exact copy will be gone.")) return;
  const seq = candidateDetailSeq;
  candidateDetailPending.add(key);
  setCandidateSummaryControlsBusy(true);
  try {
    await api(`/hr/candidates/${id}/summary`, { method: "DELETE" });
  } catch (e) {
    candidateDetailPending.delete(key);
    if (seq !== candidateDetailSeq) return;
    setCandidateSummaryControlsBusy(false);
    alert(e.message);
    return;
  }
  candidateDetailPending.delete(key);
  if (seq !== candidateDetailSeq) return;
  candidateSummaryData = null;
  document.getElementById("candidate-summary-body").innerHTML = "";
  document.getElementById("candidate-summary-controls").innerHTML = `
    <button onclick="generateCandidateSummary(${id})">Generate Summary</button>
  `;
  announceCandidateDetail("Summary deleted.");
}

function renderRound4Report(s) {
  // Round 3's content is always null now - the candidate's own,
  // self-titled test cases (s.test_cases) and their transcripts
  // (s.conversation_turns, grouped by test_case_id) ARE the submission.
  // Same execution-trace rendering as the candidate sees, deliberately -
  // HR reviewing "was the candidate's reasoning sound" only needs the
  // same observable evidence the candidate had, not the hidden code either.
  if (!s.test_cases) return "";
  if (s.test_cases.length === 0) return `<p class="muted">No test cases.</p>`;

  const turnsByTestCase = {};
  for (const t of s.conversation_turns || []) {
    (turnsByTestCase[t.test_case_id] = turnsByTestCase[t.test_case_id] || []).push(t);
  }

  return s.test_cases.map((tc, i) => `
    <h5>${escapeHtml(tc.title || `Test case ${i + 1}`)}</h5>
    ${(turnsByTestCase[tc.id] || []).map((t) => `
      <div class="panel-inset">
        <p class="muted">Turn ${t.turn_number}</p>
        <p><strong>Candidate:</strong> ${escapeHtml(t.candidate_prompt)}</p>
        <p>${escapeHtml(t.model_response.response_text)}</p>
        ${renderExecutionSteps(t.model_response.steps)}
        <div class="observed-box">
          <span class="badge badge-${t.model_response.status}">${t.model_response.status}</span>
          ${escapeHtml(t.model_response.observed_result)}
        </div>
      </div>
    `).join("") || `<p class="muted">No messages sent in this test case.</p>`}
  `).join("");
}

// Round 3's content is always null now - the turn-by-turn transcript
// (s.round3_turns) and the run history (s.round3_runs) ARE the
// submission, same as round 4's turns/test_cases above. Each turn shows
// the candidate's instruction and the assistant's classified response
// (clarify/refuse/code_edit, surfaced as a badge so HR can see at a
// glance whether the candidate was steering with enough precision to get
// real code out of the assistant); runs are shown separately below,
// newest first, since a run isn't tied to one specific turn server-side.
const ROUND3_RESPONSE_KIND_LABELS = { clarify: "Asked to clarify", refuse: "Refused", code_edit: "Code edit", direct_edit: "Direct edit", explain: "Explained" };
const ROUND3_RESPONSE_KIND_BADGE = { clarify: "badge-partial", refuse: "badge-fail", code_edit: "badge-pass", direct_edit: "badge-pass", explain: "badge-neutral" };

// The four sub-scores behind Round 3's final_score (see
// models.Score.correctness_score and its siblings, and
// round3_coding_scoring.txt's Evaluate section) - shown as a breakdown
// so HR can see WHERE a candidate scored or fell short, not just the
// single blended number renderScoreBlock already shows above this.
const ROUND3_SCORE_DIMENSIONS = [
  ["correctness_score", "Correctness"],
  ["precision_score", "Precision"],
  ["efficiency_score", "Efficiency"],
  ["independent_judgment_score", "Independent judgment"],
];

function renderRound3ScoreBreakdown(score) {
  // Absent for a submission that's never been scored, or one scored
  // before these columns existed (see migrate_round3_subscores.py) -
  // never render a breakdown of nothing.
  if (!score || ROUND3_SCORE_DIMENSIONS.every(([key]) => score[key] == null)) return "";
  return `
    <div class="panel-inset">
      <p class="muted round3-pane-label">Score breakdown</p>
      <div class="round3-score-breakdown">
        ${ROUND3_SCORE_DIMENSIONS.map(([key, label]) => {
          const value = score[key];
          if (value == null) return "";
          return `
            <div class="round3-score-dim">
              <span class="round3-score-dim-label">${label}</span>
              <div class="round3-score-bar"><div class="round3-score-bar-fill" style="width:${value}%"></div></div>
              <span class="round3-score-dim-value">${value}/100</span>
            </div>
          `;
        }).join("")}
      </div>
    </div>
  `;
}

// The candidate's FINAL code's pass/fail result against every one of
// HR's reference test cases (see models.Score.test_results_json,
// computed in scoring_service.score_round3_submission) - the objective
// evidence behind coverage_score, so HR can see exactly which cases
// passed/failed rather than only the aggregate percentage
// renderScoreBlock already shows.
function renderRound3TestResultsTable(score) {
  if (!score || !score.test_results_json || score.test_results_json.length === 0) return "";
  const rows = score.test_results_json;
  const passedCount = rows.filter((r) => r.passed).length;
  return `
    <div class="panel-inset">
      <p class="muted round3-pane-label">Test cases - ${passedCount}/${rows.length} passed</p>
      <div class="table-scroll">
        <table>
          <thead><tr><th>Input</th><th>Expected</th><th>Actual</th><th></th></tr></thead>
          <tbody>
            ${rows.map((r) => `
              <tr>
                <td><code>${escapeHtml(r.input)}</code></td>
                <td><code>${escapeHtml(String(r.expected_output))}</code></td>
                <td><code>${escapeHtml(r.actual_output == null ? "(none)" : String(r.actual_output))}</code></td>
                <td><span class="badge ${r.passed ? "badge-pass" : "badge-fail"}">${r.passed ? "Passed" : "Failed"}</span></td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function renderRound3Report(s) {
  if (!s.round3_turns) return "";
  if (s.round3_turns.length === 0) return `<p class="muted">No messages sent.</p>`;

  // A count per response_kind reads at a glance ("14 turns - 5 code
  // edits, 6 clarifications, 3 refusals") - the full line-by-line
  // transcript below is collapsed by default (see the <details> below);
  // renderScoreBlock's feedback_text (always shown above this, for every
  // round) is the actual prose summary of how the session went - this is
  // just enough shape to judge at a glance whether it's worth opening.
  const counts = s.round3_turns.reduce((acc, t) => {
    acc[t.response_kind] = (acc[t.response_kind] || 0) + 1;
    return acc;
  }, {});
  const turnSummaryParts = [
    counts.direct_edit ? `${counts.direct_edit} direct edit${counts.direct_edit === 1 ? "" : "s"}` : null,
    counts.code_edit ? `${counts.code_edit} code edit${counts.code_edit === 1 ? "" : "s"}` : null,
    counts.clarify ? `${counts.clarify} clarif${counts.clarify === 1 ? "y" : "ies"}` : null,
    counts.refuse ? `${counts.refuse} refusal${counts.refuse === 1 ? "" : "s"}` : null,
  ].filter(Boolean).join(", ");

  const turnsHtml = s.round3_turns.map((t) => `
    <div class="panel-inset round3-coding-turn">
      <p class="muted">Turn ${t.turn_number}</p>
      <p><strong>Candidate:</strong> ${escapeHtml(t.candidate_prompt)}</p>
      <p><strong>Assistant:</strong> <span class="badge ${ROUND3_RESPONSE_KIND_BADGE[t.response_kind] || ""}">${escapeHtml(ROUND3_RESPONSE_KIND_LABELS[t.response_kind] || t.response_kind)}</span> ${escapeHtml(t.response_message)}</p>
      ${t.code_after ? `<pre class="code-snippet">${escapeHtml(t.code_after)}</pre>` : ""}
    </div>
  `).join("");

  const runsHtml = (s.round3_runs || []).map((r) => `
    <div class="panel-inset round3-coding-run">
      <p class="muted">Run at ${formatDateTime(r.created_at)} -${r.timed_out ? "timed out" : r.infra_error ? "execution service error" : `exit code ${r.exit_code}`}</p>
      ${r.stdin_json && r.stdin_json.length > 0 ? `<p class="muted">Typed: ${escapeHtml(r.stdin_json.join(" / "))}</p>` : ""}
      ${r.stdout ? `<pre class="code-snippet">${escapeHtml(r.stdout)}</pre>` : ""}
      ${r.stderr ? `<pre class="code-snippet round3-coding-stderr">${escapeHtml(r.stderr)}</pre>` : ""}
    </div>
  `).join("") || `<p class="muted">No runs.</p>`;

  return `
    ${renderRound3ScoreBreakdown(s.score)}
    ${renderRound3TestResultsTable(s.score)}
    <details class="hint-box">
      <summary><strong>Full conversation</strong> - ${s.round3_turns.length} turn${s.round3_turns.length === 1 ? "" : "s"}${turnSummaryParts ? ` (${turnSummaryParts})` : ""}</summary>
      ${turnsHtml}
    </details>
    <details class="hint-box">
      <summary><strong>Run history</strong> - ${(s.round3_runs || []).length} run${(s.round3_runs || []).length === 1 ? "" : "s"}</summary>
      ${runsHtml}
    </details>
  `;
}

function renderSideBySide(candidateRows, referenceRows) {
  // Round 1 only now - round 2's candidate submission is investigation-
  // shaped (see renderRound2Report) and round 3's is a conversation
  // (see renderRound4Report), neither fits this title/steps/expected
  // row-vs-row comparison.
  if (!candidateRows) return "";
  const renderTable = (rows) => `
    <table>
      <thead><tr><th>Title</th><th>Steps</th><th>Test data</th><th>Expected</th></tr></thead>
      <tbody>
        ${(rows || []).map((r) => `<tr><td>${escapeHtml(r.title)}</td><td>${escapeHtml(r.steps)}</td><td>${escapeHtml(r.test_data || "")}</td><td>${escapeHtml(r.expected_result)}</td></tr>`).join("") || '<tr><td colspan="4" class="muted">-</td></tr>'}
      </tbody>
    </table>`;
  return `
    <div class="side-by-side">
      <div><p class="muted">Candidate's submission</p><div class="table-scroll">${renderTable(candidateRows)}</div></div>
      <div><p class="muted">Reference</p><div class="table-scroll">${renderTable(referenceRows)}</div></div>
    </div>
  `;
}

function renderRound2Report(s) {
  // Candidate content is {investigation: [{area}], root_cause} now, not
  // comparable row-for-row against the scenario's still row-shaped
  // reference - show the candidate's investigation/conclusion plainly,
  // then the reference debugging steps below for HR to compare by eye.
  const content = s.content || {};
  const investigation = content.investigation || [];
  const referenceRows = s.scenario ? s.scenario.reference_json || [] : [];

  const investigationRows = investigation.map((row, i) => `
    <tr><td>${i + 1}</td><td>${escapeHtml(row.area)}</td></tr>
  `).join("") || '<tr><td colspan="2" class="muted">-</td></tr>';

  const referenceTable = referenceRows.map((r) => `
    <tr><td>${escapeHtml(r.title)}</td><td>${escapeHtml(r.steps)}</td><td>${escapeHtml(r.expected_result)}</td></tr>
  `).join("") || '<tr><td colspan="3" class="muted">-</td></tr>';

  return `
    <h5>Candidate's investigation</h5>
    <div class="table-scroll">
      <table>
        <thead><tr><th>SI.No</th><th>Investigation area</th></tr></thead>
        <tbody>${investigationRows}</tbody>
      </table>
    </div>
    <h5>Possible root cause (candidate's conclusion)</h5>
    <div class="panel-inset">${escapeHtml(content.root_cause || "")}</div>
    <h5>Reference debugging approach</h5>
    <div class="table-scroll">
      <table>
        <thead><tr><th>Title</th><th>Steps</th><th>Expected</th></tr></thead>
        <tbody>${referenceTable}</tbody>
      </table>
    </div>
  `;
}

async function loadHistory() {
  const box = document.getElementById("history-list");
  let history;
  try {
    history = (await api("/hr/history")).filter((h) => h.round_number === currentHRRound);
  } catch (e) {
    box.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
    return;
  }
  if (history.length === 0) {
    box.innerHTML = `<div class="empty-state">No Round ${currentHRRound} scenario has been attempted by a candidate yet.</div>`;
    return;
  }
  box.innerHTML = history.map(renderHistoryCard).join("");
}

function renderHistoryCard(h) {
  const clearedPct = h.cleared_pct == null ? "no scored submissions yet" : `${h.cleared_pct}% cleared`;
  const pctCls = h.cleared_pct == null ? "muted" : h.cleared_pct >= 50 ? "score-good" : "score-bad";
  const dateRange = h.first_used_at === h.last_used_at
    ? formatDate(h.first_used_at)
    : `${formatDate(h.first_used_at)} – ${formatDate(h.last_used_at)}`;

  const missesList = h.common_misses.length === 0
    ? `<p class="muted">No scored submissions yet - nothing to pattern-match on.</p>`
    : `<ul>${h.common_misses.map((m) => `<li>${escapeHtml(m.text)} <span class="muted">- ${m.count} candidate${m.count === 1 ? "" : "s"}</span></li>`).join("")}</ul>`;

  // Round 1 only - empty for round 2/3 scenarios (see
  // ConceptCoverageAverage). Shows where candidates against THIS
  // scenario, in aggregate, are weakest/strongest by category - distinct
  // from the per-candidate breakdown on renderScoreBlock.
  const coverageAverages = (h.concept_coverage_averages || []).length === 0 ? "" : `
    <h5>Average coverage by type</h5>
    <p>${h.concept_coverage_averages.map((c) => {
      const cls = c.avg_pct >= 75 ? "score-good" : c.avg_pct < 40 ? "score-bad" : "";
      return `<span class="${cls}">${escapeHtml(c.category)} ${c.avg_pct}%</span>`;
    }).join(" · ")}</p>
  `;

  return `
    <div class="panel-inset">
      <h4>
        ${escapeHtml(h.title)}
        ${h.is_live ? '<span class="badge badge-published">LIVE</span>' : '<span class="badge">retired</span>'}
      </h4>
      <p class="muted">Used ${dateRange}</p>
      <p>${h.total_attempted} attempted · ${h.scored_count} scored${h.pending_count ? ` · ${h.pending_count} pending` : ""}</p>
      <p>
        <strong class="score-good">${h.cleared_count} cleared</strong> ·
        <strong class="score-bad">${h.not_cleared_count} not cleared</strong> ·
        <strong class="${pctCls}">${clearedPct}</strong>
        <span class="muted">(passing score: ${h.passing_score})</span>
      </p>
      <h5>Most commonly missed</h5>
      ${missesList}
      ${coverageAverages}
    </div>
  `;
}

function formatDate(iso) {
  if (!iso) return "-";
  return new Date(iso + "Z").toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

// Date AND time - for real event timestamps (a round actually starting/
// finishing) as opposed to formatDate's date-only use (HR's uploaded
// exam_date, which is genuinely date-only data - see
// candidate_upload_service._parse_exam_date's "%Y-%m-%d" format, no time
// component exists there to show).
function formatDateTime(iso) {
  if (!iso) return "-";
  return new Date(iso + "Z").toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
