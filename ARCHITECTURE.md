# QA Eval Tool — Architecture

## What this is

A candidate-screening platform that replaces manual QA interviews with a
systematic, three-round assessment. HR authors each round's scenario and
reviews an LLM-generated "superhuman" reference answer before publishing
it; candidates then write test cases (round 1), debug a seeded bug (round
2), and prompt-engineer an LLM into producing correct automated tests
(round 3). Every round is scored automatically by comparing the
candidate's work against the same HR-approved reference, and HR gets a
running per-candidate, per-round report.

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
creates 1 HR account and 3 candidate accounts (2×`0-7`yrs, 1×`7+`yrs) from
`.env` values, idempotently. `/auth/login` is the only auth route.

**Auth: JWT, stored in an httpOnly-style bearer token.** Two roles
(`candidate`, `hr`) on one `users` table, distinguished by a `role`
column rather than two separate tables — simpler joins, and HR is just
"a user who can author scenarios and see reports," not a fundamentally
different entity.

**Scenario lifecycle: draft → published → archived.** HR creating a
scenario immediately (synchronously) triggers an LLM call to generate the
reference answer, but candidates can't see it yet — it's `draft` until HR
reviews it (regenerate, hand-edit the JSON, or just accept it) and clicks
Publish. Publishing archives whatever was previously `published` for that
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

**LLM: Anthropic Claude API, called from a single `llm_service.py`.**
All prompts live in `backend/app/prompts/*.txt` as plain text files, not
inline strings — so you (or HR, later, via an admin screen) can tune
what the model is and isn't allowed to say without touching Python. This
directly serves your "control over what the LLM is allowed to respond
with" requirement.

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
  id, round_number (1 | 2 | 3), title, description,
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

conversation_turns   -- Round 3 only: the prompt-refinement dialogue
  id, submission_id, turn_number, candidate_prompt, model_response,
  created_at
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
- **Round 2 — debugging.** HR preloads a bug. Candidate writes how they'd
  debug it. Claude compares against a human-quality reference debugging
  answer and scores it. *(Stubbed in this pass — see below. Scenario
  authoring/publishing already works for round 2; only the candidate-side
  submit/score pipeline is missing.)*
- **Round 3 — prompt-driven test automation.** Candidate must get Claude
  to produce 6 automated test cases (2 UI, 2 API, 2 DB) via conversation.
  Claude is deliberately instructed to hold back to ~60% correctness on
  each turn (wrong status codes, partial coverage, etc.) so the
  candidate has to notice and refine their prompts. The quality of *that
  refinement conversation* is itself the thing being scored. *(Stubbed.)*

Both bands (0-7 years / 7+ years) reuse the same round logic; the
difference is which scenario is published for a candidate's band and how
strict the scoring rubric prompt is — both are just data
(`experience_band` on `scenarios`, and band-aware wording inside the
prompt files), not separate code paths.

## What's built in this pass vs. stubbed

Built end-to-end: seeded auth (1 HR + 3 candidates, no signup), HR
scenario authoring with draft→publish lifecycle and reference
review/regenerate/hand-edit, round-gating, a server-authoritative
per-round timer with client auto-submit, Round 1's full flow (start →
structured submit → LLM scoring against the published reference → HR
dashboard + per-candidate drill-down report).

Stubbed (routes exist and return `501` for candidate-side submit/score,
but HR can already author/publish scenarios for round 2): Round 2 and
Round 3 candidate flows. These are flagged with `# TODO(round2)` /
`# TODO(round3)` comments and are the natural next things to build — each
is its own vertical slice, same pattern as Round 1.

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
      routers/
        auth.py            # login only
        hr.py              # scenario authoring + candidate dashboard
        candidate.py        # round fetch/start/submit + my results
      services/
        llm_service.py      # all Claude API calls live here
        scoring_service.py    # turns LLM output into Score rows
      prompts/             # editable text files, not inline strings
      templates/            # single Jinja2 page (index.html)
      static/              # app.js, style.css
  tests/
```
