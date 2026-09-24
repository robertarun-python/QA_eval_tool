"""
Round 5 (Progressive Engineering) POC - Phase 1 data-model tests.

Isolated from the rest of the test suite on purpose: no `client` fixture,
no FastAPI app, no seeded HR/candidate accounts, no LLM mocking - this
phase built a data model only, so these tests talk to a raw in-memory
SQLite DB via the ORM classes directly. Confirms the schema itself is
sound (constraints, relationships) before any service/route code exists
to build on it.

Also confirms (see test_progressive_tables_coexist_with_existing_schema)
that importing and creating these new tables alongside models.py's
existing ones causes no collision - the actual proof, alongside a full
existing-suite run reported separately, that Rounds 1-4 are unaffected.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app import models  # noqa: F401 - registers users/etc. onto Base.metadata
from app.models import User, Role
from app.models_progressive import (
    ProgressiveProblem, ProgressiveProblemStatus, ProgressiveRequirement,
    ProgressiveAttempt, ProgressiveStageResult, ProgressiveAiTurn,
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
    user = User(email="hr-progressive-test@example.com", password_hash="x", role=Role.hr)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def candidate_user(db):
    user = User(email="candidate-progressive-test@example.com", password_hash="x", role=Role.candidate)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _make_problem(db, hr_user, **overrides):
    defaults = dict(
        title="Transaction Reconciliation",
        description="Process a batch of transactions and reconcile totals.",
        input_spec_json={"kind": "csv", "filename": "transactions.csv", "columns": ["id", "amount", "date"]},
        language="python",
        created_by=hr_user.id,
    )
    defaults.update(overrides)
    problem = ProgressiveProblem(**defaults)
    db.add(problem)
    db.commit()
    db.refresh(problem)
    return problem


# ---- 1. Problem creation ----

def test_problem_creation(db, hr_user):
    problem = _make_problem(db, hr_user)
    assert problem.id is not None
    assert problem.status == ProgressiveProblemStatus.draft
    assert problem.input_spec_json["kind"] == "csv"


def test_problem_input_spec_is_domain_neutral(db, hr_user):
    """No CSV-specific columns exist - a JSON-processing problem and a
    log-analysis problem both fit the same input_spec_json field."""
    json_problem = _make_problem(
        db, hr_user, title="API Response Aggregation",
        input_spec_json={"kind": "json", "schema": {"records": "array"}},
    )
    log_problem = _make_problem(
        db, hr_user, title="Log Analysis",
        input_spec_json={"kind": "log", "format": "combined_log_format"},
    )
    assert json_problem.input_spec_json["kind"] == "json"
    assert log_problem.input_spec_json["kind"] == "log"


# ---- 2. Multiple requirements for one problem ----

def test_multiple_requirements_for_one_problem(db, hr_user):
    problem = _make_problem(db, hr_user)
    stage1 = ProgressiveRequirement(problem_id=problem.id, stage_order=1, requirement_text="Sum the amounts.")
    stage2 = ProgressiveRequirement(problem_id=problem.id, stage_order=2, requirement_text="Now compute the average.")
    stage3 = ProgressiveRequirement(problem_id=problem.id, stage_order=3, requirement_text="Now find the top 10.")
    db.add_all([stage1, stage2, stage3])
    db.commit()

    db.refresh(problem)
    assert [r.stage_order for r in problem.requirements] == [1, 2, 3]


# ---- 3. stage_order uniqueness ----

def test_stage_order_unique_per_problem(db, hr_user):
    problem = _make_problem(db, hr_user)
    db.add(ProgressiveRequirement(problem_id=problem.id, stage_order=1, requirement_text="First."))
    db.commit()
    db.add(ProgressiveRequirement(problem_id=problem.id, stage_order=1, requirement_text="Duplicate stage order."))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_stage_order_must_be_positive(db, hr_user):
    problem = _make_problem(db, hr_user)
    db.add(ProgressiveRequirement(problem_id=problem.id, stage_order=0, requirement_text="Invalid."))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_same_stage_order_allowed_across_different_problems(db, hr_user):
    """The uniqueness constraint is scoped to (problem_id, stage_order),
    not stage_order alone."""
    problem_a = _make_problem(db, hr_user, title="Problem A")
    problem_b = _make_problem(db, hr_user, title="Problem B")
    db.add(ProgressiveRequirement(problem_id=problem_a.id, stage_order=1, requirement_text="A stage 1."))
    db.add(ProgressiveRequirement(problem_id=problem_b.id, stage_order=1, requirement_text="B stage 1."))
    db.commit()  # must not raise


# ---- 4. Attempt creation ----

def test_attempt_creation(db, hr_user, candidate_user):
    problem = _make_problem(db, hr_user)
    attempt = ProgressiveAttempt(user_id=candidate_user.id, problem_id=problem.id)
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    assert attempt.current_stage == 1
    assert attempt.archived is False
    assert attempt.status.value == "in_progress"


# ---- 5. Stage result creation ----

def test_stage_result_creation(db, hr_user, candidate_user):
    problem = _make_problem(db, hr_user)
    attempt = ProgressiveAttempt(user_id=candidate_user.id, problem_id=problem.id)
    db.add(attempt)
    db.commit()
    db.refresh(attempt)

    result = ProgressiveStageResult(
        attempt_id=attempt.id, stage_order=1, code_snapshot="def total(rows):\n    return sum(r['amount'] for r in rows)",
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    assert result.passed is None  # not yet scored - no scoring service exists in this phase
    assert result.test_results_json is None


# ---- 6. Multiple stage results for the same attempt ----

def test_multiple_stage_results_for_same_attempt(db, hr_user, candidate_user):
    problem = _make_problem(db, hr_user)
    attempt = ProgressiveAttempt(user_id=candidate_user.id, problem_id=problem.id)
    db.add(attempt)
    db.commit()
    db.refresh(attempt)

    db.add(ProgressiveStageResult(attempt_id=attempt.id, stage_order=1, code_snapshot="v1"))
    db.add(ProgressiveStageResult(attempt_id=attempt.id, stage_order=2, code_snapshot="v2"))
    db.commit()

    db.refresh(attempt)
    assert [r.stage_order for r in attempt.stage_results] == [1, 2]
    assert attempt.stage_results[1].code_snapshot == "v2"


# ---- 7. Immutable snapshot representation ----

def test_stage_result_is_immutable_per_stage(db, hr_user, candidate_user):
    """A second result row for the SAME (attempt, stage) is rejected at
    the DB level - the actual immutability guarantee, not just a naming
    convention. A resubmission would need an explicit new attempt, never
    an overwrite of an existing stage result."""
    problem = _make_problem(db, hr_user)
    attempt = ProgressiveAttempt(user_id=candidate_user.id, problem_id=problem.id)
    db.add(attempt)
    db.commit()
    db.refresh(attempt)

    db.add(ProgressiveStageResult(attempt_id=attempt.id, stage_order=1, code_snapshot="first"))
    db.commit()
    db.add(ProgressiveStageResult(attempt_id=attempt.id, stage_order=1, code_snapshot="second"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ---- 8. Requirement freeze behaviour (model-level only - no service exists yet) ----

def test_requirement_frozen_flag_defaults_false_and_is_settable(db, hr_user):
    """This phase implements the column only - enforcing WHEN it flips
    (on publish, or once any attempt exists) is service-layer logic for
    a later phase, not built here. This test only proves the flag itself
    is a real, working boolean a future service can rely on."""
    problem = _make_problem(db, hr_user)
    req = ProgressiveRequirement(problem_id=problem.id, stage_order=1, requirement_text="Sum the amounts.")
    db.add(req)
    db.commit()
    db.refresh(req)
    assert req.frozen is False

    req.frozen = True
    db.commit()
    db.refresh(req)
    assert req.frozen is True


# ---- 9. Foreign-key relationships ----

def test_relationships_are_navigable_both_ways(db, hr_user, candidate_user):
    problem = _make_problem(db, hr_user)
    req = ProgressiveRequirement(problem_id=problem.id, stage_order=1, requirement_text="Sum the amounts.")
    db.add(req)
    attempt = ProgressiveAttempt(user_id=candidate_user.id, problem_id=problem.id)
    db.add(attempt)
    db.commit()
    db.refresh(attempt)

    turn = ProgressiveAiTurn(
        attempt_id=attempt.id, stage_order=1, candidate_prompt="explain the loop",
        generated_response="It iterates over each row.",
        detector_status="SAFE", judge_verdict="PASS",
    )
    result = ProgressiveStageResult(attempt_id=attempt.id, stage_order=1, code_snapshot="def total(): pass")
    db.add_all([turn, result])
    db.commit()

    db.refresh(problem)
    db.refresh(attempt)
    assert problem.requirements[0].problem_id == problem.id
    assert attempt.problem.id == problem.id
    assert attempt.ai_turns[0].generated_response == "It iterates over each row."
    assert attempt.stage_results[0].code_snapshot == "def total(): pass"


def test_ai_turn_has_no_field_for_future_stage_content(db):
    """Structural proof of the future-content isolation claim: there is
    no column on ProgressiveAiTurn a caller could even accidentally use
    to store a future requirement, a hidden test, or a reference
    solution - only ProgressiveRequirement carries those, one per stage."""
    column_names = {c.name for c in ProgressiveAiTurn.__table__.columns}
    for forbidden in ("future", "hidden_test", "reference_solution", "requirement_text"):
        assert not any(forbidden in name for name in column_names), (
            f"ProgressiveAiTurn must never grow a field shaped like future-stage content ('{forbidden}' found)"
        )


# ---- 10. Round 1-4 unaffected - the new tables coexist cleanly ----

def test_progressive_tables_coexist_with_existing_schema(db):
    """Creating models.py's tables (users, scenarios, submissions, ...)
    and models_progressive.py's tables in the SAME metadata/engine causes
    no name collision or FK resolution failure - the `db` fixture above
    already does exactly this on every test in this file; this test just
    makes the assertion explicit and checks both schemas' tables exist
    side by side."""
    table_names = set(Base.metadata.tables.keys())
    existing_round_tables = {"users", "scenarios", "submissions", "scores", "round3_turns"}
    new_progressive_tables = {
        "progressive_problems", "progressive_requirements", "progressive_attempts",
        "progressive_stage_results", "progressive_ai_turns",
    }
    assert existing_round_tables.issubset(table_names)
    assert new_progressive_tables.issubset(table_names)
