// ==================== Round 4 pilot ("Focused Automation Pilot") ====================
// Candidate UI for the single-file automation exercise - see
// routers/candidate.py's /round/4/pilot/* endpoints and
// renderRound4View's is_pilot branch above. Renders into the same
// #round-view container every other round uses; no template/index.html
// changes needed. Deliberately separate render/action functions from the
// legacy conversational round 4 UI above (renderRound4Layout etc.) -
// same reasoning the backend already used to keep the two flows apart.
//
// Code editing model: the code textarea is directly editable. Run and
// Submit both read its current value and send it as {code: ...} to
// /pilot/run and /pilot/submit (see Round4PilotCodeUpdate/
// _apply_pilot_code_edit in routers/candidate.py) - the server persists
// that edit BEFORE executing/scoring, so both always act on exactly
// what's in the editor at the moment the button was clicked, never a
// stale server-side copy. An AI turn (Ask AI) can still update the same
// buffer via code_after, same as before - the two ways of changing the
// code (typing directly, or an AI-suggested edit) share one buffer.

let round4PilotBusy = false; // guards against overlapping Run/Ask/Clarify/Submit calls

function round4PilotSetBusy(busy) {
  round4PilotBusy = busy;
  ["round4-pilot-run-btn", "round4-pilot-ask-btn", "round4-pilot-clarify-btn", "round4-pilot-submit-btn"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = busy;
  });
}

function renderRound4PilotTurnsHtml() {
  const turns = round4State.pilot_turns || [];
  if (turns.length === 0) return `<p class="muted">No messages yet - ask the assistant something below.</p>`;
  return turns.map((t) => `
    <div class="panel-inset" style="margin-bottom:0.5rem">
      <p style="margin:0 0 0.35rem 0"><strong>You:</strong> ${escapeHtml(t.candidate_prompt)}</p>
      <p style="margin:0 0 ${t.code_after ? "0.35rem" : "0"} 0"><strong>Assistant</strong> <span class="muted">(${escapeHtml(t.response_kind)})</span>: ${escapeHtml(t.response_message)}</p>
      ${t.code_after ? `<pre class="code-snippet">${escapeHtml(t.code_after)}</pre>` : ""}
    </div>
  `).join("");
}

function renderRound4PilotRunResultHtml(run) {
  if (!run) return `<p class="muted">Not run yet.</p>`;
  return `
    <div class="panel-inset">
      ${run.timed_out ? `<p style="color:var(--warn)">Timed out.</p>` : ""}
      ${run.infra_error ? `<p style="color:var(--warn)">The execution service had a problem - try running again.</p>` : ""}
      <p class="muted" style="margin:0 0 0.3rem 0">Exit code: ${run.exit_code === null || run.exit_code === undefined ? "-" : run.exit_code}</p>
      ${run.stdout ? `<pre class="code-snippet">${escapeHtml(run.stdout)}</pre>` : `<p class="muted">No stdout.</p>`}
      ${run.stderr ? `<pre class="code-snippet round3-coding-stderr">${escapeHtml(run.stderr)}</pre>` : ""}
    </div>
  `;
}

function renderRound4PilotLayout(box) {
  const s = round4State;
  box.innerHTML = `
    <h3>Round 2: ${escapeHtml(s.scenario.title)}</h3>
    ${formatScenarioDescription(s.scenario.description)}
    <div class="panel-inset" style="margin-bottom:0.85rem">
      <p class="muted" style="margin:0">You're extending the existing automation below with the AI assistant's help. Ask it to explain code, help debug a failure, or make one specific, narrow change - it won't design your test strategy or write the whole thing for you, and you're responsible for reviewing anything it changes before you rely on it. If anything in the requirement is ambiguous, ask for a clarification rather than guessing.</p>
    </div>

    <h4>Current automation code</h4>
    <textarea id="round4-pilot-code" class="code-textarea round4-pilot-code">${escapeHtml(s.pilot_code || "")}</textarea>
    <p class="muted" style="margin:0.3rem 0 1rem 0">Edit this directly, or ask the assistant for a narrow change below - Run and Submit always use exactly what's in this box.</p>

    <h4>Run</h4>
    <div class="row" style="margin-bottom:0.5rem">
      <button id="round4-pilot-run-btn" onclick="round4PilotRunClicked()">Run</button>
    </div>
    <div id="round4-pilot-run-result">${renderRound4PilotRunResultHtml(s.pilot_last_run)}</div>

    <h4 style="margin-top:1.25rem">Ask the assistant</h4>
    <div class="field-row">
      <div class="field">
        <textarea id="round4-pilot-prompt" class="ta-short ta-grow" rows="2" placeholder="e.g. Explain what ApiHelper.submit_transaction does, or: add a test that automates the transaction flow for a valid customer, reusing the existing helpers."></textarea>
      </div>
      <button id="round4-pilot-ask-btn" onclick="round4PilotAskAIClicked()">Ask AI</button>
    </div>

    <h4 style="margin-top:1.25rem">Requirement clarification</h4>
    ${s.pilot_clarification ? `
      <div class="panel-inset" style="margin-bottom:0.5rem">
        <p style="margin:0 0 0.35rem 0"><strong>You asked:</strong> ${escapeHtml(s.pilot_clarification.question)}</p>
        <p style="margin:0">${escapeHtml(s.pilot_clarification.response)}</p>
      </div>
    ` : `<p class="muted">You haven't asked for a clarification yet.</p>`}
    <div class="field-row">
      <div class="field">
        <input id="round4-pilot-clarify-input" type="text" maxlength="2000" placeholder="e.g. What does &quot;persisted correctly&quot; mean here?" />
      </div>
      <button id="round4-pilot-clarify-btn" onclick="round4PilotClarifyClicked()">Clarify Requirement</button>
    </div>

    <h4 style="margin-top:1.25rem">Conversation history</h4>
    <div id="round4-pilot-turns">${renderRound4PilotTurnsHtml()}</div>

    <div class="row" style="margin-top:1.25rem">
      <button id="round4-pilot-submit-btn" class="btn-block" onclick="round4PilotSubmitClicked()">Submit Round 2</button>
    </div>
    <p id="round4-pilot-status" class="muted"></p>
  `;
}

// Shared by Run/Ask AI/Clarify: call the endpoint, then re-fetch full
// state and re-render - simpler and less bug-prone than patching three
// different DOM regions with three different response shapes, and
// round4State (source of truth for the next render) never drifts from
// what the server actually persisted. Submit is handled separately
// below since a successful submit ends the round instead of re-rendering it.
async function round4PilotAction(actionFn) {
  if (round4PilotBusy) return;
  round4PilotBusy = true;
  round4PilotSetBusy(true);
  const statusEl = document.getElementById("round4-pilot-status");
  if (statusEl) statusEl.textContent = "";
  try {
    await actionFn();
    round4State = await api("/candidate/round/2/state");
    renderRound4PilotLayout(document.getElementById("round-view"));
  } catch (e) {
    const s = document.getElementById("round4-pilot-status");
    if (s) s.textContent = e.message;
  } finally {
    round4PilotBusy = false;
  }
}

function round4PilotRunClicked() {
  const code = document.getElementById("round4-pilot-code").value;
  round4PilotAction(() => api("/candidate/round/2/pilot/run", { method: "POST", body: JSON.stringify({ code }) }));
}

function round4PilotAskAIClicked() {
  const input = document.getElementById("round4-pilot-prompt");
  const prompt = input.value.trim();
  if (!prompt) return;
  round4PilotAction(() => api("/candidate/round/2/pilot/turn", { method: "POST", body: JSON.stringify({ candidate_prompt: prompt }) }));
}

function round4PilotClarifyClicked() {
  const input = document.getElementById("round4-pilot-clarify-input");
  const question = input.value.trim();
  if (!question) return;
  round4PilotAction(() => api("/candidate/round/2/pilot/clarify", { method: "POST", body: JSON.stringify({ question }) }));
}

async function round4PilotSubmitClicked() {
  if (round4PilotBusy) return;
  round4PilotBusy = true;
  round4PilotSetBusy(true);
  const statusEl = document.getElementById("round4-pilot-status");
  if (statusEl) statusEl.textContent = "";
  const code = document.getElementById("round4-pilot-code").value;
  try {
    await api("/candidate/round/2/pilot/submit", { method: "POST", body: JSON.stringify({ code }) });
    stopTimer();
    disarmTabGuard();
    refreshCandidateNav();
  } catch (e) {
    // Round still in progress (e.g. "Make some changes before submitting.") -
    // timer/guard must stay engaged, same as legacy round4Submit's own catch.
    if (statusEl) statusEl.textContent = e.message;
    round4PilotBusy = false;
    round4PilotSetBusy(false);
  }
}
