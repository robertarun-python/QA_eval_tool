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
        <p class="muted">Time limit: ${scenarioTimeLimit(scenario)} minutes, starting once you confirm below.</p>
      `;
      if (scenario.is_pilot) {
        showRound4PilotIntro(scenarioTimeLimit(scenario));
      } else if (scenario.is_auto) {
        showRound4AutoIntro(scenarioTimeLimit(scenario));
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
