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


def score_round1(db: Session, submission: Submission) -> Score:
    scenario = submission.scenario
    experience_band = scenario.experience_band.value if scenario.experience_band else "both"

    # The reference was already generated and HR-approved when the
    # scenario was published (see hr.py) - reused here rather than
    # generated fresh, so every candidate is scored against the exact
    # reference HR reviewed, not a new LLM roll each time.
    reference_cases = scenario.reference_json or []
    result = llm_service.score_round1_submission(
        scenario_description=scenario.description,
        experience_band=experience_band,
        reference_cases=reference_cases,
        candidate_submission=json.dumps(submission.content or [], indent=2),
    )

    score = Score(
        submission_id=submission.id,
        coverage_score=result.get("coverage_score"),
        misses_json=result.get("misses", []),
        final_score=result.get("final_score"),
        feedback_text=result.get("feedback_text"),
        raw_llm_response_json={"reference_cases": reference_cases, "scoring": result},
    )
    submission.status = RoundStatus.scored
    db.add(score)
    db.commit()
    db.refresh(score)
    return score
