"""
llm_service.py's Round 3 (AI-prompted coding) functions - see
docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md. These tests
monkeypatch _call_claude (the one function that actually calls the Claude
API), same isolation pattern every other llm_service test in this repo
uses.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest
from pydantic import ValidationError

from app.services import llm_service


def test_generate_round3_reference_returns_test_cases_and_expected_approach(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "test_cases": [{"input": "2 3", "expected_output": "5", "description": "basic sum"}],
        "expected_approach": "Read two integers and add them directly.",
    }))
    result = llm_service.generate_round3_reference(scenario_description="Add two numbers", experience_band="0-7")
    assert result["test_cases"][0]["expected_output"] == "5"
    assert "add them" in result["expected_approach"]


def test_generate_round3_reference_rejects_malformed_shape(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({"oops": True}))
    with pytest.raises(ValueError):
        llm_service.generate_round3_reference(scenario_description="x", experience_band="0-7")


def test_round3_coding_turn_classifies_code_edit(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "code_edit", "response_message": "Added the two variables and printed their sum.",
        "code_after": "a = int(input())\nb = int(input())\nprint(a + b)",
    }))
    result = llm_service.round3_coding_turn(
        scenario_description="Add two numbers", language="python",
        conversation_so_far=[], current_code=None,
        candidate_prompt="I need two int variables read from stdin, then print their sum",
        turn_number=1,
    )
    assert result["response_kind"] == "code_edit"
    assert "a + b" in result["code_after"]


def test_round3_coding_turn_classifies_clarify(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "clarify", "response_message": "What data type should the variable be?",
        "code_after": None,
    }))
    result = llm_service.round3_coding_turn(
        scenario_description="Add two numbers", language="python",
        conversation_so_far=[], current_code=None,
        candidate_prompt="I need a variable", turn_number=1,
    )
    assert result["response_kind"] == "clarify"
    assert result["code_after"] is None


def test_round3_coding_turn_rejects_code_edit_without_code(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "code_edit", "response_message": "done", "code_after": None,
    }))
    with pytest.raises(ValueError):
        llm_service.round3_coding_turn(
            scenario_description="x", language="python", conversation_so_far=[],
            current_code=None, candidate_prompt="do it", turn_number=1,
        )


def test_round3_coding_turn_response_accepts_category_status():
    from app.schemas import Round3CodingTurnResponse
    parsed = Round3CodingTurnResponse.model_validate({
        "response_kind": "clarify", "response_message": "...", "code_after": None,
        "category_status": {
            "iteration": {"status": "attempted_but_vague", "neutral_question": "How will it work through them?"},
            "collection": {"status": "declared", "value": "a list"},
        },
    })
    assert parsed.category_status["collection"].value == "a list"
    assert parsed.category_status["iteration"].status == "attempted_but_vague"


def test_round3_coding_turn_response_defaults_category_status_to_empty():
    from app.schemas import Round3CodingTurnResponse
    parsed = Round3CodingTurnResponse.model_validate({
        "response_kind": "code_edit", "response_message": "done", "code_after": "x = 1",
    })
    assert parsed.category_status == {}


def test_category_status_entry_requires_value_when_declared():
    from app.schemas import Round3CodingTurnResponse
    with pytest.raises(ValidationError):
        Round3CodingTurnResponse.model_validate({
            "response_kind": "clarify", "response_message": "...", "code_after": None,
            "category_status": {"collection": {"status": "declared"}},
        })


def test_category_status_entry_requires_neutral_question_when_not_declared():
    from app.schemas import Round3CodingTurnResponse
    with pytest.raises(ValidationError):
        Round3CodingTurnResponse.model_validate({
            "response_kind": "clarify", "response_message": "...", "code_after": None,
            "category_status": {"collection": {"status": "not_addressed"}},
        })


def test_score_round3_coding_includes_provenance(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "correctness_score": 100, "precision_score": 80, "efficiency_score": 70,
        "independent_judgment_score": 90, "final_score": 85,
        "misses": [], "guardrail_violations": [], "feedback_text": "Solid work.",
    }))
    result = llm_service.score_round3_coding(
        scenario_description="Add two numbers", expected_approach="read two ints, add them",
        conversation_so_far=[], test_results=[{"input": "2 3", "expected_output": "5", "actual_output": "5", "passed": True}],
    )
    assert result["final_score"] == 85
    assert "_provenance" in result
    assert result["_provenance"]["prompt_file"] == "round3_coding_scoring.txt"


def test_generate_round3_reference_returns_required_constructs(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "test_cases": [{"input": "2 3", "expected_output": "5", "description": "basic sum"}],
        "expected_approach": "Read two integers and add them directly.",
        "required_constructs": ["variable", "input", "output"],
    }))
    result = llm_service.generate_round3_reference(scenario_description="Add two numbers", experience_band="0-7")
    assert result["required_constructs"] == ["variable", "input", "output"]


def test_generate_round3_reference_rejects_unknown_required_construct(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "test_cases": [{"input": "2 3", "expected_output": "5", "description": "basic sum"}],
        "expected_approach": "...",
        "required_constructs": ["not_a_real_category"],
    }))
    with pytest.raises(ValueError):
        llm_service.generate_round3_reference(scenario_description="x", experience_band="0-7")
