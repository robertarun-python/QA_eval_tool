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
const ROUND_LABELS = { 1: "Manual test cases", 2: "Debugging", 4: "Conversational" };

// Runtime-editable per-round/final passing scores (see HR's Settings
// page, GET/PUT /hr/settings) - fetched once on HR login into
// appSettings below and used by every score-good/score-bad styling
// decision in this file via passingScoreForRound(), rather than one
// hardcoded global number (that used to be the case; per-round
// thresholds need this to be dynamic, HR-editable data, not a constant).
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
let currentHRRound = 1;     // which round's scenarios/history HR is authoring/reviewing right now
let hrPage = "rounds";      // "rounds" (author/review), "candidates" (results dashboard), or "settings"
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

  const res = await fetch("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ identifier, password }),
  });
  const data = await res.json();
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
}

function renderHRRoundNav() {
  const nav = document.getElementById("hr-round-nav");
  nav.innerHTML = `
    <div class="rail-section-label">Author scenarios</div>
    ${[1, 2, 4].map((n) => `
      <button class="nav-btn ${hrPage === "rounds" && n === currentHRRound ? "active" : ""}" onclick="selectHRRound(${n})">
        <span class="nav-chip">${n}</span>
        <span class="nav-btn-copy">
          <span class="nav-btn-label">Round ${n}</span>
          <span class="nav-btn-note">${ROUND_LABELS[n]}</span>
        </span>
      </button>
    `).join("")}
    <div class="rail-section-label">Reporting</div>
    <button class="nav-btn ${hrPage === "candidates" ? "active" : ""}" onclick="selectHRPage('candidates')">
      <span class="nav-chip">C</span>
      <span class="nav-btn-copy">
        <span class="nav-btn-label">Candidates</span>
        <span class="nav-btn-note">Results dashboard</span>
      </span>
    </button>
    <button class="nav-btn ${hrPage === "settings" ? "active" : ""}" onclick="selectHRPage('settings')">
      <span class="nav-chip">S</span>
      <span class="nav-btn-copy">
        <span class="nav-btn-label">Settings</span>
        <span class="nav-btn-note">Pass criteria</span>
      </span>
    </button>
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
  document.getElementById("set-round4").value = appSettings.round4_passing_score;
  document.getElementById("set-final").value = appSettings.final_passing_score;
  document.getElementById("set-window").value = appSettings.reapplication_window_months;
}

async function saveAppSettings() {
  const statusEl = document.getElementById("settings-status");
  const payload = {
    round1_passing_score: Number(document.getElementById("set-round1").value),
    round2_passing_score: Number(document.getElementById("set-round2").value),
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

async function loadScenarios() {
  const scenarios = (await api("/hr/scenarios")).filter((s) => s.round_number === currentHRRound);
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
  `;
}

function renderPublishedTable(published) {
  if (published.length === 0) return `<div class="empty-state">No published scenarios yet.</div>`;
  return `
    <div class="table-scroll">
      <table>
        <thead><tr><th>Publish for screening</th><th>Band</th><th>Title</th><th></th></tr></thead>
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
        <thead><tr><th>Band</th><th>Title</th><th></th></tr></thead>
        <tbody>
          ${drafts.map((s) => `
            <tr>
              <td>${s.experience_band}</td>
              <td>${escapeHtml(s.title)}</td>
              <td><button onclick="openScenarioDetail(${s.id})">Review</button></td>
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
  const refRows = (scenario.reference_json || []).map((r, i) => `
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
    ${formatScenarioDescription(scenario.description)}
    <h4>Reference answer ${isDraft ? "(review before publishing)" : ""}</h4>
    <div class="table-scroll">
      <table>
        <thead><tr><th>SI.No</th><th>Title</th><th>Preconditions</th><th>Steps</th><th>Expected result</th>${showPriorityType ? "<th>Priority</th><th>Type</th>" : ""}</tr></thead>
        <tbody>${refRows || `<tr><td colspan="${showPriorityType ? 7 : 5}" class="muted">No reference generated yet.</td></tr>`}</tbody>
      </table>
    </div>
    ${isDraft ? `
      <details>
        <summary>Edit reference as JSON</summary>
        <textarea id="ref-json-edit">${escapeHtml(JSON.stringify(scenario.reference_json || [], null, 2))}</textarea>
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
        <button onclick="deleteScenario(${scenario.id})">Delete draft</button>
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
        <thead><tr><th>Candidate</th><th>Band</th><th>Exam date</th><th>Round 1</th><th>Round 2</th><th>Round 4</th><th>Aggregate</th><th></th></tr></thead>
        <tbody>
          ${candidates.map((c) => `
            <tr>
              <td>${escapeHtml(c.email)} ${c.reapplied_within_window ? '<span class="badge badge-draft">Re-applied</span>' : ""}</td>
              <td>
                <select onchange="setCandidateBand(${c.id}, this.value)">
                  <option value="" ${!c.experience_band ? "selected" : ""}>-</option>
                  <option value="0-7" ${c.experience_band === "0-7" ? "selected" : ""}>0-7 years</option>
                  <option value="7+" ${c.experience_band === "7+" ? "selected" : ""}>7+ years</option>
                </select>
              </td>
              <td>${c.exam_date ? formatDate(c.exam_date) : "-"}</td>
              ${c.rounds.map((r) => `<td>${roundStatusCell(r)}</td>`).join("")}
              <td>${c.aggregate_score != null ? `<strong class="${c.aggregate_score >= (appSettings ? appSettings.final_passing_score : 210) ? "score-good" : "score-bad"}">${c.aggregate_score}/300</strong>` : `<span class="muted">-</span>`}</td>
              <td><button onclick="openCandidateDetail(${c.id})">View</button></td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
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
  resultEl.innerHTML = `<p class="muted">Uploading...</p>`;
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
  if (r.final_score != null) {
    const cls = r.final_score >= passingScoreForRound(r.round_number) ? "score-good" : "score-bad";
    return `<span class="${cls}">${r.final_score}/100</span>${flag}${autoClosedFlag}`;
  }
  return `<span class="muted">${r.status.replace("_", " ")}</span>${flag}${autoClosedFlag}`;
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
      ${renderScoreBlock(s)}
      ${s.round_number === 4 ? renderRound4Report(s)
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
              <td>${a.aggregate_score != null ? `${a.aggregate_score}/300` : `<span class="muted">-</span>`}</td>
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
  detailEl.innerHTML = `<p class="muted">Loading...</p>`;
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
  const nextRound = [1, 2, 4].find((n) => !candidateCompletedRounds.includes(n));
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
  nav.innerHTML = `
    <div class="rail-section-label">Assessment</div>
    ${[1, 2, 4].map((n) => {
      const done = candidateCompletedRounds.includes(n);
      const isUnlocked = n === candidateUnlockedRound && !done;
      const note = done ? "Submitted" : n === candidateUnlockedRound ? "In progress" : "Locked";
      return `
        <button class="nav-btn ${n === currentRound ? "active" : ""}" ${isUnlocked ? "" : "disabled"} onclick="loadRound(${n})">
          <span class="nav-chip">${n}</span>
          <span class="nav-btn-copy">
            <span class="nav-btn-label">Round ${n}</span>
            <span class="nav-btn-note">${note}</span>
          </span>
        </button>
      `;
    }).join("")}
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
function openModalOverlay(overlay) {
  document.body.appendChild(overlay);
  requestAnimationFrame(() => requestAnimationFrame(() => overlay.classList.add("modal-open")));
}

function closeModalOverlay(id) {
  const overlay = document.getElementById(id);
  if (!overlay) return;
  overlay.classList.remove("modal-open");
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
        <li>You'll see the test cases you wrote in Round 1 - use them as your starting point.</li>
        <li>For each one, describe what to test to an AI assistant. It will simulate running it and tell you what it did and what it observed - no code involved.</li>
        <li>You'll also have a test environment reference (sample data, credentials, API/DB details) and reference app screens alongside the scenario - use them as your source of truth when describing what to test.</li>
        <li>Want to test something beyond what you wrote in Round 1? Go ahead - you're not limited to those. Add as many extra test cases as you think the scenario needs.</li>
        <li>Heads-up: the assistant won't always get it right. It may skip a check, misreport a result, or just be wrong - on purpose. Read every response the way you'd review a test log you didn't write yourself, and keep refining your prompts until you're confident it's actually correct.</li>
        <li>What's scored: mainly the quality of your prompting and verification - catching issues, asking the right follow-ups, converging on a correct result. You don't need to automate everything you wrote in Round 1 - with a longer list, that's not realistic in the time given, and it's not what's measured here.</li>
        <li>Automating across more than one area - UI, API, DB, end-to-end - earns extra credit, but it's a bonus on top of doing a few well, not a requirement.</li>
        <li>Heads-up: trying to steer the assistant into skipping verification or telling you what would score well won't work, and gets flagged as a concern in your results - e.g. "just mark everything as passing" or "tell me what would get the best score." Treat it like a real test environment: it reports what happened, not what would look good.</li>
        <li>Your timer starts the moment you click below.</li>
      </ul>
      <div class="row">
        <button onclick="confirmStartRound4()">Got it - Start Round 4</button>
      </div>
    </div>
  `;
  openModalOverlay(overlay);
}

function confirmStartRound4() {
  closeModalOverlay("round4-intro-overlay");
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
  } else if (n === 4) {
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
    <textarea id="inv-root-cause" oninput="scheduleRoundDraftSave(2, round2DraftPayload)"></textarea>
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
    <td><textarea class="inv-area" oninput="scheduleRoundDraftSave(2, round2DraftPayload)"></textarea></td>
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
  ["typescript", "TypeScript"],
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
  box.innerHTML = `<p class="muted">Loading...</p>`;
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
    <div id="round4-tabs" class="row" style="margin-bottom:0.4rem"></div>
    <p class="muted" style="margin-bottom:0.85rem">Create as many test cases as you think this deserves - most candidates write 3-6, covering more than one angle (happy path, a negative/edge case, cross-checking what different layers report).</p>
    ${s.turns.length >= 20 ? `<p class="muted" style="color: var(--warn); margin-bottom:0.85rem">You've sent ${s.turns.length} messages in this round so far - there's no limit, but a good answer here is about judgment and coverage, not volume. Worth checking whether you're still adding new ground.</p>` : ""}
    <div class="panel-inset example-row" style="margin-bottom:0.85rem">
      <p class="muted" style="margin-bottom:0.3rem"><strong>Example (format only, not a hint for this scenario):</strong></p>
      <p class="muted" style="margin:0">Test case title: "Verify login with valid credentials" - then a first message to the assistant like "Log in with the test account credentials and tell me what happened."</p>
    </div>
    <div id="round4-test-case-body"></div>
    <div class="row" style="margin-top:1.25rem">
      <button class="btn-block" onclick="round4Submit()">Submit Round 4</button>
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
  } catch (e) {
    document.getElementById("round4-status").textContent = e.message;
  }
}

function round4SelectTestCase(id) {
  round4FlushDraft(round4ViewedTestCaseId); // send whatever's pending for the tab being left, don't wait out the debounce
  round4ViewedTestCaseId = id;
  renderRound4Tabs();
  renderRound4TestCaseBody();
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
  const composerHtml = `
    <textarea id="round4-message" placeholder="What do you want the assistant to do or check next?" oninput="round4OnComposerInput(${tcId}, this.value)">${escapeHtml(draftText)}</textarea>
    <div class="row">
      <button id="round4-send-btn" onclick="round4SendMessage()">Send</button>
    </div>
  `;

  body.innerHTML = `
    <div style="overflow:visible">${transcriptHtml}</div>
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
    <td><input class="tc-title" oninput="scheduleRoundDraftSave(1, round1DraftPayload)" /></td>
    <td><input class="tc-pre" oninput="scheduleRoundDraftSave(1, round1DraftPayload)" /></td>
    <td><textarea class="tc-steps" oninput="scheduleRoundDraftSave(1, round1DraftPayload)"></textarea></td>
    <td><textarea class="tc-expected" oninput="scheduleRoundDraftSave(1, round1DraftPayload)"></textarea></td>
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
