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

// Coding screens (Round 2 automation, Round 3) get a wider page than the
// 76rem reading width - code and conversation need the room. Every other
// screen resets it (loadRound, selectCandidatePage, logout).
function setWideLayout(on) {
  const main = document.querySelector(".app-content");
  if (main) main.classList.toggle("is-wide", Boolean(on));
}

// The time limit a candidate starting now gets - HR Settings' round limit
// when one is set, else the scenario's own (Scenario.round_time_limit_minutes).
function scenarioTimeLimit(scenario) {
  return scenario.round_time_limit_minutes || scenario.time_limit_minutes;
}
// A started attempt's own limit, recorded when it began - an HR change
// mid-round never moves it (Submission.time_limit_minutes).
function attemptTimeLimit(submission, scenario) {
  return submission.time_limit_minutes || scenarioTimeLimit(scenario);
}

async function loadRound(n) {
  currentRound = n;
  setWideLayout(false);
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
        <p class="muted">Time limit: ${scenarioTimeLimit(scenario)} minutes, starting once you confirm below.</p>
      `;
      showRound3CodingIntro();
      return;
    }
    if (n === 2) {
      // No Start button here - the briefing modal is the only way in, and
      // its "Got it - Start Round 2" (confirmStartRound4Auto) starts the
      // timer. Round 2's earlier formats (simulated conversation, pilot)
      // are retired - HR can still read their results; a candidate can't start them.
      if (!scenario.is_auto) {
        box.innerHTML = `<h3>Round ${n}</h3><p class="muted">This Round 2 format has been retired - please contact HR.</p>`;
        return;
      }
      box.innerHTML = `
        <h3>Round ${n}: ${escapeHtml(scenario.title)}</h3>
        ${formatScenarioDescription(scenario.description)}
        <p class="muted">Time limit: ${scenarioTimeLimit(scenario)} minutes, starting once you confirm below.</p>
      `;
      showRound4AutoIntro(scenarioTimeLimit(scenario));
      return;
    }
    // Same pattern as round 4: no separate Start button - the briefing
    // modal appears the instant the round is opened, and its own button
    // is what actually starts the timer.
    box.innerHTML = `
      <h3>Round ${n}: ${escapeHtml(scenario.title)}</h3>
      ${formatScenarioDescription(scenario.description)}
      <p class="muted">Time limit: ${scenarioTimeLimit(scenario)} minutes, starting once you confirm below.</p>
    `;
    showRoundIntro(n, scenarioTimeLimit(scenario), environment);
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

function round4OnLanguageSelectChange() {
  const language = document.getElementById("round4-language-select").value;
  document.getElementById("round4-start-btn").disabled = !language;
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
  const deadline = new Date(submission.started_at + "Z").getTime() + attemptTimeLimit(submission, scenario) * 60 * 1000;
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

// Grows a textarea with what's typed instead of scrolling inside a fixed
// box - the CSS min-height is still the starting size.
function autoGrowTextarea(el) {
  if (!el) return;
  el.style.height = "auto";
  el.style.height = `${el.scrollHeight + el.offsetHeight - el.clientHeight}px`;
}

// Every text box with .ta-grow (style.css - sized by purpose) grows as the
// candidate types; one listener covers them all. Boxes filled from a saved
// draft are sized by an explicit autoGrowTextarea/autoGrowAll after render.
document.addEventListener("input", (e) => {
  if (e.target instanceof HTMLTextAreaElement && e.target.classList.contains("ta-grow")) autoGrowTextarea(e.target);
});
function autoGrowAll(root = document) {
  root.querySelectorAll("textarea.ta-grow").forEach(autoGrowTextarea);
}

function renderInvestigationForm(box, scenario, submission) {
  rowCount = 0;
  box.innerHTML = `
    <h3>Round 4: ${escapeHtml(scenario.title)}</h3>
    ${formatScenarioDescription(scenario.description)}
    <p class="muted example-note">Example format (not a hint for this scenario): "Checked the application logs around the time of the issue for related error messages."</p>
    <div class="table-scroll">
      <table class="inv-table">
        <thead><tr><th class="inv-col-no">SI.No</th><th>Investigation area</th><th class="inv-col-action"><span class="sr-only">Actions</span></th></tr></thead>
        <tbody id="inv-rows"></tbody>
      </table>
    </div>
    <div class="row">
      <button onclick="addInvestigationRow()">+ Add row</button>
    </div>
    <h4>Possible Root Cause</h4>
    <p class="muted">What you investigated, which areas you eliminated, and your conclusion.</p>
    <p class="muted example-note">Example format: "The [component] shows [incorrect behavior] when [condition]. Ruled out [alternative cause] because [reason]. Root cause is [cause], confirmed by [evidence]."</p>
    <textarea id="inv-root-cause" class="ta-long ta-grow" oninput="scheduleRoundDraftSave(4, round2DraftPayload); round2UpdateSubmitState()"></textarea>
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
  autoGrowTextarea(document.getElementById("inv-root-cause"));
  round2UpdateSubmitState();
  const deadline = new Date(submission.started_at + "Z").getTime() + attemptTimeLimit(submission, scenario) * 60 * 1000;
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
    <td><textarea class="inv-area ta-short ta-grow" rows="2" placeholder="One area you investigated and what you found" oninput="scheduleRoundDraftSave(4, round2DraftPayload); round2UpdateSubmitState()"></textarea></td>
    <td><button class="btn-ghost btn-sm inv-remove" onclick="removeInvestigationRow('inv-row-${id}')">Remove</button></td>
  `;
  tbody.appendChild(tr);
  if (initial) tr.querySelector(".inv-area").value = initial.area || "";
  autoGrowTextarea(tr.querySelector(".inv-area"));
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
  document.querySelectorAll("#inv-rows tr").forEach((tr, i) => {
    tr.querySelector(".inv-no").textContent = i + 1;
    // Screen readers otherwise hear "text area" and a row of identical "Remove" buttons.
    tr.querySelector(".inv-area").setAttribute("aria-label", `Investigation area ${i + 1}`);
    tr.querySelector(".inv-remove").setAttribute("aria-label", `Remove investigation area ${i + 1}`);
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

// ---- Moved from the retired Round 2 script (round2_legacy.js): Round 1's
// test-case rows, the round timer, time-up expiry and reference panels -
// live code that shared a file with the retired simulated Round 2. ----

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

let round4DefaultLanguage = "python";

const ROUND4_CODE_LANGUAGE_OPTIONS = [
  ["python", "Python"],
  ["java", "Java"],
  ["javascript", "JavaScript"],
];

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
