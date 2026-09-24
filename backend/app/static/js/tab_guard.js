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

// Logs one fullscreen-exit and reports back the running strike count -
// see candidate.py's log_tab_switch, the single source of truth for the
// count (never tracked separately client-side, so a page reload mid-round
// can't desync it). On network failure, treat it as "no strike yet" -
// same fail-open spirit as the old fire-and-forget version, since a
// dropped request here must never itself end a candidate's round.
async function logTabSwitch(roundNumber) {
  if (role !== "candidate") return { strike_count: 0, round_ended: false };
  try {
    return await api(`/candidate/round/${roundNumber}/tab-switch`, { method: "POST" });
  } catch (e) {
    return { strike_count: 0, round_ended: false };
  }
}

// Shared by both exit-detection paths below (fullscreenchange and the
// passive visibilitychange/blur fallback) - the 3rd strike ends the
// round the same way regardless of which path caught it.
function handleStrikeRoundEnded() {
  removeFsOverlay();
  const toast = document.getElementById("tab-switch-toast");
  if (toast) toast.remove();
  const timerEl = document.getElementById("timer");
  if (timerEl) timerEl.textContent = "Round ended - left fullscreen 3 times. Saving your work...";
  stopTimer();
  disarmTabGuard();
  refreshCandidateNav();
}

document.addEventListener("fullscreenchange", () => {
  if (!fsGuardArmed || !fsGuardActive) return;
  if (document.fullscreenElement) {
    removeFsOverlay();
    return;
  }
  const roundNumber = fsGuardRound;
  logTabSwitch(roundNumber).then((result) => {
    if (result.round_ended) {
      handleStrikeRoundEnded();
    } else {
      showFsOverlay(roundNumber, result.strike_count);
    }
  });
});

function showFsOverlay(roundNumber, strikeCount) {
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
    const warn = existing.querySelector("#fs-guard-warning");
    if (warn) warn.textContent = `Warning ${strikeCount} of 3 - one more and this round ends automatically, scored on what you've written so far.`;
    return;
  }
  const overlay = document.createElement("div");
  overlay.id = "fs-guard-overlay";
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-box">
      <h3>Fullscreen required</h3>
      <p>This round must be taken in fullscreen. Leaving it has been logged and is visible to HR.</p>
      <p id="fs-guard-warning"><strong>Warning ${strikeCount} of 3</strong> - one more and this round ends automatically, scored on what you've written so far.</p>
      <div class="row">
        <button onclick="reenterFullscreen()">Return to fullscreen</button>
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
  // few times quickly can trigger that. This is always temporary (a
  // few seconds), never permanent, so simply retrying this same button
  // shortly after is the way through - no separate "continue without
  // fullscreen" escape is needed (and none is offered: every exit
  // already counted as a strike the instant it happened, in
  // logTabSwitch above, regardless of whether re-entry ever succeeds).
  document.documentElement.requestFullscreen().catch(() => {
    const statusEl = document.getElementById("fs-guard-status");
    if (statusEl) statusEl.textContent = "Couldn't re-enter fullscreen yet - wait a moment and try again.";
  });
}

// Fallback for browsers/contexts where fullscreen enforcement isn't
// available at all (fsGuardActive stays false) - same passive log +
// dismissible toast as before, so there's still some signal instead of
// no guard whatsoever. Exits here count toward the same 3-strike total
// (see log_tab_switch) - fullscreen being unavailable isn't a reason to
// exempt this path from the same consequence.
let tabSwitchPending = false;

document.addEventListener("visibilitychange", () => {
  if (fsGuardArmed && !fsGuardActive && document.hidden && !tabSwitchPending) {
    tabSwitchPending = true;
    const roundNumber = fsGuardRound;
    logTabSwitch(roundNumber).then((result) => {
      if (result.round_ended) handleStrikeRoundEnded();
    });
  }
});
window.addEventListener("blur", () => {
  if (fsGuardArmed && !fsGuardActive && !tabSwitchPending) {
    tabSwitchPending = true;
    logTabSwitch(fsGuardRound).then((result) => {
      if (result.round_ended) {
        tabSwitchPending = false; // nothing to show a returning-focus toast for - the round is already over
        handleStrikeRoundEnded();
      }
    });
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

// escapeHtml() is only safe for a text node - per the HTML serialization
// spec it never escapes " or ', so dropping its output inside a quoted
// HTML attribute (title="...", value="...", an inline onclick="...'...'")
// doesn't protect against a value that itself contains that quote
// character breaking out of the attribute. Use this instead anywhere
// untrusted (or LLM-echoed, which can reproduce untrusted text verbatim)
// text is interpolated into an attribute in a template string.
function escapeAttr(str) {
  return escapeHtml(str).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
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
