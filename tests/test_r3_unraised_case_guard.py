"""
Round 3: a reply never raises an input case the candidate didn't raise (Batch 1b correction, 2026-10-07).

Real-AI gate: for complete steps ("Remove the duplicates, sort ascending, and print the last element") the
model asked "What should the program do if there aren't enough distinct values?" - the task's hidden -1
rule, handed to the candidate. Such a question is never shown: it goes through the no-questions retry.
Questions about information the candidate's own step needs ("What threshold should I use?") still go
through. The model is a scripted fake; no AI call is made.
"""
import json

import pytest

from app.services import llm_service

TASK = ("Given a list of integers, write a program that finds and prints the second-largest DISTINCT value in the list "
        "(duplicates of the largest value do not count as a separate value). If the list has fewer than two distinct "
        "values, print -1 instead.")
STEPS = "Remove the duplicates, sort the numbers in descending order, and print the element at index 1."
CODE = "nums = [int(x) for x in input().split(',')]\nnums = sorted(set(nums), reverse=True)\nprint(nums[1])\n"

LEAKING = [
    "What should the program do if there aren't enough distinct values?",
    "What should happen if there aren't enough numbers?",
    "What if the input is empty?",
    "What should happen when there is a tie?",
    "Should we return -1 if there is no second value?",
    "What should happen for invalid input?",
    "What should be printed when there is only one distinct value?",
    "How should the program handle fewer than two values?",
    "What should it output if all the numbers are the same?",
    "Should I print None when nothing is entered?",
    "How should errors be handled?",
    "What about edge cases like an empty list?",
    "Is there a default value to print if the list is too short? Fewer than 2 values?",
]
LEGITIMATE = [
    "What threshold should the values be compared against?",
    "Which value should I compare against?",
    "What should the list be called?",
    "Which element should be printed?",
    "How should the values be ordered?",
]


@pytest.mark.parametrize("question", LEAKING)
def test_questions_about_unraised_cases_are_recognised(question):
    assert llm_service.r3_unraised_case(question, [], STEPS)


@pytest.mark.parametrize("question", LEGITIMATE)
def test_questions_about_missing_information_are_not(question):
    assert llm_service.r3_unraised_case(question, [], "keep the values greater than the threshold") is None


@pytest.mark.parametrize("raised,question", [
    ("print -1 if the list is empty", "What should be printed when the list is empty?"),
    ("handle ties by printing the first one", "What counts as a tie here?"),
    ("if there are fewer than two values print a message", "What message should be printed when there are fewer than two values?"),
    ("skip invalid input", "What counts as invalid input?"),
])
def test_a_case_the_candidate_raised_may_be_asked_about(raised, question):
    assert llm_service.r3_unraised_case(question, [], raised) is None
    earlier = [{"candidate_prompt": raised, "response_kind": "code_edit"}]
    assert llm_service.r3_unraised_case(question, earlier, "now continue") is None


def _clarify(msg):
    return json.dumps({"response_kind": "clarify", "response_message": msg, "code_after": None, "category_status": {}})


def _explain(msg):
    return json.dumps({"response_kind": "explain", "response_message": msg, "code_after": None, "category_status": {}})


def _edit(code, msg="Done."):
    return json.dumps({"response_kind": "code_edit", "response_message": msg, "code_after": code, "category_status": {}})


def _turn(monkeypatch, replies, prompt=STEPS, conversation=None, code=None):
    calls, queue = [], list(replies)

    def fake(p, max_tokens=4096):
        calls.append(p)
        return queue.pop(0) if queue else _clarify("Anything else?")
    monkeypatch.setattr(llm_service, "_call_claude", fake)
    result = llm_service.round3_coding_turn(scenario_description=TASK, language="python", conversation_so_far=conversation or [],
                                            current_code=code, candidate_prompt=prompt, turn_number=len(conversation or []) + 1)
    return result, calls


def _shown(result):
    return json.dumps(result).lower()


def test_a_leaking_question_is_never_shown_and_the_steps_get_code(monkeypatch):
    result, calls = _turn(monkeypatch, [_clarify(LEAKING[0]), _edit(CODE)])
    assert result["response_kind"] == "code_edit" and result["code_after"] == CODE
    assert "enough" not in _shown(result) and "-1" not in _shown(result)
    assert len(calls) == 2 and "do not mention, ask about or handle any input case they have not raised" in calls[1]


def test_a_retry_that_leaks_again_becomes_a_way_forward_that_reveals_nothing(monkeypatch):
    result, calls = _turn(monkeypatch, [_clarify(LEAKING[0]), _clarify(LEAKING[2])])
    assert result["response_kind"] == "explain" and result["response_message"] in llm_service.R3_PROGRESS_MESSAGES
    assert llm_service.r3_unraised_case(result["response_message"], [], STEPS) is None
    assert len(calls) == 2  # one retry, never a loop


def test_a_retry_explanation_that_leaks_is_replaced_too(monkeypatch):
    result, _ = _turn(monkeypatch, [_clarify(LEAKING[1]), _explain("I need to know what to print when there are fewer than two values.")])
    assert result["response_message"] in llm_service.R3_PROGRESS_MESSAGES


def test_a_question_about_genuinely_missing_information_is_still_asked(monkeypatch):
    result, calls = _turn(monkeypatch, [_clarify(LEGITIMATE[0])], prompt="Keep values greater than the threshold.")
    assert result["response_kind"] == "clarify" and result["response_message"] == LEGITIMATE[0] and len(calls) == 1


def test_a_case_the_candidate_raised_can_still_be_clarified(monkeypatch):
    question = "What should be printed when the list is empty?"
    result, calls = _turn(monkeypatch, [_clarify(question)], prompt="Sort the numbers, and if the list is empty print a message.")
    assert result["response_message"] == question and len(calls) == 1


def test_a_code_reply_that_mentions_an_unraised_case_is_described_from_the_change_instead(monkeypatch):
    result, _ = _turn(monkeypatch, [_edit(CODE, "Done. Note this will fail if there are fewer than two distinct values.")])
    assert result["response_kind"] == "code_edit" and result["code_after"] == CODE
    assert "fewer" not in result["response_message"].lower() and result["response_message"].startswith("Updated the code")


def test_an_explanation_the_candidate_asked_for_is_never_touched(monkeypatch):
    msg = "nums[1] raises an exception when the list has only one value, because index 1 doesn't exist."
    result, calls = _turn(monkeypatch, [_explain(msg)], prompt="Why does my code crash?", code=CODE)
    assert result["response_kind"] == "explain" and result["response_message"] == msg and len(calls) == 1


def test_the_retry_note_names_no_requirement_of_this_task(monkeypatch):
    _, calls = _turn(monkeypatch, [_clarify(LEAKING[0]), _edit(CODE)])
    note = calls[1].split("IMPORTANT - do NOT ask the candidate anything this turn, and do not mention")[1][:600]
    assert "second" not in note and "distinct" not in note
