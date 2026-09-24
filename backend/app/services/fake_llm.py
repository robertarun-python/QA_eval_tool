"""
Fake-AI mode (settings.llm_fake_mode, env LLM_FAKE_MODE=true): every Claude
call returns a scripted reply instead of reaching the API - so a person, or
the browser tests (tests/e2e), can go through all four rounds without API
credit and get the same result every time.

It sits inside llm_service._call_claude, so everything around the call -
the reply contracts, the scope checks, scoring, persistence - runs exactly
as it does with the real model. Only the model is replaced.

Replies are chosen by the llm_service function that made the call and must
meet that call site's contract (tests/test_fake_llm.py runs each one through
the real function). Code-writing turns return the current code plus one
comment line recording the instruction - it still runs, and the scope check
sees no unrequested additions. A call it has no script for gets a generic
"explain" reply; one that would need to invent code it can't find raises a
clear error rather than return empty code that would overwrite the
candidate's work.

Never enable this for real candidates: scores and replies are placeholders.
"""
import json
import re

BANNER = "FAKE AI MODE - replies and scores are scripted placeholders, not a real assessment."


def _between(text: str, start: str, end: str) -> str | None:
    m = re.search(re.escape(start) + r"\n?(.*?)\n?" + re.escape(end), text, re.S)
    return m.group(1) if m else None


def _instruction(prompt: str) -> str:
    text = _between(prompt, "<candidate_message>", "</candidate_message>") or ""
    first_line = text.strip().splitlines()[0] if text.strip() else "(empty instruction)"
    return first_line[:160]


def _current_code(prompt: str) -> str | None:
    code = _between(prompt, "<candidate_code>", "</candidate_code>")
    if code is None:
        return None
    return "" if code.strip() == "(no code written yet)" else code


def _with_comment(code: str, instruction: str, marker: str = "#") -> str:
    sep = "" if not code or code.endswith("\n") else "\n"
    return f"{code}{sep}{marker} fake AI mode - instruction: {instruction}\n"


_SCORE_FEEDBACK = "Fake AI mode: placeholder feedback - no real assessment was made."


def _reply(caller: str, prompt: str):
    if caller == "generate_round1_reference":
        return [
            {"title": f"Fake reference case {i}", "preconditions": "User has an account", "steps": f"1. Do step {i}",
             "test_data": "username = demo; password = demo", "expected_result": f"Outcome {i} is shown"}
            for i in (1, 2, 3)
        ]
    if caller == "generate_round2_reference":
        return [{"title": f"Fake investigation step {i}", "preconditions": "", "steps": f"Check area {i}",
                 "expected_result": f"Area {i} ruled in or out"} for i in (1, 2, 3)]
    if caller == "generate_round3_reference":
        return {
            "test_cases": [{"input": "3,1,4", "expected_output": "3", "description": "fake basic case"},
                           {"input": "", "expected_output": "-1", "description": "fake empty case"}],
            "expected_approach": "Fake AI mode placeholder approach.",
            "required_constructs": [],
            "reference_solution": "print('fake reference solution')\n",
        }
    if caller in ("score_round1_submission", "score_round2_submission"):
        return {"coverage_score": 70, "misses": ["Fake AI mode: placeholder miss"], "specificity_score": 70,
                "specificity_notes": "fake", "final_score": 70, "feedback_text": _SCORE_FEEDBACK, "concept_coverage": []}
    if caller == "score_round3_coding":
        return {"correctness_score": 70, "precision_score": 70, "efficiency_score": 70, "independent_judgment_score": 70,
                "final_score": 70, "misses": [], "guardrail_violations": [], "feedback_text": _SCORE_FEEDBACK}
    if caller == "score_round2_automation_conversation":
        return {"scores": {"automation_design": 14, "test_data_and_assertions": 14, "ai_usage": 14, "ai_output_review": 14,
                           "execution_and_validation": 14}, "final_score": 70, "findings": [], "feedback_text": _SCORE_FEEDBACK}
    if caller == "generate_candidate_summary":
        return {"rounds": [], "key_observations": ["Fake AI mode: placeholder observation"], "verdict": "Fake AI mode placeholder verdict"}
    if caller == "generate_round4_environment":
        return {"fields": {"Test account email": "fake.tester@example.com", "Test account password": "FakePass!1"},
                "notes": "Fake AI mode placeholder environment."}
    if caller == "generate_round4_ui_mockup":
        return {"screens": [{"name": "Login", "elements": [{"type": "label", "text": "Email"}, {"type": "input", "text": "Email"},
                                                           {"type": "button", "text": "Login"}]}]}
    if caller == "round2_automation_clarify":
        return {"status": "sufficient", "question": None, "prior_value": None, "current_value": None}
    if caller in ("round2_automation_turn", "_round3_coding_turn_once"):
        code = _current_code(prompt)
        if code is None:
            raise ValueError("Fake AI mode: no current code found in the prompt - refusing to write empty code.")
        return {"response_kind": "code_edit",
                "response_message": "Fake AI mode: recorded your instruction as a comment at the end of the code.",
                "code_after": _with_comment(code, _instruction(prompt), "//" if "public class Main" in code else "#"),
                "category_status": {}}
    if caller == "round3_syntax_fix":
        code = _between(prompt, "The candidate's code:", "PART 1")
        if code is None:
            raise ValueError("Fake AI mode: couldn't find the candidate's code - refusing to replace it.")
        return {"response_kind": "direct_edit", "response_message": "Fake AI mode: code saved unchanged.",
                "code_after": code.strip("\n") + "\n", "category_status": {}}
    return {"response_kind": "explain", "response_message": "Fake AI mode: this step has no scripted reply.", "code_after": None}


def reply_text(caller: str, prompt: str) -> str:
    """The scripted reply for the llm_service function `caller`, as the
    JSON text the real model would send."""
    return json.dumps(_reply(caller, prompt))
