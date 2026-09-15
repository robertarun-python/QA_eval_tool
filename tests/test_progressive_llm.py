"""
Round 5 (Progressive Engineering) POC - Phase 3 generator + structural
isolation tests.

Same isolation as the rest of Round 5's test files: raw in-memory
SQLite, no FastAPI client, no routes. llm_service._call_claude is
mocked in every test that calls generate_response - no live-LLM
dependency here.

The isolation tests below are the actual proof this session's CRITICAL
SECURITY RULE holds, not just a docstring claim: each stage's hidden
tests, reference solution, and requirement text are tagged with unique
marker strings, and the tests assert those markers for OTHER stages
never appear anywhere in the rendered prompt.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app import models  # noqa: F401 - registers users/etc. onto Base.metadata
from app.models import User, Role
from app.models_progressive import ProgressiveProblem, ProgressiveRequirement
from app.services import llm_service, progressive_service, progressive_llm
from app.services.progressive_llm import build_generator_context, render_prompt, generate_response
from app.services.progressive_service import start_attempt, AttemptNotActiveError


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
    user = User(email="hr-progressive-llm@example.com", password_hash="x", role=Role.hr)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def candidate_user(db):
    user = User(email="candidate-progressive-llm@example.com", password_hash="x", role=Role.candidate)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def marked_three_stage_problem(db, hr_user):
    """Every piece of content that must never leak is given a unique,
    greppable marker string - the tests below assert those exact markers
    are absent, not just "similar text.\""""
    problem = ProgressiveProblem(
        title="Transaction Processing", description="You will receive transaction data and evolve a solution.",
        input_spec_json={"kind": "csv", "filename": "transactions.csv"}, language="python",
        starter_code="", created_by=hr_user.id,
    )
    db.add(problem)
    db.commit()
    db.refresh(problem)

    for stage, name in ((1, "SUM"), (2, "AVERAGE"), (3, "TOP_TEN")):
        db.add(ProgressiveRequirement(
            problem_id=problem.id, stage_order=stage,
            requirement_text=f"STAGE{stage}_{name}_REQUIREMENT_TEXT",
            expected_behavior=f"STAGE{stage}_{name}_EXPECTED_BEHAVIOR_TEXT",
            hidden_tests_json=[{"input": f"STAGE{stage}_{name}_HIDDEN_TEST_INPUT", "expected_output": f"STAGE{stage}_{name}_HIDDEN_TEST_OUTPUT"}],
            reference_solution=f"STAGE{stage}_{name}_REFERENCE_SOLUTION_CODE",
        ))
    db.commit()
    return problem


ALL_MARKERS = {
    1: ("STAGE1_SUM_REQUIREMENT_TEXT", "STAGE1_SUM_HIDDEN_TEST_INPUT", "STAGE1_SUM_REFERENCE_SOLUTION_CODE"),
    2: ("STAGE2_AVERAGE_REQUIREMENT_TEXT", "STAGE2_AVERAGE_HIDDEN_TEST_INPUT", "STAGE2_AVERAGE_REFERENCE_SOLUTION_CODE"),
    3: ("STAGE3_TOP_TEN_REQUIREMENT_TEXT", "STAGE3_TOP_TEN_HIDDEN_TEST_INPUT", "STAGE3_TOP_TEN_REFERENCE_SOLUTION_CODE"),
}


def _prompt_for_stage(db, candidate_user, problem, stage):
    attempt = start_attempt(db, candidate_user.id, problem.id)
    attempt.current_stage = stage
    db.commit()
    context = build_generator_context(db, attempt)
    return render_prompt(context, candidate_request="what should I do next?")


# ---- Stage isolation: the required proof ----

def test_stage_1_prompt_does_not_contain_stage_2_or_3_requirement_text(db, candidate_user, marked_three_stage_problem):
    prompt = _prompt_for_stage(db, candidate_user, marked_three_stage_problem, 1)
    assert "STAGE1_SUM_REQUIREMENT_TEXT" in prompt
    assert "STAGE2_AVERAGE_REQUIREMENT_TEXT" not in prompt
    assert "STAGE3_TOP_TEN_REQUIREMENT_TEXT" not in prompt


def test_stage_2_prompt_does_not_contain_stage_3_requirement_text(db, candidate_user, marked_three_stage_problem):
    prompt = _prompt_for_stage(db, candidate_user, marked_three_stage_problem, 2)
    assert "STAGE2_AVERAGE_REQUIREMENT_TEXT" in prompt
    assert "STAGE3_TOP_TEN_REQUIREMENT_TEXT" not in prompt
    # Bonus, stronger than what was asked: even the PAST stage's
    # requirement text is absent - the generator only ever knows the
    # single current requirement, never a running history of them.
    assert "STAGE1_SUM_REQUIREMENT_TEXT" not in prompt


def test_stage_3_prompt_contains_only_stage_3_requirement_text(db, candidate_user, marked_three_stage_problem):
    prompt = _prompt_for_stage(db, candidate_user, marked_three_stage_problem, 3)
    assert "STAGE3_TOP_TEN_REQUIREMENT_TEXT" in prompt
    assert "STAGE1_SUM_REQUIREMENT_TEXT" not in prompt
    assert "STAGE2_AVERAGE_REQUIREMENT_TEXT" not in prompt


# ---- Hidden tests / reference solutions: must NEVER appear, for ANY stage ----

def test_hidden_tests_never_enter_generator_context(db, candidate_user, marked_three_stage_problem):
    for stage in (1, 2, 3):
        prompt = _prompt_for_stage(db, candidate_user, marked_three_stage_problem, stage)
        for s, markers in ALL_MARKERS.items():
            hidden_test_marker = markers[1]
            assert hidden_test_marker not in prompt, f"stage {stage}'s prompt leaked stage {s}'s hidden test"


def test_reference_solution_never_enters_generator_context(db, candidate_user, marked_three_stage_problem):
    for stage in (1, 2, 3):
        prompt = _prompt_for_stage(db, candidate_user, marked_three_stage_problem, stage)
        for s, markers in ALL_MARKERS.items():
            reference_marker = markers[2]
            assert reference_marker not in prompt, f"stage {stage}'s prompt leaked stage {s}'s reference solution"


def test_current_requirement_does_enter_generator_context(db, candidate_user, marked_three_stage_problem):
    """The positive case - isolation must not mean the generator gets
    NOTHING; the one requirement it's entitled to must actually be there."""
    for stage in (1, 2, 3):
        prompt = _prompt_for_stage(db, candidate_user, marked_three_stage_problem, stage)
        own_requirement_marker = ALL_MARKERS[stage][0]
        assert own_requirement_marker in prompt


def test_expected_behavior_for_current_stage_is_included_when_present(db, candidate_user, marked_three_stage_problem):
    prompt = _prompt_for_stage(db, candidate_user, marked_three_stage_problem, 1)
    assert "STAGE1_SUM_EXPECTED_BEHAVIOR_TEXT" in prompt
    assert "STAGE2_AVERAGE_EXPECTED_BEHAVIOR_TEXT" not in prompt


# ---- Structural guarantee, not just a probabilistic one ----

def test_completed_attempt_cannot_have_a_context_built(db, candidate_user, marked_three_stage_problem):
    """Reuses Phase 2's own guarantee (get_current_requirement raises
    once completed) - proves Phase 3 inherits it rather than bypassing it."""
    attempt = start_attempt(db, candidate_user.id, marked_three_stage_problem.id)
    attempt.completed_at = datetime.utcnow()
    db.commit()
    with pytest.raises(AttemptNotActiveError):
        build_generator_context(db, attempt)


# ---- Domain neutrality ----

def test_context_building_is_domain_neutral(db, hr_user, candidate_user):
    """A JSON-processing-flavored problem, not CSV - proves nothing in
    the context-building path assumes a particular input format."""
    problem = ProgressiveProblem(
        title="API Response Aggregation", description="You will receive JSON API responses and evolve a solution.",
        input_spec_json={"kind": "json", "schema": {"records": "array"}}, language="javascript",
        starter_code="", created_by=hr_user.id,
    )
    db.add(problem)
    db.commit()
    db.refresh(problem)
    db.add(ProgressiveRequirement(
        problem_id=problem.id, stage_order=1, requirement_text="Count the total number of records.",
    ))
    db.commit()

    attempt = start_attempt(db, candidate_user.id, problem.id)
    context = build_generator_context(db, attempt)
    assert context.language == "javascript"
    assert context.input_spec["kind"] == "json"
    prompt = render_prompt(context, "how do I read the records array?")
    assert "Count the total number of records." in prompt
    assert "javascript" in prompt


# ---- generate_response plumbing (mocked LLM, no live call) ----

def test_generate_response_round_trips_a_code_edit(db, candidate_user, marked_three_stage_problem, monkeypatch):
    attempt = start_attempt(db, candidate_user.id, marked_three_stage_problem.id)
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "code_edit", "response_message": "Added the sum.", "code_after": "def total(rows):\n    return sum(rows)",
    }))
    result = generate_response(db, attempt, "sum the amounts")
    assert result.response_kind == "code_edit"
    assert result.code_after == "def total(rows):\n    return sum(rows)"


def test_generate_response_rejects_unrecognized_response_kind(db, candidate_user, marked_three_stage_problem, monkeypatch):
    attempt = start_attempt(db, candidate_user.id, marked_three_stage_problem.id)
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "solve_it_all", "response_message": "...", "code_after": None,
    }))
    with pytest.raises(ValueError):
        generate_response(db, attempt, "sum the amounts")


def test_prompt_never_invents_a_domain_or_function_name():
    """Static check on the template itself: no CSV/language/function-name
    assumption baked into the prompt text."""
    prompt = llm_service._load_prompt("progressive_generator.txt")
    for forbidden in ("csv", "\\.csv", "def main(", "function main("):
        assert forbidden.lower() not in prompt.lower()
