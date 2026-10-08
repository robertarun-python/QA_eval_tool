"""
Round 3: two ways of stating "keep each value once" that the operation vocabulary missed (real-AI gate,
2026-10-07). Case 5, "keep each value once", was not seen as de-duplication: the scope check rejected the
set and the model dropped the step - code without the candidate's requirement. Case 7, "put the unique
values in a list", got "How should the program get the unique values?" instead of code. A stated
de-duplication is now recognised in these forms, may be written with a standard technique, and is a
requirement the code can never drop. Scripted model; no AI call.
"""
import json

import pytest

from app.services import assistant_operations as ops
from app.services import llm_service, round3_policy
from app.services import round3_scope_guard as guard

TASK = ("Given a list of integers, write a program that finds and prints the second-largest DISTINCT value in the list "
        "(duplicates of the largest value do not count as a separate value). If the list has fewer than two distinct "
        "values, print -1 instead.")
CASE5 = "Read the comma-separated numbers, keep each value once, sort them from largest to smallest, and print the first one."
CASE7 = "Put the unique values in a list sorted from biggest to smallest and print the value at position 1."

STATED = [
    "keep each value once", "put the unique values in a list",
    "keep every number only once", "each value should appear only once", "keep each element a single time",
    "no value more than once", "no number should appear twice", "without duplicates", "list them without any repeats",
    "store the distinct values in an array", "add the unique numbers to a list", "list the unique values",
    "place the distinct numbers into a list", "gather all the unique values", "use only the unique values",
]
NOT_AN_OPERATION = [  # a requirement or a goal - no operation of the candidate's own
    "count how many distinct numbers in nums are bigger than it",
    "find the second largest distinct value",
    "print the number of unique values",
    "use a nested loop to count how many distinct numbers are bigger",
    "each value is an integer",
]


@pytest.mark.parametrize("text", STATED)
def test_stated_deduplication_is_recognised(text):
    assert ops.states_deduplication(text)


@pytest.mark.parametrize("text", NOT_AN_OPERATION)
def test_requirement_wording_is_not_an_operation(text):
    assert not ops.states_deduplication(text)


SET_CODE = "nums = [3, 1, 3]\nuniq = list(set(nums))\n"
LOOP_CODE = "nums = [3, 1, 3]\nuniq = []\nfor n in nums:\n    if n not in uniq:\n        uniq.append(n)\n"


@pytest.mark.parametrize("text", STATED)
@pytest.mark.parametrize("code", [SET_CODE, LOOP_CODE])
def test_the_scope_check_accepts_the_stated_operation(text, code):
    added = guard.unrequested_additions("python", "nums = [3, 1, 3]", code, text, technique_free=True)
    assert not {"de-duplication", "chose a set to remove duplicates", "a loop", "a collection"} & set(added)


@pytest.mark.parametrize("text", STATED)
def test_code_that_drops_it_is_always_caught(text):
    assert guard.dropped_requirement(text, "nums = [3, 1, 3]\nnums.sort(reverse=True)\nprint(nums[0])\n")
    assert guard.dropped_requirement(text, SET_CODE) is None


def test_with_a_construct_checklist_the_technique_stays_the_candidates_choice():
    assert "chose a set to remove duplicates" in guard.unrequested_additions(
        "python", "nums = [3, 1, 3]", SET_CODE, "keep each value once", technique_free=False)


@pytest.mark.parametrize("message", [CASE5, CASE7])
def test_gate_cases_are_candidate_owned(message):
    assert ops.is_candidate_approach(message)
    assert not round3_policy.is_continuation_of_whole_task([], message, TASK)


def _edit(code, msg="Done."):
    return json.dumps({"response_kind": "code_edit", "response_message": msg, "code_after": code, "category_status": {}})


def _turn(monkeypatch, replies, prompt):
    calls, queue = [], list(replies)

    def fake(p, max_tokens=4096):
        calls.append(p)
        return queue.pop(0)
    monkeypatch.setattr(llm_service, "_call_claude", fake)
    result = llm_service.round3_coding_turn(scenario_description=TASK, language="python", conversation_so_far=[],
                                            current_code=None, candidate_prompt=prompt, turn_number=1)
    return result, calls


READ = "nums = [int(x) for x in input().split(',')]\n"
CASE5_KEPT = READ + "nums = sorted(set(nums), reverse=True)\nprint(nums[0])\n"
CASE5_DROPPED = READ + "nums.sort(reverse=True)\nprint(nums[0])\n"
CASE7_CODE = READ + "uniq = sorted(set(nums), reverse=True)\nprint(uniq[1])\n"


def test_case5_code_that_keeps_each_value_once_is_accepted_first_time(monkeypatch):
    result, calls = _turn(monkeypatch, [_edit(CASE5_KEPT)], CASE5)
    assert result["response_kind"] == "code_edit" and result["code_after"] == CASE5_KEPT and len(calls) == 1


def test_case5_code_that_drops_the_step_is_never_delivered(monkeypatch):
    """The gate's failure: the model left the de-duplication out. Now that is caught and regenerated."""
    result, calls = _turn(monkeypatch, [_edit(CASE5_DROPPED), _edit(CASE5_KEPT)], CASE5)
    assert len(calls) == 2 and "keep each value once" in calls[1]
    assert result["response_kind"] == "code_edit" and "set(nums)" in result["code_after"]
    dropped_twice, _ = _turn(monkeypatch, [_edit(CASE5_DROPPED), _edit(CASE5_DROPPED)], CASE5)
    assert dropped_twice["code_after"] is None  # code without their step never reaches them


def test_case7_gets_code(monkeypatch):
    result, calls = _turn(monkeypatch, [_edit(CASE7_CODE)], CASE7)
    assert result["response_kind"] == "code_edit" and result["code_after"] == CASE7_CODE and len(calls) == 1
