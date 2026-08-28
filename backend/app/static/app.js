// Minimal vanilla-JS frontend. No build step, no framework - talks to
// the FastAPI JSON API with plain fetch() calls. The session token lives
// in an httpOnly cookie set by POST /auth/login (see routers/auth.py) -
// this JS file never sees the raw token at all, so an XSS injection
// can't read it out of sessionStorage/localStorage the way it could
// before. Every fetch() below is same-origin, so the browser attaches
// that cookie automatically; nothing here needs to build an
// Authorization header. "Am I logged in" is resolved by asking the
// server (GET /auth/me) on page load, not by checking local state - see
// the bootstrap call at the bottom of this file.

// ---- Shared constants ----
//
// Round display names - one source, used by both HR's nav (renderHRRoundNav)
// and the candidate's nav (renderCandidateRoundNav). Used to be two
// identical maps (HR_ROUND_LABELS and ROUND_TITLES) defined separately.
const ROUND_LABELS = { 1: "Manual test cases", 2: "Debugging", 3: "AI-prompted coding", 4: "Conversational" };

// Per-round description-field guidance for the shared "Create a scenario"
// form (see resetCreateScenarioForm) - each round hands the candidate a
// different kind of prompt (a feature to test, a bug report to debug, a
// problem statement to solve), so one static placeholder can't describe
// all of them. Round 4 isn't here - it has its own dedicated authoring
// panel (see selectHRRound/loadRound4Settings), never this shared form.
const ROUND_DESC_PLACEHOLDERS = {
  1: "Describe the feature/system the candidate should write test cases for...",
  2: "Describe the bug/production issue the candidate should debug - what's broken, how it was reported...",
  3: "Write the coding problem statement the candidate should solve by prompting the AI assistant - what the program should read from stdin and print to stdout...",
};

// Runtime-editable per-round/final passing scores (see HR's Settings
// page, GET/PUT /hr/settings) - fetched once on HR login into
// appSettings below and used by every score-good/score-bad styling
// decision in this file via passingScoreForRound(), rather than one
// hardcoded global number (that used to be the case; per-round
// thresholds need this to be dynamic, HR-editable data, not a constant).
// ---- Theme (light/dark) ----
//
// The actual attribute is set synchronously in index.html's <head>,
// before first paint, so there's no flash of the wrong theme - this
// just keeps the toggle button's label/icon in sync and persists a
// manual choice. Presentation only; nothing here affects app state.
function currentTheme() {
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}
function syncThemeToggleLabel() {
  const label = document.getElementById("theme-toggle-label");
  if (label) label.textContent = currentTheme() === "light" ? "Dark mode" : "Light mode";
}
function toggleTheme() {
  const next = currentTheme() === "light" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", next);
  try {
    localStorage.setItem("qa_eval_theme", next);
  } catch (e) {
    // Private browsing / storage disabled - the toggle still works for
    // this page view, it just won't be remembered next visit.
  }
  syncThemeToggleLabel();
}
syncThemeToggleLabel();

// Small inline spinner + label, used instead of bare "Loading..." text
// for the handful of fetches below that take long enough to notice.
function loadingHtml(label) {
  return `<div class="loading-inline"><span class="spinner"></span>${escapeHtml(label || "Loading...")}</div>`;
}

let appSettings = null;

function passingScoreForRound(roundNumber) {
  // 70 is a defensive fallback only (e.g. a call site rendering before
  // appSettings has loaded) - the real value always comes from the server.
  return (appSettings && appSettings[`round${roundNumber}_passing_score`]) ?? 70;
}

let role = null;       // resolved from GET /auth/me on every session load - see loadWhoAmI() / the bootstrap call at the bottom of this file
let userEmail = null; // resolved fresh from GET /auth/me every session load - see loadWhoAmI()
let currentRound = 1;       // which round's content is showing right now (candidate view)
let candidateUnlockedRound = 1; // the one round a candidate is allowed into - see refreshCandidateNav()
let candidateCompletedRounds = [];
// currentHRRound/hrPage's initial values here are only the very-first-
// ever-visit default - restoreHRNavState() (called from onLoggedIn)
// overwrites them from localStorage before anything renders, so a
// browser refresh lands back on whichever HR page/round was open
// instead of always resetting to Round 1 - see saveHRNavState, called
// from selectHRPage/selectHRRound, for the other half of this.
let currentHRRound = 1;     // which round's scenarios/history HR is authoring/reviewing right now
let hrPage = "rounds";      // "rounds" (author/review), "candidates" (results dashboard), or "settings"
const HR_NAV_STORAGE_KEY = "qa_eval_hr_nav";

function saveHRNavState() {
  try {
    localStorage.setItem(HR_NAV_STORAGE_KEY, JSON.stringify({ page: hrPage, round: currentHRRound }));
  } catch (e) {
    // Private browsing / storage disabled - losing your place on refresh
    // is the worst case, not a functional break, so fail silently.
  }
}

function restoreHRNavState() {
  try {
    const saved = JSON.parse(localStorage.getItem(HR_NAV_STORAGE_KEY) || "null");
    if (!saved) return;
    if (["rounds", "candidates", "settings"].includes(saved.page)) hrPage = saved.page;
    if ([1, 2, 3, 4].includes(saved.round)) currentHRRound = saved.round;
  } catch (e) {
    // Corrupt/unreadable value - just keep the defaults above.
  }
}
let candidateSummaryData = null;        // last-generated { round_comments, final_summary } (see generateCandidateSummary) - reused by the PDF download so it doesn't cost a second LLM call
let candidateDetailSubmissions = [];    // the currently-open candidate's submissions (see openCandidateDetail) - lets generateCandidateSummary label each round comment with its real title/score
let currentCandidateDetailId = null;    // which candidate's detail panel is open - lets retryScoring/saveScoreOverride re-render the panel they're inside after a successful action
let timerHandle = null;
let rowCount = 0;
let round4State = null;           // last-fetched Round4StateOut, refreshed after every turn/test-case creation
let round4ViewedTestCaseId = null; // which test case tab is showing
let round4DraftBuffer = {};        // { testCaseId: latestTypedText } - instant, in-memory, survives tab switches with zero latency
let round4DraftTimers = {};        // { testCaseId: setTimeout handle } - debounced PATCH to the server

function jsonHeaders() {
  return { "Content-Type": "application/json" };
}

// FastAPI/Pydantic's 422 response shape is {detail: [{loc, msg, type}, ...]}
// - a LIST of error objects, not a string. `new Error(thatList)` used to
// get passed straight through, and stringifying an array of objects in
// a template/textContent assignment silently produces "[object Object],
// [object Object]" instead of anything a candidate can act on. This
// turns that same list into "Row 2 - expected result: field required"
// style text; a plain string detail (every other error in this app)
// passes through unchanged.
function apiErrorMessage(data, fallback) {
  const detail = data && data.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    return detail.map((d) => {
      if (typeof d === "string") return d;
      const loc = Array.isArray(d.loc) ? d.loc : [];
      const rowIndex = loc.find((seg) => typeof seg === "number");
      const field = [...loc].reverse().find((seg) => typeof seg === "string" && seg !== "body");
      const label = field ? (rowIndex !== undefined ? `Row ${rowIndex + 1} - ${field}` : field) : null;
      return label ? `${label}: ${d.msg}` : (d.msg || "Invalid input");
    }).join("; ");
  }
  return fallback;
}

// HR-authored scenario descriptions are free text, and routinely include
// a table pasted straight from Excel/Word - which arrives here as plain
// tab-separated lines (e.g. "Feature\tWhat to verify\nLogin\tValid/invalid
// login, mandatory fields\n..."). Rendered as plain pre-line text those
// tabs collapse to a single space, so the "table" reads as one unreadable
// run-on line per row. Any line containing a tab is treated as a table
// row instead (first such line in a run becomes the header row); every
// other line keeps the old pre-line paragraph treatment, so a scenario
// that's just prose renders exactly as it did before.
function formatScenarioDescription(description) {
  const lines = (description || "").split("\n");
  let html = "";
  let textBuf = [];
  let tableBuf = [];

  function flushText() {
    if (textBuf.length === 0) return;
    html += `<p class="scenario-description">${escapeHtml(textBuf.join("\n"))}</p>`;
    textBuf = [];
  }
  function flushTable() {
    if (tableBuf.length === 0) return;
    const rows = tableBuf.map((line) => line.split("\t").map((cell) => cell.trim()));
    const [headerRow, ...bodyRows] = rows;
    const thead = `<thead><tr>${headerRow.map((c) => `<th>${escapeHtml(c)}</th>`).join("")}</tr></thead>`;
    const tbody = `<tbody>${bodyRows.map((cells) => `<tr>${cells.map((c) => `<td>${escapeHtml(c)}</td>`).join("")}</tr>`).join("")}</tbody>`;
    html += `<div class="table-scroll"><table>${thead}${tbody}</table></div>`;
    tableBuf = [];
  }

  for (const line of lines) {
    if (line.includes("\t")) {
      flushText();
      tableBuf.push(line);
    } else {
      flushTable();
      textBuf.push(line);
    }
  }
  flushText();
  flushTable();
  return html;
}

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: jsonHeaders(), ...opts });
  if (res.status === 401) {
    // The session cookie is missing/expired (sessions last 12h) - the
    // server no longer recognizes it, so there's nothing useful left to
    // do but send the user back to login rather than fail silently.
    logout();
    throw new Error("Your session expired - please log in again.");
  }
  const data = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, `Request failed (${res.status})`));
  }
  return data;
}

// ---- Auth ----

async function login() {
  // Field id is still "email" (the input just accepts either an email
  // or a bulk-uploaded candidate's plain username now - see
  // credential_service.py) - only the wire key sent to the server changed.
  const identifier = document.getElementById("email").value;
  const password = document.getElementById("password").value;
  document.getElementById("auth-error").textContent = "";

  try {
    // fetch() itself can reject (server unreachable, connection reset,
    // DNS failure) before there's even a response to check .ok on - this
    // is a user's very first interaction with the app, so unlike most
    // other call sites here, there's no api() helper wrapping this one;
    // the try/catch has to live here directly rather than being able to
    // rely on a shared "some caller up the chain will catch it" pattern.
    const res = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ identifier, password }),
    });
    // .catch(() => null): a non-JSON body (e.g. an HTML error page from
    // a proxy in front of a down server) would otherwise throw here too,
    // same reasoning as api()'s identical fallback.
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      document.getElementById("auth-error").textContent = apiErrorMessage(data, "Something went wrong");
      return;
    }
    // The actual session token isn't in this response body at all - it
    // arrived as an httpOnly Set-Cookie header on the response above,
    // which this fetch() already accepted (same-origin). role is all the
    // body carries now, just enough to route to the right view immediately.
    role = data.role;
    onLoggedIn();
  } catch (e) {
    document.getElementById("auth-error").textContent = "Couldn't reach the server - check your connection and try again.";
  }
}

async function logout() {
  // A nudge, not a block - matches how the tab-switch guard already
  // treats this class of problem (log/discourage, never trap the
  // candidate with no way out). Logging out doesn't stop the round's
  // clock or score whatever's there yet either way (see
  // scoring_service.close_expired_submissions) - it just ends the
  // session, so this is purely about avoiding an accidental click, not
  // enforcing anything.
  if (timerHandle && !confirm(
    "You have an active timed round in progress. Logging out won't stop your timer or let you resume it - " +
    "your time keeps running either way. Log out anyway?"
  )) {
    return;
  }
  stopTimer();
  resetTopbarTimer();
  disarmTabGuard();
  // JS can't clear an httpOnly cookie itself - a real request is the
  // only way. Not api() here: if the cookie's already expired this would
  // 401 and api() would call logout() again on that 401, recursing. Best-
  // effort either way - local UI state below gets cleared regardless of
  // whether this call actually succeeds.
  try {
    await fetch("/auth/logout", { method: "POST" });
  } catch (e) {
    // ignored - see comment above
  }
  role = null;
  userEmail = null;
  document.getElementById("hr-panel").classList.add("hidden");
  document.getElementById("candidate-panel").classList.add("hidden");
  document.getElementById("who").innerHTML = "";
  document.getElementById("email").value = "";
  document.getElementById("password").value = "";
  document.getElementById("app-shell").classList.add("hidden");
  document.getElementById("auth-screen").classList.remove("hidden");
}

function renderWho() {
  const initial = (userEmail || role || "?").trim().charAt(0).toUpperCase();
  document.getElementById("who").innerHTML = `
    <div class="rail-user-avatar">${escapeHtml(initial)}</div>
    <div class="rail-user-info">
      <span class="rail-user-email" title="${escapeHtml(userEmail || "")}">${escapeHtml(userEmail || "Loading...")}</span>
      <span class="rail-user-role">${escapeHtml(role || "")}</span>
    </div>
  `;
}

// Resolved from the server on every session load (fresh login AND a page
// reload that restores an already-signed-in session via the httpOnly
// cookie) rather than trusted from whatever was last typed into the
// login form - a tab that was already signed in before this existed
// would otherwise show a blank identity forever, since it never went
// through the login form again to capture it.
async function loadWhoAmI() {
  try {
    const me = await api("/auth/me");
    userEmail = me.email;
    renderWho();
  } catch (e) {
    // api() already redirects to logout on a 401; nothing else to do here.
  }
}

function onLoggedIn() {
  document.getElementById("auth-screen").classList.add("hidden");
  document.getElementById("app-shell").classList.remove("hidden");
  renderWho();
  loadWhoAmI();
  // Only one of these two navs gets rendered below, based on role - but
  // logging out doesn't clear either one, so without this, logging into
  // a different role on the same page (no full reload) left the previous
  // role's nav sitting in the DOM alongside the new one.
  document.getElementById("hr-round-nav").innerHTML = "";
  document.getElementById("candidate-round-nav").innerHTML = "";
  if (role === "hr") {
    document.getElementById("hr-panel").classList.remove("hidden");
    restoreHRNavState();
    renderHRRoundNav();
    loadLiveScenarioWidget();
    loadScenarios();
    loadCandidates();
    loadHistory();
    loadAppSettings();
  } else {
    document.getElementById("candidate-panel").classList.remove("hidden");
    refreshCandidateNav();
  }
}

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

function renderHRRoundNav() {
  const nav = document.getElementById("hr-round-nav");
  // HR authors rounds in any order - no done/locked state here, just
  // which one's open right now (see the candidate nav below for the
  // sequence-with-real-state version of this same rail language).
  nav.innerHTML = `
    <div class="rail-section-label">Author scenarios</div>
    <nav class="tick-rail">
      ${[1, 2, 3, 4].map((n) => `
        <button class="tick ${hrPage === "rounds" && n === currentHRRound ? "active" : ""}" onclick="selectHRRound(${n})">
          <span class="tick-num">${n}</span>
          <span class="tick-label">${ROUND_LABELS[n]}</span>
        </button>
      `).join("")}
    </nav>
    <div class="rail-divider">
      <div class="rail-section-label">Reporting</div>
      <button class="index-item ${hrPage === "candidates" ? "active" : ""}" onclick="selectHRPage('candidates')"><span>Candidates</span></button>
      <button class="index-item ${hrPage === "settings" ? "active" : ""}" onclick="selectHRPage('settings')"><span>Settings</span></button>
    </div>
  `;

  document.getElementById("hr-page-rounds").classList.toggle("hidden", hrPage !== "rounds");
  document.getElementById("hr-page-candidates").classList.toggle("hidden", hrPage !== "candidates");
  document.getElementById("hr-page-settings").classList.toggle("hidden", hrPage !== "settings");

  if (hrPage === "rounds") {
    setPageHeader("HR Console", `Round ${currentHRRound} · ${ROUND_LABELS[currentHRRound]}`, "Author, review, and publish scenarios for this round.");
    document.getElementById("hr-round-context").textContent = `Now creating for Round ${currentHRRound} (${ROUND_LABELS[currentHRRound]}).`;
  } else if (hrPage === "candidates") {
    setPageHeader("HR Console", "Candidates", "Every candidate's progress and results, across all rounds.");
  } else {
    setPageHeader("HR Console", "Settings", "Pass criteria and re-application handling - HR-editable, applies immediately.");
  }
  // Every hrPage/currentHRRound change routes through here (selectHRRound,
  // selectHRPage, and the restore call in onLoggedIn) - persisting once
  // here instead of at each call site means a refresh always lands back
  // on whichever page/round was actually last showing.
  saveHRNavState();
}

function selectHRRound(n) {
  currentHRRound = n;
  hrPage = "rounds";
  renderHRRoundNav();
  // Defensive reset, same panels closeScenarioDetail restores - if a
  // scenario's detail was left open when HR switched rounds, its
  // hidden/full-width state shouldn't follow them to a round they
  // haven't opened anything in yet.
  closeScenarioDetail();

  const isRound4 = n === 4;
  // Round 4 gets its own single settings view instead of the round1/2
  // author/review/publish flow - see loadRound4Settings for why none of
  // that maps onto round 4's actual shape (no fixed reference, no
  // meaningfully different "versions" to browse or compare).
  document.getElementById("live-scenario-panel").classList.toggle("hidden", isRound4);
  document.getElementById("create-scenario-row").classList.toggle("hidden", isRound4);
  document.getElementById("screening-history-panel").classList.toggle("hidden", isRound4);
  document.getElementById("round4-settings-panel").classList.toggle("hidden", !isRound4);

  if (isRound4) {
    loadRound4Settings();
  } else {
    resetCreateScenarioForm();
    loadLiveScenarioWidget();
    loadScenarios();
    loadHistory();
  }
}

// Standalone, always-visible time-limit editor for whichever scenario(s)
// are actually live for the current round - deliberately independent of
// openScenarioDetail's much larger render (title/description/reference/
// environment/mockups, all wrapped in the draft/published logic there).
// Exists because "find the live scenario in the list, click Review,
// scroll to the time field" turned out to not be a path HR reliably
// found - this needs zero clicks beyond typing a number and hitting Save.
async function loadLiveScenarioWidget() {
  const box = document.getElementById("live-scenario-list");
  let scenarios;
  try {
    scenarios = (await api("/hr/scenarios")).filter((s) => s.round_number === currentHRRound && s.is_live);
  } catch (e) {
    box.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
    return;
  }
  if (scenarios.length === 0) {
    box.innerHTML = `<p class="muted">No live scenario yet for Round ${currentHRRound} - publish one below and mark it live for screening.</p>`;
    return;
  }
  box.innerHTML = scenarios.map((s) => `
    <div class="panel-inset">
      <p><strong>${escapeHtml(s.title)}</strong> <span class="badge badge-published">LIVE</span> · ${s.experience_band}</p>
      <div class="row" style="align-items:center">
        <div class="field-inline">
          <span class="muted">min limit</span>
          <input id="live-time-limit-${s.id}" type="number" min="1" value="${s.time_limit_minutes}" />
        </div>
        <button onclick="saveLiveTimeLimit(${s.id})">Save</button>
      </div>
      <p id="live-time-status-${s.id}" class="muted"></p>
    </div>
  `).join("");
}

async function saveLiveTimeLimit(id) {
  const statusEl = document.getElementById(`live-time-status-${id}`);
  const inputEl = document.getElementById(`live-time-limit-${id}`);
  const value = Number(inputEl.value);
  statusEl.className = "muted";
  if (!Number.isInteger(value) || value < 1) {
    statusEl.className = "error-text";
    statusEl.textContent = "Must be a whole number of minutes, at least 1.";
    return;
  }
  try {
    await api(`/hr/scenarios/${id}/time-limit`, { method: "PATCH", body: JSON.stringify({ time_limit_minutes: value }) });
    statusEl.textContent = "Saved.";
  } catch (e) {
    // The save was rejected - the input still shows the value the HR
    // typed, which would look like it took effect even though nothing
    // was persisted. Snap it back to what's actually live so a blocked
    // change can't be mistaken for a successful one.
    inputEl.value = inputEl.defaultValue;
    statusEl.className = "error-text";
    statusEl.textContent = e.message;
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
  document.getElementById("s-band").value = "0-7";
  document.getElementById("s-time-limit").value = "30";
  document.getElementById("s-title").value = "";
  document.getElementById("s-desc").value = "";
  document.getElementById("s-desc").placeholder = ROUND_DESC_PLACEHOLDERS[currentHRRound] || "";
  updateCreateBtnState();
}

function selectHRPage(page) {
  hrPage = page;
  renderHRRoundNav();
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
  };
  try {
    appSettings = await api("/hr/settings", { method: "PUT", body: JSON.stringify(payload) });
    statusEl.textContent = "Saved.";
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
function updateCreateBtnState() {
  const title = document.getElementById("s-title").value.trim();
  const description = document.getElementById("s-desc").value.trim();
  document.getElementById("s-create-btn").disabled = !(title && description);
}

async function createScenario() {
  const round_number = currentHRRound;
  const experience_band = document.getElementById("s-band").value;
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
  statusEl.textContent = "Generating reference answer (a few seconds)...";
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

async function loadScenarios() {
  const scenarios = (await api("/hr/scenarios")).filter((s) => s.round_number === currentHRRound);
  lastLoadedScenarios = scenarios;
  const list = document.getElementById("scenario-list");
  if (scenarios.length === 0) {
    list.innerHTML = `<div class="empty-state">No Round ${currentHRRound} scenarios yet - create one to get started.</div>`;
    return;
  }
  const published = scenarios.filter((s) => s.status === "published");
  const drafts = scenarios.filter((s) => s.status === "draft");
  list.innerHTML = `
    <h4>Published</h4>
    ${renderPublishedTable(published)}
    <h4>Drafts</h4>
    ${renderDraftTable(drafts)}
    <p id="scenario-list-status" class="muted"></p>
  `;
}

function renderPublishedTable(published) {
  if (published.length === 0) return `<div class="empty-state">No published scenarios yet.</div>`;
  return `
    <div class="table-scroll">
      <table>
        <thead><tr><th>Publish for screening</th><th>Band</th><th>Title</th><th></th><th></th></tr></thead>
        <tbody>
          ${published.map((s) => `
            <tr>
              <td>
                <input type="radio" name="live-r${s.round_number}-${s.experience_band}"
                  ${s.is_live ? "checked" : ""} onchange="moveToScreening(${s.id})"
                  title="Publish for screening" />
              </td>
              <td>${s.experience_band}</td>
              <td>${escapeHtml(s.title)} ${s.is_live ? '<span class="badge badge-published">LIVE</span>' : ""}</td>
              <td><button onclick="openScenarioDetail(${s.id})">Review</button></td>
              <td>${s.is_live
                ? `<button class="btn-ghost" disabled title="Can't delete the live scenario - make a different one live first.">Delete</button>`
                : `<button class="btn-ghost" onclick="deleteScenarioFromList(${s.id})">Delete</button>`
              }</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderDraftTable(drafts) {
  if (drafts.length === 0) return `<div class="empty-state">No drafts.</div>`;
  return `
    <div class="table-scroll">
      <table>
        <thead><tr><th>Band</th><th>Title</th><th></th><th></th></tr></thead>
        <tbody>
          ${drafts.map((s) => `
            <tr>
              <td>${s.experience_band}</td>
              <td>${escapeHtml(s.title)}</td>
              <td><button onclick="openScenarioDetail(${s.id})">Review</button></td>
              <td><button class="btn-ghost" onclick="deleteScenarioFromList(${s.id})">Delete</button></td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

async function moveToScreening(id) {
  const scenario = await api(`/hr/scenarios/${id}`);
  const confirmed = confirm(
    `Publish "${scenario.title}" (Round ${scenario.round_number} / ${scenario.experience_band}) for screening? ` +
    `Candidates in that round+band will see this one immediately, replacing whichever scenario was live before.`
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
  loadLiveScenarioWidget(); // which scenario shows here can change too
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
    : (scenario.reference_json || []).map((r, i) => `
        <tr>
          <td>${i + 1}</td>
          <td>${escapeHtml(r.title)}</td>
          <td>${escapeHtml(r.preconditions || "")}</td>
          <td>${escapeHtml(r.steps)}</td>
          <td>${escapeHtml(r.expected_result)}</td>
          ${showPriorityType ? `<td>${escapeHtml(r.priority)}</td><td>${escapeHtml(r.type)}</td>` : ""}
        </tr>
      `).join("");

  const isDraft = scenario.status === "draft";
  // Round 4 no longer routes through here at all (see loadRound4Settings/
  // renderRound4SettingsCard) - it has no fixed reference to author/
  // review/compare across versions the way round 1/2 do, so it gets its
  // own dedicated settings panel instead of a "Review" flow into this one.
  box.innerHTML = `
    <div class="row" style="align-items:center; justify-content:space-between">
      <h3 style="margin:0">#${scenario.id} - ${escapeHtml(scenario.title)} <span class="badge badge-${scenario.status}">${statusLabel(scenario.status)}</span>${scenario.is_live ? ' <span class="badge badge-published">LIVE</span>' : ""}</h3>
      <button class="btn-ghost" onclick="closeScenarioDetail()">Close</button>
    </div>
    ${scenario.is_live ? `<p class="muted">This is the one scenario Round ${scenario.round_number} / ${scenario.experience_band} candidates currently see.</p>` : ""}
    <p class="muted">Round ${scenario.round_number} · ${scenario.experience_band}</p>
    ${scenario.is_live ? `
      <p class="muted">Time limit is editable from the "Live scenario time limit" panel above - no need to repeat it here.</p>
    ` : `
      <div class="row" style="align-items:center">
        <div class="field-inline">
          <span class="muted">min limit</span>
          <input id="time-limit-edit" type="number" min="1" value="${scenario.time_limit_minutes}" />
        </div>
        <button onclick="saveTimeLimitEdit(${scenario.id})">Save</button>
      </div>
    `}
    <h4>Question</h4>
    ${formatScenarioDescription(scenario.description)}
    <h4>Reference answer ${isDraft ? "(review before publishing)" : ""}</h4>
    <div class="table-scroll">
      <table>
        <thead><tr>${isCodingReference
          ? "<th>SI.No</th><th>Input</th><th>Expected output</th><th>Description</th>"
          : `<th>SI.No</th><th>Title</th><th>Preconditions</th><th>Steps</th><th>Expected result</th>${showPriorityType ? "<th>Priority</th><th>Type</th>" : ""}`
        }</tr></thead>
        <tbody>${refRows || `<tr><td colspan="${isCodingReference ? 4 : showPriorityType ? 7 : 5}" class="muted">No reference generated yet.</td></tr>`}</tbody>
      </table>
    </div>
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
    ` : ""}
    <p id="scenario-detail-status" class="muted"></p>
  `;
  } catch (e) {
    box.innerHTML = `<p class="muted">Couldn't render this scenario's detail view: ${escapeHtml(e.message)}. Check the browser console for more, and try a hard refresh (Ctrl+Shift+R) in case this page is running an old cached version.</p>`;
  }
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

// ---- Round 4 settings: one card per experience band, replacing the
// round1/2-style Create-a-scenario/Scenarios-list/Screening-history flow
// entirely (see selectHRRound). Round 4 has no fixed reference to
// author, review, or compare across versions - each candidate automates
// their own round 1 answer, and the environment/screens are auto-
// generated, not HR-authored - so there's no "library of scenarios" to
// browse the way round 1/2 genuinely have, and never more than one
// meaningful configuration per band worth looking at.

const ROUND4_BANDS = [["0-7", "0-7 years"], ["7+", "7+ years"]];

async function loadRound4Settings() {
  const box = document.getElementById("round4-settings-panel");
  let allScenarios;
  try {
    allScenarios = await api("/hr/scenarios");
  } catch (e) {
    box.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
    return;
  }
  // Only bands with an actual live scenario show anything at all - a
  // band nobody's set up yet is simply not shown, full stop, not a
  // "create one" prompt for a band that isn't even being screened right
  // now (bootstrapping round 4 for a new band, if ever needed, is a
  // direct API action, not a standing part of this page).
  box.innerHTML = ROUND4_BANDS.map(([band, bandLabel]) => {
    const liveScenario = allScenarios.find((s) => s.round_number === 4 && s.experience_band === band && s.is_live);
    if (!liveScenario) return "";
    const liveRound1 = allScenarios.find((s) => s.round_number === 1 && s.experience_band === band && s.is_live);
    return renderRound4SettingsCard(liveScenario, liveRound1 ? liveRound1.title : null, bandLabel);
  }).join("");
}

function renderRound4SettingsCard(scenario, groundedInTitle, bandLabel) {
  // 60 below is a defensive fallback only (e.g. this renders before
  // appSettings has loaded) - the real default always comes from the
  // server (see AppSettingsOut.round4_default_assistance_pct /
  // config.py's round4_default_assistance_pct), same pattern as
  // passingScoreForRound() above.
  const serverDefault = (appSettings && appSettings.round4_default_assistance_pct) ?? 60;
  const assistancePct = (scenario.config_json && scenario.config_json.assistance_pct) || serverDefault;
  return `
    <div class="panel card" style="margin-bottom:1.5rem">
      <h3>Round 4 - ${bandLabel} <span class="badge badge-published">LIVE</span></h3>
      <p class="muted">This is what Round 4 / ${scenario.experience_band} candidates currently see.</p>

      <h4>Instructions</h4>
      <p class="muted">What candidates read when they open this round.</p>
      <input id="r4-title-${scenario.id}" value="${escapeHtml(scenario.title)}" />
      <textarea id="r4-desc-${scenario.id}">${escapeHtml(scenario.description)}</textarea>
      <div class="row">
        <button onclick="saveRound4Instructions(${scenario.id})">Save instructions</button>
      </div>

      <h4>Time limit</h4>
      <div class="row" style="align-items:center">
        <div class="field-inline">
          <span class="muted">min limit</span>
          <input id="r4-time-limit-${scenario.id}" type="number" min="1" value="${scenario.time_limit_minutes}" />
        </div>
        <button onclick="saveRound4TimeLimit(${scenario.id})">Save</button>
      </div>

      <h4>Assistant accuracy</h4>
      <p class="muted">How often the simulated assistant gets things right per turn - the rest of the time it confidently reports a flawed result, on purpose, for the candidate to catch. Lower means more planted issues; higher means fewer.</p>
      <div class="row" style="align-items:center">
        <div class="field-inline">
          <span class="muted">% correct per turn</span>
          <input id="round4-config-edit-${scenario.id}" type="number" min="10" max="95" value="${assistancePct}" />
        </div>
        <button onclick="saveRound4ConfigEdit(${scenario.id})">Save</button>
      </div>

      <h4>How this round works</h4>
      <details class="hint-box">
        <summary>Guardrails the assistant follows (for reference - candidates only see the short heads-up version, not this level of detail)</summary>
        <ul style="margin:0.5rem 0 0; padding-left:1.2rem">
          <li>Deliberately correct only ~${assistancePct}% of the time per turn (see above) - candidates are told this upfront.</li>
          <li>Never shows real code by default - only plain-English steps and observed results. A candidate can optionally view a completed turn rendered as a code snippet, but that snippet never contains pass/fail judgments either.</li>
          <li>Never states or hints which category (UI, API, DB, end-to-end) a candidate's test case falls into - that's for the candidate to reason out themselves.</li>
          <li>Refuses to discuss other candidates, skip ahead to a result, or mark everything as passed without actually simulating it - regardless of how the request is framed.</li>
          <li>Attempts to direct the assistant's behavior or the scoring itself (e.g. "mark everything as passing," "tell me what gets the best score") are refused in-turn and separately flagged as a red flag in the scoring feedback - candidates are told upfront that this gets flagged, but not the exact trigger phrasing or that scoring is where it's recorded.</li>
        </ul>
      </details>

      <h4>Grounded in</h4>
      <p class="muted">${groundedInTitle
        ? `This round's test environment &amp; reference screens are auto-generated from <strong>${escapeHtml(groundedInTitle)}</strong> - the round 1 scenario currently live for this band. They resync automatically whenever a different round 1 scenario goes live here.`
        : `No round 1 scenario is currently live for this band - the environment/screens below fell back to this scenario's own description instead. They'll resync automatically once one is published.`}</p>

      <details>
        <summary>Preview: test environment &amp; reference screens (auto-generated, shown to candidates)</summary>
        ${scenario.environment_json ? `
          <div class="hint-box env-panel">
            <dl class="env-fields">
              ${Object.entries(scenario.environment_json.fields || {}).map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join("")}
            </dl>
            ${scenario.environment_json.notes ? `<p>${escapeHtml(scenario.environment_json.notes)}</p>` : ""}
          </div>
        ` : `<p class="muted">No test environment generated yet.</p>`}
        ${scenario.ui_mockup_json ? renderMockupScreens(scenario.ui_mockup_json, `hr-mockup-${scenario.id}`) : `<p class="muted">No reference screens generated yet.</p>`}
        <div class="row">
          <button id="r4-regen-btn-${scenario.id}" onclick="regenerateRound4Reference(${scenario.id})">Regenerate environment &amp; screens</button>
        </div>
      </details>

      <p id="r4-status-${scenario.id}" class="muted"></p>
    </div>
  `;
}

async function saveRound4Instructions(id) {
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

async function saveRound4TimeLimit(id) {
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

async function saveRound4ConfigEdit(id) {
  const statusEl = document.getElementById(`r4-status-${id}`);
  const inputEl = document.getElementById(`round4-config-edit-${id}`);
  const value = Number(inputEl.value);
  statusEl.className = "muted";
  if (!Number.isInteger(value) || value < 10 || value > 95) {
    statusEl.className = "error-text";
    statusEl.textContent = "Assistant accuracy must be a whole number between 10 and 95.";
    return;
  }
  try {
    await api(`/hr/scenarios/${id}/round4-config`, { method: "PATCH", body: JSON.stringify({ assistance_pct: value }) });
    statusEl.textContent = "Saved.";
  } catch (e) {
    inputEl.value = inputEl.defaultValue;
    statusEl.className = "error-text";
    statusEl.textContent = e.message;
  }
}

async function regenerateRound4Reference(id) {
  const statusEl = document.getElementById(`r4-status-${id}`);
  const btn = document.getElementById(`r4-regen-btn-${id}`);
  btn.disabled = true;
  btn.textContent = "Regenerating...";
  statusEl.className = "muted";
  statusEl.textContent = "Regenerating (a few seconds)...";
  try {
    await api(`/hr/scenarios/${id}/regenerate-reference`, { method: "POST" });
    loadRound4Settings();
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
  // Its own endpoint, not the draft-only PATCH /hr/scenarios/{id} used
  // for title/description/reference edits - the time limit is allowed to
  // change on a published/live scenario too (see hr.py's
  // update_scenario_time_limit), which is exactly the case that matters
  // in practice: adjusting the duration of the scenario candidates are
  // actually taking right now, not just an unpublished draft.
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

async function publishScenario(id) {
  const statusEl = document.getElementById("scenario-detail-status");
  const scenario = await api(`/hr/scenarios/${id}`);
  const scenarios = await api("/hr/scenarios");
  const currentlyLive = scenarios.find((s) =>
    s.is_live && s.round_number === scenario.round_number && s.experience_band === scenario.experience_band
  );
  const warning = currentlyLive
    ? `Publish this into the Round ${scenario.round_number} / ${scenario.experience_band} library alongside "${currentlyLive.title}", which stays live for candidates until you explicitly publish it for screening. Continue?`
    : `Publish this scenario? Since nothing is currently live for Round ${scenario.round_number} / ${scenario.experience_band}, it will also become the one candidates see immediately.`;
  if (!confirm(warning)) return;

  try {
    await api(`/hr/scenarios/${id}/publish`, { method: "POST" });
    loadScenarios();
    loadLiveScenarioWidget(); // this publish may have just made a scenario live
    openScenarioDetail(id);
  } catch (e) {
    statusEl.textContent = e.message;
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

// Delete straight from the Published/Drafts list rows (see
// renderPublishedTable/renderDraftTable) - unlike deleteScenario above
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

async function loadCandidates() {
  const box = document.getElementById("candidates-table");
  let candidates;
  try {
    candidates = await api("/hr/candidates");
  } catch (e) {
    box.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
    return;
  }
  box.innerHTML = `
    <div class="table-scroll">
      <table>
        <thead><tr><th>Candidate</th><th>Band</th><th>Exam date</th><th>Round 1</th><th>Round 2</th><th>Round 3</th><th>Round 4</th><th>Aggregate</th><th>Result</th><th></th></tr></thead>
        <tbody>
          ${candidates.map((c) => `
            <tr>
              <td>${escapeHtml(c.email)} ${c.reapplied_within_window ? '<span class="badge badge-draft">Re-applied</span>' : ""}</td>
              <td>
                <select class="band-select" onchange="setCandidateBand(${c.id}, this.value)">
                  <option value="" ${!c.experience_band ? "selected" : ""}>-</option>
                  <option value="0-7" ${c.experience_band === "0-7" ? "selected" : ""}>0-7 years</option>
                  <option value="7+" ${c.experience_band === "7+" ? "selected" : ""}>7+ years</option>
                </select>
              </td>
              <td>${c.exam_date ? formatDate(c.exam_date) : "-"}</td>
              ${c.rounds.map((r) => `<td>${roundStatusCell(r)}</td>`).join("")}
              <td>${c.aggregate_score != null ? `<strong class="${c.aggregate_score >= (appSettings ? appSettings.final_passing_score : 280) ? "score-good" : "score-bad"}">${c.aggregate_score}/400</strong>` : `<span class="muted">-</span>`}</td>
              <td>${resultBadge(c.result)}</td>
              <td><button onclick="openCandidateDetail(${c.id})">View</button></td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

// "selected"/"not_selected"/"in_progress" - see hr.py's
// _build_candidate_summary for the actual determination (aggregate_score
// vs AppSettings.final_passing_score, only once every round is scored).
// Reuses the existing badge-published/badge-fail/badge-draft classes
// (green/red/amber) rather than inventing new ones, matching the same
// visual vocabulary published/failed/draft-status badges already use
// elsewhere in this dashboard.
function resultBadge(result) {
  if (result === "selected") return `<span class="badge badge-published">Selected</span>`;
  if (result === "not_selected") return `<span class="badge badge-fail">Not selected</span>`;
  return `<span class="badge badge-draft">In progress</span>`;
}

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
        <td class="muted">${r.error ? escapeHtml(r.error) : ""}</td>
      </tr>
    `).join("");
    resultEl.innerHTML = `
      <p>${result.created_count} created, ${result.reset_count} reset, ${result.error_count} error${result.error_count === 1 ? "" : "s"}.</p>
      <div class="table-scroll">
        <table>
          <thead><tr><th>Row</th><th>Email</th><th>Status</th><th>Username</th><th>Error</th></tr></thead>
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

function roundStatusCell(r) {
  const flag = r.tab_switch_count > 0
    ? ` <span class="badge badge-fail" title="Left the test ${r.tab_switch_count} time${r.tab_switch_count === 1 ? "" : "s"} during this round">${r.tab_switch_count}x tab switch</span>`
    : "";
  const autoClosedFlag = r.auto_closed_reason
    ? ` <span class="badge badge-draft" title="${escapeHtml(r.auto_closed_reason)}">Auto-closed</span>`
    : "";
  // Real completion timestamp (see models.Submission.submitted_at) on
  // hover - a title attribute rather than its own table column, so the
  // already-wide dashboard doesn't grow a column per round just for
  // this. Only ever set once a round has actually finished, so this
  // naturally never appears on a not_started/in_progress cell.
  const submittedTitle = r.submitted_at ? ` title="Submitted ${formatDateTime(r.submitted_at)}"` : "";
  if (r.final_score != null) {
    const cls = r.final_score >= passingScoreForRound(r.round_number) ? "score-good" : "score-bad";
    return `<span class="${cls}"${submittedTitle}>${r.final_score}/100</span>${flag}${autoClosedFlag}`;
  }
  return `<span class="muted"${submittedTitle}>${r.status.replace("_", " ")}</span>${flag}${autoClosedFlag}`;
}

// Shared by the live candidate-detail view and the "Past appearances"
// drill-down (viewAppearance) - same per-round rendering either way, fed
// either the current cycle's submissions or one specific past cycle's.
function renderSubmissionsPanels(submissions) {
  if (submissions.length === 0) return `<p class="muted">No submissions in this cycle.</p>`;
  return submissions.map((s) => `
    <div class="panel-inset">
      <h4>Round ${s.round_number} - ${s.scenario ? escapeHtml(s.scenario.title) : ""} <span class="badge">${s.status}</span>
        ${s.tab_switch_count > 0 ? `<span class="badge badge-fail" title="Timestamps: ${s.tab_switch_events_json.map(formatDate).join(", ")}">Left the test ${s.tab_switch_count} time${s.tab_switch_count === 1 ? "" : "s"}</span>` : ""}
        ${s.auto_closed_reason ? `<span class="badge badge-draft" title="${escapeHtml(s.auto_closed_reason)}">Auto-closed</span>` : ""}
      </h4>
      <p class="muted">${s.started_at ? `Started ${formatDateTime(s.started_at)}` : ""}${s.started_at && s.submitted_at ? " · " : ""}${s.submitted_at ? `Submitted ${formatDateTime(s.submitted_at)}` : ""}</p>
      ${s.scenario ? `
        <details class="scenario-question" open>
          <summary>Question</summary>
          ${formatScenarioDescription(s.scenario.description)}
        </details>
      ` : ""}
      ${renderScoreBlock(s)}
      ${s.round_number === 4 ? renderRound4Report(s)
        : s.round_number === 3 ? renderRound3Report(s)
        : s.round_number === 2 ? renderRound2Report(s)
        : renderSideBySide(s.content, s.scenario ? s.scenario.reference_json : null)}
    </div>
  `).join("");
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
    return `<span class="${cls}" title="${escapeHtml(c.notes || "")}">${escapeHtml(c.category)} ${c.covered}/${c.total}</span>`;
  });
  return `<p class="muted">Coverage by type: ${parts.join(" · ")}</p>`;
}

function renderScoreBlock(s) {
  if (s.status === "scoring_failed") {
    return `
      <div class="panel-inset" style="border-color: var(--bad)">
        <p><strong class="score-bad">Scoring failed</strong></p>
        <p class="muted">${escapeHtml(s.scoring_error || "Unknown error.")}</p>
        <div class="row">
          <button onclick="retryScoring(${s.id})">Retry scoring</button>
          <button class="btn-ghost" onclick="toggleScoreOverrideForm(${s.id})">Score manually</button>
        </div>
        <p id="score-status-${s.id}" class="muted"></p>
        <div id="override-form-${s.id}"></div>
      </div>
    `;
  }
  if (s.score) {
    const passed = s.score.final_score >= passingScoreForRound(s.round_number);
    return `
      <p>Final score: <strong class="${passed ? "score-good" : "score-bad"}">${s.score.final_score}/100</strong> · Coverage: ${s.score.coverage_score}/100
        ${s.score.overridden_by_hr ? `<span class="badge">Overridden by HR - LLM originally said ${s.score.original_final_score}/100</span>` : ""}
      </p>
      <p>${escapeHtml(s.score.feedback_text || "")}</p>
      <p class="muted">Missed: ${(s.score.misses_json || []).map(escapeHtml).join(", ") || "none noted"}</p>
      ${conceptCoverageLine(s.score.concept_coverage_json)}
      ${s.score.overridden_by_hr ? `<p class="muted">Override note: ${escapeHtml(s.score.override_note || "")}</p>` : ""}
      ${s.score.scoring_model ? `<p class="muted">Scored with ${escapeHtml(s.score.scoring_model)} · prompt ${escapeHtml(s.score.scoring_prompt_hash || "")}</p>` : ""}
      <div class="row">
        <button class="btn-ghost" onclick="toggleScoreOverrideForm(${s.id})">Override score</button>
      </div>
      <p id="score-status-${s.id}" class="muted"></p>
      <div id="override-form-${s.id}"></div>
    `;
  }
  return `<p class="muted">Not scored yet.</p>`;
}

function toggleScoreOverrideForm(submissionId) {
  const el = document.getElementById(`override-form-${submissionId}`);
  if (el.innerHTML) {
    el.innerHTML = "";
    return;
  }
  el.innerHTML = `
    <div class="panel-inset">
      <label class="muted">Final score (0-100)</label>
      <input id="override-score-${submissionId}" type="number" min="0" max="100" />
      <label class="muted">Feedback (optional - leave blank to keep as-is)</label>
      <textarea id="override-feedback-${submissionId}"></textarea>
      <label class="muted">Why is this being overridden? (required)</label>
      <textarea id="override-note-${submissionId}"></textarea>
      <button onclick="saveScoreOverride(${submissionId})">Save override</button>
    </div>
  `;
}

async function retryScoring(submissionId) {
  const statusEl = document.getElementById(`score-status-${submissionId}`);
  statusEl.textContent = "Retrying...";
  try {
    await api(`/hr/submissions/${submissionId}/retry-scoring`, { method: "POST" });
    openCandidateDetail(currentCandidateDetailId);
  } catch (e) {
    statusEl.textContent = e.message;
  }
}

async function saveScoreOverride(submissionId) {
  const statusEl = document.getElementById(`score-status-${submissionId}`);
  const final_score = Number(document.getElementById(`override-score-${submissionId}`).value);
  const feedback_text = document.getElementById(`override-feedback-${submissionId}`).value.trim() || null;
  const override_note = document.getElementById(`override-note-${submissionId}`).value.trim();
  if (!override_note) {
    statusEl.textContent = "Explain why this is being overridden before saving.";
    return;
  }
  try {
    await api(`/hr/submissions/${submissionId}/score`, {
      method: "PATCH",
      body: JSON.stringify({ final_score, feedback_text, override_note }),
    });
    openCandidateDetail(currentCandidateDetailId);
  } catch (e) {
    statusEl.textContent = e.message;
  }
}

async function openCandidateDetail(id) {
  const submissions = await api(`/hr/candidates/${id}/report`);
  const box = document.getElementById("candidate-detail");
  box.classList.remove("hidden");
  candidateSummaryData = null;       // stale from whatever candidate was open before
  candidateDetailSubmissions = submissions; // so generateCandidateSummary can label each round's comment with its real title/score
  currentCandidateDetailId = id;

  box.innerHTML = `
    ${renderSubmissionsPanels(submissions)}
    <div class="panel-inset">
      <h4>Summary</h4>
      <p class="muted">A crisp, cross-round synthesis for feedback to the candidate or a briefing for the next round's interviewers.</p>
      <div class="row">
        <button onclick="generateCandidateSummary(${id})">Generate Summary</button>
      </div>
      <div id="candidate-summary-body"></div>
    </div>
    <div class="panel-inset">
      <h4>Past appearances</h4>
      <p class="muted">Every previous upload cycle for this candidate - re-applying resets their current attempt but keeps the old one here.</p>
      <div id="appearances-list"></div>
      <div id="appearance-detail"></div>
    </div>
  `;
  loadAppearances(id);
  // The candidates table above can easily be long enough that this panel
  // renders off-screen - clicking "View" filled it in, but nothing
  // visibly happened until the HR user thought to scroll down and find
  // it. Bring it into view instead of leaving that to chance.
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadAppearances(candidateId) {
  const listEl = document.getElementById("appearances-list");
  let appearances;
  try {
    appearances = await api(`/hr/candidates/${candidateId}/appearances`);
  } catch (e) {
    listEl.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
    return;
  }
  if (appearances.length === 0) {
    listEl.innerHTML = `<p class="muted">No upload history - this candidate wasn't created via bulk upload.</p>`;
    return;
  }
  listEl.innerHTML = `
    <div class="table-scroll">
      <table>
        <thead><tr><th>Exam date</th><th>Status</th><th>Aggregate</th><th></th></tr></thead>
        <tbody>
          ${appearances.map((a) => `
            <tr>
              <td>${formatDate(a.exam_date)}</td>
              <td>
                ${a.is_current ? '<span class="badge badge-published">Current</span>' : '<span class="badge">Archived</span>'}
                ${a.reapplied_within_window ? '<span class="badge badge-draft">Re-applied</span>' : ""}
              </td>
              <td>${a.aggregate_score != null ? `${a.aggregate_score}/400` : `<span class="muted">-</span>`}</td>
              <td><button onclick="viewAppearance(${candidateId}, ${a.id})">View</button></td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

async function viewAppearance(candidateId, appearanceId) {
  const detailEl = document.getElementById("appearance-detail");
  detailEl.innerHTML = loadingHtml();
  const submissions = await api(`/hr/candidates/${candidateId}/appearances/${appearanceId}/report`);
  detailEl.innerHTML = renderSubmissionsPanels(submissions);
}

async function generateCandidateSummary(id) {
  const body = document.getElementById("candidate-summary-body");
  body.innerHTML = `<p class="muted">Generating (a few seconds)...</p>`;
  try {
    const result = await api(`/hr/candidates/${id}/summary`, { method: "POST" });
    candidateSummaryData = result;

    const roundBlocks = result.round_comments.map((rc) => {
      const submission = candidateDetailSubmissions.find((s) => s.round_number === rc.round_number);
      const label = ROUND_LABELS[rc.round_number] || `Round ${rc.round_number}`;
      const scoreNote = submission && submission.score
        ? `<strong class="${submission.score.final_score >= passingScoreForRound(rc.round_number) ? "score-good" : "score-bad"}">${submission.score.final_score}/100</strong>`
        : submission && submission.status === "scoring_failed"
          ? `<span class="score-bad">scoring failed</span>`
          : `<span class="muted">not scored yet</span>`;
      return `
        <div class="panel-inset">
          <h5>Round ${rc.round_number} - ${escapeHtml(label)} ${scoreNote}</h5>
          <p>${escapeHtml(rc.comment)}</p>
        </div>
      `;
    }).join("");

    body.innerHTML = `
      ${roundBlocks}
      <div class="panel-inset">
        <h5>Final Summary</h5>
        <p>${escapeHtml(result.final_summary)}</p>
      </div>
      <div class="row">
        <button onclick="downloadCandidateSummaryPdf(${id}, '${escapeHtml(result.candidate_email)}')">Download as PDF</button>
      </div>
    `;
  } catch (e) {
    body.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
  }
}

async function downloadCandidateSummaryPdf(id, email) {
  // Not api() on purpose - that helper always calls res.json(), which
  // would fail on this endpoint's binary PDF response.
  try {
    // fetch() itself can reject (network failure) before there's even a
    // response to check .ok on - same gap login() had, same fix.
    const res = await fetch(`/hr/candidates/${id}/summary/pdf`, {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({
        round_comments: candidateSummaryData.round_comments,
        final_summary: candidateSummaryData.final_summary,
      }),
    });
    if (!res.ok) {
      alert("Couldn't generate the PDF - try again.");
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${email.replace("@", "_at_").replace(/\./g, "_")}-summary.pdf`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    alert("Couldn't reach the server - check your connection and try again.");
  }
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
const ROUND3_RESPONSE_KIND_BADGE = { clarify: "badge-partial", refuse: "badge-fail", code_edit: "badge-pass", direct_edit: "badge-pass" };

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
      <p><strong>Assistant:</strong> <span class="badge ${ROUND3_RESPONSE_KIND_BADGE[t.response_kind] || ""}">${escapeHtml(t.response_kind)}</span> ${escapeHtml(t.response_message)}</p>
      ${t.code_after ? `<pre class="code-snippet">${escapeHtml(t.code_after)}</pre>` : ""}
    </div>
  `).join("");

  const runsHtml = (s.round3_runs || []).map((r) => `
    <div class="panel-inset round3-coding-run">
      <p class="muted">Run at ${formatDate(r.created_at)} - ${r.timed_out ? "timed out" : r.infra_error ? "execution service error" : `exit code ${r.exit_code}`}</p>
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
      <thead><tr><th>Title</th><th>Steps</th><th>Expected</th></tr></thead>
      <tbody>
        ${(rows || []).map((r) => `<tr><td>${escapeHtml(r.title)}</td><td>${escapeHtml(r.steps)}</td><td>${escapeHtml(r.expected_result)}</td></tr>`).join("") || '<tr><td colspan="3" class="muted">-</td></tr>'}
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
        ${h.experience_band} · ${escapeHtml(h.title)}
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

// ==================== Candidate ====================
//
// No scores, no "what did I miss", no comparison against the reference
// ever render here - that's HR's dashboard (see openCandidateDetail
// above). A candidate mid-round should feel like they're taking a real
// timed assessment, not previewing their own grading.

// Progress is round-gated one at a time: finishing a round locks it
// (nothing to revisit - no results shown) and unlocks exactly the next
// one. Recomputed from /candidate/submissions rather than trusted
// client state, since that's the same source of truth the backend's
// own _require_round_unlocked check uses.
async function refreshCandidateNav() {
  const submissions = await api("/candidate/submissions");
  candidateCompletedRounds = submissions
    .filter((s) => s.status === "submitted" || s.status === "scored")
    .map((s) => s.round_number);
  const nextRound = [1, 2, 3, 4].find((n) => !candidateCompletedRounds.includes(n));
  renderCandidateRoundNav();

  if (nextRound === undefined) {
    currentRound = 0; // nothing in the nav is "active" once everything's submitted
    stopTimer(); // defensive - loadRound (which also stops it) isn't reached on this branch
    resetTopbarTimer();
    disarmTabGuard(); // defensive - loadRound (which also disarms) isn't reached on this branch
    renderCandidateRoundNav();
    document.getElementById("round-view").innerHTML = `
      <h3>All rounds complete</h3>
      <p class="muted">You've submitted every round. HR will be in touch with next steps.</p>
    `;
    return;
  }

  candidateUnlockedRound = nextRound;
  loadRound(candidateUnlockedRound);
}

function renderCandidateRoundNav() {
  const nav = document.getElementById("candidate-round-nav");
  // done/current/locked are the three states a candidate actually
  // spends their time looking at - each gets its own tick treatment
  // (checkmark / accent / dimmed) rather than one generic "active" flag.
  nav.innerHTML = `
    <div class="rail-section-label">Assessment</div>
    <nav class="tick-rail">
      ${[1, 2, 3, 4].map((n) => {
        const done = candidateCompletedRounds.includes(n);
        const isUnlocked = n === candidateUnlockedRound && !done;
        const stateClass = done ? "done" : n === currentRound ? "active" : !isUnlocked ? "locked" : "";
        return `
          <button class="tick ${stateClass}" ${isUnlocked ? "" : "disabled"} onclick="loadRound(${n})">
            <span class="tick-num">${done ? "&#10003;" : n}</span>
            <span class="tick-label">${ROUND_LABELS[n]}</span>
          </button>
        `;
      }).join("")}
    </nav>
  `;
  if (currentRound >= 1 && currentRound <= 4) {
    setPageHeader("Candidate Assessment", `Round ${currentRound} · ${ROUND_LABELS[currentRound]}`, "");
  } else {
    setPageHeader("Candidate Assessment", "Assessment complete", "");
  }
}

async function loadRound(n) {
  currentRound = n;
  stopTimer();
  resetTopbarTimer();
  disarmTabGuard();
  renderCandidateRoundNav();
  const box = document.getElementById("round-view");
  let state;
  try {
    state = await api(`/candidate/round/${n}`);
  } catch (e) {
    box.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
    return;
  }
  renderRoundView(box, n, state);
}

function renderRoundView(box, n, state) {
  const { scenario, submission } = state;

  if (!scenario) {
    box.innerHTML = `<h3>Round ${n}: ${ROUND_LABELS[n]}</h3><div class="empty-state">Not available yet - check back once HR has published this round's scenario.</div>`;
    return;
  }

  if (submission && (submission.status === "submitted" || submission.status === "scored")) {
    // Only reachable via a stale nav click (disabled buttons prevent it
    // normally) - a neutral landing, no status/score wording at all.
    box.innerHTML = `<h3>Round ${n}: ${escapeHtml(scenario.title)}</h3><p class="muted">You've already submitted this round.</p>`;
    return;
  }

  if (!submission) {
    if (n === 3) {
      box.innerHTML = `
        <h3>Round ${n}: ${escapeHtml(scenario.title)}</h3>
        ${formatScenarioDescription(scenario.description)}
        <p class="muted">Time limit: ${scenario.time_limit_minutes} minutes, starting once you confirm below.</p>
      `;
      showRound3CodingIntro();
      return;
    }
    if (n === 4) {
      // No Start button here at all - the briefing modal below is the
      // only way in, appearing the instant this round is opened. Its own
      // "Got it - Start Round 4" button is what actually starts the
      // round (see confirmStartRound4) - reading this costs no time
      // either way, since the timer only starts on that click.
      box.innerHTML = `
        <h3>Round ${n}: ${escapeHtml(scenario.title)}</h3>
        ${formatScenarioDescription(scenario.description)}
        <p class="muted">Time limit: ${scenario.time_limit_minutes} minutes, starting once you confirm below.</p>
      `;
      showRound4Intro();
      return;
    }
    // Same pattern as round 4: no separate Start button - the briefing
    // modal appears the instant the round is opened, and its own button
    // is what actually starts the timer.
    box.innerHTML = `
      <h3>Round ${n}: ${escapeHtml(scenario.title)}</h3>
      ${formatScenarioDescription(scenario.description)}
      <p class="muted">Time limit: ${scenario.time_limit_minutes} minutes, starting once you confirm below.</p>
    `;
    showRoundIntro(n, scenario.time_limit_minutes);
    return;
  }

  renderRoundEntry(n, box, scenario, submission);
}

// Shared open/close for every full-screen modal-overlay (round intros,
// the fullscreen guard) - appends first, then flips .modal-open a frame
// later so the CSS transition actually has a "before" state to animate
// from; closing reverses that and waits out the transition before
// removing the node, so it fades instead of slamming away.
// Every real, visible, non-disabled control inside a modal - the set
// Tab is allowed to cycle through while the modal is open (see
// openModalOverlay's trap handler below). offsetParent === null is the
// standard cheap "is this actually rendered" check (catches
// display:none - not currently used inside any modal here, but a
// correct trap has to filter for it regardless).
function _modalFocusableElements(box) {
  return Array.from(box.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'))
    .filter((el) => !el.disabled && el.offsetParent !== null);
}

// WAI-ARIA dialog pattern - the three things a hand-rolled modal has to
// implement itself that a Radix/shadcn Dialog would give for free:
// role="dialog"+aria-modal so assistive tech knows the rest of the page
// is inert, focus moving INTO the dialog on open (not left stranded on
// whatever triggered it, now hidden behind the scrim), and Tab trapped
// inside it so a keyboard user can never tab out to the inert page
// behind it. closeModalOverlay below restores focus back to the
// trigger on close - the other half of this same pattern.
function openModalOverlay(overlay) {
  document.body.appendChild(overlay);
  requestAnimationFrame(() => requestAnimationFrame(() => overlay.classList.add("modal-open")));

  const box = overlay.querySelector(".modal-box");
  if (!box) return;

  box.setAttribute("role", "dialog");
  box.setAttribute("aria-modal", "true");
  // Every .modal-box here happens to lead with an <h3> - reused as the
  // dialog's accessible name rather than asking each call site to name
  // its own modal a second time.
  const heading = box.querySelector("h3");
  if (heading) {
    if (!heading.id) heading.id = `modal-heading-${Math.random().toString(36).slice(2, 8)}`;
    box.setAttribute("aria-labelledby", heading.id);
  }

  overlay._previouslyFocused = document.activeElement;
  const focusable = _modalFocusableElements(box);
  if (focusable.length > 0) {
    focusable[0].focus();
  } else {
    // Defensive only - every modal in this app has at least one real
    // button, so this path shouldn't actually trigger; tabindex="-1"
    // lets the box itself take focus if a future modal somehow doesn't.
    box.tabIndex = -1;
    box.focus();
  }
  // The neutral briefing modals (see showRoundIntro/showRound4Intro/
  // showRound3CodingIntro) put their only focusable control - the "Got
  // it" button - at the BOTTOM, after several paragraphs of notes. box
  // is capped to max-height + overflow-y:auto (see style.css), so the
  // .focus() call above scrolls that button into view, which lands the
  // box scrolled to the bottom the instant it opens - the candidate
  // sees the last line, not the first. Force it back to the top after
  // focus has already landed, so the notes are readable from the start
  // regardless of which control ended up focused.
  box.scrollTop = 0;

  overlay._trapKeydown = (e) => {
    if (e.key !== "Tab") return;
    const els = _modalFocusableElements(box);
    if (els.length === 0) { e.preventDefault(); return; }
    const first = els[0];
    const last = els[els.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };
  overlay.addEventListener("keydown", overlay._trapKeydown);
}

function closeModalOverlay(id) {
  const overlay = document.getElementById(id);
  if (!overlay) return;
  overlay.classList.remove("modal-open");
  if (overlay._trapKeydown) overlay.removeEventListener("keydown", overlay._trapKeydown);
  // Send focus back to whatever had it before this modal opened (e.g.
  // the button that triggered it) rather than leaving it stranded on an
  // element that's about to be removed from the DOM entirely.
  if (overlay._previouslyFocused && document.body.contains(overlay._previouslyFocused)) {
    overlay._previouslyFocused.focus();
  }
  // Tracked on the element itself so a re-open during this 200ms fade
  // (see showFsOverlay) can cancel it - otherwise the delayed remove()
  // still fires and deletes an overlay that was just reopened.
  clearTimeout(overlay._closeTimeout);
  overlay._closeTimeout = setTimeout(() => overlay.remove(), 200);
}

function showRoundIntro(n, timeLimitMinutes) {
  const overlay = document.createElement("div");
  overlay.id = "round-intro-overlay";
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-box neutral">
      <h3>Before you start Round ${n}</h3>
      <ul>
        <li>You'll have ${timeLimitMinutes} minutes once you click below - the timer starts immediately.</li>
        <li>If time runs out, whatever you've written gets submitted automatically as it stands.</li>
        <li>Finished earlier? Submit yourself and move straight to the next round - no need to wait out the clock.</li>
      </ul>
      <div class="row">
        <button onclick="confirmStartRound(${n})">Got it - Start Round ${n}</button>
      </div>
    </div>
  `;
  openModalOverlay(overlay);
}

function confirmStartRound(n) {
  closeModalOverlay("round-intro-overlay");
  startRound(n);
}

async function startRound(n) {
  const submission = await api(`/candidate/round/${n}/start`, { method: "POST" });
  const state = await api(`/candidate/round/${n}`);
  const box = document.getElementById("round-view");
  renderRoundEntry(n, box, state.scenario, submission);
}

// One-time briefing before round 4's timer starts - shown instead of an
// immediate Start, since round 4's format (prompt-driven, an
// intentionally imperfect assistant, no fixed checklist) isn't
// self-explanatory the way rounds 1/2's plain forms are. Reading this
// doesn't cost any time - the timer only starts once startRound(4) is
// actually called, from confirmStartRound4 below. Deliberately no
// specifics on how often or how the assistant gets things wrong - that's
// what the round is testing; this only sets expectations, not answers.
function showRound4Intro() {
  const overlay = document.createElement("div");
  overlay.id = "round4-intro-overlay";
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-box neutral">
      <h3>Before you start Round 4</h3>
      <ul>
        <li>Your Round 1 test cases are the starting point, not a limit - add as many extra as the scenario needs. Describe each to an AI assistant, which simulates running it and reports what it observed (no code involved). You'll have a test environment reference (sample data, credentials, API/DB details) and reference app screens as your source of truth.</li>
        <li>The assistant won't always get it right - it may skip a check, misreport a result, or be wrong on purpose. Review every response like a test log you didn't write, and refine your prompts until you're confident it's actually correct.</li>
        <li>Scored mainly on prompting and verification quality - catching issues, asking the right follow-ups, converging on a correct result - not on automating your entire Round 1 list, which isn't realistic or measured. Automating more than one area (UI, API, DB, end-to-end) earns bonus credit, on top of doing a few well.</li>
        <li>Steering the assistant to skip verification or reveal what scores well won't work and gets flagged as a concern (e.g. "just mark everything passing"). It reports what happened, not what looks good.</li>
        <li>Timer starts the moment you click below.</li>
      </ul>
      <div class="row">
        <button onclick="confirmStartRound4()">Got it - Start Round 4</button>
      </div>
    </div>
  `;
  openModalOverlay(overlay);
}

// Split from the intro (see showRound4Intro) so the language choice
// happens as its own confirmed step, matching Round 3's
// confirmRound3CodingIntro/confirmStartRound3Coding pattern - not
// silently defaulting round4DefaultLanguage's initial "python" value
// the way it used to. Unlike Round 3 this choice ISN'T locked for the
// round - round4OnLanguageChange already lets the candidate switch per
// turn - this only sets what that default starts as, so no code has to
// change to support it.
function confirmStartRound4() {
  closeModalOverlay("round4-intro-overlay");
  document.getElementById("round-view").insertAdjacentHTML("beforeend", `
    <div class="field-row" style="align-items:center">
      <span class="muted">Preferred language for generated code</span>
      <select id="round4-language-select" onchange="round4OnLanguageSelectChange()">
        <option value="" selected>Select a language...</option>
        ${ROUND4_CODE_LANGUAGE_OPTIONS.map(([value, label]) => `<option value="${value}">${label}</option>`).join("")}
      </select>
    </div>
    <p class="muted">You can switch languages per turn once the round starts - this just sets your starting default.</p>
    <div class="row">
      <button id="round4-start-btn" onclick="confirmStartRound4WithLanguage()" disabled>Start Round 4</button>
    </div>
  `);
}

function round4OnLanguageSelectChange() {
  const language = document.getElementById("round4-language-select").value;
  document.getElementById("round4-start-btn").disabled = !language;
}

function confirmStartRound4WithLanguage() {
  const language = document.getElementById("round4-language-select").value;
  if (!language) return;
  round4DefaultLanguage = language;
  startRound(4);
}

// Each round's candidate-facing shape is genuinely different now: round
// 1 is repeatable test-case rows, round 2 is a shorter investigation
// list + one root-cause conclusion, round 4 is conversational. No
// shared "structured rounds" bucket anymore - just dispatch by number.
function renderRoundEntry(n, box, scenario, submission) {
  if (n === 1) {
    renderEntryForm(box, scenario, submission);
  } else if (n === 2) {
    renderInvestigationForm(box, scenario, submission);
  } else if (n === 3) {
    renderRound3CodingView(box);
  } else {
    renderRound4View(box);
  }
}

// Periodic autosave for rounds 1/2's whole-form in-progress content (see
// candidate.py's PATCH /round/{n}/draft) - the same "so a crash/refresh
// doesn't silently lose typed work while the timer keeps running"
// guarantee round 3's test cases already have (see
// ROUND4_DRAFT_DEBOUNCE_MS below), just for one whole-round form instead
// of a per-test-case composer. Debounced so normal typing doesn't fire a
// request per keystroke; best-effort (like round4FlushDraft) since the
// DOM itself is always the source of truth for what's on screen right
// now - a failed autosave only risks losing up to the debounce window's
// worth of typing on an actual crash/refresh, never anything visible.
const ROUND_DRAFT_DEBOUNCE_MS = 2000;
let roundDraftTimer = null;

function scheduleRoundDraftSave(roundNumber, buildPayload) {
  clearTimeout(roundDraftTimer);
  roundDraftTimer = setTimeout(() => flushRoundDraft(roundNumber, buildPayload), ROUND_DRAFT_DEBOUNCE_MS);
}

function flushRoundDraft(roundNumber, buildPayload) {
  clearTimeout(roundDraftTimer);
  roundDraftTimer = null;
  api(`/candidate/round/${roundNumber}/draft`, {
    method: "PATCH",
    body: JSON.stringify(buildPayload()),
  }).catch(() => {}); // best-effort - see comment above
}

// Round 1: repeatable test-case rows (title/preconditions/steps/expected_result).

function round1DraftPayload() {
  return { content: collectRows() };
}

function renderEntryForm(box, scenario, submission) {
  rowCount = 0;
  box.innerHTML = `
    <h3>Round 1: ${escapeHtml(scenario.title)}</h3>
    ${formatScenarioDescription(scenario.description)}
    <div class="table-scroll">
      <table>
        <thead><tr><th>SI.No</th><th>Title</th><th>Preconditions</th><th>Steps</th><th>Expected result</th><th></th></tr></thead>
        <tbody>${exampleTestCaseRowHtml()}</tbody>
        <tbody id="tc-rows"></tbody>
      </table>
    </div>
    <div class="row">
      <button onclick="addRow()">+ Add row</button>
      <button id="round1-submit-btn" onclick="doSubmitRound1()">Submit</button>
    </div>
    <p id="submit-status" class="muted"></p>
  `;
  // Resume from whatever was last autosaved server-side (see
  // round1DraftPayload/save_round_draft) rather than always starting
  // from two blank rows - a page refresh mid-round must not look like a
  // fresh start.
  const savedRows = Array.isArray(submission.content) ? submission.content : [];
  if (savedRows.length > 0) {
    savedRows.forEach((row) => addRow(row));
  } else {
    addRow();
    addRow();
  }
  round1UpdateSubmitState();
  const deadline = new Date(submission.started_at + "Z").getTime() + scenario.time_limit_minutes * 60 * 1000;
  startTimer(deadline, () => {
    document.getElementById("timer").textContent = "Time's up - submitting automatically...";
    doSubmitRound1(true);
  }, 1);
}

// Round 2: an investigation write-up, not test cases - a short repeatable
// list of areas checked (SI.No + one free-text field each) plus a single
// closing "Possible Root Cause" box where the candidate states what they
// eliminated and what they concluded. See schemas.Round2SubmissionCreate.

function round2DraftPayload() {
  return {
    investigation: collectInvestigationRows(),
    root_cause: document.getElementById("inv-root-cause").value,
  };
}

function renderInvestigationForm(box, scenario, submission) {
  rowCount = 0;
  box.innerHTML = `
    <h3>Round 2: ${escapeHtml(scenario.title)}</h3>
    ${formatScenarioDescription(scenario.description)}
    <div class="table-scroll">
      <table>
        <thead><tr><th>SI.No</th><th>Investigation area</th><th></th></tr></thead>
        <tbody>
          <tr class="example-row"><td class="tc-no">Ex</td><td>Checked the application logs around the time of the issue for related error messages (format only, not a hint for this scenario)</td><td></td></tr>
        </tbody>
        <tbody id="inv-rows"></tbody>
      </table>
    </div>
    <div class="row">
      <button onclick="addInvestigationRow()">+ Add row</button>
    </div>
    <h4>Possible Root Cause</h4>
    <p class="muted">What you investigated, which areas you eliminated, and your conclusion.</p>
    <p class="muted example-note">Example format: "The [component] shows [incorrect behavior] when [condition]. Ruled out [alternative cause] because [reason]. Root cause is [cause], confirmed by [evidence]."</p>
    <textarea id="inv-root-cause" oninput="scheduleRoundDraftSave(2, round2DraftPayload); round2UpdateSubmitState()"></textarea>
    <div class="row">
      <button id="round2-submit-btn" onclick="doSubmitRound2Investigation()">Submit</button>
    </div>
    <p id="submit-status" class="muted"></p>
  `;
  // Resume from whatever was last autosaved server-side (see
  // round2DraftPayload/save_round_draft) rather than always starting
  // from two blank rows - a page refresh mid-round must not look like a
  // fresh start.
  const saved = submission.content && typeof submission.content === "object" ? submission.content : null;
  const savedRows = saved && Array.isArray(saved.investigation) ? saved.investigation : [];
  if (savedRows.length > 0) {
    savedRows.forEach((row) => addInvestigationRow(row));
  } else {
    addInvestigationRow();
    addInvestigationRow();
  }
  if (saved && saved.root_cause) {
    document.getElementById("inv-root-cause").value = saved.root_cause;
  }
  round2UpdateSubmitState();
  const deadline = new Date(submission.started_at + "Z").getTime() + scenario.time_limit_minutes * 60 * 1000;
  startTimer(deadline, () => {
    document.getElementById("timer").textContent = "Time's up - submitting automatically...";
    doSubmitRound2Investigation(true);
  }, 2);
}

function addInvestigationRow(initial = null) {
  const id = rowCount++;
  const tbody = document.getElementById("inv-rows");
  const tr = document.createElement("tr");
  tr.id = `inv-row-${id}`;
  tr.innerHTML = `
    <td class="inv-no"></td>
    <td><textarea class="inv-area" oninput="scheduleRoundDraftSave(2, round2DraftPayload); round2UpdateSubmitState()"></textarea></td>
    <td><button onclick="removeInvestigationRow('inv-row-${id}')">Remove</button></td>
  `;
  tbody.appendChild(tr);
  if (initial) tr.querySelector(".inv-area").value = initial.area || "";
  renumberInvestigationRows();
}

function removeInvestigationRow(rowId) {
  document.getElementById(rowId).remove();
  renumberInvestigationRows();
  scheduleRoundDraftSave(2, round2DraftPayload); // removing a row must survive a refresh too, not just additions
  round2UpdateSubmitState();
}

// Mirrors doSubmitRound2Investigation's own gate (at least one
// investigation row, plus a non-empty root cause) - see
// round1UpdateSubmitState for why this can't just live behind the
// click.
function round2UpdateSubmitState() {
  const submitBtn = document.getElementById("round2-submit-btn");
  if (!submitBtn) return;
  const hasRow = collectInvestigationRows().length > 0;
  const hasRootCause = document.getElementById("inv-root-cause").value.trim().length > 0;
  submitBtn.disabled = !(hasRow && hasRootCause);
}

function renumberInvestigationRows() {
  document.querySelectorAll("#inv-rows .inv-no").forEach((cell, i) => {
    cell.textContent = i + 1;
  });
}

function collectInvestigationRows() {
  return [...document.querySelectorAll("#inv-rows tr")]
    .map((tr) => ({ area: tr.querySelector(".inv-area").value.trim() }))
    .filter((r) => r.area);
}

async function doSubmitRound2Investigation(force = false) {
  // Timer/guard only stop once the submit actually goes through below - a
  // validation failure here means the round is still very much in
  // progress and must keep counting down with the guard still armed.
  const statusEl = document.getElementById("submit-status");
  const submitBtn = document.getElementById("round2-submit-btn");
  if (submitBtn && submitBtn.disabled) return; // guards against a double-click firing two concurrent submits
  const investigation = collectInvestigationRows();
  const root_cause = document.getElementById("inv-root-cause").value.trim();
  // force (the timer just hit zero) skips these - they exist to help a
  // candidate who still has time avoid wasting their one submit, not to
  // block the round from ever closing once time is actually up.
  if (!force) {
    if (investigation.length === 0) {
      statusEl.textContent = "Add at least one investigation row before submitting.";
      return;
    }
    if (!root_cause) {
      statusEl.textContent = "Fill in the Possible Root Cause box before submitting.";
      return;
    }
  }
  if (submitBtn) submitBtn.disabled = true;
  try {
    await api("/candidate/round/2/submit", { method: "POST", body: JSON.stringify({ investigation, root_cause }) });
    stopTimer();
    disarmTabGuard();
    refreshCandidateNav();
  } catch (e) {
    if (force) {
      // Nothing submittable even now, or the server's own deadline check
      // beat this attempt - the round still has to end, saving whatever's
      // here. See forceExpireRound.
      await forceExpireRound(2, { investigation, root_cause });
      return;
    }
    if (submitBtn) submitBtn.disabled = false;
    statusEl.textContent = e.message;
  }
}

// ---- Round 3: AI-prompted coding. The candidate never edits code
// directly - every code_after comes from the assistant's response to a
// candidate instruction (see routers/candidate.py's round3_coding_turn).
// A single evolving code buffer per submission, unlike Round 4's
// multiple self-titled test cases - so there's one composer, one code
// pane, one run history, not per-test-case tabs. ----

function showRound3CodingIntro() {
  const overlay = document.createElement("div");
  overlay.id = "round3-coding-intro-overlay";
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-box neutral">
      <h3>Before you start Round 3</h3>
      <ul>
        <li>Direct an assistant with plain-English instructions (variables, loops, data, what to read/print) - it writes the code. You can also type or paste code directly into the pane instead; either way, every decision is yours.</li>
        <li>If you write code directly, the assistant only fixes syntax there - it never touches your logic.</li>
        <li>Bundle several steps in one instruction (e.g. "read two numbers and print their sum") - but asking it to "write the whole program," "give me the solution," or pick an approach gets refused, even as your first instruction.</li>
        <li>It won't decide for you either - "which loop is right?" gets a question back, not an answer.</li>
        <li>Run is a real terminal: code executes for real, and if it calls input(), you type the answer and it continues. Debugging is on you - the assistant won't fix a pasted error; tell it exactly what to change.</li>
        <li>Submission runs against hidden test cases you never see - scored on how many pass, how precisely you specified things, and whether you pushed toward a more efficient solution.</li>
        <li>Pick your language next - Python, Java, or JavaScript - the timer starts then, and the choice is final for the round.</li>
      </ul>
      <div class="row">
        <button onclick="confirmRound3CodingIntro()">Got it</button>
      </div>
    </div>
  `;
  openModalOverlay(overlay);
}

// Split from the intro (see showRound3CodingIntro) so language choice
// happens as its own step after the candidate has actually read the
// notes, not bundled into the same click - matching round 4's intro,
// which also only ever asks the candidate to confirm one thing at a
// time. The language picker renders into the round view itself (below
// the scenario title/description already sitting there - see
// renderRoundEntry), not another modal.
function confirmRound3CodingIntro() {
  closeModalOverlay("round3-coding-intro-overlay");
  document.getElementById("round-view").insertAdjacentHTML("beforeend", `
    <div class="field-row" style="align-items:center">
      <span class="muted">Language</span>
      <select id="round3-language-select" onchange="round3OnLanguageSelectChange()">
        <option value="" selected>Select a language...</option>
        <option value="python">Python</option>
        <option value="java">Java</option>
        <option value="javascript">JavaScript</option>
      </select>
    </div>
    <p class="muted">This is a one-time choice - you won't be able to change it once the round starts.</p>
    <div class="row">
      <button id="round3-start-btn" onclick="confirmStartRound3Coding()" disabled>Start Round 3</button>
    </div>
  `);
}

// A blank first option (no default language pre-selected) plus this
// disable/enable toggle is what actually mandates the choice - without
// it the select's first real <option> would be silently accepted as
// the language the instant the candidate clicked Start.
function round3OnLanguageSelectChange() {
  const language = document.getElementById("round3-language-select").value;
  document.getElementById("round3-start-btn").disabled = !language;
}

function confirmStartRound3Coding() {
  const language = document.getElementById("round3-language-select").value;
  if (!language) return;
  startRound3Coding(language);
}

async function startRound3Coding(language) {
  await api("/candidate/round/3/start", { method: "POST", body: JSON.stringify({ language }) });
  const box = document.getElementById("round-view");
  renderRoundEntry(3, box, null, null);
}

let round3CodingState = null;
let round3CodingDraftTimer = null;
const ROUND3_CODING_DRAFT_DEBOUNCE_MS = 1000;
let round3CodingEditingCode = false;

// ---- Round 3 interactive terminal (the candidate's own Run button) ----
//
// Genuinely interactive, not batch: /round/3/run/start kicks off a real,
// live subprocess server-side (see execution_service.InteractiveSession);
// this polls /round/3/run/poll every ROUND3_RUN_POLL_MS for new output
// and lets the candidate answer whatever the program's own input() calls
// actually ask for via /round/3/run/input, exactly like typing into a
// real terminal - nothing here ever pre-supplies or guesses a value the
// candidate didn't type. Kept as a client-side transcript log
// (round3RunLog), not just "whatever the last poll said", because each
// poll returns the FULL accumulated output, not a delta - the log is
// built by diffing each poll against how much of stdout/stderr has
// already been rendered, and separately recording the candidate's own
// typed lines (echoed here since a real input() call never echoes them
// itself) at the exact point they were sent, so replaying the log reads
// like an actual terminal session in the order things happened.
const ROUND3_RUN_POLL_MS = 400;
let round3RunLog = [];
let round3RunKnownStdoutLen = 0;
let round3RunKnownStderrLen = 0;
let round3RunPollHandle = null;
let round3RunActive = false;
let round3RunFinalStatus = null; // {timed_out, infra_error, exit_code} once exited

async function renderRound3CodingView(box) {
  round3CodingEditingCode = false;
  try {
    round3CodingState = await api("/candidate/round/3/state");
  } catch (e) {
    box.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
    return;
  }
  renderRound3CodingLayout(box);
  const submission = round3CodingState.submission;
  if (submission.status === "in_progress" && submission.started_at) {
    const deadline = new Date(submission.started_at + "Z").getTime()
      + round3CodingState.scenario.time_limit_minutes * 60 * 1000;
    startTimer(deadline, round3CodingAutoSubmit, 3);
  }
}

async function round3CodingAutoSubmit() {
  try {
    await api("/candidate/round/3/submit", { method: "POST" });
  } catch (e) {
    await api("/candidate/round/3/expire", { method: "POST", body: JSON.stringify({}) }).catch(() => {});
  }
  refreshCandidateNav();
}

function renderRound3CodingLayout(box) {
  const s = round3CodingState;
  if (!s) return;
  const turnsHtml = s.turns.map((t) => `
    <div class="round3-coding-turn">
      <p class="round3-coding-prompt"><strong>You:</strong> ${escapeHtml(t.candidate_prompt)}</p>
      <p class="round3-coding-response round3-coding-response-${t.response_kind}"><strong>Assistant:</strong> ${escapeHtml(t.response_message)}</p>
    </div>
  `).join("");

  const latestCode = [...s.turns].reverse().find((t) => t.code_after)?.code_after || "";

  box.innerHTML = `
    <h3>Round 3: ${escapeHtml(s.scenario.title)}</h3>
    ${formatScenarioDescription(s.scenario.description)}
    <p class="muted">Language: ${escapeHtml(s.language || "")}</p>
    <details class="hint-box">
      <summary><strong>Example conversation</strong> (format only, not a hint for this scenario)</summary>
      <p class="round3-coding-prompt"><strong>You:</strong> Write a program to add two numbers</p>
      <p class="round3-coding-response round3-coding-response-refuse"><strong>Assistant:</strong> I can't write this for you - tell me what you want built, and I'll write exactly that.</p>
      <p class="round3-coding-prompt"><strong>You:</strong> Read two integers from the user, one per line, and print their sum</p>
      <p class="round3-coding-response round3-coding-response-code_edit"><strong>Assistant:</strong> Added code to read two integers and print their sum.</p>
      <p class="round3-coding-prompt"><strong>You:</strong> Now handle the case where the input isn't a number</p>
      <p class="round3-coding-response round3-coding-response-clarify"><strong>Assistant:</strong> What should happen when the input isn't a number?</p>
      <p class="muted">Notice the assistant never names a technique, never offers multiple-choice options, and never builds more than exactly what was asked - that's true on every turn, not just these.</p>
    </details>
    <div class="round3-coding-grid">
      <div class="panel-inset round3-coding-pane">
        <p class="muted round3-pane-label">Conversation</p>
        <div class="round3-pane-body" id="round3-coding-turns">${turnsHtml || '<p class="muted">Nothing yet - tell the assistant what you need.</p>'}</div>
        <textarea id="round3-coding-message" placeholder="What do you want the assistant to do next?" oninput="round3CodingOnComposerInput(this.value)">${escapeHtml((s.submission.content && s.submission.content.draft_prompt) || "")}</textarea>
        <div class="row">
          <button id="round3-coding-send-btn" onclick="round3CodingSendMessage()">Send</button>
        </div>
      </div>
      <div class="panel-inset round3-coding-pane">
        <p class="muted round3-pane-label">Code</p>
        ${round3CodingEditingCode ? `
          <div class="code-editor-wrap">
            <div class="code-editor-gutter" id="round3-coding-code-edit-gutter"><span>1</span></div>
            <textarea id="round3-coding-code-edit" oninput="round3CodeEditorUpdateGutter(this)" onscroll="round3CodeEditorSyncScroll(this)">${escapeHtml(latestCode)}</textarea>
          </div>
          <div class="row">
            <button id="round3-coding-save-btn" onclick="round3CodingSaveDirectEdit()">Save</button>
            <button onclick="round3CodingCancelDirectEdit()">Cancel</button>
          </div>
        ` : `
          <div class="code-snippet code-with-lines" id="round3-coding-code">${latestCode ? codeWithLineNumbersHtml(latestCode) : '<div class="code-line-content">(no code yet)</div>'}</div>
          <div class="row">
            <button onclick="round3CodingStartDirectEdit()">Edit code</button>
            <button id="round3-coding-run-btn" onclick="round3CodingRun()" ${latestCode ? "" : "disabled"}>Run</button>
            <button class="btn-block" onclick="round3CodingSubmit()" ${s.turns.length > 0 ? "" : "disabled"}>Submit Round 3</button>
          </div>
        `}
        <p class="muted round3-pane-label">Terminal <span id="round3-terminal-status" class="muted"></span></p>
        <p class="muted">Read-only output from your code - you can't type into it.</p>
        <div class="round3-terminal" id="round3-terminal-output"></div>
        <div class="row" id="round3-terminal-input-row">
          <input id="round3-terminal-input" placeholder="Type your answer here, then press Enter" onkeydown="round3TerminalInputKeydown(event)" />
          <button onclick="round3TerminalSendInput()">Send</button>
        </div>
      </div>
    </div>
    <p id="round3-coding-status" class="muted"></p>
  `;
  // Full box.innerHTML re-render on every send/run (see round3CodingSendMessage/
  // round3CodingRun) resets scroll position to the top of a long, fixed-
  // height conversation pane - without this, the candidate has to
  // manually scroll down to their own latest message and the assistant's
  // reply every single turn, which only gets worse as the transcript
  // grows. Jump straight to the bottom after each render instead.
  const turnsBox = document.getElementById("round3-coding-turns");
  if (turnsBox) turnsBox.scrollTop = turnsBox.scrollHeight;
  // The terminal transcript (round3RunLog) lives across re-renders the
  // same way - a chat Send shouldn't wipe out a run still in progress or
  // just finished, so repaint it from the log rather than starting blank.
  renderRound3Terminal();
  // The edit textarea's gutter starts with a single placeholder <span> in
  // its own markup above - existing multi-line code (resuming an edit, or
  // code already pasted in) needs its real line count immediately, not
  // just after the candidate's next keystroke.
  const editTextarea = document.getElementById("round3-coding-code-edit");
  if (editTextarea) round3CodeEditorUpdateGutter(editTextarea);
}

// Line numbers make it possible to match a runtime error or a candidate's
// own bug report ("line 12 is wrong") back to the actual code, for both
// the read-only pane (LLM-generated) and the direct-edit textarea
// (candidate-written) - see round3-coding-code / round3-coding-code-edit
// above. Uses `white-space: pre` (no wrapping) rather than the pre-wrap
// this pane used before - numbers only stay meaningful against real
// source lines, not visually-wrapped ones, so long lines scroll
// horizontally instead (see .code-with-lines / .code-editor-wrap in
// style.css).
function codeWithLineNumbersHtml(code) {
  const lines = code.split("\n");
  const numbersHtml = lines.map((_, i) => `<span>${i + 1}</span>`).join("");
  return `
    <div class="code-line-numbers">${numbersHtml}</div>
    <div class="code-line-content">${escapeHtml(code)}</div>
  `;
}

// Keeps the direct-edit textarea's gutter in sync with its actual line
// count as the candidate types/pastes - only rewrites the gutter when the
// count genuinely changed, so a normal keystroke inside a line (not
// adding/removing a newline) doesn't thrash the DOM on every input event.
function round3CodeEditorUpdateGutter(textarea) {
  const gutter = document.getElementById("round3-coding-code-edit-gutter");
  if (!gutter) return;
  const lineCount = textarea.value.split("\n").length;
  if (gutter.children.length === lineCount) return;
  gutter.innerHTML = Array.from({ length: lineCount }, (_, i) => `<span>${i + 1}</span>`).join("");
}

function round3CodeEditorSyncScroll(textarea) {
  const gutter = document.getElementById("round3-coding-code-edit-gutter");
  if (gutter) gutter.scrollTop = textarea.scrollTop;
}

function round3CodingOnComposerInput(value) {
  clearTimeout(round3CodingDraftTimer);
  round3CodingDraftTimer = setTimeout(() => round3CodingFlushDraft(value), ROUND3_CODING_DRAFT_DEBOUNCE_MS);
}

function round3CodingFlushDraft(value) {
  api("/candidate/round/3/draft", { method: "PATCH", body: JSON.stringify({ draft_prompt: value }) }).catch(() => {});
}

async function round3CodingSendMessage() {
  const textarea = document.getElementById("round3-coding-message");
  const prompt = textarea.value.trim();
  const statusEl = document.getElementById("round3-coding-status");
  const sendBtn = document.getElementById("round3-coding-send-btn");
  if (!prompt) return;
  if (round3CodingEditingCode) {
    statusEl.className = "error-text";
    statusEl.textContent = "Finish or cancel your code edit before sending an instruction.";
    return;
  }
  sendBtn.disabled = true;
  statusEl.className = "muted";
  statusEl.textContent = "Sending...";
  try {
    clearTimeout(round3CodingDraftTimer);
    await api("/candidate/round/3/turn", { method: "POST", body: JSON.stringify({ candidate_prompt: prompt }) });
    round3CodingState = await api("/candidate/round/3/state");
    renderRound3CodingLayout(document.getElementById("round-view"));
    statusEl.textContent = "";
  } catch (e) {
    statusEl.className = "error-text";
    statusEl.textContent = e.message;
  } finally {
    sendBtn.disabled = false;
  }
}

function round3CodingStartDirectEdit() {
  round3CodingEditingCode = true;
  renderRound3CodingLayout(document.getElementById("round-view"));
}

function round3CodingCancelDirectEdit() {
  round3CodingEditingCode = false;
  renderRound3CodingLayout(document.getElementById("round-view"));
}

async function round3CodingSaveDirectEdit() {
  const textarea = document.getElementById("round3-coding-code-edit");
  const code = textarea.value.trim();
  const statusEl = document.getElementById("round3-coding-status");
  const saveBtn = document.getElementById("round3-coding-save-btn");
  if (!code) return;
  saveBtn.disabled = true;
  statusEl.className = "muted";
  statusEl.textContent = "Saving...";
  try {
    await api("/candidate/round/3/edit", { method: "POST", body: JSON.stringify({ code }) });
    round3CodingState = await api("/candidate/round/3/state");
    round3CodingEditingCode = false;
    renderRound3CodingLayout(document.getElementById("round-view"));
    statusEl.textContent = "";
  } catch (e) {
    statusEl.className = "error-text";
    statusEl.textContent = e.message;
  } finally {
    saveBtn.disabled = false;
  }
}

// Starts a real, live run (see execution_service.InteractiveSession) -
// nothing is fed to it upfront; it runs until its own code either
// finishes, crashes, or actually blocks on an input() call waiting for
// the candidate to answer through the terminal box below.
async function round3CodingRun() {
  if (round3RunPollHandle) {
    clearInterval(round3RunPollHandle);
    round3RunPollHandle = null;
  }
  round3RunLog = [];
  round3RunKnownStdoutLen = 0;
  round3RunKnownStderrLen = 0;
  round3RunFinalStatus = null;
  round3RunActive = true;
  renderRound3Terminal();

  const statusEl = document.getElementById("round3-coding-status");
  const runBtn = document.getElementById("round3-coding-run-btn");
  runBtn.disabled = true;
  statusEl.className = "muted";
  statusEl.textContent = "";
  try {
    const result = await api("/candidate/round/3/run/start", { method: "POST" });
    round3ApplyRunPoll(result);
  } catch (e) {
    round3RunActive = false;
    statusEl.className = "error-text";
    statusEl.textContent = e.message;
    renderRound3Terminal();
  } finally {
    runBtn.disabled = false;
  }
}

// Applies one poll's worth of FULL accumulated stdout/stderr - diffs
// each against how much has already been appended to round3RunLog so
// only the genuinely NEW slice gets added (see this section's opening
// comment for why a delta, not the whole string, is what goes in the log).
function round3ApplyRunPoll(result) {
  const newStdout = result.stdout.slice(round3RunKnownStdoutLen);
  round3RunKnownStdoutLen = result.stdout.length;
  if (newStdout) round3RunLog.push({ type: "output", text: newStdout });

  const newStderr = result.stderr.slice(round3RunKnownStderrLen);
  round3RunKnownStderrLen = result.stderr.length;
  if (newStderr) round3RunLog.push({ type: "stderr", text: newStderr });

  if (result.exited) {
    round3RunActive = false;
    round3RunFinalStatus = { timed_out: result.timed_out, infra_error: result.infra_error, exit_code: result.exit_code };
    if (round3RunPollHandle) {
      clearInterval(round3RunPollHandle);
      round3RunPollHandle = null;
    }
    // Refreshes round3CodingState so the now-persisted Round3ExecutionRun
    // shows up in history too (e.g. an HR report later) - deliberately
    // NOT re-rendering the whole layout from it, which would disrupt the
    // terminal transcript already sitting on screen for no reason.
    api("/candidate/round/3/state").then((s) => { round3CodingState = s; }).catch(() => {});
  } else if (!round3RunPollHandle) {
    round3RunPollHandle = setInterval(round3PollRun, ROUND3_RUN_POLL_MS);
  }
  renderRound3Terminal();
}

async function round3PollRun() {
  // The candidate navigated away from this view (e.g. switched to
  // another round or logged out) - nothing left to poll into, and no
  // Round 3 elements left in the DOM to check against next time either.
  if (!document.getElementById("round3-terminal-output")) {
    clearInterval(round3RunPollHandle);
    round3RunPollHandle = null;
    return;
  }
  try {
    const result = await api("/candidate/round/3/run/poll");
    round3ApplyRunPoll(result);
  } catch (e) {
    clearInterval(round3RunPollHandle);
    round3RunPollHandle = null;
    round3RunActive = false;
    round3RunFinalStatus = { timed_out: false, infra_error: true, exit_code: null };
    renderRound3Terminal();
  }
}

function round3TerminalInputKeydown(event) {
  if (event.key === "Enter") round3TerminalSendInput();
}

// Echoes the candidate's own typed line into the log immediately (a
// real input() call never echoes what was typed back to stdout itself -
// this is purely a client-side "here's what you just sent" record,
// placed at exactly the right point in the transcript since it's
// appended before the next poll's output arrives) and sends it to the
// live process for real.
async function round3TerminalSendInput() {
  if (!round3RunActive) return;
  const inputEl = document.getElementById("round3-terminal-input");
  const line = inputEl.value;
  inputEl.value = "";
  round3RunLog.push({ type: "stdin", text: line + "\n" });
  renderRound3Terminal();
  try {
    await api("/candidate/round/3/run/input", { method: "POST", body: JSON.stringify({ line }) });
  } catch (e) {
    // A stale/already-exited session - the next poll (or its absence)
    // already reflects that; nothing further to do here.
  }
}

function round3TerminalStatusText() {
  if (round3RunActive) return "- running...";
  if (!round3RunFinalStatus) return "";
  if (round3RunFinalStatus.timed_out) return "- timed out";
  if (round3RunFinalStatus.infra_error) return "- execution service error, try again";
  return `- exited with code ${round3RunFinalStatus.exit_code}`;
}

function round3TerminalLogHtml() {
  if (round3RunLog.length === 0) return `<p class="muted">Click Run to execute your code.</p>`;
  return `<pre class="round3-terminal-pre">${round3RunLog.map((seg) => {
    const text = escapeHtml(seg.text);
    if (seg.type === "stdin") return `<span class="round3-terminal-stdin">${text}</span>`;
    if (seg.type === "stderr") return `<span class="round3-terminal-stderr">${text}</span>`;
    return text;
  }).join("")}</pre>`;
}

// Targeted update of just the terminal region - called on every poll
// tick (every ROUND3_RUN_POLL_MS) as well as after a full layout
// re-render, so it deliberately never touches the rest of the page
// (the conversation pane, composer draft, etc.) the way re-rendering
// the whole layout on each poll would.
function renderRound3Terminal() {
  const outputBox = document.getElementById("round3-terminal-output");
  if (!outputBox) return; // not on the Round 3 view right now
  outputBox.innerHTML = round3TerminalLogHtml();
  outputBox.scrollTop = outputBox.scrollHeight;
  const statusEl = document.getElementById("round3-terminal-status");
  if (statusEl) statusEl.textContent = round3TerminalStatusText();
  const inputRow = document.getElementById("round3-terminal-input-row");
  if (inputRow) inputRow.classList.toggle("hidden", !round3RunActive);
  if (round3RunActive) {
    const inputEl = document.getElementById("round3-terminal-input");
    if (inputEl && document.activeElement !== inputEl) inputEl.focus();
  }
}

async function round3CodingSubmit() {
  const statusEl = document.getElementById("round3-coding-status");
  statusEl.className = "muted";
  statusEl.textContent = "Submitting...";
  try {
    await api("/candidate/round/3/submit", { method: "POST" });
    stopTimer();
    disarmTabGuard();
    refreshCandidateNav();
  } catch (e) {
    statusEl.className = "error-text";
    statusEl.textContent = e.message;
  }
}

// ---- Round 3: conversational, open-ended test automation ----
//
// State lives server-side (Round4StateOut, see candidate.py) and gets
// re-fetched into round4State after every turn/test-case creation, then
// the whole panel re-renders from it - simpler than trying to patch the
// DOM incrementally for something this stateful. The one thing that
// does NOT live in round4State is each test case's in-progress draft
// message (round4DraftBuffer) - that's candidate-typed and would be
// lost on every re-render (and on switching tabs) otherwise; see
// round4OnComposerInput below for the autosave mechanism.

const STEP_MARKS = { pass: "✓", fail: "✗", partial: "~" };

// Shared by the candidate's live transcript and HR's read-only report -
// plain-English action list, each with its own pass/fail marker, so a
// failure is locatable to a specific step rather than just an overall
// verdict. Deliberately never renders code - see Round4ExecutionStep.
function renderExecutionSteps(steps) {
  if (!steps || steps.length === 0) return "";
  return `
    <ol class="exec-steps">
      ${steps.map((step) => `
        <li class="exec-step exec-step-${step.status}">
          <span class="exec-step-mark">${STEP_MARKS[step.status] || "?"}</span>
          ${escapeHtml(step.description)}
          ${step.detail ? `
            <details class="exec-step-detail">
              <summary>Show detail</summary>
              ${escapeHtml(step.detail)}
            </details>
          ` : ""}
        </li>
      `).join("")}
    </ol>
  `;
}

// Trial feature (see candidate.py's GET /round/3/turn/{id}/code) - shown
// automatically for whichever ONE language the candidate currently has
// selected (round4DefaultLanguage, remembered across turns so it isn't
// re-picked every time) - not all four languages per turn, which would
// multiply the LLM calls behind every single message. Switching to a
// different language for one turn is still just a click away via that
// turn's own dropdown, generated on demand at that point.
//
// Cached client-side per (turn, language) as a fast path (skips even the
// network round-trip within this page load); the server persists the
// same thing per turn (see candidate.py), so a reload or a language
// switch back to one already viewed never re-calls the LLM either way -
// the exact same code shows every time, not a different roll.
let round4CodeCache = {};
let round4DefaultLanguage = "python";
const ROUND4_CODE_LANGUAGE_OPTIONS = [
  ["python", "Python"],
  ["java", "Java"],
  ["javascript", "JavaScript"],
];

async function round4OnLanguageChange(turnId) {
  round4DefaultLanguage = document.getElementById(`code-lang-${turnId}`).value;
  await showRound4Code(turnId);
}

async function showRound4Code(turnId) {
  const lang = document.getElementById(`code-lang-${turnId}`).value;
  const container = document.getElementById(`code-snippet-${turnId}`);
  const cacheKey = `${turnId}:${lang}`;
  if (round4CodeCache[cacheKey]) {
    container.innerHTML = `<pre class="code-snippet">${escapeHtml(round4CodeCache[cacheKey])}</pre>`;
    return;
  }
  container.innerHTML = `<p class="muted">Generating...</p>`;
  try {
    const result = await api(`/candidate/round/4/turn/${turnId}/code?language=${lang}`);
    round4CodeCache[cacheKey] = result.code;
    container.innerHTML = `<pre class="code-snippet">${escapeHtml(result.code)}</pre>`;
  } catch (e) {
    container.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
  }
}

// A "label" element immediately followed by a "text" element is a
// detail-view field (Title / The Great Gatsby), not two independent
// headings - see renderMockupScreens below for why that distinction
// matters. Any other element (including a label with no following
// text, or two labels/texts that aren't adjacent) passes through
// unchanged.
function groupMockupElements(elements) {
  const grouped = [];
  for (let i = 0; i < elements.length; i++) {
    const el = elements[i];
    const next = elements[i + 1];
    if (el.type === "label" && next && next.type === "text") {
      grouped.push({ kind: "field", label: el.text, value: next.text });
      i++; // consumed both
    } else {
      grouped.push(el);
    }
  }
  return grouped;
}

// Shared by the candidate's round 3 view and HR's scenario detail -
// renders Round4UiMockupOut's structured screens (screen -> ordered
// typed elements) as a static, schematic wireframe. Deliberately never
// injects LLM-authored HTML/CSS: every element renders through this
// app's own trusted CSS classes, keyed only off `type`, with all text
// escaped - see models.Scenario.ui_mockup_json for why.
function renderMockupScreens(mockup, idPrefix) {
  if (!mockup || !mockup.screens || mockup.screens.length === 0) return "";
  const screens = mockup.screens;
  const tabs = screens.map((screen, i) => `
    <button class="nav-btn ${i === 0 ? "active" : ""}" onclick="selectMockupScreen('${idPrefix}', ${i})">
      <span class="nav-chip">${i + 1}</span>
      <span class="nav-btn-copy"><span class="nav-btn-label">${escapeHtml(screen.name)}</span></span>
    </button>
  `).join("");

  // Window "chrome" (dots + a fake address-bar pill showing the screen
  // name) is purely decorative - it's what turns "a dashed box of
  // stacked labels" into something that actually reads as a browser
  // window a tester would have open, without pretending to be a real
  // screenshot. Elements below flow in a wrapping row (see .mockup-
  // screen-body) instead of one per line, so short controls like a
  // button next to a link sit side by side - a genuinely standalone
  // label/text (a section heading, a status message) still forces its
  // own line so it doesn't blend into the controls next to it, input
  // fields lay their caption beside the box instead of above it. A
  // "label" element immediately followed by a "text" element (e.g.
  // {label:"Title"}, {text:"The Great Gatsby"}) is a detail-view field,
  // not two separate headings - a details/summary screen is routinely
  // ALL such pairs back to back (see groupMockupElements below), and
  // rendering each half as its own identically-styled full-width line
  // read as a wall of disconnected, misaligned text. Merged into one
  // compact "label: value" row instead.
  const panels = screens.map((screen, i) => `
    <div class="mockup-screen ${i === 0 ? "" : "hidden"}" id="${idPrefix}-screen-${i}">
      <div class="mockup-screen-chrome">
        <span class="mockup-screen-dot"></span>
        <span class="mockup-screen-dot"></span>
        <span class="mockup-screen-dot"></span>
        <span class="mockup-screen-url">${escapeHtml(screen.name)}</span>
      </div>
      <div class="mockup-screen-body">
        ${groupMockupElements(screen.elements).map((el) => el.kind === "field" ? `
          <div class="mockup-el mockup-el-field">
            <span class="mockup-el-field-label">${escapeHtml(el.label)}</span>
            <span class="mockup-el-field-value">${escapeHtml(el.value)}</span>
          </div>
        ` : `
          <div class="mockup-el mockup-el-${el.type}">
            ${el.type === "input" ? `<span class="mockup-el-caption">${escapeHtml(el.text)}</span><span class="mockup-el-box"></span>` : escapeHtml(el.text)}
          </div>
        `).join("")}
      </div>
    </div>
  `).join("");

  return `
    <div class="row" id="${idPrefix}-screens" style="margin-bottom:0.5rem">${tabs}</div>
    ${panels}
    <p class="muted">Static reference only - nothing here is clickable or live.</p>
  `;
}

function selectMockupScreen(idPrefix, index) {
  document.querySelectorAll(`#${idPrefix}-screens .nav-btn`).forEach((btn, i) => btn.classList.toggle("active", i === index));
  document.querySelectorAll(`[id^="${idPrefix}-screen-"]`).forEach((panel, i) => panel.classList.toggle("hidden", i !== index));
}

async function renderRound4View(box) {
  box.innerHTML = loadingHtml();
  try {
    round4State = await api("/candidate/round/4/state");
  } catch (e) {
    box.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
    return;
  }
  // Seed the draft buffer from whatever was last autosaved server-side -
  // recovers in-progress text across a full page refresh, not just a tab
  // switch within the same page load.
  round4DraftBuffer = {};
  for (const tc of round4State.test_cases) round4DraftBuffer[tc.id] = tc.draft_prompt || "";
  round4ViewedTestCaseId = round4State.test_cases.length > 0 ? round4State.test_cases[0].id : null;
  renderRound4Layout(box);

  if (!timerHandle) {
    const submission = round4State.submission;
    const deadline = new Date(submission.started_at + "Z").getTime() + round4State.scenario.time_limit_minutes * 60 * 1000;
    startTimer(deadline, round4AutoSubmit, 4);
  }
}

async function round4AutoSubmit() {
  const timerEl = document.getElementById("timer");
  if (timerEl) timerEl.textContent = "Time's up - submitting automatically...";
  try {
    await api("/candidate/round/4/submit", { method: "POST" });
    round4DraftBuffer = {};
    stopTimer();
    disarmTabGuard();
    refreshCandidateNav();
  } catch (e) {
    // Most likely cause: time ran out before the candidate ever sent a
    // single message, so there's nothing to submit (see
    // _require_within_time_limit's 400) - forceExpireRound closes the
    // round regardless, so it's safe to call refreshCandidateNav()
    // afterward now: the submission is no longer in_progress by the time
    // that reload happens, so it won't re-trigger this same function -
    // this used to be exactly that infinite "flickering" loop (repeat
    // 400s until the candidate's tab was closed) before expire existed;
    // the fix is closing the round for real, not just avoiding the reload.
    await forceExpireRound(4);
  }
}

// Shared by all three rounds' auto-submit-on-expiry paths (see
// doSubmitRound1/doSubmitRound2Investigation's force=true, and
// round4AutoSubmit's catch above) - the guaranteed way a round closes
// once its timer hits zero and a real submit wasn't possible (empty/
// incomplete content, or losing a race against the server's own deadline
// check). Whatever draft content the candidate had (round is passed in
// via `body`, may be empty) is saved as-is and the round is finalized as
// a real submission - see candidate.py's POST /round/{n}/expire. A
// candidate who ran out of time having written nothing still has to move
// on to the next round, not get stuck here.
async function forceExpireRound(roundNumber, body = {}) {
  try {
    await api(`/candidate/round/${roundNumber}/expire`, { method: "POST", body: JSON.stringify(body) });
  } catch (e) {
    // Nothing more to do client-side if even this fails (e.g. a network
    // blip) - the candidate stays on this screen, but at least the timer
    // display already says time's up rather than claiming to still be
    // "submitting automatically."
  }
  stopTimer();
  disarmTabGuard();
  refreshCandidateNav();
}

function renderRound4Layout(box) {
  const s = round4State;
  const envFields = s.environment ? Object.entries(s.environment.fields || {}) : [];

  const r1Rows = s.round1_context.submitted_rows || [];

  // Problem + environment context comes first, same as every other
  // round - a candidate reads what they're automating before being
  // asked to act. What actually made the composer hard to find wasn't
  // its position, it was that a growing transcript (each turn's own
  // .round4-pane-body can run to 34rem) had no scroll boundary, so the
  // composer kept receding further down the page the more a candidate
  // used it. See renderRound4TestCaseBody: the transcript now scrolls
  // in its own bounded region and the composer sits in a fixed,
  // visually distinct docked card right below it - never further away
  // than one screen's worth of scrolling, matching how a real chat
  // composer stays reachable (docked, not buried) regardless of
  // conversation length.
  box.innerHTML = `
    <h3>Round 4: ${escapeHtml(s.scenario.title)}</h3>
    ${formatScenarioDescription(s.scenario.description)}
    <details class="hint-box">
      <summary><strong>Automating your own Round 1 answer</strong> - "${escapeHtml(s.round1_context.scenario_title)}"</summary>
      <p>${escapeHtml(s.round1_context.scenario_description)}</p>
      <div class="table-scroll">
        <table>
          <thead><tr><th>SI.No</th><th>Title</th><th>Preconditions</th><th>Steps</th><th>Expected result</th></tr></thead>
          <tbody>
            ${r1Rows.length > 0 ? r1Rows.map((r, i) => `
              <tr>
                <td>${i + 1}</td>
                <td>${escapeHtml(r.title || "")}</td>
                <td>${escapeHtml(r.preconditions || "")}</td>
                <td>${escapeHtml(r.steps || "")}</td>
                <td>${escapeHtml(r.expected_result || "")}</td>
              </tr>
            `).join("") : `<tr><td colspan="5" class="muted">No rows found.</td></tr>`}
          </tbody>
        </table>
      </div>
    </details>
    ${envFields.length > 0 ? `
      <details class="hint-box env-panel">
        <summary><strong>Test environment</strong></summary>
        <dl class="env-fields">
          ${envFields.map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join("")}
        </dl>
        ${s.environment.notes ? `<p>${escapeHtml(s.environment.notes)}</p>` : ""}
      </details>
    ` : ""}
    ${s.ui_mockup ? `
      <details class="hint-box mockup-details">
        <summary><strong>Reference: App screens</strong></summary>
        ${renderMockupScreens(s.ui_mockup, "cand-mockup")}
      </details>
    ` : ""}
    <div id="round4-tabs" class="row" style="margin-bottom:0.4rem; margin-top:1rem"></div>
    <p class="muted" style="margin-bottom:0.85rem">Create as many test cases as you think this deserves - most candidates write 3-6, covering more than one angle (happy path, a negative/edge case, cross-checking what different layers report).</p>
    ${s.turns.length >= 20 ? `<p class="muted" style="color: var(--warn); margin-bottom:0.85rem">You've sent ${s.turns.length} messages in this round so far - there's no limit, but a good answer here is about judgment and coverage, not volume. Worth checking whether you're still adding new ground.</p>` : ""}
    <div class="panel-inset example-row" style="margin-bottom:0.85rem">
      <p class="muted" style="margin-bottom:0.3rem"><strong>Example (format only, not a hint for this scenario):</strong></p>
      <p class="muted" style="margin:0">Test case title: "Verify login with valid credentials" - then a first message to the assistant like "Log in with the test account credentials and tell me what happened."</p>
    </div>
    <div id="round4-test-case-body"></div>
    <div class="row" style="margin-top:1.25rem">
      <button id="round4-submit-btn" class="btn-block" onclick="round4Submit()" ${s.test_cases.length > 0 ? "" : "disabled"}>Submit Round 4</button>
    </div>
    <p id="round4-status" class="muted"></p>
  `;

  renderRound4Tabs();
  renderRound4TestCaseBody();
}

function renderRound4Tabs() {
  const s = round4State;
  const tabs = s.test_cases.map((tc, i) => {
    const count = tc.turn_count;
    return `
      <button class="nav-btn ${tc.id === round4ViewedTestCaseId ? "active" : ""}" onclick="round4SelectTestCase(${tc.id})">
        <span class="nav-chip">${i + 1}</span>
        <span class="nav-btn-copy">
          <span class="nav-btn-label">${escapeHtml(tc.title || `Test case ${i + 1}`)}</span>
          <span class="nav-btn-note">${count} turn${count === 1 ? "" : "s"}</span>
        </span>
      </button>
    `;
  }).join("");

  document.getElementById("round4-tabs").innerHTML = `
    ${tabs}
    <button onclick="round4CreateTestCase()">+ New test case</button>
  `;
}

async function round4CreateTestCase() {
  // No prompt() dialog - creates immediately with no title; the tab
  // falls back to "Test case N" for display (see Round4TestCaseOut) and
  // the candidate's own prompts are what actually convey intent.
  try {
    const tc = await api("/candidate/round/4/test-case", { method: "POST", body: JSON.stringify({ title: null }) });
    round4State = await api("/candidate/round/4/state");
    round4DraftBuffer[tc.id] = "";
    round4ViewedTestCaseId = tc.id;
    renderRound4Tabs();
    renderRound4TestCaseBody();
    round4ScrollToComposer();
    // renderRound4Tabs/TestCaseBody only patch their own sub-regions -
    // the Submit button lives in the outer layout template and won't
    // see this new test case unless told directly.
    const submitBtn = document.getElementById("round4-submit-btn");
    if (submitBtn) submitBtn.disabled = round4State.test_cases.length === 0;
  } catch (e) {
    document.getElementById("round4-status").textContent = e.message;
  }
}

// The composer sits below however much transcript + reference material
// is on the page - without this, creating or switching to a test case
// silently leaves the candidate staring at whatever they were already
// scrolled to, with no indication the actual input box even moved.
function round4ScrollToComposer() {
  const el = document.getElementById("round4-message");
  if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
}

function round4SelectTestCase(id) {
  round4FlushDraft(round4ViewedTestCaseId); // send whatever's pending for the tab being left, don't wait out the debounce
  round4ViewedTestCaseId = id;
  renderRound4Tabs();
  renderRound4TestCaseBody();
  round4ScrollToComposer();
}

function renderRound4TestCaseBody() {
  const s = round4State;
  const body = document.getElementById("round4-test-case-body");
  const tcId = round4ViewedTestCaseId;
  if (tcId == null) {
    body.innerHTML = `<div class="empty-state">No test cases yet - click "+ New test case" above to describe the first thing you want to automate.</div>`;
    return;
  }

  const turns = s.turns.filter((t) => t.test_case_id === tcId);

  const languageOptionsHtml = (turnId) => ROUND4_CODE_LANGUAGE_OPTIONS.map(([value, label]) =>
    `<option value="${value}" ${round4DefaultLanguage === value ? "selected" : ""}>${label}</option>`
  ).join("");

  // Three panes per turn, side by side (prompt | result | code) instead
  // of stacked in one column - code used to be a click away behind a
  // "View as code" button at the bottom; it's the same information a
  // candidate needs to correct a wrong response, so it's shown right
  // alongside the prompt/result that produced it, not buried below.
  // Column WIDTHS are draggable via the two .round4-col-resizer bars
  // (see round4StartColResize) - a real split-pane divider, not just
  // native per-pane resize, since "make the code pane bigger" means
  // taking width away from its neighbors, which CSS `resize` alone can't
  // do. The widths are shared across every turn row (applied as CSS
  // custom properties on the document root - see round4ApplyColWidths),
  // so dragging once resizes all of them together, not just this row.
  const transcriptHtml = turns.length === 0
    ? `<div class="empty-state">No messages yet in this test case - describe what you want automated to get started.</div>`
    : turns.map((t) => `
      <div class="round4-turn-grid">
        <div class="panel-inset round4-pane">
          <p class="muted round4-pane-label">Prompt</p>
          <div class="round4-pane-body">
            <p>${escapeHtml(t.candidate_prompt)}</p>
          </div>
        </div>
        <div class="round4-col-resizer" onmousedown="round4StartColResize(event, 'c1-c2')"></div>
        <div class="panel-inset round4-pane">
          <p class="muted round4-pane-label">Result</p>
          <div class="round4-pane-body">
            <p>${escapeHtml(t.model_response.response_text)}</p>
            ${renderExecutionSteps(t.model_response.steps)}
            <div class="observed-box">
              <span class="badge badge-${t.model_response.status}">${t.model_response.status}</span>
              ${escapeHtml(t.model_response.observed_result)}
            </div>
          </div>
        </div>
        <div class="round4-col-resizer" onmousedown="round4StartColResize(event, 'c2-c3')"></div>
        <div class="panel-inset round4-pane">
          <div class="row" style="align-items:center; justify-content:space-between; margin-bottom:0">
            <p class="muted round4-pane-label" style="margin:0">Code</p>
            <select id="code-lang-${t.id}" onchange="round4OnLanguageChange(${t.id})">${languageOptionsHtml(t.id)}</select>
          </div>
          <div class="round4-pane-body">
            <div id="code-snippet-${t.id}"><p class="muted">Generating...</p></div>
          </div>
        </div>
      </div>
    `).join("");

  const draftText = round4DraftBuffer[tcId] || "";
  // A visually distinct, always-in-the-same-place card - "docked, not
  // floating" (the standard chat-composer pattern: ChatGPT, Slack,
  // Intercom all keep the input in a fixed, bordered container instead
  // of letting it blend into the page). panel-inset + a border gives it
  // the same visual weight as the transcript panes above it, so it
  // reads as "the place you type" rather than one more paragraph of
  // muted text to scan past.
  const composerHtml = `
    <div class="panel-inset round4-composer">
      <p class="muted round4-pane-label">Your message</p>
      <textarea id="round4-message" placeholder="What do you want the assistant to do or check next?" oninput="round4OnComposerInput(${tcId}, this.value)">${escapeHtml(draftText)}</textarea>
      <div class="row">
        <button id="round4-send-btn" onclick="round4SendMessage()">Send</button>
      </div>
    </div>
  `;

  // Bounded scroll region for the transcript only (not the whole page) -
  // a long conversation grows inside this box, never past it, so the
  // composer right below it is never more than one screen away no
  // matter how many turns pile up. Only bounded once there's something
  // to bound - the "no messages yet" empty state stays unscrolled.
  body.innerHTML = `
    <div class="${turns.length > 0 ? "round4-transcript-scroll" : ""}">${transcriptHtml}</div>
    ${composerHtml}
  `;

  // Auto-generate/show code for the candidate's current default language -
  // shown by default now, not gated behind a click. Cheap for anything
  // already viewed (client cache, then the server's own persisted copy -
  // see showRound4Code), so re-rendering this same list on every new
  // message doesn't re-cost a call for turns already shown.
  turns.forEach((t) => showRound4Code(t.id));
  round4ApplyColWidths();
}

// Draggable column-width splitter for the three round4 panes (prompt |
// result | code) - see the .round4-turn-grid markup above. Widths live
// in fr units, same as the CSS grid-template-columns they drive, and
// are applied as custom properties on the document root rather than on
// #round4-test-case-body itself: that element's innerHTML gets replaced
// wholesale on every re-render (a new message, switching test cases),
// which would silently wipe an inline style set directly on it. The
// root element is never destroyed that way, so a resize made once
// keeps applying across every future re-render in this session.
let round4ColFr = { c1: 0.65, c2: 1.5, c3: 1.5 };
let round4ColResizeState = null;

function round4ApplyColWidths() {
  const root = document.documentElement.style;
  root.setProperty("--r4c1", `${round4ColFr.c1}fr`);
  root.setProperty("--r4c2", `${round4ColFr.c2}fr`);
  root.setProperty("--r4c3", `${round4ColFr.c3}fr`);
}

function round4StartColResize(e, edge) {
  // Below the breakpoint where the grid collapses to a single stacked
  // column (see the media query in style.css), there's nothing
  // meaningful to drag - matches disabling the height-resize there too.
  if (window.innerWidth <= 860) return;
  e.preventDefault();
  const grid = e.currentTarget.closest(".round4-turn-grid");
  const rect = grid.getBoundingClientRect();
  const leftKey = edge === "c1-c2" ? "c1" : "c2";
  const rightKey = edge === "c1-c2" ? "c2" : "c3";
  const totalFr = round4ColFr.c1 + round4ColFr.c2 + round4ColFr.c3;
  // Two 6px resizer tracks eat into the grid's width but carry no fr
  // share of their own - excluded here so the fr<->pixel conversion
  // below lines up with what the fr units actually control.
  const pxPerFr = (rect.width - 12) / totalFr;
  round4ColResizeState = { leftKey, rightKey, startX: e.clientX, startLeftFr: round4ColFr[leftKey], startRightFr: round4ColFr[rightKey], pxPerFr };
  document.body.style.cursor = "col-resize";
  document.body.style.userSelect = "none";
  document.addEventListener("mousemove", round4OnColResizeMove);
  document.addEventListener("mouseup", round4StopColResize);
}

function round4OnColResizeMove(e) {
  if (!round4ColResizeState) return;
  const { leftKey, rightKey, startX, startLeftFr, startRightFr, pxPerFr } = round4ColResizeState;
  const deltaFr = (e.clientX - startX) / pxPerFr;
  // Each pane keeps a floor of 0.35fr - narrow enough to clearly favor
  // whichever pane the candidate is expanding, wide enough that the
  // shrunk one doesn't disappear or make its content unreadable.
  const MIN_FR = 0.35;
  let newLeft = startLeftFr + deltaFr;
  let newRight = startRightFr - deltaFr;
  if (newLeft < MIN_FR) { newRight -= (MIN_FR - newLeft); newLeft = MIN_FR; }
  if (newRight < MIN_FR) { newLeft -= (MIN_FR - newRight); newRight = MIN_FR; }
  round4ColFr[leftKey] = Math.max(MIN_FR, newLeft);
  round4ColFr[rightKey] = Math.max(MIN_FR, newRight);
  round4ApplyColWidths();
}

function round4StopColResize() {
  round4ColResizeState = null;
  document.body.style.cursor = "";
  document.body.style.userSelect = "";
  document.removeEventListener("mousemove", round4OnColResizeMove);
  document.removeEventListener("mouseup", round4StopColResize);
}

// Two-layer autosave: the in-memory buffer update is instant (this is
// what tab-switching restores from - zero network latency), the PATCH to
// the server is debounced so normal typing doesn't fire a request per
// keystroke. round4SelectTestCase flushes early on tab-switch so a fast
// switch-and-close doesn't lose up to DEBOUNCE_MS of typing to a
// cancelled timeout.
const ROUND4_DRAFT_DEBOUNCE_MS = 2000;

function round4OnComposerInput(tcId, value) {
  round4DraftBuffer[tcId] = value;
  clearTimeout(round4DraftTimers[tcId]);
  round4DraftTimers[tcId] = setTimeout(() => round4FlushDraft(tcId), ROUND4_DRAFT_DEBOUNCE_MS);
}

function round4FlushDraft(tcId) {
  if (tcId == null || round4DraftTimers[tcId] == null) return;
  clearTimeout(round4DraftTimers[tcId]);
  delete round4DraftTimers[tcId];
  api(`/candidate/round/4/test-case/${tcId}/draft`, {
    method: "PATCH",
    body: JSON.stringify({ draft_prompt: round4DraftBuffer[tcId] || "" }),
  }).catch(() => {}); // best-effort - the in-memory buffer is still correct either way
}

async function round4SendMessage() {
  const tcId = round4ViewedTestCaseId;
  const prompt = (round4DraftBuffer[tcId] || "").trim();
  const statusEl = document.getElementById("round4-status");
  const sendBtn = document.getElementById("round4-send-btn");
  // Guards against a double-click firing two concurrent /turn requests -
  // without this, both could read the same "existing turns" count before
  // either commits and independently compute the same turn_number,
  // landing two turns with an identical number on the same test case
  // (plus a wasted second LLM call). renderRound4TestCaseBody() below
  // rebuilds this button fresh (enabled) on success; the catch path
  // re-enables it explicitly since no re-render happens there.
  if (sendBtn && sendBtn.disabled) return;
  if (!prompt) {
    statusEl.textContent = "Type a message first.";
    return;
  }
  statusEl.textContent = "Thinking...";
  if (sendBtn) sendBtn.disabled = true;
  try {
    await api("/candidate/round/4/turn", {
      method: "POST",
      body: JSON.stringify({ test_case_id: tcId, candidate_prompt: prompt }),
    });
    clearTimeout(round4DraftTimers[tcId]);
    delete round4DraftTimers[tcId];
    round4DraftBuffer[tcId] = ""; // the server already cleared its copy as part of turn creation
    round4State = await api("/candidate/round/4/state");
    statusEl.textContent = "";
    renderRound4Tabs();
    renderRound4TestCaseBody();
  } catch (e) {
    if (sendBtn) sendBtn.disabled = false;
    statusEl.textContent = e.message;
  }
}

async function round4Submit() {
  const statusEl = document.getElementById("round4-status");
  try {
    await api("/candidate/round/4/submit", { method: "POST" });
    stopTimer();
    disarmTabGuard();
    round4DraftBuffer = {};
    refreshCandidateNav();
  } catch (e) {
    // e.g. "Create at least one test case before submitting." - the
    // round is still in progress, so the timer/guard must stay engaged.
    statusEl.textContent = e.message;
  }
}

// A generic, non-editable format example shown right above the
// candidate's own rows - deliberately a universal "login" example rather
// than anything drawn from the actual scenario, so it illustrates the
// expected shape of an answer without hinting at what to test in THIS
// scenario. Lives in its own <tbody>, outside #tc-rows, so collectRows()
// (which only queries within #tc-rows) never picks it up - nothing extra
// needed to keep it out of what gets submitted.
function exampleTestCaseRowHtml() {
  return `
    <tr class="example-row">
      <td class="tc-no">Ex</td>
      <td>Verify login with valid credentials</td>
      <td>User has a registered account</td>
      <td>1. Open the login page. 2. Enter a valid username and password. 3. Click "Login".</td>
      <td>User is redirected to the home/dashboard screen and a welcome message is shown.</td>
      <td></td>
    </tr>
  `;
}

function addRow(initial = null) {
  const id = rowCount++;
  const tbody = document.getElementById("tc-rows");
  const tr = document.createElement("tr");
  tr.id = `row-${id}`;
  tr.innerHTML = `
    <td class="tc-no"></td>
    <td><input class="tc-title" oninput="scheduleRoundDraftSave(1, round1DraftPayload); round1UpdateSubmitState()" /></td>
    <td><input class="tc-pre" oninput="scheduleRoundDraftSave(1, round1DraftPayload)" /></td>
    <td><textarea class="tc-steps" oninput="scheduleRoundDraftSave(1, round1DraftPayload); round1UpdateSubmitState()"></textarea></td>
    <td><textarea class="tc-expected" oninput="scheduleRoundDraftSave(1, round1DraftPayload); round1UpdateSubmitState()"></textarea></td>
    <td><button onclick="removeRow('row-${id}')">Remove</button></td>
  `;
  tbody.appendChild(tr);
  if (initial) {
    tr.querySelector(".tc-title").value = initial.title || "";
    tr.querySelector(".tc-pre").value = initial.preconditions || "";
    tr.querySelector(".tc-steps").value = initial.steps || "";
    tr.querySelector(".tc-expected").value = initial.expected_result || "";
  }
  renumberRows();
}

function removeRow(rowId) {
  document.getElementById(rowId).remove();
  renumberRows();
  scheduleRoundDraftSave(1, round1DraftPayload); // removing a row must survive a refresh too, not just additions
  round1UpdateSubmitState();
}

// Mirrors doSubmitRound1's own completeness check (title + steps +
// expected_result on every row collectRows() returns) so the button's
// disabled state is never a lie the candidate discovers only after
// clicking - see the identical reasoning on round1UpdateSubmitState's
// counterparts for rounds 2-4.
function round1UpdateSubmitState() {
  const submitBtn = document.getElementById("round1-submit-btn");
  if (!submitBtn) return;
  const content = collectRows();
  const ready = content.length > 0 && content.every((r) => r.title.trim() && r.steps.trim() && r.expected_result.trim());
  submitBtn.disabled = !ready;
}

function renumberRows() {
  document.querySelectorAll("#tc-rows .tc-no").forEach((cell, i) => {
    cell.textContent = i + 1;
  });
}

function collectRows() {
  return [...document.querySelectorAll("#tc-rows tr")]
    .map((tr) => ({
      title: tr.querySelector(".tc-title").value,
      preconditions: tr.querySelector(".tc-pre").value,
      steps: tr.querySelector(".tc-steps").value,
      expected_result: tr.querySelector(".tc-expected").value,
    }))
    .filter((r) => r.title.trim() || r.steps.trim());
}

async function doSubmitRound1(force = false) {
  const statusEl = document.getElementById("submit-status");
  const submitBtn = document.getElementById("round1-submit-btn");
  // Guards against a double-click firing two concurrent submits (the
  // second would just 400 on the server, but this avoids the confusing
  // in-between state and a wasted round trip) - see the identical guard
  // on round4SendMessage for the more consequential version of this bug
  // (there it could create two conversation turns with the same number).
  if (submitBtn && submitBtn.disabled) return;
  const content = collectRows();
  // force (the timer just hit zero) skips these checks entirely - they
  // exist to help a candidate who still has time avoid wasting their one
  // submit, not to block the round from ever closing once time is up.
  // They used to run unconditionally, which silently blocked the
  // auto-submit path too: an empty or incomplete row at zero meant
  // nothing was ever sent, and every later attempt hit the same wall -
  // the round just stayed on screen forever with an expired timer.
  if (!force) {
    if (content.length === 0) {
      statusEl.textContent = "Add at least one row before submitting.";
      return;
    }
    // collectRows() only drops rows with NEITHER title nor steps filled
    // in (an unused blank row) - a row with just one of the required
    // fields typed in would otherwise reach the server, which requires
    // title, steps, AND expected result (see schemas.TestCaseRow) and
    // rejects it. Catch that here with a specific message instead of a
    // round trip.
    const incompleteIndex = content.findIndex((r) => !r.title.trim() || !r.steps.trim() || !r.expected_result.trim());
    if (incompleteIndex !== -1) {
      const r = content[incompleteIndex];
      const missing = [
        !r.title.trim() && "Title",
        !r.steps.trim() && "Steps",
        !r.expected_result.trim() && "Expected result",
      ].filter(Boolean);
      statusEl.textContent = `Row ${incompleteIndex + 1} is missing: ${missing.join(", ")}.`;
      return;
    }
  }
  if (submitBtn) submitBtn.disabled = true;
  try {
    await api("/candidate/round/1/submit", { method: "POST", body: JSON.stringify({ content }) });
    stopTimer();
    disarmTabGuard();
    // No results screen - scoring happens in the background on HR's
    // side; the candidate just moves on to whatever's unlocked next.
    refreshCandidateNav();
  } catch (e) {
    if (force) {
      // Nothing submittable even now (empty, or a row missing a required
      // field), or the server's own deadline check beat this attempt -
      // the round still has to end, saving whatever's here. See
      // forceExpireRound.
      await forceExpireRound(1, { content });
      return;
    }
    if (submitBtn) submitBtn.disabled = false;
    statusEl.textContent = e.message;
  }
}

function startTimer(deadlineMs, onExpire, roundNumber) {
  const timerEl = document.getElementById("timer");
  // Lives in the sticky topbar, not inline in the round's own content -
  // Round 3's reference panels alone run 900px+, so a timer buried in
  // that flow could go unseen for a while on a scroll. Hidden by default
  // (see index.html); shown only while a round is actually running.
  timerEl.classList.remove("hidden");
  armTabGuard(roundNumber);
  function tick() {
    const remaining = deadlineMs - Date.now();
    if (remaining <= 0) {
      stopTimer();
      disarmTabGuard(); // time's up is a hard boundary regardless of whether auto-submit below succeeds
      onExpire();
      return;
    }
    const mins = Math.floor(remaining / 60000);
    const secs = Math.floor((remaining % 60000) / 1000);
    timerEl.textContent = `Time remaining: ${mins}:${String(secs).padStart(2, "0")}`;
    timerEl.classList.toggle("timer-critical", remaining <= 5 * 60 * 1000);
  }
  tick();
  timerHandle = setInterval(tick, 1000);
}

// Deliberately does NOT touch the tab-switch guard - see disarmTabGuard,
// called separately (and only) at points where a round genuinely ends:
// a successful submit, the timer naturally expiring, navigating to a
// different round, or logging out. A validation failure inside a submit
// attempt (e.g. "add a row first") means the round is still very much
// in progress, so the guard/fullscreen must stay engaged through that -
// this used to also exit fullscreen on every submit *attempt*,
// regardless of whether it actually succeeded, which is why "Submit"
// with an empty form looked like it silently disabled the guard.
function stopTimer() {
  if (timerHandle) {
    clearInterval(timerHandle);
    timerHandle = null;
  }
  // Deliberately does NOT hide/clear the topbar timer element here - a
  // couple of call sites (the onExpire callbacks in renderEntryForm/
  // renderInvestigationForm/round4AutoSubmit) call this and THEN set the
  // timer's text to "Time's up - submitting automatically..." while the
  // auto-submit request is in flight; hiding it here would make that
  // message invisible. See resetTopbarTimer, called instead at the
  // points where a round view actually finishes transitioning away
  // (loadRound, the "all rounds complete" branch, logout).
}

function resetTopbarTimer() {
  const timerEl = document.getElementById("timer");
  timerEl.classList.add("hidden");
  timerEl.classList.remove("timer-critical");
  timerEl.textContent = "";
}

// ---- Anti-cheating: fullscreen-enforced tab-switch guard (candidate
// rounds only - armed exactly while a round's timer is running, see
// startTimer/stopTimer above) ----
//
// No website can literally block Alt+Tab or a tab switch - that's a
// deliberate browser/OS boundary, not a gap in this code (see MDN's
// Fullscreen API docs). What real lockdown-style exam tools actually do
// (TestInvite, ClassMarker, and others) is require fullscreen and react
// the INSTANT it's exited - exiting fullscreen is a same-tab event that
// fires while the page is still rendering, unlike visibilitychange/blur
// which only fire once the tab is already hidden. Escape, Alt+Tab, and
// switching browser tabs all trigger a fullscreenchange in current
// browsers, so this is the closest thing to "catch it as it happens"
// that's actually possible on the web - a prior attempt using only
// visibilitychange/blur could only ever react once the candidate
// returned, which wasn't convincing as a deterrent.
let fsGuardArmed = false;  // true only while a round's timer is running
let fsGuardRound = null;   // which round number is currently being watched
let fsGuardActive = false; // true once requestFullscreen() actually succeeded for this round - if false, falls back to passive logging below

async function armTabGuard(roundNumber) {
  fsGuardArmed = true;
  fsGuardRound = roundNumber;
  fsGuardActive = false;
  if (document.fullscreenEnabled) {
    try {
      await document.documentElement.requestFullscreen();
      fsGuardActive = true;
    } catch (e) {
      // Denied, or not allowed in this context - fall back to passive
      // logging rather than leaving the candidate with no guard at all.
    }
  }
}

function disarmTabGuard() {
  fsGuardArmed = false;
  fsGuardRound = null;
  fsGuardActive = false;
  removeFsOverlay();
  const toast = document.getElementById("tab-switch-toast");
  if (toast) toast.remove();
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
}

function logTabSwitch(roundNumber) {
  if (role !== "candidate") return;
  api(`/candidate/round/${roundNumber}/tab-switch`, { method: "POST" }).catch(() => {});
}

document.addEventListener("fullscreenchange", () => {
  if (!fsGuardArmed || !fsGuardActive) return;
  if (document.fullscreenElement) {
    removeFsOverlay();
  } else {
    logTabSwitch(fsGuardRound);
    showFsOverlay(fsGuardRound);
  }
});

function showFsOverlay(roundNumber) {
  // A rapid exit/re-enter/exit-fullscreen sequence can call this again
  // while the previous overlay is still mid fade-out (see
  // closeModalOverlay's 200ms delayed remove()) - reuse it and cancel
  // that pending removal instead of silently no-op'ing, or the delayed
  // remove() still fires afterward and deletes the overlay this call
  // was supposed to guarantee is showing.
  const existing = document.getElementById("fs-guard-overlay");
  if (existing) {
    clearTimeout(existing._closeTimeout);
    existing.classList.add("modal-open");
    return;
  }
  const overlay = document.createElement("div");
  overlay.id = "fs-guard-overlay";
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-box">
      <h3>Fullscreen required</h3>
      <p>This round must be taken in fullscreen. Leaving it has been logged and is visible to HR.</p>
      <div class="row">
        <button onclick="reenterFullscreen()">Return to fullscreen</button>
        <button class="btn-ghost" onclick="continueWithoutFullscreen()">Continue without fullscreen</button>
      </div>
      <p id="fs-guard-status" class="muted"></p>
    </div>
  `;
  openModalOverlay(overlay);
}

function removeFsOverlay() {
  closeModalOverlay("fs-guard-overlay");
}

function reenterFullscreen() {
  // Browsers throttle repeated requestFullscreen() calls in a short
  // window as anti-annoyance protection - a candidate who taps Escape a
  // few times quickly can trigger that, and this call silently rejecting
  // used to leave them stuck behind the overlay with literally no way
  // forward (no Submit, no Exit, nothing). "Continue without fullscreen"
  // below is the guaranteed way out regardless of what this does.
  document.documentElement.requestFullscreen().catch(() => {
    const statusEl = document.getElementById("fs-guard-status");
    if (statusEl) statusEl.textContent = "Couldn't re-enter fullscreen - try again, or continue without it below.";
  });
}

function continueWithoutFullscreen() {
  // Drops to the same passive logging used when fullscreen was never
  // available in the first place (see the visibilitychange/blur
  // listeners below) - still logged and visible to HR, just no longer
  // blocking. The round is never held hostage by a browser quirk.
  fsGuardActive = false;
  removeFsOverlay();
}

// Fallback for browsers/contexts where fullscreen enforcement isn't
// available at all (fsGuardActive stays false) - same passive log +
// dismissible toast as before, so there's still some signal instead of
// no guard whatsoever.
let tabSwitchPending = false;

document.addEventListener("visibilitychange", () => {
  if (fsGuardArmed && !fsGuardActive && document.hidden && !tabSwitchPending) {
    tabSwitchPending = true;
    logTabSwitch(fsGuardRound);
  }
});
window.addEventListener("blur", () => {
  if (fsGuardArmed && !fsGuardActive && !tabSwitchPending) {
    tabSwitchPending = true;
    logTabSwitch(fsGuardRound);
  }
});
window.addEventListener("focus", () => {
  if (!tabSwitchPending) return;
  tabSwitchPending = false;
  showTabSwitchToast();
});

// Tracks the current toast's pending auto-dismiss timer, so a later
// toast (same element id, reused on every tab-switch) can never be cut
// short by an earlier toast's timer that outlived a manual dismiss.
let tabSwitchToastTimer = null;

function showTabSwitchToast() {
  const existing = document.getElementById("tab-switch-toast");
  if (existing) existing.remove();
  clearTimeout(tabSwitchToastTimer);
  const toast = document.createElement("div");
  toast.id = "tab-switch-toast";
  toast.className = "toast";
  toast.innerHTML = `
    <span>You switched away from this test - it's been logged and is visible to HR.</span>
    <button class="toast-dismiss" onclick="dismissTabSwitchToast()" aria-label="Dismiss">&times;</button>
  `;
  document.body.appendChild(toast);
  requestAnimationFrame(() => requestAnimationFrame(() => toast.classList.add("toast-open")));
  tabSwitchToastTimer = setTimeout(dismissTabSwitchToast, 6000);
}

function dismissTabSwitchToast() {
  clearTimeout(tabSwitchToastTimer);
  const toast = document.getElementById("tab-switch-toast");
  if (!toast) return;
  toast.classList.remove("toast-open");
  setTimeout(() => toast.remove(), 200);
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}

// Entry point - deliberately the last thing in the file. JS can't read
// the httpOnly session cookie itself to know whether a returning user is
// still signed in, so this asks the server (GET /auth/me) instead - a
// valid cookie resolves it and logs the user straight back in, same as
// the old sessionStorage-based check used to, just via a real round trip
// instead of a synchronous local read. Deliberately last in the file for
// the same reason the old check was: it calls straight into
// renderHRRoundNav()/renderCandidateRoundNav() (via onLoggedIn), which
// reference `const`s declared further up this file (ROUND_LABELS and
// others) - those are in a "temporal dead zone" until their own
// declaration line has run.
(async function bootstrapSession() {
  try {
    const me = await fetch("/auth/me");
    if (!me.ok) return; // no valid session cookie - stay on the login screen
    const data = await me.json();
    role = data.role;
    userEmail = data.email;
    onLoggedIn();
  } catch (e) {
    // Network error on page load - stay on the login screen, same as a 401.
  }
})();
