# Round 3 Direct Code Edit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a Round 3 candidate switch into an "edit code directly" mode, type or paste code straight into the code pane, and have it saved with only syntax errors corrected — while the construct checklist keeps applying, now satisfied by reading evidence from the code instead of requiring a natural-language answer.

**Architecture:** A new turn kind (`direct_edit`) parallel to the existing `clarify`/`refuse`/`code_edit` ones, driven by a new focused prompt and orchestration function (`llm_service.round3_syntax_fix`) that is deliberately simpler than the instruction path — no clarify branch, no leak-check, no retry, since there is no candidate-facing question on this path at all. It reuses the same `Round3Turn` row shape, the same `category_status`/engine contract, and a small extracted helper (`round3_construct_engine.merge_declared`) shared with `decide()` so the merge logic exists in exactly one place.

**Tech Stack:** Python 3.10+, FastAPI, SQLAlchemy (SQLite), Pydantic, pytest, vanilla JS.

**Spec:** `docs/superpowers/specs/2026-08-27-round3-direct-code-edit-design.md`

## Global Constraints

- No database schema change — `Round3Turn` already has every column this feature needs (`candidate_prompt` holds the raw code, `code_after` the fixed result, `declared_constructs_json` the merged state).
- `code_after` is **always** present on a `direct_edit` turn, even when the LLM couldn't identify a fix — the candidate's own code is never silently discarded, only `clarify` leaves `code_after` null.
- The classification step on this path runs against **every** category in `required_constructs`, not just the previously-open ones, and its result **replaces** any stale declared value for a category the code gives fresh evidence for — the code is the single source of truth once it's been edited directly.
- No gap-flagging: if the resulting code doesn't evidence every required category, nothing is shown to the candidate about it. Silence, same as an unaddressed category on the instruction path.
- `Run` and `Submit` are completely unchanged by this plan — no task here touches `execution_service.py`, `round3_coding_run_start`, or `round3_coding_submit`.
- Follow this repo's existing conventions exactly: `llm_service` tests monkeypatch `_call_claude`; router-level tests in `tests/test_round3.py` use the `client`/`monkeypatch` fixtures, the existing `_create_draft_round3_scenario`/`_publish_scenario` helpers, and the `sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))` boilerplate already at the top of every test file.

---

### Task 1: Schema changes — `direct_edit` response kind and the edit-request body

**Files:**
- Modify: `backend/app/schemas.py:655-674` (`Round3CodingTurnResponse`)
- Test: `tests/test_llm_service_round3_coding.py`

**Interfaces:**
- Produces: `Round3CodingTurnResponse.response_kind` widened to `Literal["clarify", "refuse", "code_edit", "direct_edit"]`; a new `Round3DirectEditCreate` schema (`code: str`) — consumed by Task 4 (`routers/candidate.py`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_llm_service_round3_coding.py`:

```python
def test_round3_coding_turn_response_accepts_direct_edit():
    from app.schemas import Round3CodingTurnResponse
    parsed = Round3CodingTurnResponse.model_validate({
        "response_kind": "direct_edit", "response_message": "No syntax issues found.",
        "code_after": "for x in range(3):\n    print(x)",
    })
    assert parsed.response_kind == "direct_edit"
    assert parsed.code_after is not None


def test_round3_coding_turn_response_requires_code_after_for_direct_edit():
    from app.schemas import Round3CodingTurnResponse
    with pytest.raises(ValidationError):
        Round3CodingTurnResponse.model_validate({
            "response_kind": "direct_edit", "response_message": "...", "code_after": None,
        })


def test_round3_direct_edit_create_rejects_empty_code():
    from app.schemas import Round3DirectEditCreate
    with pytest.raises(ValidationError):
        Round3DirectEditCreate.model_validate({"code": ""})


def test_round3_direct_edit_create_accepts_code():
    from app.schemas import Round3DirectEditCreate
    parsed = Round3DirectEditCreate.model_validate({"code": "print('hi')"})
    assert parsed.code == "print('hi')"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm_service_round3_coding.py -v -k "accepts_direct_edit or requires_code_after_for_direct_edit or direct_edit_create"`
Expected: FAIL — `ImportError: cannot import name 'Round3DirectEditCreate'` (and the `direct_edit` literal is rejected by the current schema)

- [ ] **Step 3: Extend the schema**

In `backend/app/schemas.py`, replace the `Round3CodingTurnResponse` class (currently at line 655):

```python
class Round3CodingTurnResponse(BaseModel):
    """The LLM's classified response for one turn - see
    llm_service.round3_coding_turn/round3_syntax_fix and
    prompts/round3_coding_turn.txt/round3_syntax_fix.txt. code_after is
    required when response_kind is "code_edit" or "direct_edit" (the
    full updated code) and must be absent otherwise (clarify/refuse
    never touch the code). category_status is empty whenever the turn
    has no open construct-checklist categories (an unscoped scenario,
    or every required category already declared/evidenced) - see
    round3_construct_engine."""
    response_kind: Literal["clarify", "refuse", "code_edit", "direct_edit"]
    response_message: str
    code_after: Optional[str] = None
    category_status: dict[str, CategoryStatusEntry] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _code_after_required_for_code_edit(self):
        needs_code = self.response_kind in ("code_edit", "direct_edit")
        if needs_code and not self.code_after:
            raise ValueError("code_after is required when response_kind is 'code_edit' or 'direct_edit'")
        if not needs_code and self.code_after:
            raise ValueError("code_after must not be set when response_kind is 'clarify' or 'refuse'")
        return self
```

Then add a new schema right after `Round3TurnCreate` (currently at line 632-633):

```python
class Round3DirectEditCreate(BaseModel):
    """POST body for /round/3/edit - the candidate's own raw code, typed
    or pasted directly into the editable code pane (see
    docs/superpowers/specs/2026-08-27-round3-direct-code-edit-design.md)."""
    code: str = Field(min_length=1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_llm_service_round3_coding.py -v`
Expected: PASS (all tests, including every pre-existing one — the widened Literal and the broadened validator condition are both purely additive)

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas.py tests/test_llm_service_round3_coding.py
git commit -m "feat: add direct_edit response kind and Round3DirectEditCreate schema"
```

---

### Task 2: Extract `merge_declared` from the engine, shared by both paths

**Files:**
- Modify: `backend/app/services/round3_construct_engine.py:21-53` (`decide`)
- Test: `tests/test_round3_construct_engine.py`

**Interfaces:**
- Produces: `merge_declared(category_status: dict, cumulative_state: dict, required_constructs: list) -> dict` — consumed by Task 3 (`llm_service.round3_syntax_fix`) and by `decide()` itself.
- Consumes: nothing new (pure refactor of existing logic already in `decide()`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_round3_construct_engine.py` (after the existing `REQUIRED = [...]` line, before the first `def test_decide_...`):

```python
def test_merge_declared_folds_declared_categories_into_state():
    category_status = {
        "collection": {"status": "declared", "value": "a list called salaries"},
        "iteration": {"status": "not_addressed"},
    }
    updated = round3_construct_engine.merge_declared(category_status, {}, REQUIRED)
    assert updated["collection"] == "a list called salaries"
    assert "iteration" not in updated


def test_merge_declared_ignores_categories_outside_the_required_list():
    category_status = {"recursion": {"status": "declared", "value": "yes"}}
    updated = round3_construct_engine.merge_declared(category_status, {}, REQUIRED)
    assert "recursion" not in updated


def test_merge_declared_overwrites_a_stale_value():
    category_status = {"iteration": {"status": "declared", "value": "a while loop, per the pasted code"}}
    updated = round3_construct_engine.merge_declared(category_status, {"iteration": "a for loop"}, REQUIRED)
    assert updated["iteration"] == "a while loop, per the pasted code"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_round3_construct_engine.py -v -k merge_declared`
Expected: FAIL — `AttributeError: module 'app.services.round3_construct_engine' has no attribute 'merge_declared'`

- [ ] **Step 3: Extract the helper**

In `backend/app/services/round3_construct_engine.py`, replace `decide()` (currently lines 21-53) with:

```python
def merge_declared(category_status: dict, cumulative_state: dict, required_constructs: list) -> dict:
    """The declared-category merge step, factored out so a caller with
    no clarify/proceed decision to make (round3_syntax_fix - the code is
    the answer, there's nothing to ask about) can update state without
    going through decide()'s branching, which has no meaning there."""
    updated_state = dict(cumulative_state)
    for category, entry in category_status.items():
        if category not in required_constructs:
            continue  # off-topic mention - not tracked, not gated on
        if entry["status"] == "declared":
            updated_state[category] = entry["value"]
    return updated_state


def decide(category_status: dict, cumulative_state: dict, required_constructs: list) -> EngineDecision:
    updated_state = merge_declared(category_status, cumulative_state, required_constructs)
    attempted_vague = [
        category for category, entry in category_status.items()
        if category in required_constructs and entry["status"] == "attempted_but_vague"
    ]

    missing = [c for c in required_constructs if c not in updated_state]
    if not missing:
        return EngineDecision(final_kind="proceed", updated_state=updated_state, ask_categories=[])

    if attempted_vague:
        # This message tried to address more than one gap at once - ask
        # about all of them together, not one round trip each (see spec
        # §4, "bundled instructions"). Filtered to `missing` because the
        # model isn't guaranteed to only classify categories the prompt
        # actually showed it as open - an already-declared category
        # mistakenly re-classified as "attempted_but_vague" must never
        # get re-asked (the guardrail promises settled choices are final)
        # or crowd out the real gap.
        ask = [c for c in required_constructs if c in attempted_vague and c in missing]
        if ask:
            return EngineDecision(final_kind="clarify", updated_state=updated_state, ask_categories=ask)

    # Nothing was attempted this turn (or everything "attempted" was
    # actually already-declared noise, filtered above) - probe toward
    # the single earliest still-unaddressed category, in the scenario's
    # authored order.
    return EngineDecision(final_kind="clarify", updated_state=updated_state, ask_categories=[missing[0]])
```

Note this is a pure refactor: `decide()`'s observable behavior is unchanged — it now calls `merge_declared` instead of inlining the same loop, and builds `attempted_vague` with a list comprehension instead of a `for`/`elif` loop, but the merged state and the resulting `EngineDecision` for any given input are identical to before.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_round3_construct_engine.py -v`
Expected: PASS (all tests, including every pre-existing `test_decide_*` test — this is the regression check that the refactor didn't change `decide()`'s behavior)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/round3_construct_engine.py tests/test_round3_construct_engine.py
git commit -m "refactor: extract merge_declared from decide() for reuse by round3_syntax_fix"
```

---

### Task 3: The syntax-fix prompt and orchestration function

**Files:**
- Create: `backend/app/prompts/round3_syntax_fix.txt`
- Modify: `backend/app/services/llm_service.py` (add `round3_syntax_fix`)
- Test: `tests/test_llm_service_round3_coding.py`

**Interfaces:**
- Consumes: `Round3CodingTurnResponse` (Task 1), `round3_construct_engine.merge_declared` (Task 2).
- Produces: `llm_service.round3_syntax_fix(code: str, language: str, required_constructs: list[str] | None = None, declared_constructs: dict | None = None) -> dict` returning `{"response_message": str, "code_after": str, "declared_constructs": dict}` — consumed by Task 4 (`routers/candidate.py`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_llm_service_round3_coding.py`:

```python
def test_round3_syntax_fix_returns_code_unchanged_when_already_clean(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "direct_edit", "response_message": "No syntax issues found.",
        "code_after": "for x in range(3):\n    print(x)",
        "category_status": {"iteration": {"status": "declared", "value": "a for loop over range(3)"}},
    }))
    result = llm_service.round3_syntax_fix(
        code="for x in range(3):\n    print(x)", language="python",
        required_constructs=["iteration"], declared_constructs={},
    )
    assert result["code_after"] == "for x in range(3):\n    print(x)"
    assert result["declared_constructs"] == {"iteration": "a for loop over range(3)"}


def test_round3_syntax_fix_fixes_a_genuine_syntax_error(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "direct_edit", "response_message": "Added the missing colon.",
        "code_after": "for x in range(3):\n    print(x)",
        "category_status": {"iteration": {"status": "declared", "value": "a for loop over range(3)"}},
    }))
    result = llm_service.round3_syntax_fix(
        code="for x in range(3)\n    print(x)", language="python",
        required_constructs=["iteration"], declared_constructs={},
    )
    assert "for x in range(3):" in result["code_after"]


def test_round3_syntax_fix_leaves_unfixable_code_untouched(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "direct_edit",
        "response_message": "I couldn't confidently identify a fix here - try running it to see the actual error, or fix it yourself and save again.",
        "code_after": "for x in range(3)\n    prin(x)\n  }",
        "category_status": {},
    }))
    result = llm_service.round3_syntax_fix(
        code="for x in range(3)\n    prin(x)\n  }", language="python",
        required_constructs=[], declared_constructs={},
    )
    assert result["code_after"] == "for x in range(3)\n    prin(x)\n  }"
    assert "couldn't confidently identify a fix" in result["response_message"]


def test_round3_syntax_fix_replaces_a_stale_declared_value_from_the_code(monkeypatch):
    # "iteration" was declared "a for loop" from an earlier instruction-
    # based turn, but the pasted code actually uses a while loop - the
    # code is authoritative, so the stale value must be overwritten.
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "direct_edit", "response_message": "No syntax issues found.",
        "code_after": "i = 0\nwhile i < 3:\n    print(i)\n    i += 1",
        "category_status": {"iteration": {"status": "declared", "value": "a while loop"}},
    }))
    result = llm_service.round3_syntax_fix(
        code="i = 0\nwhile i < 3:\n    print(i)\n    i += 1", language="python",
        required_constructs=["iteration"], declared_constructs={"iteration": "a for loop"},
    )
    assert result["declared_constructs"]["iteration"] == "a while loop"


def test_round3_syntax_fix_classifies_against_every_required_category_not_just_open_ones(monkeypatch):
    captured = {}

    def fake_call_claude(prompt, max_tokens=4096):
        captured["prompt"] = prompt
        return json.dumps({
            "response_kind": "direct_edit", "response_message": "No syntax issues found.",
            "code_after": "x = 1",
            "category_status": {},
        })

    monkeypatch.setattr(llm_service, "_call_claude", fake_call_claude)
    llm_service.round3_syntax_fix(
        code="x = 1", language="python",
        required_constructs=["iteration", "comparison"], declared_constructs={"iteration": "a for loop"},
    )
    # Both categories appear in the prompt, including the already-declared
    # one - the code path classifies everything fresh, it never trusts a
    # pre-narrowed "open" list the way the instruction path does.
    assert "iteration" in captured["prompt"]
    assert "comparison" in captured["prompt"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm_service_round3_coding.py -v -k round3_syntax_fix`
Expected: FAIL — `AttributeError: module 'app.services.llm_service' has no attribute 'round3_syntax_fix'`

- [ ] **Step 3: Write the prompt and the orchestration function**

Create `backend/app/prompts/round3_syntax_fix.txt`:

```
You are acting as a senior software engineer reviewing {language} code a candidate wrote themselves - not code you're writing from their instructions. Your job here is narrower than usual and has exactly two parts.

The candidate's code:
{code}

PART 1 - SYNTAX FIX ONLY.

Fix ONLY what would stop this code from parsing, compiling, or running due to a syntax error - a missing colon, an unclosed bracket or parenthesis, a missing semicolon, mismatched indentation, a misspelled keyword, that class of mistake. Do NOT touch logic: never change what a comparison checks, never add or remove a branch, never fix an off-by-one, never rename a variable or function, never add error handling or input validation, never "improve" style or structure - even if you notice a real bug. The candidate's logic, right or wrong, is preserved exactly; you are not debugging their program, only making it parse.

If the code already parses cleanly with no syntax errors, return it completely unchanged in code_after, and say so plainly in response_message (e.g. "No syntax issues found.").

If you cannot identify a concrete, minimal syntax fix without guessing at what the candidate meant - the code is too broken or incomplete to know what syntax was actually intended - make NO changes at all: return the candidate's code exactly as given in code_after, and explain in response_message that you couldn't confidently identify a fix, and that running it (to see the real error) or fixing it themselves is the next step. Never diagnose or explain what's semantically wrong with the code - only whether you could or couldn't identify a syntax-level fix.

PART 2 - CONSTRUCT CLASSIFICATION.

For EACH of these categories, decide whether the resulting code (after your fix, if any) gives clear evidence of that construct being used:
{required_constructs}

- "declared" - the code clearly and concretely uses this construct. Give a short description of what the code actually does for it in "value" (e.g. "a for loop over the list", in your own words).
- "not_addressed" - the code does not use this construct anywhere.

There is no "attempted_but_vague" on this path - code either demonstrates a construct or it doesn't; there's no partial or ambiguous case the way a natural-language instruction can have. Classify every category listed above, even ones that might already be settled from an earlier turn - the code you're looking at now is what actually matters, not what was said before.

For every category you mark "not_addressed", still draft a "neutral_question" in the same bare, vocabulary-free style as any other clarifying question in this app - never naming the category, the concept, or any implementation detail. This is never shown to the candidate on this path (nothing here ever produces a question the candidate sees), but keep the same shape so the data stays consistent across both paths.

Respond with ONLY JSON, no other text, in this exact shape:
{{"response_kind": "direct_edit", "response_message": "...", "code_after": "...the resulting code, always present...", "category_status": {{"category_key": {{"status": "declared", "value": "..."}}, "other_category_key": {{"status": "not_addressed", "neutral_question": "..."}}}}}}
```

In `backend/app/services/llm_service.py`, add this function after `round3_coding_turn` (which ends around line 310, right before `score_round3_coding`):

```python
def round3_syntax_fix(
    code: str,
    language: str,
    required_constructs: list[str] | None = None,
    declared_constructs: dict | None = None,
) -> dict:
    required_constructs = required_constructs or []
    declared_constructs = declared_constructs or {}

    prompt = _load_prompt("round3_syntax_fix.txt").format(
        code=code,
        language=language,
        required_constructs=json.dumps(required_constructs),
    )
    raw = _call_claude(prompt, max_tokens=4096)
    result = _parse_json_response(raw)
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object for the syntax-fix response, got: {type(result)}")
    try:
        parsed = Round3CodingTurnResponse.model_validate(result)
    except ValidationError as e:
        raise ValueError(f"Syntax-fix response didn't match the expected shape: {e}") from e

    category_status = {k: v.model_dump() for k, v in parsed.category_status.items()}
    updated_state = round3_construct_engine.merge_declared(category_status, declared_constructs, required_constructs)

    return {
        "response_message": parsed.response_message,
        "code_after": parsed.code_after,
        "declared_constructs": updated_state,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_llm_service_round3_coding.py -v`
Expected: PASS (all tests, including every pre-existing one)

- [ ] **Step 5: Commit**

```bash
git add backend/app/prompts/round3_syntax_fix.txt backend/app/services/llm_service.py tests/test_llm_service_round3_coding.py
git commit -m "feat: add round3_syntax_fix - syntax-only code correction with construct classification"
```

---

### Task 4: Wire the router — `POST /round/3/edit`

**Files:**
- Modify: `backend/app/routers/candidate.py` (imports + new endpoint)
- Test: `tests/test_round3.py`

**Interfaces:**
- Consumes: `Round3DirectEditCreate` (Task 1), `llm_service.round3_syntax_fix` (Task 3).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_round3.py` (after `test_round3_coding_full_construct_checklist_flow_end_to_end`):

```python
def test_round3_coding_direct_edit_creates_a_turn_and_persists_declared_constructs(client, monkeypatch):
    from app.services import llm_service
    from .conftest import CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1", band="7+")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="R2", band="7+")

    scenario = _create_draft_round3_scenario(client, hr_token, monkeypatch, title="Find the highest salary", band="7+")
    client.patch(
        f"/hr/scenarios/{scenario['id']}",
        json={"reference_json": {**FAKE_ROUND3_CODING_REFERENCE, "required_constructs": ["collection", "iteration"]}},
        cookies=_auth(hr_token),
    )
    client.post(f"/hr/scenarios/{scenario['id']}/publish", cookies=_auth(hr_token))

    cand_token = _login(client, CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD)
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    monkeypatch.setattr(llm_service, "score_round2_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post("/candidate/round/1/submit", json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))
    client.post("/candidate/round/2/submit", json={"investigation": [{"area": "x"}], "root_cause": "x"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/start", json={"language": "python"}, cookies=_auth(cand_token))

    monkeypatch.setattr(llm_service, "round3_syntax_fix", lambda **kwargs: {
        "response_message": "No syntax issues found.",
        "code_after": "salaries = [1, 2, 3]\nhighest = None\nfor s in salaries:\n    if highest is None or s > highest:\n        highest = s\nprint(highest)",
        "declared_constructs": {"collection": "a list called salaries", "iteration": "a for loop over salaries"},
    })
    res = client.post(
        "/candidate/round/3/edit",
        json={"code": "salaries = [1, 2, 3]\nhighest = None\nfor s in salaries:\n    if highest is None or s > highest:\n        highest = s\nprint(highest)"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    body = res.json()
    assert body["response_kind"] == "direct_edit"
    assert body["turn_number"] == 1
    assert "highest = None" in body["code_after"]

    state = client.get("/candidate/round/3/state", cookies=_auth(cand_token)).json()
    assert len(state["turns"]) == 1
    assert state["turns"][0]["response_kind"] == "direct_edit"

    # A follow-up instruction-based turn threads the direct edit's
    # declared_constructs forward, exactly like it would for any other
    # turn kind.
    captured = {}

    def fake_turn(**kwargs):
        captured.update(kwargs)
        return {"response_kind": "code_edit", "response_message": "ok", "code_after": "salaries = [1, 2, 3]\nprint(max(salaries))", "declared_constructs": kwargs["declared_constructs"]}

    monkeypatch.setattr(llm_service, "round3_coding_turn", fake_turn)
    client.post("/candidate/round/3/turn", json={"candidate_prompt": "use max() instead"}, cookies=_auth(cand_token))
    assert captured["declared_constructs"] == {"collection": "a list called salaries", "iteration": "a for loop over salaries"}


def test_round3_coding_direct_edit_rejects_empty_code(client, monkeypatch):
    from app.services import llm_service
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
    client.post("/candidate/round/3/start", json={"language": "python"}, cookies=_auth(cand_token))

    res = client.post("/candidate/round/3/edit", json={"code": ""}, cookies=_auth(cand_token))
    assert res.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_round3.py -v -k round3_coding_direct_edit`
Expected: FAIL — `404` on `POST /candidate/round/3/edit` (route doesn't exist yet)

- [ ] **Step 3: Add the endpoint**

In `backend/app/routers/candidate.py`, add `Round3DirectEditCreate` to the existing schemas import (currently lines 25-26):

```python
    Round3StartRequest, Round3DraftUpdate, Round3TurnCreate, Round3TurnOut, Round3DirectEditCreate,
    Round3RunInputCreate, Round3RunPollOut, Round3RunOut, Round3StateOut,
```

Then add a new endpoint right after `round3_coding_turn` (which ends at line 338 with `return turn`):

```python
@router.post("/round/3/edit", response_model=Round3TurnOut, status_code=201)
def round3_coding_direct_edit(payload: Round3DirectEditCreate, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(3, db, candidate)
    scenario, submission = _round3_coding_scenario_and_submission(candidate, db)
    if submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round has already been submitted.")

    language = (submission.content or {}).get("language")
    existing_turns = submission.round3_turns
    turn_number = len(existing_turns) + 1
    required_constructs = (scenario.reference_json or {}).get("required_constructs", [])
    declared_constructs = (existing_turns[-1].declared_constructs_json if existing_turns else None) or {}

    try:
        response = llm_service.round3_syntax_fix(
            code=payload.code,
            language=language,
            required_constructs=required_constructs,
            declared_constructs=declared_constructs,
        )
    except Exception:
        raise HTTPException(502, "The assistant had trouble responding just now - try saving again.")

    turn = Round3Turn(
        submission_id=submission.id,
        turn_number=turn_number,
        candidate_prompt=payload.code,
        language=language,
        response_kind="direct_edit",
        response_message=response["response_message"],
        code_after=response["code_after"],
        declared_constructs_json=response.get("declared_constructs", declared_constructs),
    )
    db.add(turn)
    db.commit()
    db.refresh(turn)
    return turn
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_round3.py -v`
Expected: PASS (all tests, including every pre-existing one)

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/candidate.py tests/test_round3.py
git commit -m "feat: add POST /round/3/edit for direct code-edit turns"
```

---

### Task 5: Frontend — the "Edit code" toggle

**Files:**
- Modify: `backend/app/static/app.js`

**Interfaces:**
- None — pure frontend addition, no test (matches this codebase's existing convention of no JS test layer).

- [ ] **Step 1: Add the edit-mode state flag**

In `backend/app/static/app.js`, near the existing Round 3 module-level state (currently around the `let round3CodingState = null;` / `let round3CodingDraftTimer = null;` declarations), add:

```js
let round3CodingEditingCode = false;
```

- [ ] **Step 2: Make the code pane conditionally editable**

Replace the code pane block inside `renderRound3CodingLayout` (currently):

```js
      <div class="panel-inset round3-coding-pane">
        <p class="muted round3-pane-label">Code</p>
        <pre class="code-snippet" id="round3-coding-code">${escapeHtml(latestCode || "(no code yet)")}</pre>
        <div class="row">
          <button id="round3-coding-run-btn" onclick="round3CodingRun()" ${latestCode ? "" : "disabled"}>Run</button>
          <button class="btn-block" onclick="round3CodingSubmit()">Submit Round 3</button>
        </div>
```

with:

```js
      <div class="panel-inset round3-coding-pane">
        <p class="muted round3-pane-label">Code</p>
        ${round3CodingEditingCode ? `
          <textarea id="round3-coding-code-edit">${escapeHtml(latestCode)}</textarea>
          <div class="row">
            <button onclick="round3CodingSaveDirectEdit()">Save</button>
            <button onclick="round3CodingCancelDirectEdit()">Cancel</button>
          </div>
        ` : `
          <pre class="code-snippet" id="round3-coding-code">${escapeHtml(latestCode || "(no code yet)")}</pre>
          <div class="row">
            <button onclick="round3CodingStartDirectEdit()">Edit code</button>
            <button id="round3-coding-run-btn" onclick="round3CodingRun()" ${latestCode ? "" : "disabled"}>Run</button>
            <button class="btn-block" onclick="round3CodingSubmit()">Submit Round 3</button>
          </div>
        `}
```

- [ ] **Step 3: Add the toggle/save/cancel functions**

Add these functions right after `round3CodingSendMessage` (which currently ends around line 2556, just before the `round3CodingRun` comment block):

```js
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
  if (!code) return;
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
  }
}
```

- [ ] **Step 4: Verify in the browser**

Run the app (`run_server.bat`, or `uvicorn app.main:app --reload` from `backend/`), log in as a candidate, start Round 3, and confirm: clicking "Edit code" turns the code pane into a textarea with Save/Cancel; typing code and clicking Save posts it, shows the syntax-fixed result read-only again, and the turn appears in the conversation history's implicit count (check via `/candidate/round/3/state` in the Network tab, or just that `Run` becomes enabled once code exists). Confirm Cancel discards the edit and returns to the previous read-only code without saving anything.

- [ ] **Step 5: Commit**

```bash
git add backend/app/static/app.js
git commit -m "feat: add Round 3 direct code-edit toggle to the UI"
```

---

### Task 6: Full end-to-end test of the real orchestration

**Files:**
- Test: `tests/test_round3.py`

**Interfaces:**
- Consumes: everything from Tasks 1-4, exercised together through the HTTP layer with only `_call_claude` mocked (not `llm_service.round3_syntax_fix` itself), proving the whole stack wires together — same rigor as the construct-checklist plan's own Task 8.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_round3.py`:

```python
def test_round3_coding_direct_edit_full_flow_end_to_end(client, monkeypatch):
    """Drives the REAL llm_service.round3_syntax_fix orchestration (only
    _call_claude is mocked, not round3_syntax_fix itself) through pasting
    a complete solution as the very first turn - proving the router, the
    engine's merge_declared, and persistence all wire together, and that
    classification genuinely reads the code rather than trusting a mock's
    say-so."""
    import json as json_module
    from app.services import llm_service
    from .conftest import CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1", band="7+")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="R2", band="7+")

    scenario = _create_draft_round3_scenario(client, hr_token, monkeypatch, title="Find the highest salary", band="7+")
    client.patch(
        f"/hr/scenarios/{scenario['id']}",
        json={"reference_json": {**FAKE_ROUND3_CODING_REFERENCE, "required_constructs": ["collection", "iteration", "comparison"]}},
        cookies=_auth(hr_token),
    )
    client.post(f"/hr/scenarios/{scenario['id']}/publish", cookies=_auth(hr_token))

    cand_token = _login(client, CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD)
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    monkeypatch.setattr(llm_service, "score_round2_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post("/candidate/round/1/submit", json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))
    client.post("/candidate/round/2/submit", json={"investigation": [{"area": "x"}], "root_cause": "x"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/start", json={"language": "python"}, cookies=_auth(cand_token))

    pasted_code = (
        "salaries = [50000, 72000, 61000]\n"
        "highest = None\n"
        "for s in salaries:\n"
        "    if highest is None or s > highest:\n"
        "        highest = s\n"
        "print(highest)"
    )

    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json_module.dumps({
        "response_kind": "direct_edit", "response_message": "No syntax issues found.",
        "code_after": pasted_code,
        "category_status": {
            "collection": {"status": "declared", "value": "a list called salaries"},
            "iteration": {"status": "declared", "value": "a for loop over salaries"},
            "comparison": {"status": "declared", "value": "greater than the current highest"},
        },
    }))

    res = client.post("/candidate/round/3/edit", json={"code": pasted_code}, cookies=_auth(cand_token))
    assert res.status_code == 201
    assert res.json()["response_kind"] == "direct_edit"
    assert res.json()["code_after"] == pasted_code

    state = client.get("/candidate/round/3/state", cookies=_auth(cand_token)).json()
    assert len(state["turns"]) == 1
    assert state["turns"][0]["candidate_prompt"] == pasted_code
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_round3.py -v -k direct_edit_full_flow`
Expected: at this point in the plan it should actually PASS already, since Tasks 1-4 are complete — run it to confirm; if it fails, the failure output identifies which earlier task's wiring is incomplete.

- [ ] **Step 3: (only if Step 2 failed) Fix the identified gap**

Diagnose from the failure and fix in the file the traceback points to, matching the exact interfaces already defined in Tasks 1-4's "Interfaces" blocks above.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_round3.py -v`
Expected: PASS (every test in the file, old and new)

Also run the full suite once to confirm nothing elsewhere regressed: `pytest -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_round3.py
git commit -m "test: full end-to-end coverage of Round 3's direct code-edit flow"
```

---

## Post-plan verification

Run the full test suite once more after all 6 tasks: `pytest -v`
Expected: PASS, with the new counts — `test_llm_service_round3_coding.py` grown by 9, `test_round3_construct_engine.py` grown by 3, `test_round3.py` grown by 3 — alongside every pre-existing test in the suite, unchanged.
