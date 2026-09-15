"""
ROUND 5 -- PROGRESSIVE ENGINEERING (POC). Phase 2 of this round's own
build: stage lifecycle only - see models_progressive.py (Phase 1) and
this session's Round 5 architecture-discovery notes for the full plan.

Deliberately isolated: imports only models_progressive.py and
execution_service.py (reused as-is, not modified, not forked - the same
local-subprocess trust model documented on that module applies here
unchanged). Nothing here imports Round 3's services, and nothing in
Round 1-4 imports this module.

NOT built in this phase (see the Phase 2 scope): no routes, no LLM
generator/prompt, no Policy Core, no Detector/Judge wiring. A future
route layer calls the functions below and translates the exceptions
here into HTTP errors - this module raises plain Python exceptions, not
HTTPException, since it has no framework dependency of its own.

CUMULATIVE VALIDATION (the actual regression-protection mechanism, not
just a design aspiration): submit_stage always re-runs every requirement
from stage 1 through the stage being submitted, not just the new one.
A candidate whose Stage 3 change breaks Stage 1's behavior sees that
failure in the SAME submission, immediately - see _run_cumulative_tests.

STAGE IMMUTABILITY: enforced twice - a proactive check here (a clear,
specific exception, not a raw DB error) before any insert is attempted,
and the DB's own unique(attempt_id, stage_order) constraint underneath
it (see models_progressive.ProgressiveStageResult) as the real,
non-bypassable guarantee. There is no update/overwrite function for a
ProgressiveStageResult anywhere in this module - the only way to create
one is submit_stage, and it will never touch a stage that already has one.
"""
from datetime import datetime

from sqlalchemy.orm import Session

from ..models_progressive import ProgressiveAttempt, ProgressiveRequirement, ProgressiveStageResult
from ..models import RoundStatus
from . import execution_service


class ProgressiveServiceError(Exception):
    """Base for every error this module raises - a future route layer
    can catch this one type and map it to a 400, or catch the specific
    subclasses below for a more precise HTTP status/message."""


class AttemptNotActiveError(ProgressiveServiceError):
    """The attempt is archived or already completed - no further stage
    submissions are accepted."""


class StageOutOfOrderError(ProgressiveServiceError):
    """The submitted stage_order isn't the attempt's current stage - a
    candidate can't skip ahead or resubmit past a stage this way (see
    StageAlreadyCompletedError for the specific case where the stage in
    question already has a result)."""


class StageAlreadyCompletedError(ProgressiveServiceError):
    """A ProgressiveStageResult already exists for this (attempt, stage) -
    the proactive half of the immutability guarantee (see module
    docstring); the DB's unique constraint is the other half."""


class UnknownStageError(ProgressiveServiceError):
    """No ProgressiveRequirement exists for this problem at this
    stage_order - a misconfigured problem, or a stage number that was
    never authored."""


def start_attempt(db: Session, user_id: int, problem_id: int) -> ProgressiveAttempt:
    """Idempotent, same reasoning as Round 3's start_round3: a second
    call with the same (user, problem) while one is already active
    returns the existing attempt unchanged rather than creating a
    duplicate. An archived attempt (a prior reset) does NOT count as
    "already active" - a fresh attempt is created, same as Submission's
    own archived/re-attempt convention."""
    existing = (
        db.query(ProgressiveAttempt)
        .filter(
            ProgressiveAttempt.user_id == user_id,
            ProgressiveAttempt.problem_id == problem_id,
            ProgressiveAttempt.archived.is_(False),
        )
        .first()
    )
    if existing is not None:
        return existing

    attempt = ProgressiveAttempt(user_id=user_id, problem_id=problem_id, current_stage=1)
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt


def _requirement_for_stage(db: Session, problem_id: int, stage_order: int) -> ProgressiveRequirement:
    requirement = (
        db.query(ProgressiveRequirement)
        .filter(ProgressiveRequirement.problem_id == problem_id, ProgressiveRequirement.stage_order == stage_order)
        .first()
    )
    if requirement is None:
        raise UnknownStageError(f"No requirement exists for problem {problem_id} at stage {stage_order}.")
    return requirement


def _max_stage(db: Session, problem_id: int) -> int:
    requirements = db.query(ProgressiveRequirement).filter(ProgressiveRequirement.problem_id == problem_id).all()
    if not requirements:
        raise UnknownStageError(f"Problem {problem_id} has no requirements defined.")
    return max(r.stage_order for r in requirements)


def get_current_requirement(db: Session, attempt: ProgressiveAttempt) -> ProgressiveRequirement:
    """The ONE requirement the candidate-facing workflow is allowed to
    load - never anything beyond attempt.current_stage. This is the
    structural half of future-requirement isolation: a caller that only
    ever calls this function, and never queries ProgressiveRequirement
    directly, cannot accidentally surface a later stage's text."""
    if attempt.archived:
        raise AttemptNotActiveError(f"Attempt {attempt.id} is archived.")
    if attempt.completed_at is not None:
        raise AttemptNotActiveError(f"Attempt {attempt.id} is already completed - no current stage remains.")
    return _requirement_for_stage(db, attempt.problem_id, attempt.current_stage)


def get_current_code(db: Session, attempt: ProgressiveAttempt) -> str | None:
    """What the candidate's editor should show right now: the code
    they left off with at the end of the last COMPLETED stage (the "same
    solution continues" guarantee), or the problem's starter_code if no
    stage has been submitted yet."""
    last_result = (
        db.query(ProgressiveStageResult)
        .filter(ProgressiveStageResult.attempt_id == attempt.id)
        .order_by(ProgressiveStageResult.stage_order.desc())
        .first()
    )
    if last_result is not None:
        return last_result.code_snapshot
    return attempt.problem.starter_code


def _compare_output(actual_output: str, expected_output) -> bool:
    """Same suffix-match reasoning Round 3's scoring already uses: a
    program that prompts before reading input() writes that prompt to
    stdout with no separator before its real output, so the prompt can
    only ever be a PREFIX of the real answer - comparing the suffix is
    correct, an exact match would wrongly fail any program that prompts
    at all. An empty expected_output can't use suffix-match (every
    string trivially "ends with" ""), so that falls back to exact match.
    Reimplemented locally rather than importing scoring_service - see
    module docstring on isolation."""
    expected_stripped = str(expected_output).strip() if expected_output is not None else None
    if expected_stripped:
        return actual_output.strip().endswith(expected_stripped)
    return expected_output is not None and actual_output.strip() == expected_stripped


def _run_cumulative_tests(language: str, code: str, requirements: list[ProgressiveRequirement]) -> list[dict]:
    """Runs every hidden test from EVERY requirement in `requirements`
    (already filtered to stage_order <= the stage being submitted by the
    caller) against the candidate's current code - the actual mechanism
    that catches a Stage 3 change breaking Stage 1's behavior, not just a
    design intention. Each result is tagged with which stage it came
    from so a caller can show "you broke Stage 1" specifically, not just
    an undifferentiated failure count."""
    results = []
    for requirement in requirements:
        for test_case in requirement.hidden_tests_json or []:
            test_input = test_case.get("input")
            expected_output = test_case.get("expected_output")
            if test_input is None:
                results.append({
                    "stage_order": requirement.stage_order, "input": test_input,
                    "expected_output": expected_output, "actual_output": None, "passed": False,
                    "error": "Hidden test is missing 'input' - could not run.",
                })
                continue
            execution = execution_service.run_code(language=language, code=code, stdin=[test_input])
            if execution.infra_error:
                results.append({
                    "stage_order": requirement.stage_order, "input": test_input,
                    "expected_output": expected_output, "actual_output": None, "passed": False,
                    "error": "Execution infrastructure error - not a candidate code fault.",
                })
                continue
            actual_output = (execution.stdout or "").strip()
            results.append({
                "stage_order": requirement.stage_order, "input": test_input,
                "expected_output": expected_output, "actual_output": actual_output,
                "passed": _compare_output(actual_output, expected_output),
            })
    return results


def _is_valid_hidden_test(test_case) -> bool:
    """A hidden test that can actually be won. Matches the exact failure
    modes _run_cumulative_tests/_compare_output already have: a missing
    'input' triggers that function's own explicit auto-fail, and a
    missing 'expected_output' makes _compare_output's `is not None` guard
    always False - both mean this test can never pass, no matter what
    the candidate submits. An empty-string 'expected_output' (checking
    for empty output) is legitimately winnable, so is not rejected here -
    only a genuinely MISSING key is."""
    return isinstance(test_case, dict) and test_case.get("input") is not None and test_case.get("expected_output") is not None


def stage_orders_missing_hidden_tests(requirements: list[ProgressiveRequirement]) -> list[int]:
    """Stage orders with zero winnable hidden tests - see
    _is_valid_hidden_test. Publishing one of these guarantees every
    candidate silently fails that stage regardless of what they submit
    (submit_stage's `all_passed = all(...) if test_results else False`
    already makes zero test cases an automatic fail). Used at publish
    time (routers/progressive.py's publish_problem) to block exactly
    that, before any candidate is ever given an attempt."""
    return [
        r.stage_order for r in requirements
        if not any(_is_valid_hidden_test(t) for t in (r.hidden_tests_json or []))
    ]


def validate_stage_compatibility(db: Session, problem_id: int) -> list[dict]:
    """Deterministic (no LLM) publish-time check for the cumulative-
    testing trap this round's whole design depends on avoiding: for
    every stage N that has a reference_solution, replay every EARLIER
    stage's hidden tests against stage N's own reference solution, using
    the exact same mechanism (_run_cumulative_tests) a real candidate
    submission is checked with - not a semantic guess about whether the
    requirement text "sounds" compatible. If stage N's own reference
    solution can't pass a test stage 1..N-1 already promised, stage N's
    requirement silently redefined or broke earlier behavior - publishing
    it would guarantee every candidate fails a stage they may have
    already correctly completed, through no fault of their own.

    Returns a list of {"stage_order", "breaks_stage_orders", "failed_test_count"}
    dicts, one per incompatible stage - empty means every stage is
    compatible with everything before it. A stage with no
    reference_solution yet (still being authored) is skipped, not
    flagged - there's nothing to replay. Called from routers/progressive.py's
    publish_problem, which decides whether to block on a non-empty result."""
    requirements = (
        db.query(ProgressiveRequirement)
        .filter(ProgressiveRequirement.problem_id == problem_id)
        .order_by(ProgressiveRequirement.stage_order)
        .all()
    )
    if len(requirements) < 2:
        return []  # nothing earlier to break

    problem = requirements[0].problem
    incompatibilities = []
    for i, requirement in enumerate(requirements):
        if not requirement.reference_solution:
            continue
        earlier_requirements = requirements[:i]
        if not earlier_requirements:
            continue
        results = _run_cumulative_tests(problem.language, requirement.reference_solution, earlier_requirements)
        failed = [r for r in results if not r["passed"]]
        if failed:
            incompatibilities.append({
                "stage_order": requirement.stage_order,
                "breaks_stage_orders": sorted({r["stage_order"] for r in failed}),
                "failed_test_count": len(failed),
            })
    return incompatibilities


def submit_stage(db: Session, attempt: ProgressiveAttempt, stage_order: int, code_snapshot: str) -> ProgressiveStageResult:
    """Accepts one stage's submission: validates it's actually the
    attempt's current stage, runs CUMULATIVE hidden tests (every
    requirement 1..stage_order, per this round's whole premise - see
    module docstring), creates the immutable ProgressiveStageResult, then
    either advances current_stage (unlocking the next stage regardless of
    100% correctness - matching Round 3's own "candidate discovers gaps
    later" philosophy, not gating advancement on perfection) or completes
    the attempt if this was the final stage."""
    if attempt.archived:
        raise AttemptNotActiveError(f"Attempt {attempt.id} is archived.")
    if attempt.completed_at is not None:
        raise AttemptNotActiveError(f"Attempt {attempt.id} is already completed.")

    # Checked BEFORE the ordering check on purpose: current_stage always
    # advances past a stage the moment it's submitted (see the end of
    # this function), so a resubmission attempt for that same stage is
    # always ALSO out-of-order by the time it arrives. Checking existence
    # first gives the caller the more specific, more actionable error
    # ("this stage is already done") instead of the generic "wrong stage
    # number" - both are real, but this one is more useful here.
    existing = (
        db.query(ProgressiveStageResult)
        .filter(ProgressiveStageResult.attempt_id == attempt.id, ProgressiveStageResult.stage_order == stage_order)
        .first()
    )
    if existing is not None:
        raise StageAlreadyCompletedError(f"Attempt {attempt.id} already has a result for stage {stage_order}.")

    if stage_order != attempt.current_stage:
        raise StageOutOfOrderError(
            f"Attempt {attempt.id} is on stage {attempt.current_stage}, cannot submit stage {stage_order}."
        )

    max_stage = _max_stage(db, attempt.problem_id)
    if stage_order > max_stage:
        raise UnknownStageError(f"Problem {attempt.problem_id} has no stage {stage_order} (max is {max_stage}).")

    requirements_up_to_here = (
        db.query(ProgressiveRequirement)
        .filter(ProgressiveRequirement.problem_id == attempt.problem_id, ProgressiveRequirement.stage_order <= stage_order)
        .order_by(ProgressiveRequirement.stage_order)
        .all()
    )
    language = attempt.problem.language
    test_results = _run_cumulative_tests(language, code_snapshot, requirements_up_to_here)
    all_passed = all(r["passed"] for r in test_results) if test_results else False

    result = ProgressiveStageResult(
        attempt_id=attempt.id, stage_order=stage_order, code_snapshot=code_snapshot,
        test_results_json=test_results, passed=all_passed, submitted_at=datetime.utcnow(),
    )
    db.add(result)

    if stage_order == max_stage:
        attempt.completed_at = datetime.utcnow()
        attempt.status = RoundStatus.submitted
    else:
        attempt.current_stage = stage_order + 1

    db.commit()
    db.refresh(result)
    return result
