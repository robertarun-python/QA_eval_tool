"""
Round 3: a goal-only request is refused before the AI, however it's worded (policy A, 2026-10-08).
Real-AI gate case 7: "Work out the second largest distinct number and print it." restated the task with no
operation of the candidate's own, but "work", "out" and "number" counted as content and diluted the share
of task words to 4/7 (under 60%), so it reached the model, which asked for steps instead of refusing. Goal
verbs, generic data nouns and filler now count as generic - no task word is involved. A candidate's own
method, and an incomplete requirement that needs a question, are untouched. No AI call.
"""
import json
import re

import pytest

from app.services import llm_service, round3_policy

TASK = ("Given a list of integers, write a program that finds and prints the second-largest DISTINCT value in the list "
        "(duplicates of the largest value do not count as a separate value). If the list has fewer than two distinct "
        "values, print -1 instead.")
NEW_WORDS = ("work out find figure compute calculate determine get number numbers value values integer integers "
             "element elements item items there isn aren one any none").split()

NEWLY_REFUSED = [
    "Work out the second largest distinct number and print it.",                       # gate case 7, verbatim
    "Find the second-largest distinct value and print it, or -1 if there isn't one.",  # gate 3 #37, verbatim
    "Figure out the second biggest distinct number and print it.",
]
STILL_REFUSED = [
    "Work out the second largest distinct number in the list and print it.",
    "Calculate the second largest distinct value and output it.",
    "Write the complete solution for this problem.",
    "Can you solve this task for me?",
]
MUST_STAY_ALLOWED = [
    "Write the program using my approach: remove duplicates, sort descending, and return the first element.",
    "Use a set to remove duplicates, sort with reverse=True, and print the first element.",
    "Find the maximum value with max(), then print how many times it appears.",
    "Go through the numbers, keep the largest value seen, and print it.",
    "Remove the duplicates, sort the numbers in descending order, and print the element at index 1.",
    "Read the comma-separated numbers - an empty line means there are no values - then remove the duplicates, sort them "
    "in descending order, and print the first one.",
    "Don't sort. Go through the numbers once, track the largest value seen, and print it at the end.",
    "Print the largest value.",
    "Find the largest number and print it.",
    "Print the numbers.",
    "Count the distinct values and print the count.",
    "If there are fewer than 2 distinct values print -1.",
    "Find the maximum value and print how many times it appears in the list.",
]
MISSING_INFORMATION = [  # incomplete - reach the AI, which asks
    "Keep values greater than the threshold.",
    "Print only the values that are greater than the limit.",
    "Filter the numbers to keep the ones above my cutoff value, then print them.",
    "Print the list I created earlier.",
]


@pytest.mark.parametrize("message", NEWLY_REFUSED + STILL_REFUSED)
def test_goal_only_requests_are_refused_before_the_ai(message):
    assert round3_policy.is_whole_task_request(message, TASK)
    assert round3_policy.is_continuation_of_whole_task([], message, TASK)


@pytest.mark.parametrize("message", MUST_STAY_ALLOWED + MISSING_INFORMATION)
def test_own_methods_and_incomplete_requirements_still_reach_the_ai(message):
    assert not round3_policy.is_whole_task_request(message, TASK)
    assert not round3_policy.is_continuation_of_whole_task([], message, TASK)


def test_the_new_generic_words_name_nothing_of_any_task():
    assert set(NEW_WORDS) <= round3_policy._GENERIC_WORDS
    task_words = set(re.findall(r"[a-z]+", TASK.lower())) - {"value", "values", "integers", "one"}
    # generic data nouns the task statement also happens to use are the only overlap
    assert not set(NEW_WORDS) & task_words
    for word in ("second", "largest", "distinct", "duplicates", "list", "biggest", "smallest", "unique", "sorted"):
        assert word not in round3_policy._GENERIC_WORDS


def test_the_paraphrase_rule_itself_is_unchanged():
    assert round3_policy._PARAPHRASE_MIN_WORDS == 4 and round3_policy._PARAPHRASE_SHARE == 0.6


def _turn(monkeypatch, prompt, replies=()):
    calls, queue = [], list(replies)

    def fake(p, max_tokens=4096):
        calls.append(p)
        return queue.pop(0)
    monkeypatch.setattr(llm_service, "_call_claude", fake)
    result = llm_service.round3_coding_turn(scenario_description=TASK, language="python", conversation_so_far=[],
                                            current_code=None, candidate_prompt=prompt, turn_number=1)
    return result, calls


def test_case7_replay_is_refused_with_no_ai_call(monkeypatch):
    result, calls = _turn(monkeypatch, NEWLY_REFUSED[0])
    assert result["response_kind"] == "refuse" and result["code_after"] is None and calls == []
    assert result["response_message"] == round3_policy.WHOLE_TASK_REFUSAL_MESSAGE


def test_a_threshold_request_still_gets_its_question(monkeypatch):
    question = "What threshold should the values be compared against?"
    reply = json.dumps({"response_kind": "clarify", "response_message": question, "code_after": None, "category_status": {}})
    result, calls = _turn(monkeypatch, MISSING_INFORMATION[0], [reply])
    assert result["response_message"] == question and len(calls) == 1


def test_the_prompt_example_is_no_longer_task_specific():
    prompt = llm_service._load_prompt("round3_coding_turn.txt")
    assert "work out the second largest" not in prompt
    assert 'they said "get the answer" or "then combine them" without saying what the code should do' in prompt
