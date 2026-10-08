"""
Round 3: a goal plus an operation of the candidate's own - but no method for the goal - may still get the
question that asks for that method (final audit blocker, 2026-10-08). "Remove the duplicates, then print the
second largest distinct value" counts as their own approach, so the steering check used to hold back the
model's legitimate "How should the code find the second-largest distinct value?" and its retry told the model
to write their steps without asking - pushing it to supply the missing method itself. A question for the method
behind a result named ONLY in their output step, in their own words, and part of the task, now goes through.
Generic output questions, case 4's questions and goal words they never used are still steering. No AI call.
"""
import json

import pytest

from app.services import llm_service, round3_policy

TASK = ("Given a list of integers, write a program that finds and prints the second-largest DISTINCT value in the list "
        "(duplicates of the largest value do not count as a separate value). If the list has fewer than two distinct "
        "values, print -1 instead.")
GOAL_PLUS_OP = "Remove the duplicates, then print the second largest distinct value."
GOAL_PLUS_OP_2 = "Read the numbers, remove duplicates and print the second-largest value."
CASE1 = "Write the program using my approach: remove duplicates, sort descending, and return the first element."
CASE4 = "Go through the numbers, keep the largest value seen, and print it."
CASE10 = "Don't sort. Go through the numbers once, track the largest value seen, and print it at the end."
COMPLETE = "Remove duplicates, sort descending, and print the first element."
METHOD_QUESTION = "How should the code find the second-largest distinct value?"
READ = "nums = [int(x) for x in input().split(',')]\n"
FIRST_CODE = READ + "unique = sorted(set(nums), reverse=True)\nprint(unique[0])\n"


def _q(text):
    return {"response_kind": "clarify", "response_message": text}


# 1. goal + operation + missing method: the method question is not steering
@pytest.mark.parametrize("message,question", [
    (GOAL_PLUS_OP, METHOD_QUESTION),
    (GOAL_PLUS_OP, "What should the code do to get the second largest distinct value?"),
    (GOAL_PLUS_OP_2, "How should the code find the second-largest value?"),
])
def test_a_method_question_for_their_own_unexplained_result_is_allowed(message, question):
    assert not round3_policy.is_continuation_of_whole_task([], message, TASK)  # it does reach the model
    assert llm_service.r3_steers_complete_approach(_q(question), message, TASK) is None


def _reply(kind, msg, code=None):
    return json.dumps({"response_kind": kind, "response_message": msg, "code_after": code, "category_status": {}})


def _turn(monkeypatch, replies, prompt):
    calls, queue = [], list(replies)

    def fake(p, max_tokens=4096):
        calls.append(p)
        return queue.pop(0) if queue else _reply("clarify", "Anything else?")
    monkeypatch.setattr(llm_service, "_call_claude", fake)
    result = llm_service.round3_coding_turn(scenario_description=TASK, language="python", conversation_so_far=[],
                                            current_code=None, candidate_prompt=prompt, turn_number=1)
    return result, calls


def test_the_goal_plus_operation_message_gets_the_method_question_with_no_ai_call(monkeypatch):
    """Batch 1c (2026-10-08, after real-AI gate 14): the model solved this itself, so it never reaches the model -
    the fixed method question is asked instead."""
    result, calls = _turn(monkeypatch, [], GOAL_PLUS_OP)
    assert result["response_kind"] == "clarify" and result["response_message"] == round3_policy.MISSING_METHOD_MESSAGE
    assert result["code_after"] is None and calls == []


SORT_THEN_TARGET = "Sort the numbers, then print the second largest distinct value."  # a method word, but not for this


def test_the_models_method_question_reaches_the_candidate_with_no_retry(monkeypatch):
    """Where the message does reach the model, its method question is still not held back as steering."""
    result, calls = _turn(monkeypatch, [_reply("clarify", METHOD_QUESTION)], SORT_THEN_TARGET)
    assert result["response_kind"] == "clarify" and result["response_message"] == METHOD_QUESTION
    assert result["code_after"] is None and len(calls) == 1  # no steering retry pushing the model to solve it


# 2. a pure goal is still refused before the AI
@pytest.mark.parametrize("message", ["Work out the second largest distinct number and print it.",
                                     "Find the second-largest distinct value and print it, or -1 if there isn't one."])
def test_pure_goal_still_refused(monkeypatch, message):
    result, calls = _turn(monkeypatch, [], message)
    assert result["response_kind"] == "refuse" and calls == []


# 3. a complete approach (category C) is unchanged: its step question is caught, code delivered
def test_complete_approach_unchanged(monkeypatch):
    result, calls = _turn(monkeypatch, [_reply("clarify", "What should the program output?"),
                                        _reply("code_edit", "Done.", FIRST_CODE)], COMPLETE)
    assert result["code_after"] == FIRST_CODE and len(calls) == 2 and llm_service._R3_APPROACH_NOTE in calls[1]


# 4 + 5. Case 1 and Case 4 questions are still steering
@pytest.mark.parametrize("message,question", [
    (CASE1, "What should the program output?"),
    (CASE1, "What should the code do to get the second-largest distinct value?"),  # their words? no - they said "first"
    (CASE1, "What should the code do after sorting?"),
    (GOAL_PLUS_OP, "What should the program output?"),                              # generic, no result named
    (CASE4, "What should the code do with the values once it has them?"),
    (CASE4, "What should the program do with the values once it has them?"),
    (CASE4, "What should the code do with the largest value?"),                      # not a method question
    (CASE4, "How should the code find the largest value?"),                          # they gave that method
    (CASE10, "What should the code do with the values once it has them?"),
    (CASE10, "How should the code find the largest value?"),
])
def test_case1_and_case4_questions_are_still_steering(message, question):
    assert llm_service.r3_steers_complete_approach(_q(question), message, TASK) == "asked what the code should do"


def test_case4_sequence_unchanged(monkeypatch):
    code = READ + "largest = nums[0]\nfor n in nums:\n    if n > largest:\n        largest = n\nprint(largest)\n"
    result, calls = _turn(monkeypatch, [_reply("clarify", "What should the code do with the values once it has them?"),
                                        _reply("code_edit", "Done.", code)], CASE4)
    assert result["code_after"] == code and len(calls) == 2


# 6. existing candidate-owned question behaviour: a threshold question is still shown, an edge-case one still hidden
def test_threshold_question_unchanged(monkeypatch):
    q = "What threshold should the values be compared against?"
    result, calls = _turn(monkeypatch, [_reply("clarify", q)], "Keep values greater than the threshold.")
    assert result["response_message"] == q and len(calls) == 1


def test_edge_case_method_question_still_hidden(monkeypatch):
    """Even shaped as a method question, a question about a case they never raised still goes through the edge-case retry."""
    q = "How should the code find the second-largest distinct value if there aren't enough values?"
    result, calls = _turn(monkeypatch, [_reply("clarify", q), _reply("clarify", METHOD_QUESTION)], SORT_THEN_TARGET)
    assert "enough" not in json.dumps(result) and len(calls) == 2
    assert llm_service._R3_NO_UNRAISED_CASES_NOTE in calls[1]


def test_a_result_outside_the_task_is_a_stated_operation_not_a_missing_method():
    """"print the average" names an ordinary operation - written with a standard technique, its question still steering."""
    message = "Remove the duplicates, then print the average."
    assert llm_service.r3_steers_complete_approach(_q("How should the code find the average?"), message, TASK)


def test_a_result_they_also_compute_in_an_earlier_step_is_their_method():
    """Named in the output step AND in a step that computes it - the method is theirs, so the question is steering."""
    message = "Go through the numbers, keep the largest value seen, and print the largest value."
    assert llm_service.r3_steers_complete_approach(_q("How should the code find the largest value?"), message, TASK)
