"""
Round 3: an "output" question to a complete approach of the candidate's own is a step question too
(real-AI gate 8, 2026-10-08, case 1). "Write the program using my approach: remove duplicates, sort
descending, and return the first element." got "What should the program output?" - shown to the candidate,
because the steering check only knew "what should the code DO". The verb now also covers output, print,
return, display, show and produce. Questions for missing information (a threshold, a cutoff, a name) are
not this shape and stay allowed. Scripted model; no AI call.
"""
import json

import pytest

from app.services import llm_service

TASK = ("Given a list of integers, write a program that finds and prints the second-largest DISTINCT value in the list "
        "(duplicates of the largest value do not count as a separate value). If the list has fewer than two distinct "
        "values, print -1 instead.")
CASE1 = "Write the program using my approach: remove duplicates, sort descending, and return the first element."
THRESHOLD_APPROACH = "Remove the duplicates, keep the values greater than the threshold, and print them."
READ = "nums = [int(x) for x in input().split(',')]\n"
CASE1_CODE = READ + "unique = sorted(set(nums), reverse=True)\nprint(unique[0])\n"

NEWLY_CAUGHT = [
    "What should the program output?",  # gate 8, case 1, verbatim
    "What should the code output?",
    "What do you want the program to print?",
    "What should it return?",
    "What would you like the code to display?",
    "What should the program produce?",
]
STILL_CAUGHT = ["What should the program do with the values?", "What should the code do with the values?"]
MISSING_INFORMATION = [
    "What threshold should I use?",
    "Which value should be the cutoff?",
    "What threshold should the values be compared against?",
    "What cutoff value should the code use?",
    "What is the threshold?",
    "What values should be kept, and what is the threshold?",
    "What should the list be called?",
    "Which element should be printed?",
    "What output format should I use?",
]


def _clarify(q):
    return {"response_kind": "clarify", "response_message": q}


@pytest.mark.parametrize("question", NEWLY_CAUGHT + STILL_CAUGHT)
def test_output_and_action_questions_to_a_complete_approach_are_caught(question):
    assert llm_service.r3_steers_complete_approach(_clarify(question), CASE1, TASK) == "asked what the code should do"


@pytest.mark.parametrize("question", MISSING_INFORMATION)
@pytest.mark.parametrize("approach", [CASE1, THRESHOLD_APPROACH])
def test_missing_information_questions_stay_allowed(question, approach):
    assert llm_service.r3_steers_complete_approach(_clarify(question), approach, TASK) is None


@pytest.mark.parametrize("question", NEWLY_CAUGHT)
@pytest.mark.parametrize("vague", ["Print the result.", "Work with the numbers next.", "Make it output something."])
def test_a_vague_message_may_still_be_asked_about(question, vague):
    """Only a complete approach of their own is protected - a vague message is untouched."""
    assert llm_service.r3_steers_complete_approach(_clarify(question), vague, TASK) is None


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


def test_case1_replay_gets_code_and_never_shows_the_question(monkeypatch):
    result, calls = _turn(monkeypatch, [_reply("clarify", "What should the program output?"),
                                        _reply("code_edit", "Done.", CASE1_CODE)], CASE1)
    assert result["response_kind"] == "code_edit" and result["code_after"] == CASE1_CODE and len(calls) == 2
    assert llm_service._R3_APPROACH_NOTE in calls[1]
    assert "output?" not in json.dumps(result)


def test_a_missing_threshold_question_is_still_shown(monkeypatch):
    result, calls = _turn(monkeypatch, [_reply("clarify", "What threshold should I use?")], THRESHOLD_APPROACH)
    assert result["response_message"] == "What threshold should I use?" and len(calls) == 1


def test_an_edge_case_output_question_keeps_the_edge_case_retry(monkeypatch):
    """Both checks match; the edge-case guard's own retry still comes first."""
    question = "What should the program print if the list is empty?"
    assert llm_service.r3_steers_complete_approach(_clarify(question), CASE1, TASK)
    result, calls = _turn(monkeypatch, [_reply("clarify", question), _reply("code_edit", "Done.", CASE1_CODE)], CASE1)
    assert result["code_after"] == CASE1_CODE and len(calls) == 2
    assert llm_service._R3_NO_UNRAISED_CASES_NOTE in calls[1] and llm_service._R3_APPROACH_NOTE not in calls[1]
    assert "empty" not in json.dumps(result).lower()
