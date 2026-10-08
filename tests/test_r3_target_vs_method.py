"""
Round 3: an output TARGET is not an implementation METHOD (Batch 1c, 2026-10-08). Real-AI gate 14: "Remove the
duplicates, then print the second largest distinct value" got three home-made algorithms from the model (sort and
index; remove the max, then max), each with an invented -1 - the scope check blocked them all, and the candidate
got a generic fallback after 4 calls. Naming a ranked value to produce ("the largest", "the second smallest", "the
maximum") says nothing about how to get it: with no way of getting it anywhere in what the candidate has said, a
fixed question asks for the method, before any AI call. Any stated way - even a wrong one - is their approach and
goes to the model exactly as before (category C). No AI call.
"""
import json

import pytest

from app.services import llm_service, round3_policy

TASK = ("Given a list of integers, write a program that finds and prints the second-largest DISTINCT value in the list "
        "(duplicates of the largest value do not count as a separate value). If the list has fewer than two distinct "
        "values, print -1 instead.")
LIVE_FORMAT = {
    "input": "One line of comma-separated integers (no spaces), read from standard input. An empty line means an empty list.",
    "output": "Print a single integer on one line - the second-largest distinct value, or -1 - with no labels or extra text.",
    "value_type": "integers",
}

INCOMPLETE = [  # a target, no method
    "Remove the duplicates, then print the second largest distinct value.",   # A - gate 14, verbatim
    "Remove duplicates, then print the largest value.",                         # C
    "Read the numbers, remove duplicates and print the second-largest value.",
    "Find the largest number and print it.",
    "Print the largest value.",
    "Remove duplicates and return the smallest number.",
    "Get the third highest value and output it.",
]
COMPLETE = [  # a stated way of getting it - right or wrong
    "Remove the duplicates, sort descending, and print the first element.",   # B
    "Use sorting to find the second largest, then print index 1.",
    "Use max() twice to find the second largest.",
    "Write the program using my approach: remove duplicates, sort descending, and return the first element.",
    "Use a set to remove duplicates, sort with reverse=True, and print the first element.",
    "Find the maximum value with max(), then print how many times it appears.",
    "Go through the numbers, keep the largest value seen, and print it.",
    "Don't sort. Go through the numbers once, track the largest value seen, and print it at the end.",
    "Go through the numbers once, keep track of the largest and the second largest value, and print the second largest at the end.",
    "Put the unique values in a list sorted from biggest to smallest and print the value at position 1.",
    "if greater than the current highest, replace it",
]
NO_TARGET = [
    "Keep values greater than the threshold.",
    "Read the input, split it on commas, convert every value with int(), and print the sum.",
    "Sort the numbers from largest to smallest.",
    "Print the list I created earlier.",
]


@pytest.mark.parametrize("message", INCOMPLETE)
def test_a_target_without_a_method_is_recognised(message):
    assert not round3_policy.is_continuation_of_whole_task([], message, TASK)  # not a pure goal: it isn't refused
    assert round3_policy.names_target_without_method([], message)


@pytest.mark.parametrize("message", COMPLETE + NO_TARGET)
def test_a_stated_method_or_no_target_is_not(message):
    assert not round3_policy.names_target_without_method([], message)


def test_a_method_from_earlier_in_the_conversation_counts():
    earlier = [{"candidate_prompt": "Sort the numbers in descending order.", "response_kind": "code_edit"}]
    assert not round3_policy.names_target_without_method(earlier, "Now print the second largest value.")
    refused = [{"candidate_prompt": "Sort the numbers in descending order and solve it.", "response_kind": "refuse"}]
    assert round3_policy.names_target_without_method(refused, "Now print the second largest value.")


def test_the_question_names_no_method_and_nothing_of_the_task():
    msg = round3_policy.MISSING_METHOD_MESSAGE.lower()
    assert not [w for w in ("sort", "max", "min", "loop", "index", "second", "largest", "distinct", "-1", "empty") if w in msg]


def _reply(kind, msg, code=None):
    return json.dumps({"response_kind": kind, "response_message": msg, "code_after": code, "category_status": {}})


def _turn(monkeypatch, replies, prompt, conversation=None):
    calls, queue = [], list(replies)

    def fake(p, max_tokens=4096):
        calls.append(p)
        return queue.pop(0)
    monkeypatch.setattr(llm_service, "_call_claude", fake)
    result = llm_service.round3_coding_turn(scenario_description=TASK, language="python", conversation_so_far=conversation or [],
                                            current_code=None, candidate_prompt=prompt, turn_number=len(conversation or []) + 1,
                                            io_format=LIVE_FORMAT)
    return result, calls


# A + C: incomplete -> the method is requested, no code, no AI call (so no model can invent sorting, max(), -1 ...)
@pytest.mark.parametrize("message", INCOMPLETE)
def test_incomplete_gets_the_method_question_and_no_code(monkeypatch, message):
    result, calls = _turn(monkeypatch, [], message)
    assert result["response_kind"] == "clarify" and result["response_message"] == round3_policy.MISSING_METHOD_MESSAGE
    assert result["code_after"] is None and calls == []


def test_asked_twice_it_moves_on_instead_of_repeating(monkeypatch):
    first = [{"candidate_prompt": INCOMPLETE[0], "response_kind": "clarify",
              "response_message": round3_policy.MISSING_METHOD_MESSAGE}]
    result, calls = _turn(monkeypatch, [], INCOMPLETE[0], conversation=first)
    assert result["response_message"] in llm_service.R3_PROGRESS_MESSAGES and calls == []


def test_their_answer_then_goes_to_the_model(monkeypatch):
    first = [{"candidate_prompt": INCOMPLETE[0], "response_kind": "clarify",
              "response_message": round3_policy.MISSING_METHOD_MESSAGE}]
    code = "nums = [int(x) for x in input().split(',')]\nu = sorted(set(nums), reverse=True)\nprint(u[1])\n"
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", code)], "Sort them descending and print index 1.",
                          conversation=first)
    assert result["code_after"] == code and len(calls) == 1


# B: complete -> code, the candidate's method kept even though it is wrong (category C, live format)
def test_complete_but_wrong_method_is_written_as_stated(monkeypatch):
    code = ("line = input()\nif line:\n    numbers = [int(x) for x in line.split(',')]\nelse:\n    numbers = []\n"
            "unique = list(set(numbers))\nunique.sort(reverse=True)\nprint(unique[0])\n")
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", code)], COMPLETE[0])
    assert result["code_after"] == code and len(calls) == 1
    assert "[1]" not in result["code_after"] and "-1" not in result["code_after"]


def test_category_c_case1_path_unchanged(monkeypatch):
    code = ("line = input()\nif line:\n    numbers = [int(x) for x in line.split(',')]\nelse:\n    numbers = []\n"
            "unique = list(set(numbers))\nunique.sort(reverse=True)\nprint(unique[0])\n")
    result, calls = _turn(monkeypatch, [_reply("clarify", "What should the program output?"),
                                        _reply("code_edit", "Done.", code)], COMPLETE[3])
    assert result["code_after"] == code and len(calls) == 2 and llm_service._R3_APPROACH_NOTE in calls[1]


def test_pure_goal_still_refused_first(monkeypatch):
    result, calls = _turn(monkeypatch, [], "Print the second largest distinct value.")
    assert result["response_kind"] == "refuse" and calls == []


def test_a_comparison_alone_is_a_stated_method():
    """The target is named in a step that obtains it - the comparison they state is their way of getting it."""
    message = "Find the largest value: replace the current one whenever a number is greater than it, and print it."
    assert not round3_policy.names_target_without_method([], message)


def test_a_ranked_word_used_as_a_label_is_not_a_target():
    """No step outputs or obtains it - "maximum" just names a setting."""
    message = "Store the maximum allowed size, 100, in a variable called limit."
    assert not round3_policy.names_target_without_method([], message)
