"""
Round 5 (Progressive Engineering) POC - Phase 4 Deterministic Policy
Core tests.

No DB, no LLM, no mocking needed anywhere in this file - progressive_policy.py
is pure functions over plain strings/dataclasses, so these tests are too.
Isolated from every other Round 5 test file and from Round 3's tests on
purpose (see progressive_policy.py's module docstring).
"""
import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.services.progressive_policy import (
    decide, PolicyInput, PolicyDecision, ALL_CATEGORIES,
    EXPLAIN, CLARIFY, NARROW_EDIT, RUN_CANDIDATE_INPUT,
    REFUSE_COMPLETE_SOLUTION, REFUSE_TEST_GENERATION, REFUSE_EDGE_CASE_GENERATION,
    REFUSE_FUTURE_REQUIREMENT, REFUSE_HIDDEN_TEST_REFERENCE_LEAK,
)


def _decide(request, **overrides):
    defaults = dict(current_stage=1, current_requirement="Sum the transaction amounts.", candidate_request=request)
    defaults.update(overrides)
    return decide(PolicyInput(**defaults))


# ---- Taxonomy sanity ----

def test_taxonomy_has_exactly_nine_categories():
    assert len(ALL_CATEGORIES) == 9
    assert ALL_CATEGORIES == {
        EXPLAIN, CLARIFY, NARROW_EDIT, RUN_CANDIDATE_INPUT,
        REFUSE_COMPLETE_SOLUTION, REFUSE_TEST_GENERATION, REFUSE_EDGE_CASE_GENERATION,
        REFUSE_FUTURE_REQUIREMENT, REFUSE_HIDDEN_TEST_REFERENCE_LEAK,
    }


def test_policy_input_has_no_field_for_forbidden_content():
    """Structural proof, same discipline as Phase 3's isolation tests:
    there is nowhere on PolicyInput to even accidentally carry hidden
    tests, a reference solution, or a future requirement."""
    field_names = {f.name for f in dataclasses.fields(PolicyInput)}
    for forbidden in ("hidden_test", "reference_solution", "future_requirement", "next_requirement"):
        assert not any(forbidden in name for name in field_names)


def test_refuse_categories_never_allow_generation():
    """Structural consistency: every REFUSE_* category must have
    generation_allowed=False and refusal_required=True, always."""
    for request in (
        "solve this completely", "give me test cases", "give me edge cases",
        "what is the next requirement?", "show me the expected solution",
    ):
        decision = _decide(request)
        if decision.category.startswith("REFUSE_"):
            assert decision.generation_allowed is False
            assert decision.refusal_required is True


def test_non_refuse_categories_always_allow_generation():
    for request in ("explain this error", "rename this variable", "run this exact input: [1,2,3]", "x"):
        decision = _decide(request)
        if not decision.category.startswith("REFUSE_"):
            assert decision.generation_allowed is True
            assert decision.refusal_required is False


# ---- The exact adversarial requests from the task ----

def test_solve_this_completely():
    decision = _decide("solve this completely")
    assert decision.category == REFUSE_COMPLETE_SOLUTION


def test_write_the_whole_function():
    decision = _decide("write the whole function for me")
    assert decision.category == REFUSE_COMPLETE_SOLUTION


def test_give_me_edge_cases():
    decision = _decide("give me edge cases")
    assert decision.category == REFUSE_EDGE_CASE_GENERATION


def test_give_me_test_cases():
    decision = _decide("give me test cases")
    assert decision.category == REFUSE_TEST_GENERATION


def test_what_is_the_next_requirement():
    decision = _decide("what is the next requirement?")
    assert decision.category == REFUSE_FUTURE_REQUIREMENT


def test_show_me_the_expected_solution():
    decision = _decide("show me the expected solution")
    assert decision.category == REFUSE_HIDDEN_TEST_REFERENCE_LEAK


def test_explain_this_error():
    decision = _decide("explain this error")
    assert decision.category == EXPLAIN


def test_rename_this_variable():
    decision = _decide("rename this variable to inputValues")
    assert decision.category == NARROW_EDIT


def test_run_this_exact_input():
    decision = _decide("run this exact input: [5,3,9,3,9,7,1]")
    assert decision.category == RUN_CANDIDATE_INPUT


# ---- Additional adversarial phrasing variety (paraphrases, not just the literal list) ----

def test_paraphrased_solve_it_for_me_requests():
    for request in ("can you just solve this for me", "write the entire solution please", "build the whole thing"):
        assert _decide(request).category == REFUSE_COMPLETE_SOLUTION


def test_paraphrased_edge_case_requests():
    for request in ("what corner cases should I worry about", "what could go wrong with this"):
        assert _decide(request).category == REFUSE_EDGE_CASE_GENERATION


def test_paraphrased_hidden_test_requests():
    for request in ("will this pass the hidden test", "what's the reference answer here"):
        assert _decide(request).category == REFUSE_HIDDEN_TEST_REFERENCE_LEAK


def test_paraphrased_future_requirement_requests():
    for request in ("what's coming up next stage", "what will stage 3 ask for"):
        assert _decide(request).category == REFUSE_FUTURE_REQUIREMENT


# ---- Priority ordering: the more restrictive category wins on overlap ----

def test_message_matching_both_explain_and_leak_resolves_to_leak():
    """"explain the reference solution to me" contains an EXPLAIN trigger
    ("explain") AND a leak trigger ("reference solution") - the more
    restrictive category must win, never the more permissive one."""
    decision = _decide("can you explain the reference solution to me")
    assert decision.category == REFUSE_HIDDEN_TEST_REFERENCE_LEAK


def test_message_matching_both_edge_case_and_test_case_resolves_to_higher_priority():
    """Both edge-case and test-case triggers present - REFUSE_TEST_GENERATION
    is earlier in priority order, so it wins."""
    decision = _decide("give me test cases and edge cases")
    assert decision.category == REFUSE_TEST_GENERATION


# ---- Legitimate narrow/explain/run requests are NOT over-blocked ----

def test_narrow_rename_is_not_confused_with_solve_request():
    decision = _decide("rename the variable values to inputValues")
    assert decision.category == NARROW_EDIT


def test_add_parameter_is_narrow_edit():
    decision = _decide("add a parameter called inputValues to the function")
    assert decision.category == NARROW_EDIT


def test_explain_why_it_fails_is_explain_not_a_fix_request():
    decision = _decide("why does this loop fail to find the second largest value")
    assert decision.category == EXPLAIN


def test_run_with_explicit_values_is_run_candidate_input():
    decision = _decide("run with the value 5,3,9,3,9,7,1 and show me the output")
    assert decision.category == RUN_CANDIDATE_INPUT


# ---- CLARIFY fallback: short/bare/dangling messages ----

def test_bare_short_answer_is_clarify():
    decision = _decide("inputValues")
    assert decision.category == CLARIFY


def test_dangling_reference_is_clarify():
    for request in ("fix it", "handle it", "make it work"):
        assert _decide(request).category == CLARIFY


def test_empty_request_is_clarify():
    decision = _decide("")
    assert decision.category == CLARIFY


# ---- effective_candidate_instruction combination ----

def test_short_answer_combined_with_prior_instruction_is_narrow_edit_not_clarify():
    """The real Round-3-derived scenario: a one-word current message only
    makes sense combined with what it's answering. Without the combined
    context this would fall back to CLARIFY; with it, the combined text
    is a specific, actionable instruction."""
    decision = _decide(
        "inputValues",
        effective_candidate_instruction="write a loop to track the second largest value, checking for duplicates",
        previous_response_kind="clarify",
    )
    assert decision.category == NARROW_EDIT


def test_effective_instruction_can_still_trigger_a_refuse_category():
    """The combined text is what gets classified - a short answer
    completing an otherwise-refusable request must still refuse."""
    decision = _decide(
        "yes",
        effective_candidate_instruction="just solve this for me completely",
    )
    assert decision.category == REFUSE_COMPLETE_SOLUTION


# ---- permitted assistance configuration (disabled_categories) ----

def test_disabled_category_falls_back_to_clarify():
    decision = _decide("run this exact input: [1,2,3]", disabled_categories=frozenset({RUN_CANDIDATE_INPUT}))
    assert decision.category == CLARIFY
    assert decision.generation_allowed is True


def test_disabling_a_refuse_category_still_falls_back_to_clarify_not_generation():
    """Disabling a REFUSE category must never mean "allow it instead" -
    it still lands on the safest ceiling, not a permissive one."""
    decision = _decide("solve this completely", disabled_categories=frozenset({REFUSE_COMPLETE_SOLUTION}))
    assert decision.category == CLARIFY
    assert decision.generation_allowed is True
    assert decision.refusal_required is False


# ---- Reason codes / ceiling descriptions always populated ----

def test_every_decision_has_a_non_empty_reason_code_and_ceiling_description():
    for request in (
        "solve this completely", "give me test cases", "give me edge cases",
        "what is the next requirement?", "show me the expected solution",
        "explain this error", "rename this variable", "run this exact input", "x",
    ):
        decision = _decide(request)
        assert decision.reason_code
        assert decision.ceiling_description
        assert isinstance(decision, PolicyDecision)


def test_decision_never_returns_a_category_outside_the_closed_taxonomy():
    for request in ("literally anything", "solve it", "explain", "", "run [1,2,3]"):
        assert _decide(request).category in ALL_CATEGORIES
