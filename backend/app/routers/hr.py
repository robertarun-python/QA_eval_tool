"""
HR-only endpoints: author scenarios (draft -> review generated reference ->
publish), and view the candidate results dashboard.

Rounds 1 and 2 have a working reference-generation/scoring pipeline (see
llm_service.py) and share the same candidate-facing row shape (title,
preconditions, steps, expected_result) - round 1's cases and round 2's
debugging steps are structurally identical, just differently worded.
Round 4 is conversational and has no scenario-level test-case reference
(its target is each candidate's own round 1 answer) - instead it has an
auto-generated Test Environment reference sheet (environment_json, see
_generate_reference) that plays the same role reference_json does for
rounds 1/2 (generated at creation, required before publish) - see
publish_scenario and list_scenarios below, and routers/candidate.py for
its dedicated endpoints.
"""
import traceback
import difflib
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, UploadFile, File
from fpdf import FPDF
from fpdf.fonts import FontFace
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    User, Scenario, ScenarioStatus, Submission, Score, RoundStatus, ExperienceBand, Role, AppSettings,
    CandidateAppearance, CandidateSummary, Round3Turn,
)
from pydantic import ValidationError

from ..schemas import (
    ScenarioCreate, ScenarioUpdate, ScenarioOut, SubmissionReportOut,
    CandidateSummaryOut, CandidateRoundSummary, ScenarioHistoryOut, MissPattern, ConceptCoverageAverage,
    Round4TestCaseOut, Round3TurnOut, Round3TurnAuditOut, Round3RunOut, CandidateAssessmentSummaryOut,
    CandidateRoundComment, AppSettingsOut, AppSettingsUpdate,
    BulkUploadResult, CandidateBandUpdate, CandidateAppearanceOut, ScoreOverrideRequest,
    ScenarioTimeLimitUpdate, Round2AutomationInstructionsUpdate, TestCaseRow,
    Round2AutomationEnvironmentUpdate, Round2AutomationEnvironmentOut,
)
from ..dependencies import require_hr
from ..services import llm_service
from ..services import candidate_upload_service
from ..services.practice_app import service as practice_app_service
from ..services.scoring_service import score_submission_in_background, close_expired_submissions, close_expired_assessment_windows, fail_interrupted_scoring

router = APIRouter(prefix="/hr", tags=["hr"])

VALID_BANDS = (ExperienceBand.junior.value, ExperienceBand.senior.value)

# Mirrors app.js's ROUND_LABELS - only used here for the PDF's per-round
# headings, so a small local copy (not worth a shared-constants file
# across two different languages) is the pragmatic choice.
ROUND_LABELS = {1: "Manual test cases", 2: "AI-Assisted Test Automation", 3: "AI-prompted coding", 4: "Debugging"}


def get_settings(db: Session) -> AppSettings:
    """Get-or-create accessor for the AppSettings singleton row (id=1) -
    normally created by migrate_bulk_candidates.py, but this is a safety
    net for a DB that skipped that migration (e.g. a fresh create_all()
    without ever running it)."""
    app_settings = db.get(AppSettings, 1)
    if app_settings is None:
        # HR's dashboard fires several requests at once; on a fresh database
        # two of them could both find no row and both insert it - the loser
        # crashed with a UNIQUE error. Losing that race is fine: use the row
        # the other request created.
        try:
            app_settings = AppSettings(id=1)
            db.add(app_settings)
            db.commit()
        except IntegrityError:
            db.rollback()
            app_settings = db.get(AppSettings, 1)
        db.refresh(app_settings)
    return app_settings


def _app_settings_out(app_settings: AppSettings) -> AppSettingsOut:
    """The HR-editable settings, as the Settings page reads them."""
    return AppSettingsOut(
        round1_passing_score=app_settings.round1_passing_score,
        round2_passing_score=app_settings.round2_passing_score,
        round3_passing_score=app_settings.round3_passing_score,
        round4_passing_score=app_settings.round4_passing_score,
        final_passing_score=app_settings.final_passing_score,
        reapplication_window_months=app_settings.reapplication_window_months,
        assessment_window_days=app_settings.assessment_window_days,
        round1_time_limit_minutes=app_settings.round1_time_limit_minutes,
        round2_time_limit_minutes=app_settings.round2_time_limit_minutes,
        round3_time_limit_minutes=app_settings.round3_time_limit_minutes,
        round4_time_limit_minutes=app_settings.round4_time_limit_minutes,
    )


@router.get("/ai-health")
def ai_health(hr: User = Depends(require_hr)):
    """Recent AI calls since the server started (metadata only - never
    prompts or replies): counts by outcome and the latest calls, so a
    failure the candidate saw as "trouble responding" can be traced to its
    cause (see llm_service._record_call)."""
    calls = llm_service.recent_calls()
    by_outcome: dict[str, int] = {}
    for call in calls:
        by_outcome[call["outcome"]] = by_outcome.get(call["outcome"], 0) + 1
    problems = [c for c in calls if c["outcome"] not in ("ok", "fake")]
    from ..config import settings as app_config
    mode = "fake" if app_config.llm_fake_mode else ("real (tool output)" if app_config.llm_tool_output else "real")
    return {"mode": mode, "total": len(calls), "by_outcome": by_outcome, "recent_problems": problems[:20], "recent_calls": calls[:30]}


@router.get("/settings", response_model=AppSettingsOut)
def get_app_settings(db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    return _app_settings_out(get_settings(db))


@router.put("/settings", response_model=AppSettingsOut)
def update_app_settings(payload: AppSettingsUpdate, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    app_settings = get_settings(db)
    for field, value in payload.model_dump().items():
        setattr(app_settings, field, value)
    db.commit()
    db.refresh(app_settings)
    return _app_settings_out(app_settings)


@router.post("/scenarios", response_model=ScenarioOut, status_code=201)
def create_scenario(payload: ScenarioCreate, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    if payload.round_number not in (1, 2, 3, 4):
        raise HTTPException(400, "round_number must be 1, 2, 3, or 4")
    if payload.experience_band not in VALID_BANDS:
        raise HTTPException(400, "experience_band must be '0-7' or '7+'")
    if payload.time_limit_minutes < 1:
        raise HTTPException(400, "time_limit_minutes must be at least 1")
    # .strip() so whitespace-only doesn't slip past a bare emptiness
    # check - checked before the Scenario row is even created, since the
    # alternative (validate after _generate_reference) would burn a real,
    # ~20-30s LLM call generating a reference for a title/description
    # that was never usable in the first place.
    if not payload.title.strip():
        raise HTTPException(400, "title is required")
    if not payload.description.strip():
        raise HTTPException(400, "description is required")

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
        traceback.print_exc()  # the real cause - the candidate/HR only sees the generic message
        raise HTTPException(502, "Reference generation failed - try again.")


def _generate_reference_unsafe(scenario: Scenario, db: Session) -> None:
    if scenario.round_number == 1:
        scenario.reference_json = llm_service.generate_round1_reference(
            scenario_description=scenario.description,
            experience_band=scenario.experience_band.value,
            time_limit_minutes=scenario.round_time_limit_minutes,
        )
    elif scenario.round_number == 4:
        scenario.reference_json = llm_service.generate_round2_reference(
            scenario_description=scenario.description,
            experience_band=scenario.experience_band.value,
            time_limit_minutes=scenario.round_time_limit_minutes,
        )
    elif scenario.round_number == 3:
        scenario.reference_json = llm_service.generate_round3_reference(
            scenario_description=scenario.description,
            experience_band=scenario.experience_band.value,
            io_format=scenario.round3_io_format,
        )
    elif scenario.round_number == 2:
        # Round 4 has no scenario-level test-case reference (its target
        # is each candidate's own round 1 answer) - what it needs instead
        # is a Test Environment reference sheet (credentials, API
        # endpoints, DB schema, ...) plus a reference sketch of the app's
        # screens, both shown to every candidate. Both describe the
        # actual app under test, which lives in round 1's scenario, not
        # round 4's own (round 4's description is just instructions to
        # the candidate, not a description of the app) - ground both in
        # whichever round 1 scenario is currently live for this band,
        # falling back to round 4's own text only if round 1 hasn't been
        # published for this band yet. Same lifecycle as reference_json
        # otherwise: generated here (together, one HR "Regenerate" action
        # refreshes both), required before publish (see publish_scenario).
        live_round1 = db.query(Scenario).filter(
            Scenario.round_number == 1,
            Scenario.experience_band == scenario.experience_band,
            Scenario.is_live.is_(True),
        ).first()
        app_description = live_round1.description if live_round1 else scenario.description
        scenario.environment_json = llm_service.generate_round2_automation_environment(
            app_description=app_description,
        )
        scenario.ui_mockup_json = llm_service.generate_round2_automation_ui_mockup(
            app_description=app_description,
        )
        # A fresh AI-generated sheet, not HR's own edit anymore - see
        # environment_hr_edited and update_round2_automation_environment below.
        scenario.environment_hr_edited = False
    db.commit()
    db.refresh(scenario)


def _resync_round2_automation_reference_for_band(round1_scenario: Scenario, db: Session) -> None:
    """Called right after a round1 scenario newly goes live (see
    publish_scenario/move_to_screening below). If a round4 scenario is
    ALSO currently live for the same band, its environment_json/
    ui_mockup_json were grounded in whichever round1 scenario was live
    at the moment IT was created or last regenerated (see
    _generate_reference_unsafe above) - now stale, since a different
    round1 scenario just took over. Left alone, every candidate in this
    band would see round4 test data (credentials, API endpoints, screens)
    describing a completely different app than the one their own round1
    answer was actually about - a real, observed bug this fixes at the
    source instead of requiring HR to remember to hit "Regenerate" on
    round4 every time round1 rotates.

    Best-effort: a failure here (LLM error, bad shape) must not block the
    round1 publish/promotion that triggered it - HR still has the manual
    Regenerate button on round4 as a fallback.

    Also skipped (silently, same best-effort spirit) while any candidate
    is actively mid-round-4 on that live scenario - same "don't change
    the rules mid-conversation" guard as every other round4-config
    mutation (see _require_round2_automation_not_in_progress). This action is about
    round 1, not round 4, but round 1 going live is exactly what
    triggers this resync, so without the guard a candidate's
    environment/screens could silently change out from under them
    mid-conversation."""
    if round1_scenario.round_number != 1:
        return
    live_round2_automation = db.query(Scenario).filter(
        Scenario.round_number == 2,
        Scenario.experience_band == round1_scenario.experience_band,
        Scenario.is_live.is_(True),
    ).first()
    if live_round2_automation is None:
        return
    if (live_round2_automation.config_json or {}).get("paired_round1_title"):
        # A fixed practice environment paired with one Round 1 scenario (see
        # seed_round2_appointments.py): its facts and screens match that
        # environment's code, so they're never regenerated. HR is warned in
        # the Round 2 settings card when the live Round 1 doesn't match.
        return
    in_progress_count = (
        db.query(Submission)
        .filter(
            Submission.scenario_id == live_round2_automation.id,
            Submission.status == RoundStatus.in_progress,
            Submission.archived.is_(False),
        )
        .count()
    )
    if in_progress_count > 0:
        return
    try:
        # environment_hr_edited (see models.Scenario) means HR hand-typed
        # specific credentials/fields here - e.g. so a candidate's round 1
        # test case and round 4 automation both key off the same login.
        # This resync must not silently overwrite that; only HR's own
        # "Regenerate" button (regenerate_reference) is allowed to.
        if not live_round2_automation.environment_hr_edited:
            live_round2_automation.environment_json = llm_service.generate_round2_automation_environment(
                app_description=round1_scenario.description,
            )
        live_round2_automation.ui_mockup_json = llm_service.generate_round2_automation_ui_mockup(
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
    truth out from under whoever's already been scored. Round 4 has no
    such answer key (its "reference" is just environment/screen flavor
    text), and its scenarios go live immediately on creation (see
    createRound2AutomationScenario in app.js) rather than sitting as a draft
    first, so it gets the same live-editable-but-blocked-mid-round
    treatment as its other settings instead."""
    existing = db.get(Scenario, scenario_id)
    paired = (existing.config_json or {}).get("paired_round1_title") if existing else None
    if paired:
        raise HTTPException(400, f"This Round 2 scenario uses a fixed practice environment built for \"{paired}\"; "
                                 "its reference facts and screens match that environment and can't be regenerated.")
    scenario = db.get(Scenario, scenario_id)
    if scenario is not None and scenario.round_number == 2:
        _require_round2_automation_not_in_progress(scenario_id, db, background_tasks, "regenerate this round's environment & screens")
    else:
        scenario = _get_draft_scenario_or_404(scenario_id, db)
    _generate_reference(scenario, db)
    return scenario


def _validate_reference_json_for_update(round_number: int, reference_json):
    """reference_json's shape depends on round_number - list[TestCaseRow]
    for rounds 1/2, {"test_cases": [...], "expected_approach": "..."} for
    round 3 (see ScenarioOut.reference_json / ScenarioUpdate.reference_json
    above, and llm_service.generate_round3_reference which produces this
    same shape). ScenarioUpdate.reference_json is typed Any now (pydantic
    can't shape-check a round-dependent field on the wire), so this is
    where that validation actually happens - a basic "is this shape sane"
    check, not exhaustive, but enough to reject something obviously wrong
    with a clear 4xx instead of corrupting the scenario or blowing up at
    scoring time later. Returns the value to actually store."""
    if round_number == 3:
        if (
            not isinstance(reference_json, dict)
            or not isinstance(reference_json.get("test_cases"), list)
            or not isinstance(reference_json.get("expected_approach"), str)
        ):
            raise HTTPException(
                400,
                "reference_json for round 3 must be an object with a 'test_cases' list "
                "and an 'expected_approach' string",
            )
        return reference_json
    if not isinstance(reference_json, list):
        raise HTTPException(400, "reference_json must be a list of test-case rows")
    # Checked before TestCaseRow(**row) rather than folded into the
    # except below: a non-dict row (a string, a number, a nested list)
    # raises a plain TypeError there whose message is Python/pydantic
    # internals ("...argument after ** must be a mapping, not str"), not
    # something HR should ever see in an HTTP error body. A ValidationError
    # (a dict that's just missing/wrong-typed fields) IS a clean,
    # purpose-built pydantic message, so that one's fine to surface as-is.
    for row in reference_json:
        if not isinstance(row, dict):
            raise HTTPException(400, f"reference_json must be a list of test-case rows: each row must be an object, got {type(row).__name__}")
    try:
        rows = [TestCaseRow(**row) for row in reference_json]
    except ValidationError as e:
        raise HTTPException(400, f"reference_json must be a list of test-case rows: {e}")
    return [row.model_dump() for row in rows]


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
        scenario.reference_json = _validate_reference_json_for_update(scenario.round_number, payload.reference_json)

    db.commit()
    db.refresh(scenario)
    return scenario


@router.patch("/scenarios/{scenario_id}/time-limit", response_model=ScenarioOut)
def update_scenario_time_limit(scenario_id: int, payload: ScenarioTimeLimitUpdate, background_tasks: BackgroundTasks, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Rounds 1-3: same rule as title/description/reference_json (see
    update_scenario / _get_draft_scenario_or_404) - a published scenario's
    parameters must stay exactly as they were when candidates were scored
    against it, time limit included, so this 400s once the scenario is no
    longer a draft. This used to be a deliberate exception (mutable
    regardless of publish status, gated only on the in-progress check
    below) so HR could quick-fix a live time limit without unpublishing -
    but that let two candidates take the "same" published scenario under
    two different real durations with no record of which one applied to
    which candidate, which is exactly the kind of scoring-relevant
    inconsistency the draft-only rule exists to prevent elsewhere. Fixing
    a wrong live time limit now means publishing a corrected scenario and
    making that one live instead, same as fixing a wrong title/reference
    already requires.

    Round 4 keeps the old always-mutable-but-guarded behavior: it never
    has a draft phase to edit before going live (see
    regenerate_reference's docstring above - its scenarios go live
    immediately on creation), so "draft-only" isn't a meaningful
    restriction there; the in-progress guard below is what already
    handles Round 4 safely.

    The in-progress guard is scoped to the whole band, not just this one
    scenario: a candidate who's actively taking round 1 could reach round
    2 or 3 within the same sitting, so editing THOSE rounds' time limits
    mid-round-1 is just as much a live change-out-from-under-them as
    editing round 1 itself would be. Blocked while ANY candidate in this
    band has an in_progress submission on ANY round - not just this one -
    deferred only until they finish that round (or it times out), not
    until their whole assessment is done; between rounds, with no clock
    actively running, edits are allowed again.

    Before counting, lazily closes out anything that LOOKS in_progress
    but has actually already run past its own deadline (see
    scoring_service.close_expired_submissions) - otherwise a candidate
    who abandoned a round (closed the tab, crashed, logged out, network
    loss - anything, not just a deliberate logout) would block this
    forever, since nothing else would ever go back and close it."""
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(404, "Scenario not found")
    if scenario.round_number != 2 and scenario.status != ScenarioStatus.draft:
        raise HTTPException(400, "This scenario is published - its time limit can't change anymore. Publish a new scenario with the corrected time limit instead.")

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


def _get_round2_automation_scenario_or_404(scenario_id: int, db: Session) -> Scenario:
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(404, "Scenario not found")
    if scenario.round_number != 2:
        raise HTTPException(400, "This setting only applies to round 4 scenarios.")
    return scenario


def _require_round2_automation_not_in_progress(scenario_id: int, db: Session, background_tasks: BackgroundTasks, action: str) -> None:
    """Shared by round4-config and round4-instructions below. Scoped to
    this one exact scenario, not band-wide like the time-limit block:
    both of these only ever affect round 4's own LLM calls, so a
    candidate mid-round-1 or -2 hasn't touched this scenario's behavior
    yet and isn't affected either way. Blocked because changing either
    mid-conversation would mean that same candidate's later turns get
    judged under different rules than their earlier ones - a fairness
    problem regardless of timing.

    Before counting, lazily closes out anything that LOOKS in_progress
    but has actually already run past its own deadline (see
    scoring_service.close_expired_submissions) - otherwise a candidate
    who abandoned round 4 (closed the tab, crashed, logged out, network
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


@router.patch("/scenarios/{scenario_id}/round4-instructions", response_model=ScenarioOut)
def update_round2_automation_instructions(scenario_id: int, payload: Round2AutomationInstructionsUpdate, background_tasks: BackgroundTasks, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Title/description on a round4 scenario, editable regardless of
    status - unlike round1/2 (see ScenarioUpdate, draft-only), round 4
    has no fixed reference answer gating a "review before publish" step,
    so there's no equivalent reason to restrict this to drafts."""
    scenario = _get_round2_automation_scenario_or_404(scenario_id, db)
    _require_round2_automation_not_in_progress(scenario_id, db, background_tasks, "change this round's instructions")

    scenario.title = payload.title
    scenario.description = payload.description
    db.commit()
    db.refresh(scenario)
    return scenario


@router.patch("/scenarios/{scenario_id}/round4-environment", response_model=ScenarioOut)
def update_round2_automation_environment(scenario_id: int, payload: Round2AutomationEnvironmentUpdate, background_tasks: BackgroundTasks, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Lets HR hand-set the auto-generated environment_json.fields (e.g.
    pin a specific test login) instead of only being able to regenerate
    the whole sheet blind. Sets environment_hr_edited so
    _resync_round2_automation_reference_for_band stops silently overwriting this on
    an unrelated round1 rotation - see that function and models.Scenario.
    Blocked mid-round for the same fairness reason as round4-config/
    round4-instructions: a candidate's test data shouldn't change under
    them mid-conversation."""
    scenario = _get_round2_automation_scenario_or_404(scenario_id, db)
    _require_round2_automation_not_in_progress(scenario_id, db, background_tasks, "change this round's test environment")

    scenario.environment_json = Round2AutomationEnvironmentOut(fields=payload.fields, notes=payload.notes).model_dump()
    scenario.environment_hr_edited = True
    db.commit()
    db.refresh(scenario)
    return scenario


def _reject_retired_round2_mode(scenario: Scenario) -> None:
    """Round 2's legacy conversational and pilot modes are retired (their
    old results stay viewable) - only an AI-Assisted Test Automation
    scenario can be published or go live."""
    if scenario.round_number == 2 and (scenario.config_json or {}).get("mode") != "ai_test_automation":
        raise HTTPException(
            400,
            "This Round 2 format is retired. Use an AI-Assisted Test Automation scenario for round 2 instead.",
        )


@router.post("/scenarios/{scenario_id}/publish", response_model=ScenarioOut)
def publish_scenario(scenario_id: int, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Moves a draft into the published library for its round+band. This
    does NOT by itself decide what candidates see - `is_live` (set via
    move_to_screening below) controls that. The one exception: if
    nothing is currently live for this round+band, publishing the first
    scenario for it also makes it live, so a scenario doesn't sit
    published-but-invisible with no HR action ever having asked for that."""
    scenario = _get_draft_scenario_or_404(scenario_id, db)
    _reject_retired_round2_mode(scenario)
    if scenario.round_number == 2:
        # AI-Assisted Test Automation (the only live round 2 format) - the
        # candidate automates their own round 1 design, so there's no
        # scenario-level test-case reference. What must exist is the
        # HR/system-only ground truth the scorer judges the candidate's
        # execution interpretation against (see seed_round2_automation.py) and
        # the automation environment code.
        if not (scenario.reference_json or {}).get("ground_truth"):
            raise HTTPException(400, "Can't publish an automation scenario with no ground truth for execution interpretation yet.")
        if not (scenario.config_json or {}).get("environment_code_by_language"):
            raise HTTPException(400, "Can't publish an automation scenario with no automation environment configured yet.")
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

    if scenario.is_live:
        practice_app_service.activate_paired_round2(scenario, db)
    db.commit()
    db.refresh(scenario)
    if scenario.is_live:
        _resync_round2_automation_reference_for_band(scenario, db)
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
    _reject_retired_round2_mode(scenario)

    db.query(Scenario).filter(
        Scenario.round_number == scenario.round_number,
        Scenario.experience_band == scenario.experience_band,
        Scenario.is_live.is_(True),
    ).update({"is_live": False})

    scenario.is_live = True
    # A Round 1 scenario's approved practice app goes live with it.
    practice_app_service.activate_paired_round2(scenario, db)
    db.commit()
    db.refresh(scenario)
    _resync_round2_automation_reference_for_band(scenario, db)
    return scenario


# ---- Round 2 practice app, built from a Round 1 scenario (see services/practice_app) ----

@router.get("/scenarios/{scenario_id}/practice-app")
def practice_app_status(scenario_id: int, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(404, "Scenario not found")
    return {**practice_app_service.summary(scenario), "cannot_start": practice_app_service.can_start(scenario),
            "estimate": practice_app_service.ESTIMATE}


@router.post("/scenarios/{scenario_id}/practice-app", status_code=202)
def build_practice_app(scenario_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Starts building the Round 2 practice app for this Round 1 scenario.
    Makes paid AI calls (see practice_app_service.ESTIMATE) - the HR screen
    asks for confirmation first."""
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(404, "Scenario not found")
    reason = practice_app_service.can_start(scenario)
    if reason:
        raise HTTPException(400, reason)
    data = practice_app_service.start_build(scenario, db)
    background_tasks.add_task(practice_app_service.run_build, scenario_id)
    return data


@router.post("/scenarios/{scenario_id}/practice-app/approve")
def approve_practice_app(scenario_id: int, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(404, "Scenario not found")
    try:
        round2 = practice_app_service.approve(scenario, db)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"round2_scenario_id": round2.id, "round2_is_live": round2.is_live, **practice_app_service.summary(scenario)}


def _get_draft_scenario_or_404(scenario_id: int, db: Session) -> Scenario:
    """For actions that only make sense pre-publish: edit, regenerate,
    (re-)publish. A published scenario's content must stay exactly as it
    was reviewed/scored against - move_to_screening is the only thing
    that still acts on it. Deleting is a separate concern from editing
    content, so delete_scenario below does NOT use this helper - it
    allows both draft and published, gated on is_live/submissions
    instead of status."""
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
    they're reachable directly by id if needed). Round 4's generated
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
    """Discard a scenario HR doesn't want - a bad draft, a duplicate, a
    published-but-never-actually-used one. Two guards, in both draft and
    published status:
    - Never the currently live one - that's what's actively being served
      to candidates in this round+band right now; deleting it out from
      under an in-progress candidate (or leaving the band with nothing
      live at all) is never the right way to retire it. Publish a
      replacement and make THAT live first (see move_to_screening), then
      delete this one.
    - Never one with ANY submission pointing at it - published or draft,
      archived or not. A submission can only exist by a candidate (or
      test) actually starting/attempting it, so this is the real
      signal of "has this scenario ever been used", not scenario.status -
      a draft can accumulate a submission during dev/testing just like a
      published one can. Deleting a scenario out from under a real
      submission would orphan it (its scenario_id would point nowhere),
      breaking every downstream join that reads submission.scenario
      (HR's reports, candidate history, aggregate scoring) - the
      candidate.py comment on Submission.archived explains why archiving
      instead of deleting is this app's standing pattern for exactly
      this class of problem; scenarios follow the same rule."""
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(404, "Scenario not found")
    if scenario.is_live:
        raise HTTPException(400, "Can't delete the live scenario - publish a different one and make it live first.")
    has_submissions = db.query(Submission.id).filter(Submission.scenario_id == scenario_id).first() is not None
    if has_submissions:
        raise HTTPException(400, "Can't delete this scenario - at least one submission (including archived/test ones) points to it.")
    db.delete(scenario)
    db.commit()


# ---- Candidate results dashboard ----

def _build_candidate_summary(candidate: User, db: Session, background_tasks: BackgroundTasks) -> CandidateSummaryOut:
    """Shared by list_candidates and set_candidate_band (which returns
    the one candidate it just updated, in the same shape)."""
    app_settings = get_settings(db)
    # Only the current cycle's submissions - a reset candidate's old,
    # archived ones must not appear as if they were still active (see
    # Submission.archived / CandidateAppearance).
    current_submissions = [s for s in candidate.submissions if not s.archived]
    # Lazily close out anything that looks in_progress but has actually
    # run past its own deadline (see scoring_service.
    # close_expired_submissions) - otherwise this dashboard would keep
    # showing an abandoned round as "in progress" indefinitely.
    close_expired_submissions(db, current_submissions, background_tasks)
    # One level earlier: a round that was never even started has no
    # started_at for the check above to ever act on, so without this,
    # "not_started" has no maximum residency at all - see
    # scoring_service.close_expired_assessment_windows.
    current_submissions = current_submissions + close_expired_assessment_windows(
        db, candidate, app_settings, background_tasks,
    )
    submissions_by_round = {s.round_number: s for s in current_submissions}
    rounds = []
    aggregate_score = None
    all_four_scored = True
    for round_number in (1, 2, 3, 4):
        submission = submissions_by_round.get(round_number)
        if submission is None:
            status = "not_started"
            final_score = None
        else:
            status = submission.status.value
            final_score = submission.score.final_score if submission.score else None
        if status != "scored":
            all_four_scored = False
        if final_score is not None:
            aggregate_score = (aggregate_score or 0) + final_score
        tab_switch_count = submission.tab_switch_count if submission else 0
        auto_closed_reason = submission.auto_closed_reason if submission else None
        rounds.append(CandidateRoundSummary(
            round_number=round_number, status=status, final_score=final_score,
            submitted_at=submission.submitted_at if submission else None,
            tab_switch_count=tab_switch_count, auto_closed_reason=auto_closed_reason,
        ))

    # "selected"/"not_selected" only once every round is actually
    # scored - a partial aggregate mid-assessment (or one round stuck at
    # scoring_failed) is never a real verdict, so it stays "in_progress"
    # until all four genuinely have a final_score. Aggregate-only against
    # AppSettings.final_passing_score, per the app's existing convention
    # (see the aggregate-score color-coding this same comparison already
    # drives in app.js) - not also gated on each round's own per-round
    # passing_score individually.
    if all_four_scored:
        result = "selected" if (aggregate_score or 0) >= app_settings.final_passing_score else "not_selected"
    else:
        result = "in_progress"

    current_appearance = next((a for a in candidate.appearances if a.is_current), None)
    exam_date = current_appearance.exam_date if current_appearance else None
    if exam_date is None:
        # No CandidateAppearance row at all (candidate was never part of a
        # bulk roster upload - e.g. a seeded/test account created directly
        # via app.seed) means there's no HR-declared exam date to show.
        # Fall back to the date they actually first submitted a round, so
        # the column reflects real activity instead of staying blank
        # forever regardless of how much of the assessment they finish.
        submitted_dates = [r.submitted_at for r in rounds if r.submitted_at is not None]
        exam_date = min(submitted_dates) if submitted_dates else None
    return CandidateSummaryOut(
        id=candidate.id,
        email=candidate.email,
        experience_band=candidate.experience_band.value if candidate.experience_band else None,
        rounds=rounds,
        exam_date=exam_date,
        aggregate_score=aggregate_score,
        reapplied_within_window=current_appearance.reapplied_within_window if current_appearance else False,
        result=result,
    )


@router.get("/candidates", response_model=list[CandidateSummaryOut])
def list_candidates(background_tasks: BackgroundTasks, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    fail_interrupted_scoring(db)
    candidates = db.query(User).filter(User.role == Role.candidate).order_by(User.email).all()
    return [_build_candidate_summary(c, db, background_tasks) for c in candidates]


_ROUND3_SCOPE_LABELS = {
    "clarify": "clarification requested (no code change)",
    "refuse": "refused - outside the granted assistance scope",
    "code_edit": "narrow code edit",
    "direct_edit": "candidate-pasted code (syntax check only)",
    "explain": "explanation only (no code change)",
}


def _round3_turn_audit(turn: Round3Turn, previous_code: str | None) -> Round3TurnAuditOut:
    """HR-only audit record for one AI interaction - candidate request
    (candidate_prompt, already on the base turn), AI response/action
    (response_kind/response_message), requested scope, whether code was
    modified, and how many lines changed, alongside the existing
    timestamp - see Round3TurnAuditOut's docstring for why the extra
    fields are derived here rather than stored as new columns."""
    code_modified = turn.response_kind in ("code_edit", "direct_edit")
    lines_changed = None
    if code_modified and turn.code_after is not None:
        before_lines = (previous_code or "").splitlines()
        after_lines = turn.code_after.splitlines()
        diff = difflib.ndiff(before_lines, after_lines)
        lines_changed = sum(1 for line in diff if line.startswith("+ ") or line.startswith("- "))
    return Round3TurnAuditOut(
        **Round3TurnOut.model_validate(turn).model_dump(),
        code_modified=code_modified,
        requested_scope=_ROUND3_SCOPE_LABELS.get(turn.response_kind, turn.response_kind),
        lines_changed=lines_changed,
    )


def _build_submission_reports(submissions: list[Submission]) -> list[SubmissionReportOut]:
    """Shared by the current-cycle report and the past-appearance report
    (see candidate_report / appearance_report below) - same shape either
    way, just a different submissions queryset feeding it."""
    out = []
    for s in submissions:
        report = SubmissionReportOut.model_validate(s)
        if s.round_number == 3:
            audit_turns = []
            previous_code = None
            for t in s.round3_turns:
                audit_turns.append(_round3_turn_audit(t, previous_code))
                if t.code_after is not None:
                    previous_code = t.code_after
            report.round3_turns = audit_turns
            report.round3_runs = [Round3RunOut.model_validate(r) for r in s.round3_execution_runs]
        elif s.round_number == 2:
            report.test_cases = [
                Round4TestCaseOut(
                    id=tc.id, title=tc.title, draft_prompt=tc.draft_prompt,
                    created_at=tc.created_at, turn_count=len(tc.turns),
                )
                for tc in s.round4_test_cases
            ]
        out.append(report)
    return out


@router.get("/candidates/{candidate_id}/report", response_model=list[SubmissionReportOut])
def candidate_report(candidate_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """All submissions (with scores where available) for one candidate's
    CURRENT cycle, across all rounds. A past, archived cycle's report is
    GET /candidates/{id}/appearances/{appearance_id}/report instead."""
    fail_interrupted_scoring(db)
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
    """Not currently reachable from the UI - experience band is a hidden
    feature (see app.js), and every candidate (seeded or bulk-uploaded)
    now gets the same band automatically at creation. Left in place as a
    direct API for re-enabling per-candidate bands later."""
    candidate = db.get(User, candidate_id)
    if candidate is None or candidate.role != Role.candidate:
        raise HTTPException(404, "Candidate not found")
    candidate.experience_band = ExperienceBand(payload.experience_band)
    db.commit()
    db.refresh(candidate)
    return _build_candidate_summary(candidate, db, background_tasks)


@router.post("/candidates/upload", response_model=BulkUploadResult)
def upload_candidates(background_tasks: BackgroundTasks, file: UploadFile = File(...), db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Bulk-creates (or resets, for a re-applying email - see
    candidate_upload_service._reset_and_archive) candidate logins from an
    uploaded .xlsx or .txt file of email,exam_date rows. Partial success:
    valid rows are processed and committed as they're reached, invalid
    ones are reported and skipped, so one bad row doesn't block the rest
    of the file - see candidate_upload_service.process_upload_rows.
    background_tasks is threaded through to there: a reset that finalizes
    an abandoned in_progress round needs to schedule its scoring the same
    way close_expired_submissions does."""
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
    return candidate_upload_service.process_upload_rows(db, rows, app_settings, background_tasks)


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
    # Only the current cycle's submissions, same filter _build_candidate_summary
    # uses - an archived retry must never outrank the live attempt just because
    # of incidental relationship/dict ordering.
    current_submissions = [s for s in candidate.submissions if not s.archived]
    submissions_by_round = {s.round_number: s for s in current_submissions}
    rounds = []
    for round_number in (1, 2, 3, 4):
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
            # for rounds 2/4, in which case the key is omitted entirely so
            # the summary prompt doesn't have to special-case an empty list.
            if submission.score.concept_coverage_json:
                entry["concept_coverage"] = submission.score.concept_coverage_json
        rounds.append(entry)
    return rounds


def _candidate_summary_out(candidate: User, summary: CandidateSummary) -> CandidateAssessmentSummaryOut:
    return CandidateAssessmentSummaryOut(
        candidate_email=candidate.email,
        experience_band=candidate.experience_band.value if candidate.experience_band else None,
        round_comments=[CandidateRoundComment(**r) for r in summary.round_comments_json],
        key_observations=summary.key_observations_json,
        verdict=summary.verdict,
        generated_at=summary.updated_at,
    )


@router.post("/candidates/{candidate_id}/summary", response_model=CandidateAssessmentSummaryOut)
def candidate_summary(candidate_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Cross-round synthesis for HR to hand off - to the candidate as
    feedback, or to the next round's interviewers as a briefing. Generates
    via the LLM and saves the result (see models.CandidateSummary) - used
    to be regenerated fresh on every click with nothing saved, which meant
    downloading the PDF later meant paying for a second LLM call and
    risked it reading differently than what HR actually reviewed. Calling
    this again (HR's "Regenerate") overwrites whatever was saved before.
    See llm_service.generate_candidate_summary, and GET below for reading
    the saved one back with no LLM call."""
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
    # Validated before ever being persisted - a malformed generation (a
    # missing did_well/missed key, an oversized bullet) must never sail
    # into the DB only to blow up the next time GET/the PDF tries to read
    # it back; a clean 502 here is a retryable failure, same reasoning as
    # every other LLM-response validation in this app.
    try:
        validated_rounds = [CandidateRoundComment(**r) for r in result["rounds"]]
    except ValidationError as e:
        traceback.print_exc()  # the real cause - the candidate/HR only sees the generic message
        raise HTTPException(502, f"The generated summary didn't match the expected shape: {e}")

    summary = db.query(CandidateSummary).filter(CandidateSummary.user_id == candidate.id).first()
    if summary is None:
        summary = CandidateSummary(user_id=candidate.id)
        db.add(summary)
    summary.round_comments_json = [r.model_dump() for r in validated_rounds]
    summary.key_observations_json = result["key_observations"]
    summary.verdict = result["verdict"]
    db.commit()
    db.refresh(summary)
    return _candidate_summary_out(candidate, summary)


@router.get("/candidates/{candidate_id}/summary", response_model=CandidateAssessmentSummaryOut)
def get_candidate_summary(candidate_id: int, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Reads back whatever was last saved by POST above - no LLM call,
    so opening a candidate's detail view can show an already-generated
    summary (and offer it for PDF download) without regenerating it every
    time. 404 means nothing has been generated yet, not an error - the
    frontend uses that to show "Generate Summary" instead."""
    candidate = db.get(User, candidate_id)
    if candidate is None or candidate.role != Role.candidate:
        raise HTTPException(404, "Candidate not found")
    summary = db.query(CandidateSummary).filter(CandidateSummary.user_id == candidate.id).first()
    if summary is None:
        raise HTTPException(404, "No summary has been generated for this candidate yet.")
    return _candidate_summary_out(candidate, summary)


@router.delete("/candidates/{candidate_id}/summary", status_code=204)
def delete_candidate_summary(candidate_id: int, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Clears a saved summary - e.g. after a re-application, when the one
    on file no longer reflects the candidate's current cycle. Idempotent:
    succeeds whether or not one existed, same as this app's other delete
    endpoints."""
    candidate = db.get(User, candidate_id)
    if candidate is None or candidate.role != Role.candidate:
        raise HTTPException(404, "Candidate not found")
    db.query(CandidateSummary).filter(CandidateSummary.user_id == candidate.id).delete()
    db.commit()


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


# ---- Enterprise report styling (candidate_summary_pdf below) ----
# One centralized palette/type scale so every section of the report
# pulls from the same tokens instead of scattering magic RGB tuples
# through the function. Inter isn't bundled with this repo (no font
# file shipped, and adding one is a bigger dependency than a report
# template warrants) - this falls back to Helvetica, exactly per the
# report spec's own "Inter if available, otherwise Arial/Helvetica"
# fallback rule; fpdf2's core fonts render Helvetica natively.
_REPORT_FONT = "Helvetica"
_NAVY = (23, 54, 93)
_BLUE = (47, 117, 181)
_TEAL = (0, 140, 149)
_CRITICAL = (198, 40, 40)
_CRITICAL_FILL = (250, 231, 231)
_HIGH = (230, 81, 0)
_HIGH_FILL = (253, 236, 220)
_MEDIUM = (183, 121, 31)
_MEDIUM_FILL = (250, 240, 219)
_SUCCESS = (46, 125, 50)
_SUCCESS_FILL = (232, 244, 233)
_TEXT = (38, 50, 56)
_MUTED = (99, 111, 118)
_BORDER = (214, 219, 223)
_CARD_BG = (246, 247, 248)
_WHITE = (255, 255, 255)


def _severity_for_score(score: int | None, passing_score: int) -> tuple[str, tuple, tuple]:
    """Buckets an EXISTING final_score against its EXISTING passing_score
    into a presentation-only severity tier (label, text color, fill
    color) - never a new judgment layered on top of the real score,
    just a color/label band for the same number the app already
    computes and shows everywhere else."""
    if score is None:
        return "Not scored", _MUTED, _CARD_BG
    if score >= passing_score:
        return "Strong", _SUCCESS, _SUCCESS_FILL
    if score >= passing_score * 0.5:
        return "Medium Concern", _MEDIUM, _MEDIUM_FILL
    if score >= passing_score * 0.25:
        return "High Concern", _HIGH, _HIGH_FILL
    return "Critical Concern", _CRITICAL, _CRITICAL_FILL


def _verdict_sentiment(verdict_text: str) -> tuple[str, tuple, tuple]:
    """A mechanical keyword read of the LLM's own verdict sentence into
    an ADVANCE / DO NOT ADVANCE / CONDITIONAL headline label. The full,
    original verdict text is always rendered verbatim alongside this
    label (see the Executive Recommendation and Final Assessment
    sections below) - this never changes, rewrites, or overrides what
    the verdict actually says, it only gives the existing wording a
    prominent, color-coded headline."""
    t = verdict_text.lower()
    if "not recommend" in t or "do not advance" in t or "reject" in t:
        return "DO NOT ADVANCE", _CRITICAL, _CRITICAL_FILL
    if "conditional" in t:
        return "CONDITIONAL ADVANCE", _MEDIUM, _MEDIUM_FILL
    if "recommend advanc" in t or "advance" in t:
        return "ADVANCE", _SUCCESS, _SUCCESS_FILL
    return "MANUAL REVIEW", _BLUE, (232, 240, 247)


def _first_sentence(text: str) -> str:
    for sep in (". ", "! ", "? "):
        idx = text.find(sep)
        if idx != -1:
            return text[: idx + 1]
    return text


def _split_observation(text: str) -> tuple[str, str]:
    """Splits a key-observation sentence into a short card title + body,
    purely mechanically (on the sentence's own first colon) - never
    inventing a category label the underlying data doesn't have. Falls
    back to a generic title when the sentence has no natural split."""
    if ":" in text:
        head, _, tail = text.partition(":")
        head, tail = head.strip(), tail.strip()
        if head and tail and len(head) <= 70:
            return head, tail
    return "Observation", text


class _AssessmentReportPDF(FPDF):
    """Running header/footer, page numbers, and CONFIDENTIAL marking
    applied automatically to every page after page 1 (which gets its own
    full masthead, built inline in candidate_summary_pdf) - fpdf2's
    header()/footer() hooks keep this consistent without repeating it at
    every page break."""

    def __init__(self, candidate_email: str):
        super().__init__(format="A4", orientation="P", unit="mm")
        self._candidate_email = candidate_email
        self.set_margins(16, 16, 16)
        self.set_auto_page_break(auto=True, margin=22)
        self.alias_nb_pages()

    def header(self) -> None:
        if self.page_no() == 1:
            return
        self.set_y(9)
        self.set_font(_REPORT_FONT, "B", 8)
        self.set_text_color(*_NAVY)
        self.cell(120, 5, "QA EVAL TOOL - CANDIDATE ASSESSMENT REPORT")
        self.set_font(_REPORT_FONT, "", 8)
        self.set_text_color(*_MUTED)
        self.cell(0, 5, _pdf_safe_text(self._candidate_email), align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*_BORDER)
        self.set_line_width(0.3)
        self.line(self.l_margin, 15, self.w - self.r_margin, 15)
        self.set_y(19)
        self.set_text_color(*_TEXT)

    def footer(self) -> None:
        self.set_y(-16)
        self.set_draw_color(*_BORDER)
        self.set_line_width(0.3)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.set_y(-13)
        self.set_font(_REPORT_FONT, "I", 7)
        self.set_text_color(*_MUTED)
        self.cell(120, 8, "CONFIDENTIAL - Internal hiring use only")
        self.cell(0, 8, f"Page {self.page_no()} of {{nb}}", align="R")


def _ensure_space(pdf: FPDF, needed_mm: float) -> None:
    """Manual page-break guard for content built from raw rect()/line()
    calls, which - unlike cell()/multi_cell() - fpdf2's auto-page-break
    never triggers on its own. Used before section headers and cards so
    a heading is never stranded alone at the bottom of a page."""
    if pdf.get_y() + needed_mm > pdf.h - pdf.b_margin:
        pdf.add_page()


def _section_title(pdf: FPDF, text: str, min_space: float = 24) -> None:
    _ensure_space(pdf, min_space)
    pdf.set_font(_REPORT_FONT, "B", 11)
    pdf.set_text_color(*_NAVY)
    pdf.cell(0, 8, _pdf_safe_text(text.upper()), new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*_NAVY)
    pdf.set_line_width(0.5)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.set_line_width(0.2)
    pdf.set_text_color(*_TEXT)
    pdf.ln(3)


def _estimate_round_card_height(feedback_text: str | None, did_well: list, missed: list, misses: list) -> float:
    """Rough content-height estimate (mm) for a round's detail card, used
    to decide whether to start it fresh on a new page rather than let it
    split mid-card. Deliberately generous (assumes ~2 wrapped lines per
    bullet) since under-estimating is what caused the original split;
    the _card()/_insight_card() page-mismatch guard is the backstop for
    whatever this still gets wrong. feedback_text here is only the lead
    sentence actually rendered (see candidate_summary_pdf), not the full
    paragraph - matching what has to fit on the page."""
    mm = 30.0
    mm += ((len(feedback_text) // 95) + 1) * 5.5 if feedback_text else 5.5
    if did_well:
        mm += 5 + len(did_well) * 11
    if missed:
        mm += 5 + len(missed) * 11
    if misses:
        mm += 5 + (min(len(misses), 5) + (1 if len(misses) > 5 else 0)) * 9
    return mm


def _reset_ink(pdf: FPDF) -> None:
    """fpdf2's Table() paints any cell without an explicit FontFace using
    whatever fill/text color was last set on the pdf object - not "no
    fill" as its API might suggest. Call this right before every
    pdf.table() block, or a leftover color from an earlier card/banner
    (verdict_fill, _NAVY, ...) silently tints or blacks out every
    unstyled body cell - confirmed by an isolated fpdf2 repro during
    this report's development."""
    pdf.set_fill_color(255, 255, 255)
    pdf.set_text_color(*_TEXT)


def _score_bar(pdf: FPDF, x: float, y: float, w: float, h: float, fraction: float, color: tuple) -> None:
    fraction = max(0.0, min(1.0, fraction))
    pdf.set_fill_color(*_BORDER)
    pdf.rect(x, y, w, h, style="F")
    if fraction > 0:
        pdf.set_fill_color(*color)
        pdf.rect(x, y, w * fraction, h, style="F")


@contextmanager
def _card(pdf: FPDF, fill_color: tuple | None = None, border_color: tuple | None = None, margin_bottom: float = 4):
    """Draws a card border/fill AROUND whatever gets written inside the
    `with` block, measured after the fact (top-of-block to bottom-of-
    block) rather than pre-computed - avoids having to predict variable-
    length text height up front. Callers still set fill=True on their
    own cell()/multi_cell() calls inside the block; this only paints the
    border once the final height is known."""
    if fill_color:
        pdf.set_fill_color(*fill_color)
    top_page, top_y = pdf.page_no(), pdf.get_y()
    yield
    bottom_page, bottom_y = pdf.page_no(), pdf.get_y()
    # If fpdf2's auto-page-break fired mid-block, top_y/bottom_y refer to
    # two different pages - a rect() drawn from them would land on the
    # wrong page with a nonsensical height. Skip the border rather than
    # draw a broken one; the content itself already paginated correctly.
    if border_color and bottom_page == top_page:
        pdf.set_draw_color(*border_color)
        pdf.set_line_width(0.6)
        pdf.rect(pdf.l_margin, top_y, pdf.w - pdf.l_margin - pdf.r_margin, bottom_y - top_y, style="D")
        pdf.set_line_width(0.2)
    pdf.ln(margin_bottom)


@contextmanager
def _insight_card(pdf: FPDF, accent_color: tuple):
    """Same idea as _card, but paints a colored left-accent stripe
    (matching the on-screen report's round-card styling) instead of a
    full border, sized to the block's actual height after the fact."""
    pdf.set_fill_color(*_CARD_BG)
    top_page, top_y = pdf.page_no(), pdf.get_y()
    yield
    bottom_page, bottom_y = pdf.page_no(), pdf.get_y()
    if bottom_page == top_page:
        pdf.set_fill_color(*accent_color)
        pdf.rect(pdf.l_margin, top_y, 1.5, bottom_y - top_y, style="F")
    pdf.ln(3)


@router.post("/candidates/{candidate_id}/summary/pdf")
def candidate_summary_pdf(
    candidate_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    hr: User = Depends(require_hr),
):
    """Renders whatever summary is currently saved for this candidate
    (see models.CandidateSummary, POST above) into a downloadable,
    enterprise-styled assessment report PDF - reads the saved copy
    rather than taking one in the request body, so the PDF is always
    exactly what GET/the candidate-detail view shows, and downloading it
    costs neither an LLM call nor requires the frontend to have the
    summary sitting in memory from earlier in the same page load. 404s
    if nothing has been generated yet - generate one first. Re-fetches
    the round table itself (for each round's status/score/raw misses/
    concept coverage, paired with its comment) rather than trusting a
    stored one, so a round rescored after the summary was generated
    still shows its current score here.

    This is a presentation layer only: every score, verdict, finding and
    coverage number below comes straight from the existing scoring data
    (Score.final_score/misses_json/concept_coverage_json,
    CandidateSummary.verdict/key_observations_json/round_comments_json) -
    nothing here recomputes, rewrites, or invents an evaluation. Fields
    the app genuinely has no data for (a distinct "confidence" score, a
    per-round "impact" rating) are rendered as "Not assessed" rather than
    guessed at, per this report's own content-integrity rule."""
    candidate = db.get(User, candidate_id)
    if candidate is None or candidate.role != Role.candidate:
        raise HTTPException(404, "Candidate not found")
    summary = db.query(CandidateSummary).filter(CandidateSummary.user_id == candidate.id).first()
    if summary is None:
        raise HTTPException(404, "No summary has been generated for this candidate yet.")

    rounds_by_number = {r["round_number"]: r for r in _gather_candidate_rounds(candidate, db, background_tasks)}
    app_settings = get_settings(db)
    round_comments = [CandidateRoundComment(**r) for r in summary.round_comments_json]

    def _round_label(n: int) -> str:
        return ROUND_LABELS.get(n, f"Round {n}")

    def _passing_score(n: int) -> int:
        return getattr(app_settings, f"round{n}_passing_score", 70)

    # ---- Figures reused across every section below - computed once here
    # from the real per-round data, never invented per-section. ----
    scored_rounds = [
        (rc, rounds_by_number.get(rc.round_number, {}))
        for rc in round_comments
        if rounds_by_number.get(rc.round_number, {}).get("final_score") is not None
    ]
    overall_score = sum(r["final_score"] for _, r in scored_rounds) if scored_rounds else None
    overall_max = len(round_comments) * 100
    verdict_label, verdict_color, verdict_fill = _verdict_sentiment(summary.verdict)
    severity_by_round = {
        rc.round_number: _severity_for_score(
            rounds_by_number.get(rc.round_number, {}).get("final_score"), _passing_score(rc.round_number)
        )
        for rc in round_comments
    }

    def _bullet_list(items: list[str], marker_color: tuple | None = None) -> None:
        pdf.set_font(_REPORT_FONT, "", 9.5)
        for item in items:
            if marker_color:
                pdf.set_text_color(*marker_color)
                pdf.cell(4, 5.5, "-")
                pdf.set_text_color(*_TEXT)
            else:
                pdf.cell(4, 5.5, "-")
            # multi_cell's default new_x is XPos.RIGHT, not LMARGIN -
            # without this the next call starts at the right margin with
            # ~0 width left and fpdf2 raises "Not enough horizontal space".
            pdf.set_x(pdf.l_margin + 4)
            pdf.multi_cell(pdf.w - pdf.r_margin - pdf.l_margin - 4, 5.5, _pdf_safe_text(item), new_x="LMARGIN", new_y="NEXT")

    pdf = _AssessmentReportPDF(candidate.email)
    pdf.add_page()

    # ============ 1. REPORT HEADER (page-1 masthead) ============
    pdf.set_font(_REPORT_FONT, "B", 9)
    pdf.set_text_color(*_MUTED)
    pdf.cell(130, 5, "QA EVAL TOOL")
    pdf.set_fill_color(*_NAVY)
    pdf.set_text_color(*_WHITE)
    pdf.set_font(_REPORT_FONT, "B", 8)
    pdf.cell(0, 6, "  CONFIDENTIAL  ", align="R", new_x="LMARGIN", new_y="NEXT", fill=True)
    pdf.set_font(_REPORT_FONT, "B", 19)
    pdf.set_text_color(*_NAVY)
    pdf.cell(0, 11, "Candidate Assessment Report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*_NAVY)
    pdf.set_line_width(0.8)
    pdf.line(pdf.l_margin, pdf.get_y() + 1, pdf.w - pdf.r_margin, pdf.get_y() + 1)
    pdf.set_line_width(0.2)
    pdf.ln(7)

    stat_y = pdf.get_y()
    stats = [
        (74, "CANDIDATE", candidate.email),
        (36, "ASSESSMENT DATE", summary.updated_at.strftime("%d %b %Y")),
        (30, "ROUNDS ASSESSED", str(len(round_comments))),
        (38, "OVERALL SCORE", f"{overall_score}/{overall_max}" if overall_score is not None else "Not assessed"),
    ]
    x = pdf.l_margin
    for w, label, value in stats:
        pdf.set_xy(x, stat_y)
        pdf.set_font(_REPORT_FONT, "", 7)
        pdf.set_text_color(*_MUTED)
        pdf.cell(w - 4, 4, label)
        pdf.set_xy(x, stat_y + 5)
        pdf.set_font(_REPORT_FONT, "B", 12)
        pdf.set_text_color(*_NAVY)
        pdf.multi_cell(w - 4, 6, _pdf_safe_text(value))
        x += w
    pdf.set_y(stat_y + 18)
    pdf.set_draw_color(*_BORDER)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(6)

    # ============ 2. EXECUTIVE RECOMMENDATION ============
    _section_title(pdf, "2. Executive Recommendation", min_space=50)
    lead = _first_sentence(summary.verdict)
    rest = summary.verdict[len(lead):].strip()
    with _card(pdf, fill_color=verdict_fill, border_color=verdict_color):
        pdf.set_font(_REPORT_FONT, "B", 13)
        pdf.set_text_color(*verdict_color)
        pdf.cell(0, 8, f"  {verdict_label}", new_x="LMARGIN", new_y="NEXT", fill=True)
        # The verdict headline is bold and prominent; the explanatory
        # text underneath stays normal weight and readable, per the
        # report spec - only the lead sentence is visually emphasized.
        pdf.set_font(_REPORT_FONT, "B", 10.5)
        pdf.set_text_color(*_TEXT)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(pdf.w - pdf.l_margin - pdf.r_margin - 4, 6, f"  {_pdf_safe_text(lead)}", new_x="LMARGIN", new_y="NEXT", fill=True)
        if rest:
            pdf.set_font(_REPORT_FONT, "", 9.5)
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(pdf.w - pdf.l_margin - pdf.r_margin - 4, 5.5, f"  {_pdf_safe_text(rest)}", new_x="LMARGIN", new_y="NEXT", fill=True)
        pdf.set_x(pdf.l_margin)
        pdf.set_font(_REPORT_FONT, "", 8)
        pdf.set_text_color(*_MUTED)
        pdf.cell(0, 6, "  Confidence level: Not assessed", new_x="LMARGIN", new_y="NEXT", fill=True)

    # Primary concerns deliberately not repeated here - Section 4 (Key
    # Observations) gives the same list in full, one screen later.

    # ============ 3. ASSESSMENT SCORECARD ============
    _section_title(pdf, "3. Assessment Scorecard")
    col_w = (16, 40, 18, 30, 74)
    _reset_ink(pdf)
    pdf.set_font(_REPORT_FONT, "", 9)
    with pdf.table(
        col_widths=col_w,
        text_align=("CENTER", "LEFT", "CENTER", "CENTER", "LEFT"),
        borders_layout="MINIMAL",
        line_height=5.5,
    ) as table:
        header = table.row()
        for text in ("Round", "Title", "Score", "Rating", "Summary"):
            header.cell(_pdf_safe_text(text), style=FontFace(emphasis="BOLD", fill_color=_NAVY, color=_WHITE, size_pt=9))
        for rc in round_comments:
            r = rounds_by_number.get(rc.round_number, {})
            score = r.get("final_score")
            severity_label, severity_color, _ = severity_by_round[rc.round_number]
            summary_text = r.get("feedback_text") or "Not assessed"
            if len(summary_text) > 160:
                summary_text = summary_text[:157].rstrip() + "..."
            row = table.row()
            row.cell(str(rc.round_number))
            row.cell(_pdf_safe_text(_round_label(rc.round_number)))
            row.cell(f"{score}/100" if score is not None else "-")
            row.cell(_pdf_safe_text(severity_label), style=FontFace(emphasis="BOLD", color=severity_color))
            row.cell(_pdf_safe_text(summary_text))
    if overall_score is not None:
        bar_y = pdf.get_y() + 2
        pdf.set_font(_REPORT_FONT, "B", 9.5)
        pdf.set_text_color(*_TEXT)
        pdf.cell(38, 6, "Overall score")
        _score_bar(pdf, pdf.get_x(), bar_y + 1, 100, 4, overall_score / overall_max, _NAVY)
        pdf.set_xy(pdf.get_x() + 104, bar_y)
        pdf.cell(0, 6, f"{overall_score} / {overall_max}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # ============ 4. KEY OBSERVATIONS ============
    if summary.key_observations_json:
        _section_title(pdf, "4. Key Observations")
        for obs in summary.key_observations_json:
            title, body = _split_observation(obs)
            _ensure_space(pdf, 16)
            with _insight_card(pdf, _TEAL):
                pdf.set_x(pdf.l_margin + 4)
                pdf.set_font(_REPORT_FONT, "B", 9.5)
                pdf.set_text_color(*_NAVY)
                pdf.cell(pdf.w - pdf.l_margin - pdf.r_margin - 4, 6, _pdf_safe_text(title), new_x="LMARGIN", new_y="NEXT", fill=True)
                pdf.set_x(pdf.l_margin + 4)
                pdf.set_font(_REPORT_FONT, "", 9.5)
                pdf.set_text_color(*_TEXT)
                pdf.multi_cell(pdf.w - pdf.l_margin - pdf.r_margin - 8, 5.5, _pdf_safe_text(body), new_x="LMARGIN", new_y="NEXT", fill=True)

    # ============ 5. ROUND-BY-ROUND ASSESSMENT ============
    # min_space accounts for the first round's own card, not just the
    # header - otherwise the header alone fits at the bottom of the
    # current page while the first card immediately jumps to the next
    # one, stranding the heading with a block of empty space beneath it.
    def _lead_or_none(text: str | None) -> str | None:
        return _first_sentence(text) if text else None

    first_round_height = 0.0
    if round_comments:
        first, first_r = round_comments[0], rounds_by_number.get(round_comments[0].round_number, {})
        first_round_height = _estimate_round_card_height(_lead_or_none(first_r.get("feedback_text")), first.did_well, first.missed, first_r.get("misses") or [])
    _section_title(pdf, "5. Round-by-Round Assessment", min_space=15 + first_round_height)
    for rc in round_comments:
        r = rounds_by_number.get(rc.round_number, {})
        score = r.get("final_score")
        severity_label, severity_color, severity_fill = severity_by_round[rc.round_number]
        _ensure_space(pdf, _estimate_round_card_height(_lead_or_none(r.get("feedback_text")), rc.did_well, rc.missed, r.get("misses") or []))
        with _card(pdf, border_color=_BORDER):
            # A fixed-height, non-wrapping header row (cell(), not
            # multi_cell()) - round titles are always short (see
            # ROUND_LABELS), so this never wraps, which keeps the score
            # chip reliably aligned beside it. The scenario title (which
            # CAN be long) gets its own line below instead of sharing
            # this row.
            row_y = pdf.get_y()
            pdf.set_font(_REPORT_FONT, "B", 12)
            pdf.set_text_color(*_NAVY)
            pdf.cell(124, 7, _pdf_safe_text(f"Round {rc.round_number} - {_round_label(rc.round_number)}"))
            pdf.set_xy(pdf.w - pdf.r_margin - 44, row_y)
            pdf.set_font(_REPORT_FONT, "B", 10)
            pdf.set_fill_color(*severity_fill)
            pdf.set_text_color(*severity_color)
            score_text = f"{score}/100 - {severity_label}" if score is not None else "Not scored yet"
            pdf.cell(44, 7, score_text, align="C", fill=True, new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(*_TEXT)
            if r.get("scenario_title"):
                pdf.set_font(_REPORT_FONT, "I", 8.5)
                pdf.set_text_color(*_MUTED)
                pdf.cell(0, 5, _pdf_safe_text(f"Scenario: {r['scenario_title']}"), new_x="LMARGIN", new_y="NEXT")
                pdf.set_text_color(*_TEXT)
            pdf.ln(1)

            # One lead sentence, not the full scoring paragraph - the
            # bullets right below already carry the specifics; this is
            # just enough narrative context to anchor them.
            pdf.set_font(_REPORT_FONT, "B", 9)
            pdf.set_text_color(*_MUTED)
            pdf.cell(0, 5, "ASSESSMENT SUMMARY", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font(_REPORT_FONT, "", 9.5)
            pdf.set_text_color(*_TEXT)
            feedback_text = r.get("feedback_text")
            pdf.multi_cell(0, 5.5, _pdf_safe_text(_first_sentence(feedback_text) if feedback_text else "Not assessed"), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)

            if rc.did_well:
                pdf.set_font(_REPORT_FONT, "B", 9)
                pdf.set_text_color(*_SUCCESS)
                pdf.cell(0, 5, "WHAT WENT WELL", new_x="LMARGIN", new_y="NEXT")
                pdf.set_text_color(*_TEXT)
                _bullet_list(rc.did_well, marker_color=_SUCCESS)
            if rc.missed:
                pdf.set_font(_REPORT_FONT, "B", 9)
                pdf.set_text_color(*_CRITICAL)
                pdf.cell(0, 5, "WHAT WAS MISSED", new_x="LMARGIN", new_y="NEXT")
                pdf.set_text_color(*_TEXT)
                _bullet_list(rc.missed, marker_color=_CRITICAL)

            misses = r.get("misses") or []
            if misses:
                pdf.set_font(_REPORT_FONT, "B", 8)
                pdf.set_text_color(*_MUTED)
                pdf.cell(0, 5, "EVIDENCE LOG (from automated scoring)", new_x="LMARGIN", new_y="NEXT")
                pdf.set_font(_REPORT_FONT, "I", 8.5)
                pdf.set_text_color(*_MUTED)
                for item in misses[:5]:
                    pdf.multi_cell(0, 4.8, _pdf_safe_text(f"  * {item}"), new_x="LMARGIN", new_y="NEXT")
                if len(misses) > 5:
                    pdf.multi_cell(0, 4.8, f"  ...and {len(misses) - 5} more", new_x="LMARGIN", new_y="NEXT")
                pdf.set_text_color(*_TEXT)

            pdf.set_font(_REPORT_FONT, "", 8.5)
            pdf.set_text_color(*_MUTED)
            pdf.cell(0, 5, f"Risk level: {severity_label}", new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(*_TEXT)

    # A standalone "Evidence-Based Findings" table (Ref/Category/
    # Severity/Finding, one row per Score.misses_json item) was tried
    # here and dropped - it just re-listed the same items already shown
    # in each round's "EVIDENCE LOG" above, tripling the page count for
    # a candidate with many misses without adding information.

    # ============ 6. COVERAGE / QUALITY VIEW ============
    coverage_rows = []
    for rc in round_comments:
        for entry in rounds_by_number.get(rc.round_number, {}).get("concept_coverage") or []:
            total, covered = entry.get("total"), entry.get("covered")
            pct = f"{round(covered / total * 100)}%" if total else "Not assessed"
            ratio = f"{covered}/{total}" if total is not None and covered is not None else "Not assessed"
            coverage_rows.append((rc.round_number, entry.get("category") or "-", ratio, pct, entry.get("notes") or ""))
    if coverage_rows:
        _section_title(pdf, "6. Coverage / Quality View")
        _reset_ink(pdf)
        pdf.set_font(_REPORT_FONT, "", 9)
        with pdf.table(
            col_widths=(16, 24, 22, 14, 102),
            text_align=("CENTER", "LEFT", "CENTER", "CENTER", "LEFT"),
            borders_layout="MINIMAL",
            line_height=5,
        ) as table:
            header = table.row()
            for text in ("Round", "Category", "Covered", "%", "Notes"):
                header.cell(_pdf_safe_text(text), style=FontFace(emphasis="BOLD", fill_color=_NAVY, color=_WHITE, size_pt=9))
            for round_number, category, ratio, pct, notes in coverage_rows:
                row = table.row()
                row.cell(str(round_number))
                row.cell(_pdf_safe_text(category))
                row.cell(ratio)
                row.cell(pct)
                row.cell(_pdf_safe_text(notes))
        pdf.ln(4)

    # ============ 7. FINAL ASSESSMENT ============
    # Deliberately just the decision, not a rehash - overall score is on
    # page 1, severity per round is in the Scorecard, and Key
    # Observations already covers the "why". This is the closing
    # statement for a reader who skipped straight to the end.
    _section_title(pdf, "7. Final Assessment", min_space=35)
    with _card(pdf, fill_color=_CARD_BG, border_color=_BORDER):
        pdf.set_x(pdf.l_margin + 4)
        pdf.set_font(_REPORT_FONT, "B", 14)
        pdf.set_text_color(*verdict_color)
        pdf.cell(0, 9, f"  {verdict_label}", new_x="LMARGIN", new_y="NEXT", fill=True)
        pdf.set_x(pdf.l_margin + 4)
        pdf.set_font(_REPORT_FONT, "", 9.5)
        pdf.set_text_color(*_TEXT)
        pdf.multi_cell(pdf.w - pdf.l_margin - pdf.r_margin - 8, 5.5, f"  {_pdf_safe_text(summary.verdict)}", new_x="LMARGIN", new_y="NEXT", fill=True)

    # ============ 8. INTERVIEWER QUICK VIEW ============
    _section_title(pdf, "8. Interviewer Quick View", min_space=70)
    strengths = [b for rc in round_comments for b in rc.did_well][:3]
    risks = [b for rc in round_comments for b in rc.missed][:3]
    critical_gaps = [
        f"Round {rc.round_number} ({_round_label(rc.round_number)})"
        for rc in round_comments if severity_by_round[rc.round_number][0] == "Critical Concern"
    ]
    next_action = {
        "ADVANCE": "Proceed to next round",
        "DO NOT ADVANCE": "Do not proceed",
        "CONDITIONAL ADVANCE": "Proceed with reservations - see verdict above",
        "MANUAL REVIEW": "Manual review required",
    }[verdict_label]

    with _card(pdf, fill_color=_CARD_BG, border_color=_BORDER):
        pdf.set_x(pdf.l_margin + 4)
        pdf.set_font(_REPORT_FONT, "", 9.5)
        pdf.set_text_color(*_TEXT)
        overall_text = f"{overall_score}/{overall_max}" if overall_score is not None else "Not assessed"
        for line_label, value, color in (
            ("Candidate", candidate.email, _TEXT),
            ("Overall score", overall_text, _NAVY),
            ("Recommendation", verdict_label, verdict_color),
            ("Confidence", "Not assessed", _MUTED),
            ("Recommended next action", next_action, _NAVY),
        ):
            pdf.set_x(pdf.l_margin + 4)
            pdf.set_font(_REPORT_FONT, "B", 9.5)
            pdf.set_text_color(*_TEXT)
            pdf.cell(50, 6, f"  {line_label}", fill=True)
            pdf.set_font(_REPORT_FONT, "B", 9.5)
            pdf.set_text_color(*color)
            pdf.cell(0, 6, _pdf_safe_text(str(value)), new_x="LMARGIN", new_y="NEXT", fill=True)

        for heading, items, color in (
            ("Strongest areas", strengths, _SUCCESS),
            ("Biggest risks", risks, _CRITICAL),
            ("Critical gaps", critical_gaps, _CRITICAL),
        ):
            pdf.set_x(pdf.l_margin + 4)
            pdf.set_font(_REPORT_FONT, "B", 9)
            pdf.set_text_color(*_MUTED)
            pdf.cell(0, 6, f"  {heading}", new_x="LMARGIN", new_y="NEXT", fill=True)
            if items:
                for item in items:
                    pdf.set_x(pdf.l_margin + 4)
                    pdf.set_font(_REPORT_FONT, "", 9)
                    pdf.set_text_color(*color)
                    pdf.multi_cell(pdf.w - pdf.l_margin - pdf.r_margin - 8, 5.5, f"  - {_pdf_safe_text(item)}", new_x="LMARGIN", new_y="NEXT", fill=True)
            else:
                pdf.set_x(pdf.l_margin + 4)
                pdf.set_font(_REPORT_FONT, "I", 9)
                pdf.set_text_color(*_MUTED)
                pdf.cell(0, 5.5, "  Not assessed", new_x="LMARGIN", new_y="NEXT", fill=True)
        pdf.set_text_color(*_TEXT)

    pdf_bytes = bytes(pdf.output())
    safe_email = candidate.email.replace("@", "_at_").replace(".", "_")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_email}-summary.pdf"'},
    )


_RESULT_STYLE = {
    # "Needs review" (display-only rename of the real "in_progress"
    # result - see _build_candidate_summary) reads better for a cohort
    # report handed to HR after the fact than "in progress" does; the
    # underlying determination is untouched.
    "selected": ("Selected", _SUCCESS),
    "not_selected": ("Not selected", _CRITICAL),
    "in_progress": ("Needs review", _MEDIUM),
}
_PERFORMANCE_BANDS = [("75-100%", 75, 101), ("50-74%", 50, 75), ("25-49%", 25, 50), ("0-24%", 0, 25)]


def _kpi_card(pdf: FPDF, x: float, y: float, w: float, h: float, label: str, value: str, color: tuple = _NAVY) -> None:
    pdf.set_draw_color(*_BORDER)
    pdf.set_line_width(0.3)
    pdf.rect(x, y, w, h, style="D")
    pdf.set_xy(x + 3, y + 2.5)
    pdf.set_font(_REPORT_FONT, "", 6.5)
    pdf.set_text_color(*_MUTED)
    pdf.multi_cell(w - 6, 3.2, _pdf_safe_text(label))
    pdf.set_xy(x + 3, y + h - 9)
    pdf.set_font(_REPORT_FONT, "B", 12.5)
    pdf.set_text_color(*color)
    pdf.cell(w - 6, 7, _pdf_safe_text(value))
    pdf.set_text_color(*_TEXT)


def _gather_cohort_row(candidate: User, db: Session, background_tasks: BackgroundTasks) -> dict:
    """Everything the cohort report needs for one candidate, built
    entirely from existing per-candidate helpers - no new scoring/
    aggregation logic beyond what list_candidates/candidate_summary_pdf
    already compute."""
    info = _build_candidate_summary(candidate, db, background_tasks)
    raw_rounds = {r["round_number"]: r for r in _gather_candidate_rounds(candidate, db, background_tasks)}
    cs = db.query(CandidateSummary).filter(CandidateSummary.user_id == candidate.id).first()
    return {"candidate": candidate, "info": info, "raw_rounds": raw_rounds, "cs": cs}


@router.get("/reports/daily-summary")
def daily_summary_pdf(
    exam_date: date,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    hr: User = Depends(require_hr),
):
    """One export per screening day: every candidate whose CURRENT
    appearance's exam_date is this date - an enterprise HR/QA cohort
    report (executive KPIs, a candidate comparison table, round-wise
    metrics, a per-candidate decision summary, and cohort-wide insights)
    built entirely from data this app already computes elsewhere:
    _build_candidate_summary (aggregate_score/result - same logic the
    Candidates dashboard uses), _gather_candidate_rounds (per-round
    score/misses/concept_coverage), and CandidateSummary (an existing AI
    summary's did_well/missed, if one was generated - never generates a
    new one). Nothing here recomputes scores or selection criteria."""
    day_start = datetime.combine(exam_date, datetime.min.time())
    day_end = datetime.combine(exam_date, datetime.max.time())
    candidates = (
        db.query(User)
        .join(CandidateAppearance, CandidateAppearance.user_id == User.id)
        .filter(
            User.role == Role.candidate,
            CandidateAppearance.is_current == True,
            CandidateAppearance.exam_date >= day_start,
            CandidateAppearance.exam_date <= day_end,
        )
        .order_by(User.email)
        .all()
    )
    if not candidates:
        raise HTTPException(404, f"No candidates found with exam date {exam_date.isoformat()}.")

    app_settings = get_settings(db)
    rows = [_gather_cohort_row(c, db, background_tasks) for c in candidates]
    total = len(rows)

    # ---- Cohort-level aggregates (all derived from the per-candidate
    # data above - no separate computation of scores/selection). ----
    started = sum(1 for r in rows if any(rr.status != "not_started" for rr in r["info"].rounds))
    completed = sum(1 for r in rows if all(rr.status == "scored" for rr in r["info"].rounds))
    selected = sum(1 for r in rows if r["info"].result == "selected")
    not_selected = sum(1 for r in rows if r["info"].result == "not_selected")
    needs_review = total - selected - not_selected
    agg_scores = [r["info"].aggregate_score for r in rows if r["info"].aggregate_score is not None]
    avg_score = sum(agg_scores) / len(agg_scores) if agg_scores else None
    highest, lowest = (max(agg_scores), min(agg_scores)) if agg_scores else (None, None)
    pass_rate = (selected / total * 100) if total else 0.0

    # Round-wise metrics + per-round weaknesses/capability coverage -
    # same misses_json Counter / concept_coverage_json averaging
    # scenario_history() already uses, just scoped to this cohort's
    # rounds instead of a scenario's all-time submissions.
    round_scores: dict[int, list[int]] = {n: [] for n in (1, 2, 3, 4)}
    round_misses: dict[int, Counter] = {n: Counter() for n in (1, 2, 3, 4)}
    round_coverage: dict[int, dict[str, list[float]]] = {n: {} for n in (1, 2, 3, 4)}
    for r in rows:
        for n, raw in r["raw_rounds"].items():
            if raw.get("final_score") is not None:
                round_scores[n].append(raw["final_score"])
            for miss in raw.get("misses") or []:
                round_misses[n][miss] += 1
            for c in raw.get("concept_coverage") or []:
                c_total = c.get("total") or 0
                if c_total > 0:
                    round_coverage[n].setdefault(c.get("category", "Unknown"), []).append(c.get("covered", 0) / c_total * 100)

    round_metrics = {}
    for n in (1, 2, 3, 4):
        scores = round_scores[n]
        passing = getattr(app_settings, f"round{n}_passing_score", 70)
        pass_count = sum(1 for s in scores if s >= passing)
        round_metrics[n] = {
            "assessed": len(scores),
            "avg": sum(scores) / len(scores) if scores else None,
            "high": max(scores) if scores else None,
            "low": min(scores) if scores else None,
            "pass_count": pass_count,
            "pass_pct": (pass_count / len(scores) * 100) if scores else None,
            "top_weaknesses": round_misses[n].most_common(3),
            "coverage": {cat: sum(v) / len(v) for cat, v in round_coverage[n].items()},
        }

    # Drop-off: candidates who attempted round n but never even started
    # round n+1 - a real attrition signal from existing per-round status,
    # not a new metric layered on top of the scoring data.
    dropoff = {}
    for n in (1, 2, 3):
        dropoff[n] = sum(
            1 for r in rows
            for by_num in [{rr.round_number: rr for rr in r["info"].rounds}]
            if by_num[n].status != "not_started" and by_num[n + 1].status == "not_started"
        )
    worst_dropoff = max(dropoff, key=dropoff.get) if any(dropoff.values()) else None

    band_counts = {label: 0 for label, _, _ in _PERFORMANCE_BANDS}
    for r in rows:
        if r["info"].aggregate_score is None:
            continue
        pct = r["info"].aggregate_score / 400 * 100
        for label, lo, hi in _PERFORMANCE_BANDS:
            if lo <= pct < hi or (hi == 101 and pct == 100):
                band_counts[label] += 1
                break

    cohort_misses = Counter()
    for n in (1, 2, 3, 4):
        cohort_misses.update(round_misses[n])
    top_cohort_weaknesses = cohort_misses.most_common(5)
    r1_coverage = round_metrics[1]["coverage"]
    strongest_capability = max(r1_coverage, key=r1_coverage.get) if r1_coverage else None
    weakest_capability = min(r1_coverage, key=r1_coverage.get) if r1_coverage else None

    pdf = _AssessmentReportPDF(f"Cohort - {exam_date.isoformat()}")
    pdf.add_page()
    pdf.set_font(_REPORT_FONT, "B", 9)
    pdf.set_text_color(*_MUTED)
    pdf.cell(130, 5, "QA EVAL TOOL")
    pdf.set_fill_color(*_NAVY)
    pdf.set_text_color(*_WHITE)
    pdf.set_font(_REPORT_FONT, "B", 8)
    pdf.cell(0, 6, "  CONFIDENTIAL  ", align="R", new_x="LMARGIN", new_y="NEXT", fill=True)
    pdf.set_font(_REPORT_FONT, "B", 19)
    pdf.set_text_color(*_NAVY)
    pdf.cell(0, 11, "Daily Cohort Summary", new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*_NAVY)
    pdf.set_line_width(0.8)
    pdf.line(pdf.l_margin, pdf.get_y() + 1, pdf.w - pdf.r_margin, pdf.get_y() + 1)
    pdf.set_line_width(0.2)
    pdf.ln(5)

    # ============ 1. EXECUTIVE SUMMARY ============
    _section_title(pdf, "1. Executive Summary", min_space=90)
    kpis = [
        ("ASSESSMENT DATE", exam_date.strftime("%d %b %Y"), _NAVY),
        ("TOTAL CANDIDATES", str(total), _NAVY),
        ("STARTED", str(started), _NAVY),
        ("COMPLETED", str(completed), _NAVY),
        ("SELECTED", str(selected), _SUCCESS),
        ("NOT SELECTED", str(not_selected), _CRITICAL),
        ("NEEDS REVIEW", str(needs_review), _MEDIUM),
        ("AVERAGE SCORE", f"{avg_score:.0f}/400 ({avg_score/4:.0f}%)" if avg_score is not None else "Not assessed", _NAVY),
        ("HIGHEST SCORE", f"{highest}/400" if highest is not None else "Not assessed", _SUCCESS),
        ("LOWEST SCORE", f"{lowest}/400" if lowest is not None else "Not assessed", _CRITICAL),
        ("OVERALL PASS RATE", f"{pass_rate:.0f}%", _NAVY),
        ("PASSING THRESHOLD", f"{app_settings.final_passing_score}/400", _MUTED),
    ]
    card_w, card_h, gap = 43.0, 20.0, 2.0
    grid_top_y = pdf.get_y()  # fixed grid origin - _kpi_card() moves the
    # cursor internally (cell/multi_cell calls), so reading pdf.get_y()
    # fresh on each iteration would drift the grid diagonally down the
    # page instead of laying out a clean 4-column grid.
    for i, (label, value, color) in enumerate(kpis):
        col, row_i = i % 4, i // 4
        x = pdf.l_margin + col * (card_w + gap)
        y = grid_top_y + row_i * (card_h + gap)
        _kpi_card(pdf, x, y, card_w, card_h, label, value, color)
    pdf.set_y(grid_top_y + 3 * (card_h + gap) + 2)

    interpretation = (
        f"{selected} of {total} candidates ({pass_rate:.0f}%) met the {app_settings.final_passing_score}/400 "
        f"passing threshold. {completed} of {total} completed all four rounds"
        + (f"; {needs_review} still need review." if needs_review else ".")
    )
    if worst_dropoff:
        interpretation += f" Round {worst_dropoff} to {worst_dropoff + 1} shows the largest drop-off ({dropoff[worst_dropoff]} candidate(s) stalled)."
    with _card(pdf, fill_color=_CARD_BG, border_color=_BORDER):
        pdf.set_x(pdf.l_margin + 4)
        pdf.set_font(_REPORT_FONT, "", 9.5)
        pdf.multi_cell(pdf.w - pdf.l_margin - pdf.r_margin - 8, 5.5, f"  {_pdf_safe_text(interpretation)}", new_x="LMARGIN", new_y="NEXT", fill=True)

    # ============ 2. CANDIDATE COMPARISON ============
    _section_title(pdf, "2. Candidate Comparison")
    ranked = sorted(rows, key=lambda r: (r["info"].aggregate_score is None, -(r["info"].aggregate_score or 0), r["candidate"].email))
    _reset_ink(pdf)
    pdf.set_font(_REPORT_FONT, "", 9)
    with pdf.table(
        col_widths=(10, 48, 14, 14, 14, 14, 20, 18, 26),
        text_align=("CENTER", "LEFT", "CENTER", "CENTER", "CENTER", "CENTER", "CENTER", "CENTER", "CENTER"),
        borders_layout="MINIMAL",
        line_height=5.5,
    ) as table:
        header = table.row()
        for text in ("Rank", "Candidate", "R1", "R2", "R3", "R4", "Total", "%", "Decision"):
            header.cell(_pdf_safe_text(text), style=FontFace(emphasis="BOLD", fill_color=_NAVY, color=_WHITE, size_pt=9))
        for rank, r in enumerate(ranked, start=1):
            by_num = {rr.round_number: rr for rr in r["info"].rounds}
            agg = r["info"].aggregate_score
            label, color = _RESULT_STYLE[r["info"].result]
            row = table.row()
            row.cell(str(rank))
            row.cell(_pdf_safe_text(r["candidate"].email))
            for n in (1, 2, 3, 4):
                rr = by_num[n]
                row.cell(str(rr.final_score) if rr.status == "scored" and rr.final_score is not None else "-")
            row.cell(str(agg) if agg is not None else "-")
            row.cell(f"{agg/400*100:.0f}%" if agg is not None else "-")
            row.cell(label, style=FontFace(emphasis="BOLD", color=color))
    pdf.ln(4)

    # ============ 3. ROUND-WISE METRICS ============
    _section_title(pdf, "3. Round-wise Metrics")
    _reset_ink(pdf)
    pdf.set_font(_REPORT_FONT, "", 9)
    with pdf.table(
        col_widths=(16, 26, 24, 22, 22, 28, 40),
        text_align=("CENTER", "LEFT", "CENTER", "CENTER", "CENTER", "CENTER", "LEFT"),
        borders_layout="MINIMAL",
        line_height=5.5,
    ) as table:
        header = table.row()
        for text in ("Round", "Title", "Avg", "High", "Low", "Pass rate", "Assessed / Pass count"):
            header.cell(_pdf_safe_text(text), style=FontFace(emphasis="BOLD", fill_color=_NAVY, color=_WHITE, size_pt=9))
        for n in (1, 2, 3, 4):
            m = round_metrics[n]
            row = table.row()
            row.cell(str(n))
            row.cell(_pdf_safe_text(ROUND_LABELS.get(n, f"Round {n}")))
            row.cell(f"{m['avg']:.0f}" if m["avg"] is not None else "-")
            row.cell(str(m["high"]) if m["high"] is not None else "-")
            row.cell(str(m["low"]) if m["low"] is not None else "-")
            row.cell(f"{m['pass_pct']:.0f}%" if m["pass_pct"] is not None else "-")
            row.cell(f"{m['assessed']} assessed / {m['pass_count']} passed")
    pdf.ln(2)

    pdf.set_font(_REPORT_FONT, "", 8.5)
    for n in (1, 2, 3, 4):
        m = round_metrics[n]
        line_parts = []
        if m["coverage"]:
            cov = ", ".join(f"{cat} {pct:.0f}%" for cat, pct in m["coverage"].items())
            line_parts.append(f"capability coverage: {cov}")
        if m["top_weaknesses"]:
            weak = ", ".join(f"{text} (x{count})" for text, count in m["top_weaknesses"])
            line_parts.append(f"top gaps: {weak}")
        if line_parts:
            pdf.set_text_color(*_MUTED)
            pdf.multi_cell(0, 4.8, _pdf_safe_text(f"R{n} - " + "; ".join(line_parts)), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_TEXT)
    pdf.ln(2)

    # ============ 4. CANDIDATE DECISION SUMMARY ============
    _section_title(pdf, "4. Candidate Decision Summary")
    for r in ranked:
        candidate, info, raw_rounds, cs = r["candidate"], r["info"], r["raw_rounds"], r["cs"]
        label, color = _RESULT_STYLE[info.result]
        if cs:
            did_well = [b for rc in cs.round_comments_json for b in rc.get("did_well", [])][:3]
            missed = [b for rc in cs.round_comments_json for b in rc.get("missed", [])][:5]
        else:
            did_well = []
            missed = [m for n in (1, 2, 3, 4) for m in (raw_rounds.get(n, {}).get("misses") or [])][:5]
        cleared = sum(
            1 for rr in info.rounds
            if rr.status == "scored" and rr.final_score is not None
            and rr.final_score >= getattr(app_settings, f"round{rr.round_number}_passing_score", 70)
        )
        scored_n = sum(1 for rr in info.rounds if rr.status == "scored")
        agg = info.aggregate_score
        rationale = (
            f"Cleared {cleared} of {scored_n} scored rounds; aggregate {agg}/400 vs the {app_settings.final_passing_score} threshold."
            if agg is not None else "Assessment incomplete - no rounds scored yet."
        )

        _ensure_space(pdf, 20 + 5.5 * (len(did_well) + len(missed)) + 12)
        with _card(pdf, border_color=_BORDER):
            row_y = pdf.get_y()
            pdf.set_font(_REPORT_FONT, "B", 11)
            pdf.set_text_color(*_NAVY)
            pdf.cell(90, 7, _pdf_safe_text(candidate.email))
            pdf.set_font(_REPORT_FONT, "", 9.5)
            pdf.set_text_color(*_MUTED)
            pdf.cell(46, 7, f"{agg}/400 ({agg/400*100:.0f}%)" if agg is not None else "Not assessed")
            pdf.set_xy(pdf.w - pdf.r_margin - 38, row_y)
            pdf.set_font(_REPORT_FONT, "B", 9.5)
            pdf.set_fill_color(*_CARD_BG)
            pdf.set_text_color(*color)
            pdf.cell(38, 7, label, align="C", new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(*_TEXT)

            round_line = "  ".join(
                f"R{rr.round_number}: {rr.final_score if rr.status == 'scored' and rr.final_score is not None else '-'}"
                for rr in info.rounds
            )
            pdf.set_font(_REPORT_FONT, "", 8.5)
            pdf.set_text_color(*_MUTED)
            pdf.cell(0, 5, round_line, new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(*_TEXT)
            pdf.ln(1)

            if did_well:
                pdf.set_font(_REPORT_FONT, "B", 8.5)
                pdf.set_text_color(*_SUCCESS)
                pdf.cell(0, 5, "STRENGTHS", new_x="LMARGIN", new_y="NEXT")
                pdf.set_text_color(*_TEXT)
                pdf.set_font(_REPORT_FONT, "", 8.5)
                for b in did_well:
                    pdf.multi_cell(0, 4.8, f"  - {_pdf_safe_text(b)}", new_x="LMARGIN", new_y="NEXT")
            if missed:
                pdf.set_font(_REPORT_FONT, "B", 8.5)
                pdf.set_text_color(*_CRITICAL)
                pdf.cell(0, 5, "KEY WEAKNESSES", new_x="LMARGIN", new_y="NEXT")
                pdf.set_text_color(*_TEXT)
                pdf.set_font(_REPORT_FONT, "", 8.5)
                for b in missed:
                    pdf.multi_cell(0, 4.8, f"  - {_pdf_safe_text(b)}", new_x="LMARGIN", new_y="NEXT")

            pdf.set_font(_REPORT_FONT, "I", 8.5)
            pdf.set_text_color(*_MUTED)
            pdf.multi_cell(0, 4.8, _pdf_safe_text(f"Rationale: {rationale}"), new_x="LMARGIN", new_y="NEXT")
            if info.result != "selected" and missed:
                pdf.multi_cell(0, 4.8, _pdf_safe_text(f"Interview focus: {missed[0]}"), new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(*_TEXT)

    # ============ 5. COHORT INSIGHTS ============
    _section_title(pdf, "5. Cohort Insights", min_space=80)
    if top_cohort_weaknesses:
        pdf.set_font(_REPORT_FONT, "B", 9.5)
        pdf.cell(0, 6, "Top common weaknesses", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font(_REPORT_FONT, "", 9)
        for text, count in top_cohort_weaknesses:
            pdf.multi_cell(0, 5, f"  - {_pdf_safe_text(text)} (x{count})", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

    if strongest_capability and weakest_capability:
        pdf.set_font(_REPORT_FONT, "B", 9.5)
        pdf.cell(0, 6, "Capabilities (Round 1 coverage)", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font(_REPORT_FONT, "", 9)
        if r1_coverage[strongest_capability] == r1_coverage[weakest_capability]:
            # Every category tied (commonly all 0%, as with a cohort that
            # skipped Round 1 almost entirely) - a false "strongest vs
            # weakest" split would misrepresent an actual tie as a
            # meaningful difference.
            pdf.set_text_color(*_MUTED)
            cats = ", ".join(r1_coverage.keys())
            pdf.cell(0, 5, f"  All categories tied at {r1_coverage[strongest_capability]:.0f}% avg coverage ({_pdf_safe_text(cats)})", new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.set_text_color(*_SUCCESS)
            pdf.cell(0, 5, f"  Strongest: {_pdf_safe_text(strongest_capability)} ({r1_coverage[strongest_capability]:.0f}% avg coverage)", new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(*_CRITICAL)
            pdf.cell(0, 5, f"  Weakest: {_pdf_safe_text(weakest_capability)} ({r1_coverage[weakest_capability]:.0f}% avg coverage)", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*_TEXT)
        pdf.ln(1)

    pdf.set_font(_REPORT_FONT, "B", 9.5)
    pdf.cell(0, 6, "Round drop-off", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(_REPORT_FONT, "", 9)
    if worst_dropoff:
        pdf.cell(0, 5, f"  Round {worst_dropoff} to {worst_dropoff + 1}: {dropoff[worst_dropoff]} candidate(s) never started the next round after this one.", new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.cell(0, 5, "  No drop-off observed - every candidate who started a round progressed to the next.", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    pdf.set_font(_REPORT_FONT, "B", 9.5)
    pdf.cell(0, 6, "Performance band distribution", new_x="LMARGIN", new_y="NEXT")
    bar_x, bar_w = pdf.l_margin + 26, 100.0
    max_band = max(band_counts.values()) or 1
    for label, _, _ in _PERFORMANCE_BANDS:
        count = band_counts[label]
        y = pdf.get_y()
        pdf.set_font(_REPORT_FONT, "", 8.5)
        pdf.set_text_color(*_MUTED)
        pdf.cell(24, 5, label)
        _score_bar(pdf, bar_x, y + 0.5, bar_w, 4, count / max_band, _BLUE)
        pdf.set_xy(bar_x + bar_w + 3, y)
        pdf.set_text_color(*_TEXT)
        pdf.cell(0, 5, str(count), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    with _card(pdf, fill_color=_CARD_BG, border_color=_BORDER):
        pdf.set_x(pdf.l_margin + 4)
        pdf.set_font(_REPORT_FONT, "B", 9.5)
        pdf.set_text_color(*_NAVY)
        pdf.cell(0, 6, "  Overall hiring recommendation", new_x="LMARGIN", new_y="NEXT", fill=True)
        pdf.set_x(pdf.l_margin + 4)
        pdf.set_font(_REPORT_FONT, "", 9.5)
        pdf.set_text_color(*_TEXT)
        recommendation = f"Advance {selected} of {total} candidates ({pass_rate:.0f}%) who met the passing threshold."
        if needs_review:
            recommendation += f" {needs_review} candidate(s) need manual review before a final call."
        pdf.multi_cell(pdf.w - pdf.l_margin - pdf.r_margin - 8, 5.5, f"  {_pdf_safe_text(recommendation)}", new_x="LMARGIN", new_y="NEXT", fill=True)

    pdf_bytes = bytes(pdf.output())
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="cohort-{exam_date.isoformat()}.pdf"'},
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
        # for round 2/4 scenarios, which never populate that column.
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
