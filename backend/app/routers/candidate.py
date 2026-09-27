"""
Candidate-only endpoints: fetch the one live (published) scenario for the
current round + the candidate's own band, start the timer, submit, and view
past results. Rounds are gated in a fixed sequence (see ROUND_SEQUENCE
below) rather than by raw round-number arithmetic - round_number 3 was
freed up by the Round 3 -> Round 2 renumbering and has since been reused
for a new round (AI-prompted coding), unrelated to the manual-testing
round that used to live at that number.
"""
import hashlib
import json
import re
import threading
import time
import traceback
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import User, Scenario, Submission, RoundStatus, Round4TestCase, CandidateAppearance, Round3Turn, Round3ExecutionRun
from ..schemas import (
    RoundStateOut, SubmissionCreate, SubmissionOut,
    Round1ContextOut, Round2EntryStateOut, Round4TestCaseOut, Round2AutomationEnvironmentOut,
    Round2AutomationUiMockupOut, Round2SubmissionCreate, ExpireRoundPayload, TabSwitchOut,
    Round3StartRequest, Round3DraftUpdate, Round3TurnCreate, Round3TurnOut, Round3DirectEditCreate,
    Round3RunInputCreate, Round3RunPollOut, Round3StateOut,
    Round2AutomationStateOut, Round2AutomationDesignRowOut, Round2AutomationLanguageCreate, Round2AutomationSelectCreate, Round2AutomationRefineCreate,
    Round2AutomationTestDataUpdate,
    Round2AutomationTurnCreate, Round2AutomationTurnOut, Round2AutomationClarifyCreate, Round2AutomationCodeUpdate, Round2AutomationRunCreate,
    Round2AutomationSubmitCreate, Round2AutomationTCSubmitEntry, Round2AutomationRunOut, Round2AutomationTCStateOut,
)
from ..dependencies import require_candidate
from ..services import llm_service, execution_service, round_scenarios, round2_typist
from ..services.practice_engine import practice_run
from ..services.scoring_service import score_submission_in_background, close_expired_submissions

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
# generic route on purpose (see the routing-order note in the round 2
# section for why registration order matters here).
STRUCTURED_ROUNDS = (1,)


router = APIRouter(prefix="/candidate", tags=["candidate"])


def _live_scenario(db: Session, round_number: int, candidate: User) -> Scenario | None:
    """The scenario this candidate takes for this round - see
    services/round_scenarios.candidate_scenario (shared with the
    assessment-window close, so the two can't disagree)."""
    return round_scenarios.candidate_scenario(db, candidate, round_number)


def _round2_for_round1(db: Session, round1: Scenario | None) -> Scenario | None:
    return round_scenarios.round2_for_round1(db, round1)


def _round1_environment_view(round2: Scenario) -> tuple[Round2AutomationEnvironmentOut | None, Round2AutomationUiMockupOut | None]:
    """What Round 1 shows of the Round 2 reference sheet and screens, so the
    candidate's test data fits the app they'll automate against: the web
    address and the main test login, and the screens without their
    "(shown when ...)" messages. The rest - accounts set up for special
    cases, which data is unavailable, every message the app can show - is
    built from the Round 1 answer key, so it would list the edge cases Round
    1 is scored on."""
    environment = None
    sheet = (round2.config_json or {}).get("round1_sheet")
    if sheet:  # stored at approval, straight from the practice app's design (its main test account)
        environment = Round2AutomationEnvironmentOut(fields={str(k): str(v) for k, v in sheet.items()})
    else:
        kept = _main_login_fields((round2.environment_json or {}).get("fields") or {})
        if kept:
            environment = Round2AutomationEnvironmentOut(fields=kept)
    ui_mockup = None
    screens = []
    for screen in (round2.ui_mockup_json or {}).get("screens") or []:
        elements = [e for e in screen.get("elements") or [] if not _CONDITIONAL_MESSAGE.search(str(e.get("text") or ""))]
        if elements:
            screens.append({**screen, "elements": elements})
    if screens:
        ui_mockup = Round2AutomationUiMockupOut(screens=screens)
    return environment, ui_mockup


# Words that mark a field as a special-case setup (an account at a limit, a
# locked one, unavailable data...) - built from the Round 1 answer key, so
# never shown in Round 1.
_SPECIAL_CASE = re.compile(
    r"lock|limit|block|expir|inactive|suspend|disabl|second|other|admin|invalid|unavailable|max|min|page|with \d", re.IGNORECASE)


def _main_login_fields(fields: dict) -> dict:
    """For sheets saved before approval stored round1_sheet: the web address
    (a field named like a URL whose value is one) and the main test login -
    the first username/email/login field that isn't a special-case account or
    a page, and the password beside it."""
    kept = {}
    for key, value in fields.items():
        name = key.lower()
        if ("url" in name or "web address" in name) and str(value).startswith("http") and not _SPECIAL_CASE.search(key):
            kept[key] = value
            break
    login = next((k for k in fields if re.search(r"username|email|login", k, re.I) and not _SPECIAL_CASE.search(k)
                  and not str(fields[k]).startswith("/") and "url" not in k.lower()), None)
    if login is not None:
        kept[login] = fields[login]
        stem = re.sub(r"username|email|login", "", login, flags=re.I).strip().lower()
        passwords = [k for k in fields if "password" in k.lower() and not _SPECIAL_CASE.search(k)]
        password = next((k for k in passwords if k.lower().replace("password", "").strip() == stem), None) or next(iter(passwords), None)
        if password is not None:
            kept[password] = fields[password]
    return {k: fields[k] for k in fields if k in kept}


# The practice-app design marks every message a page can show with
# "(shown when ...)" (prompts/practice_app_plan.txt); hand-written
# environments use "(shown on ...)".
_CONDITIONAL_MESSAGE = re.compile(r"\((shown|appears|displayed)\b", re.IGNORECASE)


def _current_submission(db: Session, candidate: User, scenario: Scenario) -> Submission | None:
    """The candidate's live (non-archived) submission for this scenario,
    if one exists yet. Shared by every round endpoint below that needs
    to find or check for an in-progress/submitted row - keeping this in
    one place means a future change to what "current" means (e.g. an
    added exclusion) only has to happen once."""
    return (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.scenario_id == scenario.id, Submission.archived.is_(False))
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
    round2_automation_submit, start_round). Closing an expired submission there
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
        minutes=submission.time_limit_minutes, seconds=settings.submission_grace_seconds,
    )
    if datetime.utcnow() > deadline:
        raise HTTPException(400, "Time limit for this round has passed - it can no longer be submitted.")


# ---- Round 4 (debugging investigation, one-shot submit) ----
#
# Registered before the generic /round/{round_number}/... routes for the
# same routing-order reason round 2's endpoints are below: this would
# otherwise be shadowed by /round/{round_number}/submit. Round 4's
# candidate payload (investigation rows + one root-cause conclusion) no
# longer matches SubmissionCreate's test-case-row shape - see
# schemas.Round2SubmissionCreate - so it needs its own endpoint rather
# than reusing the generic one the way it used to.

@router.post("/round/4/submit", response_model=SubmissionOut, status_code=201)
def submit_round2(
    payload: Round2SubmissionCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    candidate: User = Depends(require_candidate),
):
    _require_round_unlocked(4, db, candidate)
    scenario = _live_scenario(db, 4, candidate)
    if scenario is None:
        raise HTTPException(404, "No published scenario for this round.")

    submission = _current_submission(db, candidate, scenario)
    if submission is None:
        # Allow submitting without an explicit prior /start call too (e.g.
        # tests, or a client that just posts straight through).
        submission = Submission(
            user_id=candidate.id, scenario_id=scenario.id, round_number=4,
            started_at=datetime.utcnow(), time_limit_minutes_at_start=scenario.round_time_limit_minutes,
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
    submission.submitted_at = datetime.utcnow()
    db.commit()
    db.refresh(submission)

    background_tasks.add_task(score_submission_in_background, submission.id)
    return submission


# ---- Round 3 (AI-prompted coding: the candidate never writes code
# directly - they direct the LLM turn by turn via dedicated endpoints
# below, registered ahead of the generic /round/{round_number}/... routes
# for the same routing-order reason Round 4/4's dedicated endpoints are -
# see those sections' comments.) ----

def _round3_coding_scenario_and_submission(candidate: User, db: Session) -> tuple[Scenario, Submission]:
    scenario = _live_scenario(db, 3, candidate)
    if scenario is None:
        raise HTTPException(404, "No published scenario for round 3 yet - check back once HR has published one.")
    submission = _current_submission(db, candidate, scenario)
    if submission is None:
        raise HTTPException(404, "Round 3 hasn't been started yet - call /round/3/start first.")
    return scenario, submission


@router.post("/round/3/start", response_model=SubmissionOut, status_code=201)
def start_round3(payload: Round3StartRequest, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(3, db, candidate)
    scenario = _live_scenario(db, 3, candidate)
    if scenario is None:
        raise HTTPException(404, "No published scenario for round 3 yet - check back once HR has published one.")

    existing = _current_submission(db, candidate, scenario)
    if existing is not None:
        return existing  # idempotent: same started_at, same language, same timer deadline

    submission = Submission(
        user_id=candidate.id, scenario_id=scenario.id, round_number=3,
        status=RoundStatus.in_progress, started_at=datetime.utcnow(), time_limit_minutes_at_start=scenario.round_time_limit_minutes,
        # payload.language is accepted but ignored - the language is
        # inherited from round 2 (see _round3_language_for), not chosen
        # here.
        content={"language": _round3_language_for(candidate, db), "draft_prompt": ""},
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
    conversation_so_far = [t.to_conversation_payload() for t in existing_turns]
    current_code = next((t.code_after for t in reversed(existing_turns) if t.code_after), None)
    turn_number = len(existing_turns) + 1
    required_constructs = (scenario.reference_json or {}).get("required_constructs", [])
    # Every turn writes this (see models.Round3Turn.declared_constructs_json)
    # - `or {}` only guards a row written before this column existed.
    declared_constructs = (existing_turns[-1].declared_constructs_json if existing_turns else None) or {}

    # Called synchronously in the request path (same as Round 2's
    # round2_automation_turn): nothing is
    # persisted below until the LLM call succeeds and validates.
    try:
        with llm_service.call_context(round_number=submission.round_number, scenario_id=scenario.id,
                                      submission_id=submission.id, user_id=candidate.id):
            response = llm_service.round3_coding_turn(
                scenario_description=scenario.description,
                language=language,
                conversation_so_far=conversation_so_far,
                current_code=current_code,
                candidate_prompt=payload.candidate_prompt,
                turn_number=turn_number,
                required_constructs=required_constructs,
                declared_constructs=declared_constructs,
                io_format=scenario.round3_io_format,
            )
    except Exception:
        traceback.print_exc()
        raise HTTPException(502, "The assistant had trouble responding just now - try sending your message again.")

    turn = Round3Turn(
        submission_id=submission.id,
        turn_number=turn_number,
        candidate_prompt=payload.candidate_prompt,
        language=language,
        response_kind=response["response_kind"],
        response_message=response["response_message"],
        code_after=response.get("code_after"),
        declared_constructs_json=response.get("declared_constructs", declared_constructs),
    )
    db.add(turn)
    submission.content = {**(submission.content or {}), "draft_prompt": ""}
    db.commit()
    db.refresh(turn)
    return turn


@router.post("/round/3/edit", response_model=Round3TurnOut, status_code=201)
def round3_coding_direct_edit(payload: Round3DirectEditCreate, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(3, db, candidate)
    scenario, submission = _round3_coding_scenario_and_submission(candidate, db)
    if submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round has already been submitted.")

    language = (submission.content or {}).get("language")
    existing_turns = submission.round3_turns
    turn_number = len(existing_turns) + 1
    required_constructs = (scenario.reference_json or {}).get("required_constructs", [])
    declared_constructs = (existing_turns[-1].declared_constructs_json if existing_turns else None) or {}

    try:
        with llm_service.call_context(round_number=submission.round_number, scenario_id=scenario.id,
                                      submission_id=submission.id, user_id=candidate.id):
            response = llm_service.round3_syntax_fix(
                code=payload.code,
                language=language,
                required_constructs=required_constructs,
                declared_constructs=declared_constructs,
            )
    except Exception:
        traceback.print_exc()  # the real cause - the candidate/HR only sees the generic message
        raise HTTPException(502, "The assistant had trouble responding just now - try saving again.")

    turn = Round3Turn(
        submission_id=submission.id,
        turn_number=turn_number,
        candidate_prompt=payload.code,
        language=language,
        response_kind="direct_edit",
        response_message=response["response_message"],
        code_after=response["code_after"],
        declared_constructs_json=response.get("declared_constructs", declared_constructs),
    )
    db.add(turn)
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
    submission.submitted_at = datetime.utcnow()
    db.commit()
    db.refresh(submission)
    background_tasks.add_task(score_submission_in_background, submission.id)
    return submission


# ---- Round 2 (conversational, open-ended: the candidate creates their
# own self-titled test cases, no fixed category or ordering) ----
#
# Registered before the generic /round/{round_number}/... routes below
# on purpose: Starlette matches routes in registration order, and
# /round/2/submit would otherwise be shadowed by /round/{round_number}/submit
# (both match the literal path "/round/2/submit") - the generic one
# would win and this round's real endpoint would never be reached.
# /round/{n}/start further down already works unchanged for round 2 (it
# just creates the Submission row - there's no phase to initialize
# anymore). Everything else round-4-specific lives here rather than
# being forced through the row-based STRUCTURED_ROUNDS endpoints, since
# the shapes genuinely differ (a multi-turn conversation vs. a single
# list-of-rows payload).

def _round1_context_for(candidate: User, db: Session) -> Round1ContextOut:
    round1_submission = (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.round_number == 1, Submission.archived.is_(False))
        .first()
    )
    # Round gating guarantees this exists by the time round 2 is
    # reachable (round 2 only unlocks after round 1 is submitted) - this
    # is a defensive guard, not an expected user-facing path.
    if round1_submission is None:
        raise HTTPException(409, "No round 1 submission found - round 2 automates your round 1 answer, which has to exist first.")
    return Round1ContextOut(
        scenario_title=round1_submission.scenario.title,
        scenario_description=round1_submission.scenario.description,
        submitted_rows=round1_submission.content or [],
    )


def _round2_scenario_and_submission(candidate: User, db: Session) -> tuple[Scenario, Submission]:
    scenario = _live_scenario(db, 2, candidate)
    if scenario is None:
        raise HTTPException(404, "No published scenario for round 2 yet - check back once HR has published one.")
    submission = _current_submission(db, candidate, scenario)
    if submission is None:
        raise HTTPException(404, "Round 2 hasn't been started yet - call /round/2/start first.")
    return scenario, submission


def _test_case_out(tc: Round4TestCase) -> Round4TestCaseOut:
    return Round4TestCaseOut(
        id=tc.id, title=tc.title, draft_prompt=tc.draft_prompt,
        created_at=tc.created_at, turn_count=len(tc.turns),
    )


def _build_round2_entry_state(scenario: Scenario, submission: Submission, candidate: User, db: Session) -> Round2EntryStateOut:
    environment = Round2AutomationEnvironmentOut(**scenario.environment_json) if scenario.environment_json else None
    ui_mockup = Round2AutomationUiMockupOut(**scenario.ui_mockup_json) if scenario.ui_mockup_json else None
    fields = dict(
        scenario=scenario,
        submission=submission,
        round1_context=_round1_context_for(candidate, db),
        environment=environment,
        ui_mockup=ui_mockup,
        test_cases=[_test_case_out(tc) for tc in submission.round4_test_cases],
        turns=submission.conversation_turns,
    )
    return Round2EntryStateOut(**fields)


@router.get("/round/2/state", response_model=Round2EntryStateOut)
def round2_entry_state(db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(2, db, candidate)
    scenario, submission = _round2_scenario_and_submission(candidate, db)
    return _build_round2_entry_state(scenario, submission, candidate, db)


# ---- AI-Assisted Test Automation round (Scenario.config_json["mode"] ==
# "ai_test_automation") - the candidate automates test cases THEY designed
# in round 1. The only live round 2 format (the legacy conversational
# flow and the Focused Automation Pilot are retired). State lives entirely
# in Submission.content (no migration). ----

_AUTO_LANGUAGE_HELPER_FILES = {
    "python": "round2_automation_helpers_python.txt",
    "javascript": "round2_automation_helpers_javascript.txt",
    "java": "round2_automation_helpers_java.txt",
}


def _is_auto_scenario(scenario: Scenario) -> bool:
    return (scenario.config_json or {}).get("mode") == "ai_test_automation"


def _round3_language_for(candidate: User, db: Session) -> str:
    """Round 3's language. Round 3 no longer asks - it inherits whatever
    the candidate locked in round 2's automation round (see
    round2_automation_lock_language), so a candidate is never asked to pick a
    language twice.

    Preference order:
    1. The candidate's own round 2 automation submission's locked
       language - the normal path for every new candidate.
    2. An existing round 3 submission's language - legacy/re-entry
       compatibility only, for a candidate who already has one on file
       from before this lock existed (e.g. one predating the 2<->4
       renumbering, when round 3 still asked directly).
    3. "python", if neither exists (e.g. round 2 is configured as a
       non-automation mode, which has no language concept at all)."""
    round2 = (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.round_number == 2, Submission.archived.is_(False))
        .first()
    )
    round2_language = (round2.content or {}).get("language") if round2 else None
    if round2_language in _AUTO_LANGUAGE_HELPER_FILES:
        return round2_language

    round3 = (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.round_number == 3, Submission.archived.is_(False))
        .first()
    )
    language = ((round3.content or {}).get("language") if round3 else None) or "python"
    return language if language in _AUTO_LANGUAGE_HELPER_FILES else "python"


def _auto_environment_code(scenario: Scenario, language: str) -> str:
    """The provided automation environment for this language. Prefers the
    scenario's own configured environment (HR-authored, see
    seed_round2_automation.py); falls back to the packaged helper file."""
    configured = (scenario.config_json or {}).get("environment_code_by_language") or {}
    if configured.get(language):
        return configured[language]
    return llm_service._load_prompt(_AUTO_LANGUAGE_HELPER_FILES[language])


def _practice_spec(scenario: Scenario) -> dict | None:
    """The scenario description of a Round 2 built on the real practice
    environment (practice_engine.server) - None for older practice apps. It
    switches on the typing assistant and the environment Run."""
    spec = (scenario.config_json or {}).get("practice_spec")
    return spec if isinstance(spec, dict) else None


def _auto_environment_support(scenario: Scenario, language: str) -> dict[str, str]:
    """Files placed next to the candidate's code when it runs - an
    engine-built practice app's engine (practice_app.service.approve); none
    for older practice apps, which are one self-contained file."""
    support = ((scenario.config_json or {}).get("environment_support_by_language") or {}).get(language) or {}
    return {str(k): str(v) for k, v in support.items()} if isinstance(support, dict) else {}


def _ensure_auto_content(scenario: Scenario, submission: Submission, candidate: User, db: Session) -> dict:
    """Seeds this submission's automation state on first access - /round/2/start
    is shared with the other round 2 flows and knows nothing about this mode.
    language starts unset - the candidate locks it explicitly via
    round2_automation_lock_language before anything else in this round is
    reachable (see round2_automation_select's guard)."""
    content = submission.content
    if not content or content.get("mode") != "ai_test_automation":
        content = {
            "mode": "ai_test_automation",
            "language": None,
            # Each row added to "selected" (see round2_automation_select) carries
            # its own state - code/turns/code_edits/last_run/validation -
            # independent of every other selected row. Nothing to seed here
            # until a test case is actually selected.
            "selected": [],
            "refinements": [],
        }
        submission.content = content
        db.commit()
        db.refresh(submission)
    return submission.content


def _round1_rows_for(candidate: User, db: Session) -> list[dict]:
    round1 = (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.round_number == 1, Submission.archived.is_(False))
        .first()
    )
    if round1 is None:
        raise HTTPException(409, "No round 1 submission found - this round automates your own round 1 test cases, which have to exist first.")
    return list(round1.content or [])


def _auto_row_out(row: dict) -> Round2AutomationDesignRowOut:
    return Round2AutomationDesignRowOut(
        index=row.get("index", 0),
        title=row.get("title", "") or "",
        preconditions=row.get("preconditions", "") or "",
        steps=row.get("steps", "") or "",
        # The candidate's own correction, if they made one (see
        # round2_automation_update_test_data), else the original - `or ""`
        # also covers a round 1 submission written before test_data
        # existed. available_rows entries never have an override key, so
        # this is a no-op there; only a selected row can carry one.
        test_data=row.get("test_data_override") or row.get("test_data", "") or "",
        expected_result=row.get("expected_result", "") or "",
        refinements=list(row.get("refinements") or []),
    )


def _auto_tc_state_out(row: dict) -> Round2AutomationTCStateOut:
    row_index = row.get("index", 0)
    return Round2AutomationTCStateOut(
        row_index=row_index,
        code=row.get("code", ""),
        turns=[Round2AutomationTurnOut(row_index=row_index, **t) for t in (row.get("turns") or [])],
        code_edits_count=len(row.get("code_edits") or []),
        last_run=row.get("last_run"),
        validation=row.get("validation", ""),
    )


def _build_auto_state(scenario: Scenario, submission: Submission, candidate: User, db: Session) -> Round2AutomationStateOut:
    content = _ensure_auto_content(scenario, submission, candidate, db)
    language = content.get("language")
    available = [
        {**row, "index": i} for i, row in enumerate(_round1_rows_for(candidate, db))
    ]
    selected = content.get("selected") or []
    return Round2AutomationStateOut(
        language=language,
        language_locked=bool(language),
        available_rows=[_auto_row_out(r) for r in available],
        selected=[_auto_row_out(r) for r in selected],
        selection_locked=bool(selected),
        environment_code=_auto_environment_code(scenario, language) if language else "",
        environment=scenario.environment_json,
        ui_mockup=scenario.ui_mockup_json,
        reference_panel=(scenario.config_json or {}).get("reference_panel") if _practice_spec(scenario) is not None else None,
        tc_state=[_auto_tc_state_out(r) for r in selected],
    )


def _round2_automation_scenario_and_submission(candidate: User, db: Session) -> tuple[Scenario, Submission]:
    scenario, submission = _round2_scenario_and_submission(candidate, db)
    if not _is_auto_scenario(scenario):
        raise HTTPException(400, "This round 2 scenario is not an AI-assisted automation scenario.")
    return scenario, submission


def _auto_in_progress(candidate: User, db: Session) -> tuple[Scenario, Submission]:
    scenario, submission = _round2_automation_scenario_and_submission(candidate, db)
    if submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round has already been submitted.")
    return scenario, submission


def _require_selection(content: dict) -> list[dict]:
    selected = content.get("selected") or []
    if not selected:
        raise HTTPException(400, "Select the test case(s) you're automating first.")
    return selected


def _resolve_tc_row(selected: list[dict], row_index: int | None) -> dict:
    """Which selected test case an action targets. Explicit row_index is
    always honoured (and must be one of the selected ones); omitted is
    only valid when exactly one test case is selected, so every
    single-TC caller keeps working without ever having to pass it."""
    if row_index is None:
        if len(selected) == 1:
            return selected[0]
        raise HTTPException(400, "row_index is required - you have more than one test case selected.")
    for row in selected:
        if row["index"] == row_index:
            return row
    raise HTTPException(400, f"Test case {row_index} isn't one of the ones you selected.")


def _tc_is_unlocked(row: dict) -> bool:
    """Whether this test case's code panel (hand-editing and running) has
    ever been unlocked - permanently, once the assistant has produced its
    first code_edit for it (see round2_automation_turn's clarify-then-generate
    gate). Derived from existing turn history, not a stored flag - a
    code_edit turn can only exist once that gate has already been passed."""
    return any(t.get("response_kind") == "code_edit" for t in row.get("turns") or [])


def _require_tc_unlocked(row: dict) -> None:
    if not _tc_is_unlocked(row):
        raise HTTPException(400, "Ask the assistant to encode your design first - you can edit or run the code once it generates a first version.")


def _apply_tc_code_edit(submission: Submission, content: dict, db: Session, row_index: int, code: str | None) -> dict:
    """Persists the candidate's current editor contents for ONE selected
    test case, if sent, so Run and Submit act on exactly what's on
    screen for that TC. Same pattern as the pilot's
    _apply_pilot_code_edit; a None is a no-op. Does NOT log a code_edits
    audit entry - unlike round2_automation_save_code, this is "whatever's on
    screen right now" being used, not a distinguishable edit event."""
    if code is None:
        return content
    selected = content.get("selected") or []
    updated_selected = [
        {**row, "code": code} if row["index"] == row_index else row
        for row in selected
    ]
    updated = dict(content)
    updated["selected"] = updated_selected
    submission.content = updated
    db.commit()
    db.refresh(submission)
    return submission.content


@router.get("/round/2/auto/state", response_model=Round2AutomationStateOut)
def round2_automation_state(db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    _require_round_unlocked(2, db, candidate)
    scenario, submission = _round2_automation_scenario_and_submission(candidate, db)
    return _build_auto_state(scenario, submission, candidate, db)


@router.post("/round/2/auto/language", response_model=Round2AutomationStateOut, status_code=201)
def round2_automation_lock_language(payload: Round2AutomationLanguageCreate, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    """Locks this round's language. Allowed exactly once, and must happen
    before any test case can be selected (see round2_automation_select's guard
    below) - round 3 inherits whatever is locked here (see
    _round3_language_for) rather than asking again."""
    _require_round_unlocked(2, db, candidate)
    scenario, submission = _auto_in_progress(candidate, db)
    content = _ensure_auto_content(scenario, submission, candidate, db)
    if content.get("language"):
        raise HTTPException(400, "Your language is already locked in and can't be changed.")

    updated = dict(content)
    updated["language"] = payload.language
    submission.content = updated
    db.commit()
    db.refresh(submission)
    return _build_auto_state(scenario, submission, candidate, db)


@router.post("/round/2/auto/select", response_model=Round2AutomationStateOut, status_code=201)
def round2_automation_select(payload: Round2AutomationSelectCreate, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    """Adds one (normally) or two Round 1 rows to the IMMUTABLE snapshot
    of test cases the candidate is automating. Callable again later, up
    to a total of two, so the candidate can automate one test case at a
    time and only decide on a second after seeing the first's result
    (see _build_auto_state's remaining_rows, and the frontend's
    "Automate a test case" section, which reappears once the most
    recently added row has a result). A row already in the snapshot is
    never touched again by a later call - it's the audit record of what
    the candidate designed BEFORE automating it, so re-selecting or
    changing it would defeat that purpose. The round 1 submission itself
    is never touched - this copies out of it."""
    _require_round_unlocked(2, db, candidate)
    scenario, submission = _auto_in_progress(candidate, db)
    content = _ensure_auto_content(scenario, submission, candidate, db)
    if not content.get("language"):
        raise HTTPException(400, "Lock your language first.")
    existing = content.get("selected") or []
    already_selected = {r["index"] for r in existing}
    if len(existing) >= 2:
        raise HTTPException(400, "You've already automated two test cases - that's the most this round allows.")

    rows = _round1_rows_for(candidate, db)
    if len(set(payload.row_indexes)) != len(payload.row_indexes):
        raise HTTPException(400, "Each test case can only be selected once.")
    if len(existing) + len(payload.row_indexes) > 2:
        raise HTTPException(400, "You can automate at most two test cases in total.")
    language = content.get("language", "python")
    snapshot = list(existing)
    for index in payload.row_indexes:
        if index < 0 or index >= len(rows):
            raise HTTPException(400, f"No round 1 test case at position {index}.")
        if index in already_selected:
            raise HTTPException(400, f"Test case {index} is already selected.")
        row = rows[index]
        already_selected.add(index)
        snapshot.append({
            "index": index,
            "title": row.get("title", "") or "",
            "preconditions": row.get("preconditions", "") or "",
            "steps": row.get("steps", "") or "",
            "test_data": row.get("test_data", "") or "",
            "expected_result": row.get("expected_result", "") or "",
            "refinements": [],
            # Independent per-TC automation state (see
            # Round2AutomationTCStateOut) - starts from the provided environment,
            # same as the old single-buffer design used to, just seeded
            # once per selected test case now instead of once per round.
            "code": _auto_environment_code(scenario, language),
            "turns": [],
            "code_edits": [],
            "last_run": None,
            "validation": "",
        })

    updated = dict(content)
    updated["selected"] = snapshot
    submission.content = updated
    db.commit()
    db.refresh(submission)
    return _build_auto_state(scenario, submission, candidate, db)


@router.post("/round/2/auto/refine", response_model=Round2AutomationStateOut, status_code=201)
def round2_automation_refine(payload: Round2AutomationRefineCreate, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    """APPEND-ONLY. A refinement note is added alongside the original row;
    nothing in the snapshot (or in round 1) is ever overwritten, so the
    original design stays auditable next to whatever the candidate
    realized later."""
    _require_round_unlocked(2, db, candidate)
    scenario, submission = _auto_in_progress(candidate, db)
    content = _ensure_auto_content(scenario, submission, candidate, db)
    selected = _require_selection(content)

    updated = dict(content)
    new_selected = []
    matched = False
    for row in selected:
        if row["index"] == payload.row_index:
            matched = True
            new_selected.append({**row, "refinements": list(row.get("refinements") or []) + [payload.note]})
        else:
            new_selected.append(dict(row))
    if not matched:
        raise HTTPException(400, f"Test case {payload.row_index} isn't one of the ones you selected.")
    updated["selected"] = new_selected
    updated["refinements"] = list(content.get("refinements") or []) + [
        {"row_index": payload.row_index, "note": payload.note, "created_at": datetime.utcnow().isoformat()}
    ]
    submission.content = updated
    db.commit()
    db.refresh(submission)
    return _build_auto_state(scenario, submission, candidate, db)


@router.post("/round/2/auto/test-data", response_model=Round2AutomationStateOut, status_code=201)
def round2_automation_update_test_data(payload: Round2AutomationTestDataUpdate, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    """Corrects ONE selected test case's own test data for automation
    purposes - e.g. the candidate notices a typo in their own Round 1
    answer. Recorded as an override alongside the row, never overwriting
    row["test_data"] itself (that stays the immutable Round 1 record -
    see _auto_row_out, which exposes the override as the effective value
    for the "selected" design, and scoring_service._auto_tc_design_only,
    which does the same for the scorer/AI). Allowed at any time,
    independent of whether this test case's code is unlocked yet."""
    _require_round_unlocked(2, db, candidate)
    scenario, submission = _auto_in_progress(candidate, db)
    content = _ensure_auto_content(scenario, submission, candidate, db)
    selected = _require_selection(content)
    row = _resolve_tc_row(selected, payload.row_index)
    row_index = row["index"]

    new_row = dict(row)
    new_row["test_data_override"] = payload.test_data
    updated_selected = [new_row if r["index"] == row_index else r for r in selected]
    updated = dict(content)
    updated["selected"] = updated_selected
    submission.content = updated
    db.commit()
    db.refresh(submission)
    return _build_auto_state(scenario, submission, candidate, db)


# One assistant turn at a time per test case: a double-click or a retry that
# lands while the first request is still waiting on the model used to pay
# for (and record) the same turn twice.
_turn_locks: dict[tuple[int, int], threading.Lock] = {}
_turn_locks_guard = threading.Lock()
REPEAT_MESSAGE = ("You've sent the same message again and nothing has changed since my last reply, so it still "
                  "stands (see above). Tell me what you'd like changed, or run the code to check it.")


def _turn_lock(submission_id: int, row_index: int) -> threading.Lock:
    with _turn_locks_guard:
        return _turn_locks.setdefault((submission_id, row_index), threading.Lock())


def _state_fingerprint(row: dict) -> str:
    """What the candidate can see changing between messages: the code and the last run."""
    last_run = row.get("last_run") or {}
    raw = json.dumps([row.get("code") or "", last_run.get("ran_at"), last_run.get("exit_code"), last_run.get("stdout")], default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


_FILLER_WORDS = {"please", "pls", "can", "could", "would", "you", "the", "a", "an", "to", "and", "me", "for", "it", "this", "that", "just",
                 "now", "again", "again,", "i", "want", "need", "kindly", "ok", "okay", "so", "then", "of", "my", "is", "are", "be"}
_EXACT_VALUE_RE = re.compile(r"""["'][^"']*["']|\d+(?:\.\d+)?|[\w.+-]+@[\w.-]+""")


def _same_message(a: str | None, b: str | None) -> bool:
    """The same request again, even reworded ("write code to log in" / "please
    write the code to log in again"): the same exact values (numbers, quoted
    text, emails) and nearly the same meaningful words. A changed value
    ("balance is 500" -> "600") is always a new request."""
    def norm(t: str | None) -> str:
        return " ".join((t or "").lower().split()).strip(" .!?")
    if not norm(a) or not norm(b):
        return False
    if norm(a) == norm(b):
        return True
    if sorted(_EXACT_VALUE_RE.findall(norm(a))) != sorted(_EXACT_VALUE_RE.findall(norm(b))):
        return False
    words = [{w.strip(".,!?;:") for w in norm(t).split()} - _FILLER_WORDS - {""} for t in (a, b)]
    union = words[0] | words[1]
    return bool(union) and len(words[0] & words[1]) / len(union) >= 0.8


@router.post("/round/2/auto/turn", response_model=Round2AutomationTurnOut, status_code=201)
def round2_automation_turn(payload: Round2AutomationTurnCreate, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    """See _round2_automation_turn - this wrapper allows one turn at a time per test case."""
    _require_round_unlocked(2, db, candidate)
    scenario, submission = _auto_in_progress(candidate, db)
    lock = _turn_lock(submission.id, payload.row_index)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "The assistant is still answering your previous message for this test case - wait for that reply.")
    try:
        with llm_service.call_context(round_number=submission.round_number, scenario_id=scenario.id,
                                      submission_id=submission.id, user_id=candidate.id):
            return _round2_automation_turn(payload, db, candidate)
    finally:
        lock.release()


def _round2_automation_turn(payload: Round2AutomationTurnCreate, db: Session, candidate: User):
    """Before this test case's first ever code_edit, every instruction
    goes through the specification-sufficiency check (llm_service.
    round2_automation_clarify) first - insufficient/contradicts_prior comes
    back as a clarify turn with no code, same as the standalone /clarify
    endpoint always has; only once that check comes back sufficient does
    this call proceed to generation, and that one first generation is
    the only one asked to plant a misleading-pass gap (see llm_service.
    round2_automation_turn's inject_flaw) rather than fully correct code -
    every later turn for this same test case skips the check and
    generates normally, same as before this gate existed."""
    _require_round_unlocked(2, db, candidate)
    scenario, submission = _auto_in_progress(candidate, db)
    content = _ensure_auto_content(scenario, submission, candidate, db)
    selected = _require_selection(content)
    row = _resolve_tc_row(selected, payload.row_index)

    turns = list(row.get("turns") or [])
    conversation_so_far = [
        {"candidate_prompt": t["candidate_prompt"], "response_message": t["response_message"], "response_kind": t.get("response_kind")} for t in turns
    ]
    language = content.get("language", "python")
    if _practice_spec(scenario) is not None:  # the typing assistant: it never sees the application
        try:
            response = round2_typist.turn(language, row, conversation_so_far, row.get("code", ""), payload.candidate_prompt)
        except Exception:
            traceback.print_exc()
            raise HTTPException(502, "The assistant had trouble responding just now - try sending your message again.")
        return _record_round2_turn(submission, content, selected, row, turns, payload.candidate_prompt, db, response)
    environment_code = _auto_environment_code(scenario, language)
    is_first_generation = not _tc_is_unlocked(row)

    # The same message again with nothing changed since (no edit, no run):
    # answered without an AI call - the earlier reply still stands, and a
    # model at temperature 0 would only repeat it word for word. Not after a
    # clarifying question: that case proceeds (the gate asks only once).
    last = turns[-1] if turns else None
    if (last and last.get("response_kind") != "clarify" and _same_message(last.get("candidate_prompt"), payload.candidate_prompt)
            and last.get("state_seen") == _state_fingerprint(row)):
        return _record_round2_turn(submission, content, selected, row, turns, payload.candidate_prompt, db,
                                   {"response_kind": "explain", "response_message": REPEAT_MESSAGE, "code_after": None})

    # Synchronous, same reasoning as every other round's turn endpoint -
    # nothing is persisted until the call succeeds and validates.
    # selected_design is scoped to THIS one test case only - each
    # selected test case gets its own independent instructions/turns, not
    # a combined design spanning every selected row.
    try:
        if is_first_generation:
            clarify_response = llm_service.round2_automation_clarify(
                language=language,
                selected_design=[row],
                environment_code=environment_code,
                current_code=row.get("code", ""),
                conversation_so_far=conversation_so_far,
                candidate_prompt=payload.candidate_prompt,
            )
            # round2_automation_clarify_policy.build_clarify_response only ever
            # returns "explain" for a "sufficient" classification - every
            # other outcome (insufficient/contradicts_prior/a prohibited
            # request) comes back as "clarify"/"refuse" instead, so that's
            # the one signal here that means "proceed to generation".
            if clarify_response["response_kind"] != "explain":
                response = clarify_response
            else:
                response = llm_service.round2_automation_turn(
                    language=language,
                    selected_design=[row],
                    environment_code=environment_code,
                    current_code=row.get("code", ""),
                    conversation_so_far=conversation_so_far,
                    candidate_prompt=payload.candidate_prompt,
                    # No planted flaw (owner decision 2026-09-27): the candidate's own
                    # instructions and mistakes are what is assessed.
                    inject_flaw=False,
                )
        else:
            response = llm_service.round2_automation_turn(
                language=language,
                selected_design=[row],
                environment_code=environment_code,
                current_code=row.get("code", ""),
                conversation_so_far=conversation_so_far,
                candidate_prompt=payload.candidate_prompt,
            )
    except Exception:
        traceback.print_exc()  # the real cause - the candidate/HR only sees the generic message
        raise HTTPException(502, "The assistant had trouble responding just now - try sending your message again.")
    return _record_round2_turn(submission, content, selected, row, turns, payload.candidate_prompt, db, response)


def _record_round2_turn(submission, content, selected, row, turns, candidate_prompt, db, response) -> Round2AutomationTurnOut:
    turn_record = {
        "turn_number": len(turns) + 1,
        "candidate_prompt": candidate_prompt,
        "response_kind": response["response_kind"],
        "response_message": response["response_message"],
        "code_after": response.get("code_after"),
    }
    for key in ("planted_flaw", "unrequested_checks", "fabricated_observations", "changed_values"):  # assessor-only - see schemas.SubmissionOut
        if response.get(key):
            turn_record[key] = response[key]
    new_row = dict(row)
    new_row["turns"] = [*turns, turn_record]
    if response.get("code_after"):
        new_row["code"] = response["code_after"]
    # What the candidate saw once this reply landed - the repeat check above compares against it.
    turn_record["state_seen"] = _state_fingerprint(new_row)
    updated = dict(content)
    updated["selected"] = [new_row if r["index"] == row["index"] else r for r in selected]
    submission.content = updated
    db.commit()
    return Round2AutomationTurnOut(row_index=row["index"], **turn_record)


@router.post("/round/2/auto/clarify", response_model=Round2AutomationTurnOut, status_code=201)
def round2_automation_clarify(payload: Round2AutomationClarifyCreate, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    """The clarification-only flow for ONE selected test case: checks
    whether the candidate's instruction leaves a decision open, and if
    so asks a neutral question that never names the environment layer
    (UI/API/DB) it would take to encode - see llm_service.round2_automation_clarify
    and round2_automation_clarify_policy for the two-layer guard. Distinct from
    round2_automation_turn: this endpoint NEVER writes code, regardless of
    whether the instruction turns out to be complete or not - code
    generation stays exclusively /turn's job, unmodified. Same
    prohibited-request policy as /turn, reused unmodified, so a request
    to invent test data or assertions can't be routed around it through
    here. Writes into the SAME per-test-case turns log /turn does - this
    is the same conversation, just an entry point that can't produce
    code."""
    _require_round_unlocked(2, db, candidate)
    scenario, submission = _auto_in_progress(candidate, db)
    content = _ensure_auto_content(scenario, submission, candidate, db)
    selected = _require_selection(content)
    row = _resolve_tc_row(selected, payload.row_index)
    row_index = row["index"]

    turns = list(row.get("turns") or [])
    conversation_so_far = [
        {"candidate_prompt": t["candidate_prompt"], "response_message": t["response_message"], "response_kind": t.get("response_kind")} for t in turns
    ]
    language = content.get("language", "python")

    try:
        response = llm_service.round2_automation_clarify(
            language=language,
            selected_design=[row],
            environment_code=_auto_environment_code(scenario, language),
            current_code=row.get("code", ""),
            conversation_so_far=conversation_so_far,
            candidate_prompt=payload.candidate_prompt,
        )
    except Exception:
        traceback.print_exc()  # the real cause - the candidate/HR only sees the generic message
        raise HTTPException(502, "The assistant had trouble responding just now - try sending your message again.")

    turn_record = {
        "turn_number": len(turns) + 1,
        "candidate_prompt": payload.candidate_prompt,
        "response_kind": response["response_kind"],
        "response_message": response["response_message"],
        "code_after": None,  # this endpoint never writes code, whatever the response
    }
    turns.append(turn_record)
    new_row = dict(row)
    new_row["turns"] = turns
    updated_selected = [new_row if r["index"] == row_index else r for r in selected]
    updated = dict(content)
    updated["selected"] = updated_selected
    submission.content = updated
    db.commit()
    return Round2AutomationTurnOut(row_index=row_index, **turn_record)


@router.post("/round/2/auto/code", response_model=Round2AutomationStateOut, status_code=201)
def round2_automation_save_code(payload: Round2AutomationCodeUpdate, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    """The candidate's own direct edit to ONE selected test case's own
    code, recorded as its own audit entry so "what the assistant wrote"
    and "what the candidate changed themselves" stay separable at scoring
    time (see prompts/round2_automation_scoring.txt's ai_output_review area)."""
    _require_round_unlocked(2, db, candidate)
    scenario, submission = _auto_in_progress(candidate, db)
    content = _ensure_auto_content(scenario, submission, candidate, db)
    selected = _require_selection(content)
    row = _resolve_tc_row(selected, payload.row_index)
    row_index = row["index"]
    _require_tc_unlocked(row)

    new_row = dict(row)
    new_row["code"] = payload.code
    new_row["code_edits"] = list(row.get("code_edits") or []) + [
        {"seq": len(row.get("code_edits") or []) + 1, "created_at": datetime.utcnow().isoformat(), "code": payload.code}
    ]
    updated_selected = [new_row if r["index"] == row_index else r for r in selected]
    updated = dict(content)
    updated["selected"] = updated_selected
    submission.content = updated
    db.commit()
    db.refresh(submission)
    return _build_auto_state(scenario, submission, candidate, db)


@router.post("/round/2/auto/run", response_model=Round2AutomationRunOut, status_code=201)
def round2_automation_run(
    payload: Round2AutomationRunCreate | None = None,
    db: Session = Depends(get_db), candidate: User = Depends(require_candidate),
):
    """Batch execution through the EXISTING execution_service.run_code,
    unmodified, in the candidate's locked language - single file (one
    selected test case's own code), which is exactly what that engine
    already supports. Each selected test case is run independently and
    starts from its own code buffer, which itself started from the
    provided environment at selection time (see round2_automation_select) -
    running one test case never touches another's code or last_run."""
    _require_round_unlocked(2, db, candidate)
    scenario, submission = _auto_in_progress(candidate, db)
    content = _ensure_auto_content(scenario, submission, candidate, db)
    selected = _require_selection(content)
    row = _resolve_tc_row(selected, payload.row_index if payload else None)
    row_index = row["index"]
    _require_tc_unlocked(row)
    content = _apply_tc_code_edit(submission, content, db, row_index, payload.code if payload else None)
    row = _resolve_tc_row(content.get("selected") or [], row_index)

    language = content.get("language", "python")
    spec = _practice_spec(scenario)
    if spec is not None:  # the real practice environment: fresh app, browser, API, database
        try:
            result = practice_run.run(language, row.get("code", ""), spec)
        except practice_run.EnvironmentUnavailable:
            traceback.print_exc()
            result = execution_service.ExecutionResult(stdout="", stderr="", exit_code=None, timed_out=False, infra_error=True)
    else:
        result = execution_service.run_code(
            language=language, code=row.get("code", ""), stdin=[],
            support_files=_auto_environment_support(scenario, language),
        )
    last_run = {
        "stdout": result.stdout, "stderr": result.stderr, "exit_code": result.exit_code,
        "timed_out": result.timed_out, "infra_error": result.infra_error,
        # duration_ms was already computed by execution_service.run_code
        # and simply discarded before now; ran_at is stamped here since
        # run_code itself has no reason to know wall-clock time.
        "duration_ms": result.duration_ms, "ran_at": datetime.utcnow().isoformat(),
        "status": round2_typist.run_status(result.exit_code, result.stdout, result.timed_out, result.infra_error),
    }
    selected = content.get("selected") or []
    updated_selected = [{**r, "last_run": last_run} if r["index"] == row_index else r for r in selected]
    updated = dict(content)
    updated["selected"] = updated_selected
    submission.content = updated
    db.commit()
    return Round2AutomationRunOut(**last_run)


@router.post("/round/2/auto/submit", response_model=SubmissionOut, status_code=201)
def round2_automation_submit(
    payload: Round2AutomationSubmitCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), candidate: User = Depends(require_candidate),
):
    _require_round_unlocked(2, db, candidate)
    scenario, submission = _auto_in_progress(candidate, db)
    _require_within_time_limit(submission, scenario)
    content = _ensure_auto_content(scenario, submission, candidate, db)
    selected = _require_selection(content)

    # Every selected test case is checked independently: back-compat
    # single-TC shape (flat `code`) is only ever wrapped into a
    # single-entry list when there is exactly one to wrap - two or more
    # selected test cases must send `entries` explicitly, since there's
    # no longer one buffer a flat `code` could unambiguously mean.
    entries = payload.entries
    if not entries:
        if len(selected) != 1:
            raise HTTPException(400, "Each selected test case needs its own run - submit with entries.")
        entries = [Round2AutomationTCSubmitEntry(row_index=selected[0]["index"], code=payload.code)]

    entry_by_index = {e.row_index: e for e in entries}
    if set(entry_by_index) != {row["index"] for row in selected}:
        raise HTTPException(400, "Every selected test case needs its own entry to submit.")

    updated_selected = []
    for row in selected:
        entry = entry_by_index[row["index"]]
        new_row = dict(row)
        if entry.code is not None:
            new_row["code"] = entry.code
        if not new_row.get("code"):
            raise HTTPException(400, f"There's no automation code to submit yet for test case {row['index']}.")
        if not new_row.get("last_run"):
            raise HTTPException(400, f"Run test case {row['index']} at least once before submitting.")
        updated_selected.append(new_row)

    updated = dict(content)
    updated["selected"] = updated_selected
    submission.content = updated
    submission.status = RoundStatus.submitted
    submission.submitted_at = datetime.utcnow()
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

    submission = _current_submission(db, candidate, scenario)
    environment = None
    ui_mockup = None
    panel = None
    if round_number == 1:
        # A cut-down look at the Round 2 this Round 1 leads to (see
        # _round1_environment_view), so the test data fits the app.
        round2_scenario = _round2_for_round1(db, scenario)
        if round2_scenario is not None:
            environment, ui_mockup = _round1_environment_view(round2_scenario)
            if _practice_spec(round2_scenario) is not None:
                from ..services.practice_engine import reference
                panel = reference.round1_panel((round2_scenario.config_json or {}).get("reference_panel"))
    return RoundStateOut(scenario=scenario, submission=submission, environment=environment, ui_mockup=ui_mockup, reference_panel=panel)


@router.post("/round/{round_number}/start", response_model=SubmissionOut, status_code=201)
def start_round(round_number: int, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    if round_number not in (1, 2, 4):
        raise HTTPException(400, "round_number must be 1, 2, or 4")
    _require_round_unlocked(round_number, db, candidate)

    scenario = _live_scenario(db, round_number, candidate)
    if scenario is None:
        raise HTTPException(404, "No published scenario for this round yet - check back once HR has published one.")

    existing = _current_submission(db, candidate, scenario)
    if existing is not None:
        return existing  # idempotent: same started_at, same timer deadline

    submission = Submission(
        user_id=candidate.id,
        scenario_id=scenario.id,
        round_number=round_number,
        status=RoundStatus.in_progress,
        started_at=datetime.utcnow(), time_limit_minutes_at_start=scenario.round_time_limit_minutes,
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

    submission = _current_submission(db, candidate, scenario)
    if submission is None:
        # Allow submitting without an explicit prior /start call too (e.g.
        # tests, or a client that just posts straight through).
        submission = Submission(
            user_id=candidate.id, scenario_id=scenario.id, round_number=round_number,
            started_at=datetime.utcnow(), time_limit_minutes_at_start=scenario.round_time_limit_minutes,
            appearance_id=_current_appearance_id(db, candidate),
        )
        db.add(submission)
    elif submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round has already been submitted.")

    _require_within_time_limit(submission, scenario)

    submission.content = [row.model_dump() for row in payload.content]
    submission.status = RoundStatus.submitted
    submission.submitted_at = datetime.utcnow()
    db.commit()
    db.refresh(submission)

    # Scoring calls the LLM, which can take a few seconds - run it after
    # the response is sent so the candidate isn't stuck on a spinner, and
    # so the next round is unlocked immediately (see plan: don't block
    # round progression on background scoring).
    background_tasks.add_task(score_submission_in_background, submission.id)

    return submission


@router.post("/round/{round_number}/tab-switch", response_model=TabSwitchOut)
def log_tab_switch(
    round_number: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    candidate: User = Depends(require_candidate),
):
    """Telemetry from app.js's tab-switch guard: the candidate left this
    tab/app (or exited fullscreen) while a round's timer was running.
    Logged for HR either way (Submission.tab_switch_events_json), but
    now also a genuine 3-strike gate: on the 3rd exit in one round, this
    force-ends the round right here - same "whatever they've got is the
    final answer" outcome as expire_round below, just triggered by
    strikes instead of the deadline, and without expire_round's deadline
    check (a strike-ended round can, by design, finish well before time
    runs out). No content payload needed - unlike expire_round (which
    takes the client's draft because a round can end mid-edit with
    unsaved changes), whatever's already persisted via each round's own
    periodic autosave/incremental writes IS "what they'd written so
    far." Silently a no-op (0/false) if there's nothing in-progress to
    attach it to (the round may have already ended by the time this
    request lands) - app.js only acts on round_ended, so this is safe."""
    if round_number not in (1, 2, 3, 4):
        raise HTTPException(400, "round_number must be 1, 2, 3, or 4")
    scenario = _live_scenario(db, round_number, candidate)
    if scenario is None:
        return TabSwitchOut(strike_count=0, round_ended=False)
    submission = _current_submission(db, candidate, scenario)
    if submission is None or submission.status != RoundStatus.in_progress:
        return TabSwitchOut(strike_count=0, round_ended=False)
    # A fresh list, not an in-place mutation of the loaded one - SQLAlchemy
    # doesn't track in-place JSON-column mutations, so appending to the
    # existing list and reassigning it (same object identity) can leave
    # the row un-flushed. See migrate_tab_switch_guard.py.
    events = list(submission.tab_switch_events_json or [])
    events.append(datetime.utcnow().isoformat())
    submission.tab_switch_events_json = events

    round_ended = False
    if len(events) >= 3:
        round_ended = True
        submission.status = RoundStatus.submitted
        submission.submitted_at = datetime.utcnow()
        submission.auto_closed_reason = "Left fullscreen 3 times - round ended automatically"
        background_tasks.add_task(score_submission_in_background, submission.id)

    db.commit()
    return TabSwitchOut(strike_count=len(events), round_ended=round_ended)


@router.patch("/round/{round_number}/draft", status_code=204)
def save_round_draft(
    round_number: int,
    payload: ExpireRoundPayload,
    db: Session = Depends(get_db),
    candidate: User = Depends(require_candidate),
):
    """Periodic autosave for rounds 1/2's in-progress content, the same
    pattern round 2's code already has (see round2_automation_save_code)
    - so a crash, refresh, or network loss mid-round doesn't
    silently lose typed-but-unsubmitted work while the timer keeps
    counting down. Reuses ExpireRoundPayload's shape (deliberately
    permissive - see its docstring), but unlike /expire below this never
    touches submission.status or checks the deadline: only a real
    Submit, or /expire once the deadline has genuinely passed, ever ends
    the round. Round 2 has no equivalent here - it already autosaves per
    test case instead (PATCH /round/2/test-case/{id}/draft), since it
    has no single whole-round form the way rounds 1/4 do.

    Slots 1 and 4 since the 2<->4 renumbering: round 1 is the test-case
    rows, round 4 is the debugging investigation write-up. Round 2 (the
    automation round) is the one that autosaves per test case instead."""
    if round_number not in (1, 4):
        raise HTTPException(400, "round_number must be 1 or 4 - round 2 autosaves per test case, see PATCH /round/2/test-case/{id}/draft.")
    _require_round_unlocked(round_number, db, candidate)
    scenario = _live_scenario(db, round_number, candidate)
    if scenario is None:
        raise HTTPException(404, "No published scenario for this round.")
    submission = _current_submission(db, candidate, scenario)
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
    doSubmitRound2Investigation/round2AutomationSubmit with force=true), and
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
    submission = _current_submission(db, candidate, scenario)
    if submission is None or submission.status != RoundStatus.in_progress:
        raise HTTPException(400, "This round isn't in progress - nothing to expire.")
    if submission.started_at is not None:
        deadline = submission.started_at + timedelta(minutes=submission.time_limit_minutes)
        if datetime.utcnow() < deadline:
            raise HTTPException(400, "This round's time limit hasn't passed yet.")

    if round_number == 1:
        submission.content = [_sanitize_expired_round1_row(r) for r in payload.content]
    elif round_number == 4:
        # Debugging (slot 4 since the 2<->4 renumbering) - investigation-shaped.
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
    submission.submitted_at = datetime.utcnow()
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
