"""
ROUND 5 -- PROGRESSIVE ENGINEERING (POC). Phase 5: wires the four
previously-standalone components into one controlled pipeline:

  Candidate request
        |
        v
  Deterministic Policy Core (progressive_policy.py, Phase 4)
        |
        v
  Generator (progressive_llm.py, Phase 3)
        |
        v
  Phase 1 Output Detector (poc_ai_output_detector.py - REUSED AS-IS)
        |
        v
  Phase 2 Independent Judge (poc_ai_judge.py - REUSED AS-IS, runs for
        |                     every generated response, not just
        |                     detector-flagged ones - see below)
        v
  Final candidate-facing response

REUSE, NOT DUPLICATION: this module imports poc_ai_output_detector.inspect_response
and poc_ai_judge.judge_ai_response/JudgeInput directly - neither module's
code, prompt, or dataclasses are copied or altered anywhere in this file.

ROLE SEPARATION (the point of having four stages, not one):
- Policy Core is the PRE-generation authority: for the 5 REFUSE_*
  categories, it short-circuits BEFORE the Generator ever runs - no LLM
  call happens at all for those, which is a stronger guarantee than
  "the model was asked nicely to refuse" (see handle_candidate_turn).
- Detector is POST-generation EVIDENCE - a cheap, deterministic signal,
  never a verdict on its own.
- Judge is INDEPENDENT SEMANTIC EVALUATION - the actual PASS/FAIL/
  UNCERTAIN authority once generation happens. Per explicit instruction,
  a SUSPICIOUS detector result must never auto-become FAIL, and a SAFE
  detector result must never auto-become PASS - both are passed to the
  Judge as detector_status/detector_reason_codes (evidence), and the
  Judge's own independent verdict is what actually decides the outcome.
  See test_progressive_pipeline.py's dedicated tests proving both
  directions (Judge overriding a SUSPICIOUS detector to PASS, and Judge
  overriding a SAFE detector to FAIL).

SAFE CONTROLLED RESPONSE ON REJECTION: if the Judge returns FAIL or
UNCERTAIN, the candidate never sees the generator's actual output - see
_SAFE_FALLBACK_MESSAGE and _POLICY_REFUSAL_MESSAGES. Nothing about WHY
(no detector findings, no Judge explanation, no policy internals) is
ever included in what reaches the candidate; that reasoning is recorded
on the ProgressiveAiTurn audit row instead (HR-only, same tier as
Round3TurnAuditOut from Round 3's own hardening - never exposed through
this module's PipelineResult, which is the only thing a route would ever
hand to a candidate).

ISOLATION: imports models_progressive.py, progressive_service.py,
progressive_llm.py, progressive_policy.py (this round's own Phases 1-4),
and poc_ai_output_detector.py/poc_ai_judge.py (reused as-is, unmodified).
Imports nothing from Round 3. Nothing in Round 1-4 imports this module.
Not wired to any route - callable in isolation for testing/replay only.
"""
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from ..models_progressive import ProgressiveAttempt, ProgressiveAiTurn, ProgressiveRequirement
from . import progressive_service
from . import progressive_llm
from . import progressive_policy
from .poc_ai_output_detector import inspect_response
from .poc_ai_judge import JudgeInput, judge_ai_response, VERDICT_PASS

_SAFE_FALLBACK_MESSAGE = (
    "I can't complete that request as asked - please make your instruction more specific and try again."
)

# Candidate-facing text per policy refusal category - distinct from
# progressive_policy._CEILING_DESCRIPTIONS, which is internal/audit
# phrasing. These mirror Round 3's own proven canned-refusal tone (a
# pattern, not shared code - see progressive_policy.py's own docstring).
_POLICY_REFUSAL_MESSAGES = {
    progressive_policy.REFUSE_COMPLETE_SOLUTION: "I can't build this for you - tell me what you want built, and I'll do exactly that.",
    progressive_policy.REFUSE_TEST_GENERATION: "That's for you to decide - tell me the specific test values you want to try.",
    progressive_policy.REFUSE_EDGE_CASE_GENERATION: "That's for you to identify - decide which cases matter, then tell me what to write.",
    progressive_policy.REFUSE_FUTURE_REQUIREMENT: "I can only help with the current stage's requirement - I don't have information about anything beyond it.",
    progressive_policy.REFUSE_HIDDEN_TEST_REFERENCE_LEAK: "I can't share that - hidden tests and reference solutions aren't available to disclose.",
    progressive_policy.REFUSE_CANDIDATE_REASONING: "Deciding on your approach or algorithm is your job, not mine - tell me the specific step you've already decided on, and I'll help you execute it.",
}

_GENERATION_ALLOWED_KINDS = {
    progressive_policy.EXPLAIN, progressive_policy.CLARIFY,
    progressive_policy.NARROW_EDIT, progressive_policy.RUN_CANDIDATE_INPUT,
}


@dataclass
class PipelineResult:
    """The ONLY thing a future route would ever hand to a candidate.
    No field here carries detector findings, Judge explanation, policy
    reason codes, or anything about a future requirement/hidden test/
    reference solution - see test_progressive_pipeline.py's structural
    check on this dataclass, same discipline as JudgeResult (Phase 2)."""
    response_message: str
    code_after: Optional[str]
    accepted: bool  # False for a policy refusal or a Judge FAIL/UNCERTAIN


def _forbidden_snippets_for(db: Session, attempt: ProgressiveAttempt) -> list[tuple[str, str]]:
    """Every piece of server-side-only content that must never appear
    verbatim in a candidate-facing response - wired into the Phase 1
    Detector's exact-match leak check (poc_ai_output_detector.py's
    forbidden_snippets) as a second, independent layer behind the
    Generator's own structural isolation (progressive_llm.build_generator_context
    has no field for any of this to begin with - see that module's
    docstring). Sourced ONLY from this problem's own ProgressiveRequirement
    rows, trusted server-side state - never from candidate input or a
    prior candidate submission. Covers every stage, not just the current
    one: a future stage's hidden tests/reference solution must never leak
    any more than the current stage's can. A future stage's requirement
    TEXT is included too, purely as defense-in-depth - the Policy Core
    already refuses a request for it (REFUSE_FUTURE_REQUIREMENT) before
    generation ever happens, so this only matters if a generated response
    ends up quoting it despite never being given it."""
    requirements = (
        db.query(ProgressiveRequirement)
        .filter(ProgressiveRequirement.problem_id == attempt.problem_id)
        .all()
    )
    snippets: list[tuple[str, str]] = []
    for requirement in requirements:
        stage = requirement.stage_order
        if requirement.reference_solution:
            snippets.append((f"stage_{stage}_reference_solution", requirement.reference_solution))
        for i, test_case in enumerate(requirement.hidden_tests_json or []):
            test_input = test_case.get("input")
            expected_output = test_case.get("expected_output")
            if test_input:
                snippets.append((f"stage_{stage}_hidden_test_{i}_input", str(test_input)))
            if expected_output:
                snippets.append((f"stage_{stage}_hidden_test_{i}_expected_output", str(expected_output)))
        if stage != attempt.current_stage:
            snippets.append((f"stage_{stage}_requirement_text", requirement.requirement_text))
    return snippets


def _last_turn_for_stage(db: Session, attempt: ProgressiveAttempt) -> Optional[ProgressiveAiTurn]:
    return (
        db.query(ProgressiveAiTurn)
        .filter(ProgressiveAiTurn.attempt_id == attempt.id, ProgressiveAiTurn.stage_order == attempt.current_stage)
        .order_by(ProgressiveAiTurn.id.desc())
        .first()
    )


def _effective_instruction_for(last_turn: Optional[ProgressiveAiTurn]) -> Optional[str]:
    """Chains back through consecutive clarify turns to the original
    real instruction - same reasoning as Round 3's continuation check
    and Phase 2's hardening, applied here at the point turns actually
    get persisted (Phase 5 is the first phase that writes ProgressiveAiTurn
    rows, so this is also the first phase that CAN thread this)."""
    if last_turn is None or last_turn.judge_verdict != VERDICT_PASS:
        # Only a genuinely ACCEPTED turn is worth chaining from - a
        # policy refusal or a Judge rejection was never shown to the
        # candidate as "here's my question", so there's nothing for a
        # new message to be "answering".
        return None
    # response_kind isn't stored directly on ProgressiveAiTurn (see
    # models_progressive.py) - detector_status/judge fields are, but the
    # "was this a clarify" signal for chaining is simply whether any code
    # came out of it: an accepted clarify never has code_after set.
    if last_turn.code_after is not None:
        return None
    return last_turn.effective_candidate_instruction or last_turn.candidate_prompt


def _record_turn(
    db: Session, attempt: ProgressiveAttempt, candidate_request: str, effective_instruction: Optional[str],
    generator_response: Optional[progressive_llm.GeneratorResponse], detector_status: Optional[str],
    detector_reason_codes: list, judge_verdict: Optional[str], judge_reason_codes: list,
    judge_severity: Optional[str], candidate_facing_response: str,
) -> ProgressiveAiTurn:
    """generated_response/code_after are the GENERATOR's true raw output
    (audit ground truth, populated even on rejection); candidate_facing_response
    is what was actually shown - see models_progressive.ProgressiveAiTurn's
    docstring. There is no separate candidate-facing-code column: the
    candidate is only ever shown code_after verbatim when judge_verdict
    is PASS, so that single stored fact is enough to reconstruct it -
    adding a second, always-redundant code column would just be a second
    place for the two to silently drift apart."""
    turn = ProgressiveAiTurn(
        attempt_id=attempt.id,
        stage_order=attempt.current_stage,
        candidate_prompt=candidate_request,
        effective_candidate_instruction=effective_instruction,
        generated_response=generator_response.response_message if generator_response else candidate_facing_response,
        detector_status=detector_status,
        detector_reason_codes=detector_reason_codes,
        judge_verdict=judge_verdict,
        judge_reason_codes=judge_reason_codes,
        judge_severity=judge_severity,
        code_after=generator_response.code_after if generator_response else None,
        candidate_facing_response=candidate_facing_response,
    )
    db.add(turn)
    db.commit()
    db.refresh(turn)
    return turn


def handle_candidate_turn(db: Session, attempt: ProgressiveAttempt, candidate_request: str) -> PipelineResult:
    """The one entry point: runs candidate_request through the full
    controlled pipeline and returns exactly what the candidate is allowed
    to see, while recording the complete internal picture on a new
    ProgressiveAiTurn row for audit. Not wired to any route yet."""
    requirement = progressive_service.get_current_requirement(db, attempt)
    current_code = progressive_service.get_current_code(db, attempt)

    last_turn = _last_turn_for_stage(db, attempt)
    effective_instruction = _effective_instruction_for(last_turn)

    # ---- Stage 1: Deterministic Policy Core ----
    policy_decision = progressive_policy.decide(progressive_policy.PolicyInput(
        current_stage=attempt.current_stage,
        current_requirement=requirement.requirement_text,
        candidate_request=candidate_request,
        effective_candidate_instruction=effective_instruction,
    ))

    if policy_decision.category not in _GENERATION_ALLOWED_KINDS:
        # Refused BEFORE generation - no LLM call happens at all for
        # this turn. Nothing to detect or judge either; recorded plainly.
        message = _POLICY_REFUSAL_MESSAGES[policy_decision.category]
        _record_turn(
            db, attempt, candidate_request, effective_instruction,
            generator_response=None, detector_status=None, detector_reason_codes=[],
            judge_verdict=None, judge_reason_codes=[], judge_severity=None,
            candidate_facing_response=message,
        )
        return PipelineResult(response_message=message, code_after=None, accepted=False)

    # ---- Stage 2: Generator ----
    generator_response = progressive_llm.generate_response(db, attempt, candidate_request)

    # ---- Stage 3: Phase 1 Detector (reused as-is) ----
    claims_no_code = generator_response.response_kind not in ("code_edit", "direct_edit")
    detector_result = inspect_response(
        candidate_instruction=candidate_request,
        claims_no_code=claims_no_code,
        response_text=generator_response.response_message,
        previous_code=current_code,
        new_code=generator_response.code_after,
        forbidden_snippets=_forbidden_snippets_for(db, attempt),
    )
    detector_reason_codes = [f.check for f in detector_result.findings]

    # ---- Stage 4: Phase 2 Independent Judge (reused as-is) - runs for
    # every generated response. Its own verdict decides the outcome; the
    # detector's status/findings are handed over purely as evidence (see
    # module docstring on why neither SUSPICIOUS nor SAFE auto-decides). ----
    judge_result = judge_ai_response(JudgeInput(
        candidate_request=candidate_request,
        generated_response=f"{generator_response.response_message}\n\n{generator_response.code_after or ''}".strip(),
        response_kind=generator_response.response_kind,
        allowed_assistance_scope=policy_decision.ceiling_description,
        current_requirement=requirement.requirement_text,
        detector_status=detector_result.verdict,
        detector_reason_codes=detector_reason_codes,
        effective_candidate_instruction=effective_instruction,
    ))

    if judge_result.verdict == VERDICT_PASS:
        final_message = generator_response.response_message
        final_code = generator_response.code_after
        accepted = True
    else:
        # FAIL or UNCERTAIN - safe controlled response. Never the
        # generator's real output, never the Judge's explanation, never
        # the detector's findings, never policy internals.
        final_message = _SAFE_FALLBACK_MESSAGE
        final_code = None
        accepted = False

    _record_turn(
        db, attempt, candidate_request, effective_instruction,
        generator_response=generator_response,
        detector_status=detector_result.verdict, detector_reason_codes=detector_reason_codes,
        judge_verdict=judge_result.verdict, judge_reason_codes=judge_result.reason_codes,
        judge_severity=judge_result.severity,
        candidate_facing_response=final_message,
    )
    return PipelineResult(response_message=final_message, code_after=final_code, accepted=accepted)
