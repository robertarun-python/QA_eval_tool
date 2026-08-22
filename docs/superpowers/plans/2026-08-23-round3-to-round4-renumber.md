# Round 3 → Round 4 Renumbering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the existing "prompt-driven test automation" round from round_number 3 to round_number 4 everywhere in the codebase (models, schemas, services, routers, prompts, frontend, tests), freeing up round_number 3 for the new AI-prompted-coding round built in the next plan.

**Architecture:** This is a pure mechanical rename plus one data migration — no behavior changes. Every `round3`/`Round3`/`ROUND3` identifier and every `/round/3/...` path becomes `round4`/`Round4`/`ROUND4` / `/round/4/...`. A new `migrate_round_renumber.py` updates any existing local `qa_eval.db` rows from `round_number=3` to `round_number=4` and renames the `round3_test_cases` table and `app_settings.round3_passing_score` column in place.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, SQLite, pytest, vanilla JS.

**Spec:** `docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md` (see its "Renumbering" section)

## Global Constraints

- Every rename must be exhaustive: after this plan, `grep -rn "round3\|Round3\|ROUND3" backend/ tests/ --include=*.py --include=*.js --include=*.css --include=*.html` must return **zero** matches, with the sole exception of the pre-existing historical migration scripts `backend/app/migrate_round3_code_cache.py` and `backend/app/migrate_round3_freeform.py` (left untouched — they're a historical record of migrations that ran when this round really was numbered 3, see "What's explicitly NOT touched" below).
- Round_number literal `3` must not appear anywhere as "the automation round" after this plan — round 3 is fully vacated. Do not pre-emptively add round 3 back for the new coding round; that's the next plan's job.
- No behavior change: the automation round's actual logic (LLM behavior, scoring, gating) must be byte-for-byte the same, just addressed as round 4.
- This is a local dev SQLite DB with no real candidate data — the migration script does a direct in-place `UPDATE`/`ALTER TABLE`, no need for anything fancier (see spec's "Renumbering" section).

**⚠️ Do not deploy this plan on its own to any environment with candidates actively progressing through rounds, and don't run the migration against a DB with real in-progress candidates until the follow-up plan (`2026-08-23-round3-ai-coding-design` implementation, the new AAI-prompted-coding round) is ready to ship alongside it.** Round-gating (`routers/candidate.py`'s `_require_round_unlocked`, see `ARCHITECTURE.md`) is a strict `max_completed_round + 1 >= N` check with no concept of "round 3 is intentionally vacant" — if this plan ships alone, round 3 has no scenario a candidate can ever complete, which means **round 4 becomes permanently unreachable** for anyone who has only finished rounds 1–2 (`4 > 2 + 1` fails the unlock check forever). This is fine for local iteration (this is a personal dev POC with only seeded test accounts, not live candidates today), but treat these two plans as one deployment unit, not two independently shippable increments.

---

## What's explicitly NOT touched

- `backend/app/migrate_round3_code_cache.py`, `backend/app/migrate_round3_freeform.py` — historical one-time migration scripts. They already ran; their filenames correctly describe what was true when they ran (the round genuinely was 3 at that time). Renaming them would serve no purpose and makes git history harder to follow.
- `backend/app/migrate_indexes.py`, `backend/app/migrate_bulk_candidates.py` — these reference `round3_test_cases` only in passing comments about history; leave their comments as-is for the same reason.
- `ARCHITECTURE.md` — describes the pre-this-plan design. Update it in a small follow-up once both plans are done, not as part of either plan (avoids describing a half-finished state).
- `docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md` — the spec itself, obviously left alone.

## File Structure

No new files except:
- `backend/app/migrate_round_renumber.py` (new migration script)

Renamed:
- `tests/test_round3.py` → `tests/test_round4.py`
- `backend/app/prompts/round3_environment_generation.txt` → `round4_environment_generation.txt`
- `backend/app/prompts/round3_ui_mockup_generation.txt` → `round4_ui_mockup_generation.txt`
- `backend/app/prompts/round3_partial_response.txt` → `round4_partial_response.txt`
- `backend/app/prompts/round3_code_snippet.txt` → `round4_code_snippet.txt`
- `backend/app/prompts/round3_scoring.txt` → `round4_scoring.txt`

Modified (identifier renames only, see Task 1/2 for exact changes):
- `backend/app/models.py`, `backend/app/schemas.py`, `backend/app/config.py`
- `backend/app/services/llm_service.py`, `backend/app/services/scoring_service.py`
- `backend/app/routers/candidate.py`, `backend/app/routers/hr.py`
- `backend/app/static/app.js`, `backend/app/static/style.css`, `backend/app/templates/index.html`
- `tests/conftest.py`, `tests/test_round4.py` (renamed from test_round3.py), `tests/test_round_draft_autosave.py`, `tests/test_scoring_prompt_injection_guardrail.py`, `tests/test_settings.py`, `tests/test_lazy_expiry.py`, `tests/test_round2.py`

---

### Task 1: Backend rename (models, schemas, services, routers, prompts, migration, tests)

**Files:**
- Modify: `backend/app/models.py`, `backend/app/schemas.py`, `backend/app/config.py`, `backend/app/services/llm_service.py`, `backend/app/services/scoring_service.py`, `backend/app/routers/candidate.py`, `backend/app/routers/hr.py`
- Rename: 5 prompt files (listed above), `tests/test_round3.py` → `tests/test_round4.py`
- Create: `backend/app/migrate_round_renumber.py`
- Modify: `tests/conftest.py`, `tests/test_round4.py`, `tests/test_round_draft_autosave.py`, `tests/test_scoring_prompt_injection_guardrail.py`, `tests/test_settings.py`, `tests/test_lazy_expiry.py`, `tests/test_round2.py`

**Interfaces:**
- Produces: `Round4TestCase` model (was `Round3TestCase`), `Submission.round4_test_cases` relationship (was `round3_test_cases`), `AppSettings.round4_passing_score` column (was `round3_passing_score`), `Settings.round4_default_assistance_pct` (was `round3_default_assistance_pct`), `llm_service.DEFAULT_ROUND4_CONFIG`, `llm_service.generate_round4_environment`, `llm_service.generate_round4_ui_mockup`, `llm_service.round4_respond`, `llm_service.generate_round4_code_snippet`, `llm_service.score_round4_conversation`, `scoring_service.score_round4_submission`, all `Round4*` schemas (was `Round3*`), candidate router endpoints under `/candidate/round/4/...`, HR router endpoints `/hr/scenarios/{id}/round4-config` and `/round4-instructions` (were `round3-config`/`round3-instructions`). These exact names are what Task 2 (frontend) and the next plan (new round 3) will reference.

- [ ] **Step 1: Confirm baseline is green before changing anything**

Run: `cd C:\Arun\Learning\Python\qa-eval-tool && python -m pytest tests/ -q`
Expected: all tests currently pass (this is your rollback point).

- [ ] **Step 2: Rename identifiers in `backend/app/models.py`**

Apply these exact changes:
1. Class `Round3TestCase` → `Round4TestCase`. Its docstring's `"""Round 3 only: ..."""` → `"""Round 4 only: ...”` (keep the rest of the docstring text, just the round number).
2. `__tablename__ = "round3_test_cases"` → `__tablename__ = "round4_test_cases"`.
3. Inside `Round4TestCase` (renamed): `submission = relationship("Submission", back_populates="round3_test_cases")` → `back_populates="round4_test_cases"`.
4. In `ConversationTurn`: `test_case_id = Column(Integer, ForeignKey("round3_test_cases.id"), nullable=False, index=True)` → `ForeignKey("round4_test_cases.id")`. Its class docstring's `"""Round 3 only: ..."""` → `"""Round 4 only: ...”`.
5. In `Submission`: the relationship currently named
   ```python
   round3_test_cases = relationship(
       "Round3TestCase", back_populates="submission",
       order_by="Round3TestCase.created_at",
   )
   ```
   becomes
   ```python
   round4_test_cases = relationship(
       "Round4TestCase", back_populates="submission",
       order_by="Round4TestCase.created_at",
   )
   ```
   (and its preceding comment's "Round 3 only" → "Round 4 only").
6. In `AppSettings`: `round3_passing_score = Column(Integer, nullable=False, default=70)` → `round4_passing_score = Column(Integer, nullable=False, default=70)`.

- [ ] **Step 3: Rename identifiers in `backend/app/config.py`**

Change:
```python
    round3_default_assistance_pct: int = 60
```
to:
```python
    round4_default_assistance_pct: int = 60
```
and update the preceding comment block's "Round 3's assistant" → "Round 4's assistant", "Scenario.config_json" reference unchanged.

- [ ] **Step 4: Rename the 5 prompt files**

```bash
cd backend/app/prompts
git mv round3_environment_generation.txt round4_environment_generation.txt
git mv round3_ui_mockup_generation.txt round4_ui_mockup_generation.txt
git mv round3_partial_response.txt round4_partial_response.txt
git mv round3_code_snippet.txt round4_code_snippet.txt
git mv round3_scoring.txt round4_scoring.txt
```
No content changes needed inside these files — their wording already just says "Round 3" as a candidate-facing/HR-facing label in a couple of comments; leave prose content alone for this task (a pure filename rename), Task 2 will not need to touch prompt file contents either since none of the 5 files' bodies are grep-matched for `round3` as an identifier (they use natural language "Round 3" only in the human-readable framing already verified in the earlier read of `round3_scoring.txt` — if you find any, replace "Round 3" with "Round 4" there too).

- [ ] **Step 5: Rename identifiers in `backend/app/services/llm_service.py`**

1. Import line: `from ..schemas import Round3TurnResponse, Round3EnvironmentOut, Round3UiMockupOut` → `from ..schemas import Round4TurnResponse, Round4EnvironmentOut, Round4UiMockupOut`.
2. `DEFAULT_ROUND3_CONFIG = {"assistance_pct": settings.round3_default_assistance_pct}` → `DEFAULT_ROUND4_CONFIG = {"assistance_pct": settings.round4_default_assistance_pct}`.
3. `def generate_round3_environment(...)` → `def generate_round4_environment(...)`; inside it, `_load_prompt("round3_environment_generation.txt")` → `_load_prompt("round4_environment_generation.txt")`; `Round3EnvironmentOut.model_validate(result).model_dump()` → `Round4EnvironmentOut...`.
4. `def generate_round3_ui_mockup(...)` → `def generate_round4_ui_mockup(...)`; `_load_prompt("round3_ui_mockup_generation.txt")` → `round4_ui_mockup_generation.txt`; `Round3UiMockupOut` → `Round4UiMockupOut`.
5. `def round3_respond(...)` → `def round4_respond(...)`; `_load_prompt("round3_partial_response.txt")` → `round4_partial_response.txt`; `Round3TurnResponse.model_validate(result).model_dump()` → `Round4TurnResponse...`.
6. `def generate_round3_code_snippet(...)` → `def generate_round4_code_snippet(...)`; `_load_prompt("round3_code_snippet.txt")` → `round4_code_snippet.txt`.
7. `def score_round3_conversation(...)` → `def score_round4_conversation(...)`; `_load_prompt("round3_scoring.txt")` → `round4_scoring.txt`; `_scoring_provenance("round3_scoring.txt", prompt_text)` → `_scoring_provenance("round4_scoring.txt", prompt_text)`.
8. Update every comment in the "---- Round 3 ----" section header and its long explanatory comment block: change "Round 3"/"round 3" prose mentions to "Round 4"/"round 4" (this is the block starting `# ---- Round 3 ----` through the `DEFAULT_ROUND3_CONFIG` definition, and the "Trial feature" comment above `generate_round3_code_snippet`).

- [ ] **Step 6: Rename identifiers in `backend/app/schemas.py`**

Rename every one of these classes (keep fields/docstrings identical except round-number mentions in prose):
`Round3ConfigUpdate`→`Round4ConfigUpdate`, `Round3InstructionsUpdate`→`Round4InstructionsUpdate`, `Round3EnvironmentOut`→`Round4EnvironmentOut`, `Round3MockupElement`→`Round4MockupElement`, `Round3MockupScreen`→`Round4MockupScreen`, `Round3UiMockupOut`→`Round4UiMockupOut`, `Round3ExecutionStep`→`Round4ExecutionStep`, `Round3TurnResponse`→`Round4TurnResponse`, `Round3TestCaseCreate`→`Round4TestCaseCreate`, `Round3TestCaseOut`→`Round4TestCaseOut`, `Round3DraftUpdate`→`Round4DraftUpdate`, `Round3TurnCreate`→`Round4TurnCreate`, `Round3TurnOut`→`Round4TurnOut`, `Round3CodeSnippetOut`→`Round4CodeSnippetOut`, `Round3StateOut`→`Round4StateOut`.

Also:
- `AppSettingsOut.round3_passing_score: int` → `round4_passing_score: int`; `AppSettingsOut.round3_default_assistance_pct: int` → `round4_default_assistance_pct: int` (and its comment's "round3 settings card" → "round4 settings card").
- `AppSettingsUpdate.round3_passing_score: int = Field(ge=0, le=100)` → `round4_passing_score: int = Field(ge=0, le=100)`.
- `SubmissionReportOut.test_cases: Optional[list["Round3TestCaseOut"]]` → `Optional[list["Round4TestCaseOut"]]`.
- `SubmissionReportOut.conversation_turns: Optional[list["Round3TurnOut"]]` → `Optional[list["Round4TurnOut"]]`.
- The section comment `# ---- Round 3 (conversational automation: ...` → `# ---- Round 4 (conversational automation: ...`.
- `Round1ContextOut` is unrenamed (it's genuinely about round 1's content, just consumed by round 4 now) — leave it exactly as-is.

- [ ] **Step 7: Rename identifiers in `backend/app/services/scoring_service.py`**

1. `def score_round3_submission(db, submission)` → `def score_round4_submission(db, submission)`; docstring's "Round 3" → "Round 4".
2. Inside it: `config = {**llm_service.DEFAULT_ROUND3_CONFIG, **(scenario.config_json or {})}` → `DEFAULT_ROUND4_CONFIG`.
3. `submission.round3_test_cases` → `submission.round4_test_cases`.
4. `llm_service.score_round3_conversation(...)` → `llm_service.score_round4_conversation(...)`.
5. `_SCORERS = {1: score_round1_submission, 2: score_round2_investigation, 3: score_round3_submission}` → `{1: score_round1_submission, 2: score_round2_investigation, 4: score_round4_submission}`.
6. In `close_expired_submissions`'s comment: `# Round 3 has no content field to set` → `# Round 4 has no content field to set`.

- [ ] **Step 8: Rename identifiers/routes in `backend/app/routers/hr.py`**

1. `ROUND_LABELS = {1: "Manual test cases", 2: "Debugging", 3: "Conversational"}` → `{1: "Manual test cases", 2: "Debugging", 4: "Conversational"}`.
2. Import line: `Round3TestCaseOut, ... Round3ConfigUpdate, Round3InstructionsUpdate,` → `Round4TestCaseOut, ... Round4ConfigUpdate, Round4InstructionsUpdate,` (match against the actual import list in the file).
3. `create_scenario`: `if payload.round_number not in (1, 2, 3): raise HTTPException(400, "round_number must be 1, 2, or 3")` → `not in (1, 2, 4)` / `"round_number must be 1, 2, or 4"`.
4. `_generate_reference_unsafe`: the trailing `else:` branch (currently handling round 3) becomes `elif scenario.round_number == 4:` — do not leave a bare `else` here, since round_number 3 must not silently fall into this branch once it's freed up by the next plan. Inside that branch: `llm_service.generate_round3_environment` → `generate_round4_environment`, `generate_round3_ui_mockup` → `generate_round4_ui_mockup`. Update the branch's long comment: "Round 3 has no scenario-level test-case reference" → "Round 4 has no scenario-level test-case reference", and its `live_round3` variable/query below stays local to this function so just rename it for clarity: `live_round1` unchanged, the filter `Scenario.round_number == 1` unchanged.
5. Rename function `_resync_round3_reference_for_band` → `_resync_round4_reference_for_band`; inside it, `live_round3 = db.query(Scenario).filter(Scenario.round_number == 3, ...)` → `Scenario.round_number == 4`; its docstring's "round3"/"Round 3" mentions → "round4"/"Round 4". Update its 2 call sites (in `publish_scenario` and `move_to_screening`) to the new name.
6. `regenerate_reference`: `if scenario is not None and scenario.round_number == 3:` → `== 4`; docstring "Round 3" mentions → "Round 4".
7. Rename `_get_round3_scenario_or_404` → `_get_round4_scenario_or_404`; inside it `if scenario.round_number != 3:` → `!= 4`; error message "This setting only applies to round 3 scenarios." → "round 4 scenarios.".
8. Rename `_require_round3_not_in_progress` → `_require_round4_not_in_progress` (update its 2 call sites too, in the two endpoints below).
9. Rename endpoint function `update_round3_config` → `update_round4_config`; its route decorator `@router.patch("/scenarios/{scenario_id}/round3-config", ...)` → `"/scenarios/{scenario_id}/round4-config"`; body uses `payload: Round3ConfigUpdate` → `Round4ConfigUpdate`, calls `_get_round3_scenario_or_404` → `_get_round4_scenario_or_404`, `_require_round3_not_in_progress` → `_require_round4_not_in_progress`.
10. Rename endpoint function `update_round3_instructions` → `update_round4_instructions`; route `"/scenarios/{scenario_id}/round3-instructions"` → `"/round4-instructions"`; body uses `Round3InstructionsUpdate` → `Round4InstructionsUpdate` and the same two renamed helpers as above.
11. `publish_scenario`: `if scenario.round_number == 3:` → `== 4`; the two error messages "Can't publish a round 3 scenario..." → "Can't publish a round 4 scenario...".
12. `_build_submission_reports`: `if s.round_number == 3:` → `== 4`; `Round3TestCaseOut(...)` → `Round4TestCaseOut(...)`; `s.round3_test_cases` → `s.round4_test_cases`.
13. `_gather_candidate_rounds`: `for round_number in (1, 2, 3):` → `(1, 2, 4)`.
14. `_build_candidate_summary`: `for round_number in (1, 2, 3):` → `(1, 2, 4)`.
15. Module docstring at the top of the file: update its "Round 3 is conversational..." paragraph's round number references to "Round 4".

- [ ] **Step 9: Rename identifiers/routes in `backend/app/routers/candidate.py`**

1. `ROUND3_CODE_LANGUAGES = (...)` → `ROUND4_CODE_LANGUAGES = (...)`.
2. `_round3_code_locks` / `_round3_code_locks_guard` / `_round3_code_lock` → `_round4_code_locks` / `_round4_code_locks_guard` / `_round4_code_lock` (all 3 references, including inside the function body).
3. Import line: `from ..models import ..., Round3TestCase, ...` → `Round4TestCase`; `from ..schemas import (..., Round3StateOut, Round3TurnCreate, Round3TurnOut, Round3TestCaseCreate, Round3TestCaseOut, Round3DraftUpdate, Round3EnvironmentOut, Round3UiMockupOut, ..., Round3CodeSnippetOut, ...)` → all `Round3*` → `Round4*`.
4. `ROUND3_CONFIG_DEFAULTS = llm_service.DEFAULT_ROUND3_CONFIG` → `ROUND4_CONFIG_DEFAULTS = llm_service.DEFAULT_ROUND4_CONFIG`.
5. Section comment block `# ---- Round 3 (conversational, ...` → `# ---- Round 4 (conversational, ...`; update the routing-order explanatory comment's `/round/3/submit` mentions → `/round/4/submit`.
6. `def _round3_config(scenario)` → `def _round4_config(scenario)`.
7. `def _round3_scenario_and_submission(candidate, db)` → `def _round4_scenario_and_submission(candidate, db)`; inside it `_live_scenario(db, 3, candidate)` → `_live_scenario(db, 4, candidate)`; error message "No published scenario for round 3 yet" → "round 4 yet"; "Round 3 hasn't been started yet - call /round/3/start first." → "Round 4 hasn't been started yet - call /round/4/start first.".
8. `def _test_case_out(tc: Round3TestCase) -> Round3TestCaseOut` → `def _test_case_out(tc: Round4TestCase) -> Round4TestCaseOut`.
9. `def _build_round3_state(...) -> Round3StateOut` → `def _build_round4_state(...) -> Round4StateOut`; inside: `Round3EnvironmentOut(**scenario.environment_json)` → `Round4EnvironmentOut(...)`; `Round3UiMockupOut(**scenario.ui_mockup_json)` → `Round4UiMockupOut(...)`; `Round3StateOut(...)` → `Round4StateOut(...)`; `submission.round3_test_cases` → `submission.round4_test_cases`.
10. `def _owned_test_case(test_case_id, submission, db) -> Round3TestCase` → `-> Round4TestCase`; `db.get(Round3TestCase, test_case_id)` → `db.get(Round4TestCase, test_case_id)`.
11. `@router.get("/round/3/state", response_model=Round3StateOut)` `def round3_state(...)` → `@router.get("/round/4/state", response_model=Round4StateOut)` `def round4_state(...)`; body calls `_require_round_unlocked(3, ...)` → `_require_round_unlocked(4, ...)`, `_round3_scenario_and_submission` → `_round4_scenario_and_submission`, `_build_round3_state` → `_build_round4_state`.
12. `@router.post("/round/3/test-case", ...)` `def round3_create_test_case(payload: Round3TestCaseCreate, ...)` → `@router.post("/round/4/test-case", ...)` `def round4_create_test_case(payload: Round4TestCaseCreate, ...)`; body: `_require_round_unlocked(3, ...)` → `(4, ...)`, `_round3_scenario_and_submission` → `_round4_scenario_and_submission`, `Round3TestCase(submission_id=..., title=...)` → `Round4TestCase(...)`.
13. `@router.patch("/round/3/test-case/{test_case_id}/draft", ...)` `def round3_save_draft(test_case_id, payload: Round3DraftUpdate, ...)` → `@router.patch("/round/4/test-case/{test_case_id}/draft", ...)` `def round4_save_draft(test_case_id, payload: Round4DraftUpdate, ...)`; body renames as above.
14. `@router.post("/round/3/turn", response_model=Round3TurnOut, ...)` `def round3_turn(payload: Round3TurnCreate, ...)` → `@router.post("/round/4/turn", response_model=Round4TurnOut, ...)` `def round4_turn(payload: Round4TurnCreate, ...)`; body: `_require_round_unlocked(3, ...)` → `(4, ...)`, `_round3_scenario_and_submission`→renamed, `config = _round3_config(scenario)` → `_round4_config(scenario)`, `llm_service.round3_respond(...)` → `llm_service.round4_respond(...)`, `ConversationTurn(submission_id=..., test_case_id=..., ...)` unchanged (class name didn't change).
15. `@router.get("/round/3/turn/{turn_id}/code", response_model=Round3CodeSnippetOut)` `def round3_turn_code(turn_id, language, ...)` → `@router.get("/round/4/turn/{turn_id}/code", response_model=Round4CodeSnippetOut)` `def round4_turn_code(...)`; body: `_require_round_unlocked(3, ...)` → `(4, ...)`, `ROUND3_CODE_LANGUAGES` → `ROUND4_CODE_LANGUAGES`, `_round3_scenario_and_submission` → renamed, `_round3_code_lock(turn_id)` → `_round4_code_lock(turn_id)`, `llm_service.generate_round3_code_snippet(...)` → `generate_round4_code_snippet(...)`, `Round3CodeSnippetOut(...)` → `Round4CodeSnippetOut(...)`.
16. `@router.post("/round/3/submit", ...)` `def round3_submit(...)` → `@router.post("/round/4/submit", ...)` `def round4_submit(...)`; body: `_require_round_unlocked(3, ...)` → `(4, ...)`, `_round3_scenario_and_submission` → renamed, `submission.round3_test_cases` → `submission.round4_test_cases`.
17. `get_round`: `if round_number not in (1, 2, 3):` → `(1, 2, 4)`; error message `"round_number must be 1, 2, or 3"` → `"round_number must be 1, 2, or 4"`.
18. `start_round`: same `(1, 2, 3)` → `(1, 2, 4)` change and message.
19. `log_tab_switch`: same `(1, 2, 3)` → `(1, 2, 4)` change and message.
20. `save_round_draft`: leave `if round_number not in (1, 2):` exactly as-is (round 4 still autosaves per-test-case, not through this endpoint) — only update its error message text "round 3 autosaves per test case" → "round 4 autosaves per test case".
21. `expire_round`: `if round_number not in (1, 2, 3):` → `(1, 2, 4)`; error message → `"round_number must be 1, 2, or 4"`; its trailing comment "Round 3 has no content field to set here" → "Round 4 has no content field to set here".

- [ ] **Step 10: Write the migration script `backend/app/migrate_round_renumber.py`**

```python
"""
Migration for the Round 3 -> Round 4 renumbering (see
docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md's
"Renumbering" section): the existing prompt-driven test automation round
moves from round_number 3 to round_number 4, freeing up round_number 3
for the new AI-prompted-coding round. This is a dev-seeded local SQLite
DB (no real candidate data at stake), so a straight in-place UPDATE/ALTER
is sufficient.

Idempotent - checks state first, so it's safe to run more than once.

Run from backend/: python -m app.migrate_round_renumber
"""
import sqlite3

from .database import sqlite_db_path


def _table_exists(cur: sqlite3.Cursor, table: str) -> bool:
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

        cur.execute("UPDATE scenarios SET round_number = 4 WHERE round_number = 3")
        if cur.rowcount > 0:
            applied.append(f"scenarios: {cur.rowcount} row(s) round_number 3 -> 4")

        cur.execute("UPDATE submissions SET round_number = 4 WHERE round_number = 3")
        if cur.rowcount > 0:
            applied.append(f"submissions: {cur.rowcount} row(s) round_number 3 -> 4")

        if _table_exists(cur, "round3_test_cases") and not _table_exists(cur, "round4_test_cases"):
            cur.execute("ALTER TABLE round3_test_cases RENAME TO round4_test_cases")
            applied.append("round3_test_cases -> round4_test_cases (table renamed)")

        if _has_column(cur, "app_settings", "round3_passing_score") and not _has_column(cur, "app_settings", "round4_passing_score"):
            cur.execute("ALTER TABLE app_settings RENAME COLUMN round3_passing_score TO round4_passing_score")
            applied.append("app_settings.round3_passing_score -> round4_passing_score (column renamed)")

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

- [ ] **Step 11: Run the migration against your local dev DB**

Run: `cd backend && python -m app.migrate_round_renumber`
Expected: prints what it applied (or "Nothing to do" on a fresh DB that never had a `round3_test_cases` table).

- [ ] **Step 12: Rename `tests/conftest.py` helpers**

1. `def _publish_round3_scenario(client, hr_token, monkeypatch, band="0-7", title="Automation challenge")` → `def _publish_round4_scenario(...)` (same signature); inside it, `monkeypatch.setattr(llm_service, "generate_round3_environment", ...)` → `"generate_round4_environment"`, `monkeypatch.setattr(llm_service, "generate_round3_ui_mockup", ...)` → `"generate_round4_ui_mockup"`; the POST body `json={"round_number": 3, ...}` → `{"round_number": 4, ...}`. Its docstring's "Round 3" mentions → "Round 4".
2. `def _create_round3_test_case(client, token, title=None)` → `def _create_round4_test_case(client, token, title=None)`; inside it, `client.post("/candidate/round/3/test-case", ...)` → `"/candidate/round/4/test-case"`.

- [ ] **Step 13: Rename `tests/test_round3.py` to `tests/test_round4.py` and update its contents**

```bash
cd tests
git mv test_round3.py test_round4.py
```
Then, within `test_round4.py`, apply these replacements throughout the file:
- Every `/candidate/round/3/` path segment → `/candidate/round/4/`.
- Every `/hr/scenarios/{...}/round3-config` and `/round3-instructions` path → `/round4-config` / `/round4-instructions`.
- `_publish_round3_scenario` → `_publish_round4_scenario`; `_create_round3_test_case` → `_create_round4_test_case` (matching the conftest.py renames from Step 12).
- Any `"round_number": 3` / `.round_number == 3` / `round_number=3` assertion or payload → `4`.
- Any `Round3*` schema/model name appearing in the test (e.g. via imports) → `Round4*`.
- Any `llm_service.score_round3_conversation` / `generate_round3_environment` / `generate_round3_ui_mockup` / `round3_respond` / `generate_round3_code_snippet` monkeypatch target → the renamed `round4` equivalents.
- Docstrings/comments mentioning "Round 3" describing this round → "Round 4".
- Test function names containing `round3` → `round4` (e.g. `test_round3_...` → `test_round4_...`).

- [ ] **Step 14: Update `tests/test_round_draft_autosave.py` (comment only)**

Change the docstring line:
```
already have via PATCH /round/3/test-case/{id}/draft (see test_round3.py),
```
to:
```
already have via PATCH /round/4/test-case/{id}/draft (see test_round4.py),
```
No functional code in this file references round 3.

- [ ] **Step 15: Update `tests/test_scoring_prompt_injection_guardrail.py`**

1. Comment mentions of `round3_scoring.txt` → `round4_scoring.txt`; `round3_partial_response.txt` → `round4_partial_response.txt`.
2. `def test_round3_scoring_prompt_has_an_injection_guardrail(monkeypatch):` → `def test_round4_scoring_prompt_has_an_injection_guardrail(monkeypatch):`.
3. `llm_service.score_round3_conversation(...)` call inside it → `llm_service.score_round4_conversation(...)`.

- [ ] **Step 16: Update `tests/test_settings.py`**

Replace every occurrence of the JSON key `"round3_passing_score"` → `"round4_passing_score"` and `"round3_default_assistance_pct"` → `"round4_default_assistance_pct"` (in both request payloads and response assertions, across all the test functions in this file — Step 16 covers every line the earlier grep found: 19, 22, 26-27, 29, 34, 40, 48, 54, 57, 63, 79, 92). Rename `def test_settings_exposes_the_round3_default_assistance_pct_read_only(client):` → `def test_settings_exposes_the_round4_default_assistance_pct_read_only(client):` and its docstring's "round3" mentions → "round4".

- [ ] **Step 17: Update `tests/test_lazy_expiry.py`**

1. Import line: `_publish_round3_scenario` → `_publish_round4_scenario`.
2. Comment: `submit_round/submit_round2/round3_submit/` → `submit_round/submit_round2/round4_submit/`.
3. `def test_expired_round3_in_progress_submission_no_longer_blocks_round3_config_edit(client, monkeypatch):` → `def test_expired_round4_in_progress_submission_no_longer_blocks_round4_config_edit(client, monkeypatch):`; its docstring's `_require_round3_not_in_progress` → `_require_round4_not_in_progress`.
4. `published = _publish_round3_scenario(client, hr_token, monkeypatch)` → `_publish_round4_scenario(...)`.
5. `monkeypatch.setattr(llm_service, "score_round3_conversation", ...)` → `"score_round4_conversation"`.
6. Both `f"/hr/scenarios/{published['id']}/round3-config"` occurrences → `"/round4-config"`.

- [ ] **Step 18: Update `tests/test_round2.py`**

1. Comment: `submit_round/submit_round2/round3_submit/` → `submit_round/submit_round2/round4_submit/`.
2. `def test_round3_dedicated_submit_endpoint_is_reached_not_the_generic_one(client, monkeypatch):` → `def test_round4_dedicated_submit_endpoint_is_reached_not_the_generic_one(client, monkeypatch):`; its docstring's "Round 3"/"/round/3/..." mentions → "Round 4"/"/round/4/...".
3. `res = client.post("/candidate/round/3/submit", cookies=_auth(cand_token))` → `client.post("/candidate/round/4/submit", ...)`.

- [ ] **Step 19: Run the full test suite**

Run: `cd C:\Arun\Learning\Python\qa-eval-tool && python -m pytest tests/ -q`
Expected: all tests pass, same count as the Step 1 baseline.

- [ ] **Step 20: Verify the rename is exhaustive**

Run: `grep -rni "round3" backend/app tests --include=*.py`
Expected: only `backend/app/migrate_round3_code_cache.py` and `backend/app/migrate_round3_freeform.py` remain (both explicitly excluded per this plan's Global Constraints) — no other matches.

- [ ] **Step 21: Commit**

```bash
git add backend/app/models.py backend/app/schemas.py backend/app/config.py \
        backend/app/services/llm_service.py backend/app/services/scoring_service.py \
        backend/app/routers/candidate.py backend/app/routers/hr.py \
        backend/app/migrate_round_renumber.py \
        backend/app/prompts/round4_environment_generation.txt \
        backend/app/prompts/round4_ui_mockup_generation.txt \
        backend/app/prompts/round4_partial_response.txt \
        backend/app/prompts/round4_code_snippet.txt \
        backend/app/prompts/round4_scoring.txt \
        tests/conftest.py tests/test_round4.py tests/test_round_draft_autosave.py \
        tests/test_scoring_prompt_injection_guardrail.py tests/test_settings.py \
        tests/test_lazy_expiry.py tests/test_round2.py
git commit -m "Renumber prompt-driven test automation round from Round 3 to Round 4 (backend)"
```

---

### Task 2: Frontend rename (app.js, style.css, index.html)

**Files:**
- Modify: `backend/app/static/app.js`, `backend/app/static/style.css`, `backend/app/templates/index.html`

**Interfaces:**
- Consumes: the renamed backend endpoints/schemas from Task 1 (`/candidate/round/4/...`, `/hr/scenarios/{id}/round4-config`, `round4_passing_score`, `round4_default_assistance_pct` in `AppSettingsOut`/`AppSettingsUpdate`).
- Produces: no new interfaces — purely renamed DOM ids/CSS classes/JS identifiers, consumed only within this same file.

- [ ] **Step 1: Rename in `backend/app/static/app.js`**

1. `const ROUND_LABELS = { 1: "Manual test cases", 2: "Debugging", 3: "Conversational" };` → `{ 1: "Manual test cases", 2: "Debugging", 4: "Conversational" };`.
2. State variables: `round3State` → `round4State`, `round3ViewedTestCaseId` → `round4ViewedTestCaseId`, `round3DraftBuffer` → `round4DraftBuffer`, `round3DraftTimers` → `round4DraftTimers` (and their declaration comments' "test case" wording is fine as-is, just the identifier).
3. `renderHRRoundNav`: `${[1, 2, 3].map((n) => ...)}` → `${[1, 2, 4].map((n) => ...)}`.
3b. `renderCandidateRoundNav`: `${[1, 2, 3].map((n) => ...)}` → `${[1, 2, 4].map((n) => ...)}`; the check `if (currentRound >= 1 && currentRound <= 3) { setPageHeader(...) } else { ... "Assessment complete" ... }` → `if (currentRound >= 1 && currentRound <= 4) { ... }` (this is the candidate-facing nav — do not confuse with `renderHRRoundNav` in item 3 above, they're two separate functions with separate `[1, 2, 3]` arrays).
4. `document.getElementById("set-round3")` → `document.getElementById("set-round4")` in both `loadAppSettings` (the read: `document.getElementById("set-round4").value = appSettings.round4_passing_score;`) and `saveAppSettings` (the write: `round4_passing_score: Number(document.getElementById("set-round4").value),`).

5. **HR "Round 3 settings" card section (the automation round's single settings view — everything from the `ROUND3_BANDS` constant through `saveRound3ConfigEdit`, roughly the block starting `const ROUND3_BANDS = ...` through the end of that section):**
   - `const ROUND3_BANDS = [...]` → `const ROUND4_BANDS = [...]`.
   - `async function loadRound3Settings()` → `async function loadRound4Settings()`; inside it: `document.getElementById("round3-settings-panel")` → `document.getElementById("round4-settings-panel")`; `ROUND3_BANDS.map(...)` → `ROUND4_BANDS.map(...)`; `s.round_number === 3 && s.experience_band === band && s.is_live` → `s.round_number === 4 && ...` (the `liveRound1` lookup's `s.round_number === 1` stays unchanged); `renderRound3SettingsCard(...)` → `renderRound4SettingsCard(...)`.
   - `function renderRound3SettingsCard(scenario, groundedInTitle, bandLabel)` → `function renderRound4SettingsCard(...)`; inside it: `appSettings.round3_default_assistance_pct` → `appSettings.round4_default_assistance_pct`; heading text `<h3>Round 3 - ${bandLabel} ...` → `<h3>Round 4 - ${bandLabel} ...`; `This is what Round 3 / ${scenario.experience_band} candidates currently see.` → `Round 4`; every templated DOM id in this function's returned markup: `r3-title-${scenario.id}` → `r4-title-${scenario.id}`, `r3-desc-${scenario.id}` → `r4-desc-${scenario.id}`, `r3-time-limit-${scenario.id}` → `r4-time-limit-${scenario.id}`, `round3-config-edit-${scenario.id}` → `round4-config-edit-${scenario.id}`, `r3-regen-btn-${scenario.id}` → `r4-regen-btn-${scenario.id}`, `r3-status-${scenario.id}` → `r4-status-${scenario.id}`; every `onclick=` handler name referenced in this markup — `saveRound3Instructions` → `saveRound4Instructions`, `saveRound3TimeLimit` → `saveRound4TimeLimit`, `saveRound3ConfigEdit` → `saveRound4ConfigEdit`, `regenerateRound3Reference` → `regenerateRound4Reference` — updated to match the function renames below.
   - `async function saveRound3Instructions(id)` → `async function saveRound4Instructions(id)`; inside: `r3-status-${id}` → `r4-status-${id}`, `r3-title-${id}` → `r4-title-${id}`, `r3-desc-${id}` → `r4-desc-${id}`; fetch path `/hr/scenarios/${id}/round3-instructions` → `/round4-instructions`.
   - `async function saveRound3TimeLimit(id)` → `async function saveRound4TimeLimit(id)`; inside: `r3-status-${id}` → `r4-status-${id}`, `r3-time-limit-${id}` → `r4-time-limit-${id}` (its `/time-limit` fetch path is round-agnostic, leave unchanged).
   - `async function saveRound3ConfigEdit(id)` → `async function saveRound4ConfigEdit(id)`; inside: `r3-status-${id}` → `r4-status-${id}`, `round3-config-edit-${id}` → `round4-config-edit-${id}`; fetch path `/hr/scenarios/${id}/round3-config` → `/round4-config`.
   - `async function regenerateRound3Reference(id)` → `async function regenerateRound4Reference(id)`; inside: `r3-status-${id}` → `r4-status-${id}`, `r3-regen-btn-${id}` → `r4-regen-btn-${id}`; the call `loadRound3Settings()` → `loadRound4Settings()`.

6. `selectHRRound(n)`: `const isRound3 = n === 3;` → `const isRound4 = n === 4;`; its preceding comment "Round 3 gets its own single settings view... see loadRound3Settings..." → "Round 4 gets its own single settings view... see loadRound4Settings..."; the four `classList.toggle(..., isRound3)` / `classList.toggle(..., !isRound3)` calls (on `live-scenario-panel`, `create-scenario-row`, `screening-history-panel`, and `round3-settings-panel`, the last of which also needs its id changed to `round4-settings-panel`) → all use `isRound4`; `if (isRound3) { loadRound3Settings(); }` → `if (isRound4) { loadRound4Settings(); }`.

7. `openScenarioDetail`: update its comment "Round 3 no longer routes through here at all (see loadRound3Settings/renderRound3SettingsCard) - ..." → "Round 4 no longer routes through here at all (see loadRound4Settings/renderRound4SettingsCard) - ...".

8. `renderRound3Report(s)` → `renderRound4Report(s)` (and its call site).
9. `showRound3Intro()` → `showRound4Intro()`; its overlay id `"round3-intro-overlay"` → `"round4-intro-overlay"`; `confirmStartRound3()` → `confirmStartRound4()`, which calls `startRound(3)` → `startRound(4)`.
10. `renderRoundEntry(n, box, scenario, submission)`: the `n === 3` check dispatching to `renderRound3View` — since round 3 doesn't exist yet after this plan (it's freed up, not yet reused), change the dispatch so `n === 4` routes to `renderRound3View` (renamed below to `renderRound4View`). Concretely: `if (n === 1) {...} else if (n === 2) {...} else { renderRound3View(box); }` → `if (n === 1) {...} else if (n === 2) {...} else if (n === 4) { renderRound4View(box); }` (the trailing bare `else` must become an explicit `n === 4` check — leaving it bare would silently render round 4's view for a hypothetical round 3 the next plan is about to introduce).
11. Also in `renderRoundView`: `if (n === 3) { ... showRound3Intro(); return; }` → `if (n === 4) { ... showRound4Intro(); return; }`.
12. Rename every remaining `round3*`-prefixed function and variable to `round4*`, preserving behavior exactly: `renderRound3View`→`renderRound4View`, `round3AutoSubmit`→`round4AutoSubmit`, `renderRound3Layout`→`renderRound4Layout`, `renderRound3Tabs`→`renderRound4Tabs`, `round3CreateTestCase`→`round4CreateTestCase`, `round3SelectTestCase`→`round4SelectTestCase`, `renderRound3TestCaseBody`→`renderRound4TestCaseBody`, `round3ColFr`→`round4ColFr`, `round3ColResizeState`→`round4ColResizeState`, `round3ApplyColWidths`→`round4ApplyColWidths`, `round3StartColResize`→`round4StartColResize`, `round3OnColResizeMove`→`round4OnColResizeMove`, `round3StopColResize`→`round4StopColResize`, `round3OnComposerInput`→`round4OnComposerInput`, `round3FlushDraft`→`round4FlushDraft`, `round3SendMessage`→`round4SendMessage`, `round3Submit`→`round4Submit`, `round3CodeCache`→`round4CodeCache`, `round3DefaultLanguage`→`round4DefaultLanguage`, `round3OnLanguageChange`→`round4OnLanguageChange`, `showRound3Code`→`showRound4Code`. Every DOM id/class referencing `round3-` inside these functions' template strings (`round3-tabs`, `round3-test-case-body`, `round3-status`, `round3-message`, `round3-send-btn`, `round3-turn-grid`, `round3-pane`, `round3-pane-label`, `round3-pane-body`, `round3-col-resizer`, `code-lang-${t.id}` unaffected since it's turn-scoped not round-scoped) → the `round4-` equivalents, and every `onclick="round3...(...)"` inline handler string updated to call the renamed function.
13. Every `/candidate/round/3/...` fetch call inside these functions → `/candidate/round/4/...`.
14. `ROUND3_DRAFT_DEBOUNCE_MS` (referenced near `round3OnComposerInput`) → `ROUND4_DRAFT_DEBOUNCE_MS`.
15. `--r3c1`/`--r3c2`/`--r3c3` CSS custom property names set via `root.setProperty(...)` in `round3ApplyColWidths` (now `round4ApplyColWidths`) → `--r4c1`/`--r4c2`/`--r4c3` (must match the CSS rename in Step 2 below).
16. Trailing comment near the bootstrap code at the end of the file referencing "ROUND_LABELS and others" is fine unchanged (no round3-specific identifier there).

After this step, grep app.js for `round3\|Round3\|ROUND3` (case-insensitive) yourself before moving on — Step 20 of Task 1's checklist only covers `.py` files, so this file's exhaustiveness is on you here.

- [ ] **Step 2: Rename in `backend/app/static/style.css`**

Rename every `.round3-*` class to `.round4-*` and every `--r3c*` custom property to `--r4c*`, matching Step 1's renames exactly:
`.round3-turn-grid`→`.round4-turn-grid`, `.round3-pane`→`.round4-pane`, `.round3-pane-label`→`.round4-pane-label`, `.round3-col-resizer`→`.round4-col-resizer` (all 3 rules, including the `::after` and `:hover::after` variants), `.round3-pane-body`→`.round4-pane-body`, and inside the responsive block near the end of the file: `.round3-turn-grid { grid-template-columns: 1fr; }` / `.round3-pane-body { ... }` / `.round3-col-resizer { display: none; }` → their `.round4-*` equivalents. Also update the comment mentions ("see llm_service.round3_respond" → "round4_respond", "schemas.Round3ExecutionStep" → "Round4ExecutionStep", "app.js's showRound3Code" → "showRound4Code", "renderRound3TestCaseBody" → "renderRound4TestCaseBody", "round3StartColResize" → "round4StartColResize").

- [ ] **Step 3: Rename in `backend/app/templates/index.html`**

Change the comment and div id:
```html
<!-- Round 3 only (see selectHRRound/loadRound3Settings): no
     fixed reference answer to author/review/compare across
     versions, and only ever one meaningful configuration per
     band - the sections above don't map onto that, so round 3
     gets this single settings view instead of all of them. -->
<div id="round3-settings-panel" class="hidden"></div>
```
to:
```html
<!-- Round 4 only (see selectHRRound/loadRound4Settings): no
     fixed reference answer to author/review/compare across
     versions, and only ever one meaningful configuration per
     band - the sections above don't map onto that, so round 4
     gets this single settings view instead of all of them. -->
<div id="round4-settings-panel" class="hidden"></div>
```
Also update the Settings page's pass-criteria field: `<div class="field-inline"><span class="muted">Round 3 pass score</span><input id="set-round3" type="number" min="0" max="100" /></div>` → `<span class="muted">Round 4 pass score</span><input id="set-round4" .../>`.

- [ ] **Step 4: Verify the rename is exhaustive, then manually smoke test**

Run: `grep -rni "round3" backend/app/static backend/app/templates`
Expected: zero matches.

There is no frontend test suite in this repo — verify by hand:
1. Run: `cd backend && uvicorn app.main:app --reload`
2. Log in as HR (`hr@example.com` / whatever `.env` has), open the round nav — confirm it now shows rounds 1, 2, and 4 (no round 3 tab), and round 4 is still labeled "Conversational" with its settings panel working (assistance % editable, saves).
3. Log in as a candidate, confirm round 4 (not round 3) is the one that runs the conversational automation flow end-to-end: intro modal, create a test case, send a message, view generated code, submit.
4. Confirm the Settings page's "Round 4 pass score" field loads and saves correctly.

- [ ] **Step 5: Commit**

```bash
git add backend/app/static/app.js backend/app/static/style.css backend/app/templates/index.html
git commit -m "Renumber prompt-driven test automation round from Round 3 to Round 4 (frontend)"
```
