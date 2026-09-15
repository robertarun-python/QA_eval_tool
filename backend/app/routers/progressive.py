"""
ROUND 5 -- PROGRESSIVE ENGINEERING (POC). Phase 6: the minimum HTTP
surface needed to prove Phases 1-5 over a real request/response cycle.
No production UI - see the round's architecture-discovery notes for the
full plan; this is deliberately the thinnest possible layer on top of
the already-built, already-tested services (models_progressive.py,
progressive_service.py, progressive_llm.py, progressive_policy.py,
progressive_pipeline.py) - no new business logic lives in this file
beyond request/response shaping and ownership checks.

ISOLATION: a brand-new router, registered in main.py alongside (not
instead of) auth/hr/candidate - no existing route in hr.py/candidate.py
is touched, and Round 5 is NOT added to candidate.py's ROUND_SEQUENCE
gate (this round's own stage progression, inside progressive_service.py,
is a separate, already-built concern from that outer round-to-round
gate). Reuses require_hr/require_candidate from dependencies.py exactly
as they already exist - no auth refactor.

CANDIDATE-FACING SECURITY: every candidate response model below
(Candidate*Out) is a narrow, explicit allowlist of fields - never the
ORM row, never anything from hidden_tests_json/reference_solution, never
a future stage's requirement_text. FastAPI's response_model does its own
filtering on top of that (a defense-in-depth backstop, same reasoning as
every other layer this round has added), but the real guarantee is that
these response models simply have no field to put that content in - see
test_progressive_routes.py's isolation tests for the actual proof.

HR endpoints are unrestricted by design (same as every other round's HR
view) - HR is trusted with hidden tests and reference solutions, that's
the whole point of authoring them.
"""
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_hr, require_candidate
from ..models import User
from ..models_progressive import ProgressiveProblem, ProgressiveRequirement, ProgressiveAttempt, ProgressiveProblemStatus
from ..services import progressive_service, progressive_pipeline
from ..services.progressive_service import (
    ProgressiveServiceError, AttemptNotActiveError, StageOutOfOrderError,
    StageAlreadyCompletedError, UnknownStageError,
)

candidate_router = APIRouter(prefix="/candidate/progressive", tags=["progressive-candidate"])
hr_router = APIRouter(prefix="/hr/progressive", tags=["progressive-hr"])


def _map_service_error(e: ProgressiveServiceError) -> HTTPException:
    if isinstance(e, (AttemptNotActiveError, StageOutOfOrderError, StageAlreadyCompletedError, UnknownStageError)):
        return HTTPException(400, str(e))
    return HTTPException(400, str(e))


# ==================== Candidate-facing schemas (narrow allowlists) ====================

class CandidateProblemOut(BaseModel):
    id: int
    title: str
    description: str
    input_spec_json: dict
    language: str
    starter_code: Optional[str]
    difficulty: Optional[str]
    time_limit_minutes: int

    class Config:
        from_attributes = True


class CandidateAttemptOut(BaseModel):
    id: int
    problem_id: int
    current_stage: int
    status: str


class CandidateStageOut(BaseModel):
    """The ONE requirement a candidate is entitled to see right now -
    no field here could ever carry a later stage's text, a hidden test,
    or a reference solution (those simply aren't on this model)."""
    stage_order: int
    requirement_text: str
    expected_behavior: Optional[str]


class CandidateStateOut(BaseModel):
    current_stage: int
    current_code: Optional[str]
    status: str
    completed: bool


class CandidateTurnRequest(BaseModel):
    candidate_request: str = Field(min_length=1, max_length=10_000)


class CandidateTurnOut(BaseModel):
    """Mirrors progressive_pipeline.PipelineResult exactly - the only
    thing a candidate is ever allowed to see about a turn. No detector
    findings, no Judge explanation, no policy reason codes."""
    response_message: str
    code_after: Optional[str]
    accepted: bool


class CandidateStageSubmitRequest(BaseModel):
    code_snapshot: str = Field(min_length=1)


class CandidateStageResultOut(BaseModel):
    """Deliberately an AGGREGATE, not the raw test_results_json - the
    literal input/expected_output values in that JSON originated from
    hidden_tests_json, so exposing them verbatim (even from a "results"
    table, after the fact) would be exactly the hidden-test leak this
    round exists to prevent. Only counts and pass/fail reach the
    candidate, never a single literal test value."""
    stage_order: int
    passed: bool
    test_count: int
    passed_count: int


def _to_candidate_stage_result(result) -> CandidateStageResultOut:
    test_results = result.test_results_json or []
    return CandidateStageResultOut(
        stage_order=result.stage_order,
        passed=bool(result.passed),
        test_count=len(test_results),
        passed_count=sum(1 for t in test_results if t.get("passed")),
    )


# ==================== Candidate routes ====================

@candidate_router.get("/problems", response_model=list[CandidateProblemOut])
def list_problems(db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    problems = db.query(ProgressiveProblem).filter(ProgressiveProblem.status == ProgressiveProblemStatus.published).all()
    return problems


@candidate_router.get("/problems/{problem_id}", response_model=CandidateProblemOut)
def get_problem(problem_id: int, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    problem = db.get(ProgressiveProblem, problem_id)
    if problem is None or problem.status != ProgressiveProblemStatus.published:
        raise HTTPException(404, "Problem not found.")
    return problem


def _get_owned_attempt(db: Session, attempt_id: int, candidate: User) -> ProgressiveAttempt:
    attempt = db.get(ProgressiveAttempt, attempt_id)
    if attempt is None or attempt.user_id != candidate.id:
        raise HTTPException(404, "Attempt not found.")
    return attempt


@candidate_router.post("/problems/{problem_id}/start", response_model=CandidateAttemptOut)
def start_problem_attempt(problem_id: int, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    problem = db.get(ProgressiveProblem, problem_id)
    if problem is None or problem.status != ProgressiveProblemStatus.published:
        raise HTTPException(404, "Problem not found.")
    attempt = progressive_service.start_attempt(db, candidate.id, problem_id)
    return CandidateAttemptOut(id=attempt.id, problem_id=attempt.problem_id, current_stage=attempt.current_stage, status=attempt.status.value)


@candidate_router.get("/attempts/{attempt_id}/current-stage", response_model=CandidateStageOut)
def get_current_stage(attempt_id: int, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    attempt = _get_owned_attempt(db, attempt_id, candidate)
    try:
        requirement = progressive_service.get_current_requirement(db, attempt)
    except ProgressiveServiceError as e:
        raise _map_service_error(e)
    return CandidateStageOut(
        stage_order=requirement.stage_order, requirement_text=requirement.requirement_text,
        expected_behavior=requirement.expected_behavior,
    )


@candidate_router.get("/attempts/{attempt_id}/next-stage", response_model=CandidateStageOut)
def get_next_stage(attempt_id: int, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    """Alias of GET current-stage: progressive_service.submit_stage
    already auto-advances current_stage on submission (see Phase 2), so
    "proceed to the next unlocked stage" IS "read current_stage again" -
    exposed as its own route to name the operation explicitly, not
    because the underlying logic differs."""
    return get_current_stage(attempt_id, db, candidate)


@candidate_router.get("/attempts/{attempt_id}/state", response_model=CandidateStateOut)
def get_attempt_state(attempt_id: int, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    attempt = _get_owned_attempt(db, attempt_id, candidate)
    return CandidateStateOut(
        current_stage=attempt.current_stage,
        current_code=progressive_service.get_current_code(db, attempt),
        status=attempt.status.value,
        completed=attempt.completed_at is not None,
    )


@candidate_router.post("/attempts/{attempt_id}/turn", response_model=CandidateTurnOut)
def send_turn(attempt_id: int, payload: CandidateTurnRequest, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    attempt = _get_owned_attempt(db, attempt_id, candidate)
    try:
        result = progressive_pipeline.handle_candidate_turn(db, attempt, payload.candidate_request)
    except ProgressiveServiceError as e:
        raise _map_service_error(e)
    return CandidateTurnOut(response_message=result.response_message, code_after=result.code_after, accepted=result.accepted)


@candidate_router.post("/attempts/{attempt_id}/submit", response_model=CandidateStageResultOut, status_code=201)
def submit_stage(attempt_id: int, payload: CandidateStageSubmitRequest, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    attempt = _get_owned_attempt(db, attempt_id, candidate)
    try:
        result = progressive_service.submit_stage(db, attempt, attempt.current_stage, payload.code_snapshot)
    except ProgressiveServiceError as e:
        raise _map_service_error(e)
    return _to_candidate_stage_result(result)


@candidate_router.get("/attempts/{attempt_id}/stages/{stage_order}/result", response_model=CandidateStageResultOut)
def get_stage_result(attempt_id: int, stage_order: int, db: Session = Depends(get_db), candidate: User = Depends(require_candidate)):
    from ..models_progressive import ProgressiveStageResult
    attempt = _get_owned_attempt(db, attempt_id, candidate)
    result = (
        db.query(ProgressiveStageResult)
        .filter(ProgressiveStageResult.attempt_id == attempt.id, ProgressiveStageResult.stage_order == stage_order)
        .first()
    )
    if result is None:
        raise HTTPException(404, "No result for this stage yet.")
    return _to_candidate_stage_result(result)


# ==================== HR schemas ====================

class HrProblemCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=20_000)
    input_spec_json: dict[str, Any] = {}
    language: str = Field(min_length=1)
    starter_code: Optional[str] = None
    difficulty: Optional[str] = None
    time_limit_minutes: int = Field(default=30, ge=1)
    experience_band: Optional[str] = None


class HrProblemUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=300)
    description: Optional[str] = Field(default=None, max_length=20_000)
    input_spec_json: Optional[dict[str, Any]] = None
    starter_code: Optional[str] = None
    difficulty: Optional[str] = None
    time_limit_minutes: Optional[int] = Field(default=None, ge=1)


class HrRequirementCreate(BaseModel):
    stage_order: int = Field(ge=1)
    requirement_text: str = Field(min_length=1)
    expected_behavior: Optional[str] = None
    hidden_tests_json: list[dict] = []
    reference_solution: Optional[str] = None


class HrRequirementUpdate(BaseModel):
    requirement_text: Optional[str] = None
    expected_behavior: Optional[str] = None
    hidden_tests_json: Optional[list[dict]] = None
    reference_solution: Optional[str] = None


class HrReorderEntry(BaseModel):
    requirement_id: int
    stage_order: int = Field(ge=1)


class HrReorderRequest(BaseModel):
    order: list[HrReorderEntry]


class HrRequirementOut(BaseModel):
    """HR-only, full detail - unlike CandidateStageOut above, this
    legitimately includes hidden_tests_json/reference_solution: HR is
    trusted with everything it authored, same as every other round."""
    id: int
    problem_id: int
    stage_order: int
    requirement_text: str
    expected_behavior: Optional[str]
    hidden_tests_json: Optional[list]
    reference_solution: Optional[str]
    frozen: bool

    class Config:
        from_attributes = True


class HrProblemOut(BaseModel):
    id: int
    title: str
    description: str
    input_spec_json: dict
    language: str
    starter_code: Optional[str]
    difficulty: Optional[str]
    time_limit_minutes: int
    experience_band: Optional[str]
    status: str
    created_by: int

    class Config:
        from_attributes = True


class HrStageResultOut(BaseModel):
    """HR audit view - unlike CandidateStageResultOut, this legitimately
    includes the real code_snapshot and the raw test_results_json."""
    id: int
    stage_order: int
    code_snapshot: str
    test_results_json: Optional[list]
    passed: Optional[bool]

    class Config:
        from_attributes = True


# ==================== HR routes ====================
# Full detail throughout, including hidden_tests_json/reference_solution -
# HR is trusted with everything it authored, same as every other round.

def _get_problem_or_404(db: Session, problem_id: int) -> ProgressiveProblem:
    problem = db.get(ProgressiveProblem, problem_id)
    if problem is None:
        raise HTTPException(404, "Problem not found.")
    return problem


def _require_draft(problem: ProgressiveProblem) -> None:
    if problem.status != ProgressiveProblemStatus.draft:
        raise HTTPException(400, "This problem is published - requirements are frozen and can no longer be edited.")


@hr_router.post("/problems", status_code=201, response_model=HrProblemOut)
def create_problem(payload: HrProblemCreate, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    problem = ProgressiveProblem(created_by=hr.id, **payload.model_dump())
    db.add(problem)
    db.commit()
    db.refresh(problem)
    return problem


@hr_router.get("/problems", response_model=list[HrProblemOut])
def list_all_problems(db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    return db.query(ProgressiveProblem).order_by(ProgressiveProblem.created_at.desc()).all()


@hr_router.get("/problems/{problem_id}", response_model=HrProblemOut)
def review_problem(problem_id: int, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    return _get_problem_or_404(db, problem_id)


@hr_router.patch("/problems/{problem_id}", response_model=HrProblemOut)
def update_problem(problem_id: int, payload: HrProblemUpdate, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    problem = _get_problem_or_404(db, problem_id)
    _require_draft(problem)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(problem, field, value)
    db.commit()
    db.refresh(problem)
    return problem


@hr_router.get("/problems/{problem_id}/stages", response_model=list[HrRequirementOut])
def list_stages(problem_id: int, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """Full authoring detail, HR-only - hidden_tests_json/reference_solution
    included, the direct opposite of the candidate-facing routes above."""
    problem = _get_problem_or_404(db, problem_id)
    return sorted(problem.requirements, key=lambda r: r.stage_order)


@hr_router.post("/problems/{problem_id}/requirements", status_code=201, response_model=HrRequirementOut)
def create_requirement(problem_id: int, payload: HrRequirementCreate, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    problem = _get_problem_or_404(db, problem_id)
    _require_draft(problem)
    requirement = ProgressiveRequirement(problem_id=problem_id, **payload.model_dump())
    db.add(requirement)
    db.commit()
    db.refresh(requirement)
    return requirement


@hr_router.patch("/problems/{problem_id}/requirements/{requirement_id}", response_model=HrRequirementOut)
def update_requirement(problem_id: int, requirement_id: int, payload: HrRequirementUpdate, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    problem = _get_problem_or_404(db, problem_id)
    _require_draft(problem)
    requirement = db.get(ProgressiveRequirement, requirement_id)
    if requirement is None or requirement.problem_id != problem_id:
        raise HTTPException(404, "Requirement not found.")
    if requirement.frozen:
        raise HTTPException(400, "This requirement is frozen and can no longer be edited.")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(requirement, field, value)
    db.commit()
    db.refresh(requirement)
    return requirement


@hr_router.post("/problems/{problem_id}/requirements/reorder", response_model=list[HrRequirementOut])
def reorder_requirements(problem_id: int, payload: HrReorderRequest, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    problem = _get_problem_or_404(db, problem_id)
    _require_draft(problem)
    by_id = {r.id: r for r in problem.requirements}
    for entry in payload.order:
        if entry.requirement_id not in by_id:
            raise HTTPException(400, f"Requirement {entry.requirement_id} does not belong to problem {problem_id}.")
        if by_id[entry.requirement_id].frozen:
            raise HTTPException(400, f"Requirement {entry.requirement_id} is frozen and cannot be reordered.")
    new_orders = [entry.stage_order for entry in payload.order]
    if len(set(new_orders)) != len(new_orders):
        raise HTTPException(400, "stage_order values in a reorder request must be unique.")
    # Two-phase write (temporary large-offset values first, never
    # negative - stage_order has a CHECK(stage_order > 0) constraint) so
    # the DB's own unique(problem_id, stage_order) constraint never trips
    # on an intermediate state while swapping orders around. The offset
    # is far above any realistic stage count for this POC.
    for entry in payload.order:
        by_id[entry.requirement_id].stage_order = 1_000_000 + entry.stage_order
    db.flush()
    for entry in payload.order:
        by_id[entry.requirement_id].stage_order = entry.stage_order
    db.commit()
    return sorted(problem.requirements, key=lambda r: r.stage_order)


@hr_router.post("/problems/{problem_id}/publish", response_model=HrProblemOut)
def publish_problem(problem_id: int, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    problem = _get_problem_or_404(db, problem_id)
    _require_draft(problem)
    if not problem.requirements:
        raise HTTPException(400, "Cannot publish a problem with no requirements/stages defined.")

    missing_hidden_tests = progressive_service.stage_orders_missing_hidden_tests(problem.requirements)
    if missing_hidden_tests:
        raise HTTPException(
            400,
            f"Cannot publish: stage(s) {missing_hidden_tests} have no valid hidden test - "
            "every stage needs at least one hidden test with both an input and an expected output.",
        )

    # Deterministic, no LLM - see progressive_service.validate_stage_compatibility.
    # Runs after the hidden-tests check above on purpose: this replays
    # earlier stages' own hidden tests, so an earlier stage with none
    # would otherwise look vacuously "compatible" while actually being
    # untested.
    incompatibilities = progressive_service.validate_stage_compatibility(db, problem_id)
    if incompatibilities:
        details = "; ".join(
            f"stage {i['stage_order']}'s reference solution breaks stage(s) {i['breaks_stage_orders']} "
            f"({i['failed_test_count']} hidden test(s))"
            for i in incompatibilities
        )
        raise HTTPException(
            400,
            f"Cannot publish: a later stage's reference solution breaks an earlier stage's expected "
            f"behavior - this would guarantee candidate failure. {details}.",
        )

    for requirement in problem.requirements:
        requirement.frozen = True
    problem.status = ProgressiveProblemStatus.published
    db.commit()
    db.refresh(problem)
    return problem


@hr_router.get("/attempts/{attempt_id}/results", response_model=list[HrStageResultOut])
def view_attempt_results(attempt_id: int, db: Session = Depends(get_db), hr: User = Depends(require_hr)):
    """HR audit view - full detail (code snapshots, real test_results_json)
    for every stage result on this attempt, the direct opposite of the
    candidate-facing aggregate-only view above."""
    attempt = db.get(ProgressiveAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(404, "Attempt not found.")
    return sorted(attempt.stage_results, key=lambda r: r.stage_order)
