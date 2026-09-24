"""
Rules added after the Sep 2026 transcript review of the Round 2 / Round 3
AI assistants (see services/round3_scope_guard.py, round3_policy.
is_continuation_of_whole_task and round2_automation_clarify_policy.
should_stop_clarifying). Each case below is a real transcript pattern,
reduced to a small fixture, so the loophole it exposed stays closed.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.services import llm_service, round3_policy, round3_scope_guard as guard, round2_automation_clarify_policy as clarify


# ---- Round 3: scope check on what the model wrote ---------------------------

def test_remove_duplicates_must_not_also_rebuild_input_collection():
    """The colleague's report: "remove the duplicates from the list" also
    created a new list and converted/stored every input as an int."""
    before = "distinct_list = input()\ntry:\n    for value in distinct_list.split(','):\n        int(value.strip())\nexcept ValueError:\n    print('bad')"
    after = "distinct_list = input()\ntry:\n    int_values = []\n    for value in distinct_list.split(','):\n        int_values.append(int(value.strip()))\n    distinct_list = list(set(int_values))\nexcept ValueError:\n    print('bad')"
    issues = guard.unrequested_additions("python", before, after, "remove the duplicates from the list")
    assert "type conversion" in issues


def test_constraint_only_instruction_must_not_get_a_whole_algorithm():
    """"remove the set and do that in place" produced the full single-pass
    second-largest algorithm plus a -1 fallback (Java)."""
    before = "import java.util.*;\npublic class Main {\n    public static void main(String[] args) {\n        List<Integer> numbers = new ArrayList<>();\n        System.out.println(numbers.get(1));\n    }\n}"
    after = ("import java.util.*;\npublic class Main {\n    public static void main(String[] args) {\n        List<Integer> numbers = new ArrayList<>();\n"
             "        int largest = Integer.MIN_VALUE;\n        int secondLargest = Integer.MIN_VALUE;\n        for (int num : numbers) {\n"
             "            if (num > largest) { secondLargest = largest; largest = num; }\n        }\n        System.out.println(-1);\n    }\n}")
    issues = guard.unrequested_additions("java", before, after, "remove the set and do that in place")
    assert "a loop" in issues and "a fallback value" in issues


def test_corner_cases_request_must_not_invent_test_data():
    after = "def main():\n    for t in [[5, 3, 9, 3], [10, 10, 10], [], [-5, -1, -10]]:\n        print(f(t))"
    issues = guard.unrequested_additions("python", "def f(v):\n    pass", after, "i want to try with various combinations and corner cases")
    assert "hard-coded data the candidate didn't give" in issues


def test_three_word_goal_must_not_get_a_full_program():
    issues = guard.unrequested_additions("python", None, "a = int(input())\nb = int(input())\nprint(a + b)", "add 2 numbers")
    assert "reading input" in issues and "printing output" in issues


def test_choosing_a_set_needs_the_candidate_to_name_it():
    """R3-113/125/180: "remove the duplicates" doesn't say how - a set is the
    assistant's choice, and the data structure is part of what's assessed."""
    assert guard.unrequested_additions("python", "numbers = [3, 1]", "numbers = [3, 1]\nnumbers = list(set(numbers))",
                                       "now check if there are duplicates and if so remove them") == ["chose a set to remove duplicates"]


def test_literal_instructions_pass_untouched():
    assert guard.unrequested_additions("python", "numbers = [3, 1]", "numbers = [3, 1]\nnumbers = list(set(numbers))",
                                       "now check if there are duplicates and if so remove them using a set") == []
    assert guard.unrequested_additions("python", None, "a = int(input())\nb = int(input())\nprint(a + b)",
                                       "read two integers from the user, one per line, and print their sum") == []
    # typo-tolerant: "unitil" still asks for a loop
    assert guard.unrequested_additions("python", "x = input()", "while True:\n    x = input()\n    if x.isdigit():\n        break",
                                       "if the user enters other than int throw an error as invalid input unitil the user inputs int") == []


def test_moved_or_reindented_lines_are_not_additions():
    before = "print(result)"
    after = "if ok:\n    print(result)"
    assert guard.added_lines(before, after) == ["if ok:"]


def test_java_scaffolding_is_never_counted():
    after = "import java.util.Scanner;\npublic class Main {\n    public static void main(String[] args) {\n    }\n}"
    assert guard.added_lines(None, after) == []


def test_instruction_includes_the_question_it_answers():
    history = [{"candidate_prompt": "read the numbers the user types", "response_kind": "clarify"}]
    assert "read the numbers" in guard.instruction_text(history, "comma separated")
    history = [{"candidate_prompt": "read the numbers", "response_kind": "code_edit"}]
    assert "read the numbers" not in guard.instruction_text(history, "now sort them")


def test_noop_edit_is_detected():
    assert guard.is_noop_edit("s = set(x)", "s = set(x)\n")
    assert not guard.is_noop_edit("s = set(x)", "s = sorted(set(x))")


# ---- Round 3: whole-task requests, including the two-step bypass -------------

def test_whole_task_requests_are_recognised_without_catching_concrete_steps():
    for text in ["write a program to add 2 numbers", "give me the code", "write the code", "solve this",
                 "get the user input and write a program to check if its odd or even and print the result"]:
        assert round3_policy.is_whole_task_request(text), text
    for text in ["write the code for the loop", "write the code to print the result", "write a loop to track the second largest value",
                 "create a list ori_list and use input to get the numbers", "when the program runs it should wait for numbers"]:
        assert not round3_policy.is_whole_task_request(text), text


def test_one_word_answer_to_a_whole_task_request_is_still_refused():
    """"write a program to check odd or even" -> clarify -> "cli" used to
    hand over the full solution (that attempt scored 90)."""
    history = [{"candidate_prompt": "get the user input and write a program to check if its odd or even and print the result",
                "response_kind": "clarify", "response_message": "How should the input be read?"}]
    assert round3_policy.is_continuation_of_whole_task(history, "cli")
    history = [{"candidate_prompt": "create a variable n", "response_kind": "clarify"}]
    assert not round3_policy.is_continuation_of_whole_task(history, "call it n")


# ---- Round 3: the live turn flow ----------------------------------------------

def _turn(monkeypatch, replies, candidate_prompt, current_code=None, conversation_so_far=None):
    calls = []

    def fake_call(prompt, max_tokens=4096):
        calls.append(prompt)
        return json.dumps(replies[min(len(calls) - 1, len(replies) - 1)])

    monkeypatch.setattr(llm_service, "_call_claude", fake_call)
    result = llm_service.round3_coding_turn(
        scenario_description="Find the second-largest distinct value.", language="python",
        conversation_so_far=conversation_so_far or [], current_code=current_code,
        candidate_prompt=candidate_prompt, turn_number=3,
    )
    return result, calls


def _edit(code, message="Done."):
    return {"response_kind": "code_edit", "response_message": message, "code_after": code, "category_status": {}}


def test_over_reaching_edit_is_regenerated_with_the_reason(monkeypatch):
    over = _edit("numbers = [3, 1]\nvals = [int(x) for x in numbers]\nnumbers = list(set(vals))")
    ok = _edit("numbers = [3, 1]\nnumbers = list(set(numbers))")
    result, calls = _turn(monkeypatch, [over, ok], "remove the duplicates using a set", current_code="numbers = [3, 1]")
    assert result["response_kind"] == "code_edit"
    assert result["code_after"] == "numbers = [3, 1]\nnumbers = list(set(numbers))"
    assert len(calls) == 2
    assert "rejected before the candidate saw it" in calls[1] and "type conversion" in calls[1]


def test_persistent_over_reach_falls_back_to_asking_the_candidate(monkeypatch):
    over = _edit("numbers = [3, 1]\nvals = [int(x) for x in numbers]\nnumbers = list(set(vals))")
    result, calls = _turn(monkeypatch, [over, over], "remove the duplicates", current_code="numbers = [3, 1]")
    assert result["response_kind"] == "clarify"
    assert result["code_after"] is None
    assert result["response_message"] == guard.SCOPE_FALLBACK_MESSAGE
    assert len(calls) == 2  # one regeneration, never a loop


def test_edit_that_changes_nothing_says_so(monkeypatch):
    result, _ = _turn(monkeypatch, [_edit("s = set(x)", "Converted the list to a set.")], "convert the list to a set", current_code="s = set(x)")
    assert result["response_kind"] == "explain"
    assert result["response_message"] == guard.NOOP_MESSAGE
    assert result["code_after"] is None


def test_two_step_bypass_is_refused_before_any_model_call(monkeypatch):
    history = [{"turn_number": 1, "candidate_prompt": "write a program to check if a number is odd or even",
                "response_kind": "clarify", "response_message": "How should the input be read?", "code_after": None}]
    result, calls = _turn(monkeypatch, [_edit("n = int(input())")], "cli", conversation_so_far=history)
    assert result["response_kind"] == "refuse"
    assert result["response_message"] == round3_policy.WHOLE_TASK_REFUSAL_MESSAGE
    assert calls == []


# ---- Round 2: clarification loop breaker ------------------------------------

Q = "What specific actions should be taken in this test, and what should prove each step succeeded?"


def _asked(*prompts):
    return [{"candidate_prompt": p, "response_message": Q if i % 2 == 0 else f"Question {i}?", "response_kind": "clarify"} for i, p in enumerate(prompts)]


def test_first_question_is_always_allowed():
    assert not clarify.should_stop_clarifying([], "automate the test case", Q)


def test_repeating_an_earlier_question_is_never_allowed():
    """Candidate 5's current session: the same question 9 times."""
    assert clarify.should_stop_clarifying(_asked("automate it"), "the dropdown should show Cardiology", Q)
    assert clarify.should_stop_clarifying(_asked("automate it"), "x", "what SPECIFIC actions should be taken in this test and what should prove each step succeeded")


def test_candidate_saying_they_are_done_ends_the_questions():
    for said in ["this is enough. give the code", "All done nothing more", "No, only this", "ignore that and use only the label", "that's all"]:
        assert clarify.should_stop_clarifying(_asked("automate it"), said, "A new question?"), said


def test_three_unresolved_questions_in_a_row_is_the_limit():
    assert not clarify.should_stop_clarifying(_asked("a", "b"), "c", "A brand new question?")
    assert clarify.should_stop_clarifying(_asked("a", "b", "c"), "d", "A brand new question?")


def test_clarify_gate_proceeds_instead_of_repeating_itself(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=1024: json.dumps(
        {"status": "insufficient", "question": Q, "prior_value": None, "current_value": None}))
    kwargs = dict(language="python", selected_design=[{"title": "Login"}], environment_code="", current_code="")
    first = llm_service.round2_automation_clarify(conversation_so_far=[], candidate_prompt="automate the login", **kwargs)
    assert first["response_kind"] == "clarify"
    history = [{"candidate_prompt": "automate the login", "response_message": first["response_message"], "response_kind": "clarify"}]
    second = llm_service.round2_automation_clarify(conversation_so_far=history, candidate_prompt="log in with the given user", **kwargs)
    assert second["response_kind"] == "explain"  # "sufficient" -> the router proceeds to generation
