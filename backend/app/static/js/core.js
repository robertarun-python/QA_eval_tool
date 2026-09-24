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
// panel (see selectHRRound/loadRound2AutomationSettings), never this shared form.
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
let candidatesListStale = false;
let candidateDetailArchived = null;     // { appearanceId, exam_date, created_at, aggregate_score } while an archived appearance is open in the detail view - null for the current report
const archivedSubmissionIds = new Set(); // submissions of the archived appearance on screen - Retry/Override refuse these even if called directly
let candidateAppearances = [];          // last GET /hr/candidates/{id}/appearances for the open candidate        // a retry/override changed a score while the detail view was open - Back re-fetches the list (keeping search/filter/page) instead of showing stale numbers
let timerHandle = null;
let rowCount = 0;
let round2EntryState = null;           // last-fetched Round 2 state (GET /candidate/round/2/state)

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
  setWideLayout(false);
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
