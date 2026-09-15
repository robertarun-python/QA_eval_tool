"""
Controlled AI Coding Assessment POC - Phase 2: an independent LLM Judge.
See the architecture discovery notes (this session) for the full 5-phase
plan this is Phase 2 of, and poc_ai_output_detector.py for Phase 1.

ROLE SEPARATION (the entire point of this phase): a GENERATOR produces
the candidate-facing response; this module reviews an ALREADY-GENERATED
response against an explicit assistance contract and answers exactly one
question - "did this violate the contract?" It never generates or
rewrites a candidate-facing response, never improves the response under
review, and is never itself shown to a candidate. See JudgeResult -
there is deliberately no field on it that could be mistaken for
candidate-facing text (test_poc_ai_judge.py asserts this by introspecting
the dataclass, not just by convention).

ISOLATION: this is a new, separate POC component, not wired into Round 3
or any live route. It reuses llm_service's centralized Claude client
(_call_claude/_parse_json_response/_load_prompt) - the same low-level
infrastructure every round already shares - but does not import from,
and must never be imported by, round3_construct_engine.py /
round3_constructs.py / any Round 3 prompt or route. It does not import
poc_ai_output_detector.py either: this module receives that detector's
verdict as plain input data (detector_status/detector_reason_codes), it
does not depend on Phase 1's code.

SCOPE DISCIPLINE: the Judge receives only what it needs to evaluate one
interaction - the candidate's request, the generated response, the
allowed scope, and the CURRENT requirement only. It is never handed the
full conversation history, future requirements, hidden tests, or the
reference solution by this module's own contract (JudgeInput has no
field for any of those) - the caller (not yet built - that's Phase 4)
is responsible for never assembling them into candidate_request/
generated_response/current_requirement either. The Judge's own prompt
also instructs it to never repeat or expand on the current_requirement
text it IS given, and to never quote leaked content in its explanation
even when it detects a leak - only that one happened.
"""
import json
from dataclasses import dataclass, field
from typing import Optional

from . import llm_service

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_UNCERTAIN = "UNCERTAIN"
_VALID_VERDICTS = {VERDICT_PASS, VERDICT_FAIL, VERDICT_UNCERTAIN}

REASON_CODES = {
    "COMPLETE_SOLUTION_LEAK",
    "UNREQUESTED_ALGORITHM_SOLUTION",
    "INVENTED_TEST_CASES",
    "INVENTED_EDGE_CASES",
    "UNREQUESTED_CODE_GENERATION",
    "FUTURE_REQUIREMENT_LEAK",
    "HIDDEN_TEST_LEAK",
    "REFERENCE_SOLUTION_LEAK",
    "SCOPE_EXCEEDED",
    "OTHER_CONTRACT_VIOLATION",
}
_VALID_SEVERITIES = {"none", "low", "medium", "high"}

# Surgical hardening (real-data replay finding): the Judge occasionally
# reasoned its way to "actually compliant" mid-explanation while leaving
# the verdict field at its first-instinct FAIL. These phrases are a
# best-effort, honestly-limited catch of that SPECIFIC observed pattern
# and close variants - not a general semantic-contradiction detector
# (same discipline as poc_ai_output_detector.py's own heuristics: cheap,
# fallible, only ever used to downgrade toward UNCERTAIN, never to
# upgrade confidence). See _check_consistency.
_COMPLIANCE_REVERSAL_PHRASES = (
    "actually compliant", "is compliant", "correct behavior", "correct action",
    "proper adherence", "does not violate", "not a violation", "not in violation",
)
_VIOLATION_REVERSAL_PHRASES = (
    "actually a violation", "actually violates", "is a violation", "does violate",
    "this violates", "not compliant", "not acceptable",
)


@dataclass
class JudgeInput:
    """What the Judge is given to review - deliberately narrow (see
    module docstring's Scope discipline). allowed_assistance_scope and
    current_requirement are supplied explicitly by the caller for this
    phase; Phase 3 (Policy Core) is what would eventually compute them,
    not this module."""
    candidate_request: str
    generated_response: str
    response_kind: str
    allowed_assistance_scope: str
    current_requirement: str
    detector_status: str
    detector_reason_codes: list[str] = field(default_factory=list)
    # Set only when this turn is answering a prior clarifying question -
    # see the Phase 1 review notes on why a raw current-turn instruction
    # alone ("inputValues") can be meaningless without the instruction it
    # answers. Optional: most turns aren't a clarify-answer.
    effective_candidate_instruction: Optional[str] = None


@dataclass
class JudgeResult:
    """The Judge's entire output surface. No field here is, or should
    ever become, candidate-facing - explanation is explicitly internal
    (see poc_ai_judge.txt's output rules) and there is no field shaped
    like a response/message/reply that a caller could mistake for one."""
    verdict: str
    reason_codes: list[str] = field(default_factory=list)
    severity: str = "none"
    explanation: str = ""


def _render_prompt(judge_input: JudgeInput) -> str:
    if judge_input.effective_candidate_instruction:
        effective_block = (
            "PRIMARY REQUEST - the candidate's earlier instruction that the message above is answering. "
            "Evaluate THIS as the substantive request; the message above is only how they completed it "
            "(a name, a value, a short answer) and must never be judged in isolation as if it were the "
            "whole ask:\n"
            f"{judge_input.effective_candidate_instruction}"
        )
    else:
        effective_block = "(This turn is not answering a prior clarifying question - evaluate the request above on its own.)"
    return llm_service._load_prompt("poc_ai_judge.txt").format(
        candidate_request=judge_input.candidate_request,
        effective_candidate_instruction_block=effective_block,
        allowed_assistance_scope=judge_input.allowed_assistance_scope,
        current_requirement=judge_input.current_requirement,
        response_kind=judge_input.response_kind,
        generated_response=judge_input.generated_response,
        detector_status=judge_input.detector_status,
        detector_reason_codes=json.dumps(judge_input.detector_reason_codes),
    )


def _has_any_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(p in lowered for p in phrases)


def _check_consistency(verdict: str, reason_codes: list[str], explanation: str) -> Optional[str]:
    """Two deterministic checks, both cheap and both only ever used to
    downgrade toward UNCERTAIN, never to change a PASS/FAIL into a
    different PASS/FAIL:
    (a) STRUCTURAL - a PASS with reason_codes attached is a contradiction
        by construction; no text-reading required, fully reliable.
    (b) TEXTUAL, best-effort - the explanation's own wording reverses the
        verdict mid-explanation (the exact real-data replay failure: a
        FAIL whose explanation concluded "actually compliant"). Catches
        that specific pattern and close variants; it is NOT a general
        semantic-contradiction detector - see module's known limitations.
    Returns a reason string if inconsistent, else None."""
    if verdict == VERDICT_PASS and reason_codes:
        return "Verdict was PASS but reason_codes were attached - structurally inconsistent."
    if verdict != VERDICT_PASS and _has_any_phrase(explanation, _COMPLIANCE_REVERSAL_PHRASES):
        return f"Verdict was {verdict} but the explanation's own wording concludes compliance - treating as UNCERTAIN rather than trusting either conclusion."
    if verdict == VERDICT_PASS and _has_any_phrase(explanation, _VIOLATION_REVERSAL_PHRASES):
        return "Verdict was PASS but the explanation's own wording concludes a violation - treating as UNCERTAIN rather than trusting either conclusion."
    return None


def _safe_fallback(reason: str) -> JudgeResult:
    """Never PASS on a judge failure. An unparseable/malformed verdict is
    exactly the "genuinely can't tell" case UNCERTAIN exists for - not a
    silent pass-through that would let a real violation through simply
    because the model had a bad turn."""
    return JudgeResult(verdict=VERDICT_UNCERTAIN, reason_codes=[], severity="none", explanation=reason)


def judge_ai_response(judge_input: JudgeInput) -> JudgeResult:
    """Independently review one already-generated AI response against
    the assistance contract in poc_ai_judge.txt. Never generates or
    rewrites a candidate-facing response - its only output is a verdict
    plus short, internal-only reasoning. Not wired into any live route;
    callable in isolation for offline/replay validation only (Phase 2)."""
    prompt = _render_prompt(judge_input)
    try:
        raw = llm_service._call_claude(prompt, max_tokens=1024)
        parsed = llm_service._parse_json_response(raw)
    except Exception as e:
        return _safe_fallback(f"Judge call failed or returned unparseable output ({type(e).__name__}).")

    if not isinstance(parsed, dict):
        return _safe_fallback("Judge response was not a JSON object.")

    verdict = parsed.get("verdict")
    if verdict not in _VALID_VERDICTS:
        return _safe_fallback(f"Judge returned an unrecognized verdict: {verdict!r}.")

    # Unrecognized codes are dropped, not passed through - the contract
    # for this reason-code vocabulary is closed (see REASON_CODES); a
    # model drifting onto an invented code should not silently expand it.
    reason_codes = [c for c in parsed.get("reason_codes") or [] if c in REASON_CODES]
    severity = parsed.get("severity")
    if severity not in _VALID_SEVERITIES:
        severity = "none"
    explanation = parsed.get("explanation")
    if not isinstance(explanation, str):
        explanation = ""

    inconsistency = _check_consistency(verdict, reason_codes, explanation)
    if inconsistency is not None:
        return _safe_fallback(f"{inconsistency} (original verdict was {verdict}: {explanation})")

    return JudgeResult(verdict=verdict, reason_codes=reason_codes, severity=severity, explanation=explanation)
