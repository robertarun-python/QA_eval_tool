"""
Controlled AI Coding Assessment POC - Phase 1 detector tests.

Standalone: no app/client/DB fixtures, no LLM calls - this module is
pure functions over plain strings, so the tests are too. Isolated from
Round 3's own test_round3.py on purpose (see poc_ai_output_detector.py's
module docstring on why this is a separate, not shared, component).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.services.poc_ai_output_detector import (
    inspect_response, VERDICT_SAFE, VERDICT_SUSPICIOUS, VERDICT_BLOCK,
)


def test_narrow_code_edit_matching_instruction_is_safe():
    result = inspect_response(
        candidate_instruction="add a parameter called inputValues to the function",
        claims_no_code=False,
        response_text="Added the inputValues parameter.",
        previous_code="def f():\n    pass",
        new_code="def f(inputValues):\n    pass",
    )
    assert result.verdict == VERDICT_SAFE
    assert result.findings == []


def test_plain_explanation_with_no_code_change_is_safe():
    result = inspect_response(
        candidate_instruction="why does this loop fail?",
        claims_no_code=True,
        response_text="The loop stops one index early because range(len(vals) - 1) skips the last element.",
        previous_code="def f(vals):\n    for i in range(len(vals) - 1):\n        pass",
        new_code=None,
    )
    assert result.verdict == VERDICT_SAFE


def test_contract_violation_is_blocked():
    """Response claims to be pure explanation but the code changed anyway."""
    result = inspect_response(
        candidate_instruction="explain why this fails",
        claims_no_code=True,
        response_text="Here's the fix.",
        previous_code="def f(vals):\n    return vals[0]",
        new_code="def f(vals):\n    return max(vals)",
    )
    assert result.verdict == VERDICT_BLOCK
    assert result.findings[0].check == "contract_consistency"


def test_code_created_from_nothing_while_claiming_no_code_is_blocked():
    result = inspect_response(
        candidate_instruction="what does append do?",
        claims_no_code=True,
        response_text="It's a list method.",
        previous_code=None,
        new_code="def solve():\n    pass",
    )
    assert result.verdict == VERDICT_BLOCK


def test_forbidden_snippet_leak_is_blocked_and_does_not_echo_the_secret():
    result = inspect_response(
        candidate_instruction="add a parameter",
        claims_no_code=False,
        response_text="Added it.",
        previous_code="def f():\n    pass",
        new_code="def f(inputValues):\n    largest = max(inputValues)\n    return largest",
        forbidden_snippets=[("reference_solution", "largest = max(inputValues)")],
    )
    assert result.verdict == VERDICT_BLOCK
    assert result.findings[0].check == "forbidden_snippet_leak"
    assert "largest = max" not in result.findings[0].detail
    assert "reference_solution" in result.findings[0].detail


def test_forbidden_snippet_match_is_whitespace_and_case_insensitive():
    result = inspect_response(
        candidate_instruction="run this",
        claims_no_code=False,
        response_text="ok",
        previous_code=None,
        new_code="def F():\n    Return   42",
        forbidden_snippets=[("hidden_test", "return 42")],
    )
    assert result.verdict == VERDICT_BLOCK


def test_no_forbidden_match_stays_safe():
    result = inspect_response(
        candidate_instruction="add a parameter",
        claims_no_code=False,
        response_text="Added it.",
        previous_code="def f():\n    pass",
        new_code="def f(inputValues):\n    pass",
        forbidden_snippets=[("reference_solution", "return sorted(values)[-2]")],
    )
    assert result.verdict == VERDICT_SAFE


def test_disproportionate_change_size_is_suspicious_not_blocked():
    """The CTO's original example: a large chunk of algorithm written
    from a short instruction. Flagged for review, not auto-rejected -
    per explicit product direction, only the Judge (Phase 2) gets to
    decide whether it's actually a leak."""
    result = inspect_response(
        candidate_instruction="track the second largest value",
        claims_no_code=False,
        response_text="Added the loop.",
        previous_code="def f(vals):\n    pass",
        new_code=(
            "def f(vals):\n"
            "    largest = None\n"
            "    second = None\n"
            "    for v in vals:\n"
            "        if largest is None or v > largest:\n"
            "            second = largest\n"
            "            largest = v\n"
            "        elif v != largest and (second is None or v > second):\n"
            "            second = v\n"
            "    return second\n"
        ),
    )
    assert result.verdict == VERDICT_SUSPICIOUS
    assert result.findings[0].check == "disproportionate_change_size"


def test_small_diff_for_a_short_instruction_is_not_flagged():
    result = inspect_response(
        candidate_instruction="rename the variable to inputValues",
        claims_no_code=False,
        response_text="Renamed it.",
        previous_code="def f(vals):\n    return vals",
        new_code="def f(inputValues):\n    return inputValues",
    )
    assert result.verdict == VERDICT_SAFE


def test_code_shaped_content_in_a_no_code_response_is_suspicious():
    result = inspect_response(
        candidate_instruction="explain why this fails",
        claims_no_code=True,
        response_text="Here's how to fix it:\n```\ndef f(vals):\n    return max(vals)\n```",
        previous_code="def f(vals):\n    return vals[0]",
        new_code=None,
    )
    assert result.verdict == VERDICT_SUSPICIOUS
    assert result.findings[0].check == "code_shaped_content_in_explanation"


def test_literal_data_list_in_explanation_is_suspicious():
    """The exact reported failure shape: inventing sample values inside
    a text response instead of asking the candidate for them."""
    result = inspect_response(
        candidate_instruction="try various combinations and corner cases",
        claims_no_code=True,
        response_text="I tried it with [3, 5, 5, 1, 9, 9] and it correctly returned 5.",
        previous_code="def f(vals):\n    pass",
        new_code=None,
    )
    assert result.verdict == VERDICT_SUSPICIOUS
    assert result.findings[0].check == "literal_data_in_explanation"


def test_numeric_list_in_a_real_code_edit_is_not_flagged():
    """The heuristic only applies to no-code responses - a candidate
    explicitly supplying literal test values to run is normal, allowed
    behavior for a real code_edit-shaped turn."""
    result = inspect_response(
        candidate_instruction="test it with [3, 5, 5, 1, 9, 9]",
        claims_no_code=False,
        response_text="Ran it with [3, 5, 5, 1, 9, 9].",
        previous_code="def f(vals):\n    pass",
        new_code="def f(vals):\n    return max(vals)",
    )
    assert result.verdict == VERDICT_SAFE


def test_multiple_findings_take_the_highest_severity():
    """A response that's both a contract violation (BLOCK) and would
    otherwise also trip the code-shape suspicion signal (SUSPICIOUS) -
    the aggregate verdict must be BLOCK, and both findings are reported."""
    result = inspect_response(
        candidate_instruction="explain this",
        claims_no_code=True,
        response_text="Here:\n```\ndef f(vals):\n    return max(vals)\n```",
        previous_code="def f(vals):\n    return vals[0]",
        new_code="def f(vals):\n    return max(vals)",
    )
    assert result.verdict == VERDICT_BLOCK
    checks_fired = {f.check for f in result.findings}
    assert "contract_consistency" in checks_fired
    assert "code_shaped_content_in_explanation" in checks_fired


def test_result_is_pure_no_side_effects_or_external_calls():
    """Sanity check on the "standalone, deterministic" claim itself:
    calling it twice with identical inputs gives identical output."""
    kwargs = dict(
        candidate_instruction="add a parameter",
        claims_no_code=False,
        response_text="Added it.",
        previous_code="def f():\n    pass",
        new_code="def f(inputValues):\n    pass",
    )
    first = inspect_response(**kwargs)
    second = inspect_response(**kwargs)
    assert first.verdict == second.verdict == VERDICT_SAFE
