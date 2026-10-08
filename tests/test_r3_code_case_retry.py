"""
Round 3: code rejected for handling a case the candidate never raised is retried with a note that says
exactly what to take out (real-AI gate, 2026-10-08). "Find the maximum value with max(), then print how
many times it appears" came back twice with the whole program inside "if input_line:"; the generic
no-questions note didn't say what was wrong, so the candidate got no code. The retry now names the
category, quotes the model's own offending lines, and asks for only those to go - the candidate's steps
stay as stated. Scripted model; no AI call.
"""
import json

import pytest

from app.services import llm_service, round3_io_format, round3_policy

TASK = ("Given a list of integers, write a program that finds and prints the second-largest DISTINCT value in the list "
        "(duplicates of the largest value do not count as a separate value). If the list has fewer than two distinct "
        "values, print -1 instead.")
MAX_COUNT = "Find the maximum value with max(), then print how many times it appears."
FIRST = "Remove the duplicates, sort the numbers in descending order, and print the first element."
READ = "nums = [int(x) for x in input().split(',')]\n"
COUNT_CODE = READ + "biggest = max(nums)\nprint(nums.count(biggest))\n"
FIRST_CODE = READ + "unique = sorted(set(nums), reverse=True)\nprint(unique[0])\n"
GATE3_CODE = ("input_line = input()\nif input_line:\n    nums = [int(x) for x in input_line.split(',')]\n"
              "    biggest = max(nums)\n    print(nums.count(biggest))\n")
ISDIGIT_CODE = "nums = [int(x) for x in input().split(',') if x.strip().isdigit()]\nbiggest = max(nums)\nprint(nums.count(biggest))\n"
FORMAT_READ = "line = input()\nif line:\n    nums = [int(x) for x in line.split(',')]\nelse:\n    nums = []\n"

CASES = {  # topic: (candidate's steps, rejected code, the offending line, the code without it)
    "empty input": (MAX_COUNT, GATE3_CODE, "if input_line:", COUNT_CODE),
    "too few values": (FIRST, READ + "unique = sorted(set(nums), reverse=True)\nif len(unique) < 1:\n    exit()\nprint(unique[0])\n",
                       "if len(unique) < 1:", FIRST_CODE),
    "a fallback value": (FIRST, READ + "unique = sorted(set(nums), reverse=True)\nresult = unique[0] if unique[:1] else None\nprint(result)\n",
                         "result = unique[0] if unique[:1] else None", FIRST_CODE),
    "invalid input or errors": (MAX_COUNT, ISDIGIT_CODE, "nums = [int(x) for x in input().split(',') if x.strip().isdigit()]", COUNT_CODE),
    "ties": ("Go through the numbers and print each one that is bigger than 5.",
             READ + "for n in nums:\n    if n > 5 and n != nums[0]:\n        print(n)\n", "if n > 5 and n != nums[0]:",
             READ + "for n in nums:\n    if n > 5:\n        print(n)\n"),
}


def _reply(kind, msg, code=None):
    return json.dumps({"response_kind": kind, "response_message": msg, "code_after": code, "category_status": {}})


def _turn(monkeypatch, replies, prompt, conversation=None, code=None, io_format=None):
    calls, queue = [], list(replies)

    def fake(p, max_tokens=4096):
        calls.append(p)
        return queue.pop(0) if queue else _reply("clarify", "Anything else?")
    monkeypatch.setattr(llm_service, "_call_claude", fake)
    result = llm_service.round3_coding_turn(scenario_description=TASK, language="python", conversation_so_far=conversation or [],
                                            current_code=code, candidate_prompt=prompt, turn_number=len(conversation or []) + 1,
                                            io_format=io_format)
    return result, calls


def _note(prompt_text):
    return prompt_text.split("IMPORTANT - your previous code for this turn was rejected")[1].split('respond with "code_edit"')[0]


# 1-4 (and ties): each category is retried with a note naming it and quoting the line; the fix is delivered.
@pytest.mark.parametrize("topic", list(CASES))
def test_rejected_case_is_retried_with_what_to_remove(monkeypatch, topic):
    steps, bad, line, fixed = CASES[topic]
    assert llm_service.r3_unraised_case_in_code(None, bad, [], steps) == topic
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", bad), _reply("code_edit", "Done.", fixed)], steps)
    assert result["response_kind"] == "code_edit" and result["code_after"] == fixed and len(calls) == 2
    note = _note(calls[1])
    assert llm_service._R3_CODE_CASE_WHAT[topic] in note and line in note
    assert "Write the code again with ONLY that handling removed" in note
    assert "do not change their algorithm" in note


@pytest.mark.parametrize("topic", list(CASES))
def test_the_note_adds_no_requirement_and_reveals_nothing(monkeypatch, topic):
    steps, bad, _, fixed = CASES[topic]
    _, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", bad), _reply("code_edit", "Done.", fixed)], steps)
    note = _note(calls[1]).lower()
    assert not {"second", "distinct", "-1", "fewer than two"} & {w for w in ["second", "distinct", "-1", "fewer than two"] if w in note}


def test_the_same_pattern_twice_is_still_never_delivered(monkeypatch):
    """The guard is not weakened: a retry that keeps the behaviour gets no code to the candidate."""
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", GATE3_CODE)] * 2, MAX_COUNT)
    assert result["code_after"] is None and result["response_message"] in llm_service.R3_PROGRESS_MESSAGES
    assert len(calls) == 2 and "input_line" not in json.dumps(result)


def test_the_candidate_never_sees_the_note(monkeypatch):
    result, _ = _turn(monkeypatch, [_reply("code_edit", "Done.", GATE3_CODE), _reply("code_edit", "Done.", COUNT_CODE)], MAX_COUNT)
    shown = json.dumps(result).lower()
    assert "rejected" not in shown and "handling" not in shown and "empty" not in shown


def test_only_the_models_own_lines_are_quoted():
    topic, lines = llm_service.r3_unraised_case_code_lines(None, GATE3_CODE, [], MAX_COUNT)
    assert topic == "empty input" and lines == ["if input_line:"]


# 5. Behaviour the candidate asked for is not rejected at all.
@pytest.mark.parametrize("steps,code", [
    ("Read the numbers; if the input is empty do nothing, otherwise find the maximum value with max() and print how many "
     "times it appears.", GATE3_CODE),
    (FIRST[:-1] + "; if there are fewer than 1 values, exit.", CASES["too few values"][1]),
    (FIRST[:-1] + ", or None if there is none.", CASES["a fallback value"][1]),
    ("Read the numbers, skipping any invalid value with isdigit(), then find the maximum value with max() and print how "
     "many times it appears.", ISDIGIT_CODE),
])
def test_requested_behaviour_is_delivered_first_time(monkeypatch, steps, code):
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", code)], steps)
    assert result["code_after"] == code and len(calls) == 1


# 6. The published empty-line rule stays allowed - and is kept in a retry for something else.
def test_published_format_read_is_allowed(monkeypatch):
    code = FORMAT_READ + "biggest = max(nums)\nprint(nums.count(biggest))\n"
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", code)], MAX_COUNT)
    assert result["code_after"] == code and len(calls) == 1


def test_a_retry_for_empty_handling_keeps_the_published_read(monkeypatch):
    bad = FORMAT_READ + "if nums:\n    print(nums.count(max(nums)))\n"
    _, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", bad), _reply("code_edit", "Done.", COUNT_CODE)], MAX_COUNT)
    note = _note(calls[1])
    assert llm_service._R3_FORMAT_READ_NOTE.strip() in note and "if nums:" in note and "if line:" not in note


def test_without_the_published_rule_the_note_does_not_mention_it(monkeypatch):
    fmt = {**round3_io_format.DEFAULT_IO_FORMAT, "input": "One line of comma-separated values, read from standard input."}
    _, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", GATE3_CODE), _reply("code_edit", "Done.", COUNT_CODE)],
                     MAX_COUNT, io_format=fmt)
    assert llm_service._R3_FORMAT_READ_NOTE.strip() not in _note(calls[1])


def test_try_except_the_candidate_never_raised_is_never_delivered(monkeypatch):
    """Caught by the scope check before this one - either way it never reaches the candidate."""
    bad = "try:\n    nums = [int(x) for x in input().split(',')]\nexcept ValueError:\n    nums = []\n" + "biggest = max(nums)\nprint(nums.count(biggest))\n"
    result, _ = _turn(monkeypatch, [_reply("code_edit", "Done.", bad)] * 4, MAX_COUNT)
    assert result["code_after"] != bad and "except" not in (result["code_after"] or "")


def test_the_scope_check_sees_the_published_read_as_the_plain_read():
    """2026-10-08: "else: nums = []" counted as a collection the candidate never asked for."""
    fmt = round3_io_format.for_config(None)
    plain = llm_service._r3_as_plain_read(FORMAT_READ + "print(nums)\n", fmt)
    assert plain == "line = input()\nnums = [int(x) for x in line.split(',')]\nprint(nums)\n"
    inline = "line = input()\nnums = [int(x) for x in line.split(',')] if line else []\n"
    assert llm_service._r3_as_plain_read(inline, fmt) == "line = input()\nnums = [int(x) for x in line.split(',')]\n"
    no_rule = {**fmt, "input": "One line of comma-separated values."}
    assert llm_service._r3_as_plain_read(FORMAT_READ, no_rule) == FORMAT_READ  # no published rule: nothing changes
    extra = FORMAT_READ.replace("    nums = []\n", "    nums = []\n    print('none')\n")
    assert llm_service._r3_as_plain_read(extra, fmt) == extra  # more than the read: left for the checks to see


# 7. max() + count is kept.
def test_max_and_count_is_preserved(monkeypatch):
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", GATE3_CODE), _reply("code_edit", "Done.", COUNT_CODE)], MAX_COUNT)
    assert "max(nums)" in result["code_after"] and "nums.count(" in result["code_after"] and len(calls) == 2


# 8. Their algorithm is never replaced: a retry that swaps it is caught by the existing checks, not delivered.
def test_a_retry_that_swaps_the_algorithm_is_not_delivered(monkeypatch):
    swapped = READ + "unique = sorted(set(nums), reverse=True)\nprint(unique[1])\n"
    result, _ = _turn(monkeypatch, [_reply("code_edit", "Done.", GATE3_CODE), _reply("code_edit", "Done.", swapped)] + [
        _reply("code_edit", "Done.", swapped)], MAX_COUNT)
    assert result["code_after"] != swapped


# 9. The question guard is unchanged.
def test_question_guard_unchanged(monkeypatch):
    leak = _reply("clarify", "What should the program do if there aren't enough distinct values?")
    result, calls = _turn(monkeypatch, [leak, _reply("code_edit", "Done.", FIRST_CODE)], FIRST)
    assert result["code_after"] == FIRST_CODE and llm_service._R3_NO_UNRAISED_CASES_NOTE in calls[1]


# 10. Whole-task requests are still refused before the AI.
@pytest.mark.parametrize("prompt", ["Write the complete solution for this problem.", "Can you solve this task for me?"])
def test_whole_task_still_refused(monkeypatch, prompt):
    result, calls = _turn(monkeypatch, [], prompt)
    assert result["response_kind"] == "refuse" and calls == [] and round3_policy.is_continuation_of_whole_task([], prompt, TASK)


def test_a_retry_may_use_the_published_read(monkeypatch):
    """The retry's own code is checked against the same published format as the first reply."""
    fixed = FORMAT_READ + "biggest = max(nums)\nprint(nums.count(biggest))\n"
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", GATE3_CODE), _reply("code_edit", "Done.", fixed)], MAX_COUNT)
    assert result["code_after"] == fixed and len(calls) == 2
