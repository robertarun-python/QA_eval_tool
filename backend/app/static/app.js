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
const ROUND_LABELS = { 1: "Manual test cases", 2: "AI-Assisted Test Automation", 3: "AI-prompted coding", 4: "Debugging" };

// Per-round description-field guidance for the shared "Create a scenario"
// form (see resetCreateScenarioForm) - each round hands the candidate a
// different kind of prompt (a feature to test, a bug report to debug, a
// problem statement to solve), so one static placeholder can't describe
// all of them. Round 4 isn't here - it has its own dedicated authoring
// panel (see selectHRRound/loadRound4Settings), never this shared form.
// Experience band is a hidden feature right now - HR no longer picks one
// per scenario or per candidate (see resetCreateScenarioForm/createScenario
// below and loadCandidates), so every scenario is created under this one
// band. The band model/filtering itself is untouched - see models.Scenario/
// User.experience_band and candidate.py's _live_scenario - only the UI
// controls for choosing a different one are hidden.
const DEFAULT_BAND = "0-7";

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
let hrPage = "rounds";      // "rounds" (author/review), "candidates" (results dashboard), "settings", or "progressive" (Round 5 POC)
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
    if (["rounds", "candidates", "settings", "progressive"].includes(saved.page)) hrPage = saved.page;
    if ([1, 2, 3, 4].includes(saved.round)) currentHRRound = saved.round;
  } catch (e) {
    // Corrupt/unreadable value - just keep the defaults above.
  }
}
let candidateSummaryData = null;        // last-generated { round_comments, final_summary } (see generateCandidateSummary) - reused by the PDF download so it doesn't cost a second LLM call
let candidateDetailSubmissions = [];    // the currently-open candidate's submissions (see openCandidateDetail) - lets generateCandidateSummary label each round comment with its real title/score
let currentCandidateDetailId = null;    // which candidate's detail panel is open - lets retryScoring/saveScoreOverride re-render the panel they're inside after a successful action
let candidateDetailSeq = 0;             // bumped on every openCandidateDetail - a response that comes back after HR has already opened another candidate (or re-opened this one) checks this and drops itself instead of painting stale data into the panel
let appearanceDetailSeq = 0;            // same idea for the "Past appearances" drill-down
const candidateDetailPending = new Set(); // in-flight detail actions ("retry-12", "override-12", "summary-3"...) - a double-click can't send the same request twice
let candidateDetailReturn = null;       // { scrollTop, email } - where Back returns to in the candidates list (scroll position, and whose "View report" button gets focus back)
let candidatesListStale = false;        // a retry/override changed a score while the detail view was open - Back re-fetches the list (keeping search/filter/page) instead of showing stale numbers
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
    // The session cookie is missing/expired, or (see dependencies.
    // get_current_user) a later login elsewhere has invalidated it mid-
    // round - either way the server no longer recognizes it, so there's
    // nothing useful left to do but send the user back to login. The
    // response's own detail distinguishes the two cases for the
    // candidate ("logged in from another device" vs. a plain expiry) -
    // fall back to a generic message only if the body doesn't have one.
    const body = await res.json().catch(() => null);
    // _performLogout(), not logout() - this already happened, it isn't
    // a choice to confirm, and an expired token can't be decoded
    // server-side to finalize a round anyway (see auth.py's logout).
    _performLogout();
    throw new Error(apiErrorMessage(body, "Your session expired - please log in again."));
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
  // A nudge, not a block, same as the tab-switch guard - candidates can
  // still choose to log out, they just can't do it by accident. Unlike
  // the tab-switch guard, though, this one really does end the round:
  // logging out now finalizes whatever's in progress immediately (see
  // routers/auth.py's logout) - completed work gets scored as of this
  // moment, and anything not attempted at all scores zero, exactly like
  // a genuine timeout. There's no coming back to this attempt afterward.
  //
  // Only shown for an actual click on the Log out button - api()'s own
  // 401 handler calls _performLogout() directly, not this, since a
  // session that already expired server-side isn't a choice to confirm,
  // and by that point there's nothing left for /auth/logout to finalize
  // anyway (an expired token can't be decoded to find whose round it
  // was - see auth.py's logout - so that case falls to the deadline-based
  // close_expired_submissions backstop instead).
  if (timerHandle && !confirm(
    "Logging out now will end this round immediately - you won't be able to come back to it. " +
    "Whatever you've completed (or left in progress) will be scored exactly as it stands right now; " +
    "anything you haven't attempted at all will score zero. Log out anyway?"
  )) {
    return;
  }
  await _performLogout();
}

async function _performLogout() {
  stopTimer();
  resetTopbarTimer();
  disarmTabGuard();
  // JS can't clear an httpOnly cookie itself - a real request is the
  // only way. Not api() here: if the cookie's already expired this would
  // 401 and api()'s own handler would call this right back, recursing.
  // Best-effort either way - local UI state below gets cleared
  // regardless of whether this call actually succeeds.
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
      <span class="rail-user-email" title="${escapeAttr(userEmail || "")}">${escapeHtml(userEmail || "Loading...")}</span>
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
    // A previous HR session on this page (logout without a reload) may
    // have left a candidate report open - always start on the list.
    closeCandidateDetail({ silent: true });
    restoreHRNavState();
    renderHRRoundNav();
    loadScenarios();
    loadCandidates();
    loadHistory();
    loadAppSettings();
  } else {
    document.getElementById("candidate-panel").classList.remove("hidden");
    refreshCandidateNav();
    // Independent of the round-sequence nav above - see renderCandidateProgressiveNav.
    document.getElementById("candidate-progressive-nav").innerHTML = "";
    candidatePage = "rounds";
    renderCandidateProgressiveNav();
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
  // Defensive reset, same panels closeScenarioDetail restores - if a
  // scenario's detail was left open when HR switched rounds, its
  // hidden/full-width state shouldn't follow them to a round they
  // haven't opened anything in yet.
  closeScenarioDetail();

  // The automation round sits at slot 2 since the 2<->4 renumbering; the
  // identifier keeps its historical name (see scoring_service._SCORERS).
  const isRound4 = n === 2;
  // It gets its own single settings view instead of the author/review/
  // publish flow - see loadRound4Settings for why none of that maps onto
  // this round's actual shape (no fixed reference, no meaningfully
  // different "versions" to browse or compare).
  document.getElementById("create-scenario-row").classList.toggle("hidden", isRound4);
  document.getElementById("screening-history-panel").classList.toggle("hidden", isRound4);
  document.getElementById("round4-settings-panel").classList.toggle("hidden", !isRound4);
  // #scenario-kpis sits outside create-scenario-row (so it reads as part
  // of the page, not nested inside the 2-column workspace) - it needs
  // its own hide, or it'd keep showing whichever round's counts were
  // last loaded instead of disappearing along with the rest of the
  // round 1-3 scenario-library UI.
  document.getElementById("scenario-kpis").classList.toggle("hidden", isRound4);
  // Round 3's guardrail reference (see index.html) - static, no API call,
  // just shown/hidden alongside the rest of this round's panels.
  document.getElementById("round3-guardrails-panel").classList.toggle("hidden", n !== 3);

  if (isRound4) {
    loadRound4Settings();
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
  document.getElementById("settings-status").textContent = "Defaults filled in - click \"Save changes\" to apply.";
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
// loadRound4Settings) - these counts wouldn't mean anything there.
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
    : `<span class="muted">-</span>`;
  const deleteBtn = s.is_live
    ? `<button class="btn-ghost btn-sm" disabled title="Can't delete the live scenario - make a different one live first.">Delete</button>`
    : `<button class="btn-danger btn-sm" onclick="deleteScenarioFromList(${s.id})">Delete</button>`;
  return `
    <tr class="${s.is_live ? "scenario-row-live" : ""}">
      <td class="tabular">${rank}</td>
      <td class="scenario-title-cell">${escapeHtml(s.title)}${s.is_live ? ' <span class="badge badge-published">LIVE</span>' : ""}</td>
      <td>${statusBadge}</td>
      <td>${liveCell}</td>
      <td class="tabular">${s.time_limit_minutes}</td>
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
    : (scenario.reference_json || []).map((r, i) => `
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
  // Round 4 no longer routes through here at all (see loadRound4Settings/
  // renderRound4SettingsCard) - it has no fixed reference to author/
  // review/compare across versions the way round 1/2 do, so it gets its
  // own dedicated settings panel instead of a "Review" flow into this one.
  box.innerHTML = `
    <div class="row" style="align-items:center; justify-content:space-between">
      <h3 style="margin:0">#${scenario.id} - ${escapeHtml(scenario.title)} <span class="badge badge-${scenario.status}">${statusLabel(scenario.status)}</span>${scenario.is_live ? ' <span class="badge badge-published">LIVE</span>' : ""}</h3>
      <button class="btn-ghost" onclick="closeScenarioDetail()">Close</button>
    </div>
    ${scenario.is_live ? `<p class="muted">This is the one scenario Round ${scenario.round_number} candidates currently see.</p>` : ""}
    <p class="muted">Round ${scenario.round_number}</p>
    ${isDraft ? `
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

async function loadRound4Settings() {
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
  if (!liveScenario) {
    box.innerHTML = "";
    return;
  }
  const liveRound1 = allScenarios.find((s) => s.round_number === 1 && s.experience_band === DEFAULT_BAND && s.is_live);
  box.innerHTML = renderRound4SettingsCard(liveScenario, liveRound1 ? liveRound1.title : null);
}

function renderRound4SettingsCard(scenario, groundedInTitle) {
  // 60 below is a defensive fallback only (e.g. this renders before
  // appSettings has loaded) - the real default always comes from the
  // server (see AppSettingsOut.round4_default_assistance_pct /
  // config.py's round4_default_assistance_pct), same pattern as
  // passingScoreForRound() above.
  const serverDefault = (appSettings && appSettings.round4_default_assistance_pct) ?? 60;
  const assistancePct = (scenario.config_json && scenario.config_json.assistance_pct) || serverDefault;
  return `
    <div class="panel card" style="margin-bottom:1.5rem">
      <h3>Round 2 <span class="badge badge-published">LIVE</span></h3>
      <p class="muted">This is what Round 2 candidates currently see.</p>

      <h4>Instructions</h4>
      <p class="muted">What candidates read when they open this round.</p>
      <input id="r4-title-${scenario.id}" value="${escapeAttr(scenario.title)}" />
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
        ? `This round's test environment &amp; reference screens are auto-generated from <strong>${escapeHtml(groundedInTitle)}</strong> - the round 1 scenario currently live. They resync automatically whenever a different round 1 scenario goes live here.`
        : `No round 1 scenario is currently live - the environment/screens below fell back to this scenario's own description instead. They'll resync automatically once one is published.`}</p>

      <details>
        <summary>Preview: test environment &amp; reference screens (auto-generated, shown to candidates)</summary>
        ${scenario.environment_json ? `
          <div class="hint-box env-panel">
            <dl class="env-fields" id="r4-env-fields-${scenario.id}">
              ${Object.entries(scenario.environment_json.fields || {}).map(([k, v]) => `
                <dt>${escapeHtml(k)}</dt>
                <dd><input type="text" class="env-field-input" data-key="${escapeAttr(k)}" value="${escapeAttr(v)}" /></dd>
              `).join("")}
            </dl>
            <textarea id="r4-env-notes-${scenario.id}" rows="2" placeholder="Notes (optional)">${escapeHtml(scenario.environment_json.notes || "")}</textarea>
            <div class="row">
              <button onclick="saveRound4Environment(${scenario.id})">Save environment fields</button>
            </div>
            ${scenario.environment_hr_edited
              ? `<p class="muted">These fields were hand-set by HR - the automatic refresh that runs when a different round 1 scenario goes live won't overwrite them. Only "Regenerate" below replaces them.</p>`
              : ""}
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

async function saveRound4Environment(id) {
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
    loadRound4Settings();
  } catch (e) {
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
  if (statusFilter) rows = rows.filter((c) => c.result === statusFilter);

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
  { value: "in_progress", label: "In progress" },
  { value: "selected", label: "Selected" },
  { value: "not_selected", label: "Not selected" },
];

function renderCandidateStatusTabs(query, statusFilter) {
  const searched = query ? lastLoadedCandidates.filter((c) => c.email.toLowerCase().includes(query)) : lastLoadedCandidates;
  document.getElementById("candidates-status-tabs").innerHTML = CANDIDATE_STATUS_TABS.map((t) => {
    const count = t.value ? searched.filter((c) => c.result === t.value).length : searched.length;
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
  return `<span class="badge cd-badge-info">In progress</span>`;
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
  return `<div class="status-cell">${resultBadge(c.result)}</div>`;
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
function renderSubmissionsPanels(submissions) {
  if (submissions.length === 0) return `<p class="muted">No submissions in this cycle.</p>`;
  return submissions.map((s) => `
    <div class="panel-inset">
      <h4>Round ${s.round_number} - ${s.scenario ? escapeHtml(s.scenario.title) : ""} <span class="badge">${s.status}</span>
        ${s.tab_switch_count > 0 ? `<span class="badge badge-fail" title="Timestamps: ${s.tab_switch_events_json.map(formatDateTime).join(", ")}">Left the test ${s.tab_switch_count} time${s.tab_switch_count === 1 ? "" : "s"}</span>` : ""}
        ${s.auto_closed_reason ? `<span class="badge badge-draft" title="${escapeAttr(s.auto_closed_reason)}">Auto-closed</span>` : ""}
      </h4>
      <p class="muted">${s.started_at ? `Started ${formatDateTime(s.started_at)}` : ""}${s.started_at && s.submitted_at ? " · " : ""}${s.submitted_at ? `Submitted ${formatDateTime(s.submitted_at)}` : ""}</p>
      ${s.scenario ? `
        <details class="scenario-question" open>
          <summary>Question</summary>
          ${formatScenarioDescription(s.scenario.description)}
        </details>
      ` : ""}
      ${renderScoreBlock(s)}
      ${s.round_number === 2 ? renderRound4Report(s)
        : s.round_number === 3 ? renderRound3Report(s)
        : s.round_number === 4 ? renderRound2Report(s)
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
    return `<span class="${cls}" title="${escapeAttr(c.notes || "")}">${escapeHtml(c.category)} ${c.covered}/${c.total}</span>`;
  });
  return `<p class="muted">Coverage by type: ${parts.join(" · ")}</p>`;
}

function renderScoreBlock(s) {
  if (s.status === "scoring_failed") {
    return `
      <div class="panel-inset cd-score-failed">
        <p><strong class="score-bad">Scoring failed</strong></p>
        <p class="muted">${escapeHtml(s.scoring_error || "Unknown error.")}</p>
        <div class="row">
          <button id="retry-btn-${s.id}" onclick="retryScoring(${s.id})">Retry scoring</button>
          <button class="btn-ghost" onclick="toggleScoreOverrideForm(${s.id})">Score manually</button>
        </div>
        <p id="score-status-${s.id}" class="muted" role="status"></p>
        <div id="override-form-${s.id}"></div>
      </div>
    `;
  }
  if (s.score) {
    const passed = s.score.final_score >= passingScoreForRound(s.round_number);
    return `
      <p>Final score: <strong class="${passed ? "score-good" : "score-bad"}">${s.score.final_score}/100</strong>${s.score.coverage_score != null ? ` · Coverage: ${s.score.coverage_score}/100` : ""}
        ${s.score.overridden_by_hr ? `<span class="badge">Overridden by HR${s.score.original_final_score != null ? ` - LLM originally said ${s.score.original_final_score}/100` : ""}</span>` : ""}
      </p>
      <p>${escapeHtml(s.score.feedback_text || "")}</p>
      <p class="muted">Missed: ${(s.score.misses_json || []).map(escapeHtml).join(", ") || "none noted"}</p>
      ${conceptCoverageLine(s.score.concept_coverage_json)}
      ${s.score.overridden_by_hr ? `<p class="muted">Override note: ${escapeHtml(s.score.override_note || "")}</p>` : ""}
      ${s.score.scoring_model ? `<p class="muted">Scored with ${escapeHtml(s.score.scoring_model)} · prompt ${escapeHtml(s.score.scoring_prompt_hash || "")}</p>` : ""}
      <div class="row">
        <button class="btn-ghost" onclick="toggleScoreOverrideForm(${s.id})">Override score</button>
      </div>
      <p id="score-status-${s.id}" class="muted" role="status"></p>
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
      <input id="override-score-${submissionId}" type="number" min="0" max="100" step="1" required />
      <label class="muted">Feedback (optional - leave blank to keep as-is)</label>
      <textarea id="override-feedback-${submissionId}"></textarea>
      <label class="muted">Why is this being overridden? (required)</label>
      <textarea id="override-note-${submissionId}"></textarea>
      <button id="override-save-${submissionId}" onclick="saveScoreOverride(${submissionId})">Save override</button>
    </div>
  `;
}

async function retryScoring(submissionId) {
  const key = `retry-${submissionId}`;
  if (candidateDetailPending.has(key)) return;
  const statusEl = document.getElementById(`score-status-${submissionId}`);
  const btn = document.getElementById(`retry-btn-${submissionId}`);
  candidateDetailPending.add(key);
  if (btn) btn.disabled = true;
  statusEl.textContent = "Retrying - this can take a few seconds...";
  try {
    await api(`/hr/submissions/${submissionId}/retry-scoring`, { method: "POST" });
    candidateDetailPending.delete(key);
    candidatesListStale = true;
    openCandidateDetail(currentCandidateDetailId, { refresh: true });
  } catch (e) {
    candidateDetailPending.delete(key);
    // The panel may have been re-rendered (or another candidate opened)
    // while this was in flight - only touch elements that still exist.
    if (btn && btn.isConnected) btn.disabled = false;
    if (statusEl.isConnected) statusEl.textContent = e.message;
  }
}

async function saveScoreOverride(submissionId) {
  const key = `override-${submissionId}`;
  if (candidateDetailPending.has(key)) return;
  const statusEl = document.getElementById(`score-status-${submissionId}`);
  const scoreRaw = document.getElementById(`override-score-${submissionId}`).value.trim();
  const feedback_text = document.getElementById(`override-feedback-${submissionId}`).value.trim() || null;
  const override_note = document.getElementById(`override-note-${submissionId}`).value.trim();
  // An empty field must not become Number("") === 0 - that would save a
  // real 0/100 the HR user never typed.
  if (!/^\d+$/.test(scoreRaw) || Number(scoreRaw) > 100) {
    statusEl.textContent = "Enter a whole-number score from 0 to 100.";
    document.getElementById(`override-score-${submissionId}`).focus();
    return;
  }
  const final_score = Number(scoreRaw);
  if (!override_note) {
    statusEl.textContent = "Explain why this is being overridden before saving.";
    document.getElementById(`override-note-${submissionId}`).focus();
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
    openCandidateDetail(currentCandidateDetailId, { refresh: true });
  } catch (e) {
    candidateDetailPending.delete(key);
    if (btn && btn.isConnected) btn.disabled = false;
    if (statusEl.isConnected) statusEl.textContent = e.message;
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
    ${c ? `<div class="status-cell" id="candidate-detail-result">${resultBadge(c.result)}</div>` : ""}
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
          <div class="status-cell">${resultBadge(c.result)}</div>
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
  if (badge) badge.innerHTML = resultBadge(c.result);
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

  box.innerHTML = `
    ${renderSubmissionsPanels(submissions)}
    <div class="panel-inset">
      <h4>Summary</h4>
      <p class="muted">A crisp, cross-round synthesis for feedback to the candidate or a briefing for the next round's interviewers.</p>
      <div id="candidate-summary-controls" class="row">
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
  loadAppearances(id, seq);
  loadExistingCandidateSummary(id, seq);
  if (opts.refresh) refreshCandidateDetailSummary(id, seq);
  // The load-failure banner above is role="alert" and announces itself.
  announceCandidateDetail(opts.refresh ? "Report updated." : "Candidate report loaded.");
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
  const seq = ++appearanceDetailSeq;
  const detailEl = document.getElementById("appearance-detail");
  detailEl.innerHTML = loadingHtml();
  let submissions;
  try {
    submissions = await api(`/hr/candidates/${candidateId}/appearances/${appearanceId}/report`);
  } catch (e) {
    if (seq !== appearanceDetailSeq || !detailEl.isConnected) return;
    detailEl.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
    announceCandidateDetail(`Couldn't load that appearance: ${e.message}`);
    return;
  }
  // A different appearance was clicked, or the whole panel re-rendered,
  // while this one was loading.
  if (seq !== appearanceDetailSeq || !detailEl.isConnected) return;
  detailEl.innerHTML = renderSubmissionsPanels(submissions);
  announceCandidateDetail("Past appearance report loaded.");
}

// Tries to load a summary saved from an earlier visit (see models.
// CandidateSummary) - no LLM call, so opening a candidate HR has already
// summarized before shows it and its Download/Regenerate/Delete controls
// immediately, rather than making HR click Generate again just to get
// back something that already exists. A 404 here is the normal "nothing
// generated yet" case, not an error - the static "Generate Summary"
// button already in the panel is left exactly as it is.
async function loadExistingCandidateSummary(id, seq) {
  let result;
  try {
    result = await api(`/hr/candidates/${id}/summary`);
  } catch (e) {
    // Leave the initial "Generate Summary" button in place.
    return;
  }
  if (seq !== candidateDetailSeq) return;
  renderCandidateSummary(id, result);
}

// Disables every summary button (Generate/Regenerate/Download/Delete)
// while one of them is in flight - they all act on the same saved
// summary, so none should run alongside another.
function setCandidateSummaryControlsBusy(busy) {
  document.querySelectorAll("#candidate-summary-controls button").forEach((b) => { b.disabled = busy; });
}

async function generateCandidateSummary(id) {
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
function renderCandidateSummary(id, result) {
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
      ? `<span class="badge badge-score ${passed ? "badge-pass" : "badge-fail"}">${submission.score.final_score}/100</span>`
      : submission && submission.status === "scoring_failed"
        ? `<span class="badge badge-score badge-fail">Scoring failed</span>`
        : `<span class="badge badge-score">Not scored yet</span>`;
    const accentClass = !submission || !submission.score ? "" : passed ? "round-pass" : "round-fail";
    return `
      <div class="panel-inset summary-round ${accentClass}">
        <div class="summary-round-head">
          <h5><span class="summary-round-number">${String(rc.round_number).padStart(2, "0")}</span>${escapeHtml(label)}</h5>
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
      <h5>Key Observations</h5>
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
  document.getElementById("candidate-summary-controls").innerHTML = `
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
      <p><strong>Assistant:</strong> <span class="badge ${ROUND3_RESPONSE_KIND_BADGE[t.response_kind] || ""}">${escapeHtml(t.response_kind)}</span> ${escapeHtml(t.response_message)}</p>
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
            <span class="tick-label" id="round-tick-label-${n}">${ROUND_LABELS[n]}</span>
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
  // renderCandidateRoundNav (above) already set the page header AND the
  // rail's tick label from the static ROUND_LABELS map, before this
  // scenario was known - neither the Focused Automation Pilot (see
  // models.Scenario.is_pilot) nor AI-Assisted Test Automation (see
  // models.Scenario.is_auto) is "Conversational" at all, so correct both
  // now that we actually know which round 4 shape this is. No-op for
  // every other round/scenario.
  if (n === 2 && state.scenario && state.scenario.is_pilot) {
    setPageHeader("Candidate Assessment", "Round 2 · Automation Engineering with AI Assistance", "");
    const tickLabel = document.getElementById("round-tick-label-2");
    if (tickLabel) tickLabel.textContent = "Automation Engineering";
  } else if (n === 2 && state.scenario && state.scenario.is_auto) {
    setPageHeader("Candidate Assessment", "Round 2 · AI-Assisted Test Automation", "");
    const tickLabel = document.getElementById("round-tick-label-2");
    if (tickLabel) tickLabel.textContent = "AI-Assisted Test Automation";
  }
  renderRoundView(box, n, state);
}

function renderRoundView(box, n, state) {
  const { scenario, submission, environment, ui_mockup } = state;

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
    if (n === 2) {
      // No Start button here at all - the briefing modal below is the
      // only way in, appearing the instant this round is opened. Its own
      // "Got it - Start Round 4" button is what actually starts the
      // round (see confirmStartRound4/confirmStartRound4Pilot/
      // confirmStartRound4Auto) - reading this costs no time either way,
      // since the timer only starts on that click. Pilot (see
      // models.Scenario.is_pilot) and AI-Assisted Test Automation (see
      // models.Scenario.is_auto) each get their own self-contained
      // briefing - showRound4Intro's content (round 1 test cases, a
      // simulated no-code assistant, UI/API/DB bonus credit) describes
      // the legacy conversational flow and is wrong for either.
      box.innerHTML = `
        <h3>Round ${n}: ${escapeHtml(scenario.title)}</h3>
        ${formatScenarioDescription(scenario.description)}
        <p class="muted">Time limit: ${scenario.time_limit_minutes} minutes, starting once you confirm below.</p>
      `;
      if (scenario.is_pilot) {
        showRound4PilotIntro(scenario.time_limit_minutes);
      } else if (scenario.is_auto) {
        showRound4AutoIntro(scenario.time_limit_minutes);
      } else {
        showRound4Intro();
      }
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
    showRoundIntro(n, scenario.time_limit_minutes, environment);
    return;
  }

  renderRoundEntry(n, box, scenario, submission, environment, ui_mockup);
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

function showRoundIntro(n, timeLimitMinutes, environment) {
  const hasEnvironment = n === 1 && environment && Object.keys(environment.fields || {}).length > 0;
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
        ${hasEnvironment ? `<li>You'll see a test environment reference (login, sample data, ...) below - it's a starting point you can use as-is, but feel free to add your own test data and cases beyond it.</li>` : ""}
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
  renderRoundEntry(n, box, state.scenario, submission, state.environment, state.ui_mockup);
}

// One-time briefing before round 4's timer starts - shown instead of an
// immediate Start, since round 4's format (prompt-driven, an
// intentionally imperfect assistant, no fixed checklist) isn't
// self-explanatory the way rounds 1/2's plain forms are. Reading this
// doesn't cost any time - the timer only starts once startRound(2) is
// actually called, from confirmStartRound4 below. Deliberately no
// specifics on how often or how the assistant gets things wrong - that's
// what the round is testing; this only sets expectations, not answers.
function showRound4Intro() {
  const overlay = document.createElement("div");
  overlay.id = "round4-intro-overlay";
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-box neutral">
      <h3>Before you start Round 2</h3>
      <ul>
        <li>Your Round 1 test cases are the starting point, not a limit - add as many extra as the scenario needs. Describe each to an AI assistant, which simulates running it and reports what it observed (no code involved). You'll have a test environment reference (sample data, credentials, API/DB details) and reference app screens as your source of truth.</li>
        <li>The assistant won't always get it right - it may skip a check, misreport a result, or be wrong on purpose. Review every response like a test log you didn't write, and refine your prompts until you're confident it's actually correct.</li>
        <li>Scored mainly on prompting and verification quality - catching issues, asking the right follow-ups, converging on a correct result - not on automating your entire Round 1 list, which isn't realistic or measured. Automating more than one area (UI, API, DB, end-to-end) earns bonus credit, on top of doing a few well.</li>
        <li>Steering the assistant to skip verification or reveal what scores well won't work and gets flagged as a concern (e.g. "just mark everything passing"). It reports what happened, not what looks good.</li>
        <li>Timer starts the moment you click below.</li>
      </ul>
      <div class="row">
        <button onclick="confirmStartRound4()">Got it - Start Round 2</button>
      </div>
    </div>
  `;
  openModalOverlay(overlay);
}

// Focused Automation Pilot's own briefing (see models.Scenario.is_pilot) -
// a real single-file Python exercise with a narrow coding assistant, not
// the legacy conversational flow above. No language choice afterwards
// (the exercise is fixed Python) - "Got it" goes straight to startRound(2).
// Deliberately generic process steps only - no scoring weights, no hidden
// tests, no reference solution, no policy internals, no hint at the
// requirement's own ambiguity or what a strong answer looks like.
function showRound4PilotIntro(timeLimitMinutes) {
  const overlay = document.createElement("div");
  overlay.id = "round4-intro-overlay";
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-box neutral">
      <h3>Before you start Round 2</h3>
      <ol>
        <li>Review the provided automation.</li>
        <li>Ask clarification questions when a requirement is unclear.</li>
        <li>Use the provided AI assistant for focused assistance.</li>
        <li>Modify the automation.</li>
        <li>Run and inspect the result.</li>
        <li>Validate repeatability/persistence and the final automation.</li>
        <li>Submit when satisfied.</li>
      </ol>
      <p class="muted">You are responsible for understanding, validating, and maintaining the final automation.</p>
      <p class="muted">You'll have ${timeLimitMinutes} minutes once you click below - the timer starts immediately.</p>
      <div class="row">
        <button onclick="confirmStartRound4Pilot()">Got it - Start Round 2</button>
      </div>
    </div>
  `;
  openModalOverlay(overlay);
}

function confirmStartRound4Pilot() {
  closeModalOverlay("round4-intro-overlay");
  startRound(2);
}

// AI-Assisted Test Automation's own briefing (see models.Scenario.is_auto) -
// the candidate automates test cases THEY designed in round 1, not the
// legacy conversational flow above. Unlike the other round 2 briefings,
// this one picks and locks the language ITSELF (see confirmStartRound4Auto)
// before startRound(2) ever fires, so the timer never burns on a
// language screen and the round's own first view is test case
// selection - renderRound4AutomationLayout's own language-lock branch is
// now only a defensive fallback for a round already in progress from
// before this. Locked here is inherited by round 3 too (see
// routers/candidate.py's _round3_language_for) - round 3 never asks.
// Deliberately generic process steps only - no scoring weights, no
// ground truth, no policy internals, no hint at what a strong answer
// looks like.
function showRound4AutoIntro(timeLimitMinutes) {
  const overlay = document.createElement("div");
  overlay.id = "round4-intro-overlay";
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-box neutral">
      <h3>Before you start Round 2</h3>
      <ol>
        <li>Choose the language you'll automate in - locked for the rest of this round.</li>
        <li>Pick one or two of your own Round 1 test cases to automate.</li>
        <li>Use the provided AI assistant to turn your design into working code against the provided environment.</li>
        <li>Review everything it produces, and edit the code yourself where you disagree.</li>
        <li>Run it and inspect the result.</li>
        <li>Explain what the result actually proves.</li>
        <li>Submit when satisfied.</li>
      </ol>
      <p class="muted">The assistant will only encode what you already specified in Round 1 - it won't invent test cases, test data, or assertions. You are responsible for the final automation.</p>
      <p class="muted">AI-generated code may not always be clean, complete, correct, or reliable. You are responsible for reviewing and validating it.</p>
      <p class="muted">You'll have ${timeLimitMinutes} minutes once you click below - the timer starts immediately, after you pick a language.</p>
      <div class="field-row" style="align-items:center">
        <select id="r4a-intro-language-select" onchange="round4AutoIntroLanguageChanged()">
          <option value="" selected>Select a language...</option>
          <option value="python">Python</option>
          <option value="java">Java</option>
          <option value="javascript">JavaScript</option>
        </select>
      </div>
      <div class="row">
        <button id="r4a-intro-start-btn" onclick="confirmStartRound4Auto()" disabled>Got it - Start Round 2</button>
      </div>
    </div>
  `;
  openModalOverlay(overlay);
}

function round4AutoIntroLanguageChanged() {
  const language = document.getElementById("r4a-intro-language-select").value;
  document.getElementById("r4a-intro-start-btn").disabled = !language;
}

async function confirmStartRound4Auto() {
  const language = document.getElementById("r4a-intro-language-select").value;
  if (!language) return;
  closeModalOverlay("round4-intro-overlay");
  // Locked here, before the round's own view ever renders, so the
  // candidate never sees an in-round language screen and the timer
  // (started by /start below) never burns on picking one - see
  // renderRound4AutomationLayout's now-defensive-only language_locked
  // branch, kept for any round already in progress from before this.
  await api("/candidate/round/2/start", { method: "POST" });
  await api("/candidate/round/2/auto/language", { method: "POST", body: JSON.stringify({ language }) });
  const state = await api("/candidate/round/2");
  const box = document.getElementById("round-view");
  renderRoundEntry(2, box, state.scenario, null);
}

// Split from the intro (see showRound4Intro) so the language choice
// happens as its own confirmed step, matching the "Got it" -> pick ->
// Start pattern used elsewhere - not silently defaulting
// round4DefaultLanguage's initial "python" value the way it used to.
// Unlike round 2/3's locked language, this choice ISN'T locked for the
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
      <button id="round4-start-btn" onclick="confirmStartRound4WithLanguage()" disabled>Start Round 2</button>
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
  startRound(2);
}

// Each round's candidate-facing shape is genuinely different now: round
// 1 is repeatable test-case rows, round 2 is a shorter investigation
// list + one root-cause conclusion, round 4 is conversational. No
// shared "structured rounds" bucket anymore - just dispatch by number.
function renderRoundEntry(n, box, scenario, submission, environment, uiMockup) {
  if (n === 1) {
    renderEntryForm(box, scenario, submission, environment, uiMockup);
  } else if (n === 4) {
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

// Round 1: repeatable test-case rows (title/preconditions/steps/test_data/expected_result).

function round1DraftPayload() {
  return { content: collectRows() };
}

// Read-only look at the same test-environment reference round 2
// generates/owns (see RoundStateOut.environment) - a specific example to
// anchor "Test data" around instead of writing something vague, but
// never the only allowed values (see the pre-start note in
// showRoundIntro). Same markup as round4AutoReferenceHtml's env block,
// just without the edit controls - this view is read-only.
function round1EnvironmentReferenceHtml(environment, uiMockup) {
  const mockupHtml = uiMockup ? `
    <details class="hint-box mockup-details" open>
      <summary><strong>Reference: App screens</strong></summary>
      ${renderMockupScreens(uiMockup, "r1-ref-mockup")}
    </details>
  ` : "";
  const envFields = environment ? Object.entries(environment.fields || {}) : [];
  const envHtml = envFields.length > 0 ? `
    <details class="hint-box env-panel" open>
      <summary><strong>Reference: test environment</strong> - use this test data, or add your own beyond it</summary>
      <dl class="env-fields">
        ${envFields.map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join("")}
      </dl>
      ${environment.notes ? `<p>${escapeHtml(environment.notes)}</p>` : ""}
    </details>
  ` : "";
  return mockupHtml + envHtml;
}

function renderEntryForm(box, scenario, submission, environment, uiMockup) {
  rowCount = 0;
  box.innerHTML = `
    <div class="page-header">
      <span class="eyebrow">Round 1 &middot; Manual Test Cases</span>
      <h1>${escapeHtml(scenario.title)}</h1>
    </div>
    <div class="surface" style="margin-bottom: var(--space-default)">
      ${formatScenarioDescription(scenario.description)}
    </div>
    ${round1EnvironmentReferenceHtml(environment, uiMockup)}
    <div class="section-header">
      <h2>Your test cases</h2>
      <span class="muted">Fill in every field below - be as specific as possible, especially test data.</span>
    </div>
    <div class="table-scroll">
      <table class="tc-table">
        <thead><tr><th class="tc-col-no">#</th><th class="tc-col-title">Test Case / Scenario</th><th class="tc-col-pre">Preconditions</th><th class="tc-col-steps">Steps</th><th class="tc-col-data">Test Data</th><th class="tc-col-expected">Expected Result / Assertions</th><th class="tc-col-actions"></th></tr></thead>
        <tbody>${exampleTestCaseRowHtml()}</tbody>
        <tbody id="tc-rows"></tbody>
      </table>
    </div>
    <div class="row">
      <button class="btn-secondary" onclick="addRow()">+ Add Test Case</button>
    </div>
    <div class="action-bar">
      <span class="autosave-status is-saved">Your progress autosaves as you type</span>
      <div class="action-bar-buttons">
        <button id="round1-submit-btn" class="btn-primary" onclick="doSubmitRound1()">Submit Round 1</button>
      </div>
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
    <h3>Round 4: ${escapeHtml(scenario.title)}</h3>
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
    <textarea id="inv-root-cause" oninput="scheduleRoundDraftSave(4, round2DraftPayload); round2UpdateSubmitState()"></textarea>
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
  }, 4);  // debugging is slot 4 since the 2<->4 renumbering (this arg drives armTabGuard)
}

function addInvestigationRow(initial = null) {
  const id = rowCount++;
  const tbody = document.getElementById("inv-rows");
  const tr = document.createElement("tr");
  tr.id = `inv-row-${id}`;
  tr.innerHTML = `
    <td class="inv-no"></td>
    <td><textarea class="inv-area" oninput="scheduleRoundDraftSave(4, round2DraftPayload); round2UpdateSubmitState()"></textarea></td>
    <td><button onclick="removeInvestigationRow('inv-row-${id}')">Remove</button></td>
  `;
  tbody.appendChild(tr);
  if (initial) tr.querySelector(".inv-area").value = initial.area || "";
  renumberInvestigationRows();
}

function removeInvestigationRow(rowId) {
  document.getElementById(rowId).remove();
  renumberInvestigationRows();
  scheduleRoundDraftSave(4, round2DraftPayload); // removing a row must survive a refresh too, not just additions
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
    await api("/candidate/round/4/submit", { method: "POST", body: JSON.stringify({ investigation, root_cause }) });
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
        <li>The timer starts the moment you click below, in the language you locked during Round 2.</li>
      </ul>
      <div class="row">
        <button onclick="confirmRound3CodingIntro()">Got it</button>
      </div>
    </div>
  `;
  openModalOverlay(overlay);
}

// Split from the intro (see showRound3CodingIntro) purely to match round
// 4's pattern of "Got it" being its own confirmed step. No language
// picker here anymore - round 3 inherits whatever was locked in round 2
// (see routers/candidate.py's _round3_language_for), so "Got it" goes
// straight to starting the round.
function confirmRound3CodingIntro() {
  closeModalOverlay("round3-coding-intro-overlay");
  startRound3Coding();
}

async function startRound3Coding() {
  await api("/candidate/round/3/start", { method: "POST", body: JSON.stringify({}) });
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
    const active = document.activeElement;
    // Only steal focus into the terminal when nothing else is claiming
    // it - this re-renders on every poll tick (ROUND3_RUN_POLL_MS) while
    // a run is active, so an unconditional focus() here would yank the
    // candidate's cursor back out of the composer/code-edit textarea on
    // every tick the instant they click into either to type a new
    // instruction while their program is still running - effectively
    // locking them out of typing anywhere but the terminal until the
    // run ends. Only take focus when the candidate hasn't deliberately
    // put it somewhere else (an <input>/<textarea>, or already the
    // terminal input itself).
    const userTypingElsewhere = active && active !== inputEl && (active.tagName === "TEXTAREA" || active.tagName === "INPUT");
    if (inputEl && active !== inputEl && !userTypingElsewhere) inputEl.focus();
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
    const result = await api(`/candidate/round/2/turn/${turnId}/code?language=${lang}`);
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
    round4State = await api("/candidate/round/2/state");
  } catch (e) {
    box.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
    return;
  }
  // Focused Automation Pilot (see routers/candidate.py's /round/4/pilot/*
  // endpoints) is a different candidate-facing shape entirely - a single
  // Python file + a narrow AI assistant, not test-case tabs and a
  // conversation - see renderRound4PilotLayout below. Same state fetch
  // and timer wiring either way; only which layout renders differs.
  // AI-Assisted Test Automation is a third round-4 mode - the candidate
  // automates the test cases THEY designed in round 1, so its state is
  // shaped nothing like the other two and lives behind its own endpoint
  // (see routers/candidate.py's /round/4/auto/*). The shared
  // /round/4/state call above still provides scenario + submission, so
  // the timer/tab-guard wiring below is identical for all three modes.
  if (round4State.scenario && round4State.scenario.is_auto) {
    await loadRound4Automation(box);
  } else if (round4State.is_pilot) {
    renderRound4PilotLayout(box);
  } else {
    // Seed the draft buffer from whatever was last autosaved server-side -
    // recovers in-progress text across a full page refresh, not just a tab
    // switch within the same page load.
    round4DraftBuffer = {};
    for (const tc of round4State.test_cases) round4DraftBuffer[tc.id] = tc.draft_prompt || "";
    round4ViewedTestCaseId = round4State.test_cases.length > 0 ? round4State.test_cases[0].id : null;
    renderRound4Layout(box);
  }

  if (!timerHandle) {
    const submission = round4State.submission;
    const deadline = new Date(submission.started_at + "Z").getTime() + round4State.scenario.time_limit_minutes * 60 * 1000;
    startTimer(deadline, round4AutoSubmit, 2);
  }
}

async function round4AutoSubmit() {
  const timerEl = document.getElementById("timer");
  if (timerEl) timerEl.textContent = "Time's up - submitting automatically...";
  // Pilot submissions live under a separate endpoint (see
  // renderRound4PilotLayout/round4PilotSubmitClicked below) - the legacy
  // one would 400 ("Create at least one test case...") since a pilot
  // submission has no round4_test_cases at all.
  // The automation mode can't be auto-submitted with an empty body at
  // all (it requires the candidate's own interpretation of their run -
  // see Round4AutoSubmitCreate), so on expiry it sends a placeholder
  // rather than silently discarding the round; a candidate who never got
  // that far just fails that rubric area, same as any other unfinished
  // work. Its own catch below already handles "nothing to submit".
  const isAuto = round4State && round4State.scenario && round4State.scenario.is_auto;
  const submitPath = isAuto
    ? "/candidate/round/2/auto/submit"
    : (round4State && round4State.is_pilot) ? "/candidate/round/2/pilot/submit" : "/candidate/round/2/submit";
  try {
    await api(submitPath, isAuto
      ? { method: "POST", body: JSON.stringify({ validation: "(time expired before the candidate submitted an interpretation)" }) }
      : { method: "POST" });
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
    <h3>Round 2: ${escapeHtml(s.scenario.title)}</h3>
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
      <button id="round4-submit-btn" class="btn-block" onclick="round4Submit()" ${s.test_cases.length > 0 ? "" : "disabled"}>Submit Round 2</button>
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
    const tc = await api("/candidate/round/2/test-case", { method: "POST", body: JSON.stringify({ title: null }) });
    round4State = await api("/candidate/round/2/state");
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
  api(`/candidate/round/2/test-case/${tcId}/draft`, {
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
    await api("/candidate/round/2/turn", {
      method: "POST",
      body: JSON.stringify({ test_case_id: tcId, candidate_prompt: prompt }),
    });
    clearTimeout(round4DraftTimers[tcId]);
    delete round4DraftTimers[tcId];
    round4DraftBuffer[tcId] = ""; // the server already cleared its copy as part of turn creation
    round4State = await api("/candidate/round/2/state");
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
    await api("/candidate/round/2/submit", { method: "POST" });
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
      <td>username = jordan.rivera@example.com; password = Passw0rd!2026</td>
      <td>User is redirected to /dashboard and the header shows "Welcome, Jordan".</td>
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
    <td><textarea class="tc-data" placeholder="Concrete values, e.g. amount = 0.00; card = 4000-0000-0000-0069" oninput="scheduleRoundDraftSave(1, round1DraftPayload)"></textarea></td>
    <td><textarea class="tc-expected" oninput="scheduleRoundDraftSave(1, round1DraftPayload); round1UpdateSubmitState()"></textarea></td>
    <td><button class="btn-danger btn-sm" onclick="removeRow('row-${id}')">Remove</button></td>
  `;
  tbody.appendChild(tr);
  if (initial) {
    tr.querySelector(".tc-title").value = initial.title || "";
    tr.querySelector(".tc-pre").value = initial.preconditions || "";
    tr.querySelector(".tc-steps").value = initial.steps || "";
    // `|| ""` covers a draft/submission autosaved before test_data existed.
    tr.querySelector(".tc-data").value = initial.test_data || "";
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
      test_data: tr.querySelector(".tc-data").value,
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

// Logs one fullscreen-exit and reports back the running strike count -
// see candidate.py's log_tab_switch, the single source of truth for the
// count (never tracked separately client-side, so a page reload mid-round
// can't desync it). On network failure, treat it as "no strike yet" -
// same fail-open spirit as the old fire-and-forget version, since a
// dropped request here must never itself end a candidate's round.
async function logTabSwitch(roundNumber) {
  if (role !== "candidate") return { strike_count: 0, round_ended: false };
  try {
    return await api(`/candidate/round/${roundNumber}/tab-switch`, { method: "POST" });
  } catch (e) {
    return { strike_count: 0, round_ended: false };
  }
}

// Shared by both exit-detection paths below (fullscreenchange and the
// passive visibilitychange/blur fallback) - the 3rd strike ends the
// round the same way regardless of which path caught it.
function handleStrikeRoundEnded() {
  removeFsOverlay();
  const toast = document.getElementById("tab-switch-toast");
  if (toast) toast.remove();
  const timerEl = document.getElementById("timer");
  if (timerEl) timerEl.textContent = "Round ended - left fullscreen 3 times. Saving your work...";
  stopTimer();
  disarmTabGuard();
  refreshCandidateNav();
}

document.addEventListener("fullscreenchange", () => {
  if (!fsGuardArmed || !fsGuardActive) return;
  if (document.fullscreenElement) {
    removeFsOverlay();
    return;
  }
  const roundNumber = fsGuardRound;
  logTabSwitch(roundNumber).then((result) => {
    if (result.round_ended) {
      handleStrikeRoundEnded();
    } else {
      showFsOverlay(roundNumber, result.strike_count);
    }
  });
});

function showFsOverlay(roundNumber, strikeCount) {
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
    const warn = existing.querySelector("#fs-guard-warning");
    if (warn) warn.textContent = `Warning ${strikeCount} of 3 - one more and this round ends automatically, scored on what you've written so far.`;
    return;
  }
  const overlay = document.createElement("div");
  overlay.id = "fs-guard-overlay";
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-box">
      <h3>Fullscreen required</h3>
      <p>This round must be taken in fullscreen. Leaving it has been logged and is visible to HR.</p>
      <p id="fs-guard-warning"><strong>Warning ${strikeCount} of 3</strong> - one more and this round ends automatically, scored on what you've written so far.</p>
      <div class="row">
        <button onclick="reenterFullscreen()">Return to fullscreen</button>
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
  // few times quickly can trigger that. This is always temporary (a
  // few seconds), never permanent, so simply retrying this same button
  // shortly after is the way through - no separate "continue without
  // fullscreen" escape is needed (and none is offered: every exit
  // already counted as a strike the instant it happened, in
  // logTabSwitch above, regardless of whether re-entry ever succeeds).
  document.documentElement.requestFullscreen().catch(() => {
    const statusEl = document.getElementById("fs-guard-status");
    if (statusEl) statusEl.textContent = "Couldn't re-enter fullscreen yet - wait a moment and try again.";
  });
}

// Fallback for browsers/contexts where fullscreen enforcement isn't
// available at all (fsGuardActive stays false) - same passive log +
// dismissible toast as before, so there's still some signal instead of
// no guard whatsoever. Exits here count toward the same 3-strike total
// (see log_tab_switch) - fullscreen being unavailable isn't a reason to
// exempt this path from the same consequence.
let tabSwitchPending = false;

document.addEventListener("visibilitychange", () => {
  if (fsGuardArmed && !fsGuardActive && document.hidden && !tabSwitchPending) {
    tabSwitchPending = true;
    const roundNumber = fsGuardRound;
    logTabSwitch(roundNumber).then((result) => {
      if (result.round_ended) handleStrikeRoundEnded();
    });
  }
});
window.addEventListener("blur", () => {
  if (fsGuardArmed && !fsGuardActive && !tabSwitchPending) {
    tabSwitchPending = true;
    logTabSwitch(fsGuardRound).then((result) => {
      if (result.round_ended) {
        tabSwitchPending = false; // nothing to show a returning-focus toast for - the round is already over
        handleStrikeRoundEnded();
      }
    });
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

// escapeHtml() is only safe for a text node - per the HTML serialization
// spec it never escapes " or ', so dropping its output inside a quoted
// HTML attribute (title="...", value="...", an inline onclick="...'...'")
// doesn't protect against a value that itself contains that quote
// character breaking out of the attribute. Use this instead anywhere
// untrusted (or LLM-echoed, which can reproduce untrusted text verbatim)
// text is interpolated into an attribute in a template string.
function escapeAttr(str) {
  return escapeHtml(str).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
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

// ==================== Round 4 pilot ("Focused Automation Pilot") ====================
// Candidate UI for the single-file automation exercise - see
// routers/candidate.py's /round/4/pilot/* endpoints and
// renderRound4View's is_pilot branch above. Renders into the same
// #round-view container every other round uses; no template/index.html
// changes needed. Deliberately separate render/action functions from the
// legacy conversational round 4 UI above (renderRound4Layout etc.) -
// same reasoning the backend already used to keep the two flows apart.
//
// Code editing model: the code textarea is directly editable. Run and
// Submit both read its current value and send it as {code: ...} to
// /pilot/run and /pilot/submit (see Round4PilotCodeUpdate/
// _apply_pilot_code_edit in routers/candidate.py) - the server persists
// that edit BEFORE executing/scoring, so both always act on exactly
// what's in the editor at the moment the button was clicked, never a
// stale server-side copy. An AI turn (Ask AI) can still update the same
// buffer via code_after, same as before - the two ways of changing the
// code (typing directly, or an AI-suggested edit) share one buffer.

let round4PilotBusy = false; // guards against overlapping Run/Ask/Clarify/Submit calls

function round4PilotSetBusy(busy) {
  round4PilotBusy = busy;
  ["round4-pilot-run-btn", "round4-pilot-ask-btn", "round4-pilot-clarify-btn", "round4-pilot-submit-btn"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = busy;
  });
}

function renderRound4PilotTurnsHtml() {
  const turns = round4State.pilot_turns || [];
  if (turns.length === 0) return `<p class="muted">No messages yet - ask the assistant something below.</p>`;
  return turns.map((t) => `
    <div class="panel-inset" style="margin-bottom:0.5rem">
      <p style="margin:0 0 0.35rem 0"><strong>You:</strong> ${escapeHtml(t.candidate_prompt)}</p>
      <p style="margin:0 0 ${t.code_after ? "0.35rem" : "0"} 0"><strong>Assistant</strong> <span class="muted">(${escapeHtml(t.response_kind)})</span>: ${escapeHtml(t.response_message)}</p>
      ${t.code_after ? `<pre class="code-snippet">${escapeHtml(t.code_after)}</pre>` : ""}
    </div>
  `).join("");
}

function renderRound4PilotRunResultHtml(run) {
  if (!run) return `<p class="muted">Not run yet.</p>`;
  return `
    <div class="panel-inset">
      ${run.timed_out ? `<p style="color:var(--warn)">Timed out.</p>` : ""}
      ${run.infra_error ? `<p style="color:var(--warn)">The execution service had a problem - try running again.</p>` : ""}
      <p class="muted" style="margin:0 0 0.3rem 0">Exit code: ${run.exit_code === null || run.exit_code === undefined ? "-" : run.exit_code}</p>
      ${run.stdout ? `<pre class="code-snippet">${escapeHtml(run.stdout)}</pre>` : `<p class="muted">No stdout.</p>`}
      ${run.stderr ? `<pre class="code-snippet round3-coding-stderr">${escapeHtml(run.stderr)}</pre>` : ""}
    </div>
  `;
}

function renderRound4PilotLayout(box) {
  const s = round4State;
  box.innerHTML = `
    <h3>Round 2: ${escapeHtml(s.scenario.title)}</h3>
    ${formatScenarioDescription(s.scenario.description)}
    <div class="panel-inset" style="margin-bottom:0.85rem">
      <p class="muted" style="margin:0">You're extending the existing automation below with the AI assistant's help. Ask it to explain code, help debug a failure, or make one specific, narrow change - it won't design your test strategy or write the whole thing for you, and you're responsible for reviewing anything it changes before you rely on it. If anything in the requirement is ambiguous, ask for a clarification rather than guessing.</p>
    </div>

    <h4>Current automation code</h4>
    <textarea id="round4-pilot-code" class="code-textarea round4-pilot-code">${escapeHtml(s.pilot_code || "")}</textarea>
    <p class="muted" style="margin:0.3rem 0 1rem 0">Edit this directly, or ask the assistant for a narrow change below - Run and Submit always use exactly what's in this box.</p>

    <h4>Run</h4>
    <div class="row" style="margin-bottom:0.5rem">
      <button id="round4-pilot-run-btn" onclick="round4PilotRunClicked()">Run</button>
    </div>
    <div id="round4-pilot-run-result">${renderRound4PilotRunResultHtml(s.pilot_last_run)}</div>

    <h4 style="margin-top:1.25rem">Ask the assistant</h4>
    <div class="field-row">
      <div class="field">
        <textarea id="round4-pilot-prompt" rows="2" placeholder="e.g. Explain what ApiHelper.submit_transaction does, or: add a test that automates the transaction flow for a valid customer, reusing the existing helpers."></textarea>
      </div>
      <button id="round4-pilot-ask-btn" onclick="round4PilotAskAIClicked()">Ask AI</button>
    </div>

    <h4 style="margin-top:1.25rem">Requirement clarification</h4>
    ${s.pilot_clarification ? `
      <div class="panel-inset" style="margin-bottom:0.5rem">
        <p style="margin:0 0 0.35rem 0"><strong>You asked:</strong> ${escapeHtml(s.pilot_clarification.question)}</p>
        <p style="margin:0">${escapeHtml(s.pilot_clarification.response)}</p>
      </div>
    ` : `<p class="muted">You haven't asked for a clarification yet.</p>`}
    <div class="field-row">
      <div class="field">
        <input id="round4-pilot-clarify-input" type="text" maxlength="2000" placeholder="e.g. What does &quot;persisted correctly&quot; mean here?" />
      </div>
      <button id="round4-pilot-clarify-btn" onclick="round4PilotClarifyClicked()">Clarify Requirement</button>
    </div>

    <h4 style="margin-top:1.25rem">Conversation history</h4>
    <div id="round4-pilot-turns">${renderRound4PilotTurnsHtml()}</div>

    <div class="row" style="margin-top:1.25rem">
      <button id="round4-pilot-submit-btn" class="btn-block" onclick="round4PilotSubmitClicked()">Submit Round 2</button>
    </div>
    <p id="round4-pilot-status" class="muted"></p>
  `;
}

// Shared by Run/Ask AI/Clarify: call the endpoint, then re-fetch full
// state and re-render - simpler and less bug-prone than patching three
// different DOM regions with three different response shapes, and
// round4State (source of truth for the next render) never drifts from
// what the server actually persisted. Submit is handled separately
// below since a successful submit ends the round instead of re-rendering it.
async function round4PilotAction(actionFn) {
  if (round4PilotBusy) return;
  round4PilotBusy = true;
  round4PilotSetBusy(true);
  const statusEl = document.getElementById("round4-pilot-status");
  if (statusEl) statusEl.textContent = "";
  try {
    await actionFn();
    round4State = await api("/candidate/round/2/state");
    renderRound4PilotLayout(document.getElementById("round-view"));
  } catch (e) {
    const s = document.getElementById("round4-pilot-status");
    if (s) s.textContent = e.message;
  } finally {
    round4PilotBusy = false;
  }
}

function round4PilotRunClicked() {
  const code = document.getElementById("round4-pilot-code").value;
  round4PilotAction(() => api("/candidate/round/2/pilot/run", { method: "POST", body: JSON.stringify({ code }) }));
}

function round4PilotAskAIClicked() {
  const input = document.getElementById("round4-pilot-prompt");
  const prompt = input.value.trim();
  if (!prompt) return;
  round4PilotAction(() => api("/candidate/round/2/pilot/turn", { method: "POST", body: JSON.stringify({ candidate_prompt: prompt }) }));
}

function round4PilotClarifyClicked() {
  const input = document.getElementById("round4-pilot-clarify-input");
  const question = input.value.trim();
  if (!question) return;
  round4PilotAction(() => api("/candidate/round/2/pilot/clarify", { method: "POST", body: JSON.stringify({ question }) }));
}

async function round4PilotSubmitClicked() {
  if (round4PilotBusy) return;
  round4PilotBusy = true;
  round4PilotSetBusy(true);
  const statusEl = document.getElementById("round4-pilot-status");
  if (statusEl) statusEl.textContent = "";
  const code = document.getElementById("round4-pilot-code").value;
  try {
    await api("/candidate/round/2/pilot/submit", { method: "POST", body: JSON.stringify({ code }) });
    stopTimer();
    disarmTabGuard();
    refreshCandidateNav();
  } catch (e) {
    // Round still in progress (e.g. "Make some changes before submitting.") -
    // timer/guard must stay engaged, same as legacy round4Submit's own catch.
    if (statusEl) statusEl.textContent = e.message;
    round4PilotBusy = false;
    round4PilotSetBusy(false);
  }
}

// ==================== AI-Assisted Test Automation (round 4 slot) ====================
// The candidate automates the test cases THEY designed in round 1 - see
// routers/candidate.py's /round/4/auto/* endpoints. Deliberately its own
// render/action functions, separate from both the legacy round 4 UI and
// the pilot UI above, same reasoning the backend already uses to keep the
// three modes apart. Backend stays authoritative throughout: no scoring,
// policy or selection rule is duplicated here.

let round4AutoState = null;
let round4AutoBusy = false;

function round4AutoSetBusy(busy) {
  round4AutoBusy = busy;
  ["r4a-lock-language-btn", "r4a-automate-btn", "r4a-submit-btn"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = busy;
  });
  // ask/save/run buttons are one-per-selected-TC (see round4AutoTcSectionHtml),
  // so they're classes, not unique ids.
  document.querySelectorAll(".r4a-ask-btn, .r4a-save-btn, .r4a-run-btn, .r4a-test-data-btn").forEach((el) => { el.disabled = busy; });
}

async function loadRound4Automation(box) {
  try {
    round4AutoState = await api("/candidate/round/2/auto/state");
  } catch (e) {
    box.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
    return;
  }
  renderRound4AutomationLayout(box);
}

// Read-only reference table of every Round 1 test case, mirroring
// Round 1's own entry table exactly (see renderEntryForm) - SI.No/Title/
// Preconditions/Steps/Test data/Expected result, no checkboxes or picks
// here at all. Selection itself happens via round4AutoNextPickHtml's
// dropdown, one test case at a time.
function round4AutoTcTableHtml(rows, selectedIndexes) {
  if (rows.length === 0) return `<p class="muted">No Round 1 test cases found.</p>`;
  const selectedSet = new Set(selectedIndexes || []);
  return rows.map((r) => `
    <div class="tc-card${selectedSet.has(r.index) ? " is-selected" : ""}">
      <div class="tc-card-head">
        <div class="tc-card-title-group">
          <span class="tc-card-num">${r.index}</span>
          <span class="tc-card-title">${escapeHtml(r.title || "(untitled)")}</span>
        </div>
        ${selectedSet.has(r.index) ? `<span class="tc-card-selected-tag">Being automated</span>` : ""}
      </div>
      <div class="tc-card-grid">
        <div>
          <div class="tc-card-field-label">Preconditions</div>
          <div class="tc-card-field-value">${escapeHtml(r.preconditions || "-")}</div>
        </div>
        <div>
          <div class="tc-card-field-label">Steps</div>
          <div class="tc-card-field-value">${escapeHtml(r.steps || "-")}</div>
        </div>
        <div>
          <div class="tc-card-field-label">Test Data</div>
          <div class="tc-card-field-value">${escapeHtml(r.test_data || "-")}</div>
        </div>
        <div>
          <div class="tc-card-field-label">Expected Result</div>
          <div class="tc-card-field-value">${escapeHtml(r.expected_result || "-")}</div>
        </div>
      </div>
    </div>`).join("");
}

// The dropdown that starts automating one more test case (see
// round4AutoAutomateClicked) - only ever offers rows not already in
// round4AutoState.selected. Shown by renderRound4AutomationLayout: once
// with every test case when nothing's picked yet, and again after the
// most recently added test case has a run result, as long as the
// two-test-case cap hasn't been reached and something's left to offer.
function round4AutoNextPickHtml(remaining) {
  return `
    <div class="surface" style="margin:var(--space-default) 0; border-style:dashed">
      <div class="field-label" style="margin-bottom:0.4rem">Automate a test case</div>
      <div class="field-row" style="align-items:flex-end">
        <div class="field" style="flex:1 1 18rem">
          <select id="r4a-next-pick-select">
            <option value="" selected>Select...</option>
            ${remaining.map((r) => `<option value="${r.index}">${escapeHtml(r.title || `Test case ${r.index}`)}</option>`).join("")}
          </select>
        </div>
        <button class="btn-primary" id="r4a-automate-btn" onclick="round4AutoAutomateClicked()">Automate this test case</button>
      </div>
    </div>`;
}

function round4AutoAutomateClicked() {
  const val = document.getElementById("r4a-next-pick-select").value;
  const statusEl = document.getElementById("r4a-status");
  if (!val) {
    if (statusEl) statusEl.textContent = "Select a test case first.";
    return;
  }
  round4AutoAction(() => api("/candidate/round/2/auto/select", { method: "POST", body: JSON.stringify({ row_indexes: [Number(val)] }) }));
}

function round4AutoRunResultHtml(rowIndex, run) {
  if (!run) return `<p class="muted">Not run yet.</p>`;
  const passed = run.exit_code === 0 && !run.timed_out && !run.infra_error;
  const meta = [
    `Exit code: ${run.exit_code === null || run.exit_code === undefined ? "-" : run.exit_code}`,
    run.duration_ms !== null && run.duration_ms !== undefined ? `${run.duration_ms}ms` : null,
    run.ran_at ? new Date(run.ran_at).toLocaleString() : null,   // "where available" - absent on a run recorded before this field existed
  ].filter(Boolean).join(" · ");
  return `
    <div class="result-state ${passed ? "is-pass" : "is-fail"}">
      <span class="result-state-icon">${passed ? "✓" : "✕"}</span>
      <div class="result-state-body">
        <div class="result-state-label">${passed ? "PASS" : "FAIL"} - Test case ${rowIndex}</div>
        <p class="result-state-detail">${meta}</p>
        ${run.timed_out ? `<p class="result-state-detail">Timed out.</p>` : ""}
        ${run.infra_error ? `<p class="result-state-detail">The execution service had a problem - try running again.</p>` : ""}
        ${passed ? `<p class="result-state-caveat">PASS does not necessarily mean correct - check what was actually verified.</p>` : ""}
        <details style="margin-top:0.6rem">
          <summary>Execution log</summary>
          ${run.stdout ? `<pre class="code-snippet">${escapeHtml(run.stdout)}</pre>` : `<p class="muted">No stdout.</p>`}
          ${run.stderr ? `<pre class="code-snippet round3-coding-stderr">${escapeHtml(run.stderr)}</pre>` : ""}
        </details>
      </div>
    </div>`;
}

function round4AutoTurnsHtml(turns) {
  if (!turns || turns.length === 0) return `<p class="muted">No messages yet.</p>`;
  return `<div class="ai-thread">${turns.map((t) => `
    <div class="ai-turn ai-turn-candidate">
      <div class="ai-turn-role">You</div>
      <div class="ai-turn-body">${escapeHtml(t.candidate_prompt)}</div>
    </div>
    <div class="ai-turn ai-turn-assistant">
      <div class="ai-turn-role">Assistant &middot; ${escapeHtml(t.response_kind)}</div>
      <div class="ai-turn-body">${escapeHtml(t.response_message)}</div>
    </div>`).join("")}</div>`;
}

// Each selected test case (1-2) has its own independent automation state
// (see Round4AutoTCStateOut) - code, AI conversation, run result and
// interpretation, none of it shared with the other selected test case.
// Every selected test case's own section stacks permanently in the DOM,
// always visible (no tabs, nothing hidden) - the candidate can return to
// an earlier one at any time to keep fixing/rerunning it (see
// renderRound4AutomationLayout, which decides when a NEW section for a
// not-yet-automated test case should appear below the rest).

function round4AutoTcState(rowIndex) {
  return (round4AutoState.tc_state || []).find((t) => t.row_index === rowIndex)
    || { code: "", turns: [], code_edits_count: 0, last_run: null, validation: "" };
}

function round4AutoTcIsUnlocked(tc) {
  // Permanent once true - matches the backend's own derivation (see
  // routers/candidate.py's _tc_is_unlocked): the assistant has produced
  // its first code_edit for this test case, via a prompt it already
  // judged complete enough to encode without guessing.
  return (tc.turns || []).some((t) => t.response_kind === "code_edit");
}

// Editable test data, separate from the immutable Round 1 table above -
// lets the candidate correct their OWN mistake (a typo, a stale value)
// for automation purposes without touching the Round 1 record or the
// original design snapshot (see routers/candidate.py's
// round4_auto_update_test_data / scoring_service._auto_tc_design_only,
// which keeps the original alongside for transparency once corrected).
function round4AutoTestDataHtml(row) {
  return `
    <div class="surface" style="margin:0.85rem 0">
      <div class="field-label" style="margin-bottom:0.3rem">Test data</div>
      <p class="text-muted" style="margin:0 0 0.6rem 0">What your automation runs against. Made a mistake in Round 1? Correct it here - your original Round 1 answer is never changed.</p>
      <div class="field-row">
        <div class="field" style="flex:1 1 20rem">
          <textarea id="r4a-test-data-${row.index}" rows="2">${escapeHtml(row.test_data || "")}</textarea>
        </div>
        <button class="btn-secondary r4a-test-data-btn" onclick="round4AutoSaveTestDataClicked(${row.index})">Save test data</button>
      </div>
    </div>`;
}

function round4AutoSaveTestDataClicked(rowIndex) {
  const testData = document.getElementById(`r4a-test-data-${rowIndex}`).value.trim();
  if (!testData) return;
  round4AutoAction(() => api("/candidate/round/2/auto/test-data", { method: "POST", body: JSON.stringify({ row_index: rowIndex, test_data: testData }) }));
}

// Reference material - the app's own screens and test-environment facts,
// reused unmodified from whatever the scenario already generated (see
// Round4AutoStateOut.ui_mockup/environment) - never code, never the
// solution. Shown ONCE at the page level, right alongside the Round 1
// table (see renderRound4AutomationLayout) - it's the same reference
// regardless of which selected test case is being worked on, so
// repeating it inside every test case's own section was just noise, not
// scoped information. Never hidden either way - visible before any test
// case is even picked, and permanently after.
// Segmented into tabs (one pane visible at a time) rather than two
// stacked <details> blocks - same underlying data (Round4AutoStateOut.
// ui_mockup/environment), just presented so only the section the
// candidate actually needs right now is on screen. Switching is pure
// client-side visibility (round4AutoReferenceTabClicked below) - never
// re-fetches or re-derives anything.
function round4AutoReferenceHtml() {
  const s = round4AutoState;
  const hasMockup = !!s.ui_mockup;
  const hasEnv = !!(s.environment && Object.keys(s.environment.fields || {}).length > 0);
  if (!hasMockup && !hasEnv) return "";
  const envFields = hasEnv ? Object.entries(s.environment.fields || {}) : [];
  return `
    <div class="surface">
      <div class="section-header"><h3>Reference material</h3></div>
      <div class="tabs" role="tablist">
        ${hasMockup ? `<button class="tab active" data-r4a-ref-tab="screens" onclick="round4AutoReferenceTabClicked('screens')">Reference Screens</button>` : ""}
        ${hasEnv ? `<button class="tab${hasMockup ? "" : " active"}" data-r4a-ref-tab="environment" onclick="round4AutoReferenceTabClicked('environment')">Test Environment</button>` : ""}
      </div>
      ${hasMockup ? `
        <div class="tab-panel active" data-r4a-ref-panel="screens">
          ${renderMockupScreens(s.ui_mockup, "r4a-ref-mockup")}
        </div>` : ""}
      ${hasEnv ? `
        <div class="tab-panel${hasMockup ? "" : " active"}" data-r4a-ref-panel="environment">
          <dl class="env-fields">
            ${envFields.map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join("")}
          </dl>
          ${s.environment.notes ? `<p class="text-muted">${escapeHtml(s.environment.notes)}</p>` : ""}
        </div>` : ""}
    </div>`;
}

// Pure display toggle - no state fetch, no re-render of anything else.
function round4AutoReferenceTabClicked(name) {
  document.querySelectorAll("[data-r4a-ref-tab]").forEach((btn) => btn.classList.toggle("active", btn.dataset.r4aRefTab === name));
  document.querySelectorAll("[data-r4a-ref-panel]").forEach((panel) => panel.classList.toggle("active", panel.dataset.r4aRefPanel === name));
}

function round4AutoTcSectionHtml(row) {
  const tc = round4AutoTcState(row.index);
  const unlocked = round4AutoTcIsUnlocked(tc);
  // Order matches the actual workflow: prompt first (below), code
  // appears as a result of that and sits right under where the
  // candidate was just typing, then the test data it's about to run
  // against (a "run space", editable right before running - not
  // scattered after the result it's supposed to explain), THEN the Run
  // action, and the result comes last - only after everything it
  // depends on has already been shown.
  const codeSectionHtml = unlocked ? `
      <div class="section-header"><h3>Generated code &middot; your edits</h3></div>
      <div class="code-panel">
        <div class="code-panel-head"><span>Automation code</span><span>Editable - review before you trust a PASS</span></div>
        <div class="code-panel-body">
          <textarea id="r4a-code-${row.index}" class="round4-pilot-code">${escapeHtml(tc.code || "")}</textarea>
        </div>
      </div>
      ${round4AutoTestDataHtml(row)}
      <div class="action-bar" style="border-top:none; margin-top:0; padding-top:0">
        <span class="muted">Run uses exactly what's in the code box above. Your own edits are recorded separately from the assistant's.</span>
        <div class="action-bar-buttons">
          <button class="btn-secondary r4a-save-btn" onclick="round4AutoSaveCodeClicked(${row.index})">Save my edit</button>
          <button class="btn-primary r4a-run-btn" onclick="round4AutoRunClicked(${row.index})">Run</button>
        </div>
      </div>
      <div class="section-header" style="margin-top:var(--space-default)"><h3>Execution result</h3></div>
      <div id="r4a-run-result-${row.index}">${round4AutoRunResultHtml(row.index, tc.last_run)}</div>` : `
      <p class="muted" style="margin-top:1.25rem">No code yet - describe what you want automated above. Once the assistant has enough detail to encode it without guessing, it'll write the first version here.</p>`;

  return `
    <div class="surface r4a-tc-panel" data-row-index="${row.index}" style="margin:var(--space-default) 0">
      <div class="section-header">
        <h2>Test case ${row.index} <span class="muted" style="font-weight:400">- ${escapeHtml(row.title || "(untitled)")}</span></h2>
      </div>
      ${(row.refinements || []).length > 0 ? `<p class="muted" style="margin:0 0 0.5rem 0"><strong>Your refinement notes:</strong> ${row.refinements.map((n) => escapeHtml(n)).join(" &middot; ")}</p>` : ""}
      <p class="muted" style="margin:0 0 var(--space-compact) 0">AI-generated code may be buggy, incomplete, or subtly wrong even when it runs cleanly - review it before trusting a PASS.</p>

      <div class="section-header"><h3>Your instruction &middot; AI conversation</h3></div>
      <div class="r4a-chat-log">${round4AutoTurnsHtml(tc.turns)}</div>
      <div class="field-row" style="margin-top:0.5rem; align-items:flex-start">
        <div class="field" style="flex:1 1 20rem">
          <textarea id="r4a-prompt-${row.index}" rows="2" placeholder="e.g. Encode step 2 of this test case using UI.login, asserting the expected result I wrote."></textarea>
        </div>
        <button class="btn-primary r4a-ask-btn" onclick="round4AutoAskClicked(${row.index})">Ask AI</button>
      </div>

      ${codeSectionHtml}
    </div>`;
}

function renderRound4AutomationLayout(box) {
  const s = round4AutoState;
  const scenario = round4State.scenario;

  if (!s.language_locked) {
    box.innerHTML = `
      <div class="page-header">
        <span class="eyebrow">Round 2 &middot; AI-Assisted Test Automation</span>
        <h1>${escapeHtml(scenario.title)}</h1>
      </div>
      <div class="surface">${formatScenarioDescription(scenario.description)}</div>
      <div class="surface">
        <div class="field-label" style="margin-bottom:0.3rem">Programming language</div>
        <p class="text-muted" style="margin:0 0 var(--space-compact) 0">Choose the language you'll automate in. This is locked for the rest of Round 2 and carries into Round 3 - you won't be asked again.</p>
        <div class="field-row" style="align-items:center">
          <select id="r4a-language-select" style="max-width:16rem">
            <option value="" selected>Select a language...</option>
            <option value="python">Python</option>
            <option value="java">Java</option>
            <option value="javascript">JavaScript</option>
          </select>
          <button class="btn-primary" id="r4a-lock-language-btn" onclick="round4AutoLockLanguageClicked()">Lock language</button>
        </div>
        <p id="r4a-status" class="muted"></p>
      </div>`;
    return;
  }

  // No more "pick both, then confirm" screen - test cases are selected
  // one at a time via round4AutoNextPickHtml's dropdown, immutably per
  // pick (see routers/candidate.py's round4_auto_select), so the whole
  // rest of the page renders unconditionally once the language is
  // locked, whether zero, one or two test cases have been picked so far.
  const selected = s.selected || [];
  const remaining = (s.available_rows || []).filter((r) => !selected.some((sel) => sel.index === r.index));
  const lastSelected = selected.length > 0 ? selected[selected.length - 1] : null;
  const lastHasResult = lastSelected ? !!round4AutoTcState(lastSelected.index).last_run : false;
  // The picker shows for the very first test case unconditionally, and
  // again after the most recently added one has a result (pass or fail
  // - "has a result" is the trigger, not whether it's correct yet) - as
  // long as the two-test-case cap isn't reached and something's left.
  const showPicker = remaining.length > 0 && selected.length < 2 && (selected.length === 0 || lastHasResult);

  box.innerHTML = `
    <div class="page-header">
      <span class="eyebrow">Round 2 &middot; AI-Assisted Test Automation</span>
      <h1>${escapeHtml(scenario.title)}</h1>
    </div>
    <div class="surface">
      ${formatScenarioDescription(scenario.description)}
      <div class="row" style="margin-top:var(--space-compact); margin-bottom:0">
        <span class="tag tag-accent">Language locked: ${escapeHtml(s.language)}</span>
      </div>
      <p class="text-muted" style="margin:var(--space-compact) 0 0">Pick a test case below to automate it - your original design is kept exactly as you wrote it, and the assistant will only encode what you specify, never inventing test cases, data or assertions. Review everything it writes, edit the code yourself where you disagree, and run it. You can automate up to two, one at a time - you'll decide on a second only after finishing the first.</p>
    </div>

    <div class="section-header"><h2>Your Round 1 test cases</h2></div>
    ${round4AutoTcTableHtml(s.available_rows || [], selected.map((r) => r.index))}
    ${round4AutoReferenceHtml()}

    ${selected.map((r) => round4AutoTcSectionHtml(r)).join("")}

    ${showPicker ? round4AutoNextPickHtml(remaining) : ""}

    ${selected.length > 0 ? `
    <div class="action-bar">
      <span class="muted">Submitting ends Round 2 and moves you on - you can't return to it afterward.</span>
      <div class="action-bar-buttons">
        <button id="r4a-submit-btn" class="btn-primary" onclick="round4AutoSubmitClicked()">Submit Round 2</button>
      </div>
    </div>` : ""}
    <p id="r4a-status" class="muted"></p>`;
}

// Shared by every action except submit: call the endpoint, re-fetch state,
// re-render. Same reasoning as the pilot's round4PilotAction - the server
// is the single source of truth for what actually persisted.
async function round4AutoAction(actionFn) {
  if (round4AutoBusy) return;
  round4AutoBusy = true;
  round4AutoSetBusy(true);
  const statusEl = document.getElementById("r4a-status");
  if (statusEl) statusEl.textContent = "";
  try {
    await actionFn();
    round4AutoState = await api("/candidate/round/2/auto/state");
    renderRound4AutomationLayout(document.getElementById("round-view"));
  } catch (e) {
    const el = document.getElementById("r4a-status");
    if (el) el.textContent = e.message;
  } finally {
    round4AutoBusy = false;
    round4AutoSetBusy(false);
  }
}

function round4AutoLockLanguageClicked() {
  const language = document.getElementById("r4a-language-select").value;
  const statusEl = document.getElementById("r4a-status");
  if (!language) {
    if (statusEl) statusEl.textContent = "Pick a language first.";
    return;
  }
  round4AutoAction(() => api("/candidate/round/2/auto/language", { method: "POST", body: JSON.stringify({ language }) }));
}

function round4AutoAskClicked(rowIndex) {
  const promptEl = document.getElementById(`r4a-prompt-${rowIndex}`);
  const prompt = promptEl.value.trim();
  if (!prompt) return;
  round4AutoAction(() => api("/candidate/round/2/auto/turn", { method: "POST", body: JSON.stringify({ candidate_prompt: prompt, row_index: rowIndex }) }));
}

function round4AutoSaveCodeClicked(rowIndex) {
  const code = document.getElementById(`r4a-code-${rowIndex}`).value;
  if (!code) return;
  round4AutoAction(() => api("/candidate/round/2/auto/code", { method: "POST", body: JSON.stringify({ code, row_index: rowIndex }) }));
}

function round4AutoRunClicked(rowIndex) {
  const code = document.getElementById(`r4a-code-${rowIndex}`).value;
  round4AutoAction(() => api("/candidate/round/2/auto/run", { method: "POST", body: JSON.stringify({ code, row_index: rowIndex }) }));
}

async function round4AutoSubmitClicked() {
  if (round4AutoBusy) return;
  const selected = round4AutoState.selected || [];
  const statusEl = document.getElementById("r4a-status");

  // Every selected test case needs its own generated code and its own
  // run - gathered independently per TC, not one shared check for the
  // whole round (see Round4AutoSubmitCreate.entries). No candidate-
  // written interpretation required: whether the run genuinely proves
  // the expected result is scored from the code and result alone.
  const entries = [];
  for (const r of selected) {
    const sectionEl = document.querySelector(`.r4a-tc-panel[data-row-index="${r.index}"]`);
    const tc = round4AutoTcState(r.index);
    if (!round4AutoTcIsUnlocked(tc)) {
      if (sectionEl) sectionEl.scrollIntoView({ behavior: "smooth", block: "center" });
      if (statusEl) statusEl.textContent = `Ask the assistant to generate code for "${r.title || `test case ${r.index}`}" before submitting.`;
      return;
    }
    if (!tc.last_run) {
      if (sectionEl) sectionEl.scrollIntoView({ behavior: "smooth", block: "center" });
      if (statusEl) statusEl.textContent = `Run "${r.title || `test case ${r.index}`}" at least once before submitting.`;
      return;
    }
    entries.push({ row_index: r.index, code: document.getElementById(`r4a-code-${r.index}`).value });
  }

  round4AutoBusy = true;
  round4AutoSetBusy(true);
  if (statusEl) statusEl.textContent = "";
  try {
    await api("/candidate/round/2/auto/submit", { method: "POST", body: JSON.stringify({ entries }) });
    stopTimer();
    disarmTabGuard();
    refreshCandidateNav();
  } catch (e) {
    if (statusEl) statusEl.textContent = e.message;
    round4AutoBusy = false;
    round4AutoSetBusy(false);
  }
}

// ==================== Round 5 (Progressive Engineering) POC ====================
// HR authoring UI only (Phase 7) - talks to routers/progressive.py's
// hr_router, which is a brand-new router (see main.py) not merged into
// this file's existing scenario-authoring functions above. Deliberately
// reuses the same design-system classes those already use (.panel/.card,
// .scenario-kpis/.kpi-card, .badge-draft/.badge-published, .btn-*,
// .field-hint-text/.required-mark/.char-counter, .table-scroll) rather
// than inventing new component styles - see style.css's own "Round 5"
// section for the handful of genuinely new pieces (the stage lifecycle
// stepper, stage cards) this page needed on top of that.
//
// "Review" in the Draft -> Review -> Published/Frozen lifecycle shown
// here is a UI-presented step, not a stored backend status - the
// backend (models_progressive.ProgressiveProblemStatus) only has
// draft/published. Opening a draft problem's detail panel to look it
// over before publishing IS the review step; nothing new was added to
// the backend to track it separately, per this phase's own "don't
// invent backend capabilities that don't exist" instruction.

let lastLoadedProgressiveProblems = [];
let currentProgressiveProblem = null;      // the problem currently open in the detail panel, or null
let currentProgressiveStages = [];         // that problem's requirements, most-recently-fetched

function updateProgressiveCreateBtnState() {
  const title = document.getElementById("pp-title").value.trim();
  const description = document.getElementById("pp-description").value.trim();
  const language = document.getElementById("pp-language").value.trim();
  document.getElementById("pp-title-counter").textContent = `${document.getElementById("pp-title").value.length}/300`;
  document.getElementById("pp-description-counter").textContent = `${document.getElementById("pp-description").value.length}/20000`;
  document.getElementById("pp-create-btn").disabled = !(title && description && language);
}

function resetProgressiveCreateForm() {
  document.getElementById("pp-title").value = "";
  document.getElementById("pp-description").value = "";
  document.getElementById("pp-input-spec").value = "";
  document.getElementById("pp-language").value = "";
  document.getElementById("pp-difficulty").value = "";
  document.getElementById("pp-time-limit").value = "60";
  document.getElementById("pp-starter-code").value = "";
  document.getElementById("progressive-create-status").textContent = "";
  updateProgressiveCreateBtnState();
}

async function createProgressiveProblem() {
  const statusEl = document.getElementById("progressive-create-status");
  const inputSpecRaw = document.getElementById("pp-input-spec").value.trim();
  let inputSpecJson = {};
  if (inputSpecRaw) {
    try {
      inputSpecJson = JSON.parse(inputSpecRaw);
    } catch (e) {
      // Not valid JSON - treated as a plain free-text description
      // instead of rejecting it outright, since the field is explicitly
      // "any format is fine" (a log format, a data shape in prose, etc.).
      inputSpecJson = { description: inputSpecRaw };
    }
  }
  const btn = document.getElementById("pp-create-btn");
  btn.disabled = true;
  statusEl.textContent = "Creating...";
  try {
    const problem = await api("/hr/progressive/problems", {
      method: "POST",
      body: JSON.stringify({
        title: document.getElementById("pp-title").value.trim(),
        description: document.getElementById("pp-description").value.trim(),
        input_spec_json: inputSpecJson,
        language: document.getElementById("pp-language").value.trim(),
        starter_code: document.getElementById("pp-starter-code").value || null,
        difficulty: document.getElementById("pp-difficulty").value.trim() || null,
        time_limit_minutes: parseInt(document.getElementById("pp-time-limit").value, 10) || 60,
      }),
    });
    statusEl.textContent = `Created draft #${problem.id}: ${problem.title}`;
    resetProgressiveCreateForm();
    await loadProgressiveProblems();
    openProgressiveProblemDetail(problem.id);
  } catch (e) {
    statusEl.textContent = e.message;
  } finally {
    updateProgressiveCreateBtnState();
  }
}

async function loadProgressiveProblems() {
  lastLoadedProgressiveProblems = await api("/hr/progressive/problems");
  renderProgressiveKpis();
  renderProgressiveList();
}

function renderProgressiveKpis() {
  const box = document.getElementById("progressive-kpis");
  const total = lastLoadedProgressiveProblems.length;
  const published = lastLoadedProgressiveProblems.filter((p) => p.status === "published").length;
  const draft = total - published;
  const kpis = [
    { label: "Total Problems", value: total, caption: "Across all statuses", icon: "file", cls: "kpi-icon-accent" },
    { label: "Published & Frozen", value: published, caption: "Live definition, locked", icon: "check", cls: "kpi-icon-success" },
    { label: "Draft", value: draft, caption: "Still being authored", icon: "file", cls: "kpi-icon-warning" },
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

function renderProgressiveList() {
  const box = document.getElementById("progressive-list");
  if (lastLoadedProgressiveProblems.length === 0) {
    box.innerHTML = `<div class="empty-state">No progressive problems yet - create one to get started.</div>`;
    return;
  }
  box.innerHTML = `
    <div class="table-scroll">
      <table class="scenario-table-el">
        <thead><tr><th>#</th><th>Title</th><th>Language</th><th>Status</th><th>Time</th><th>Actions</th></tr></thead>
        <tbody>${lastLoadedProgressiveProblems.map((p) => `
          <tr>
            <td class="tabular">${p.id}</td>
            <td class="scenario-title-cell">${escapeHtml(p.title)}</td>
            <td>${escapeHtml(p.language)}</td>
            <td><span class="badge badge-${p.status}">${p.status === "published" ? "Published &amp; Frozen" : "Draft"}</span></td>
            <td class="tabular">${p.time_limit_minutes}</td>
            <td><button class="btn-primary btn-sm" onclick="openProgressiveProblemDetail(${p.id})">Review</button></td>
          </tr>
        `).join("")}</tbody>
      </table>
    </div>
  `;
}

async function openProgressiveProblemDetail(problemId) {
  const box = document.getElementById("progressive-detail");
  box.classList.remove("hidden");
  box.innerHTML = `<p class="muted">Loading...</p>`;
  try {
    const [problem, stages] = await Promise.all([
      api(`/hr/progressive/problems/${problemId}`),
      api(`/hr/progressive/problems/${problemId}/stages`),
    ]);
    currentProgressiveProblem = problem;
    currentProgressiveStages = stages;
    renderProgressiveDetail();
  } catch (e) {
    box.innerHTML = `<p class="error-text">${escapeHtml(e.message)}</p>`;
  }
}

function closeProgressiveProblemDetail() {
  currentProgressiveProblem = null;
  currentProgressiveStages = [];
  document.getElementById("progressive-detail").classList.add("hidden");
}

function renderProgressiveDetail() {
  const p = currentProgressiveProblem;
  const box = document.getElementById("progressive-detail");
  const isDraft = p.status === "draft";

  box.innerHTML = `
    <div class="row" style="align-items:center; justify-content:space-between">
      <h3 style="margin:0">#${p.id} - ${escapeHtml(p.title)} <span class="badge badge-${p.status}">${isDraft ? "Draft" : "Published &amp; Frozen"}</span></h3>
      <button class="btn-ghost" onclick="closeProgressiveProblemDetail()">Close</button>
    </div>

    <ol class="lifecycle-stepper">
      <li class="${isDraft ? "current" : "done"}">Draft</li>
      <li class="${isDraft ? "current" : "done"}">Review</li>
      <li class="${isDraft ? "" : "current"}">Published &amp; Frozen</li>
    </ol>

    <h4>Problem</h4>
    ${isDraft ? `
      <label for="pp-edit-title">Title</label>
      <input id="pp-edit-title" value="${escapeHtml(p.title)}" maxlength="300" />
      <label for="pp-edit-description">Description</label>
      <textarea id="pp-edit-description" maxlength="20000">${escapeHtml(p.description)}</textarea>
      <label for="pp-edit-time-limit">Time limit (minutes)</label>
      <input id="pp-edit-time-limit" type="number" min="1" value="${p.time_limit_minutes}" />
      <div class="row create-scenario-actions">
        <button class="btn-primary btn-sm" onclick="saveProgressiveProblemEdit(${p.id})">Save changes</button>
      </div>
    ` : `
      <p><strong>Description:</strong></p>
      ${formatScenarioDescription(p.description)}
      <p class="muted">Time limit is locked at ${p.time_limit_minutes} minutes - it can't change once published. Language: ${escapeHtml(p.language)}${p.difficulty ? ` &middot; Difficulty: ${escapeHtml(p.difficulty)}` : ""}</p>
    `}

    <h4>Progressive Requirements (Stages)</h4>
    <p class="muted">${isDraft ? "Add, edit, and reorder stages while this problem is still a draft." : "Frozen - every stage below is locked exactly as published."}</p>
    <div id="progressive-stages-list">${renderProgressiveStageCards()}</div>

    ${isDraft ? `
      <div class="row create-scenario-actions">
        <button class="btn-secondary btn-sm" onclick="showAddProgressiveStageForm()">+ Add Stage</button>
        <button class="btn-primary btn-sm" onclick="publishProgressiveProblem(${p.id})" ${currentProgressiveStages.length === 0 ? "disabled title=\"Add at least one stage first\"" : ""}>Publish &amp; Freeze</button>
      </div>
      <div id="progressive-add-stage-form"></div>
    ` : ""}
    <p id="progressive-detail-status" class="muted"></p>
  `;
}

function renderProgressiveStageCards() {
  if (currentProgressiveStages.length === 0) {
    return `<div class="empty-state">No stages defined yet.</div>`;
  }
  const isDraft = currentProgressiveProblem.status === "draft";
  const sorted = [...currentProgressiveStages].sort((a, b) => a.stage_order - b.stage_order);
  return sorted.map((stage, i) => `
    <div class="panel stage-card" id="stage-card-${stage.id}">
      <div class="row" style="align-items:center; justify-content:space-between">
        <h4 style="margin:0">Stage ${stage.stage_order}${stage.frozen ? ' <span class="badge badge-published">Frozen</span>' : ""}</h4>
        ${isDraft ? `
          <div class="row-actions">
            <button class="btn-ghost btn-sm" ${i === 0 ? "disabled" : ""} onclick="moveProgressiveStage(${stage.id}, -1)" aria-label="Move up">&uarr;</button>
            <button class="btn-ghost btn-sm" ${i === sorted.length - 1 ? "disabled" : ""} onclick="moveProgressiveStage(${stage.id}, 1)" aria-label="Move down">&darr;</button>
            <button class="btn-secondary btn-sm" onclick="toggleProgressiveStageEdit(${stage.id})">Edit</button>
          </div>
        ` : ""}
      </div>
      <div id="stage-view-${stage.id}">
        <p><strong>Requirement:</strong> ${escapeHtml(stage.requirement_text)}</p>
        ${stage.expected_behavior ? `<p><strong>Expected behavior:</strong> ${escapeHtml(stage.expected_behavior)}</p>` : ""}
        <p><strong>Hidden tests:</strong> ${stage.hidden_tests_json ? stage.hidden_tests_json.length : 0} defined</p>
        ${stage.reference_solution ? `<p><strong>Reference solution:</strong></p><pre class="code-preview">${escapeHtml(stage.reference_solution)}</pre>` : `<p class="muted">No reference solution yet.</p>`}
      </div>
      <div id="stage-edit-${stage.id}" class="hidden"></div>
    </div>
  `).join("");
}

function showAddProgressiveStageForm() {
  const nextOrder = currentProgressiveStages.length
    ? Math.max(...currentProgressiveStages.map((s) => s.stage_order)) + 1
    : 1;
  document.getElementById("progressive-add-stage-form").innerHTML = `
    <div class="panel stage-card">
      <h4>New Stage ${nextOrder}</h4>
      <label for="new-stage-requirement">Requirement <span class="required-mark">*</span></label>
      <textarea id="new-stage-requirement" placeholder="What should the candidate build or change for this stage?"></textarea>
      <label for="new-stage-expected">Expected behavior</label>
      <textarea id="new-stage-expected" placeholder="What should the solution do once this stage is correctly implemented?"></textarea>
      <label for="new-stage-hidden-tests">Hidden tests (JSON array of {"input", "expected_output"})</label>
      <textarea id="new-stage-hidden-tests" class="code-textarea" placeholder='[{"input": "...", "expected_output": "..."}]'></textarea>
      <label for="new-stage-reference">Reference solution</label>
      <textarea id="new-stage-reference" class="code-textarea" placeholder="HR-only - never shown to the candidate."></textarea>
      <div class="row create-scenario-actions">
        <button class="btn-secondary btn-sm" onclick="document.getElementById('progressive-add-stage-form').innerHTML=''">Cancel</button>
        <button class="btn-primary btn-sm" onclick="submitNewProgressiveStage(${nextOrder})">Add Stage</button>
      </div>
      <p id="new-stage-status" class="muted"></p>
    </div>
  `;
}

function _parseHiddenTestsInput(rawText, statusElId) {
  if (!rawText.trim()) return [];
  try {
    const parsed = JSON.parse(rawText);
    if (!Array.isArray(parsed)) throw new Error("must be a JSON array");
    return parsed;
  } catch (e) {
    document.getElementById(statusElId).textContent = `Hidden tests must be valid JSON (an array of {"input","expected_output"} objects): ${e.message}`;
    throw e;
  }
}

async function submitNewProgressiveStage(stageOrder) {
  let hiddenTests;
  try {
    hiddenTests = _parseHiddenTestsInput(document.getElementById("new-stage-hidden-tests").value, "new-stage-status");
  } catch (e) {
    return;
  }
  const requirementText = document.getElementById("new-stage-requirement").value.trim();
  if (!requirementText) {
    document.getElementById("new-stage-status").textContent = "Requirement text is required.";
    return;
  }
  try {
    await api(`/hr/progressive/problems/${currentProgressiveProblem.id}/requirements`, {
      method: "POST",
      body: JSON.stringify({
        stage_order: stageOrder,
        requirement_text: requirementText,
        expected_behavior: document.getElementById("new-stage-expected").value.trim() || null,
        hidden_tests_json: hiddenTests,
        reference_solution: document.getElementById("new-stage-reference").value || null,
      }),
    });
    document.getElementById("progressive-add-stage-form").innerHTML = "";
    currentProgressiveStages = await api(`/hr/progressive/problems/${currentProgressiveProblem.id}/stages`);
    renderProgressiveDetail();
  } catch (e) {
    document.getElementById("new-stage-status").textContent = e.message;
  }
}

function toggleProgressiveStageEdit(stageId) {
  const stage = currentProgressiveStages.find((s) => s.id === stageId);
  const viewEl = document.getElementById(`stage-view-${stageId}`);
  const editEl = document.getElementById(`stage-edit-${stageId}`);
  const opening = editEl.classList.contains("hidden");
  if (!opening) {
    editEl.classList.add("hidden");
    editEl.innerHTML = "";
    viewEl.classList.remove("hidden");
    return;
  }
  viewEl.classList.add("hidden");
  editEl.classList.remove("hidden");
  editEl.innerHTML = `
    <label for="edit-stage-requirement-${stageId}">Requirement</label>
    <textarea id="edit-stage-requirement-${stageId}">${escapeHtml(stage.requirement_text)}</textarea>
    <label for="edit-stage-expected-${stageId}">Expected behavior</label>
    <textarea id="edit-stage-expected-${stageId}">${escapeHtml(stage.expected_behavior || "")}</textarea>
    <label for="edit-stage-hidden-tests-${stageId}">Hidden tests (JSON)</label>
    <textarea id="edit-stage-hidden-tests-${stageId}" class="code-textarea">${escapeHtml(JSON.stringify(stage.hidden_tests_json || [], null, 2))}</textarea>
    <label for="edit-stage-reference-${stageId}">Reference solution</label>
    <textarea id="edit-stage-reference-${stageId}" class="code-textarea">${escapeHtml(stage.reference_solution || "")}</textarea>
    <div class="row create-scenario-actions">
      <button class="btn-secondary btn-sm" onclick="toggleProgressiveStageEdit(${stageId})">Cancel</button>
      <button class="btn-primary btn-sm" onclick="saveProgressiveStageEdit(${stageId})">Save</button>
    </div>
    <p id="edit-stage-status-${stageId}" class="muted"></p>
  `;
}

async function saveProgressiveStageEdit(stageId) {
  let hiddenTests;
  try {
    hiddenTests = _parseHiddenTestsInput(document.getElementById(`edit-stage-hidden-tests-${stageId}`).value, `edit-stage-status-${stageId}`);
  } catch (e) {
    return;
  }
  try {
    await api(`/hr/progressive/problems/${currentProgressiveProblem.id}/requirements/${stageId}`, {
      method: "PATCH",
      body: JSON.stringify({
        requirement_text: document.getElementById(`edit-stage-requirement-${stageId}`).value.trim(),
        expected_behavior: document.getElementById(`edit-stage-expected-${stageId}`).value.trim() || null,
        hidden_tests_json: hiddenTests,
        reference_solution: document.getElementById(`edit-stage-reference-${stageId}`).value || null,
      }),
    });
    currentProgressiveStages = await api(`/hr/progressive/problems/${currentProgressiveProblem.id}/stages`);
    renderProgressiveDetail();
  } catch (e) {
    document.getElementById(`edit-stage-status-${stageId}`).textContent = e.message;
  }
}

async function moveProgressiveStage(stageId, direction) {
  const sorted = [...currentProgressiveStages].sort((a, b) => a.stage_order - b.stage_order);
  const index = sorted.findIndex((s) => s.id === stageId);
  const swapWith = sorted[index + direction];
  if (!swapWith) return;
  const thisStage = sorted[index];
  try {
    await api(`/hr/progressive/problems/${currentProgressiveProblem.id}/requirements/reorder`, {
      method: "POST",
      body: JSON.stringify({
        order: [
          { requirement_id: thisStage.id, stage_order: swapWith.stage_order },
          { requirement_id: swapWith.id, stage_order: thisStage.stage_order },
        ],
      }),
    });
    currentProgressiveStages = await api(`/hr/progressive/problems/${currentProgressiveProblem.id}/stages`);
    renderProgressiveDetail();
  } catch (e) {
    document.getElementById("progressive-detail-status").textContent = e.message;
  }
}

async function saveProgressiveProblemEdit(problemId) {
  try {
    currentProgressiveProblem = await api(`/hr/progressive/problems/${problemId}`, {
      method: "PATCH",
      body: JSON.stringify({
        title: document.getElementById("pp-edit-title").value.trim(),
        description: document.getElementById("pp-edit-description").value.trim(),
        time_limit_minutes: parseInt(document.getElementById("pp-edit-time-limit").value, 10) || undefined,
      }),
    });
    renderProgressiveDetail();
    loadProgressiveProblems();
  } catch (e) {
    document.getElementById("progressive-detail-status").textContent = e.message;
  }
}

async function publishProgressiveProblem(problemId) {
  const confirmed = confirm(
    `Publish and freeze "${currentProgressiveProblem.title}"? Every stage's requirement, expected behavior, hidden tests, and reference solution become permanently locked - this cannot be undone.`
  );
  if (!confirmed) return;
  try {
    currentProgressiveProblem = await api(`/hr/progressive/problems/${problemId}/publish`, { method: "POST" });
    currentProgressiveStages = await api(`/hr/progressive/problems/${problemId}/stages`);
    renderProgressiveDetail();
    loadProgressiveProblems();
  } catch (e) {
    document.getElementById("progressive-detail-status").textContent = e.message;
  }
}

// ==================== Round 5 (Progressive Engineering) POC - candidate UI ====================
// Phase 8: candidate experience only. Talks exclusively to
// routers/progressive.py's candidate_router - never touches
// ROUND_SEQUENCE, refreshCandidateNav, or loadRound above, so nothing
// here can affect the real Round 1-4 assessment.
//
// SECURITY NOTE (why some things are deliberately absent from this UI,
// not just unused): there is no total-stage-count anywhere below - only
// "Stage {current_stage}" is ever shown, never "Stage N of M" - because
// the backend itself never tells the candidate-facing API how many
// stages exist in total (see routers/progressive.py's CandidateStageOut,
// which has no such field). There is also no turn-history endpoint for
// progressive attempts (unlike Round 3), so the AI Assistance transcript
// below (progressiveChatTurns) is session-only - it resets on a page
// reload. That's a real, honest limitation, not a bug: inventing a new
// backend route to restore it was out of scope for this phase ("do not
// invent backend capabilities that don't exist").

let candidatePage = "rounds";                  // "rounds" (the real assessment) or "progressive" (Round 5 POC)
let currentProgressiveAttempt = null;          // {id, problem_id, current_stage, status}
let currentProgressiveProblemView = null;
let currentProgressiveStageView = null;        // {stage_order, requirement_text, expected_behavior} - current stage ONLY
let progressiveChatTurns = [];                 // session-only transcript, see note above
let progressiveStageResultView = null;         // aggregate-only {stage_order, passed, test_count, passed_count} for the just-submitted stage

function renderCandidateProgressiveNav() {
  document.getElementById("candidate-progressive-nav").innerHTML = `
    <div class="rail-divider">
      <div class="rail-section-label">Experimental</div>
      <button class="index-item ${candidatePage === "progressive" ? "active" : ""}" onclick="selectCandidatePage('progressive')"><span>Progressive Engineering</span></button>
      ${candidatePage === "progressive" ? `<button class="index-item" onclick="selectCandidatePage('rounds')"><span>&larr; Back to assessment</span></button>` : ""}
    </div>
  `;
}

function selectCandidatePage(page) {
  candidatePage = page;
  document.getElementById("round-view").classList.toggle("hidden", page !== "rounds");
  document.getElementById("progressive-candidate-view").classList.toggle("hidden", page !== "progressive");
  renderCandidateProgressiveNav();
  if (page === "progressive") {
    setPageHeader("Candidate Assessment", "Progressive Engineering", "Experimental (POC) - separate from the assessment above.");
    loadProgressiveCandidateView();
  } else {
    renderCandidateRoundNav(); // restores the normal round-specific header
  }
}

async function loadProgressiveCandidateView() {
  if (currentProgressiveAttempt) {
    // Already mid-attempt this session - just re-show it, no re-fetch of
    // the problem list needed.
    renderProgressiveCandidateWorkspace();
    return;
  }
  const box = document.getElementById("progressive-candidate-view");
  box.innerHTML = `<p class="muted">Loading...</p>`;
  const problems = await api("/candidate/progressive/problems");
  renderProgressiveProblemPicker(problems);
}

function renderProgressiveProblemPicker(problems) {
  const box = document.getElementById("progressive-candidate-view");
  if (problems.length === 0) {
    box.innerHTML = `<div class="panel"><p class="muted">No progressive problems are available yet.</p></div>`;
    return;
  }
  box.innerHTML = `
    <div class="panel">
      <h3>Progressive Engineering</h3>
      <p class="muted">Choose a problem to begin. Requirements are revealed one stage at a time - you won't see what's coming next until you get there.</p>
      ${problems.map((p) => `
        <div class="panel-inset row" style="align-items:center; justify-content:space-between">
          <div>
            <strong>${escapeHtml(p.title)}</strong>
            <p class="muted">${escapeHtml(p.description)}</p>
          </div>
          <button class="btn-primary btn-sm" onclick="startProgressiveProblem(${p.id})">Start</button>
        </div>
      `).join("")}
    </div>
  `;
}

async function startProgressiveProblem(problemId) {
  // start_attempt is idempotent server-side (see progressive_service.py) -
  // this also doubles as "resume" if the candidate already has an
  // active attempt on this problem (e.g. after a page reload), since
  // there's no separate "list my attempts" endpoint to restore from.
  currentProgressiveAttempt = await api(`/candidate/progressive/problems/${problemId}/start`, { method: "POST" });
  currentProgressiveProblemView = await api(`/candidate/progressive/problems/${problemId}`);
  progressiveChatTurns = [];
  progressiveStageResultView = null;
  await refreshProgressiveStageAndState();
}

async function refreshProgressiveStageAndState() {
  const state = await api(`/candidate/progressive/attempts/${currentProgressiveAttempt.id}/state`);
  currentProgressiveAttempt.current_stage = state.current_stage;
  currentProgressiveAttempt.status = state.status;
  if (state.completed) {
    renderProgressiveCompletedView();
    return;
  }
  currentProgressiveStageView = await api(`/candidate/progressive/attempts/${currentProgressiveAttempt.id}/current-stage`);
  renderProgressiveCandidateWorkspace(state.current_code);
}

function renderProgressiveCompletedView() {
  const box = document.getElementById("progressive-candidate-view");
  box.innerHTML = `
    <div class="panel">
      <h3>All stages complete</h3>
      ${progressiveStageResultView ? renderProgressiveStageResultCard() : ""}
      <p class="muted">You've submitted every stage of this problem.</p>
      <button class="btn-secondary" onclick="selectCandidatePage('rounds')">Back to assessment</button>
    </div>
  `;
}

function renderProgressiveStageResultCard() {
  const r = progressiveStageResultView;
  // Aggregate only - count and pass/fail, never a literal hidden-test
  // input/expected value (see routers/progressive.py's
  // CandidateStageResultOut, which has no field for either).
  return `
    <div class="panel-inset">
      <h4 style="margin:0 0 0.3rem">Stage ${r.stage_order} result <span class="badge ${r.passed ? "badge-published" : "badge-fail"}">${r.passed ? "All checks passed" : "Some checks failed"}</span></h4>
      <p class="muted">${r.passed_count} of ${r.test_count} checks passed.</p>
    </div>
  `;
}

function renderProgressiveCandidateWorkspace(codeOverride) {
  const box = document.getElementById("progressive-candidate-view");
  const p = currentProgressiveProblemView;
  const stage = currentProgressiveStageView;
  const existingEditor = document.getElementById("progressive-code-editor");
  const editorValue = codeOverride !== undefined ? (codeOverride || "") : (existingEditor ? existingEditor.value : "");

  box.innerHTML = `
    <div class="row" style="align-items:center; justify-content:space-between">
      <div>
        <div class="eyebrow">${escapeHtml(p.title)}</div>
        <h3 style="margin:0.2rem 0 0">Stage ${stage.stage_order}</h3>
      </div>
    </div>

    <div class="panel-inset">
      <h4 style="margin-top:0">Current Requirement</h4>
      <p>${escapeHtml(stage.requirement_text)}</p>
      ${stage.expected_behavior ? `<p class="muted"><strong>Expected behavior:</strong> ${escapeHtml(stage.expected_behavior)}</p>` : ""}
    </div>

    ${progressiveStageResultView ? renderProgressiveStageResultCard() : ""}

    <div class="round3-coding-grid">
      <div class="panel-inset round3-coding-pane">
        <div class="round3-pane-label">AI Assistance</div>
        <div class="round3-pane-body" id="progressive-chat-turns">${renderProgressiveChatTurns()}</div>
        <textarea id="progressive-chat-message" placeholder="Ask the assistant to explain, clarify, or make a specific code change..."></textarea>
        <div class="row">
          <button class="btn-secondary btn-sm" onclick="showProgressiveRunHelper()">Run / test with specific values</button>
          <button class="btn-primary btn-sm" onclick="sendProgressiveTurn()">Send</button>
        </div>
        <div id="progressive-run-helper"></div>
        <p id="progressive-chat-status" class="muted"></p>
      </div>

      <div class="panel-inset round3-coding-pane">
        <div class="round3-pane-label">Current Code</div>
        <textarea id="progressive-code-editor" class="code-textarea">${escapeHtml(editorValue)}</textarea>
        <div class="row create-scenario-actions">
          <button class="btn-primary" onclick="submitProgressiveStage()">Submit Stage</button>
        </div>
        <p id="progressive-submit-status" class="muted"></p>
      </div>
    </div>
  `;
}

function renderProgressiveChatTurns() {
  if (progressiveChatTurns.length === 0) {
    return `<p class="muted">Nothing yet - ask the assistant something to get started.</p>`;
  }
  return progressiveChatTurns.map((t) => t.who === "you"
    ? `<p class="round3-coding-prompt"><strong>You:</strong> ${escapeHtml(t.text)}</p>`
    : `<p class="round3-coding-response ${t.accepted ? "" : "round3-coding-response-refuse"}"><strong>Assistant:</strong> ${escapeHtml(t.text)}</p>`
  ).join("");
}

function showProgressiveRunHelper() {
  // There is no separate real-execution endpoint for Round 5 yet (unlike
  // Round 3's /round/3/run/start) - "running" here means asking the AI
  // assistant to do it via the SAME turn endpoint everything else in
  // this pane uses, formatted as an explicit "run this exact input"
  // instruction so the policy layer classifies it correctly (see
  // progressive_policy.py's RUN_CANDIDATE_INPUT category).
  document.getElementById("progressive-run-helper").innerHTML = `
    <div class="row">
      <label class="sr-only" for="progressive-run-values">Values to test with</label>
      <input id="progressive-run-values" placeholder="Values to test with, e.g. 10, 20, 30" />
      <button class="btn-secondary btn-sm" onclick="sendProgressiveRunRequest()">Run</button>
    </div>
  `;
}

async function sendProgressiveRunRequest() {
  const values = document.getElementById("progressive-run-values").value.trim();
  if (!values) return;
  document.getElementById("progressive-chat-message").value = `run this exact input: ${values}`;
  document.getElementById("progressive-run-helper").innerHTML = "";
  await sendProgressiveTurn();
}

async function sendProgressiveTurn() {
  const textarea = document.getElementById("progressive-chat-message");
  const message = textarea.value.trim();
  if (!message) return;
  const statusEl = document.getElementById("progressive-chat-status");
  statusEl.textContent = "Thinking...";
  try {
    const result = await api(`/candidate/progressive/attempts/${currentProgressiveAttempt.id}/turn`, {
      method: "POST",
      body: JSON.stringify({ candidate_request: message }),
    });
    progressiveChatTurns.push({ who: "you", text: message });
    progressiveChatTurns.push({ who: "assistant", text: result.response_message, accepted: result.accepted });
    const newCode = result.accepted && result.code_after ? result.code_after : undefined;
    renderProgressiveCandidateWorkspace(newCode);
  } catch (e) {
    statusEl.textContent = e.message;
  }
}

async function submitProgressiveStage() {
  const code = document.getElementById("progressive-code-editor").value;
  const statusEl = document.getElementById("progressive-submit-status");
  statusEl.textContent = "Submitting...";
  try {
    const result = await api(`/candidate/progressive/attempts/${currentProgressiveAttempt.id}/submit`, {
      method: "POST",
      body: JSON.stringify({ code_snapshot: code }),
    });
    // Stage 2 (or whichever comes next) continues from this SAME
    // submitted code, never a blank slate - see progressive_service.
    // get_current_code, which refreshProgressiveStageAndState below reads
    // via GET .../state.
    progressiveStageResultView = result;
    progressiveChatTurns = []; // the code carries forward into the next stage; the chat transcript does not
    await refreshProgressiveStageAndState();
  } catch (e) {
    statusEl.textContent = e.message;
  }
}

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
