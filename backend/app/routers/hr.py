"""
HR-only endpoints: author scenarios (draft -> review generated reference ->
publish), and view the candidate results dashboard.

Rounds 1 and 2 have a working reference-generation/scoring pipeline (see
llm_service.py) and share the same candidate-facing row shape (title,
preconditions, steps, expected_result) - round 1's cases and round 2's
debugging steps are structurally identical, just differently worded.
Round 3 is conversational and has no scenario-level test-case reference
(its target is each candidate's own round 1 answer) - instead it has an
auto-generated Test Environment reference sheet (environment_json, see
_generate_reference) that plays the same role reference_json does for
rounds 1/2 (generated at creation, required before publish) - see
publish_scenario and list_scenarios below, and routers/candidate.py for
its dedicated endpoints.
"""
from collections import Counter
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, UploadFile, File
from fpdf import FPDF
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    User, Scenario, ScenarioStatus, Submission, Score, RoundStatus, ExperienceBand, Role, AppSettings,
    CandidateAppearance,
)
from ..schemas import (
    ScenarioCreate, ScenarioUpdate, ScenarioOut, SubmissionReportOut,
    CandidateSummaryOut, CandidateRoundSummary, ScenarioHistoryOut, MissPattern, ConceptCoverageAverage,
    Round3TestCaseOut, CandidateAssessmentSummaryOut, CandidateSummaryPdfRequest,
    CandidateRoundComment, AppSettingsOut, AppSettingsUpdate,
    BulkUploadResult, CandidateBandUpdate, CandidateAppearanceOut, ScoreOverrideRequest,
    ScenarioTimeLimitUpdate, Round3ConfigUpdate, Round3InstructionsUpdate,
)
from ..dependencies import require_hr
from ..services import llm_service
from ..services import candidate_upload_service
from ..services.scoring_service import score_submission_in_background, close_expired_submissions

router = APIRouter(prefix="/hr", tags=["hr"])

VALID_BANDS = (ExperienceBand.junior.value, ExperienceBand.senior.value)

# Mirrors app.js's ROUND_LABELS - only used here for the PDF's per-round
# headings, so a small local copy (not worth a shared-constants file
# across two different languages) is the pragmatic choice.
ROUND_LABELS = {1: "Manual test cases", 2: "Debugging", 3: "Conversational"}


def get_settings(db: Session) -> AppSettings:
    """Get-or-create accessor for the AppSettings singleton row (id=1) -
    normally created by migrate_bulk_candidates.py, but this is a safety
    net for a DB that skipped that migration (e.g. a fresh create_all()
    without ever running it)."""
    app_settings = db.get(AppSettings, 1)
    if app_settings is None:
        app_settings = AppSettings(id=1)
        db.add(app_settings)
        db.commit()
        db.refresh(app_settings)
    return app_settings


@router.get("/settings", response_model=AppSettingsOut)
def get_app_settings(db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    return get_settings(db)


@router.put("/settings", response_model=AppSettingsOut)
def update_app_settings(payload: AppSettingsUpdate, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    app_settings = get_settings(db)
    for field, value in payload.model_dump().items():
        setattr(app_settings, field, value)
    db.commit()
    db.refresh(app_settings)
    return app_settings


@router.post("/scenarios", response_model=ScenarioOut, status_code=201)
def create_scenario(payload: ScenarioCreate, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    if payload.round_number not in (1, 2, 3):
        raise HTTPException(400, "round_number must be 1, 2, or 3")
    if payload.experience_band not in VALID_BANDS:
        raise HTTPException(400, "experience_band must be '0-7' or '7+'")
    if payload.time_limit_minutes < 1:
        raise HTTPException(400, "time_limit_minutes must be at least 1")

    scenario = Scenario(
        round_number=payload.round_number,
        title=payload.title,
        description=payload.description,
        experience_band=payload.experience_band,
        time_limit_minutes=payload.time_limit_minutes,
        created_by=hr.id,
        config_json=payload.config_json,
        status=ScenarioStatus.draft,
    )
    db.add(scenario)
    db.commit()
    db.refresh(scenario)

    _generate_reference(scenario, db)
    return scenario


def _generate_reference(scenario: Scenario, db: Session) -> None:
    """Synchronous on purpose: HR is actively waiting to review this
    (unlike candidate-facing scoring, which runs in the background).
    Nothing here is committed until the very end (see db.commit() below),
    so a failure partway through (malformed LLM JSON, a shape that
    doesn't match the target schema, a network/API error) just leaves the
    scenario's reference fields unset rather than corrupting anything -
    HR sees a clean error and can hit "Regenerate" to retry."""
    try:
        _generate_reference_unsafe(scenario, db)
    except Exception:
        raise HTTPException(502, "Reference generation failed - try again.")


def _generate_reference_unsafe(scenario: Scenario, db: Session) -> None:
    if scenario.round_number == 1:
        scenario.reference_json = llm_service.generate_round1_reference(
            scenario_description=scenario.description,
            experience_band=scenario.experience_band.value,
            time_limit_minutes=scenario.time_limit_minutes,
        )
    elif scenario.round_number == 2:
        scenario.reference_json = llm_service.generate_round2_reference(
            scenario_description=scenario.description,
            experience_band=scenario.experience_band.value,
            time_limit_minutes=scenario.time_limit_minutes,
        )
    else:
        # Round 3 has no scenario-level test-case reference (its target
        # is each candidate's own round 1 answer) - what it needs instead
        # is a Test Environment reference sheet (credentials, API
        # endpoints, DB schema, ...) plus a reference sketch of the app's
        # screens, both shown to every candidate. Both describe the
        # actual app under test, which lives in round 1's scenario, not
        # round 3's own (round 3's description is just instructions to
        # the candidate, not a description of the app) - ground both in
        # whichever round 1 scenario is currently live for this band,
        # falling back to round 3's own text only if round 1 hasn't been
        # published for this band yet. Same lifecycle as reference_json
        # otherwise: generated here (together, one HR "Regenerate" action
        # refreshes both), required before publish (see publish_scenario).
        live_round1 = db.query(Scenario).filter(
            Scenario.round_number == 1,
            Scenario.experience_band == scenario.experience_band,
            Scenario.is_live.is_(True),
        ).first()
        app_description = live_round1.description if live_round1 else scenario.description
        scenario.environment_json = llm_service.generate_round3_environment(
            app_description=app_description,
        )
        scenario.ui_mockup_json = llm_service.generate_round3_ui_mockup(
            app_description=app_description,
        )
    db.commit()
    db.refresh(scenario)


def _resync_round3_reference_for_band(round1_scenario: Scenario, db: Session) -> None:
    """Called right after a round1 scenario newly goes live (see
    publish_scenario/move_to_screening below). If a round3 scenario is
    ALSO currently live for the same band, its environment_json/
    ui_mockup_json were grounded in whichever round1 scenario was live
    at the moment IT was created or last regenerated (see
    _generate_reference_unsafe above) - now stale, since a different
    round1 scenario just took over. Left alone, every candidate in this
    band would see round3 test data (credentials, API endpoints, screens)
    describing a completely different app than the one their own round1
    answer was actually about - a real, observed bug this fixes at the
    source instead of requiring HR to remember to hit "Regenerate" on
    round3 every time round1 rotates.

    Best-effort: a failure here (LLM error, bad shape) must not block the
    round1 publish/promotion that triggered it - HR still has the manual
    Regenerate button on round3 as a fallback."""
    if round1_scenario.round_number != 1:
        return
    live_round3 = db.query(Scenario).filter(
        Scenario.round_number == 3,
        Scenario.experience_band == round1_scenario.experience_band,
        Scenario.is_live.is_(True),
    ).first()
    if live_round3 is None:
        return
    try:
        live_round3.environment_json = llm_service.generate_round3_environment(
            app_description=round1_scenario.description,
        )
        live_round3.ui_mockup_json = llm_service.generate_round3_ui_mockup(
            app_description=round1_scenario.description,
        )
        db.commit()
    except Exception:
        db.rollback()


@router.post("/scenarios/{scenario_id}/regenerate-reference", response_model=ScenarioOut)
def regenerate_reference(scenario_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Draft-only for round 1/2 (see _get_draft_scenario_or_404) - their
    reference is a fixed answer key candidates get scored against, so
    regenerating it on a live scenario would be rewriting the ground
    truth out from under whoever's already been scored. Round 3 has no
    such answer key (its "reference" is just environment/screen flavor
    text), and its scenarios go live immediately on creation (see
    createRound3Scenario in app.js) rather than sitting as a draft
    first, so it gets the same live-editable-but-blocked-mid-round
    treatment as its other settings instead."""
    scenario = db.get(Scenario, scenario_id)
    if scenario is not None and scenario.round_number == 3:
        _require_round3_not_in_progress(scenario_id, db, background_tasks, "regenerate this round's environment & screens")
    else:
        scenario = _get_draft_scenario_or_404(scenario_id, db)
    _generate_reference(scenario, db)
    return scenario


@router.patch("/scenarios/{scenario_id}", response_model=ScenarioOut)
def update_scenario(scenario_id: int, payload: ScenarioUpdate, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    scenario = _get_draft_scenario_or_404(scenario_id, db)

    if payload.title is not None:
        scenario.title = payload.title
    if payload.description is not None:
        scenario.description = payload.description
    if payload.time_limit_minutes is not None:
        if payload.time_limit_minutes < 1:
            raise HTTPException(400, "time_limit_minutes must be at least 1")
        scenario.time_limit_minutes = payload.time_limit_minutes
    if payload.reference_json is not None:
        scenario.reference_json = [row.model_dump() for row in payload.reference_json]

    db.commit()
    db.refresh(scenario)
    return scenario


@router.patch("/scenarios/{scenario_id}/time-limit", response_model=ScenarioOut)
def update_scenario_time_limit(scenario_id: int, payload: ScenarioTimeLimitUpdate, background_tasks: BackgroundTasks, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Unlike title/description/reference_json (see update_scenario above,
    gated by _get_draft_scenario_or_404), the time limit is allowed to
    change regardless of draft/published/live status - it doesn't
    retroactively invalidate anything a candidate was already scored
    against. What it must NOT do is change out from under a candidate
    who's actively mid-assessment right now - a deadline shifting while
    someone's clock is already running isn't something they could
    reasonably plan around, and it lets HR make exam-integrity-relevant
    changes (e.g. shortening a round because a question turned out too
    easy) without a mid-flight instance leaking the fact that something
    just changed.

    Scoped to the whole band, not just this one scenario: a candidate who's
    actively taking round 1 could reach round 2 or 3 within the same
    sitting, so editing THOSE rounds' time limits mid-round-1 is just as
    much a live change-out-from-under-them as editing round 1 itself would
    be. Blocked while ANY candidate in this band has an in_progress
    submission on ANY round - not just this one - deferred only until
    they finish that round (or it times out), not until their whole
    assessment is done; between rounds, with no clock actively running,
    edits are allowed again.

    Before counting, lazily closes out anything that LOOKS in_progress
    but has actually already run past its own deadline (see
    scoring_service.close_expired_submissions) - otherwise a candidate
    who abandoned a round (closed the tab, crashed, logged out, network
    loss - anything, not just a deliberate logout) would block this
    forever, since nothing else would ever go back and close it."""
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(404, "Scenario not found")

    band_in_progress = (
        db.query(Submission)
        .join(Scenario, Submission.scenario_id == Scenario.id)
        .filter(
            Scenario.experience_band == scenario.experience_band,
            Submission.status == RoundStatus.in_progress,
            Submission.archived.is_(False),
        )
        .all()
    )
    close_expired_submissions(db, band_in_progress, background_tasks)
    in_progress_emails = sorted({
        s.candidate.email for s in band_in_progress if s.status == RoundStatus.in_progress
    })
    if in_progress_emails:
        # Named by email, not just a count - "2 candidates" on its own
        # gives HR no way to tell whether that's really a live exam in
        # progress or, e.g., a test account left mid-round from earlier
        # testing, without going and looking it up separately.
        who = ", ".join(in_progress_emails)
        raise HTTPException(
            409,
            f"Can't change any round's time limit right now - {who} "
            f"{'are' if len(in_progress_emails) != 1 else 'is'} actively taking a round in this band. "
            f"Try again once they finish that round (or between rounds).",
        )

    scenario.time_limit_minutes = payload.time_limit_minutes
    db.commit()
    db.refresh(scenario)
    return scenario


def _get_round3_scenario_or_404(scenario_id: int, db: Session) -> Scenario:
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(404, "Scenario not found")
    if scenario.round_number != 3:
        raise HTTPException(400, "This setting only applies to round 3 scenarios.")
    return scenario


def _require_round3_not_in_progress(scenario_id: int, db: Session, background_tasks: BackgroundTasks, action: str) -> None:
    """Shared by round3-config and round3-instructions below. Scoped to
    this one exact scenario, not band-wide like the time-limit block:
    both of these only ever affect round 3's own LLM calls, so a
    candidate mid-round-1 or -2 hasn't touched this scenario's behavior
    yet and isn't affected either way. Blocked because changing either
    mid-conversation would mean that same candidate's later turns get
    judged under different rules than their earlier ones - a fairness
    problem regardless of timing.

    Before counting, lazily closes out anything that LOOKS in_progress
    but has actually already run past its own deadline (see
    scoring_service.close_expired_submissions) - otherwise a candidate
    who abandoned round 3 (closed the tab, crashed, logged out, network
    loss) would block this forever, since nothing else would ever go
    back and close it."""
    in_progress = (
        db.query(Submission)
        .filter(
            Submission.scenario_id == scenario_id,
            Submission.status == RoundStatus.in_progress,
            Submission.archived.is_(False),
        )
        .all()
    )
    close_expired_submissions(db, in_progress, background_tasks)
    in_progress_count = sum(1 for s in in_progress if s.status == RoundStatus.in_progress)
    if in_progress_count > 0:
        raise HTTPException(
            409,
            f"Can't {action} right now - {in_progress_count} candidate{'s are' if in_progress_count != 1 else ' is'} "
            f"actively in this round. Changing it mid-conversation would judge them under different rules than they started with.",
        )


@router.patch("/scenarios/{scenario_id}/round3-config", response_model=ScenarioOut)
def update_round3_config(scenario_id: int, payload: Round3ConfigUpdate, background_tasks: BackgroundTasks, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """The one round3-specific tunable exposed to HR: how often the
    simulated assistant gets things right per turn (see Round3ConfigUpdate
    and llm_service.DEFAULT_ROUND3_CONFIG). Allowed regardless of draft/
    published/live status, same reasoning as update_scenario_time_limit -
    doesn't retroactively invalidate anything already scored."""
    scenario = _get_round3_scenario_or_404(scenario_id, db)
    _require_round3_not_in_progress(scenario_id, db, background_tasks, "change the assistant's accuracy")

    scenario.config_json = {**(scenario.config_json or {}), "assistance_pct": payload.assistance_pct}
    db.commit()
    db.refresh(scenario)
    return scenario


@router.patch("/scenarios/{scenario_id}/round3-instructions", response_model=ScenarioOut)
def update_round3_instructions(scenario_id: int, payload: Round3InstructionsUpdate, background_tasks: BackgroundTasks, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Title/description on a round3 scenario, editable regardless of
    status - unlike round1/2 (see ScenarioUpdate, draft-only), round 3
    has no fixed reference answer gating a "review before publish" step,
    so there's no equivalent reason to restrict this to drafts."""
    scenario = _get_round3_scenario_or_404(scenario_id, db)
    _require_round3_not_in_progress(scenario_id, db, background_tasks, "change this round's instructions")

    scenario.title = payload.title
    scenario.description = payload.description
    db.commit()
    db.refresh(scenario)
    return scenario


@router.post("/scenarios/{scenario_id}/publish", response_model=ScenarioOut)
def publish_scenario(scenario_id: int, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Moves a draft into the published library for its round+band. This
    does NOT by itself decide what candidates see - `is_live` (set via
    move_to_screening below) controls that. The one exception: if
    nothing is currently live for this round+band, publishing the first
    scenario for it also makes it live, so a scenario doesn't sit
    published-but-invisible with no HR action ever having asked for that."""
    scenario = _get_draft_scenario_or_404(scenario_id, db)
    # Round 3 has no scenario-level test-case reference to review upfront
    # (its target is each candidate's own round 1 answer) - but it does
    # have its own generated content that must exist before candidates
    # see this scenario: the Test Environment reference sheet and the
    # reference UI screens.
    if scenario.round_number == 3:
        if not scenario.environment_json:
            raise HTTPException(400, "Can't publish a round 3 scenario with no test environment generated yet.")
        if not scenario.ui_mockup_json:
            raise HTTPException(400, "Can't publish a round 3 scenario with no reference UI screens generated yet.")
    elif not scenario.reference_json:
        raise HTTPException(400, "Can't publish a scenario with no reference answer yet - generate or write one first.")

    scenario.status = ScenarioStatus.published
    scenario.published_at = datetime.utcnow()

    has_live = db.query(Scenario).filter(
        Scenario.round_number == scenario.round_number,
        Scenario.experience_band == scenario.experience_band,
        Scenario.is_live.is_(True),
    ).first()
    if has_live is None:
        scenario.is_live = True

    db.commit()
    db.refresh(scenario)
    if scenario.is_live:
        _resync_round3_reference_for_band(scenario, db)
    return scenario


@router.post("/scenarios/{scenario_id}/move-to-screening", response_model=ScenarioOut)
def move_to_screening(scenario_id: int, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Makes this published scenario the one candidates in its round+band
    actually get served, demoting whichever one held that spot before.
    Only one scenario per (round, band) is ever live at a time."""
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(404, "Scenario not found")
    if scenario.status != ScenarioStatus.published:
        raise HTTPException(400, f"Scenario is {scenario.status.value}, not published - publish it first.")
    if scenario.is_live:
        return scenario

    db.query(Scenario).filter(
        Scenario.round_number == scenario.round_number,
        Scenario.experience_band == scenario.experience_band,
        Scenario.is_live.is_(True),
    ).update({"is_live": False})

    scenario.is_live = True
    db.commit()
    db.refresh(scenario)
    _resync_round3_reference_for_band(scenario, db)
    return scenario


def _get_draft_scenario_or_404(scenario_id: int, db: Session) -> Scenario:
    """For actions that only make sense pre-publish: edit, regenerate,
    delete, (re-)publish. A published scenario may have real candidate
    submissions scored against it and must stay exactly as it was -
    move_to_screening is the only thing that still acts on it."""
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(404, "Scenario not found")
    if scenario.status != ScenarioStatus.draft:
        raise HTTPException(400, f"Scenario is {scenario.status.value}, not draft - can't edit/publish it again.")
    return scenario


@router.get("/scenarios", response_model=list[ScenarioOut])
def list_scenarios(db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Every scenario that successfully generated its reference content -
    draft or published - so HR builds a real library over time and
    nothing they intentionally created disappears. The only thing
    filtered out is a draft with no generated content: a failed or
    interrupted generation, not real content (Regenerate fixes those;
    they're reachable directly by id if needed). Round 3's generated
    content is environment_json, not reference_json (see publish_scenario)."""
    scenarios = (
        db.query(Scenario)
        .filter(or_(
            Scenario.reference_json.isnot(None),
            Scenario.environment_json.isnot(None),
        ))
        .order_by(Scenario.id.desc())
        .all()
    )
    return sorted(
        scenarios,
        key=lambda s: (s.round_number, s.experience_band.value, s.status != ScenarioStatus.published.value),
    )


@router.get("/scenarios/{scenario_id}", response_model=ScenarioOut)
def get_scenario(scenario_id: int, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Fetch any one scenario by id, regardless of whether it's the
    'current' one for its slot - list_scenarios() filters for the
    summary view, but HR must still be able to open/review a scenario
    right after creating it even if something else is currently live
    for that round+band."""
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(404, "Scenario not found")
    return scenario


@router.delete("/scenarios/{scenario_id}", status_code=204)
def delete_scenario(scenario_id: int, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Discard a draft you don't want (e.g. a bad description, a failed
    generation). Only drafts - a published scenario can have candidate
    submissions pointing at it and must never be deleted."""
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(404, "Scenario not found")
    if scenario.status != ScenarioStatus.draft:
        raise HTTPException(400, f"Scenario is {scenario.status.value}, not draft - can't delete it.")
    db.delete(scenario)
    db.commit()


# ---- Candidate results dashboard ----

def _build_candidate_summary(candidate: User, db: Session, background_tasks: BackgroundTasks) -> CandidateSummaryOut:
    """Shared by list_candidates and set_candidate_band (which returns
    the one candidate it just updated, in the same shape)."""
    # Only the current cycle's submissions - a reset candidate's old,
    # archived ones must not appear as if they were still active (see
    # Submission.archived / CandidateAppearance).
    current_submissions = [s for s in candidate.submissions if not s.archived]
    # Lazily close out anything that looks in_progress but has actually
    # run past its own deadline (see scoring_service.
    # close_expired_submissions) - otherwise this dashboard would keep
    # showing an abandoned round as "in progress" indefinitely.
    close_expired_submissions(db, current_submissions, background_tasks)
    submissions_by_round = {s.round_number: s for s in current_submissions}
    rounds = []
    aggregate_score = None
    for round_number in (1, 2, 3):
        submission = submissions_by_round.get(round_number)
        if submission is None:
            status = "not_started"
            final_score = None
        else:
            status = submission.status.value
            final_score = submission.score.final_score if submission.score else None
        if final_score is not None:
            aggregate_score = (aggregate_score or 0) + final_score
        tab_switch_count = submission.tab_switch_count if submission else 0
        auto_closed_reason = submission.auto_closed_reason if submission else None
        rounds.append(CandidateRoundSummary(
            round_number=round_number, status=status, final_score=final_score,
            tab_switch_count=tab_switch_count, auto_closed_reason=auto_closed_reason,
        ))

    current_appearance = next((a for a in candidate.appearances if a.is_current), None)
    return CandidateSummaryOut(
        id=candidate.id,
        email=candidate.email,
        experience_band=candidate.experience_band.value if candidate.experience_band else None,
        rounds=rounds,
        exam_date=current_appearance.exam_date if current_appearance else None,
        aggregate_score=aggregate_score,
        reapplied_within_window=current_appearance.reapplied_within_window if current_appearance else False,
    )


@router.get("/candidates", response_model=list[CandidateSummaryOut])
def list_candidates(background_tasks: BackgroundTasks, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    candidates = db.query(User).filter(User.role == Role.candidate).order_by(User.email).all()
    return [_build_candidate_summary(c, db, background_tasks) for c in candidates]


def _build_submission_reports(submissions: list[Submission]) -> list[SubmissionReportOut]:
    """Shared by the current-cycle report and the past-appearance report
    (see candidate_report / appearance_report below) - same shape either
    way, just a different submissions queryset feeding it."""
    out = []
    for s in submissions:
        report = SubmissionReportOut.model_validate(s)
        if s.round_number == 3:
            report.test_cases = [
                Round3TestCaseOut(
                    id=tc.id, title=tc.title, draft_prompt=tc.draft_prompt,
                    created_at=tc.created_at, turn_count=len(tc.turns),
                )
                for tc in s.round3_test_cases
            ]
        out.append(report)
    return out


@router.get("/candidates/{candidate_id}/report", response_model=list[SubmissionReportOut])
def candidate_report(candidate_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """All submissions (with scores where available) for one candidate's
    CURRENT cycle, across all rounds. A past, archived cycle's report is
    GET /candidates/{id}/appearances/{appearance_id}/report instead."""
    candidate = db.get(User, candidate_id)
    if candidate is None or candidate.role != Role.candidate:
        raise HTTPException(404, "Candidate not found")
    submissions = (
        db.query(Submission)
        .filter(Submission.user_id == candidate_id, Submission.archived.is_(False))
        .order_by(Submission.round_number)
        .all()
    )
    close_expired_submissions(db, submissions, background_tasks)
    return _build_submission_reports(submissions)


@router.post("/submissions/{submission_id}/retry-scoring", response_model=SubmissionReportOut)
def retry_scoring(submission_id: int, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """The lightweight recovery path for a submission stuck at
    scoring_failed (see scoring_service.score_submission_in_background) -
    re-runs the same scoring call synchronously (HR clicks it and waits a
    few seconds, like "Regenerate reference" already works), for the
    common case where the failure was transient and a second attempt
    just works. If it isn't - see PATCH .../score below for the manual path."""
    submission = db.get(Submission, submission_id)
    if submission is None:
        raise HTTPException(404, "Submission not found")
    if submission.status != RoundStatus.scoring_failed:
        raise HTTPException(400, f"This submission is {submission.status.value}, not scoring_failed - nothing to retry.")

    score_submission_in_background(submission_id)
    db.refresh(submission)
    return _build_submission_reports([submission])[0]


@router.patch("/submissions/{submission_id}/score", response_model=SubmissionReportOut)
def override_score(submission_id: int, payload: ScoreOverrideRequest, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """HR correcting or hand-entering a score - the human-in-the-loop
    escape hatch this app didn't have before: works whether a Score
    already exists (an LLM score HR disagrees with - the LLM's real
    number is preserved in original_final_score, only on the FIRST
    override) or not (a submission stuck at scoring_failed that HR wants
    to score by hand rather than retry)."""
    submission = db.get(Submission, submission_id)
    if submission is None:
        raise HTTPException(404, "Submission not found")

    score = submission.score
    if score is None:
        score = Score(submission_id=submission.id, original_final_score=None)
        db.add(score)
    elif score.overridden_by_user_id is None:
        # First override - preserve the LLM's real original score before
        # it gets overwritten below. A second/third override must NOT
        # re-copy final_score here, or it would clobber the true
        # original with a previous override's value.
        score.original_final_score = score.final_score

    score.final_score = payload.final_score
    if payload.feedback_text is not None:
        score.feedback_text = payload.feedback_text
    score.overridden_by_user_id = hr.id
    score.override_note = payload.override_note
    score.overridden_at = datetime.utcnow()

    submission.status = RoundStatus.scored
    submission.scoring_error = None
    db.commit()
    db.refresh(submission)
    return _build_submission_reports([submission])[0]


@router.patch("/candidates/{candidate_id}/band", response_model=CandidateSummaryOut)
def set_candidate_band(candidate_id: int, payload: CandidateBandUpdate, background_tasks: BackgroundTasks, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Bulk upload deliberately doesn't set a band (see the upload
    endpoint below) - HR sets it here afterward, once per candidate."""
    candidate = db.get(User, candidate_id)
    if candidate is None or candidate.role != Role.candidate:
        raise HTTPException(404, "Candidate not found")
    candidate.experience_band = ExperienceBand(payload.experience_band)
    db.commit()
    db.refresh(candidate)
    return _build_candidate_summary(candidate, db, background_tasks)


@router.post("/candidates/upload", response_model=BulkUploadResult)
def upload_candidates(file: UploadFile = File(...), db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Bulk-creates (or resets, for a re-applying email - see
    candidate_upload_service._reset_and_archive) candidate logins from an
    uploaded .xlsx or .txt file of email,exam_date rows. Partial success:
    valid rows are processed and committed as they're reached, invalid
    ones are reported and skipped, so one bad row doesn't block the rest
    of the file - see candidate_upload_service.process_upload_rows."""
    content = file.file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(400, "File is too large (max 5MB) - this should only ever be a plain list of emails and exam dates.")
    try:
        rows = candidate_upload_service.parse_upload_rows(file.filename or "", content)
    except Exception as e:
        raise HTTPException(400, f"Couldn't read this file: {e}")
    if not rows:
        raise HTTPException(400, "No rows found in this file.")

    app_settings = get_settings(db)
    return candidate_upload_service.process_upload_rows(db, rows, app_settings)


@router.get("/candidates/{candidate_id}/appearances", response_model=list[CandidateAppearanceOut])
def candidate_appearances(candidate_id: int, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Every upload cycle for this candidate, current and archived - see
    models.CandidateAppearance. Most recent first."""
    candidate = db.get(User, candidate_id)
    if candidate is None or candidate.role != Role.candidate:
        raise HTTPException(404, "Candidate not found")

    out = []
    for appearance in sorted(candidate.appearances, key=lambda a: a.created_at, reverse=True):
        cycle_submissions = [s for s in candidate.submissions if s.appearance_id == appearance.id]
        scores = [s.score.final_score for s in cycle_submissions if s.score and s.score.final_score is not None]
        aggregate_score = sum(scores) if scores else None
        out.append(CandidateAppearanceOut(
            id=appearance.id, exam_date=appearance.exam_date, is_current=appearance.is_current,
            reapplied_within_window=appearance.reapplied_within_window, created_at=appearance.created_at,
            aggregate_score=aggregate_score,
        ))
    return out


@router.get("/candidates/{candidate_id}/appearances/{appearance_id}/report", response_model=list[SubmissionReportOut])
def appearance_report(candidate_id: int, appearance_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Same shape as candidate_report, but for one specific past (or
    current) cycle - HR's "Past appearances" drill-down in the candidate
    detail panel reuses the exact same rendering as the live report."""
    candidate = db.get(User, candidate_id)
    if candidate is None or candidate.role != Role.candidate:
        raise HTTPException(404, "Candidate not found")
    appearance = db.get(CandidateAppearance, appearance_id)
    if appearance is None or appearance.user_id != candidate_id:
        raise HTTPException(404, "Appearance not found")

    submissions = (
        db.query(Submission)
        .filter(Submission.user_id == candidate_id, Submission.appearance_id == appearance_id)
        .order_by(Submission.round_number)
        .all()
    )
    # Lazily close out anything that looks in_progress but has actually
    # run past its own deadline (see scoring_service.
    # close_expired_submissions) - same as candidate_report above, so
    # this drill-down for the current cycle can't show a stale
    # in_progress round that candidate_report would already show closed.
    close_expired_submissions(db, submissions, background_tasks)
    return _build_submission_reports(submissions)


def _gather_candidate_rounds(candidate: User, db: Session, background_tasks: BackgroundTasks) -> list[dict]:
    """Per-round data for whichever rounds this candidate has actually
    reached - feeds both the LLM summary and the PDF's round table.
    A round the candidate hasn't started yet is omitted entirely rather
    than included as an empty stub, so the summary prompt (and the PDF)
    only ever sees rounds that are genuinely in progress or further."""
    # Lazily close out anything that looks in_progress but has actually
    # run past its own deadline (see scoring_service.
    # close_expired_submissions) - otherwise the AI summary/PDF can
    # describe a round as still in_progress when other HR views (see
    # _build_candidate_summary) already show it correctly closed.
    close_expired_submissions(db, list(candidate.submissions), background_tasks)
    submissions_by_round = {s.round_number: s for s in candidate.submissions}
    rounds = []
    for round_number in (1, 2, 3):
        submission = submissions_by_round.get(round_number)
        if submission is None:
            continue
        entry = {
            "round_number": round_number,
            "status": submission.status.value,
            "scenario_title": submission.scenario.title if submission.scenario else None,
        }
        if submission.score is not None:
            entry.update({
                "final_score": submission.score.final_score,
                "coverage_score": submission.score.coverage_score,
                "feedback_text": submission.score.feedback_text,
                "misses": submission.score.misses_json or [],
            })
            # Round 1 only (see models.Score.concept_coverage_json) - []
            # for rounds 2/3, in which case the key is omitted entirely so
            # the summary prompt doesn't have to special-case an empty list.
            if submission.score.concept_coverage_json:
                entry["concept_coverage"] = submission.score.concept_coverage_json
        rounds.append(entry)
    return rounds


@router.post("/candidates/{candidate_id}/summary", response_model=CandidateAssessmentSummaryOut)
def candidate_summary(candidate_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Cross-round synthesis for HR to hand off - to the candidate as
    feedback, or to the next round's interviewers as a briefing. Not
    persisted - generated fresh on each click, same as "Regenerate
    reference" elsewhere in this file. See llm_service.generate_candidate_summary."""
    candidate = db.get(User, candidate_id)
    if candidate is None or candidate.role != Role.candidate:
        raise HTTPException(404, "Candidate not found")

    rounds = _gather_candidate_rounds(candidate, db, background_tasks)
    if not rounds:
        raise HTTPException(400, "This candidate hasn't started any round yet - nothing to summarize.")

    band = candidate.experience_band.value if candidate.experience_band else "unspecified"
    result = llm_service.generate_candidate_summary(
        candidate_email=candidate.email,
        experience_band=band,
        rounds=rounds,
    )
    return CandidateAssessmentSummaryOut(
        candidate_email=candidate.email,
        experience_band=candidate.experience_band.value if candidate.experience_band else None,
        round_comments=[CandidateRoundComment(**r) for r in result["rounds"]],
        final_summary=result["final_summary"],
    )


# fpdf2's built-in core fonts (Helvetica/Times/Courier) only render
# Latin-1 - no embedded Unicode font shipped with this repo, so LLM
# prose (which routinely uses curly quotes, em dashes, ellipses) would
# otherwise crash the endpoint with a 500 on generation. Normalize the
# common "smart typography" characters to their plain-ASCII equivalents,
# then fall back to replacing anything still outside Latin-1 with "?"
# as a last resort - this endpoint must never fail to render just
# because the LLM used a fancy dash.
_SMART_TYPOGRAPHY = {
    "‘": "'", "’": "'",   # ‘ ’
    "“": '"', "”": '"',   # “ ”
    "–": "-", "—": "-",   # – —
    "…": "...",                # …
    "•": "-",                  # •
    # Currency symbols outside Latin-1's coverage - genuinely present in
    # this app's own scenario data (e.g. the seeded doctor-appointment
    # scenario's consultation fee), not just a hypothetical LLM quirk.
    "₹": "Rs.", "€": "EUR", "£": "GBP",
    " ": " ",                  # non-breaking space
}


def _pdf_safe_text(text: str) -> str:
    for smart, plain in _SMART_TYPOGRAPHY.items():
        text = text.replace(smart, plain)
    return text.encode("latin-1", errors="replace").decode("latin-1")


@router.post("/candidates/{candidate_id}/summary/pdf")
def candidate_summary_pdf(
    candidate_id: int,
    payload: CandidateSummaryPdfRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    hr: User = Depends(require_hr),
):
    """Renders the round_comments/final_summary the frontend already
    generated (via the endpoint above) into a downloadable PDF - takes
    them as input rather than regenerating, so downloading doesn't cost
    a second LLM call. Re-fetches the round table itself (for each
    round's status/score, paired with its comment) rather than trusting
    a client-supplied one."""
    candidate = db.get(User, candidate_id)
    if candidate is None or candidate.role != Role.candidate:
        raise HTTPException(404, "Candidate not found")

    rounds_by_number = {r["round_number"]: r for r in _gather_candidate_rounds(candidate, db, background_tasks)}
    band = candidate.experience_band.value if candidate.experience_band else "unspecified"

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Candidate Assessment Summary", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 8, f"Candidate: {_pdf_safe_text(candidate.email)}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"Experience band: {band}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"Generated: {datetime.utcnow().strftime('%Y-%m-%d')}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    for rc in payload.round_comments:
        r = rounds_by_number.get(rc.round_number, {})
        label = ROUND_LABELS.get(rc.round_number, f"Round {rc.round_number}")
        score = r.get("final_score")
        score_text = f"{score}/100" if score is not None else "not scored yet"

        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, f"Round {rc.round_number} - {label}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "I", 9)
        pdf.cell(0, 6, f"Status: {r.get('status', 'unknown')}  |  Score: {score_text}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 7, _pdf_safe_text(rc.comment))
        pdf.ln(2)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Final Summary", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 7, _pdf_safe_text(payload.final_summary))

    pdf_bytes = bytes(pdf.output())
    safe_email = candidate.email.replace("@", "_at_").replace(".", "_")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_email}-summary.pdf"'},
    )


# ---- Screening history ----
#
# Per-scenario performance across everyone who's ever attempted it -
# the point is closing the loop on scenario authoring: a question that
# nobody clears (or that everybody clears) isn't discriminating, and a
# question with a tight cluster of common misses is telling you either
# "candidates keep missing this, it's a good question" or "the prompt/
# reference is ambiguous here" - both worth knowing before writing the
# next one. A scenario_id is a fixed, HR-approved reference the moment
# it's published (edits are draft-only, see _get_draft_scenario_or_404),
# so grouping by scenario_id always compares candidates against the
# exact same question - never a moving target.

@router.get("/history", response_model=list[ScenarioHistoryOut])
def scenario_history(db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    app_settings = get_settings(db)
    scenarios = (
        db.query(Scenario)
        .join(Submission, Submission.scenario_id == Scenario.id)
        .distinct()
        .all()
    )

    out = []
    for scenario in scenarios:
        # Per-round passing score, not one global number - a round's own
        # bar is what should decide whether a submission against it
        # cleared (see AppSettings; this used to read the single global
        # settings.passing_score for every round alike).
        round_passing_score = getattr(app_settings, f"round{scenario.round_number}_passing_score")
        # scenario_history is a question-quality metric ("does this
        # scenario discriminate well across everyone who's ever attempted
        # it"), not a candidate-status metric - deliberately NOT filtered
        # on Submission.archived, unlike the candidate-facing dashboard
        # queries in list_candidates/_gather_candidate_rounds.
        submissions = scenario.submissions
        scored = [s for s in submissions if s.status == RoundStatus.scored and s.score is not None]
        pending = [s for s in submissions if s.status == RoundStatus.submitted]
        cleared = [s for s in scored if (s.score.final_score or 0) >= round_passing_score]

        # Grouped by exact text, not summarized by another LLM call: the
        # scoring LLM already names each gap in its own words per
        # submission, so counting *that* text directly is a transparent,
        # reproducible read on what recurs - worth more here than a fluent
        # paraphrase would be, given this feeds a hiring-process decision.
        misses_counter = Counter(
            miss for s in scored for miss in (s.score.misses_json or [])
        )
        common_misses = [
            MissPattern(text=text, count=count)
            for text, count in misses_counter.most_common(10)
        ]

        # Round 1 only (see models.Score.concept_coverage_json) - empty
        # for round 2/3 scenarios, which never populate that column.
        # Averaged as a percentage (covered/total), not raw counts, since
        # different candidates' reference sets could in principle have a
        # different total per category - a straight count average would
        # be misleading if that ever happens.
        category_pcts: dict[str, list[float]] = {}
        for s in scored:
            for c in (s.score.concept_coverage_json or []):
                total = c.get("total") or 0
                if total <= 0:
                    continue
                category_pcts.setdefault(c.get("category", "Unknown"), []).append(c.get("covered", 0) / total * 100)
        _CATEGORY_ORDER = ["Positive", "Negative", "Boundary", "Edge"]
        concept_coverage_averages = [
            ConceptCoverageAverage(category=cat, avg_pct=round(sum(pcts) / len(pcts), 1), sample_count=len(pcts))
            for cat, pcts in sorted(
                category_pcts.items(),
                key=lambda kv: (_CATEGORY_ORDER.index(kv[0]) if kv[0] in _CATEGORY_ORDER else len(_CATEGORY_ORDER), kv[0]),
            )
        ]

        dates = [s.started_at or s.created_at for s in submissions]

        out.append(ScenarioHistoryOut(
            scenario_id=scenario.id,
            round_number=scenario.round_number,
            experience_band=scenario.experience_band.value,
            title=scenario.title,
            is_live=scenario.is_live,
            first_used_at=min(dates) if dates else None,
            last_used_at=max(dates) if dates else None,
            total_attempted=len(submissions),
            scored_count=len(scored),
            pending_count=len(pending),
            cleared_count=len(cleared),
            not_cleared_count=len(scored) - len(cleared),
            cleared_pct=round(len(cleared) / len(scored) * 100, 1) if scored else None,
            passing_score=round_passing_score,
            common_misses=common_misses,
            concept_coverage_averages=concept_coverage_averages,
        ))

    return sorted(out, key=lambda h: h.last_used_at or datetime.min, reverse=True)
