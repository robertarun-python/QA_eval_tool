// Minimal vanilla-JS frontend. No build step, no framework - talks to
// the FastAPI JSON API with plain fetch() calls. The token lives in
// sessionStorage (survives a refresh, clears when the tab closes) -
// explicit logout is the only way out otherwise. A production version
// would move this to an httpOnly cookie instead (sessionStorage is
// still readable by any injected script, same as localStorage).

// ---- Shared constants ----
//
// Round display names - one source, used by both HR's nav (renderHRRoundNav)
// and the candidate's nav (renderCandidateRoundNav). Used to be two
// identical maps (HR_ROUND_LABELS and ROUND_TITLES) defined separately.
const ROUND_LABELS = { 1: "Manual test cases", 2: "Debugging", 3: "Conversational" };

// Mirrors config.py's `passing_score` setting on the backend - there's
// no shared-schema/codegen step between the Python backend and this
// plain JS frontend, so this is the one place on this side that needs
// to be kept in sync if that setting ever changes. Every score-good/
// score-bad styling decision in this file reads from here, not a
// hardcoded "70", so there's only one spot to update.
const PASSING_SCORE = 70;

let token = sessionStorage.getItem("qa_eval_token");
let role = sessionStorage.getItem("qa_eval_role");
let userEmail = null; // resolved fresh from GET /auth/me every session load - see loadWhoAmI()
let currentRound = 1;       // which round's content is showing right now (candidate view)
let candidateUnlockedRound = 1; // the one round a candidate is allowed into - see refreshCandidateNav()
let candidateCompletedRounds = [];
let currentHRRound = 1;     // which round's scenarios/history HR is authoring/reviewing right now
let hrPage = "rounds";      // "rounds" (author/review) or "candidates" (results dashboard)
let candidateSummaryData = null;        // last-generated { round_comments, final_summary } (see generateCandidateSummary) - reused by the PDF download so it doesn't cost a second LLM call
let candidateDetailSubmissions = [];    // the currently-open candidate's submissions (see openCandidateDetail) - lets generateCandidateSummary label each round comment with its real title/score
let timerHandle = null;
let rowCount = 0;
let round3State = null;           // last-fetched Round3StateOut, refreshed after every turn/test-case creation
let round3ViewedTestCaseId = null; // which test case tab is showing
let round3DraftBuffer = {};        // { testCaseId: latestTypedText } - instant, in-memory, survives tab switches with zero latency
let round3DraftTimers = {};        // { testCaseId: setTimeout handle } - debounced PATCH to the server

function authHeaders() {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
}

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: authHeaders(), ...opts });
  if (res.status === 401) {
    // The stored token is missing/expired (sessions last 12h) - the
    // server no longer recognizes it, so there's nothing useful left to
    // do but send the user back to login rather than fail silently.
    logout();
    throw new Error("Your session expired - please log in again.");
  }
  const data = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error((data && data.detail) || `Request failed (${res.status})`);
  }
  return data;
}

// ---- Auth ----

async function login() {
  const email = document.getElementById("email").value;
  const password = document.getElementById("password").value;
  document.getElementById("auth-error").textContent = "";

  const res = await fetch("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const data = await res.json();
  if (!res.ok) {
    document.getElementById("auth-error").textContent = data.detail || "Something went wrong";
    return;
  }
  token = data.access_token;
  role = data.role;
  sessionStorage.setItem("qa_eval_token", token);
  sessionStorage.setItem("qa_eval_role", role);
  onLoggedIn();
}

function logout() {
  stopTimer();
  sessionStorage.removeItem("qa_eval_token");
  sessionStorage.removeItem("qa_eval_role");
  token = null;
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

// Resolved from the server on every session load (fresh login AND a
// page reload that restores an already-signed-in session from
// sessionStorage) rather than trusted from whatever was last typed into
// the login form - a tab that was already signed in before this existed
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
  if (role === "hr") {
    document.getElementById("hr-panel").classList.remove("hidden");
    renderHRRoundNav();
    loadScenarios();
    loadCandidates();
    loadHistory();
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
    ${[1, 2, 3].map((n) => `
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
  `;

  document.getElementById("hr-page-rounds").classList.toggle("hidden", hrPage !== "rounds");
  document.getElementById("hr-page-candidates").classList.toggle("hidden", hrPage !== "candidates");

  if (hrPage === "rounds") {
    setPageHeader("HR Console", `Round ${currentHRRound} · ${ROUND_LABELS[currentHRRound]}`, "Author, review, and publish scenarios for this round.");
    document.getElementById("hr-round-context").textContent = `Now creating for Round ${currentHRRound} (${ROUND_LABELS[currentHRRound]}).`;
  } else {
    setPageHeader("HR Console", "Candidates", "Every candidate's progress and results, across all rounds.");
  }
}

function selectHRRound(n) {
  currentHRRound = n;
  hrPage = "rounds";
  renderHRRoundNav();
  document.getElementById("scenario-detail").classList.add("hidden");
  loadScenarios();
  loadHistory();
}

function selectHRPage(page) {
  hrPage = page;
  renderHRRoundNav();
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
  const isRound3 = scenario.round_number === 3;
  box.innerHTML = `
    <h3>#${scenario.id} - ${escapeHtml(scenario.title)} <span class="badge badge-${scenario.status}">${statusLabel(scenario.status)}</span>${scenario.is_live ? ' <span class="badge badge-published">LIVE</span>' : ""}</h3>
    ${scenario.is_live ? `<p class="muted">This is the one scenario Round ${scenario.round_number} / ${scenario.experience_band} candidates currently see.</p>` : ""}
    <p class="muted">Round ${scenario.round_number} · ${scenario.experience_band} · ${scenario.time_limit_minutes} min limit</p>
    <p class="scenario-description">${escapeHtml(scenario.description)}</p>
    ${isRound3 ? `
      <div class="hint-box">Round 3 has no fixed test-case reference to review - each candidate automates their own Round 1 answer, open-endedly (no fixed category or count), so there's nothing to approve there. This description is the instructions candidates see. The assistance level (60% helpfulness) currently uses a sensible default, not per-scenario configuration.</div>
      <h4>Test environment (auto-generated, shown to candidates)</h4>
      ${scenario.environment_json ? `
        <div class="hint-box env-panel">
          <dl class="env-fields">
            ${Object.entries(scenario.environment_json.fields || {}).map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join("")}
          </dl>
          ${scenario.environment_json.notes ? `<p>${escapeHtml(scenario.environment_json.notes)}</p>` : ""}
        </div>
      ` : `<p class="muted">No test environment generated yet.</p>`}
      <h4>Reference app screens (auto-generated, shown to candidates)</h4>
      ${scenario.ui_mockup_json ? renderMockupScreens(scenario.ui_mockup_json, "hr-mockup") : `<p class="muted">No reference screens generated yet.</p>`}
      ${isDraft ? `
        <div class="row">
          <button id="regenerate-btn" onclick="regenerateReference(${scenario.id})">Regenerate environment &amp; screens</button>
        </div>
      ` : ""}
    ` : `
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
    `}
    ${isDraft ? `
      <div class="row">
        <button onclick="publishScenario(${scenario.id})">Publish</button>
        <button onclick="deleteScenario(${scenario.id})">Delete draft</button>
      </div>
    ` : ""}
    <p id="scenario-detail-status" class="muted"></p>
  `;
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
    openScenarioDetail(id);
  } catch (e) {
    statusEl.textContent = e.message.includes("JSON") ? "Invalid JSON - check the syntax." : e.message;
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
  const candidates = await api("/hr/candidates");
  const box = document.getElementById("candidates-table");
  box.innerHTML = `
    <div class="table-scroll">
      <table>
        <thead><tr><th>Candidate</th><th>Band</th><th>Round 1</th><th>Round 2</th><th>Round 3</th><th></th></tr></thead>
        <tbody>
          ${candidates.map((c) => `
            <tr>
              <td>${escapeHtml(c.email)}</td>
              <td>${c.experience_band || ""}</td>
              ${c.rounds.map((r) => `<td>${roundStatusCell(r)}</td>`).join("")}
              <td><button onclick="openCandidateDetail(${c.id})">View</button></td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function roundStatusCell(r) {
  if (r.final_score != null) {
    const cls = r.final_score >= PASSING_SCORE ? "score-good" : "score-bad";
    return `<span class="${cls}">${r.final_score}/100</span>`;
  }
  return `<span class="muted">${r.status.replace("_", " ")}</span>`;
}

async function openCandidateDetail(id) {
  const submissions = await api(`/hr/candidates/${id}/report`);
  const box = document.getElementById("candidate-detail");
  box.classList.remove("hidden");
  candidateSummaryData = null;       // stale from whatever candidate was open before
  candidateDetailSubmissions = submissions; // so generateCandidateSummary can label each round's comment with its real title/score

  if (submissions.length === 0) {
    box.innerHTML = `<div class="empty-state">No submissions yet.</div>`;
    return;
  }

  const rows = submissions.map((s) => `
    <div class="panel-inset">
      <h4>Round ${s.round_number} - ${s.scenario ? escapeHtml(s.scenario.title) : ""} <span class="badge">${s.status}</span></h4>
      ${s.score ? `
        <p>Final score: <strong class="${s.score.final_score >= PASSING_SCORE ? "score-good" : "score-bad"}">${s.score.final_score}/100</strong> · Coverage: ${s.score.coverage_score}/100</p>
        <p>${escapeHtml(s.score.feedback_text || "")}</p>
        <p class="muted">Missed: ${(s.score.misses_json || []).map(escapeHtml).join(", ") || "none noted"}</p>
      ` : `<p class="muted">Not scored yet.</p>`}
      ${s.round_number === 3 ? renderRound3Report(s)
        : s.round_number === 2 ? renderRound2Report(s)
        : renderSideBySide(s.content, s.scenario ? s.scenario.reference_json : null)}
    </div>
  `).join("");

  box.innerHTML = `
    ${rows}
    <div class="panel-inset">
      <h4>Summary</h4>
      <p class="muted">A crisp, cross-round synthesis for feedback to the candidate or a briefing for the next round's interviewers.</p>
      <div class="row">
        <button onclick="generateCandidateSummary(${id})">Generate Summary</button>
      </div>
      <div id="candidate-summary-body"></div>
    </div>
  `;
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
        ? `<strong class="${submission.score.final_score >= PASSING_SCORE ? "score-good" : "score-bad"}">${submission.score.final_score}/100</strong>`
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
    headers: authHeaders(),
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

function renderRound3Report(s) {
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
  // (see renderRound3Report), neither fits this title/steps/expected
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
  const history = (await api("/hr/history")).filter((h) => h.round_number === currentHRRound);
  const box = document.getElementById("history-list");
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
  const nextRound = [1, 2, 3].find((n) => !candidateCompletedRounds.includes(n));
  renderCandidateRoundNav();

  if (nextRound === undefined) {
    currentRound = 0; // nothing in the nav is "active" once everything's submitted
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
    ${[1, 2, 3].map((n) => {
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
  if (currentRound >= 1 && currentRound <= 3) {
    setPageHeader("Candidate Assessment", `Round ${currentRound} · ${ROUND_LABELS[currentRound]}`, "");
  } else {
    setPageHeader("Candidate Assessment", "Assessment complete", "");
  }
}

async function loadRound(n) {
  currentRound = n;
  stopTimer();
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
    box.innerHTML = `
      <h3>Round ${n}: ${escapeHtml(scenario.title)}</h3>
      <p class="scenario-description">${escapeHtml(scenario.description)}</p>
      <p class="muted">Time limit: ${scenario.time_limit_minutes} minutes, starting when you click Start.</p>
      <button onclick="startRound(${n})">Start</button>
    `;
    return;
  }

  renderRoundEntry(n, box, scenario, submission);
}

async function startRound(n) {
  const submission = await api(`/candidate/round/${n}/start`, { method: "POST" });
  const state = await api(`/candidate/round/${n}`);
  const box = document.getElementById("round-view");
  renderRoundEntry(n, box, state.scenario, submission);
}

// Each round's candidate-facing shape is genuinely different now: round
// 1 is repeatable test-case rows, round 2 is a shorter investigation
// list + one root-cause conclusion, round 3 is conversational. No
// shared "structured rounds" bucket anymore - just dispatch by number.
function renderRoundEntry(n, box, scenario, submission) {
  if (n === 1) {
    renderEntryForm(box, scenario, submission);
  } else if (n === 2) {
    renderInvestigationForm(box, scenario, submission);
  } else {
    renderRound3View(box);
  }
}

// Round 1: repeatable test-case rows (title/preconditions/steps/expected_result).

function renderEntryForm(box, scenario, submission) {
  rowCount = 0;
  box.innerHTML = `
    <h3>Round 1: ${escapeHtml(scenario.title)}</h3>
    <p class="scenario-description">${escapeHtml(scenario.description)}</p>
    <p id="timer" class="timer"></p>
    <div class="table-scroll">
      <table>
        <thead><tr><th>SI.No</th><th>Title</th><th>Preconditions</th><th>Steps</th><th>Expected result</th><th></th></tr></thead>
        <tbody id="tc-rows"></tbody>
      </table>
    </div>
    <div class="row">
      <button onclick="addRow()">+ Add row</button>
      <button onclick="doSubmitRound1()">Submit</button>
    </div>
    <p id="submit-status" class="muted"></p>
  `;
  addRow();
  addRow();
  const deadline = new Date(submission.started_at + "Z").getTime() + scenario.time_limit_minutes * 60 * 1000;
  startTimer(deadline, () => {
    document.getElementById("timer").textContent = "Time's up - submitting automatically...";
    doSubmitRound1();
  });
}

// Round 2: an investigation write-up, not test cases - a short repeatable
// list of areas checked (SI.No + one free-text field each) plus a single
// closing "Possible Root Cause" box where the candidate states what they
// eliminated and what they concluded. See schemas.Round2SubmissionCreate.

function renderInvestigationForm(box, scenario, submission) {
  rowCount = 0;
  box.innerHTML = `
    <h3>Round 2: ${escapeHtml(scenario.title)}</h3>
    <p class="scenario-description">${escapeHtml(scenario.description)}</p>
    <p id="timer" class="timer"></p>
    <div class="table-scroll">
      <table>
        <thead><tr><th>SI.No</th><th>Investigation area</th><th></th></tr></thead>
        <tbody id="inv-rows"></tbody>
      </table>
    </div>
    <div class="row">
      <button onclick="addInvestigationRow()">+ Add row</button>
    </div>
    <h4>Possible Root Cause</h4>
    <p class="muted">What you investigated, which areas you eliminated, and your conclusion.</p>
    <textarea id="inv-root-cause"></textarea>
    <div class="row">
      <button onclick="doSubmitRound2Investigation()">Submit</button>
    </div>
    <p id="submit-status" class="muted"></p>
  `;
  addInvestigationRow();
  addInvestigationRow();
  const deadline = new Date(submission.started_at + "Z").getTime() + scenario.time_limit_minutes * 60 * 1000;
  startTimer(deadline, () => {
    document.getElementById("timer").textContent = "Time's up - submitting automatically...";
    doSubmitRound2Investigation();
  });
}

function addInvestigationRow() {
  const id = rowCount++;
  const tbody = document.getElementById("inv-rows");
  const tr = document.createElement("tr");
  tr.id = `inv-row-${id}`;
  tr.innerHTML = `
    <td class="inv-no"></td>
    <td><textarea class="inv-area"></textarea></td>
    <td><button onclick="removeInvestigationRow('inv-row-${id}')">Remove</button></td>
  `;
  tbody.appendChild(tr);
  renumberInvestigationRows();
}

function removeInvestigationRow(rowId) {
  document.getElementById(rowId).remove();
  renumberInvestigationRows();
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

async function doSubmitRound2Investigation() {
  stopTimer();
  const statusEl = document.getElementById("submit-status");
  const investigation = collectInvestigationRows();
  const root_cause = document.getElementById("inv-root-cause").value.trim();
  if (investigation.length === 0) {
    statusEl.textContent = "Add at least one investigation row before submitting.";
    return;
  }
  if (!root_cause) {
    statusEl.textContent = "Fill in the Possible Root Cause box before submitting.";
    return;
  }
  try {
    await api("/candidate/round/2/submit", { method: "POST", body: JSON.stringify({ investigation, root_cause }) });
    refreshCandidateNav();
  } catch (e) {
    statusEl.textContent = e.message;
  }
}

// ---- Round 3: conversational, open-ended test automation ----
//
// State lives server-side (Round3StateOut, see candidate.py) and gets
// re-fetched into round3State after every turn/test-case creation, then
// the whole panel re-renders from it - simpler than trying to patch the
// DOM incrementally for something this stateful. The one thing that
// does NOT live in round3State is each test case's in-progress draft
// message (round3DraftBuffer) - that's candidate-typed and would be
// lost on every re-render (and on switching tabs) otherwise; see
// round3OnComposerInput below for the autosave mechanism.

const STEP_MARKS = { pass: "✓", fail: "✗", partial: "~" };

// Shared by the candidate's live transcript and HR's read-only report -
// plain-English action list, each with its own pass/fail marker, so a
// failure is locatable to a specific step rather than just an overall
// verdict. Deliberately never renders code - see Round3ExecutionStep.
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

// Shared by the candidate's round 3 view and HR's scenario detail -
// renders Round3UiMockupOut's structured screens (screen -> ordered
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

  const panels = screens.map((screen, i) => `
    <div class="mockup-screen ${i === 0 ? "" : "hidden"}" id="${idPrefix}-screen-${i}">
      ${screen.elements.map((el) => `
        <div class="mockup-el mockup-el-${el.type}">
          ${el.type === "input" ? `<span class="mockup-el-caption">${escapeHtml(el.text)}</span><span class="mockup-el-box"></span>` : escapeHtml(el.text)}
        </div>
      `).join("")}
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

async function renderRound3View(box) {
  box.innerHTML = `<p class="muted">Loading...</p>`;
  try {
    round3State = await api("/candidate/round/3/state");
  } catch (e) {
    box.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
    return;
  }
  // Seed the draft buffer from whatever was last autosaved server-side -
  // recovers in-progress text across a full page refresh, not just a tab
  // switch within the same page load.
  round3DraftBuffer = {};
  for (const tc of round3State.test_cases) round3DraftBuffer[tc.id] = tc.draft_prompt || "";
  round3ViewedTestCaseId = round3State.test_cases.length > 0 ? round3State.test_cases[0].id : null;
  renderRound3Layout(box);

  if (!timerHandle) {
    const submission = round3State.submission;
    const deadline = new Date(submission.started_at + "Z").getTime() + round3State.scenario.time_limit_minutes * 60 * 1000;
    startTimer(deadline, round3AutoSubmit);
  }
}

async function round3AutoSubmit() {
  const timerEl = document.getElementById("timer");
  if (timerEl) timerEl.textContent = "Time's up - submitting automatically...";
  try {
    await api("/candidate/round/3/submit", { method: "POST" });
  } catch (e) {
    // Most likely cause: time ran out before the candidate ever sent a
    // single message - nothing to auto-submit in that case, just let
    // refreshCandidateNav reflect wherever they actually got to.
  }
  round3DraftBuffer = {};
  refreshCandidateNav();
}

function renderRound3Layout(box) {
  const s = round3State;
  const envFields = s.environment ? Object.entries(s.environment.fields || {}) : [];

  const r1Rows = s.round1_context.submitted_rows || [];

  box.innerHTML = `
    <h3>Round 3: ${escapeHtml(s.scenario.title)}</h3>
    <p class="scenario-description">${escapeHtml(s.scenario.description)}</p>
    <details class="hint-box" open>
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
      <details class="hint-box env-panel" open>
        <summary><strong>Test environment</strong></summary>
        <dl class="env-fields">
          ${envFields.map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join("")}
        </dl>
        ${s.environment.notes ? `<p>${escapeHtml(s.environment.notes)}</p>` : ""}
      </details>
    ` : ""}
    ${s.ui_mockup ? `
      <details class="hint-box mockup-details" open>
        <summary><strong>Reference: App screens</strong></summary>
        ${renderMockupScreens(s.ui_mockup, "cand-mockup")}
      </details>
    ` : ""}
    <p id="timer" class="timer"></p>
    <div id="round3-tabs" class="row" style="margin-bottom:0.4rem"></div>
    <p class="muted" style="margin-bottom:0.85rem">Create as many test cases as you think this deserves - most candidates write 3-6, covering more than one angle (happy path, a negative/edge case, cross-checking what different layers report).</p>
    <div id="round3-test-case-body"></div>
    <div class="row" style="margin-top:1.25rem">
      <button class="btn-block" onclick="round3Submit()">Submit Round 3</button>
    </div>
    <p id="round3-status" class="muted"></p>
  `;

  renderRound3Tabs();
  renderRound3TestCaseBody();
}

function renderRound3Tabs() {
  const s = round3State;
  const tabs = s.test_cases.map((tc, i) => {
    const count = tc.turn_count;
    return `
      <button class="nav-btn ${tc.id === round3ViewedTestCaseId ? "active" : ""}" onclick="round3SelectTestCase(${tc.id})">
        <span class="nav-chip">${i + 1}</span>
        <span class="nav-btn-copy">
          <span class="nav-btn-label">${escapeHtml(tc.title || `Test case ${i + 1}`)}</span>
          <span class="nav-btn-note">${count} turn${count === 1 ? "" : "s"}</span>
        </span>
      </button>
    `;
  }).join("");

  document.getElementById("round3-tabs").innerHTML = `
    ${tabs}
    <button class="btn-ghost" onclick="round3CreateTestCase()">+ New test case</button>
  `;
}

async function round3CreateTestCase() {
  // No prompt() dialog - creates immediately with no title; the tab
  // falls back to "Test case N" for display (see Round3TestCaseOut) and
  // the candidate's own prompts are what actually convey intent.
  try {
    const tc = await api("/candidate/round/3/test-case", { method: "POST", body: JSON.stringify({ title: null }) });
    round3State = await api("/candidate/round/3/state");
    round3DraftBuffer[tc.id] = "";
    round3ViewedTestCaseId = tc.id;
    renderRound3Tabs();
    renderRound3TestCaseBody();
  } catch (e) {
    document.getElementById("round3-status").textContent = e.message;
  }
}

function round3SelectTestCase(id) {
  round3FlushDraft(round3ViewedTestCaseId); // send whatever's pending for the tab being left, don't wait out the debounce
  round3ViewedTestCaseId = id;
  renderRound3Tabs();
  renderRound3TestCaseBody();
}

function renderRound3TestCaseBody() {
  const s = round3State;
  const body = document.getElementById("round3-test-case-body");
  const tcId = round3ViewedTestCaseId;
  if (tcId == null) {
    body.innerHTML = `<div class="empty-state">No test cases yet - click "+ New test case" above to describe the first thing you want to automate.</div>`;
    return;
  }

  const turns = s.turns.filter((t) => t.test_case_id === tcId);

  const transcriptHtml = turns.length === 0
    ? `<div class="empty-state">No messages yet in this test case - describe what you want automated to get started.</div>`
    : turns.map((t) => `
      <div class="panel-inset">
        <p><strong>You:</strong> ${escapeHtml(t.candidate_prompt)}</p>
        <p>${escapeHtml(t.model_response.response_text)}</p>
        ${renderExecutionSteps(t.model_response.steps)}
        <div class="observed-box">
          <span class="badge badge-${t.model_response.status}">${t.model_response.status}</span>
          ${escapeHtml(t.model_response.observed_result)}
        </div>
      </div>
    `).join("");

  const draftText = round3DraftBuffer[tcId] || "";
  const composerHtml = `
    <textarea id="round3-message" placeholder="What do you want the assistant to do or check next?" oninput="round3OnComposerInput(${tcId}, this.value)">${escapeHtml(draftText)}</textarea>
    <div class="row">
      <button onclick="round3SendMessage()">Send</button>
    </div>
  `;

  body.innerHTML = `
    <div class="table-scroll" style="overflow:visible">${transcriptHtml}</div>
    ${composerHtml}
  `;
}

// Two-layer autosave: the in-memory buffer update is instant (this is
// what tab-switching restores from - zero network latency), the PATCH to
// the server is debounced so normal typing doesn't fire a request per
// keystroke. round3SelectTestCase flushes early on tab-switch so a fast
// switch-and-close doesn't lose up to DEBOUNCE_MS of typing to a
// cancelled timeout.
const ROUND3_DRAFT_DEBOUNCE_MS = 2000;

function round3OnComposerInput(tcId, value) {
  round3DraftBuffer[tcId] = value;
  clearTimeout(round3DraftTimers[tcId]);
  round3DraftTimers[tcId] = setTimeout(() => round3FlushDraft(tcId), ROUND3_DRAFT_DEBOUNCE_MS);
}

function round3FlushDraft(tcId) {
  if (tcId == null || round3DraftTimers[tcId] == null) return;
  clearTimeout(round3DraftTimers[tcId]);
  delete round3DraftTimers[tcId];
  api(`/candidate/round/3/test-case/${tcId}/draft`, {
    method: "PATCH",
    body: JSON.stringify({ draft_prompt: round3DraftBuffer[tcId] || "" }),
  }).catch(() => {}); // best-effort - the in-memory buffer is still correct either way
}

async function round3SendMessage() {
  const tcId = round3ViewedTestCaseId;
  const prompt = (round3DraftBuffer[tcId] || "").trim();
  const statusEl = document.getElementById("round3-status");
  if (!prompt) {
    statusEl.textContent = "Type a message first.";
    return;
  }
  statusEl.textContent = "Thinking...";
  try {
    await api("/candidate/round/3/turn", {
      method: "POST",
      body: JSON.stringify({ test_case_id: tcId, candidate_prompt: prompt }),
    });
    clearTimeout(round3DraftTimers[tcId]);
    delete round3DraftTimers[tcId];
    round3DraftBuffer[tcId] = ""; // the server already cleared its copy as part of turn creation
    round3State = await api("/candidate/round/3/state");
    statusEl.textContent = "";
    renderRound3Tabs();
    renderRound3TestCaseBody();
  } catch (e) {
    statusEl.textContent = e.message;
  }
}

async function round3Submit() {
  const statusEl = document.getElementById("round3-status");
  try {
    await api("/candidate/round/3/submit", { method: "POST" });
    round3DraftBuffer = {};
    refreshCandidateNav();
  } catch (e) {
    statusEl.textContent = e.message;
  }
}

function addRow() {
  const id = rowCount++;
  const tbody = document.getElementById("tc-rows");
  const tr = document.createElement("tr");
  tr.id = `row-${id}`;
  tr.innerHTML = `
    <td class="tc-no"></td>
    <td><input class="tc-title" /></td>
    <td><input class="tc-pre" /></td>
    <td><textarea class="tc-steps"></textarea></td>
    <td><textarea class="tc-expected"></textarea></td>
    <td><button onclick="removeRow('row-${id}')">Remove</button></td>
  `;
  tbody.appendChild(tr);
  renumberRows();
}

function removeRow(rowId) {
  document.getElementById(rowId).remove();
  renumberRows();
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

async function doSubmitRound1() {
  stopTimer();
  const statusEl = document.getElementById("submit-status");
  const content = collectRows();
  if (content.length === 0) {
    statusEl.textContent = "Add at least one row before submitting.";
    return;
  }
  try {
    await api("/candidate/round/1/submit", { method: "POST", body: JSON.stringify({ content }) });
    // No results screen - scoring happens in the background on HR's
    // side; the candidate just moves on to whatever's unlocked next.
    refreshCandidateNav();
  } catch (e) {
    statusEl.textContent = e.message;
  }
}

function startTimer(deadlineMs, onExpire) {
  const timerEl = document.getElementById("timer");
  function tick() {
    const remaining = deadlineMs - Date.now();
    if (remaining <= 0) {
      stopTimer();
      onExpire();
      return;
    }
    const mins = Math.floor(remaining / 60000);
    const secs = Math.floor((remaining % 60000) / 1000);
    timerEl.textContent = `Time remaining: ${mins}:${String(secs).padStart(2, "0")}`;
  }
  tick();
  timerHandle = setInterval(tick, 1000);
}

function stopTimer() {
  if (timerHandle) {
    clearInterval(timerHandle);
    timerHandle = null;
  }
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}

// Entry point - deliberately the last thing in the file. A returning
// user's stored token auto-logs them back in immediately on page load,
// which calls straight into renderHRRoundNav()/renderCandidateRoundNav()
// (via onLoggedIn) - those reference `const`s declared further up this
// file (ROUND_LABELS and others). const/let bindings exist in a
// "temporal dead zone" until their own declaration line has run, so
// this trigger must come after every such declaration, not before it -
// otherwise it only breaks for auto-login (a fresh manual login via the
// button always runs after the whole script has finished loading).
if (token && role) {
  onLoggedIn();
}
