"""
Batch E of the Sep 2026 assistant guardrail fixes - closing the gaps left
after Batch D:

- R3 replies that claim a change the code doesn't contain (not only sort
  order) are replaced with a summary of the actual change.
- R2 code that fakes what it should observe (R2-186's first code) is
  regenerated, and recorded for the scorer if the fake survives; a
  planted flaw may use at most one.
- The R3 scorer is told not to credit the assistant's own decisions to
  the candidate.
"""
import json

import pytest

from app.services import llm_service, round3_scope_guard as guard, round4_auto_policy

# ---- R3: replies must match the code ----------------------------------------

@pytest.mark.parametrize("message, before, after", [
    ("Converted the ArrayList to a Set.", "Set<Integer> s = new HashSet<>(n);", "Set<Integer> s = new HashSet<>(n);\nint x = 1;"),
    ("Added a loop to keep prompting.", "x = 1", "x = 1\ny = 2"),
    ("Sorted the list from smallest to largest.", "x = [3, 1]", "x = [3, 1]\nprint(x)"),
    ("Converted a and b to integers.", "a = input()", "a = input()\nc = a + b"),
])
def test_claimed_change_that_is_not_in_the_code_is_detected(message, before, after):
    assert guard.misleading_claim(message, before, after)


@pytest.mark.parametrize("message, before, after", [
    ("Printed the value of c.", "c = 1", "c = 1\nprint(c)"),
    ("Removed duplicates from the list.", "x = [1]", "x = [1]\nx = list(set(x))"),
    ("Converted a and b to integers before adding them to c.", "a = input()", "a = int(input())\nc = a + b"),
    ("Added try-except block to check if input is an integer.", "x = 1", "try:\n    x = int(input())\nexcept ValueError:\n    print(1)"),
    ("Declared variable c and stored the sum.", "", "c = a + b"),
])
def test_accurate_reply_is_not_flagged(message, before, after):
    assert not guard.misleading_claim(message, before, after)


def test_r3_turn_replaces_a_false_claim_with_the_real_change(monkeypatch):
    reply = json.dumps({"response_kind": "code_edit", "category_status": {},
                        "response_message": "Added a loop that keeps asking until two numbers are entered.",
                        "code_after": "nums = []\nprint(nums)"})
    monkeypatch.setattr(llm_service, "_call_claude", lambda p, max_tokens=4096: reply)
    result = llm_service.round3_coding_turn(
        scenario_description="d", language="python", conversation_so_far=[], current_code="nums = []",
        candidate_prompt="print the list", turn_number=1,
    )
    assert result["response_message"] == "Updated the code - added: print(nums)"


# ---- R2: faked observations -------------------------------------------------

_R2_186_FAKE = (
    "    static void testBook() {\n"
    "        boolean ok = UI.login(\"jordan.rivera@example.com\", \"Passw0rd!2026\");\n"
    "        String dropdownText = \"Cardiology\";\n"
    "        String currentPage = \"appointment\";\n"
    "    }\n"
)


def test_r2_186_style_fake_test_is_detected():
    assert round4_auto_policy.fabricated_observations("", _R2_186_FAKE) == [
        'String dropdownText = "Cardiology";', 'String currentPage = "appointment";',
    ]


@pytest.mark.parametrize("line", [
    'expected_header = "Welcome, Jordan"',  # states what the test looks for
    'username = "jordan.rivera@example.com"',  # test data, not an observation
    'header = UI.header()',  # actually read from the app
])
def test_legitimate_assignments_are_not_flagged(line):
    assert round4_auto_policy.fabricated_observations("", line) == []


_DESIGN = [{"title": "Book appointment", "steps": "Log in, search by Cardiology, open the appointment page.",
            "test_data": "username = jordan.rivera@example.com; password = Passw0rd!2026",
            "expected_result": "The dropdown shows Cardiology and the appointment page opens."}]


def _auto(monkeypatch, replies, inject_flaw=False):
    prompts, queue = [], list(replies)

    def _fake(p, max_tokens=4096):
        prompts.append(p)
        return queue.pop(0)

    monkeypatch.setattr(llm_service, "_call_claude", _fake)
    result = llm_service.round4_auto_turn(
        language="java", selected_design=_DESIGN, environment_code="// env", current_code="",
        conversation_so_far=[], candidate_prompt="log in, search by Cardiology and check the appointment page",
        inject_flaw=inject_flaw,
    )
    return result, prompts


def _edit(code):
    return json.dumps({"response_kind": "code_edit", "response_message": "Done.", "code_after": code})


def test_r2_fake_test_is_regenerated(monkeypatch):
    real = "        String page = UI.currentPage();\n"
    result, prompts = _auto(monkeypatch, [_edit(_R2_186_FAKE), _edit(real)])
    assert result["code_after"] == real and len(prompts) == 2
    assert 'String currentPage = "appointment";' in prompts[1]
    assert "fabricated_observations" not in result


def test_r2_fake_that_survives_the_retry_is_recorded_for_scoring(monkeypatch):
    result, _ = _auto(monkeypatch, [_edit(_R2_186_FAKE), _edit(_R2_186_FAKE)])
    assert len(result["fabricated_observations"]) == 2


def test_a_planted_flaw_may_use_one_fake_but_not_more(monkeypatch):
    one_fake = "        String dropdownText = \"Cardiology\";\n"
    result, prompts = _auto(monkeypatch, [_edit(one_fake)], inject_flaw=True)
    assert len(prompts) == 1 and "fabricated_observations" not in result
    _, prompts = _auto(monkeypatch, [_edit(_R2_186_FAKE), _edit(one_fake)], inject_flaw=True)
    assert len(prompts) == 2


# ---- R3 scorer ----------------------------------------------------------------

def test_r3_scorer_does_not_credit_assistant_decisions(monkeypatch):
    captured = {}

    def _fake(prompt, max_tokens=2048):
        captured["prompt"] = prompt
        return ('{"correctness_score": 0, "precision_score": 0, "efficiency_score": 0, "independent_judgment_score": 0, '
                '"final_score": 0, "misses": [], "guardrail_violations": [], "feedback_text": ""}')

    monkeypatch.setattr(llm_service, "_call_claude", _fake)
    llm_service.score_round3_coding(scenario_description="d", expected_approach="a", conversation_so_far=[], test_results=[])
    assert "was the assistant's decision, not the candidate's" in captured["prompt"]
    assert "assistant chose <what> without being asked" in captured["prompt"]
