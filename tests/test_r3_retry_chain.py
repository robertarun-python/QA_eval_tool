"""
Round 3: a steering retry whose code handles a case the candidate never raised gets ONE more, targeted
repair (real-AI gate, 2026-10-08, case 4). "Go through the numbers, keep the largest value seen, and print
it." got a step question (steering retry), then the candidate's exact algorithm wrapped in "if input_line:"
- caught, but the single retry was spent, so the candidate got no code. Only this chain gets a second
retry; at most three calls; the edge-case check still decides what is delivered. Scripted model; no AI call.
"""
import json

import pytest

from app.services import llm_service

TASK = ("Given a list of integers, write a program that finds and prints the second-largest DISTINCT value in the list "
        "(duplicates of the largest value do not count as a separate value). If the list has fewer than two distinct "
        "values, print -1 instead.")
TRACK = "Go through the numbers, keep the largest value seen, and print it."
MAX_COUNT = "Find the maximum value with max(), then print how many times it appears."
FIRST = "Remove the duplicates, sort the numbers in descending order, and print the first element."
STEP_QUESTION = "What should the code do with the values once it has them?"  # case 4's first draft, verbatim
CASE4_DRAFT = ("input_line = input()\nif input_line:\n    numbers = [int(x) for x in input_line.split(',')]\n"
               "    largest = numbers[0]\n    for num in numbers:\n        if num > largest:\n            largest = num\n"
               "    print(largest)\n")  # case 4's second draft, verbatim
TRACK_CODE = ("input_line = input()\nvalues = input_line.split(',')\nnumbers = [int(v) for v in values]\nlargest = numbers[0]\n"
              "for num in numbers:\n    if num > largest:\n        largest = num\nprint(largest)\n")
FORMAT_TRACK = ("line = input()\nif line:\n    numbers = [int(x) for x in line.split(',')]\nelse:\n    numbers = []\n"
                "largest = numbers[0]\nfor num in numbers:\n    if num > largest:\n        largest = num\nprint(largest)\n")
READ = "nums = [int(x) for x in input().split(',')]\n"
GATE3_CODE = ("input_line = input()\nif input_line:\n    nums = [int(x) for x in input_line.split(',')]\n"
              "    biggest = max(nums)\n    print(nums.count(biggest))\n")
COUNT_CODE = READ + "biggest = max(nums)\nprint(nums.count(biggest))\n"
WRAPPED_FIRST = ("input_line = input()\nif input_line:\n    nums = [int(x) for x in input_line.split(',')]\n"
                 "    nums = sorted(set(nums), reverse=True)\n    print(nums[0])\n")
FIRST_CODE = READ + "unique = sorted(set(nums), reverse=True)\nprint(unique[0])\n"


def _reply(kind, msg, code=None):
    return json.dumps({"response_kind": kind, "response_message": msg, "code_after": code, "category_status": {}})


def _turn(monkeypatch, replies, prompt):
    calls, queue = [], list(replies)

    def fake(p, max_tokens=4096):
        calls.append(p)
        return queue.pop(0) if queue else _reply("code_edit", "Done.", CASE4_DRAFT)
    monkeypatch.setattr(llm_service, "_call_claude", fake)
    result = llm_service.round3_coding_turn(scenario_description=TASK, language="python", conversation_so_far=[],
                                            current_code=None, candidate_prompt=prompt, turn_number=1)
    return result, calls


CODE_NOTE = "IMPORTANT - your previous code for this turn was rejected"


# 1 + 5. The exact case-4 sequence: step question -> steering retry -> wrapped code -> targeted repair -> code.
def test_case4_sequence_gets_its_code(monkeypatch):
    result, calls = _turn(monkeypatch, [_reply("clarify", STEP_QUESTION), _reply("code_edit", "Done.", CASE4_DRAFT),
                                        _reply("code_edit", "Done.", TRACK_CODE)], TRACK)
    assert result["response_kind"] == "code_edit" and result["code_after"] == TRACK_CODE and len(calls) == 3
    assert llm_service._R3_APPROACH_NOTE in calls[1] and CODE_NOTE not in calls[1]
    assert llm_service._R3_APPROACH_NOTE in calls[2] and CODE_NOTE in calls[2]  # keeps steering protection too
    assert "it added handling for empty input" in calls[2] and "    if input_line:" in calls[2]


def test_the_repair_may_keep_the_published_read(monkeypatch):
    result, calls = _turn(monkeypatch, [_reply("clarify", STEP_QUESTION), _reply("code_edit", "Done.", CASE4_DRAFT),
                                        _reply("code_edit", "Done.", FORMAT_TRACK)], TRACK)
    assert result["code_after"] == FORMAT_TRACK and len(calls) == 3
    assert llm_service._R3_FORMAT_READ_NOTE.strip() in calls[2]


# 6. Never a third retry: a repair that fails again ends in the usual no-code way forward.
@pytest.mark.parametrize("repair", [
    _reply("code_edit", "Done.", CASE4_DRAFT),                     # same wrapper again
    _reply("clarify", STEP_QUESTION),                             # back to asking
    _reply("clarify", "What should happen if there are no numbers?"),  # an edge-case question
    _reply("clarify", "What should the variable for the result be called?"),  # any new question - not shown either
])
def test_no_third_retry(monkeypatch, repair):
    result, calls = _turn(monkeypatch, [_reply("clarify", STEP_QUESTION), _reply("code_edit", "Done.", CASE4_DRAFT), repair], TRACK)
    assert len(calls) == 3
    assert result["response_kind"] == "explain" and result["code_after"] is None
    assert result["response_message"] in llm_service.R3_PROGRESS_MESSAGES


# 7. Nothing of the task's goal reaches the model's notes or the candidate.
def test_no_hidden_goal_in_notes_or_reply(monkeypatch):
    result, calls = _turn(monkeypatch, [_reply("clarify", STEP_QUESTION), _reply("code_edit", "Done.", CASE4_DRAFT),
                                        _reply("code_edit", "Done.", TRACK_CODE)], TRACK)
    end = 'Do not ask the candidate anything and do not mention this - respond with "code_edit".'
    notes = [calls[1][calls[1].index(llm_service._R3_APPROACH_NOTE):][:len(llm_service._R3_APPROACH_NOTE)],
             calls[2][calls[2].index(llm_service._R3_APPROACH_NOTE):calls[2].index(end) + len(end)]]
    assert CODE_NOTE in notes[1]
    for note in notes:
        assert not [w for w in ("second", "distinct", "-1", "fewer than two") if w in note.lower()]
    shown = json.dumps(result).lower()
    assert not [w for w in ("second", "distinct", "-1", "empty", "rejected") if w in shown]


# 2. Case 3 (a first reply with the wrapper) is unchanged: one targeted retry, two calls.
def test_case3_unchanged(monkeypatch):
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", GATE3_CODE), _reply("code_edit", "Done.", COUNT_CODE)], MAX_COUNT)
    assert result["code_after"] == COUNT_CODE and len(calls) == 2 and CODE_NOTE in calls[1]
    assert llm_service._R3_APPROACH_NOTE not in calls[1]


# 3. A single edge-case retry that fails again still gets no second retry.
def test_single_edge_case_retry_still_capped_at_two_calls(monkeypatch):
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", GATE3_CODE)] * 3, MAX_COUNT)
    assert len(calls) == 2 and result["code_after"] is None


# 4. Steering alone is unchanged: clean code from the steering retry is delivered in two calls.
def test_steering_only_unchanged(monkeypatch):
    result, calls = _turn(monkeypatch, [_reply("clarify", STEP_QUESTION), _reply("code_edit", "Done.", TRACK_CODE)], TRACK)
    assert result["code_after"] == TRACK_CODE and len(calls) == 2


def test_steering_retry_that_asks_again_gets_no_repair(monkeypatch):
    """The extra retry is only for code with an unraised case - not another go at a question."""
    result, calls = _turn(monkeypatch, [_reply("clarify", STEP_QUESTION), _reply("clarify", STEP_QUESTION)], TRACK)
    assert len(calls) == 2 and result["code_after"] is None


def test_other_retry_kinds_get_no_repair(monkeypatch):
    """An edge-case QUESTION goes through its own retry; wrapped code from that retry is not repaired again."""
    leak = _reply("clarify", "What should the program do if there aren't enough distinct values?")
    result, calls = _turn(monkeypatch, [leak, _reply("code_edit", "Done.", WRAPPED_FIRST)], FIRST)
    assert len(calls) == 2 and result["code_after"] is None


def test_a_repair_that_drops_their_steps_is_not_delivered(monkeypatch):
    """The repair still goes through the scope check: code without their stated de-duplication never reaches them."""
    dropped = READ + "nums.sort(reverse=True)\nprint(nums[0])\n"
    wrapped = "input_line = input()\nif input_line:\n    nums = [int(x) for x in input_line.split(',')]\n    nums = sorted(set(nums), reverse=True)\n    print(nums[0])\n"
    result, calls = _turn(monkeypatch, [_reply("clarify", "What should the code do after sorting?"), _reply("code_edit", "Done.", wrapped),
                                        _reply("code_edit", "Done.", dropped), _reply("code_edit", "Done.", dropped)], FIRST)
    assert result["code_after"] != dropped
