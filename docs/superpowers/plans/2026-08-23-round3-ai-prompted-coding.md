# Round 3: AI-Prompted Coding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the new Round 3, where the candidate directs an LLM turn-by-turn in plain English (never writing code by hand) to build a solution to an HR-authored problem, can compile/run the resulting code against real input, and gets graded on an automated test-case pass rate blended with an LLM read of how precisely/efficiently they directed the work.

**Architecture:** Reuses this app's existing draft→published→live scenario lifecycle (same as Round 1) for HR authoring. A new `Round3Turn` table stores one row per candidate instruction with the LLM's classified response (`clarify` / `refuse` / `code_edit`) and a full code snapshot; a new `Round3ExecutionRun` table stores one row per Run click via a new `execution_service.py` that calls a hosted code-execution API (Piston). Scoring re-runs the final code against HR's test suite for an objective pass rate, then one LLM call judges direction quality.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, SQLite, httpx (already a dependency), pytest, vanilla JS.

**Spec:** `docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md`

## Global Constraints

- **Depends on** `docs/superpowers/plans/2026-08-23-round3-to-round4-renumber.md` having already been fully implemented (models/schemas/routes renamed to Round 4, round_number 3 vacated) — every task below assumes that plan's end state. Do not start this plan until that one's full test suite is green.
- Two-tier guardrail (Tier 1 "clarify" vs Tier 2 "refuse") is enforced by prompt instruction *and* audited after the fact at scoring time — never assume the LLM will mechanically refuse every Tier-2 question; the scoring prompt's `guardrail_violations` output is what actually catches a slip.
- The candidate never edits code directly — every `code_after` value comes from the (mocked-in-tests, real-in-production) LLM response. Nothing in this plan adds a code-editing UI control.
- Code execution is a single batch call (all `stdin` values supplied upfront) — no live/interactive terminal, per the spec's "Execution model".
- A hosted-API infra failure (`infra_error`) must never be scored as if it were the candidate's own program crashing — every place that reads `stdout`/`stderr`/`exit_code` for scoring must check `infra_error` first (see Task 6).

---

## File Structure

New files:
- `backend/app/services/execution_service.py` — calls the hosted execution API.
- `backend/app/prompts/round3_reference_generation.txt`, `round3_coding_turn.txt`, `round3_coding_scoring.txt`.
- `backend/app/migrate_round3_coding.py` — new tables + `app_settings.round3_passing_score` column.
- `tests/test_execution_service.py`, `tests/test_round3.py` (new — this is the coding round; not to be confused with the renamed `tests/test_round4.py` from the prior plan).

Modified files:
- `backend/app/models.py` (new `Round3Turn`, `Round3ExecutionRun`; `Submission` relationships; `AppSettings.round3_passing_score`)
- `backend/app/config.py` (`piston_api_url`, `execution_timeout_seconds`)
- `backend/app/schemas.py` (new Round 3 coding schemas; `AppSettingsOut`/`Update` additions; `ScenarioOut.reference_json` type loosened)
- `backend/app/services/llm_service.py` (3 new functions)
- `backend/app/services/scoring_service.py` (`score_round3_submission`, `_SCORERS` entry)
- `backend/app/routers/hr.py` (round_number 3 re-admitted, reference generation branch, labels, loops, report)
- `backend/app/routers/candidate.py` (5 new dedicated endpoints, 3 generic tuples widened)
- `backend/app/static/app.js`, `backend/app/static/style.css`, `backend/app/templates/index.html` (candidate coding UI, HR settings field, HR reference-review branch)
- `tests/conftest.py` (round 3 added to `_REFERENCE_GENERATOR_BY_ROUND`, a fake reference fixture)
- `tests/test_settings.py` (round3_passing_score coverage, final_passing_score ceiling bump)

---

### Task 1: Data model + migration

**Files:**
- Modify: `backend/app/models.py`, `backend/app/config.py`
- Create: `backend/app/migrate_round3_coding.py`

**Interfaces:**
- Produces: `Round3Turn` (`id, submission_id, turn_number, candidate_prompt, language, response_kind, response_message, code_after, created_at`), `Round3ExecutionRun` (`id, submission_id, turn_id, language, code_snapshot, stdin_json, stdout, stderr, exit_code, timed_out, infra_error, duration_ms, created_at`), `Submission.round3_turns` / `Submission.round3_execution_runs` relationships (ordered by `created_at`), `AppSettings.round3_passing_score`, `Settings.piston_api_url` / `Settings.execution_timeout_seconds`. Every later task's model/config references these exact names.

- [ ] **Step 1: Add the two new models to `backend/app/models.py`**

Insert after the `Round4TestCase`/`ConversationTurn` classes (i.e. right before `CandidateAppearance`):

```python
class Round3Turn(Base):
    """Round 3 only (AI-prompted coding): one candidate instruction and
    the LLM's classified response. Unlike Round 4's automation round,
    there's a single evolving code buffer per submission, not multiple
    self-titled test cases - so this is scoped straight to submission_id,
    no intermediate test-case table. See
    docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md."""
    __tablename__ = "round3_turns"

    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey("submissions.id"), nullable=False, index=True)
    turn_number = Column(Integer, nullable=False)
    candidate_prompt = Column(Text, nullable=False)
    language = Column(String, nullable=False)
    response_kind = Column(String, nullable=False)  # "clarify" | "refuse" | "code_edit"
    response_message = Column(Text, nullable=False)
    # Full code snapshot after this turn, NOT a diff - NULL for
    # clarify/refuse turns (nothing changed). See the design spec's data
    # model section for why a full snapshot per turn is the right
    # tradeoff at this scale (one candidate, one submission, SQLite).
    code_after = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    submission = relationship("Submission", back_populates="round3_turns")


class Round3ExecutionRun(Base):
    """Round 3 only (AI-prompted coding): one Run click's result.
    Denormalizes language/code_snapshot rather than joining through
    turn_id, so a run record is self-contained even when turn_id is null
    (a re-run of already-generated code, no new turn since)."""
    __tablename__ = "round3_execution_runs"

    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey("submissions.id"), nullable=False, index=True)
    turn_id = Column(Integer, ForeignKey("round3_turns.id"), nullable=True)
    language = Column(String, nullable=False)
    code_snapshot = Column(Text, nullable=False)
    stdin_json = Column(JSON, default=list)
    stdout = Column(Text, nullable=True)
    stderr = Column(Text, nullable=True)
    exit_code = Column(Integer, nullable=True)
    timed_out = Column(Boolean, nullable=False, default=False)
    # Set when the hosted execution API itself failed (network error,
    # rate limit, ...) - distinct from exit_code/stderr, which are the
    # CANDIDATE's program's own output. Never treat this run's
    # stdout/stderr as a real result when this is true - see
    # scoring_service.score_round3_submission.
    infra_error = Column(Boolean, nullable=False, default=False)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    submission = relationship("Submission", back_populates="round3_execution_runs")
```

- [ ] **Step 2: Add the `Submission` relationships**

In the `Submission` class, right after the existing `round4_test_cases` relationship, add:

```python
    # Round 3 only (AI-prompted coding) - see Round3Turn/Round3ExecutionRun.
    round3_turns = relationship(
        "Round3Turn", back_populates="submission",
        order_by="Round3Turn.created_at",
    )
    round3_execution_runs = relationship(
        "Round3ExecutionRun", back_populates="submission",
        order_by="Round3ExecutionRun.created_at",
    )
```

- [ ] **Step 3: Add `AppSettings.round3_passing_score`**

In the `AppSettings` class, add the new column right before `round4_passing_score` (keeping round-number order in the source):

```python
    round3_passing_score = Column(Integer, nullable=False, default=70)
```

- [ ] **Step 4: Add execution-service config to `backend/app/config.py`**

Add, right after `round4_default_assistance_pct`:

```python
    # Round 3 (AI-prompted coding) code execution - see
    # services/execution_service.py. Piston is a free, open hosted
    # code-execution API; swappable later via this one setting, same
    # "one place to change" pattern as claude_model above.
    piston_api_url: str = "https://emkc.org/api/v2/piston"
    execution_timeout_seconds: int = 10
```

- [ ] **Step 5: Write the migration script `backend/app/migrate_round3_coding.py`**

```python
"""
Migration for Round 3's AI-prompted-coding build (see
docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md): new
round3_turns/round3_execution_runs tables plus a fresh
app_settings.round3_passing_score column - distinct from round4's
column, which the earlier renumbering migration already renamed.

Idempotent - checks table/column presence first, so it's safe to run
more than once (e.g. after a fresh create_all() already created
everything for a brand new DB - nothing to do there).

Run from backend/: python -m app.migrate_round3_coding
"""
import sqlite3

from .database import sqlite_db_path


def _has_table(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def _has_column(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def migrate() -> list[str]:
    con = sqlite3.connect(sqlite_db_path())
    applied = []
    try:
        cur = con.cursor()

        if not _has_table(cur, "round3_turns"):
            cur.execute(
                """
                CREATE TABLE round3_turns (
                    id INTEGER PRIMARY KEY,
                    submission_id INTEGER NOT NULL REFERENCES submissions(id),
                    turn_number INTEGER NOT NULL,
                    candidate_prompt TEXT NOT NULL,
                    language VARCHAR NOT NULL,
                    response_kind VARCHAR NOT NULL,
                    response_message TEXT NOT NULL,
                    code_after TEXT,
                    created_at DATETIME
                )
                """
            )
            applied.append("round3_turns (table)")

        if not _has_table(cur, "round3_execution_runs"):
            cur.execute(
                """
                CREATE TABLE round3_execution_runs (
                    id INTEGER PRIMARY KEY,
                    submission_id INTEGER NOT NULL REFERENCES submissions(id),
                    turn_id INTEGER REFERENCES round3_turns(id),
                    language VARCHAR NOT NULL,
                    code_snapshot TEXT NOT NULL,
                    stdin_json TEXT,
                    stdout TEXT,
                    stderr TEXT,
                    exit_code INTEGER,
                    timed_out BOOLEAN NOT NULL DEFAULT 0,
                    infra_error BOOLEAN NOT NULL DEFAULT 0,
                    duration_ms INTEGER,
                    created_at DATETIME
                )
                """
            )
            applied.append("round3_execution_runs (table)")

        if not _has_column(cur, "app_settings", "round3_passing_score"):
            cur.execute("ALTER TABLE app_settings ADD COLUMN round3_passing_score INTEGER NOT NULL DEFAULT 70")
            applied.append("app_settings.round3_passing_score")

        con.commit()
    finally:
        con.close()
    return applied


if __name__ == "__main__":
    applied = migrate()
    if applied:
        print(f"Applied: {', '.join(applied)}")
    else:
        print("Nothing to do - schema already up to date.")
```

- [ ] **Step 6: Run the full test suite (proves the new models don't break table creation)**

Run: `cd C:\Arun\Learning\Python\qa-eval-tool && python -m pytest tests/ -q`
Expected: all tests still pass (the in-memory test DB's `Base.metadata.create_all()` now includes the 2 new tables and the new column).

- [ ] **Step 7: Run the migration against your local dev DB**

Run: `cd backend && python -m app.migrate_round3_coding`
Expected: prints what it applied (or "Nothing to do" on a fresh DB).

- [ ] **Step 8: Commit**

```bash
git add backend/app/models.py backend/app/config.py backend/app/migrate_round3_coding.py
git commit -m "Add Round 3 (AI-prompted coding) data model and migration"
```

---

### Task 2: Schemas

**Files:**
- Modify: `backend/app/schemas.py`

**Interfaces:**
- Consumes: nothing new from Task 1 directly (schemas are independent of ORM models).
- Produces: `Round3CodingTurnResponse`, `Round3StartRequest`, `Round3DraftUpdate`, `Round3TurnCreate`, `Round3TurnOut`, `Round3RunCreate`, `Round3RunOut`, `Round3StateOut` — these exact names/fields are what `llm_service.py` (Task 5) and `routers/candidate.py` (Task 8) import.

- [ ] **Step 1: Add `model_validator` to the pydantic import**

Change:
```python
from pydantic import BaseModel, Field, field_validator
```
to:
```python
from pydantic import BaseModel, Field, field_validator, model_validator
```

- [ ] **Step 2: Loosen `ScenarioOut.reference_json`'s type**

Round 1/2's reference is `list[dict]`-shaped; Round 3 (coding)'s is `{"test_cases": [...], "expected_approach": "..."}` - a dict, not a list. Change:
```python
    reference_json: Optional[list[dict]] = None
```
to:
```python
    reference_json: Optional[Any] = None  # list[dict] for rounds 1/2, {"test_cases": [...], "expected_approach": "..."} for round 3
```

- [ ] **Step 3: Add the new Round 3 (coding) schemas**

Add this new section right after the `# ---- Round 4 (conversational automation: ...) ----` section (i.e. as its own section, after all the `Round4*` schemas):

```python
# ---- Round 3 (AI-prompted coding: the candidate never writes code
# directly - they direct an LLM turn by turn, and it writes/edits the
# actual source. See docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md.) ----


class Round3StartRequest(BaseModel):
    language: Literal["python", "java", "javascript"]


class Round3DraftUpdate(BaseModel):
    """PATCH body for autosaving the candidate's in-progress, unsent
    message - see candidate.py's PATCH /round/3/draft. Submission-scoped,
    not per-test-case, since round 3 (coding) has a single evolving code
    buffer, not Round 4's multiple self-titled test cases."""
    draft_prompt: str


class Round3TurnCreate(BaseModel):
    candidate_prompt: str = Field(min_length=1)


class Round3CodingTurnResponse(BaseModel):
    """The LLM's classified response for one turn - see
    llm_service.round3_coding_turn and prompts/round3_coding_turn.txt.
    code_after is required when response_kind is "code_edit" (the full
    updated code) and must be absent otherwise (clarify/refuse never
    touch the code)."""
    response_kind: Literal["clarify", "refuse", "code_edit"]
    response_message: str
    code_after: Optional[str] = None

    @model_validator(mode="after")
    def _code_after_required_for_code_edit(self):
        if self.response_kind == "code_edit" and not self.code_after:
            raise ValueError("code_after is required when response_kind is 'code_edit'")
        return self


class Round3TurnOut(BaseModel):
    id: int
    turn_number: int
    candidate_prompt: str
    language: str
    response_kind: str
    response_message: str
    code_after: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class Round3RunCreate(BaseModel):
    stdin: list[str] = []


class Round3RunOut(BaseModel):
    id: int
    language: str
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    exit_code: Optional[int] = None
    timed_out: bool
    infra_error: bool
    duration_ms: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class Round3StateOut(BaseModel):
    """Everything the round 3 (coding) candidate screen needs in one
    call: the scenario, their submission (content.language/draft_prompt
    live inside SubmissionOut.content), the full turn history, and every
    run's result."""
    scenario: ScenarioPublicOut
    submission: SubmissionOut
    language: Optional[str] = None
    turns: list[Round3TurnOut]
    runs: list[Round3RunOut]
```

- [ ] **Step 4: Add `round3_passing_score` to `AppSettingsOut`/`AppSettingsUpdate` and bump `final_passing_score`'s ceiling**

In `AppSettingsOut`, add (right before `round4_passing_score`, keeping round-number order):
```python
    round3_passing_score: int
```

In `AppSettingsUpdate`, add (same position):
```python
    round3_passing_score: int = Field(ge=0, le=100)
```

And in `AppSettingsUpdate`, change:
```python
    final_passing_score: int = Field(ge=0, le=300)
```
to:
```python
    final_passing_score: int = Field(ge=0, le=400)
```
(4 rounds now contribute to the sum instead of 3 — see the design spec's data model section.)

- [ ] **Step 5: Update `routers/hr.py`'s `_app_settings_out` to pass the new field through**

This is a one-line addition inside `_app_settings_out` (in `routers/hr.py`, not this file) — included here as a heads-up since Task 7 does the actual edit: `AppSettingsOut(round3_passing_score=app_settings.round3_passing_score, ...)`.

- [ ] **Step 6: Run the full test suite**

Run: `cd C:\Arun\Learning\Python\qa-eval-tool && python -m pytest tests/ -q`
Expected: fails at this point on `tests/test_settings.py` (it doesn't yet send `round3_passing_score` in its `AppSettingsUpdate` payloads, which is now a required field) — this is expected; Task 10 updates that test file. Confirm the *only* new failures are in `test_settings.py`, and that the failure is specifically a 422 validation error about the missing field (not something else) before moving on.

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas.py
git commit -m "Add Round 3 (AI-prompted coding) schemas"
```

---

### Task 3: Prompts

**Files:**
- Create: `backend/app/prompts/round3_reference_generation.txt`, `backend/app/prompts/round3_coding_turn.txt`, `backend/app/prompts/round3_coding_scoring.txt`

**Interfaces:**
- Consumes: nothing (plain text files).
- Produces: the exact `.format(...)` placeholder names Task 5's `llm_service.py` functions must pass — `{scenario_description}`, `{experience_band}` (reference generation); `{scenario_description}`, `{language}`, `{conversation_so_far}`, `{current_code}`, `{turn_number}`, `{is_first_turn}`, `{candidate_prompt}` (coding turn); `{scenario_description}`, `{expected_approach}`, `{conversation_so_far}`, `{test_results}` (scoring).

- [ ] **Step 1: Write `backend/app/prompts/round3_reference_generation.txt`**

```
You are a senior software engineer with 8+ years of experience, authoring a REFERENCE solution and test suite for a candidate assessment - this reference will be used to automatically grade a candidate's code, so it needs to be correct, unambiguous, and realistic.

Problem statement (experience band: {experience_band}):
{scenario_description}

Produce:
1. A set of test cases that fully exercise this problem's correctness - realistic inputs (including edge cases: empty/zero/negative/boundary values where they genuinely apply to this problem) paired with the exact expected output a correct solution would print. Each test case's "input" is the exact stdin content a program would read (space or newline separated values, matching how the problem statement describes input), and "expected_output" is the exact stdout content a correct solution prints (no extra commentary).
2. A one-to-two sentence note on the efficient approach a strong {experience_band}-level engineer would converge on (e.g. "use a hash map for O(n) lookup instead of nested loops", or "sort first, then a single pass") - this is used later to judge whether a candidate iteratively improved toward it, and is never shown to the candidate directly.

Respond with ONLY a JSON object, no other text, in this shape:
{{"test_cases": [{{"input": "...", "expected_output": "...", "description": "..."}}], "expected_approach": "..."}}
```

- [ ] **Step 2: Write `backend/app/prompts/round3_coding_turn.txt`**

```
You are acting as a senior software engineer with 8+ years of experience, writing {language} code for a candidate who directs you entirely through natural-language instructions. You NEVER make a design or algorithmic decision on the candidate's behalf - the candidate must specify what to build, and you write exactly that, nothing more.

Problem statement:
{scenario_description}

Conversation so far (oldest first, each entry is what the candidate asked and how you responded):
{conversation_so_far}

Current code (the last code you produced - "(no code written yet)" if nothing has been written yet):
{current_code}

This is turn number {turn_number}. is_first_turn = {is_first_turn}.

The candidate's new instruction:
{candidate_prompt}

Classify this instruction and respond with exactly one of these three response_kind values:

1. "clarify" - the candidate asked for something concrete (a variable, a loop, a data structure, reading input, printing output, ...) but left out a MECHANICAL detail you need before you can write it (e.g. what data type, what to name it, which specific collection type, what format to print). Ask ONE direct question about that missing detail. Do not change any code this turn.

2. "refuse" - the candidate is asking YOU to make a design or algorithmic judgment call instead of specifying it themselves - e.g. "which loop is correct here", "is my approach right", "what's the best data structure for this", "what would you do", or any variant asking you to decide, validate, or compare approaches on their behalf. Always respond with EXACTLY this response_message, regardless of how the question is phrased: "I can't make that call for you - tell me specifically what you want (which loop, which data structure, which approach), and I'll write it." Do not change any code this turn, no matter how the request is worded or how many times it's asked.

3. "code_edit" - the candidate gave you a concrete, specific instruction you can act on directly (a tier-1 gap already resolved, or nothing was ambiguous to begin with). Write or modify the code to do EXACTLY and ONLY what this instruction asks. Do not fix, refactor, optimize, rename, or clean up anything else in the existing code, even if you notice a real bug or bad practice, unless the candidate's instruction explicitly asks you to change that specific thing. Return the FULL updated code in code_after (not a diff, not a fragment).

Special rule for turn 1 only (is_first_turn = true): if the candidate's very first instruction already gives you enough to start (they've said what the inputs/variables are and roughly what the program should do), your code_edit response must be a plain-language PSEUDOCODE skeleton of the simplest, most brute-force approach that satisfies what's been specified so far - not real {language} syntax yet, and not the efficient approach even if you can see one. If turn 1 doesn't yet give you enough to know what the inputs/output even are, respond "clarify" instead - do not guess.

response_message should be short and conversational either way - what you did (for code_edit) or the direct question/refusal (for clarify/refuse).

Respond with ONLY JSON, no other text, in one of these exact shapes:
{{"response_kind": "clarify", "response_message": "...", "code_after": null}}
{{"response_kind": "refuse", "response_message": "...", "code_after": null}}
{{"response_kind": "code_edit", "response_message": "...", "code_after": "...the full code..."}}
```

- [ ] **Step 3: Write `backend/app/prompts/round3_coding_scoring.txt`**

```
You are grading a candidate's AI-prompted coding session against a senior-hire bar. The candidate never wrote code by hand - every line came from an LLM they directed turn by turn - so what you're grading is the QUALITY OF THEIR DIRECTION: how precisely they specified requirements, how well they diagnosed and fixed problems from run output, and whether they pushed the solution from a brute-force start toward an efficient one. Be fair but rigorous - this score affects a hiring decision.

Problem statement:
{scenario_description}

The efficient approach a strong candidate should converge toward (reference only, never shown to the candidate):
{expected_approach}

Full turn-by-turn transcript (turn_number / candidate_prompt / response_kind / response_message / code_after, oldest first):
{conversation_so_far}

Automated test results against the candidate's FINAL code (input / expected_output / actual_output / passed):
{test_results}

Guardrail check: scan the transcript for any candidate_prompt that asked the assistant to make a design or algorithmic judgment call on the candidate's behalf (e.g. "which loop is correct", "is my approach right", "what's the best data structure") where the corresponding response_kind was "code_edit" or "clarify" instead of "refuse" - that is a guardrail violation (the assistant should have refused). List any you find in guardrail_violations. A single occurrence is not an automatic score penalty by itself, but a repeated PATTERN of the candidate asking the assistant to make decisions instead of specifying them itself should lower independent_judgment_score.

Evaluate:
- correctness_score (0-100): driven by the test_results pass rate.
- precision_score (0-100): how completely/unambiguously the candidate specified requirements - fewer "clarify" round-trips needed to pin down a mechanical detail reflects better command of the problem. A candidate who front-loads clear, complete instructions should score higher here than one who dribbles out details one at a time or leaves obvious gaps.
- efficiency_score (0-100): did the candidate iteratively push the solution from the brute-force starting point toward the expected_approach above, when the problem genuinely calls for it? A candidate who never asks to improve past the first working version, when a materially better approach was available, should score lower here.
- independent_judgment_score (0-100): did the candidate make their own design/algorithm decisions rather than repeatedly asking the assistant to decide? See the guardrail check above.
- final_score (0-100): overall, weighted toward correctness first, then precision and efficiency, with independent_judgment_score as a modifier - a candidate who leaned on the assistant to make decisions for them should not score as well as one who directed it precisely, even at similar correctness.
- misses (list of short strings): specific gaps - e.g. "never asked to optimize past the O(n^2) brute-force version", "left the data type unspecified until asked twice", "final code fails on empty input (test case: ...)".
- guardrail_violations (list of short strings, may be empty): each instance found per the guardrail check above.
- feedback_text: 3-6 sentences of direct, specific, constructive feedback as if written by a hiring panel member.

Respond with ONLY JSON, no other text, in this shape:
{{"correctness_score": 0, "precision_score": 0, "efficiency_score": 0, "independent_judgment_score": 0, "final_score": 0, "misses": ["..."], "guardrail_violations": ["..."], "feedback_text": "..."}}
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/prompts/round3_reference_generation.txt backend/app/prompts/round3_coding_turn.txt backend/app/prompts/round3_coding_scoring.txt
git commit -m "Add Round 3 (AI-prompted coding) prompts"
```

---

### Task 4: `execution_service.py`

**Files:**
- Create: `backend/app/services/execution_service.py`
- Test: `tests/test_execution_service.py`

**Interfaces:**
- Consumes: `settings.piston_api_url`, `settings.execution_timeout_seconds` (Task 1).
- Produces: `execution_service.run_code(language: str, code: str, stdin: list[str]) -> ExecutionResult`, where `ExecutionResult` has `.stdout`, `.stderr`, `.exit_code`, `.timed_out`, `.infra_error`, `.duration_ms`. `execution_service._execute(payload: dict) -> dict` is the single network call point tests monkeypatch. Task 6 (scoring) and Task 8 (candidate router) both call `run_code` directly with these exact names.

- [ ] **Step 1: Write the failing tests in `tests/test_execution_service.py`**

```python
"""
execution_service.py wraps calls to a hosted code-execution API (Piston) -
see docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md's
"Execution model". These tests monkeypatch _execute (the one function
that actually makes a network call, same isolation pattern llm_service.py
uses for _call_claude) so no real HTTP call ever happens in the suite.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import httpx
import pytest

from app.services import execution_service


def test_run_code_returns_stdout_on_success(monkeypatch):
    monkeypatch.setattr(execution_service, "_execute", lambda payload: {
        "run": {"stdout": "5\n", "stderr": "", "code": 0, "signal": None, "wall_time": 120},
    })
    result = execution_service.run_code(language="python", code="print(2 + 3)", stdin=[])
    assert result.stdout == "5\n"
    assert result.stderr == ""
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.infra_error is False
    assert result.duration_ms == 120


def test_run_code_passes_stdin_joined_by_newlines(monkeypatch):
    captured = {}

    def fake_execute(payload):
        captured["payload"] = payload
        return {"run": {"stdout": "5\n", "stderr": "", "code": 0, "signal": None, "wall_time": 50}}

    monkeypatch.setattr(execution_service, "_execute", fake_execute)
    execution_service.run_code(language="python", code="a=int(input());b=int(input());print(a+b)", stdin=["2", "3"])
    assert captured["payload"]["stdin"] == "2\n3"
    assert captured["payload"]["language"] == "python"


def test_run_code_detects_timeout(monkeypatch):
    monkeypatch.setattr(execution_service, "_execute", lambda payload: {
        "run": {"stdout": "", "stderr": "", "code": None, "signal": "SIGKILL", "wall_time": 10000},
    })
    result = execution_service.run_code(language="python", code="while True: pass", stdin=[])
    assert result.timed_out is True
    assert result.infra_error is False


def test_run_code_reports_infra_error_on_network_failure(monkeypatch):
    def boom(payload):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(execution_service, "_execute", boom)
    result = execution_service.run_code(language="python", code="print(1)", stdin=[])
    assert result.infra_error is True
    assert result.exit_code is None
    assert result.timed_out is False


def test_run_code_rejects_unsupported_language():
    with pytest.raises(ValueError):
        execution_service.run_code(language="ruby", code="puts 1", stdin=[])
```

- [ ] **Step 2: Run the tests, confirm they fail with an import error**

Run: `cd C:\Arun\Learning\Python\qa-eval-tool && python -m pytest tests/test_execution_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.execution_service'` (the module doesn't exist yet).

- [ ] **Step 3: Write `backend/app/services/execution_service.py`**

```python
"""
Executes candidate code via a hosted code-execution API (Piston) - see
docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md's "Execution
model" section. Round 3 (AI-prompted coding) is the only round that runs
real code; centralizing this the same way llm_service.py centralizes the
Claude API means there's one place to change providers/timeouts later.

Batch stdin only, not a live terminal: the candidate supplies every input
value upfront (see run_code's `stdin` argument), the process runs
start-to-finish in one call, and the full output is returned afterward.
"""
import httpx

from ..config import settings

# Piston requires an exact runtime version per language, not "latest" -
# see https://github.com/engineer-man/piston#executing-code. One place
# to bump these if the public instance's supported versions change.
_LANGUAGE_RUNTIME = {
    "python": {"language": "python", "version": "3.10.0", "filename": "main.py"},
    "java": {"language": "java", "version": "15.0.2", "filename": "Main.java"},
    "javascript": {"language": "javascript", "version": "18.15.0", "filename": "main.js"},
}


class ExecutionResult:
    def __init__(self, stdout: str, stderr: str, exit_code: int | None, timed_out: bool, infra_error: bool, duration_ms: int | None = None):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.infra_error = infra_error
        self.duration_ms = duration_ms


def _execute(payload: dict) -> dict:
    """The one function that actually calls the hosted execution API -
    isolated so tests can monkeypatch just this, the same pattern
    llm_service.py uses for _call_claude."""
    response = httpx.post(
        f"{settings.piston_api_url}/execute", json=payload,
        timeout=settings.execution_timeout_seconds + 5,  # a little slack over Piston's own run_timeout below
    )
    response.raise_for_status()
    return response.json()


def run_code(language: str, code: str, stdin: list[str]) -> ExecutionResult:
    """Runs `code` once, feeding `stdin` (one value per line, in order)
    to the process's standard input - a single batch call, not a live
    interactive session (see this module's docstring)."""
    runtime = _LANGUAGE_RUNTIME.get(language)
    if runtime is None:
        raise ValueError(f"Unsupported language: {language}")

    payload = {
        "language": runtime["language"],
        "version": runtime["version"],
        "files": [{"name": runtime["filename"], "content": code}],
        "stdin": "\n".join(stdin),
        "run_timeout": settings.execution_timeout_seconds * 1000,
    }
    try:
        data = _execute(payload)
    except (httpx.HTTPError, ValueError, KeyError):
        return ExecutionResult(stdout="", stderr="", exit_code=None, timed_out=False, infra_error=True)

    run = data.get("run", {})
    # Piston reports a killed-by-timeout run via signal "SIGKILL" rather
    # than a dedicated boolean field - see the API's documented shape.
    timed_out = run.get("signal") == "SIGKILL"
    return ExecutionResult(
        stdout=run.get("stdout", ""),
        stderr=run.get("stderr", ""),
        exit_code=run.get("code"),
        timed_out=timed_out,
        infra_error=False,
        duration_ms=run.get("wall_time"),
    )
```

Verify against Piston's live API docs (https://github.com/engineer-man/piston/tree/master/api) during this step in case field names (`run.stdout`/`run.code`/`run.signal`/`run.wall_time`) or supported runtime versions have changed since this plan was written — adjust the dict keys in `_LANGUAGE_RUNTIME` and the `run.get(...)` calls above to match if so; the test suite in this task (which mocks `_execute`'s return value) will still pass unchanged as long as `run_code`'s own parsing logic matches whatever shape you confirm.

- [ ] **Step 4: Run the tests, confirm they pass**

Run: `cd C:\Arun\Learning\Python\qa-eval-tool && python -m pytest tests/test_execution_service.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/execution_service.py tests/test_execution_service.py
git commit -m "Add execution_service for Round 3's hosted code execution"
```

---

### Task 5: `llm_service.py` additions

**Files:**
- Modify: `backend/app/services/llm_service.py`
- Test: `tests/test_llm_service_round3_coding.py` (new)

**Interfaces:**
- Consumes: `Round3CodingTurnResponse` (Task 2), the 3 prompt files (Task 3).
- Produces: `llm_service.generate_round3_reference(scenario_description, experience_band) -> dict`, `llm_service.round3_coding_turn(scenario_description, language, conversation_so_far, current_code, candidate_prompt, turn_number) -> dict`, `llm_service.score_round3_coding(scenario_description, expected_approach, conversation_so_far, test_results) -> dict`. Task 6 (scoring) and Task 7/8 (routers) call these exact names/signatures.

- [ ] **Step 1: Write the failing tests**

```python
"""
llm_service.py's Round 3 (AI-prompted coding) functions - see
docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md. These tests
monkeypatch _call_claude (the one function that actually calls the Claude
API), same isolation pattern every other llm_service test in this repo
uses.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest

from app.services import llm_service


def test_generate_round3_reference_returns_test_cases_and_expected_approach(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "test_cases": [{"input": "2 3", "expected_output": "5", "description": "basic sum"}],
        "expected_approach": "Read two integers and add them directly.",
    }))
    result = llm_service.generate_round3_reference(scenario_description="Add two numbers", experience_band="0-7")
    assert result["test_cases"][0]["expected_output"] == "5"
    assert "add them" in result["expected_approach"]


def test_generate_round3_reference_rejects_malformed_shape(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({"oops": True}))
    with pytest.raises(ValueError):
        llm_service.generate_round3_reference(scenario_description="x", experience_band="0-7")


def test_round3_coding_turn_classifies_code_edit(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "code_edit", "response_message": "Added the two variables and printed their sum.",
        "code_after": "a = int(input())\nb = int(input())\nprint(a + b)",
    }))
    result = llm_service.round3_coding_turn(
        scenario_description="Add two numbers", language="python",
        conversation_so_far=[], current_code=None,
        candidate_prompt="I need two int variables read from stdin, then print their sum",
        turn_number=1,
    )
    assert result["response_kind"] == "code_edit"
    assert "a + b" in result["code_after"]


def test_round3_coding_turn_classifies_clarify(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "clarify", "response_message": "What data type should the variable be?",
        "code_after": None,
    }))
    result = llm_service.round3_coding_turn(
        scenario_description="Add two numbers", language="python",
        conversation_so_far=[], current_code=None,
        candidate_prompt="I need a variable", turn_number=1,
    )
    assert result["response_kind"] == "clarify"
    assert result["code_after"] is None


def test_round3_coding_turn_rejects_code_edit_without_code(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "code_edit", "response_message": "done", "code_after": None,
    }))
    with pytest.raises(ValueError):
        llm_service.round3_coding_turn(
            scenario_description="x", language="python", conversation_so_far=[],
            current_code=None, candidate_prompt="do it", turn_number=1,
        )


def test_score_round3_coding_includes_provenance(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "correctness_score": 100, "precision_score": 80, "efficiency_score": 70,
        "independent_judgment_score": 90, "final_score": 85,
        "misses": [], "guardrail_violations": [], "feedback_text": "Solid work.",
    }))
    result = llm_service.score_round3_coding(
        scenario_description="Add two numbers", expected_approach="read two ints, add them",
        conversation_so_far=[], test_results=[{"input": "2 3", "expected_output": "5", "actual_output": "5", "passed": True}],
    )
    assert result["final_score"] == 85
    assert "_provenance" in result
    assert result["_provenance"]["prompt_file"] == "round3_coding_scoring.txt"
```

- [ ] **Step 2: Run the tests, confirm they fail**

Run: `cd C:\Arun\Learning\Python\qa-eval-tool && python -m pytest tests/test_llm_service_round3_coding.py -v`
Expected: FAIL — `AttributeError: module 'app.services.llm_service' has no attribute 'generate_round3_reference'` (and similarly for the other two functions).

- [ ] **Step 3: Add the 3 functions to `backend/app/services/llm_service.py`**

1. Update the import line to bring in the new response schema:
```python
from ..schemas import Round4TurnResponse, Round4EnvironmentOut, Round4UiMockupOut, Round3CodingTurnResponse
```

2. Add this new section anywhere after the Round 1 section and before the Round 4 section (matching numeric order):

```python
# ---- Round 3 (AI-prompted coding: the candidate never writes code
# directly - they direct the LLM turn by turn, and it writes/edits the
# actual source. See
# docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md.) ----

def generate_round3_reference(scenario_description: str, experience_band: str) -> dict:
    prompt = _load_prompt("round3_reference_generation.txt").format(
        scenario_description=scenario_description,
        experience_band=experience_band,
    )
    raw = _call_claude(prompt)
    result = _parse_json_response(raw)
    if not isinstance(result, dict) or "test_cases" not in result or "expected_approach" not in result:
        raise ValueError(f"Expected a JSON object with 'test_cases' and 'expected_approach' keys, got: {result!r}")
    return result


def round3_coding_turn(
    scenario_description: str,
    language: str,
    conversation_so_far: list[dict],
    current_code: str | None,
    candidate_prompt: str,
    turn_number: int,
) -> dict:
    prompt = _load_prompt("round3_coding_turn.txt").format(
        scenario_description=scenario_description,
        language=language,
        conversation_so_far=json.dumps(conversation_so_far, indent=2),
        current_code=current_code or "(no code written yet)",
        candidate_prompt=candidate_prompt,
        turn_number=turn_number,
        is_first_turn="true" if turn_number == 1 else "false",
    )
    raw = _call_claude(prompt, max_tokens=2048)
    result = _parse_json_response(raw)
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object for the assistant's turn, got: {type(result)}")
    try:
        return Round3CodingTurnResponse.model_validate(result).model_dump()
    except ValidationError as e:
        raise ValueError(f"Assistant's turn response didn't match the expected shape: {e}") from e


def score_round3_coding(
    scenario_description: str,
    expected_approach: str,
    conversation_so_far: list[dict],
    test_results: list[dict],
) -> dict:
    prompt_text = _load_prompt("round3_coding_scoring.txt")
    prompt = prompt_text.format(
        scenario_description=scenario_description,
        expected_approach=expected_approach,
        conversation_so_far=json.dumps(conversation_so_far, indent=2),
        test_results=json.dumps(test_results, indent=2),
    )
    raw = _call_claude(prompt, max_tokens=2048)
    result = _parse_json_response(raw)
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object for scoring, got: {type(result)}")
    result["_provenance"] = _scoring_provenance("round3_coding_scoring.txt", prompt_text)
    return result
```

- [ ] **Step 4: Run the tests, confirm they pass**

Run: `cd C:\Arun\Learning\Python\qa-eval-tool && python -m pytest tests/test_llm_service_round3_coding.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Run the full suite**

Run: `cd C:\Arun\Learning\Python\qa-eval-tool && python -m pytest tests/ -q`
Expected: same failures as Task 2 Step 6 (only `test_settings.py`), nothing new broken.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/llm_service.py tests/test_llm_service_round3_coding.py
git commit -m "Add Round 3 (AI-prompted coding) LLM functions"
```

---

### Task 6: `scoring_service.py` addition

**Files:**
- Modify: `backend/app/services/scoring_service.py`
- Test: `tests/test_scoring_service_round3_coding.py` (new)

**Interfaces:**
- Consumes: `llm_service.score_round3_coding` (Task 5), `execution_service.run_code` (Task 4), `Round3Turn` (Task 1).
- Produces: `scoring_service.score_round3_submission(db, submission) -> Score`, registered in `_SCORERS[3]`. Task 8's submit endpoint schedules this via the existing `score_submission_in_background` dispatcher — no new wiring needed there beyond the `_SCORERS` entry added in this task.

- [ ] **Step 1: Write the failing test**

```python
"""
scoring_service.score_round3_submission - re-runs the candidate's final
code against the scenario's reference test suite for an objective pass
rate, then blends in one LLM judgment call. Uses the `client` fixture
purely to get a real DB session (via the same TestingSessionLocal the
app itself uses) - this test calls scoring_service directly rather than
through the HTTP API, since what's being verified here is the scoring
MATH (coverage_score computation, guardrail-violation prefixing in
misses_json), not the endpoint plumbing (see test_round3.py for the
full HTTP-level happy path).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from datetime import datetime

from app.database import SessionLocal
from app.models import Scenario, Submission, Round3Turn, RoundStatus, ScenarioStatus, ExperienceBand, User, Role
from app.services import scoring_service, llm_service, execution_service


def test_score_round3_submission_computes_coverage_and_flags_guardrail_violations(client, monkeypatch):
    db = SessionLocal()
    try:
        candidate = db.query(User).filter(User.role == Role.candidate).first()
        scenario = Scenario(
            round_number=3, title="Add two numbers", description="Read two ints, print their sum.",
            experience_band=ExperienceBand.junior, created_by=candidate.id, status=ScenarioStatus.published,
            is_live=True, time_limit_minutes=30,
            reference_json={
                "test_cases": [
                    {"input": "2 3", "expected_output": "5", "description": "basic sum"},
                    {"input": "0 0", "expected_output": "0", "description": "zeros"},
                ],
                "expected_approach": "Read two integers and add them directly.",
            },
        )
        db.add(scenario)
        db.commit()
        db.refresh(scenario)

        submission = Submission(
            user_id=candidate.id, scenario_id=scenario.id, round_number=3,
            status=RoundStatus.submitted, started_at=datetime.utcnow(),
            content={"language": "python", "draft_prompt": ""},
        )
        db.add(submission)
        db.commit()
        db.refresh(submission)

        turn = Round3Turn(
            submission_id=submission.id, turn_number=1, candidate_prompt="which loop is correct here?",
            language="python", response_kind="code_edit",  # a guardrail violation on purpose - see below
            response_message="Used a for loop.",
            code_after="a=int(input());b=int(input());print(a+b)",
        )
        db.add(turn)
        db.commit()

        def fake_run_code(language, code, stdin):
            joined = " ".join(stdin)
            output = {"2 3": "5", "0 0": "0"}.get(joined, "")
            return execution_service.ExecutionResult(stdout=output, stderr="", exit_code=0, timed_out=False, infra_error=False)

        monkeypatch.setattr(execution_service, "run_code", fake_run_code)
        monkeypatch.setattr(llm_service, "score_round3_coding", lambda **kwargs: {
            "correctness_score": 100, "precision_score": 90, "efficiency_score": 80,
            "independent_judgment_score": 40, "final_score": 75,
            "misses": ["Never asked to validate non-numeric input."],
            "guardrail_violations": ["Turn 1: asked which loop is correct instead of specifying it."],
            "feedback_text": "Correct and efficient, but leaned on the assistant for a design decision.",
        })

        score = scoring_service.score_round3_submission(db, submission)

        assert score.coverage_score == 100  # both test cases passed
        assert score.final_score == 75
        assert "Never asked to validate non-numeric input." in score.misses_json
        assert any("Guardrail" in m and "which loop is correct" in m for m in score.misses_json)
        db.refresh(submission)
        assert submission.status == RoundStatus.scored
    finally:
        db.close()
```

- [ ] **Step 2: Run the test, confirm it fails**

Run: `cd C:\Arun\Learning\Python\qa-eval-tool && python -m pytest tests/test_scoring_service_round3_coding.py -v`
Expected: FAIL — `AttributeError: module 'app.services.scoring_service' has no attribute 'score_round3_submission'`.

- [ ] **Step 3: Add `score_round3_submission` to `backend/app/services/scoring_service.py`**

1. Update the import line: `from . import llm_service` → `from . import llm_service, execution_service`.
2. Add this function right before `score_round4_submission` (matching numeric order):

```python
def score_round3_submission(db: Session, submission: Submission) -> Score:
    """Round 3 (AI-prompted coding): re-run the candidate's final code
    against the scenario's HR-approved test suite for an objective pass
    rate, then one LLM call judges the direction quality (precision,
    efficiency, independent judgment) against the full transcript."""
    scenario = submission.scenario
    reference = scenario.reference_json or {}
    test_cases = reference.get("test_cases", [])
    expected_approach = reference.get("expected_approach", "")

    turns = submission.round3_turns
    language = (submission.content or {}).get("language", "")
    final_code = next((t.code_after for t in reversed(turns) if t.code_after), None)

    if final_code is None:
        # No turn ever produced real code - nothing to run. See the
        # design spec's Scoring section: skip execution entirely rather
        # than attempting to run nothing.
        test_results = [{**tc, "actual_output": None, "passed": False} for tc in test_cases]
    else:
        test_results = []
        for tc in test_cases:
            result = execution_service.run_code(language=language, code=final_code, stdin=[tc["input"]])
            actual_output = None if result.infra_error else (result.stdout or "").strip()
            test_results.append({
                **tc,
                "actual_output": actual_output,
                "passed": actual_output is not None and actual_output == str(tc["expected_output"]).strip(),
            })

    conversation_payload = [
        {
            "turn_number": t.turn_number, "candidate_prompt": t.candidate_prompt,
            "response_kind": t.response_kind, "response_message": t.response_message,
            "code_after": t.code_after,
        }
        for t in turns
    ]

    result = llm_service.score_round3_coding(
        scenario_description=scenario.description,
        expected_approach=expected_approach,
        conversation_so_far=conversation_payload,
        test_results=test_results,
    )

    score = _get_or_create_score(db, submission)
    _apply_provenance(score, result)
    passed_count = sum(1 for r in test_results if r["passed"])
    score.coverage_score = round(passed_count / len(test_results) * 100) if test_results else 0
    score.misses_json = result.get("misses", []) + [f"Guardrail: {g}" for g in result.get("guardrail_violations", [])]
    score.final_score = result.get("final_score")
    score.feedback_text = result.get("feedback_text")
    score.raw_llm_response_json = {"test_results": test_results, "conversation": conversation_payload, "scoring": result}
    submission.status = RoundStatus.scored
    db.commit()
    db.refresh(score)
    return score
```

3. Add it to the dispatcher:
```python
_SCORERS = {
    1: score_round1_submission,
    2: score_round2_investigation,
    3: score_round3_submission,
    4: score_round4_submission,
}
```

- [ ] **Step 4: Run the test, confirm it passes**

Run: `cd C:\Arun\Learning\Python\qa-eval-tool && python -m pytest tests/test_scoring_service_round3_coding.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scoring_service.py tests/test_scoring_service_round3_coding.py
git commit -m "Add Round 3 (AI-prompted coding) scoring"
```

---

### Task 7: HR router (`routers/hr.py`) + `conftest.py` helper

**Files:**
- Modify: `backend/app/routers/hr.py`, `tests/conftest.py`
- Test: `tests/test_round3.py` (new — created here with its first HR-authoring test; Task 10 adds the rest)

**Interfaces:**
- Consumes: `llm_service.generate_round3_reference` (Task 5).
- Produces: HR can create/publish a round_number=3 scenario through the existing generic draft→publish flow; `_publish_scenario(client, hr_token, monkeypatch, round_number=3, ...)` in `conftest.py` is the exact helper Task 8/10's tests reuse.

- [ ] **Step 1: Add round 3 to `conftest.py`'s reference-generator/fake-reference maps**

Add a new fake reference fixture near `FAKE_REFERENCE`:
```python
FAKE_ROUND3_CODING_REFERENCE = {
    "test_cases": [{"input": "2 3", "expected_output": "5", "description": "basic sum"}],
    "expected_approach": "Read two integers and add them directly.",
}
```

Change:
```python
_REFERENCE_GENERATOR_BY_ROUND = {
    1: "generate_round1_reference",
    2: "generate_round2_reference",
}
```
to:
```python
_REFERENCE_GENERATOR_BY_ROUND = {
    1: "generate_round1_reference",
    2: "generate_round2_reference",
    3: "generate_round3_reference",
}

_FAKE_REFERENCE_BY_ROUND = {
    1: lambda: list(FAKE_REFERENCE),
    2: lambda: list(FAKE_REFERENCE),
    3: lambda: dict(FAKE_ROUND3_CODING_REFERENCE),
}
```

And in `_publish_scenario`, change:
```python
    monkeypatch.setattr(llm_service, generator_name, lambda **kwargs: list(FAKE_REFERENCE))
```
to:
```python
    monkeypatch.setattr(llm_service, generator_name, lambda **kwargs: _FAKE_REFERENCE_BY_ROUND[round_number]())
```

- [ ] **Step 2: Write the failing test in the new `tests/test_round3.py`**

```python
"""
Round 3: AI-prompted coding - see
docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md. Not to be
confused with tests/test_round4.py (the renamed automation round).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from .conftest import HR_EMAIL, HR_PASSWORD, _login, _auth, _publish_scenario


def test_hr_can_create_and_publish_a_round3_coding_scenario(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    scenario = _publish_scenario(client, hr_token, monkeypatch, round_number=3, title="Add two numbers")
    assert scenario["round_number"] == 3
    assert scenario["status"] == "published"

    fetched = client.get(f"/hr/scenarios/{scenario['id']}", cookies=_auth(hr_token)).json()
    assert fetched["reference_json"]["test_cases"][0]["expected_output"] == "5"
    assert "expected_approach" in fetched["reference_json"]
```

- [ ] **Step 3: Run the test, confirm it fails**

Run: `cd C:\Arun\Learning\Python\qa-eval-tool && python -m pytest tests/test_round3.py -v`
Expected: FAIL — `400: round_number must be 1, 2, or 4` (round 3 isn't accepted yet by `create_scenario`).

- [ ] **Step 4: Update `backend/app/routers/hr.py`**

1. `create_scenario`: change `if payload.round_number not in (1, 2, 4):` back to `if payload.round_number not in (1, 2, 3, 4):`; update the error message to `"round_number must be 1, 2, 3, or 4"`.

2. `_generate_reference_unsafe`: this currently has `if round_number == 1: ... elif round_number == 2: ... elif round_number == 4: ...` (per the prior renumbering plan). Add a new branch for round 3, ordered numerically:
```python
    elif scenario.round_number == 3:
        scenario.reference_json = llm_service.generate_round3_reference(
            scenario_description=scenario.description,
            experience_band=scenario.experience_band.value,
        )
```
(placed before the existing `elif scenario.round_number == 4:` branch).

3. `ROUND_LABELS`: `{1: "Manual test cases", 2: "Debugging", 4: "Conversational"}` → `{1: "Manual test cases", 2: "Debugging", 3: "AI-prompted coding", 4: "Conversational"}`.

4. `_gather_candidate_rounds`: `for round_number in (1, 2, 4):` → `(1, 2, 3, 4)`.

5. `_build_candidate_summary`: `for round_number in (1, 2, 4):` → `(1, 2, 3, 4)`.

6. `_build_submission_reports`: this currently has `if s.round_number == 4: report.test_cases = [...]`. Add a round-3 branch, since round 3 (coding)'s report needs turns/runs, not test_cases — add an `elif s.round_number == 3:` populating a `conversation_turns`-shaped field. Since `SubmissionReportOut` doesn't yet have a round-3-shaped field, this needs a small schema addition too: in `schemas.py`, add to `SubmissionReportOut`:
```python
    round3_turns: Optional[list["Round3TurnOut"]] = None
    round3_runs: Optional[list["Round3RunOut"]] = None
```
Then in `_build_submission_reports`:
```python
        if s.round_number == 3:
            report.round3_turns = [Round3TurnOut.model_validate(t) for t in s.round3_turns]
            report.round3_runs = [Round3RunOut.model_validate(r) for r in s.round3_execution_runs]
        elif s.round_number == 4:
            report.test_cases = [
                Round4TestCaseOut(
                    id=tc.id, title=tc.title, draft_prompt=tc.draft_prompt,
                    created_at=tc.created_at, turn_count=len(tc.turns),
                )
                for tc in s.round4_test_cases
            ]
```

7. `_app_settings_out`: add `round3_passing_score=app_settings.round3_passing_score,` to the `AppSettingsOut(...)` call (placed before `round4_passing_score=...`).

- [ ] **Step 5: Run the test, confirm it passes**

Run: `cd C:\Arun\Learning\Python\qa-eval-tool && python -m pytest tests/test_round3.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `cd C:\Arun\Learning\Python\qa-eval-tool && python -m pytest tests/ -q`
Expected: same pre-existing `test_settings.py` failures as before (Task 10 fixes those), nothing else broken.

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/hr.py backend/app/schemas.py tests/conftest.py tests/test_round3.py
git commit -m "Wire Round 3 (AI-prompted coding) scenario authoring into the HR router"
```

---

### Task 8: Candidate router (`routers/candidate.py`) — new endpoints

**Files:**
- Modify: `backend/app/routers/candidate.py`
- Test: `tests/test_round3.py` (appended to)

**Interfaces:**
- Consumes: `Round3Turn`/`Round3ExecutionRun` (Task 1), `Round3StartRequest`/`Round3DraftUpdate`/`Round3TurnCreate`/`Round3TurnOut`/`Round3RunCreate`/`Round3RunOut`/`Round3StateOut` (Task 2), `llm_service.round3_coding_turn` (Task 5), `execution_service.run_code` (Task 4).
- Produces: `POST /candidate/round/3/start`, `PATCH /candidate/round/3/draft`, `POST /candidate/round/3/turn`, `POST /candidate/round/3/run`, `GET /candidate/round/3/state`, `POST /candidate/round/3/submit` — the exact paths Task 9's frontend calls.

- [ ] **Step 1: Write the failing tests (append to `tests/test_round3.py`)**

```python
def test_round3_coding_full_happy_path(client, monkeypatch):
    from app.services import llm_service, execution_service
    from .conftest import CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="R2")
    _publish_scenario(client, hr_token, monkeypatch, round_number=3, title="Add two numbers")

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    monkeypatch.setattr(llm_service, "score_round2_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post("/candidate/round/1/submit", json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))
    client.post("/candidate/round/2/submit", json={"investigation": [{"area": "x"}], "root_cause": "x"}, cookies=_auth(cand_token))

    # Round 3 isn't reachable without a language.
    res = client.post("/candidate/round/3/start", json={}, cookies=_auth(cand_token))
    assert res.status_code == 422

    res = client.post("/candidate/round/3/start", json={"language": "python"}, cookies=_auth(cand_token))
    assert res.status_code == 201
    # Idempotent - a second call with the same body doesn't reset anything.
    res2 = client.post("/candidate/round/3/start", json={"language": "python"}, cookies=_auth(cand_token))
    assert res2.json()["id"] == res.json()["id"]

    state = client.get("/candidate/round/3/state", cookies=_auth(cand_token)).json()
    assert state["language"] == "python"
    assert state["turns"] == []

    # Can't run before any code exists.
    res = client.post("/candidate/round/3/run", json={"stdin": ["2", "3"]}, cookies=_auth(cand_token))
    assert res.status_code == 400

    monkeypatch.setattr(llm_service, "round3_coding_turn", lambda **kwargs: {
        "response_kind": "code_edit", "response_message": "Added the two variables and printed their sum.",
        "code_after": "a = int(input())\nb = int(input())\nprint(a + b)",
    })
    turn_res = client.post("/candidate/round/3/turn", json={"candidate_prompt": "read two ints and print their sum"}, cookies=_auth(cand_token))
    assert turn_res.status_code == 201
    assert turn_res.json()["response_kind"] == "code_edit"
    assert "a + b" in turn_res.json()["code_after"]

    monkeypatch.setattr(execution_service, "run_code", lambda **kwargs: execution_service.ExecutionResult(
        stdout="5\n", stderr="", exit_code=0, timed_out=False, infra_error=False, duration_ms=42,
    ))
    run_res = client.post("/candidate/round/3/run", json={"stdin": ["2", "3"]}, cookies=_auth(cand_token))
    assert run_res.status_code == 201
    assert run_res.json()["stdout"] == "5\n"

    state = client.get("/candidate/round/3/state", cookies=_auth(cand_token)).json()
    assert len(state["turns"]) == 1
    assert len(state["runs"]) == 1

    monkeypatch.setattr(llm_service, "score_round3_coding", lambda **kwargs: {
        "correctness_score": 100, "precision_score": 90, "efficiency_score": 80,
        "independent_judgment_score": 90, "final_score": 90,
        "misses": [], "guardrail_violations": [], "feedback_text": "Great work.",
    })
    submit_res = client.post("/candidate/round/3/submit", cookies=_auth(cand_token))
    assert submit_res.status_code == 201
    assert submit_res.json()["status"] == "submitted"


def test_round3_coding_turn_asking_which_loop_is_correct_gets_refused(client, monkeypatch):
    from app.services import llm_service
    from .conftest import CANDIDATE2_EMAIL, CANDIDATE2_PASSWORD

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1", band="0-7")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="R2", band="0-7")
    _publish_scenario(client, hr_token, monkeypatch, round_number=3, title="Add two numbers", band="0-7")

    cand_token = _login(client, CANDIDATE2_EMAIL, CANDIDATE2_PASSWORD)
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    monkeypatch.setattr(llm_service, "score_round2_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post("/candidate/round/1/submit", json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))
    client.post("/candidate/round/2/submit", json={"investigation": [{"area": "x"}], "root_cause": "x"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/start", json={"language": "python"}, cookies=_auth(cand_token))

    monkeypatch.setattr(llm_service, "round3_coding_turn", lambda **kwargs: {
        "response_kind": "refuse",
        "response_message": "I can't make that call for you - tell me specifically what you want (which loop, which data structure, which approach), and I'll write it.",
        "code_after": None,
    })
    res = client.post("/candidate/round/3/turn", json={"candidate_prompt": "which loop is correct here?"}, cookies=_auth(cand_token))
    assert res.status_code == 201
    assert res.json()["response_kind"] == "refuse"
    assert res.json()["code_after"] is None


def test_round3_coding_current_code_threads_between_turns(client, monkeypatch):
    """Verifies THIS APP's own code correctly passes the prior turn's
    code_after as current_code into the next LLM call - not a claim
    about what the (mocked) LLM does with it, which is a prompt-design
    property audited at scoring time instead (see
    test_scoring_service_round3_coding.py's guardrail-violation test)."""
    from app.services import llm_service
    from .conftest import CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1", band="7+")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="R2", band="7+")
    _publish_scenario(client, hr_token, monkeypatch, round_number=3, title="Add two numbers", band="7+")

    cand_token = _login(client, CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD)
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    monkeypatch.setattr(llm_service, "score_round2_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post("/candidate/round/1/submit", json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))
    client.post("/candidate/round/2/submit", json={"investigation": [{"area": "x"}], "root_cause": "x"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/start", json={"language": "python"}, cookies=_auth(cand_token))

    monkeypatch.setattr(llm_service, "round3_coding_turn", lambda **kwargs: {
        "response_kind": "code_edit", "response_message": "ok", "code_after": "a=1",
    })
    client.post("/candidate/round/3/turn", json={"candidate_prompt": "first"}, cookies=_auth(cand_token))

    captured = {}

    def fake_turn(**kwargs):
        captured.update(kwargs)
        return {"response_kind": "code_edit", "response_message": "ok", "code_after": "a=1\nb=2"}

    monkeypatch.setattr(llm_service, "round3_coding_turn", fake_turn)
    client.post("/candidate/round/3/turn", json={"candidate_prompt": "second"}, cookies=_auth(cand_token))

    assert captured["current_code"] == "a=1"
    assert captured["turn_number"] == 2
    assert len(captured["conversation_so_far"]) == 1
    assert captured["conversation_so_far"][0]["candidate_prompt"] == "first"
```

- [ ] **Step 2: Run the tests, confirm they fail**

Run: `cd C:\Arun\Learning\Python\qa-eval-tool && python -m pytest tests/test_round3.py -v`
Expected: FAIL — `404` on `/candidate/round/3/start` (endpoint doesn't exist yet; falls through to the generic `/round/{round_number}/start`, which 400s since round 3 isn't in that generic tuple).

- [ ] **Step 3: Update `backend/app/routers/candidate.py`**

1. Update imports:
```python
from ..models import User, Scenario, Submission, RoundStatus, ConversationTurn, Round4TestCase, CandidateAppearance, Round3Turn, Round3ExecutionRun
from ..schemas import (
    RoundStateOut, SubmissionCreate, SubmissionOut,
    Round4StateOut, Round4TurnCreate, Round4TurnOut,
    Round4TestCaseCreate, Round4TestCaseOut, Round4DraftUpdate, Round4EnvironmentOut,
    Round4UiMockupOut, Round2SubmissionCreate, Round4CodeSnippetOut, ExpireRoundPayload,
    Round3StartRequest, Round3DraftUpdate, Round3TurnCreate, Round3TurnOut,
    Round3RunCreate, Round3RunOut, Round3StateOut,
)
from ..dependencies import require_candidate
from ..services import llm_service, execution_service
from ..services.scoring_service import score_submission_in_background, close_expired_submissions
```
(this widens the existing `from ..models import ...` / `from ..schemas import (...)` / `from ..services import llm_service` lines — match against the post-renumbering-plan versions of those lines in the file rather than assuming exact prior wording.)

2. Add this new section, registered **before** the generic `/round/{round_number}/...` routes (same routing-order requirement as Round 2/4's dedicated endpoints — add it right after the Round 2 section and before the Round 4 section, keeping numeric order):

```python
# ---- Round 3 (AI-prompted coding: the candidate never writes code
# directly - they direct the LLM turn by turn via dedicated endpoints
# below, registered ahead of the generic /round/{round_number}/... routes
# for the same routing-order reason Round 2/4's dedicated endpoints are -
# see those sections' comments.) ----

def _round3_coding_scenario_and_submission(candidate: User, db: Session) -> tuple[Scenario, Submission]:
    scenario = _live_scenario(db, 3, candidate)
    if scenario is None:
        raise HTTPException(404, "No published scenario for round 3 yet - check back once HR has published one.")
    submission = (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.scenario_id == scenario.id, Submission.archived.is_(False))
        .first()
    )
    if submission is None:
        raise HTTPException(404, "Round 3 hasn't been started yet - call /round/3/start first.")
    return scenario, submission


@router.post("/round/3/start", response_model=SubmissionOut, status_code=201)
def start_round3(payload: Round3StartRequest, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(3, db, candidate)
    scenario = _live_scenario(db, 3, candidate)
    if scenario is None:
        raise HTTPException(404, "No published scenario for round 3 yet - check back once HR has published one.")

    existing = (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.scenario_id == scenario.id, Submission.archived.is_(False))
        .first()
    )
    if existing is not None:
        return existing  # idempotent: same started_at, same language, same timer deadline

    submission = Submission(
        user_id=candidate.id, scenario_id=scenario.id, round_number=3,
        status=RoundStatus.in_progress, started_at=datetime.utcnow(),
        content={"language": payload.language, "draft_prompt": ""},
        appearance_id=_current_appearance_id(db, candidate),
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)
    return submission


@router.patch("/round/3/draft", status_code=204)
def round3_coding_save_draft(payload: Round3DraftUpdate, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(3, db, candidate)
    _, submission = _round3_coding_scenario_and_submission(candidate, db)
    if submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round has already been submitted.")
    submission.content = {**(submission.content or {}), "draft_prompt": payload.draft_prompt}
    db.commit()


@router.post("/round/3/turn", response_model=Round3TurnOut, status_code=201)
def round3_coding_turn(payload: Round3TurnCreate, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(3, db, candidate)
    scenario, submission = _round3_coding_scenario_and_submission(candidate, db)
    if submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round has already been submitted.")

    language = (submission.content or {}).get("language")
    existing_turns = submission.round3_turns
    conversation_so_far = [
        {
            "turn_number": t.turn_number, "candidate_prompt": t.candidate_prompt,
            "response_kind": t.response_kind, "response_message": t.response_message,
            "code_after": t.code_after,
        }
        for t in existing_turns
    ]
    current_code = next((t.code_after for t in reversed(existing_turns) if t.code_after), None)
    turn_number = len(existing_turns) + 1

    # Called synchronously in the request path (same reasoning as Round
    # 4's round4_turn - see that function's comment): nothing is
    # persisted below until the LLM call succeeds and validates.
    try:
        response = llm_service.round3_coding_turn(
            scenario_description=scenario.description,
            language=language,
            conversation_so_far=conversation_so_far,
            current_code=current_code,
            candidate_prompt=payload.candidate_prompt,
            turn_number=turn_number,
        )
    except Exception:
        raise HTTPException(502, "The assistant had trouble responding just now - try sending your message again.")

    turn = Round3Turn(
        submission_id=submission.id,
        turn_number=turn_number,
        candidate_prompt=payload.candidate_prompt,
        language=language,
        response_kind=response["response_kind"],
        response_message=response["response_message"],
        code_after=response.get("code_after"),
    )
    db.add(turn)
    submission.content = {**(submission.content or {}), "draft_prompt": ""}
    db.commit()
    db.refresh(turn)
    return turn


@router.post("/round/3/run", response_model=Round3RunOut, status_code=201)
def round3_coding_run(payload: Round3RunCreate, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(3, db, candidate)
    _, submission = _round3_coding_scenario_and_submission(candidate, db)
    if submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round has already been submitted.")

    latest_turn = next((t for t in reversed(submission.round3_turns) if t.code_after), None)
    if latest_turn is None:
        raise HTTPException(400, "There's no code to run yet - direct the assistant to write some first.")

    language = (submission.content or {}).get("language")
    result = execution_service.run_code(language=language, code=latest_turn.code_after, stdin=payload.stdin)

    run = Round3ExecutionRun(
        submission_id=submission.id,
        turn_id=latest_turn.id,
        language=language,
        code_snapshot=latest_turn.code_after,
        stdin_json=payload.stdin,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        infra_error=result.infra_error,
        duration_ms=result.duration_ms,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


@router.get("/round/3/state", response_model=Round3StateOut)
def round3_coding_state(db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(3, db, candidate)
    scenario, submission = _round3_coding_scenario_and_submission(candidate, db)
    return Round3StateOut(
        scenario=scenario, submission=submission,
        language=(submission.content or {}).get("language"),
        turns=submission.round3_turns, runs=submission.round3_execution_runs,
    )


@router.post("/round/3/submit", response_model=SubmissionOut, status_code=201)
def round3_coding_submit(background_tasks: BackgroundTasks, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(3, db, candidate)
    scenario, submission = _round3_coding_scenario_and_submission(candidate, db)
    if submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round has already been submitted.")
    _require_within_time_limit(submission, scenario)

    if not submission.round3_turns:
        raise HTTPException(400, "Send at least one message before submitting.")

    submission.status = RoundStatus.submitted
    db.commit()
    db.refresh(submission)
    background_tasks.add_task(score_submission_in_background, submission.id)
    return submission
```

3. Widen the 3 generic tuples that need round 3 back:
   - `get_round`: `if round_number not in (1, 2, 4):` → `if round_number not in (1, 2, 3, 4):`; message → `"round_number must be 1, 2, 3, or 4"`.
   - `log_tab_switch`: same `(1, 2, 4)` → `(1, 2, 3, 4)` change and message.
   - `expire_round`: same `(1, 2, 4)` → `(1, 2, 3, 4)` change and message. No new branch needed inside its body — round 3 (coding), like round 4, has no `content` field to set on expiry (state lives entirely in `Round3Turn`/`Round3ExecutionRun` rows), so it already falls through the existing `if round_number == 1: ... elif round_number == 2: ...` with no `elif` needed for 3 or 4.

   Leave `start_round`'s generic tuple `(1, 2, 4)` and `save_round_draft`'s `(1, 2)` **unchanged** — round 3's dedicated `/round/3/start` and `/round/3/draft` (registered above, earlier in the file) already intercept those literal paths before the generic routes ever see `round_number == 3`, the same routing-order trick already used for Round 2/4.

4. **Plan-amendment note (added after the renumbering plan's Task 1 review):** that plan's implementer discovered `_require_round_unlocked`'s round-gating math assumes contiguous round numbers, which broke round 4 once round 3 was vacated — fixed there with an explicit `ROUND_SEQUENCE = (1, 2, 4)` tuple-walk in `candidate.py` (added just above `_require_round_unlocked`), replacing the old `round_number > _max_completed_round(db, candidate) + 1` arithmetic. Now that round 3 exists again, update that tuple: `ROUND_SEQUENCE = (1, 2, 4)` → `ROUND_SEQUENCE = (1, 2, 3, 4)`. Do this as part of this step (not a separate one) — it's the same "make gating match which rounds actually exist" concern as the 3 tuples above, just in one shared constant instead of three separate literals.

- [ ] **Step 4: Run the tests, confirm they pass**

Run: `cd C:\Arun\Learning\Python\qa-eval-tool && python -m pytest tests/test_round3.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Run the full suite**

Run: `cd C:\Arun\Learning\Python\qa-eval-tool && python -m pytest tests/ -q`
Expected: same pre-existing `test_settings.py` failures as before (fixed in Task 10), nothing else broken.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/candidate.py tests/test_round3.py
git commit -m "Add Round 3 (AI-prompted coding) candidate endpoints"
```

---

### Task 9: Frontend

**Files:**
- Modify: `backend/app/static/app.js`, `backend/app/static/style.css`, `backend/app/templates/index.html`

**Interfaces:**
- Consumes: every Round 3 endpoint from Task 8, `AppSettingsOut.round3_passing_score` (Task 7).
- Produces: no new interfaces — this is the last, UI-facing task.

- [ ] **Step 1: Re-admit round 3 into shared nav/label constants in `backend/app/static/app.js`**

1. `const ROUND_LABELS = { 1: "Manual test cases", 2: "Debugging", 4: "Conversational" };` → `{ 1: "Manual test cases", 2: "Debugging", 3: "AI-prompted coding", 4: "Conversational" };`.
2. `renderHRRoundNav`: `${[1, 2, 4].map((n) => ...)}` → `${[1, 2, 3, 4].map((n) => ...)}`.
3. `renderCandidateRoundNav`: `${[1, 2, 4].map((n) => ...)}` → `${[1, 2, 3, 4].map((n) => ...)}`; `if (currentRound >= 1 && currentRound <= 4)` stays as-is (already correct from the renumbering plan).
4. `selectHRRound(n)`: round 3 now reuses the SAME generic create-scenario/scenarios-list/screening-history flow as round 1/2 (it authors through the standard draft→publish lifecycle, unlike round 4's dedicated settings-card view) — no special-casing needed here at all; `isRound4` continues to gate only round 4's dedicated settings panel, exactly as it does today. Confirm (don't need to change) that `isRound4 = n === 4` still correctly leaves round 3 falling into the generic `else` branch (`resetCreateScenarioForm(); loadLiveScenarioWidget(); loadScenarios(); loadHistory();`).

- [ ] **Step 2: Add a round-3-aware branch to `openScenarioDetail`'s reference preview**

Round 3 (coding)'s `reference_json` is `{test_cases: [...], expected_approach: "..."}` — a dict, not the `list[TestCaseRow]` shape rounds 1/2 use, so the existing `refRows`/`<thead>` table needs a branch. Change:
```js
  const showPriorityType = scenario.round_number === 1;
  const refRows = (scenario.reference_json || []).map((r, i) => `
    <tr>
      <td>${i + 1}</td>
      <td>${escapeHtml(r.title)}</td>
      <td>${escapeHtml(r.preconditions || "")}</td>
      <td>${escapeHtml(r.steps)}</td>
      <td>${escapeHtml(r.expected_result)}</td>
      ${showPriorityType ? `<td>${escapeHtml(r.priority)}</td><td>${escapeHtml(r.type)}</td>` : ""}
    </tr>
  `).join("");
```
to:
```js
  const showPriorityType = scenario.round_number === 1;
  const isCodingReference = scenario.round_number === 3;
  const refRows = isCodingReference
    ? ((scenario.reference_json && scenario.reference_json.test_cases) || []).map((tc, i) => `
        <tr>
          <td>${i + 1}</td>
          <td>${escapeHtml(tc.input)}</td>
          <td>${escapeHtml(tc.expected_output)}</td>
          <td>${escapeHtml(tc.description || "")}</td>
        </tr>
      `).join("")
    : (scenario.reference_json || []).map((r, i) => `
        <tr>
          <td>${i + 1}</td>
          <td>${escapeHtml(r.title)}</td>
          <td>${escapeHtml(r.preconditions || "")}</td>
          <td>${escapeHtml(r.steps)}</td>
          <td>${escapeHtml(r.expected_result)}</td>
          ${showPriorityType ? `<td>${escapeHtml(r.priority)}</td><td>${escapeHtml(r.type)}</td>` : ""}
        </tr>
      `).join("");
```
And its `<thead>` line:
```js
        <thead><tr><th>SI.No</th><th>Title</th><th>Preconditions</th><th>Steps</th><th>Expected result</th>${showPriorityType ? "<th>Priority</th><th>Type</th>" : ""}</tr></thead>
```
to:
```js
        <thead><tr>${isCodingReference
          ? "<th>SI.No</th><th>Input</th><th>Expected output</th><th>Description</th>"
          : `<th>SI.No</th><th>Title</th><th>Preconditions</th><th>Steps</th><th>Expected result</th>${showPriorityType ? "<th>Priority</th><th>Type</th>" : ""}`
        }</tr></thead>
```
And its colspan fallback:
```js
        <tbody>${refRows || `<tr><td colspan="${showPriorityType ? 7 : 5}" class="muted">No reference generated yet.</td></tr>`}</tbody>
```
to:
```js
        <tbody>${refRows || `<tr><td colspan="${isCodingReference ? 4 : showPriorityType ? 7 : 5}" class="muted">No reference generated yet.</td></tr>`}</tbody>
```
And add a note for `expected_approach` right after that `</div>` closing the `table-scroll`:
```js
    ${isCodingReference && scenario.reference_json ? `<p class="muted"><strong>Expected approach:</strong> ${escapeHtml(scenario.reference_json.expected_approach || "")}</p>` : ""}
```
Finally, fix the "Edit reference as JSON" textarea's default value so it doesn't fall back to `[]` for a round-3 scenario with no reference yet:
```js
        <textarea id="ref-json-edit">${escapeHtml(JSON.stringify(scenario.reference_json || [], null, 2))}</textarea>
```
to:
```js
        <textarea id="ref-json-edit">${escapeHtml(JSON.stringify(scenario.reference_json || (isCodingReference ? {test_cases: [], expected_approach: ""} : []), null, 2))}</textarea>
```
(Hand-editing this JSON already round-trips generically regardless of shape — no further change needed there. Out of scope for this pass: a purpose-built table editor for round 3's `{test_cases, expected_approach}` shape akin to round 1's row editor — Regenerate + raw-JSON edit is enough for a first pass; a dedicated editor UI can follow later if HR actually needs it.)

- [ ] **Step 3: Add the "Round 3 pass score" settings field**

In `backend/app/templates/index.html`, add a new field-inline right before the existing Round 4 one:
```html
                <div class="field-inline"><span class="muted">Round 3 pass score</span><input id="set-round3" type="number" min="0" max="100" /></div>
```
And bump the final-score ceiling:
```html
                <div class="field-inline"><span class="muted">Final pass score (out of 300)</span><input id="set-final" type="number" min="0" max="300" /></div>
```
to:
```html
                <div class="field-inline"><span class="muted">Final pass score (out of 400)</span><input id="set-final" type="number" min="0" max="400" /></div>
```

In `backend/app/static/app.js`, update `loadAppSettings`/`saveAppSettings`:
```js
async function loadAppSettings() {
  appSettings = await api("/hr/settings");
  document.getElementById("set-round1").value = appSettings.round1_passing_score;
  document.getElementById("set-round2").value = appSettings.round2_passing_score;
  document.getElementById("set-round3").value = appSettings.round3_passing_score;
  document.getElementById("set-round4").value = appSettings.round4_passing_score;
  document.getElementById("set-final").value = appSettings.final_passing_score;
  document.getElementById("set-window").value = appSettings.reapplication_window_months;
}

async function saveAppSettings() {
  const statusEl = document.getElementById("settings-status");
  const payload = {
    round1_passing_score: Number(document.getElementById("set-round1").value),
    round2_passing_score: Number(document.getElementById("set-round2").value),
    round3_passing_score: Number(document.getElementById("set-round3").value),
    round4_passing_score: Number(document.getElementById("set-round4").value),
    final_passing_score: Number(document.getElementById("set-final").value),
    reapplication_window_months: Number(document.getElementById("set-window").value),
  };
  // ...rest of the function is unchanged...
```

- [ ] **Step 4: Add the candidate-facing Round 3 coding view to `backend/app/static/app.js`**

1. In `renderRoundView`, add a branch for round 3 (before the existing round 4 branch, matching numeric order):
```js
  if (!submission) {
    if (n === 3) {
      box.innerHTML = `
        <h3>Round ${n}: ${escapeHtml(scenario.title)}</h3>
        ${formatScenarioDescription(scenario.description)}
        <p class="muted">Time limit: ${scenario.time_limit_minutes} minutes, starting once you confirm below.</p>
      `;
      showRound3CodingIntro();
      return;
    }
    if (n === 4) {
      /* ...existing round 4 branch, unchanged... */
    }
    ...
  }
```

2. In `renderRoundEntry`, add the round-3 dispatch:
```js
function renderRoundEntry(n, box, scenario, submission) {
  if (n === 1) {
    renderEntryForm(box, scenario, submission);
  } else if (n === 2) {
    renderInvestigationForm(box, scenario, submission);
  } else if (n === 3) {
    renderRound3CodingView(box);
  } else {
    renderRound4View(box);
  }
}
```

3. Add this new section (place it right after the Round 2 section, before the Round 4 section, matching numeric order):

```js
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
        <li>You never write code directly - you direct an assistant with plain-English instructions (variables, loops, data structures, what to read/print), and it writes the actual code.</li>
        <li>Your first message should describe enough for a first attempt - the assistant's first version will be a plain-language, brute-force sketch, not the polished final answer.</li>
        <li>The assistant won't decide anything for you - if you ask "which loop is right" or "what's the best approach", it will ask you to specify instead of answering.</li>
        <li>You can run your code at any point once it's real code, and see real output (or a real error) - reading and fixing what went wrong is on you: the assistant won't jump in on its own just because a run failed.</li>
        <li>What's scored: correctness, how precisely you specified things, and whether you pushed toward a more efficient solution - not just getting something that happens to work.</li>
        <li>Your timer starts the moment you click below.</li>
      </ul>
      <div class="field-row" style="align-items:center">
        <span class="muted">Language</span>
        <select id="round3-language-select">
          <option value="python">Python</option>
          <option value="java">Java</option>
          <option value="javascript">JavaScript</option>
        </select>
      </div>
      <div class="row">
        <button onclick="confirmStartRound3Coding()">Got it - Start Round 3</button>
      </div>
    </div>
  `;
  openModalOverlay(overlay);
}

function confirmStartRound3Coding() {
  const language = document.getElementById("round3-language-select").value;
  closeModalOverlay("round3-coding-intro-overlay");
  startRound3Coding(language);
}

async function startRound3Coding(language) {
  await api("/candidate/round/3/start", { method: "POST", body: JSON.stringify({ language }) });
  const state = await api("/candidate/round/3");
  const box = document.getElementById("round-view");
  renderRoundEntry(3, box, state.scenario, state.submission);
}

let round3CodingState = null;
let round3CodingDraftTimer = null;
const ROUND3_CODING_DRAFT_DEBOUNCE_MS = 1000;

async function renderRound3CodingView(box) {
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
      + round3CodingState.scenario.time_limit_minutes * 60 * 1000;
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

function renderRound3CodingLayout(box) {
  const s = round3CodingState;
  if (!s) return;
  const turnsHtml = s.turns.map((t) => `
    <div class="round3-coding-turn">
      <p class="round3-coding-prompt"><strong>You:</strong> ${escapeHtml(t.candidate_prompt)}</p>
      <p class="round3-coding-response round3-coding-response-${t.response_kind}"><strong>Assistant:</strong> ${escapeHtml(t.response_message)}</p>
    </div>
  `).join("");

  const latestCode = [...s.turns].reverse().find((t) => t.code_after)?.code_after || "";
  const runsHtml = s.runs.slice().reverse().map((r) => `
    <div class="round3-coding-run">
      <p class="muted">Run at ${new Date(r.created_at).toLocaleTimeString()} - ${r.timed_out ? "timed out" : r.infra_error ? "execution service error, try again" : `exit code ${r.exit_code}`}</p>
      ${r.stdout ? `<pre class="code-snippet">${escapeHtml(r.stdout)}</pre>` : ""}
      ${r.stderr ? `<pre class="code-snippet round3-coding-stderr">${escapeHtml(r.stderr)}</pre>` : ""}
    </div>
  `).join("");

  box.innerHTML = `
    <h3>Round 3: ${escapeHtml(s.scenario.title)}</h3>
    ${formatScenarioDescription(s.scenario.description)}
    <p class="muted">Language: ${escapeHtml(s.language || "")}</p>
    <div class="round3-coding-grid">
      <div class="panel-inset round3-coding-pane">
        <p class="muted round3-pane-label">Conversation</p>
        <div class="round3-pane-body" id="round3-coding-turns">${turnsHtml || '<p class="muted">Nothing yet - tell the assistant what you need.</p>'}</div>
        <textarea id="round3-coding-message" placeholder="What do you want the assistant to do next?" oninput="round3CodingOnComposerInput(this.value)">${escapeHtml((s.submission.content && s.submission.content.draft_prompt) || "")}</textarea>
        <div class="row">
          <button id="round3-coding-send-btn" onclick="round3CodingSendMessage()">Send</button>
        </div>
      </div>
      <div class="panel-inset round3-coding-pane">
        <p class="muted round3-pane-label">Code</p>
        <pre class="code-snippet" id="round3-coding-code">${escapeHtml(latestCode || "(no code yet)")}</pre>
        <div class="field-row">
          <input id="round3-coding-stdin" placeholder="Input values, one per line" />
        </div>
        <div class="row">
          <button id="round3-coding-run-btn" onclick="round3CodingRun()" ${latestCode ? "" : "disabled"}>Run</button>
          <button class="btn-block" onclick="round3CodingSubmit()">Submit Round 3</button>
        </div>
        <div id="round3-coding-runs">${runsHtml}</div>
      </div>
    </div>
    <p id="round3-coding-status" class="muted"></p>
  `;
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

async function round3CodingRun() {
  const stdinRaw = document.getElementById("round3-coding-stdin").value;
  const stdin = stdinRaw.split("\n").map((s) => s.trim()).filter((s) => s.length > 0);
  const statusEl = document.getElementById("round3-coding-status");
  const runBtn = document.getElementById("round3-coding-run-btn");
  runBtn.disabled = true;
  statusEl.className = "muted";
  statusEl.textContent = "Running...";
  try {
    await api("/candidate/round/3/run", { method: "POST", body: JSON.stringify({ stdin }) });
    round3CodingState = await api("/candidate/round/3/state");
    renderRound3CodingLayout(document.getElementById("round-view"));
    statusEl.textContent = "";
  } catch (e) {
    statusEl.className = "error-text";
    statusEl.textContent = e.message;
  } finally {
    runBtn.disabled = false;
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
```

- [ ] **Step 5: Add CSS for the Round 3 coding layout to `backend/app/static/style.css`**

Add near the existing `.round4-turn-grid`/`.round4-pane` rules (reusing the same visual language, but a simpler 2-column layout — no draggable resizer, since there's no third "Code" column distinct from the conversation the way Round 4's trial code-viewer needed one):

```css
.round3-coding-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
  align-items: start;
}
.round3-coding-pane { display: flex; flex-direction: column; min-width: 0; }
.round3-coding-turn { margin-bottom: 0.75rem; }
.round3-coding-prompt { margin: 0 0 0.25rem; }
.round3-coding-response { margin: 0; }
.round3-coding-response-refuse { color: var(--accent); }
.round3-coding-stderr { color: var(--accent); }
.round3-coding-run { margin-bottom: 0.75rem; }
#round3-coding-message { min-height: 4rem; }
#round3-coding-code { min-height: 8rem; white-space: pre-wrap; }

@media (max-width: 900px) {
  .round3-coding-grid { grid-template-columns: 1fr; }
}
```
(If `--accent` isn't already a defined custom property in this file, use whichever token this stylesheet already uses for its one warning/error accent color instead — check the file for how `.round3-coding-stderr`'s sibling classes like `.error-text` are colored and match that token rather than inventing a new one.)

- [ ] **Step 6: Manual smoke test**

1. Run: `cd backend && uvicorn app.main:app --reload`
2. Log in as HR, open Round 3 in the nav — confirm it now looks like Round 1/2's authoring flow (Create a scenario / Scenarios list / Screening history), not Round 4's settings card. Create a scenario (e.g. "Add two numbers"), confirm the reference preview shows an Input/Expected output/Description table plus an "Expected approach" note, and Publish works.
3. Log in as a candidate who's completed rounds 1 and 2, open Round 3 — confirm the intro modal shows with a language picker, starting it shows the conversation/code two-pane layout, sending a message gets a real assistant response, "Run" is disabled until real code exists then works and shows output, and Submit works.
4. Confirm the Settings page now shows a "Round 3 pass score" field alongside Round 4's, and "Final pass score (out of 400)".

- [ ] **Step 7: Commit**

```bash
git add backend/app/static/app.js backend/app/static/style.css backend/app/templates/index.html
git commit -m "Add Round 3 (AI-prompted coding) frontend"
```

---

### Task 10: Settings test updates + final full-suite verification

**Files:**
- Modify: `tests/test_settings.py`

**Interfaces:**
- Consumes: `AppSettingsOut`/`AppSettingsUpdate.round3_passing_score` (Task 2/7).
- Produces: nothing new — this task closes out the `test_settings.py` failures every prior task's Step 6 has been tracking, and is the final green-suite checkpoint for the whole plan.

- [ ] **Step 1: Update every `AppSettingsUpdate` payload and assertion in `tests/test_settings.py`**

Add `"round3_passing_score": 70` (or whatever value that specific test already uses for the other per-round fields, to stay consistent with each test's own numbers) to every JSON payload/assertion the earlier `grep -n "round3" tests/test_settings.py` pass in this plan's Task discovery found — every occurrence of `"round3_passing_score"` in that file already reads correctly (this file's `round3_passing_score` mentions were never touched by the renumbering plan, since that plan renamed them to `round4_passing_score` — wait, re-check: after the renumbering plan, this file's fields are `round4_passing_score`/`round4_default_assistance_pct`, and this task ADDS BACK a fresh `round3_passing_score` field to every payload/assertion, it does not rename anything). Concretely, for each place a payload/assertion dict currently reads like:
```python
"round1_passing_score": 70, "round2_passing_score": 70, "round4_passing_score": 70,
```
add `"round3_passing_score": 70,` into the same dict (placed before `round4_passing_score`, matching numeric order):
```python
"round1_passing_score": 70, "round2_passing_score": 70, "round3_passing_score": 70, "round4_passing_score": 70,
```
Apply this to every payload sent to `PUT /hr/settings` and every response assertion in this file that checks the full settings shape.

- [ ] **Step 2: Run `test_settings.py`, confirm it passes**

Run: `cd C:\Arun\Learning\Python\qa-eval-tool && python -m pytest tests/test_settings.py -v`
Expected: all tests PASS.

- [ ] **Step 3: Run the entire test suite one final time**

Run: `cd C:\Arun\Learning\Python\qa-eval-tool && python -m pytest tests/ -q`
Expected: all tests pass, zero failures.

- [ ] **Step 4: Commit**

```bash
git add tests/test_settings.py
git commit -m "Update settings tests for Round 3 (AI-prompted coding)'s passing score"
```
