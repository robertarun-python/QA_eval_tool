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


def test_round3_coding_turn_forces_clarify_when_a_required_category_is_missing(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "code_edit", "response_message": "Added the loop.",
        "code_after": "for x in salaries: print(x)",
        "category_status": {
            "iteration": {"status": "declared", "value": "a for loop over salaries"},
            "comparison": {"status": "not_addressed", "neutral_question": "What should determine a match?"},
        },
    }))
    result = llm_service.round3_coding_turn(
        scenario_description="Find the highest salary", language="python",
        conversation_so_far=[], current_code=None,
        candidate_prompt="loop through the salaries",
        turn_number=2,
        required_constructs=["iteration", "comparison"],
        declared_constructs={},
    )
    # The model tried to hand back code_edit, but "comparison" is still
    # missing - the engine, not the model's self-report, must win.
    assert result["response_kind"] == "clarify"
    assert result["code_after"] is None
    assert "What should determine a match?" in result["response_message"]
    assert result["declared_constructs"]["iteration"] == "a for loop over salaries"


def test_round3_coding_turn_proceeds_once_every_required_category_is_declared(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "code_edit", "response_message": "Added the comparison.",
        "code_after": "if s > highest: highest = s",
        "category_status": {"comparison": {"status": "declared", "value": "greater than the current highest"}},
    }))
    result = llm_service.round3_coding_turn(
        scenario_description="Find the highest salary", language="python",
        conversation_so_far=[], current_code="salaries = [1, 2]",
        candidate_prompt="if greater than the current highest, replace it",
        turn_number=3,
        required_constructs=["iteration", "comparison"],
        declared_constructs={"iteration": "a for loop over salaries"},
    )
    assert result["response_kind"] == "code_edit"
    assert "if s > highest" in result["code_after"]
    assert result["declared_constructs"] == {
        "iteration": "a for loop over salaries",
        "comparison": "greater than the current highest",
    }


def test_round3_coding_turn_ignores_the_checklist_when_none_is_configured(monkeypatch):
    # No required_constructs at all (an old scenario, or a scoped-out
    # problem) - behavior must be identical to before this feature.
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
    assert result["declared_constructs"] == {}


def test_round3_coding_turn_regenerates_once_on_a_vocabulary_leak(monkeypatch):
    calls = []

    def fake_call_claude(prompt, max_tokens=4096):
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps({
                "response_kind": "clarify", "response_message": "...", "code_after": None,
                "category_status": {"iteration": {"status": "not_addressed", "neutral_question": "Should this be a for loop?"}},
            })
        return json.dumps({
            "response_kind": "clarify", "response_message": "...", "code_after": None,
            "category_status": {"iteration": {"status": "not_addressed", "neutral_question": "How will it work through them, one at a time?"}},
        })

    monkeypatch.setattr(llm_service, "_call_claude", fake_call_claude)
    result = llm_service.round3_coding_turn(
        scenario_description="x", language="python", conversation_so_far=[], current_code=None,
        candidate_prompt="I need to process the salaries",
        turn_number=1, required_constructs=["iteration"], declared_constructs={},
    )
    assert len(calls) == 2
    assert "for loop" not in result["response_message"]
    assert "How will it work through them" in result["response_message"]


def test_round3_coding_turn_falls_back_to_template_after_two_leaks(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "clarify", "response_message": "...", "code_after": None,
        "category_status": {"iteration": {"status": "not_addressed", "neutral_question": "Should this use a for loop or a while loop?"}},
    }))
    result = llm_service.round3_coding_turn(
        scenario_description="x", language="python", conversation_so_far=[], current_code=None,
        candidate_prompt="I need to process the salaries",
        turn_number=1, required_constructs=["iteration"], declared_constructs={},
    )
    from app.services import round3_constructs
    assert result["response_message"] == round3_constructs.FALLBACK_QUESTIONS["iteration"]


def test_round3_coding_turn_bundles_multiple_gaps_into_one_clarify(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "clarify", "response_message": "...", "code_after": None,
        "category_status": {
            "iteration": {"status": "attempted_but_vague", "neutral_question": "How will it work through them, one at a time?"},
            "comparison": {"status": "attempted_but_vague", "neutral_question": "What should determine a match?"},
        },
    }))
    result = llm_service.round3_coding_turn(
        scenario_description="x", language="python", conversation_so_far=[], current_code=None,
        candidate_prompt="loop through the salaries and compare each to the current highest",
        turn_number=2, required_constructs=["iteration", "comparison"], declared_constructs={},
    )
    assert result["response_kind"] == "clarify"
    assert "How will it work through them, one at a time?" in result["response_message"]
    assert "What should determine a match?" in result["response_message"]
