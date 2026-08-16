// Minimal vanilla-JS frontend. No build step, no framework - talks to
// the FastAPI JSON API with plain fetch() calls. The token lives in
// sessionStorage (survives a refresh, clears when the tab closes) -
// explicit logout is the only way out otherwise. A production version
// would move this to an httpOnly cookie instead (sessionStorage is
// still readable by any injected script, same as localStorage).

let token = sessionStorage.getItem("qa_eval_token");
let role = sessionStorage.getItem("qa_eval_role");
let currentRound = 1;
let timerHandle = null;
let rowCount = 0;

if (token && role) {
  onLoggedIn();
}

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
  document.getElementById("hr-panel").classList.add("hidden");
  document.getElementById("candidate-panel").classList.add("hidden");
  document.getElementById("who").innerHTML = "";
  document.getElementById("email").value = "";
  document.getElementById("password").value = "";
  document.getElementById("auth-panel").classList.remove("hidden");
}

function onLoggedIn() {
  document.getElementById("auth-panel").classList.add("hidden");
  document.getElementById("who").innerHTML = `Logged in as ${role} <button onclick="logout()">Log out</button>`;
  if (role === "hr") {
    document.getElementById("hr-panel").classList.remove("hidden");
    loadScenarios();
    loadCandidates();
  } else {
    document.getElementById("candidate-panel").classList.remove("hidden");
    loadRound(1);
    loadMySubmissions();
  }
}

// ==================== HR ====================

async function createScenario() {
  const round_number = Number(document.getElementById("s-round").value);
  const experience_band = document.getElementById("s-band").value;
  const time_limit_minutes = Number(document.getElementById("s-time-limit").value);
  const title = document.getElementById("s-title").value;
  const description = document.getElementById("s-desc").value;
  const statusEl = document.getElementById("hr-create-status");

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
  }
}

async function loadScenarios() {
  const scenarios = await api("/hr/scenarios");
  const list = document.getElementById("scenario-list");
  if (scenarios.length === 0) {
    list.innerHTML = `<p class="muted">No scenarios yet.</p>`;
    return;
  }
  list.innerHTML = scenarios.map((s) => `
    <div class="row list-row">
      <span class="badge badge-${s.status}">${statusLabel(s.status)}</span>
      <span>Round ${s.round_number} · ${s.experience_band}</span>
      <strong style="flex:1">${escapeHtml(s.title)}</strong>
      <button onclick="openScenarioDetail(${s.id})">Review</button>
    </div>
  `).join("");
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

  const refRows = (scenario.reference_json || []).map((r) => `
    <tr>
      <td>${escapeHtml(r.title)}</td>
      <td>${escapeHtml(r.preconditions || "")}</td>
      <td>${escapeHtml(r.steps)}</td>
      <td>${escapeHtml(r.expected_result)}</td>
      <td>${escapeHtml(r.priority)}</td>
      <td>${escapeHtml(r.type)}</td>
    </tr>
  `).join("");

  const isDraft = scenario.status === "draft";
  box.innerHTML = `
    <h3>#${scenario.id} - ${escapeHtml(scenario.title)} <span class="badge badge-${scenario.status}">${statusLabel(scenario.status)}</span></h3>
    ${scenario.status === "published" ? `<p class="muted">This is the one scenario Round ${scenario.round_number} / ${scenario.experience_band} candidates currently see.</p>` : ""}
    <p class="muted">Round ${scenario.round_number} · ${scenario.experience_band} · ${scenario.time_limit_minutes} min limit</p>
    <p>${escapeHtml(scenario.description)}</p>
    <h4>Reference answer ${isDraft ? "(review before publishing)" : ""}</h4>
    <div class="table-scroll">
      <table>
        <thead><tr><th>Title</th><th>Preconditions</th><th>Steps</th><th>Expected result</th><th>Priority</th><th>Type</th></tr></thead>
        <tbody>${refRows || '<tr><td colspan="6" class="muted">No reference generated yet.</td></tr>'}</tbody>
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
        <button onclick="regenerateReference(${scenario.id})">Regenerate reference</button>
        <button onclick="publishScenario(${scenario.id})">Publish</button>
        <button onclick="deleteScenario(${scenario.id})">Delete draft</button>
      </div>
    ` : ""}
    <p id="scenario-detail-status" class="muted"></p>
  `;
}

async function regenerateReference(id) {
  const statusEl = document.getElementById("scenario-detail-status");
  statusEl.textContent = "Regenerating...";
  try {
    await api(`/hr/scenarios/${id}/regenerate-reference`, { method: "POST" });
    openScenarioDetail(id);
  } catch (e) {
    statusEl.textContent = e.message;
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
  if (status === "published") return "LIVE - candidates see this";
  if (status === "draft") return "draft";
  return status;
}

async function publishScenario(id) {
  const statusEl = document.getElementById("scenario-detail-status");
  const scenario = await api(`/hr/scenarios/${id}`);
  const scenarios = await api("/hr/scenarios");
  const currentlyLive = scenarios.find((s) =>
    s.status === "published" && s.round_number === scenario.round_number && s.experience_band === scenario.experience_band
  );
  const warning = currentlyLive
    ? `This will replace "${currentlyLive.title}" as the live Round ${scenario.round_number} / ${scenario.experience_band} scenario - candidates will only see the new one from now on. Continue?`
    : `Make this the live Round ${scenario.round_number} / ${scenario.experience_band} scenario? Candidates will see it immediately.`;
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
    const cls = r.final_score >= 70 ? "score-good" : "score-bad";
    return `<span class="${cls}">${r.final_score}/100</span>`;
  }
  return `<span class="muted">${r.status.replace("_", " ")}</span>`;
}

async function openCandidateDetail(id) {
  const submissions = await api(`/hr/candidates/${id}/report`);
  const box = document.getElementById("candidate-detail");
  box.classList.remove("hidden");

  if (submissions.length === 0) {
    box.innerHTML = `<p class="muted">No submissions yet.</p>`;
    return;
  }

  box.innerHTML = submissions.map((s) => `
    <div class="panel-inset">
      <h4>Round ${s.round_number} - ${s.scenario ? escapeHtml(s.scenario.title) : ""} <span class="badge">${s.status}</span></h4>
      ${s.score ? `
        <p>Final score: <strong class="${s.score.final_score >= 70 ? "score-good" : "score-bad"}">${s.score.final_score}/100</strong> · Coverage: ${s.score.coverage_score}/100</p>
        <p>${escapeHtml(s.score.feedback_text || "")}</p>
        <p class="muted">Missed: ${(s.score.misses_json || []).join(", ") || "none noted"}</p>
      ` : `<p class="muted">Not scored yet.</p>`}
      ${renderSideBySide(s.content, s.scenario ? s.scenario.reference_json : null)}
    </div>
  `).join("");
}

function renderSideBySide(candidateRows, referenceRows) {
  if (!candidateRows) return "";
  const renderTable = (rows) => `
    <table>
      <thead><tr><th>Title</th><th>Steps</th><th>Expected</th><th>Pri</th><th>Type</th></tr></thead>
      <tbody>
        ${(rows || []).map((r) => `<tr><td>${escapeHtml(r.title)}</td><td>${escapeHtml(r.steps)}</td><td>${escapeHtml(r.expected_result)}</td><td>${escapeHtml(r.priority)}</td><td>${escapeHtml(r.type)}</td></tr>`).join("") || '<tr><td colspan="5" class="muted">-</td></tr>'}
      </tbody>
    </table>`;
  return `
    <div class="side-by-side">
      <div><p class="muted">Candidate's test cases</p><div class="table-scroll">${renderTable(candidateRows)}</div></div>
      <div><p class="muted">Reference</p><div class="table-scroll">${renderTable(referenceRows)}</div></div>
    </div>
  `;
}

// ==================== Candidate ====================

async function loadRound(n) {
  currentRound = n;
  stopTimer();
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
    box.innerHTML = `<h3>Round ${n}</h3><p class="muted">Not available yet - check back once HR has published this round's scenario.</p>`;
    return;
  }

  if (submission && (submission.status === "submitted" || submission.status === "scored")) {
    const nextRound = n + 1;
    const nextNote = n === 1
      ? `<p class="muted">Round 2 (debugging) is coming in a future update.</p>`
      : `<button onclick="loadRound(${nextRound})">Continue to Round ${nextRound}</button>`;
    box.innerHTML = `
      <h3>Round ${n}: ${escapeHtml(scenario.title)}</h3>
      <p>Submitted. ${submission.status === "scored" ? "Scored - see results below." : "Scoring in progress - check results below shortly."}</p>
      ${nextNote}
    `;
    return;
  }

  if (n !== 1) {
    box.innerHTML = `<h3>Round ${n}: ${escapeHtml(scenario.title)}</h3><p class="muted">This round isn't open for submissions yet.</p>`;
    return;
  }

  if (!submission) {
    box.innerHTML = `
      <h3>Round 1: ${escapeHtml(scenario.title)}</h3>
      <p>${escapeHtml(scenario.description)}</p>
      <p class="muted">Time limit: ${scenario.time_limit_minutes} minutes, starting when you click Start.</p>
      <button onclick="startRoundOne()">Start</button>
    `;
    return;
  }

  renderEntryForm(box, scenario, submission);
}

async function startRoundOne() {
  const submission = await api(`/candidate/round/1/start`, { method: "POST" });
  const state = await api(`/candidate/round/1`);
  renderEntryForm(document.getElementById("round-view"), state.scenario, submission);
}

function renderEntryForm(box, scenario, submission) {
  rowCount = 0;
  box.innerHTML = `
    <h3>Round 1: ${escapeHtml(scenario.title)}</h3>
    <p>${escapeHtml(scenario.description)}</p>
    <p id="timer" class="timer"></p>
    <div class="table-scroll">
      <table>
        <thead><tr><th>Title</th><th>Preconditions</th><th>Steps</th><th>Expected result</th><th>Priority</th><th>Type</th><th></th></tr></thead>
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
  startTimer(deadline);
}

function addRow() {
  const id = rowCount++;
  const tbody = document.getElementById("tc-rows");
  const tr = document.createElement("tr");
  tr.id = `row-${id}`;
  tr.innerHTML = `
    <td><input class="tc-title" /></td>
    <td><input class="tc-pre" /></td>
    <td><textarea class="tc-steps"></textarea></td>
    <td><textarea class="tc-expected"></textarea></td>
    <td>
      <select class="tc-priority">
        <option>High</option><option selected>Medium</option><option>Low</option>
      </select>
    </td>
    <td>
      <select class="tc-type">
        <option selected>Positive</option><option>Negative</option><option>Boundary</option><option>Edge</option>
      </select>
    </td>
    <td><button onclick="document.getElementById('row-${id}').remove()">Remove</button></td>
  `;
  tbody.appendChild(tr);
}

function collectRows() {
  return [...document.querySelectorAll("#tc-rows tr")]
    .map((tr) => ({
      title: tr.querySelector(".tc-title").value,
      preconditions: tr.querySelector(".tc-pre").value,
      steps: tr.querySelector(".tc-steps").value,
      expected_result: tr.querySelector(".tc-expected").value,
      priority: tr.querySelector(".tc-priority").value,
      type: tr.querySelector(".tc-type").value,
    }))
    .filter((r) => r.title.trim() || r.steps.trim());
}

async function doSubmitRound1() {
  stopTimer();
  const statusEl = document.getElementById("submit-status");
  const content = collectRows();
  if (content.length === 0) {
    statusEl.textContent = "Add at least one test case before submitting.";
    return;
  }
  try {
    await api(`/candidate/round/1/submit`, { method: "POST", body: JSON.stringify({ content }) });
    setTimeout(loadMySubmissions, 4000); // scoring runs in the background; give it a moment
    loadRound(1);
  } catch (e) {
    statusEl.textContent = e.message;
  }
}

function startTimer(deadlineMs) {
  const timerEl = document.getElementById("timer");
  function tick() {
    const remaining = deadlineMs - Date.now();
    if (remaining <= 0) {
      timerEl.textContent = "Time's up - submitting automatically...";
      stopTimer();
      doSubmitRound1();
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

async function loadMySubmissions() {
  const submissions = await api("/candidate/submissions");
  const box = document.getElementById("my-submissions");
  if (submissions.length === 0) {
    box.innerHTML = `<p class="muted">No submissions yet.</p>`;
    return;
  }
  box.innerHTML = submissions.map((s) => {
    if (s.score) {
      const cls = s.score.final_score >= 70 ? "score-good" : "score-bad";
      return `
        <div class="panel-inset">
          <p>Round ${s.round_number} - <span class="${cls}">${s.score.final_score}/100</span></p>
          <p class="muted">Coverage: ${s.score.coverage_score}/100</p>
          <p>${escapeHtml(s.score.feedback_text || "")}</p>
          <p class="muted">Missed: ${(s.score.misses_json || []).join(", ") || "none noted"}</p>
        </div>`;
    }
    return `<div class="panel-inset"><p>Round ${s.round_number} - status: ${s.status} (scoring in progress...)</p></div>`;
  }).join("");
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}
