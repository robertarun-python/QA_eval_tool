"""
Turns raw LLM output into a persisted Score row. Split out from
llm_service so llm_service stays "talk to Claude" only, and this file
is "what do we do with the answer" - easier to unit test scoring logic
without mocking the Anthropic client every time.
"""
import json
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import Submission, Score, RoundStatus
from . import llm_service


def _apply_provenance(score: Score, result: dict) -> None:
    """Pops _provenance off the raw LLM result (see llm_service.py's
    _scoring_provenance) and writes it onto the Score row, so it never
    ends up duplicated inside raw_llm_response_json too. Missing/empty
    when the caller is a test that monkeypatches llm_service's scoring
    function directly (bypassing the real prompt-loading/API call this
    comes from) - that's fine, these columns are all nullable."""
    provenance = result.pop("_provenance", None) or {}
    score.scoring_model = provenance.get("model")
    score.scoring_prompt_file = provenance.get("prompt_file")
    score.scoring_prompt_hash = provenance.get("prompt_hash")
    score.scored_at = datetime.utcnow()


def _get_or_create_score(db: Session, submission: Submission) -> Score:
    """A retry (see routers/hr.py's retry-scoring) re-runs one of the
    scorer functions below against a submission that may already have a
    Score row (e.g. HR hand-scored it after an earlier failure, then
    retried anyway) - reuse that row instead of inserting a second one
    and violating scores.submission_id's uniqueness constraint. A fresh
    real LLM score also isn't an HR override anymore, so clear any
    override audit fields the reused row was carrying."""
    score = submission.score
    if score is None:
        score = Score(submission_id=submission.id)
        db.add(score)
    else:
        score.original_final_score = None
        score.overridden_by_user_id = None
        score.override_note = None
        score.overridden_at = None
    return score


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

    score = _get_or_create_score(db, submission)
    _apply_provenance(score, result)
    score.coverage_score = result.get("coverage_score")
    score.misses_json = result.get("misses", [])
    score.concept_coverage_json = result.get("concept_coverage", [])
    score.final_score = result.get("final_score")
    score.feedback_text = result.get("feedback_text")
    score.raw_llm_response_json = {"reference_rows": reference_rows, "scoring": result}
    submission.status = RoundStatus.scored
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

    score = _get_or_create_score(db, submission)
    _apply_provenance(score, result)
    score.coverage_score = result.get("coverage_score")
    score.misses_json = result.get("misses", [])
    score.final_score = result.get("final_score")
    score.feedback_text = result.get("feedback_text")
    score.raw_llm_response_json = {"reference_rows": reference_rows, "scoring": result}
    submission.status = RoundStatus.scored
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

    # archived.is_(False) matters for a re-applied candidate (see
    # CandidateAppearance) - without it, a candidate with more than one
    # round 1 submission (current + an old, archived one from a prior
    # cycle) could get scored against the WRONG scenario's title/
    # description/rows, since .first() with no ordering has no guarantee
    # of picking the current one. _round1_context_for (used for the live
    # round 3 UI) already filters this correctly - this lookup, used at
    # final scoring time, was the one place that didn't.
    round1_submission = (
        db.query(Submission)
        .filter(Submission.user_id == submission.user_id, Submission.round_number == 1, Submission.archived.is_(False))
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

    score = _get_or_create_score(db, submission)
    _apply_provenance(score, result)
    score.coverage_score = result.get("coverage_score")
    score.misses_json = result.get("misses", [])
    score.final_score = result.get("final_score")
    score.feedback_text = result.get("feedback_text")
    score.raw_llm_response_json = {"round1_context": round1_context, "test_cases": test_cases_payload, "scoring": result}
    submission.status = RoundStatus.scored
    db.commit()
    db.refresh(score)
    return score


_SCORERS = {
    1: score_round1_submission,
    2: score_round2_investigation,
    3: score_round3_submission,
}


def score_submission_in_background(submission_id: int) -> None:
    """The one entry point every submit endpoint's BackgroundTasks call
    and HR's retry-scoring endpoint (routers/hr.py) both go through -
    consolidates what used to be three near-identical, exception-unsafe
    _score_roundN_in_background functions in routers/candidate.py.

    Never lets a failed LLM call strand a submission at status="submitted"
    forever with nothing to show for it: on any exception (a bad LLM
    response, a network blip, a rate limit, ...) the submission moves to
    scoring_failed with the error message attached, visible to HR, who
    can retry (call this function again) or score it manually (see
    hr.py's PATCH /submissions/{id}/score)."""
    from ..database import SessionLocal  # local import: avoid circular import at module load

    db = SessionLocal()
    try:
        submission = db.get(Submission, submission_id)
        if submission is None:
            return
        scorer = _SCORERS.get(submission.round_number)
        if scorer is None:
            return
        # Clear any stale error from a previous failed attempt before
        # trying again - a successful retry must not leave old error
        # text sitting next to a fresh, real score.
        submission.scoring_error = None
        try:
            scorer(db, submission)
        except Exception as e:
            db.rollback()  # discard any half-formed pending changes (e.g. a Score added but not yet committed) before recording the failure
            submission.status = RoundStatus.scoring_failed
            submission.scoring_error = str(e)[:2000]
            db.commit()
    finally:
        db.close()
