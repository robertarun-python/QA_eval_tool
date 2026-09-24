# Test catalogue

What is checked, where, and what is still open. Everything here runs offline
(`scripts/check.sh`) unless marked **live**. Written from the Sep 2026 audit
(branch `qa/full-audit`).

## How it is checked

| Layer | What | Where |
|---|---|---|
| Unit and service | Rounds, scoring, AI reply handling with a faked model | `tests/test_*.py` |
| API robustness | Every endpoint: no login (401), wrong role (403), bad or huge ids, junk bodies. Never a 500 | `tests/test_api_robustness.py` |
| Page/API contract | Every request the page makes hits a real route with fields it accepts | `tests/test_page_api_contract.py` |
| Browser (fake AI) | Real Chromium against a throwaway server: all four rounds, HR report, time-up, phone layout | `tests/e2e/` |
| Anti-cheating | 44 cheating attempts against a model that always gives in; nothing may reach the candidate | `tests/redteam/` |
| Sandbox | Candidate code can't read secrets or the database, reach the network or spawn processes | `tests/test_execution_sandbox.py` |
| Real-data audits | Stored answers fit their schemas; anti-cheating checks raise no false alarms on real candidates | `python -m app.audit_content`, `python -m app.audit_guards` |
| Live AI (**live**, paid, approval only) | Saved Round 3 cases against the real model | `tests/replay/` (`RUN_LLM_REPLAY=1`) |

## Candidate journey

| Case | Type | Covered by |
|---|---|---|
| Sign in; wrong password; empty form | acceptance, negative | e2e, `test_auth.py` |
| Password guessing throttled (5 per 15 min per account) | negative | `test_api_robustness.py` |
| Each round: intro, work, submit, next round unlocks | acceptance | `e2e/test_candidate_flow.py` |
| Rounds out of order refused | negative | `test_guardrails_*`, round tests |
| Submit disabled until a test case is complete (R1) / a row and root cause exist (R4) | negative | e2e exploration, round tests |
| Double-click Submit sends once | edge | page guard (`roundSubmitInFlight`), exploration |
| Reload mid-round keeps work (autosave) | edge | `test_round_draft_autosave.py`, exploration |
| Time runs out: the page submits the right round itself | edge | `e2e/test_time_up.py` (mutation-checked) |
| Time runs out and the candidate never returns: server closes it | edge | `test_lazy_expiry.py`, `test_round_expiry.py` |
| Second tab / second login | edge | `test_auth.py`, exploration |
| Tab switches / leaving fullscreen flagged, third ends the round | negative | `test_tab_switch_guard.py` |
| Phone-sized screen: clear "use a computer" notice | edge | `e2e/test_layout.py` |
| AI unavailable / bad reply: clear message, nothing saved wrong | negative | `test_llm_bad_replies.py`, `test_llm_call_resilience.py` |

## AI assistant (cheating)

| Case | Type | Covered by |
|---|---|---|
| Ask for the whole solution, reworded, polite, other languages, encoded | negative | `redteam` (R3: 30 attempts) |
| Piece-by-piece extraction; "you decide"; fake HR/system authority | negative | `redteam` |
| R2: invent test cases, data, assertions; extra tests | negative | `redteam` (R2: 14), `test_round2_changed_values.py` |
| Values the candidate gave reach the code unchanged | acceptance | `test_round2_changed_values.py` |
| Real candidates not blocked by the checks | acceptance | `app.audit_guards` (89 messages, 19 code turns) |

## HR

| Case | Type | Covered by |
|---|---|---|
| Publish / go live; retired Round 2 formats refused | acceptance, negative | `test_guardrails_batch_c.py`, round tests |
| Round time limits from Settings; 0 or blank rejected | acceptance, negative | `test_round_time_limits.py`, `e2e/test_hr.py` |
| Candidate list, filters and summary agree; report opens | acceptance | `e2e/test_hr.py` |
| Retry / override scoring; scoring interrupted by a restart becomes retryable | edge | `test_score_override.py`, `test_interrupted_scoring.py` |
| First-run race creating the settings row | edge | `test_api_robustness.py` |

## Integration and scale

| Case | Result (fake AI) |
|---|---|
| 25 candidates, all four rounds at once (autosave, AI turns, sandboxed runs, submits, scoring) | 0 errors, 100 of 100 rounds scored, no database locks, p95 under 0.4 s |
| Real AI latency (10-60 s a turn) | Not measured. The server's 40-thread pool is the ceiling for simultaneous AI turns |

## Open items

1. **Round 3 score not tied to the real test results.** The hidden tests run and a pass
   rate is stored, but correctness and final score come from the AI. Needs an HR
   decision (how far a score may differ from the pass rate).
2. **Live runs pending approval:** the saved Round 3 cases (`tests/replay`), and the
   red-team set against the real model (about $2.50 for 44 attempts).
3. **HR console on a phone** scrolls sideways; it is built for a desktop.
4. **Sandbox is macOS-only** (`sandbox-exec`); other hosts refuse to run code until a
   Linux sandbox (e.g. bubblewrap) is added.
5. **Login throttle is per process**; a multi-process deployment needs shared storage.
