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


def test_round3_coding_turn_retry_only_touches_the_leaking_category(monkeypatch):
    # Two categories are asked about together; only "iteration" leaks.
    # The retry's answer must replace ONLY iteration's question - "comparison"'s
    # first-pass question must survive untouched, not be overwritten wholesale
    # by whatever the retry call returns for it.
    calls = []

    def fake_call_claude(prompt, max_tokens=4096):
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps({
                "response_kind": "clarify", "response_message": "...", "code_after": None,
                "category_status": {
                    "iteration": {"status": "attempted_but_vague", "neutral_question": "Should this be a for loop?"},
                    "comparison": {"status": "attempted_but_vague", "neutral_question": "What decides a match?"},
                },
            })
        # The retry call - only asked to rephrase, but even if it also
        # returns something different for "comparison", that must be ignored.
        return json.dumps({
            "response_kind": "clarify", "response_message": "...", "code_after": None,
            "category_status": {
                "iteration": {"status": "attempted_but_vague", "neutral_question": "How will it work through them, one at a time?"},
                "comparison": {"status": "attempted_but_vague", "neutral_question": "A DIFFERENT, unrelated question that must not appear."},
            },
        })

    monkeypatch.setattr(llm_service, "_call_claude", fake_call_claude)
    result = llm_service.round3_coding_turn(
        scenario_description="x", language="python", conversation_so_far=[], current_code=None,
        candidate_prompt="loop through the salaries and compare each to the current highest",
        turn_number=2, required_constructs=["iteration", "comparison"], declared_constructs={},
    )
    assert len(calls) == 2
    assert "How will it work through them, one at a time?" in result["response_message"]
    assert "What decides a match?" in result["response_message"]
    assert "A DIFFERENT, unrelated question" not in result["response_message"]


def test_round3_coding_turn_falls_back_when_category_status_is_missing_entirely(monkeypatch):
    # Schema-legal but unhelpful: response_kind is "clarify" and
    # category_status is entirely absent (defaults to {}). The engine
    # still says "iteration" needs asking about - this must fall back to
    # the hardcoded template, never crash on a missing dict key.
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "clarify", "response_message": "...", "code_after": None,
    }))
    result = llm_service.round3_coding_turn(
        scenario_description="x", language="python", conversation_so_far=[], current_code=None,
        candidate_prompt="I need to process the salaries",
        turn_number=1, required_constructs=["iteration"], declared_constructs={},
    )
    from app.services import round3_constructs
    assert result["response_kind"] == "clarify"
    assert result["response_message"] == round3_constructs.FALLBACK_QUESTIONS["iteration"]


def test_round3_coding_turn_proceed_passes_through_a_genuine_non_construct_clarify(monkeypatch):
    # One required category ("comparison") is still open coming into
    # this turn, and this turn's instruction genuinely resolves it (the
    # mocked category_status marks it "declared") - so the engine
    # computes final_kind == "proceed". But the model's OWN
    # classification for this turn is "clarify" (an ordinary missing
    # name, unrelated to constructs). The engine's "proceed" must never
    # be rewritten into a code_edit the model didn't actually produce
    # code for - this must genuinely exercise the `proceed` branch, not
    # the earlier `not open_categories` short-circuit (declared_constructs
    # is deliberately NOT yet complete on entry, unlike an earlier,
    # mistaken version of this test).
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "clarify", "response_message": "What should I name the running total?", "code_after": None,
        "category_status": {"comparison": {"status": "declared", "value": "greater than the current highest"}},
    }))
    result = llm_service.round3_coding_turn(
        scenario_description="x", language="python", conversation_so_far=[], current_code="salaries = [1, 2]",
        candidate_prompt="keep a running total, and compare each to the current highest as you go",
        turn_number=4, required_constructs=["iteration", "comparison"], declared_constructs={"iteration": "a for loop"},
    )
    assert result["response_kind"] == "clarify"
    assert result["code_after"] is None
    assert result["response_message"] == "What should I name the running total?"
    assert result["declared_constructs"] == {"iteration": "a for loop", "comparison": "greater than the current highest"}


def test_round3_coding_turn_prompt_keys_code_edit_off_this_turns_classification():
    # Regression guard for a real bug: the prompt must decide code_edit
    # eligibility from THIS turn's own Construct classification, not
    # just the pre-turn checklist snapshot - otherwise the model is
    # instructed to force "clarify" on the very turn that resolves the
    # last open category, and that forced clarify has no leak-check
    # applied to it (the leak-check only runs on the engine's own
    # clarify branch, never on a passthrough). No LLM call here - this
    # reads the prompt file's own text.
    from app.services.llm_service import _load_prompt
    prompt_text = _load_prompt("round3_coding_turn.txt")
    assert "per your own Construct classification below" in prompt_text
    assert 'classified "declared" by THIS instruction' in prompt_text
    assert "explicitly override one of these, classify that category as \"declared\"" in prompt_text


def test_round3_coding_turn_response_accepts_direct_edit():
    from app.schemas import Round3CodingTurnResponse
    parsed = Round3CodingTurnResponse.model_validate({
        "response_kind": "direct_edit", "response_message": "No syntax issues found.",
        "code_after": "for x in range(3):\n    print(x)",
    })
    assert parsed.response_kind == "direct_edit"
    assert parsed.code_after is not None


def test_round3_coding_turn_response_requires_code_after_for_direct_edit():
    from app.schemas import Round3CodingTurnResponse
    with pytest.raises(ValidationError):
        Round3CodingTurnResponse.model_validate({
            "response_kind": "direct_edit", "response_message": "...", "code_after": None,
        })


def test_round3_direct_edit_create_rejects_empty_code():
    from app.schemas import Round3DirectEditCreate
    with pytest.raises(ValidationError):
        Round3DirectEditCreate.model_validate({"code": ""})


def test_round3_direct_edit_create_accepts_code():
    from app.schemas import Round3DirectEditCreate
    parsed = Round3DirectEditCreate.model_validate({"code": "print('hi')"})
    assert parsed.code == "print('hi')"


def test_round3_syntax_fix_returns_code_unchanged_when_already_clean(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "direct_edit", "response_message": "No syntax issues found.",
        "code_after": "for x in range(3):\n    print(x)",
        "category_status": {"iteration": {"status": "declared", "value": "a for loop over range(3)"}},
    }))
    result = llm_service.round3_syntax_fix(
        code="for x in range(3):\n    print(x)", language="python",
        required_constructs=["iteration"], declared_constructs={},
    )
    assert result["code_after"] == "for x in range(3):\n    print(x)"
    assert result["declared_constructs"] == {"iteration": "a for loop over range(3)"}


def test_round3_syntax_fix_fixes_a_genuine_syntax_error(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "direct_edit", "response_message": "Added the missing colon.",
        "code_after": "for x in range(3):\n    print(x)",
        "category_status": {"iteration": {"status": "declared", "value": "a for loop over range(3)"}},
    }))
    result = llm_service.round3_syntax_fix(
        code="for x in range(3)\n    print(x)", language="python",
        required_constructs=["iteration"], declared_constructs={},
    )
    assert "for x in range(3):" in result["code_after"]


def test_round3_syntax_fix_leaves_unfixable_code_untouched(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "direct_edit",
        "response_message": "I couldn't confidently identify a fix here - try running it to see the actual error, or fix it yourself and save again.",
        "code_after": "for x in range(3)\n    prin(x)\n  }",
        "category_status": {},
    }))
    result = llm_service.round3_syntax_fix(
        code="for x in range(3)\n    prin(x)\n  }", language="python",
        required_constructs=[], declared_constructs={},
    )
    assert result["code_after"] == "for x in range(3)\n    prin(x)\n  }"
    assert "couldn't confidently identify a fix" in result["response_message"]


def test_round3_syntax_fix_replaces_a_stale_declared_value_from_the_code(monkeypatch):
    # "iteration" was declared "a for loop" from an earlier instruction-
    # based turn, but the pasted code actually uses a while loop - the
    # code is authoritative, so the stale value must be overwritten.
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "direct_edit", "response_message": "No syntax issues found.",
        "code_after": "i = 0\nwhile i < 3:\n    print(i)\n    i += 1",
        "category_status": {"iteration": {"status": "declared", "value": "a while loop"}},
    }))
    result = llm_service.round3_syntax_fix(
        code="i = 0\nwhile i < 3:\n    print(i)\n    i += 1", language="python",
        required_constructs=["iteration"], declared_constructs={"iteration": "a for loop"},
    )
    assert result["declared_constructs"]["iteration"] == "a while loop"


def test_round3_syntax_fix_classifies_against_every_required_category_not_just_open_ones(monkeypatch):
    captured = {}

    def fake_call_claude(prompt, max_tokens=4096):
        captured["prompt"] = prompt
        return json.dumps({
            "response_kind": "direct_edit", "response_message": "No syntax issues found.",
            "code_after": "x = 1",
            "category_status": {},
        })

    monkeypatch.setattr(llm_service, "_call_claude", fake_call_claude)
    llm_service.round3_syntax_fix(
        code="x = 1", language="python",
        required_constructs=["iteration", "comparison"], declared_constructs={"iteration": "a for loop"},
    )
    # Both categories appear in the SERIALIZED LIST passed to the prompt,
    # including the already-declared one - the code path classifies
    # everything fresh, it never trusts a pre-narrowed "open" list the
    # way the instruction path does. Checking the exact serialized list
    # (not just substring presence) matters here: the prompt's own fixed
    # instructional text happens to contain the word "comparison"
    # already, so a bare `"comparison" in captured["prompt"]` would pass
    # even if the category were silently dropped from classification.
    assert json.dumps(["iteration", "comparison"]) in captured["prompt"]


def test_round3_syntax_fix_never_leaks_a_question_or_loses_code_on_model_drift(monkeypatch):
    # The shared schema legally accepts "clarify"/"refuse"/"code_edit"
    # too, even though this prompt only ever describes "direct_edit". If
    # the model ever drifts to one of those, this path must never show
    # the candidate a leaked clarifying question, and must never lose
    # their code - both the question-shaped response_message AND a null
    # code_after are schema-legal for "clarify", and neither is
    # acceptable here.
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "clarify",
        "response_message": "What should happen when the list is empty?",
        "code_after": None,
        "category_status": {"iteration": {"status": "not_addressed", "neutral_question": "How should the program work through them?"}},
    }))
    result = llm_service.round3_syntax_fix(
        code="for x in range(3)\n    print(x)", language="python",
        required_constructs=["iteration"], declared_constructs={"iteration": "a for loop"},
    )
    assert result["code_after"] == "for x in range(3)\n    print(x)"
    assert "empty" not in result["response_message"]
    assert result["declared_constructs"] == {"iteration": "a for loop"}


def test_round3_syntax_fix_scrubs_a_response_message_that_leaks_forbidden_vocab(monkeypatch):
    # The freeform response_message on this path isn't run through the
    # instruction path's regenerate-then-fallback machinery - it's just
    # prose describing what the model fixed. But it's still candidate-
    # facing, so it must never leak a required category's forbidden
    # vocabulary (here, "loop" for "iteration" - see
    # round3_constructs.GENERIC_VOCAB) any more than a clarifying
    # question would.
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "direct_edit",
        "response_message": "Fixed a missing colon in your for loop.",
        "code_after": "for x in range(3):\n    print(x)",
        "category_status": {"iteration": {"status": "declared", "value": "a for loop over range(3)"}},
    }))
    result = llm_service.round3_syntax_fix(
        code="for x in range(3)\n    print(x)", language="python",
        required_constructs=["iteration"], declared_constructs={},
    )
    assert "loop" not in result["response_message"]
    assert result["response_message"] == "Your code has been checked - see the updated version below."
    # Code and declared constructs still pass through unaffected.
    assert result["code_after"] == "for x in range(3):\n    print(x)"
    assert result["declared_constructs"] == {"iteration": "a for loop over range(3)"}


def test_round3_syntax_fix_passes_through_a_clean_response_message_unchanged(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "direct_edit",
        "response_message": "Added the missing colon.",
        "code_after": "for x in range(3):\n    print(x)",
        "category_status": {"iteration": {"status": "declared", "value": "a for loop over range(3)"}},
    }))
    result = llm_service.round3_syntax_fix(
        code="for x in range(3)\n    print(x)", language="python",
        required_constructs=["iteration"], declared_constructs={},
    )
    assert result["response_message"] == "Added the missing colon."
