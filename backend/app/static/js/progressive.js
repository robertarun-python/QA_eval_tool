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
  setWideLayout(false);
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
