"""
Candidate-only endpoints: fetch the one live (published) scenario for the
current round + the candidate's own band, start the timer, submit, and view
past results. Rounds are gated - a candidate can't reach round N until
round N-1 has been submitted.
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import User, Scenario, Submission, RoundStatus, ConversationTurn, Round3TestCase
from ..schemas import (
    RoundStateOut, SubmissionCreate, SubmissionOut,
    Round1ContextOut, Round3StateOut, Round3TurnCreate, Round3TurnOut,
    Round3TestCaseCreate, Round3TestCaseOut, Round3DraftUpdate, Round3EnvironmentOut,
    Round3UiMockupOut, Round2SubmissionCreate,
)
from ..dependencies import require_candidate
from ..services import llm_service
from ..services.scoring_service import score_round1_submission, score_round2_investigation, score_round3_submission

# The one round still on the generic row-based /round/{round_number}/submit
# endpoint below. Rounds 2 and 3 each have their own dedicated submit
# endpoint (different payload shapes - see Round2SubmissionCreate; round
# 3 takes no body at all) registered further up this file, ahead of the
# generic route on purpose (see the routing-order note in the round 3
# section for why registration order matters here).
STRUCTURED_ROUNDS = (1,)

ROUND3_CONFIG_DEFAULTS = llm_service.DEFAULT_ROUND3_CONFIG

router = APIRouter(prefix="/candidate", tags=["candidate"])


def _live_scenario(db: Session, round_number: int, candidate: User) -> Scenario | None:
    return (
        db.query(Scenario)
        .filter(
            Scenario.round_number == round_number,
            Scenario.experience_band == candidate.experience_band,
            Scenario.is_live.is_(True),
        )
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


def _require_within_time_limit(submission: Submission, scenario: Scenario) -> None:
    """Server-side backstop for the timed assessment - the client-side
    countdown (startTimer in app.js) is what candidates see, but nothing
    stopped a direct API call from submitting long after it expired
    without this. submission_grace_seconds covers ordinary clock drift
    and the auto-submit request's own network latency, not real extra
    time - the UI's own timer already fires right at zero."""
    if submission.started_at is None:
        return  # defensive only - every real submission has a server-set started_at
    deadline = submission.started_at + timedelta(
        minutes=scenario.time_limit_minutes, seconds=settings.submission_grace_seconds,
    )
    if datetime.utcnow() > deadline:
        raise HTTPException(400, "Time limit for this round has passed - it can no longer be submitted.")


# ---- Round 2 (debugging investigation, one-shot submit) ----
#
# Registered before the generic /round/{round_number}/... routes for the
# same routing-order reason round 3's endpoints are below: this would
# otherwise be shadowed by /round/{round_number}/submit. Round 2's
# candidate payload (investigation rows + one root-cause conclusion) no
# longer matches SubmissionCreate's test-case-row shape - see
# schemas.Round2SubmissionCreate - so it needs its own endpoint rather
# than reusing the generic one the way it used to.

@router.post("/round/2/submit", response_model=SubmissionOut, status_code=201)
def submit_round2(
    payload: Round2SubmissionCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    candidate: User = Depends(require_candidate),
):
    _require_round_unlocked(2, db, candidate)
    scenario = _live_scenario(db, 2, candidate)
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
            user_id=candidate.id, scenario_id=scenario.id, round_number=2,
            started_at=datetime.utcnow(),
        )
        db.add(submission)
    elif submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round has already been submitted.")

    _require_within_time_limit(submission, scenario)

    submission.content = {
        "investigation": [row.model_dump() for row in payload.investigation],
        "root_cause": payload.root_cause,
    }
    submission.status = RoundStatus.submitted
    db.commit()
    db.refresh(submission)

    background_tasks.add_task(_score_round2_in_background, submission.id)
    return submission


def _score_round2_in_background(submission_id: int):
    from ..database import SessionLocal  # local import: avoid circular import at module load

    db = SessionLocal()
    try:
        submission = db.get(Submission, submission_id)
        if submission is not None:
            score_round2_investigation(db, submission)
    finally:
        db.close()


# ---- Round 3 (conversational, open-ended: the candidate creates their
# own self-titled test cases, no fixed category or ordering) ----
#
# Registered before the generic /round/{round_number}/... routes below
# on purpose: Starlette matches routes in registration order, and
# /round/3/submit would otherwise be shadowed by /round/{round_number}/submit
# (both match the literal path "/round/3/submit") - the generic one
# would win and this round's real endpoint would never be reached.
# /round/{n}/start further down already works unchanged for round 3 (it
# just creates the Submission row - there's no phase to initialize
# anymore). Everything else round-3-specific lives here rather than
# being forced through the row-based STRUCTURED_ROUNDS endpoints, since
# the shapes genuinely differ (a multi-turn conversation vs. a single
# list-of-rows payload).

def _round3_config(scenario: Scenario) -> dict:
    return {**ROUND3_CONFIG_DEFAULTS, **(scenario.config_json or {})}


def _round1_context_for(candidate: User, db: Session) -> Round1ContextOut:
    round1_submission = (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.round_number == 1)
        .first()
    )
    # Round gating guarantees this exists by the time round 3 is
    # reachable (round 3 only unlocks after round 1 is submitted) - this
    # is a defensive guard, not an expected user-facing path.
    if round1_submission is None:
        raise HTTPException(409, "No round 1 submission found - round 3 automates your round 1 answer, which has to exist first.")
    return Round1ContextOut(
        scenario_title=round1_submission.scenario.title,
        scenario_description=round1_submission.scenario.description,
        submitted_rows=round1_submission.content or [],
    )


def _round3_scenario_and_submission(candidate: User, db: Session) -> tuple[Scenario, Submission]:
    scenario = _live_scenario(db, 3, candidate)
    if scenario is None:
        raise HTTPException(404, "No published scenario for round 3 yet - check back once HR has published one.")
    submission = (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.scenario_id == scenario.id)
        .first()
    )
    if submission is None:
        raise HTTPException(404, "Round 3 hasn't been started yet - call /round/3/start first.")
    return scenario, submission


def _test_case_out(tc: Round3TestCase) -> Round3TestCaseOut:
    return Round3TestCaseOut(
        id=tc.id, title=tc.title, draft_prompt=tc.draft_prompt,
        created_at=tc.created_at, turn_count=len(tc.turns),
    )


def _build_round3_state(scenario: Scenario, submission: Submission, candidate: User, db: Session) -> Round3StateOut:
    environment = Round3EnvironmentOut(**scenario.environment_json) if scenario.environment_json else None
    ui_mockup = Round3UiMockupOut(**scenario.ui_mockup_json) if scenario.ui_mockup_json else None
    return Round3StateOut(
        scenario=scenario,
        submission=submission,
        round1_context=_round1_context_for(candidate, db),
        environment=environment,
        ui_mockup=ui_mockup,
        test_cases=[_test_case_out(tc) for tc in submission.round3_test_cases],
        turns=submission.conversation_turns,
    )


def _owned_test_case(test_case_id: int, submission: Submission, db: Session) -> Round3TestCase:
    tc = db.get(Round3TestCase, test_case_id)
    if tc is None or tc.submission_id != submission.id:
        raise HTTPException(404, "No such test case on this submission.")
    return tc


@router.get("/round/3/state", response_model=Round3StateOut)
def round3_state(db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(3, db, candidate)
    scenario, submission = _round3_scenario_and_submission(candidate, db)
    return _build_round3_state(scenario, submission, candidate, db)


@router.post("/round/3/test-case", response_model=Round3TestCaseOut, status_code=201)
def round3_create_test_case(
    payload: Round3TestCaseCreate,
    db: Session = Depends(get_db),
    candidate: User = Depends(require_candidate),
):
    _require_round_unlocked(3, db, candidate)
    _, submission = _round3_scenario_and_submission(candidate, db)
    if submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round has already been submitted.")

    tc = Round3TestCase(submission_id=submission.id, title=payload.title)
    db.add(tc)
    db.commit()
    db.refresh(tc)
    return _test_case_out(tc)


@router.patch("/round/3/test-case/{test_case_id}/draft", status_code=204)
def round3_save_draft(
    test_case_id: int,
    payload: Round3DraftUpdate,
    db: Session = Depends(get_db),
    candidate: User = Depends(require_candidate),
):
    _require_round_unlocked(3, db, candidate)
    _, submission = _round3_scenario_and_submission(candidate, db)
    if submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round has already been submitted.")

    tc = _owned_test_case(test_case_id, submission, db)
    tc.draft_prompt = payload.draft_prompt
    db.commit()


@router.post("/round/3/turn", response_model=Round3TurnOut, status_code=201)
def round3_turn(payload: Round3TurnCreate, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(3, db, candidate)
    scenario, submission = _round3_scenario_and_submission(candidate, db)
    if submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round has already been submitted.")

    test_case = _owned_test_case(payload.test_case_id, submission, db)
    config = _round3_config(scenario)
    existing_turns = test_case.turns

    round1_context = _round1_context_for(candidate, db)
    conversation_so_far = [
        {"candidate_prompt": t.candidate_prompt, "model_response": t.model_response}
        for t in existing_turns
    ]
    turn_number = len(existing_turns) + 1

    response = llm_service.round3_respond(
        test_case_title=test_case.title or "",
        environment=scenario.environment_json,
        scenario_instructions=scenario.description,
        round1_context=round1_context.model_dump(),
        conversation_so_far=conversation_so_far,
        candidate_prompt=payload.candidate_prompt,
        assistance_pct=config["assistance_pct"],
        turn_number=turn_number,
    )

    turn = ConversationTurn(
        submission_id=submission.id,
        test_case_id=test_case.id,
        turn_number=turn_number,
        candidate_prompt=payload.candidate_prompt,
        model_response=response,
    )
    db.add(turn)
    # The message just described has now actually been sent - clear the
    # autosaved draft so a stale copy doesn't linger in the composer.
    test_case.draft_prompt = ""
    db.commit()
    db.refresh(turn)
    return turn


@router.post("/round/3/submit", response_model=SubmissionOut, status_code=201)
def round3_submit(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    candidate: User = Depends(require_candidate),
):
    _require_round_unlocked(3, db, candidate)
    scenario, submission = _round3_scenario_and_submission(candidate, db)
    if submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round has already been submitted.")

    _require_within_time_limit(submission, scenario)

    if not submission.round3_test_cases:
        raise HTTPException(400, "Create at least one test case before submitting.")
    if not any(tc.turns for tc in submission.round3_test_cases):
        raise HTTPException(400, "Send at least one message in a test case before submitting.")

    submission.status = RoundStatus.submitted
    db.commit()
    db.refresh(submission)

    background_tasks.add_task(_score_round3_in_background, submission.id)
    return submission


def _score_round3_in_background(submission_id: int):
    from ..database import SessionLocal  # local import: avoid circular import at module load

    db = SessionLocal()
    try:
        submission = db.get(Submission, submission_id)
        if submission is not None:
            score_round3_submission(db, submission)
    finally:
        db.close()


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
    if round_number not in STRUCTURED_ROUNDS:
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

    _require_within_time_limit(submission, scenario)

    submission.content = [row.model_dump() for row in payload.content]
    submission.status = RoundStatus.submitted
    db.commit()
    db.refresh(submission)

    # Scoring calls the LLM, which can take a few seconds - run it after
    # the response is sent so the candidate isn't stuck on a spinner, and
    # so the next round is unlocked immediately (see plan: don't block
    # round progression on background scoring).
    background_tasks.add_task(_score_round1_in_background, submission.id)

    return submission


def _score_round1_in_background(submission_id: int):
    from ..database import SessionLocal  # local import: avoid circular import at module load

    db = SessionLocal()
    try:
        submission = db.get(Submission, submission_id)
        if submission is not None:
            score_round1_submission(db, submission)
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
