"""
HR-only endpoints: author scenarios (draft -> review generated reference ->
publish), and view the candidate results dashboard.

Only round 1 has a working reference-generation/scoring pipeline so far
(see llm_service.py) - round 2 scenarios can be authored and published here
too (the lifecycle is round-agnostic), but candidate-side submission/scoring
for round 2 is still a TODO(round2) stub in candidate.py.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User, Scenario, ScenarioStatus, Submission, ExperienceBand, Role
from ..schemas import ScenarioCreate, ScenarioUpdate, ScenarioOut, SubmissionReportOut, CandidateSummaryOut, CandidateRoundSummary
from ..dependencies import require_hr
from ..services import llm_service

router = APIRouter(prefix="/hr", tags=["hr"])

VALID_BANDS = (ExperienceBand.junior.value, ExperienceBand.senior.value)


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
    (unlike candidate-facing scoring, which runs in the background)."""
    if scenario.round_number != 1:
        # TODO(round2): plug in llm_service.generate_round2_reference once
        # that exists. Leaving reference_json empty means HR can still
        # create/save a draft Round 2 scenario now; publishing it is
        # blocked below until a reference exists.
        return
    scenario.reference_json = llm_service.generate_round1_reference(
        scenario_description=scenario.description,
        experience_band=scenario.experience_band.value,
    )
    db.commit()
    db.refresh(scenario)


@router.post("/scenarios/{scenario_id}/regenerate-reference", response_model=ScenarioOut)
def regenerate_reference(scenario_id: int, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
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


@router.post("/scenarios/{scenario_id}/publish", response_model=ScenarioOut)
def publish_scenario(scenario_id: int, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    scenario = _get_draft_scenario_or_404(scenario_id, db)
    if not scenario.reference_json:
        raise HTTPException(400, "Can't publish a scenario with no reference answer yet - generate or write one first.")

    # Enforce "one live scenario per round+band": archive whatever was
    # published before for this exact (round, band) combination.
    db.query(Scenario).filter(
        Scenario.round_number == scenario.round_number,
        Scenario.experience_band == scenario.experience_band,
        Scenario.status == ScenarioStatus.published,
    ).update({"status": ScenarioStatus.archived})

    scenario.status = ScenarioStatus.published
    scenario.published_at = datetime.utcnow()
    db.commit()
    db.refresh(scenario)
    return scenario


def _get_draft_scenario_or_404(scenario_id: int, db: Session) -> Scenario:
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(404, "Scenario not found")
    if scenario.status != ScenarioStatus.draft:
        raise HTTPException(400, f"Scenario is {scenario.status.value}, not draft - can't edit/publish it again.")
    return scenario


@router.get("/scenarios", response_model=list[ScenarioOut])
def list_scenarios(db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Just the current scenario per (round, band) - not full history.
    The published scenario (what candidates are actually being scored
    against) always wins if one exists, even if there's a newer draft
    being prepared to replace it - losing sight of what's live would be
    worse than the clutter this is meant to fix. Falls back to the most
    recent draft only when nothing's published yet for that slot. Older
    drafts/archived scenarios still exist in the DB (submissions
    reference them by id), they're just not surfaced here."""
    all_scenarios = db.query(Scenario).order_by(Scenario.created_at.desc()).all()
    current_per_slot = {}
    for scenario in all_scenarios:
        key = (scenario.round_number, scenario.experience_band)
        current = current_per_slot.get(key)
        is_better = current is None or (
            scenario.status == ScenarioStatus.published and current.status != ScenarioStatus.published
        )
        if is_better:
            current_per_slot[key] = scenario
    return sorted(current_per_slot.values(), key=lambda s: (s.round_number, s.experience_band.value))


@router.delete("/scenarios/{scenario_id}", status_code=204)
def delete_scenario(scenario_id: int, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Discard a draft you don't want (e.g. a bad description, a failed
    generation). Only drafts - a published/archived scenario can have
    candidate submissions pointing at it and must never be deleted."""
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(404, "Scenario not found")
    if scenario.status != ScenarioStatus.draft:
        raise HTTPException(400, f"Scenario is {scenario.status.value}, not draft - can't delete it.")
    db.delete(scenario)
    db.commit()


# ---- Candidate results dashboard ----

@router.get("/candidates", response_model=list[CandidateSummaryOut])
def list_candidates(db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    candidates = db.query(User).filter(User.role == Role.candidate).order_by(User.email).all()
    out = []
    for candidate in candidates:
        submissions_by_round = {s.round_number: s for s in candidate.submissions}
        rounds = []
        for round_number in (1, 2, 3):
            submission = submissions_by_round.get(round_number)
            if submission is None:
                status = "not_started"
                final_score = None
            else:
                status = submission.status.value
                final_score = submission.score.final_score if submission.score else None
            rounds.append(CandidateRoundSummary(round_number=round_number, status=status, final_score=final_score))
        out.append(CandidateSummaryOut(
            id=candidate.id,
            email=candidate.email,
            experience_band=candidate.experience_band.value if candidate.experience_band else None,
            rounds=rounds,
        ))
    return out


@router.get("/candidates/{candidate_id}/report", response_model=list[SubmissionReportOut])
def candidate_report(candidate_id: int, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """All submissions (with scores where available) for one candidate, across all rounds."""
    candidate = db.get(User, candidate_id)
    if candidate is None or candidate.role != Role.candidate:
        raise HTTPException(404, "Candidate not found")
    return (
        db.query(Submission)
        .filter(Submission.user_id == candidate_id)
        .order_by(Submission.round_number)
        .all()
    )
