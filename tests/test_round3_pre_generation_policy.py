"""
Round 3 - deterministic pre-generation policy hardening (QA validation
finding: Round 3 previously relied on the model to self-classify and
refuse prohibited requests from inside its own prompt). See
round3_policy.py and llm_service.round3_coding_turn's new check at the
top of that function.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest

from app.services import round3_policy, llm_service

PROHIBITED_REQUESTS = [
    "solve this for me",
    "write the complete solution",
    "give me the algorithm",
    "generate test cases",
    "give me edge cases",
    "invent sample data",
    "do the reasoning for me",
    "decide how I should solve this",
]

LEGITIMATE_REQUESTS = [
    "explain my error",
    "explain why my loop fails",
    "explain this concept",
    "rename this variable",
    "run this exact input: [5, 3, 9]",
    "add a parameter called inputValues to the function",
]


# ---- Unit level: round3_policy.is_prohibited ----

@pytest.mark.parametrize("request_text", PROHIBITED_REQUESTS)
def test_prohibited_requests_are_flagged(request_text):
    assert round3_policy.is_prohibited(request_text) is True


@pytest.mark.parametrize("request_text", LEGITIMATE_REQUESTS)
def test_legitimate_requests_are_not_flagged(request_text):
    assert round3_policy.is_prohibited(request_text) is False


# ---- Integration level: llm_service.round3_coding_turn - ZERO Generator
# (LLM) calls for a prohibited request ----

@pytest.mark.parametrize("request_text", PROHIBITED_REQUESTS)
def test_prohibited_request_causes_zero_generator_calls(monkeypatch, request_text):
    call_count = {"n": 0}

    def _fail_if_called(prompt, max_tokens=2048):
        call_count["n"] += 1
        return '{"response_kind": "refuse", "response_message": "x", "code_after": null, "category_status": {}}'

    monkeypatch.setattr(llm_service, "_call_claude", _fail_if_called)
    result = llm_service.round3_coding_turn(
        scenario_description="Find the second-largest distinct value in a list.", language="python",
        conversation_so_far=[], current_code=None,
        candidate_prompt=request_text, turn_number=1,
    )
    assert call_count["n"] == 0, "policy refusal must short-circuit before any LLM call"
    assert result["response_kind"] == "refuse"
    assert result["response_message"] == round3_policy.REFUSAL_MESSAGE
    assert result["code_after"] is None


@pytest.mark.parametrize("request_text", LEGITIMATE_REQUESTS)
def test_legitimate_request_still_reaches_the_generator(monkeypatch, request_text):
    call_count = {"n": 0}

    def _fake_call(prompt, max_tokens=2048):
        call_count["n"] += 1
        return '{"response_kind": "explain", "response_message": "ok", "code_after": null, "category_status": {}}'

    monkeypatch.setattr(llm_service, "_call_claude", _fake_call)
    llm_service.round3_coding_turn(
        scenario_description="Find the second-largest distinct value in a list.", language="python",
        conversation_so_far=[], current_code=None,
        candidate_prompt=request_text, turn_number=1,
    )
    assert call_count["n"] == 1, f"expected {request_text!r} to reach the generator"
