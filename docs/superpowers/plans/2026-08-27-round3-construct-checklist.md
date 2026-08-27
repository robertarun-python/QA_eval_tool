# Round 3 Construct-Checklist Clarify Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Round 3's free-form clarify judgment call with a deterministic, checklist-driven engine that mechanically forces every required programming construct (collection, iteration, comparison, conditional, etc.) to be explicitly supplied by the candidate, and mechanically prevents the assistant's clarifying question from naming the construct it's testing for.

**Architecture:** A new fixed taxonomy module (`round3_constructs.py`) and a pure, LLM-free decision engine (`round3_construct_engine.py`) sit between the existing single-call LLM classification (`llm_service.round3_coding_turn`) and the router. The LLM still classifies and drafts; the engine decides `clarify` vs `code_edit` from a persisted, incrementally-updated declared-state (`Round3Turn.declared_constructs_json`), never trusting the model's own self-reported kind for that distinction. A mechanical leak-check with a regenerate-once-then-fallback-template safety net guards every clarifying question actually shown to the candidate.

**Tech Stack:** Python 3.10+, FastAPI, SQLAlchemy (SQLite), Pydantic, pytest, vanilla JS (no framework) for the frontend.

**Spec:** `docs/superpowers/specs/2026-08-27-round3-construct-checklist-design.md`

## Global Constraints

- `Round3CodingTurnResponse.category_status` is optional and defaults to `{}` — every existing raw LLM response shape (no `category_status` key at all) must keep validating and behaving exactly as it does today.
- `llm_service.round3_coding_turn`'s new `required_constructs`/`declared_constructs` parameters default to `None` (treated as `[]`/`{}`) — when a scenario has no `required_constructs`, behavior is byte-for-byte identical to today's single-call classification.
- Never expose `reference_json` or `declared_constructs_json` to a candidate-facing schema — `ScenarioPublicOut` already excludes `reference_json` for this reason (see its docstring in `schemas.py`); `Round3TurnOut` must stay exactly as it is today (no new field), for the same reason: its keys would leak the internal category taxonomy.
- Follow this repo's existing conventions exactly: `llm_service` tests monkeypatch `_call_claude`; router-level tests in `tests/test_round3.py` use the `client`/`monkeypatch` fixtures and the `sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))` boilerplate already at the top of every test file; migration scripts follow `migrate_round3_code_cache.py`'s idempotent-`ALTER`-via-`sqlite3` shape and are never auto-run (a developer runs them manually — see `run_server.bat`/README, no wiring into `main.py`).
- No new dependencies.
- Prompts are plain hand-editable `.txt` files under `backend/app/prompts/` — never generate them from Python.

---

### Task 1: Construct taxonomy module

**Files:**
- Create: `backend/app/services/round3_constructs.py`
- Test: `tests/test_round3_constructs.py`

**Interfaces:**
- Produces: `CONSTRUCT_CATEGORIES: list[str]`, `FALLBACK_QUESTIONS: dict[str, str]`, `forbidden_vocab(category: str, language: str) -> set[str]`, `contains_forbidden_vocab(text: str, category: str, language: str) -> bool` — consumed by Task 2 (`round3_construct_engine.py`), Task 5 (`generate_round3_reference` validation), and Task 6 (`llm_service.round3_coding_turn`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_round3_constructs.py`:

```python
"""
Round 3's fixed construct taxonomy - see
docs/superpowers/specs/2026-08-27-round3-construct-checklist-design.md.
Pure data + pure functions, no LLM involved.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.services import round3_constructs


def test_forbidden_vocab_combines_generic_and_language_specific():
    words = round3_constructs.forbidden_vocab("collection", "python")
    assert "data structure" in words  # generic
    assert "list" in words            # python-specific


def test_forbidden_vocab_is_scoped_to_the_active_language():
    words = round3_constructs.forbidden_vocab("collection", "java")
    assert "list" not in words        # python-only keyword
    assert "arraylist" in words


def test_forbidden_vocab_for_unknown_language_is_generic_only():
    words = round3_constructs.forbidden_vocab("iteration", "cobol")
    assert "loop" in words
    assert "for" not in words  # no language-specific set exists for "cobol"


def test_contains_forbidden_vocab_catches_a_leak_case_insensitively():
    assert round3_constructs.contains_forbidden_vocab(
        "Should this be a List or a Dict?", "collection", "python"
    )


def test_contains_forbidden_vocab_passes_a_clean_neutral_question():
    assert not round3_constructs.contains_forbidden_vocab(
        "How do you want to represent and hold onto that information?", "collection", "python"
    )


def test_contains_forbidden_vocab_is_scoped_to_the_active_language():
    # "arraylist" is a Java leak, not a Python one - a Python candidate's
    # question is never penalized for another language's vocabulary.
    assert not round3_constructs.contains_forbidden_vocab(
        "How should this be represented as an ArrayList?", "collection", "python"
    )


def test_every_category_has_a_fallback_question():
    for category in round3_constructs.CONSTRUCT_CATEGORIES:
        assert category in round3_constructs.FALLBACK_QUESTIONS
        assert round3_constructs.FALLBACK_QUESTIONS[category]


def test_fallback_questions_never_leak_their_own_category():
    # The safety-net template itself must obey the same rule it enforces.
    for category in round3_constructs.CONSTRUCT_CATEGORIES:
        question = round3_constructs.FALLBACK_QUESTIONS[category]
        for language in ("python", "java", "javascript"):
            assert not round3_constructs.contains_forbidden_vocab(question, category, language)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_round3_constructs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.round3_constructs'`

- [ ] **Step 3: Write the taxonomy module**

Create `backend/app/services/round3_constructs.py`:

```python
"""
Round 3's fixed construct taxonomy: the categories a coding problem's
solution can require, plus the vocabulary that would leak each one if the
assistant's clarifying question named it. See
docs/superpowers/specs/2026-08-27-round3-construct-checklist-design.md.

This module ships the categories Round 3's current problem set actually
exercises (procedural: variables, collections, iteration, conditionals,
functions, I/O). It's a plain data module by design - extending it to
OOP/async/recursion categories later is a data-only change, not a
redesign; nothing that consumes this module cares how many entries exist.
"""
import re

CONSTRUCT_CATEGORIES = [
    "variable", "collection", "element_access",
    "iteration", "nested_iteration",
    "condition", "comparison", "boolean_logic",
    "function", "parameter", "return_value",
    "arithmetic_operation", "string_operation", "type_conversion",
    "input", "output",
]

# Language-neutral trigger words - forbidden regardless of which language
# is active, because they name the CONCEPT ("loop", "iterate") rather
# than any one language's syntax for it.
GENERIC_VOCAB = {
    "variable": {"variable", "name it", "call it"},
    "collection": {"collection", "data structure", "group of values"},
    "element_access": {"index", "indexing", "element access", "position in the"},
    "iteration": {"loop", "iterate", "iteration", "repeat", "cycle through", "go through each"},
    "nested_iteration": {"nested loop", "inner loop", "outer loop", "nested"},
    "condition": {"condition", "conditional", "decision", "branch"},
    "comparison": {"compare", "comparison", "comparison operator"},
    # "and"/"or"/"not" are deliberately NOT listed bare here - they are
    # ordinary English function words ("before", "hold, or", "handle"),
    # and a bare-word block on them would make almost any natural
    # sentence about this category unphraseable. The multi-word phrases
    # below still catch the LLM naming the concept outright; Java/JS
    # additionally get the unambiguous operator symbols in
    # LANGUAGE_CONSTRUCTS. Python's mechanical coverage here is
    # intentionally weaker for this one category - see the design spec.
    "boolean_logic": {"boolean logic", "combine conditions", "both conditions", "either condition"},
    "function": {"function", "method", "define a function", "subroutine"},
    "parameter": {"parameter", "argument", "input to the function"},
    "return_value": {"return", "return value", "return statement"},
    "arithmetic_operation": {"arithmetic operation", "add", "subtract", "multiply", "divide"},
    "string_operation": {"string operation", "concatenate", "substring", "string method"},
    "type_conversion": {"convert", "conversion", "cast", "type conversion", "parse"},
    "input": {"input", "read input", "read from"},
    "output": {"output", "print", "display the result"},
}

# Per-language literal keywords/idioms. Only the active submission's
# language layer is ever checked - a Java candidate is never penalized
# for Python's "def" leaking, because it's simply not in their set.
LANGUAGE_CONSTRUCTS = {
    "python": {
        "collection": {"list", "tuple", "set", "dict", "dictionary"},
        "element_access": {"square brackets"},
        "iteration": {"for", "while", "for loop", "while loop", "range("},
        "condition": {"if", "elif", "else"},
        "comparison": {"==", "!=", ">=", "<="},
        "boolean_logic": set(),  # see the GENERIC_VOCAB comment above
        "function": {"def", "lambda"},
        "return_value": {"return"},
        "arithmetic_operation": {"+", "-", "*", "/", "//", "%", "**"},
        "string_operation": {".join(", ".split(", "f-string"},
        "type_conversion": {"int(", "str(", "float(", "list("},
        "input": {"input("},
        "output": {"print("},
    },
    "java": {
        "collection": {"array", "arraylist", "hashmap", "hashset", "linkedlist"},
        "element_access": {"square brackets", ".get("},
        "iteration": {"for", "while", "do-while", "enhanced-for", "for-each"},
        "condition": {"if", "else if", "switch"},
        "comparison": {"==", "!=", ">=", "<=", ".equals("},
        "boolean_logic": {"&&", "||"},
        "function": {"method", "public", "private", "static"},
        "return_value": {"return"},
        "arithmetic_operation": {"+", "-", "*", "/", "%"},
        "string_operation": {".concat(", ".substring(", "stringbuilder"},
        "type_conversion": {"(int)", "(double)", "integer.parseint", "string.valueof"},
        "input": {"scanner", "system.in", "bufferedreader"},
        "output": {"system.out.println", "system.out.print"},
    },
    "javascript": {
        "collection": {"array", "object", "map", "set"},
        "element_access": {"square brackets"},
        "iteration": {"for", "while", "for-of", "for-in", "foreach", "for each"},
        "condition": {"if", "else if", "switch"},
        "comparison": {"===", "!==", "==", "!=", ">=", "<="},
        "boolean_logic": {"&&", "||"},
        "function": {"function", "arrow function", "=>"},
        "return_value": {"return"},
        "arithmetic_operation": {"+", "-", "*", "/", "%"},
        "string_operation": {"template literal", ".concat(", "${"},
        "type_conversion": {"parseint", "parsefloat", "number(", "string(", "tostring"},
        "input": {"prompt(", "readline"},
        "output": {"console.log"},
    },
}

# One hardcoded, category-neutral fallback question per category - used
# only if the LLM leaks vocabulary twice in a row for that category (see
# round3_construct_engine.leaking_categories).
FALLBACK_QUESTIONS = {
    "variable": "What information does your program need to keep track of here?",
    "collection": "How do you want to represent and hold onto that information in your program?",
    "element_access": "How should your program get to a specific piece of that information?",
    "iteration": "How should the program work through them, one at a time?",
    "nested_iteration": "After handling one of those, what else needs to be examined for it?",
    "condition": "What should determine whether this step happens or not?",
    "comparison": "What should be checked when comparing those two values?",
    "boolean_logic": "When should this be considered true overall - does everything need to hold, or just one part?",
    "function": "How should this piece of logic be packaged so it can be used?",
    "parameter": "What does that piece of logic need to be given in order to run?",
    "return_value": "What should this piece of logic hand back once it's done?",
    "arithmetic_operation": "What calculation should be performed here?",
    "string_operation": "What should happen to combine or reshape that text?",
    "type_conversion": "What form does that value need to be in before it's used this way?",
    "input": "Where should this value come from?",
    "output": "What should the program show once this is done?",
}


def forbidden_vocab(category: str, language: str) -> set[str]:
    generic = GENERIC_VOCAB.get(category, set())
    lang_specific = LANGUAGE_CONSTRUCTS.get(language, {}).get(category, set())
    return {w.lower() for w in generic | lang_specific}


def contains_forbidden_vocab(text: str, category: str, language: str) -> bool:
    # Alphabetic words/phrases are matched at word boundaries, so "list"
    # doesn't falsely trip inside "arraylist" and "or" doesn't falsely
    # trip inside "before" - but still catches the word used on its own
    # ("a list of", "combine conditions"). Symbol tokens ("==", "+",
    # "&&") have no meaningful word boundary, so those fall back to
    # plain substring containment, which is exactly right for code-shaped
    # fragments like "int(" appearing inside a longer expression.
    lowered = text.lower()
    for word in forbidden_vocab(category, language):
        if all(ch.isalpha() or ch == " " for ch in word):
            if re.search(r"\b" + re.escape(word) + r"\b", lowered):
                return True
        elif word in lowered:
            return True
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_round3_constructs.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/round3_constructs.py tests/test_round3_constructs.py
git commit -m "feat: add Round 3's construct taxonomy and vocabulary leak-check"
```

---

### Task 2: Deterministic construct-checklist decision engine

**Files:**
- Create: `backend/app/services/round3_construct_engine.py`
- Test: `tests/test_round3_construct_engine.py`

**Interfaces:**
- Consumes: `round3_constructs.contains_forbidden_vocab(text, category, language)`, `round3_constructs.FALLBACK_QUESTIONS[category]` (Task 1).
- Produces: `EngineDecision` (dataclass: `final_kind: str` — `"clarify"` or `"proceed"` —, `updated_state: dict`, `ask_categories: list[str]`), `decide(category_status: dict, cumulative_state: dict, required_constructs: list[str]) -> EngineDecision`, `leaking_categories(ask_categories: list[str], category_status: dict, language: str) -> list[str]`, `assemble_message(ask_categories: list[str], category_status: dict, use_fallback_for: set[str]) -> str` — all consumed by Task 6 (`llm_service.round3_coding_turn`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_round3_construct_engine.py`:

```python
"""
The deterministic decision layer for Round 3's construct checklist - see
docs/superpowers/specs/2026-08-27-round3-construct-checklist-design.md.
Pure Python, no LLM calls: given what the model classified this turn
(category_status) and what's already known (cumulative_state), decides
what's actually missing and what to ask.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.services import round3_construct_engine, round3_constructs

REQUIRED = ["collection", "iteration", "comparison"]


def test_decide_merges_declared_categories_into_state():
    category_status = {
        "collection": {"status": "declared", "value": "a list called salaries"},
        "iteration": {"status": "not_addressed"},
        "comparison": {"status": "not_addressed"},
    }
    decision = round3_construct_engine.decide(category_status, {}, REQUIRED)
    assert decision.updated_state["collection"] == "a list called salaries"
    assert decision.final_kind == "clarify"


def test_decide_asks_about_every_vague_category_in_one_bundle():
    category_status = {
        "collection": {"status": "declared", "value": "a list called salaries"},
        "iteration": {"status": "attempted_but_vague", "neutral_question": "How will it work through them?"},
        "comparison": {"status": "attempted_but_vague", "neutral_question": "What decides a match?"},
    }
    decision = round3_construct_engine.decide(category_status, {}, REQUIRED)
    assert decision.final_kind == "clarify"
    assert decision.ask_categories == ["iteration", "comparison"]


def test_decide_probes_earliest_not_addressed_when_nothing_attempted():
    category_status = {
        "collection": {"status": "not_addressed"},
        "iteration": {"status": "not_addressed"},
        "comparison": {"status": "not_addressed"},
    }
    decision = round3_construct_engine.decide(category_status, {}, REQUIRED)
    assert decision.ask_categories == ["collection"]


def test_decide_proceeds_once_every_required_category_is_declared():
    category_status = {"comparison": {"status": "declared", "value": "greater than"}}
    decision = round3_construct_engine.decide(
        category_status,
        {"collection": "a list", "iteration": "a for loop"},
        REQUIRED,
    )
    assert decision.final_kind == "proceed"
    assert decision.ask_categories == []


def test_decide_ignores_categories_outside_the_required_list():
    category_status = {"recursion": {"status": "declared", "value": "yes"}}
    decision = round3_construct_engine.decide(category_status, {}, REQUIRED)
    assert "recursion" not in decision.updated_state
    assert decision.final_kind == "clarify"  # nothing in REQUIRED got resolved


def test_decide_honors_an_explicit_correction_to_an_already_declared_category():
    category_status = {"iteration": {"status": "declared", "value": "a while loop instead"}}
    decision = round3_construct_engine.decide(
        category_status,
        {"collection": "a list", "iteration": "a for loop", "comparison": "greater than"},
        REQUIRED,
    )
    assert decision.updated_state["iteration"] == "a while loop instead"
    assert decision.final_kind == "proceed"


def test_leaking_categories_flags_a_question_that_names_the_construct():
    category_status = {"iteration": {"status": "attempted_but_vague", "neutral_question": "Should this be a for loop?"}}
    leaked = round3_construct_engine.leaking_categories(["iteration"], category_status, "python")
    assert leaked == ["iteration"]


def test_leaking_categories_passes_a_clean_question():
    category_status = {"iteration": {"status": "attempted_but_vague", "neutral_question": "How will it work through them, one at a time?"}}
    leaked = round3_construct_engine.leaking_categories(["iteration"], category_status, "python")
    assert leaked == []


def test_assemble_message_uses_fallback_for_flagged_categories():
    category_status = {
        "iteration": {"status": "attempted_but_vague", "neutral_question": "Should this be a for loop?"},
        "comparison": {"status": "attempted_but_vague", "neutral_question": "What decides a match?"},
    }
    message = round3_construct_engine.assemble_message(
        ["iteration", "comparison"], category_status, use_fallback_for={"iteration"}
    )
    assert round3_constructs.FALLBACK_QUESTIONS["iteration"] in message
    assert "What decides a match?" in message
    assert "Should this be a for loop?" not in message
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_round3_construct_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.round3_construct_engine'`

- [ ] **Step 3: Write the engine**

Create `backend/app/services/round3_construct_engine.py`:

```python
"""
Deterministic decision layer for Round 3's construct checklist - see
docs/superpowers/specs/2026-08-27-round3-construct-checklist-design.md.
Pure Python, no LLM calls of its own: the model classifies and drafts
(llm_service.round3_coding_turn), this decides what's actually missing
and what gets asked - never the model's own self-reported response_kind.
"""
from dataclasses import dataclass

from . import round3_constructs


@dataclass
class EngineDecision:
    final_kind: str  # "clarify" | "proceed" - "proceed" means the caller
                      # may use the model's own code_after as a code_edit.
    updated_state: dict
    ask_categories: list  # ordered; empty when final_kind == "proceed"


def decide(category_status: dict, cumulative_state: dict, required_constructs: list) -> EngineDecision:
    updated_state = dict(cumulative_state)
    attempted_vague = []
    for category, entry in category_status.items():
        if category not in required_constructs:
            continue  # off-topic mention - not tracked, not gated on
        if entry["status"] == "declared":
            updated_state[category] = entry["value"]
        elif entry["status"] == "attempted_but_vague":
            attempted_vague.append(category)

    missing = [c for c in required_constructs if c not in updated_state]
    if not missing:
        return EngineDecision(final_kind="proceed", updated_state=updated_state, ask_categories=[])

    if attempted_vague:
        # This message tried to address more than one gap at once - ask
        # about all of them together, not one round trip each (see spec
        # §4, "bundled instructions").
        ask = [c for c in required_constructs if c in attempted_vague]
        return EngineDecision(final_kind="clarify", updated_state=updated_state, ask_categories=ask)

    # Nothing was attempted this turn - probe toward the single earliest
    # still-unaddressed category, in the scenario's authored order.
    return EngineDecision(final_kind="clarify", updated_state=updated_state, ask_categories=[missing[0]])


def leaking_categories(ask_categories: list, category_status: dict, language: str) -> list:
    return [
        c for c in ask_categories
        if round3_constructs.contains_forbidden_vocab(category_status[c]["neutral_question"], c, language)
    ]


def assemble_message(ask_categories: list, category_status: dict, use_fallback_for: set) -> str:
    lines = []
    for c in ask_categories:
        if c in use_fallback_for:
            lines.append(round3_constructs.FALLBACK_QUESTIONS[c])
        else:
            lines.append(category_status[c]["neutral_question"])
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_round3_construct_engine.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/round3_construct_engine.py tests/test_round3_construct_engine.py
git commit -m "feat: add deterministic decision engine for Round 3's construct checklist"
```

---

### Task 3: `category_status` on the turn-response schema

**Files:**
- Modify: `backend/app/schemas.py:636-652` (`Round3CodingTurnResponse`)
- Test: `tests/test_llm_service_round3_coding.py`

**Interfaces:**
- Produces: `CategoryStatusEntry` (fields: `status: Literal["declared", "attempted_but_vague", "not_addressed"]`, `value: Optional[str]`, `neutral_question: Optional[str]`), `Round3CodingTurnResponse.category_status: dict[str, CategoryStatusEntry]` (default `{}`) — consumed by Task 6 (`llm_service.round3_coding_turn`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_llm_service_round3_coding.py` (after the existing `test_round3_coding_turn_rejects_code_edit_without_code`):

```python
def test_round3_coding_turn_response_accepts_category_status():
    from app.schemas import Round3CodingTurnResponse
    parsed = Round3CodingTurnResponse.model_validate({
        "response_kind": "clarify", "response_message": "...", "code_after": None,
        "category_status": {
            "iteration": {"status": "attempted_but_vague", "neutral_question": "How will it work through them?"},
            "collection": {"status": "declared", "value": "a list"},
        },
    })
    assert parsed.category_status["collection"].value == "a list"
    assert parsed.category_status["iteration"].status == "attempted_but_vague"


def test_round3_coding_turn_response_defaults_category_status_to_empty():
    from app.schemas import Round3CodingTurnResponse
    parsed = Round3CodingTurnResponse.model_validate({
        "response_kind": "code_edit", "response_message": "done", "code_after": "x = 1",
    })
    assert parsed.category_status == {}


def test_category_status_entry_requires_value_when_declared():
    from app.schemas import Round3CodingTurnResponse
    with pytest.raises(ValidationError):
        Round3CodingTurnResponse.model_validate({
            "response_kind": "clarify", "response_message": "...", "code_after": None,
            "category_status": {"collection": {"status": "declared"}},
        })


def test_category_status_entry_requires_neutral_question_when_not_declared():
    from app.schemas import Round3CodingTurnResponse
    with pytest.raises(ValidationError):
        Round3CodingTurnResponse.model_validate({
            "response_kind": "clarify", "response_message": "...", "code_after": None,
            "category_status": {"collection": {"status": "not_addressed"}},
        })
```

Add `from pydantic import ValidationError` to the file's imports (alongside the existing `import pytest`).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm_service_round3_coding.py -v -k category_status`
Expected: FAIL — `pydantic_core._pydantic_core.ValidationError: ... Extra inputs are not permitted` is NOT raised (schema currently ignores/ has no `category_status` field, so `parsed.category_status` raises `AttributeError`), and the two "requires" tests fail because no validation exists yet to reject them.

- [ ] **Step 3: Extend the schema**

In `backend/app/schemas.py`, replace the `Round3CodingTurnResponse` class (currently at line 636) with:

```python
class CategoryStatusEntry(BaseModel):
    """One construct category's classification for a single turn - see
    llm_service.round3_coding_turn and round3_construct_engine.decide.
    "declared" commits to a specific choice (value required); anything
    else must carry the vocabulary-free neutral_question the assistant
    would ask about it."""
    status: Literal["declared", "attempted_but_vague", "not_addressed"]
    value: Optional[str] = None
    neutral_question: Optional[str] = None

    @model_validator(mode="after")
    def _fields_match_status(self):
        if self.status == "declared" and not self.value:
            raise ValueError("value is required when status is 'declared'")
        if self.status != "declared" and not self.neutral_question:
            raise ValueError("neutral_question is required unless status is 'declared'")
        return self


class Round3CodingTurnResponse(BaseModel):
    """The LLM's classified response for one turn - see
    llm_service.round3_coding_turn and prompts/round3_coding_turn.txt.
    code_after is required when response_kind is "code_edit" (the full
    updated code) and must be absent otherwise (clarify/refuse never
    touch the code). category_status is empty whenever the turn has no
    open construct-checklist categories (an unscoped scenario, or every
    required category already declared) - see round3_construct_engine."""
    response_kind: Literal["clarify", "refuse", "code_edit"]
    response_message: str
    code_after: Optional[str] = None
    category_status: dict[str, CategoryStatusEntry] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _code_after_required_for_code_edit(self):
        if self.response_kind == "code_edit" and not self.code_after:
            raise ValueError("code_after is required when response_kind is 'code_edit'")
        if self.response_kind != "code_edit" and self.code_after:
            raise ValueError("code_after must not be set when response_kind is 'clarify' or 'refuse'")
        return self
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_llm_service_round3_coding.py -v`
Expected: PASS (all tests, including the 3 pre-existing ones and the 4 new ones)

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas.py tests/test_llm_service_round3_coding.py
git commit -m "feat: add category_status to Round3CodingTurnResponse"
```

---

### Task 4: `declared_constructs_json` column + migration

**Files:**
- Modify: `backend/app/models.py:367-390` (`Round3Turn`)
- Create: `backend/app/migrate_round3_construct_state.py`

**Interfaces:**
- Produces: `Round3Turn.declared_constructs_json: Column(JSON, nullable=True)` — consumed by Task 7 (`routers/candidate.py`).

- [ ] **Step 1: Add the column**

In `backend/app/models.py`, in the `Round3Turn` class, replace:

```python
    code_after = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
```

with:

```python
    code_after = Column(Text, nullable=True)
    # Cumulative snapshot of which required constructs (see
    # services/round3_constructs.py) the candidate has explicitly
    # declared as of this turn - written on every turn, not just
    # code_edit ones, so "what's currently known" is always a plain read
    # of the latest turn. NULL only for rows written before this column
    # existed. See
    # docs/superpowers/specs/2026-08-27-round3-construct-checklist-design.md.
    declared_constructs_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
```

- [ ] **Step 2: Verify the model change loads cleanly**

Run: `python -c "import sys; sys.path.insert(0, 'backend'); from app.models import Round3Turn; print(Round3Turn.declared_constructs_json)"`
Expected: prints the SQLAlchemy `InstrumentedAttribute` for the new column, no error.

(A fresh test run's in-memory SQLite DB is built from `Base.metadata.create_all()`, so this column is already present for every test from this point on — no test in this task is needed beyond the import check above; the tests that actually exercise the column arrive in Task 7.)

- [ ] **Step 3: Write the migration script**

Create `backend/app/migrate_round3_construct_state.py`:

```python
"""
Migration for Round 3's construct-checklist state (see models.py:
Round3Turn.declared_constructs_json). `Base.metadata.create_all()` only
creates missing tables, so the existing round3_turns table needs an
explicit ALTER for its new column. See
docs/superpowers/specs/2026-08-27-round3-construct-checklist-design.md.

Idempotent - checks column presence first, so it's safe to run more than
once (e.g. after a fresh `create_all()` already created everything for a
brand new DB - nothing to do there).

Run from backend/: python -m app.migrate_round3_construct_state
"""
import sqlite3

from .database import sqlite_db_path


def _has_column(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def migrate() -> list[str]:
    con = sqlite3.connect(sqlite_db_path())
    applied = []
    try:
        cur = con.cursor()

        if not _has_column(cur, "round3_turns", "declared_constructs_json"):
            cur.execute("ALTER TABLE round3_turns ADD COLUMN declared_constructs_json TEXT")
            applied.append("round3_turns.declared_constructs_json")

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

- [ ] **Step 4: Run the migration against the dev DB and verify idempotency**

Run (from `backend/`): `python -m app.migrate_round3_construct_state`
Expected: `Applied: round3_turns.declared_constructs_json` (or `Nothing to do...` if the dev DB doesn't have a `round3_turns` table yet — both are fine).
Run it again immediately: Expected: `Nothing to do - schema already up to date.`

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/app/migrate_round3_construct_state.py
git commit -m "feat: add Round3Turn.declared_constructs_json column and migration"
```

---

### Task 5: HR authoring — `required_constructs`

**Files:**
- Modify: `backend/app/prompts/round3_reference_generation.txt`
- Modify: `backend/app/services/llm_service.py:173-182` (`generate_round3_reference`)
- Modify: `backend/app/static/app.js:778,782` (HR review UI)
- Test: `tests/test_llm_service_round3_coding.py`

**Interfaces:**
- Consumes: `round3_constructs.CONSTRUCT_CATEGORIES` (Task 1).
- Produces: `reference_json["required_constructs"]: list[str]` (optional key, validated when present) — consumed by Task 7 (`routers/candidate.py`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_llm_service_round3_coding.py`:

```python
def test_generate_round3_reference_returns_required_constructs(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "test_cases": [{"input": "2 3", "expected_output": "5", "description": "basic sum"}],
        "expected_approach": "Read two integers and add them directly.",
        "required_constructs": ["variable", "input", "output"],
    }))
    result = llm_service.generate_round3_reference(scenario_description="Add two numbers", experience_band="0-7")
    assert result["required_constructs"] == ["variable", "input", "output"]


def test_generate_round3_reference_rejects_unknown_required_construct(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "test_cases": [{"input": "2 3", "expected_output": "5", "description": "basic sum"}],
        "expected_approach": "...",
        "required_constructs": ["not_a_real_category"],
    }))
    with pytest.raises(ValueError):
        llm_service.generate_round3_reference(scenario_description="x", experience_band="0-7")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm_service_round3_coding.py -v -k required_construct`
Expected: FAIL — the first test fails because `"required_constructs"` isn't in `result` (nothing put it there, but it would actually pass trivially since the function just returns `result` unchanged... check by running: the REJECTS test is what must fail, since nothing currently validates against unknown categories, so no `ValueError` is raised).

- [ ] **Step 3: Extend the prompt and the validation**

In `backend/app/prompts/round3_reference_generation.txt`, replace the file with:

```
You are a senior software engineer with 8+ years of experience, authoring a REFERENCE solution and test suite for a candidate assessment - this reference will be used to automatically grade a candidate's code, so it needs to be correct, unambiguous, and realistic.

Problem statement (experience band: {experience_band}):
{scenario_description}

Produce:
1. A set of test cases that fully exercise this problem's correctness - realistic inputs (including edge cases: empty/zero/negative/boundary values where they genuinely apply to this problem) paired with the exact expected output a correct solution would print. Each test case's "input" is the exact stdin content a program would read (space or newline separated values, matching how the problem statement describes input), and "expected_output" is the exact stdout content a correct solution prints (no extra commentary).
2. A one-to-two sentence note on the efficient approach a strong {experience_band}-level engineer would converge on (e.g. "use a hash map for O(n) lookup instead of nested loops", or "sort first, then a single pass") - this is used later to judge whether a candidate iteratively improved toward it, and is never shown to the candidate directly.
3. The ordered list of construct categories (from the fixed list below) a correct solution genuinely requires, in the order a candidate would naturally decide them - what to store, before how to process it, before what determines a match. Only include a category the problem actually needs; don't pad the list. This is used to make sure the candidate explicitly specifies each one themselves rather than the assistant assuming it, and is never shown to the candidate directly.

Categories: variable, collection, element_access, iteration, nested_iteration, condition, comparison, boolean_logic, function, parameter, return_value, arithmetic_operation, string_operation, type_conversion, input, output

Respond with ONLY a JSON object, no other text, in this shape:
{{"test_cases": [{{"input": "...", "expected_output": "...", "description": "..."}}], "expected_approach": "...", "required_constructs": ["...", "..."]}}
```

In `backend/app/services/llm_service.py`, add `from . import round3_constructs` to the imports (near the existing `from ..schemas import ...` line), then replace `generate_round3_reference`:

```python
def generate_round3_reference(scenario_description: str, experience_band: str) -> dict:
    prompt = _load_prompt("round3_reference_generation.txt").format(
        scenario_description=scenario_description,
        experience_band=experience_band,
    )
    raw = _call_claude(prompt)
    result = _parse_json_response(raw)
    if not isinstance(result, dict) or "test_cases" not in result or "expected_approach" not in result:
        raise ValueError(f"Expected a JSON object with 'test_cases' and 'expected_approach' keys, got: {result!r}")
    unknown = set(result.get("required_constructs", [])) - set(round3_constructs.CONSTRUCT_CATEGORIES)
    if unknown:
        raise ValueError(f"required_constructs contains unknown categories: {sorted(unknown)}")
    return result
```

In `backend/app/static/app.js`, replace line 778:

```js
    ${isCodingReference && scenario.reference_json ? `<p class="muted"><strong>Expected approach:</strong> ${escapeHtml(scenario.reference_json.expected_approach || "")}</p>` : ""}
```

with:

```js
    ${isCodingReference && scenario.reference_json ? `<p class="muted"><strong>Expected approach:</strong> ${escapeHtml(scenario.reference_json.expected_approach || "")}</p>` : ""}
    ${isCodingReference && scenario.reference_json && scenario.reference_json.required_constructs && scenario.reference_json.required_constructs.length
      ? `<p class="muted"><strong>Required concepts:</strong> ${escapeHtml(scenario.reference_json.required_constructs.join(", "))}</p>`
      : ""}
```

And update the default-shape fallback at (what is now) the following line:

```js
        <textarea id="ref-json-edit">${escapeHtml(JSON.stringify(scenario.reference_json || (isCodingReference ? {test_cases: [], expected_approach: ""} : []), null, 2))}</textarea>
```

to:

```js
        <textarea id="ref-json-edit">${escapeHtml(JSON.stringify(scenario.reference_json || (isCodingReference ? {test_cases: [], expected_approach: "", required_constructs: []} : []), null, 2))}</textarea>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_llm_service_round3_coding.py -v`
Expected: PASS (all tests)

Also run the full round-3 HR test to confirm nothing broke: `pytest tests/test_round3.py -v -k hr`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/prompts/round3_reference_generation.txt backend/app/services/llm_service.py backend/app/static/app.js tests/test_llm_service_round3_coding.py
git commit -m "feat: HR-authored required_constructs for Round 3 scenarios"
```

---

### Task 6: Rewrite `round3_coding_turn.txt` and the turn orchestration

**Files:**
- Modify: `backend/app/prompts/round3_coding_turn.txt`
- Modify: `backend/app/services/llm_service.py:185-209` (`round3_coding_turn`)
- Test: `tests/test_llm_service_round3_coding.py`

**Interfaces:**
- Consumes: `round3_construct_engine.decide/leaking_categories/assemble_message` (Task 2), `Round3CodingTurnResponse` with `category_status` (Task 3).
- Produces: `llm_service.round3_coding_turn(..., required_constructs: list[str] | None = None, declared_constructs: dict | None = None) -> dict` where the returned dict always includes a `"declared_constructs"` key — consumed by Task 7 (`routers/candidate.py`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_llm_service_round3_coding.py`:

```python
def test_round3_coding_turn_forces_clarify_when_a_required_category_is_missing(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "code_edit", "response_message": "Added the loop.",
        "code_after": "for x in salaries: print(x)",
        "category_status": {
            "iteration": {"status": "declared", "value": "a for loop over salaries"},
            "comparison": {"status": "not_addressed", "neutral_question": "What should determine a match?"},
        },
    }))
    result = llm_service.round3_coding_turn(
        scenario_description="Find the highest salary", language="python",
        conversation_so_far=[], current_code=None,
        candidate_prompt="loop through the salaries",
        turn_number=2,
        required_constructs=["iteration", "comparison"],
        declared_constructs={},
    )
    # The model tried to hand back code_edit, but "comparison" is still
    # missing - the engine, not the model's self-report, must win.
    assert result["response_kind"] == "clarify"
    assert result["code_after"] is None
    assert "What should determine a match?" in result["response_message"]
    assert result["declared_constructs"]["iteration"] == "a for loop over salaries"


def test_round3_coding_turn_proceeds_once_every_required_category_is_declared(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "code_edit", "response_message": "Added the comparison.",
        "code_after": "if s > highest: highest = s",
        "category_status": {"comparison": {"status": "declared", "value": "greater than the current highest"}},
    }))
    result = llm_service.round3_coding_turn(
        scenario_description="Find the highest salary", language="python",
        conversation_so_far=[], current_code="salaries = [1, 2]",
        candidate_prompt="if greater than the current highest, replace it",
        turn_number=3,
        required_constructs=["iteration", "comparison"],
        declared_constructs={"iteration": "a for loop over salaries"},
    )
    assert result["response_kind"] == "code_edit"
    assert "if s > highest" in result["code_after"]
    assert result["declared_constructs"] == {
        "iteration": "a for loop over salaries",
        "comparison": "greater than the current highest",
    }


def test_round3_coding_turn_ignores_the_checklist_when_none_is_configured(monkeypatch):
    # No required_constructs at all (an old scenario, or a scoped-out
    # problem) - behavior must be identical to before this feature.
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
    assert result["declared_constructs"] == {}


def test_round3_coding_turn_regenerates_once_on_a_vocabulary_leak(monkeypatch):
    calls = []

    def fake_call_claude(prompt, max_tokens=4096):
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps({
                "response_kind": "clarify", "response_message": "...", "code_after": None,
                "category_status": {"iteration": {"status": "not_addressed", "neutral_question": "Should this be a for loop?"}},
            })
        return json.dumps({
            "response_kind": "clarify", "response_message": "...", "code_after": None,
            "category_status": {"iteration": {"status": "not_addressed", "neutral_question": "How will it work through them, one at a time?"}},
        })

    monkeypatch.setattr(llm_service, "_call_claude", fake_call_claude)
    result = llm_service.round3_coding_turn(
        scenario_description="x", language="python", conversation_so_far=[], current_code=None,
        candidate_prompt="I need to process the salaries",
        turn_number=1, required_constructs=["iteration"], declared_constructs={},
    )
    assert len(calls) == 2
    assert "for loop" not in result["response_message"]
    assert "How will it work through them" in result["response_message"]


def test_round3_coding_turn_falls_back_to_template_after_two_leaks(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "clarify", "response_message": "...", "code_after": None,
        "category_status": {"iteration": {"status": "not_addressed", "neutral_question": "Should this use a for loop or a while loop?"}},
    }))
    result = llm_service.round3_coding_turn(
        scenario_description="x", language="python", conversation_so_far=[], current_code=None,
        candidate_prompt="I need to process the salaries",
        turn_number=1, required_constructs=["iteration"], declared_constructs={},
    )
    from app.services import round3_constructs
    assert result["response_message"] == round3_constructs.FALLBACK_QUESTIONS["iteration"]


def test_round3_coding_turn_bundles_multiple_gaps_into_one_clarify(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "clarify", "response_message": "...", "code_after": None,
        "category_status": {
            "iteration": {"status": "attempted_but_vague", "neutral_question": "How will it work through them, one at a time?"},
            "comparison": {"status": "attempted_but_vague", "neutral_question": "What should determine a match?"},
        },
    }))
    result = llm_service.round3_coding_turn(
        scenario_description="x", language="python", conversation_so_far=[], current_code=None,
        candidate_prompt="loop through the salaries and compare each to the current highest",
        turn_number=2, required_constructs=["iteration", "comparison"], declared_constructs={},
    )
    assert result["response_kind"] == "clarify"
    assert "How will it work through them, one at a time?" in result["response_message"]
    assert "What should determine a match?" in result["response_message"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm_service_round3_coding.py -v -k "forces_clarify or proceeds_once or ignores_the_checklist or regenerates_once or falls_back or bundles_multiple"`
Expected: FAIL — `TypeError: round3_coding_turn() got an unexpected keyword argument 'required_constructs'`

- [ ] **Step 3: Rewrite the prompt and the orchestration**

Replace `backend/app/prompts/round3_coding_turn.txt` with:

```
You are acting as a senior software engineer with 8+ years of experience, writing {language} code for a candidate who directs you entirely through natural-language instructions. You NEVER make a design or algorithmic decision on the candidate's behalf - the candidate must specify what to build, and you write exactly that, nothing more.

If {language} is java, the public class MUST be named exactly `Main` (this gets compiled from a file named Main.java, and javac requires the public class name to match the filename exactly) - never Solution or any other name, regardless of what a typical solution to this kind of problem would conventionally be called.

Problem statement:
{scenario_description}

Conversation so far (oldest first, each entry is what the candidate asked and how you responded):
{conversation_so_far}

Current code (the last code you produced - "(no code written yet)" if nothing has been written yet):
{current_code}

This is turn number {turn_number}. is_first_turn = {is_first_turn}.

Construct checklist for this problem - categories a correct solution requires, already narrowed to only the ones NOT yet pinned down (anything not listed here is either irrelevant to this problem or already decided - see "Already declared" below). An empty list means there is nothing left to classify - skip the "Construct classification" section entirely and return an empty category_status object:
{open_categories}

Already declared - these choices are FINAL. Never re-ask about them, never hint they might be wrong, never suggest an alternative; just use them exactly as given for any new code you write. A candidate who wants something different must say so explicitly in a new instruction - you never initiate that:
{declared_constructs}

The candidate's new instruction:
{candidate_prompt}

{regeneration_note}

Continuation check - do this BEFORE classifying: look at the LAST entry in the conversation so far. If its response_kind was "clarify", the candidate's new instruction is very likely their direct answer to that exact question (a name, a value, a message, a technique) rather than a brand-new instruction - read it that way first. If it reasonably supplies the one specific thing you asked for last turn, combine it with whatever instruction originally prompted that clarify question, and classify that COMBINED instruction normally (code_edit once it's now fully specified and isn't solve-it-for-me - see refuse (c) below). Do NOT re-ask the same clarifying question or a reworded version of it just because the answer is short, informal, or doesn't repeat your question's exact wording back to you - a short answer is still an answer. Only clarify again (rather than proceed to code_edit) if their new message genuinely still fails to supply that one specific thing (e.g. they changed the subject instead of answering, or their answer is itself unnamed/ambiguous in the same way).

Classify this instruction and respond with exactly one of these three response_kind values:

1. "clarify" - use this whenever writing the code requires YOU to invent a NAME or a MECHANICAL TECHNIQUE the candidate didn't state, no matter how small or how "obvious" a reasonable convention might seem to you. This is also the correct response_kind whenever the construct checklist above lists any open category - see "Construct classification" below, which governs exactly how you handle that.

   NEVER clarify about runtime behavior, edge cases, or error conditions - this is the single most important boundary on this rule. If an instruction is syntactically well-defined and you know exactly what code to write for it, WRITE IT, even if you can see it might crash, raise an exception, or produce a weird result for some inputs (an empty list, an out-of-range index, a value that doesn't exist yet, too few elements, division by zero, whatever it may be). Examples of questions you must NEVER ask, because they are exactly this mistake: "what should happen if the list has fewer than 2 elements", "what if the input is empty", "should I handle the case where...". Whether an edge case gets handled at all is the candidate's decision to make (or not make, and then discover from a crash) in a later instruction - never something you flag, hint at, or ask about. This applies no matter how obviously the literal code would break.

   If the instruction is so vague that it doesn't identify ANY concrete action at all - a dangling reference with no clear antecedent ("handle it", "fix it", "make it work", "deal with that") - do not try to guess what it might mean or what scenario it might be about. Respond with response_kind "clarify" and a bare response_message such as "I need a specific instruction - what exactly should the code do?" - do not speculate about, name, or hint at what "it" might refer to.

   Guardrail - once something is already established (named, and its reading/parsing/conversion method already chosen, whether by the candidate or by you in an earlier turn they didn't object to), never re-ask about it or hint it might be wrong; always just use it exactly as it already exists and write the new instruction literally:
   - What type to treat an EXISTING variable/value as, or whether to convert it (e.g. "should I add these as numbers or as strings?" for two variables that already exist) - use whatever type it already is.
   - Whether an operation, comparison, or conversion the candidate asked for is likely to produce the result they want.
   - Any runtime behavior, edge case, or error condition the literal code might hit (see above - this is never a clarify trigger, for something new OR something established).
   - Any other "did you mean X or Y" framing where X or Y reveals a design or correctness concern rather than a pure missing name/technique for something NEW.
   A candidate who wants different behavior for something already established must say so explicitly in a later instruction; you never hint that different behavior might be needed.

2. "refuse" - any of these three situations. Do not change any code this turn in any case, no matter how the request is worded or how many times it's asked.
   a. Design/algorithm judgment call: the candidate is asking YOU to decide instead of specifying it themselves - e.g. "which loop is correct here", "is my approach right", "what's the best data structure for this", "what would you do", or any variant asking you to decide, validate, or compare approaches on their behalf. Always respond with EXACTLY this response_message, regardless of how the question is phrased: "I can't make that call for you - tell me specifically what you want (which loop, which data structure, which approach), and I'll write it."
   b. Self-diagnosis dodge: the candidate reports, describes, or pastes a runtime error, exception, stack trace, or wrong/unexpected output (their own or copied from a run) and asks you to diagnose or fix it, rather than telling you the specific code change to make. Always respond with EXACTLY this response_message, regardless of how it's phrased or what the error/output actually is: "I can't fix that. Diagnose it yourself and tell me exactly what to change in the code." This applies even if what they pasted would make the cause obvious to you - the candidate must do that diagnosis, not you. It does NOT apply once they've done that diagnosis themselves and give you a concrete instruction (e.g. "the total variable needs to start at 0, it's currently unset" is a normal code_edit, not this).
   c. Solve-it-for-me request: the candidate restates the overall task and asks you to produce "a program", "the solution", "code for this", or otherwise build the thing end-to-end - e.g. "write a program to add two numbers", "give me the solution", "can you help me with this", "any suggestions on how to start", "how would you solve this" - rather than directing what to build. This applies identically on every turn, INCLUDING turn 1 - there is no exception for an opening instruction that just restates the problem statement in different words instead of giving a real first instruction. Always respond with EXACTLY this response_message, regardless of how it's phrased: "I can't write this for you - tell me what you want built, and I'll write exactly that." Do not soften this or add your own examples of what a good instruction might look like - the candidate decides what to specify, not you.

   A single instruction is free to bundle several mechanical steps in one message - "read two integers, add them, and print the result" is a normal code_edit, not a refusal - as long as every step is something you can act on directly (no missing name/technique - see "clarify" above) and the instruction as a whole is still the candidate directing specific work rather than restating the problem for you to solve (see (c)).

3. "code_edit" - the candidate gave you an instruction you can act on directly - not a restatement of the overall problem or a request to build "a program"/"the solution" for you to design (see refuse (c) above), AND every category in the construct checklist above is already in "Already declared" (an empty checklist above means there's nothing left to gate on). Write or modify the code to do EXACTLY and ONLY what this instruction asks - including every mechanical step it names, in order, if it names more than one. Do not fix, refactor, optimize, rename, or clean up anything else in the existing code, even if you notice a real bug or bad practice, unless the candidate's instruction explicitly asks you to change that specific thing. Return the FULL updated code in code_after (not a diff, not a fragment).

There is no special exception for turn 1 (is_first_turn = true): refuse (c) above applies exactly the same way there - an opening instruction that just restates the problem statement in different words instead of giving a real first instruction must still be refused. Turn 1 is "code_edit" whenever the candidate's instruction is something you can act on directly without inventing a name or technique (see "clarify" above), the same as any other turn.

Construct classification - do this whenever the checklist above lists any open categories, regardless of which response_kind you chose:

For EACH category listed in the checklist above, decide, based ONLY on the candidate's new instruction this turn (never re-derive anything from earlier turns - "Already declared" above already tells you what's settled):

- "declared" - this instruction commits to a specific, concrete choice for this category (a name, a technique, a structure) that you could act on right now. A hedge, a guess, or musing out loud ("I think maybe...", "probably a...") is NOT declared - only an instruction you could actually build from counts. Give the exact value in "value" (in your own words, not necessarily the candidate's).
- "attempted_but_vague" - this instruction is clearly ABOUT this category (the candidate is trying to address it) but doesn't commit to a specific choice.
- "not_addressed" - this instruction says nothing relevant to this category at all.

For every category that isn't "declared", draft a "neutral_question" - ONE bare, minimal, natural-language question that would prompt the candidate to make this decision themselves. This is the single most important rule in this whole prompt: the question must NEVER name the category, the concept, the vocabulary, the technique, or any specific option - not the words used to describe this category above, not any language keyword, not "which X or Y" framing. Ask about the underlying need in plain terms instead. For example, for a category about repeating an action over a set of items, ask something like "How should the program work through them, one at a time?" - never "should this be a for loop or a while loop?" or "how should this be iterated?". Do not offer multiple-choice options or examples - naming the possibilities is doing the candidate's thinking for them just as much as answering would be.

If a category is "declared", you may omit "neutral_question" (or leave it null).

Respond with ONLY JSON, no other text, in one of these exact shapes:
{{"response_kind": "clarify", "response_message": "...", "code_after": null, "category_status": {{"category_key": {{"status": "attempted_but_vague", "neutral_question": "..."}}}}}}
{{"response_kind": "refuse", "response_message": "...", "code_after": null, "category_status": {{}}}}
{{"response_kind": "code_edit", "response_message": "...", "code_after": "...the full code...", "category_status": {{"category_key": {{"status": "declared", "value": "..."}}}}}}

response_message should be short and conversational either way - what you did (for code_edit) or the direct question/refusal (for clarify/refuse). For "clarify" specifically, an assembly step downstream may replace response_message using your per-category neutral_question fields instead - still write a reasonable response_message here as a fallback, but the per-category neutral_question fields are what actually matters.
```

In `backend/app/services/llm_service.py`, add `from . import round3_construct_engine` to the imports, then replace `round3_coding_turn`:

```python
def round3_coding_turn(
    scenario_description: str,
    language: str,
    conversation_so_far: list[dict],
    current_code: str | None,
    candidate_prompt: str,
    turn_number: int,
    required_constructs: list[str] | None = None,
    declared_constructs: dict | None = None,
) -> dict:
    required_constructs = required_constructs or []
    declared_constructs = declared_constructs or {}
    open_categories = [c for c in required_constructs if c not in declared_constructs]

    def _raw_turn(regeneration_note: str = "") -> Round3CodingTurnResponse:
        prompt = _load_prompt("round3_coding_turn.txt").format(
            scenario_description=scenario_description,
            language=language,
            conversation_so_far=json.dumps(conversation_so_far, indent=2),
            current_code=current_code or "(no code written yet)",
            candidate_prompt=candidate_prompt,
            turn_number=turn_number,
            is_first_turn="true" if turn_number == 1 else "false",
            open_categories=json.dumps(open_categories),
            declared_constructs=json.dumps(declared_constructs, indent=2),
            regeneration_note=regeneration_note,
        )
        raw = _call_claude(prompt, max_tokens=2048)
        result = _parse_json_response(raw)
        if not isinstance(result, dict):
            raise ValueError(f"Expected a JSON object for the assistant's turn, got: {type(result)}")
        try:
            return Round3CodingTurnResponse.model_validate(result)
        except ValidationError as e:
            raise ValueError(f"Assistant's turn response didn't match the expected shape: {e}") from e

    parsed = _raw_turn()

    if parsed.response_kind == "refuse" or not open_categories:
        return {
            "response_kind": parsed.response_kind,
            "response_message": parsed.response_message,
            "code_after": parsed.code_after,
            "declared_constructs": declared_constructs,
        }

    category_status = {k: v.model_dump() for k, v in parsed.category_status.items()}
    decision = round3_construct_engine.decide(category_status, declared_constructs, required_constructs)

    if decision.final_kind == "proceed":
        return {
            "response_kind": "code_edit",
            "response_message": parsed.response_message,
            "code_after": parsed.code_after,
            "declared_constructs": decision.updated_state,
        }

    # clarify: check for vocabulary leaks, regenerate once, then fall back.
    leaked = round3_construct_engine.leaking_categories(decision.ask_categories, category_status, language)
    if leaked:
        note = (
            "Your last attempt named the very construct you were testing for in your "
            f"question about: {', '.join(leaked)}. Rephrase those specific questions "
            "without naming the concept, technique, or vocabulary at all - describe "
            "only the underlying need."
        )
        retry = _raw_turn(regeneration_note=note)
        retry_status = {k: v.model_dump() for k, v in retry.category_status.items()}
        for category in leaked:
            if category in retry_status:
                category_status[category] = retry_status[category]
        leaked = round3_construct_engine.leaking_categories(decision.ask_categories, category_status, language)

    message = round3_construct_engine.assemble_message(decision.ask_categories, category_status, set(leaked))
    return {
        "response_kind": "clarify",
        "response_message": message,
        "code_after": None,
        "declared_constructs": decision.updated_state,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_llm_service_round3_coding.py -v`
Expected: PASS (all tests, including the 3 pre-existing ones — required_constructs/declared_constructs default to `[]`/`{}`, so `open_categories` is empty and the short-circuit returns the model's raw output unchanged)

- [ ] **Step 5: Commit**

```bash
git add backend/app/prompts/round3_coding_turn.txt backend/app/services/llm_service.py tests/test_llm_service_round3_coding.py
git commit -m "feat: gate Round 3 code_edit turns on the construct checklist, mechanically"
```

---

### Task 7: Wire the router — pass the checklist in, persist the declared state

**Files:**
- Modify: `backend/app/routers/candidate.py:291-332` (`round3_coding_turn`)
- Test: `tests/test_round3.py`

**Interfaces:**
- Consumes: `llm_service.round3_coding_turn(..., required_constructs, declared_constructs)` returning a dict with `"declared_constructs"` (Task 6), `Round3Turn.declared_constructs_json` (Task 4), `scenario.reference_json["required_constructs"]` (Task 5).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_round3.py` (after `test_round3_coding_current_code_threads_between_turns`):

```python
def test_round3_coding_declared_constructs_persist_and_thread_between_turns(client, monkeypatch):
    """Verifies THIS APP's own code correctly reads the prior turn's
    declared_constructs_json and threads it into the next LLM call - not
    a claim about what the (mocked) LLM does with it, which is
    llm_service's own concern (see test_llm_service_round3_coding.py)."""
    from app.services import llm_service
    from .conftest import CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1", band="7+")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="R2", band="7+")

    scenario = _create_draft_round3_scenario(client, hr_token, monkeypatch, title="Find the highest salary")
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

    captured_first = {}

    def fake_turn_1(**kwargs):
        captured_first.update(kwargs)
        return {
            "response_kind": "clarify", "response_message": "How do you want to represent that information?",
            "code_after": None, "declared_constructs": {"collection": "a list called salaries"},
        }

    monkeypatch.setattr(llm_service, "round3_coding_turn", fake_turn_1)
    client.post("/candidate/round/3/turn", json={"candidate_prompt": "I need to store the salaries"}, cookies=_auth(cand_token))
    assert captured_first["required_constructs"] == ["collection", "iteration"]
    assert captured_first["declared_constructs"] == {}

    captured_second = {}

    def fake_turn_2(**kwargs):
        captured_second.update(kwargs)
        return {
            "response_kind": "code_edit", "response_message": "Added the loop.",
            "code_after": "for s in salaries: print(s)",
            "declared_constructs": {"collection": "a list called salaries", "iteration": "a for loop"},
        }

    monkeypatch.setattr(llm_service, "round3_coding_turn", fake_turn_2)
    client.post("/candidate/round/3/turn", json={"candidate_prompt": "loop through them"}, cookies=_auth(cand_token))
    # The previous turn's declared state is read back and threaded into
    # the next call - the candidate never has to repeat "a list called
    # salaries" for it to still count as settled.
    assert captured_second["declared_constructs"] == {"collection": "a list called salaries"}

    state = client.get("/candidate/round/3/state", cookies=_auth(cand_token)).json()
    assert len(state["turns"]) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_round3.py -v -k declared_constructs_persist`
Expected: FAIL — `KeyError: 'required_constructs'` on the `captured_first["required_constructs"]` assertion. The mock is `fake_turn_1(**kwargs)`, which accepts any kwargs silently; the router (not yet updated) simply never passes `required_constructs`/`declared_constructs` at all, so the key is missing from `captured_first` rather than the call raising.

- [ ] **Step 3: Wire the router**

In `backend/app/routers/candidate.py`, replace the `round3_coding_turn` handler (currently at line 291) with:

```python
@router.post("/round/3/turn", response_model=Round3TurnOut, status_code=201)
def round3_coding_turn(payload: Round3TurnCreate, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(3, db, candidate)
    scenario, submission = _round3_coding_scenario_and_submission(candidate, db)
    if submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round has already been submitted.")

    language = (submission.content or {}).get("language")
    existing_turns = submission.round3_turns
    conversation_so_far = [t.to_conversation_payload() for t in existing_turns]
    current_code = next((t.code_after for t in reversed(existing_turns) if t.code_after), None)
    turn_number = len(existing_turns) + 1
    required_constructs = (scenario.reference_json or {}).get("required_constructs", [])
    # Every turn writes this (see models.Round3Turn.declared_constructs_json)
    # - `or {}` only guards a row written before this column existed.
    declared_constructs = (existing_turns[-1].declared_constructs_json if existing_turns else None) or {}

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
            required_constructs=required_constructs,
            declared_constructs=declared_constructs,
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
        declared_constructs_json=response.get("declared_constructs", declared_constructs),
    )
    db.add(turn)
    submission.content = {**(submission.content or {}), "draft_prompt": ""}
    db.commit()
    db.refresh(turn)
    return turn
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_round3.py -v`
Expected: PASS (all tests, including every pre-existing one — none of them set `required_constructs`, so it defaults to `[]` from `reference_json.get(...)` and behavior is unchanged)

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/candidate.py tests/test_round3.py
git commit -m "feat: thread Round 3's construct checklist through the turn endpoint"
```

---

### Task 8: Full end-to-end test of the real orchestration

**Files:**
- Test: `tests/test_round3.py`

**Interfaces:**
- Consumes: everything from Tasks 1–7, exercised together through the HTTP layer with only `_call_claude` mocked (not `llm_service.round3_coding_turn` itself), proving the whole stack wires together.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_round3.py`:

```python
def test_round3_coding_full_construct_checklist_flow_end_to_end(client, monkeypatch):
    """Drives the REAL llm_service.round3_coding_turn orchestration (only
    _call_claude is mocked, not round3_coding_turn itself) through a
    multi-turn conversation with a required_constructs checklist: a goal
    statement gets a single-category probe, a bundled instruction with
    two gaps gets asked about both together, and once everything is
    declared the turn finally produces code. Proves the router, the
    engine, the schema, and persistence all wire together correctly, not
    just each piece in isolation."""
    import json as json_module
    from app.services import llm_service
    from .conftest import CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1", band="7+")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="R2", band="7+")

    scenario = _create_draft_round3_scenario(client, hr_token, monkeypatch, title="Find the highest salary")
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

    responses = [
        # Turn 1: a goal statement, nothing attempted.
        {
            "response_kind": "clarify", "response_message": "(unused - assembled from category_status)", "code_after": None,
            "category_status": {"collection": {"status": "not_addressed", "neutral_question": "What information do you need to store first?"}},
        },
        # Turn 2: a bundled instruction leaving two gaps.
        {
            "response_kind": "clarify", "response_message": "(unused)", "code_after": None,
            "category_status": {
                "collection": {"status": "declared", "value": "a list called salaries"},
                "iteration": {"status": "attempted_but_vague", "neutral_question": "How will your program work through them, one at a time?"},
                "comparison": {"status": "attempted_but_vague", "neutral_question": "What should determine whether one value replaces another?"},
            },
        },
        # Turn 3: resolves both remaining gaps - everything is now
        # declared, so the engine allows code_edit.
        {
            "response_kind": "code_edit", "response_message": "Added the loop and the comparison.",
            "code_after": "highest = None\nfor s in salaries:\n    if highest is None or s > highest:\n        highest = s\nprint(highest)",
            "category_status": {
                "iteration": {"status": "declared", "value": "a for loop over salaries"},
                "comparison": {"status": "declared", "value": "greater than the current highest"},
            },
        },
    ]

    def fake_call_claude(prompt, max_tokens=4096):
        return json_module.dumps(responses.pop(0))

    monkeypatch.setattr(llm_service, "_call_claude", fake_call_claude)

    r1 = client.post("/candidate/round/3/turn", json={"candidate_prompt": "I need to find the highest salary"}, cookies=_auth(cand_token))
    assert r1.json()["response_kind"] == "clarify"
    assert "What information do you need to store first?" in r1.json()["response_message"]

    r2 = client.post("/candidate/round/3/turn", json={"candidate_prompt": "store the salaries in a list, loop through them, and compare each to the current highest"}, cookies=_auth(cand_token))
    assert r2.json()["response_kind"] == "clarify"
    assert "How will your program work through them, one at a time?" in r2.json()["response_message"]
    assert "What should determine whether one value replaces another?" in r2.json()["response_message"]

    r3 = client.post("/candidate/round/3/turn", json={"candidate_prompt": "use a for loop, and if a salary is greater than the current highest, replace it"}, cookies=_auth(cand_token))
    assert r3.json()["response_kind"] == "code_edit"
    assert "highest = None" in r3.json()["code_after"]

    state = client.get("/candidate/round/3/state", cookies=_auth(cand_token)).json()
    assert len(state["turns"]) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_round3.py -v -k full_construct_checklist_flow`
Expected: at this point in the plan it should actually PASS already, since Tasks 1–7 are complete — run it to confirm; if it fails, the failure output identifies exactly which earlier task's wiring is incomplete (a genuinely useful integration check rather than a step that's expected to fail).

- [ ] **Step 3: (only if Step 2 failed) Fix the identified gap**

Diagnose from the failure — likely causes are a mismatch between what a task's step 3 actually implemented and what an earlier step assumed (e.g. a typo in a dict key). Fix in the file the traceback points to, matching the exact interfaces already defined in Tasks 1–7's "Interfaces" blocks above.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_round3.py -v`
Expected: PASS (every test in the file, old and new)

Also run the full suite once to confirm nothing elsewhere regressed: `pytest -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_round3.py
git commit -m "test: full end-to-end coverage of Round 3's construct-checklist flow"
```

---

### Task 9: Language-lock instructional copy

**Files:**
- Modify: `backend/app/static/app.js:2354-2398` (`showRound3CodingIntro`, `confirmRound3CodingIntro`)

**Interfaces:**
- None — pure copy addition, no new function, no test needed (matches this repo's own convention of not testing static copy strings).

- [ ] **Step 1: Add the intro-modal bullet**

In `backend/app/static/app.js`, in `showRound3CodingIntro`, replace:

```js
        <li>Next, you'll pick your language - your timer starts the moment you start from there.</li>
```

with:

```js
        <li>Next, you'll pick your language - your timer starts the moment you start from there. This choice is final for the whole round: once you start, you can't switch languages.</li>
```

- [ ] **Step 2: Add the note next to the language picker itself**

In the same file, in `confirmRound3CodingIntro`, replace:

```js
    <div class="field-row" style="align-items:center">
      <span class="muted">Language</span>
      <select id="round3-language-select">
        <option value="python">Python</option>
        <option value="java">Java</option>
        <option value="javascript">JavaScript</option>
      </select>
    </div>
```

with:

```js
    <div class="field-row" style="align-items:center">
      <span class="muted">Language</span>
      <select id="round3-language-select">
        <option value="python">Python</option>
        <option value="java">Java</option>
        <option value="javascript">JavaScript</option>
      </select>
    </div>
    <p class="muted">This is a one-time choice - you won't be able to change it once the round starts.</p>
```

- [ ] **Step 3: Verify in the browser**

Run the app (`run_server.bat`, or `uvicorn app.main:app --reload` from `backend/`), log in as a candidate, open Round 3, and confirm: the intro modal's last bullet mentions the language choice being final, and the note appears directly under the language dropdown before clicking "Start Round 3".

- [ ] **Step 4: Commit**

```bash
git add backend/app/static/app.js
git commit -m "docs: tell candidates the Round 3 language choice is final"
```

---

## Post-plan verification

Run the full test suite once more after all 9 tasks: `pytest -v`
Expected: PASS, with the new counts — `test_round3_constructs.py` (8), `test_round3_construct_engine.py` (9), `test_llm_service_round3_coding.py` (grown from 5 to 16), `test_round3.py` (grown by 2) — alongside every pre-existing test in the suite, unchanged.
