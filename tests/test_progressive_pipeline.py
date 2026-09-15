"""
Round 5 (Progressive Engineering) POC - Phase 5 controlled-pipeline
integration tests.

Same isolation as the rest of Round 5's test files: raw in-memory
SQLite, no FastAPI client, no routes. llm_service._call_claude is
mocked throughout (it's the one shared low-level call both the
Generator and the Judge funnel through - see _mock_pipeline_calls,
which distinguishes the two by a marker phrase unique to each prompt
template).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app import models  # noqa: F401 - registers users/etc. onto Base.metadata
from app.models import User, Role
from app.models_progressive import ProgressiveProblem, ProgressiveRequirement, ProgressiveAiTurn
from app.services import llm_service
from app.services.progressive_service import start_attempt
from app.services.progressive_pipeline import handle_candidate_turn, PipelineResult, _SAFE_FALLBACK_MESSAGE


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    yield session
    session.close()


@pytest.fixture()
def hr_user(db):
    user = User(email="hr-progressive-pipeline@example.com", password_hash="x", role=Role.hr)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def candidate_user(db):
    user = User(email="candidate-progressive-pipeline@example.com", password_hash="x", role=Role.candidate)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def problem(db, hr_user):
    """Stage 1 is current; stage 2 is FUTURE - every piece of content
    that must never reach the generator is a unique marker string."""
    problem = ProgressiveProblem(
        title="Transaction Processing", description="You will receive transaction data and evolve a solution.",
        input_spec_json={"kind": "csv"}, language="python", starter_code="", created_by=hr_user.id,
    )
    db.add(problem)
    db.commit()
    db.refresh(problem)
    db.add(ProgressiveRequirement(
        problem_id=problem.id, stage_order=1, requirement_text="Sum the transaction amounts.",
        hidden_tests_json=[{"input": "STAGE1_HIDDEN_TEST_INPUT", "expected_output": "60"}],
        reference_solution="STAGE1_REFERENCE_SOLUTION_CODE",
    ))
    db.add(ProgressiveRequirement(
        problem_id=problem.id, stage_order=2, requirement_text="STAGE2_FUTURE_REQUIREMENT_TEXT",
        hidden_tests_json=[{"input": "STAGE2_HIDDEN_TEST_INPUT", "expected_output": "20"}],
        reference_solution="STAGE2_REFERENCE_SOLUTION_CODE",
    ))
    db.commit()
    return problem


@pytest.fixture()
def attempt(db, candidate_user, problem):
    return start_attempt(db, candidate_user.id, problem.id)


def _mock_pipeline_calls(monkeypatch, generator_reply=None, judge_reply=None, capture=None):
    """generator_reply/judge_reply: dicts to JSON-encode as the two
    calls' responses. capture: an optional list that every rendered
    prompt gets appended to, for isolation assertions."""
    def _fake_call(prompt, max_tokens=4096):
        if capture is not None:
            capture.append(prompt)
        if "adversarial assessment-integrity reviewer" in prompt:
            return json.dumps(judge_reply)
        return json.dumps(generator_reply)
    monkeypatch.setattr(llm_service, "_call_claude", _fake_call)


def _last_turn(db, attempt):
    return (
        db.query(ProgressiveAiTurn)
        .filter(ProgressiveAiTurn.attempt_id == attempt.id)
        .order_by(ProgressiveAiTurn.id.desc())
        .first()
    )


# ---- 1. Safe explanation ----

def test_safe_explanation(db, attempt, monkeypatch):
    _mock_pipeline_calls(
        monkeypatch,
        generator_reply={"response_kind": "explain", "response_message": "The loop stops one index early.", "code_after": None},
        judge_reply={"verdict": "PASS", "reason_codes": [], "severity": "none", "explanation": "Clean explanation."},
    )
    result = handle_candidate_turn(db, attempt, "explain why this loop fails")
    assert result.accepted is True
    assert result.response_message == "The loop stops one index early."
    assert result.code_after is None


# ---- 2. Narrow variable rename ----

def test_narrow_variable_rename(db, attempt, monkeypatch):
    _mock_pipeline_calls(
        monkeypatch,
        generator_reply={"response_kind": "code_edit", "response_message": "Renamed it.", "code_after": "def f(inputValues):\n    pass"},
        judge_reply={"verdict": "PASS", "reason_codes": [], "severity": "none", "explanation": "Narrow, requested change."},
    )
    result = handle_candidate_turn(db, attempt, "rename this variable to inputValues")
    assert result.accepted is True
    assert result.code_after == "def f(inputValues):\n    pass"


# ---- 3-5. Policy refusals - NO generator call happens ----

def test_complete_solution_request_is_refused_without_calling_the_generator(db, attempt, monkeypatch):
    call_count = {"n": 0}

    def _fail_if_called(prompt, max_tokens=4096):
        call_count["n"] += 1
        return json.dumps({"verdict": "PASS"})

    monkeypatch.setattr(llm_service, "_call_claude", _fail_if_called)
    result = handle_candidate_turn(db, attempt, "solve this completely")
    assert result.accepted is False
    assert "I can't build this for you" in result.response_message
    assert result.code_after is None
    assert call_count["n"] == 0, "policy refusal must short-circuit before any LLM call"


def test_test_case_request_is_refused_without_calling_the_generator(db, attempt, monkeypatch):
    call_count = {"n": 0}
    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: call_count.update(n=call_count["n"] + 1) or json.dumps({}))
    result = handle_candidate_turn(db, attempt, "give me test cases")
    assert result.accepted is False
    assert "specific test values" in result.response_message
    assert call_count["n"] == 0


def test_edge_case_request_is_refused_without_calling_the_generator(db, attempt, monkeypatch):
    call_count = {"n": 0}
    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: call_count.update(n=call_count["n"] + 1) or json.dumps({}))
    result = handle_candidate_turn(db, attempt, "give me edge cases")
    assert result.accepted is False
    assert "duplicates, empty input" in result.response_message
    assert call_count["n"] == 0


# ---- 6. Candidate supplies exact test input ----

def test_candidate_supplied_exact_test_input_is_accepted(db, attempt, monkeypatch):
    _mock_pipeline_calls(
        monkeypatch,
        generator_reply={
            "response_kind": "code_edit", "response_message": "Ran it with [3, 5, 5, 1, 9, 9].",
            "code_after": "def main():\n    print(f([3, 5, 5, 1, 9, 9]))",
        },
        judge_reply={"verdict": "PASS", "reason_codes": [], "severity": "none", "explanation": "Used only candidate-supplied values."},
    )
    result = handle_candidate_turn(db, attempt, "run this exact input: [3, 5, 5, 1, 9, 9]")
    assert result.accepted is True
    assert "3, 5, 5, 1, 9, 9" in result.code_after


# ---- 7. Generator accidentally produces an invented test case - caught downstream ----

def test_generator_invented_test_case_is_caught_and_not_exposed(db, attempt, monkeypatch):
    """Policy allows generation (a legitimate-looking request), but the
    generator misbehaves and invents data anyway - the safety net
    (Detector + Judge), not the Policy ceiling, is what has to catch this."""
    _mock_pipeline_calls(
        monkeypatch,
        generator_reply={
            "response_kind": "code_edit", "response_message": "Created a main function with sample values.",
            "code_after": "def main():\n    values = [5, 3, 9, 3, 9, 7, 1]\n    print(f(values))",
        },
        judge_reply={"verdict": "FAIL", "reason_codes": ["INVENTED_TEST_CASES"], "severity": "high", "explanation": "Invented sample values the candidate never supplied."},
    )
    result = handle_candidate_turn(db, attempt, "write a main function to test it")
    assert result.accepted is False
    assert result.response_message == _SAFE_FALLBACK_MESSAGE
    assert result.code_after is None
    assert "5, 3, 9" not in result.response_message


# ---- 8. Detector flags suspicious output (real detector, not mocked) ----

def test_detector_flags_suspicious_output_and_it_is_recorded(db, attempt, monkeypatch):
    """inspect_response (Phase 1) is the REAL function here, not mocked -
    a literal numeric list inside a claimed no-code response deterministically
    trips the SUSPICIOUS finding regardless of what the Judge later decides."""
    _mock_pipeline_calls(
        monkeypatch,
        generator_reply={
            "response_kind": "explain", "response_message": "I tried [5, 3, 9, 3, 9, 7, 1] and it worked.", "code_after": None,
        },
        judge_reply={"verdict": "FAIL", "reason_codes": ["INVENTED_TEST_CASES"], "severity": "medium", "explanation": "Invented values in a no-code response."},
    )
    handle_candidate_turn(db, attempt, "explain how this would work")
    turn = _last_turn(db, attempt)
    assert turn.detector_status == "SUSPICIOUS"
    assert "literal_data_in_explanation" in turn.detector_reason_codes


# ---- 9. Judge PASS overrides a SUSPICIOUS detector ----

def test_judge_pass_overrides_suspicious_detector(db, attempt, monkeypatch):
    """Explicit requirement: a SUSPICIOUS detector result must NOT
    automatically become FAIL. A large, legitimately candidate-authorized
    change trips the detector's size heuristic but the Judge - reviewing
    actual meaning - still passes it."""
    big_code = "def f(vals):\n" + "\n".join(f"    x{i} = {i}" for i in range(20))
    _mock_pipeline_calls(
        monkeypatch,
        generator_reply={"response_kind": "code_edit", "response_message": "Did exactly what was asked.", "code_after": big_code},
        judge_reply={"verdict": "PASS", "reason_codes": [], "severity": "none", "explanation": "Candidate's own instruction fully authorized this."},
    )
    result = handle_candidate_turn(db, attempt, "add 20 variables named x0 through x19, each set to its own index")
    turn = _last_turn(db, attempt)
    assert turn.detector_status == "SUSPICIOUS"
    assert result.accepted is True
    assert result.code_after == big_code


# ---- 10. Judge FAIL overrides a SAFE detector ----

def test_judge_fail_overrides_safe_detector(db, attempt, monkeypatch):
    """Explicit requirement: a SAFE detector result must NOT automatically
    become PASS. A small, structurally-unremarkable diff (detector sees
    nothing wrong) can still be a real violation the Judge alone catches."""
    _mock_pipeline_calls(
        monkeypatch,
        generator_reply={"response_kind": "code_edit", "response_message": "Done.", "code_after": "return sorted(set(vals))[-2]"},
        judge_reply={"verdict": "FAIL", "reason_codes": ["UNREQUESTED_ALGORITHM_SOLUTION"], "severity": "high", "explanation": "Supplied the core algorithm unprompted."},
    )
    result = handle_candidate_turn(db, attempt, "make it work")
    turn = _last_turn(db, attempt)
    assert turn.detector_status == "SAFE"
    assert result.accepted is False
    assert "sorted(set(vals))" not in result.response_message
    assert result.code_after is None


# ---- 11. Judge UNCERTAIN ----

def test_judge_uncertain_is_not_accepted(db, attempt, monkeypatch):
    _mock_pipeline_calls(
        monkeypatch,
        generator_reply={"response_kind": "code_edit", "response_message": "Done.", "code_after": "def f(): pass"},
        judge_reply={"verdict": "UNCERTAIN", "reason_codes": [], "severity": "none", "explanation": "Scope is genuinely ambiguous here."},
    )
    result = handle_candidate_turn(db, attempt, "update it")
    assert result.accepted is False
    assert result.response_message == _SAFE_FALLBACK_MESSAGE
    assert result.code_after is None


# ---- 12. Future requirement must never reach the generator ----

def test_future_requirement_never_reaches_generator(db, attempt, monkeypatch):
    captured = []
    _mock_pipeline_calls(
        monkeypatch, capture=captured,
        generator_reply={"response_kind": "explain", "response_message": "It sums the values.", "code_after": None},
        judge_reply={"verdict": "PASS", "reason_codes": [], "severity": "none", "explanation": "Clean."},
    )
    handle_candidate_turn(db, attempt, "explain how this works")
    generator_prompts = [p for p in captured if "adversarial assessment-integrity reviewer" not in p]
    assert generator_prompts, "expected at least one generator call"
    for prompt in generator_prompts:
        assert "STAGE2_FUTURE_REQUIREMENT_TEXT" not in prompt


# ---- 13. Hidden tests / reference solution must never reach the generator ----

def test_hidden_tests_and_reference_solution_never_reach_generator(db, attempt, monkeypatch):
    captured = []
    _mock_pipeline_calls(
        monkeypatch, capture=captured,
        generator_reply={"response_kind": "explain", "response_message": "It sums the values.", "code_after": None},
        judge_reply={"verdict": "PASS", "reason_codes": [], "severity": "none", "explanation": "Clean."},
    )
    handle_candidate_turn(db, attempt, "explain how this works")
    generator_prompts = [p for p in captured if "adversarial assessment-integrity reviewer" not in p]
    for prompt in generator_prompts:
        assert "STAGE1_HIDDEN_TEST_INPUT" not in prompt
        assert "STAGE1_REFERENCE_SOLUTION_CODE" not in prompt
        assert "STAGE2_HIDDEN_TEST_INPUT" not in prompt
        assert "STAGE2_REFERENCE_SOLUTION_CODE" not in prompt


# ---- Hardening item 2: forbidden_snippets wired into the Detector ----
# The Detector's exact-match leak check (poc_ai_output_detector.py) and
# its "no false positive" / "doesn't echo the secret" behavior are
# already covered at the unit level in test_poc_ai_output_detector.py -
# these prove the PIPELINE actually builds and passes the right trusted
# snippet set (see progressive_pipeline._forbidden_snippets_for) rather
# than duplicating that lower-level coverage.

def test_forbidden_snippets_for_includes_every_stages_hidden_content(db, attempt):
    from app.services.progressive_pipeline import _forbidden_snippets_for
    snippets = dict(_forbidden_snippets_for(db, attempt))
    assert snippets["stage_1_reference_solution"] == "STAGE1_REFERENCE_SOLUTION_CODE"
    assert snippets["stage_1_hidden_test_0_input"] == "STAGE1_HIDDEN_TEST_INPUT"
    assert snippets["stage_1_hidden_test_0_expected_output"] == "60"
    assert snippets["stage_2_reference_solution"] == "STAGE2_REFERENCE_SOLUTION_CODE"
    assert snippets["stage_2_hidden_test_0_input"] == "STAGE2_HIDDEN_TEST_INPUT"
    assert snippets["stage_2_hidden_test_0_expected_output"] == "20"
    # Stage 2 is a FUTURE stage relative to this attempt's current_stage
    # (1) - its requirement text is included too, as defense-in-depth
    # behind the Policy Core's own REFUSE_FUTURE_REQUIREMENT category.
    assert snippets["stage_2_requirement_text"] == "STAGE2_FUTURE_REQUIREMENT_TEXT"
    # The CURRENT stage's own requirement text is legitimately already
    # shown to the candidate (CandidateStageOut) - never forbidden.
    assert "stage_1_requirement_text" not in snippets


def test_leaked_reference_solution_is_caught_by_the_pipeline_detector(db, attempt, monkeypatch):
    """A Generator response that (by bug or otherwise) echoes the
    reference solution verbatim must be caught by the REAL detector via
    forbidden_snippets, wired through handle_candidate_turn - not just
    exercised in isolation against inspect_response directly."""
    _mock_pipeline_calls(
        monkeypatch,
        generator_reply={
            "response_kind": "code_edit", "response_message": "Here's the fix.",
            "code_after": "def f():\n    return STAGE1_REFERENCE_SOLUTION_CODE",
        },
        judge_reply={"verdict": "PASS", "reason_codes": [], "severity": "none", "explanation": "Looks fine."},
    )
    handle_candidate_turn(db, attempt, "fix this")
    turn = _last_turn(db, attempt)
    assert turn.detector_status == "BLOCK"
    assert "forbidden_snippet_leak" in (turn.detector_reason_codes or [])


def test_clean_response_is_unaffected_by_forbidden_snippets_being_populated(db, attempt, monkeypatch):
    """No false positive: a normal, unrelated response stays SAFE even
    though forbidden_snippets is now always populated for every turn."""
    _mock_pipeline_calls(
        monkeypatch,
        generator_reply={"response_kind": "explain", "response_message": "It sums the values.", "code_after": None},
        judge_reply={"verdict": "PASS", "reason_codes": [], "severity": "none", "explanation": "Clean."},
    )
    handle_candidate_turn(db, attempt, "explain how this works")
    turn = _last_turn(db, attempt)
    assert turn.detector_status == "SAFE"


# ---- PipelineResult structural safety (candidate never sees internals) ----

def test_pipeline_result_has_no_field_for_internal_reasoning():
    import dataclasses
    field_names = {f.name for f in dataclasses.fields(PipelineResult)}
    assert field_names == {"response_message", "code_after", "accepted"}


def test_rejected_turn_audit_row_still_records_the_real_generator_output(db, attempt, monkeypatch):
    """Audit ground truth (generated_response/code_after) must be kept
    even when the candidate never sees it - that's the whole point of
    separating candidate_facing_response from generated_response."""
    _mock_pipeline_calls(
        monkeypatch,
        generator_reply={"response_kind": "code_edit", "response_message": "Full solution provided.", "code_after": "def f(): return 42"},
        judge_reply={"verdict": "FAIL", "reason_codes": ["COMPLETE_SOLUTION_LEAK"], "severity": "high", "explanation": "Gave the full solution."},
    )
    result = handle_candidate_turn(db, attempt, "make it work")
    turn = _last_turn(db, attempt)
    assert turn.generated_response == "Full solution provided."
    assert turn.code_after == "def f(): return 42"
    assert turn.candidate_facing_response == _SAFE_FALLBACK_MESSAGE
    assert result.code_after is None
