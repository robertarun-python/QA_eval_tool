"""
Candidate-only endpoints: fetch the one live (published) scenario for the
current round + the candidate's own band, start the timer, submit, and view
past results. Rounds are gated in a fixed sequence (see ROUND_SEQUENCE
below) rather than by raw round-number arithmetic - round_number 3 was
freed up by the Round 3 -> Round 4 renumbering and has since been reused
for a new round (AI-prompted coding), unrelated to the manual-testing
round that used to live at that number.
"""
import threading
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import User, Scenario, Submission, RoundStatus, ConversationTurn, Round4TestCase, CandidateAppearance, Round3Turn, Round3ExecutionRun
from ..schemas import (
    RoundStateOut, SubmissionCreate, SubmissionOut,
    Round1ContextOut, Round4StateOut, Round4TurnCreate, Round4TurnOut,
    Round4TestCaseCreate, Round4TestCaseOut, Round4DraftUpdate, Round4EnvironmentOut,
    Round4UiMockupOut, Round2SubmissionCreate, Round4CodeSnippetOut, ExpireRoundPayload,
    Round3StartRequest, Round3DraftUpdate, Round3TurnCreate, Round3TurnOut,
    Round3RunInputCreate, Round3RunPollOut, Round3RunOut, Round3StateOut,
)
from ..dependencies import require_candidate
from ..services import llm_service, execution_service
from ..services.scoring_service import score_submission_in_background, close_expired_submissions

ROUND4_CODE_LANGUAGES = ("python", "java", "javascript")

# Serializes the generate-then-persist section of round4_turn_code per
# turn (see below) - two concurrent requests for the same (turn,
# language), e.g. the candidate opening the same turn's code view in
# two tabs, would otherwise both see the cache empty and both call the
# LLM, and whichever commits last would silently overwrite the other's
# cached code. One lock per turn_id (not a single global lock) so
# unrelated turns/candidates never wait on each other.
_round4_code_locks: dict[int, threading.Lock] = {}
_round4_code_locks_guard = threading.Lock()


def _round4_code_lock(turn_id: int) -> threading.Lock:
    with _round4_code_locks_guard:
        lock = _round4_code_locks.get(turn_id)
        if lock is None:
            lock = threading.Lock()
            _round4_code_locks[turn_id] = lock
        return lock

_VALID_PRIORITIES = ("High", "Medium", "Low")
_VALID_TYPES = ("Positive", "Negative", "Boundary", "Edge")


def _sanitize_expired_round1_row(row: dict) -> dict:
    """Round 1 content coming through /expire or /draft skips
    TestCaseRow's min_length validation on purpose (see
    ExpireRoundPayload) - a timed-out or in-progress round must save
    whatever's there, blank fields included, not get rejected for being
    incomplete. Still coerced to the same shape/types the rest of the
    app (scoring, HR's report view) expects, so a stray non-string or
    invalid literal from a malformed direct API call can't reach either
    of those."""
    return {
        "title": str(row.get("title") or ""),
        "preconditions": str(row.get("preconditions") or ""),
        "steps": str(row.get("steps") or ""),
        "expected_result": str(row.get("expected_result") or ""),
        "priority": row.get("priority") if row.get("priority") in _VALID_PRIORITIES else "Medium",
        "type": row.get("type") if row.get("type") in _VALID_TYPES else "Positive",
    }

# The one round still on the generic row-based /round/{round_number}/submit
# endpoint below. Rounds 2 and 4 each have their own dedicated submit
# endpoint (different payload shapes - see Round2SubmissionCreate; round
# 4 takes no body at all) registered further up this file, ahead of the
# generic route on purpose (see the routing-order note in the round 4
# section for why registration order matters here).
STRUCTURED_ROUNDS = (1,)

ROUND4_CONFIG_DEFAULTS = llm_service.DEFAULT_ROUND4_CONFIG

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
    """Highest round number the candidate has submitted (or scored) in
    their CURRENT cycle. 0 if none yet. Excludes archived submissions
    (see Submission.archived) - a reset candidate's old, already-scored
    rounds must not keep round-gating from unlocking round 1 again.

    Deliberately does NOT lazily close expired submissions itself (see
    scoring_service.close_expired_submissions) - this is called by
    _require_round_unlocked, which every round endpoint checks first,
    including the write endpoints (submit_round, submit_round2,
    round4_submit, start_round). Closing an expired submission there
    would risk discarding a real, in-flight submit payload for that
    exact round the instant it arrived even slightly late - the correct
    rejection for that is _require_within_time_limit's own 400, not a
    silent auto-close-with-empty-content underneath it. The lazy check
    only ever runs from read-only paths - see get_round below."""
    completed = (
        db.query(Submission.round_number)
        .filter(
            Submission.user_id == candidate.id,
            Submission.status.in_([RoundStatus.submitted, RoundStatus.scored]),
            Submission.archived.is_(False),
        )
        .all()
    )
    return max((r for (r,) in completed), default=0)


def _current_appearance_id(db: Session, candidate: User) -> int | None:
    """The candidate's current CandidateAppearance id, if they have one
    (only bulk-uploaded candidates do - see credential_service.py /
    candidate_upload_service.py). Tagged onto every new Submission so
    HR's "past appearances" drill-down can group a cycle's work - see
    routers/hr.py's appearance_report. None for the 3 seeded accounts,
    which is fine: their submissions were never grouped by appearance."""
    appearance = (
        db.query(CandidateAppearance)
        .filter(CandidateAppearance.user_id == candidate.id, CandidateAppearance.is_current.is_(True))
        .first()
    )
    return appearance.id if appearance else None


# The actual round sequence a candidate progresses through, in order.
# Gating walks this explicit sequence rather than comparing raw numbers
# (round_number > max_completed + 1) - see migrate_round_renumber.py /
# the Round 3 (AI-prompted coding) design spec for why round_number 3 was
# freed up and then reused for a different round than the one that used
# to occupy it.
ROUND_SEQUENCE = (1, 2, 3, 4)


def _require_round_unlocked(round_number: int, db: Session, candidate: User) -> None:
    if round_number not in ROUND_SEQUENCE:
        raise HTTPException(400, f"round_number must be one of {ROUND_SEQUENCE}")
    position = ROUND_SEQUENCE.index(round_number)
    if position == 0:
        return
    previous_round = ROUND_SEQUENCE[position - 1]
    if _max_completed_round(db, candidate) < previous_round:
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
# same routing-order reason round 4's endpoints are below: this would
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
        .filter(Submission.user_id == candidate.id, Submission.scenario_id == scenario.id, Submission.archived.is_(False))
        .first()
    )
    if submission is None:
        # Allow submitting without an explicit prior /start call too (e.g.
        # tests, or a client that just posts straight through).
        submission = Submission(
            user_id=candidate.id, scenario_id=scenario.id, round_number=2,
            started_at=datetime.utcnow(),
            appearance_id=_current_appearance_id(db, candidate),
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

    background_tasks.add_task(score_submission_in_background, submission.id)
    return submission


# ---- Round 3 (AI-prompted coding: the candidate never writes code
# directly - they direct the LLM turn by turn via dedicated endpoints
# below, registered ahead of the generic /round/{round_number}/... routes
# for the same routing-order reason Round 2/4's dedicated endpoints are -
# see those sections' comments.) ----

def _round3_coding_scenario_and_submission(candidate: User, db: Session) -> tuple[Scenario, Submission]:
    scenario = _live_scenario(db, 3, candidate)
    if scenario is None:
        raise HTTPException(404, "No published scenario for round 3 yet - check back once HR has published one.")
    submission = (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.scenario_id == scenario.id, Submission.archived.is_(False))
        .first()
    )
    if submission is None:
        raise HTTPException(404, "Round 3 hasn't been started yet - call /round/3/start first.")
    return scenario, submission


@router.post("/round/3/start", response_model=SubmissionOut, status_code=201)
def start_round3(payload: Round3StartRequest, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(3, db, candidate)
    scenario = _live_scenario(db, 3, candidate)
    if scenario is None:
        raise HTTPException(404, "No published scenario for round 3 yet - check back once HR has published one.")

    existing = (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.scenario_id == scenario.id, Submission.archived.is_(False))
        .first()
    )
    if existing is not None:
        return existing  # idempotent: same started_at, same language, same timer deadline

    submission = Submission(
        user_id=candidate.id, scenario_id=scenario.id, round_number=3,
        status=RoundStatus.in_progress, started_at=datetime.utcnow(),
        content={"language": payload.language, "draft_prompt": ""},
        appearance_id=_current_appearance_id(db, candidate),
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)
    return submission


@router.patch("/round/3/draft", status_code=204)
def round3_coding_save_draft(payload: Round3DraftUpdate, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(3, db, candidate)
    _, submission = _round3_coding_scenario_and_submission(candidate, db)
    if submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round has already been submitted.")
    submission.content = {**(submission.content or {}), "draft_prompt": payload.draft_prompt}
    db.commit()


@router.post("/round/3/turn", response_model=Round3TurnOut, status_code=201)
def round3_coding_turn(payload: Round3TurnCreate, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(3, db, candidate)
    scenario, submission = _round3_coding_scenario_and_submission(candidate, db)
    if submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round has already been submitted.")

    language = (submission.content or {}).get("language")
    existing_turns = submission.round3_turns
    conversation_so_far = [
        {
            "turn_number": t.turn_number, "candidate_prompt": t.candidate_prompt,
            "response_kind": t.response_kind, "response_message": t.response_message,
            "code_after": t.code_after,
        }
        for t in existing_turns
    ]
    current_code = next((t.code_after for t in reversed(existing_turns) if t.code_after), None)
    turn_number = len(existing_turns) + 1

    # Called synchronously in the request path (same reasoning as Round
    # 4's round4_turn - see that function's comment): nothing is
    # persisted below until the LLM call succeeds and validates.
    try:
        response = llm_service.round3_coding_turn(
            scenario_description=scenario.description,
            language=language,
            conversation_so_far=conversation_so_far,
            current_code=current_code,
            candidate_prompt=payload.candidate_prompt,
            turn_number=turn_number,
        )
    except Exception:
        raise HTTPException(502, "The assistant had trouble responding just now - try sending your message again.")

    turn = Round3Turn(
        submission_id=submission.id,
        turn_number=turn_number,
        candidate_prompt=payload.candidate_prompt,
        language=language,
        response_kind=response["response_kind"],
        response_message=response["response_message"],
        code_after=response.get("code_after"),
    )
    db.add(turn)
    submission.content = {**(submission.content or {}), "draft_prompt": ""}
    db.commit()
    db.refresh(turn)
    return turn


# One live InteractiveSession per submission at a time (see
# execution_service.InteractiveSession) - a candidate only ever directs
# one Run at a time for their own submission, so this is keyed simply by
# submission_id. In-memory only, deliberately not persisted: a server
# restart just drops any live session, and the candidate clicking Run
# again starts a fresh one - acceptable for this tool's scale, and far
# simpler than trying to resume a subprocess across a process restart.
_interactive_sessions: dict[int, execution_service.InteractiveSession] = {}


def _session_to_poll_out(session: execution_service.InteractiveSession) -> Round3RunPollOut:
    return Round3RunPollOut(
        stdout=session.stdout_buffer, stderr=session.stderr_buffer,
        exited=session.exited, exit_code=session.exit_code,
        timed_out=session.timed_out, infra_error=session.infra_error,
    )


def _persist_interactive_run(db: Session, submission: Submission, language: str, session: execution_service.InteractiveSession) -> None:
    """Called once, right as an InteractiveSession is first observed to
    have exited (from either /run/start or /run/poll, whichever gets
    there first) - writes the same Round3ExecutionRun shape run_code's
    batch path used to, so HR's report and Round3StateOut.runs (the
    "latest run" panel) don't need to know the run was interactive."""
    latest_turn = next((t for t in reversed(submission.round3_turns) if t.code_after), None)
    duration_ms = (
        int((time.monotonic() - session.started_monotonic) * 1000)
        if session.started_monotonic is not None else None
    )
    run = Round3ExecutionRun(
        submission_id=submission.id,
        turn_id=latest_turn.id if latest_turn else None,
        language=language,
        code_snapshot=latest_turn.code_after if latest_turn else "",
        stdin_json=session.stdin_sent,
        stdout=session.stdout_buffer,
        stderr=session.stderr_buffer,
        exit_code=session.exit_code,
        timed_out=session.timed_out,
        infra_error=session.infra_error,
        duration_ms=duration_ms,
    )
    db.add(run)
    db.commit()


@router.post("/round/3/run/start", response_model=Round3RunPollOut, status_code=201)
async def round3_coding_run_start(db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(3, db, candidate)
    _, submission = _round3_coding_scenario_and_submission(candidate, db)
    if submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round has already been submitted.")

    latest_turn = next((t for t in reversed(submission.round3_turns) if t.code_after), None)
    if latest_turn is None:
        raise HTTPException(400, "There's no code to run yet - direct the assistant to write some first.")

    # Clicking Run again while one is already live replaces it, same as
    # re-running a program in a real terminal would - not queued or
    # rejected.
    existing = _interactive_sessions.pop(submission.id, None)
    if existing is not None:
        await existing.stop()

    language = (submission.content or {}).get("language")
    session = await execution_service.start_interactive(language=language, code=latest_turn.code_after)
    if session.exited:
        # Never really started (unsupported infra, a Java compile
        # failure) - nothing to poll further, persist immediately.
        _persist_interactive_run(db, submission, language, session)
    else:
        _interactive_sessions[submission.id] = session
    return _session_to_poll_out(session)


@router.get("/round/3/run/poll", response_model=Round3RunPollOut)
def round3_coding_run_poll(db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(3, db, candidate)
    _, submission = _round3_coding_scenario_and_submission(candidate, db)
    session = _interactive_sessions.get(submission.id)
    if session is None:
        raise HTTPException(404, "No run in progress - click Run to start one.")
    if session.exited:
        language = (submission.content or {}).get("language")
        _persist_interactive_run(db, submission, language, session)
        _interactive_sessions.pop(submission.id, None)
    return _session_to_poll_out(session)


@router.post("/round/3/run/input", status_code=204)
async def round3_coding_run_input(payload: Round3RunInputCreate, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(3, db, candidate)
    _, submission = _round3_coding_scenario_and_submission(candidate, db)
    session = _interactive_sessions.get(submission.id)
    if session is None or session.exited:
        raise HTTPException(400, "No run in progress to send input to.")
    await session.write_input(payload.line)


@router.post("/round/3/run/stop", status_code=204)
async def round3_coding_run_stop(db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(3, db, candidate)
    _, submission = _round3_coding_scenario_and_submission(candidate, db)
    session = _interactive_sessions.pop(submission.id, None)
    if session is not None:
        await session.stop()


@router.get("/round/3/state", response_model=Round3StateOut)
def round3_coding_state(db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(3, db, candidate)
    scenario, submission = _round3_coding_scenario_and_submission(candidate, db)
    return Round3StateOut(
        scenario=scenario, submission=submission,
        language=(submission.content or {}).get("language"),
        turns=submission.round3_turns, runs=submission.round3_execution_runs,
    )


@router.post("/round/3/submit", response_model=SubmissionOut, status_code=201)
def round3_coding_submit(background_tasks: BackgroundTasks, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(3, db, candidate)
    scenario, submission = _round3_coding_scenario_and_submission(candidate, db)
    if submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round has already been submitted.")
    _require_within_time_limit(submission, scenario)

    if not submission.round3_turns:
        raise HTTPException(400, "Send at least one message before submitting.")

    submission.status = RoundStatus.submitted
    db.commit()
    db.refresh(submission)
    background_tasks.add_task(score_submission_in_background, submission.id)
    return submission


# ---- Round 4 (conversational, open-ended: the candidate creates their
# own self-titled test cases, no fixed category or ordering) ----
#
# Registered before the generic /round/{round_number}/... routes below
# on purpose: Starlette matches routes in registration order, and
# /round/4/submit would otherwise be shadowed by /round/{round_number}/submit
# (both match the literal path "/round/4/submit") - the generic one
# would win and this round's real endpoint would never be reached.
# /round/{n}/start further down already works unchanged for round 4 (it
# just creates the Submission row - there's no phase to initialize
# anymore). Everything else round-4-specific lives here rather than
# being forced through the row-based STRUCTURED_ROUNDS endpoints, since
# the shapes genuinely differ (a multi-turn conversation vs. a single
# list-of-rows payload).

def _round4_config(scenario: Scenario) -> dict:
    return {**ROUND4_CONFIG_DEFAULTS, **(scenario.config_json or {})}


def _round1_context_for(candidate: User, db: Session) -> Round1ContextOut:
    round1_submission = (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.round_number == 1, Submission.archived.is_(False))
        .first()
    )
    # Round gating guarantees this exists by the time round 4 is
    # reachable (round 4 only unlocks after round 1 is submitted) - this
    # is a defensive guard, not an expected user-facing path.
    if round1_submission is None:
        raise HTTPException(409, "No round 1 submission found - round 4 automates your round 1 answer, which has to exist first.")
    return Round1ContextOut(
        scenario_title=round1_submission.scenario.title,
        scenario_description=round1_submission.scenario.description,
        submitted_rows=round1_submission.content or [],
    )


def _round4_scenario_and_submission(candidate: User, db: Session) -> tuple[Scenario, Submission]:
    scenario = _live_scenario(db, 4, candidate)
    if scenario is None:
        raise HTTPException(404, "No published scenario for round 4 yet - check back once HR has published one.")
    submission = (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.scenario_id == scenario.id, Submission.archived.is_(False))
        .first()
    )
    if submission is None:
        raise HTTPException(404, "Round 4 hasn't been started yet - call /round/4/start first.")
    return scenario, submission


def _test_case_out(tc: Round4TestCase) -> Round4TestCaseOut:
    return Round4TestCaseOut(
        id=tc.id, title=tc.title, draft_prompt=tc.draft_prompt,
        created_at=tc.created_at, turn_count=len(tc.turns),
    )


def _build_round4_state(scenario: Scenario, submission: Submission, candidate: User, db: Session) -> Round4StateOut:
    environment = Round4EnvironmentOut(**scenario.environment_json) if scenario.environment_json else None
    ui_mockup = Round4UiMockupOut(**scenario.ui_mockup_json) if scenario.ui_mockup_json else None
    return Round4StateOut(
        scenario=scenario,
        submission=submission,
        round1_context=_round1_context_for(candidate, db),
        environment=environment,
        ui_mockup=ui_mockup,
        test_cases=[_test_case_out(tc) for tc in submission.round4_test_cases],
        turns=submission.conversation_turns,
    )


def _owned_test_case(test_case_id: int, submission: Submission, db: Session) -> Round4TestCase:
    tc = db.get(Round4TestCase, test_case_id)
    if tc is None or tc.submission_id != submission.id:
        raise HTTPException(404, "No such test case on this submission.")
    return tc


@router.get("/round/4/state", response_model=Round4StateOut)
def round4_state(db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(4, db, candidate)
    scenario, submission = _round4_scenario_and_submission(candidate, db)
    return _build_round4_state(scenario, submission, candidate, db)


@router.post("/round/4/test-case", response_model=Round4TestCaseOut, status_code=201)
def round4_create_test_case(
    payload: Round4TestCaseCreate,
    db: Session = Depends(get_db),
    candidate: User = Depends(require_candidate),
):
    _require_round_unlocked(4, db, candidate)
    _, submission = _round4_scenario_and_submission(candidate, db)
    if submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round has already been submitted.")

    tc = Round4TestCase(submission_id=submission.id, title=payload.title)
    db.add(tc)
    db.commit()
    db.refresh(tc)
    return _test_case_out(tc)


@router.patch("/round/4/test-case/{test_case_id}/draft", status_code=204)
def round4_save_draft(
    test_case_id: int,
    payload: Round4DraftUpdate,
    db: Session = Depends(get_db),
    candidate: User = Depends(require_candidate),
):
    _require_round_unlocked(4, db, candidate)
    _, submission = _round4_scenario_and_submission(candidate, db)
    if submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round has already been submitted.")

    tc = _owned_test_case(test_case_id, submission, db)
    tc.draft_prompt = payload.draft_prompt
    db.commit()


@router.post("/round/4/turn", response_model=Round4TurnOut, status_code=201)
def round4_turn(payload: Round4TurnCreate, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(4, db, candidate)
    scenario, submission = _round4_scenario_and_submission(candidate, db)
    if submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round has already been submitted.")

    test_case = _owned_test_case(payload.test_case_id, submission, db)
    config = _round4_config(scenario)
    existing_turns = test_case.turns

    round1_context = _round1_context_for(candidate, db)
    conversation_so_far = [
        {"candidate_prompt": t.candidate_prompt, "model_response": t.model_response}
        for t in existing_turns
    ]
    turn_number = len(existing_turns) + 1

    # Called synchronously in the request path (unlike round 1/2/4 scoring,
    # which run as a background task) - a bad response here (malformed
    # JSON, an API error, or a shape that doesn't match Round4TurnResponse
    # - see llm_service.round4_respond) must never reach db.add() below.
    # Nothing has been persisted yet at this point, so this is a clean,
    # retryable failure for the candidate - not the stuck-forever state a
    # persisted-then-invalid turn used to cause on every later read of
    # this candidate's round 4 state.
    try:
        response = llm_service.round4_respond(
            test_case_title=test_case.title or "",
            environment=scenario.environment_json,
            scenario_instructions=scenario.description,
            round1_context=round1_context.model_dump(),
            conversation_so_far=conversation_so_far,
            candidate_prompt=payload.candidate_prompt,
            assistance_pct=config["assistance_pct"],
            turn_number=turn_number,
        )
    except Exception:
        raise HTTPException(502, "The assistant had trouble responding just now - try sending your message again.")

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


@router.get("/round/4/turn/{turn_id}/code", response_model=Round4CodeSnippetOut)
def round4_turn_code(turn_id: int, language: str, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    """Trial feature: an on-demand, candidate-facing rendering of an
    already-completed turn as a code snippet, in a language the candidate
    picks. Generated from that turn's OWN already-recorded steps/
    observed_result - this can only re-describe what's already visible
    in the transcript, never reveal anything new, and carries no scoring
    weight. Deliberately isolated from the rest of round 4 so it's easy
    to remove if it doesn't hold up.

    Persisted per (turn, language) in ConversationTurn.generated_code_json
    rather than generated fresh every call - the LLM isn't deterministic,
    so without this, revisiting the same turn could show meaningfully
    different code each time, which is confusing for something meant to
    just be a fixed re-rendering of a decision already made. Also means
    switching back to an already-viewed language costs nothing."""
    _require_round_unlocked(4, db, candidate)
    if language not in ROUND4_CODE_LANGUAGES:
        raise HTTPException(400, f"language must be one of: {', '.join(ROUND4_CODE_LANGUAGES)}")
    _, submission = _round4_scenario_and_submission(candidate, db)
    turn = db.get(ConversationTurn, turn_id)
    if turn is None or turn.submission_id != submission.id:
        raise HTTPException(404, "No such turn on this submission.")

    cached = (turn.generated_code_json or {}).get(language)
    if cached is not None:
        return Round4CodeSnippetOut(language=language, code=cached)

    with _round4_code_lock(turn_id):
        # Another request for this exact turn may have generated and
        # committed while we were waiting for the lock - re-read rather
        # than trust the pre-lock snapshot above.
        db.refresh(turn)
        cached = (turn.generated_code_json or {}).get(language)
        if cached is not None:
            return Round4CodeSnippetOut(language=language, code=cached)

        try:
            code = llm_service.generate_round4_code_snippet(
                test_case_title=turn.test_case.title,
                steps=turn.model_response.get("steps", []),
                observed_result=turn.model_response.get("observed_result", ""),
                language=language,
            )
        except Exception:
            raise HTTPException(502, "Couldn't generate a code snippet just now - try again.")

        turn.generated_code_json = {**(turn.generated_code_json or {}), language: code}
        db.commit()
        return Round4CodeSnippetOut(language=language, code=code)


@router.post("/round/4/submit", response_model=SubmissionOut, status_code=201)
def round4_submit(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    candidate: User = Depends(require_candidate),
):
    _require_round_unlocked(4, db, candidate)
    scenario, submission = _round4_scenario_and_submission(candidate, db)
    if submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round has already been submitted.")

    _require_within_time_limit(submission, scenario)

    if not submission.round4_test_cases:
        raise HTTPException(400, "Create at least one test case before submitting.")
    if not any(tc.turns for tc in submission.round4_test_cases):
        raise HTTPException(400, "Send at least one message in a test case before submitting.")

    submission.status = RoundStatus.submitted
    db.commit()
    db.refresh(submission)

    background_tasks.add_task(score_submission_in_background, submission.id)
    return submission


@router.get("/round/{round_number}", response_model=RoundStateOut)
def get_round(round_number: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    if round_number not in (1, 2, 3, 4):
        raise HTTPException(400, "round_number must be 1, 2, 3, or 4")

    # Lazily close out any of this candidate's abandoned-and-expired
    # submissions before checking round-gating below - unlike the write
    # endpoints (see _max_completed_round's docstring for why they don't
    # do this), a GET is read-only, so there's no in-flight payload this
    # could ever clobber. Runs first so an earlier round abandoned past
    # its deadline doesn't keep blocking access to a later one forever -
    # see scoring_service.close_expired_submissions.
    current = (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.archived.is_(False))
        .all()
    )
    close_expired_submissions(db, current, background_tasks)

    _require_round_unlocked(round_number, db, candidate)

    scenario = _live_scenario(db, round_number, candidate)
    if scenario is None:
        return RoundStateOut(scenario=None, submission=None)

    submission = (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.scenario_id == scenario.id, Submission.archived.is_(False))
        .first()
    )
    return RoundStateOut(scenario=scenario, submission=submission)


@router.post("/round/{round_number}/start", response_model=SubmissionOut, status_code=201)
def start_round(round_number: int, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    if round_number not in (1, 2, 4):
        raise HTTPException(400, "round_number must be 1, 2, or 4")
    _require_round_unlocked(round_number, db, candidate)

    scenario = _live_scenario(db, round_number, candidate)
    if scenario is None:
        raise HTTPException(404, "No published scenario for this round yet - check back once HR has published one.")

    existing = (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.scenario_id == scenario.id, Submission.archived.is_(False))
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
        appearance_id=_current_appearance_id(db, candidate),
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
        .filter(Submission.user_id == candidate.id, Submission.scenario_id == scenario.id, Submission.archived.is_(False))
        .first()
    )
    if submission is None:
        # Allow submitting without an explicit prior /start call too (e.g.
        # tests, or a client that just posts straight through).
        submission = Submission(
            user_id=candidate.id, scenario_id=scenario.id, round_number=round_number,
            started_at=datetime.utcnow(),
            appearance_id=_current_appearance_id(db, candidate),
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
    background_tasks.add_task(score_submission_in_background, submission.id)

    return submission


@router.post("/round/{round_number}/tab-switch", status_code=204)
def log_tab_switch(round_number: int, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    """Fire-and-forget telemetry from app.js's tab-switch guard: the
    candidate left this tab/app while a round's timer was running. Purely
    passive, same as how real assessment platforms handle this (see
    app.js's tab-switch guard) - never blocks the candidate or interrupts
    the round, just logs it for HR to see later (Submission.
    tab_switch_events_json). Silently a no-op if there's nothing
    in-progress to attach it to (the round may have already ended by the
    time this request lands)."""
    if round_number not in (1, 2, 3, 4):
        raise HTTPException(400, "round_number must be 1, 2, 3, or 4")
    scenario = _live_scenario(db, round_number, candidate)
    if scenario is None:
        return
    submission = (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.scenario_id == scenario.id, Submission.archived.is_(False))
        .first()
    )
    if submission is None or submission.status != RoundStatus.in_progress:
        return
    # A fresh list, not an in-place mutation of the loaded one - SQLAlchemy
    # doesn't track in-place JSON-column mutations, so appending to the
    # existing list and reassigning it (same object identity) can leave
    # the row un-flushed. See migrate_tab_switch_guard.py.
    events = list(submission.tab_switch_events_json or [])
    events.append(datetime.utcnow().isoformat())
    submission.tab_switch_events_json = events
    db.commit()


@router.patch("/round/{round_number}/draft", status_code=204)
def save_round_draft(
    round_number: int,
    payload: ExpireRoundPayload,
    db: Session = Depends(get_db),
    candidate: User = Depends(require_candidate),
):
    """Periodic autosave for rounds 1/2's in-progress content, the same
    pattern round 4's test cases already have (see round4_save_draft
    above) - so a crash, refresh, or network loss mid-round doesn't
    silently lose typed-but-unsubmitted work while the timer keeps
    counting down. Reuses ExpireRoundPayload's shape (deliberately
    permissive - see its docstring), but unlike /expire below this never
    touches submission.status or checks the deadline: only a real
    Submit, or /expire once the deadline has genuinely passed, ever ends
    the round. Round 4 has no equivalent here - it already autosaves per
    test case instead (PATCH /round/4/test-case/{id}/draft), since it
    has no single whole-round form the way rounds 1/2 do."""
    if round_number not in (1, 2):
        raise HTTPException(400, "round_number must be 1 or 2 - round 4 autosaves per test case, see PATCH /round/4/test-case/{id}/draft.")
    _require_round_unlocked(round_number, db, candidate)
    scenario = _live_scenario(db, round_number, candidate)
    if scenario is None:
        raise HTTPException(404, "No published scenario for this round.")
    submission = (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.scenario_id == scenario.id, Submission.archived.is_(False))
        .first()
    )
    if submission is None or submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round isn't in progress - nothing to autosave.")

    if round_number == 1:
        submission.content = [_sanitize_expired_round1_row(r) for r in payload.content]
    else:
        # Unlike /expire below (which drops a row with no area filled in
        # - a finalized answer should only keep what's genuinely there),
        # this keeps every row exactly as typed, including a blank one
        # mid-edit: a draft restore has to reproduce the exact editing
        # state, not silently delete a row the candidate hasn't finished
        # typing into yet.
        submission.content = {
            "investigation": [{"area": str(r.get("area") or "")} for r in payload.investigation],
            "root_cause": payload.root_cause or "",
        }
    db.commit()


@router.post("/round/{round_number}/expire", response_model=SubmissionOut)
def expire_round(
    round_number: int,
    payload: ExpireRoundPayload,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    candidate: User = Depends(require_candidate),
):
    """The frontend's guaranteed fallback once a round's timer hits zero:
    it always tries a real submit first (see app.js's doSubmitRound1/
    doSubmitRound2Investigation/round4AutoSubmit with force=true), and
    only calls this if that attempt failed - empty/incomplete content
    that a normal submit would correctly reject, or the rare case of
    losing a race against _require_within_time_limit. Either way the
    round has to end here, as a real submission: whatever draft content
    the candidate had (possibly none at all) gets saved as-is and the
    round moves to "submitted" exactly like a normal submit, so it's
    scored and the next round unlocks. A candidate must never be left
    staring at an expired timer with no way forward - that includes the
    case where nothing was ever written; "didn't attempt it" is a scoring
    outcome, not a reason to strand the round. Requires the deadline to
    have genuinely passed server-side, not just claimed by the client, so
    this can't be used to skip a round early."""
    if round_number not in (1, 2, 3, 4):
        raise HTTPException(400, "round_number must be 1, 2, 3, or 4")
    scenario = _live_scenario(db, round_number, candidate)
    if scenario is None:
        raise HTTPException(404, "No published scenario for this round.")
    submission = (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.scenario_id == scenario.id, Submission.archived.is_(False))
        .first()
    )
    if submission is None or submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round isn't in progress - nothing to expire.")
    if submission.started_at is not None:
        deadline = submission.started_at + timedelta(minutes=scenario.time_limit_minutes)
        if datetime.utcnow() < deadline:
            raise HTTPException(400, "This round's time limit hasn't passed yet.")

    if round_number == 1:
        submission.content = [_sanitize_expired_round1_row(r) for r in payload.content]
    elif round_number == 2:
        submission.content = {
            "investigation": [
                {"area": str(r.get("area") or "")} for r in payload.investigation if str(r.get("area") or "").strip()
            ],
            "root_cause": payload.root_cause or "",
        }
    # Rounds 3 and 4 have no content field to set here - their state
    # already lives in round3_turns/round3_execution_runs and
    # round4_test_cases/conversation_turns respectively; whatever exists
    # (including none) is what gets scored.

    submission.status = RoundStatus.submitted
    submission.auto_closed_reason = "Time limit reached without a manual submit"
    db.commit()
    db.refresh(submission)

    background_tasks.add_task(score_submission_in_background, submission.id)
    return submission


@router.get("/submissions", response_model=list[SubmissionOut])
def my_submissions(db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    # Current cycle only - a re-applying candidate's old, archived
    # submissions are HR's history to see (see hr.py's appearances
    # endpoints), not something the candidate encounters again themselves.
    return (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.archived.is_(False))
        .order_by(Submission.created_at.desc())
        .all()
    )
