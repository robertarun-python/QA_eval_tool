"""
Candidate-only endpoints: fetch the one live (published) scenario for the
current round + the candidate's own band, start the timer, submit, and view
past results. Rounds are gated - a candidate can't reach round N until
round N-1 has been submitted.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User, Scenario, ScenarioStatus, Submission, RoundStatus
from ..schemas import RoundStateOut, SubmissionCreate, SubmissionOut
from ..dependencies import require_candidate
from ..services.scoring_service import score_round1

router = APIRouter(prefix="/candidate", tags=["candidate"])


def _live_scenario(db: Session, round_number: int, candidate: User) -> Scenario | None:
    return (
        db.query(Scenario)
        .filter(
            Scenario.round_number == round_number,
            Scenario.experience_band == candidate.experience_band,
            Scenario.status == ScenarioStatus.published,
        )
        .order_by(Scenario.published_at.desc())
        .first()
    )


def _max_completed_round(db: Session, candidate: User) -> int:
    """Highest round number the candidate has submitted (or scored). 0 if none yet."""
    completed = (
        db.query(Submission.round_number)
        .filter(
            Submission.user_id == candidate.id,
            Submission.status.in_([RoundStatus.submitted, RoundStatus.scored]),
        )
        .all()
    )
    return max((r for (r,) in completed), default=0)


def _require_round_unlocked(round_number: int, db: Session, candidate: User) -> None:
    if round_number > _max_completed_round(db, candidate) + 1:
        raise HTTPException(403, f"Round {round_number} isn't unlocked yet - complete the earlier rounds first.")


@router.get("/round/{round_number}", response_model=RoundStateOut)
def get_round(round_number: int, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    if round_number not in (1, 2, 3):
        raise HTTPException(400, "round_number must be 1, 2, or 3")
    _require_round_unlocked(round_number, db, candidate)

    scenario = _live_scenario(db, round_number, candidate)
    if scenario is None:
        return RoundStateOut(scenario=None, submission=None)

    submission = (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.scenario_id == scenario.id)
        .first()
    )
    return RoundStateOut(scenario=scenario, submission=submission)


@router.post("/round/{round_number}/start", response_model=SubmissionOut, status_code=201)
def start_round(round_number: int, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    if round_number not in (1, 2, 3):
        raise HTTPException(400, "round_number must be 1, 2, or 3")
    _require_round_unlocked(round_number, db, candidate)

    scenario = _live_scenario(db, round_number, candidate)
    if scenario is None:
        raise HTTPException(404, "No published scenario for this round yet - check back once HR has published one.")

    existing = (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.scenario_id == scenario.id)
        .first()
    )
    if existing is not None:
        return existing  # idempotent: same started_at, same timer deadline

    submission = Submission(
        user_id=candidate.id,
        scenario_id=scenario.id,
        round_number=round_number,
        status=RoundStatus.in_progress,
        started_at=datetime.utcnow(),
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)
    return submission


@router.post("/round/{round_number}/submit", response_model=SubmissionOut, status_code=201)
def submit_round(
    round_number: int,
    payload: SubmissionCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    candidate: User = Depends(require_candidate),
):
    if round_number != 1:
        raise HTTPException(501, f"Round {round_number} is not implemented yet - see TODO(round{round_number}) in llm_service.py.")
    _require_round_unlocked(round_number, db, candidate)

    scenario = _live_scenario(db, round_number, candidate)
    if scenario is None:
        raise HTTPException(404, "No published scenario for this round.")

    submission = (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.scenario_id == scenario.id)
        .first()
    )
    if submission is None:
        # Allow submitting without an explicit prior /start call too (e.g.
        # tests, or a client that just posts straight through).
        submission = Submission(
            user_id=candidate.id, scenario_id=scenario.id, round_number=round_number,
            started_at=datetime.utcnow(),
        )
        db.add(submission)
    elif submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round has already been submitted.")

    submission.content = [row.model_dump() for row in payload.content]
    submission.status = RoundStatus.submitted
    db.commit()
    db.refresh(submission)

    # Scoring calls the LLM, which can take a few seconds - run it after
    # the response is sent so the candidate isn't stuck on a spinner, and
    # so the next round is unlocked immediately (see plan: don't block
    # round progression on background scoring).
    background_tasks.add_task(_score_in_background, submission.id)

    return submission


def _score_in_background(submission_id: int):
    from ..database import SessionLocal  # local import: avoid circular import at module load

    db = SessionLocal()
    try:
        submission = db.get(Submission, submission_id)
        if submission is not None:
            score_round1(db, submission)
    finally:
        db.close()


@router.get("/submissions", response_model=list[SubmissionOut])
def my_submissions(db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    return (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id)
        .order_by(Submission.created_at.desc())
        .all()
    )
