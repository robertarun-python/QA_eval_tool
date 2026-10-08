"""
Round 3: the published-format exemption recognises what the format SAYS an empty line means, not one
sentence (final audit, 2026-10-08). The live scenario publishes "An empty line means an empty list."; the
exemption only knew the default "An empty line means there are no values.", so the gate's delivered code
("if line: ... else: nums = []") would have been blocked in production as invented empty-input handling.
Only the read is allowed - output, fallbacks, error handling or anything else for empty input stays caught,
whatever the format says. Scripted model; no AI call.
"""
import json

import pytest

from app.services import llm_service, round3_io_format

TASK = ("Given a list of integers, write a program that finds and prints the second-largest DISTINCT value in the list "
        "(duplicates of the largest value do not count as a separate value). If the list has fewer than two distinct "
        "values, print -1 instead.")
# The live Round 3 scenario's published format, verbatim (config_json["round3_io_format"], read 2026-10-08).
LIVE_FORMAT = {
    "input": "One line of comma-separated integers (no spaces), read from standard input. An empty line means an empty list.",
    "output": "Print a single integer on one line - the second-largest distinct value, or -1 - with no labels or extra text.",
    "value_type": "integers",
}
DEFAULT_FORMAT = round3_io_format.for_config(None)
NO_RULE = {**DEFAULT_FORMAT, "input": "One line of comma-separated values, read from standard input."}

STATES_THE_RULE = [
    LIVE_FORMAT["input"],
    DEFAULT_FORMAT["input"],
    "A blank line represents no numbers.",
    "An empty line indicates an empty array.",
    "An empty input line means zero values.",
]
DOES_NOT = [
    NO_RULE["input"],
    "An empty line is invalid input.",
    "An empty line means the program should print -1.",
    "An empty line means an error.",
    "Empty lines are not allowed.",
]

FIRST = "Write the program using my approach: remove duplicates, sort descending, and return the first element."
MAX_COUNT = "Find the maximum value with max(), then print how many times it appears."
TRACK = "Go through the numbers, keep the largest value seen, and print it."
GATE1_CODE = ("input_line = input()\nif input_line:\n    numbers = list(map(int, input_line.split(',')))\nelse:\n    numbers = []\n\n"
              "unique_numbers = list(set(numbers))\nunique_numbers.sort(reverse=True)\nprint(unique_numbers[0])\n")  # gate 9, verbatim
GATE4_CODE = ("input_line = input()\nvalues = input_line.split(',') if input_line else []\nnumbers = [int(v) for v in values]\n\n"
              "largest = numbers[0]\nfor num in numbers:\n    if num > largest:\n        largest = num\n\nprint(largest)")  # gate 7
GATE3_CODE = ("input_line = input()\nvalues = [int(x) for x in input_line.split(',')] if input_line else []\n\n"
              "max_value = max(values)\ncount = values.count(max_value)\nprint(count)")  # gate 8, verbatim
READ = "line = input()\nif line:\n    numbers = [int(x) for x in line.split(',')]\nelse:\n    numbers = []\n"
BODY = "unique = sorted(set(numbers), reverse=True)\nprint(unique[0])\n"


@pytest.mark.parametrize("text", STATES_THE_RULE)
def test_formats_that_say_what_an_empty_line_means_are_recognised(text):
    assert llm_service._R3_FORMAT_EMPTY_LINE.search(text)


@pytest.mark.parametrize("text", DOES_NOT)
def test_other_statements_about_empty_input_unlock_nothing(text):
    assert not llm_service._R3_FORMAT_EMPTY_LINE.search(text)


@pytest.mark.parametrize("message,code", [(FIRST, GATE1_CODE), (TRACK, GATE4_CODE), (MAX_COUNT, GATE3_CODE)])
def test_the_gates_delivered_reads_are_allowed_under_the_live_format(message, code):
    assert llm_service.r3_unraised_case_in_code(None, code, [], message, LIVE_FORMAT["input"]) is None


@pytest.mark.parametrize("message,code", [(FIRST, GATE1_CODE), (TRACK, GATE4_CODE), (MAX_COUNT, GATE3_CODE)])
def test_without_a_published_rule_the_same_reads_are_caught(message, code):
    assert llm_service.r3_unraised_case_in_code(None, code, [], message, NO_RULE["input"]) == "empty input"


INVENTED = [  # (topic, code) - business behaviour for empty or bad input that no format states
    ("empty input", "line = input()\nif line:\n    numbers = [int(x) for x in line.split(',')]\n    print(max(numbers))\n"),
    ("empty input", READ.replace("    numbers = []\n", "    numbers = []\n    print('no values')\n") + BODY),
    ("empty input", READ + "if not numbers:\n    print(0)\nelse:\n    print(max(numbers))\n"),
    ("empty input", "line = input()\nif line:\n    numbers = [int(x) for x in line.split(',')]\nelse:\n    print(-1)\n" + BODY),
    ("a fallback value", READ + "unique = sorted(set(numbers), reverse=True)\nprint(unique[0] if unique[:1] else -1)\n"),
    ("too few values", READ + "unique = sorted(set(numbers), reverse=True)\nif len(unique) < 2:\n    exit()\nprint(unique[0])\n"),
    ("invalid input or errors", "try:\n    numbers = [int(x) for x in input().split(',')]\nexcept ValueError:\n    numbers = []\n" + BODY),
]


@pytest.mark.parametrize("topic,code", INVENTED)
def test_invented_behaviour_is_still_caught_under_the_live_format(topic, code):
    assert llm_service.r3_unraised_case_in_code(None, code, [], FIRST, LIVE_FORMAT["input"]) == topic


def test_the_scope_check_sees_the_live_read_as_the_plain_read():
    plain = llm_service._r3_as_plain_read(READ + "print(numbers)\n", LIVE_FORMAT)
    assert plain == "line = input()\nnumbers = [int(x) for x in line.split(',')]\nprint(numbers)\n"
    assert llm_service._r3_as_plain_read(READ, NO_RULE) == READ


def _reply(kind, msg, code=None):
    return json.dumps({"response_kind": kind, "response_message": msg, "code_after": code, "category_status": {}})


def _turn(monkeypatch, replies, prompt, io_format):
    calls, queue = [], list(replies)

    def fake(p, max_tokens=4096):
        calls.append(p)
        return queue.pop(0) if queue else _reply("clarify", "Anything else?")
    monkeypatch.setattr(llm_service, "_call_claude", fake)
    result = llm_service.round3_coding_turn(scenario_description=TASK, language="python", conversation_so_far=[],
                                            current_code=None, candidate_prompt=prompt, turn_number=1, io_format=io_format)
    return result, calls


@pytest.mark.parametrize("message,code", [(FIRST, GATE1_CODE), (TRACK, GATE4_CODE), (MAX_COUNT, GATE3_CODE)])
def test_live_format_reads_are_delivered_first_time(monkeypatch, message, code):
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", code)], message, LIVE_FORMAT)
    assert result["response_kind"] == "code_edit" and result["code_after"] == code and len(calls) == 1


def test_invented_empty_behaviour_under_the_live_format_is_retried_with_the_read_kept(monkeypatch):
    wrapped = ("input_line = input()\nif input_line:\n    numbers = [int(x) for x in input_line.split(',')]\n"
               "    biggest = max(numbers)\n    print(numbers.count(biggest))\n")
    fixed = READ + "biggest = max(numbers)\nprint(numbers.count(biggest))\n"
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", wrapped), _reply("code_edit", "Done.", fixed)],
                          MAX_COUNT, LIVE_FORMAT)
    assert result["code_after"] == fixed and len(calls) == 2
    assert llm_service._R3_FORMAT_READ_NOTE.strip() in calls[1]


def test_the_live_format_never_unlocks_a_fallback(monkeypatch):
    bad = INVENTED[4][1]
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", bad)] * 2, FIRST, LIVE_FORMAT)
    assert result["code_after"] is None and len(calls) == 2
