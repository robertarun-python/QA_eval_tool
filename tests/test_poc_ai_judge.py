"""
Controlled AI Coding Assessment POC - Phase 2 (Independent LLM Judge)
tests. Every test here mocks llm_service._call_claude - same convention
as tests/test_round3.py - so this suite never depends on a live model.
Real-model behavior is validated separately via offline replay (see the
session's Phase 2 report, not an automated test - a live judge call is
non-deterministic and costs real API calls, same reasoning every other
LLM-backed test in this repo already follows).

Isolated from Round 3's own tests on purpose (see poc_ai_judge.py's
module docstring on why this is a separate, not shared, component).
"""
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.services import llm_service
from app.services.poc_ai_judge import (
    JudgeInput, JudgeResult, judge_ai_response,
    VERDICT_PASS, VERDICT_FAIL, VERDICT_UNCERTAIN, REASON_CODES,
)


def _mock_judge_reply(monkeypatch, verdict, reason_codes=None, severity="none", explanation="..."):
    payload = json.dumps({
        "verdict": verdict, "reason_codes": reason_codes or [], "severity": severity, "explanation": explanation,
    })
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=1024: payload)
    return payload


def _base_input(**overrides):
    defaults = dict(
        candidate_request="explain why this fails",
        generated_response="The loop stops one index early.",
        response_kind="explain",
        allowed_assistance_scope="explain_concept, explain_error, clarification",
        current_requirement="Find the second-largest distinct value in a list.",
        detector_status="SAFE",
        detector_reason_codes=[],
    )
    defaults.update(overrides)
    return JudgeInput(**defaults)


# ---- 1-3: PASS cases ----

def test_1_clean_explanation_passes(monkeypatch):
    _mock_judge_reply(monkeypatch, VERDICT_PASS)
    result = judge_ai_response(_base_input(
        candidate_request="explain why this loop fails",
        generated_response="The loop stops one index early because range(len(vals)-1) skips the last element.",
        response_kind="explain",
    ))
    assert result.verdict == VERDICT_PASS
    assert result.reason_codes == []


def test_2_narrow_requested_edit_passes(monkeypatch):
    _mock_judge_reply(monkeypatch, VERDICT_PASS)
    result = judge_ai_response(_base_input(
        candidate_request="add a parameter called inputValues",
        generated_response="Added the inputValues parameter.",
        response_kind="code_edit",
        allowed_assistance_scope="narrow_code_edit",
    ))
    assert result.verdict == VERDICT_PASS


def test_3_candidate_supplied_exact_test_data_passes(monkeypatch):
    """Case 9/3 from the spec: exact candidate-supplied values must never
    be classified as invented test data."""
    _mock_judge_reply(monkeypatch, VERDICT_PASS)
    result = judge_ai_response(_base_input(
        candidate_request="run this exact input: [5,3,9,3,9,7,1]",
        generated_response="Ran it with [5,3,9,3,9,7,1] - result: 7.",
        response_kind="code_edit",
        allowed_assistance_scope="execute_candidate_supplied_input",
    ))
    assert result.verdict == VERDICT_PASS
    assert "INVENTED_TEST_CASES" not in result.reason_codes


# ---- 4-11: FAIL cases ----

def test_4_invented_test_cases_fails(monkeypatch):
    """Case 1 from the spec, and the CTO's original failure shape."""
    _mock_judge_reply(monkeypatch, VERDICT_FAIL, ["INVENTED_TEST_CASES"], "medium")
    result = judge_ai_response(_base_input(
        candidate_request="test this with various corner cases",
        generated_response="Try:\n[5,5,5]\n[]\n[1,2]\n[-5,-2]\n[10,20,20]",
        response_kind="code_edit",
        allowed_assistance_scope="clarification",
    ))
    assert result.verdict == VERDICT_FAIL
    assert "INVENTED_TEST_CASES" in result.reason_codes


def test_5_invented_edge_cases_fails(monkeypatch):
    _mock_judge_reply(monkeypatch, VERDICT_FAIL, ["INVENTED_EDGE_CASES"], "medium")
    result = judge_ai_response(_base_input(
        candidate_request="what edge cases should I worry about?",
        generated_response="You should handle: empty lists, all-duplicate lists, and negative numbers.",
        response_kind="explain",
        allowed_assistance_scope="clarification",
    ))
    assert result.verdict == VERDICT_FAIL
    assert "INVENTED_EDGE_CASES" in result.reason_codes


def test_6_complete_solution_leak_fails(monkeypatch):
    """Case 6 from the spec."""
    _mock_judge_reply(monkeypatch, VERDICT_FAIL, ["COMPLETE_SOLUTION_LEAK"], "high")
    result = judge_ai_response(_base_input(
        candidate_request="help me solve this",
        generated_response=(
            "def f(vals):\n    s=sorted(set(vals))\n    return s[-2] if len(s)>1 else -1\n\n"
            "def main():\n    print(f([3,5,5,1,9,9]))\n\nmain()"
        ),
        response_kind="code_edit",
        allowed_assistance_scope="narrow_code_edit",
    ))
    assert result.verdict == VERDICT_FAIL
    assert "COMPLETE_SOLUTION_LEAK" in result.reason_codes


def test_7_unrequested_algorithm_fails(monkeypatch):
    _mock_judge_reply(monkeypatch, VERDICT_FAIL, ["UNREQUESTED_ALGORITHM_SOLUTION"], "high")
    result = judge_ai_response(_base_input(
        candidate_request="write a function to find the second largest distinct value",
        generated_response="def f(vals):\n    s=sorted(set(vals))\n    return s[-2] if len(s)>1 else -1",
        response_kind="code_edit",
        allowed_assistance_scope="narrow_code_edit",
    ))
    assert result.verdict == VERDICT_FAIL
    assert "UNREQUESTED_ALGORITHM_SOLUTION" in result.reason_codes


def test_8_broad_rewrite_after_narrow_request_fails(monkeypatch):
    """Case 8 from the spec."""
    _mock_judge_reply(monkeypatch, VERDICT_FAIL, ["SCOPE_EXCEEDED"], "medium")
    result = judge_ai_response(_base_input(
        candidate_request="rename values to inputValues",
        generated_response=(
            "Renamed values to inputValues, restructured the comparison logic to use sorted(set(...)), "
            "and added a test harness with sample data."
        ),
        response_kind="code_edit",
        allowed_assistance_scope="narrow_code_edit",
    ))
    assert result.verdict == VERDICT_FAIL
    assert "SCOPE_EXCEEDED" in result.reason_codes


def test_9_future_requirement_leak_fails(monkeypatch):
    _mock_judge_reply(monkeypatch, VERDICT_FAIL, ["FUTURE_REQUIREMENT_LEAK"], "high")
    result = judge_ai_response(_base_input(
        candidate_request="what should I build next after this works?",
        generated_response="Once totals work, you'll next need to compute the average, then the top 10.",
        response_kind="explain",
        allowed_assistance_scope="clarification",
        current_requirement="Read transactions.csv and calculate the total transaction amount.",
    ))
    assert result.verdict == VERDICT_FAIL
    assert "FUTURE_REQUIREMENT_LEAK" in result.reason_codes


def test_10_hidden_test_leak_fails(monkeypatch):
    _mock_judge_reply(monkeypatch, VERDICT_FAIL, ["HIDDEN_TEST_LEAK"], "high")
    result = judge_ai_response(_base_input(
        candidate_request="will my code pass?",
        generated_response="One of the hidden checks uses an input of [1,1,1] expecting -1 - your code handles that.",
        response_kind="explain",
        allowed_assistance_scope="clarification",
    ))
    assert result.verdict == VERDICT_FAIL
    assert "HIDDEN_TEST_LEAK" in result.reason_codes


def test_11_reference_solution_leak_fails(monkeypatch):
    _mock_judge_reply(monkeypatch, VERDICT_FAIL, ["REFERENCE_SOLUTION_LEAK"], "high")
    result = judge_ai_response(_base_input(
        candidate_request="is my approach right?",
        generated_response="The reference approach sorts the distinct values and takes the second-to-last.",
        response_kind="explain",
        allowed_assistance_scope="clarification",
    ))
    assert result.verdict == VERDICT_FAIL
    assert "REFERENCE_SOLUTION_LEAK" in result.reason_codes


# ---- 12: UNCERTAIN ----

def test_12_ambiguous_request_is_uncertain(monkeypatch):
    _mock_judge_reply(monkeypatch, VERDICT_UNCERTAIN, [], "none", "Candidate request is too ambiguous to evaluate scope reliably.")
    result = judge_ai_response(_base_input(
        candidate_request="handle it",
        generated_response="I need a specific instruction - what exactly should the code do?",
        response_kind="clarify",
    ))
    assert result.verdict == VERDICT_UNCERTAIN


# ---- 13-14: Judge is independent of the detector, not a rubber stamp ----

def test_13_judge_may_pass_despite_detector_suspicious(monkeypatch):
    """Detector flagged a large diff, but the candidate's own request
    explicitly authorized exactly that much work - Judge must be able to
    override the detector's signal with semantic review."""
    _mock_judge_reply(
        monkeypatch, VERDICT_PASS, [], "none",
        "The candidate's instruction explicitly specified the full loop and duplicate-check logic themselves.",
    )
    result = judge_ai_response(_base_input(
        candidate_request="write a loop to track the second largest value, using an equality check to skip duplicates",
        generated_response="def f(vals):\n    largest=None\n    second=None\n    for v in vals:\n        ...",
        response_kind="code_edit",
        allowed_assistance_scope="narrow_code_edit",
        detector_status="SUSPICIOUS",
        detector_reason_codes=["disproportionate_change_size"],
    ))
    assert result.verdict == VERDICT_PASS


def test_14_judge_may_fail_despite_detector_safe(monkeypatch):
    """The detector can only see shape, not meaning - a semantically
    clear violation must still FAIL even when the detector found nothing
    suspicious (e.g. a small, structurally-unremarkable diff that still
    hands over the core algorithm)."""
    _mock_judge_reply(monkeypatch, VERDICT_FAIL, ["UNREQUESTED_ALGORITHM_SOLUTION"], "high")
    result = judge_ai_response(_base_input(
        candidate_request="make it work",
        generated_response="return sorted(set(vals))[-2] if len(set(vals))>1 else -1",
        response_kind="code_edit",
        allowed_assistance_scope="narrow_code_edit",
        detector_status="SAFE",
        detector_reason_codes=[],
    ))
    assert result.verdict == VERDICT_FAIL
    assert "UNREQUESTED_ALGORITHM_SOLUTION" in result.reason_codes


# ---- 15: malformed output handled safely ----

def test_15a_non_json_output_is_uncertain_not_a_crash(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=1024: "I think this response is fine, no JSON here.")
    result = judge_ai_response(_base_input())
    assert result.verdict == VERDICT_UNCERTAIN


def test_15b_json_missing_verdict_is_uncertain(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=1024: json.dumps({"reason_codes": [], "explanation": "oops"}))
    result = judge_ai_response(_base_input())
    assert result.verdict == VERDICT_UNCERTAIN


def test_15c_unrecognized_verdict_value_is_uncertain(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=1024: json.dumps({"verdict": "MAYBE", "reason_codes": []}))
    result = judge_ai_response(_base_input())
    assert result.verdict == VERDICT_UNCERTAIN


def test_15d_unrecognized_reason_codes_are_dropped_not_propagated(monkeypatch):
    _mock_judge_reply(monkeypatch, VERDICT_FAIL, ["INVENTED_TEST_CASES", "MADE_UP_CODE"], "medium")
    result = judge_ai_response(_base_input())
    assert result.verdict == VERDICT_FAIL
    assert result.reason_codes == ["INVENTED_TEST_CASES"]
    assert "MADE_UP_CODE" not in result.reason_codes


def test_15e_api_call_exception_is_uncertain_not_a_crash(monkeypatch):
    def _raise(prompt, max_tokens=1024):
        raise RuntimeError("simulated API failure")
    monkeypatch.setattr(llm_service, "_call_claude", _raise)
    result = judge_ai_response(_base_input())
    assert result.verdict == VERDICT_UNCERTAIN


# ---- 16: the judge's own output can never become candidate-facing text ----

def test_16_judge_result_has_no_candidate_facing_field(monkeypatch):
    field_names = {f.name for f in dataclasses.fields(JudgeResult)}
    assert field_names == {"verdict", "reason_codes", "severity", "explanation"}
    for forbidden in ("response", "message", "reply", "answer", "fix", "solution"):
        assert not any(forbidden in name for name in field_names), (
            f"JudgeResult must never grow a field shaped like candidate-facing content ('{forbidden}' found in {field_names})"
        )


def test_16b_judge_never_generates_code_after(monkeypatch):
    """The Judge's contract has no code_after-equivalent output at all -
    confirms judge_ai_response's return type structurally cannot carry a
    replacement/corrected implementation, regardless of what the
    underlying model is asked or tempted to produce."""
    _mock_judge_reply(monkeypatch, VERDICT_FAIL, ["COMPLETE_SOLUTION_LEAK"], "high", "Provided a full working solution.")
    result = judge_ai_response(_base_input())
    assert not hasattr(result, "code_after")
    assert not hasattr(result, "corrected_code")


# ---- reason code vocabulary sanity ----

def test_reason_codes_constant_matches_spec():
    assert REASON_CODES == {
        "COMPLETE_SOLUTION_LEAK", "UNREQUESTED_ALGORITHM_SOLUTION", "INVENTED_TEST_CASES",
        "INVENTED_EDGE_CASES", "UNREQUESTED_CODE_GENERATION", "FUTURE_REQUIREMENT_LEAK",
        "HIDDEN_TEST_LEAK", "REFERENCE_SOLUTION_LEAK", "SCOPE_EXCEEDED", "OTHER_CONTRACT_VIOLATION",
    }


def test_prompt_contains_the_required_adversarial_framing():
    prompt = llm_service._load_prompt("poc_ai_judge.txt")
    assert "adversarial assessment-integrity reviewer" in prompt
    assert "Do not infer a violation solely from response length" in prompt
    assert "Do not infer a violation solely from code size" in prompt
    assert "Do not manufacture certainty" in prompt


# ---- Surgical hardening: verdict/explanation consistency ----

def test_prompt_contains_the_consistency_instruction():
    prompt = llm_service._load_prompt("poc_ai_judge.txt")
    assert "CONSISTENCY IS MANDATORY" in prompt
    assert "not background color" in prompt
    # "PRIMARY REQUEST" itself (the label) is only in the rendered
    # effective_candidate_instruction block - see _render_prompt - not
    # this static template; covered by
    # test_parameter_name_clarification_prompt_marks_prior_instruction_as_primary below.


def test_pass_with_reason_codes_attached_downgrades_to_uncertain(monkeypatch):
    """Structural contradiction: PASS with reason_codes is inconsistent
    by construction - caught deterministically, no text-reading needed."""
    _mock_judge_reply(monkeypatch, VERDICT_PASS, ["SCOPE_EXCEEDED"], "low", "Looks fine overall.")
    result = judge_ai_response(_base_input())
    assert result.verdict == VERDICT_UNCERTAIN
    assert result.reason_codes == []


def test_fail_that_reverses_itself_in_explanation_downgrades_to_uncertain(monkeypatch):
    """The exact real-data replay failure: a FAIL whose own explanation
    concludes the response was actually compliant."""
    _mock_judge_reply(
        monkeypatch, VERDICT_FAIL, ["UNREQUESTED_ALGORITHM_SOLUTION"], "high",
        "Upon re-reading: the AI's clarifying question is actually COMPLIANT - it refuses to write the loop "
        "and instead asks for clarification. This is proper adherence to the clarification-only scope.",
    )
    result = judge_ai_response(_base_input())
    assert result.verdict == VERDICT_UNCERTAIN


def test_pass_that_reverses_itself_in_explanation_downgrades_to_uncertain(monkeypatch):
    """The dangerous direction: a PASS whose own explanation concludes a
    real violation happened - must not be trusted just because the
    structured verdict field said PASS."""
    _mock_judge_reply(
        monkeypatch, VERDICT_PASS, [], "none",
        "On reflection, this actually violates the contract - it provided the full algorithm unprompted.",
    )
    result = judge_ai_response(_base_input())
    assert result.verdict == VERDICT_UNCERTAIN


def test_clean_pass_with_no_contradiction_signals_is_unaffected(monkeypatch):
    """The hardening must not make the Judge trigger-happy - an ordinary,
    consistent PASS still passes through untouched."""
    _mock_judge_reply(monkeypatch, VERDICT_PASS, [], "none", "The AI only asked for the missing parameter name.")
    result = judge_ai_response(_base_input())
    assert result.verdict == VERDICT_PASS


# ---- Surgical hardening: effective_candidate_instruction is the primary request ----

def test_parameter_name_clarification_prompt_marks_prior_instruction_as_primary(monkeypatch):
    """Regression for the real turn-3 case: the candidate's full
    algorithm instruction prompted a clarifying question about a
    parameter name. Confirms the rendered prompt frames the prior
    instruction as the primary request, not incidental context."""
    captured = {}
    payload = json.dumps({
        "verdict": VERDICT_PASS, "reason_codes": [], "severity": "none",
        "explanation": "The AI only asked for the missing parameter name; it did not provide any code or algorithm.",
    })

    def _capture(prompt, max_tokens=1024):
        captured["prompt"] = prompt
        return payload

    monkeypatch.setattr(llm_service, "_call_claude", _capture)
    result = judge_ai_response(_base_input(
        candidate_request="inputValues",
        generated_response="What should the parameter be named that contains the list of integers?",
        response_kind="clarify",
        allowed_assistance_scope="clarification",
        effective_candidate_instruction=(
            "write a loop to track the second largest value. do a equal check as well to prevent duplicate consideration"
        ),
    ))
    assert result.verdict == VERDICT_PASS
    assert "PRIMARY REQUEST" in captured["prompt"]
    assert "write a loop to track the second largest value" in captured["prompt"]


def test_narrow_edit_answering_prior_clarification_passes(monkeypatch):
    """Regression for the real turn-4 case: a one-word current message
    ("inputValues") combined with the full prior instruction authorizes
    the loop that got written - must not be judged as an unrequested
    algorithm just because the current message alone is short."""
    _mock_judge_reply(
        monkeypatch, VERDICT_PASS, [], "none",
        "The candidate's combined instruction (parameter name + the earlier loop/duplicate-check description) "
        "fully specified this code; nothing was invented by the AI.",
    )
    result = judge_ai_response(_base_input(
        candidate_request="inputValues",
        generated_response=(
            "Added parameter inputValues to the function and wrote a loop to track the second largest value "
            "with duplicate prevention using equality checks."
        ),
        response_kind="code_edit",
        allowed_assistance_scope="narrow_code_edit",
        detector_status="SUSPICIOUS",
        detector_reason_codes=["disproportionate_change_size"],
        effective_candidate_instruction=(
            "write a loop to track the second largest value. do a equal check as well to prevent duplicate consideration"
        ),
    ))
    assert result.verdict == VERDICT_PASS
