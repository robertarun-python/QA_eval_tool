// ==================== AI-Assisted Test Automation (round 4 slot) ====================
// The candidate automates the test cases THEY designed in round 1 - see
// routers/candidate.py's /round/4/auto/* endpoints. Deliberately its own
// render/action functions, separate from both the legacy round 4 UI and
// the pilot UI above, same reasoning the backend already uses to keep the
// three modes apart. Backend stays authoritative throughout: no scoring,
// policy or selection rule is duplicated here.

let round2AutomationState = null;
let round2AutomationBusy = false;

function round2AutomationSetBusy(busy) {
  round2AutomationBusy = busy;
  ["r4a-lock-language-btn", "r4a-automate-btn", "r4a-submit-btn"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = busy;
  });
  // ask/save/run buttons are one-per-selected-TC (see round2AutomationTcSectionHtml),
  // so they're classes, not unique ids.
  document.querySelectorAll(".r4a-ask-btn, .r4a-save-btn, .r4a-run-btn, .r4a-test-data-btn").forEach((el) => { el.disabled = busy; });
}

async function loadRound2Automation(box) {
  try {
    round2AutomationState = await api("/candidate/round/2/auto/state");
  } catch (e) {
    box.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
    return;
  }
  renderRound2AutomationLayout(box);
}

// Read-only reference table of every Round 1 test case, mirroring
// Round 1's own entry table exactly (see renderEntryForm) - SI.No/Title/
// Preconditions/Steps/Test data/Expected result, no checkboxes or picks
// here at all. Selection itself happens via round2AutomationNextPickHtml's
// dropdown, one test case at a time.
function round2AutomationTcTableHtml(rows, selectedIndexes) {
  if (rows.length === 0) return `<p class="muted">No Round 1 test cases found.</p>`;
  const selectedSet = new Set(selectedIndexes || []);
  return rows.map((r) => `
    <div class="tc-card${selectedSet.has(r.index) ? " is-selected" : ""}">
      <div class="tc-card-head">
        <div class="tc-card-title-group">
          <span class="tc-card-num">${r.index + 1}</span>
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
// round2AutomationAutomateClicked) - only ever offers rows not already in
// round2AutomationState.selected. Shown by renderRound2AutomationLayout: once
// with every test case when nothing's picked yet, and again after the
// most recently added test case has a run result, as long as the
// two-test-case cap hasn't been reached and something's left to offer.
function round2AutomationNextPickHtml(remaining) {
  return `
    <div class="surface" style="margin:var(--space-default) 0; border-style:dashed">
      <div class="field-label" style="margin-bottom:0.4rem">Automate a test case</div>
      <div class="field-row" style="align-items:flex-end">
        <div class="field" style="flex:1 1 18rem">
          <select id="r4a-next-pick-select">
            <option value="" selected>Select...</option>
            ${remaining.map((r) => `<option value="${r.index}">${escapeHtml(r.title || `Test case ${r.index + 1}`)}</option>`).join("")}
          </select>
        </div>
        <button class="btn-primary" id="r4a-automate-btn" onclick="round2AutomationAutomateClicked()">Automate this test case</button>
      </div>
    </div>`;
}

function round2AutomationAutomateClicked() {
  const val = document.getElementById("r4a-next-pick-select").value;
  const statusEl = document.getElementById("r4a-status");
  if (!val) {
    if (statusEl) statusEl.textContent = "Select a test case first.";
    return;
  }
  round2AutomationAction(() => api("/candidate/round/2/auto/select", { method: "POST", body: JSON.stringify({ row_indexes: [Number(val)] }) }));
}

// Test cases are numbered from 1 on screen, as in Round 1; row indexes stay 0-based.
// What to do after a run that didn't pass - never why it failed (the candidate judges that).
function round2AutomationNextStepHtml(run, passed) {
  if (passed || run.infra_error) return "";
  const step = run.status === "incomplete" ? ((run.stdout || "").match(/INCOMPLETE:\s*(.+)/) || [])[1] : null;
  const what = step
    ? `The run stopped at a step that isn't finished yet: <strong>${escapeHtml(step.trim())}</strong>. Tell the assistant how to do it in the box below.`
    : "The test ran and did not pass - read the log below, then tell the assistant what to change in the box below.";
  return `<p class="result-state-detail r4a-next-step">${what}</p>`;
}

function round2AutomationRunResultHtml(rowIndex, run) {
  if (!run) return `<p class="muted">Not run yet.</p>`;
  // "status" (new runs): passed only for a complete test that exited cleanly - a test with gaps is INCOMPLETE, never PASS.
  const passed = run.status ? run.status === "passed" : (run.exit_code === 0 && !run.timed_out && !run.infra_error);
  const silent = passed && !(run.stdout || "").trim();  // ran cleanly but reported nothing - did it check anything?
  const label = run.status === "incomplete" ? "INCOMPLETE" : (silent ? "RAN - NOTHING REPORTED" : (passed ? "PASS" : "FAIL"));
  const meta = [
    `Exit code: ${run.exit_code === null || run.exit_code === undefined ? "-" : run.exit_code}`,
    run.duration_ms !== null && run.duration_ms !== undefined ? `${run.duration_ms}ms` : null,
    run.ran_at ? new Date(run.ran_at).toLocaleString() : null,   // "where available" - absent on a run recorded before this field existed
  ].filter(Boolean).join(" · ");
  return `
    <div class="result-state ${silent ? "is-silent" : passed ? "is-pass" : "is-fail"}">
      <span class="result-state-icon">${silent ? "?" : passed ? "✓" : "✕"}</span>
      <div class="result-state-body">
        <div class="result-state-label">${label} - Test case ${rowIndex + 1}</div>
        <p class="result-state-detail">${meta}</p>
        ${run.timed_out ? `<p class="result-state-detail">Timed out.</p>` : ""}
        ${run.infra_error ? `<p class="result-state-detail">The execution service had a problem - try running again.</p>` : ""}
        ${silent ? `<p class="result-state-detail">It ran without errors but printed nothing - make sure the test actually checks something and reports it.</p>`
                 : passed ? `<p class="result-state-caveat">PASS does not necessarily mean correct - check what was actually verified.</p>` : ""}
        ${round2AutomationNextStepHtml(run, passed)}
        <details style="margin-top:0.6rem"${passed ? "" : " open"}>
          <summary>Execution log</summary>
          ${run.stdout ? `<pre class="code-snippet">${escapeHtml(run.stdout)}</pre>` : `<p class="muted">No stdout.</p>`}
          ${run.stderr ? `<pre class="code-snippet round3-coding-stderr">${escapeHtml(run.stderr)}</pre>` : ""}
        </details>
      </div>
    </div>`;
}

function round2AutomationTurnsHtml(turns) {
  if (!turns || turns.length === 0) return `<p class="muted">No messages yet.</p>`;
  return `<div class="ai-thread">${turns.map((t) => `
    <div class="ai-turn ai-turn-candidate">
      <div class="ai-turn-role">You</div>
      <div class="ai-turn-body">${escapeHtml(t.candidate_prompt)}</div>
    </div>
    <div class="ai-turn ai-turn-assistant">
      <div class="ai-turn-role">Assistant${t.response_kind === "code_edit" ? " &middot; wrote code" : t.response_kind === "refuse" ? " &middot; declined" : ""}</div>
      <div class="ai-turn-body">${escapeHtml(t.response_message)}</div>
    </div>`).join("")}</div>`;
}

// Each selected test case (1-2) has its own independent automation state
// (see Round2AutomationTCStateOut) - code, AI conversation, run result and
// interpretation, none of it shared with the other selected test case.
// Every selected test case's own section stacks permanently in the DOM,
// always visible (no tabs, nothing hidden) - the candidate can return to
// an earlier one at any time to keep fixing/rerunning it (see
// renderRound2AutomationLayout, which decides when a NEW section for a
// not-yet-automated test case should appear below the rest).

function round2AutomationTcState(rowIndex) {
  return (round2AutomationState.tc_state || []).find((t) => t.row_index === rowIndex)
    || { code: "", turns: [], code_edits_count: 0, last_run: null, validation: "" };
}

function round2AutomationTcIsUnlocked(tc) {
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
// round2_automation_update_test_data / scoring_service._auto_tc_design_only,
// which keeps the original alongside for transparency once corrected).
function round2AutomationTestDataHtml(row) {
  return `
    <div class="surface" style="margin:0.85rem 0">
      <div class="field-label" style="margin-bottom:0.3rem">Test data</div>
      <p class="text-muted" style="margin:0 0 0.6rem 0">What your automation runs against. Made a mistake in Round 1? Correct it here - your original Round 1 answer is never changed.</p>
      <div class="field-row">
        <div class="field" style="flex:1 1 20rem">
          <textarea id="r4a-test-data-${row.index}" class="ta-medium ta-grow" rows="2">${escapeHtml(row.test_data || "")}</textarea>
        </div>
        <button class="btn-secondary r4a-test-data-btn" onclick="round2AutomationSaveTestDataClicked(${row.index})">Save test data</button>
      </div>
    </div>`;
}

function round2AutomationSaveTestDataClicked(rowIndex) {
  const testData = document.getElementById(`r4a-test-data-${rowIndex}`).value.trim();
  if (!testData) return;
  round2AutomationAction(() => api("/candidate/round/2/auto/test-data", { method: "POST", body: JSON.stringify({ row_index: rowIndex, test_data: testData }) }), rowIndex, "Saving test data...");
}

// Reference material - the app's own screens and test-environment facts,
// reused unmodified from whatever the scenario already generated (see
// Round2AutomationStateOut.ui_mockup/environment) - never code, never the
// solution. Shown ONCE at the page level, right alongside the Round 1
// table (see renderRound2AutomationLayout) - it's the same reference
// regardless of which selected test case is being worked on, so
// repeating it inside every test case's own section was just noise, not
// scoped information. Never hidden either way - visible before any test
// case is even picked, and permanently after.
// Segmented into tabs (one pane visible at a time) rather than two
// stacked <details> blocks - same underlying data (Round2AutomationStateOut.
// ui_mockup/environment), just presented so only the section the
// candidate actually needs right now is on screen. Switching is pure
// client-side visibility (round2AutomationReferenceTabClicked below) - never
// re-fetches or re-derives anything.
// The real practice environment's reference panel (built by code from the
// scenario description - practice_engine/reference.py): what a tester would be
// given on a real project, never the app's rules or messages.
// A page's HTML laid out like the browser's Inspect-element view: one element per line,
// indented by nesting. Read with DOMParser (inert - nothing in it runs); on any problem
// the source is shown as it came.
const _VOID_TAGS = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"]);
function round2PrettyHtml(source) {
  try {
    const doc = new DOMParser().parseFromString(String(source || ""), "text/html");
    const lines = [];
    const open = (el) => `<${el.tagName.toLowerCase()}${[...el.attributes].map((a) => ` ${a.name}="${a.value}"`).join("")}>`;
    const walk = (node, depth) => {
      const pad = "  ".repeat(depth);
      for (const child of node.childNodes) {
        if (child.nodeType === Node.TEXT_NODE) {
          const text = child.textContent.replace(/\s+/g, " ").trim();
          if (text) lines.push(pad + text);
        } else if (child.nodeType === Node.ELEMENT_NODE) {
          const tag = child.tagName.toLowerCase();
          const onlyText = child.childNodes.length === 1 && child.firstChild.nodeType === Node.TEXT_NODE;
          if (_VOID_TAGS.has(tag)) lines.push(pad + open(child));
          else if (!child.childNodes.length) lines.push(`${pad}${open(child)}</${tag}>`);
          else if (onlyText) lines.push(`${pad}${open(child)}${child.textContent.replace(/\s+/g, " ").trim()}</${tag}>`);
          else { lines.push(pad + open(child)); walk(child, depth + 1); lines.push(`${pad}</${tag}>`); }
        }
      }
    };
    walk(doc.documentElement.parentNode, 0);
    return lines.join("\n") || String(source || "");
  } catch (e) {
    return String(source || "");
  }
}

function round2AutomationPanelHtml(p) {
  const esc = escapeHtml;
  const cell = (v) => esc(v === null || v === undefined ? "" : String(v));
  const hasConnect = (p.connect || []).length > 0;  // Round 2; Round 1 runs nothing, so just the address and logins
  const tab = (name, label, active) =>
    `<button class="tab${active ? " active" : ""}" data-r4a-ref-tab="${name}" onclick="round2AutomationReferenceTabClicked('${name}')">${label}</button>`;
  const panel = (name, html, active) => `<div class="tab-panel${active ? " active" : ""}" data-r4a-ref-panel="${name}">${html}</div>`;
  const connect = `
    ${hasConnect ? `<p class="text-muted">Your test finds the application through these environment variables (set for every Run):</p>
    <dl class="env-fields">${p.connect.map((c) => `<dt><code>${esc(c.name)}</code></dt><dd>${esc(c.meaning)}</dd>`).join("")}</dl>
    ${p.web_address ? `<p class="text-muted">In the browser, the address Round 1 showed - <code>${esc(p.web_address)}</code> - opens the application too.</p>` : ""}`
      : p.web_address ? `<dl class="env-fields"><dt>Web address</dt><dd><code>${esc(p.web_address)}</code></dd></dl>` : ""}
    ${(p.accounts || []).length ? `<h4>Test accounts</h4><table class="data-table"><thead><tr><th>Login</th><th>Password</th><th>Name</th></tr></thead><tbody>
      ${p.accounts.map((a) => `<tr><td>${cell(a.login)}</td><td>${cell(a.password)}</td><td>${cell(a.name)}</td></tr>`).join("")}</tbody></table>` : ""}
    ${(p.failures || []).length ? `<h4>Simulated failures</h4><p>${p.failures.map((f) => `<code>${esc(f)}</code>`).join(", ")} - switch one on with the test controls.</p>` : ""}`;
  // Each page as it looks in a browser, then its source (for element ids). The screen is
  // shown in a locked-down frame: no scripts, and its links and forms can't go anywhere.
  // A picture of the page: nothing in it can be clicked (a link used to load an empty page into the frame).
  const screen = (source) => String(source || "").replace(/<head>/i,
    '<head><base target="_blank"><style>a,button,input,select,textarea,label{pointer-events:none!important}</style>');
  const pages = (p.pages || []).map((pg, i) => `
    <details class="r4a-page-source" ${i === 0 ? "open" : ""}><summary>${esc(pg.name)} <span class="muted">${esc(pg.path)}</span></summary>
      <iframe class="r4a-page-screen" sandbox="" referrerpolicy="no-referrer" title="${escapeAttr(pg.name)} page"
        srcdoc="${escapeAttr(screen(pg.source))}" style="width:100%;height:320px;border:1px solid var(--border, #ccc);border-radius:6px;background:#fff"></iframe>
      <details><summary class="muted">Page source - like Inspect element in the browser</summary><pre class="code-block r4a-page-html">${esc(round2PrettyHtml(pg.source))}</pre></details>
    </details>`).join("");
  const api = `
    <p class="text-muted">${esc(p.api_sign_in || "")} ${esc(p.api_errors || "")}</p>
    <table class="data-table"><thead><tr><th>Method</th><th>Path</th><th>Body / query fields</th><th>Success</th><th>Returns</th><th>Sign-in</th></tr></thead><tbody>
      ${(p.api || []).map((r) => `<tr><td>${cell(r.method)}</td><td><code>${cell(r.path)}</code></td><td>${cell([...(r.body || []), ...(r.query || [])].join(", "))}</td>
        <td>${cell(r.success)}</td><td>${cell((r.returns || []).join(", "))}${r.fields ? ` <span class="muted">(${cell(r.fields.join(", "))})</span>` : ""}</td>
        <td>${r.sign_in ? "yes" : "no"}</td></tr>`).join("")}</tbody></table>`;
  const db = (p.database || []).map((t) => {
    const cols = (t.columns || []).map((c) => c.name);
    return `<details class="r4a-table"><summary><code>${esc(t.table)}</code> <span class="muted">key ${esc(t.key)} &middot; ${t.rows.length} starting rows</span></summary>
      <p class="muted">${(t.columns || []).map((c) => `${esc(c.name)} ${esc(c.type)}`).join(", ")}</p>
      <div class="table-scroll"><table class="data-table"><thead><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>
        ${(t.rows || []).map((row) => `<tr>${cols.map((c) => `<td>${cell(row[c])}</td>`).join("")}</tr>`).join("")}</tbody></table></div></details>`;
  }).join("");
  const controls = `<table class="data-table"><thead><tr><th>Method</th><th>Path (on PRACTICE_APP_URL)</th><th>Body</th><th>What it does</th></tr></thead><tbody>
    ${(p.test_controls || []).map((c) => `<tr><td>${cell(c.method)}</td><td><code>${cell(c.path)}</code></td><td>${cell((c.body || []).join(", "))}</td><td>${cell(c.meaning)}</td></tr>`).join("")}</tbody></table>`;
  return `
    <div class="surface">
      <div class="section-header"><h3>Reference - ${esc(p.app_name || "practice application")}</h3></div>
      <div class="tabs" role="tablist">${tab("connect", hasConnect ? "Connect & accounts" : "Address & accounts", true)}${tab("pages", "Pages")}${tab("api", "API")}${tab("database", "Database")}${(p.rules || []).length ? tab("rules", "Business rules") : ""}${p.test_controls ? tab("controls", "Test controls") : ""}</div>
      ${panel("connect", connect, true)}${panel("pages", pages)}${panel("api", api)}${panel("database", db)}${(p.rules || []).length ? panel("rules", `<p class="text-muted">What the application does - the requirements a tester would be given. Exact on-screen messages aren't listed.</p><ol>${p.rules.map((r) => `<li>${esc(r)}</li>`).join("")}</ol>`) : ""}${p.test_controls ? panel("controls", controls) : ""}
    </div>`;
}

function round2AutomationReferenceHtml() {
  const s = round2AutomationState;
  if (s.reference_panel) return round2AutomationPanelHtml(s.reference_panel);
  const hasMockup = !!s.ui_mockup;
  const hasEnv = !!(s.environment && Object.keys(s.environment.fields || {}).length > 0);
  if (!hasMockup && !hasEnv) return "";
  const envFields = hasEnv ? Object.entries(s.environment.fields || {}) : [];
  return `
    <div class="surface">
      <div class="section-header"><h3>Reference material</h3></div>
      <div class="tabs" role="tablist">
        ${hasMockup ? `<button class="tab active" data-r4a-ref-tab="screens" onclick="round2AutomationReferenceTabClicked('screens')">Reference Screens</button>` : ""}
        ${hasEnv ? `<button class="tab${hasMockup ? "" : " active"}" data-r4a-ref-tab="environment" onclick="round2AutomationReferenceTabClicked('environment')">Test Environment</button>` : ""}
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
function round2AutomationReferenceTabClicked(name) {
  document.querySelectorAll("[data-r4a-ref-tab]").forEach((btn) => btn.classList.toggle("active", btn.dataset.r4aRefTab === name));
  document.querySelectorAll("[data-r4a-ref-panel]").forEach((panel) => panel.classList.toggle("active", panel.dataset.r4aRefPanel === name));
}

// The fixed practice environment is shown read-only and collapsed; only the
// candidate's part is editable (UI review item 8). It splits ONLY while the
// file still starts with the environment exactly as provided - if the
// assistant or anyone changed it, the whole file stays editable and nothing
// is hidden. Save/Run/Submit always send fixed part + box (round2AutomationCode),
// byte-identical to the file.
const r4aFixedPrefix = {};  // code box id -> the read-only text in front of it

function round2AutomationFixedPrefix(environmentCode) {
  const env = environmentCode || "";
  const m = /^[ \t]*(#|\/\/) *TODO: write your automated/m.exec(env);
  return m ? env.slice(0, m.index) : "";
}
function round2AutomationSplitCode(code, environmentCode) {
  const prefix = round2AutomationFixedPrefix(environmentCode);
  const text = code || "";
  if (prefix && text.startsWith(prefix)) return { fixed: prefix, editable: text.slice(prefix.length) };
  return { fixed: "", editable: text };
}
function round2AutomationCode(rowIndex) {
  const id = `r4a-code-${rowIndex}`;
  const el = document.getElementById(id);
  return el ? (r4aFixedPrefix[id] || "") + el.value : "";
}

function round2AutomationTcSectionHtml(row) {
  const tc = round2AutomationTcState(row.index);
  const unlocked = round2AutomationTcIsUnlocked(tc);
  const codeId = `r4a-code-${row.index}`;
  const split = round2AutomationSplitCode(tc.code, round2AutomationState && round2AutomationState.environment_code);
  r4aFixedPrefix[codeId] = split.fixed;
  const fixedLines = split.fixed ? split.fixed.split("\n").length - 1 : 0;
  const envHtml = split.fixed ? `
          <details class="r4a-env">
            <summary>Practice environment &middot; read-only &middot; lines 1&ndash;${fixedLines} (the app and helpers your test uses)</summary>
            <div class="code-with-lines r4a-env-code">${codeWithLineNumbersHtml(split.fixed.replace(/\n$/, ""))}</div>
          </details>` : "";
  // Order matches the actual workflow: prompt first (below), code
  // appears as a result of that and sits right under where the
  // candidate was just typing, then the test data it's about to run
  // against (a "run space", editable right before running - not
  // scattered after the result it's supposed to explain), THEN the Run
  // action, and the result comes last - only after everything it
  // depends on has already been shown.
  const codeSectionHtml = unlocked ? `
      <div class="section-header"><h3>Generated code &middot; your edits</h3></div>
      <div class="code-panel r4a-code-panel">
        <div class="code-panel-head"><span>Automation code</span><span>Editable - review before you trust a PASS</span></div>
        <div class="code-panel-body">
          ${envHtml}
          ${codeEditorHtml(codeId, split.editable, "round4-pilot-code r4a-code-editor", `data-first-line="${fixedLines + 1}"`)}
        </div>
      </div>
      ${round2AutomationTestDataHtml(row)}
      <div class="action-bar code-actions-sticky" style="border-top:none; margin-top:0">
        <span class="muted">Run uses exactly this file - the practice environment plus your code box. Your own edits are recorded separately from the assistant's.</span>
        <div class="action-bar-buttons">
          <button class="btn-secondary r4a-save-btn" onclick="round2AutomationSaveCodeClicked(${row.index})">Save my edit</button>
          <button class="btn-primary r4a-run-btn" onclick="round2AutomationRunClicked(${row.index})">Run</button>
        </div>
      </div>
      <div class="section-header" style="margin-top:var(--space-default)"><h3>Execution result</h3></div>
      <div id="r4a-run-result-${row.index}">${round2AutomationRunResultHtml(row.index, tc.last_run)}</div>` : `
      <p class="muted" style="margin-top:1.25rem">No code yet - describe what you want automated above. Once the assistant has enough detail to encode it without guessing, it'll write the first version here.</p>`;

  return `
    <div class="surface r4a-tc-panel" data-row-index="${row.index}" style="margin:var(--space-default) 0">
      <div class="section-header">
        <h2>Test case ${row.index + 1} <span class="muted" style="font-weight:400">- ${escapeHtml(row.title || "(untitled)")}</span></h2>
      </div>
      ${(row.refinements || []).length > 0 ? `<p class="muted" style="margin:0 0 0.5rem 0"><strong>Your refinement notes:</strong> ${row.refinements.map((n) => escapeHtml(n)).join(" &middot; ")}</p>` : ""}
      <p class="muted" style="margin:0 0 var(--space-compact) 0">AI-generated code may be buggy, incomplete, or subtly wrong even when it runs cleanly - review it before trusting a PASS.</p>

      <!-- Side by side (owner, 2026-09-28): the conversation with its reply box on the left, the code,
           Run and result on the right - both in view, instead of new messages landing far above the
           result the candidate is reading. Stacked on a narrow screen, reply box under the conversation. -->
      <div class="r4a-work">
        <div class="r4a-chat-col">
          <div class="section-header"><h3>Conversation with the assistant</h3></div>
          <div class="r4a-chat-log" id="r4a-chat-log-${row.index}">${round2AutomationTurnsHtml(tc.turns)}</div>
          <div class="field-label" style="margin-top:0.6rem">${unlocked ? "Tell the assistant what to change or add" : "Tell the assistant what to automate"}</div>
          <textarea id="r4a-prompt-${row.index}" class="ta-short ta-grow" rows="3" placeholder="Describe the steps, or answer the assistant - e.g. how to find an element on the page and which value to use."></textarea>
          <div class="row" style="justify-content:flex-end"><button class="btn-primary r4a-ask-btn" onclick="round2AutomationAskClicked(${row.index})">Ask AI</button></div>
          <p id="r4a-tc-status-${row.index}" class="muted" role="status" aria-live="polite" style="margin:0.25rem 0 0"></p>
        </div>
        <div class="r4a-code-col">${codeSectionHtml}</div>
      </div>
    </div>`;
}

function renderRound2AutomationLayout(box) {
  setWideLayout(true);
  const s = round2AutomationState;
  const scenario = round2EntryState.scenario;

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
          <button class="btn-primary" id="r4a-lock-language-btn" onclick="round2AutomationLockLanguageClicked()">Lock language</button>
        </div>
        <p id="r4a-status" class="muted"></p>
      </div>`;
    return;
  }

  // No more "pick both, then confirm" screen - test cases are selected
  // one at a time via round2AutomationNextPickHtml's dropdown, immutably per
  // pick (see routers/candidate.py's round2_automation_select), so the whole
  // rest of the page renders unconditionally once the language is
  // locked, whether zero, one or two test cases have been picked so far.
  const selected = s.selected || [];
  const remaining = (s.available_rows || []).filter((r) => !selected.some((sel) => sel.index === r.index));
  const lastSelected = selected.length > 0 ? selected[selected.length - 1] : null;
  const lastHasResult = lastSelected ? !!round2AutomationTcState(lastSelected.index).last_run : false;
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
      <p class="text-muted" style="margin:var(--space-compact) 0 0">Pick a test case below to automate it (your Round 1 design stays exactly as you wrote it). You can automate up to two, one at a time - you'll decide on a second only after finishing the first.</p>
    </div>

    <div class="section-header"><h2>Your Round 1 test cases</h2></div>
    ${round2AutomationTcTableHtml(s.available_rows || [], selected.map((r) => r.index))}
    ${round2AutomationReferenceHtml()}

    ${selected.map((r) => round2AutomationTcSectionHtml(r)).join("")}

    ${showPicker ? round2AutomationNextPickHtml(remaining) : ""}

    ${selected.length > 0 ? `
    <div class="action-bar">
      <span class="muted">Submitting ends Round 2 and moves you on - you can't return to it afterward.</span>
      <div class="action-bar-buttons">
        <button id="r4a-submit-btn" class="btn-primary" onclick="round2AutomationSubmitClicked()">Submit Round 2</button>
      </div>
    </div>` : ""}
    <p id="r4a-status" class="muted"></p>`;
  round2AutomationAfterRender();
}

// Where each code box was left, keyed by its id - every action re-renders
// the whole screen, and Save/Run must not throw the candidate back to line 1.
const r4aEditorView = {};
// The candidate's own test starts after ~100 lines of fixed practice
// environment (Run B: line 110 of 134). New code opens there - at the first
// test, or the "write your tests below" marker before any exists.
const R4A_OWN_CODE_RE = /^[ \t]*(async def test_|def test_|function test|(public |private |static |async )*void test|@Test\b|(#|\/\/) *TODO: write your automated)/m;

function round2AutomationAfterRender() {
  initCodeEditors(document.getElementById("round-view"));
  document.querySelectorAll("textarea.r4a-code-editor").forEach((el) => {
    const prev = r4aEditorView[el.id];
    if (prev && prev.code === el.value) {
      el.scrollTop = prev.scrollTop;
    } else {
      const m = R4A_OWN_CODE_RE.exec(el.value);
      if (m) {
        const line = el.value.slice(0, m.index).split("\n").length - 1;
        const lineHeight = parseFloat(getComputedStyle(el).lineHeight) || 20;
        el.scrollTop = Math.max(0, (line - 2) * lineHeight);  // two lines of context above
      }
    }
    codeEditorSyncScroll(el);
    const remember = () => { r4aEditorView[el.id] = { code: el.value, scrollTop: el.scrollTop }; };
    remember();
    el.addEventListener("scroll", remember);
    el.addEventListener("input", remember);
  });
  autoGrowAll(document.getElementById("round-view"));  // prompts / test data restored from state
  // The conversation is capped in height (style.css) - keep the newest message in view.
  document.querySelectorAll(".r4a-chat-log").forEach((log) => { log.scrollTop = log.scrollHeight; });
}

// Shared by every action except submit: call the endpoint, re-fetch state,
// re-render - the server
// is the single source of truth for what actually persisted.
// rowIndex: a test case's own action (Ask / Save / Run / test data) - its
// progress and any error show beside that test case's buttons, not in the
// page-bottom status line (a failed Ask AI used to look like "nothing
// happened" because the error appeared below Submit).
async function round2AutomationAction(actionFn, rowIndex = null, workingText = "") {
  if (round2AutomationBusy) return;
  round2AutomationBusy = true;
  round2AutomationSetBusy(true);
  const statusEl = (rowIndex !== null && document.getElementById(`r4a-tc-status-${rowIndex}`)) || document.getElementById("r4a-status");
  if (statusEl) { statusEl.className = "muted"; statusEl.textContent = workingText; }
  try {
    await actionFn();
    round2AutomationState = await api("/candidate/round/2/auto/state");
    renderRound2AutomationLayout(document.getElementById("round-view"));
  } catch (e) {
    if (statusEl) { statusEl.className = "error-text"; statusEl.textContent = e.message; }
  } finally {
    round2AutomationBusy = false;
    round2AutomationSetBusy(false);
  }
}

function round2AutomationLockLanguageClicked() {
  const language = document.getElementById("r4a-language-select").value;
  const statusEl = document.getElementById("r4a-status");
  if (!language) {
    if (statusEl) statusEl.textContent = "Pick a language first.";
    return;
  }
  round2AutomationAction(() => api("/candidate/round/2/auto/language", { method: "POST", body: JSON.stringify({ language }) }));
}

function round2AutomationAskClicked(rowIndex) {
  const promptEl = document.getElementById(`r4a-prompt-${rowIndex}`);
  const prompt = promptEl.value.trim();
  if (!prompt) return;
  round2AutomationAction(() => api("/candidate/round/2/auto/turn", { method: "POST", body: JSON.stringify({ candidate_prompt: prompt, row_index: rowIndex }) }),
    rowIndex, "Asking the assistant - writing code can take up to a minute or two...");
}

function round2AutomationSaveCodeClicked(rowIndex) {
  const code = round2AutomationCode(rowIndex);
  if (!code) return;
  round2AutomationAction(() => api("/candidate/round/2/auto/code", { method: "POST", body: JSON.stringify({ code, row_index: rowIndex }) }), rowIndex, "Saving...");
}

function round2AutomationRunClicked(rowIndex) {
  const code = round2AutomationCode(rowIndex);
  round2AutomationAction(() => api("/candidate/round/2/auto/run", { method: "POST", body: JSON.stringify({ code, row_index: rowIndex }) }), rowIndex, "Running...");
}

async function round2AutomationSubmitClicked() {
  if (round2AutomationBusy) return;
  const selected = round2AutomationState.selected || [];
  const statusEl = document.getElementById("r4a-status");

  // Every selected test case needs its own generated code and its own
  // run - gathered independently per TC, not one shared check for the
  // whole round (see Round2AutomationSubmitCreate.entries). No candidate-
  // written interpretation required: whether the run genuinely proves
  // the expected result is scored from the code and result alone.
  const entries = [];
  for (const r of selected) {
    const sectionEl = document.querySelector(`.r4a-tc-panel[data-row-index="${r.index}"]`);
    const tc = round2AutomationTcState(r.index);
    if (!round2AutomationTcIsUnlocked(tc)) {
      if (sectionEl) sectionEl.scrollIntoView({ behavior: "smooth", block: "center" });
      if (statusEl) statusEl.textContent = `Ask the assistant to generate code for "${r.title || `test case ${r.index}`}" before submitting.`;
      return;
    }
    if (!tc.last_run) {
      if (sectionEl) sectionEl.scrollIntoView({ behavior: "smooth", block: "center" });
      if (statusEl) statusEl.textContent = `Run "${r.title || `test case ${r.index}`}" at least once before submitting.`;
      return;
    }
    entries.push({ row_index: r.index, code: round2AutomationCode(r.index) });
  }

  round2AutomationBusy = true;
  round2AutomationSetBusy(true);
  if (statusEl) statusEl.textContent = "";
  try {
    await api("/candidate/round/2/auto/submit", { method: "POST", body: JSON.stringify({ entries }) });
    stopTimer();
    disarmTabGuard();
    refreshCandidateNav();
  } catch (e) {
    if (statusEl) statusEl.textContent = e.message;
    round2AutomationBusy = false;
    round2AutomationSetBusy(false);
  }
}

// ---- Round 2 view dispatch and time-up (moved from round2_legacy.js) ----

async function renderRound2AutomationView(box) {
  box.innerHTML = loadingHtml();
  try {
    round2EntryState = await api("/candidate/round/2/state");
  } catch (e) {
    box.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
    return;
  }
  // Round 2 is the AI-Assisted Test Automation round. Its two earlier
  // formats (the simulated conversation and the pilot) are retired - HR can
  // still read their stored results, but a candidate can't take them.
  if (!(round2EntryState.scenario && round2EntryState.scenario.is_auto)) {
    box.innerHTML = `<h3>Round 2</h3><p class="muted">This Round 2 format has been retired - please contact HR.</p>`;
    return;
  }
  await loadRound2Automation(box);

  if (!timerHandle) {
    const submission = round2EntryState.submission;
    const deadline = new Date(submission.started_at + "Z").getTime() + attemptTimeLimit(submission, round2EntryState.scenario) * 60 * 1000;
    startTimer(deadline, round2AutomationSubmit, 2);
  }
}

// Round 2's time is up: submit every selected test case's current code -
// the same request as the Submit button. If that's rejected (a test case
// never generated or never run), expire Round 2 so it still ends and is
// scored. Before this, time-up sent a field the endpoint doesn't have
// ("validation", silently dropped) and then expired ROUND 4 - a leftover
// from the Round 2<->4 swap - leaving Round 2 open and the timer firing again.
async function round2AutomationSubmit() {
  const timerEl = document.getElementById("timer");
  if (timerEl) timerEl.textContent = "Time's up - submitting automatically...";
  const selected = (round2AutomationState && round2AutomationState.selected) || [];
  const entries = selected.map((r) => ({ row_index: r.index, code: round2AutomationCode(r.index) || null }));
  try {
    await api("/candidate/round/2/auto/submit", { method: "POST", body: JSON.stringify({ entries }) });
    stopTimer();
    disarmTabGuard();
    refreshCandidateNav();
  } catch (e) {
    await forceExpireRound(2);
  }
}
