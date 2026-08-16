"""
Turns raw LLM output into a persisted Score row. Split out from
llm_service so llm_service stays "talk to Claude" only, and this file
is "what do we do with the answer" - easier to unit test scoring logic
without mocking the Anthropic client every time.
"""
import json

from sqlalchemy.orm import Session

from ..models import Submission, Score, RoundStatus
from . import llm_service


def score_round1_submission(db: Session, submission: Submission) -> Score:
    """Round 1: candidate's structured test-case rows vs. the scenario's
    HR-approved reference rows - same shape on both sides."""
    scenario = submission.scenario
    experience_band = scenario.experience_band.value if scenario.experience_band else "both"

    # The reference was already generated and HR-approved when the
    # scenario was published (see hr.py) - reused here rather than
    # generated fresh, so every candidate is scored against the exact
    # reference HR reviewed, not a new LLM roll each time.
    reference_rows = scenario.reference_json or []
    candidate_submission = json.dumps(submission.content or [], indent=2)

    result = llm_service.score_round1_submission(
        scenario_description=scenario.description,
        experience_band=experience_band,
        reference_cases=reference_rows,
        candidate_submission=candidate_submission,
    )

    score = Score(
        submission_id=submission.id,
        coverage_score=result.get("coverage_score"),
        misses_json=result.get("misses", []),
        final_score=result.get("final_score"),
        feedback_text=result.get("feedback_text"),
        raw_llm_response_json={"reference_rows": reference_rows, "scoring": result},
    )
    submission.status = RoundStatus.scored
    db.add(score)
    db.commit()
    db.refresh(score)
    return score


def score_round2_investigation(db: Session, submission: Submission) -> Score:
    """Round 2: candidate's submission is investigation-shaped (a list of
    areas checked + one root-cause conclusion, see schemas.Round2SubmissionCreate),
    scored against the scenario's still test-case-shaped reference (HR's
    review UI is unaffected by this - see llm_service.py's round 2 section)."""
    scenario = submission.scenario
    experience_band = scenario.experience_band.value if scenario.experience_band else "both"
    reference_rows = scenario.reference_json or []

    content = submission.content or {}
    candidate_investigation = content.get("investigation", [])
    candidate_root_cause = content.get("root_cause", "")

    result = llm_service.score_round2_submission(
        scenario_description=scenario.description,
        experience_band=experience_band,
        reference_steps=reference_rows,
        candidate_investigation=candidate_investigation,
        candidate_root_cause=candidate_root_cause,
    )

    score = Score(
        submission_id=submission.id,
        coverage_score=result.get("coverage_score"),
        misses_json=result.get("misses", []),
        final_score=result.get("final_score"),
        feedback_text=result.get("feedback_text"),
        raw_llm_response_json={"reference_rows": reference_rows, "scoring": result},
    )
    submission.status = RoundStatus.scored
    db.add(score)
    db.commit()
    db.refresh(score)
    return score


def score_round3_submission(db: Session, submission: Submission) -> Score:
    """Round 3 gets one holistic score across all the candidate's own
    test cases (not per-test-case sub-scores - see
    llm_service.score_round3_conversation), same Score shape as every
    other round. Unlike rounds 1/2 there's no scenario reference to
    score against - the target is the candidate's own round 1
    submission, fetched fresh here rather than passed in."""
    scenario = submission.scenario
    config = {**llm_service.DEFAULT_ROUND3_CONFIG, **(scenario.config_json or {})}

    round1_submission = (
        db.query(Submission)
        .filter(Submission.user_id == submission.user_id, Submission.round_number == 1)
        .first()
    )
    round1_context = {
        "scenario_title": round1_submission.scenario.title if round1_submission else "",
        "scenario_description": round1_submission.scenario.description if round1_submission else "",
        "submitted_rows": (round1_submission.content or []) if round1_submission else [],
    }

    test_cases_payload = [
        {
            "title": tc.title or f"Test case {i + 1}",
            "turns": [
                {"turn_number": t.turn_number, "candidate_prompt": t.candidate_prompt, "model_response": t.model_response}
                for t in tc.turns
            ],
        }
        for i, tc in enumerate(submission.round3_test_cases)
    ]

    result = llm_service.score_round3_conversation(
        round1_context=round1_context,
        test_cases=test_cases_payload,
        assistance_pct=config["assistance_pct"],
    )

    score = Score(
        submission_id=submission.id,
        coverage_score=result.get("coverage_score"),
        misses_json=result.get("misses", []),
        final_score=result.get("final_score"),
        feedback_text=result.get("feedback_text"),
        raw_llm_response_json={"round1_context": round1_context, "test_cases": test_cases_payload, "scoring": result},
    )
    submission.status = RoundStatus.scored
    db.add(score)
    db.commit()
    db.refresh(score)
    return score
