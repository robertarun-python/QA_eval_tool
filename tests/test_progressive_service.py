"""
Round 5 (Progressive Engineering) POC - Phase 2 stage-lifecycle tests.

Isolated the same way test_progressive_models.py is: raw in-memory
SQLite via the ORM, no FastAPI client, no routes (none exist yet).
execution_service.run_code is monkeypatched throughout - "use mocked
execution where practical" per the task, and this module never modifies
execution_service.py itself.
"""
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
from app.models_progressive import ProgressiveProblem, ProgressiveRequirement, ProgressiveStageResult
from app.services import execution_service, progressive_service
from app.services.progressive_service import (
    start_attempt, get_current_requirement, get_current_code, submit_stage,
    stage_orders_missing_hidden_tests, validate_stage_compatibility,
    AttemptNotActiveError, StageOutOfOrderError, StageAlreadyCompletedError, UnknownStageError,
)


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
    user = User(email="hr-progressive-svc@example.com", password_hash="x", role=Role.hr)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def candidate_user(db):
    user = User(email="candidate-progressive-svc@example.com", password_hash="x", role=Role.candidate)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def three_stage_problem(db, hr_user):
    """Sum -> average -> top-N, the exact example from the architecture
    discussion - three stages, each with one hidden test tagged to that
    stage's own expected behavior."""
    problem = ProgressiveProblem(
        title="Transaction Totals", description="Process a batch of transaction amounts.",
        input_spec_json={"kind": "csv", "filename": "transactions.csv"}, language="python",
        starter_code="def process(amounts):\n    pass", created_by=hr_user.id,
    )
    db.add(problem)
    db.commit()
    db.refresh(problem)

    db.add(ProgressiveRequirement(
        problem_id=problem.id, stage_order=1, requirement_text="Sum the amounts.",
        hidden_tests_json=[{"input": "sum", "expected_output": "60"}],
    ))
    db.add(ProgressiveRequirement(
        problem_id=problem.id, stage_order=2, requirement_text="Now compute the average.",
        hidden_tests_json=[{"input": "avg", "expected_output": "20"}],
    ))
    db.add(ProgressiveRequirement(
        problem_id=problem.id, stage_order=3, requirement_text="Now find the top value.",
        hidden_tests_json=[{"input": "top", "expected_output": "30"}],
    ))
    db.commit()
    return problem


def _mock_run_code(monkeypatch, output_map: dict, infra_error_for: set = frozenset()):
    """output_map: {stdin_value: stdout_value}. Anything not in the map
    returns a deliberately wrong output, so an untested input never
    accidentally "passes"."""
    def _fake(language, code, stdin):
        key = stdin[0]
        if key in infra_error_for:
            return execution_service.ExecutionResult(stdout="", stderr="", exit_code=None, timed_out=False, infra_error=True)
        return execution_service.ExecutionResult(
            stdout=output_map.get(key, "WRONG"), stderr="", exit_code=0, timed_out=False, infra_error=False,
        )
    monkeypatch.setattr(progressive_service.execution_service, "run_code", _fake)


# ---- 1. start attempt ----

def test_start_attempt(db, hr_user, candidate_user, three_stage_problem):
    attempt = start_attempt(db, candidate_user.id, three_stage_problem.id)
    assert attempt.current_stage == 1
    assert attempt.archived is False
    assert attempt.completed_at is None


def test_start_attempt_is_idempotent(db, candidate_user, three_stage_problem):
    first = start_attempt(db, candidate_user.id, three_stage_problem.id)
    second = start_attempt(db, candidate_user.id, three_stage_problem.id)
    assert first.id == second.id


# ---- current requirement / current code ----

def test_current_requirement_is_only_stage_1_at_start(db, candidate_user, three_stage_problem):
    attempt = start_attempt(db, candidate_user.id, three_stage_problem.id)
    requirement = get_current_requirement(db, attempt)
    assert requirement.stage_order == 1
    assert requirement.requirement_text == "Sum the amounts."


def test_current_code_falls_back_to_starter_code_before_any_submission(db, candidate_user, three_stage_problem):
    attempt = start_attempt(db, candidate_user.id, three_stage_problem.id)
    assert get_current_code(db, attempt) == "def process(amounts):\n    pass"


# ---- 2. stage 1 submission ----

def test_stage_1_submission(db, candidate_user, three_stage_problem, monkeypatch):
    attempt = start_attempt(db, candidate_user.id, three_stage_problem.id)
    _mock_run_code(monkeypatch, {"sum": "60"})

    result = submit_stage(db, attempt, 1, "def process(amounts):\n    return sum(amounts)")
    assert result.passed is True
    assert result.stage_order == 1
    assert len(result.test_results_json) == 1
    assert result.test_results_json[0]["passed"] is True


# ---- 3. stage unlock ----

def test_stage_unlocks_after_submission(db, candidate_user, three_stage_problem, monkeypatch):
    attempt = start_attempt(db, candidate_user.id, three_stage_problem.id)
    _mock_run_code(monkeypatch, {"sum": "60"})
    submit_stage(db, attempt, 1, "def process(amounts):\n    return sum(amounts)")
    assert attempt.current_stage == 2


def test_stage_unlocks_even_with_a_failing_result(db, candidate_user, three_stage_problem, monkeypatch):
    """Do NOT require 100% correctness to unlock the next stage - per
    explicit product direction."""
    attempt = start_attempt(db, candidate_user.id, three_stage_problem.id)
    _mock_run_code(monkeypatch, {})  # nothing matches -> "WRONG" -> fails
    result = submit_stage(db, attempt, 1, "def process(amounts):\n    return 0")
    assert result.passed is False
    assert attempt.current_stage == 2


# ---- 4. stage 2 submission ----

def test_stage_2_submission_runs_cumulative_tests(db, candidate_user, three_stage_problem, monkeypatch):
    attempt = start_attempt(db, candidate_user.id, three_stage_problem.id)
    _mock_run_code(monkeypatch, {"sum": "60"})
    submit_stage(db, attempt, 1, "def process(amounts):\n    return sum(amounts)")

    _mock_run_code(monkeypatch, {"sum": "60", "avg": "20"})
    result = submit_stage(db, attempt, 2, "def process(amounts):\n    ...")
    assert result.stage_order == 2
    assert {r["stage_order"] for r in result.test_results_json} == {1, 2}
    assert result.passed is True


# ---- 5. cumulative regression detection ----

def test_cumulative_regression_is_detected(db, candidate_user, three_stage_problem, monkeypatch):
    """The actual proof of the regression-protection mechanism: Stage 2's
    code breaks Stage 1's behavior, and that must be visible in Stage 2's
    OWN result, immediately - not silently passed."""
    attempt = start_attempt(db, candidate_user.id, three_stage_problem.id)
    _mock_run_code(monkeypatch, {"sum": "60"})
    submit_stage(db, attempt, 1, "def process(amounts):\n    return sum(amounts)")

    # Stage 2's new code now gets "avg" right but "sum" wrong - a real regression.
    _mock_run_code(monkeypatch, {"avg": "20"})
    result = submit_stage(db, attempt, 2, "def process(amounts):\n    return average_only(amounts)")

    assert result.passed is False
    stage1_result = next(r for r in result.test_results_json if r["stage_order"] == 1)
    stage2_result = next(r for r in result.test_results_json if r["stage_order"] == 2)
    assert stage1_result["passed"] is False  # the regression
    assert stage2_result["passed"] is True   # the new behavior works on its own


# ---- 6. same code continuing between stages ----

def test_same_code_continues_between_stages(db, candidate_user, three_stage_problem, monkeypatch):
    attempt = start_attempt(db, candidate_user.id, three_stage_problem.id)
    _mock_run_code(monkeypatch, {"sum": "60"})
    submit_stage(db, attempt, 1, "def process(amounts):\n    return sum(amounts)  # stage 1")

    assert get_current_code(db, attempt) == "def process(amounts):\n    return sum(amounts)  # stage 1"


# ---- 7. snapshot immutability ----

def test_completed_stage_result_cannot_be_resubmitted(db, candidate_user, three_stage_problem, monkeypatch):
    attempt = start_attempt(db, candidate_user.id, three_stage_problem.id)
    _mock_run_code(monkeypatch, {"sum": "60"})
    submit_stage(db, attempt, 1, "v1")

    with pytest.raises(StageAlreadyCompletedError):
        submit_stage(db, attempt, 1, "v2 - trying to overwrite stage 1")

    # The original snapshot is untouched.
    stored = db.query(ProgressiveStageResult).filter_by(attempt_id=attempt.id, stage_order=1).one()
    assert stored.code_snapshot == "v1"


# ---- 8. final stage completion ----

def test_final_stage_completes_the_attempt(db, candidate_user, three_stage_problem, monkeypatch):
    attempt = start_attempt(db, candidate_user.id, three_stage_problem.id)
    _mock_run_code(monkeypatch, {"sum": "60"})
    submit_stage(db, attempt, 1, "v1")
    _mock_run_code(monkeypatch, {"sum": "60", "avg": "20"})
    submit_stage(db, attempt, 2, "v2")
    _mock_run_code(monkeypatch, {"sum": "60", "avg": "20", "top": "30"})
    submit_stage(db, attempt, 3, "v3")

    assert attempt.completed_at is not None
    assert attempt.status.value == "submitted"
    assert attempt.current_stage == 3  # never advances past the last real stage


def test_cannot_submit_after_attempt_completed(db, candidate_user, three_stage_problem, monkeypatch):
    attempt = start_attempt(db, candidate_user.id, three_stage_problem.id)
    _mock_run_code(monkeypatch, {"sum": "60", "avg": "20", "top": "30"})
    submit_stage(db, attempt, 1, "v1")
    submit_stage(db, attempt, 2, "v2")
    submit_stage(db, attempt, 3, "v3")

    with pytest.raises(AttemptNotActiveError):
        submit_stage(db, attempt, 3, "v4")
    with pytest.raises(AttemptNotActiveError):
        get_current_requirement(db, attempt)


# ---- 9. invalid stage submission ----

def test_submitting_a_nonexistent_stage_raises(db, candidate_user, three_stage_problem, monkeypatch):
    attempt = start_attempt(db, candidate_user.id, three_stage_problem.id)
    _mock_run_code(monkeypatch, {})
    with pytest.raises(StageOutOfOrderError):
        # stage 99 isn't even the current stage (1), so this is caught by
        # the ordering check before the "does stage 99 exist" check runs.
        submit_stage(db, attempt, 99, "code")


def test_submitting_a_stage_beyond_the_problems_last_stage_raises_unknown_stage(db, candidate_user, three_stage_problem, monkeypatch):
    """Distinct from the ordering check above: this reaches the
    "does this stage actually exist for the problem" guard specifically,
    by simulating a current_stage pointer that's run past what the
    problem actually defines."""
    attempt = start_attempt(db, candidate_user.id, three_stage_problem.id)
    attempt.current_stage = 99
    db.commit()
    _mock_run_code(monkeypatch, {})
    with pytest.raises(UnknownStageError):
        submit_stage(db, attempt, 99, "code")


# ---- 10. attempting to skip a stage ----

def test_skipping_ahead_to_stage_2_before_stage_1_is_rejected(db, candidate_user, three_stage_problem, monkeypatch):
    attempt = start_attempt(db, candidate_user.id, three_stage_problem.id)
    _mock_run_code(monkeypatch, {"avg": "20"})
    with pytest.raises(StageOutOfOrderError):
        submit_stage(db, attempt, 2, "code")


# ---- 11. archived attempt behaviour ----

def test_archived_attempt_rejects_submission(db, candidate_user, three_stage_problem, monkeypatch):
    attempt = start_attempt(db, candidate_user.id, three_stage_problem.id)
    attempt.archived = True
    db.commit()

    _mock_run_code(monkeypatch, {"sum": "60"})
    with pytest.raises(AttemptNotActiveError):
        submit_stage(db, attempt, 1, "code")


def test_archived_attempt_rejects_current_requirement_lookup(db, candidate_user, three_stage_problem):
    attempt = start_attempt(db, candidate_user.id, three_stage_problem.id)
    attempt.archived = True
    db.commit()

    with pytest.raises(AttemptNotActiveError):
        get_current_requirement(db, attempt)


def test_archived_attempt_does_not_block_starting_a_fresh_one(db, candidate_user, three_stage_problem):
    """Same convention as Submission.archived: an archived attempt is
    never "still active" for start_attempt's idempotency check - a
    genuinely new attempt is created instead."""
    first = start_attempt(db, candidate_user.id, three_stage_problem.id)
    first.archived = True
    db.commit()

    second = start_attempt(db, candidate_user.id, three_stage_problem.id)
    assert second.id != first.id
    assert second.archived is False


# ---- infra error handling inside cumulative tests ----

def test_execution_infra_error_is_recorded_not_silently_passed(db, candidate_user, three_stage_problem, monkeypatch):
    attempt = start_attempt(db, candidate_user.id, three_stage_problem.id)
    _mock_run_code(monkeypatch, {}, infra_error_for={"sum"})
    result = submit_stage(db, attempt, 1, "code")
    assert result.passed is False
    assert result.test_results_json[0]["passed"] is False
    assert "error" in result.test_results_json[0]


# ---- Hardening item 3: stage_orders_missing_hidden_tests ----
# Pure function - plain (unpersisted) ProgressiveRequirement objects are
# enough, no db/execution needed.

def test_stage_orders_missing_hidden_tests_flags_zero_tests():
    req = ProgressiveRequirement(stage_order=1, hidden_tests_json=[])
    assert stage_orders_missing_hidden_tests([req]) == [1]


def test_stage_orders_missing_hidden_tests_flags_missing_json():
    req = ProgressiveRequirement(stage_order=1, hidden_tests_json=None)
    assert stage_orders_missing_hidden_tests([req]) == [1]


def test_stage_orders_missing_hidden_tests_flags_malformed_entries():
    req = ProgressiveRequirement(
        stage_order=1,
        hidden_tests_json=[{"input": "x"}, {"expected_output": "60"}, "not even a dict", {"input": None, "expected_output": "60"}],
    )
    assert stage_orders_missing_hidden_tests([req]) == [1]


def test_stage_orders_missing_hidden_tests_allows_one_valid_test():
    req = ProgressiveRequirement(stage_order=1, hidden_tests_json=[{"input": "x", "expected_output": "60"}])
    assert stage_orders_missing_hidden_tests([req]) == []


def test_stage_orders_missing_hidden_tests_allows_multiple_valid_tests():
    req = ProgressiveRequirement(
        stage_order=1,
        hidden_tests_json=[{"input": "x", "expected_output": "60"}, {"input": "y", "expected_output": "70"}],
    )
    assert stage_orders_missing_hidden_tests([req]) == []


def test_stage_orders_missing_hidden_tests_checks_each_stage_independently():
    good = ProgressiveRequirement(stage_order=1, hidden_tests_json=[{"input": "x", "expected_output": "60"}])
    bad = ProgressiveRequirement(stage_order=2, hidden_tests_json=[])
    assert stage_orders_missing_hidden_tests([good, bad]) == [2]


# ---- Hardening item 1: validate_stage_compatibility ----

def test_validate_stage_compatibility_detects_a_breaking_change(db, hr_user, monkeypatch):
    problem = ProgressiveProblem(
        title="Incompatible stages", description="d", input_spec_json={}, language="python", created_by=hr_user.id,
    )
    db.add(problem)
    db.commit()
    db.refresh(problem)
    db.add(ProgressiveRequirement(
        problem_id=problem.id, stage_order=1, requirement_text="Sum the amounts.",
        hidden_tests_json=[{"input": "sum", "expected_output": "60"}], reference_solution="GOOD_STAGE1_CODE",
    ))
    db.add(ProgressiveRequirement(
        problem_id=problem.id, stage_order=2, requirement_text="Now redefine the output entirely.",
        hidden_tests_json=[{"input": "avg", "expected_output": "20"}], reference_solution="BAD_STAGE2_CODE",
    ))
    db.commit()

    def _fake(language, code, stdin):
        # BAD_STAGE2_CODE no longer reproduces stage 1's expected "60" for
        # stage 1's own hidden test input - simulates stage 2 silently
        # redefining/breaking stage 1's contract.
        output = "60" if code == "GOOD_STAGE1_CODE" else "WRONG"
        return execution_service.ExecutionResult(stdout=output, stderr="", exit_code=0, timed_out=False, infra_error=False)

    monkeypatch.setattr(progressive_service.execution_service, "run_code", _fake)

    incompatibilities = validate_stage_compatibility(db, problem.id)
    assert len(incompatibilities) == 1
    assert incompatibilities[0]["stage_order"] == 2
    assert incompatibilities[0]["breaks_stage_orders"] == [1]
    assert incompatibilities[0]["failed_test_count"] == 1


def test_validate_stage_compatibility_allows_a_genuinely_compatible_sequence(db, hr_user, monkeypatch):
    problem = ProgressiveProblem(
        title="Compatible stages", description="d", input_spec_json={}, language="python", created_by=hr_user.id,
    )
    db.add(problem)
    db.commit()
    db.refresh(problem)
    db.add(ProgressiveRequirement(
        problem_id=problem.id, stage_order=1, requirement_text="Sum the amounts.",
        hidden_tests_json=[{"input": "sum", "expected_output": "60"}], reference_solution="STAGE1_CODE",
    ))
    db.add(ProgressiveRequirement(
        problem_id=problem.id, stage_order=2, requirement_text="Also compute the average.",
        hidden_tests_json=[{"input": "avg", "expected_output": "20"}], reference_solution="STAGE2_CODE",
    ))
    db.commit()

    def _fake(language, code, stdin):
        # STAGE2_CODE still reproduces stage 1's expected "60" for stage
        # 1's own hidden test input - a genuinely additive, compatible change.
        return execution_service.ExecutionResult(stdout="60", stderr="", exit_code=0, timed_out=False, infra_error=False)

    monkeypatch.setattr(progressive_service.execution_service, "run_code", _fake)
    assert validate_stage_compatibility(db, problem.id) == []


def test_validate_stage_compatibility_skips_stages_with_no_reference_solution_yet(db, hr_user, monkeypatch):
    def _fail(language, code, stdin):
        raise AssertionError("run_code should never be called when no reference_solution exists to replay")

    monkeypatch.setattr(progressive_service.execution_service, "run_code", _fail)

    problem = ProgressiveProblem(
        title="Still drafting", description="d", input_spec_json={}, language="python", created_by=hr_user.id,
    )
    db.add(problem)
    db.commit()
    db.refresh(problem)
    db.add(ProgressiveRequirement(
        problem_id=problem.id, stage_order=1, requirement_text="Sum the amounts.",
        hidden_tests_json=[{"input": "sum", "expected_output": "60"}],
    ))
    db.add(ProgressiveRequirement(
        problem_id=problem.id, stage_order=2, requirement_text="Now compute the average.",
        hidden_tests_json=[{"input": "avg", "expected_output": "20"}],
    ))
    db.commit()

    assert validate_stage_compatibility(db, problem.id) == []
