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
    const deadline = new Date(submission.started_at + "Z").getTime() + attemptTimeLimit(submission, round4State.scenario) * 60 * 1000;
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
      <textarea id="round4-message" class="ta-short ta-grow" placeholder="What do you want the assistant to do or check next?" oninput="round4OnComposerInput(${tcId}, this.value)">${escapeHtml(draftText)}</textarea>
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
      <td data-label="Test Case / Scenario">Verify login with valid credentials</td>
      <td data-label="Preconditions">User has a registered account</td>
      <td data-label="Steps">1. Open the login page. 2. Enter a valid username and password. 3. Click "Login".</td>
      <td data-label="Test Data">username = jordan.rivera@example.com; password = Passw0rd!2026</td>
      <td data-label="Expected Result / Assertions">User is redirected to /dashboard and the header shows "Welcome, Jordan".</td>
      <td class="tc-actions"></td>
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
    <td data-label="Test Case / Scenario"><input class="tc-title" oninput="scheduleRoundDraftSave(1, round1DraftPayload); round1UpdateSubmitState()" /></td>
    <td data-label="Preconditions"><input class="tc-pre" oninput="scheduleRoundDraftSave(1, round1DraftPayload)" /></td>
    <td data-label="Steps"><textarea class="tc-steps ta-grow" oninput="scheduleRoundDraftSave(1, round1DraftPayload); round1UpdateSubmitState()"></textarea></td>
    <td data-label="Test Data"><textarea class="tc-data ta-grow" placeholder="Concrete values, e.g. amount = 0.00; card = 4000-0000-0000-0069" oninput="scheduleRoundDraftSave(1, round1DraftPayload)"></textarea></td>
    <td data-label="Expected Result / Assertions"><textarea class="tc-expected ta-grow" oninput="scheduleRoundDraftSave(1, round1DraftPayload); round1UpdateSubmitState()"></textarea></td>
    <td class="tc-actions"><button class="btn-danger btn-sm tc-remove" onclick="removeRow('row-${id}')">Remove</button></td>
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
  autoGrowAll(tr);
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
  document.querySelectorAll("#tc-rows tr").forEach((tr, i) => {
    tr.querySelector(".tc-no").textContent = i + 1;
    // Each field names its column and row - the column headers are hidden
    // when rows become cards on smaller screens (style.css, .tc-table).
    tr.querySelectorAll("td[data-label]").forEach((td) => {
      const field = td.querySelector("input, textarea");
      if (field) field.setAttribute("aria-label", `${td.dataset.label}, test case ${i + 1}`);
    });
    tr.querySelector(".tc-remove")?.setAttribute("aria-label", `Remove test case ${i + 1}`);
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
