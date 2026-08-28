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

import pytest

from app.models import Scenario, Submission, Round3Turn, RoundStatus, ScenarioStatus, ExperienceBand, User, Role
from app.services import scoring_service, llm_service, execution_service


def test_score_round3_submission_computes_coverage_and_flags_guardrail_violations(client, monkeypatch):
    # Imported locally (not at module level) so this resolves to the
    # `client` fixture's in-memory test SessionLocal (patched onto
    # app.database at fixture setup time via monkeypatch) rather than
    # binding to the real qa_eval.db's SessionLocal at module-import
    # time - see tests/test_auth.py, which uses this same local-import
    # pattern for the same reason.
    from app.database import SessionLocal

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
        # The sub-score breakdown behind final_score (see
        # models.Score.correctness_score and its siblings) - HR's report
        # renders these as a "where did they score/miss" breakdown, not
        # just the single blended final_score.
        assert score.correctness_score == 100
        assert score.precision_score == 90
        assert score.efficiency_score == 80
        assert score.independent_judgment_score == 40
        db.refresh(submission)
        assert submission.status == RoundStatus.scored
    finally:
        db.close()


def test_score_round3_submission_passes_a_test_case_whose_stdout_is_prefixed_by_an_input_prompt(client):
    # Regression test: a real candidate hit this. input()'s prompt
    # argument writes to stdout with no trailing newline, so
    # "Please enter the integer" + "even" (no separator) is exactly
    # what a program that reads with a prompt actually produces on a
    # piped, non-interactive stdin - the prompt is a PREFIX of the real
    # answer, and the test case must still pass.
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        candidate = db.query(User).filter(User.role == Role.candidate).first()
        scenario = Scenario(
            round_number=3, title="Odd or even", description="Read a number, print odd or even.",
            experience_band=ExperienceBand.junior, created_by=candidate.id, status=ScenarioStatus.published,
            is_live=True, time_limit_minutes=30,
            reference_json={
                "test_cases": [
                    {"input": "4", "expected_output": "even", "description": "basic even"},
                    {"input": "7", "expected_output": "odd", "description": "basic odd"},
                ],
                "expected_approach": "Use the modulo operator.",
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
            submission_id=submission.id, turn_number=1, candidate_prompt="read the integer with a prompt",
            language="python", response_kind="code_edit", response_message="Added the input and check.",
            code_after="n = int(input('Please enter the integer'))\nprint('even' if n % 2 == 0 else 'odd')",
        )
        db.add(turn)
        db.commit()

        def fake_run_code(language, code, stdin):
            joined = " ".join(stdin)
            # Mirrors real input()-with-a-prompt behavior against piped
            # stdin: the prompt text is written first, with no newline,
            # immediately followed by whatever the program prints.
            answer = {"4": "even", "7": "odd"}[joined]
            return execution_service.ExecutionResult(
                stdout=f"Please enter the integer{answer}", stderr="", exit_code=0, timed_out=False, infra_error=False,
            )

        monkeypatch_run = execution_service.run_code
        execution_service.run_code = fake_run_code
        try:
            llm_service_run = llm_service.score_round3_coding
            llm_service.score_round3_coding = lambda **kwargs: {
                "correctness_score": 100, "precision_score": 100, "efficiency_score": 100,
                "independent_judgment_score": 100, "final_score": 100,
                "misses": [], "guardrail_violations": [], "feedback_text": "Correct.",
            }
            try:
                score = scoring_service.score_round3_submission(db, submission)
            finally:
                llm_service.score_round3_coding = llm_service_run
        finally:
            execution_service.run_code = monkeypatch_run

        assert score.coverage_score == 100
        assert score.test_results_json[0]["passed"] is True
        assert score.test_results_json[0]["actual_output"] == "Please enter the integereven"
    finally:
        db.close()


def test_score_round3_submission_empty_expected_output_still_uses_exact_match(client):
    # A blank expected_output must not trivially "pass" every actual
    # output (endswith("") is always True) - the one case suffix
    # matching can't safely handle falls back to exact match.
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        candidate = db.query(User).filter(User.role == Role.candidate).first()
        scenario = Scenario(
            round_number=3, title="Print nothing", description="x",
            experience_band=ExperienceBand.junior, created_by=candidate.id, status=ScenarioStatus.published,
            is_live=True, time_limit_minutes=30,
            reference_json={
                "test_cases": [{"input": "x", "expected_output": "", "description": "no output expected"}],
                "expected_approach": "x",
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
            submission_id=submission.id, turn_number=1, candidate_prompt="x",
            language="python", response_kind="code_edit", response_message="x",
            code_after="pass",
        )
        db.add(turn)
        db.commit()

        run_code_run = execution_service.run_code
        execution_service.run_code = lambda language, code, stdin: execution_service.ExecutionResult(
            stdout="unexpected output", stderr="", exit_code=0, timed_out=False, infra_error=False,
        )
        try:
            score_run = llm_service.score_round3_coding
            llm_service.score_round3_coding = lambda **kwargs: {
                "correctness_score": 0, "precision_score": 0, "efficiency_score": 0,
                "independent_judgment_score": 0, "final_score": 0,
                "misses": [], "guardrail_violations": [], "feedback_text": "x",
            }
            try:
                score = scoring_service.score_round3_submission(db, submission)
            finally:
                llm_service.score_round3_coding = score_run
        finally:
            execution_service.run_code = run_code_run

        assert score.test_results_json[0]["passed"] is False
    finally:
        db.close()


def test_score_round3_submission_handles_a_malformed_reference_test_case_gracefully(client, monkeypatch):
    """Fix 9 (final whole-branch review): a malformed reference test case
    (missing 'input' or 'expected_output') must fail that one test case
    with a clear description, not blow up scoring with an opaque
    KeyError - a defensive backstop in case Fix 2's HR-authoring
    validation isn't airtight, or a reference was hand-edited before
    Fix 2 landed."""
    from app.database import SessionLocal

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
                    {"description": "malformed row - missing 'input'"},  # no "input" key
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
            submission_id=submission.id, turn_number=1, candidate_prompt="write it for me",
            language="python", response_kind="code_edit", response_message="Here you go.",
            code_after="a=int(input());b=int(input());print(a+b)",
        )
        db.add(turn)
        db.commit()

        def fake_run_code(language, code, stdin):
            joined = " ".join(stdin)
            output = {"2 3": "5"}.get(joined, "")
            return execution_service.ExecutionResult(stdout=output, stderr="", exit_code=0, timed_out=False, infra_error=False)

        monkeypatch.setattr(execution_service, "run_code", fake_run_code)
        monkeypatch.setattr(llm_service, "score_round3_coding", lambda **kwargs: {
            "correctness_score": 50, "precision_score": 80, "efficiency_score": 80,
            "independent_judgment_score": 90, "final_score": 60,
            "misses": [], "guardrail_violations": [], "feedback_text": "ok",
        })

        # Must not raise - the malformed row fails as a test case, not
        # as an uncaught KeyError.
        score = scoring_service.score_round3_submission(db, submission)

        assert score.coverage_score == 50  # 1 of 2 test cases passed
        db.refresh(submission)
        assert submission.status == RoundStatus.scored
    finally:
        db.close()


def test_score_round3_submission_raises_when_execution_hits_infra_error(client, monkeypatch):
    """Regression test for the design spec's Error handling rule: a
    hosted-execution-API infra failure (e.g. Piston returning 401/
    whitelist-required) must never be silently counted as a failed test
    case in the coverage_score denominator. Instead it must propagate as
    an exception, so score_submission_in_background's existing
    try/except (scoring_service.py) routes the submission to
    RoundStatus.scoring_failed instead of persisting a bogus low score."""
    from app.database import SessionLocal

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
            submission_id=submission.id, turn_number=1, candidate_prompt="write it for me",
            language="python", response_kind="code_edit",
            response_message="Here you go.",
            code_after="a=int(input());b=int(input());print(a+b)",
        )
        db.add(turn)
        db.commit()

        def fake_run_code_infra_error(language, code, stdin):
            return execution_service.ExecutionResult(stdout="", stderr="", exit_code=None, timed_out=False, infra_error=True)

        monkeypatch.setattr(execution_service, "run_code", fake_run_code_infra_error)
        monkeypatch.setattr(llm_service, "score_round3_coding", lambda **kwargs: pytest.fail(
            "llm_service.score_round3_coding must not be called when execution hit an infra error"
        ))

        # Specifically RuntimeError (not bare Exception) so this test
        # can't be satisfied by an unrelated failure, e.g. the
        # AttributeError from score_round3_submission not existing yet
        # pre-implementation - that must fail this assertion, not
        # accidentally pass it.
        with pytest.raises(RuntimeError, match="infra error"):
            scoring_service.score_round3_submission(db, submission)

        db.refresh(submission)
        assert submission.status == RoundStatus.submitted  # unchanged - the caller (score_submission_in_background) is what would move it to scoring_failed
        assert submission.score is None
    finally:
        db.close()
