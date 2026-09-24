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
      + attemptTimeLimit(submission, round3CodingState.scenario) * 60 * 1000;
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

// The task's fixed stdin/stdout format (Scenario.round3_io_format) - the
// same text the hidden tests were generated from, shown with the task so
// the candidate is never graded on a format they weren't told.
function renderRound3IoFormat(fmt) {
  if (!fmt) return "";
  return `
    <div class="panel-inset round3-io-format">
      <p><strong>Input:</strong> ${escapeHtml(fmt.input)}</p>
      <p><strong>Output:</strong> ${escapeHtml(fmt.output)}</p>
    </div>
  `;
}

function renderRound3CodingLayout(box) {
  const s = round3CodingState;
  if (!s) return;
  setWideLayout(true);
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
    ${renderRound3IoFormat(s.scenario.round3_io_format)}
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
        <textarea id="round3-coding-message" class="ta-short ta-grow" placeholder="What do you want the assistant to do next?" oninput="round3CodingOnComposerInput(this.value)">${escapeHtml((s.submission.content && s.submission.content.draft_prompt) || "")}</textarea>
        <div class="row">
          <button id="round3-coding-send-btn" onclick="round3CodingSendMessage()">Send</button>
        </div>
      </div>
      <div class="panel-inset round3-coding-pane">
        <p class="muted round3-pane-label">Code</p>
        ${round3CodingEditingCode ? `
          ${codeEditorHtml("round3-coding-code-edit", latestCode)}
          <div class="row code-actions-sticky">
            <button id="round3-coding-save-btn" onclick="round3CodingSaveDirectEdit()">Save</button>
            <button onclick="round3CodingCancelDirectEdit()">Cancel</button>
          </div>
        ` : `
          <div class="code-snippet code-with-lines" id="round3-coding-code">${latestCode ? codeWithLineNumbersHtml(latestCode) : '<div class="code-line-content">(no code yet)</div>'}</div>
          <div class="row code-actions-sticky">
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
  initCodeEditors(box);
  autoGrowTextarea(document.getElementById("round3-coding-message"));  // a restored draft
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

// One code editor for Round 2 (automation) and Round 3 (coding): a textarea
// with a line-number gutter, so "line 118" in a stack trace can be found.
// The gutter is always "<textarea id>-gutter".
function codeEditorHtml(id, code, extraClass = "", attrs = "") {
  return `
    <div class="code-editor-wrap">
      <div class="code-editor-gutter" id="${id}-gutter" aria-hidden="true"><span>1</span></div>
      <textarea id="${id}" class="code-editor-input ${extraClass}" spellcheck="false" wrap="off"
        oninput="codeEditorUpdateGutter(this)" onscroll="codeEditorSyncScroll(this)" ${attrs}>${escapeHtml(code || "")}</textarea>
    </div>`;
}
// Only rewrites the numbers when the line count actually changed, so a
// keystroke inside a line doesn't rebuild the gutter on every input event.
function codeEditorUpdateGutter(textarea) {
  const gutter = document.getElementById(`${textarea.id}-gutter`);
  if (!gutter) return;
  // Numbers only line up if the gutter uses the textarea's exact type
  // metrics - the Round 2 (dark panel) and Round 3 boxes differ in padding.
  const cs = getComputedStyle(textarea);
  for (const prop of ["fontSize", "lineHeight", "paddingTop", "paddingBottom"]) gutter.style[prop] = cs[prop];
  // data-first-line: the box may hold only the end of a file (Round 2 keeps
  // the practice environment read-only above it) - number from the real line.
  const first = parseInt(textarea.dataset.firstLine || "1", 10);
  const lineCount = textarea.value.split("\n").length;
  if (gutter.children.length !== lineCount || gutter.dataset.first !== String(first)) {
    gutter.innerHTML = Array.from({ length: lineCount }, (_, i) => `<span>${first + i}</span>`).join("");
    gutter.dataset.first = String(first);
  }
  codeEditorSyncScroll(textarea);
}
function codeEditorSyncScroll(textarea) {
  const gutter = document.getElementById(`${textarea.id}-gutter`);
  if (gutter) gutter.scrollTop = textarea.scrollTop;
}
// Existing code (resuming an edit, a re-render, code from the assistant)
// needs its real line numbers straight away, not after the next keystroke.
function initCodeEditors(root = document) {
  root.querySelectorAll("textarea.code-editor-input").forEach(codeEditorUpdateGutter);
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
