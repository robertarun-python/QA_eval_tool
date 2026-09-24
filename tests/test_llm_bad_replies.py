"""
Every AI call that expects JSON, fed the replies models really send when
they go wrong. Offline: _call_claude is replaced, nothing reaches the API.

Why this file exists: the other test files fake the model with clean,
well-formed replies, so they prove the code works when the AI is perfect -
the one case that never fails. Live bugs came from the other cases (a
reply cut off, invalid JSON, a missing field). For each call site, a bad
reply must end in ONE of two ways:
  - a clean, explained error (ValueError / RuntimeError family) - the
    router turns it into a visible "trouble responding" 502, background
    scoring into scoring_failed;
  - a result that still meets that function's output contract (CONTRACTS).
Never a KeyError/TypeError/AttributeError from unvalidated access, and never
a malformed result returned as if it were fine - that is what gets saved.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest

from app.services import llm_service

# ---- replies models really send when they go wrong ----
BAD_REPLIES = {
    "empty_object": "{}",
    "empty_list": "[]",
    "json_null": "null",
    "bare_string": '"sure"',
    "prose_only": "I'm sorry, I can't help with that request.",
    "unrelated_fields": '{"unexpected": true, "note": "hello"}',
    "wrong_types": json.dumps({
        "response_kind": 5, "response_message": ["not", "a", "string"], "code_after": {"x": 1},
        "final_score": "high", "coverage_score": None, "misses": "none", "feedback_text": 7,
        "test_cases": "none", "expected_approach": 3, "reference_solution": [], "status": 9,
        "verdict": 0, "fields": "x", "screens": 4, "category_status": [],
    }),
    "list_of_junk": '[1, "two", null]',
}

ROUND_TURN_KINDS = {"clarify", "refuse", "code_edit", "explain", "direct_edit"}


def _is_str(x):
    return isinstance(x, str)


def _turn_ok(r):
    return (isinstance(r, dict) and r.get("response_kind") in ROUND_TURN_KINDS and _is_str(r.get("response_message"))
            and (r["response_kind"] != "code_edit" or _is_str(r.get("code_after"))))


def _score_ok(r, *fields):
    return isinstance(r, dict) and all(isinstance(r.get(f), (int, float)) and not isinstance(r.get(f), bool) for f in fields)


def _cases_ok(r):
    return isinstance(r, list) and len(r) > 0 and all(isinstance(c, dict) for c in r)


# ---- each call site: (callable, kwargs, contract a successful result must meet) ----
DESIGN = [{"index": 0, "title": "Login", "preconditions": "", "steps": "1. log in", "test_data": "u/p", "expected_result": "ok"}]
CALLS = {
    "generate_round1_reference": (llm_service.generate_round1_reference,
                                  dict(scenario_description="d", experience_band="0-7", time_limit_minutes=20), _cases_ok),
    "generate_round2_reference": (llm_service.generate_round2_reference,
                                  dict(scenario_description="d", experience_band="0-7", time_limit_minutes=20), _cases_ok),
    "generate_round3_reference": (llm_service.generate_round3_reference,
                                  dict(scenario_description="d", experience_band="0-7"),
                                  lambda r: isinstance(r, dict) and isinstance(r.get("test_cases"), list) and _is_str(r.get("reference_solution"))),
    "generate_round2_automation_environment": (llm_service.generate_round2_automation_environment, dict(app_description="an app"), lambda r: isinstance(r, dict)),
    "generate_round2_automation_ui_mockup": (llm_service.generate_round2_automation_ui_mockup, dict(app_description="an app"), lambda r: isinstance(r, dict)),
    "score_round1_submission": (llm_service.score_round1_submission,
                                dict(scenario_description="d", experience_band="0-7", reference_cases=[], candidate_submission="[]"),
                                lambda r: _score_ok(r, "final_score")),
    "score_round2_submission": (llm_service.score_round2_submission,
                                dict(scenario_description="d", experience_band="0-7", reference_steps=[], candidate_investigation=[], candidate_root_cause="x"),
                                lambda r: _score_ok(r, "final_score")),
    "score_round3_coding": (llm_service.score_round3_coding,
                            dict(scenario_description="d", expected_approach="e", conversation_so_far=[], test_results=[]),
                            lambda r: _score_ok(r, "final_score")),
    "round3_coding_turn": (llm_service.round3_coding_turn,
                           dict(scenario_description="d", language="python", conversation_so_far=[], current_code="x = 1",
                                candidate_prompt="print x", turn_number=2), _turn_ok),
    "round3_syntax_fix": (llm_service.round3_syntax_fix, dict(code="print(1", language="python"),
                          lambda r: isinstance(r, dict) and _is_str(r.get("code_after", r.get("code")))),
    "round2_automation_clarify": (llm_service.round2_automation_clarify,
                            dict(language="python", selected_design=DESIGN, environment_code="# env\n", current_code="# env\n",
                                 conversation_so_far=[], candidate_prompt="encode step 1"), _turn_ok),
    "round2_automation_turn": (llm_service.round2_automation_turn,
                         dict(language="python", selected_design=DESIGN, environment_code="# env\n", current_code="# env\n",
                              conversation_so_far=[], candidate_prompt="encode step 1"), _turn_ok),
    "score_round2_automation_conversation": (llm_service.score_round2_automation_conversation,
                                       dict(language="python", tc_evidence=[], ground_truth="g", validation_notes=""),
                                       lambda r: _score_ok(r, "final_score")),
    "generate_candidate_summary": (llm_service.generate_candidate_summary,
                                   dict(candidate_email="c@example.com", experience_band="0-7", rounds=[]),
                                   lambda r: isinstance(r, dict) and _is_str(r.get("verdict"))),
}

CLEAN_ERRORS = (ValueError, RuntimeError)  # JSONDecodeError and pydantic ValidationError are ValueErrors


@pytest.mark.parametrize("reply_name", sorted(BAD_REPLIES))
@pytest.mark.parametrize("call_name", sorted(CALLS))
def test_bad_reply_fails_cleanly_or_meets_the_contract(monkeypatch, call_name, reply_name):
    fn, kwargs, contract = CALLS[call_name]
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: BAD_REPLIES[reply_name])
    try:
        result = fn(**kwargs)
    except CLEAN_ERRORS:
        return
    except Exception as e:  # noqa: BLE001 - the point is to catch the unclean ones
        pytest.fail(f"{call_name} crashed on a {reply_name} reply with {type(e).__name__}: {e}")
    assert contract(result), f"{call_name} returned a malformed result for a {reply_name} reply: {result!r}"[:600]


def test_experimental_judge_fails_cleanly(monkeypatch):
    """The POC AI judge (experimental) - its own result type, so checked here."""
    from app.services import poc_ai_judge
    judge_input = poc_ai_judge.JudgeInput(candidate_request="r", generated_response="g", response_kind="code_edit",
                                          allowed_assistance_scope="s", current_requirement="c", detector_status="clean")
    for reply in BAD_REPLIES.values():
        monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096, reply=reply: reply)
        try:
            result = poc_ai_judge.judge_ai_response(judge_input)
        except CLEAN_ERRORS:
            continue
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"judge crashed on {reply!r} with {type(e).__name__}: {e}")
        assert isinstance(result, poc_ai_judge.JudgeResult)
