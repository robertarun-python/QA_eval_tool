# Round 3: AI-Prompted Coding — Design

## What this is

A new round where the candidate never writes code directly — they direct
an LLM turn-by-turn in natural language ("I need two int variables read
from stdin", "wrap this in a loop over the array") and the LLM writes and
edits the actual source. The candidate can compile/run the current code
at any point and see real output (or a real exception). The round
assesses the candidate's ability to specify requirements precisely,
diagnose problems from output, and direct incremental improvement —
never their hand-typing fluency, and never the LLM's own judgment (the
LLM is barred from making design/algorithmic decisions on the
candidate's behalf).

This inserts as **Round 3**. The existing Round 3 ("prompt-driven test
automation" — candidate automates their own Round 1 test cases via
conversation) shifts to **Round 4**. See "Renumbering" below.

## Renumbering: existing Round 3 → Round 4

This is a mechanical rename + data migration, done as part of this
work, not a separate future task:

- `Round3TestCase` model → renamed `Round4TestCase`. `ConversationTurn`
  keeps its name (already generic) but its comments/usage now say
  "Round 4 only."
- Prompt files `round3_code_snippet.txt`, `round3_environment_generation.txt`,
  `round3_partial_response.txt`, `round3_scoring.txt`,
  `round3_ui_mockup_generation.txt` → renamed `round4_*.txt`.
- `AppSettings.round3_passing_score` → renamed `round4_passing_score`
  (existing values carry over unchanged). A new `round3_passing_score`
  is added for this new round. `final_passing_score`'s denominator
  widens from 300 to 400.
- `tests/test_round3.py` (currently tests automation) → renamed
  `tests/test_round4.py`. A new `tests/test_round3.py` is written for
  the coding round.
- Frontend labels reading "Round 3" for the automation flow → relabeled
  "Round 4."
- A new `backend/app/migrate_round_renumber.py`, matching the existing
  `migrate_*.py` script pattern in this repo, updates any existing rows
  in `scenarios`/`submissions` with `round_number = 3` to `round_number
  = 4`. This is a dev-seeded local SQLite DB (no real candidate data at
  stake), so a straight in-place `UPDATE` is sufficient — no need for
  anything more elaborate. The script should be idempotent (safe to run
  twice), matching the convention of the other `migrate_*.py` files.

Everything below refers to the **new** Round 3 (AI-prompted coding).

## Scope for this pass

One problem per submission. A single evolving code buffer (not multiple
independently-titled test cases like Round 4's automation round). One
language, chosen once at the start of the attempt and locked for the
whole submission. Grading blends automated test-case pass/fail against
an HR-approved reference test suite with an LLM holistic read of the
conversation for precision and efficiency.

Explicitly out of scope for this pass: a live/interactive terminal (see
"Execution model" below — batch stdin only), candidates hand-editing
code directly, and per-scenario language restriction (HR's problem
statement is language-agnostic; the candidate picks Java, Python, or
JavaScript at start).

## Data model

**New tables:**

- **`Round3Turn`** — one row per candidate instruction.
  - `submission_id` (FK, indexed)
  - `turn_number` (1-indexed within the submission)
  - `candidate_prompt` (text)
  - `language` (denormalized from the submission for convenience)
  - `response_kind` — `clarify` | `refuse` | `code_edit`
  - `response_message` — the LLM's natural-language reply: the
    clarifying question, the fixed refusal text, or a short note
    accompanying a code edit
  - `code_after` — full code snapshot after this turn. `NULL` for
    `clarify`/`refuse` turns (nothing changed). Storing a full snapshot
    rather than a diff keeps "what was the code as of turn N" a plain
    read; this repo already makes the same full-blob-over-diff
    tradeoff for `raw_llm_response_json` and is a fine cost at
    one-candidate/one-submission scale on SQLite.
  - `created_at`

- **`Round3ExecutionRun`** — one row per Run click.
  - `submission_id` (FK, indexed)
  - `turn_id` (FK, nullable — a candidate can re-run the same code
    without a new turn)
  - `language`
  - `code_snapshot` (denormalized, not joined through `turn_id`, so a
    run record is self-contained even if `turn_id` is null)
  - `stdin_json` — ordered list of input values the candidate supplied
  - `stdout`, `stderr`, `exit_code`
  - `timed_out` (bool)
  - `infra_error` (bool) — see "Error handling"
  - `duration_ms`
  - `created_at`

**Reused as-is:**

- `Scenario` (`round_number = 3`): `description` is the problem
  statement; `reference_json` holds the HR-approved test suite (see
  "HR authoring"). This field is currently unused by the automation
  round, so there's no collision once that round is Round 4.
- `Submission` (`round_number = 3`): `content` stores
  `{language, draft_prompt}` — the chosen language, locked at start,
  plus the autosave target for the candidate's in-progress unsent
  instruction (same autosave-on-a-JSON-field idea used elsewhere in
  this app, just submission-scoped instead of per-test-case since
  there's only one code buffer here).
- `Score`: unchanged shape. `coverage_score` = test-case pass rate,
  `misses_json` = failed test cases' descriptions, `final_score` =
  blended score, `feedback_text` = process commentary.
- `AppSettings.round3_passing_score` (new field, see "Renumbering").

## LLM behavior contract

**Two-tier guardrail**, enforced by system prompt *and* checked
mechanically at scoring time — not trusted to prompt wording alone,
since LLMs drift toward being helpful across many turns:

- **Tier 1 — mechanical gap.** Candidate didn't specify a data type,
  variable name, or which collection to use for something they did ask
  for → the LLM asks one direct clarifying question
  (`response_kind = "clarify"`), no code change.
- **Tier 2 — judgment question.** Candidate asks the LLM to
  decide/validate/compare ("which loop is correct," "is my approach
  right," "what's the best data structure here") → the LLM always
  returns the same fixed refusal text (`response_kind = "refuse"`), no
  code change, regardless of phrasing. Because this can't be reliably
  guaranteed by prompting alone, the scoring pass separately re-reads
  the full transcript and flags any Tier-2-shaped question the model
  answered instead of refusing as a guardrail violation — the same
  after-the-fact-detectable approach as the existing
  `test_scoring_prompt_injection_guardrail.py`.

**Code-edit discipline.** Every `code_edit` turn's prompt includes the
current full code and an explicit instruction to modify *only* what the
candidate's instruction asked for — no unrelated fixes, refactors, or
cleanup, even for a bug the model notices, unless explicitly asked.
Because `code_after` is a full snapshot per turn, a test can diff
consecutive snapshots and flag unrelated-line changes on turns whose
instruction wasn't fix/correct/improve-flavored — a mechanically
checkable invariant, not just a prompt hope.

**First turn is pseudocode, brute-force.** The system prompt for
`turn_number == 1` is a distinct, stricter template: produce a
plain-language pseudocode skeleton for the simplest/brute-force
approach implied by whatever requirements have been gathered so far. If
requirements are still incomplete (inputs/outputs not yet pinned down),
the turn stays in `clarify` rather than producing pseudocode
prematurely. Real language syntax begins only once the candidate has
specified enough to write actual code.

## Execution model

**Batch stdin, not a live terminal.** Before running, the candidate
fills in the values their program will read, in order. `Round3ExecutionRun`
sends the current code plus `stdin_json` (joined as line-separated
input) to a hosted code-execution API in one request/response call —
no persistent connection or PTY needed. The full transcript (what was
sent as input, what came back as output) is shown afterward. Default
provider: Piston (free, open, covers Java/Python/JavaScript with
built-in per-request wall-clock limits), configured the same
one-integration-point way `llm_service.py` centralizes the Claude API
— swappable later without touching call sites.

**Infinite loops are the sandbox's problem, not the LLM's.** The hosted
API's own timeout is the safety net. A run that times out records
`timed_out = true` with whatever `stdout`/`stderr` was captured before
the kill. The LLM is never invoked to react differently to a timeout
than to any other run result — if the candidate wants it addressed,
they have to prompt about it like any other output, per the guardrail
contract above.

## HR authoring flow

Same draft → review → publish lifecycle as Round 1/2. HR provides
title/description (the problem statement) and a time limit. On
creation, a new prompt `round3_reference_generation.txt` synchronously
generates:

```
reference_json = {
  "test_cases": [{input, expected_output, description}, ...],
  "expected_approach": "<brief note on the efficient approach,
                          e.g. 'use a hash map instead of nested loops'>"
}
```

HR reviews, regenerates, or hand-edits this before publishing —
identical UX to Round 1's reference review.

## Candidate flow

1. `POST /candidate/round/3/start {language}` — creates the
   `Submission` (`content = {language}`), starts the server-authoritative
   timer.
2. `PATCH /candidate/round/3/draft {prompt}` — autosaves the
   in-progress instruction into `Submission.content.draft_prompt`
   (mirrors the existing autosave pattern, submission-scoped rather
   than per-test-case since there's one code buffer).
3. `POST /candidate/round/3/turn {prompt}` — classifies and responds
   per the LLM behavior contract, creates a `Round3Turn`, clears the
   draft.
4. `POST /candidate/round/3/run {stdin: [...]}` — executes the latest
   `code_after` via the execution service, logs a `Round3ExecutionRun`.
   Disabled until the first real `code_edit` turn exists (nothing to
   run during the pseudocode-only stage).
5. Submit follows the existing generic submit/expire/lazy-expiry path
   (`scoring_service.close_expired_submissions` etc.), extended to
   include round 3 alongside 1/2/4.

## Scoring

New `score_round3_coding_submission`:

1. Re-run the final `code_after` against every entry in
   `reference_json.test_cases` via the execution service →
   `coverage_score` = pass rate, `misses_json` = failed cases'
   descriptions.
   If no turn ever produced a `code_after` (the submission never left
   the pseudocode/clarify stage), skip execution entirely —
   `coverage_score = 0`, every test case listed in `misses_json`,
   rather than attempting to run nothing.
2. One LLM call (`round3_coding_scoring.txt`) given the full turn
   transcript, the test results, and `expected_approach`, producing:
   - `final_score` blending correctness (test pass rate), precision
     (how completely/unambiguously the candidate specified requirements
     — fewer clarifying round-trips reflects better command of the
     problem), and efficiency (did they iteratively push from
     brute-force toward the expected approach when the problem calls
     for it)
   - `feedback_text`
   - a guardrail-violation check: did the model answer any
     Tier-2-shaped question instead of refusing anywhere in the
     transcript? A pattern of the candidate repeatedly asking the LLM
     to make design decisions (whether or not the model held the line)
     lowers the "independent judgment" component; it is not, by
     itself, an automatic penalty for a single occurrence.

## Error handling

A hosted-execution-API infra failure (network error, rate limit) is
recorded as `Round3ExecutionRun.infra_error = true`, distinct from
`exit_code`/`stderr`. This must never be scored as if it were the
candidate's own program crashing — an infra error is excluded from the
test-case pass-rate calculation and, if it occurs on the final code at
scoring time, surfaces as `scoring_failed` (existing `RoundStatus`) the
same way any other scoring-time exception does, rather than silently
counting as a failed test case.

## Testing

- Guardrail classification unit tests: a range of Tier-2 phrasings all
  produce `refuse`; Tier-1 gaps produce `clarify`.
- A diff-assertion test that a `code_edit` turn's `code_after` differs
  from the prior snapshot only in the region implied by its
  instruction.
- Execution-service tests against a stubbed hosted-API client (no real
  network calls in the test suite) covering: normal run, timeout,
  infra error.
- `tests/test_round3.py` (new): full happy path — start, choose
  language, first turn (clarify, since requirements are incomplete),
  provide details (pseudocode), refine into real code, run, submit,
  verify the blended score.
- `tests/test_round4.py` (renamed from today's `test_round3.py`): same
  coverage, asserting `round_number = 4`.
- A migration test for `migrate_round_renumber.py`: existing
  round_number=3 rows move to 4, round 1/2 rows are untouched, running
  it twice is a no-op the second time.

## Open risks / explicitly deferred

- **Piston (or whichever hosted execution API is chosen) availability
  and rate limits** are an external dependency this app has never had
  before — worth a fallback story (e.g., a clear `infra_error` surfaced
  to the candidate with time added back to their clock) but full retry/
  failover logic is deferred past this first pass.
- **Guardrail enforcement is best-effort at generation time, audited at
  scoring time.** There is no hard mechanical block preventing the LLM
  from answering a Tier-2 question in the moment; the design accepts
  detecting it after the fact rather than guaranteeing it can't happen,
  consistent with how the existing prompt-injection guardrail works in
  this codebase.
- **Multi-turn cost/latency** (a full code-execution round-trip per Run
  click, potentially several LLM calls per attempt) is not benchmarked
  against the round's time limit in this pass — worth watching once
  real candidates use it, particularly for Java's slower compile step.
