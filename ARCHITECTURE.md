# QA Eval Tool — Architecture

## What this is

A candidate-screening platform that replaces manual QA interviews with a
systematic, four-round assessment. HR authors each round's scenario and
reviews an LLM-generated "superhuman" reference answer before publishing
it; candidates then write test cases (round 1), debug a seeded bug (round
2), prompt an LLM into writing and running real code against a spec
(round 3), and prompt-engineer an LLM into producing correct automated
tests (round 4). Every round is scored automatically by comparing the
candidate's work against the same HR-approved reference, and HR gets a
running per-candidate, per-round report.

Round 3 and round 4 were built in that historical order but under
swapped numbers — the original "round 3" (prompt-driven test automation,
no code shown) was renumbered to round 4, and what's now round 3
("AI-prompted coding", a real terminal running candidate-directed code)
took its place. See `docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md`
and `backend/app/migrate_round_renumber.py` for that history if the
numbering in old commits looks backwards.

This doc exists so the build stays coherent as it grows in later
sessions, and so it can double as a system-design worked example (one of
the stated goals of this project — this codebase is a learning vehicle,
not just a deliverable).

## Why these choices (for the "why", not just the "what")

**Backend: FastAPI (Python).** Python matches your existing background
and this Learning/Python folder. FastAPI is a good next step up from
plain scripts: it forces you to think in request/response schemas
(Pydantic), gives you free interactive API docs at `/docs` (great for
learning how HTTP APIs actually behave), and has a shallow learning
curve compared to Django.

**DB: SQLite via SQLAlchemy.** Zero setup (it's a single file,
`qa_eval.db`), which matters for a POC you'll run on your own laptop
with pay-as-you-go API costs — no server to babysit. SQLAlchemy is the
ORM either way, so moving to Postgres later (when you have real
concurrent users) means changing one connection string, not rewriting
queries. This is deliberately the same pattern real companies use to
defer that decision.

**Accounts: seeded, not self-service.** This is a screening tool for a
fixed, known set of people (HR + whichever candidates you're currently
evaluating), not a public product — so there's no signup flow. `app/seed.py`
creates 1 HR account and 3 candidate accounts from `.env` values, idempotently.
`/auth/login` is the only auth route.

**Experience band: modeled, but hidden from the UI.** `Scenario` and
`User` both still carry `experience_band` (`0-7` / `7+`), and
`candidate.py`'s `_live_scenario` still matches on it exactly — none of
that was removed. What changed: HR no longer sees a band picker when
creating a scenario or a per-candidate band selector in the candidate
table (`app.js`'s `DEFAULT_BAND` feeds the hidden value instead), and
every candidate — seeded or bulk-uploaded — is assigned that same band
at creation time (`seed.py`, `candidate_upload_service.py`). Screening a
second band again just means re-adding those UI controls; the backend
already supports it. `PATCH /hr/candidates/{id}/band` still exists for
direct API use but isn't wired to anything in the UI anymore.

**Auth: JWT, stored in an httpOnly-style bearer token.** Two roles
(`candidate`, `hr`) on one `users` table, distinguished by a `role`
column rather than two separate tables — simpler joins, and HR is just
"a user who can author scenarios and see reports," not a fundamentally
different entity.

**Single active session, enforced only mid-round.** `User.
active_session_id` holds a random id minted fresh on every login and
embedded in that login's JWT as its `sid` claim (`security.
generate_session_id`/`create_access_token`) — a later login anywhere
overwrites it, so an earlier still-unexpired token's `sid` stops
matching. `dependencies.get_current_user` only actually rejects that
mismatch for a candidate with a round genuinely `in_progress` — outside
of an active round (or for HR, at all) a stale session is left alone
rather than forcing a surprise logout with nothing at stake. This is
what closes the two-devices-during-a-timed-test gap; it does not enforce
"one login ever," just "one that counts while it matters."

**Every round has a maximum residency, including "not started" — and the
whole assessment is a same-day activity by default.** A round that's
`in_progress` expires against its own `time_limit_minutes`
(`close_expired_submissions`) or ends immediately on logout
(`routers/auth.py`'s logout). But a round the candidate never even
started has no `started_at` for either of those to act on — nothing
bounded how long "not_started" could last on its own, so a candidate who
simply never begins the next round could sit there indefinitely with
nothing forcing a resolution. `AppSettings.assessment_window_days`
(default `1`) closes that: `scoring_service.
close_expired_assessment_windows` anchors to round 1's own
`Submission.started_at` — universal, every candidate gets one the
instant they click Start, regardless of how their account was created —
and once that many days have passed, finalizes any round with no
submission at all as a zero-attempt, through the same real scoring
pipeline a timeout uses. Falls back to `CandidateAppearance.exam_date`
only for a genuine no-show (scheduled, never started anything at all -
round 1 itself has no `started_at` yet either). This used to be
anchored to `exam_date` alone, which meant any candidate without a
`CandidateAppearance` — every seeded/demo account included — got no
enforcement whatsoever; anchoring to `started_at` first closes that for
every candidate, not just the bulk-uploaded ones.

**Scenario lifecycle: draft → published → archived.** HR creating a
scenario immediately (synchronously) triggers an LLM call to generate the
reference answer, but candidates can't see it yet — it's `draft` until HR
reviews it (regenerate, hand-edit the JSON, or just accept it) and clicks
Publish. `title`, `description`, `reference_json`, and `time_limit_minutes`
are all frozen the moment a scenario leaves `draft` (see hr.py's
`_get_draft_scenario_or_404` and `update_scenario_time_limit`) — a
published scenario's parameters must stay exactly as they were when
candidates were scored against them. Time limit used to be an exception
(editable on a live scenario too, gated only on nobody being mid-round),
but that let two candidates take the "same" scenario under two different
real durations with no record of which applied to which candidate,
undermining the whole point of comparing them against one fixed
assessment — fixing a wrong live time limit now means publishing a
corrected scenario, same as fixing a wrong title/reference already
required. Round 4 is the one exception to all of this: it has no draft
phase (its scenarios go live immediately on creation), so its settings
stay mutable post-publish, gated only on nobody being mid-round. Publishing
archives whatever was previously `published` for that
same `(round_number, experience_band)` pair, which is what enforces "one
live scenario per round + band" — candidates never pick from a list, they
just see whatever's currently live for their band and round.

**Round gating, not a separate "progress" table.** Whether a candidate can
access round *N* is computed on the fly from their submission history
(`max completed round + 1 >= N`), rather than stored as explicit state.
One less thing that can get out of sync with reality.

**Timer: server-authoritative start time.** `Submission.started_at` is set
once, server-side, the first time a candidate hits "Start" for a round —
not tracked purely in the browser — so a page refresh mid-round doesn't
reset (or extend) their clock. The frontend computes the countdown as
`started_at + scenario.time_limit_minutes` and auto-submits at zero.

**Logout ends the round, not just the session.** `POST /auth/logout`
finalizes whatever round the candidate has `in_progress` immediately
(`scoring_service.finalize_abandoned_submission`, the same mechanism a
genuine timeout and a bulk-upload reset both use) — completed work is
scored as of that moment, and no attempt at all scores zero. There is no
resuming an attempt after a deliberate logout. The frontend warns about
this before calling it (`app.js`'s `logout()`), but only for an actual
button click — an automatic 401-triggered logout (an already-expired
session) skips the warning and, since an expired token can't be decoded
server-side to identify whose round it was, doesn't finalize anything
either; that case is left to the deadline-based lazy-close path instead
(`close_expired_submissions`), the backstop for anyone who never
explicitly logs out at all (closed tab, crash, lost network/power).

**LLM: Anthropic Claude API, called from a single `llm_service.py`.**
All prompts live in `backend/app/prompts/*.txt` as plain text files, not
inline strings — so you (or HR, later, via an admin screen) can tune
what the model is and isn't allowed to say without touching Python. This
directly serves your "control over what the LLM is allowed to respond
with" requirement.

**Round 3 code execution: local subprocess, not a hosted sandbox.**
`execution_service.py` runs candidate/LLM-written code as a plain local
subprocess (batch stdin, wall-clock timeout only — no network isolation,
filesystem restriction, or memory/CPU cap). It originally used the hosted
Piston API; that went whitelist-only in Feb 2026 with no self-hosting
option available here, so this switched to local subprocess execution.
Acceptable trust boundary for "one HR user running it locally, scoring
LLM-written code" — would need real isolation (containers/VM/gVisor)
before this tool could ever run untrusted code in a multi-tenant
deployment.

**Frontend: server-rendered Jinja2 template + a little vanilla JS.**
No React/Node build step to debug on top of everything else you're
learning. Once the backend API is solid, swapping the template for a
React frontend later is a contained, well-scoped exercise — the API
doesn't change.

## Data model

```
users
  id, email, password_hash, role ('candidate' | 'hr'),
  experience_band ('0-7' | '7+' | NULL for HR), created_at

scenarios
  id, round_number (1 | 2 | 3 | 4), title, description,
  experience_band ('0-7' | '7+'), created_by (users.id),
  status ('draft' | 'published' | 'archived'),
  reference_json (the HR-approved reference answer),
  time_limit_minutes, published_at,
  config_json (round-specific extras, e.g. round 2's bug seed), created_at

submissions
  id, user_id, scenario_id, round_number,
  content (JSON - structured test-case rows for round 1),
  status ('in_progress' | 'submitted' | 'scored'),
  started_at (server-set timer zero point), created_at

scores
  id, submission_id, coverage_score, misses_json, final_score,
  feedback_text, raw_llm_response_json, created_at

round3_turns   -- Round 3 only (AI-prompted coding): one evolving code
  id, submission_id, turn_number, candidate_prompt, language,           -- buffer per submission, not per test case
  response_kind ('clarify' | 'refuse' | 'code_edit' | 'direct_edit'), response_message,
  code_after (full snapshot, NULL for clarify/refuse),
  declared_constructs_json (cumulative snapshot of which required        -- construct-checklist state (see below),
  constructs the candidate has explicitly declared as of this turn -      -- written on every turn, not just code_edit
  written even on clarify/refuse turns, so the latest row is always
  the current state), created_at

round3_execution_runs   -- Round 3 only: one "Run" click's result
  id, submission_id, turn_id (nullable), language, code_snapshot,
  stdin_json, stdout, stderr, exit_code, timed_out, infra_error,
  duration_ms, created_at

round4_test_cases   -- Round 4 only: the candidate's own, self-titled
  id, submission_id, title (nullable), draft_prompt (autosave target),
  created_at

conversation_turns   -- Round 4 only: the prompt-refinement dialogue,
  id, submission_id, test_case_id, turn_number, candidate_prompt,        -- scoped to one test case
  model_response, generated_code_json (lazy, trial-view only), created_at

candidate_appearances   -- one row per bulk-upload event for a candidate;
  id, user_id, email, exam_date, is_current, reapplied_within_window,    -- drives the reapplication-window check
  created_at                                                             -- and re-archives old submissions on reupload

app_settings   -- singleton row (id=1), HR-editable at runtime, no restart
  id, round1_passing_score, round2_passing_score, round3_passing_score,
  round4_passing_score, final_passing_score (out of 400),
  reapplication_window_months, updated_at
```

## Round mechanics (from the design notes)

- **Round 1 — manual test case writing.** HR authors a scenario; Claude
  generates a reference set of *structured* test cases (title,
  preconditions, steps, expected result, priority, type — see
  `prompts/round1_reference_generation.txt`) written to read like a real
  QA engineer's wiki page, not obviously AI-generated. HR reviews and
  publishes it. The candidate fills the same structured shape (a
  repeatable row, not free text) within the time limit; on submit, Claude
  scores the candidate's rows against the *same* reference that was
  published (not a fresh generation — see `scoring_service.score_round1`)
  for coverage, misses, and a final score.
- **Round 2 — debugging.** HR preloads a bug report as free text (the
  same `description` field every round uses — no separate structured
  fields for symptom/package/etc., see `prompts/round2_reference_generation.txt`).
  Claude generates a reference debugging approach: reproduce/characterize
  the issue, eliminate the layers actually relevant to that bug (UI,
  device, network, API, server/business logic, database/config,
  location/geo rules, cache, deployment — whichever apply) with concrete
  rule-in/rule-out evidence per step, converge on 1-2 justified root
  causes, then a fix and regression tests. The candidate's own answer
  shape is deliberately *not* the same as the reference: a short
  repeatable list of investigation areas (one free-text field each -
  what they checked, what they found) plus a single closing "possible
  root cause" statement, not test-case-style rows (see
  `schemas.Round2SubmissionCreate`). On submit, Claude scores that
  investigation + conclusion against the published reference for
  coverage, misses, and a final score, weighted toward methodical
  elimination over a lucky guess, and toward whether the stated root
  cause actually follows from what was investigated (see
  `prompts/round2_debug_scoring.txt`, `scoring_service.score_round2_investigation`).
  One-shot, not conversational — the candidate writes their full
  investigation in one sitting, there's no back-and-forth clue reveal.
- **Round 3 — AI-prompted coding.** Candidate directs an LLM (never
  writing code themselves) to build a solution against the scenario spec
  through a real 3-pane terminal-style UI (instructions, evolving code,
  run output) with a genuine "Run" button — code executes for real via
  `execution_service.py`, not simulated. Each candidate instruction gets
  classified into exactly one of four kinds: `clarify` (the LLM refuses
  to invent a name or technique the candidate didn't specify — asks one
  bare question instead), `refuse` (candidate asked the LLM to make a
  design/algorithm call, self-diagnose an error, or "solve it" end-to-end
  instead of directing specific work), `code_edit` (does exactly and
  only what was asked, returns the full code, never a diff — see
  `Round3Turn.code_after`) — all three via `prompts/round3_coding_turn.txt`
  — or `direct_edit`, the fourth kind, taken instead of an instruction
  entirely: the candidate types or pastes code straight into the same
  pane (`services/llm_service.round3_syntax_fix`,
  `prompts/round3_syntax_fix.txt`), and the LLM only fixes syntax errors
  — never rewrites logic, never second-guesses a design choice — then
  classifies the resulting code against the construct checklist exactly
  like any other turn, but never surfaces a gap either way: same
  silent-on-missing behavior as the instruction path's own `code_edit`,
  just without a clarifying question ever standing in the way of pasting
  a complete solution. The LLM never fixes, refactors, or second-guesses
  anything the candidate didn't explicitly ask it to touch (or, on the
  direct-edit path, anything beyond syntax), and never hints that an
  edge case might be unhandled — discovering that from a bad run is the
  point of the round.
  On top of that per-turn classification, a **construct-checklist**
  layer forces the candidate to explicitly decide every programming
  construct a problem's solution actually needs — which collection
  type, which iteration mechanism, which comparison, and so on — rather
  than letting the LLM infer or default to one. HR authors an ordered
  `required_constructs` list per scenario at creation time (alongside
  the reference test cases, see `prompts/round3_reference_generation.txt`);
  a deterministic engine (`services/round3_construct_engine.py`), not
  the LLM's own self-reported classification, decides turn to turn
  whether `code_edit` is allowed — see `Round3Turn.declared_constructs_json`
  above. The gate is per-instruction, not per-scenario: `code_edit` is
  allowed once nothing THIS instruction attempted is left ambiguous,
  even while other required constructs the instruction never touched
  remain open for a later instruction — a construct only gets asked
  about when the candidate's own instruction is clearly about it but
  doesn't commit to a choice, never proactively for one they haven't
  gotten to yet, so a fully-specified instruction is never blocked by
  work still ahead of it. When the engine does ask, the LLM must do so
  without naming the construct, its vocabulary, or its possible implementations
  (`services/round3_constructs.py` holds the fixed category taxonomy and
  a per-language forbidden-word list); a clarifying question that leaks
  anyway gets mechanically caught, regenerated once, and — if it still
  leaks — replaced with a hardcoded neutral fallback question, so the
  "never name it" rule is enforced, not just requested. See
  `docs/superpowers/specs/2026-08-27-round3-construct-checklist-design.md`
  for the full design. Scored on how precisely and completely the
  candidate directs the LLM, not on whether the resulting code happens
  to work (`prompts/round3_coding_scoring.txt`,
  `scoring_service.score_round3_coding`).
- **Round 4 — prompt-driven test automation.** Candidate automates their
  own Round 1 test cases by directing Claude through conversation,
  writing as many self-titled test cases as they judge the scenario
  needs (no fixed UI/API/DB category or count assigned to them - picking
  what's worth testing is itself part of what's assessed). Each message
  autosaves as a draft per test case (survives switching tabs and a page
  refresh) until it's actually sent. Nothing really executes - Claude
  invents an execution trace (plain-English steps, each pass/fail/
  partial, plus an "observed result") in the same call, with a
  HR-editable default accuracy (50%) and a guarantee that at least one
  early step contains a catchable mistake, preferring a quiet
  contradiction in the observed result over an outright crash. No code
  is shown to the candidate by default (see `schemas.Round4ExecutionStep`)
  - the candidate reasons from what a manual tester would see, not from
  reading an implementation, so coding fluency can't substitute for
  automation judgment; a generated code snippet is available as an
  on-demand, persisted "trial view" per turn/language, never part of the
  default reasoning flow. HR gets an auto-generated "Test environment"
  reference sheet (fictional credentials, API base URL, product URL, ...)
  per scenario, shown to every candidate alongside the description
  (`Scenario.environment_json`, same generate/review/regenerate/
  publish-gate lifecycle as `reference_json` for rounds 1/2 - see
  `llm_service.generate_round4_environment`). One holistic score per
  submission, weighted toward methodical verification (catching the
  assistant's flaws, not just accepting the first answer) and
  independent breadth of judgment about what to test, with category
  breadth counted as extra credit rather than a hard requirement (see
  `prompts/round4_scoring.txt`).

Both bands (0-7 years / 7+ years) reuse the same round logic; the
difference is which scenario is published for a candidate's band and how
strict the scoring rubric prompt is — both are just data
(`experience_band` on `scenarios`, and band-aware wording inside the
prompt files), not separate code paths.

## What's built in this pass vs. stubbed

Built end-to-end: seeded auth (1 HR + 3 candidates, no signup, now with
bulk candidate upload and a reapplication-window check via
`candidate_appearances`), HR scenario authoring with draft→publish
lifecycle and reference/environment review/regenerate/hand-edit,
round-gating, a server-authoritative per-round timer with client
auto-submit, all four rounds' full flow (start → submit → LLM scoring →
HR dashboard + per-candidate drill-down report, including a per-turn/
per-test-case score breakdown for rounds 3 and 4), real (not simulated)
local code execution for round 3, round 3's construct-checklist clarify
engine (deterministic gating of `code_edit` plus a mechanical vocabulary
leak-check — see the Round 3 bullet above), round 3's direct-code-edit
path (candidate pastes/types code instead of instructing the LLM —
syntax-only fix, same construct classification and leak-check, no
gap-flagging — see `docs/superpowers/specs/2026-08-27-round3-direct-code-edit-design.md`),
HR-editable runtime settings (per-round passing scores, reapplication
window — `app_settings`) and manual score override, a tab-switch/
fullscreen guard during timed rounds, and a screening-history dashboard
aggregating clear rate and common misses per scenario (round-agnostic —
covers rounds 1 and 2 today; rounds 3/4's conversational/coding shape
doesn't fit the same misses-pattern aggregation). Frontend is a
token-based "Calibration" design system (light + dark themes), not the
original bare Jinja2 page.

Explicitly deferred (see the construct-checklist design spec's own
"Explicitly out of scope" section): changing the start-of-round language
picker (it's already locked both server- and client-side; only the
instructional copy was added).

## Folder layout

```
qa-eval-tool/
  ARCHITECTURE.md
  README.md
  requirements.txt
  .env.example
  run_server.bat
  backend/
    app/
      main.py            # FastAPI app, mounts routers
      config.py           # env var loading (incl. seeded account creds)
      database.py          # SQLAlchemy engine/session
      models.py            # ORM models
      schemas.py           # Pydantic request/response shapes
      security.py          # password hashing, JWT
      dependencies.py       # FastAPI auth dependencies
      seed.py              # creates the 1 HR + 3 candidate accounts
      credential_service.py  # bulk candidate upload / credential generation
      routers/
        auth.py            # login only
        hr.py              # scenario authoring + candidate dashboard
        candidate.py        # round fetch/start/submit + my results
      services/
        llm_service.py      # all Claude API calls live here
        scoring_service.py    # turns LLM output into Score rows
        execution_service.py  # round 3's real local subprocess code execution
        round3_constructs.py   # round 3's fixed construct taxonomy + per-language leak vocab
        round3_construct_engine.py  # deterministic clarify/code_edit decision engine (no LLM calls)
        candidate_upload_service.py
      prompts/             # editable text files, not inline strings (per-round + shared)
      templates/            # single Jinja2 page (index.html)
      static/              # app.js, style.css ("Calibration" design system)
      migrate_*.py         # one-off SQLite migration scripts, run manually as the schema grew
  docs/superpowers/
    specs/                 # design specs
    plans/                 # matching implementation plans
  tests/
```
